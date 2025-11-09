"""Backend logic for the book download application."""

import threading, time
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
import subprocess
import os
from concurrent.futures import ThreadPoolExecutor, Future
from threading import Event

from logger import setup_logger
from config import CUSTOM_SCRIPT
from env import INGEST_DIR, TMP_DIR, MAIN_LOOP_SLEEP_TIME, USE_BOOK_TITLE, MAX_CONCURRENT_DOWNLOADS, DOWNLOAD_PROGRESS_UPDATE_INTERVAL
from models import book_queue, BookInfo, QueueStatus, SearchFilters
import book_manager

logger = setup_logger(__name__)

# Import WebSocket manager (will be initialized by app.py)
try:
    from websocket_manager import ws_manager
except ImportError:
    logger.warning("WebSocket manager not available")
    ws_manager = None

def _sanitize_filename(filename: str) -> str:
    """Sanitize a filename by replacing spaces with underscores and removing invalid characters."""
    keepcharacters = (' ','.','_')
    return "".join(c for c in filename if c.isalnum() or c in keepcharacters).rstrip()

def search_books(query: str, filters: SearchFilters) -> List[Dict[str, Any]]:
    """Search for books matching the query.
    
    Args:
        query: Search term
        filters: Search filters object
        
    Returns:
        List[Dict]: List of book information dictionaries
    """
    try:
        books = book_manager.search_books(query, filters)
        return [_book_info_to_dict(book) for book in books]
    except Exception as e:
        logger.error_trace(f"Error searching books: {e}")
        return []

def get_book_info(book_id: str) -> Optional[Dict[str, Any]]:
    """Get detailed information for a specific book.
    
    Args:
        book_id: Book identifier
        
    Returns:
        Optional[Dict]: Book information dictionary if found
    """
    try:
        book = book_manager.get_book_info(book_id)
        return _book_info_to_dict(book)
    except Exception as e:
        logger.error_trace(f"Error getting book info: {e}")
        return None

def queue_book(book_id: str, priority: int = 0) -> bool:
    """Add a book to the download queue with specified priority.
    
    Args:
        book_id: Book identifier
        priority: Priority level (lower number = higher priority)
        
    Returns:
        bool: True if book was successfully queued
    """
    try:
        book_info = book_manager.get_book_info(book_id)
        book_queue.add(book_id, book_info, priority)
        logger.info(f"Book queued with priority {priority}: {book_info.title}")
        
        # Broadcast status update via WebSocket
        if ws_manager and ws_manager.is_enabled():
            ws_manager.broadcast_status_update(queue_status())
        
        return True
    except Exception as e:
        logger.error_trace(f"Error queueing book: {e}")
        return False

def queue_status() -> Dict[str, Dict[str, Any]]:
    """Get current status of the download queue.
    
    Returns:
        Dict: Queue status organized by status type with serialized book data
    """
    status = book_queue.get_status()
    for _, books in status.items():
        for _, book_info in books.items():
            if book_info.download_path:
                if not os.path.exists(book_info.download_path):
                    book_info.download_path = None

    # Convert Enum keys to strings and BookInfo objects to dicts for JSON serialization
    return {
        status_type.value: {
            book_id: _book_info_to_dict(book_info)
            for book_id, book_info in books.items()
        }
        for status_type, books in status.items()
    }

def get_book_data(book_id: str) -> Tuple[Optional[bytes], BookInfo]:
    """Get book data for a specific book, including its title.
    
    Args:
        book_id: Book identifier
        
    Returns:
        Tuple[Optional[bytes], str]: Book data if available, and the book title
    """
    try:
        book_info = book_queue._book_data[book_id]
        path = book_info.download_path
        with open(path, "rb") as f:
            return f.read(), book_info
    except Exception as e:
        logger.error_trace(f"Error getting book data: {e}")
        if book_info:
            book_info.download_path = None
        return None, book_info if book_info else BookInfo(id=book_id, title="Unknown")

def _book_info_to_dict(book: BookInfo) -> Dict[str, Any]:
    """Convert BookInfo object to dictionary representation."""
    return {
        key: value for key, value in book.__dict__.items()
        if value is not None
    }

def _download_book_with_cancellation(book_id: str, cancel_flag: Event) -> Optional[str]:
    """Download and process a book with cancellation support.
    
    Args:
        book_id: Book identifier
        cancel_flag: Threading event to signal cancellation
        
    Returns:
        str: Path to the downloaded book if successful, None otherwise
    """
    try:
        # Check for cancellation before starting
        if cancel_flag.is_set():
            logger.info(f"Download cancelled before starting: {book_id}")
            return None
            
        book_info = book_queue._book_data[book_id]
        logger.info(f"Starting download: {book_info.title}")

        if USE_BOOK_TITLE:
            book_name = _sanitize_filename(book_info.title)
        else:
            book_name = book_id
        book_name += f".{book_info.format}"
        book_path = TMP_DIR / book_name

        # Check cancellation before download
        if cancel_flag.is_set():
            logger.info(f"Download cancelled before book manager call: {book_id}")
            return None
        
        progress_callback = lambda progress: update_download_progress(book_id, progress)
        success = book_manager.download_book(book_info, book_path, progress_callback, cancel_flag)
        
        # Stop progress updates
        cancel_flag.wait(0.1)  # Brief pause for progress thread cleanup
        
        if cancel_flag.is_set():
            logger.info(f"Download cancelled during download: {book_id}")
            # Clean up partial download
            if book_path.exists():
                book_path.unlink()
            return None
            
        if not success:
            raise Exception("Unknown error downloading book")

        # Check cancellation before post-processing
        if cancel_flag.is_set():
            logger.info(f"Download cancelled before post-processing: {book_id}")
            if book_path.exists():
                book_path.unlink()
            return None

        if CUSTOM_SCRIPT:
            logger.info(f"Running custom script: {CUSTOM_SCRIPT}")
            subprocess.run([CUSTOM_SCRIPT, book_path])
            
        intermediate_path = INGEST_DIR / f"{book_id}.crdownload"
        final_path = INGEST_DIR / book_name
        
        if os.path.exists(book_path):
            logger.info(f"Moving book to ingest directory: {book_path} -> {final_path}")
            try:
                shutil.move(book_path, intermediate_path)
            except Exception as e:
                try:
                    logger.debug(f"Error moving book: {e}, will try copying instead")
                    shutil.move(book_path, intermediate_path)
                except Exception as e:
                    logger.debug(f"Error copying book: {e}, will try copying without permissions instead")
                    shutil.copyfile(book_path, intermediate_path)
                os.remove(book_path)
            
            # Final cancellation check before completing
            if cancel_flag.is_set():
                logger.info(f"Download cancelled before final rename: {book_id}")
                if intermediate_path.exists():
                    intermediate_path.unlink()
                return None
                
            os.rename(intermediate_path, final_path)
            logger.info(f"Download completed successfully: {book_info.title}")
            
        return str(final_path)
    except Exception as e:
        if cancel_flag.is_set():
            logger.info(f"Download cancelled during error handling: {book_id}")
        else:
            logger.error_trace(f"Error downloading book: {e}")
        return None

def update_download_progress(book_id: str, progress: float) -> None:
    """Update download progress."""
    book_queue.update_progress(book_id, progress)
    
    # Broadcast progress via WebSocket
    if ws_manager and ws_manager.is_enabled():
        ws_manager.broadcast_download_progress(book_id, progress, 'downloading')

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

def _process_single_download(book_id: str, cancel_flag: Event) -> None:
    """Process a single download job."""
    try:
        book_queue.update_status(book_id, QueueStatus.DOWNLOADING)
        
        # Broadcast status change to downloading
        if ws_manager and ws_manager.is_enabled():
            ws_manager.broadcast_status_update(queue_status())
        
        download_path = _download_book_with_cancellation(book_id, cancel_flag)
        
        if cancel_flag.is_set():
            book_queue.update_status(book_id, QueueStatus.CANCELLED)
            # Broadcast cancellation
            if ws_manager and ws_manager.is_enabled():
                ws_manager.broadcast_status_update(queue_status())
            return
            
        if download_path:
            book_queue.update_download_path(book_id, download_path)
            new_status = QueueStatus.AVAILABLE
        else:
            new_status = QueueStatus.ERROR
            
        book_queue.update_status(book_id, new_status)
        
        # Broadcast final status (completed or error)
        if ws_manager and ws_manager.is_enabled():
            ws_manager.broadcast_status_update(queue_status())
        
        logger.info(
            f"Book {book_id} download {'successful' if download_path else 'failed'}"
        )
        
    except Exception as e:
        if not cancel_flag.is_set():
            logger.error_trace(f"Error in download processing: {e}")
            book_queue.update_status(book_id, QueueStatus.ERROR)
        else:
            logger.info(f"Download cancelled: {book_id}")
            book_queue.update_status(book_id, QueueStatus.CANCELLED)
        
        # Broadcast error/cancelled status
        if ws_manager and ws_manager.is_enabled():
            ws_manager.broadcast_status_update(queue_status())

def concurrent_download_loop() -> None:
    """Main download coordinator using ThreadPoolExecutor for concurrent downloads."""
    logger.info(f"Starting concurrent download loop with {MAX_CONCURRENT_DOWNLOADS} workers")
    
    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_DOWNLOADS, thread_name_prefix="BookDownload") as executor:
        active_futures: Dict[Future, str] = {}  # Track active download futures
        
        while True:
            # Clean up completed futures
            completed_futures = [f for f in active_futures if f.done()]
            for future in completed_futures:
                book_id = active_futures.pop(future)
                try:
                    future.result()  # This will raise any exceptions from the worker
                except Exception as e:
                    logger.error_trace(f"Future exception for {book_id}: {e}")
            
            # Start new downloads if we have capacity
            while len(active_futures) < MAX_CONCURRENT_DOWNLOADS:
                next_download = book_queue.get_next()
                if not next_download:
                    break
                    
                book_id, cancel_flag = next_download
                logger.info(f"Starting concurrent download: {book_id}")
                
                # Submit download job to thread pool
                future = executor.submit(_process_single_download, book_id, cancel_flag)
                active_futures[future] = book_id
            
            # Brief sleep to prevent busy waiting
            time.sleep(MAIN_LOOP_SLEEP_TIME)

# Start concurrent download coordinator
download_coordinator_thread = threading.Thread(
    target=concurrent_download_loop,
    daemon=True,
    name="DownloadCoordinator"
)
download_coordinator_thread.start()

logger.info(f"Download system initialized with {MAX_CONCURRENT_DOWNLOADS} concurrent workers")
