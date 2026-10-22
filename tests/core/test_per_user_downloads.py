"""
Tests for per-user download scoping.

Tests that DownloadTask has a user_id field and that the queue
can be filtered by user.
"""

import logging
import sqlite3

import pytest

from shelfmark.core.models import DownloadTask, QueueStatus
from shelfmark.core.queue import BookQueue


class TestDownloadTaskUserId:
    """Tests that DownloadTask supports user_id."""

    def test_download_task_has_user_id_field(self):
        task = DownloadTask(
            task_id="test-123",
            source="direct_download",
            title="Test Book",
            user_id=42,
        )
        assert task.user_id == 42

    def test_download_task_user_id_defaults_to_none(self):
        task = DownloadTask(
            task_id="test-123",
            source="direct_download",
            title="Test Book",
        )
        assert task.user_id is None

    def test_download_task_preserves_user_id_in_queue(self):
        q = BookQueue()
        task = DownloadTask(
            task_id="test-123",
            source="direct_download",
            title="Test Book",
            user_id=42,
        )
        q.add(task)
        retrieved = q.get_task("test-123")
        assert retrieved.user_id == 42


class TestQueueFilterByUser:
    """Tests for filtering queue status by user."""

    def _make_task(self, task_id, user_id=None):
        return DownloadTask(
            task_id=task_id,
            source="direct_download",
            title=f"Book {task_id}",
            user_id=user_id,
        )

    def test_get_status_returns_all_when_no_filter(self):
        q = BookQueue()
        q.add(self._make_task("book-1", user_id=1))
        q.add(self._make_task("book-2", user_id=2))
        q.add(self._make_task("book-3", user_id=1))

        status = q.get_status()
        all_tasks = {}
        for tasks_by_status in status.values():
            all_tasks.update(tasks_by_status)
        assert len(all_tasks) == 3

    def test_get_status_for_user_filters(self):
        q = BookQueue()
        q.add(self._make_task("book-1", user_id=1))
        q.add(self._make_task("book-2", user_id=2))
        q.add(self._make_task("book-3", user_id=1))

        status = q.get_status(user_id=1)
        all_tasks = {}
        for tasks_by_status in status.values():
            all_tasks.update(tasks_by_status)
        assert len(all_tasks) == 2
        assert "book-1" in all_tasks
        assert "book-3" in all_tasks
        assert "book-2" not in all_tasks

    def test_get_status_for_user_returns_empty_when_none(self):
        q = BookQueue()
        q.add(self._make_task("book-1", user_id=1))

        status = q.get_status(user_id=999)
        all_tasks = {}
        for tasks_by_status in status.values():
            all_tasks.update(tasks_by_status)
        assert len(all_tasks) == 0

    def test_get_status_no_user_id_filter_includes_legacy_tasks(self):
        """Tasks without user_id (legacy) are visible to everyone."""
        q = BookQueue()
        q.add(self._make_task("book-1", user_id=None))
        q.add(self._make_task("book-2", user_id=1))

        # No filter - see all
        status = q.get_status()
        all_tasks = {}
        for tasks_by_status in status.values():
            all_tasks.update(tasks_by_status)
        assert len(all_tasks) == 2

    def test_get_status_user_filter_excludes_legacy_tasks(self):
        """Tasks without user_id are admin-only and hidden from user-scoped views."""
        q = BookQueue()
        q.add(self._make_task("book-1", user_id=None))
        q.add(self._make_task("book-2", user_id=1))

        status = q.get_status(user_id=1)
        all_tasks = {}
        for tasks_by_status in status.values():
            all_tasks.update(tasks_by_status)
        assert len(all_tasks) == 1
        assert "book-1" not in all_tasks
        assert "book-2" in all_tasks

    def test_enqueue_existing_deduplicates_queue_entries(self):
        q = BookQueue()
        q.add(self._make_task("book-1", user_id=1))

        assert q.enqueue_existing("book-1")
        assert q.enqueue_existing("book-1", priority=-10)

        queue_order = q.get_queue_order()
        assert len(queue_order) == 1
        assert queue_order[0]["id"] == "book-1"
        assert q.get_task("book-1").priority == -10
        assert q.get_task_status("book-1") == QueueStatus.QUEUED


# ---------------------------------------------------------------------------
# Per-user destination override in get_final_destination
# ---------------------------------------------------------------------------


class TestPerUserDestination:
    """get_final_destination should resolve destination via config user context."""

    def test_passes_user_id_to_get_destination(self, monkeypatch):
        """When task has a user_id, destination resolution should receive it."""
        from pathlib import Path

        captured: dict[str, object] = {"user_id": None, "username": None}

        task = DownloadTask(
            task_id="book1",
            source="direct_download",
            title="Test Book",
            user_id=42,
            username="alice",
        )

        def fake_get_destination(is_audiobook: bool = False, user_id=None, username=None):
            captured["user_id"] = user_id
            captured["username"] = username
            return Path("/user-books/alice")

        monkeypatch.setattr(
            "shelfmark.download.postprocess.destination.get_destination",
            fake_get_destination,
        )
        monkeypatch.setattr(
            "shelfmark.download.postprocess.destination.get_source",
            lambda _: type(
                "Source", (), {"get_destination_override": staticmethod(lambda task: None)}
            )(),
        )

        from shelfmark.download.postprocess.destination import get_final_destination

        result = get_final_destination(task)
        assert result == Path("/user-books/alice")
        assert captured["user_id"] == 42
        assert captured["username"] == "alice"

    def test_without_user_id_uses_global_context(self, monkeypatch):
        """When task has no user_id, destination resolution should use global context."""
        from pathlib import Path

        captured: dict[str, object] = {"user_id": 99, "username": "someone"}

        task = DownloadTask(
            task_id="book1",
            source="direct_download",
            title="Test Book",
        )

        def fake_get_destination(is_audiobook: bool = False, user_id=None, username=None):
            captured["user_id"] = user_id
            captured["username"] = username
            return Path("/global/books")

        monkeypatch.setattr(
            "shelfmark.download.postprocess.destination.get_destination",
            fake_get_destination,
        )
        monkeypatch.setattr(
            "shelfmark.download.postprocess.destination.get_source",
            lambda _: type(
                "Source", (), {"get_destination_override": staticmethod(lambda task: None)}
            )(),
        )

        from shelfmark.download.postprocess.destination import get_final_destination

        result = get_final_destination(task)
        assert result == Path("/global/books")
        assert captured["user_id"] is None
        assert captured["username"] is None

    def test_content_type_routing_still_wins(self, monkeypatch):
        """Direct mode content-type routing should take priority over destination lookup."""
        from pathlib import Path

        task = DownloadTask(
            task_id="book1",
            source="direct_download",
            title="Test Book",
            content_type="book (fiction)",
            user_id=42,
        )

        monkeypatch.setattr(
            "shelfmark.download.postprocess.destination.get_destination",
            lambda is_audiobook=False, user_id=None, username=None: Path("/global/books"),
        )
        monkeypatch.setattr(
            "shelfmark.download.postprocess.destination.get_source",
            lambda _: type(
                "Source",
                (),
                {"get_destination_override": staticmethod(lambda task: Path("/routed/books"))},
            )(),
        )

        from shelfmark.download.postprocess.destination import get_final_destination

        result = get_final_destination(task)
        assert result == Path("/routed/books")


class TestUserDestinationTemplate:
    """Destination settings should support {User} placeholder expansion."""

    def test_get_destination_expands_user_for_books(self, monkeypatch):
        from pathlib import Path

        from shelfmark.core.config import config
        from shelfmark.core.utils import get_destination

        def fake_config_get(key, default=None, user_id=None):
            if key == "DESTINATION":
                return "/books/{User}"
            if key == "INGEST_DIR":
                return "/books"
            return default

        monkeypatch.setattr(config, "get", fake_config_get)
        result = get_destination(is_audiobook=False, user_id=42, username="alice")
        assert result == Path("/books/alice")

    def test_get_destination_expands_user_for_audiobooks(self, monkeypatch):
        from pathlib import Path

        from shelfmark.core.config import config
        from shelfmark.core.utils import get_destination

        def fake_config_get(key, default=None, user_id=None):
            if key == "DESTINATION_AUDIOBOOK":
                return "/audiobooks/{User}"
            if key == "DESTINATION":
                return "/books/{User}"
            if key == "INGEST_DIR":
                return "/books"
            return default

        monkeypatch.setattr(config, "get", fake_config_get)
        result = get_destination(is_audiobook=True, user_id=42, username="alice")
        assert result == Path("/audiobooks/alice")

    def test_get_destination_looks_up_username_from_user_db(self, monkeypatch, tmp_path):
        from pathlib import Path

        from shelfmark.core.config import config
        from shelfmark.core.user_db import UserDB
        from shelfmark.core.utils import get_destination

        monkeypatch.setenv("CONFIG_DIR", str(tmp_path))

        user_db = UserDB(str(tmp_path / "users.db"))
        user_db.initialize()
        user = user_db.create_user(username="alice")

        def fake_config_get(key, default=None, user_id=None):
            if key == "DESTINATION":
                return "/books/{User}"
            if key == "INGEST_DIR":
                return "/books"
            return default

        monkeypatch.setattr(config, "get", fake_config_get)
        result = get_destination(is_audiobook=False, user_id=user["id"], username=None)
        assert result == Path("/books/alice")

    def test_get_destination_falls_back_when_user_db_lookup_fails(self, monkeypatch):
        from pathlib import Path

        from shelfmark.core.config import config
        from shelfmark.core.utils import get_destination

        def fake_config_get(key, default=None, user_id=None):
            if key == "DESTINATION":
                return "/books/{User}"
            if key == "INGEST_DIR":
                return "/books"
            return default

        monkeypatch.setattr(config, "get", fake_config_get)
        monkeypatch.setattr(
            "shelfmark.core.user_db.UserDB.get_user",
            lambda self, **kwargs: (_ for _ in ()).throw(sqlite3.OperationalError("locked")),
        )

        result = get_destination(is_audiobook=False, user_id=42, username=None)
        assert result == Path("/books")


class TestTaskToDictUsername:
    """Tests that _task_to_dict includes username for frontend display."""

    def test_task_to_dict_includes_username(self):
        """Username should be included in serialized task dict."""
        from shelfmark.download.orchestrator import _task_to_dict

        task = DownloadTask(
            task_id="book1",
            source="direct_download",
            title="Test Book",
            user_id=5,
            username="alice",
        )
        result = _task_to_dict(task)
        assert result["username"] == "alice"

    def test_task_to_dict_username_none_when_no_auth(self):
        """Username should be None when no user is set (no-auth mode)."""
        from shelfmark.download.orchestrator import _task_to_dict

        task = DownloadTask(
            task_id="book1",
            source="direct_download",
            title="Test Book",
        )
        result = _task_to_dict(task)
        assert result["username"] is None

    def test_task_to_dict_prefers_current_queue_status(self):
        """Serialized task status should reflect the queue bucket being emitted."""
        from shelfmark.download.orchestrator import _task_to_dict

        task = DownloadTask(
            task_id="book1",
            source="direct_download",
            title="Test Book",
            status=QueueStatus.QUEUED,
        )

        result = _task_to_dict(task, current_status=QueueStatus.COMPLETE)
        assert result["status"] == QueueStatus.COMPLETE.value


@pytest.fixture
def destination_warnings():
    """Collect warnings from the utils logger.

    setup_logger builds its loggers outside the standard hierarchy, so caplog's root
    handler never sees them.
    """
    from shelfmark.core import utils

    messages: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            if record.levelno >= logging.WARNING:
                messages.append(record.getMessage())

    handler = _Capture()
    utils.logger.addHandler(handler)
    try:
        yield messages
    finally:
        utils.logger.removeHandler(handler)


class TestUserDestinationContainment:
    """A stored per-user destination only applies while it stays inside the global one."""

    @staticmethod
    def _config_get(global_values: dict, user_values: dict):
        def fake_config_get(key, default=None, user_id=None):
            if user_id is not None and key in user_values:
                return user_values[key]
            return global_values.get(key, default)

        return fake_config_get

    @pytest.mark.parametrize(
        "override",
        ["/config", "/books/../config", "/books/{User}/../../etc", "/booksmark", "../x"],
    )
    def test_override_outside_global_is_ignored_with_warning(
        self, monkeypatch, destination_warnings, override
    ):
        from pathlib import Path

        from shelfmark.core.config import config
        from shelfmark.core.utils import get_destination

        monkeypatch.setattr(
            config,
            "get",
            self._config_get({"DESTINATION": "/books"}, {"DESTINATION": override}),
        )

        result = get_destination(is_audiobook=False, user_id=42, username="alice")

        assert result == Path("/books")
        assert any("DESTINATION" in message for message in destination_warnings)

    @pytest.mark.parametrize(
        ("global_destination", "override", "expected"),
        [
            ("/books", "/books/alice", "/books/alice"),
            ("/books", "/books/{User}/fiction", "/books/alice/fiction"),
            ("/books", "fiction", "/books/fiction"),
            ("/books/{User}", "/books/alice/fiction", "/books/alice/fiction"),
            ("/books/{User}", "/books/{User}", "/books/alice"),
        ],
    )
    def test_override_inside_global_is_used(
        self, monkeypatch, global_destination, override, expected
    ):
        from pathlib import Path

        from shelfmark.core.config import config
        from shelfmark.core.utils import get_destination

        monkeypatch.setattr(
            config,
            "get",
            self._config_get({"DESTINATION": global_destination}, {"DESTINATION": override}),
        )

        result = get_destination(is_audiobook=False, user_id=42, username="alice")
        assert result == Path(expected)

    def test_audiobook_override_is_checked_against_books_destination(self, monkeypatch):
        from pathlib import Path

        from shelfmark.core.config import config
        from shelfmark.core.utils import get_destination

        monkeypatch.setattr(
            config,
            "get",
            self._config_get(
                {"DESTINATION": "/books", "DESTINATION_AUDIOBOOK": ""},
                {"DESTINATION_AUDIOBOOK": "/audio/alice"},
            ),
        )

        result = get_destination(is_audiobook=True, user_id=42, username="alice")
        assert result == Path("/books")

    def test_audiobook_override_inside_global_audiobook_destination(self, monkeypatch):
        from pathlib import Path

        from shelfmark.core.config import config
        from shelfmark.core.utils import get_destination

        monkeypatch.setattr(
            config,
            "get",
            self._config_get(
                {"DESTINATION": "/books", "DESTINATION_AUDIOBOOK": "/audio"},
                {"DESTINATION_AUDIOBOOK": "/audio/alice"},
            ),
        )

        result = get_destination(is_audiobook=True, user_id=42, username="alice")
        assert result == Path("/audio/alice")

    def test_empty_override_uses_global(self, monkeypatch, destination_warnings):
        from pathlib import Path

        from shelfmark.core.config import config
        from shelfmark.core.utils import get_destination

        monkeypatch.setattr(
            config,
            "get",
            self._config_get({"DESTINATION": "/books"}, {"DESTINATION": ""}),
        )

        result = get_destination(is_audiobook=False, user_id=42, username="alice")

        assert result == Path("/books")
        assert destination_warnings == []
