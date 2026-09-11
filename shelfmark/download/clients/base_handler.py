"""Shared download handler for external torrent/usenet clients."""

from __future__ import annotations

import errno
import math
import shutil
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, TypeGuard

from shelfmark.core.config import config
from shelfmark.core.logger import setup_logger
from shelfmark.core.request_helpers import normalize_optional_text
from shelfmark.core.utils import is_audiobook
from shelfmark.download.clients import (
    DownloadClient,
    DownloadState,
    DownloadStatus,
    get_client,
    list_configured_clients,
)
from shelfmark.download.fs import run_blocking_io
from shelfmark.download.permissions_debug import log_path_permission_context
from shelfmark.release_sources import DownloadHandler, HandoffResult

if TYPE_CHECKING:
    from collections.abc import Callable
    from threading import Event

    from shelfmark.core.models import DownloadTask

logger = setup_logger(__name__)
_CLIENT_CLEANUP_ERRORS = (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError)


class _SabnzbdLikeClient(Protocol):
    name: str

    def remove(
        self, download_id: str, *, delete_files: bool = False, archive: bool = True
    ) -> bool: ...


def _is_sabnzbd_like_client(candidate: DownloadClient) -> TypeGuard[_SabnzbdLikeClient]:
    return getattr(candidate, "name", "") == "sabnzbd"


# How often to poll the download client for status (seconds)
POLL_INTERVAL = 2
WINDOWS_DRIVE_PREFIX_LENGTH = 2
SECONDS_PER_MINUTE = 60
SECONDS_PER_HOUR = 3600
# How long to wait for completed files to appear (seconds)
COMPLETED_PATH_RETRY_INTERVAL = 5
COMPLETED_PATH_MAX_ATTEMPTS = 12  # 12 attempts * 5s = 60s grace period
COMPLETED_PATH_TIMEOUT_SETTING = "DOWNLOAD_CLIENT_COMPLETED_PATH_TIMEOUT"
COMPLETED_PATH_TIMEOUT_MAX_SECONDS = 3600
_RETRYABLE_COMPLETED_PATH_ERRNOS = frozenset(
    code
    for code in (
        errno.ENOENT,
        getattr(errno, "ESTALE", None),
        getattr(errno, "EAGAIN", None),
        getattr(errno, "EBUSY", None),
        getattr(errno, "ETIMEDOUT", None),
    )
    if code is not None
)


@dataclass(frozen=True)
class DownloadRequest:
    """Source-specific download parameters resolved before sending to a client."""

    url: str
    protocol: str
    release_name: str
    expected_hash: str | None
    seeding_time_limit: int | None = None  # minutes
    ratio_limit: float | None = None


@dataclass(frozen=True)
class _CompletedPathResolution:
    path: Path | None
    error: str | None
    retryable: bool


def _coerce_completed_path_timeout_seconds(value: object, default: float) -> float:
    if isinstance(value, bool) or value is None:
        return default
    if isinstance(value, (int, float)):
        parsed = float(value)
    elif isinstance(value, str):
        try:
            parsed = float(value.strip())
        except ValueError:
            return default
    else:
        return default

    if not math.isfinite(parsed) or parsed < 0:
        return default
    return min(parsed, float(COMPLETED_PATH_TIMEOUT_MAX_SECONDS))


def _is_retryable_completed_path_probe(error: OSError | None) -> bool:
    return error is not None and error.errno in _RETRYABLE_COMPLETED_PATH_ERRNOS


def _path_needs_mapping(path: str) -> bool:
    return (len(path) >= WINDOWS_DRIVE_PREFIX_LENGTH and path[1] == ":") or "\\" in path


def _diagnose_path_issue(path: str) -> str:
    """Analyze a path and return diagnostic hints for common issues.

    Args:
        path: The path that failed to be accessed

    Returns:
        A hint string to help users diagnose the issue.

    """
    # Detect Windows-style paths (won't work in Linux containers)
    if len(path) >= WINDOWS_DRIVE_PREFIX_LENGTH and path[1] == ":":
        return (
            f"Path '{path}' appears to be a Windows path. "
            f"Shelfmark runs in Linux and cannot access Windows paths directly. "
            f"Ensure your download client uses Linux-style paths (/path/to/files)."
        )

    # Detect backslashes (Windows path separators)
    if "\\" in path:
        return (
            f"Path '{path}' contains backslashes. "
            f"This may indicate a Windows path or incorrect path escaping. "
            f"Linux paths should use forward slashes (/)."
        )

    # Generic hint for Linux paths
    return (
        f"Path '{path}' is not accessible from Shelfmark's container. "
        f"Ensure both containers have matching volume mounts for this directory, "
        f"or configure Remote Path Mappings in Settings > Advanced."
    )


def _format_probe_error(error: OSError | None) -> str:
    """Render an OSError for inclusion in log/status messages."""
    if error is None:
        return "none"
    code = errno.errorcode.get(error.errno, str(error.errno)) if error.errno else "?"
    return f"{code}: {error.strerror or error}"


def _probe_completed_path(path: Path) -> tuple[bool, OSError | None]:
    """Probe a completed download path and preserve the underlying stat error.

    `Path.exists()` silently converts every `OSError` to `False`, which hides
    whether a failure is ENOENT (not yet written), EACCES (permission denied),
    ESTALE (NFS stale handle), or something else. Callers need the real errno
    to decide whether the condition is retryable and to surface diagnostics.
    """
    try:
        run_blocking_io(path.stat)
    except OSError as error:
        return False, error
    return True, None


class ExternalClientHandler(DownloadHandler, ABC):
    """Shared lifecycle handler for sources that hand off to torrent/usenet clients."""

    def __init__(self) -> None:
        """Initialize cleanup tracking for client-managed downloads."""
        # Track downloads that may need client-side cleanup after Shelfmark completes import.
        # task_id -> (client, download_id, protocol)
        self._cleanup_refs: dict[str, tuple[DownloadClient, str, str]] = {}

    @abstractmethod
    def _resolve_download(
        self,
        task: DownloadTask,
        status_callback: Callable[[str, str | None], None],
    ) -> DownloadRequest | None:
        """Resolve source-specific task metadata into a client download request."""

    def _on_download_complete(self, task: DownloadTask) -> None:
        """Run post-completion source cleanup hooks."""
        return

    def _get_client(self, protocol: str) -> DownloadClient | None:
        """Resolve the active client for a protocol."""
        return get_client(protocol)

    def _list_configured_clients(self) -> list[str]:
        """List protocols with configured clients."""
        return list_configured_clients()

    def _poll_interval(self) -> float:
        """Return the polling interval for status checks."""
        return POLL_INTERVAL

    def _completed_path_retry_interval(self) -> float:
        """Retry interval while waiting for completed files (seconds)."""
        return COMPLETED_PATH_RETRY_INTERVAL

    def _completed_path_max_attempts(self) -> int:
        """Maximum attempts when waiting for completed files."""
        return COMPLETED_PATH_MAX_ATTEMPTS

    def _completed_path_timeout_seconds(self) -> float:
        """Total time to wait for completed files to appear on disk."""
        fallback = self._completed_path_retry_interval() * self._completed_path_max_attempts()
        configured = config.get(COMPLETED_PATH_TIMEOUT_SETTING, fallback)
        return _coerce_completed_path_timeout_seconds(configured, fallback)

    def _refresh_download_request_after_add_failure(
        self,
        *,
        task: DownloadTask,
        request: DownloadRequest,
        error: Exception,
        status_callback: Callable[[str, str | None], None],
    ) -> DownloadRequest | None:
        """Give source handlers one chance to refresh stale resolved download data."""
        return None

    def _get_category_for_task(self, client: DownloadClient, task: DownloadTask) -> str | None:
        """Get audiobook category if configured and applicable, else None for default."""
        if not is_audiobook(task.content_type):
            return None

        # Client-specific audiobook category config keys
        audiobook_keys = {
            "qbittorrent": "QBITTORRENT_CATEGORY_AUDIOBOOK",
            "transmission": "TRANSMISSION_CATEGORY_AUDIOBOOK",
            "deluge": "DELUGE_CATEGORY_AUDIOBOOK",
            "nzbget": "NZBGET_CATEGORY_AUDIOBOOK",
            "sabnzbd": "SABNZBD_CATEGORY_AUDIOBOOK",
        }
        audiobook_key = audiobook_keys.get(client.name)
        if audiobook_key is None:
            return None
        configured_category = config.get(audiobook_key, "")
        normalized_category = normalize_optional_text(configured_category)
        if normalized_category is not None:
            return normalized_category
        if configured_category is None:
            return None
        fallback_category = str(configured_category).strip()
        return fallback_category or None

    def post_process_cleanup(self, task: DownloadTask, *, success: bool) -> None:
        """Clean up external-client state after post-processing finishes."""
        if not success:
            self._cleanup_refs.pop(task.task_id, None)
            return

        client_ref = self._cleanup_refs.pop(task.task_id, None)
        if client_ref is None:
            return

        client, download_id, protocol = client_ref

        if protocol == "usenet":
            # "Move" means copy into ingest then let the usenet client delete its own files.
            if config.get("PROWLARR_USENET_ACTION", "move") != "move":
                return
            try:
                self._delete_local_download_data(client, download_id)
                self._remove_usenet_download(client, download_id, delete_files=True, archive=True)
            except _CLIENT_CLEANUP_ERRORS as e:
                logger.warning(
                    "Failed to cleanup usenet download %s in %s: %s",
                    download_id,
                    getattr(client, "name", "client"),
                    e,
                )

        elif protocol == "torrent":
            torrent_action = config.get("PROWLARR_TORRENT_ACTION", "keep")
            if torrent_action == "remove":
                try:
                    client.remove(download_id, delete_files=False)
                except _CLIENT_CLEANUP_ERRORS as e:
                    logger.warning(
                        "Failed to remove torrent %s from %s: %s",
                        download_id,
                        getattr(client, "name", "client"),
                        e,
                    )
                return

            if torrent_action != "change_category":
                return

            post_import_category = normalize_optional_text(
                config.get("PROWLARR_TORRENT_POST_IMPORT_CATEGORY", "")
            )
            if post_import_category is None:
                return

            try:
                category_updated = client.set_category(download_id, post_import_category)
            except _CLIENT_CLEANUP_ERRORS as e:
                logger.warning(
                    "Failed to set post-import category for torrent %s in %s: %s",
                    download_id,
                    getattr(client, "name", "client"),
                    e,
                )
                return

            if not category_updated:
                # Clients that cannot label torrents (debrid services) return False here,
                # and the ones that can already log the specific failure themselves.
                logger.debug(
                    "Post-import category not applied to torrent %s in %s",
                    download_id,
                    getattr(client, "name", "client"),
                )

    def _remove_usenet_download(
        self,
        client: DownloadClient,
        download_id: str,
        *,
        delete_files: bool,
        archive: bool = True,
    ) -> None:
        """Remove a usenet download with SABnzbd-specific archive handling."""
        if _is_sabnzbd_like_client(client):
            client.remove(download_id, delete_files=delete_files, archive=archive)
        else:
            client.remove(download_id, delete_files=delete_files)

    def _delete_local_download_data(self, client: DownloadClient, download_id: str) -> None:
        """Best-effort local deletion of client download data."""
        try:
            raw_path = client.get_download_path(download_id)
        except _CLIENT_CLEANUP_ERRORS as e:
            logger.debug(
                "Failed to resolve download path for %s %s: %s", client.name, download_id, e
            )
            return

        if not raw_path:
            logger.debug("No download path available for %s %s", client.name, download_id)
            return

        from shelfmark.core.path_mappings import (
            get_client_host_identifier,
            parse_remote_path_mappings,
            remap_remote_to_local_with_match,
        )

        source_path_obj = Path(raw_path)
        host = get_client_host_identifier(client) or ""
        mapping_value = config.get("PROWLARR_REMOTE_PATH_MAPPINGS", [])
        mappings = parse_remote_path_mappings(mapping_value)
        remapped, matched_mapping = remap_remote_to_local_with_match(
            mappings=mappings,
            host=host,
            remote_path=source_path_obj,
        )

        if matched_mapping:
            if remapped is None:
                logger.warning(
                    "Refusing to delete download data for %s %s because remote path mapping rejected unsafe path: %s",
                    client.name,
                    download_id,
                    source_path_obj,
                )
                return
            delete_path = remapped
        else:
            delete_path = source_path_obj

        if str(delete_path) in ("", "/"):
            logger.warning(
                "Refusing to delete unsafe path for %s %s: %s",
                client.name,
                download_id,
                delete_path,
            )
            return

        if not run_blocking_io(delete_path.exists):
            logger.debug("Local download path does not exist for cleanup: %s", delete_path)
            return

        try:
            if run_blocking_io(delete_path.is_dir):
                run_blocking_io(shutil.rmtree, delete_path)
            else:
                run_blocking_io(delete_path.unlink)
            logger.info(
                "Deleted local download data for %s %s: %s", client.name, download_id, delete_path
            )
        except _CLIENT_CLEANUP_ERRORS as e:
            logger.warning(
                "Failed to delete local download data for %s %s: %s", client.name, download_id, e
            )

    def _safe_remove_download(
        self,
        client: DownloadClient,
        download_id: str,
        protocol: str,
        reason: str,
    ) -> None:
        """Best-effort removal of a failed/cancelled download from the client.

        Safety policy:
        - torrents: never remove or delete client data (avoid breaking seeding)
        - usenet: keep legacy behavior (delete client files on removal)
        """
        if protocol != "usenet":
            logger.info(
                "Skipping download client cleanup for protocol=%s after %s (client=%s id=%s)",
                protocol,
                reason,
                getattr(client, "name", "client"),
                download_id,
            )
            return

        try:
            # Permanent delete for failed usenet downloads (SABnzbd archive=0).
            self._delete_local_download_data(client, download_id)
            self._remove_usenet_download(client, download_id, delete_files=True, archive=False)
        except _CLIENT_CLEANUP_ERRORS as e:
            logger.warning(
                "Failed to remove download %s from %s after %s: %s",
                download_id,
                client.name,
                reason,
                e,
            )

    def _handle_cancelled_download(
        self,
        client: DownloadClient,
        download_id: str,
        protocol: str,
        status_callback: Callable[[str, str | None], None],
    ) -> None:
        if protocol == "usenet":
            logger.info("Download cancelled, removing from %s: %s", client.name, download_id)
            try:
                self._delete_local_download_data(client, download_id)
                self._remove_usenet_download(client, download_id, delete_files=True, archive=True)
            except _CLIENT_CLEANUP_ERRORS as e:
                logger.warning(
                    "Failed to remove download %s from %s after cancellation: %s",
                    download_id,
                    client.name,
                    e,
                )
        else:
            logger.info(
                "Download cancelled for protocol=%s; leaving in %s: %s",
                protocol,
                client.name,
                download_id,
            )
        status_callback("cancelled", "Cancelled")

    def _resolve_download_path_once(
        self,
        client: DownloadClient,
        download_id: str,
        *,
        log_details: bool,
    ) -> tuple[Path | None, str | None]:
        """Resolve and validate the completed download path once."""
        result = self._resolve_download_path_once_detailed(
            client,
            download_id,
            log_details=log_details,
        )
        return result.path, result.error

    def _resolve_download_path_once_detailed(
        self,
        client: DownloadClient,
        download_id: str,
        *,
        log_details: bool,
    ) -> _CompletedPathResolution:
        """Resolve and validate a completed path, including retryability."""
        try:
            raw_path = client.get_download_path(download_id)
        except Exception as e:
            message = (
                f"Could not locate completed download in {client.name} (path not returned). "
                f"Check volume mappings and category settings."
            )
            if log_details:
                logger.exception(
                    "Failed to resolve download path for %s %s", client.name, download_id
                )
            else:
                logger.debug(
                    "Failed to resolve download path for %s %s: %s", client.name, download_id, e
                )
            return _CompletedPathResolution(None, message, retryable=False)

        if not raw_path:
            message = (
                f"Could not locate completed download in {client.name} (path not returned). "
                f"Check volume mappings and category settings."
            )
            if log_details:
                logger.error(
                    "Download client returned empty path for %s %s", client.name, download_id
                )
            else:
                logger.debug(
                    "Download client returned empty path for %s %s", client.name, download_id
                )
            return _CompletedPathResolution(None, message, retryable=False)

        from shelfmark.core.path_mappings import (
            get_client_host_identifier,
            parse_remote_path_mappings,
            remap_remote_to_local_with_match,
        )

        source_path_obj = Path(raw_path)
        host = get_client_host_identifier(client) or ""
        mapping_value = config.get("PROWLARR_REMOTE_PATH_MAPPINGS", [])
        mappings = parse_remote_path_mappings(mapping_value)

        if log_details:
            logger.debug(
                "Attempting path remap: client=%s, host=%s, path=%s, mappings=%s",
                client.name,
                host,
                source_path_obj,
                [(m.host, m.remote_path, m.local_path) for m in mappings],
            )

        remapped, matched_mapping = remap_remote_to_local_with_match(
            mappings=mappings,
            host=host,
            remote_path=source_path_obj,
        )

        if matched_mapping:
            if remapped is None:
                message = (
                    f"Remote path mapping rejected unsafe path '{source_path_obj}'. "
                    f"Check Settings > Advanced > Remote Path Mappings."
                )
                failure_log = "Remote path mapping rejected unsafe path for %s (%s): %s"
                failure_args = (client.name, download_id, source_path_obj)
                if log_details:
                    logger.error(failure_log, *failure_args)
                else:
                    logger.debug(failure_log, *failure_args)
                return _CompletedPathResolution(None, message, retryable=False)

            remapped_exists, remapped_error = _probe_completed_path(remapped)

            if log_details:
                logger.debug(
                    "Remap result: %s -> %s (exists=%s, probe_error=%s, changed=%s, matched=%s)",
                    source_path_obj,
                    remapped,
                    remapped_exists,
                    _format_probe_error(remapped_error),
                    remapped != source_path_obj,
                    matched_mapping,
                )

            if remapped_exists:
                logger.info(
                    "Remapped download path for %s (%s): %s -> %s",
                    client.name,
                    download_id,
                    source_path_obj,
                    remapped,
                )
                return _CompletedPathResolution(remapped, None, retryable=False)

            message = (
                f"Remapped path '{remapped}' does not exist. "
                f"Check your Docker volume mounts match the Local Path in Settings > Advanced > Remote Path Mappings."
            )
            failure_log = "Download path does not exist after remapping: %s -> %s (probe_error=%s). Client: %s, ID: %s."
            failure_args = (
                raw_path,
                remapped,
                _format_probe_error(remapped_error),
                client.name,
                download_id,
            )
            if log_details:
                log_path_permission_context("completed_download_remap", remapped)
                logger.error(failure_log, *failure_args)
            else:
                logger.debug(failure_log, *failure_args)
            return _CompletedPathResolution(
                None,
                message,
                retryable=_is_retryable_completed_path_probe(remapped_error),
            )

        source_exists, source_error = _probe_completed_path(source_path_obj)

        if log_details:
            logger.debug(
                "Remap result: %s -> %s (exists=%s, probe_error=%s, changed=%s, matched=%s)",
                source_path_obj,
                remapped,
                source_exists,
                _format_probe_error(source_error),
                remapped != source_path_obj,
                matched_mapping,
            )

        if source_exists:
            if mappings:
                logger.info(
                    "No remote path mapping matched for %s (%s); using client path: %s",
                    client.name,
                    download_id,
                    source_path_obj,
                )
            return _CompletedPathResolution(source_path_obj, None, retryable=False)

        hint = _diagnose_path_issue(raw_path)
        if mappings:
            message = f"{hint} No remote path mapping matched for client '{client.name}'."
            failure_label = "completed_download_original"
            failure_log = "Download path does not exist and no remote path mapping matched for %s (%s): %s (probe_error=%s). %s"
            failure_args = (
                client.name,
                download_id,
                raw_path,
                _format_probe_error(source_error),
                hint,
            )
        else:
            message = hint
            failure_label = "completed_download_direct"
            failure_log = (
                "Download path does not exist: %s (probe_error=%s). Client: %s, ID: %s. %s"
            )
            failure_args = (
                raw_path,
                _format_probe_error(source_error),
                client.name,
                download_id,
                hint,
            )

        if log_details:
            log_path_permission_context(failure_label, source_path_obj)
            logger.error(failure_log, *failure_args)
        else:
            logger.debug(failure_log, *failure_args)
        return _CompletedPathResolution(
            None,
            message,
            retryable=not _path_needs_mapping(raw_path)
            and _is_retryable_completed_path_probe(source_error),
        )

    def _wait_for_completed_path(
        self,
        client: DownloadClient,
        download_id: str,
        *,
        cancel_flag: Event | None,
        status_callback: Callable[[str, str | None], None],
    ) -> tuple[Path | None, str | None]:
        """Wait briefly for completed files to appear on disk."""
        last_error: str | None = None
        retry_interval = self._completed_path_retry_interval()
        timeout_seconds = self._completed_path_timeout_seconds()
        if retry_interval <= 0 or timeout_seconds <= 0:
            max_attempts = 1
        else:
            max_attempts = int(math.ceil(timeout_seconds / retry_interval)) + 1

        for attempt in range(1, max_attempts + 1):
            if cancel_flag and cancel_flag.is_set():
                return None, last_error

            log_details = attempt == max_attempts
            result = self._resolve_download_path_once_detailed(
                client,
                download_id,
                log_details=log_details,
            )
            if result.path:
                return result.path, None

            last_error = result.error

            if not result.retryable:
                if not log_details:
                    logger.error(
                        "Completed path resolution is not retryable for %s (%s): %s",
                        client.name,
                        download_id,
                        last_error,
                    )
                return None, last_error

            if attempt < max_attempts:
                status_callback("locating", "Waiting for completed files...")
                logger.debug(
                    "Completed files not available yet for %s (%s) (attempt %d/%d)",
                    client.name,
                    download_id,
                    attempt,
                    max_attempts,
                )

                if cancel_flag:
                    if cancel_flag.wait(timeout=retry_interval):
                        return None, last_error
                else:
                    time.sleep(retry_interval)

        return None, last_error

    def _build_progress_message(self, status: DownloadStatus) -> str:
        """Build a progress message from download status."""
        msg = f"{status.progress:.0f}%"

        if status.download_speed and status.download_speed > 0:
            speed_mb = status.download_speed / 1024 / 1024
            msg += f" ({speed_mb:.1f} MB/s)"

        if status.eta and status.eta > 0:
            if status.eta < SECONDS_PER_MINUTE:
                msg += f" - {status.eta}s left"
            elif status.eta < SECONDS_PER_HOUR:
                msg += f" - {status.eta // SECONDS_PER_MINUTE}m left"
            else:
                msg += (
                    f" - {status.eta // SECONDS_PER_HOUR}h "
                    f"{(status.eta % SECONDS_PER_HOUR) // SECONDS_PER_MINUTE}m left"
                )

        return msg

    def download(
        self,
        task: DownloadTask,
        cancel_flag: Event,
        progress_callback: Callable[[float], None],
        status_callback: Callable[[str, str | None], None],
    ) -> str | HandoffResult | None:
        """Execute download via configured torrent/usenet client."""
        try:
            if cancel_flag.is_set():
                status_callback("cancelled", "Cancelled")
                return None

            request = self._resolve_download(task, status_callback)
            if not request:
                return None

            client = self._get_client(request.protocol)
            if not client:
                configured = self._list_configured_clients()
                if not configured:
                    status_callback(
                        "error",
                        "No download clients configured. Configure qBittorrent or NZBGet in settings.",
                    )
                else:
                    status_callback("error", f"No {request.protocol} client configured")
                return None

            # Check if this download already exists in the client
            status_callback("resolving", f"Checking {client.name}")
            category = self._get_category_for_task(client, task)
            existing = client.find_existing(request.url, category=category)

            if existing:
                download_id, existing_status = existing
                logger.info("Found existing download in %s: %s", client.name, download_id)

                # If already complete, skip straight to file handling
                if existing_status.complete:
                    logger.info("Existing download is complete, copying file directly")
                    status_callback("resolving", "Found existing download, copying to library")

                    source_path_obj, path_error = self._wait_for_completed_path(
                        client=client,
                        download_id=download_id,
                        cancel_flag=cancel_flag,
                        status_callback=status_callback,
                    )
                    if not source_path_obj:
                        if cancel_flag.is_set():
                            return None
                        status_callback(
                            "error",
                            path_error
                            or f"Could not locate existing download in {client.name}. Check that the file still exists.",
                        )
                        return None

                    result = self._handle_completed_file(
                        source_path=source_path_obj,
                        protocol=request.protocol,
                        task=task,
                        status_callback=status_callback,
                    )

                    if result:
                        self._on_download_complete(task)
                        self._cleanup_refs[task.task_id] = (client, download_id, request.protocol)
                    return result

                # Existing but still downloading - join the progress polling
                logger.info("Existing download in progress, joining poll loop")
                status_callback("downloading", "Resuming existing download")
            else:
                # No existing download - add new
                refresh_attempted = False
                while True:
                    status_callback("resolving", f"Sending to {client.name}")
                    try:
                        download_id = client.add_download(
                            url=request.url,
                            name=request.release_name,
                            category=category,
                            expected_hash=request.expected_hash,
                            seeding_time_limit=request.seeding_time_limit,
                            ratio_limit=request.ratio_limit,
                            # rTorrent has no category concept, so its audiobook label
                            # can only be chosen from the content type (#1235).
                            content_type=task.content_type,
                        )
                    except Exception as e:
                        if not refresh_attempted:
                            refresh_attempted = True
                            refreshed_request = self._refresh_download_request_after_add_failure(
                                task=task,
                                request=request,
                                error=e,
                                status_callback=status_callback,
                            )
                            if (
                                refreshed_request is not None
                                and refreshed_request.protocol == request.protocol
                            ):
                                request = refreshed_request
                                continue

                        logger.exception("Failed to add to %s", client.name)
                        status_callback("error", f"Failed to add to {client.name}: {e}")
                        return None
                    break

                logger.info(
                    "Added to %s: %s for '%s'", client.name, download_id, request.release_name
                )

            # Poll for progress
            return self._poll_and_complete(
                client=client,
                download_id=download_id,
                protocol=request.protocol,
                task=task,
                cancel_flag=cancel_flag,
                progress_callback=progress_callback,
                status_callback=status_callback,
            )

        except Exception as e:
            logger.exception("External client download error")
            status_callback("error", str(e))
            return None

    def _poll_and_complete(
        self,
        client: DownloadClient,
        download_id: str,
        protocol: str,
        task: DownloadTask,
        cancel_flag: Event,
        progress_callback: Callable[[float], None],
        status_callback: Callable[[str, str | None], None],
    ) -> str | HandoffResult | None:
        """Poll the download client for progress and handle completion."""
        poll_interval = self._poll_interval()
        # Track consecutive "not found" errors - torrents may take time to appear in client
        not_found_count = 0
        max_not_found_retries = 15  # 15 retries * poll interval ~= 30s grace period

        try:
            result: str | HandoffResult | None = None
            logger.debug("Starting poll for %s (content_type=%s)", download_id, task.content_type)
            while not cancel_flag.is_set():
                status = client.get_status(download_id)
                progress_callback(status.progress)

                # Check for completion
                if status.complete:
                    if status.state == DownloadState.ERROR:
                        logger.error(
                            "Download %s completed with error: %s", download_id, status.message
                        )
                        status_callback("error", status.message or "Download failed")
                        self._safe_remove_download(
                            client, download_id, protocol, "completion error"
                        )
                        return None
                    # Download complete - break to handle file
                    logger.debug(
                        "Download %s complete, file_path=%s", download_id, status.file_path
                    )
                    break

                # Check for error state
                if status.state == DownloadState.ERROR:
                    message = (status.message or "").strip()
                    message_lower = message.lower()

                    # Only treat *actual* "not found" as retryable.
                    # qBittorrent auth/network/API failures should surface immediately (more actionable)
                    # and must not be confused with "torrent missing".
                    retryable_not_found = any(
                        token in message_lower
                        for token in (
                            "torrent not found",
                            "not found in qbittorrent",
                            "download not found",
                        )
                    )

                    non_retryable = any(
                        token in message_lower
                        for token in (
                            "authentication failed",
                            "cannot connect",
                            "timed out",
                            "api request failed",
                        )
                    )

                    if retryable_not_found and not non_retryable:
                        not_found_count += 1
                        if not_found_count < max_not_found_retries:
                            logger.debug(
                                "Download %s not yet visible in client (attempt %s/%s)",
                                download_id,
                                not_found_count,
                                max_not_found_retries,
                            )
                            status_callback("resolving", "Waiting for download client...")
                            if cancel_flag.wait(timeout=poll_interval):
                                break
                            continue

                        logger.error(
                            "Download %s not found after %s attempts",
                            download_id,
                            max_not_found_retries,
                        )
                    else:
                        # Fail fast on actionable errors (auth, connectivity, API issues)
                        logger.error("Download %s error state: %s", download_id, status.message)

                    status_callback("error", status.message or "Download failed")
                    self._safe_remove_download(client, download_id, protocol, "download error")
                    return None

                # Reset not-found counter on successful status check
                not_found_count = 0

                # Build status message - use client message if provided, else build progress
                msg = status.message or self._build_progress_message(status)
                if status.state == DownloadState.PROCESSING:
                    # Post-processing (e.g., SABnzbd verifying/extracting)
                    status_callback("resolving", msg)
                else:
                    status_callback("downloading", msg)

                # Wait for next poll (interruptible by cancel)
                if cancel_flag.wait(timeout=poll_interval):
                    break

            # Handle cancellation
            if cancel_flag.is_set():
                self._handle_cancelled_download(client, download_id, protocol, status_callback)
                return None

            # Handle completed file (wait briefly for files to appear)
            source_path_obj, path_error = self._wait_for_completed_path(
                client=client,
                download_id=download_id,
                cancel_flag=cancel_flag,
                status_callback=status_callback,
            )
            if not source_path_obj:
                if cancel_flag.is_set():
                    self._handle_cancelled_download(client, download_id, protocol, status_callback)
                    return None
                status_callback(
                    "error",
                    path_error
                    or f"Could not locate completed download in {client.name} (path not returned). Check volume mappings and category settings.",
                )
                return None

            if getattr(client, "handoff_only", False) is True:
                result = HandoffResult(
                    path=str(source_path_obj),
                    message=f"Torrent file saved to {source_path_obj}",
                )
            else:
                result = self._handle_completed_file(
                    source_path=source_path_obj,
                    protocol=protocol,
                    task=task,
                    status_callback=status_callback,
                )

        except Exception as e:
            logger.exception("Error during download polling")
            status_callback("error", str(e))
            self._safe_remove_download(client, download_id, protocol, "polling exception")
            return None

        # Clean up on success
        if result:
            self._on_download_complete(task)
            if not isinstance(result, HandoffResult):
                self._cleanup_refs[task.task_id] = (client, download_id, protocol)

        return result

    def _handle_completed_file(
        self,
        source_path: Path,
        protocol: str,
        task: DownloadTask,
        status_callback: Callable[[str, str | None], None],
    ) -> str | None:
        """Handle a completed download and return its path.

        For external download clients (torrents/usenet), staging large payloads into TMP_DIR
        is expensive (and can duplicate multi-GB files). Instead, return the client's
        completed path and let the orchestrator perform any required transfer (copy/move/
        hardlink) directly from that source.

        Torrents also set ``task.original_download_path`` so the orchestrator can detect
        seeding data and enable hardlinking when configured.
        """
        try:
            if protocol == "torrent":
                task.original_download_path = str(source_path)

            logger.debug("Download complete, returning original path: %s", source_path)
            return str(source_path)

        except Exception as e:
            logger.exception("Failed to finalize completed download at %s", source_path)
            status_callback("error", f"Failed to finalize completed download: {e}")
            return None

    def cancel(self, task_id: str) -> bool:
        """Default cancellation (primary cancellation happens via cancel_flag)."""
        logger.debug("Cancel requested for external client task: %s", task_id)
        return True
