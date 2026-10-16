from __future__ import annotations

from pathlib import Path
from threading import Event
from unittest.mock import ANY, MagicMock, call

import pytest

from shelfmark.core.models import DownloadTask


class _StopLoop(BaseException):
    """Sentinel used to stop the infinite coordinator loop during tests."""


class _UnexpectedCall(BaseException):
    """Guard-rail failure that the coordinator's `except Exception` must not swallow."""


class _FakeExecutor:
    def __init__(self, *args, **kwargs) -> None:
        self.args = args
        self.kwargs = kwargs

    def __enter__(self) -> _FakeExecutor:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def submit(self, *args, **kwargs):  # pragma: no cover - not expected in these tests
        raise _UnexpectedCall("submit() should not be called in this test")


class _StopCoordinator(BaseException):
    """Sentinel used to stop a real coordinator thread cleanly in tests."""


def test_concurrent_download_loop_logs_and_recovers_after_loop_error(monkeypatch):
    import shelfmark.download.orchestrator as orchestrator

    call_count = 0

    def fake_get_next():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("boom")
        return None

    sleep_delays: list[float] = []

    def fake_sleep(delay: float) -> None:
        sleep_delays.append(delay)
        if len(sleep_delays) >= 2:
            raise _StopLoop()

    mock_queue = MagicMock()
    mock_queue.get_next.side_effect = fake_get_next

    error_trace = MagicMock()

    monkeypatch.setattr(orchestrator, "book_queue", mock_queue)
    monkeypatch.setattr(orchestrator, "ThreadPoolExecutor", _FakeExecutor)
    monkeypatch.setattr(orchestrator.time, "sleep", fake_sleep)
    monkeypatch.setattr(orchestrator.logger, "error_trace", error_trace)

    with pytest.raises(_StopLoop):
        orchestrator.concurrent_download_loop()

    assert mock_queue.get_next.call_count == 2
    error_trace.assert_called_once_with("Download coordinator loop error: %s", ANY)
    assert sleep_delays == [
        orchestrator.COORDINATOR_LOOP_ERROR_RETRY_DELAY,
        orchestrator.config.MAIN_LOOP_SLEEP_TIME,
    ]


def test_concurrent_download_loop_survives_exceptions_outside_the_legacy_list(monkeypatch):
    """Regression for #823/#1166: the coordinator must not die on an unlisted exception.

    `gevent.exceptions.LoopExit` and `StopIteration` are Exceptions that the old narrow
    except tuple let through, silently killing the only thread that drives the queue.
    """
    import shelfmark.download.orchestrator as orchestrator

    raised: list[type[BaseException]] = []
    escapes = [StopIteration, ZeroDivisionError, KeyboardInterrupt]

    def fake_get_next():
        if escapes:
            exc = escapes.pop(0)
            if exc is KeyboardInterrupt:
                # BaseException: must still propagate and stop the loop.
                raise KeyboardInterrupt
            raised.append(exc)
            raise exc("boom")
        return None

    mock_queue = MagicMock()
    mock_queue.get_next.side_effect = fake_get_next

    monkeypatch.setattr(orchestrator, "book_queue", mock_queue)
    monkeypatch.setattr(orchestrator, "ThreadPoolExecutor", _FakeExecutor)
    monkeypatch.setattr(orchestrator.time, "sleep", lambda _delay: None)
    monkeypatch.setattr(orchestrator.logger, "error_trace", MagicMock())

    with pytest.raises(KeyboardInterrupt):
        orchestrator.concurrent_download_loop()

    assert raised == [StopIteration, ZeroDivisionError]


def test_concurrent_download_loop_backs_off_on_repeated_errors(monkeypatch):
    """A persistent failure must not spin the loop at 1Hz forever."""
    import shelfmark.download.orchestrator as orchestrator

    sleep_delays: list[float] = []

    def fake_sleep(delay: float) -> None:
        sleep_delays.append(delay)
        if len(sleep_delays) >= 4:
            raise _StopLoop()

    mock_queue = MagicMock()
    mock_queue.get_next.side_effect = RuntimeError("persistent boom")

    monkeypatch.setattr(orchestrator, "book_queue", mock_queue)
    monkeypatch.setattr(orchestrator, "ThreadPoolExecutor", _FakeExecutor)
    monkeypatch.setattr(orchestrator.time, "sleep", fake_sleep)
    monkeypatch.setattr(orchestrator.logger, "error_trace", MagicMock())

    with pytest.raises(_StopLoop):
        orchestrator.concurrent_download_loop()

    base = orchestrator.COORDINATOR_LOOP_ERROR_RETRY_DELAY
    # First delay stays unchanged for a normal transient blip, then doubles.
    assert sleep_delays == [base, base * 2, base * 4, base * 8]
    assert max(sleep_delays) <= orchestrator._COORDINATOR_LOOP_ERROR_MAX_DELAY


def test_concurrent_download_loop_recovers_and_processes_task_after_transient_loop_error(
    monkeypatch,
):
    import threading

    import shelfmark.download.orchestrator as orchestrator

    processed = threading.Event()

    class FlakyQueue:
        def __init__(self) -> None:
            self.calls = 0

        def get_next(self):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("boom")
            if self.calls == 2:
                return ("task-1", threading.Event())
            if processed.is_set():
                raise _StopCoordinator()
            return None

        # These raise _UnexpectedCall rather than AssertionError because the coordinator
        # loop catches Exception: an AssertionError here would be swallowed and logged,
        # letting the test pass vacuously instead of reporting the unexpected call.
        def cancel_download(self, task_id: str) -> None:  # pragma: no cover - unused
            raise _UnexpectedCall(f"cancel_download unexpectedly called for {task_id}")

        def update_status_message(
            self, task_id: str, message: str
        ) -> None:  # pragma: no cover - unused
            raise _UnexpectedCall(
                f"update_status_message unexpectedly called for {task_id}: {message}"
            )

    queue = FlakyQueue()
    error_trace = MagicMock()

    monkeypatch.setattr(orchestrator, "book_queue", queue)
    monkeypatch.setattr(
        orchestrator,
        "_process_single_download",
        lambda task_id, cancel_flag: processed.set(),
    )
    monkeypatch.setattr(orchestrator.logger, "error_trace", error_trace)
    monkeypatch.setattr(orchestrator, "COORDINATOR_LOOP_ERROR_RETRY_DELAY", 0.01)
    monkeypatch.setattr(orchestrator.config, "MAX_CONCURRENT_DOWNLOADS", 1, raising=False)
    monkeypatch.setattr(orchestrator.config, "MAIN_LOOP_SLEEP_TIME", 0.01, raising=False)

    def run_loop() -> None:
        try:
            orchestrator.concurrent_download_loop()
        except _StopCoordinator:
            pass

    thread = threading.Thread(target=run_loop, daemon=True, name="TestDownloadCoordinator")
    thread.start()

    assert processed.wait(timeout=1.0) is True
    thread.join(timeout=1.0)

    assert thread.is_alive() is False
    assert queue.calls >= 3
    error_trace.assert_called_once_with("Download coordinator loop error: %s", ANY)


def test_start_replaces_dead_coordinator_thread(monkeypatch):
    import shelfmark.download.orchestrator as orchestrator

    dead_thread = MagicMock()
    dead_thread.is_alive.return_value = False

    new_thread = MagicMock()
    new_thread.is_alive.return_value = True

    thread_factory = MagicMock(return_value=new_thread)

    monkeypatch.setattr(orchestrator, "_coordinator_thread", dead_thread)
    monkeypatch.setattr(orchestrator.threading, "Thread", thread_factory)

    orchestrator.start()

    thread_factory.assert_called_once_with(
        target=orchestrator.concurrent_download_loop,
        daemon=True,
        name="DownloadCoordinator",
    )
    new_thread.start.assert_called_once_with()
    assert orchestrator._coordinator_thread is new_thread


@pytest.mark.parametrize("handoff", ["resident", "consumed", "write_error", "cancelled"])
def test_download_task_completes_blackhole_handoff_without_post_processing(
    monkeypatch, tmp_path, handoff
):
    import shelfmark.download.orchestrator as orchestrator
    from shelfmark.download.clients import blackhole
    from shelfmark.download.clients.base_handler import DownloadRequest
    from shelfmark.download.clients.torrent_utils import TorrentInfo
    from shelfmark.release_sources.prowlarr.handler import ProwlarrHandler

    handoff_file = tmp_path / "Book.torrent"
    cancel = Event()
    task = DownloadTask(task_id="blackhole-task", source="prowlarr", title="Book")
    queue = MagicMock()
    queue.get_task.return_value = task
    monkeypatch.setattr(
        blackhole.config,
        "get",
        lambda key, default=None: str(tmp_path) if key == "BLACKHOLE_DIRECTORY" else default,
    )
    monkeypatch.setattr(
        blackhole,
        "extract_torrent_info",
        lambda *_args, **_kwargs: TorrentInfo("abc123", b"torrent-bytes", False),
    )
    client = blackhole.BlackholeClient()
    handler = ProwlarrHandler()
    monkeypatch.setattr(handler, "_get_client", lambda _protocol: client)
    monkeypatch.setattr(
        handler,
        "_resolve_download",
        lambda *_args: DownloadRequest(
            "https://indexer.example/book.torrent", "torrent", "Book", None
        ),
    )
    replace = Path.replace
    consumed = []

    def publish(path, target):
        if handoff == "write_error":
            raise OSError("handoff directory is not writable")
        result = replace(path, target)
        if handoff == "consumed":
            consumed.append(target.read_bytes())
            target.unlink()
        elif handoff == "cancelled":
            cancel.set()
        return result

    monkeypatch.setattr(Path, "replace", publish)
    monkeypatch.setattr(orchestrator, "book_queue", queue)
    monkeypatch.setattr(orchestrator, "get_handler", lambda _source: handler)
    monkeypatch.setattr(orchestrator, "_source_unavailable_message", lambda _source: None)
    monkeypatch.setattr(orchestrator, "post_process_download", MagicMock())

    result = orchestrator._download_task(task.task_id, cancel)

    assert result == (None if handoff in ("write_error", "cancelled") else str(handoff_file))
    assert handoff_file.exists() is (handoff in ("resident", "cancelled"))
    assert consumed == ([b"torrent-bytes"] if handoff == "consumed" else [])
    assert (task.last_error_message is not None) is (handoff == "write_error")
    assert not list(tmp_path.glob(".blackhole-*"))
    orchestrator.post_process_download.assert_not_called()
    if result:
        queue.update_progress.assert_called_once_with(task.task_id, 100)
    else:
        queue.update_progress.assert_not_called()
    if handoff == "cancelled":
        # The publication is already out of our hands, so a bare "Cancelled" would
        # misdescribe what the watcher is about to do with it.
        assert (
            call(task.task_id, "Cancelled, but the torrent was already handed off to blackhole")
            in queue.update_status_message.call_args_list
        )


class _PoolRecorder:
    """Stands in for the thread pool: remembers what was started and never finishes it."""

    def __init__(self, *args, **kwargs) -> None:
        self.max_workers = kwargs.get("max_workers")
        _PoolRecorder.last = self
        self.started: list[str] = []

    def __enter__(self) -> _PoolRecorder:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def submit(self, fn, task_id, cancel_flag):
        from concurrent.futures import Future

        self.started.append(task_id)
        return Future()  # never done, so the download stays running


def _run_one_pass(monkeypatch, tasks, *, per_source, global_limit=3):
    import shelfmark.download.orchestrator as orchestrator
    from shelfmark.core.queue import BookQueue

    queue = BookQueue()
    for task in tasks:
        assert queue.add(task) is True
    values = {
        "MAX_CONCURRENT_DOWNLOADS_PER_SOURCE": per_source,
        "MAX_CONCURRENT_DOWNLOADS": global_limit,
        "MAIN_LOOP_SLEEP_TIME": 0.0,
    }
    monkeypatch.setattr(orchestrator, "book_queue", queue)
    monkeypatch.setattr(orchestrator, "ThreadPoolExecutor", _PoolRecorder)

    def sleep(delay):
        if delay == 0.0:  # the loop's own pause ends the pass; a stagger delay is just skipped
            raise _StopLoop

    monkeypatch.setattr(orchestrator.time, "sleep", sleep)
    monkeypatch.setattr(orchestrator.config, "get", lambda k, d=None: values.get(k, d))
    monkeypatch.setattr(
        orchestrator.config, "MAX_CONCURRENT_DOWNLOADS", global_limit, raising=False
    )
    monkeypatch.setattr(orchestrator.config, "MAIN_LOOP_SLEEP_TIME", 0.0, raising=False)
    with pytest.raises(_StopLoop):
        orchestrator.concurrent_download_loop()
    return _PoolRecorder.last


def _t(source, task_id):
    return DownloadTask(task_id=task_id, source=source, title=task_id)


def test_a_source_at_its_limit_does_not_hold_back_the_others(monkeypatch):
    pool = _run_one_pass(
        monkeypatch,
        [_t("slskd", "s1"), _t("slskd", "s2"), _t("slskd", "s3"), _t("audiobookbay", "torrent")],
        per_source=1,
    )

    assert sorted(pool.started) == ["s1", "torrent"]


def test_each_source_gets_its_own_limit(monkeypatch):
    pool = _run_one_pass(
        monkeypatch,
        [_t("slskd", "s1"), _t("slskd", "s2"), _t("audiobookbay", "a1"), _t("audiobookbay", "a2")],
        per_source=2,
    )

    assert sorted(pool.started) == ["a1", "a2", "s1", "s2"]


def test_with_no_per_source_limit_the_one_global_limit_applies(monkeypatch):
    pool = _run_one_pass(
        monkeypatch,
        [_t("slskd", "s1"), _t("slskd", "s2"), _t("audiobookbay", "a1"), _t("audiobookbay", "a2")],
        per_source=0,
        global_limit=3,
    )

    assert len(pool.started) == 3


def test_the_pool_is_big_enough_for_every_source_at_its_limit(monkeypatch):
    import shelfmark.download.orchestrator as orchestrator

    pool = _run_one_pass(monkeypatch, [], per_source=2)

    assert pool.max_workers == 2 * orchestrator._MAX_SOURCES


def test_a_source_has_room_only_below_its_limit():
    import shelfmark.download.orchestrator as orchestrator

    task = _t("slskd", "x")

    assert orchestrator._source_has_room(task, running={"slskd": 1}, limit=2) is True
    assert orchestrator._source_has_room(task, running={"slskd": 2}, limit=2) is False
    assert orchestrator._source_has_room(task, running={"audiobookbay": 5}, limit=2) is True


def test_a_task_waiting_in_its_clients_queue_does_not_use_up_its_source(monkeypatch):
    waiting = DownloadTask(task_id="waiting", source="audiobookbay", title="w", client_queued=True)
    pool = _run_one_pass(
        monkeypatch,
        [waiting, _t("audiobookbay", "next"), _t("audiobookbay", "third")],
        per_source=1,
    )

    # "waiting" is first and takes the one slot; being queued in qBittorrent it gives it back,
    # so "next" starts; "next" is working, so "third" has to wait.
    assert pool.started == ["waiting", "next"]
