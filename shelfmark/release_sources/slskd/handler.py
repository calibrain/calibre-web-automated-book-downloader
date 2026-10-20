"""slskd download handler - enqueues Soulseek transfers in slskd and collects the files.

slskd writes completed downloads to its own downloads directory, laid out as
``<downloads>/<remote folder name>/<file name>``. Shelfmark needs to see that directory
too (a shared volume), either at the same path, at the path configured in
``SLSKD_DOWNLOAD_PATH``, or through a remote path mapping for the ``slskd`` client.
"""

from __future__ import annotations

import json
import math
import re
import shutil
import time
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import requests

from shelfmark.core.config import config
from shelfmark.core.logger import setup_logger
from shelfmark.core.path_mappings import (
    parse_remote_path_mappings,
    remap_remote_to_local_with_match,
)
from shelfmark.download.activity import release_activity_grace, request_activity_grace
from shelfmark.download.clients.base_handler import (
    COMPLETED_PATH_MAX_ATTEMPTS as _DEFAULT_COMPLETED_PATH_MAX_ATTEMPTS,
)
from shelfmark.download.clients.base_handler import (
    COMPLETED_PATH_RETRY_INTERVAL as _DEFAULT_COMPLETED_PATH_RETRY_INTERVAL,
)
from shelfmark.download.clients.base_handler import (
    COMPLETED_PATH_TIMEOUT_SETTING,
    _coerce_completed_path_timeout_seconds,
)
from shelfmark.download.fs import run_blocking_io
from shelfmark.download.postprocess.packs import PackFile
from shelfmark.release_sources import DownloadHandler, register_handler
from shelfmark.release_sources.slskd.api import (
    SlskdBatchUnsupportedError,
    SlskdClient,
    SlskdError,
    transfer_is_complete,
    transfer_succeeded,
)
from shelfmark.release_sources.slskd.source import (
    SOURCE_NAME,
    build_client_from_config,
    remote_directory_name,
    split_remote_path,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from threading import Event

    from shelfmark.core.models import DownloadTask

logger = setup_logger(__name__)

POLL_INTERVAL = 2.0
COMPLETED_PATH_RETRY_INTERVAL = _DEFAULT_COMPLETED_PATH_RETRY_INTERVAL
COMPLETED_PATH_MAX_ATTEMPTS = _DEFAULT_COMPLETED_PATH_MAX_ATTEMPTS
# Polls with the transfer missing from slskd before giving up (~30s at POLL_INTERVAL).
MAX_MISSING_POLLS = 15
DEFAULT_QUEUE_TIMEOUT_MINUTES = 60
PATH_MAPPING_HOST = "slskd"
# After cancelling, how many times to retry removing the leftover records.
CANCEL_REMOVE_PASSES = 3
CANCEL_REMOVE_INTERVAL = 0.5
_BYTES_PER_MB = 1024 * 1024
# Isolated mode: while a peer holds the files in its queue nothing changes, so the orchestrator's
# stall timer would cancel the download. Ask for a grace instead, renewed while still queued
# (the orchestrator caps a single grace), and let the peer queue timeout decide when to give up.
QUEUE_GRACE_SECONDS = 900.0
QUEUE_GRACE_RENEW_SECONDS = 600.0
DEFAULT_DESTINATION_PREFIX = "shelfmark"
_USE_ORIGINAL_ENQUEUE = object()  # sentinel: this slskd has no batch endpoint
_SECONDS_PER_MINUTE = 60

_SLSKD_ERRORS = (requests.exceptions.RequestException, SlskdError, ValueError, TypeError)


@dataclass(frozen=True)
class SlskdDownloadSpec:
    """What to fetch: every file of one release, all shared by one peer."""

    username: str
    directory: str
    files: list[dict[str, Any]]  # {"filename": remote path, "size": bytes}

    @property
    def filenames(self) -> list[str]:
        return [str(f["filename"]) for f in self.files]

    @property
    def total_size(self) -> int:
        return sum(max(int(f.get("size") or 0), 0) for f in self.files)

    def to_context(self) -> dict[str, Any]:
        return {
            "username": self.username,
            "directory": self.directory,
            "files": [
                {"filename": str(f["filename"]), "size": int(f.get("size") or 0)}
                for f in self.files
            ],
        }


@dataclass
class _TransferSnapshot:
    """Aggregated state of every transfer in a release at one poll."""

    found: int = 0
    bytes_transferred: int = 0
    speed: float = 0.0
    all_succeeded: bool = False
    failed_state: str | None = None
    failed_filename: str | None = None
    in_progress: int = 0
    queued_remotely: int = 0
    queue_positions: list[int] = field(default_factory=list)


def _parse_spec(username: object, directory: object, raw_files: object) -> SlskdDownloadSpec | None:
    user = str(username or "").strip()
    if not user or not isinstance(raw_files, list):
        return None
    files: list[dict[str, Any]] = []
    for raw in raw_files:
        if not isinstance(raw, dict):
            continue
        filename = str(raw.get("filename") or "").strip()
        if not filename:
            continue
        try:
            size = int(raw.get("size") or 0)
        except TypeError, ValueError:
            size = 0
        files.append({"filename": filename, "size": max(size, 0)})
    if not files:
        return None
    resolved_directory = str(directory or "").strip() or split_remote_path(files[0]["filename"])[0]
    return SlskdDownloadSpec(username=user, directory=resolved_directory, files=files)


def spec_from_release_data(release_data: dict[str, Any]) -> SlskdDownloadSpec | None:
    """Read the download spec from a release payload's ``extra`` block."""
    extra = release_data.get("extra")
    if not isinstance(extra, dict):
        return None
    return _parse_spec(extra.get("username"), extra.get("directory"), extra.get("files"))


def spec_from_task(task: DownloadTask) -> SlskdDownloadSpec | None:
    """Read the download spec persisted on the task for restart-safe retries."""
    context = getattr(task, "retry_source_context", None)
    if not isinstance(context, dict):
        return None
    return _parse_spec(context.get("username"), context.get("directory"), context.get("files"))


def resolve_local_download_root(client: SlskdClient) -> tuple[Path | None, str | None]:
    """Where slskd's completed downloads live, as seen from Shelfmark.

    Returns ``(path, None)`` or ``(None, error message)``.
    """
    override = str(config.get("SLSKD_DOWNLOAD_PATH", "") or "").strip()
    if override:
        return Path(override), None

    try:
        remote_root = client.get_download_directory()
    except _SLSKD_ERRORS as e:
        return None, f"Could not read the downloads directory from slskd: {e}"
    if not remote_root:
        return None, (
            "slskd did not report a downloads directory. Set 'Downloads Path' in the "
            "slskd settings."
        )

    mappings = parse_remote_path_mappings(config.get("PROWLARR_REMOTE_PATH_MAPPINGS", []))
    remapped, matched = remap_remote_to_local_with_match(
        mappings=mappings, host=PATH_MAPPING_HOST, remote_path=remote_root
    )
    if matched and remapped is None:
        return None, (
            f"Remote path mapping rejected unsafe path '{remote_root}'. "
            "Check Settings > Advanced > Remote Path Mappings."
        )
    return remapped or Path(remote_root), None


def local_path_for(root: Path, remote_filename: str) -> Path | None:
    """Compute where slskd stored ``remote_filename`` under ``root``.

    slskd keeps only the last remote folder name, so ``@@a\\Books\\Author\\x.epub``
    lands in ``<root>/Author/x.epub``. Names that would escape ``root`` are refused.
    """
    directory, basename = split_remote_path(remote_filename)
    folder = remote_directory_name(directory)
    parts = [p for p in (folder, basename) if p]
    if not basename or any(p in {".", ".."} for p in parts):
        return None
    return root.joinpath(*parts)


def _snapshot(transfers: list[dict[str, Any]], spec: SlskdDownloadSpec) -> _TransferSnapshot:
    wanted = {name: index for index, name in enumerate(spec.filenames)}
    latest: dict[str, dict[str, Any]] = {}
    for transfer in transfers:
        filename = str(transfer.get("filename") or "")
        if filename not in wanted or transfer.get("removed"):
            continue
        # slskd can keep several records for one file (a retry after a failure);
        # prefer whichever is not terminal-failed, then the newest.
        previous = latest.get(filename)
        if previous is None or _prefer(transfer, previous):
            latest[filename] = transfer

    snap = _TransferSnapshot(found=len(latest))
    succeeded = 0
    for filename, transfer in latest.items():
        state = str(transfer.get("state") or "")
        snap.bytes_transferred += max(int(transfer.get("bytesTransferred") or 0), 0)
        if transfer_succeeded(state):
            succeeded += 1
            continue
        if transfer_is_complete(state):
            if snap.failed_state is None:
                snap.failed_state = state
                snap.failed_filename = filename
            continue
        if state.startswith("InProgress"):
            snap.in_progress += 1
            snap.speed += float(transfer.get("averageSpeed") or 0)
        elif "Remotely" in state:
            snap.queued_remotely += 1
            position = transfer.get("placeInQueue")
            if isinstance(position, int) and position > 0:
                snap.queue_positions.append(position)
    snap.all_succeeded = snap.found == len(spec.files) and succeeded == len(spec.files)
    return snap


def _prefer(candidate: dict[str, Any], current: dict[str, Any]) -> bool:
    candidate_failed = transfer_is_complete(candidate.get("state")) and not transfer_succeeded(
        candidate.get("state")
    )
    current_failed = transfer_is_complete(current.get("state")) and not transfer_succeeded(
        current.get("state")
    )
    if candidate_failed != current_failed:
        return current_failed
    return str(candidate.get("requestedAt") or "") >= str(current.get("requestedAt") or "")


def _progress_message(snap: _TransferSnapshot, spec: SlskdDownloadSpec, percent: float) -> str:
    if snap.in_progress:
        msg = f"{percent:.0f}%"
        if snap.speed > 0:
            msg += f" ({snap.speed / _BYTES_PER_MB:.1f} MB/s)"
        if len(spec.files) > 1:
            msg += f" - {snap.in_progress} of {len(spec.files)} files transferring"
        return msg
    if snap.queued_remotely:
        if snap.queue_positions:
            return f"Queued at peer (position {min(snap.queue_positions)})"
        return "Queued at peer"
    return "Waiting for peer"


def _bool_setting(key: str, default: bool) -> bool:
    value = config.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return default


def _safe_name(value: str) -> str:
    """A task id as one path component: letters, digits, dot, dash and underscore only."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value or "").strip("._") or "download"


def _clean_prefix(raw: object) -> str:
    """The folder, relative to slskd's downloads folder, that holds Shelfmark's downloads."""
    text = str(raw or "").strip().replace("\\", "/").strip("/")
    parts = [p for p in text.split("/") if p]
    if not parts or any(p in {".", ".."} or re.match(r"^[A-Za-z]:", p) for p in parts):
        return DEFAULT_DESTINATION_PREFIX
    return "/".join(_safe_name(p) for p in parts)


def _folder_files(folder: Path) -> list[Path]:
    """The finished files directly inside ``folder`` (hidden and partial files are not)."""
    try:
        if not run_blocking_io(folder.is_dir):
            return []
        return [
            p
            for p in run_blocking_io(lambda: list(folder.iterdir()))
            if not p.name.startswith(".") and run_blocking_io(p.is_file)
        ]
    except OSError:
        return []


def _folder_complete(folder: Path, expected_sizes: list[int]) -> bool:
    """True when the folder holds exactly the files that were asked for.

    Names are not compared, only sizes: slskd may rename a file that already exists, but it
    cannot change its size. A size of 0 means the peer did not say, so only the count is
    checked then.
    """
    files = _folder_files(folder)
    if not files or not expected_sizes or len(files) != len(expected_sizes):
        return False
    if any(size <= 0 for size in expected_sizes):
        return True
    try:
        actual = sorted(run_blocking_io(lambda p=p: p.stat().st_size) for p in files)
    except OSError:
        return False
    return actual == sorted(expected_sizes)


def _completed_remote_files(events: list[dict[str, Any]]) -> set[str]:
    """Remote filenames slskd's stored events say finished downloading."""
    done: set[str] = set()
    for event in events:
        if "downloadfilecomplete" not in str(event.get("type") or "").lower():
            continue
        data = event.get("data")
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except ValueError:
                continue
        if not isinstance(data, dict):
            continue
        raw_transfer = data.get("transfer")
        transfer = raw_transfer if isinstance(raw_transfer, dict) else {}
        name = data.get("remoteFilename") or data.get("RemoteFilename") or transfer.get("filename")
        if name:
            done.add(str(name))
    return done


@register_handler(SOURCE_NAME)
class SlskdHandler(DownloadHandler):
    """Download a Soulseek release through slskd."""

    def __init__(self) -> None:
        # task_id -> (client, username, transfer ids, local paths) for post-import cleanup
        self._cleanup_refs: dict[str, tuple[SlskdClient, str, list[str], list[Path]]] = {}
        self._isolated_refs: dict[str, tuple[SlskdClient, str, list[str], Path]] = {}

    # ── queue-time hooks ──────────────────────────────────────────────────────

    def build_retry_resolution_fields(self, release_data: dict[str, Any]) -> dict[str, Any]:
        spec = spec_from_release_data(release_data)
        if spec is None:
            return {}
        return {
            "retry_download_protocol": "soulseek",
            "retry_source_context": spec.to_context(),
        }

    def list_files(self, release_data: dict[str, Any]) -> list[PackFile] | None:
        spec = spec_from_release_data(release_data)
        if spec is None:
            return None
        return [
            PackFile(path=split_remote_path(f["filename"])[1], size=int(f.get("size") or 0) or None)
            for f in spec.files
        ]

    # ── overridable timing (tests patch these) ────────────────────────────────

    def _poll_interval(self) -> float:
        return POLL_INTERVAL

    def _completed_path_retry_interval(self) -> float:
        return COMPLETED_PATH_RETRY_INTERVAL

    def _completed_path_timeout_seconds(self) -> float:
        fallback = self._completed_path_retry_interval() * COMPLETED_PATH_MAX_ATTEMPTS
        return _coerce_completed_path_timeout_seconds(
            config.get(COMPLETED_PATH_TIMEOUT_SETTING, fallback), fallback
        )

    def _queue_timeout_seconds(self) -> float:
        raw = config.get("SLSKD_QUEUE_TIMEOUT_MINUTES", DEFAULT_QUEUE_TIMEOUT_MINUTES)
        minutes = float(DEFAULT_QUEUE_TIMEOUT_MINUTES)
        if isinstance(raw, (int, float, str)) and not isinstance(raw, bool):
            with suppress(ValueError):
                minutes = float(raw)
        if not math.isfinite(minutes) or minutes < 0:
            minutes = float(DEFAULT_QUEUE_TIMEOUT_MINUTES)
        return minutes * _SECONDS_PER_MINUTE

    def _get_client(self) -> SlskdClient | None:
        return build_client_from_config()

    # ── download ──────────────────────────────────────────────────────────────

    def download(
        self,
        task: DownloadTask,
        cancel_flag: Event,
        progress_callback: Callable[[float], None],
        status_callback: Callable[[str, str | None], None],
    ) -> str | None:
        """Download a release through slskd and return the folder holding its files."""
        if cancel_flag.is_set():
            status_callback("cancelled", "Cancelled")
            return None

        spec = spec_from_task(task)
        if spec is None:
            status_callback("error", "Soulseek file details are missing for this release")
            logger.warning("slskd task %s has no download spec", task.task_id)
            return None

        client = self._get_client()
        if client is None:
            status_callback("error", "slskd is not configured")
            return None

        if self._isolate_enabled():
            outcome = self._download_isolated(
                task, spec, client, cancel_flag, progress_callback, status_callback
            )
            if outcome is not _USE_ORIGINAL_ENQUEUE:
                return outcome  # type: ignore[return-value]
            logger.info("slskd has no batch endpoint; using the plain enqueue")
        return self._download_original(
            task, spec, client, cancel_flag, progress_callback, status_callback
        )

    def _download_original(
        self,
        task: DownloadTask,
        spec: SlskdDownloadSpec,
        client: SlskdClient,
        cancel_flag: Event,
        progress_callback: Callable[[float], None],
        status_callback: Callable[[str, str | None], None],
    ) -> str | None:
        """Enqueue the release in slskd, wait for it, and return the completed path.

        The original behaviour: slskd lays the files out under its downloads folder by the
        peer's last folder name, and the list of transfers is what is polled.
        """
        transfer_ids: list[str] = []
        try:
            status_callback("resolving", "Sending to slskd")
            try:
                enqueued = client.enqueue_downloads(spec.username, spec.files)
            except _SLSKD_ERRORS as e:
                logger.exception("Failed to enqueue slskd download")
                status_callback("error", f"Failed to add to slskd: {e}")
                return None
            transfer_ids = [str(t["id"]) for t in enqueued if t.get("id")]
            logger.info(
                "slskd: enqueued %d file(s) from %s for '%s'",
                len(spec.files),
                spec.username,
                task.title,
            )

            snap = self._poll(task, spec, client, cancel_flag, progress_callback, status_callback)
            if snap is None:
                return None
            transfer_ids = self._current_transfer_ids(client, spec) or transfer_ids

            root, root_error = resolve_local_download_root(client)
            if root is None:
                status_callback("error", root_error or "Could not locate slskd downloads")
                return None

            local_paths = self._wait_for_files(root, spec, cancel_flag, status_callback)
            if local_paths is None:
                if cancel_flag.is_set():
                    # The transfers finished, so nothing is left to stop, but their records
                    # (and the transfer ids) would stay in slskd's list for good.
                    self._cancel_transfers(client, spec, remove=True)
                    status_callback("cancelled", "Cancelled")
                return None

            result = self._finalize(task, local_paths)
        except Exception as e:
            logger.exception("slskd download error")
            status_callback("error", str(e))
            return None

        if result is not None:
            self._cleanup_refs[task.task_id] = (client, spec.username, transfer_ids, local_paths)
        return result

    def _poll(
        self,
        task: DownloadTask,
        spec: SlskdDownloadSpec,
        client: SlskdClient,
        cancel_flag: Event,
        progress_callback: Callable[[float], None],
        status_callback: Callable[[str, str | None], None],
    ) -> _TransferSnapshot | None:
        poll_interval = self._poll_interval()
        queue_timeout = self._queue_timeout_seconds()
        started = time.monotonic()
        missing_polls = 0
        total = spec.total_size

        while not cancel_flag.is_set():
            try:
                transfers = client.list_downloads(spec.username)
            except _SLSKD_ERRORS as e:
                logger.warning("slskd status check failed: %s", e)
                status_callback("resolving", "Waiting for slskd...")
                if cancel_flag.wait(timeout=poll_interval):
                    break
                continue

            snap = _snapshot(transfers, spec)
            if snap.found < len(spec.files):
                missing_polls += 1
                if missing_polls >= MAX_MISSING_POLLS:
                    status_callback("error", "Transfer disappeared from slskd")
                    # Part of a multi-file release may still be listed and downloading.
                    self._cancel_transfers(client, spec, remove=True)
                    return None
                status_callback("resolving", "Waiting for slskd...")
                if cancel_flag.wait(timeout=poll_interval):
                    break
                continue
            missing_polls = 0

            if snap.failed_state is not None:
                outcome = snap.failed_state.split(",", 1)[-1].strip() or snap.failed_state
                message = f"Peer transfer failed: {outcome}"
                if len(spec.files) > 1 and snap.failed_filename:
                    message += f" ({split_remote_path(snap.failed_filename)[1]})"
                logger.error("slskd transfer failed for %s: %s", task.task_id, snap.failed_state)
                status_callback("error", message)
                self._cancel_transfers(client, spec, remove=True)
                return None

            percent = (snap.bytes_transferred / total * 100) if total > 0 else 0.0
            percent = max(0.0, min(100.0, percent))
            progress_callback(percent)

            if snap.all_succeeded:
                progress_callback(100.0)
                return snap

            if (
                queue_timeout > 0
                and snap.bytes_transferred == 0
                and time.monotonic() - started > queue_timeout
            ):
                minutes = int(queue_timeout // _SECONDS_PER_MINUTE)
                status_callback(
                    "error", f"Peer did not start the transfer within {minutes} minutes"
                )
                self._cancel_transfers(client, spec, remove=True)
                return None

            status_callback("downloading", _progress_message(snap, spec, percent))
            if cancel_flag.wait(timeout=poll_interval):
                break

        # Cancelled
        self._cancel_transfers(client, spec, remove=True)
        status_callback("cancelled", "Cancelled")
        return None

    def _current_transfer_ids(self, client: SlskdClient, spec: SlskdDownloadSpec) -> list[str]:
        try:
            transfers = client.list_downloads(spec.username)
        except _SLSKD_ERRORS:
            return []
        wanted = set(spec.filenames)
        return [
            str(t["id"])
            for t in transfers
            if t.get("id") and str(t.get("filename") or "") in wanted and not t.get("removed")
        ]

    def _cancel_transfers(
        self, client: SlskdClient, spec: SlskdDownloadSpec, *, remove: bool
    ) -> None:
        """Cancel the release's transfers and, if asked, drop their records too.

        slskd's DELETE only removes a transfer that is already complete; on a live one
        it cancels and leaves a "Completed, Cancelled" record behind. So removal takes a
        second pass once the cancellations have landed.
        """
        self._delete_transfers(client, spec, remove=remove)
        if not remove:
            return
        for _ in range(CANCEL_REMOVE_PASSES):
            remaining = self._current_transfer_ids(client, spec)
            if not remaining:
                return
            self._cancel_remove_wait()
            self._delete_transfers(client, spec, remove=True)

    def _cancel_remove_wait(self) -> None:
        time.sleep(CANCEL_REMOVE_INTERVAL)

    def _delete_transfers(
        self, client: SlskdClient, spec: SlskdDownloadSpec, *, remove: bool
    ) -> None:
        for transfer_id in self._current_transfer_ids(client, spec):
            try:
                client.cancel_transfer(spec.username, transfer_id, remove=remove)
            except _SLSKD_ERRORS as e:
                logger.warning("Failed to cancel slskd transfer %s: %s", transfer_id, e)

    def _wait_for_files(
        self,
        root: Path,
        spec: SlskdDownloadSpec,
        cancel_flag: Event,
        status_callback: Callable[[str, str | None], None],
    ) -> list[Path] | None:
        paths: list[Path] = []
        for filename in spec.filenames:
            path = local_path_for(root, filename)
            if path is None:
                status_callback("error", f"Refusing unsafe Soulseek file name: {filename}")
                return None
            paths.append(path)

        retry_interval = self._completed_path_retry_interval()
        timeout = self._completed_path_timeout_seconds()
        attempts = (
            1
            if retry_interval <= 0 or timeout <= 0
            else int(math.ceil(timeout / retry_interval)) + 1
        )

        missing: list[Path] = list(paths)
        for attempt in range(1, attempts + 1):
            if cancel_flag.is_set():
                return None
            missing = [p for p in paths if not run_blocking_io(p.exists)]
            if not missing:
                return paths
            if attempt < attempts:
                status_callback("locating", "Waiting for completed files...")
                if cancel_flag.wait(timeout=retry_interval):
                    return None

        first_missing = missing[0]
        logger.error("slskd completed file not found: %s (downloads root %s)", first_missing, root)
        status_callback(
            "error",
            f"Completed file not found at '{first_missing}'. Mount slskd's downloads folder "
            "into Shelfmark and set 'Downloads Path' (or a remote path mapping for 'slskd') "
            "to where it is visible.",
        )
        return None

    def _finalize(self, task: DownloadTask, local_paths: list[Path]) -> str | None:
        if len(local_paths) == 1:
            logger.debug("slskd download complete: %s", local_paths[0])
            return str(local_paths[0])

        # Several files share slskd's per-folder directory with anything else that was
        # ever downloaded from a folder of the same name, so gather just ours.
        from shelfmark.download.staging import STAGE_MOVE, build_staging_dir, stage_path

        staging_dir = build_staging_dir("slskd", task.task_id)
        for path in local_paths:
            stage_path(path, staging_dir, STAGE_MOVE)
        logger.debug("slskd download staged %d files into %s", len(local_paths), staging_dir)
        return str(staging_dir)

    # ── isolated mode ─────────────────────────────────────────────────────────
    #
    # Several apps can share one slskd and any of them may clear its list of finished
    # transfers, so this path never depends on that list. Each download gets a folder of its
    # own (a batch with a relative destination), every transfer is read by its id, and the
    # files on disk decide whether it finished. The finished files stay where slskd put them
    # (still shared); the orchestrator copies them out because the folder is the client's.

    def _isolate_enabled(self) -> bool:
        return _bool_setting("SLSKD_ISOLATE_DOWNLOADS", True)

    def _keep_completed(self) -> bool:
        return _bool_setting("SLSKD_KEEP_COMPLETED", True)

    def _destination_for(self, task: DownloadTask) -> str:
        prefix = _clean_prefix(config.get("SLSKD_DESTINATION_PREFIX", DEFAULT_DESTINATION_PREFIX))
        return f"{prefix}/{_safe_name(task.task_id)}"

    def _clock(self) -> float:
        return time.monotonic()

    def _download_isolated(
        self,
        task: DownloadTask,
        spec: SlskdDownloadSpec,
        client: SlskdClient,
        cancel_flag: Event,
        progress_callback: Callable[[float], None],
        status_callback: Callable[[str, str | None], None],
    ) -> object:
        """Run the download in a folder of its own. Returns the folder path, None on failure,
        or ``_USE_ORIGINAL_ENQUEUE`` when slskd has no batch endpoint."""
        root, root_error = resolve_local_download_root(client)
        if root is None:
            status_callback("error", root_error or "Could not locate slskd downloads")
            return None
        destination = self._destination_for(task)
        folder = root.joinpath(*destination.split("/"))
        expected = [int(f.get("size") or 0) for f in spec.files]

        # A restart or retry finds the files already there: nothing to download again.
        if _folder_complete(folder, expected):
            logger.info("slskd: %s already holds the files for %s", folder, task.task_id)
            progress_callback(100.0)
            return self._finish_isolated(task, client, spec.username, [], folder)

        status_callback("resolving", "Sending to slskd")
        try:
            batch = client.enqueue_batch(spec.username, spec.files, destination)
        except SlskdBatchUnsupportedError:
            return _USE_ORIGINAL_ENQUEUE
        except _SLSKD_ERRORS as e:
            logger.exception("Failed to enqueue slskd batch")
            status_callback("error", f"Failed to add to slskd: {e}")
            return None

        ids = {
            str(t.get("filename") or ""): str(t["id"])
            for t in batch["transfers"]
            if t.get("id") and t.get("filename")
        }
        failures = batch["failures"]
        if failures:
            ids, unresolved = self._rejoin_refused(client, spec, ids, failures)
            if unresolved:
                first = unresolved[0].get("message") or "unknown reason"
                status_callback("error", f"slskd refused {len(unresolved)} file(s): {first}")
                self._cancel_ids(client, spec.username, list(ids.values()))
                return None
        if not ids:
            status_callback("error", "slskd did not queue any of the files")
            return None

        return self._poll_isolated(
            task,
            spec,
            client,
            ids,
            folder,
            expected,
            cancel_flag,
            progress_callback,
            status_callback,
        )

    def _rejoin_refused(
        self,
        client: SlskdClient,
        spec: SlskdDownloadSpec,
        ids: dict[str, str],
        failures: list[dict[str, Any]],
    ) -> tuple[dict[str, str], list[dict[str, Any]]]:
        """slskd refuses a file it already has queued or running. Find that transfer by name
        in the peer's list and join it instead of failing. The list is only used here, to
        discover an id; progress is always read by id."""
        try:
            listed = client.list_downloads(spec.username)
        except _SLSKD_ERRORS:
            listed = []
        by_name = {
            str(t.get("filename") or ""): str(t["id"])
            for t in listed
            if t.get("id") and not t.get("removed")
        }
        unresolved: list[dict[str, Any]] = []
        joined = dict(ids)
        for failure in failures:
            name = str(failure.get("filename") or "")
            if name in by_name:
                joined[name] = by_name[name]
            else:
                unresolved.append(failure)
        return joined, unresolved

    def _poll_isolated(
        self,
        task: DownloadTask,
        spec: SlskdDownloadSpec,
        client: SlskdClient,
        ids: dict[str, str],
        folder: Path,
        expected: list[int],
        cancel_flag: Event,
        progress_callback: Callable[[float], None],
        status_callback: Callable[[str, str | None], None],
    ) -> str | None:
        poll_interval = self._poll_interval()
        queue_timeout = self._queue_timeout_seconds()
        started = self._clock()
        grace_at: float | None = None
        missing_polls = 0
        sizes = {str(f.get("filename") or ""): int(f.get("size") or 0) for f in spec.files}
        total = sum(sizes.values())
        transfer_ids = list(ids.values())

        try:
            while not cancel_flag.is_set():
                now = self._clock()
                records: dict[str, dict[str, Any] | None] = {}
                try:
                    for name, transfer_id in ids.items():
                        records[name] = client.get_transfer(spec.username, transfer_id)
                except _SLSKD_ERRORS as e:
                    logger.warning("slskd status check failed: %s", e)
                    status_callback("resolving", "Waiting for slskd...")
                    if cancel_flag.wait(timeout=poll_interval):
                        break
                    continue

                present = {n: r for n, r in records.items() if r is not None}
                gone = [n for n, r in records.items() if r is None]
                failed = [
                    (n, r)
                    for n, r in present.items()
                    if transfer_is_complete(r.get("state"))
                    and not transfer_succeeded(r.get("state"))
                ]
                if failed:
                    name, record = failed[0]
                    outcome = str(record.get("state") or "").split(",", 1)[-1].strip()
                    message = f"Peer transfer failed: {outcome or record.get('state')}"
                    if len(spec.files) > 1:
                        message += f" ({split_remote_path(name)[1]})"
                    logger.error(
                        "slskd transfer failed for %s: %s", task.task_id, record.get("state")
                    )
                    status_callback("error", message)
                    self._cancel_ids(client, spec.username, transfer_ids)
                    return None

                active = {
                    n: r for n, r in present.items() if not transfer_succeeded(r.get("state"))
                }
                done_bytes = sum(
                    sizes.get(n, 0) if n not in active else int(r.get("bytesTransferred") or 0)
                    for n, r in present.items()
                ) + sum(sizes.get(n, 0) for n in gone)
                percent = max(0.0, min(100.0, (done_bytes / total * 100) if total > 0 else 0.0))

                queued = [r for r in active.values() if self._is_queued(r.get("state"))]
                if queued and (grace_at is None or now - grace_at >= QUEUE_GRACE_RENEW_SECONDS):
                    request_activity_grace(status_callback, QUEUE_GRACE_SECONDS)
                    grace_at = now
                elif not queued and grace_at is not None:
                    release_activity_grace(status_callback)
                    grace_at = None

                if not active:
                    # Every transfer succeeded or slskd no longer lists it: the files decide.
                    if _folder_complete(folder, expected):
                        progress_callback(100.0)
                        return self._finish_isolated(
                            task, client, spec.username, transfer_ids, folder
                        )
                    missing_polls += 1
                    if missing_polls >= MAX_MISSING_POLLS:
                        return self._confirm_by_events(
                            task,
                            spec,
                            client,
                            gone,
                            folder,
                            expected,
                            transfer_ids,
                            cancel_flag,
                            progress_callback,
                            status_callback,
                        )
                    status_callback("locating", "Waiting for completed files...")
                else:
                    missing_polls = 0
                    progress_callback(percent)
                    if queue_timeout > 0 and done_bytes == 0 and now - started > queue_timeout:
                        minutes = int(queue_timeout // _SECONDS_PER_MINUTE)
                        status_callback(
                            "error", f"Peer did not start the transfer within {minutes} minutes"
                        )
                        self._cancel_ids(client, spec.username, transfer_ids)
                        return None
                    status_callback("downloading", self._isolated_message(active, spec, percent))

                if cancel_flag.wait(timeout=poll_interval):
                    break

            self._cancel_ids(client, spec.username, transfer_ids)
            status_callback("cancelled", "Cancelled")
            return None
        finally:
            if grace_at is not None:
                release_activity_grace(status_callback)

    @staticmethod
    def _is_queued(state: object) -> bool:
        text = str(state or "").lower()
        return "queued" in text or "requested" in text

    @staticmethod
    def _isolated_message(
        active: dict[str, dict[str, Any]], spec: SlskdDownloadSpec, percent: float
    ) -> str:
        running = [r for r in active.values() if str(r.get("state") or "").startswith("InProgress")]
        if running:
            speed = sum(float(r.get("averageSpeed") or 0) for r in running)
            msg = f"{percent:.0f}%"
            if speed > 0:
                msg += f" ({speed / _BYTES_PER_MB:.1f} MB/s)"
            if len(spec.files) > 1:
                msg += f" - {len(running)} of {len(spec.files)} files transferring"
            return msg
        positions = [
            int(r["placeInQueue"])
            for r in active.values()
            if isinstance(r.get("placeInQueue"), int) and r["placeInQueue"] > 0
        ]
        if positions:
            return f"Queued at peer (position {min(positions)})"
        return "Queued at peer"

    def _confirm_by_events(
        self,
        task: DownloadTask,
        spec: SlskdDownloadSpec,
        client: SlskdClient,
        gone: list[str],
        folder: Path,
        expected: list[int],
        transfer_ids: list[str],
        cancel_flag: Event,
        progress_callback: Callable[[float], None],
        status_callback: Callable[[str, str | None], None],
    ) -> str | None:
        """The transfers vanished and the files are not all here yet. slskd's stored events
        outlive anyone clearing the list: if they say the files finished, wait for them to
        appear; if they do not, the transfer really is lost."""
        try:
            finished = _completed_remote_files(client.get_events(limit=200))
        except _SLSKD_ERRORS:
            finished = set()
        if gone and all(name in finished for name in gone):
            retry_interval = self._completed_path_retry_interval()
            timeout = self._completed_path_timeout_seconds()
            attempts = (
                1
                if retry_interval <= 0 or timeout <= 0
                else int(math.ceil(timeout / retry_interval)) + 1
            )
            for attempt in range(1, attempts + 1):
                if cancel_flag.is_set():
                    status_callback("cancelled", "Cancelled")
                    return None
                if _folder_complete(folder, expected):
                    progress_callback(100.0)
                    return self._finish_isolated(task, client, spec.username, transfer_ids, folder)
                if attempt < attempts:
                    status_callback("locating", "Waiting for completed files...")
                    if cancel_flag.wait(timeout=retry_interval):
                        status_callback("cancelled", "Cancelled")
                        return None
        logger.error(
            "slskd transfers for %s vanished and the files are not in %s", task.task_id, folder
        )
        status_callback(
            "error",
            "Transfer disappeared from slskd and its files were not found in "
            f"'{folder}'. Check that slskd's downloads folder is mounted into Shelfmark.",
        )
        return None

    def _finish_isolated(
        self,
        task: DownloadTask,
        client: SlskdClient,
        username: str,
        transfer_ids: list[str],
        folder: Path,
    ) -> str:
        task.original_download_path = str(folder)
        self._isolated_refs[task.task_id] = (client, username, transfer_ids, folder)
        logger.debug("slskd download complete: %s", folder)
        return str(folder)

    def _cancel_ids(self, client: SlskdClient, username: str, transfer_ids: list[str]) -> None:
        """Cancel transfers and drop their records. slskd removes only a completed record, so a
        live transfer needs a second pass once the cancellation has landed."""
        for attempt in range(CANCEL_REMOVE_PASSES):
            for transfer_id in transfer_ids:
                try:
                    client.cancel_transfer(username, transfer_id, remove=True)
                except _SLSKD_ERRORS as e:
                    logger.warning("Failed to cancel slskd transfer %s: %s", transfer_id, e)
            if attempt + 1 < CANCEL_REMOVE_PASSES:
                self._cancel_remove_wait()

    # ── after post-processing ─────────────────────────────────────────────────

    def post_process_cleanup(self, task: DownloadTask, *, success: bool) -> None:
        isolated = self._isolated_refs.pop(task.task_id, None)
        if isolated is not None:
            self._cleanup_isolated(isolated, success=success)
            return
        refs = self._cleanup_refs.pop(task.task_id, None)
        if refs is None or not success:
            return
        client, username, transfer_ids, local_paths = refs

        if config.get("SLSKD_REMOVE_COMPLETED", True):
            for transfer_id in transfer_ids:
                try:
                    client.cancel_transfer(username, transfer_id, remove=True)
                except _SLSKD_ERRORS as e:
                    logger.debug("Failed to remove slskd transfer %s: %s", transfer_id, e)

        # The orchestrator moved the file(s) out; drop the per-folder directory slskd
        # created if nothing else is left in it.
        for parent in {p.parent for p in local_paths}:
            try:
                if run_blocking_io(parent.is_dir) and not any(run_blocking_io(parent.iterdir)):
                    run_blocking_io(parent.rmdir)
            except OSError as e:
                logger.debug("Could not remove empty slskd folder %s: %s", parent, e)

    def _cleanup_isolated(
        self, refs: tuple[SlskdClient, str, list[str], Path], *, success: bool
    ) -> None:
        """After the orchestrator copied the files out. By default everything stays in slskd,
        so the files remain shared; with ``SLSKD_KEEP_COMPLETED`` off the records and the
        folder are removed. A failed import leaves it all for a retry."""
        client, username, transfer_ids, folder = refs
        if not success or self._keep_completed():
            return
        for transfer_id in transfer_ids:
            try:
                client.cancel_transfer(username, transfer_id, remove=True)
            except _SLSKD_ERRORS as e:
                logger.debug("Failed to remove slskd transfer %s: %s", transfer_id, e)
        try:
            if run_blocking_io(folder.is_dir):
                run_blocking_io(lambda: shutil.rmtree(folder))
        except OSError as e:
            logger.debug("Could not remove slskd folder %s: %s", folder, e)

    def cancel(self, task_id: str) -> bool:
        """Cancellation is driven by the queue's cancel flag inside ``download``."""
        logger.debug("Cancel requested for slskd task: %s", task_id)
        self._cleanup_refs.pop(task_id, None)
        self._isolated_refs.pop(task_id, None)
        return True
