"""Real-Debrid debrid service client for Shelfmark.

Routes magnet links through the Real-Debrid REST API (v1.0) to download
torrent content via Real-Debrid's CDN infrastructure.
"""

from __future__ import annotations

import shutil
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, NoReturn

import requests

from shelfmark.config.env import TMP_DIR
from shelfmark.core.config import config
from shelfmark.core.logger import setup_logger
from shelfmark.download.clients import (
    DownloadClient,
    DownloadState,
    DownloadStatus,
    register_client,
)
from shelfmark.download.clients._coercion import config_text
from shelfmark.download.clients.torrent_utils import (
    DebridMagnet,
    DebridUpload,
    resolve_debrid_upload,
)
from shelfmark.download.http import download_url
from shelfmark.download.network import get_ssl_verify

logger = setup_logger(__name__)

_API_BASE = "https://api.real-debrid.com/rest/1.0"

# One session for every client instance, so the status poll every couple of seconds
# reuses a keep-alive connection instead of opening a new TLS connection each time.
_session = requests.Session()

_REALDEBRID_CLIENT_ERRORS = (
    AttributeError,
    OSError,
    requests.exceptions.RequestException,
    RuntimeError,
    TypeError,
    ValueError,
)

# Real-Debrid torrent status values.
_STATUS_DOWNLOADING = frozenset(
    {
        "magnet_conversion",
        "waiting_files_selection",
        "queued",
        "downloading",
        "compressing",
        "uploading",
    }
)
_STATUS_READY = "downloaded"
_STATUS_ERROR = frozenset({"error", "virus", "dead"})

# Timeouts for API calls.
_API_TIMEOUT = 30
_STATUS_TIMEOUT = 15

# File extensions recognised as book or audiobook content.
_BOOK_EXTENSIONS = (
    ".aac",
    ".azw",
    ".azw3",
    ".cbr",
    ".cbz",
    ".djvu",
    ".doc",
    ".docx",
    ".epub",
    ".fb2",
    ".flac",
    ".lit",
    ".m4a",
    ".m4b",
    ".mobi",
    ".mp3",
    ".mp4",
    ".ogg",
    ".opus",
    ".pdf",
    ".rtf",
    ".txt",
    ".wma",
)


def _raise_runtime_error(message: str) -> NoReturn:
    raise RuntimeError(message)


@dataclass
class _DownloadState:
    """Internal mutable state for an in-progress Real-Debrid download."""

    torrent_id: str
    name: str
    target_dir: Path
    phase: str = "uploading"
    error_message: str | None = None
    progress: float = 0.0
    download_thread: threading.Thread | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)


@register_client("torrent")
class RealDebridClient(DownloadClient):
    """Real-Debrid debrid service client.

    Downloads torrent content by uploading magnet links to Real-Debrid,
    selecting all files for download on their servers, then unrestricting
    and fetching the resulting files via direct HTTP download from
    Real-Debrid's CDN.

    API documentation: https://api.real-debrid.com/
    """

    protocol = "torrent"
    name = "realdebrid"

    _downloads: ClassVar[dict[str, _DownloadState]] = {}
    _downloads_lock = threading.Lock()

    def __init__(self) -> None:
        self._api_key = config_text(config.get("REALDEBRID_API_KEY", ""))

    def _auth_headers(self) -> dict[str, str]:
        """Return Authorization header dict for API requests."""
        return {"Authorization": f"Bearer {self._api_key}"}

    # ------------------------------------------------------------------
    # DownloadClient interface
    # ------------------------------------------------------------------

    @staticmethod
    def is_configured() -> bool:
        """Return True when Real-Debrid is selected and an API key exists."""
        client = config_text(config.get("PROWLARR_TORRENT_CLIENT", ""))
        api_key = config_text(config.get("REALDEBRID_API_KEY", ""))
        return client == "realdebrid" and bool(api_key)

    def test_connection(self) -> tuple[bool, str]:
        """Validate the API key and check Premium subscription status."""
        if not self._api_key:
            return False, "Real-Debrid API Key is required"
        try:
            url = f"{_API_BASE}/user"
            resp = _session.get(
                url,
                headers=self._auth_headers(),
                timeout=_STATUS_TIMEOUT,
                verify=get_ssl_verify(url),
            )
            resp.raise_for_status()
            user = resp.json()
            username = user.get("username", "Unknown")
            account_type = user.get("type", "free")
            if account_type != "premium":
                return (
                    False,
                    f"Real-Debrid user '{username}' does not have "
                    f"a Premium subscription (type: {account_type})",
                )
        except _REALDEBRID_CLIENT_ERRORS as e:
            return False, f"Connection failed: {e}"
        else:
            return True, f"Connected to Real-Debrid as '{username}' (Premium)"

    def add_download(
        self,
        url: str,
        name: str,
        category: str | None = None,
        expected_hash: str | None = None,
        **kwargs: object,
    ) -> str:
        """Send a torrent to Real-Debrid and select all files.

        Accepts a magnet link, a .torrent URL, or an indexer proxy URL; anything
        that is not already a magnet is resolved first, because Real-Debrid
        answers a non-magnet body on addMagnet with a bare 404 (#1250).
        """
        if not self._api_key:
            msg = "Real-Debrid API key is not configured"
            raise RuntimeError(msg)

        try:
            upload = resolve_debrid_upload(url, expected_hash=expected_hash)
            data = self._send_torrent(upload)

            torrent_id = str(data.get("id", ""))
            if not torrent_id:
                msg = "No torrent ID returned from Real-Debrid"
                _raise_runtime_error(msg)

            # Select all files so Real-Debrid starts downloading the torrent
            select_url = f"{_API_BASE}/torrents/selectFiles/{torrent_id}"
            sel_resp = _session.post(
                select_url,
                headers=self._auth_headers(),
                data={"files": "all"},
                timeout=_API_TIMEOUT,
                verify=get_ssl_verify(select_url),
            )
            sel_resp.raise_for_status()

            target_dir = TMP_DIR / f"realdebrid_{torrent_id}"
            target_dir.mkdir(parents=True, exist_ok=True)

            state = _DownloadState(
                torrent_id=torrent_id,
                name=name,
                target_dir=target_dir,
                phase="waiting_rd",
            )
            with self._downloads_lock:
                self._downloads[torrent_id] = state

            logger.info(
                "Added torrent to Real-Debrid: ID %s (%s)",
                torrent_id,
                name,
            )

        except Exception:
            logger.exception("Failed to add torrent to Real-Debrid")
            raise

        else:
            return torrent_id

    def _send_torrent(self, upload: DebridUpload) -> dict[str, Any]:
        """Hand the torrent to Real-Debrid, as a magnet or as a file upload."""
        if isinstance(upload, DebridMagnet):
            add_url = f"{_API_BASE}/torrents/addMagnet"
            resp = _session.post(
                add_url,
                headers=self._auth_headers(),
                data={"magnet": upload.magnet_url},
                timeout=_API_TIMEOUT,
                verify=get_ssl_verify(add_url),
            )
        else:
            # addTorrent is a PUT that takes the raw file as the request body,
            # not a form field: https://api.real-debrid.com/
            add_url = f"{_API_BASE}/torrents/addTorrent"
            resp = _session.put(
                add_url,
                headers={
                    **self._auth_headers(),
                    "Content-Type": "application/x-bittorrent",
                },
                data=upload.torrent_data,
                timeout=_API_TIMEOUT,
                verify=get_ssl_verify(add_url),
            )

        resp.raise_for_status()
        return resp.json()

    def get_status(self, download_id: str) -> DownloadStatus:
        """Poll Real-Debrid for torrent status and drive the download."""
        state = self._ensure_state(download_id)

        # Return cached terminal / in-flight states immediately.
        with state.lock:
            if state.phase == "error":
                return DownloadStatus.error(
                    state.error_message or "Real-Debrid error",
                )
            if state.phase == "complete":
                return DownloadStatus(
                    progress=100.0,
                    state=DownloadState.COMPLETE,
                    message="Complete",
                    complete=True,
                    file_path=str(state.target_dir),
                )
            if state.phase == "downloading_http":
                return DownloadStatus(
                    progress=state.progress,
                    state=DownloadState.DOWNLOADING,
                    message="Downloading files via HTTP...",
                    complete=False,
                    file_path=None,
                )

        # Query Real-Debrid for torrent info.
        try:
            info_url = f"{_API_BASE}/torrents/info/{download_id}"
            resp = _session.get(
                info_url,
                headers=self._auth_headers(),
                timeout=_STATUS_TIMEOUT,
                verify=get_ssl_verify(info_url),
            )
            resp.raise_for_status()
            info = resp.json()
            return self._handle_torrent_info(info, state)

        except Exception as e:
            logger.exception(
                "Error checking Real-Debrid status for %s",
                download_id,
            )
            return DownloadStatus.error(str(e))

    def remove(
        self,
        download_id: str,
        *,
        delete_files: bool = False,
    ) -> bool:
        """Delete the torrent from Real-Debrid and clean up local files."""
        try:
            url = f"{_API_BASE}/torrents/delete/{download_id}"
            _session.delete(
                url,
                headers=self._auth_headers(),
                timeout=_STATUS_TIMEOUT,
                verify=get_ssl_verify(url),
            )
        except _REALDEBRID_CLIENT_ERRORS as e:
            logger.warning("Failed to delete torrent from Real-Debrid: %s", e)

        with self._downloads_lock:
            state = self._downloads.pop(download_id, None)

        if state and state.target_dir.exists():
            shutil.rmtree(state.target_dir, ignore_errors=True)
        return True

    def get_download_path(self, download_id: str) -> str | None:
        """Return the local directory containing downloaded files."""
        with self._downloads_lock:
            state = self._downloads.get(download_id)
        if state and state.phase == "complete":
            return str(state.target_dir)
        target_dir = TMP_DIR / f"realdebrid_{download_id}"
        if target_dir.exists():
            return str(target_dir)
        return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_state(self, download_id: str) -> _DownloadState:
        """Get or create download state for the given torrent ID."""
        with self._downloads_lock:
            state = self._downloads.get(download_id)
        if state:
            return state

        target_dir = TMP_DIR / f"realdebrid_{download_id}"
        state = _DownloadState(
            torrent_id=download_id,
            name=f"Download {download_id}",
            target_dir=target_dir,
            phase="waiting_rd",
        )
        with self._downloads_lock:
            self._downloads[download_id] = state
        return state

    def _handle_torrent_info(
        self,
        info: dict[str, Any],
        state: _DownloadState,
    ) -> DownloadStatus:
        """Map Real-Debrid torrent info to a DownloadStatus."""
        status = info.get("status", "")

        if status in _STATUS_DOWNLOADING:
            progress = float(info.get("progress", 0.0))
            speed = int(info.get("speed", 0))
            filename = info.get("filename", state.name)
            return DownloadStatus(
                progress=progress * 0.5,
                state=DownloadState.DOWNLOADING,
                message=f"Real-Debrid downloading torrent ({filename})",
                complete=False,
                file_path=None,
                download_speed=speed,
            )

        if status == _STATUS_READY:
            links = info.get("links", [])
            files = info.get("files", [])
            self._maybe_start_download_thread(state, links, files)
            return DownloadStatus(
                progress=50.0,
                state=DownloadState.DOWNLOADING,
                message="Real-Debrid ready, retrieving files...",
                complete=False,
                file_path=None,
            )

        # Terminal error from Real-Debrid.
        error_txt = f"Real-Debrid status error: {status}"
        with state.lock:
            state.phase = "error"
            state.error_message = error_txt
        return DownloadStatus.error(error_txt)

    def _maybe_start_download_thread(
        self,
        state: _DownloadState,
        links: list[str],
        files: list[dict[str, Any]],
    ) -> None:
        """Spawn a background thread to unrestrict and download files."""
        with state.lock:
            already_running = state.phase in (
                "unrestricting",
                "downloading_http",
                "complete",
            )
            thread_alive = state.download_thread is not None and state.download_thread.is_alive()
            if already_running or thread_alive:
                return
            state.phase = "unrestricting"
            t = threading.Thread(
                target=self._process_and_download,
                args=(state, links, files),
                daemon=True,
            )
            state.download_thread = t
            t.start()

    # ------------------------------------------------------------------
    # File download pipeline
    # ------------------------------------------------------------------

    def _process_and_download(
        self,
        state: _DownloadState,
        links: list[str],
        files: list[dict[str, Any]],
    ) -> None:
        """Unrestrict links and download files via HTTP.

        Runs in a background thread spawned by ``_maybe_start_download_thread``.
        """
        try:
            if not links:
                msg = "No download links returned by Real-Debrid"
                _raise_runtime_error(msg)

            # Match selected files with links
            selected_files = [f for f in files if f.get("selected") == 1]

            # Filter relevant ebook / audiobook files
            relevant_indices: list[int] = []
            for i, f_info in enumerate(selected_files):
                path_str = f_info.get("path", "").lower()
                if path_str.endswith(_BOOK_EXTENSIONS):
                    relevant_indices.append(i)

            if not relevant_indices:
                relevant_indices = list(range(len(links)))

            with state.lock:
                state.phase = "downloading_http"

            total = len(relevant_indices)
            for idx, rel_idx in enumerate(relevant_indices):
                if rel_idx >= len(links):
                    continue
                link = links[rel_idx]

                # Unrestrict the Real-Debrid link to get direct CDN download URL
                unrestrict_url = f"{_API_BASE}/unrestrict/link"
                unl_resp = _session.post(
                    unrestrict_url,
                    headers=self._auth_headers(),
                    data={"link": link},
                    timeout=_API_TIMEOUT,
                    verify=get_ssl_verify(unrestrict_url),
                )
                unl_resp.raise_for_status()
                unl_data = unl_resp.json()

                direct_url = unl_data.get("download")
                filename = unl_data.get("filename")
                if not direct_url:
                    msg = f"Failed to unrestrict Real-Debrid link: {link}"
                    _raise_runtime_error(msg)

                # Determine relative file path
                if rel_idx < len(selected_files):
                    rel_path_str = selected_files[rel_idx].get("path", "").lstrip("/")
                    rel_path = Path(rel_path_str)
                else:
                    rel_path = Path(filename or f"file_{idx + 1}")

                dest = state.target_dir / rel_path
                dest.parent.mkdir(parents=True, exist_ok=True)

                logger.info(
                    "Downloading Real-Debrid file %d/%d: %s",
                    idx + 1,
                    total,
                    rel_path,
                )

                buf = download_url(
                    direct_url,
                    referer="https://real-debrid.com/",
                )
                if not buf:
                    msg = f"Failed to download from {direct_url}"
                    _raise_runtime_error(msg)

                with dest.open("wb") as fh:
                    fh.write(buf.getvalue())

                with state.lock:
                    state.progress = 50.0 + (idx + 1) / total * 50.0

            with state.lock:
                state.phase = "complete"
                state.progress = 100.0

            logger.info(
                "Real-Debrid download complete for ID %s at %s",
                state.torrent_id,
                state.target_dir,
            )

        except Exception:
            logger.exception(
                "Error in Real-Debrid download for ID %s",
                state.torrent_id,
            )
            with state.lock:
                state.phase = "error"
                state.error_message = str(
                    state.error_message or "Download failed",
                )
