from unittest.mock import MagicMock


def test_update_download_status_dedupes_identical_events(monkeypatch):
    import shelfmark.download.orchestrator as orchestrator

    book_id = "test-book-id"

    # Ensure clean module-level state
    orchestrator._last_activity.clear()
    orchestrator._last_progress_value.clear()
    orchestrator._last_status_event.clear()

    mock_queue = MagicMock()
    monkeypatch.setattr(orchestrator, "book_queue", mock_queue)
    monkeypatch.setattr(orchestrator, "queue_status", lambda: {})

    mock_ws = MagicMock()
    monkeypatch.setattr(orchestrator, "ws_manager", mock_ws)

    times = iter([1.0, 2.0])
    monkeypatch.setattr(orchestrator.time, "time", lambda: next(times))

    orchestrator.update_download_status(book_id, "resolving", "Bypassing protection...")
    orchestrator.update_download_status(book_id, "resolving", "Bypassing protection...")

    # Status + message should only be applied/broadcast once.
    assert mock_queue.update_status.call_count == 1
    assert mock_queue.update_status_message.call_count == 1
    assert mock_ws.broadcast_status_update.call_count == 1

    # Duplicate keep-alives should not refresh stall activity.
    assert orchestrator._last_activity[book_id] == 1.0


def test_update_download_progress_dedupes_identical_progress_for_activity(monkeypatch):
    import shelfmark.download.orchestrator as orchestrator

    book_id = "test-progress-book"

    orchestrator._last_activity.clear()
    orchestrator._last_progress_value.clear()
    orchestrator._last_status_event.clear()

    mock_queue = MagicMock()
    monkeypatch.setattr(orchestrator, "book_queue", mock_queue)
    monkeypatch.setattr(orchestrator, "ws_manager", None)

    times = iter([10.0, 20.0])
    monkeypatch.setattr(orchestrator.time, "time", lambda: next(times))

    orchestrator.update_download_progress(book_id, 0.0)
    orchestrator.update_download_progress(book_id, 0.0)

    assert mock_queue.update_progress.call_count == 2
    assert orchestrator._last_activity[book_id] == 10.0
    assert orchestrator._last_progress_value[book_id] == 0.0


def test_update_download_progress_refreshes_activity_when_progress_changes(monkeypatch):
    import shelfmark.download.orchestrator as orchestrator

    book_id = "test-progress-change"

    orchestrator._last_activity.clear()
    orchestrator._last_progress_value.clear()
    orchestrator._last_status_event.clear()

    mock_queue = MagicMock()
    monkeypatch.setattr(orchestrator, "book_queue", mock_queue)
    monkeypatch.setattr(orchestrator, "ws_manager", None)

    times = iter([30.0, 40.0])
    monkeypatch.setattr(orchestrator.time, "time", lambda: next(times))

    orchestrator.update_download_progress(book_id, 0.0)
    orchestrator.update_download_progress(book_id, 0.5)

    assert orchestrator._last_activity[book_id] == 40.0
    assert orchestrator._last_progress_value[book_id] == 0.5


def _status_pipeline(monkeypatch):
    import shelfmark.download.orchestrator as orchestrator

    orchestrator._last_activity.clear()
    orchestrator._last_progress_value.clear()
    orchestrator._last_status_event.clear()
    mock_queue = MagicMock()
    monkeypatch.setattr(orchestrator, "book_queue", mock_queue)
    monkeypatch.setattr(orchestrator, "queue_status", lambda: {})
    monkeypatch.setattr(orchestrator, "ws_manager", MagicMock())
    clock = {"now": 100.0}
    monkeypatch.setattr(orchestrator.time, "time", lambda: clock["now"])
    return orchestrator, mock_queue, clock


def test_a_changing_seed_note_is_shown_but_does_not_refresh_the_stall_clock(monkeypatch):
    """The swarm count flaps and the give-up countdown ticks; neither is progress (F1)."""
    orchestrator, mock_queue, clock = _status_pipeline(monkeypatch)
    from shelfmark.download.clients.base_handler import ExternalClientHandler

    note = ExternalClientHandler._seed_note

    orchestrator.update_download_status("book", "downloading", f"0%{note(2, None)}")
    assert orchestrator._last_activity["book"] == 100.0

    for step, (seeds, give_up_in) in enumerate([(3, None), (1, None), (0, 600.0), (0, 540.0)], 1):
        clock["now"] = 100.0 + step * 60
        orchestrator.update_download_status("book", "downloading", f"0%{note(seeds, give_up_in)}")

    # The card followed every change, the stall clock did not move.
    assert mock_queue.update_status_message.call_count == 5
    assert orchestrator._last_activity["book"] == 100.0


def test_progress_and_status_changes_still_refresh_the_stall_clock(monkeypatch):
    orchestrator, _queue, clock = _status_pipeline(monkeypatch)
    from shelfmark.download.clients.base_handler import ExternalClientHandler

    note = ExternalClientHandler._seed_note

    orchestrator.update_download_status("book", "downloading", f"0%{note(2, None)}")

    clock["now"] = 200.0
    orchestrator.update_download_status("book", "downloading", f"5%{note(2, None)}")
    assert orchestrator._last_activity["book"] == 200.0

    clock["now"] = 300.0
    orchestrator.update_download_status("book", "resolving", f"5%{note(2, None)}")
    assert orchestrator._last_activity["book"] == 300.0
