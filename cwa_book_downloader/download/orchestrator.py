"""Download queue orchestration and worker management.

Two-stage architecture: handlers stage to TMP_DIR, orchestrator moves to INGEST_DIR
with archive extraction and custom script support.
"""

import hashlib
import os
import random
import shutil
import subprocess
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from threading import Event, Lock
from typing import Any, Dict, List, Optional, Tuple

from cwa_book_downloader.release_sources import direct_download
from cwa_book_downloader.release_sources.direct_download import SearchUnavailable
from cwa_book_downloader.core.config import config
from cwa_book_downloader.config.env import TMP_DIR
from cwa_book_downloader.core.utils import get_ingest_dir, get_destination, get_aa_content_type_dir, is_audiobook as check_audiobook, transform_cover_url
from cwa_book_downloader.core.naming import build_library_path, same_filesystem, assign_part_numbers, parse_naming_template, sanitize_filename
from cwa_book_downloader.download.archive import (
    is_archive,
    process_archive,
    _get_file_organization,
    _get_template,
    _get_supported_formats as _get_book_formats,
    _get_supported_audiobook_formats,
)
from cwa_book_downloader.release_sources import get_handler, get_source_display_name
from cwa_book_downloader.core.logger import setup_logger
from cwa_book_downloader.core.models import BookInfo, DownloadTask, QueueStatus, SearchFilters, SearchMode
from cwa_book_downloader.core.queue import book_queue

logger = setup_logger(__name__)


# =============================================================================
# Staging Directory Helpers
# =============================================================================
# Handlers should use these to get paths in the staging area.
# The orchestrator handles moving staged files to the ingest folder.

def get_staging_dir() -> Path:
    """Get the staging directory for downloads."""
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    return TMP_DIR


def get_staging_path(task_id: str, extension: str) -> Path:
    """Get a staging path for a download."""
    staging_dir = get_staging_dir()
    # Hash task_id in case it contains invalid filename chars (e.g., Prowlarr URLs)
    safe_id = hashlib.md5(task_id.encode()).hexdigest()[:16]
    return staging_dir / f"{safe_id}.{extension.lstrip('.')}"


def stage_file(source_path: Path, task_id: str, copy: bool = False) -> Path:
    """Stage a file for ingest processing. Use copy=True for torrents to preserve seeding."""
    staging_dir = get_staging_dir()
    # Stage with original filename, add counter suffix if collision
    staged_path = staging_dir / source_path.name
    if staged_path.exists():
        counter = 1
        while staged_path.exists():
            staged_path = staging_dir / f"{source_path.stem}_{counter}{source_path.suffix}"
            counter += 1

    if copy:
        shutil.copy2(str(source_path), str(staged_path))
        logger.debug(f"Copied to staging: {source_path} -> {staged_path}")
    else:
        shutil.move(str(source_path), str(staged_path))
        logger.debug(f"Moved to staging: {source_path} -> {staged_path}")

    return staged_path


def _should_hardlink(task: DownloadTask) -> bool:
    """Check if download should be hardlinked (Prowlarr torrents only)."""
    # Only Prowlarr downloads (torrents) can be hardlinked
    if task.source != "prowlarr":
        return False

    # Only applies if we have an original download path from torrent client
    if not task.original_download_path:
        return False

    # Check per-content-type setting
    is_audiobook = check_audiobook(task.content_type)
    key = "HARDLINK_TORRENTS_AUDIOBOOK" if is_audiobook else "HARDLINK_TORRENTS"

    # Check new setting first, then legacy TORRENT_HARDLINK
    hardlink_enabled = config.get(key)
    if hardlink_enabled is None:
        # Fall back to legacy setting (but only if explicitly set)
        hardlink_enabled = config.get("TORRENT_HARDLINK", False)

    return bool(hardlink_enabled)


def _should_extract_archives(task: DownloadTask) -> bool:
    """Check if archives should be extracted (disabled when hardlinking)."""
    return not _should_hardlink(task)


def _get_final_destination(task: DownloadTask) -> Path:
    """Get final destination directory, with content-type routing support."""
    is_audiobook = check_audiobook(task.content_type)

    # For Anna's Archive (direct_download), check for content-type routing override
    if task.source == "direct_download" and not is_audiobook:
        override = get_aa_content_type_dir(task.content_type)
        if override:
            return override

    return get_destination(is_audiobook)


def _build_metadata_dict(task: DownloadTask) -> dict:
    """Build metadata dictionary from task for template processing."""
    return {
        "Author": task.author,
        "Title": task.title,
        "Subtitle": task.subtitle,
        "Year": task.year,
        "Series": task.series_name,
        "SeriesPosition": task.series_position,
    }


def _get_supported_formats(content_type: str = None) -> List[str]:
    """Get current supported formats from config singleton based on content type."""
    if check_audiobook(content_type):
        return _get_supported_audiobook_formats()
    return _get_book_formats()


def _find_book_files_in_directory(directory: Path, content_type: str = None) -> Tuple[List[Path], List[Path]]:
    """Find book files matching supported formats. Returns (matches, rejected)."""
    book_files = []
    rejected_files = []
    supported_formats = _get_supported_formats(content_type)
    supported_exts = {f".{fmt}" for fmt in supported_formats}

    is_audiobook = check_audiobook(content_type)
    if is_audiobook:
        trackable_exts = {'.m4b', '.mp3', '.m4a', '.flac', '.ogg', '.wma', '.aac', '.wav'}
    else:
        trackable_exts = {'.pdf', '.epub', '.mobi', '.azw', '.azw3', '.fb2', '.djvu', '.cbz', '.cbr', '.doc', '.docx', '.rtf', '.txt'}

    for file_path in directory.rglob("*"):
        if file_path.is_file():
            if file_path.suffix.lower() in supported_exts:
                book_files.append(file_path)
            elif file_path.suffix.lower() in trackable_exts:
                rejected_files.append(file_path)

    return book_files, rejected_files


def process_directory(
    directory: Path,
    ingest_dir: Path,
    task: DownloadTask,
) -> Tuple[List[Path], Optional[str]]:
    """Process staged directory: find book files, extract archives, move to ingest."""
    try:
        content_type = task.content_type
        book_files, rejected_files = _find_book_files_in_directory(directory, content_type)

        # Find archives in directory (ZIP/RAR)
        archive_files = [f for f in directory.rglob("*") if f.is_file() and is_archive(f)]

        if not book_files:
            # No direct book files - check for archives to extract
            if archive_files:
                logger.info(f"No book files found, extracting {len(archive_files)} archive(s)")
                all_final_paths = []
                all_errors = []

                for archive in archive_files:
                    result = process_archive(
                        archive_path=archive,
                        temp_dir=directory,
                        ingest_dir=ingest_dir,
                        archive_id=f"{task.task_id}_{archive.stem}",
                        task=task,
                    )
                    if result.success:
                        all_final_paths.extend(result.final_paths)
                    elif result.error:
                        all_errors.append(f"{archive.name}: {result.error}")

                # Clean up directory after processing archives
                shutil.rmtree(directory, ignore_errors=True)

                if all_final_paths:
                    return all_final_paths, None
                elif all_errors:
                    return [], "; ".join(all_errors)
                else:
                    return [], "No book files found in archives"

            # No book files and no archives
            shutil.rmtree(directory, ignore_errors=True)

            if rejected_files:
                # Files were found but didn't match supported formats
                rejected_exts = sorted(set(f.suffix.lower() for f in rejected_files))
                rejected_list = ", ".join(rejected_exts)
                supported_formats = _get_supported_formats(content_type)
                logger.warning(
                    f"Found {len(rejected_files)} file(s) but none match supported formats. "
                    f"Rejected formats: {rejected_list}. Supported: {', '.join(sorted(supported_formats))}"
                )
                return [], f"Found {len(rejected_files)} file(s) but format not supported ({rejected_list}). Enable in Settings > Formats."

            return [], "No book files found in download"

        # We have book files - use them directly, skip any archives
        if archive_files:
            logger.debug(f"Ignoring {len(archive_files)} archive(s) - already have {len(book_files)} book file(s)")

        logger.info(f"Found {len(book_files)} book file(s) in directory")

        if rejected_files:
            rejected_exts = sorted(set(f.suffix.lower() for f in rejected_files))
            logger.debug(f"Also found {len(rejected_files)} file(s) with unsupported formats: {', '.join(rejected_exts)}")

        # Move each book file to destination
        final_paths = []
        is_audiobook = check_audiobook(task.content_type)
        organization_mode = _get_file_organization(is_audiobook)

        for book_file in book_files:
            # For multi-file downloads (book packs, series), always preserve original filenames
            # since metadata title only applies to the searched book, not the whole pack.
            # For single files, respect FILE_ORGANIZATION setting.
            if len(book_files) == 1 and organization_mode != "none":
                # Update task format from actual file if not already set
                # (Prowlarr releases may not know the format until download completes)
                if not task.format:
                    task.format = book_file.suffix.lower().lstrip('.')

                # Apply template to generate filename
                template = _get_template(is_audiobook, "rename")
                metadata = _build_metadata_dict(task)
                extension = book_file.suffix.lstrip('.') or task.format or ""

                filename = parse_naming_template(template, metadata)
                if filename and extension:
                    filename = f"{sanitize_filename(filename)}.{extension}"
                else:
                    filename = book_file.name
            else:
                filename = book_file.name

            dest_path = ingest_dir / filename
            final_path = _atomic_move(book_file, dest_path)
            final_paths.append(final_path)
            logger.debug(f"Moved to destination: {final_path.name}")

        shutil.rmtree(directory, ignore_errors=True)

        return final_paths, None

    except Exception as e:
        logger.error(f"Error processing directory: {e}")
        shutil.rmtree(directory, ignore_errors=True)
        return [], str(e)


# WebSocket manager (initialized by app.py)
# Track whether WebSocket is available for status reporting
WEBSOCKET_AVAILABLE = True
try:
    from cwa_book_downloader.api.websocket import ws_manager
except ImportError:
    logger.error("WebSocket unavailable - real-time updates disabled")
    ws_manager = None
    WEBSOCKET_AVAILABLE = False

# Progress update throttling - track last broadcast time per book
_progress_last_broadcast: Dict[str, float] = {}
_progress_lock = Lock()

# Stall detection - track last activity time per download
_last_activity: Dict[str, float] = {}
STALL_TIMEOUT = 300  # 5 minutes without progress/status update = stalled

def search_books(query: str, filters: SearchFilters) -> List[Dict[str, Any]]:
    """Search for books matching the query."""
    try:
        books = direct_download.search_books(query, filters)
        return [_book_info_to_dict(book) for book in books]
    except SearchUnavailable:
        raise
    except Exception as e:
        logger.error_trace(f"Error searching books: {e}")
        raise

def get_book_info(book_id: str) -> Optional[Dict[str, Any]]:
    """Get detailed information for a specific book."""
    try:
        book = direct_download.get_book_info(book_id)
        return _book_info_to_dict(book)
    except Exception as e:
        logger.error_trace(f"Error getting book info: {e}")
        raise

def queue_book(book_id: str, priority: int = 0, source: str = "direct_download") -> Tuple[bool, Optional[str]]:
    """Add a book to the download queue. Returns (success, error_message)."""
    try:
        book_info = direct_download.get_book_info(book_id, fetch_download_count=False)
        if not book_info:
            error_msg = f"Could not fetch book info for {book_id}"
            logger.warning(error_msg)
            return False, error_msg

        # Create a source-agnostic download task
        task = DownloadTask(
            task_id=book_id,
            source=source,
            title=book_info.title,
            author=book_info.author,
            format=book_info.format,
            size=book_info.size,
            preview=book_info.preview,
            content_type=book_info.content,
            search_mode=SearchMode.DIRECT,
            priority=priority,
        )

        if not book_queue.add(task):
            logger.info(f"Book already in queue: {book_info.title}")
            return False, "Book is already in the download queue"

        logger.info(f"Book queued with priority {priority}: {book_info.title}")

        # Broadcast status update via WebSocket
        if ws_manager:
            ws_manager.broadcast_status_update(queue_status())

        return True, None
    except SearchUnavailable as e:
        error_msg = f"Search service unavailable: {e}"
        logger.warning(error_msg)
        return False, error_msg
    except Exception as e:
        error_msg = f"Error queueing book: {e}"
        logger.error_trace(error_msg)
        return False, error_msg


def queue_release(release_data: dict, priority: int = 0) -> Tuple[bool, Optional[str]]:
    """Add a release to the download queue. Returns (success, error_message)."""
    try:
        source = release_data.get('source', 'direct_download')
        extra = release_data.get('extra', {})

        # Get author, year, preview, and content_type from top-level (preferred) or extra (fallback)
        author = release_data.get('author') or extra.get('author')
        year = release_data.get('year') or extra.get('year')
        preview = release_data.get('preview') or extra.get('preview')
        content_type = release_data.get('content_type') or extra.get('content_type')

        # Get series info for library naming templates
        series_name = release_data.get('series_name') or extra.get('series_name')
        series_position = release_data.get('series_position') or extra.get('series_position')
        subtitle = release_data.get('subtitle') or extra.get('subtitle')

        # Create a source-agnostic download task from release data
        task = DownloadTask(
            task_id=release_data['source_id'],
            source=source,
            title=release_data.get('title', 'Unknown'),
            author=author,
            year=year,
            format=release_data.get('format'),
            size=release_data.get('size'),
            preview=preview,
            content_type=content_type,
            series_name=series_name,
            series_position=series_position,
            subtitle=subtitle,
            search_mode=SearchMode.UNIVERSAL,
            priority=priority,
        )

        if not book_queue.add(task):
            logger.info(f"Release already in queue: {task.title}")
            return False, "Release is already in the download queue"

        logger.info(f"Release queued with priority {priority}: {task.title}")

        # Broadcast status update via WebSocket
        if ws_manager:
            ws_manager.broadcast_status_update(queue_status())

        return True, None

    except ValueError as e:
        # Handler not found for this source
        error_msg = f"Unknown release source: {e}"
        logger.warning(error_msg)
        return False, error_msg
    except KeyError as e:
        error_msg = f"Missing required field in release data: {e}"
        logger.warning(error_msg)
        return False, error_msg
    except Exception as e:
        error_msg = f"Error queueing release: {e}"
        logger.error_trace(error_msg)
        return False, error_msg

def queue_status() -> Dict[str, Dict[str, Any]]:
    """Get current status of the download queue."""
    status = book_queue.get_status()
    for _, tasks in status.items():
        for _, task in tasks.items():
            if task.download_path and not os.path.exists(task.download_path):
                task.download_path = None

    # Convert Enum keys to strings and DownloadTask objects to dicts for JSON serialization
    return {
        status_type.value: {
            task_id: _task_to_dict(task)
            for task_id, task in tasks.items()
        }
        for status_type, tasks in status.items()
    }

def get_book_data(task_id: str) -> Tuple[Optional[bytes], Optional[DownloadTask]]:
    """Get downloaded file data for a specific task."""
    task = None
    try:
        task = book_queue.get_task(task_id)
        if not task:
            return None, None

        path = task.download_path
        if not path:
            return None, task

        with open(path, "rb") as f:
            return f.read(), task
    except Exception as e:
        logger.error_trace(f"Error getting book data: {e}")
        if task:
            task.download_path = None
        return None, task

def _book_info_to_dict(book: BookInfo) -> Dict[str, Any]:
    """Convert BookInfo to dict, transforming cover URLs for caching."""
    result = {
        key: value for key, value in book.__dict__.items()
        if value is not None
    }

    # Transform external preview URLs to local proxy URLs
    if result.get('preview'):
        result['preview'] = transform_cover_url(result['preview'], book.id)

    return result


def _task_to_dict(task: DownloadTask) -> Dict[str, Any]:
    """Convert DownloadTask to dict for frontend, transforming cover URLs."""
    # Transform external preview URLs to local proxy URLs
    preview = transform_cover_url(task.preview, task.task_id)

    return {
        'id': task.task_id,
        'title': task.title,
        'author': task.author,
        'format': task.format,
        'size': task.size,
        'preview': preview,
        'content_type': task.content_type,
        'source': task.source,
        'source_display_name': get_source_display_name(task.source),
        'priority': task.priority,
        'added_time': task.added_time,
        'progress': task.progress,
        'status': task.status,
        'status_message': task.status_message,
        'download_path': task.download_path,
    }


def _download_task(task_id: str, cancel_flag: Event) -> Optional[str]:
    """Download a task via appropriate handler, then post-process to ingest."""
    try:
        # Check for cancellation before starting
        if cancel_flag.is_set():
            logger.info(f"Download cancelled before starting: {task_id}")
            return None

        task = book_queue.get_task(task_id)
        if not task:
            logger.error(f"Task not found in queue: {task_id}")
            return None

        def progress_callback(progress: float) -> None:
            update_download_progress(task_id, progress)

        def status_callback(status: str, message: Optional[str] = None) -> None:
            update_download_status(task_id, status, message)

        # Get the download handler based on the task's source
        handler = get_handler(task.source)
        temp_path = handler.download(
            task,
            cancel_flag,
            progress_callback,
            status_callback
        )

        # Handler returns temp path - orchestrator handles post-processing
        if not temp_path:
            return None

        temp_file = Path(temp_path)
        if not temp_file.exists():
            logger.error(f"Handler returned non-existent path: {temp_path}")
            return None

        # Check cancellation before post-processing
        if cancel_flag.is_set():
            logger.info(f"Download cancelled before post-processing: {task_id}")
            if temp_file.is_dir():
                shutil.rmtree(temp_file, ignore_errors=True)
            else:
                temp_file.unlink(missing_ok=True)
            return None

        # Post-processing: archive extraction or direct move to ingest
        return _post_process_download(
            temp_file, task, cancel_flag, status_callback
        )

    except Exception as e:
        if cancel_flag.is_set():
            logger.info(f"Download cancelled during error handling: {task_id}")
        else:
            logger.error_trace(f"Error downloading: {e}")
            # Update task status so user sees the failure
            task = book_queue.get_task(task_id)
            if task:
                book_queue.update_status(task_id, QueueStatus.ERROR)
                book_queue.update_status_message(task_id, f"Download failed: {type(e).__name__}")
        return None


def _process_organize_mode(
    temp_file: Path,
    task: DownloadTask,
    status_callback,
) -> Optional[str]:
    """Organize files into library folders using template. Supports hardlinking."""
    is_audiobook = check_audiobook(task.content_type)

    # Get destination and template
    destination = _get_final_destination(task)
    template = _get_template(is_audiobook, "organize")

    # Validate destination path
    if not destination.is_absolute():
        logger.warning(f"Destination must be absolute: {destination}, falling back to flat mode")
        status_callback("resolving", f"Destination must be absolute: {destination}")
        return None

    if not destination.exists():
        try:
            destination.mkdir(parents=True, exist_ok=True)
        except (OSError, PermissionError) as e:
            logger.warning(f"Cannot create destination: {e}")
            status_callback("resolving", f"Cannot create destination: {e}")
            return None

    if not os.access(destination, os.W_OK):
        logger.warning(f"Destination not writable: {destination}")
        status_callback("resolving", f"Destination not writable: {destination}")
        return None

    # Determine if we should use hardlinking
    use_hardlink = False
    source = temp_file

    if _should_hardlink(task):
        hardlink_source = Path(task.original_download_path)
        if hardlink_source.exists() and same_filesystem(hardlink_source, destination):
            use_hardlink = True
            source = hardlink_source
        elif hardlink_source.exists():
            logger.warning(
                f"Cannot hardlink: {hardlink_source} and {destination} are on different filesystems. "
                "Falling back to copy. To fix: ensure torrent client downloads to same filesystem as destination."
            )
            status_callback("resolving", "Cannot hardlink (different filesystems), using copy")

    # Build metadata dict for template
    metadata = _build_metadata_dict(task)

    try:
        status_callback("resolving", "Creating hardlinks" if use_hardlink else "Organizing files")

        if source.is_dir():
            return _transfer_directory_to_library(
                source, str(destination), template, metadata, task, temp_file, status_callback, use_hardlink
            )
        else:
            return _transfer_file_to_library(
                source, str(destination), template, metadata, task, temp_file, status_callback, use_hardlink
            )
    except PermissionError as e:
        logger.error(f"Permission denied: {e}")
        status_callback("error", f"Permission denied: {e}")
        return None
    except Exception as e:
        logger.error_trace(f"Organization failed: {e}")
        status_callback("error", f"Organization failed: {e}")
        return None


def _is_torrent_source(source_path: Path, task: DownloadTask) -> bool:
    """Check if source is the torrent client path (needs copy to preserve seeding)."""
    if not task.original_download_path:
        return False
    try:
        return source_path.resolve() == Path(task.original_download_path).resolve()
    except (OSError, ValueError):
        return False


def _stage_torrent_path(source: Path) -> Path:
    """Copy torrent source to staging directory to preserve seeding."""
    staging_dir = get_staging_dir()
    staged_path = staging_dir / source.name
    counter = 1

    if source.is_dir():
        while staged_path.exists():
            staged_path = staging_dir / f"{source.name}_{counter}"
            counter += 1
        shutil.copytree(str(source), str(staged_path))
    else:
        while staged_path.exists():
            staged_path = staging_dir / f"{source.stem}_{counter}{source.suffix}"
            counter += 1
        shutil.copy2(str(source), str(staged_path))

    logger.debug(f"Staged torrent {'directory' if source.is_dir() else 'file'}: {staged_path.name}")
    return staged_path


# Import atomic file operations from shared module
# Re-exported here for backwards compatibility with existing tests/imports
from cwa_book_downloader.download.fs import (
    atomic_hardlink as _atomic_hardlink,
    atomic_copy as _atomic_copy,
    atomic_move as _atomic_move,
)


def _cleanup_staged_files(temp_file: Path, source_dir: Optional[Path] = None) -> None:
    """Remove staged files. Optionally removes source_dir if empty."""
    try:
        if temp_file.is_dir():
            shutil.rmtree(temp_file)
        elif temp_file.exists():
            temp_file.unlink()
    except (OSError, PermissionError) as e:
        logger.debug(f"Cleanup failed for {temp_file}: {e}")

    if source_dir and source_dir.is_dir():
        try:
            source_dir.rmdir()
        except OSError:
            pass  # Directory not empty or permission issue


def _transfer_single_file(
    source_path: Path,
    dest_path: Path,
    use_hardlink: bool,
    is_torrent: bool,
) -> Tuple[Path, str]:
    """Transfer a file via hardlink, copy, or move. Returns (final_path, operation_name)."""
    if use_hardlink:
        return _atomic_hardlink(source_path, dest_path), "hardlink"
    if is_torrent:
        return _atomic_copy(source_path, dest_path), "copy"
    return _atomic_move(source_path, dest_path), "move"


def _transfer_file_to_library(
    source_path: Path,
    library_base: str,
    template: str,
    metadata: dict,
    task: DownloadTask,
    temp_file: Optional[Path],
    status_callback,
    use_hardlink: bool,
) -> Optional[str]:
    """Transfer a single file to the library with template-based naming."""
    extension = source_path.suffix.lstrip('.') or task.format
    dest_path = build_library_path(library_base, template, metadata, extension)

    dest_path.parent.mkdir(parents=True, exist_ok=True)

    is_torrent = _is_torrent_source(source_path, task)
    final_path, op = _transfer_single_file(source_path, dest_path, use_hardlink, is_torrent)
    logger.info(f"Library {op}: {final_path}")

    if use_hardlink:
        _cleanup_staged_files(temp_file)

    status_callback("complete", "Complete")
    return str(final_path)


def _transfer_directory_to_library(
    source_dir: Path,
    library_base: str,
    template: str,
    metadata: dict,
    task: DownloadTask,
    temp_file: Optional[Path],
    status_callback,
    use_hardlink: bool,
) -> Optional[str]:
    """Transfer all files from a directory to the library with template-based naming."""
    content_type = task.content_type.lower() if task.content_type else None
    supported_formats = _get_supported_formats(content_type)

    source_files = [
        f for f in source_dir.rglob("*")
        if f.is_file() and f.suffix.lower().lstrip('.') in supported_formats
    ]

    if not source_files:
        logger.warning(f"No supported files in {source_dir.name}")
        status_callback("error", "No supported file formats found")
        if temp_file:
            _cleanup_staged_files(temp_file)
        return None

    base_library_path = build_library_path(library_base, template, metadata, extension=None)
    base_library_path.parent.mkdir(parents=True, exist_ok=True)

    # Check if this is a torrent source that needs copy instead of move
    is_torrent = _is_torrent_source(source_dir, task)
    transferred_paths = []

    if len(source_files) == 1:
        # Single file - no part numbering needed
        source_file = source_files[0]
        ext = source_file.suffix.lstrip('.')
        dest_path = base_library_path.with_suffix(f'.{ext}')

        final_path, op = _transfer_single_file(source_file, dest_path, use_hardlink, is_torrent)
        logger.debug(f"Library {op}: {source_file.name} -> {final_path}")
        transferred_paths.append(final_path)
    else:
        # Multi-file: natural sort then sequential numbering
        zero_pad_width = max(len(str(len(source_files))), 2)
        files_with_parts = assign_part_numbers(source_files, zero_pad_width)

        for source_file, part_number in files_with_parts:
            ext = source_file.suffix.lstrip('.')

            file_metadata = {**metadata, "PartNumber": part_number}
            file_path = build_library_path(library_base, template, file_metadata, extension=ext)
            file_path.parent.mkdir(parents=True, exist_ok=True)

            final_path, op = _transfer_single_file(source_file, file_path, use_hardlink, is_torrent)
            logger.debug(f"Library {op}: {source_file.name} -> {final_path}")
            transferred_paths.append(final_path)

    # Get operation name for summary log
    if use_hardlink:
        operation = "hardlinks"
    elif is_torrent:
        operation = "copies"
    else:
        operation = "files"
    logger.info(f"Created {len(transferred_paths)} library {operation} in {base_library_path.parent}")

    # Cleanup staging (not torrent source - that stays for seeding)
    if use_hardlink:
        _cleanup_staged_files(temp_file)
    elif not is_torrent:
        _cleanup_staged_files(temp_file, source_dir)

    message = f"Complete ({len(transferred_paths)} files)" if len(transferred_paths) > 1 else "Complete"
    status_callback("complete", message)

    return str(transferred_paths[0])


def _post_process_download(
    temp_file: Path,
    task: DownloadTask,
    cancel_flag: Event,
    status_callback,
) -> Optional[str]:
    """Post-process download: extract archives, apply naming template, move to destination."""
    is_audiobook = check_audiobook(task.content_type)

    # Validate search_mode
    if task.search_mode is None:
        logger.warning(f"Task {task.task_id} has no search_mode set, defaulting to Direct mode behavior")
    elif task.search_mode not in (SearchMode.DIRECT, SearchMode.UNIVERSAL):
        logger.warning(f"Task {task.task_id} has invalid search_mode '{task.search_mode}', defaulting to Direct mode behavior")

    # Get file organization mode and destination
    organization_mode = _get_file_organization(is_audiobook)
    destination = _get_final_destination(task)

    logger.debug(f"File organization: mode={organization_mode}, destination={destination}")

    # "Organize" mode with folders uses specialized handler
    if organization_mode == "organize":
        result = _process_organize_mode(temp_file, task, status_callback)
        if result is not None:
            return result
        # If organize mode fails, fall through to flat mode
        logger.warning(
            f"Organize mode failed for '{task.title}', falling back to flat destination. "
            "Check destination folder permissions and ensure the path is writable."
        )
        status_callback("resolving", "Organization failed, using flat destination")

    # Ensure destination exists
    os.makedirs(destination, exist_ok=True)

    # For torrents with hardlinking disabled, stage first to preserve seeding
    # (Torrent handler returns original path, not staged copy)
    if _is_torrent_source(temp_file, task) and not _should_hardlink(task):
        status_callback("resolving", "Staging torrent files")
        temp_file = _stage_torrent_path(temp_file)

    # Handle archive extraction (RAR/ZIP) - only if not hardlinking
    if is_archive(temp_file) and _should_extract_archives(task):
        logger.info(f"Archive detected, extracting: {temp_file.name}")
        status_callback("resolving", "Extracting archive")

        result = process_archive(
            archive_path=temp_file,
            temp_dir=TMP_DIR,
            ingest_dir=destination,
            archive_id=task.task_id,
            task=task,
        )

        if result.success:
            status_callback("complete", result.message)
            return str(result.final_paths[0])
        else:
            status_callback("error", result.error)
            return None

    # Handle directory (multi-file torrent/usenet downloads)
    if temp_file.is_dir():
        logger.info(f"Directory detected, processing: {temp_file.name}")
        status_callback("resolving", "Processing download folder")

        final_paths, error = process_directory(
            directory=temp_file,
            ingest_dir=destination,
            task=task,
        )

        if error:
            status_callback("error", error)
            return None

        if not final_paths:
            status_callback("error", "No book files found")
            return None

        message = "Complete" if len(final_paths) == 1 else f"Complete ({len(final_paths)} files)"
        status_callback("complete", message)
        return str(final_paths[0])

    # Non-archive: run custom script if configured, then move to destination
    if config.CUSTOM_SCRIPT:
        logger.info(f"Running custom script: {config.CUSTOM_SCRIPT}")
        try:
            result = subprocess.run(
                [config.CUSTOM_SCRIPT, str(temp_file)],
                check=True,
                timeout=300,  # 5 minute timeout
                capture_output=True,
                text=True,
            )
            if result.stdout:
                logger.debug(f"Custom script stdout: {result.stdout.strip()}")
        except FileNotFoundError:
            logger.error(f"Custom script not found: {config.CUSTOM_SCRIPT}")
            status_callback("error", f"Custom script not found: {config.CUSTOM_SCRIPT}")
            return None
        except PermissionError:
            logger.error(f"Custom script not executable: {config.CUSTOM_SCRIPT}")
            status_callback("error", f"Custom script not executable: {config.CUSTOM_SCRIPT}")
            return None
        except subprocess.TimeoutExpired:
            logger.error(f"Custom script timed out after 300s: {config.CUSTOM_SCRIPT}")
            status_callback("error", "Custom script timed out")
            return None
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.strip() if e.stderr else "No error output"
            logger.error(f"Custom script failed (exit code {e.returncode}): {stderr}")
            status_callback("error", f"Custom script failed: {stderr[:100]}")
            return None

    # Check cancellation before final move
    if cancel_flag.is_set():
        logger.info(f"Download cancelled before final move: {task.task_id}")
        temp_file.unlink(missing_ok=True)
        return None

    # Determine filename based on organization mode
    if organization_mode == "none":
        # Keep original filename
        filename = temp_file.name
    else:
        # "rename" mode - apply template to filename
        template = _get_template(is_audiobook, "rename")
        metadata = _build_metadata_dict(task)
        extension = temp_file.suffix.lstrip('.') or task.format or ""

        # Parse template to generate filename
        filename = parse_naming_template(template, metadata)
        if filename and extension:
            filename = f"{sanitize_filename(filename)}.{extension}"
        elif not filename:
            # Template produced empty result, fall back to original
            filename = temp_file.name

    dest_path = destination / filename

    try:
        final_path = _atomic_move(temp_file, dest_path)
    except Exception as e:
        logger.error(f"Failed to move file to destination: {e}")
        status_callback("error", f"Failed to move file: {e}")
        return None

    logger.info(f"Download completed: {final_path.name}")

    status_callback("complete", "Complete")

    return str(final_path)

def update_download_progress(book_id: str, progress: float) -> None:
    """Update download progress with throttled WebSocket broadcasts."""
    book_queue.update_progress(book_id, progress)

    # Track activity for stall detection
    with _progress_lock:
        _last_activity[book_id] = time.time()
    
    # Broadcast progress via WebSocket with throttling
    if ws_manager:
        current_time = time.time()
        should_broadcast = False
        
        with _progress_lock:
            last_broadcast = _progress_last_broadcast.get(book_id, 0)
            last_progress = _progress_last_broadcast.get(f"{book_id}_progress", 0)
            time_elapsed = current_time - last_broadcast
            
            # Always broadcast at start (0%) or completion (>=99%)
            if progress <= 1 or progress >= 99:
                should_broadcast = True
            # Broadcast if enough time has passed (convert interval from seconds)
            elif time_elapsed >= config.DOWNLOAD_PROGRESS_UPDATE_INTERVAL:
                should_broadcast = True
            # Broadcast on significant progress jumps (>10%)
            elif progress - last_progress >= 10:
                should_broadcast = True
            
            if should_broadcast:
                _progress_last_broadcast[book_id] = current_time
                _progress_last_broadcast[f"{book_id}_progress"] = progress
        
        if should_broadcast:
            ws_manager.broadcast_download_progress(book_id, progress, 'downloading')

def update_download_status(book_id: str, status: str, message: Optional[str] = None) -> None:
    """Update download status with optional message for UI display."""
    # Map string status to QueueStatus enum
    status_map = {
        'queued': QueueStatus.QUEUED,
        'resolving': QueueStatus.RESOLVING,
        'downloading': QueueStatus.DOWNLOADING,
        'complete': QueueStatus.COMPLETE,
        'available': QueueStatus.AVAILABLE,
        'error': QueueStatus.ERROR,
        'done': QueueStatus.DONE,
        'cancelled': QueueStatus.CANCELLED,
    }
    
    queue_status_enum = status_map.get(status.lower())
    if queue_status_enum:
        book_queue.update_status(book_id, queue_status_enum)

        # Track activity for stall detection
        with _progress_lock:
            _last_activity[book_id] = time.time()

        # Update status message if provided (empty string clears the message)
        if message is not None:
            book_queue.update_status_message(book_id, message)

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

def set_book_priority(book_id: str, priority: int) -> bool:
    """Set priority for a queued book (lower = higher priority)."""
    return book_queue.set_priority(book_id, priority)

def reorder_queue(book_priorities: Dict[str, int]) -> bool:
    """Bulk reorder queue by mapping book_id to new priority."""
    return book_queue.reorder_queue(book_priorities)

def get_queue_order() -> List[Dict[str, Any]]:
    """Get current queue order for display."""
    return book_queue.get_queue_order()

def get_active_downloads() -> List[str]:
    """Get list of currently active downloads."""
    return book_queue.get_active_downloads()

def clear_completed() -> int:
    """Clear all completed downloads from tracking."""
    return book_queue.clear_completed()

def _cleanup_progress_tracking(task_id: str) -> None:
    """Clean up progress tracking data for a completed/cancelled download."""
    with _progress_lock:
        _progress_last_broadcast.pop(task_id, None)
        _progress_last_broadcast.pop(f"{task_id}_progress", None)
        _last_activity.pop(task_id, None)


def _process_single_download(task_id: str, cancel_flag: Event) -> None:
    """Process a single download job."""
    try:
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
            # Only update status if not already set (e.g., by archive extraction callback)
            task = book_queue.get_task(task_id)
            if not task or task.status != QueueStatus.COMPLETE:
                book_queue.update_status(task_id, QueueStatus.COMPLETE)
        else:
            book_queue.update_status(task_id, QueueStatus.ERROR)

        # Broadcast final status (completed or error)
        if ws_manager:
            ws_manager.broadcast_status_update(queue_status())

    except Exception as e:
        # Clean up progress tracking even on error
        _cleanup_progress_tracking(task_id)

        if not cancel_flag.is_set():
            logger.error_trace(f"Error in download processing: {e}")
            book_queue.update_status(task_id, QueueStatus.ERROR)
            # Set error message if not already set by handler
            task = book_queue.get_task(task_id)
            if task and not task.status_message:
                book_queue.update_status_message(task_id, f"Download failed: {type(e).__name__}: {str(e)}")
        else:
            logger.info(f"Download cancelled: {task_id}")
            book_queue.update_status(task_id, QueueStatus.CANCELLED)

        # Broadcast error/cancelled status
        if ws_manager:
            ws_manager.broadcast_status_update(queue_status())

def concurrent_download_loop() -> None:
    """Main download coordinator using ThreadPoolExecutor for concurrent downloads."""
    max_workers = config.MAX_CONCURRENT_DOWNLOADS
    logger.info(f"Starting concurrent download loop with {max_workers} workers")

    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="Download") as executor:
        active_futures: Dict[Future, str] = {}  # Track active download futures

        while True:
            # Clean up completed futures
            completed_futures = [f for f in active_futures if f.done()]
            for future in completed_futures:
                task_id = active_futures.pop(future)
                try:
                    future.result()  # This will raise any exceptions from the worker
                except Exception as e:
                    logger.error_trace(f"Future exception for {task_id}: {e}")

            # Check for stalled downloads (no activity in STALL_TIMEOUT seconds)
            current_time = time.time()
            with _progress_lock:
                for future, task_id in list(active_futures.items()):
                    last_active = _last_activity.get(task_id, current_time)
                    if current_time - last_active > STALL_TIMEOUT:
                        logger.warning(f"Download stalled for {task_id}, cancelling")
                        book_queue.cancel_download(task_id)
                        book_queue.update_status_message(task_id, f"Download stalled (no activity for {STALL_TIMEOUT}s)")

            # Start new downloads if we have capacity
            while len(active_futures) < max_workers:
                next_download = book_queue.get_next()
                if not next_download:
                    break

                # Stagger concurrent downloads to avoid rate limiting on shared download servers
                # Only delay if other downloads are already active
                if active_futures:
                    stagger_delay = random.uniform(2, 5)
                    logger.debug(f"Staggering download start by {stagger_delay:.1f}s")
                    time.sleep(stagger_delay)

                task_id, cancel_flag = next_download

                # Submit download job to thread pool
                future = executor.submit(_process_single_download, task_id, cancel_flag)
                active_futures[future] = task_id

            # Brief sleep to prevent busy waiting
            time.sleep(config.MAIN_LOOP_SLEEP_TIME)

# Download coordinator thread (started explicitly via start())
_coordinator_thread: Optional[threading.Thread] = None
_started = False


def start() -> None:
    """Start the download coordinator thread. Safe to call multiple times."""
    global _coordinator_thread, _started

    if _started:
        logger.debug("Download coordinator already started")
        return

    _coordinator_thread = threading.Thread(
        target=concurrent_download_loop,
        daemon=True,
        name="DownloadCoordinator"
    )
    _coordinator_thread.start()
    _started = True

    logger.info(f"Download coordinator started with {config.MAX_CONCURRENT_DOWNLOADS} concurrent workers")
