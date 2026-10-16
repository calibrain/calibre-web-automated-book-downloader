"""Tests for persisted download-history helpers."""

from __future__ import annotations

import os
import tempfile

from shelfmark.core.download_history_service import DownloadHistoryService
from shelfmark.core.user_db import UserDB


def test_iso_to_epoch_treats_naive_sqlite_timestamp_as_utc():
    epoch = DownloadHistoryService._iso_to_epoch("2026-01-02 03:04:05")
    assert epoch == 1767323045.0


def test_record_download_stores_utc_iso_timestamps():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "users.db")
        user_db = UserDB(db_path)
        user_db.initialize()
        service = DownloadHistoryService(db_path)

        service.record_download(
            task_id="task-1",
            user_id=None,
            username=None,
            request_id=None,
            source="direct_download",
            source_display_name="Direct Download",
            title="Example",
            author=None,
            file_format=None,
            size=None,
            preview=None,
            content_type="ebook",
            downloads=None,
            origin="direct",
        )

        conn = user_db._connect()
        try:
            row = conn.execute(
                "SELECT queued_at, terminal_at FROM download_history WHERE task_id = ?",
                ("task-1",),
            ).fetchone()
        finally:
            conn.close()

        assert row is not None
        assert "+00:00" in row["queued_at"]
        assert "+00:00" in row["terminal_at"]


def _service(tmpdir):
    db_path = os.path.join(tmpdir, "users.db")
    user_db = UserDB(db_path)
    user_db.initialize()
    return user_db, DownloadHistoryService(db_path)


def _record(service, *, request_id, title, preview=None, user_id=None):
    service.record_download(
        task_id="release-1",
        user_id=user_id,
        username="reader" if user_id else None,
        request_id=request_id,
        source="audiobookbay",
        source_display_name=None,
        title=title,
        author=None,
        file_format=None,
        size=None,
        preview=preview,
        content_type="audiobook",
        downloads=None,
        origin="requested" if request_id else "direct",
    )


def test_a_second_request_that_picks_the_same_release_takes_the_row_over():
    with tempfile.TemporaryDirectory() as tmpdir:
        _user_db, service = _service(tmpdir)
        _record(service, request_id=173, title="Cross and Sampson")
        service.finalize_download(task_id="release-1", final_status="error", status_message="x")

        _record(service, request_id=168, title="Cross Down")

        row = service.get_by_task_id("release-1")
        assert row["request_id"] == 168
        assert row["title"] == "Cross Down"
        assert row["final_status"] == "active"


def test_a_retry_without_a_request_keeps_the_request_the_row_had():
    with tempfile.TemporaryDirectory() as tmpdir:
        _user_db, service = _service(tmpdir)
        _record(service, request_id=7, title="Book")

        _record(service, request_id=None, title="Book")

        assert service.get_by_task_id("release-1")["request_id"] == 7


def test_the_download_payload_serves_the_cover_through_the_local_proxy(monkeypatch):
    monkeypatch.setattr("shelfmark.config.env.is_covers_cache_enabled", lambda: True)
    with tempfile.TemporaryDirectory() as tmpdir:
        _user_db, service = _service(tmpdir)
        _record(
            service,
            request_id=None,
            title="Book",
            preview="https://assets.hardcover.app/edition/1/a.jpg",
        )
        row = service.get_by_task_id("release-1")

        payload = DownloadHistoryService.to_download_payload(row)

    assert payload["preview"].startswith("/api/covers/release-1?url=")


def test_the_download_payload_leaves_a_missing_cover_alone():
    assert (
        DownloadHistoryService.to_download_payload(
            {"task_id": "t", "final_status": "error", "preview": None}
        )["preview"]
        is None
    )
