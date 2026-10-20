"""Thread-safe download queue manager with priority support and cancellation."""

import queue
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event, Lock
from typing import TYPE_CHECKING, Any

from shelfmark.core.config import config as app_config
from shelfmark.core.logger import setup_logger
from shelfmark.core.models import (
    TERMINAL_QUEUE_STATUSES,
    DownloadTask,
    QueueItem,
    QueueStatus,
)

if TYPE_CHECKING:
    from collections.abc import Callable

logger = setup_logger(__name__)
_QUEUE_HOOK_ERRORS = (OSError, RuntimeError, TypeError, ValueError)


def _coerce_status_timeout_seconds(value: object, *, default: int) -> int:
    """Normalize STATUS_TIMEOUT into a usable positive integer."""
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value if value > 0 else default
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isdigit():
            parsed = int(stripped)
            return parsed if parsed > 0 else default
    return default


class BookQueue:
    """Thread-safe download queue manager with priority support and cancellation."""

    def __init__(self) -> None:
        """Initialize queue state, locks, and lifecycle hooks."""
        self._queue: queue.PriorityQueue[QueueItem] = queue.PriorityQueue()
        self._lock = Lock()
        self._status: dict[str, QueueStatus] = {}
        self._task_data: dict[str, DownloadTask] = {}
        self._status_timestamps: dict[str, datetime] = {}  # Track when each status was last updated
        self._cancel_flags: dict[str, Event] = {}  # Cancellation flags for active downloads
        self._active_downloads: dict[str, bool] = {}  # Track currently downloading tasks
        self._terminal_status_hook: Callable[[str, QueueStatus, DownloadTask], None] | None = None
        self._queue_hook: Callable[[str, DownloadTask], None] | None = None

    @property
    def _status_timeout(self) -> timedelta:
        """Get status timeout from config (allows live updates)."""
        return timedelta(
            seconds=_coerce_status_timeout_seconds(
                app_config.get("STATUS_TIMEOUT", 3600),
                default=3600,
            )
        )

    def add(self, task: DownloadTask) -> bool:
        """Add a download task to the queue. Returns False if already exists."""
        hook: Callable[[str, DownloadTask], None] | None = None
        with self._lock:
            task_id = task.task_id

            # Don't add if already exists and not in error/cancelled state
            if task_id in self._status and self._status[task_id] not in [
                QueueStatus.ERROR,
                QueueStatus.CANCELLED,
            ]:
                return False

            # Ensure added_time is set
            if task.added_time == 0:
                task.added_time = time.time()

            queue_item = QueueItem(task_id, task.priority, task.added_time)
            self._queue.put(queue_item)
            self._task_data[task_id] = task
            self._update_status(task_id, QueueStatus.QUEUED)
            hook = self._queue_hook

        if hook is not None:
            try:
                hook(task_id, task)
            except _QUEUE_HOOK_ERRORS as exc:
                logger.warning("Queue hook failed while adding task %s: %s", task_id, exc)
        return True

    def get_next(self) -> tuple[str, Event] | None:
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
            except queue.Empty:
                return None
            else:
                return task_id, cancel_flag

    def get_task(self, task_id: str) -> DownloadTask | None:
        """Get a task by its ID."""
        with self._lock:
            return self._task_data.get(task_id)

    def get_task_status(self, task_id: str) -> QueueStatus | None:
        """Get queue status for a task id."""
        with self._lock:
            return self._status.get(task_id)

    def _update_status(self, book_id: str, status: QueueStatus) -> None:
        """Update the status and timestamp for a task."""
        self._status[book_id] = status
        self._status_timestamps[book_id] = datetime.now(UTC)

    def set_terminal_status_hook(
        self,
        hook: Callable[[str, QueueStatus, DownloadTask], None] | None,
    ) -> None:
        """Register a callback invoked when a task first enters a terminal status."""
        with self._lock:
            self._terminal_status_hook = hook

    def set_queue_hook(
        self,
        hook: Callable[[str, DownloadTask], None] | None,
    ) -> None:
        """Register a callback invoked when a task is added to the queue."""
        with self._lock:
            self._queue_hook = hook

    def _apply_status_locked(
        self, book_id: str, status: QueueStatus
    ) -> tuple[Callable[[str, QueueStatus, DownloadTask], None] | None, DownloadTask | None]:
        """Apply a status change; returns the terminal hook to run after releasing the lock."""
        hook: Callable[[str, QueueStatus, DownloadTask], None] | None = None
        hook_task: DownloadTask | None = None
        previous_status = self._status.get(book_id)
        if (
            previous_status in TERMINAL_QUEUE_STATUSES
            and status in TERMINAL_QUEUE_STATUSES
            and previous_status != status
        ):
            # The first terminal status wins. A worker that checked its cancel flag just before
            # a cancel landed would otherwise overwrite CANCELLED with COMPLETE and fire the
            # terminal hook a second time.
            return None, None
        self._update_status(book_id, status)

        if (
            status in TERMINAL_QUEUE_STATUSES
            and previous_status != status
            and self._terminal_status_hook is not None
        ):
            current_task = self._task_data.get(book_id)
            if current_task is not None:
                hook = self._terminal_status_hook
                hook_task = current_task

        # Clean up active download tracking when finished
        if status in TERMINAL_QUEUE_STATUSES:
            self._active_downloads.pop(book_id, None)
            self._cancel_flags.pop(book_id, None)

        return hook, hook_task

    def update_status(self, book_id: str, status: QueueStatus) -> None:
        """Update status of a book in the queue."""
        with self._lock:
            hook, hook_task = self._apply_status_locked(book_id, status)

        if hook is not None and hook_task is not None:
            hook(book_id, status, hook_task)

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

    def get_status(self, user_id: int | None = None) -> dict[QueueStatus, dict[str, DownloadTask]]:
        """Get current queue status grouped by status.

        Args:
            user_id: If provided, only return tasks belonging to this user.
                     If None, return all.

        """
        self.refresh()
        with self._lock:
            result: dict[QueueStatus, dict[str, DownloadTask]] = {
                status: {} for status in QueueStatus
            }
            for task_id, status in self._status.items():
                if task_id in self._task_data:
                    task = self._task_data[task_id]
                    if user_id is not None and task.user_id != user_id:
                        continue
                    result[status][task_id] = task
            return result

    def get_queue_order(self) -> list[dict[str, Any]]:
        """Get current queue order for display."""
        with self._lock:
            queue_items = []

            # Get items from priority queue without removing them
            temp_items = []
            while not self._queue.empty():
                item = self._queue.get_nowait()
                temp_items.append(item)
                task_id = item.book_id  # QueueItem uses book_id as the ID field
                if task_id in self._task_data:
                    task = self._task_data[task_id]
                    queue_items.append(
                        {
                            "id": task_id,
                            "title": task.title,
                            "author": task.author,
                            "priority": item.priority,
                            "added_time": item.added_time,
                            "status": self._status.get(task_id, QueueStatus.QUEUED),
                        }
                    )

            # Put items back in queue
            for item in temp_items:
                self._queue.put(item)

            return sorted(queue_items, key=lambda x: (x["priority"], x["added_time"]))

    def cancel_download(self, task_id: str) -> bool:
        """Cancel an active or queued download."""
        with self._lock:
            current_status = self._status.get(task_id)

            if current_status in [
                QueueStatus.RESOLVING,
                QueueStatus.LOCATING,
                QueueStatus.DOWNLOADING,
            ]:
                # Signal active download to stop
                if task_id in self._cancel_flags:
                    self._cancel_flags[task_id].set()
            elif current_status != QueueStatus.QUEUED:
                # Not in a cancellable state
                return False

            # Write under the same lock so a download that finishes first is not overwritten
            hook, hook_task = self._apply_status_locked(task_id, QueueStatus.CANCELLED)

        if hook is not None and hook_task is not None:
            hook(task_id, QueueStatus.CANCELLED, hook_task)
        return True

    def set_priority(self, task_id: str, new_priority: int) -> bool:
        """Change the priority of a queued task (lower = higher priority)."""
        with self._lock:
            if task_id not in self._status or self._status[task_id] != QueueStatus.QUEUED:
                return False

            # Remove task from queue and re-add with new priority
            temp_items = []
            found = False

            while not self._queue.empty():
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

            # Put all items back
            for item in temp_items:
                self._queue.put(item)

            return found

    def enqueue_existing(self, task_id: str, *, priority: int | None = None) -> bool:
        """Requeue an existing task regardless of current status.

        This is used for retries where task metadata should be preserved.
        """
        hook: Callable[[str, DownloadTask], None] | None = None
        hook_task: DownloadTask | None = None
        with self._lock:
            task = self._task_data.get(task_id)
            if task is None:
                return False

            if priority is not None:
                task.priority = priority

            # Ensure task doesn't appear active while waiting for retry.
            self._active_downloads.pop(task_id, None)
            self._cancel_flags.pop(task_id, None)

            # De-duplicate queue entries for this task id.
            temp_items: list[QueueItem] = []
            while not self._queue.empty():
                item = self._queue.get_nowait()
                if item.book_id != task_id:
                    temp_items.append(item)

            for item in temp_items:
                self._queue.put(item)

            queue_item = QueueItem(task_id, task.priority, time.time())
            self._queue.put(queue_item)
            self._update_status(task_id, QueueStatus.QUEUED)
            hook = self._queue_hook
            hook_task = task

        if hook is not None and hook_task is not None:
            try:
                hook(task_id, hook_task)
            except _QUEUE_HOOK_ERRORS as exc:
                logger.warning("Queue hook failed while requeueing task %s: %s", task_id, exc)
        return True

    def reorder_queue(self, task_priorities: dict[str, int]) -> bool:
        """Bulk reorder queue by mapping task_id to new priority."""
        with self._lock:
            # Extract all items from queue
            all_items = []
            while not self._queue.empty():
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

            # Put all items back with updated priorities
            for item in all_items:
                self._queue.put(item)

            return True

    def get_active_downloads(self) -> list[str]:
        """Get list of currently active download task IDs."""
        with self._lock:
            return list(self._active_downloads.keys())

    def has_pending_work(self) -> bool:
        """Check if there are any active downloads or queued items."""
        with self._lock:
            if self._active_downloads:
                return True
            return any(status == QueueStatus.QUEUED for status in self._status.values())

    def refresh(self) -> None:
        """Remove any tasks that are done downloading or have stale status."""
        terminal_statuses = TERMINAL_QUEUE_STATUSES
        with self._lock:
            current_time = datetime.now(UTC)
            to_remove = []

            for task_id, status in self._status.items():
                task = self._task_data.get(task_id)
                if not task:
                    continue

                # Clear stale download paths
                if task.download_path and not Path(task.download_path).exists():
                    task.download_path = None

                # Check for stale status entries
                last_update = self._status_timestamps.get(task_id)
                if (
                    last_update
                    and (current_time - last_update) > self._status_timeout
                    and status in terminal_statuses
                ):
                    to_remove.append(task_id)

            # Remove stale entries
            for task_id in to_remove:
                self._status.pop(task_id, None)
                self._status_timestamps.pop(task_id, None)
                self._task_data.pop(task_id, None)


# Global instance of BookQueue
book_queue = BookQueue()
