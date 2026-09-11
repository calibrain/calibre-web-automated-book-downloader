from __future__ import annotations

from threading import Event
from unittest.mock import ANY, MagicMock

import pytest

from shelfmark.core.models import DownloadTask
from shelfmark.release_sources import HandoffResult


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


def test_download_task_completes_blackhole_handoff_without_post_processing(monkeypatch, tmp_path):
    import shelfmark.download.orchestrator as orchestrator

    handoff_file = tmp_path / "release.torrent"
    handoff_file.write_bytes(b"torrent-bytes")
    task = DownloadTask(task_id="blackhole-task", source="prowlarr", title="Book")
    queue = MagicMock()
    queue.get_task.return_value = task
    handler = MagicMock()
    handler.download.return_value = HandoffResult(
        path=str(handoff_file),
        message=f"Torrent file saved to {handoff_file}",
    )

    monkeypatch.setattr(orchestrator, "book_queue", queue)
    monkeypatch.setattr(orchestrator, "get_handler", lambda _source: handler)
    monkeypatch.setattr(orchestrator, "_source_unavailable_message", lambda _source: None)
    monkeypatch.setattr(orchestrator, "post_process_download", MagicMock())

    result = orchestrator._download_task(task.task_id, Event())

    assert result == str(handoff_file)
    orchestrator.post_process_download.assert_not_called()
    handler.post_process_cleanup.assert_called_once_with(task, success=True)
