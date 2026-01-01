"""Download queue orchestration and worker management.

## Download Architecture

All downloads follow a two-stage process:

1. **Staging (TMP_DIR)**: Handlers download/copy files to a temp staging area.
   - Direct downloads: Downloaded directly to staging
   - Torrent downloads: Copied from torrent client's completed folder to staging
   - NZB downloads: Moved from NZB client's completed folder to staging

2. **Ingest (INGEST_DIR)**: Orchestrator moves staged files to the final location.
   - Archive extraction (RAR/ZIP) happens here
   - Custom scripts run here
   - Final move to ingest folder

This ensures:
- Handlers don't need to know about ingest folder logic
- Archive handling works uniformly for all sources
- Single point of control for what enters the ingest folder
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
from cwa_book_downloader.core.utils import get_ingest_dir
from cwa_book_downloader.core.naming import build_library_path, same_filesystem, assign_part_numbers
from cwa_book_downloader.download.archive import is_archive, process_archive
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
    """Get the staging directory for downloads.

    All handlers should stage their downloads here. The orchestrator
    handles moving staged files to the final ingest location.
    """
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    return TMP_DIR


def get_staging_path(task_id: str, extension: str) -> Path:
    """Get a staging path for a download.

    Args:
        task_id: Unique task identifier
        extension: File extension (e.g., 'epub', 'zip')

    Returns:
        Path in staging directory for this download
    """
    staging_dir = get_staging_dir()
    # Hash task_id in case it contains invalid filename chars (e.g., Prowlarr URLs)
    safe_id = hashlib.md5(task_id.encode()).hexdigest()[:16]
    return staging_dir / f"{safe_id}.{extension.lstrip('.')}"


def stage_file(source_path: Path, task_id: str, copy: bool = False) -> Path:
    """Stage a file for ingest processing.

    Use this when a download client has completed a download and the file
    needs to be staged for orchestrator processing.

    Args:
        source_path: Path to the completed download
        task_id: Unique task identifier
        copy: If True, copy the file (for torrents). If False, move it.

    Returns:
        Path to the staged file
    """
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


def _get_supported_formats(content_type: str = None) -> List[str]:
    """Get current supported formats from config singleton based on content type."""
    if content_type and content_type.lower() == "audiobook":
        formats = config.get("SUPPORTED_AUDIOBOOK_FORMATS", ["m4b", "mp3"])
    else:
        formats = config.get("SUPPORTED_FORMATS", ["epub", "mobi", "azw3", "fb2", "djvu", "cbz", "cbr"])
    # Handle both list (from MultiSelectField) and comma-separated string (legacy/env)
    if isinstance(formats, str):
        return [fmt.strip().lower() for fmt in formats.split(",") if fmt.strip()]
    return [fmt.lower() for fmt in formats]


def _find_book_files_in_directory(directory: Path, content_type: str = None) -> Tuple[List[Path], List[Path]]:
    """Find all book files in a directory matching supported formats.

    Args:
        directory: Directory to search recursively
        content_type: Content type to determine format list (e.g., "audiobook")

    Returns:
        Tuple of (matching book files, rejected files with unsupported extensions)
    """
    book_files = []
    rejected_files = []
    supported_formats = _get_supported_formats(content_type)
    supported_exts = {f".{fmt}" for fmt in supported_formats}

    is_audiobook = content_type and content_type.lower() == "audiobook"
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
    """Process a staged directory: find book files, handle archives, move to ingest.

    For multi-file torrent/usenet downloads. If book files exist, moves them directly.
    If only archives exist, extracts them to find book files inside.

    Args:
        directory: Staged directory containing downloaded files
        ingest_dir: Final destination directory for book files
        task: Download task for filename generation

    Returns:
        Tuple of (list of final paths, error message if failed)
    """
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

        # Move each book file to ingest
        final_paths = []
        for book_file in book_files:
            # For multi-file downloads (book packs, series), always preserve original filenames
            # since metadata title only applies to the searched book, not the whole pack.
            # For single files, respect USE_BOOK_TITLE setting.
            if len(book_files) == 1 and config.USE_BOOK_TITLE:
                # Update task format from actual file if not already set
                # (Prowlarr releases may not know the format until download completes)
                if not task.format:
                    task.format = book_file.suffix.lower().lstrip('.')
                filename = task.get_filename() or book_file.name
            else:
                filename = book_file.name

            dest_path = ingest_dir / filename
            final_path = _atomic_move(book_file, dest_path)
            final_paths.append(final_path)
            logger.debug(f"Moved to ingest: {final_path.name}")

        shutil.rmtree(directory, ignore_errors=True)

        return final_paths, None

    except Exception as e:
        logger.error(f"Error processing directory: {e}")
        shutil.rmtree(directory, ignore_errors=True)
        return [], str(e)


# WebSocket manager (initialized by app.py)
try:
    from cwa_book_downloader.api.websocket import ws_manager
except ImportError:
    logger.warning("WebSocket unavailable - real-time updates disabled")
    ws_manager = None

# Progress update throttling - track last broadcast time per book
_progress_last_broadcast: Dict[str, float] = {}
_progress_lock = Lock()

# Stall detection - track last activity time per download
_last_activity: Dict[str, float] = {}
STALL_TIMEOUT = 300  # 5 minutes without progress/status update = stalled

def search_books(query: str, filters: SearchFilters) -> List[Dict[str, Any]]:
    """Search for books matching the query.
    
    Args:
        query: Search term
        filters: Search filters object
        
    Returns:
        List[Dict]: List of book information dictionaries
    """
    try:
        books = direct_download.search_books(query, filters)
        return [_book_info_to_dict(book) for book in books]
    except SearchUnavailable:
        raise
    except Exception as e:
        logger.error_trace(f"Error searching books: {e}")
        raise

def get_book_info(book_id: str) -> Optional[Dict[str, Any]]:
    """Get detailed information for a specific book.

    Args:
        book_id: Book identifier

    Returns:
        Optional[Dict]: Book information dictionary if found, None if not found

    Raises:
        Exception: If there's an error fetching the book info
    """
    try:
        book = direct_download.get_book_info(book_id)
        return _book_info_to_dict(book)
    except Exception as e:
        logger.error_trace(f"Error getting book info: {e}")
        raise

def queue_book(book_id: str, priority: int = 0, source: str = "direct_download") -> bool:
    """Add a book to the download queue with specified priority.

    Fetches display info and creates a DownloadTask. The handler will fetch
    the full book details (including download URLs) when processing.

    Args:
        book_id: Book identifier (e.g., AA MD5 hash)
        priority: Priority level (lower number = higher priority)
        source: Release source handler to use (default: direct_download)

    Returns:
        bool: True if book was successfully queued
    """
    try:
        book_info = direct_download.get_book_info(book_id, fetch_download_count=False)
        if not book_info:
            logger.warning(f"Could not fetch book info for {book_id}")
            return False

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
            return False

        logger.info(f"Book queued with priority {priority}: {book_info.title}")

        # Broadcast status update via WebSocket
        if ws_manager:
            ws_manager.broadcast_status_update(queue_status())

        return True
    except Exception as e:
        logger.error_trace(f"Error queueing book: {e}")
        return False


def queue_release(release_data: dict, priority: int = 0) -> bool:
    """Add a release to the download queue.

    This is used when downloading from the ReleaseModal where we already have
    all the release data from the search - no need to re-fetch.

    Creates a DownloadTask directly from the release data. The handler will
    fetch full details when processing.

    Args:
        release_data: Release dictionary with source, source_id, title, format, etc.
        priority: Priority level (lower number = higher priority)

    Returns:
        bool: True if release was successfully queued
    """
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
            return False

        logger.info(f"Release queued with priority {priority}: {task.title}")

        # Broadcast status update via WebSocket
        if ws_manager:
            ws_manager.broadcast_status_update(queue_status())

        return True

    except ValueError as e:
        # Handler not found for this source
        logger.warning(f"Unknown release source: {e}")
        return False
    except Exception as e:
        logger.error_trace(f"Error queueing release: {e}")
        return False

def queue_status() -> Dict[str, Dict[str, Any]]:
    """Get current status of the download queue.

    Returns:
        Dict: Queue status organized by status type with serialized task data
    """
    status = book_queue.get_status()
    for _, tasks in status.items():
        for _, task in tasks.items():
            if task.download_path:
                if not os.path.exists(task.download_path):
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
    """Get downloaded file data for a specific task.

    Args:
        task_id: Task identifier

    Returns:
        Tuple[Optional[bytes], Optional[DownloadTask]]: File data if available, and the task
    """
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
    """Convert BookInfo object to dictionary representation.

    Transforms external preview URLs to local proxy URLs when cover caching is enabled.
    """
    from cwa_book_downloader.core.utils import transform_cover_url

    result = {
        key: value for key, value in book.__dict__.items()
        if value is not None
    }

    # Transform external preview URLs to local proxy URLs
    if result.get('preview'):
        result['preview'] = transform_cover_url(result['preview'], book.id)

    return result


def _task_to_dict(task: DownloadTask) -> Dict[str, Any]:
    """Convert DownloadTask object to dictionary representation.

    Maps DownloadTask fields to the format expected by the frontend,
    maintaining compatibility with the previous BookInfo-based format.
    Transforms external preview URLs to local proxy URLs when cover caching is enabled.
    """
    from cwa_book_downloader.core.utils import transform_cover_url

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
    """Download a task with cancellation support.

    Delegates to the appropriate handler based on the task's source.
    Handlers return a temp file path, orchestrator handles post-processing
    (archive extraction, moving to ingest) uniformly for all sources.

    Args:
        task_id: Task identifier
        cancel_flag: Threading event to signal cancellation

    Returns:
        str: Path to the downloaded file if successful, None otherwise
    """
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
        return None


def _process_library_mode(
    temp_file: Path,
    task: DownloadTask,
    status_callback,
) -> Optional[str]:
    """Process a download in Library Mode.

    Library mode organizes files directly into your library with template-based
    naming (e.g., "{Author}/{Series}/{Title}"). Files are transferred as-is
    with no processing.

    Use ingest mode if you need archive extraction or custom scripts.

    Returns:
        Path to library file if successful, None if library path not configured
    """
    # Check if this is an audiobook and use audiobook-specific settings if configured
    content_type = task.content_type.lower() if task.content_type else ""
    is_audiobook = "audiobook" in content_type

    if is_audiobook:
        # Use audiobook-specific settings, falling back to main settings
        library_path = config.get("LIBRARY_PATH_AUDIOBOOK") or config.get("LIBRARY_PATH")
        template = config.get("LIBRARY_TEMPLATE_AUDIOBOOK") or config.get("LIBRARY_TEMPLATE", "{Author}/{Title}")
    else:
        library_path = config.get("LIBRARY_PATH")
        template = config.get("LIBRARY_TEMPLATE", "{Author}/{Title}")

    if not library_path:
        logger.warning("Library mode enabled but no library path configured, falling back to ingest")
        status_callback("resolving", "Library path not configured, using ingest")
        return None

    # Validate library path
    library_path_obj = Path(library_path)
    if not library_path_obj.is_absolute():
        logger.warning(f"Library path must be absolute: {library_path}, falling back to ingest")
        status_callback("resolving", f"Library path must be absolute: {library_path}")
        return None

    if not library_path_obj.exists():
        try:
            library_path_obj.mkdir(parents=True, exist_ok=True)
        except (OSError, PermissionError) as e:
            logger.warning(f"Cannot create library path: {e}, falling back to ingest")
            status_callback("resolving", f"Cannot create library path: {e}")
            return None

    if not os.access(library_path_obj, os.W_OK):
        logger.warning(f"Library path not writable: {library_path}, falling back to ingest")
        status_callback("resolving", f"Library path not writable: {library_path}")
        return None

    # Determine if we should use hardlinking (torrents only)
    use_hardlink = False
    hardlink_source = None

    if task.original_download_path:
        # Torrent with download client path available
        torrent_hardlink_enabled = config.get("TORRENT_HARDLINK", True)
        if torrent_hardlink_enabled:
            hardlink_source = Path(task.original_download_path)
            if hardlink_source.exists():
                # Check same filesystem (required for hardlinks)
                if same_filesystem(hardlink_source, library_path):
                    use_hardlink = True
                else:
                    logger.warning(
                        f"Cannot hardlink: {hardlink_source} and {library_path} are on different filesystems. "
                        "Falling back to copy. To fix: ensure torrent client downloads to same filesystem as library."
                    )
                    status_callback("resolving", "Cannot hardlink (different filesystems), using copy")

    # Build metadata dict for template
    metadata = {
        "Author": task.author,
        "Title": task.title,
        "Subtitle": task.subtitle,
        "Year": task.year,
        "Series": task.series_name,
        "SeriesPosition": task.series_position,
    }

    try:
        if use_hardlink:
            status_callback("resolving", "Creating library hardlinks")
        else:
            status_callback("resolving", "Organizing in library")

        # Use torrent client path for hardlinks, staging path for moves
        source = hardlink_source if use_hardlink else temp_file

        if source.is_dir():
            return _transfer_directory_to_library(
                source, library_path, template, metadata, task, temp_file, status_callback, use_hardlink
            )
        else:
            return _transfer_file_to_library(
                source, library_path, template, metadata, task, temp_file, status_callback, use_hardlink
            )
    except PermissionError as e:
        logger.error(f"Permission denied in library mode: {e}")
        status_callback("error", f"Permission denied: {e}")
        return None
    except Exception as e:
        logger.error_trace(f"Library mode failed: {e}")
        status_callback("error", f"Library mode failed: {e}")
        return None


def _is_torrent_source(source_path: Path, task: DownloadTask) -> bool:
    """Check if source is the torrent client path (needs copy to preserve seeding)."""
    if not task.original_download_path:
        return False
    try:
        return source_path.resolve() == Path(task.original_download_path).resolve()
    except (OSError, ValueError):
        return False


def _get_unique_path(dest_path: Path) -> Path:
    """Return a unique path by appending counter if file already exists.

    Note: Has TOCTOU race. Use _atomic_hardlink/_atomic_move for concurrent safety.
    """
    if not dest_path.exists():
        return dest_path

    base = dest_path.stem
    ext = dest_path.suffix
    counter = 1
    while dest_path.exists():
        dest_path = dest_path.parent / f"{base}_{counter}{ext}"
        counter += 1

    logger.info(f"File already exists, saving as: {dest_path.name}")
    return dest_path


def _atomic_hardlink(source_path: Path, dest_path: Path, max_attempts: int = 100) -> Path:
    """Create a hardlink with atomic collision detection. Retries with counter suffix on collision."""
    base = dest_path.stem
    ext = dest_path.suffix

    for attempt in range(max_attempts):
        try_path = dest_path if attempt == 0 else dest_path.parent / f"{base}_{attempt}{ext}"
        try:
            os.link(str(source_path), str(try_path))
            if attempt > 0:
                logger.info(f"File collision resolved: {try_path.name}")
            return try_path
        except FileExistsError:
            continue

    raise RuntimeError(f"Could not create hardlink after {max_attempts} attempts: {dest_path}")


def _atomic_copy(source_path: Path, dest_path: Path, max_attempts: int = 100) -> Path:
    """Copy a file with atomic collision detection. Retries with counter suffix on collision."""
    base = dest_path.stem
    ext = dest_path.suffix

    for attempt in range(max_attempts):
        try_path = dest_path if attempt == 0 else dest_path.parent / f"{base}_{attempt}{ext}"
        try:
            # Atomically claim the destination by creating an exclusive file
            fd = os.open(str(try_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            try:
                # Copy to temp file first, then replace to avoid partial files
                temp_path = try_path.parent / f".{try_path.name}.tmp"
                shutil.copy2(str(source_path), str(temp_path))
                temp_path.replace(try_path)
                if attempt > 0:
                    logger.info(f"File collision resolved: {try_path.name}")
                return try_path
            except Exception:
                try_path.unlink(missing_ok=True)
                temp_path.unlink(missing_ok=True) if 'temp_path' in locals() else None
                raise
        except FileExistsError:
            continue

    raise RuntimeError(f"Could not copy file after {max_attempts} attempts: {dest_path}")


def _atomic_move(source_path: Path, dest_path: Path, max_attempts: int = 100) -> Path:
    """Move a file with atomic collision detection. Retries with counter suffix on collision."""
    import errno

    base = dest_path.stem
    ext = dest_path.suffix

    for attempt in range(max_attempts):
        try_path = dest_path if attempt == 0 else dest_path.parent / f"{base}_{attempt}{ext}"
        try:
            os.link(str(source_path), str(try_path))
            source_path.unlink()
            if attempt > 0:
                logger.info(f"File collision resolved: {try_path.name}")
            return try_path
        except FileExistsError:
            continue
        except OSError as e:
            # Cross-filesystem - fall back to exclusive create
            if e.errno not in (errno.EXDEV, errno.EMLINK):
                raise
            try:
                fd = os.open(str(try_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                try:
                    shutil.move(str(source_path), str(try_path))
                    if attempt > 0:
                        logger.info(f"File collision resolved: {try_path.name}")
                    return try_path
                except Exception:
                    try_path.unlink(missing_ok=True)
                    raise
            except FileExistsError:
                continue

    raise RuntimeError(f"Could not move file after {max_attempts} attempts: {dest_path}")


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

    status_callback("complete", "Complete (library mode)")
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

    count_msg = f" ({len(transferred_paths)} files," if len(transferred_paths) > 1 else " ("
    status_callback("complete", f"Complete{count_msg} library mode)")

    return str(transferred_paths[0])


def _post_process_download(
    temp_file: Path,
    task: DownloadTask,
    cancel_flag: Event,
    status_callback,
) -> Optional[str]:
    """Post-process a downloaded file: handle archives and move to ingest.

    This runs uniformly for all download sources, ensuring consistent behavior.

    Args:
        temp_file: Path to downloaded file in temp directory
        task: Download task with metadata
        cancel_flag: Cancellation event
        status_callback: Callback for status updates

    Returns:
        Final path in ingest directory, or None on failure
    """
    content_type = task.content_type.lower() if task.content_type else ""
    is_audiobook = "audiobook" in content_type

    # Validate search_mode
    if task.search_mode is None:
        logger.warning(f"Task {task.task_id} has no search_mode set, defaulting to Direct mode behavior")
    elif task.search_mode not in (SearchMode.DIRECT, SearchMode.UNIVERSAL):
        logger.warning(f"Task {task.task_id} has invalid search_mode '{task.search_mode}', defaulting to Direct mode behavior")

    # Library mode and audiobook-specific directories only apply to Universal mode
    is_universal = task.search_mode == SearchMode.UNIVERSAL

    if is_universal:
        # Determine processing mode based on content type
        if is_audiobook:
            processing_mode = config.get("PROCESSING_MODE_AUDIOBOOK", "ingest")
        else:
            processing_mode = config.get("PROCESSING_MODE", "ingest")

        if processing_mode == "library":
            result = _process_library_mode(temp_file, task, status_callback)
            if result is not None:
                return result
            # If library mode fails, fall through to normal processing

    # Determine ingest directory
    default_ingest_dir = get_ingest_dir()

    if is_universal and is_audiobook:
        # Universal audiobooks use dedicated setting, falling back to main ingest dir
        audiobook_ingest = config.get("INGEST_DIR_AUDIOBOOK", "")
        ingest_dir = Path(audiobook_ingest) if audiobook_ingest else default_ingest_dir
    else:
        # Direct mode and non-audiobook content use content-type routing
        ingest_dir = get_ingest_dir(content_type)

    if ingest_dir != default_ingest_dir:
        logger.debug(f"Routing '{content_type or 'default'}' to {ingest_dir}")
    os.makedirs(ingest_dir, exist_ok=True)

    # For torrents going to ingest mode, stage first to preserve seeding
    # (Torrent handler returns original path, not staged copy)
    if _is_torrent_source(temp_file, task):
        status_callback("resolving", "Staging torrent files")
        staging_dir = get_staging_dir()

        if temp_file.is_dir():
            staged_path = staging_dir / temp_file.name
            counter = 1
            while staged_path.exists():
                staged_path = staging_dir / f"{temp_file.name}_{counter}"
                counter += 1
            shutil.copytree(str(temp_file), str(staged_path))
            logger.debug(f"Staged torrent directory: {staged_path.name}")
        else:
            staged_path = staging_dir / temp_file.name
            counter = 1
            while staged_path.exists():
                staged_path = staging_dir / f"{temp_file.stem}_{counter}{temp_file.suffix}"
                counter += 1
            shutil.copy2(str(temp_file), str(staged_path))
            logger.debug(f"Staged torrent file: {staged_path.name}")

        temp_file = staged_path

    # Handle archive extraction (RAR/ZIP)
    if is_archive(temp_file):
        logger.info(f"Archive detected, extracting: {temp_file.name}")
        status_callback("resolving", "Extracting archive")

        result = process_archive(
            archive_path=temp_file,
            temp_dir=TMP_DIR,
            ingest_dir=ingest_dir,
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
            ingest_dir=ingest_dir,
            task=task,
        )

        if error:
            status_callback("error", error)
            return None

        if final_paths:
            if len(final_paths) == 1:
                message = "Complete"
            else:
                message = f"Complete ({len(final_paths)} files)"
            status_callback("complete", message)
            return str(final_paths[0])
        else:
            status_callback("error", "No book files found")
            return None

    # Non-archive: run custom script if configured, then move to ingest
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
        logger.info(f"Download cancelled before ingest: {task.task_id}")
        temp_file.unlink(missing_ok=True)
        return None

    # Generate filename: use formatted name if USE_BOOK_TITLE, else preserve original
    if config.USE_BOOK_TITLE:
        filename = task.get_filename()
        if not filename:
            filename = temp_file.name
    else:
        filename = temp_file.name

    dest_path = ingest_dir / filename

    try:
        final_path = _atomic_move(temp_file, dest_path)
    except Exception as e:
        logger.error(f"Failed to move file to ingest: {e}")
        status_callback("error", f"Failed to move file: {e}")
        return None

    logger.info(f"Download completed: {final_path.name}")

    status_callback("complete", "Complete")

    return str(final_path)

def update_download_progress(book_id: str, progress: float) -> None:
    """Update download progress with throttled WebSocket broadcasts.

    Progress is always stored in the queue, but WebSocket broadcasts are
    throttled to avoid flooding clients with updates. Broadcasts occur:
    - At most once per DOWNLOAD_PROGRESS_UPDATE_INTERVAL seconds
    - Always at 0% (start) and 100% (complete)
    - On significant progress jumps (>10%)
    """
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
    """Update download status with optional detailed message.
    
    Args:
        book_id: Book identifier
        status: Status string (e.g., 'resolving', 'downloading')
        message: Optional detailed status message for UI display
    """
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
    """Cancel a download.
    
    Args:
        book_id: Book identifier to cancel
        
    Returns:
        bool: True if cancellation was successful
    """
    result = book_queue.cancel_download(book_id)
    
    # Broadcast status update via WebSocket
    if result and ws_manager and ws_manager.is_enabled():
        ws_manager.broadcast_status_update(queue_status())
    
    return result

def set_book_priority(book_id: str, priority: int) -> bool:
    """Set priority for a queued book.
    
    Args:
        book_id: Book identifier
        priority: New priority level (lower = higher priority)
        
    Returns:
        bool: True if priority was successfully changed
    """
    return book_queue.set_priority(book_id, priority)

def reorder_queue(book_priorities: Dict[str, int]) -> bool:
    """Bulk reorder queue.
    
    Args:
        book_priorities: Dict mapping book_id to new priority
        
    Returns:
        bool: True if reordering was successful
    """
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
    """Start the download coordinator thread.

    This should be called once during application startup.
    Calling multiple times is safe - subsequent calls are no-ops.
    """
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
