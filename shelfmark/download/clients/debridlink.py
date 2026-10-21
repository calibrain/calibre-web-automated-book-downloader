"""Debrid-Link debrid service client for Shelfmark.

Routes magnet links through the Debrid-Link REST API (v2) to download torrent
content via Debrid-Link's seedbox infrastructure.

Unlike the other debrid services, a completed Debrid-Link torrent already
carries a direct ``downloadUrl`` on every file, so there is no per-file
unrestrict or link-request round trip.

Everything here follows the documented v2 fields only: ``status``,
``downloadPercent``, ``wait``, ``isZip``, ``srvMaint`` and the per-file
``downloadPercent``. Values of ``status`` outside the documented set (the docs'
own example shows 6) are tolerated and fall back to ``downloadPercent``.
"""

from __future__ import annotations

import shutil
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, NoReturn

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
    safe_relative_path,
)
from shelfmark.download.http import download_url
from shelfmark.download.network import get_ssl_verify

if TYPE_CHECKING:
    from pathlib import Path

logger = setup_logger(__name__)

_API_BASE = "https://debrid-link.com/api/v2"

_DEBRIDLINK_CLIENT_ERRORS = (
    AttributeError,
    OSError,
    requests.exceptions.RequestException,
    RuntimeError,
    TypeError,
    ValueError,
)

# Timeouts for API calls.
_API_TIMEOUT = 30
_STATUS_TIMEOUT = 15

# A torrent or file reports 0-100 in downloadPercent; 100 means it is ready.
_COMPLETE_PERCENT = 100

# Documented torrent status values (seedbox-list): 0 paused, 1 queued,
# 2 verification, 4 downloading, 8 seeding, 100 finished.
_STATUS_PAUSED = 0
_STATUS_QUEUED = 1
_STATUS_VERIFYING = 2
_STATUS_SEEDING = 8
_STATUS_FINISHED = 100
_DONE_STATUSES = frozenset({_STATUS_SEEDING, _STATUS_FINISHED})

# The download handler polls every couple of seconds. Debrid-Link rate-limits per
# endpoint and answers a breach with floodDetected, so the seedbox is asked at most
# this often per torrent and cached status is served in between.
_MIN_REFRESH_SECONDS = 15.0

# floodDetected only says to retry later; the docs give no duration. Status checks
# back off for a minute, doubling while the flood lasts. Each new back-off changes the
# status message, and the cap keeps the next check inside the orchestrator's
# five-minute stall window, so waiting out a flood doesn't get a download cancelled.
_FLOOD_BACKOFF_SECONDS = 60.0
_FLOOD_BACKOFF_MAX_SECONDS = 240.0

# Errors Debrid-Link documents as temporary. They, network failures and 5xx
# responses are retried until the torrent has been failing for this long.
_TRANSIENT_ERROR_CODES = frozenset({"internalError", "server_error", "freeServerOverload"})
_TRANSIENT_GRACE_SECONDS = 600.0

# How long remove() waits for the retrieval thread to notice a cancel.
_WORKER_JOIN_TIMEOUT = 10.0

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
    # A zipped book or audiobook; the post-processing step extracts it.
    ".zip",
)


class DebridLinkAPIError(RuntimeError):
    """An error Debrid-Link reported, carrying its documented error code."""

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


def _raise_runtime_error(message: str) -> NoReturn:
    raise RuntimeError(message)


@dataclass
class _DownloadState:
    """Internal mutable state for an in-progress Debrid-Link download."""

    torrent_id: str
    name: str
    target_dir: Path
    phase: str = "uploading"
    error_message: str | None = None
    progress: float = 0.0
    download_thread: threading.Thread | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)
    cancel_event: threading.Event = field(default_factory=threading.Event)
    # Throttling: the last seedbox record and when it was fetched.
    last_info: dict[str, Any] | None = None
    last_fetch_at: float = 0.0
    # Transient-failure tracking: when the current run of failures started.
    failing_since: float | None = None
    # Set once a torrent held for file selection (``wait``) has been told to start.
    start_requested: bool = False


@register_client("torrent")
class DebridLinkClient(DownloadClient):
    """Debrid-Link debrid service client.

    Downloads torrent content by handing a magnet to Debrid-Link's seedbox,
    polling until it holds every file, then fetching each file over HTTP from
    the ``downloadUrl`` the seedbox already returned.

    API documentation: https://debrid-link.com/api_doc/v2/introduction
    """

    protocol = "torrent"
    name = "debridlink"

    _downloads: ClassVar[dict[str, _DownloadState]] = {}
    _downloads_lock = threading.Lock()
    # A flood on the status endpoint applies to every torrent on the account, so the
    # status back-off is shared.
    _flood_until: ClassVar[float] = 0.0
    _flood_backoff: ClassVar[float] = 0.0

    def __init__(self) -> None:
        self._api_key = config_text(config.get("DEBRIDLINK_API_KEY", ""))

    def _auth_headers(self) -> dict[str, str]:
        """Return Authorization header dict for API requests."""
        return {"Authorization": f"Bearer {self._api_key}"}

    # ------------------------------------------------------------------
    # DownloadClient interface
    # ------------------------------------------------------------------

    @staticmethod
    def is_configured() -> bool:
        """Return True when Debrid-Link is selected and an API key exists."""
        client = config_text(config.get("PROWLARR_TORRENT_CLIENT", ""))
        api_key = config_text(config.get("DEBRIDLINK_API_KEY", ""))
        return client == "debridlink" and bool(api_key)

    def test_connection(self) -> tuple[bool, str]:
        """Validate the API key and check the account still has premium time."""
        if not self._api_key:
            return False, "Debrid-Link API Key is required"
        try:
            account = self._request_value(
                "GET", "/account/infos", operation="account check", timeout=_STATUS_TIMEOUT
            )
            if not isinstance(account, dict):
                return False, "Unexpected response from Debrid-Link account endpoint"
            username = str(account.get("username") or account.get("email") or "Unknown")
            # premiumLeft is seconds of premium remaining; 0 means the account expired.
            if not account.get("premiumLeft"):
                return (
                    False,
                    f"Debrid-Link user '{username}' does not have an active Premium subscription",
                )
        except _DEBRIDLINK_CLIENT_ERRORS as e:
            return False, f"Connection failed: {e}"
        else:
            return True, f"Connected to Debrid-Link as '{username}' (Premium)"

    def add_download(
        self,
        url: str,
        name: str,
        category: str | None = None,
        expected_hash: str | None = None,
        **kwargs: object,
    ) -> str:
        """Send a torrent to Debrid-Link's seedbox.

        Accepts a magnet link, a .torrent URL, or an indexer proxy URL. Anything
        that is not already a magnet is resolved first, then posted as a file,
        because the seedbox endpoint takes a magnet or hash in ``url`` and a
        .torrent only as multipart form data.
        """
        if not self._api_key:
            msg = "Debrid-Link API key is not configured"
            raise RuntimeError(msg)

        try:
            upload = resolve_debrid_upload(url, expected_hash=expected_hash)
            data = self._send_torrent(upload)

            torrent_id = str(data.get("id", ""))
            if not torrent_id:
                msg = "No torrent ID returned from Debrid-Link"
                _raise_runtime_error(msg)

            target_dir = TMP_DIR / f"debridlink_{torrent_id}"
            target_dir.mkdir(parents=True, exist_ok=True)

            state = _DownloadState(
                torrent_id=torrent_id,
                name=name,
                target_dir=target_dir,
                phase="waiting_dl",
            )
            with self._downloads_lock:
                self._downloads[torrent_id] = state

            logger.info(
                "Added torrent to Debrid-Link: ID %s (%s)",
                torrent_id,
                name,
            )

        except Exception:
            logger.exception("Failed to add torrent to Debrid-Link")
            raise

        else:
            return torrent_id

    def _send_torrent(self, upload: DebridUpload) -> dict[str, Any]:
        """Hand the torrent to the seedbox, as a magnet or as a file upload."""
        if isinstance(upload, DebridMagnet):
            data = self._request_value(
                "POST",
                "/seedbox/add",
                operation="torrent upload",
                json={"url": upload.magnet_url},
                timeout=_API_TIMEOUT,
            )
        else:
            # A .torrent must go as multipart/form-data under the "file" field;
            # the JSON body only accepts a magnet, a hash or a torrent URL.
            data = self._request_value(
                "POST",
                "/seedbox/add",
                operation="torrent upload",
                files={"file": ("upload.torrent", upload.torrent_data, "application/x-bittorrent")},
                timeout=_API_TIMEOUT,
            )

        if not isinstance(data, dict):
            msg = "Unexpected response when adding a torrent to Debrid-Link"
            _raise_runtime_error(msg)
        return data

    def get_status(self, download_id: str) -> DownloadStatus:
        """Poll Debrid-Link for torrent status and drive the download."""
        state = self._ensure_state(download_id)

        # Return cached terminal / in-flight states immediately.
        with state.lock:
            if state.phase == "error":
                return DownloadStatus.error(state.error_message or "Debrid-Link error")
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
            cached = state.last_info
            fresh = time.monotonic() - state.last_fetch_at < _MIN_REFRESH_SECONDS

        now = time.time()
        if now < type(self)._flood_until:
            resume = time.strftime("%H:%M", time.localtime(type(self)._flood_until))
            return self._waiting_status(
                cached, f"Debrid-Link rate limit reached, retrying at {resume}"
            )
        if fresh and cached is not None:
            return self._handle_torrent_info(cached, state)

        try:
            torrent = self._fetch_torrent(download_id)
        except _DEBRIDLINK_CLIENT_ERRORS as e:
            return self._handle_fetch_failure(e, state)

        type(self)._flood_backoff = 0.0
        with state.lock:
            state.failing_since = None
            state.last_fetch_at = time.monotonic()
            state.last_info = torrent

        if torrent is None:
            return self._set_error(state, f"Torrent {download_id} is no longer on Debrid-Link")
        return self._handle_torrent_info(torrent, state)

    def remove(
        self,
        download_id: str,
        *,
        delete_files: bool = False,
    ) -> bool:
        """Stop any retrieval, delete the torrent from Debrid-Link and clean up locally."""
        remote_removed = True
        try:
            self._request_value(
                "DELETE",
                f"/seedbox/{download_id}/remove",
                operation="torrent deletion",
                timeout=_STATUS_TIMEOUT,
            )
        except _DEBRIDLINK_CLIENT_ERRORS as e:
            remote_removed = False
            logger.warning("Failed to delete torrent from Debrid-Link: %s", e)

        with self._downloads_lock:
            state = self._downloads.get(download_id)
        if state:
            with state.lock:
                state.cancel_event.set()
            thread = state.download_thread
            if thread and thread is not threading.current_thread():
                thread.join(_WORKER_JOIN_TIMEOUT)
                if thread.is_alive():
                    logger.warning(
                        "Debrid-Link retrieval thread did not stop; deferring cleanup of %s",
                        download_id,
                    )
                    return False
            with self._downloads_lock:
                self._downloads.pop(download_id, None)
        target_dir = state.target_dir if state else TMP_DIR / f"debridlink_{download_id}"

        local_removed = True
        if target_dir.exists():
            try:
                shutil.rmtree(target_dir)
            except OSError:
                local_removed = False
                logger.warning("Failed to remove Debrid-Link temporary files for %s", download_id)
        return remote_removed and local_removed

    def get_download_path(self, download_id: str) -> str | None:
        """Return the local directory containing downloaded files."""
        with self._downloads_lock:
            state = self._downloads.get(download_id)
        if state and state.phase == "complete":
            return str(state.target_dir)
        target_dir = TMP_DIR / f"debridlink_{download_id}"
        if target_dir.exists():
            return str(target_dir)
        return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _request_value(
        self,
        method: str,
        path: str,
        *,
        operation: str,
        timeout: int = _API_TIMEOUT,
        **kwargs: Any,
    ) -> Any:
        """Call the API and unwrap Debrid-Link's ``{success, value}`` envelope.

        Errors arrive as a 4xx or 5xx with ``{success: false, error: <code>}``, so
        the body is read before the status code: raising on the status alone would
        turn ``badToken`` into a bare "401 Client Error". Messages never include the
        request URL or its parameters.
        """
        url = f"{_API_BASE}{path}"
        try:
            resp = requests.request(
                method,
                url,
                headers=self._auth_headers(),
                timeout=timeout,
                verify=get_ssl_verify(url),
                **kwargs,
            )
        except requests.exceptions.RequestException as e:
            msg = f"Debrid-Link {operation} failed: {type(e).__name__}"
            raise RuntimeError(msg) from None

        status_code = getattr(resp, "status_code", 200)
        ok_status = isinstance(status_code, int) and 200 <= status_code < 300
        try:
            payload = resp.json()
        except TypeError, ValueError:
            payload = None

        if not isinstance(payload, dict):
            detail = "an invalid response" if ok_status else f"HTTP {status_code}"
            msg = f"Debrid-Link {operation} failed: {detail}"
            raise DebridLinkAPIError(msg, None if ok_status else f"http{status_code}")

        if not ok_status or payload.get("success") is not True:
            # The documented error body is just {success: false, error: <code>}.
            code = payload.get("error")
            code = code if isinstance(code, str) and code else None
            detail = code or ("an unsuccessful response" if ok_status else f"HTTP {status_code}")
            msg = f"Debrid-Link {operation} failed: {detail}"
            raise DebridLinkAPIError(msg, code or (None if ok_status else f"http{status_code}"))

        return payload.get("value")

    def _fetch_torrent(self, download_id: str) -> dict[str, Any] | None:
        """Return the seedbox record for one torrent, or None when it is gone."""
        try:
            value = self._request_value(
                "GET",
                "/seedbox/list",
                operation="status check",
                params={"ids": download_id},
                timeout=_STATUS_TIMEOUT,
            )
        except DebridLinkAPIError as e:
            if e.code == "badId":
                return None
            raise
        return self._pick_torrent(value, download_id)

    def _fetch_file_list(self, download_id: str) -> list[dict[str, Any]]:
        """Return the torrent's individual files.

        The plain list shows a torrent with many files as one ZIP (``isZip``);
        asking for it by ``ids``, as ``_fetch_torrent`` does, lists every file.
        Fetched once, after the torrent is complete, so the list is final rather
        than the partial one an early status check can return.
        """
        torrent = self._fetch_torrent(download_id)
        if torrent is None:
            msg = f"Torrent {download_id} is no longer on Debrid-Link"
            _raise_runtime_error(msg)
        return [f for f in torrent.get("files", []) if isinstance(f, dict)]

    @staticmethod
    def _pick_torrent(value: object, download_id: str) -> dict[str, Any] | None:
        entries = value if isinstance(value, list) else [value]
        for entry in entries:
            if isinstance(entry, dict) and str(entry.get("id", "")) == download_id:
                return entry
        return None

    def _handle_fetch_failure(self, error: Exception, state: _DownloadState) -> DownloadStatus:
        """Fail on a permanent error; ride out a temporary one for a grace period."""
        code = getattr(error, "code", None)
        if code == "floodDetected":
            resume = self._start_flood_backoff()
            logger.warning("Debrid-Link rate limit reached; pausing status checks until %s", resume)
            return self._waiting_status(
                state.last_info, f"Debrid-Link rate limit reached, retrying at {resume}"
            )

        transient = (
            not isinstance(error, DebridLinkAPIError)
            or code in _TRANSIENT_ERROR_CODES
            or (isinstance(code, str) and code.startswith("http5"))
        )
        if not transient:
            return self._set_error(state, str(error))

        now = time.monotonic()
        with state.lock:
            if state.failing_since is None:
                state.failing_since = now
            failing_for = now - state.failing_since
            state.last_fetch_at = now
        if failing_for >= _TRANSIENT_GRACE_SECONDS:
            minutes = int(_TRANSIENT_GRACE_SECONDS // 60)
            return self._set_error(state, f"{error} (still failing after {minutes} minutes)")
        logger.warning("Debrid-Link status check failed, will retry: %s", error)
        return self._waiting_status(state.last_info, "Debrid-Link unreachable, retrying")

    @classmethod
    def _start_flood_backoff(cls) -> str:
        """Pause status checks after floodDetected and return the resume time as HH:MM."""
        cls._flood_backoff = min(
            max(cls._flood_backoff * 2, _FLOOD_BACKOFF_SECONDS), _FLOOD_BACKOFF_MAX_SECONDS
        )
        cls._flood_until = time.time() + cls._flood_backoff
        return time.strftime("%H:%M", time.localtime(cls._flood_until))

    @staticmethod
    def _waiting_status(info: dict[str, Any] | None, message: str) -> DownloadStatus:
        percent = _as_float(info.get("downloadPercent")) if info else 0.0
        return DownloadStatus(
            progress=min(percent, _COMPLETE_PERCENT) * 0.5,
            state=DownloadState.DOWNLOADING,
            message=message,
            complete=False,
            file_path=None,
        )

    @staticmethod
    def _set_error(state: _DownloadState, message: str) -> DownloadStatus:
        with state.lock:
            state.phase = "error"
            state.error_message = message
        return DownloadStatus.error(message)

    def _ensure_state(self, download_id: str) -> _DownloadState:
        """Get or create download state for the given torrent ID."""
        with self._downloads_lock:
            state = self._downloads.get(download_id)
        if state:
            return state

        target_dir = TMP_DIR / f"debridlink_{download_id}"
        state = _DownloadState(
            torrent_id=download_id,
            name=f"Download {download_id}",
            target_dir=target_dir,
            phase="waiting_dl",
        )
        with self._downloads_lock:
            self._downloads[download_id] = state
        return state

    def _handle_torrent_info(self, info: dict[str, Any], state: _DownloadState) -> DownloadStatus:
        """Map a Debrid-Link seedbox torrent to a DownloadStatus.

        The API documents no error status for a torrent, so a dead one is left to the
        orchestrator's stall timer, as with the other torrent clients.
        """
        percent = _as_float(info.get("downloadPercent"))
        name = str(info.get("name") or state.name)
        files = [f for f in info.get("files", []) if isinstance(f, dict)]
        status = _as_status(info.get("status"))

        ready = (
            (status in _DONE_STATUSES or percent >= _COMPLETE_PERCENT)
            and bool(files)
            and all(_as_float(f.get("downloadPercent", 100)) >= _COMPLETE_PERCENT for f in files)
        )
        if ready:
            self._maybe_start_download_thread(state, files)
            return DownloadStatus(
                progress=50.0,
                state=DownloadState.DOWNLOADING,
                message="Debrid-Link ready, retrieving files...",
                complete=False,
                file_path=None,
            )

        download_state = DownloadState.DOWNLOADING
        if info.get("wait"):
            # Held for file selection. Shelfmark wants every file, so start it.
            self._start_waiting_torrent(state)
            message = f"Starting on Debrid-Link ({name})"
        elif info.get("srvMaint"):
            message = "Debrid-Link server maintenance, waiting"
        elif status == _STATUS_PAUSED:
            download_state = DownloadState.PAUSED
            message = f"Paused on Debrid-Link ({name})"
        elif status == _STATUS_QUEUED:
            # Reported as queued so the handler gives it the queue grace, not the
            # stall window for a torrent that should be moving.
            download_state = DownloadState.QUEUED
            message = f"Queued on Debrid-Link ({name})"
        elif status == _STATUS_VERIFYING:
            download_state = DownloadState.CHECKING
            message = f"Debrid-Link verifying torrent ({name})"
        else:
            message = f"Debrid-Link downloading torrent ({name})"

        # Still being fetched by the seedbox: the first half of the progress bar.
        return DownloadStatus(
            progress=min(percent, _COMPLETE_PERCENT) * 0.5,
            state=download_state,
            message=message,
            complete=False,
            file_path=None,
            download_speed=int(_as_float(info.get("downloadSpeed"))),
        )

    def _start_waiting_torrent(self, state: _DownloadState) -> None:
        """Release a torrent held for file selection, keeping every file.

        Shelfmark never asks for ``wait``, but a torrent reported that way would
        otherwise never start. The config endpoint with no unwanted files starts it.
        """
        with state.lock:
            if state.start_requested:
                return
            state.start_requested = True
        try:
            self._request_value(
                "POST",
                f"/seedbox/{state.torrent_id}/config",
                operation="torrent start",
                json={"files-unwanted": []},
                timeout=_STATUS_TIMEOUT,
            )
        except _DEBRIDLINK_CLIENT_ERRORS as e:
            logger.warning(
                "Could not start waiting Debrid-Link torrent %s: %s", state.torrent_id, e
            )
            with state.lock:
                state.start_requested = False

    def _maybe_start_download_thread(
        self, state: _DownloadState, files: list[dict[str, Any]] | None = None
    ) -> None:
        """Spawn a background thread to download the seedbox's files.

        ``files`` is the list the status check already fetched by ``ids``; passing it on
        saves the thread a second request that has none of the status path's flood and
        transient-error handling.
        """
        with state.lock:
            already_running = state.phase in ("downloading_http", "complete")
            thread_alive = state.download_thread is not None and state.download_thread.is_alive()
            if already_running or thread_alive or state.cancel_event.is_set():
                return
            state.phase = "downloading_http"
            # The seedbox half is done; without this the bar drops to 0% until the
            # first file arrives.
            state.progress = 50.0
            t = threading.Thread(
                target=self._process_and_download,
                args=(state, files),
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
        files: list[dict[str, Any]] | None = None,
    ) -> None:
        """Download each file over HTTP.

        Runs in a background thread spawned by ``_maybe_start_download_thread``.
        Debrid-Link puts a ready-to-use ``downloadUrl`` on every file, so unlike
        the other debrid clients there is nothing to unrestrict here. ``files`` is
        fetched fresh when not given.
        """
        try:
            if files is None:
                files = self._fetch_file_list(state.torrent_id)
            if state.cancel_event.is_set():
                return

            downloadable = [f for f in files if f.get("downloadUrl")]
            if not downloadable:
                msg = "No download links returned by Debrid-Link"
                _raise_runtime_error(msg)

            relevant = [
                f for f in downloadable if str(f.get("name", "")).lower().endswith(_BOOK_EXTENSIONS)
            ]
            if not relevant:
                relevant = downloadable

            state.target_dir.mkdir(parents=True, exist_ok=True)
            used: set[Path] = set()
            total = len(relevant)
            for idx, file_info in enumerate(relevant):
                if state.cancel_event.is_set():
                    return
                name = str(file_info.get("name") or f"file_{idx + 1}")
                rel_path = _unique_path(
                    safe_relative_path(name, state.target_dir, "Debrid-Link"), used
                )

                dest = state.target_dir / rel_path
                dest.parent.mkdir(parents=True, exist_ok=True)

                logger.info("Downloading Debrid-Link file %d/%d: %s", idx + 1, total, rel_path)

                buf = download_url(
                    str(file_info.get("downloadUrl", "")),
                    referer="https://debrid-link.com/",
                    cancel_flag=state.cancel_event,
                )
                if state.cancel_event.is_set():
                    return
                if not buf:
                    # The URL is signed and stays out of the message, which ends up
                    # in logs and the UI.
                    msg = f"Failed to download {rel_path} from Debrid-Link"
                    _raise_runtime_error(msg)

                with dest.open("wb") as fh:
                    buf.seek(0)
                    shutil.copyfileobj(buf, fh)

                with state.lock:
                    state.progress = 50.0 + (idx + 1) / total * 50.0

            with state.lock:
                if state.cancel_event.is_set():
                    return
                state.phase = "complete"
                state.progress = 100.0

            logger.info(
                "Debrid-Link download complete for ID %s at %s",
                state.torrent_id,
                state.target_dir,
            )

        except Exception as e:
            logger.exception("Error in Debrid-Link download for ID %s", state.torrent_id)
            with state.lock:
                state.phase = "error"
                state.error_message = str(e) or "Download failed"


def _unique_path(path: Path, used: set[Path]) -> Path:
    """Keep two files with the same name from overwriting each other."""
    candidate = path
    counter = 2
    while candidate in used:
        candidate = path.with_name(f"{path.stem} ({counter}){path.suffix}")
        counter += 1
    used.add(candidate)
    return candidate


def _as_status(value: object) -> int | None:
    """The torrent's numeric status, or None when it is missing or not a number."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value)
    return None


def _as_float(value: object) -> float:
    """Coerce an API numeric field to a float, treating anything odd as zero."""
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return 0.0
    return 0.0
