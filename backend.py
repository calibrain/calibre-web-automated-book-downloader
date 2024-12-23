"""Backend logic for the book download application."""

import threading, time
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple

from logger import setup_logger
from config import TMP_DIR, MAIN_LOOP_SLEEP_TIME, INGEST_DIR
from models import book_queue, BookInfo, QueueStatus
import book_manager

logger = setup_logger(__name__)

def search_books(query: str) -> List[Dict[str, Any]]:
    """Search for books matching the query.
    
    Args:
        query: Search term
        
    Returns:
        List[Dict]: List of book information dictionaries
    """
    try:
        books = book_manager.search_books(query)
        return [_book_info_to_dict(book) for book in books]
    except Exception as e:
        logger.error(f"Error searching books: {e}")
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
        logger.error(f"Error getting book info: {e}")
        return None

def queue_book(book_id: str) -> bool:
    """Add a book to the download queue.
    
    Args:
        book_id: Book identifier
        
    Returns:
        bool: True if book was successfully queued
    """
    try:
        book_info = book_manager.get_book_info(book_id)
        book_queue.add(book_id, book_info)
        logger.info(f"Book queued: {book_info.title}")
        return True
    except Exception as e:
        logger.error(f"Error queueing book: {e}")
        return False

def queue_status() -> Dict[str, Dict[str, Any]]:
    """Get current status of the download queue.
    
    Returns:
        Dict: Queue status organized by status type
    """
    status = book_queue.get_status()
    # Convert Enum keys to strings and properly format the response
    return {
        status_type.value: books
        for status_type, books in status.items()
    }

def get_book_data(book_id: str) -> Tuple[Optional[bytes], str] :
    """Get book data for a specific book, including its title.
    
    Args:
        book_id: Book identifier
        
    Returns:
        Tuple[Optional[bytes], str]: Book data if available, and the book title
    """
    try:
        book_info = book_queue._book_data[book_id]
        path = INGEST_DIR / f"{book_id}.epub"
        with open(path, "rb") as f:
            return f.read(), book_info.title
    except Exception as e:
        logger.error(f"Error getting book data: {e}")
        return None, ""

def _book_info_to_dict(book: BookInfo) -> Dict[str, Any]:
    """Convert BookInfo object to dictionary representation."""
    return {
        key: value for key, value in book.__dict__.items()
        if value is not None
    }

def _process_book(book_path: str) -> bool:
    """Check if downloaded book is valid.
    
    Args:
        book_path: Path to downloaded book file
        
    Returns:
        bool: True if book is valid
    """
    try:
        logger.info(f"Verifying book health: {book_path}")
        script_path = Path(__file__).parent / "check_health.sh"
        result = subprocess.run(
            [str(script_path), book_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        logger.info(f"Health check result: {result.stdout.decode()}")
        return result.returncode == 0
    except Exception as e:
        logger.error(f"Error checking book health: {e}")
        return False

def _download_book(book_id: str) -> bool:
    """Download and process a book.
    
    Args:
        book_id: Book identifier
        
    Returns:
        bool: True if download and processing successful
    """
    try:
        book_info = book_queue._book_data[book_id]
        book_path = TMP_DIR / f"{book_id}.{book_info.format}"
        success = book_manager.download_book(book_info, book_path)
        if not success:
            raise Exception("Unkown error downloading book")
        return _process_book(str(book_path))
        
    except Exception as e:
        logger.error(f"Error downloading book: {e}")
        return False

def download_loop():
    """Background thread for processing download queue."""
    logger.info("Starting download loop")
    
    while True:
        book_id = book_queue.get_next()
        if not book_id:
            time.sleep(MAIN_LOOP_SLEEP_TIME)
            continue
            
        try:
            book_queue.update_status(book_id, QueueStatus.DOWNLOADING)
            success = _download_book(book_id)
            
            new_status = (
                QueueStatus.AVAILABLE if success else QueueStatus.ERROR
            )
            book_queue.update_status(book_id, new_status)
            
            logger.info(
                f"Book {book_id} download {'successful' if success else 'failed'}"
            )
            
        except Exception as e:
            logger.error(f"Error in download loop: {e}")
            book_queue.update_status(book_id, QueueStatus.ERROR)

# Start download loop in background thread
download_thread = threading.Thread(
    target=download_loop,
    daemon=True
)
download_thread.start()
