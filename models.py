"""Data structures and models used across the application."""

from dataclasses import dataclass, field
from typing import Dict, List, Optional
from enum import Enum
from datetime import datetime, timedelta
from threading import Lock
from pathlib import Path
from env import INGEST_DIR, STATUS_TIMEOUT

class QueueStatus(str, Enum):
    """Enum for possible book queue statuses."""
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    AVAILABLE = "available"
    ERROR = "error"
    DONE = "done"

@dataclass
class BookInfo:
    """Data class representing book information."""
    id: str
    title: str
    preview: Optional[str] = None
    author: Optional[str] = None
    publisher: Optional[str] = None
    year: Optional[str] = None
    language: Optional[str] = None
    format: Optional[str] = None
    size: Optional[str] = None
    info: Optional[Dict[str, List[str]]] = None
    download_urls: List[str] = field(default_factory=list)
    download_path: Optional[str] = None
    download_progress: Optional[int] = 0

class BookQueue:
    """Thread-safe book queue manager."""
    def __init__(self) -> None:
        self._queue: set[str] = set()
        self._lock = Lock()
        self._status: dict[str, QueueStatus] = {}
        self._book_data: dict[str, BookInfo]= {}
        self._status_timestamps: dict[str, datetime] = {}  # Track when each status was last updated
        self._status_timeout = timedelta(seconds=STATUS_TIMEOUT)  # 1 hour timeout
    
    def add(self, book_id: str, book_data: BookInfo) -> None:
        """Add a book to the queue."""
        with self._lock:
            self._queue.add(book_id)
            self._book_data[book_id] = book_data
            self._update_status(book_id, QueueStatus.QUEUED)
    
    def get_next(self) -> Optional[str]:
        """Get next book ID from queue."""
        with self._lock:
            return self._queue.pop() if self._queue else None
            
    def _update_status(self, book_id: str, status: QueueStatus) -> None:
        """Internal method to update status and timestamp."""
        self._status[book_id] = status
        self._status_timestamps[book_id] = datetime.now()
            
    def update_status(self, book_id: str, status: QueueStatus) -> None:
        """Update status of a book in the queue."""
        with self._lock:
            self._update_status(book_id, status)
    
    def update_download_path(self, book_id: str, download_path: str) -> None:
        """Update the download path of a book in the queue."""
        with self._lock:
            self._book_data[book_id].download_path = download_path
    
    def update_progress(self, book_id: str, progress: int) -> None:
        """Update download progress (0-100) for a book."""
        with self._lock:
            if book_id in self._book_data:
                # Clamp between 0 and 100
                progress = max(0, min(100, int(progress)))
                self._book_data[book_id].download_progress = progress
            
    def get_status(self) -> Dict[QueueStatus, Dict[str, BookInfo]]:
        """Get current queue status."""
        self.refresh()
        with self._lock:
            result: Dict[QueueStatus, Dict[str, BookInfo]] = {status: {} for status in QueueStatus}
            for book_id, status in self._status.items():
                if book_id in self._book_data:
                    result[status][book_id] = self._book_data[book_id]
            return result
        
    def refresh(self) -> None:
        """Remove any books that are done downloading or have stale status."""
        with self._lock:
            current_time = datetime.now()
            
            # Create a list of items to remove to avoid modifying dict during iteration
            to_remove = []
            
            for book_id, status in self._status.items():
                path = self._book_data[book_id].download_path
                if path and not Path(path).exists():
                    self._book_data[book_id].download_path = None
                    path = None
                
                # Check for completed downloads
                if status == QueueStatus.AVAILABLE:
                    if not path:
                        self._update_status(book_id, QueueStatus.DONE)
                
                # Check for stale status entries
                last_update = self._status_timestamps.get(book_id)
                if last_update and (current_time - last_update) > self._status_timeout:
                    if status == QueueStatus.DONE or status == QueueStatus.ERROR or status == QueueStatus.AVAILABLE:
                        to_remove.append(book_id)
            
            # Remove stale entries
            for book_id in to_remove:
                del self._status[book_id]
                del self._status_timestamps[book_id]
                if book_id in self._book_data:
                    del self._book_data[book_id]

    def set_status_timeout(self, hours: int) -> None:
        """Set the status timeout duration in hours."""
        with self._lock:
            self._status_timeout = timedelta(hours=hours)


# Global instance of BookQueue
book_queue = BookQueue()

@dataclass
class SearchFilters:
    isbn: Optional[List[str]] = None
    author: Optional[List[str]] = None
    title: Optional[List[str]] = None
    lang: Optional[List[str]] = None
    sort: Optional[str] = None
    content: Optional[List[str]] = None
    format: Optional[List[str]] = None