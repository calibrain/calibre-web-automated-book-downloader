"""Download queue orchestration and worker management.

Two-stage architecture: handlers stage to TMP_DIR, orchestrator delivers to the configured destination
with archive extraction and custom script support.
"""

import os
import random
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from email.utils import parseaddr
from pathlib import Path
from threading import Event, Lock
from typing import TYPE_CHECKING, Any

from shelfmark.core.config import config
from shelfmark.core.logger import setup_logger
from shelfmark.core.models import DownloadTask, QueueStatus, SearchMode
from shelfmark.core.queue import book_queue
from shelfmark.core.request_helpers import (
    normalize_optional_text,
    normalize_positive_int,
)
from shelfmark.core.utils import is_audiobook as check_audiobook
from shelfmark.core.utils import transform_cover_url
from shelfmark.download.activity import parse_activity_grace
from shelfmark.download.fs import run_blocking_io
from shelfmark.download.postprocess.pipeline import is_torrent_source, safe_cleanup_path
from shelfmark.download.postprocess.router import post_process_download
from shelfmark.release_sources import (
    HandoffResult,
    get_handler,
    get_source,
    get_source_display_name,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

logger = setup_logger(__name__)
_RNG = random.SystemRandom()


# =============================================================================
# Task Download and Processing
# =============================================================================
#
# Post-download processing (staging, extraction, transfers, cleanup) lives in
# `shelfmark.download.postprocess`.


# WebSocket manager (initialized by app.py)
# Track whether WebSocket is available for status reporting
WEBSOCKET_AVAILABLE = True
try:
    from shelfmark.api.websocket import ws_manager
except ImportError:
    logger.exception("WebSocket unavailable - real-time updates disabled")
    ws_manager = None
    WEBSOCKET_AVAILABLE = False

# Progress update throttling - track last broadcast time per book
_progress_last_broadcast: dict[str, float] = {}
_progress_lock = Lock()

# Stall detection - track last activity time per download
_last_activity: dict[str, float] = {}
_last_progress_value: dict[str, float] = {}
# De-duplicate status updates (keep-alive updates shouldn't spam clients)
_last_status_event: dict[str, tuple[str, str | None]] = {}
STALL_TIMEOUT = 300  # 5 minutes without progress/status update = stalled
# Absolute deadlines (time.time()) until which stall detection is suppressed for a task.
# Long single-shot operations (protection bypass, etc.) declare their own upper bound via
# `shelfmark.download.activity` instead of faking progress. See set_activity_grace().
_activity_grace: dict[str, float] = {}
# A caller cannot buy immortality: the largest grace any operation may request. Must stay
# above the largest budget any caller can declare (see http._bypass_grace_seconds).
_MAX_ACTIVITY_GRACE_SECONDS = 960.0
COORDINATOR_LOOP_ERROR_RETRY_DELAY = 1.0
# Ceiling for the exponential backoff applied to repeated coordinator loop failures.
_COORDINATOR_LOOP_ERROR_MAX_DELAY = 30.0
_PROGRESS_BROADCAST_START_PERCENT = 1
_PROGRESS_BROADCAST_COMPLETE_PERCENT = 99
_PROGRESS_BROADCAST_MIN_DELTA = 10


def _is_plain_email_address(value: str) -> bool:
    parsed = parseaddr(value or "")[1]
    return bool(parsed) and "@" in parsed and parsed == value


def _resolve_email_destination(
    user_id: int | None = None,
) -> tuple[str | None, str | None]:
    """Resolve the destination email address for email output mode.

    Returns:
      (email_to, error_message)

    """
    configured_recipient = str(config.get("EMAIL_RECIPIENT", "", user_id=user_id) or "").strip()
    if configured_recipient:
        if _is_plain_email_address(configured_recipient):
            return configured_recipient, None
        return None, "Configured email recipient is invalid"

    return None, None


def _parse_release_search_mode(value: object) -> SearchMode:
    if isinstance(value, SearchMode):
        return value
    if value is None:
        return SearchMode.UNIVERSAL
    if isinstance(value, str):
        try:
            return SearchMode(value.strip().lower())
        except ValueError as exc:
            msg = f"Invalid search_mode: {value}"
            raise ValueError(msg) from exc
    msg = f"Invalid search_mode: {value}"
    raise ValueError(msg)


def _source_unavailable_message(source_name: str) -> str | None:
    source = get_source(source_name)
    if source.is_available():
        return None
    return f"{source.display_name} is unavailable. Enable and configure the source in Settings."


def _optional_number(value: object) -> float | None:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _optional_positive_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, float):
        parsed = int(value)
    elif isinstance(value, str):
        try:
            parsed = int(value)
        except ValueError:
            return None
    else:
        return None
    return parsed if parsed > 0 else None


def _config_float(value: object, default: float) -> float:
    if isinstance(value, bool) or value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return default
    return default


def _build_retry_resolution_fields(
    release_data: dict[str, Any],
) -> dict[str, Any]:
    """Persist generic resolved-download data needed for restart-safe retries."""
    extra = release_data.get("extra")
    if not isinstance(extra, dict):
        extra = {}

    retry_download_url = normalize_optional_text(release_data.get("download_url"))
    protocol = normalize_optional_text(release_data.get("protocol"))
    source = normalize_optional_text(release_data.get("source"))
    retry_source_context: dict[str, Any] = {}
    if source is not None:
        handler = get_handler(source)
        source_retry_fields = handler.build_retry_resolution_fields(release_data)
        if "retry_download_url" in source_retry_fields:
            retry_download_url = normalize_optional_text(
                source_retry_fields.get("retry_download_url")
            )
        if "retry_download_protocol" in source_retry_fields:
            protocol = normalize_optional_text(source_retry_fields.get("retry_download_protocol"))
        raw_retry_source_context = source_retry_fields.get("retry_source_context")
        if isinstance(raw_retry_source_context, dict):
            retry_source_context = dict(raw_retry_source_context)

    ratio_limit = _optional_number(release_data.get("ratio_limit"))
    if ratio_limit is None and config.get("PROWLARR_USE_SEED_PREFERENCES", False):
        ratio_limit = _optional_number(extra.get("configured_ratio_limit"))

    seeding_time_limit_minutes = _optional_positive_int(
        release_data.get("seeding_time_limit_minutes")
    )
    if seeding_time_limit_minutes is None and config.get("PROWLARR_USE_SEED_PREFERENCES", False):
        seeding_time_limit_minutes = _optional_positive_int(
            extra.get("configured_seed_time_minutes")
        )

    return {
        "retry_download_url": retry_download_url,
        "retry_download_protocol": protocol.lower() if protocol is not None else None,
        "retry_release_name": normalize_optional_text(release_data.get("title")),
        "retry_expected_hash": normalize_optional_text(
            release_data.get("expected_hash") or extra.get("info_hash")
        ),
        "retry_ratio_limit": ratio_limit,
        "retry_seeding_time_limit_minutes": seeding_time_limit_minutes,
        "retry_source_context": retry_source_context,
        "can_retry_without_staged_source": True,
    }


def queue_release(
    release_data: dict,
    priority: int = 0,
    user_id: int | None = None,
    username: str | None = None,
) -> tuple[bool, str | None]:
    """Add a release to the download queue. Returns (success, error_message)."""
    try:
        source = release_data["source"]
        unavailable_message = _source_unavailable_message(source)
        if unavailable_message:
            logger.warning("Rejected queue request for unavailable source %s", source)
            return False, unavailable_message

        extra = release_data.get("extra", {})
        raw_request_id = release_data.get("_request_id")
        request_id: int | None = None
        if isinstance(raw_request_id, int) and raw_request_id > 0:
            request_id = raw_request_id
        search_mode = _parse_release_search_mode(release_data.get("search_mode"))

        # Get author, year, preview, and content_type from top-level (preferred) or extra (fallback)
        author = release_data.get("author") or extra.get("author")
        year = release_data.get("year") or extra.get("year")
        preview = release_data.get("preview") or extra.get("preview")
        content_type = release_data.get("content_type") or extra.get("content_type")
        source_url_raw = (
            release_data.get("download_url")
            or release_data.get("source_url")
            or release_data.get("info_url")
            or extra.get("detail_url")
            or extra.get("source_url")
        )
        source_url = source_url_raw.strip() if isinstance(source_url_raw, str) else None
        if source_url == "":
            source_url = None

        # Get series info for library naming templates
        series_name = release_data.get("series_name") or extra.get("series_name")
        series_position = release_data.get("series_position") or extra.get("series_position")
        subtitle = release_data.get("subtitle") or extra.get("subtitle")
        language = release_data.get("language") or extra.get("language")
        multi_book = bool(release_data.get("multi_book") or extra.get("multi_book"))
        book_plan = _normalize_book_plan(release_data.get("book_plan") or extra.get("book_plan"))

        books_output_mode = (
            str(config.get("BOOKS_OUTPUT_MODE", "folder", user_id=user_id) or "folder")
            .strip()
            .lower()
        )
        is_audiobook = check_audiobook(content_type)

        output_mode = "folder" if is_audiobook else books_output_mode
        output_args: dict[str, Any] = {}
        retry_resolution_fields = _build_retry_resolution_fields(release_data)

        if output_mode == "email" and not is_audiobook:
            email_to, email_error = _resolve_email_destination(user_id=user_id)
            if email_error:
                return False, email_error
            if email_to:
                output_args = {"to": email_to}

        # Create a source-agnostic download task from release data
        task = DownloadTask(
            task_id=release_data["source_id"],
            source=source,
            title=release_data.get("title", "Unknown"),
            author=author,
            year=year,
            format=release_data.get("format"),
            size=release_data.get("size"),
            preview=preview,
            content_type=content_type,
            source_url=source_url,
            series_name=series_name,
            series_position=series_position,
            subtitle=subtitle,
            language=language,
            multi_book=multi_book or book_plan is not None,
            book_plan=book_plan,
            search_mode=search_mode,
            output_mode=output_mode,
            output_args=output_args,
            priority=priority,
            user_id=user_id,
            username=username,
            request_id=request_id,
            **retry_resolution_fields,
        )

        if not book_queue.add(task):
            logger.info("Release already in queue: %s", task.title)
            return False, "Release is already in the download queue"

        logger.info("Release queued with priority %s: %s", priority, task.title)

        # Broadcast status update via WebSocket
        if ws_manager:
            ws_manager.broadcast_status_update(queue_status())

    except ValueError as e:
        error_msg = str(e)
        logger.warning(error_msg)
        return False, error_msg
    except KeyError as e:
        error_msg = f"Missing required field in release data: {e}"
        logger.warning(error_msg)
        return False, error_msg
    except (AttributeError, OSError, RuntimeError, TypeError) as e:
        error_msg = f"Error queueing release: {e}"
        logger.error_trace(error_msg)
        return False, error_msg
    else:
        return True, None


def queue_status(user_id: int | None = None) -> dict[str, dict[str, Any]]:
    """Get current status of the download queue."""
    status = book_queue.get_status(user_id=user_id)
    for tasks in status.values():
        for task in tasks.values():
            if task.download_path and not run_blocking_io(os.path.exists, task.download_path):
                task.download_path = None

    # Convert Enum keys to strings and DownloadTask objects to dicts for JSON serialization
    return {
        status_type.value: {
            task_id: _task_to_dict(task, current_status=status_type)
            for task_id, task in tasks.items()
        }
        for status_type, tasks in status.items()
    }


def get_book_data(task_id: str) -> tuple[bytes | None, DownloadTask | None]:
    """Get downloaded file data for a specific task."""
    task = None
    try:
        task = book_queue.get_task(task_id)
        if not task:
            return None, None

        path = task.download_path
        if not path:
            return None, task

        with Path(path).open("rb") as f:
            return f.read(), task
    except OSError as e:
        logger.error_trace(f"Error getting book data: {e}")
        if task:
            task.download_path = None
        return None, task


def _has_staged_retry_source(task: DownloadTask) -> bool:
    """Whether a failed task still has a staged file available for retry."""
    staged_path = task.staged_path.strip() if isinstance(task.staged_path, str) else ""
    if not staged_path:
        return False
    try:
        return run_blocking_io(Path(staged_path).exists)
    except OSError:
        return False


def _has_fresh_retry_context(task: DownloadTask) -> bool:
    """Whether the task can restart without relying on a staged file."""
    return bool(getattr(task, "can_retry_without_staged_source", True))


def can_retry_download_task(
    task: DownloadTask | None,
    status: QueueStatus | None,
) -> bool:
    """Whether the task can be manually retried from the Activity UI."""
    if task is None or status not in (QueueStatus.ERROR, QueueStatus.CANCELLED):
        return False

    if task.request_id is None:
        return _has_staged_retry_source(task) or _has_fresh_retry_context(task)

    if status == QueueStatus.CANCELLED:
        return _has_fresh_retry_context(task)

    return _has_staged_retry_source(task)


def _normalize_book_plan(value: object) -> list[dict[str, Any]] | None:
    """Keep only well-formed pack books: a title plus a non-empty list of file paths."""
    if not isinstance(value, list):
        return None
    books: list[dict[str, Any]] = []
    for entry in value:
        if not isinstance(entry, dict):
            continue
        title = normalize_optional_text(entry.get("title"))
        raw_files = entry.get("files")
        if title is None or not isinstance(raw_files, list):
            continue
        files = [f for f in raw_files if isinstance(f, str) and f.strip()]
        if not files:
            continue
        year = entry.get("year")
        books.append(
            {
                "title": title,
                "series_position": _optional_number(entry.get("series_position")),
                "year": year if isinstance(year, int) and not isinstance(year, bool) else None,
                "files": files,
            }
        )
    return books or None


def serialize_task_for_retry(task: DownloadTask) -> dict[str, Any]:
    """Serialize the task state needed for restart-safe retries."""
    raw_search_mode = getattr(task, "search_mode", None)
    search_mode: str | None = None
    if isinstance(raw_search_mode, SearchMode):
        search_mode = raw_search_mode.value
    elif isinstance(raw_search_mode, str):
        normalized_search_mode = raw_search_mode.strip().lower()
        search_mode = normalized_search_mode or None

    raw_output_args = getattr(task, "output_args", None)
    raw_retry_source_context = getattr(task, "retry_source_context", None)

    return {
        "task_id": getattr(task, "task_id", None),
        "source": getattr(task, "source", None),
        "title": getattr(task, "title", None),
        "author": getattr(task, "author", None),
        "year": getattr(task, "year", None),
        "format": getattr(task, "format", None),
        "size": getattr(task, "size", None),
        "preview": getattr(task, "preview", None),
        "content_type": getattr(task, "content_type", None),
        "source_url": getattr(task, "source_url", None),
        "series_name": getattr(task, "series_name", None),
        "series_position": getattr(task, "series_position", None),
        "subtitle": getattr(task, "subtitle", None),
        "language": getattr(task, "language", None),
        "search_mode": search_mode,
        "multi_book": bool(getattr(task, "multi_book", False)),
        "book_plan": _normalize_book_plan(getattr(task, "book_plan", None)),
        "output_mode": getattr(task, "output_mode", None),
        "output_args": dict(raw_output_args) if isinstance(raw_output_args, dict) else {},
        "user_id": getattr(task, "user_id", None),
        "username": getattr(task, "username", None),
        "request_id": getattr(task, "request_id", None),
        "staged_path": getattr(task, "staged_path", None),
        "retry_download_url": getattr(task, "retry_download_url", None),
        "retry_download_protocol": getattr(task, "retry_download_protocol", None),
        "retry_release_name": getattr(task, "retry_release_name", None),
        "retry_expected_hash": getattr(task, "retry_expected_hash", None),
        "retry_ratio_limit": getattr(task, "retry_ratio_limit", None),
        "retry_seeding_time_limit_minutes": getattr(task, "retry_seeding_time_limit_minutes", None),
        "retry_source_context": (
            dict(raw_retry_source_context) if isinstance(raw_retry_source_context, dict) else {}
        ),
        "can_retry_without_staged_source": bool(
            getattr(task, "can_retry_without_staged_source", True)
        ),
    }


def _restore_task_from_retry_payload(payload: object) -> DownloadTask | None:
    if not isinstance(payload, dict):
        return None

    task_id = normalize_optional_text(payload.get("task_id"))
    source = normalize_optional_text(payload.get("source"))
    title = normalize_optional_text(payload.get("title"))
    if task_id is None or source is None or title is None:
        return None

    search_mode = None
    raw_search_mode = payload.get("search_mode")
    if raw_search_mode is not None:
        try:
            search_mode = _parse_release_search_mode(raw_search_mode)
        except ValueError:
            search_mode = None

    output_args = payload.get("output_args")
    retry_source_context = payload.get("retry_source_context")

    return DownloadTask(
        task_id=task_id,
        source=source,
        title=title,
        author=normalize_optional_text(payload.get("author")),
        year=normalize_optional_text(payload.get("year")),
        format=normalize_optional_text(payload.get("format")),
        size=normalize_optional_text(payload.get("size")),
        preview=normalize_optional_text(payload.get("preview")),
        content_type=normalize_optional_text(payload.get("content_type")),
        source_url=normalize_optional_text(payload.get("source_url")),
        series_name=normalize_optional_text(payload.get("series_name")),
        series_position=_optional_number(payload.get("series_position")),
        subtitle=normalize_optional_text(payload.get("subtitle")),
        language=normalize_optional_text(payload.get("language")),
        search_mode=search_mode,
        multi_book=bool(payload.get("multi_book", False)),
        book_plan=_normalize_book_plan(payload.get("book_plan")),
        output_mode=normalize_optional_text(payload.get("output_mode")),
        output_args=dict(output_args) if isinstance(output_args, dict) else {},
        user_id=normalize_positive_int(payload.get("user_id")),
        username=normalize_optional_text(payload.get("username")),
        request_id=normalize_positive_int(payload.get("request_id")),
        staged_path=normalize_optional_text(payload.get("staged_path")),
        retry_download_url=normalize_optional_text(payload.get("retry_download_url")),
        retry_download_protocol=normalize_optional_text(payload.get("retry_download_protocol")),
        retry_release_name=normalize_optional_text(payload.get("retry_release_name")),
        retry_expected_hash=normalize_optional_text(payload.get("retry_expected_hash")),
        retry_ratio_limit=_optional_number(payload.get("retry_ratio_limit")),
        retry_seeding_time_limit_minutes=_optional_positive_int(
            payload.get("retry_seeding_time_limit_minutes")
        ),
        retry_source_context=(
            dict(retry_source_context) if isinstance(retry_source_context, dict) else {}
        ),
        can_retry_without_staged_source=bool(payload.get("can_retry_without_staged_source", True)),
    )


def retry_persisted_download(
    payload: object,
    *,
    final_status: object,
    priority: int = -10,
) -> tuple[bool, str | None]:
    """Retry a persisted download row after the in-memory task has been lost."""
    task = _restore_task_from_retry_payload(payload)
    if task is None:
        return False, "Download cannot be retried"

    normalized_status = normalize_optional_text(final_status)
    if normalized_status is None:
        return False, "Download cannot be retried"
    normalized_status = normalized_status.lower()
    if normalized_status not in {"active", "error", "cancelled"}:
        return False, "Download cannot be retried"

    has_staged_retry_source = _has_staged_retry_source(task)
    has_fresh_retry_context = _has_fresh_retry_context(task)

    if normalized_status in {"active", "cancelled"} and not has_fresh_retry_context:
        return False, "Download cannot be retried"

    if task.request_id is not None and normalized_status == "error" and not has_staged_retry_source:
        return False, "Request-linked downloads must be retried from requests"

    if (
        task.request_id is None
        and normalized_status == "error"
        and not has_staged_retry_source
        and not has_fresh_retry_context
    ):
        return False, "Download cannot be retried"

    task.priority = priority
    task.status_message = None
    _clear_task_error_state(task)

    if not book_queue.add(task):
        return False, "Failed to requeue download"

    book_queue.update_status_message(task.task_id, "Retrying now")

    if ws_manager:
        ws_manager.broadcast_status_update(queue_status())

    return True, None


def _task_to_dict(
    task: DownloadTask,
    current_status: QueueStatus | None = None,
) -> dict[str, Any]:
    """Convert DownloadTask to dict for frontend, transforming cover URLs."""
    # Transform external preview URLs to local proxy URLs
    preview = transform_cover_url(task.preview, task.task_id)
    retry_status = current_status or book_queue.get_task_status(task.task_id)

    return {
        "id": task.task_id,
        "title": task.title,
        "author": task.author,
        "format": task.format,
        "size": task.size,
        "preview": preview,
        "content_type": task.content_type,
        "source": task.source,
        "source_display_name": get_source_display_name(task.source),
        "priority": task.priority,
        "added_time": task.added_time,
        "progress": task.progress,
        "status": retry_status.value if isinstance(retry_status, QueueStatus) else task.status,
        "status_message": task.status_message,
        "download_path": task.download_path,
        "user_id": task.user_id,
        "username": task.username,
        "request_id": task.request_id,
        "retry_available": can_retry_download_task(task, retry_status),
    }


def _clear_task_error_state(task: DownloadTask) -> None:
    task.last_error_message = None
    task.last_error_type = None


def _capture_task_error(
    task: DownloadTask,
    *,
    message: str | None = None,
    exc_type: str | None = None,
) -> None:
    if isinstance(message, str):
        normalized = message.strip()
        if normalized:
            task.last_error_message = normalized
            book_queue.update_status_message(task.task_id, normalized)
    if isinstance(exc_type, str):
        normalized_type = exc_type.strip()
        if normalized_type:
            task.last_error_type = normalized_type


def _format_download_exception_message(exc: BaseException) -> str:
    if isinstance(exc, PermissionError) and "/cwa-book-ingest" in str(exc):
        return "Destination misconfigured. Go to Settings → Downloads to update."
    if isinstance(exc, PermissionError):
        return f"Permission denied: {exc}"
    return f"Download failed: {type(exc).__name__}"


def _download_task(task_id: str, cancel_flag: Event) -> str | None:
    """Download a task via appropriate handler, then post-process to ingest."""
    # Check for cancellation before starting
    if cancel_flag.is_set():
        logger.info("Task %s: cancelled before starting", task_id)
        return None

    task = book_queue.get_task(task_id)
    if not task:
        logger.error("Task not found in queue: %s", task_id)
        return None

    unavailable_message = _source_unavailable_message(task.source)
    if unavailable_message:
        logger.warning("Task %s: source unavailable: %s", task_id, unavailable_message)
        _capture_task_error(
            task,
            message=unavailable_message,
            exc_type="SourceUnavailable",
        )
        return None

    title_label = task.title or "Unknown title"
    logger.info(
        "Task %s: starting download (%s) - %s",
        task_id,
        get_source_display_name(task.source),
        title_label,
    )

    def progress_callback(progress: float) -> None:
        update_download_progress(task_id, progress)

    def status_callback(status: str, message: str | None = None) -> None:
        # Liveness hint from a long single-shot operation, not a user-visible status.
        # Handled here so it never reaches update_download_status (which dedupes status
        # transitions on purpose). See shelfmark.download.activity.
        grace = parse_activity_grace(status, message)
        if grace is not None:
            if grace > 0:
                set_activity_grace(task_id, grace)
            else:
                clear_activity_grace(task_id)
            return

        status_key = status.lower()
        if status_key == "error":
            _capture_task_error(
                task,
                message=message or "Download failed",
                exc_type="StatusCallbackError",
            )
            return
        # Don't propagate terminal statuses to the queue here. Output modules
        # call status_callback("complete") before returning the download path,
        # but _process_single_download needs to set download_path on the task
        # first so the terminal hook captures it for history persistence.
        if status_key in ("complete", "cancelled"):
            if message is not None:
                book_queue.update_status_message(task_id, message)
            return
        update_download_status(task_id, status, message)

    # Get the download handler based on the task's source
    handler = get_handler(task.source)
    temp_file: Path | None = None

    if task.staged_path:
        staged_file = Path(task.staged_path)
        if run_blocking_io(staged_file.exists):
            temp_file = staged_file
            logger.info("Task %s: reusing staged file for retry: %s", task_id, staged_file)
        else:
            task.staged_path = None

    if temp_file is None:
        temp_path = handler.download(
            task,
            cancel_flag,
            progress_callback,
            status_callback,
        )

        # Handler returns temp path - orchestrator handles post-processing
        if not temp_path:
            return None

        if isinstance(temp_path, HandoffResult):
            handoff_path = Path(temp_path.path)
            if not run_blocking_io(handoff_path.exists):
                logger.error("Handler returned non-existent handoff path: %s", handoff_path)
                _capture_task_error(
                    task,
                    message=f"Download file missing: {handoff_path}",
                    exc_type="MissingDownloadPath",
                )
                return None
            status_callback("complete", temp_path.message)
            handler.post_process_cleanup(task, success=True)
            _clear_task_error_state(task)
            return str(handoff_path)

        temp_file = Path(temp_path)
        if not run_blocking_io(temp_file.exists):
            logger.error("Handler returned non-existent path: %s", temp_path)
            _capture_task_error(
                task,
                message=f"Download file missing: {temp_path}",
                exc_type="MissingDownloadPath",
            )
            return None

    # Check cancellation before post-processing
    if cancel_flag.is_set():
        logger.info("Task %s: cancelled before post-processing", task_id)
        if not is_torrent_source(temp_file, task):
            safe_cleanup_path(temp_file, task)
        return None

    logger.info("Task %s: download finished; starting post-processing", task_id)
    logger.debug("Task %s: post-processing input path: %s", task_id, temp_file)
    task.staged_path = str(temp_file)
    preserve_source_on_failure = True

    # Post-processing: output routing + file processing pipeline
    result = post_process_download(
        temp_file,
        task,
        cancel_flag,
        status_callback,
        preserve_source_on_failure=preserve_source_on_failure,
    )

    if cancel_flag.is_set():
        logger.info("Task %s: post-processing cancelled", task_id)
    elif result:
        logger.info("Task %s: post-processing complete", task_id)
        logger.debug("Task %s: post-processing result: %s", task_id, result)
    else:
        logger.warning("Task %s: post-processing failed", task_id)
        if not task.last_error_message:
            _capture_task_error(
                task,
                message="Download failed",
                exc_type="UnknownFailure",
            )

    handler.post_process_cleanup(task, success=bool(result))

    if result:
        task.staged_path = None
        _clear_task_error_state(task)

    return result


def update_download_progress(book_id: str, progress: float) -> None:
    """Update download progress with throttled WebSocket broadcasts."""
    book_queue.update_progress(book_id, progress)

    # Only real progress changes should reset stall detection. Repeated keep-alive
    # polls at the same percentage must not hide a stuck download forever.
    with _progress_lock:
        last_progress = _last_progress_value.get(book_id)
        if last_progress is None or progress != last_progress:
            _last_activity[book_id] = time.time()
        _last_progress_value[book_id] = progress

    # Broadcast progress via WebSocket with throttling
    if ws_manager:
        current_time = time.time()
        progress_update_interval = _config_float(config.DOWNLOAD_PROGRESS_UPDATE_INTERVAL, 1.0)
        should_broadcast = False

        with _progress_lock:
            last_broadcast = _progress_last_broadcast.get(book_id, 0)
            last_progress = _progress_last_broadcast.get(f"{book_id}_progress", 0)
            time_elapsed = current_time - last_broadcast

            # Always broadcast at start (0%) or completion (>=99%)
            should_broadcast = (
                progress <= _PROGRESS_BROADCAST_START_PERCENT
                or progress >= _PROGRESS_BROADCAST_COMPLETE_PERCENT
                or time_elapsed >= progress_update_interval
                or progress - last_progress >= _PROGRESS_BROADCAST_MIN_DELTA
            )

            if should_broadcast:
                _progress_last_broadcast[book_id] = current_time
                _progress_last_broadcast[f"{book_id}_progress"] = progress

        if should_broadcast:
            task = book_queue.get_task(book_id)
            task_user_id = task.user_id if task else None
            ws_manager.broadcast_download_progress(
                book_id, progress, "downloading", user_id=task_user_id
            )


def update_download_status(book_id: str, status: str, message: str | None = None) -> None:
    """Update download status with optional message for UI display."""
    status_key = status.lower()
    try:
        queue_status_enum = QueueStatus(status_key)
    except ValueError:
        return

    with _progress_lock:
        status_event = (status_key, message)
        if _last_status_event.get(book_id) == status_event:
            return
        _last_activity[book_id] = time.time()
        _last_status_event[book_id] = status_event

    # Update status message first so terminal snapshots capture the final message
    # (for example, "Complete" or "Sent to ...") instead of a stale in-progress one.
    if message is not None:
        book_queue.update_status_message(book_id, message)

    book_queue.update_status(book_id, queue_status_enum)

    # Broadcast status update via WebSocket
    if ws_manager:
        ws_manager.broadcast_status_update(queue_status())


def cancel_download(book_id: str) -> bool:
    """Cancel a download."""
    result = book_queue.cancel_download(book_id)

    # Broadcast status update via WebSocket
    if result and ws_manager and ws_manager.is_enabled():
        ws_manager.broadcast_status_update(queue_status())

    return result


def retry_download(book_id: str) -> tuple[bool, str | None]:
    """Retry a failed or cancelled download.

    Request-linked downloads can only be manually retried when cancelled or
    when a staged post-processing retry is available.
    """
    task = book_queue.get_task(book_id)
    if task is None:
        return False, "Download not found"

    status = book_queue.get_task_status(book_id)
    if status not in (QueueStatus.ERROR, QueueStatus.CANCELLED):
        return False, "Download is not in an error or cancelled state"

    if not can_retry_download_task(task, status):
        return False, "Request-linked downloads must be retried from requests"

    task.last_error_message = None
    task.last_error_type = None
    task.priority = -10

    if not book_queue.enqueue_existing(book_id, priority=-10):
        return False, "Failed to requeue download"

    book_queue.update_status_message(book_id, "Retrying now")

    if ws_manager:
        ws_manager.broadcast_status_update(queue_status())

    return True, None


def set_book_priority(book_id: str, priority: int) -> bool:
    """Set priority for a queued book (lower = higher priority)."""
    return book_queue.set_priority(book_id, priority)


def reorder_queue(book_priorities: dict[str, int]) -> bool:
    """Bulk reorder queue by mapping book_id to new priority."""
    return book_queue.reorder_queue(book_priorities)


def get_queue_order() -> list[dict[str, Any]]:
    """Get current queue order for display."""
    return book_queue.get_queue_order()


def get_active_downloads() -> list[str]:
    """Get list of currently active downloads."""
    return book_queue.get_active_downloads()


def _cleanup_progress_tracking(task_id: str) -> None:
    """Clean up progress tracking data for a completed/cancelled download."""
    with _progress_lock:
        _progress_last_broadcast.pop(task_id, None)
        _progress_last_broadcast.pop(f"{task_id}_progress", None)
        _last_activity.pop(task_id, None)
        _last_progress_value.pop(task_id, None)
        _last_status_event.pop(task_id, None)
        _activity_grace.pop(task_id, None)


def _finalize_download_failure(task_id: str) -> None:
    task = book_queue.get_task(task_id)
    if not task:
        return

    message = task.last_error_message or task.status_message or ""
    normalized_message = message.strip()
    if not normalized_message:
        normalized_message = (
            f"Download failed: {task.last_error_type}"
            if task.last_error_type
            else "Download failed"
        )

    book_queue.update_status_message(task_id, normalized_message)
    book_queue.update_status(task_id, QueueStatus.ERROR)


def _process_single_download(task_id: str, cancel_flag: Event) -> None:
    """Process a single download job."""
    # Status will be updated through callbacks during download process
    # (resolving -> downloading -> complete)
    download_path = _download_task(task_id, cancel_flag)

    # Clean up progress tracking
    _cleanup_progress_tracking(task_id)

    if cancel_flag.is_set():
        book_queue.update_status(task_id, QueueStatus.CANCELLED)
        # Broadcast cancellation
        if ws_manager:
            ws_manager.broadcast_status_update(queue_status())
        return

    if download_path:
        book_queue.update_download_path(task_id, download_path)
        book_queue.update_status(task_id, QueueStatus.COMPLETE)
    else:
        _finalize_download_failure(task_id)

    # Broadcast final status (completed or error)
    if ws_manager:
        ws_manager.broadcast_status_update(queue_status())


def set_activity_grace(book_id: str, seconds: float) -> None:
    """Suppress stall detection for `book_id` for up to `seconds` from now.

    For long single-shot operations that cannot report incremental progress (protection
    bypass being the motivating case). The grace is a single absolute deadline computed
    once, so it cannot be extended into immortality by a keep-alive that carries no real
    liveness information - an operation that hangs forever is still cancelled once its
    declared budget expires.

    Deliberately touches neither the queue nor the WebSocket: this is a liveness
    assertion, not a user-visible status transition.
    """
    grace = _config_float(seconds, 0.0)
    grace = min(max(grace, 0.0), _MAX_ACTIVITY_GRACE_SECONDS)
    with _progress_lock:
        _activity_grace[book_id] = time.time() + grace


def clear_activity_grace(book_id: str) -> None:
    """Drop any activity grace for `book_id` and count this moment as activity.

    Resetting `_last_activity` means a nested or abandoned grace degrades to a fresh
    full STALL_TIMEOUT window rather than an immediate stall.
    """
    with _progress_lock:
        _activity_grace.pop(book_id, None)
        _last_activity[book_id] = time.time()


def _find_stalled_tasks(task_ids: Iterable[str], now: float) -> list[str]:
    """Return the task ids with no activity inside STALL_TIMEOUT and no active grace.

    Holds `_progress_lock` for dict reads only - never call into `book_queue` from here,
    see _cancel_stalled_task().
    """
    stalled: list[str] = []
    with _progress_lock:
        for task_id in task_ids:
            last_active = _last_activity.get(task_id, now)
            deadline = max(last_active + STALL_TIMEOUT, _activity_grace.get(task_id, 0.0))
            if now > deadline:
                stalled.append(task_id)
    return stalled


def _cancel_stalled_task(task_id: str) -> None:
    """Cancel a stalled download.

    Must be called WITHOUT `_progress_lock` held. `book_queue.cancel_download` runs the
    terminal-status hooks, which reach a sqlite write that gevent does not patch; holding
    the progress lock across that blocks the hub and every other download worker.
    """
    logger.warning("Download stalled for %s, cancelling", task_id)
    book_queue.cancel_download(task_id)
    book_queue.update_status_message(
        task_id,
        f"Download stalled (no activity for {STALL_TIMEOUT}s)",
    )


def concurrent_download_loop() -> None:
    """Run the main concurrent download coordinator."""
    max_workers = normalize_positive_int(config.MAX_CONCURRENT_DOWNLOADS) or 1
    main_loop_sleep_time = _config_float(config.MAIN_LOOP_SLEEP_TIME, 0.5)
    logger.info("Starting concurrent download loop with %s workers", max_workers)

    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="Download") as executor:
        active_futures: dict[Future, tuple[str, Event]] = {}  # Track active download futures
        stalled_tasks: set[str] = set()  # Track tasks already cancelled due to stall
        consecutive_errors = 0

        while True:
            try:
                # Clean up completed futures
                completed_futures = [f for f in active_futures if f.done()]
                for future in completed_futures:
                    task_id, cancel_flag = active_futures.pop(future)
                    stalled_tasks.discard(task_id)
                    if future.cancelled():
                        _cleanup_progress_tracking(task_id)
                        if cancel_flag.is_set():
                            logger.info("Download cancelled: %s", task_id)
                            book_queue.update_status(task_id, QueueStatus.CANCELLED)
                        else:
                            logger.warning("Future cancelled unexpectedly for %s", task_id)
                            task = book_queue.get_task(task_id)
                            if task:
                                _capture_task_error(
                                    task,
                                    message="Download failed: CancelledError",
                                    exc_type="CancelledError",
                                )
                            _finalize_download_failure(task_id)
                        if ws_manager:
                            ws_manager.broadcast_status_update(queue_status())
                        continue

                    worker_error = future.exception()
                    if worker_error is None:
                        continue

                    _cleanup_progress_tracking(task_id)
                    if cancel_flag.is_set():
                        logger.info("Download cancelled: %s", task_id)
                        book_queue.update_status(task_id, QueueStatus.CANCELLED)
                    else:
                        logger.error_trace("Future exception for %s: %s", task_id, worker_error)
                        task = book_queue.get_task(task_id)
                        if task:
                            _capture_task_error(
                                task,
                                message=_format_download_exception_message(worker_error),
                                exc_type=type(worker_error).__name__,
                            )
                        _finalize_download_failure(task_id)
                    if ws_manager:
                        ws_manager.broadcast_status_update(queue_status())

                # Check for stalled downloads (no activity in STALL_TIMEOUT seconds)
                current_time = time.time()
                candidates = [
                    task_id
                    for _future, (task_id, _cancel_flag) in list(active_futures.items())
                    if task_id not in stalled_tasks
                ]
                for task_id in _find_stalled_tasks(candidates, current_time):
                    _cancel_stalled_task(task_id)
                    stalled_tasks.add(task_id)

                # Start new downloads if we have capacity
                while len(active_futures) < max_workers:
                    next_download = book_queue.get_next()
                    if not next_download:
                        break

                    # Stagger concurrent downloads to avoid rate limiting on shared download servers
                    # Only delay if other downloads are already active
                    if active_futures:
                        stagger_delay = _RNG.uniform(2, 5)
                        logger.debug("Staggering download start by %.1fs", stagger_delay)
                        time.sleep(stagger_delay)

                    task_id, cancel_flag = next_download

                    # Submit download job to thread pool
                    future = executor.submit(_process_single_download, task_id, cancel_flag)
                    active_futures[future] = (task_id, cancel_flag)

                # Brief sleep to prevent busy waiting
                time.sleep(main_loop_sleep_time)
                consecutive_errors = 0
            # This loop is the only thing driving the download queue; if it exits, nothing
            # is ever picked up again and the app looks healthy while doing nothing (#823,
            # #1166). A narrow exception list let gevent's LoopExit and friends through, so
            # catch everything short of BaseException - GreenletExit and gevent.Timeout must
            # still propagate, and the tests' loop-stopping sentinels derive from
            # BaseException for exactly this reason.
            except Exception as e:  # noqa: BLE001 - coordinator loop must never die
                consecutive_errors += 1
                logger.error_trace("Download coordinator loop error: %s", e)
                # Back off when the failure is persistent so we don't spin at 1Hz forever,
                # but keep the first delay unchanged for a normal transient blip.
                time.sleep(
                    min(
                        COORDINATOR_LOOP_ERROR_RETRY_DELAY * 2 ** min(consecutive_errors - 1, 5),
                        _COORDINATOR_LOOP_ERROR_MAX_DELAY,
                    )
                )


# Download coordinator thread (started explicitly via start())
_coordinator_thread: threading.Thread | None = None
_coordinator_lock = Lock()


def start() -> None:
    """Start the download coordinator thread. Safe to call multiple times."""
    global _coordinator_thread

    with _coordinator_lock:
        if _coordinator_thread is not None and _coordinator_thread.is_alive():
            logger.debug("Download coordinator already started")
            return

        if _coordinator_thread is not None:
            logger.warning("Download coordinator thread is not alive; starting a new one")

        _coordinator_thread = threading.Thread(
            target=concurrent_download_loop, daemon=True, name="DownloadCoordinator"
        )
        _coordinator_thread.start()

    logger.info(
        "Download coordinator started with %s concurrent workers",
        config.MAX_CONCURRENT_DOWNLOADS,
    )
