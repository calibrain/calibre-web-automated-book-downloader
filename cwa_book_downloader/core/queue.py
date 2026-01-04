"""Thread-safe download queue manager with priority support and cancellation."""

import queue
import time
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock, Event
from typing import Dict, List, Optional, Tuple, Any

from cwa_book_downloader.core.config import config as app_config
from cwa_book_downloader.core.models import QueueStatus, QueueItem, DownloadTask


class BookQueue:
    """Thread-safe download queue manager with priority support and cancellation."""

    def __init__(self) -> None:
        self._queue: queue.PriorityQueue[QueueItem] = queue.PriorityQueue()
        self._lock = Lock()
        self._status: dict[str, QueueStatus] = {}
        self._task_data: dict[str, DownloadTask] = {}
        self._status_timestamps: dict[str, datetime] = {}  # Track when each status was last updated
        self._cancel_flags: dict[str, Event] = {}  # Cancellation flags for active downloads
        self._active_downloads: dict[str, bool] = {}  # Track currently downloading tasks

    @property
    def _status_timeout(self) -> timedelta:
        """Get status timeout from config (allows live updates)."""
        return timedelta(seconds=app_config.get("STATUS_TIMEOUT", 3600))

    def add(self, task: DownloadTask) -> bool:
        """Add a download task to the queue. Returns False if already exists."""
        with self._lock:
            task_id = task.task_id

            # Don't add if already exists and not in error/done state
            if task_id in self._status and self._status[task_id] not in [QueueStatus.ERROR, QueueStatus.DONE, QueueStatus.CANCELLED]:
                return False

            # Ensure added_time is set
            if task.added_time == 0:
                task.added_time = time.time()

            queue_item = QueueItem(task_id, task.priority, task.added_time)
            self._queue.put(queue_item)
            self._task_data[task_id] = task
            self._update_status(task_id, QueueStatus.QUEUED)
            return True

    def get_next(self) -> Optional[Tuple[str, Event]]:
        """Get next task ID from queue with cancellation flag."""
        # Use iterative approach to avoid stack overflow if many items are cancelled
        while True:
            try:
                queue_item = self._queue.get_nowait()
                task_id = queue_item.book_id  # QueueItem uses book_id as the ID field

                with self._lock:
                    # Check if task was cancelled while in queue
                    if task_id in self._status and self._status[task_id] == QueueStatus.CANCELLED:
                        continue  # Skip cancelled items, try next

                    # Create cancellation flag for this download
                    cancel_flag = Event()
                    self._cancel_flags[task_id] = cancel_flag
                    self._active_downloads[task_id] = True

                return task_id, cancel_flag
            except queue.Empty:
                return None

    def get_task(self, task_id: str) -> Optional[DownloadTask]:
        """Get a task by its ID."""
        with self._lock:
            return self._task_data.get(task_id)

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

    def update_download_path(self, task_id: str, download_path: str) -> None:
        """Update the download path of a task in the queue."""
        with self._lock:
            if task_id in self._task_data:
                self._task_data[task_id].download_path = download_path

    def update_progress(self, task_id: str, progress: float) -> None:
        """Update download progress for a task."""
        with self._lock:
            if task_id in self._task_data:
                self._task_data[task_id].progress = progress

    def update_status_message(self, task_id: str, message: str) -> None:
        """Update detailed status message for a task."""
        with self._lock:
            if task_id in self._task_data:
                self._task_data[task_id].status_message = message

    def get_status(self) -> Dict[QueueStatus, Dict[str, DownloadTask]]:
        """Get current queue status grouped by status."""
        self.refresh()
        with self._lock:
            result: Dict[QueueStatus, Dict[str, DownloadTask]] = {status: {} for status in QueueStatus}
            for task_id, status in self._status.items():
                if task_id in self._task_data:
                    result[status][task_id] = self._task_data[task_id]
            return result

    def get_queue_order(self) -> List[Dict[str, Any]]:
        """Get current queue order for display."""
        with self._lock:
            queue_items = []

            # Get items from priority queue without removing them
            temp_items = []
            while not self._queue.empty():
                try:
                    item = self._queue.get_nowait()
                    temp_items.append(item)
                    task_id = item.book_id  # QueueItem uses book_id as the ID field
                    if task_id in self._task_data:
                        task = self._task_data[task_id]
                        queue_items.append({
                            'id': task_id,
                            'title': task.title,
                            'author': task.author,
                            'priority': item.priority,
                            'added_time': item.added_time,
                            'status': self._status.get(task_id, QueueStatus.QUEUED)
                        })
                except queue.Empty:
                    break

            # Put items back in queue
            for item in temp_items:
                self._queue.put(item)

            return sorted(queue_items, key=lambda x: (x['priority'], x['added_time']))

    def cancel_download(self, task_id: str) -> bool:
        """Cancel a download or clear a completed/errored item."""
        with self._lock:
            current_status = self._status.get(task_id)

            # Allow cancellation during any active state
            if current_status in [QueueStatus.RESOLVING, QueueStatus.DOWNLOADING]:
                # Signal active download to stop
                if task_id in self._cancel_flags:
                    self._cancel_flags[task_id].set()
                self._update_status(task_id, QueueStatus.CANCELLED)
                return True
            elif current_status == QueueStatus.QUEUED:
                # Remove from queue and mark as cancelled
                self._update_status(task_id, QueueStatus.CANCELLED)
                return True
            elif current_status in [QueueStatus.COMPLETE, QueueStatus.DONE, QueueStatus.AVAILABLE, QueueStatus.ERROR, QueueStatus.CANCELLED]:
                # Clear completed/errored/cancelled items from tracking
                self._status.pop(task_id, None)
                self._status_timestamps.pop(task_id, None)
                self._task_data.pop(task_id, None)
                self._cancel_flags.pop(task_id, None)
                self._active_downloads.pop(task_id, None)
                return True

            return False

    def set_priority(self, task_id: str, new_priority: int) -> bool:
        """Change the priority of a queued task (lower = higher priority)."""
        with self._lock:
            if task_id not in self._status or self._status[task_id] != QueueStatus.QUEUED:
                return False

            # Remove task from queue and re-add with new priority
            temp_items = []
            found = False

            while not self._queue.empty():
                try:
                    item = self._queue.get_nowait()
                    if item.book_id == task_id:  # QueueItem uses book_id as the ID field
                        # Create new item with updated priority
                        new_item = QueueItem(task_id, new_priority, item.added_time)
                        temp_items.append(new_item)
                        found = True
                        # Update task data priority
                        if task_id in self._task_data:
                            self._task_data[task_id].priority = new_priority
                    else:
                        temp_items.append(item)
                except queue.Empty:
                    break

            # Put all items back
            for item in temp_items:
                self._queue.put(item)

            return found

    def reorder_queue(self, task_priorities: Dict[str, int]) -> bool:
        """Bulk reorder queue by mapping task_id to new priority."""
        with self._lock:
            # Extract all items from queue
            all_items = []
            while not self._queue.empty():
                try:
                    item = self._queue.get_nowait()
                    task_id = item.book_id  # QueueItem uses book_id as the ID field
                    # Update priority if specified
                    if task_id in task_priorities:
                        new_priority = task_priorities[task_id]
                        item = QueueItem(task_id, new_priority, item.added_time)
                        # Update task data priority
                        if task_id in self._task_data:
                            self._task_data[task_id].priority = new_priority
                    all_items.append(item)
                except queue.Empty:
                    break

            # Put all items back with updated priorities
            for item in all_items:
                self._queue.put(item)

            return True

    def get_active_downloads(self) -> List[str]:
        """Get list of currently active download task IDs."""
        with self._lock:
            return list(self._active_downloads.keys())

    def has_pending_work(self) -> bool:
        """Check if there are any active downloads or queued items."""
        with self._lock:
            if self._active_downloads:
                return True
            return any(status == QueueStatus.QUEUED for status in self._status.values())

    def clear_completed(self) -> int:
        """Remove all completed, errored, or cancelled tasks from tracking."""
        terminal_statuses = {QueueStatus.COMPLETE, QueueStatus.DONE, QueueStatus.AVAILABLE, QueueStatus.ERROR, QueueStatus.CANCELLED}
        with self._lock:
            to_remove = [task_id for task_id, status in self._status.items() if status in terminal_statuses]

            for task_id in to_remove:
                self._status.pop(task_id, None)
                self._status_timestamps.pop(task_id, None)
                self._task_data.pop(task_id, None)
                self._cancel_flags.pop(task_id, None)
                self._active_downloads.pop(task_id, None)

            return len(to_remove)

    def refresh(self) -> None:
        """Remove any tasks that are done downloading or have stale status."""
        terminal_statuses = {QueueStatus.COMPLETE, QueueStatus.DONE, QueueStatus.ERROR, QueueStatus.AVAILABLE, QueueStatus.CANCELLED}
        with self._lock:
            current_time = datetime.now()
            to_remove = []

            for task_id, status in self._status.items():
                task = self._task_data.get(task_id)
                if not task:
                    continue

                # Clear stale download paths
                if task.download_path and not Path(task.download_path).exists():
                    task.download_path = None

                # Mark available downloads as done if file is gone
                if status == QueueStatus.AVAILABLE and not task.download_path:
                    self._update_status(task_id, QueueStatus.DONE)

                # Check for stale status entries
                last_update = self._status_timestamps.get(task_id)
                if last_update and (current_time - last_update) > self._status_timeout:
                    if status in terminal_statuses:
                        to_remove.append(task_id)

            # Remove stale entries
            for task_id in to_remove:
                self._status.pop(task_id, None)
                self._status_timestamps.pop(task_id, None)
                self._task_data.pop(task_id, None)

# Global instance of BookQueue
book_queue = BookQueue()
