"""TorBox debrid service client for Shelfmark."""

from __future__ import annotations

import math
import shutil
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, NoReturn
from urllib.parse import urlparse

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

_API_BASE = "https://api.torbox.app/v1/api"
_API_TIMEOUT = 30
_STATUS_TIMEOUT = 15
_WORKER_JOIN_TIMEOUT = 5.0
# TorBox can report a torrent finished shortly before its files are present.
_UNAVAILABLE_GRACE_SECONDS = 120.0

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

_TERMINAL_STATES = frozenset({"error", "failed", "missingfiles", "dead"})
_PLAN_NAMES = {0: "Free", 1: "Essential", 2: "Pro", 3: "Standard"}


def _raise_runtime_error(message: str) -> NoReturn:
    raise RuntimeError(message)


@dataclass
class _DownloadState:
    """Internal mutable state for an in-progress TorBox download."""

    torrent_id: str
    name: str
    target_dir: Path
    phase: str = "waiting_torbox"
    error_message: str | None = None
    progress: float = 0.0
    unavailable_since: float | None = None
    download_thread: threading.Thread | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)


@register_client("torrent")
class TorBoxClient(DownloadClient):
    """Download torrent content through TorBox and its CDN."""

    protocol = "torrent"
    name = "torbox"
    prefers_torrent_file = True

    _downloads: ClassVar[dict[str, _DownloadState]] = {}
    _downloads_lock = threading.Lock()

    def __init__(self) -> None:
        self._api_key = config_text(config.get("TORBOX_API_KEY", ""))

    def _auth_headers(self) -> dict[str, str]:
        """Return the authorization headers used by TorBox API calls."""
        return {"Authorization": f"Bearer {self._api_key}"}

    @staticmethod
    def is_configured() -> bool:
        """Return True when TorBox is selected and an API key exists."""
        client = config_text(config.get("PROWLARR_TORRENT_CLIENT", ""))
        api_key = config_text(config.get("TORBOX_API_KEY", ""))
        return client == "torbox" and bool(api_key)

    def test_connection(self) -> tuple[bool, str]:
        """Validate the API key and report the connected TorBox plan."""
        if not self._api_key:
            return False, "TorBox API Key is required"

        try:
            user = self._request_data(
                "GET",
                "/user/me",
                operation="account lookup",
                params={"settings": "false"},
                timeout=_STATUS_TIMEOUT,
            )
            if not isinstance(user, dict):
                _raise_runtime_error("TorBox account lookup returned invalid user data")
        except (
            OSError,
            requests.exceptions.RequestException,
            RuntimeError,
            TypeError,
            ValueError,
        ) as e:
            return False, f"Connection failed: {e}"

        plan_value = user.get("plan")
        plan = _PLAN_NAMES.get(plan_value, "Unknown") if isinstance(plan_value, int) else "Unknown"
        email = user.get("email")
        account = f" as '{email}'" if isinstance(email, str) and email else ""
        return True, f"Connected to TorBox{account} ({plan} plan)"

    def add_download(
        self,
        url: str,
        name: str,
        category: str | None = None,
        expected_hash: str | None = None,
        **kwargs: Any,
    ) -> str:
        """Send a magnet or torrent file to TorBox and return its torrent ID."""
        if not self._api_key:
            _raise_runtime_error("TorBox API key is not configured")

        try:
            upload = resolve_debrid_upload(url, expected_hash=expected_hash)
            data = self._send_torrent(upload, name)
            torrent_id = self._normalize_torrent_id(data.get("torrent_id"))

            target_dir = TMP_DIR / f"torbox_{torrent_id}"
            target_dir.mkdir(parents=True, exist_ok=True)
            state = _DownloadState(torrent_id=torrent_id, name=name, target_dir=target_dir)
            with self._downloads_lock:
                self._downloads[torrent_id] = state

            logger.info(
                "Added torrent to TorBox: ID %s", torrent_id, extra={"torrent_id": torrent_id}
            )
        except Exception:
            logger.exception("Failed to add torrent to TorBox")
            raise
        else:
            return torrent_id

    def _send_torrent(self, upload: DebridUpload, name: str) -> dict[str, Any]:
        """Create a TorBox torrent from a magnet link or torrent file."""
        endpoint = "/torrents/createtorrent"
        data: dict[str, str] = {"name": name}
        files: dict[str, tuple[str, bytes, str]] | None = None
        if isinstance(upload, DebridMagnet):
            data["magnet"] = upload.magnet_url
        else:
            files = {
                "file": (
                    "release.torrent",
                    upload.torrent_data,
                    "application/x-bittorrent",
                )
            }

        result = self._request_data(
            "POST",
            endpoint,
            operation="torrent creation",
            data=data,
            files=files,
            timeout=_API_TIMEOUT,
        )
        if not isinstance(result, dict):
            _raise_runtime_error("TorBox torrent creation returned invalid data")
        return result

    def get_status(self, download_id: str) -> DownloadStatus:
        """Poll TorBox for torrent status and drive local file retrieval."""
        download_id = self._normalize_torrent_id(download_id)
        state = self._ensure_state(download_id)
        with state.lock:
            if state.phase == "error":
                return DownloadStatus.error(state.error_message or "TorBox download failed")
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
                    message="Downloading files via TorBox...",
                    complete=False,
                    file_path=None,
                )

        try:
            data = self._request_data(
                "GET",
                "/torrents/mylist",
                operation="torrent status lookup",
                params={"id": download_id, "bypass_cache": "true"},
                timeout=_STATUS_TIMEOUT,
            )
            torrent = self._extract_torrent(data, download_id)
            return self._handle_torrent_status(torrent, state)
        except Exception as e:
            logger.exception(
                "Failed to check TorBox torrent status",
                extra={"torrent_id": download_id},
            )
            return DownloadStatus.error(f"TorBox status check failed: {e}")

    def remove(self, download_id: str, *, delete_files: bool = False) -> bool:
        """Delete the remote torrent and clean up its local temporary directory."""
        download_id = self._normalize_torrent_id(download_id)
        remote_removed = True
        try:
            self._request_data(
                "POST",
                "/torrents/controltorrent",
                operation="torrent deletion",
                json={"torrent_id": int(download_id), "operation": "delete"},
                timeout=_STATUS_TIMEOUT,
                require_data=False,
            )
        except OSError, requests.exceptions.RequestException, RuntimeError, TypeError, ValueError:
            remote_removed = False
            logger.warning("Failed to delete TorBox torrent", extra={"torrent_id": download_id})

        with self._downloads_lock:
            state = self._downloads.get(download_id)
        if state:
            with state.lock:
                state.cancel_event.set()
            if state.download_thread and state.download_thread is not threading.current_thread():
                state.download_thread.join(_WORKER_JOIN_TIMEOUT)
                if state.download_thread.is_alive():
                    logger.warning(
                        "TorBox retrieval thread did not stop; deferring cleanup",
                        extra={"torrent_id": download_id},
                    )
                    return False
            with self._downloads_lock:
                state = self._downloads.pop(download_id, None)
        target_dir = state.target_dir if state else TMP_DIR / f"torbox_{download_id}"

        local_removed = True
        if target_dir.exists():
            try:
                shutil.rmtree(target_dir)
            except OSError:
                local_removed = False
                logger.warning(
                    "Failed to remove TorBox temporary files",
                    extra={"torrent_id": download_id},
                )
        return remote_removed and local_removed

    def get_download_path(self, download_id: str) -> str | None:
        """Return the local directory once TorBox files have been retrieved."""
        download_id = self._normalize_torrent_id(download_id)
        with self._downloads_lock:
            state = self._downloads.get(download_id)
        if state and state.phase == "complete":
            return str(state.target_dir)
        return None

    def _request_data(
        self,
        method: str,
        endpoint: str,
        *,
        operation: str,
        require_data: bool = True,
        **kwargs: object,
    ) -> Any:
        """Send a TorBox request and validate its JSON response envelope."""
        url = f"{_API_BASE}{endpoint}"
        request_kwargs: Any = {
            "headers": self._auth_headers(),
            "verify": get_ssl_verify(url),
            **kwargs,
        }
        request: Any = requests.get if method == "GET" else requests.post
        try:
            response = request(url, **request_kwargs)
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"TorBox {operation} failed: {type(e).__name__}") from None

        try:
            payload = response.json()
        except (AttributeError, TypeError, ValueError) as e:
            _raise_runtime_error(f"TorBox {operation} returned invalid JSON: {e}")

        if not isinstance(payload, dict):
            _raise_runtime_error(f"TorBox {operation} returned an invalid response")

        error = payload.get("error")
        detail = payload.get("detail")
        status_code = getattr(response, "status_code", 200)
        if not isinstance(status_code, int) or not 200 <= status_code < 300:
            message = detail if isinstance(detail, str) and detail else f"HTTP {status_code}"
            code = f" [{error}]" if isinstance(error, str) and error else ""
            _raise_runtime_error(f"TorBox {operation} failed{code}: {message}")
        if payload.get("success") is not True or error:
            message = detail if isinstance(detail, str) and detail else "Unknown TorBox error"
            code = f" [{error}]" if isinstance(error, str) and error else ""
            _raise_runtime_error(f"TorBox {operation} failed{code}: {message}")

        data = payload.get("data")
        if require_data and data is None:
            _raise_runtime_error(f"TorBox {operation} returned no data")
        return data

    def _ensure_state(self, download_id: str) -> _DownloadState:
        """Get or create download state for a TorBox torrent ID."""
        download_id = self._normalize_torrent_id(download_id)
        with self._downloads_lock:
            state = self._downloads.get(download_id)
            if state is None:
                state = _DownloadState(
                    torrent_id=download_id,
                    name=f"Download {download_id}",
                    target_dir=TMP_DIR / f"torbox_{download_id}",
                )
                self._downloads[download_id] = state
        return state

    @staticmethod
    def _normalize_torrent_id(value: object) -> str:
        """Return a canonical positive decimal TorBox torrent ID."""
        torrent_id = str(value) if value is not None else ""
        if not torrent_id.isascii() or not torrent_id.isdecimal():
            _raise_runtime_error("TorBox returned an invalid torrent ID")

        normalized = str(int(torrent_id))
        if normalized == "0":
            _raise_runtime_error("TorBox returned an invalid torrent ID")
        return normalized

    @staticmethod
    def _extract_torrent(data: Any, download_id: str) -> dict[str, Any]:
        """Extract the requested torrent from TorBox's object or list response."""
        if isinstance(data, dict):
            return data
        if isinstance(data, list):
            for torrent in data:
                if isinstance(torrent, dict) and str(torrent.get("id", "")) == download_id:
                    return torrent
        _raise_runtime_error(f"TorBox torrent {download_id} was not found")

    def _handle_torrent_status(
        self,
        torrent: dict[str, Any],
        state: _DownloadState,
    ) -> DownloadStatus:
        """Map a TorBox torrent object into Shelfmark download status."""
        remote_state = str(torrent.get("download_state", "unknown"))
        normalized_state = remote_state.lower()
        if normalized_state in _TERMINAL_STATES:
            message = torrent.get("tracker_message") or f"TorBox status error: {remote_state}"
            self._set_error(state, str(message))
            return DownloadStatus.error(str(message))

        finished = torrent.get("download_finished") is True
        present = torrent.get("download_present") is True
        if finished and not present:
            if self._unavailable_grace_expired(state):
                message = "TorBox finished processing but the download is unavailable"
                self._set_error(state, message)
                return DownloadStatus.error(message)
        elif finished and present:
            files = torrent.get("files")
            if not isinstance(files, list):
                message = "TorBox returned no file list for a completed torrent"
                self._set_error(state, message)
                return DownloadStatus.error(message)
            self._maybe_start_download_thread(state, files)
            return DownloadStatus(
                progress=50.0,
                state=DownloadState.DOWNLOADING,
                message="TorBox ready, retrieving files...",
                complete=False,
                file_path=None,
            )

        progress = self._normalize_remote_progress(torrent.get("progress")) * 0.5
        speed = self._integer_value(torrent.get("download_speed"))
        eta = self._integer_value(torrent.get("eta"))
        name = torrent.get("name") or state.name
        return DownloadStatus(
            progress=progress,
            state=DownloadState.DOWNLOADING,
            message=f"TorBox processing torrent ({name}: {remote_state})",
            complete=False,
            file_path=None,
            download_speed=speed,
            eta=eta,
        )

    @staticmethod
    def _unavailable_grace_expired(state: _DownloadState) -> bool:
        """Return True once finished TorBox files have stayed absent past the grace period."""
        now = time.monotonic()
        with state.lock:
            if state.unavailable_since is None:
                state.unavailable_since = now
                logger.info(
                    "TorBox finished torrent %s but its files are not present yet; waiting",
                    state.torrent_id,
                    extra={"torrent_id": state.torrent_id},
                )
            return now - state.unavailable_since >= _UNAVAILABLE_GRACE_SECONDS

    @staticmethod
    def _normalize_remote_progress(value: object) -> float:
        """Normalize fractional or percentage TorBox progress to 0 through 100."""
        if not isinstance(value, int | float | str):
            return 0.0
        try:
            progress = float(value)
        except TypeError, ValueError:
            return 0.0
        if not math.isfinite(progress):
            return 0.0
        if 0.0 <= progress <= 1.0:
            progress *= 100.0
        return max(0.0, min(100.0, progress))

    @staticmethod
    def _integer_value(value: object) -> int | None:
        """Return an integer metric when TorBox provided a numeric value."""
        if not isinstance(value, int | float | str):
            return None
        try:
            return int(value)
        except TypeError, ValueError:
            return None

    def _maybe_start_download_thread(
        self,
        state: _DownloadState,
        files: list[dict[str, Any]],
    ) -> None:
        """Start exactly one background worker to retrieve TorBox files."""
        with state.lock:
            already_running = state.phase in {"downloading_http", "complete"}
            thread_alive = state.download_thread is not None and state.download_thread.is_alive()
            if already_running or thread_alive:
                return
            state.phase = "downloading_http"
            state.progress = 50.0
            state.download_thread = threading.Thread(
                target=self._process_and_download,
                args=(state, files),
                daemon=True,
            )
            state.download_thread.start()

    def _process_and_download(self, state: _DownloadState, files: list[dict[str, Any]]) -> None:
        """Request direct file links from TorBox and download supported content."""
        try:
            if state.cancel_event.is_set():
                return
            relevant = [
                file_info
                for file_info in files
                if self._file_name(file_info).lower().endswith(_BOOK_EXTENSIONS)
            ]
            if not relevant:
                _raise_runtime_error("TorBox torrent contains no supported book or audiobook files")

            with state.lock:
                if state.cancel_event.is_set():
                    return
                state.target_dir.mkdir(parents=True, exist_ok=True)
            for index, file_info in enumerate(relevant, start=1):
                if state.cancel_event.is_set():
                    return
                file_id = self._file_id(file_info)
                relative_path = self._safe_relative_path(file_info, state.target_dir)
                direct_url = self._request_download_link(state.torrent_id, file_id)
                buffer = download_url(
                    direct_url,
                    referer="https://torbox.app/",
                    cancel_flag=state.cancel_event,
                )
                if state.cancel_event.is_set():
                    return
                if not buffer:
                    _raise_runtime_error(
                        f"TorBox file download failed for torrent {state.torrent_id}, file {file_id}"
                    )

                destination = state.target_dir / relative_path
                destination.parent.mkdir(parents=True, exist_ok=True)
                with destination.open("wb") as output:
                    buffer.seek(0)
                    shutil.copyfileobj(buffer, output)
                with state.lock:
                    state.progress = 50.0 + index / len(relevant) * 50.0

            with state.lock:
                if state.cancel_event.is_set():
                    return
                state.phase = "complete"
                state.progress = 100.0
            logger.info(
                "TorBox download complete: ID %s",
                state.torrent_id,
                extra={"torrent_id": state.torrent_id},
            )
        except Exception as e:
            if state.cancel_event.is_set():
                logger.info(
                    "TorBox file retrieval cancelled",
                    extra={"torrent_id": state.torrent_id},
                )
                return
            logger.exception(
                "TorBox file retrieval failed",
                extra={"torrent_id": state.torrent_id},
            )
            self._set_error(state, str(e) or "TorBox file retrieval failed")

    def _request_download_link(self, torrent_id: str, file_id: int) -> str:
        """Request a temporary direct link without exposing the token in messages."""
        data = self._request_data(
            "GET",
            "/torrents/requestdl",
            operation="file-link request",
            params={
                "token": self._api_key,
                "torrent_id": torrent_id,
                "file_id": file_id,
                "redirect": "false",
                "append_name": "true",
            },
            timeout=_API_TIMEOUT,
        )
        if not isinstance(data, str):
            _raise_runtime_error(
                f"TorBox returned an invalid download link for torrent {torrent_id}, file {file_id}"
            )
        parsed = urlparse(data)
        if parsed.scheme != "https" or not parsed.hostname:
            _raise_runtime_error(
                f"TorBox returned an invalid download link for torrent {torrent_id}, file {file_id}"
            )
        return data

    @staticmethod
    def _file_name(file_info: dict[str, Any]) -> str:
        """Return the provider path, falling back to its shortened name."""
        name = file_info.get("name")
        if isinstance(name, str) and name.strip():
            return name
        short_name = file_info.get("short_name")
        return short_name.strip() if isinstance(short_name, str) else ""

    @staticmethod
    def _file_id(file_info: dict[str, Any]) -> int:
        """Return a validated TorBox file ID."""
        try:
            return int(file_info["id"])
        except (KeyError, TypeError, ValueError) as e:
            _raise_runtime_error(f"TorBox returned an invalid file ID: {e}")

    @classmethod
    def _safe_relative_path(cls, file_info: dict[str, Any], target_dir: Path) -> Path:
        """Validate external file metadata before writing below ``target_dir``."""
        return safe_relative_path(cls._file_name(file_info), target_dir, "TorBox")

    @staticmethod
    def _set_error(state: _DownloadState, message: str) -> None:
        """Record a terminal local error for later polling calls."""
        with state.lock:
            state.phase = "error"
            state.error_message = message
