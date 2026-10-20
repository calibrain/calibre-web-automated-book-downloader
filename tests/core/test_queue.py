"""Tests for queue hook failure handling and cancellation."""

from unittest.mock import patch

from shelfmark.core.models import DownloadTask, QueueStatus
from shelfmark.core.queue import BookQueue


def _make_task(task_id: str = "task-1") -> DownloadTask:
    return DownloadTask(
        task_id=task_id,
        source="direct_download",
        title="Example Title",
        user_id=1,
        username="alice",
    )


def test_add_logs_queue_hook_failures():
    queue = BookQueue()

    def broken_hook(task_id: str, task: DownloadTask) -> None:
        raise RuntimeError("boom")

    queue.set_queue_hook(broken_hook)

    with patch("shelfmark.core.queue.logger.warning") as mock_warning:
        assert queue.add(_make_task()) is True

    mock_warning.assert_called_once()
    args = mock_warning.call_args.args
    assert args[0] == "Queue hook failed while adding task %s: %s"
    assert args[1] == "task-1"
    assert str(args[2]) == "boom"


def test_enqueue_existing_logs_queue_hook_failures():
    queue = BookQueue()
    assert queue.add(_make_task("task-2")) is True

    def broken_hook(task_id: str, task: DownloadTask) -> None:
        raise RuntimeError("boom")

    queue.set_queue_hook(broken_hook)

    with patch("shelfmark.core.queue.logger.warning") as mock_warning:
        assert queue.enqueue_existing("task-2") is True

    mock_warning.assert_called_once()
    args = mock_warning.call_args.args
    assert args[0] == "Queue hook failed while requeueing task %s: %s"
    assert args[1] == "task-2"
    assert str(args[2]) == "boom"


def _downloading_task(queue: BookQueue, task_id: str) -> list[QueueStatus]:
    """Put a task in DOWNLOADING and return the list its terminal hook appends to."""
    assert queue.add(_make_task(task_id)) is True
    assert queue.get_next() is not None
    queue.update_status(task_id, QueueStatus.DOWNLOADING)

    terminal_events: list[QueueStatus] = []
    queue.set_terminal_status_hook(lambda _task_id, status, _task: terminal_events.append(status))
    return terminal_events


def test_cancel_does_not_overwrite_a_download_that_finished_first():
    """A cancel that arrives after the download completed is refused and changes nothing."""
    queue = BookQueue()
    terminal_events = _downloading_task(queue, "race-task")

    queue.update_status("race-task", QueueStatus.COMPLETE)

    assert queue.cancel_download("race-task") is False
    assert queue.get_task_status("race-task") == QueueStatus.COMPLETE
    assert terminal_events == [QueueStatus.COMPLETE]


def test_a_late_complete_does_not_overwrite_a_cancel():
    """The worker checks its cancel flag and then writes COMPLETE; a cancel in between wins."""
    queue = BookQueue()
    terminal_events = _downloading_task(queue, "race-task")

    assert queue.cancel_download("race-task") is True
    queue.update_status("race-task", QueueStatus.COMPLETE)

    assert queue.get_task_status("race-task") == QueueStatus.CANCELLED
    assert terminal_events == [QueueStatus.CANCELLED]


def test_a_task_that_ended_in_error_is_not_completed_afterwards():
    queue = BookQueue()
    terminal_events = _downloading_task(queue, "race-task")

    queue.update_status("race-task", QueueStatus.ERROR)
    queue.update_status("race-task", QueueStatus.COMPLETE)

    assert queue.get_task_status("race-task") == QueueStatus.ERROR
    assert terminal_events == [QueueStatus.ERROR]


def test_a_terminal_task_can_still_be_requeued():
    queue = BookQueue()
    _downloading_task(queue, "race-task")
    queue.update_status("race-task", QueueStatus.ERROR)

    assert queue.enqueue_existing("race-task") is True
    assert queue.get_task_status("race-task") == QueueStatus.QUEUED
