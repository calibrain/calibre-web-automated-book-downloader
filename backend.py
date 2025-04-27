"""Backend logic for the book download application."""

import threading, time
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
import subprocess
import os

from logger import setup_logger
from config import CUSTOM_SCRIPT, CROSS_FILE_SYSTEM
from env import INGEST_DIR, TMP_DIR, MAIN_LOOP_SLEEP_TIME, USE_BOOK_TITLE
from models import book_queue, BookInfo, QueueStatus, SearchFilters
import book_manager

logger = setup_logger(__name__)

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
        logger.error_trace(f"Error queueing book: {e}")
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
        path = book_info.download_path
        with open(path, "rb") as f:
            return f.read(), book_info.title
    except Exception as e:
        logger.error_trace(f"Error getting book data: {e}")
        book_info.download_path = None
        return None, ""

def _book_info_to_dict(book: BookInfo) -> Dict[str, Any]:
    """Convert BookInfo object to dictionary representation."""
    return {
        key: value for key, value in book.__dict__.items()
        if value is not None
    }

def _download_book(book_id: str) -> Optional[str]:
    """Download and process a book.
    
    Args:
        book_id: Book identifier
        
    Returns:
        str: Path to the downloaded book if successful, None otherwise
    """
    try:
        book_info = book_queue._book_data[book_id]

        if USE_BOOK_TITLE:
            book_name = _sanitize_filename(book_info.title)
        else:
            book_name = book_id
        book_name += f".{book_info.format}"
        book_path = TMP_DIR / book_name

        success = book_manager.download_book(book_info, book_path)
        if not success:
            raise Exception("Unkown error downloading book")

        if CUSTOM_SCRIPT:
            logger.info(f"Running custom script: {CUSTOM_SCRIPT}")
            subprocess.run([CUSTOM_SCRIPT, book_path])

        intermediate_path = INGEST_DIR /  book_id # Without extension
        final_path = INGEST_DIR /  book_name
        
        if os.path.exists(book_path):
            if CROSS_FILE_SYSTEM:
                logger.info(f"Copying book to ingest directory then renaming: {book_path} -> {intermediate_path} -> {final_path}")
                try:
                    shutil.move(book_path, intermediate_path)
                except Exception as e:
                    logger.debug(f"Error moving book: {e}, will try copying instead")
                    shutil.copy(book_path, intermediate_path)
                    os.remove(book_path)
            else:
                logger.info(f"Moving book to ingest directory: {book_path} -> {intermediate_path}")
                shutil.move(book_path, intermediate_path)
            logger.info(f"Renaming book: {intermediate_path} -> {final_path}")
            os.rename(intermediate_path, final_path)
        return str(final_path)
    except Exception as e:
        logger.error_trace(f"Error downloading book: {e}")
        return None

def download_loop() -> None:
    """Background thread for processing download queue."""
    logger.info("Starting download loop")
    
    while True:
        book_id = book_queue.get_next()
        if not book_id:
            time.sleep(MAIN_LOOP_SLEEP_TIME)
            continue
            
        try:
            book_queue.update_status(book_id, QueueStatus.DOWNLOADING)
            download_path = _download_book(book_id)
            if download_path:
                book_queue.update_download_path(book_id, download_path)

            new_status = (
                QueueStatus.AVAILABLE if download_path else QueueStatus.ERROR
            )
            book_queue.update_status(book_id, new_status)
            
            logger.info(
                f"Book {book_id} download {'successful' if download_path else 'failed'}"
            )
            
        except Exception as e:
            logger.error_trace(f"Error in download loop: {e}")
            book_queue.update_status(book_id, QueueStatus.ERROR)

# Start download loop in background thread
download_thread = threading.Thread(
    target=download_loop,
    daemon=True
)
download_thread.start()
