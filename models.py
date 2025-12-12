"""Data structures and models used across the application."""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from enum import Enum
from datetime import datetime, timedelta
from threading import Lock, Event
from pathlib import Path
import queue
import time
from env import INGEST_DIR, STATUS_TIMEOUT

class QueueStatus(str, Enum):
    """Enum for possible book queue statuses."""
    QUEUED = "queued"
    RESOLVING = "resolving"
    BYPASSING = "bypassing"
    DOWNLOADING = "downloading"
    VERIFYING = "verifying"
    INGESTING = "ingesting"
    COMPLETE = "complete"
    AVAILABLE = "available"
    ERROR = "error"
    DONE = "done"
    CANCELLED = "cancelled"

@dataclass
class QueueItem:
    """Queue item with priority and metadata."""
    book_id: str
    priority: int
    added_time: float
    
    def __lt__(self, other):
        """Compare items for priority queue (lower priority number = higher precedence)."""
        if self.priority != other.priority:
            return self.priority < other.priority
        return self.added_time < other.added_time

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
    content: Optional[str] = None
    format: Optional[str] = None
    size: Optional[str] = None
    info: Optional[Dict[str, List[str]]] = None
    description: Optional[str] = None
    download_urls: List[str] = field(default_factory=list)
    download_path: Optional[str] = None
    priority: int = 0
    progress: Optional[float] = None
    status_message: Optional[str] = None  # Detailed status message for UI display

class BookQueue:
    """Thread-safe book queue manager with priority support and cancellation."""
    def __init__(self) -> None:
        self._queue: queue.PriorityQueue[QueueItem] = queue.PriorityQueue()
        self._lock = Lock()
        self._status: dict[str, QueueStatus] = {}
        self._book_data: dict[str, BookInfo] = {}
        self._status_timestamps: dict[str, datetime] = {}  # Track when each status was last updated
        self._status_timeout = timedelta(seconds=STATUS_TIMEOUT)  # 1 hour timeout
        self._cancel_flags: dict[str, Event] = {}  # Cancellation flags for active downloads
        self._active_downloads: dict[str, bool] = {}  # Track currently downloading books
    
    def add(self, book_id: str, book_data: BookInfo, priority: int = 0) -> None:
        """Add a book to the queue with specified priority.
        
        Args:
            book_id: Unique identifier for the book
            book_data: Book information
            priority: Priority level (lower number = higher priority)
        """
        with self._lock:
            # Don't add if already exists and not in error/done state
            if book_id in self._status and self._status[book_id] not in [QueueStatus.ERROR, QueueStatus.DONE, QueueStatus.CANCELLED]:
                return
                
            book_data.priority = priority
            queue_item = QueueItem(book_id, priority, time.time())
            self._queue.put(queue_item)
            self._book_data[book_id] = book_data
            self._update_status(book_id, QueueStatus.QUEUED)
    
    def get_next(self) -> Optional[Tuple[str, Event]]:
        """Get next book ID from queue with cancellation flag.
        
        Returns:
            Tuple of (book_id, cancel_flag) or None if queue is empty
        """
        try:
            queue_item = self._queue.get_nowait()
            book_id = queue_item.book_id
            
            with self._lock:
                # Check if book was cancelled while in queue
                if book_id in self._status and self._status[book_id] == QueueStatus.CANCELLED:
                    return self.get_next()  # Recursively get next non-cancelled item
                
                # Create cancellation flag for this download
                cancel_flag = Event()
                self._cancel_flags[book_id] = cancel_flag
                self._active_downloads[book_id] = True
                
            return book_id, cancel_flag
        except queue.Empty:
            return None
            
    def _update_status(self, book_id: str, status: QueueStatus) -> None:
        """Internal method to update status and timestamp."""
        self._status[book_id] = status
        self._status_timestamps[book_id] = datetime.now()
            
    def update_status(self, book_id: str, status: QueueStatus) -> None:
        """Update status of a book in the queue."""
        with self._lock:
            self._update_status(book_id, status)
            
            # Clean up active download tracking when finished
            if status in [QueueStatus.COMPLETE, QueueStatus.AVAILABLE, QueueStatus.ERROR, QueueStatus.DONE, QueueStatus.CANCELLED]:
                self._active_downloads.pop(book_id, None)
                self._cancel_flags.pop(book_id, None)
    
    def update_download_path(self, book_id: str, download_path: str) -> None:
        """Update the download path of a book in the queue."""
        with self._lock:
            if book_id in self._book_data:
                self._book_data[book_id].download_path = download_path
                
    def update_progress(self, book_id: str, progress: float) -> None:
        """Update download progress for a book."""
        with self._lock:
            if book_id in self._book_data:
                self._book_data[book_id].progress = progress
    
    def update_status_message(self, book_id: str, message: str) -> None:
        """Update detailed status message for a book."""
        with self._lock:
            if book_id in self._book_data:
                self._book_data[book_id].status_message = message
            
    def get_status(self) -> Dict[QueueStatus, Dict[str, BookInfo]]:
        """Get current queue status."""
        self.refresh()
        with self._lock:
            result: Dict[QueueStatus, Dict[str, BookInfo]] = {status: {} for status in QueueStatus}
            for book_id, status in self._status.items():
                if book_id in self._book_data:
                    result[status][book_id] = self._book_data[book_id]
            return result
            
    def get_queue_order(self) -> List[Dict[str, any]]:
        """Get current queue order for display."""
        with self._lock:
            queue_items = []
            
            # Get items from priority queue without removing them
            temp_items = []
            while not self._queue.empty():
                try:
                    item = self._queue.get_nowait()
                    temp_items.append(item)
                    if item.book_id in self._book_data:
                        book_info = self._book_data[item.book_id]
                        queue_items.append({
                            'id': item.book_id,
                            'title': book_info.title,
                            'author': book_info.author,
                            'priority': item.priority,
                            'added_time': item.added_time,
                            'status': self._status.get(item.book_id, QueueStatus.QUEUED)
                        })
                except queue.Empty:
                    break
            
            # Put items back in queue
            for item in temp_items:
                self._queue.put(item)
                
            return sorted(queue_items, key=lambda x: (x['priority'], x['added_time']))
            
    def cancel_download(self, book_id: str) -> bool:
        """Cancel a download and mark it as cancelled.
        
        Args:
            book_id: Book identifier to cancel
            
        Returns:
            bool: True if cancellation was successful
        """
        with self._lock:
            current_status = self._status.get(book_id)
            
            # Allow cancellation during any active state
            if current_status in [QueueStatus.RESOLVING, QueueStatus.BYPASSING, QueueStatus.DOWNLOADING, QueueStatus.VERIFYING, QueueStatus.INGESTING]:
                # Signal active download to stop
                if book_id in self._cancel_flags:
                    self._cancel_flags[book_id].set()
                self._update_status(book_id, QueueStatus.CANCELLED)
                return True
            elif current_status == QueueStatus.QUEUED:
                # Remove from queue and mark as cancelled
                self._update_status(book_id, QueueStatus.CANCELLED)
                return True
            
            return False
            
    def set_priority(self, book_id: str, new_priority: int) -> bool:
        """Change the priority of a queued book.
        
        Args:
            book_id: Book identifier
            new_priority: New priority level (lower = higher priority)
            
        Returns:
            bool: True if priority was successfully changed
        """
        with self._lock:
            if book_id not in self._status or self._status[book_id] != QueueStatus.QUEUED:
                return False
                
            # Remove book from queue and re-add with new priority
            temp_items = []
            found = False
            
            while not self._queue.empty():
                try:
                    item = self._queue.get_nowait()
                    if item.book_id == book_id:
                        # Create new item with updated priority
                        new_item = QueueItem(book_id, new_priority, item.added_time)
                        temp_items.append(new_item)
                        found = True
                        # Update book data priority
                        if book_id in self._book_data:
                            self._book_data[book_id].priority = new_priority
                    else:
                        temp_items.append(item)
                except queue.Empty:
                    break
            
            # Put all items back
            for item in temp_items:
                self._queue.put(item)
                
            return found
            
    def reorder_queue(self, book_priorities: Dict[str, int]) -> bool:
        """Bulk reorder queue by setting new priorities.
        
        Args:
            book_priorities: Dict mapping book_id to new priority
            
        Returns:
            bool: True if reordering was successful
        """
        with self._lock:
            # Extract all items from queue
            all_items = []
            while not self._queue.empty():
                try:
                    item = self._queue.get_nowait()
                    # Update priority if specified
                    if item.book_id in book_priorities:
                        new_priority = book_priorities[item.book_id]
                        item = QueueItem(item.book_id, new_priority, item.added_time)
                        # Update book data priority
                        if item.book_id in self._book_data:
                            self._book_data[item.book_id].priority = new_priority
                    all_items.append(item)
                except queue.Empty:
                    break
            
            # Put all items back with updated priorities
            for item in all_items:
                self._queue.put(item)
                
            return True
            
    def get_active_downloads(self) -> List[str]:
        """Get list of currently active download book IDs."""
        with self._lock:
            return list(self._active_downloads.keys())
    
    def has_pending_work(self) -> bool:
        """Check if there are any active downloads or queued items.
        
        This is useful for determining if the bypasser should stay active
        even when the UI is closed.
        
        Returns:
            bool: True if there are active downloads or queued items
        """
        with self._lock:
            # Check for active downloads
            if self._active_downloads:
                return True
            
            # Check for queued items (excluding cancelled ones)
            for book_id, status in self._status.items():
                if status == QueueStatus.QUEUED:
                    return True
            
            return False
            
    def clear_completed(self) -> int:
        """Remove all completed, errored, or cancelled books from tracking.
        
        Returns:
            int: Number of books removed
        """
        with self._lock:
            to_remove = []
            for book_id, status in self._status.items():
                if status in [QueueStatus.COMPLETE, QueueStatus.DONE, QueueStatus.AVAILABLE, QueueStatus.ERROR, QueueStatus.CANCELLED]:
                    to_remove.append(book_id)
            
            removed_count = len(to_remove)
            for book_id in to_remove:
                self._status.pop(book_id, None)
                self._status_timestamps.pop(book_id, None)
                self._book_data.pop(book_id, None)
                self._cancel_flags.pop(book_id, None)
                self._active_downloads.pop(book_id, None)
                
            return removed_count
        
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
                    if status in [QueueStatus.COMPLETE, QueueStatus.DONE, QueueStatus.ERROR, QueueStatus.AVAILABLE, QueueStatus.CANCELLED]:
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