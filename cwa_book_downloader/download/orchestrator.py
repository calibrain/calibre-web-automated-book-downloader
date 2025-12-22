"""Download queue orchestration and worker management."""

import os
import random
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Event, Lock
from typing import Any, Dict, List, Optional, Tuple

from cwa_book_downloader.release_sources import direct_download
from cwa_book_downloader.release_sources.direct_download import SearchUnavailable
from cwa_book_downloader.core.config import config
from cwa_book_downloader.release_sources import get_handler, get_source_display_name
from cwa_book_downloader.core.logger import setup_logger
from cwa_book_downloader.core.models import BookInfo, DownloadTask, QueueStatus, SearchFilters
from cwa_book_downloader.core.queue import book_queue

logger = setup_logger(__name__)

# WebSocket manager (initialized by app.py)
try:
    from cwa_book_downloader.api.websocket import ws_manager
except ImportError:
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
        # Fetch book info for display purposes
        book_info = direct_download.get_book_info(book_id)
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

        # Get author and preview from top-level (preferred) or extra (fallback)
        author = release_data.get('author') or extra.get('author')
        preview = release_data.get('preview') or extra.get('preview')

        # Create a source-agnostic download task from release data
        task = DownloadTask(
            task_id=release_data['source_id'],
            source=source,
            title=release_data.get('title', 'Unknown'),
            author=author,
            format=release_data.get('format'),
            size=release_data.get('size'),
            preview=preview,
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
    import base64
    from cwa_book_downloader.config.env import is_covers_cache_enabled

    result = {
        key: value for key, value in book.__dict__.items()
        if value is not None
    }

    # Transform external preview URLs to local proxy URLs
    # Skip if already a local URL (starts with /)
    if result.get('preview') and is_covers_cache_enabled() and not result['preview'].startswith('/'):
        original_url = result['preview']
        encoded_url = base64.urlsafe_b64encode(original_url.encode()).decode()
        result['preview'] = f"/api/covers/{book.id}?url={encoded_url}"

    return result


def _task_to_dict(task: DownloadTask) -> Dict[str, Any]:
    """Convert DownloadTask object to dictionary representation.

    Maps DownloadTask fields to the format expected by the frontend,
    maintaining compatibility with the previous BookInfo-based format.
    Transforms external preview URLs to local proxy URLs when cover caching is enabled.
    """
    import base64
    from cwa_book_downloader.config.env import is_covers_cache_enabled

    preview = task.preview

    # Transform external preview URLs to local proxy URLs
    # Skip if already a local URL (starts with /)
    if preview and is_covers_cache_enabled() and not preview.startswith('/'):
        encoded_url = base64.urlsafe_b64encode(preview.encode()).decode()
        preview = f"/api/covers/{task.task_id}?url={encoded_url}"

    return {
        'id': task.task_id,
        'title': task.title,
        'author': task.author,
        'format': task.format,
        'size': task.size,
        'preview': preview,
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
    Each handler encapsulates all download and post-processing logic.

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

        # Create callbacks that update the orchestrator's tracking
        progress_callback = lambda progress: update_download_progress(task_id, progress)
        status_callback = lambda status, message=None: update_download_status(task_id, status, message)

        # Get the download handler based on the task's source
        handler = get_handler(task.source)
        return handler.download(
            task,
            cancel_flag,
            progress_callback,
            status_callback
        )

    except Exception as e:
        if cancel_flag.is_set():
            logger.info(f"Download cancelled during error handling: {task_id}")
        else:
            logger.error_trace(f"Error downloading: {e}")
        return None

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

def get_queue_order() -> List[Dict[str, any]]:
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
            new_status = QueueStatus.COMPLETE
        else:
            new_status = QueueStatus.ERROR

        book_queue.update_status(task_id, new_status)

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
