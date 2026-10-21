"""A queue read that passes over the tasks its caller cannot start yet."""

from shelfmark.core.models import DownloadTask, QueueStatus
from shelfmark.core.queue import BookQueue


def _task_from(source: str, task_id: str, priority: int = 0) -> DownloadTask:
    return DownloadTask(task_id=task_id, source=source, title=task_id, priority=priority)


def test_get_next_can_pass_over_a_task_and_leave_it_first_in_line():
    queue = BookQueue()
    assert queue.add(_task_from("slskd", "first-soulseek")) is True
    assert queue.add(_task_from("audiobookbay", "torrent")) is True
    assert queue.add(_task_from("slskd", "second-soulseek")) is True

    picked = queue.get_next(lambda task: task.source != "slskd")

    assert picked is not None
    assert picked[0] == "torrent"
    # Both Soulseek tasks are back, in their original order.
    assert queue.get_next()[0] == "first-soulseek"
    assert queue.get_next()[0] == "second-soulseek"
    assert queue.get_next() is None


def test_get_next_returns_nothing_when_every_task_is_passed_over_and_keeps_them():
    queue = BookQueue()
    assert queue.add(_task_from("slskd", "a")) is True
    assert queue.add(_task_from("slskd", "b")) is True

    assert queue.get_next(lambda _task: False) is None

    assert queue.get_next()[0] == "a"
    assert queue.get_next()[0] == "b"


def test_a_passed_over_task_keeps_its_priority_against_a_later_one():
    queue = BookQueue()
    assert queue.add(_task_from("slskd", "urgent", priority=-10)) is True
    assert queue.add(_task_from("audiobookbay", "normal", priority=0)) is True

    assert queue.get_next(lambda task: task.source != "slskd")[0] == "normal"
    assert queue.get_next()[0] == "urgent"


def test_a_cancelled_task_is_dropped_even_when_a_filter_is_given():
    queue = BookQueue()
    assert queue.add(_task_from("audiobookbay", "gone")) is True
    queue.update_status("gone", QueueStatus.CANCELLED)

    assert queue.get_next(lambda _task: True) is None
