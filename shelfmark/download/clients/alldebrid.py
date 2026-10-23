"""AllDebrid debrid service client for Shelfmark.

Routes magnet links through the AllDebrid API (v4/v4.1) to download
torrent content via AllDebrid's CDN infrastructure.
"""

from __future__ import annotations

import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, NoReturn
from urllib.parse import quote

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
    _safe_url,
    resolve_debrid_upload,
)
from shelfmark.download.http import download_url
from shelfmark.download.network import get_ssl_verify

logger = setup_logger(__name__)

_API_BASE = "https://api.alldebrid.com/v4"

# One session for every client instance, so the status poll every couple of seconds
# reuses a keep-alive connection instead of opening a new TLS connection each time.
_session = requests.Session()

_AGENT = "shelfmark"

_ALLDEBRID_CLIENT_ERRORS = (
    AttributeError,
    OSError,
    requests.exceptions.RequestException,
    RuntimeError,
    TypeError,
    ValueError,
)

# AllDebrid magnet status codes (from API v4.1 documentation).
_STATUS_DOWNLOADING = frozenset({0, 1, 2, 3})
_STATUS_READY = 4

# Timeouts and retry limits for API calls.
_API_TIMEOUT = 30
_STATUS_TIMEOUT = 15
_DELAYED_POLL_INTERVAL = 5
_DELAYED_POLL_MAX_ATTEMPTS = 12

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


def _flatten_magnet_files(
    entries: list[dict[str, Any]],
    prefix: str = "",
) -> list[dict[str, Any]]:
    """Flatten AllDebrid's nested file tree into a list of file dicts.

    AllDebrid returns files with ``"n"`` (name), ``"s"`` (size),
    ``"l"`` (link), and ``"e"`` (children) keys.  Directories use
    ``"e"`` to nest their contents.

    Returns:
        List of ``{"filename": ..., "size": ..., "link": ...}`` dicts.

    """
    flat: list[dict[str, Any]] = []
    for entry in entries:
        name = entry.get("n", "")
        if "e" in entry:
            flat.extend(
                _flatten_magnet_files(entry["e"], prefix=f"{prefix}{name}/"),
            )
        elif entry.get("l"):
            flat.append(
                {
                    "filename": f"{prefix}{name}",
                    "size": entry.get("s", 0),
                    "link": entry["l"],
                }
            )
    return flat


def _raise_runtime_error(message: str) -> NoReturn:
    raise RuntimeError(message)


@dataclass
class _DownloadState:
    """Internal mutable state for an in-progress AllDebrid download."""

    magnet_id: str
    name: str
    target_dir: Path
    phase: str = "uploading"
    error_message: str | None = None
    progress: float = 0.0
    download_thread: threading.Thread | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)


@register_client("torrent")
class AllDebridClient(DownloadClient):
    """AllDebrid debrid service client.

    Downloads torrent content by uploading magnet links to AllDebrid,
    waiting for the torrent to complete on their servers, then fetching
    the resulting files via direct HTTP download from AllDebrid's CDN.

    API documentation: https://docs.alldebrid.com/
    """

    protocol = "torrent"
    name = "alldebrid"

    _downloads: ClassVar[dict[str, _DownloadState]] = {}
    _downloads_lock = threading.Lock()

    def __init__(self) -> None:
        self._api_key = config_text(config.get("ALLDEBRID_API_KEY", ""))

    def _auth_headers(self) -> dict[str, str]:
        """Return Authorization header dict for API requests."""
        return {"Authorization": f"Bearer {self._api_key}"}

    # ------------------------------------------------------------------
    # DownloadClient interface
    # ------------------------------------------------------------------

    @staticmethod
    def is_configured() -> bool:
        """Return True when AllDebrid is selected and an API key exists."""
        client = config_text(config.get("PROWLARR_TORRENT_CLIENT", ""))
        api_key = config_text(config.get("ALLDEBRID_API_KEY", ""))
        return client == "alldebrid" and bool(api_key)

    def test_connection(self) -> tuple[bool, str]:
        """Validate the API key and check Premium subscription status."""
        if not self._api_key:
            return False, "AllDebrid API Key is required"
        try:
            url = f"{_API_BASE}/user"
            resp = _session.get(
                url,
                headers=self._auth_headers(),
                timeout=_STATUS_TIMEOUT,
                verify=get_ssl_verify(url),
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("status") != "success":
                err = data.get("error", {}).get("message", "API error")
                return False, f"AllDebrid error: {err}"
            user = data.get("data", {}).get("user", {})
            username = user.get("username", "Unknown")
            if not user.get("isPremium", False):
                return (
                    False,
                    f"AllDebrid user '{username}' does not have a Premium subscription",
                )
        except _ALLDEBRID_CLIENT_ERRORS as e:
            return False, f"Connection failed: {e}"
        else:
            return True, f"Connected to AllDebrid as '{username}' (Premium)"

    def add_download(
        self,
        url: str,
        name: str,
        category: str | None = None,
        expected_hash: str | None = None,
        **kwargs: object,
    ) -> str:
        """Send a torrent to AllDebrid and return the magnet ID.

        Accepts a magnet link, a .torrent URL, or an indexer proxy URL; anything
        that is not already a magnet is resolved first, since an HTTP URL posted
        as a magnet is rejected rather than downloaded (#1250).
        """
        if not self._api_key:
            msg = "AllDebrid API key is not configured"
            raise RuntimeError(msg)

        try:
            upload = resolve_debrid_upload(url, expected_hash=expected_hash)
            info = self._send_torrent(upload)

            magnet_id = str(info.get("id", ""))
            if not magnet_id:
                msg = "No magnet ID returned from AllDebrid"
                _raise_runtime_error(msg)

            target_dir = TMP_DIR / f"alldebrid_{magnet_id}"
            target_dir.mkdir(parents=True, exist_ok=True)

            state = _DownloadState(
                magnet_id=magnet_id,
                name=name,
                target_dir=target_dir,
                phase="waiting_ad",
            )
            with self._downloads_lock:
                self._downloads[magnet_id] = state

            logger.info(
                "Added torrent to AllDebrid: ID %s (%s)",
                magnet_id,
                name,
            )

        except Exception:
            logger.exception("Failed to add torrent to AllDebrid")
            raise

        else:
            return magnet_id

    def _send_torrent(self, upload: DebridUpload) -> dict[str, Any]:
        """Hand the torrent to AllDebrid, as a magnet or as a file upload.

        Both endpoints answer with the same envelope and the same per-entry
        error shape, differing only in which key holds the entries.
        """
        if isinstance(upload, DebridMagnet):
            api_url = f"{_API_BASE}/magnet/upload"
            entries_key = "magnets"
            resp = _session.post(
                api_url,
                headers=self._auth_headers(),
                data={"magnets[]": upload.magnet_url},
                timeout=_API_TIMEOUT,
                verify=get_ssl_verify(api_url),
            )
        else:
            api_url = f"{_API_BASE}/magnet/upload/file"
            entries_key = "files"
            resp = _session.post(
                api_url,
                headers=self._auth_headers(),
                files={
                    "files[]": (
                        "release.torrent",
                        upload.torrent_data,
                        "application/x-bittorrent",
                    )
                },
                timeout=_API_TIMEOUT,
                verify=get_ssl_verify(api_url),
            )

        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "success":
            code = data.get("error", {}).get("code", "UNKNOWN")
            msg = f"AllDebrid upload failed: {code}"
            _raise_runtime_error(msg)

        entries = data.get("data", {}).get(entries_key, [])
        if not entries:
            msg = "AllDebrid accepted the upload but returned no torrent"
            _raise_runtime_error(msg)

        info = entries[0]
        if info.get("error"):
            code = info["error"].get("code", "UNKNOWN")
            msg = f"AllDebrid rejected the torrent: {code}"
            _raise_runtime_error(msg)

        return info

    def get_status(self, download_id: str) -> DownloadStatus:
        """Poll AllDebrid for magnet status and drive the download."""
        state = self._ensure_state(download_id)

        # Return cached terminal / in-flight states immediately.
        with state.lock:
            if state.phase == "error":
                return DownloadStatus.error(
                    state.error_message or "AllDebrid error",
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

        # Ask AllDebrid for the current magnet status.
        try:
            status_url = f"{_API_BASE.replace('/v4', '/v4.1')}/magnet/status"
            resp = _session.post(
                status_url,
                headers=self._auth_headers(),
                data={"id": download_id},
                timeout=_STATUS_TIMEOUT,
                verify=get_ssl_verify(status_url),
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("status") != "success":
                err = data.get("error", {}).get("message", "Status failed")
                return DownloadStatus.error(
                    f"AllDebrid status error: {err}",
                )

            mag = self._extract_magnet_info(data)
            return self._handle_magnet_status(mag, state)

        except Exception as e:
            logger.exception(
                "Error checking AllDebrid status for %s",
                download_id,
            )
            return DownloadStatus.error(str(e))

    def remove(
        self,
        download_id: str,
        *,
        delete_files: bool = False,
    ) -> bool:
        """Delete the magnet from AllDebrid and clean up local files."""
        try:
            url = f"{_API_BASE}/magnet/delete"
            _session.post(
                url,
                headers=self._auth_headers(),
                data={"id": download_id},
                timeout=_STATUS_TIMEOUT,
                verify=get_ssl_verify(url),
            )
        except _ALLDEBRID_CLIENT_ERRORS as e:
            logger.warning("Failed to delete magnet from AllDebrid: %s", e)

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
        target_dir = TMP_DIR / f"alldebrid_{download_id}"
        if target_dir.exists():
            return str(target_dir)
        return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_state(self, download_id: str) -> _DownloadState:
        """Get or create download state for the given magnet ID."""
        with self._downloads_lock:
            state = self._downloads.get(download_id)
        if state:
            return state

        target_dir = TMP_DIR / f"alldebrid_{download_id}"
        state = _DownloadState(
            magnet_id=download_id,
            name=f"Download {download_id}",
            target_dir=target_dir,
            phase="waiting_ad",
        )
        with self._downloads_lock:
            self._downloads[download_id] = state
        return state

    @staticmethod
    def _extract_magnet_info(data: dict[str, Any]) -> dict[str, Any]:
        """Extract magnet info dict from a status API response."""
        mag_data = data.get("data", {}).get("magnets", {})
        if isinstance(mag_data, list) and mag_data:
            return mag_data[0]
        if isinstance(mag_data, dict):
            return mag_data
        return {}

    def _handle_magnet_status(
        self,
        mag: dict[str, Any],
        state: _DownloadState,
    ) -> DownloadStatus:
        """Map AllDebrid magnet status to a DownloadStatus."""
        status_code = mag.get("statusCode")

        if status_code in _STATUS_DOWNLOADING:
            size = mag.get("size", 0)
            downloaded = mag.get("downloaded", 0)
            pct = (downloaded / size * 100.0) if size > 0 else 0.0
            return DownloadStatus(
                progress=pct * 0.5,
                state=DownloadState.DOWNLOADING,
                message=(f"AllDebrid downloading torrent ({mag.get('filename', state.name)})"),
                complete=False,
                file_path=None,
                download_speed=mag.get("downloadSpeed", 0),
            )

        if status_code == _STATUS_READY or mag.get("ready", False):
            self._maybe_start_download_thread(state)
            return DownloadStatus(
                progress=50.0,
                state=DownloadState.DOWNLOADING,
                message="AllDebrid ready, retrieving files...",
                complete=False,
                file_path=None,
            )

        # Terminal error from AllDebrid.
        error_txt = mag.get("error", {}).get("message") or f"AllDebrid status code {status_code}"
        with state.lock:
            state.phase = "error"
            state.error_message = error_txt
        return DownloadStatus.error(error_txt)

    def _maybe_start_download_thread(self, state: _DownloadState) -> None:
        """Spawn a background thread to unlock and download files."""
        with state.lock:
            already_running = state.phase in (
                "unlocking",
                "downloading_http",
                "complete",
            )
            thread_alive = state.download_thread is not None and state.download_thread.is_alive()
            if already_running or thread_alive:
                return
            state.phase = "unlocking"
            t = threading.Thread(
                target=self._process_and_download,
                args=(state,),
                daemon=True,
            )
            state.download_thread = t
            t.start()

    # ------------------------------------------------------------------
    # Link unlocking
    # ------------------------------------------------------------------

    def _unlock_file_link(self, link: str) -> str:
        """Resolve an AllDebrid file link to a direct CDN download URL.

        AllDebrid's ``/v4/magnet/files`` endpoint returns virtual links
        (``alldebrid.com/f/...``) that must be converted to direct CDN
        URLs via ``/v4/link/unlock``.

        Strategy:
            1. If the link is already a CDN URL (``/dl/``), return it.
            2. ``POST /v4/link/unlock`` with Bearer auth (primary).
            3. ``GET /v4/link/unlock`` with query parameters (fallback).
            4. Append ``apikey=`` to ``alldebrid.com/f/`` links
               (last-resort fallback for ghost-cached torrents).

        """
        # 1. Already a direct CDN link.
        if "/dl/" in link:
            return link

        headers = self._auth_headers()
        unlock_url = f"{_API_BASE}/link/unlock"
        err_msg = "Unknown unlock error"

        # 2. POST unlock (primary method).
        try:
            resp = _session.post(
                unlock_url,
                headers=headers,
                data={"link": link},
                timeout=_API_TIMEOUT,
                verify=get_ssl_verify(unlock_url),
            )
            if resp.status_code == 200:
                body = resp.json()
                if body.get("status") == "success":
                    direct = self._resolve_unlock_data(
                        body.get("data", {}),
                        headers,
                    )
                    if direct:
                        return direct
                err_msg = body.get("error", {}).get(
                    "message",
                    "Unlock failed",
                )
        except _ALLDEBRID_CLIENT_ERRORS as e:
            logger.debug("POST unlock exception: %s", e)

        # 3. GET unlock fallback with URL-encoded link.
        try:
            encoded = quote(link, safe="")
            get_url = (
                f"{_API_BASE}/link/unlock?agent={_AGENT}&apikey={self._api_key}&link={encoded}"
            )
            resp = _session.get(
                get_url,
                headers=headers,
                timeout=_API_TIMEOUT,
                verify=get_ssl_verify(get_url),
            )
            if resp.status_code == 200:
                body = resp.json()
                if body.get("status") == "success":
                    direct = body.get("data", {}).get("link")
                    if direct:
                        return direct
                err_msg = body.get("error", {}).get("message", err_msg)
        except _ALLDEBRID_CLIENT_ERRORS as e:
            # The request URL carries the API key and requests puts it in the message.
            logger.debug("GET unlock exception: %s", type(e).__name__)

        # 4. Last-resort: append apikey to alldebrid.com/f/ links.
        if "alldebrid.com/f/" in link:
            logger.info(
                "Using apikey fallback for AllDebrid file link: %s",
                _safe_url(link),
            )
            if "apikey=" not in link:
                sep = "&" if "?" in link else "?"
                return f"{link}{sep}apikey={self._api_key}"
            return link

        logger.error(
            "AllDebrid unlock failed for '%s': %s",
            _safe_url(link),
            err_msg,
        )
        msg = f"AllDebrid unlock failed: {err_msg}"
        raise RuntimeError(msg)

    def _resolve_unlock_data(
        self,
        data: dict[str, Any],
        headers: dict[str, str],
    ) -> str | None:
        """Extract the direct link from unlock response data.

        Handles the *delayed link* flow where AllDebrid returns a
        ``delayed`` ID instead of an immediate download link.
        """
        # Delayed link: poll until the CDN file is ready.
        if "delayed" in data:
            delayed_id = data["delayed"]
            logger.info(
                "AllDebrid link delayed (ID %s), polling...",
                delayed_id,
            )
            delayed_url = f"{_API_BASE}/link/delayed"
            for _ in range(_DELAYED_POLL_MAX_ATTEMPTS):
                time.sleep(_DELAYED_POLL_INTERVAL)
                try:
                    resp = _session.post(
                        delayed_url,
                        headers=headers,
                        data={"id": delayed_id},
                        timeout=_STATUS_TIMEOUT,
                        verify=get_ssl_verify(delayed_url),
                    )
                    if resp.status_code != 200:
                        continue
                    body = resp.json()
                    d = body.get("data", {})
                    if body.get("status") == "success" and d.get("status") == 2 and d.get("link"):
                        return d["link"]
                except _ALLDEBRID_CLIENT_ERRORS as e:
                    logger.debug("Delayed poll exception: %s", e)

        return data.get("link")

    # ------------------------------------------------------------------
    # File download pipeline
    # ------------------------------------------------------------------

    def _process_and_download(self, state: _DownloadState) -> None:
        """Fetch the file list, unlock links, and download via HTTP.

        Runs in a background thread spawned by ``_maybe_start_download_thread``.
        """
        try:
            files = self._fetch_file_list(state.magnet_id)
            relevant = [f for f in files if f["filename"].lower().endswith(_BOOK_EXTENSIONS)]
            if not relevant:
                relevant = files

            with state.lock:
                state.phase = "downloading_http"

            total = len(relevant)
            for idx, file_info in enumerate(relevant):
                direct_link = self._unlock_file_link(file_info["link"])

                rel_path = Path(file_info["filename"])
                dest = state.target_dir / rel_path
                dest.parent.mkdir(parents=True, exist_ok=True)

                logger.info(
                    "Downloading AllDebrid file %d/%d: %s",
                    idx + 1,
                    total,
                    rel_path,
                )

                buf = download_url(
                    direct_link,
                    referer="https://alldebrid.com/",
                )
                if not buf:
                    msg = f"Failed to download from {_safe_url(direct_link)}"
                    _raise_runtime_error(msg)

                with dest.open("wb") as fh:
                    fh.write(buf.getvalue())

                with state.lock:
                    state.progress = 50.0 + (idx + 1) / total * 50.0

            with state.lock:
                state.phase = "complete"
                state.progress = 100.0

            logger.info(
                "AllDebrid download complete for ID %s at %s",
                state.magnet_id,
                state.target_dir,
            )

        except Exception:
            logger.exception(
                "Error in AllDebrid download for ID %s",
                state.magnet_id,
            )
            with state.lock:
                state.phase = "error"
                state.error_message = str(
                    state.error_message or "Download failed",
                )

    def _fetch_file_list(
        self,
        magnet_id: str,
    ) -> list[dict[str, Any]]:
        """Retrieve and flatten the file tree for a magnet."""
        url = f"{_API_BASE}/magnet/files"
        resp = _session.post(
            url,
            headers=self._auth_headers(),
            data={"id[]": magnet_id},
            timeout=_API_TIMEOUT,
            verify=get_ssl_verify(url),
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "success":
            msg = f"Failed to list magnet files: {data.get('error')}"
            raise RuntimeError(msg)

        magnets = data.get("data", {}).get("magnets", [])
        if not magnets:
            msg = "No magnet files returned"
            raise RuntimeError(msg)

        files = _flatten_magnet_files(magnets[0].get("files", []))
        if not files:
            msg = "No files found in torrent"
            raise RuntimeError(msg)
        return files
