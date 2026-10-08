"""Startup reconciliation of downloads the previous process left unfinished.

The download queue lives in memory. A restart mid-download leaves the history row "active" and
the request "queued" with nothing working on either, and nothing ever revisits them.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from shelfmark.core import startup_reconcile
from shelfmark.core.activity_routes import _build_download_status_from_db
from shelfmark.core.download_history_service import DownloadHistoryService
from shelfmark.core.requests_service import sync_delivery_states_from_queue_status
from shelfmark.core.user_db import UserDB

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "users.db")
    user_db = UserDB(path)
    user_db.initialize()
    return user_db, DownloadHistoryService(path), path


def _stamp(hours_ago: float) -> str:
    """A SQLite CURRENT_TIMESTAMP style value, as download_history stores queued_at."""
    return (NOW - timedelta(hours=hours_ago)).strftime("%Y-%m-%d %H:%M:%S")


def _request(user_db, user_id, *, state="queued", status="fulfilled", hours_ago=1.0):
    row = user_db.create_request(
        user_id=user_id,
        content_type="audiobook",
        request_level="book",
        policy_mode="request_book",
        book_data={"title": "T", "author": "A", "provider": "hardcover", "provider_id": "1"},
    )
    return user_db.update_request(
        row["id"],
        status=status,
        delivery_state=state,
        delivery_updated_at=(NOW - timedelta(hours=hours_ago)).isoformat(timespec="seconds"),
        release_data={"source": "audiobookbay", "source_id": "rel-1"},
    )


def _download(
    history,
    path,
    *,
    task_id,
    user_id,
    request_id=None,
    hours_ago=1.0,
    final_status="active",
    retry_payload=None,
):
    history.record_download(
        task_id=task_id,
        user_id=user_id,
        username="reader",
        request_id=request_id,
        source="audiobookbay",
        source_display_name=None,
        title=f"Title {task_id}",
        author=None,
        file_format=None,
        size=None,
        preview=None,
        content_type="audiobook",
        downloads=None,
        origin="requested" if request_id else "direct",
        retry_payload=retry_payload,
    )
    if final_status != "active":
        history.finalize_download(task_id=task_id, final_status=final_status, status_message="x")
    conn = sqlite3.connect(path)
    conn.execute(
        "UPDATE download_history SET queued_at = ? WHERE task_id = ?", (_stamp(hours_ago), task_id)
    )
    conn.commit()
    conn.close()


def _run(user_db, history, **kwargs):
    return startup_reconcile.reconcile_interrupted_downloads(user_db, history, now=NOW, **kwargs)


def _read_activity(user_db, history):
    """Sync request delivery states the way an admin's activity snapshot does."""
    status = _build_download_status_from_db(
        db_rows=history.list_recent(user_id=None), queue_status={}
    )
    sync_delivery_states_from_queue_status(user_db, queue_status=status)


def test_a_recently_interrupted_download_is_closed_out_and_its_request_reopened(db):
    user_db, history, path = db
    user = user_db.create_user(username="reader", role="user")
    request = _request(user_db, user["id"])
    _download(history, path, task_id="t1", user_id=user["id"], request_id=request["id"])

    assert _run(user_db, history) == {"marked": 1, "reopened": 1}

    row = history.get_by_task_id("t1")
    assert row["final_status"] == "error"
    assert row["status_message"] == "Interrupted"
    stored = user_db.get_request(request["id"])
    assert stored["status"] == "pending"
    assert stored["delivery_state"] == "none"
    assert stored["release_data"] is None
    assert "restart" in stored["last_failure_reason"]


def test_an_older_request_linked_orphan_is_left_active_and_keeps_its_retry(db):
    user_db, history, path = db
    user = user_db.create_user(username="reader", role="user")
    request = _request(user_db, user["id"], hours_ago=72)
    _download(
        history,
        path,
        task_id="t1",
        user_id=user["id"],
        request_id=request["id"],
        hours_ago=72,
        retry_payload={"source": "audiobookbay", "source_id": "rel-1"},
    )

    assert _run(user_db, history) == {"marked": 0, "reopened": 0}

    row = history.get_by_task_id("t1")
    assert row["final_status"] == "active"
    assert history.is_retry_available(row) is True

    # Closing the row out as an error would end its retry, and the activity read would then
    # reopen the request regardless of the window.
    _read_activity(user_db, history)
    assert user_db.get_request(request["id"])["status"] == "fulfilled"


def test_a_download_with_no_request_is_closed_out_and_keeps_its_manual_retry(db):
    user_db, history, path = db
    user = user_db.create_user(username="reader", role="user")
    payload = {"source": "direct_download", "source_id": "abc"}
    _download(history, path, task_id="t1", user_id=user["id"], retry_payload=payload)

    assert _run(user_db, history) == {"marked": 1, "reopened": 0}

    row = history.get_by_task_id("t1")
    assert row["final_status"] == "error"
    assert row["retry_payload"] == payload
    assert history.is_retry_available(row) is True


def test_a_download_whose_request_already_completed_is_left_alone(db):
    user_db, history, path = db
    user = user_db.create_user(username="reader", role="user")
    request = _request(user_db, user["id"], state="complete")
    _download(history, path, task_id="t1", user_id=user["id"], request_id=request["id"])

    assert _run(user_db, history) == {"marked": 0, "reopened": 0}

    assert history.get_by_task_id("t1")["final_status"] == "active"
    assert user_db.get_request(request["id"])["delivery_state"] == "complete"


def test_finished_downloads_are_not_touched(db):
    user_db, history, path = db
    user = user_db.create_user(username="reader", role="user")
    for status in ("complete", "cancelled", "error"):
        _download(history, path, task_id=status, user_id=user["id"], final_status=status)

    assert _run(user_db, history) == {"marked": 0, "reopened": 0}

    assert {
        history.get_by_task_id(s)["status_message"] for s in ("complete", "cancelled", "error")
    } == {"x"}


def test_a_recent_request_stuck_queued_with_no_history_is_reopened(db):
    user_db, history, _path = db
    user = user_db.create_user(username="reader", role="user")
    recent = _request(user_db, user["id"], hours_ago=2)
    old = _request(user_db, user["id"], hours_ago=200)

    assert _run(user_db, history) == {"marked": 0, "reopened": 1}

    assert user_db.get_request(recent["id"])["status"] == "pending"
    assert user_db.get_request(old["id"])["status"] == "fulfilled"


def test_a_request_whose_download_finished_is_not_reopened(db):
    """delivery_state only moves while the queue is polled, so it can still say "queued"."""
    user_db, history, path = db
    user = user_db.create_user(username="reader", role="user")
    request = _request(user_db, user["id"], hours_ago=2)
    _download(
        history,
        path,
        task_id="t1",
        user_id=user["id"],
        request_id=request["id"],
        hours_ago=2,
        final_status="complete",
    )

    assert _run(user_db, history) == {"marked": 0, "reopened": 0}

    stored = user_db.get_request(request["id"])
    assert (stored["status"], stored["delivery_state"]) == ("fulfilled", "queued")
    assert stored["release_data"] is not None


def test_a_request_that_is_not_fulfilled_keeps_its_status(db):
    user_db, history, path = db
    user = user_db.create_user(username="reader", role="user")
    request = _request(user_db, user["id"], status="pending", state="none")
    _download(history, path, task_id="t1", user_id=user["id"], request_id=request["id"])

    assert _run(user_db, history) == {"marked": 0, "reopened": 0}

    assert history.get_by_task_id("t1")["final_status"] == "active"
    assert user_db.get_request(request["id"])["status"] == "pending"


def test_running_it_twice_changes_nothing_the_second_time(db):
    user_db, history, path = db
    user = user_db.create_user(username="reader", role="user")
    request = _request(user_db, user["id"])
    _download(history, path, task_id="t1", user_id=user["id"], request_id=request["id"])

    assert _run(user_db, history) == {"marked": 1, "reopened": 1}
    assert _run(user_db, history) == {"marked": 0, "reopened": 0}


def test_the_window_is_configurable(db):
    user_db, history, path = db
    user = user_db.create_user(username="reader", role="user")
    request = _request(user_db, user["id"], hours_ago=30)
    _download(
        history, path, task_id="t1", user_id=user["id"], request_id=request["id"], hours_ago=30
    )

    assert _run(user_db, history, reopen_window=timedelta(hours=48)) == {"marked": 1, "reopened": 1}
