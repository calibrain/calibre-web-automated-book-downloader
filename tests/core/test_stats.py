"""Tests for the dashboard counters behind /api/stats."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from datetime import UTC, datetime, timedelta

import pytest

from shelfmark.core import stats
from shelfmark.core.download_history_service import DownloadHistoryService
from shelfmark.core.user_db import UserDB

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


@pytest.fixture
def env():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "users.db")
        db = UserDB(path)
        db.initialize()
        user = db.create_user(username="reader", role="user")
        yield db, DownloadHistoryService(path), user, path


def _history(env, task, content_type, status, *, age_days=0.0, message=None):
    _db, history, user, path = env
    history.record_download(
        task_id=task,
        user_id=user["id"],
        username="reader",
        request_id=None,
        source="direct_download",
        source_display_name="x",
        title=task,
        author="a",
        file_format="epub",
        size=None,
        preview=None,
        content_type=content_type,
        downloads=None,
        origin="requested",
    )
    if status != "active":
        history.finalize_download(task_id=task, final_status=status, status_message=message)
    stamp = (NOW - timedelta(days=age_days)).isoformat()
    conn = sqlite3.connect(path)
    conn.execute("UPDATE download_history SET terminal_at = ? WHERE task_id = ?", (stamp, task))
    conn.commit()
    conn.close()


def _request(env, content_type="ebook", *, status="pending", delivery="none"):
    db, _history_svc, user, _path = env
    row = db.create_request(
        user_id=user["id"],
        content_type=content_type,
        request_level="book",
        policy_mode="request_book",
        book_data={"title": "t", "provider": "hardcover", "provider_id": str(id(object()))},
    )
    conn = sqlite3.connect(env[3])
    conn.execute(
        "UPDATE download_requests SET status = ?, delivery_state = ? WHERE id = ?",
        (status, delivery, row["id"]),
    )
    conn.commit()
    conn.close()


def test_an_empty_database_reports_zeroes(env):
    result = stats.collect(env[3], now=NOW)

    assert result["added"] == {
        "ebook_7d": 0,
        "audiobook_7d": 0,
        "total_7d": 0,
        "ebook_30d": 0,
        "audiobook_30d": 0,
        "total_30d": 0,
    }
    assert result["queue"] == {"active": 0, "awaiting_pickup": 0, "queued": 0}
    assert result["requests"] == {
        "pending": 0,
        "delivered": 0,
        "failed": 0,
        "cancelled": 0,
        "rejected": 0,
    }
    assert result["errors"] == {"failed_7d": 0}
    assert result["generated_at"] == NOW.isoformat()


def test_added_counts_completed_downloads_by_format_and_window(env):
    _history(env, "e1", "ebook", "complete", age_days=1)
    _history(env, "e2", "ebook", "complete", age_days=6.9)
    _history(env, "e3", "ebook", "complete", age_days=20)
    _history(env, "a1", "audiobook", "complete", age_days=2)
    _history(env, "a2", "audiobook", "complete", age_days=29)
    _history(env, "old", "audiobook", "complete", age_days=31)

    added = stats.collect(env[3], now=NOW)["added"]

    assert added["ebook_7d"] == 2
    assert added["audiobook_7d"] == 1
    assert added["total_7d"] == 3
    assert added["ebook_30d"] == 3
    assert added["audiobook_30d"] == 2
    assert added["total_30d"] == 5


def test_added_ignores_unfinished_and_failed_downloads(env):
    _history(env, "x1", "ebook", "error", age_days=1, message="boom")
    _history(env, "x2", "ebook", "cancelled", age_days=1)
    _history(env, "x3", "ebook", "active")

    assert stats.collect(env[3], now=NOW)["added"]["total_7d"] == 0


def test_an_unknown_content_type_counts_in_the_total_only(env):
    _history(env, "c1", "comic", "complete", age_days=1)

    added = stats.collect(env[3], now=NOW)["added"]

    assert (added["ebook_7d"], added["audiobook_7d"], added["total_7d"]) == (0, 0, 1)


def test_active_downloads_are_the_queue(env):
    _history(env, "q1", "ebook", "active")
    _history(env, "q2", "audiobook", "active")
    _history(env, "done", "ebook", "complete")

    assert stats.collect(env[3], now=NOW)["queue"]["active"] == 2


def test_requests_are_bucketed_by_status_and_delivery(env):
    _request(env)  # pending, nothing started
    _request(env)
    _request(env, delivery="queued")
    _request(env, status="fulfilled", delivery="complete")
    _request(env, status="fulfilled", delivery="error")
    _request(env, status="fulfilled", delivery="cancelled")
    _request(env, status="rejected")

    result = stats.collect(env[3], now=NOW)

    assert result["queue"]["awaiting_pickup"] == 2
    assert result["queue"]["queued"] == 1
    assert result["requests"] == {
        "pending": 3,
        "delivered": 1,
        "failed": 1,
        "cancelled": 1,
        "rejected": 1,
    }


def test_failures_are_counted_over_the_last_seven_days_only(env):
    _history(env, "f1", "ebook", "error", age_days=1, message="source said no")
    _history(env, "f2", "ebook", "error", age_days=3, message="timeout")
    _history(env, "old", "ebook", "error", age_days=8, message="timeout")
    _history(env, "ok", "ebook", "complete", age_days=1)

    assert stats.collect(env[3], now=NOW)["errors"] == {"failed_7d": 2}


def test_an_error_without_a_message_is_a_failure(env):
    _history(env, "f1", "ebook", "error", age_days=1)

    assert stats.collect(env[3], now=NOW)["errors"]["failed_7d"] == 1


def test_the_database_is_opened_read_only(env, monkeypatch):
    opened = []
    real = sqlite3.connect

    def spy(target, *args, **kwargs):
        opened.append(str(target))
        return real(target, *args, **kwargs)

    monkeypatch.setattr(stats.sqlite3, "connect", spy)

    stats.collect(env[3], now=NOW)

    assert opened
    assert all("mode=ro" in target for target in opened)
