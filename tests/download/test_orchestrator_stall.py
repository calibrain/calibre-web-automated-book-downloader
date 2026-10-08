"""Tests for stall detection and the activity-grace window.

Covers the regression behind issue #1001: a protection bypass is a single long blocking
call that emits no changing status or progress, so it used to be cancelled at exactly
STALL_TIMEOUT even though the bypasser itself was still working.
"""

from unittest.mock import MagicMock

import requests


def _stub_ext_bypasser_timeout(monkeypatch, external_bypasser, value: int) -> None:
    """Override only EXT_BYPASSER_TIMEOUT.

    `external_bypasser.config` is the shared config singleton, so a blanket `get` stub
    also answers unrelated lookups (DNS setup, etc.) with the wrong value.
    """
    real_get = external_bypasser.config.get
    monkeypatch.setattr(
        external_bypasser.config,
        "get",
        lambda key, default="": value if key == "EXT_BYPASSER_TIMEOUT" else real_get(key, default),
    )


def _reset(orchestrator) -> None:
    orchestrator._last_activity.clear()
    orchestrator._last_progress_value.clear()
    orchestrator._last_status_event.clear()
    orchestrator._activity_grace.clear()


def test_find_stalled_tasks_flags_task_past_stall_timeout():
    import shelfmark.download.orchestrator as orchestrator

    _reset(orchestrator)
    orchestrator._last_activity["book"] = 1000.0
    now = 1000.0 + orchestrator.STALL_TIMEOUT + 1

    assert orchestrator._find_stalled_tasks(["book"], now) == ["book"]


def test_find_stalled_tasks_ignores_task_within_stall_timeout():
    import shelfmark.download.orchestrator as orchestrator

    _reset(orchestrator)
    orchestrator._last_activity["book"] = 1000.0
    now = 1000.0 + orchestrator.STALL_TIMEOUT - 1

    assert orchestrator._find_stalled_tasks(["book"], now) == []


def test_find_stalled_tasks_ignores_unknown_task():
    """A task with no recorded activity yet is not considered stalled."""
    import shelfmark.download.orchestrator as orchestrator

    _reset(orchestrator)

    assert orchestrator._find_stalled_tasks(["never-seen"], 99999.0) == []


def test_activity_grace_suppresses_stall_until_its_deadline(monkeypatch):
    """A bypass that legitimately outlives STALL_TIMEOUT is not cancelled."""
    import shelfmark.download.orchestrator as orchestrator

    _reset(orchestrator)
    monkeypatch.setattr(orchestrator.time, "time", lambda: 1000.0)
    orchestrator._last_activity["book"] = 1000.0
    orchestrator.set_activity_grace("book", 400.0)

    # Well past STALL_TIMEOUT, but inside the declared budget.
    assert orchestrator._find_stalled_tasks(["book"], 1390.0) == []


def test_activity_grace_expires_and_task_is_flagged(monkeypatch):
    """A genuinely wedged operation still dies - the grace never extends itself."""
    import shelfmark.download.orchestrator as orchestrator

    _reset(orchestrator)
    monkeypatch.setattr(orchestrator.time, "time", lambda: 1000.0)
    orchestrator._last_activity["book"] = 1000.0
    orchestrator.set_activity_grace("book", 400.0)

    assert orchestrator._find_stalled_tasks(["book"], 1401.0) == ["book"]


def test_set_activity_grace_is_clamped(monkeypatch):
    """A caller cannot buy immortality by asking for an absurd grace."""
    import shelfmark.download.orchestrator as orchestrator

    _reset(orchestrator)
    monkeypatch.setattr(orchestrator.time, "time", lambda: 1000.0)
    orchestrator.set_activity_grace("book", 10_000_000.0)

    assert orchestrator._activity_grace["book"] == 1000.0 + orchestrator._MAX_ACTIVITY_GRACE_SECONDS


def test_max_activity_grace_covers_the_largest_bypass_budget():
    """The clamp must not silently bite the one caller that exists."""
    import shelfmark.download.http as http
    import shelfmark.download.orchestrator as orchestrator
    from shelfmark.bypass import external_bypasser, internal_bypasser

    largest = (
        max(
            external_bypasser.max_duration_seconds(),
            internal_bypasser.max_duration_seconds(),
        )
        + http._BYPASS_GRACE_SLACK_SECONDS
    )

    assert largest <= orchestrator._MAX_ACTIVITY_GRACE_SECONDS


def test_set_activity_grace_touches_neither_queue_nor_websocket(monkeypatch):
    """A liveness hint is not a user-visible status transition."""
    import shelfmark.download.orchestrator as orchestrator

    _reset(orchestrator)
    mock_queue = MagicMock()
    mock_ws = MagicMock()
    monkeypatch.setattr(orchestrator, "book_queue", mock_queue)
    monkeypatch.setattr(orchestrator, "ws_manager", mock_ws)

    orchestrator.set_activity_grace("book", 100.0)
    orchestrator.clear_activity_grace("book")

    assert mock_queue.mock_calls == []
    assert mock_ws.mock_calls == []


def test_clear_activity_grace_refreshes_last_activity(monkeypatch):
    """Releasing a grace restarts the normal window rather than stalling instantly."""
    import shelfmark.download.orchestrator as orchestrator

    _reset(orchestrator)
    orchestrator._last_activity["book"] = 1.0
    monkeypatch.setattr(orchestrator.time, "time", lambda: 5000.0)

    orchestrator.set_activity_grace("book", 100.0)
    orchestrator.clear_activity_grace("book")

    assert "book" not in orchestrator._activity_grace
    assert orchestrator._last_activity["book"] == 5000.0
    assert orchestrator._find_stalled_tasks(["book"], 5000.0) == []


def test_cleanup_progress_tracking_drops_activity_grace(monkeypatch):
    import shelfmark.download.orchestrator as orchestrator

    _reset(orchestrator)
    monkeypatch.setattr(orchestrator, "ws_manager", None)
    orchestrator.set_activity_grace("book", 100.0)

    orchestrator._cleanup_progress_tracking("book")

    assert "book" not in orchestrator._activity_grace


def test_update_download_status_rejects_the_grace_sentinel(monkeypatch):
    """Defence in depth: the sentinel is intercepted upstream, but must be inert here too."""
    import shelfmark.download.orchestrator as orchestrator
    from shelfmark.download.activity import ACTIVITY_GRACE_STATUS

    _reset(orchestrator)
    mock_queue = MagicMock()
    mock_ws = MagicMock()
    monkeypatch.setattr(orchestrator, "book_queue", mock_queue)
    monkeypatch.setattr(orchestrator, "ws_manager", mock_ws)
    monkeypatch.setattr(orchestrator, "queue_status", lambda: {})

    orchestrator.update_download_status("book", ACTIVITY_GRACE_STATUS, "330.0")

    assert mock_queue.mock_calls == []
    assert mock_ws.mock_calls == []
    assert "book" not in orchestrator._last_activity


def test_issue_1001_slow_bypass_is_not_cancelled_at_stall_timeout(monkeypatch):
    """End-to-end replay of the issue #1001 timeline.

    From a reporter's debug log: a 403 switched to the external bypasser at 07:04:33, five
    FlareSolverr attempts each took ~64s and returned HTTP 500, and the watchdog cancelled
    the download at 07:09:33.987 - exactly STALL_TIMEOUT later and 41s before the bypasser
    would have finished and reported the real error.

    Now the bypass declares its own budget, so the watchdog holds off and the user sees the
    actual failure instead of a five-minute silent hang.
    """
    import shelfmark.download.http as http
    import shelfmark.download.orchestrator as orchestrator
    from shelfmark.bypass import external_bypasser
    from shelfmark.download.activity import parse_activity_grace

    _reset(orchestrator)
    clock = [1000.0]
    monkeypatch.setattr(orchestrator.time, "time", lambda: clock[0])

    # Mirror the orchestrator's per-task status_callback closure.
    events: list[tuple[str, str | None]] = []

    def status_callback(status: str, message: str | None = None) -> None:
        grace = parse_activity_grace(status, message)
        if grace is not None:
            if grace > 0:
                orchestrator.set_activity_grace("book", grace)
            else:
                orchestrator.clear_activity_grace("book")
            return
        events.append((status, message))
        orchestrator._last_activity["book"] = clock[0]

    # The reporter was on external FlareSolverr with the default 60s timeout.
    monkeypatch.setattr(http, "_is_cf_bypass_enabled", lambda: True)
    monkeypatch.setattr(http, "_is_using_external_bypasser", lambda: True)
    monkeypatch.setattr(http, "_get_external_bypasser", lambda: external_bypasser)
    _stub_ext_bypasser_timeout(monkeypatch, external_bypasser, 60000)

    stall_checks: list[list[str]] = []

    def slow_failing_bypasser(*_args, **_kwargs):
        # Five attempts at ~64s, then give up - as in the log.
        for _attempt in range(5):
            clock[0] += 64.0
            stall_checks.append(orchestrator._find_stalled_tasks(["book"], clock[0]))
        raise requests.exceptions.RequestException("500 Server Error")

    monkeypatch.setattr(http, "get_bypassed_page", slow_failing_bypasser)

    html = http.html_get_page(
        "https://annas-archive.gl/slow_download/abc/0/6",
        retry=1,
        use_bypasser=True,
        status_callback=status_callback,
    )

    # The bypass ran 320s - past STALL_TIMEOUT - and was never flagged as stalled.
    assert 320.0 > orchestrator.STALL_TIMEOUT
    assert stall_checks[-1] == []
    assert all(check == [] for check in stall_checks)

    # And the real reason reached the user instead of a generic stall message.
    assert html == ""
    assert [status for status, _m in events if status == "error"]
    assert "500 Server Error" in str(events[-1][1])


def test_issue_1001_bypass_overrunning_its_own_budget_is_still_cancelled(monkeypatch):
    """The other half: declaring a budget must not make a wedged bypass immortal."""
    import shelfmark.download.orchestrator as orchestrator

    _reset(orchestrator)
    monkeypatch.setattr(orchestrator.time, "time", lambda: 1000.0)
    orchestrator._last_activity["book"] = 1000.0

    # External bypasser default budget (394s) plus http's 30s slack.
    orchestrator.set_activity_grace("book", 424.0)

    assert orchestrator._find_stalled_tasks(["book"], 1000.0 + 424.0) == []
    assert orchestrator._find_stalled_tasks(["book"], 1000.0 + 425.0) == ["book"]


def test_cancel_stalled_task_does_not_hold_the_progress_lock(monkeypatch):
    """Regression guard: cancelling reaches a sqlite write that must not block the hub.

    `book_queue.cancel_download` runs the terminal-status hooks, which are not
    gevent-patched. Holding `_progress_lock` across that stalls every download worker.
    """
    import shelfmark.download.orchestrator as orchestrator

    _reset(orchestrator)
    observed: list[bool] = []

    def fake_cancel(_task_id: str) -> bool:
        acquired = orchestrator._progress_lock.acquire(blocking=False)
        observed.append(acquired)
        if acquired:
            orchestrator._progress_lock.release()
        return True

    mock_queue = MagicMock()
    mock_queue.cancel_download.side_effect = fake_cancel
    monkeypatch.setattr(orchestrator, "book_queue", mock_queue)

    orchestrator._cancel_stalled_task("book")

    assert observed == [True], "_progress_lock was held while cancelling a stalled task"
    mock_queue.update_status_message.assert_called_once_with(
        "book", f"Download stalled (no activity for {orchestrator.STALL_TIMEOUT}s)"
    )


def test_the_stall_reason_is_set_before_the_cancel_finalises_history(monkeypatch):
    """The terminal hook copies the task's message into the history row, so a stall that
    sets its message after the cancel is recorded with whatever the last poll said ("Queued")
    and cannot be told apart from a user cancelling it."""
    import shelfmark.download.orchestrator as orchestrator

    _reset(orchestrator)
    mock_queue = MagicMock()
    monkeypatch.setattr(orchestrator, "book_queue", mock_queue)

    orchestrator._cancel_stalled_task("book")

    names = [call[0] for call in mock_queue.method_calls]
    assert names.index("update_status_message") < names.index("cancel_download")
