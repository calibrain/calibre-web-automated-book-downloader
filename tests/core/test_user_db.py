"""
Tests for SQLite user database.

Tests CRUD operations on users and user_settings tables.
"""

import os
import sqlite3
import tempfile

import pytest


@pytest.fixture
def db_path():
    """Create a temporary database path."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield os.path.join(tmpdir, "shelfmark.db")


@pytest.fixture
def user_db(db_path):
    """Create a UserDB instance with a temporary database."""
    from shelfmark.core.user_db import UserDB

    db = UserDB(db_path)
    db.initialize()
    return db


class TestUserDBInitialization:
    """Tests for database creation and schema setup."""

    def test_initialize_creates_database_file(self, db_path):
        from shelfmark.core.user_db import UserDB

        db = UserDB(db_path)
        db.initialize()
        assert os.path.exists(db_path)

    def test_initialize_creates_users_table(self, user_db, db_path):
        conn = sqlite3.connect(db_path)
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
        assert cursor.fetchone() is not None
        conn.close()

    def test_initialize_creates_user_settings_table(self, user_db, db_path):
        conn = sqlite3.connect(db_path)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='user_settings'"
        )
        assert cursor.fetchone() is not None
        conn.close()

    def test_initialize_creates_download_requests_table(self, user_db, db_path):
        conn = sqlite3.connect(db_path)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='download_requests'"
        )
        assert cursor.fetchone() is not None
        conn.close()

    def test_initialize_creates_download_history_table(self, user_db, db_path):
        conn = sqlite3.connect(db_path)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='download_history'"
        )
        assert cursor.fetchone() is not None
        conn.close()

    def test_initialize_creates_activity_view_state_table(self, user_db, db_path):
        conn = sqlite3.connect(db_path)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='activity_view_state'"
        )
        assert cursor.fetchone() is not None
        conn.close()

    def test_initialize_does_not_create_legacy_activity_tables(self, user_db, db_path):
        conn = sqlite3.connect(db_path)
        activity_log = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='activity_log'"
        ).fetchone()
        dismissals = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='activity_dismissals'"
        ).fetchone()
        assert activity_log is None
        assert dismissals is None
        conn.close()

    def test_initialize_creates_download_requests_indexes(self, user_db, db_path):
        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='download_requests'"
        ).fetchall()
        index_names = {row[0] for row in rows}
        assert "idx_download_requests_user_status_created_at" in index_names
        assert "idx_download_requests_status_created_at" in index_names
        conn.close()

    def test_initialize_does_not_create_legacy_activity_indexes(self, user_db, db_path):
        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='activity_log'"
        ).fetchall()
        log_index_names = {row[0] for row in rows}
        assert "idx_activity_log_user_terminal" not in log_index_names
        assert "idx_activity_log_lookup" not in log_index_names

        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='activity_dismissals'"
        ).fetchall()
        dismissal_index_names = {row[0] for row in rows}
        assert "idx_activity_dismissals_user_dismissed_at" not in dismissal_index_names
        conn.close()

    def test_initialize_creates_download_history_indexes(self, user_db, db_path):
        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='download_history'"
        ).fetchall()
        index_names = {row[0] for row in rows}
        assert "idx_download_history_user_status" in index_names
        assert "idx_download_history_recent" in index_names
        conn.close()

    def test_initialize_creates_activity_view_state_indexes(self, user_db, db_path):
        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='activity_view_state'"
        ).fetchall()
        index_names = {row[0] for row in rows}
        assert "idx_activity_view_state_history" in index_names
        assert "idx_activity_view_state_hidden" in index_names
        conn.close()

    def test_initialize_enables_wal_mode(self, user_db, db_path):
        conn = sqlite3.connect(db_path)
        cursor = conn.execute("PRAGMA journal_mode")
        mode = cursor.fetchone()[0]
        assert mode == "wal"
        conn.close()

    def test_initialize_is_idempotent(self, db_path):
        from shelfmark.core.user_db import UserDB

        db = UserDB(db_path)
        db.initialize()
        db.initialize()  # Should not raise
        assert os.path.exists(db_path)

    def test_initialize_migrates_auth_source_column_and_backfills(self, db_path):
        """Existing DBs without auth_source should be migrated in place."""
        conn = sqlite3.connect(db_path)
        conn.executescript(
            """
            CREATE TABLE users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                username      TEXT UNIQUE NOT NULL,
                email         TEXT,
                display_name  TEXT,
                password_hash TEXT,
                oidc_subject  TEXT UNIQUE,
                role          TEXT NOT NULL DEFAULT 'user',
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE user_settings (
                user_id       INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                settings_json TEXT NOT NULL DEFAULT '{}'
            );
            """
        )
        conn.execute(
            "INSERT INTO users (username, password_hash, oidc_subject, role) VALUES (?, ?, ?, ?)",
            ("local_admin", "hash", None, "admin"),
        )
        conn.execute(
            "INSERT INTO users (username, oidc_subject, role) VALUES (?, ?, ?)",
            ("oidc_user", "sub-123", "user"),
        )
        conn.commit()
        conn.close()

        from shelfmark.core.user_db import UserDB

        db = UserDB(db_path)
        db.initialize()

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        columns = conn.execute("PRAGMA table_info(users)").fetchall()
        assert "auth_source" in {str(c["name"]) for c in columns}

        rows = conn.execute("SELECT username, auth_source FROM users ORDER BY username").fetchall()
        by_username = {r["username"]: r["auth_source"] for r in rows}
        assert by_username["local_admin"] == "builtin"
        assert by_username["oidc_user"] == "oidc"
        conn.close()

    def test_initialize_preserves_existing_users_and_user_settings_rows(self, db_path):
        conn = sqlite3.connect(db_path)
        conn.executescript(
            """
            CREATE TABLE users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                username      TEXT UNIQUE NOT NULL,
                email         TEXT,
                display_name  TEXT,
                password_hash TEXT,
                oidc_subject  TEXT UNIQUE,
                role          TEXT NOT NULL DEFAULT 'user',
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE user_settings (
                user_id       INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                settings_json TEXT NOT NULL DEFAULT '{}'
            );
            """
        )
        conn.execute(
            "INSERT INTO users (id, username, email, role) VALUES (?, ?, ?, ?)",
            (1, "legacy-user", "legacy@example.com", "user"),
        )
        conn.execute(
            "INSERT INTO user_settings (user_id, settings_json) VALUES (?, ?)",
            (1, '{"DESTINATION":"/books/legacy"}'),
        )
        conn.commit()
        conn.close()

        from shelfmark.core.user_db import UserDB

        db = UserDB(db_path)
        db.initialize()
        db.initialize()

        conn = sqlite3.connect(db_path)
        user_row = conn.execute("SELECT username, email FROM users WHERE id = 1").fetchone()
        settings_row = conn.execute(
            "SELECT settings_json FROM user_settings WHERE user_id = 1"
        ).fetchone()
        assert user_row == ("legacy-user", "legacy@example.com")
        assert settings_row == ('{"DESTINATION":"/books/legacy"}',)
        conn.close()

    def test_initialize_does_not_add_policy_columns_to_users_table(self, user_db, db_path):
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        columns = conn.execute("PRAGMA table_info(users)").fetchall()
        column_names = {str(col["name"]) for col in columns}
        assert "REQUESTS_ENABLED" not in column_names
        assert "REQUEST_POLICY_DEFAULT_EBOOK" not in column_names
        assert "REQUEST_POLICY_DEFAULT_AUDIOBOOK" not in column_names
        assert "REQUEST_POLICY_RULES" not in column_names
        assert "MAX_PENDING_REQUESTS_PER_USER" not in column_names
        assert "REQUESTS_ALLOW_NOTES" not in column_names
        conn.close()

    def test_initialize_does_not_add_dismissed_at_to_download_requests(self, db_path):
        conn = sqlite3.connect(db_path)
        conn.executescript(
            """
            CREATE TABLE users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                username      TEXT UNIQUE NOT NULL,
                email         TEXT,
                display_name  TEXT,
                password_hash TEXT,
                oidc_subject  TEXT UNIQUE,
                auth_source   TEXT NOT NULL DEFAULT 'builtin',
                role          TEXT NOT NULL DEFAULT 'user',
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE user_settings (
                user_id       INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                settings_json TEXT NOT NULL DEFAULT '{}'
            );

            CREATE TABLE download_requests (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                status         TEXT NOT NULL DEFAULT 'pending',
                delivery_state TEXT NOT NULL DEFAULT 'none',
                source_hint    TEXT,
                content_type   TEXT NOT NULL,
                request_level  TEXT NOT NULL,
                policy_mode    TEXT NOT NULL,
                book_data      TEXT NOT NULL,
                release_data   TEXT,
                note           TEXT,
                admin_note     TEXT,
                reviewed_by    INTEGER REFERENCES users(id),
                created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                reviewed_at    TIMESTAMP,
                delivery_updated_at TIMESTAMP
            );
            """
        )
        conn.commit()
        conn.close()

        from shelfmark.core.user_db import UserDB

        db = UserDB(db_path)
        db.initialize()

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        columns = conn.execute("PRAGMA table_info(download_requests)").fetchall()
        column_names = {str(col["name"]) for col in columns}
        assert "dismissed_at" not in column_names
        conn.close()

    def test_initialize_migrates_existing_install_without_backfill(self, db_path):
        """Upgrade path: preserve existing rows and add new schema without retroactive history backfill."""
        conn = sqlite3.connect(db_path)
        conn.executescript(
            """
            CREATE TABLE users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                username      TEXT UNIQUE NOT NULL,
                email         TEXT,
                display_name  TEXT,
                password_hash TEXT,
                oidc_subject  TEXT UNIQUE,
                auth_source   TEXT NOT NULL DEFAULT 'builtin',
                role          TEXT NOT NULL DEFAULT 'user',
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE user_settings (
                user_id       INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                settings_json TEXT NOT NULL DEFAULT '{}'
            );

            CREATE TABLE download_requests (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                status         TEXT NOT NULL DEFAULT 'pending',
                delivery_state TEXT NOT NULL DEFAULT 'none',
                source_hint    TEXT,
                content_type   TEXT NOT NULL,
                request_level  TEXT NOT NULL,
                policy_mode    TEXT NOT NULL,
                book_data      TEXT NOT NULL,
                release_data   TEXT,
                note           TEXT,
                admin_note     TEXT,
                reviewed_by    INTEGER REFERENCES users(id),
                created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                reviewed_at    TIMESTAMP,
                delivery_updated_at TIMESTAMP
            );

            CREATE TABLE activity_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                item_type TEXT NOT NULL,
                item_key TEXT NOT NULL,
                request_id INTEGER,
                source_id TEXT,
                origin TEXT NOT NULL,
                final_status TEXT NOT NULL,
                snapshot_json TEXT NOT NULL,
                terminal_at TIMESTAMP NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE activity_dismissals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                item_type TEXT NOT NULL,
                item_key TEXT NOT NULL,
                activity_log_id INTEGER REFERENCES activity_log(id) ON DELETE SET NULL,
                dismissed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, item_type, item_key)
            );
            """
        )
        conn.execute(
            "INSERT INTO users (id, username, role) VALUES (?, ?, ?)", (1, "legacy-user", "user")
        )
        conn.execute(
            """
            INSERT INTO download_requests (
                id,
                user_id,
                status,
                delivery_state,
                content_type,
                request_level,
                policy_mode,
                book_data
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                11,
                1,
                "fulfilled",
                "complete",
                "ebook",
                "book",
                "request_book",
                '{"title":"Legacy Book"}',
            ),
        )
        conn.execute(
            """
            INSERT INTO activity_log (
                id,
                user_id,
                item_type,
                item_key,
                request_id,
                source_id,
                origin,
                final_status,
                snapshot_json,
                terminal_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                21,
                1,
                "download",
                "download:legacy-task",
                11,
                "legacy-task",
                "request",
                "complete",
                '{"kind":"download","download":{"id":"legacy-task","title":"Legacy Book"}}',
                "2026-01-01T00:00:00+00:00",
            ),
        )
        conn.execute(
            """
            INSERT INTO activity_dismissals (
                user_id,
                item_type,
                item_key,
                activity_log_id,
                dismissed_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (1, "download", "download:legacy-task", 21, "2026-01-02T00:00:00+00:00"),
        )
        conn.commit()
        conn.close()

        from shelfmark.core.user_db import UserDB

        db = UserDB(db_path)
        db.initialize()

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        request_row = conn.execute(
            "SELECT id, user_id, status FROM download_requests WHERE id = 11"
        ).fetchone()
        assert request_row is not None
        assert request_row["user_id"] == 1
        assert request_row["status"] == "fulfilled"

        request_columns = conn.execute("PRAGMA table_info(download_requests)").fetchall()
        request_column_names = {str(col["name"]) for col in request_columns}
        assert "dismissed_at" not in request_column_names

        history_table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='download_history'"
        ).fetchone()
        assert history_table is not None

        view_state_table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='activity_view_state'"
        ).fetchone()
        assert view_state_table is not None

        # No retroactive copy from legacy activity tables in the no-backfill plan.
        history_count = conn.execute("SELECT COUNT(*) AS count FROM download_history").fetchone()[
            "count"
        ]
        assert history_count == 0

        legacy_activity_rows = conn.execute(
            "SELECT COUNT(*) AS count FROM activity_log"
        ).fetchone()["count"]
        legacy_dismissal_rows = conn.execute(
            "SELECT COUNT(*) AS count FROM activity_dismissals"
        ).fetchone()["count"]
        assert legacy_activity_rows == 1
        assert legacy_dismissal_rows == 1
        conn.close()


class TestUserCRUD:
    """Tests for user create, read, update, delete operations."""

    def test_create_user(self, user_db):
        user = user_db.create_user(
            username="john",
            email="john@example.com",
            display_name="John Doe",
        )
        assert user["id"] is not None
        assert user["username"] == "john"
        assert user["email"] == "john@example.com"
        assert user["display_name"] == "John Doe"
        assert user["auth_source"] == "builtin"
        assert user["role"] == "user"

    def test_create_user_with_password(self, user_db):
        user = user_db.create_user(
            username="admin",
            password_hash="hashed_pw",
            role="admin",
        )
        assert user["role"] == "admin"
        assert user["password_hash"] == "hashed_pw"

    def test_create_user_with_oidc_subject(self, user_db):
        user = user_db.create_user(
            username="oidcuser",
            oidc_subject="sub-12345",
            email="oidc@example.com",
            auth_source="oidc",
        )
        assert user["oidc_subject"] == "sub-12345"
        assert user["auth_source"] == "oidc"

    def test_create_user_with_invalid_auth_source_fails(self, user_db):
        with pytest.raises(ValueError, match="Invalid auth_source"):
            user_db.create_user(username="john", auth_source="not-real")

    def test_create_duplicate_username_fails(self, user_db):
        user_db.create_user(username="john")
        with pytest.raises(ValueError, match="already exists"):
            user_db.create_user(username="john")

    def test_create_duplicate_oidc_subject_fails(self, user_db):
        user_db.create_user(username="user1", oidc_subject="sub-123")
        with pytest.raises(ValueError, match="already exists"):
            user_db.create_user(username="user2", oidc_subject="sub-123")

    def test_get_user_by_id(self, user_db):
        created = user_db.create_user(username="john")
        fetched = user_db.get_user(user_id=created["id"])
        assert fetched["username"] == "john"

    def test_get_user_by_username(self, user_db):
        user_db.create_user(username="john", email="john@example.com")
        fetched = user_db.get_user(username="john")
        assert fetched["email"] == "john@example.com"

    def test_get_user_by_oidc_subject(self, user_db):
        user_db.create_user(username="john", oidc_subject="sub-123")
        fetched = user_db.get_user(oidc_subject="sub-123")
        assert fetched["username"] == "john"

    def test_get_nonexistent_user_returns_none(self, user_db):
        assert user_db.get_user(username="nobody") is None

    def test_update_user(self, user_db):
        user = user_db.create_user(username="john", role="user")
        user_db.update_user(
            user["id"],
            username="jane",
            role="admin",
            email="new@example.com",
            auth_source="proxy",
        )
        updated = user_db.get_user(user_id=user["id"])
        assert updated["username"] == "jane"
        assert updated["role"] == "admin"
        assert updated["email"] == "new@example.com"
        assert updated["auth_source"] == "proxy"

    def test_update_user_rejects_invalid_auth_source(self, user_db):
        user = user_db.create_user(username="john")
        with pytest.raises(ValueError, match="Invalid auth_source"):
            user_db.update_user(user["id"], auth_source="bad")

    def test_update_nonexistent_user_raises(self, user_db):
        with pytest.raises(ValueError, match="not found"):
            user_db.update_user(9999, role="admin")

    def test_delete_user(self, user_db):
        user = user_db.create_user(username="john")
        user_db.delete_user(user["id"])
        assert user_db.get_user(user_id=user["id"]) is None

    def test_delete_user_cascades_settings(self, user_db):
        user = user_db.create_user(username="john")
        user_db.set_user_settings(user["id"], {"booklore_library_id": 1})
        user_db.delete_user(user["id"])
        assert user_db.get_user_settings(user["id"]) == {}

    def test_list_users(self, user_db):
        user_db.create_user(username="alice")
        user_db.create_user(username="bob")
        user_db.create_user(username="charlie")
        users = user_db.list_users()
        assert len(users) == 3
        usernames = [u["username"] for u in users]
        assert "alice" in usernames
        assert "bob" in usernames
        assert "charlie" in usernames


class TestCachedUserLookup:
    """Tests for the short-lived lookup cache used by per-request auth."""

    @pytest.fixture
    def counted(self, user_db, monkeypatch):
        calls = []
        real_get_user = user_db.get_user

        def counting_get_user(**kwargs):
            calls.append(kwargs)
            return real_get_user(**kwargs)

        monkeypatch.setattr(user_db, "get_user", counting_get_user)
        return calls

    def test_repeat_lookups_are_answered_from_memory(self, user_db, counted):
        user = user_db.create_user(username="john", role="admin")

        for _ in range(3):
            assert user_db.get_user_cached(username="john")["id"] == user["id"]
            assert user_db.get_user_cached(user_id=user["id"])["username"] == "john"
        assert user_db.get_user_cached(username="nobody") is None
        assert user_db.get_user_cached(username="nobody") is None

        assert len(counted) == 3

    def test_entries_expire_after_the_ttl(self, user_db, counted, monkeypatch):
        import shelfmark.core.user_db as user_db_module

        user_db.create_user(username="john")
        now = [1000.0]
        monkeypatch.setattr(user_db_module.time, "monotonic", lambda: now[0])

        user_db.get_user_cached(username="john")
        now[0] += user_db_module.USER_CACHE_TTL_SECONDS - 0.1
        user_db.get_user_cached(username="john")
        assert len(counted) == 1

        now[0] += 0.2
        user_db.get_user_cached(username="john")
        assert len(counted) == 2

    def test_writes_through_the_db_drop_cached_entries(self, user_db):
        user = user_db.create_user(username="john", role="admin")
        assert user_db.get_user_cached(username="john")["role"] == "admin"
        assert user_db.get_user_cached(username="jane") is None

        user_db.update_user(user["id"], role="user")
        assert user_db.get_user_cached(username="john")["role"] == "user"

        user_db.create_user(username="jane")
        assert user_db.get_user_cached(username="jane") is not None

        user_db.delete_user(user["id"])
        assert user_db.get_user_cached(username="john") is None
        assert user_db.get_user_cached(user_id=user["id"]) is None

    def test_callers_cannot_change_the_cached_row(self, user_db):
        user_db.create_user(username="john", role="user")

        user_db.get_user_cached(username="john")["role"] = "admin"

        assert user_db.get_user_cached(username="john")["role"] == "user"


class TestUserSettings:
    """Tests for per-user settings."""

    def test_set_and_get_user_settings(self, user_db):
        user = user_db.create_user(username="john")
        settings = {"booklore_library_id": 5, "booklore_path_id": 2}
        user_db.set_user_settings(user["id"], settings)
        fetched = user_db.get_user_settings(user["id"])
        assert fetched["booklore_library_id"] == 5
        assert fetched["booklore_path_id"] == 2

    def test_get_settings_for_user_without_settings(self, user_db):
        user = user_db.create_user(username="john")
        assert user_db.get_user_settings(user["id"]) == {}

    def test_update_user_settings_merges(self, user_db):
        user = user_db.create_user(username="john")
        user_db.set_user_settings(user["id"], {"key1": "val1"})
        user_db.set_user_settings(user["id"], {"key2": "val2"})
        settings = user_db.get_user_settings(user["id"])
        assert settings["key1"] == "val1"
        assert settings["key2"] == "val2"

    def test_update_user_settings_overwrites_existing_key(self, user_db):
        user = user_db.create_user(username="john")
        user_db.set_user_settings(user["id"], {"key1": "old"})
        user_db.set_user_settings(user["id"], {"key1": "new"})
        settings = user_db.get_user_settings(user["id"])
        assert settings["key1"] == "new"


class TestDownloadRequests:
    """Tests for download request storage and validation."""

    @staticmethod
    def _book_data():
        return {
            "title": "Test Book",
            "author": "Test Author",
            "content_type": "ebook",
            "provider": "openlibrary",
            "provider_id": "ol-1",
        }

    @staticmethod
    def _release_data():
        return {
            "source": "direct_download",
            "source_id": "release-1",
            "title": "Release One",
        }

    def test_create_and_get_release_level_request(self, user_db):
        user = user_db.create_user(username="alice")

        created = user_db.create_request(
            user_id=user["id"],
            content_type="ebook",
            request_level="release",
            policy_mode="request_release",
            book_data=self._book_data(),
            release_data=self._release_data(),
            note="please grab this release",
        )

        assert created["id"] is not None
        assert created["status"] == "pending"
        assert created["policy_mode"] == "request_release"
        assert created["request_level"] == "release"
        assert created["book_data"]["title"] == "Test Book"
        assert created["release_data"]["source_id"] == "release-1"

        fetched = user_db.get_request(created["id"])
        assert fetched is not None
        assert fetched["id"] == created["id"]
        assert fetched["note"] == "please grab this release"

    def test_create_request_rejects_invalid_status(self, user_db):
        user = user_db.create_user(username="alice")

        with pytest.raises(ValueError, match="Invalid request status"):
            user_db.create_request(
                user_id=user["id"],
                status="queued",
                content_type="ebook",
                request_level="book",
                policy_mode="request_book",
                book_data=self._book_data(),
            )

    def test_create_request_rejects_invalid_policy_mode(self, user_db):
        user = user_db.create_user(username="alice")

        with pytest.raises(ValueError, match="Invalid policy_mode"):
            user_db.create_request(
                user_id=user["id"],
                content_type="ebook",
                request_level="book",
                policy_mode="allow",
                book_data=self._book_data(),
            )

    def test_create_request_rejects_release_level_without_release_data(self, user_db):
        user = user_db.create_user(username="alice")

        with pytest.raises(
            ValueError, match="request_level=release requires non-null release_data"
        ):
            user_db.create_request(
                user_id=user["id"],
                content_type="ebook",
                request_level="release",
                policy_mode="request_release",
                book_data=self._book_data(),
                release_data=None,
            )

    def test_create_request_rejects_book_level_with_release_data(self, user_db):
        user = user_db.create_user(username="alice")

        with pytest.raises(ValueError, match="request_level=book requires null release_data"):
            user_db.create_request(
                user_id=user["id"],
                content_type="ebook",
                request_level="book",
                policy_mode="request_book",
                book_data=self._book_data(),
                release_data=self._release_data(),
            )

    def test_create_request_rejects_non_object_release_data(self, user_db):
        user = user_db.create_user(username="alice")

        with pytest.raises(TypeError, match="release_data must be an object when provided"):
            user_db.create_request(
                user_id=user["id"],
                content_type="ebook",
                request_level="release",
                policy_mode="request_release",
                book_data=self._book_data(),
                release_data="not-an-object",
            )

    def test_list_requests_filters_by_user_and_status(self, user_db):
        alice = user_db.create_user(username="alice")
        bob = user_db.create_user(username="bob")

        alice_pending = user_db.create_request(
            user_id=alice["id"],
            content_type="ebook",
            request_level="book",
            policy_mode="request_book",
            book_data=self._book_data(),
        )
        alice_fulfilled = user_db.create_request(
            user_id=alice["id"],
            content_type="ebook",
            request_level="book",
            policy_mode="request_book",
            book_data=self._book_data(),
            status="fulfilled",
        )
        bob_pending = user_db.create_request(
            user_id=bob["id"],
            content_type="ebook",
            request_level="book",
            policy_mode="request_book",
            book_data=self._book_data(),
        )

        alice_only = user_db.list_requests(user_id=alice["id"])
        assert {row["id"] for row in alice_only} == {alice_pending["id"], alice_fulfilled["id"]}

        pending_only = user_db.list_requests(status="pending")
        assert {row["id"] for row in pending_only} == {alice_pending["id"], bob_pending["id"]}

    def test_update_request_allows_pending_to_terminal_transition(self, user_db):
        user = user_db.create_user(username="alice")
        created = user_db.create_request(
            user_id=user["id"],
            content_type="ebook",
            request_level="book",
            policy_mode="request_book",
            book_data=self._book_data(),
        )

        updated = user_db.update_request(
            created["id"],
            status="fulfilled",
            admin_note="done",
        )

        assert updated["status"] == "fulfilled"
        assert updated["admin_note"] == "done"

    def test_update_request_expected_current_status_enforces_compare_and_swap(self, user_db):
        user = user_db.create_user(username="alice")
        created = user_db.create_request(
            user_id=user["id"],
            content_type="ebook",
            request_level="book",
            policy_mode="request_book",
            book_data=self._book_data(),
        )

        first = user_db.update_request(
            created["id"],
            expected_current_status="pending",
            status="fulfilled",
        )
        assert first["status"] == "fulfilled"

        with pytest.raises(ValueError, match="Request state changed before update"):
            user_db.update_request(
                created["id"],
                expected_current_status="pending",
                status="fulfilled",
            )

    def test_update_request_rejects_terminal_status_mutation(self, user_db):
        user = user_db.create_user(username="alice")
        created = user_db.create_request(
            user_id=user["id"],
            content_type="ebook",
            request_level="book",
            policy_mode="request_book",
            book_data=self._book_data(),
            status="cancelled",
        )

        with pytest.raises(ValueError, match="Terminal request statuses are immutable"):
            user_db.update_request(created["id"], status="fulfilled")

    def test_update_request_validates_request_level_and_release_data(self, user_db):
        user = user_db.create_user(username="alice")
        created = user_db.create_request(
            user_id=user["id"],
            content_type="ebook",
            request_level="book",
            policy_mode="request_book",
            book_data=self._book_data(),
        )

        updated = user_db.update_request(
            created["id"],
            request_level="release",
            release_data=self._release_data(),
            policy_mode="request_release",
        )
        assert updated["request_level"] == "release"
        assert updated["policy_mode"] == "request_release"
        assert updated["release_data"]["source_id"] == "release-1"

    def test_update_request_allows_fulfilled_book_level_to_store_release_data(self, user_db):
        user = user_db.create_user(username="alice")
        created = user_db.create_request(
            user_id=user["id"],
            content_type="ebook",
            request_level="book",
            policy_mode="request_book",
            book_data=self._book_data(),
        )

        updated = user_db.update_request(
            created["id"],
            status="fulfilled",
            release_data=self._release_data(),
            admin_note="Approved from browse mode",
        )
        assert updated["request_level"] == "book"
        assert updated["status"] == "fulfilled"
        assert updated["release_data"]["source_id"] == "release-1"
        assert updated["admin_note"] == "Approved from browse mode"

    def test_update_request_rejects_non_object_release_data(self, user_db):
        user = user_db.create_user(username="alice")
        created = user_db.create_request(
            user_id=user["id"],
            content_type="ebook",
            request_level="release",
            policy_mode="request_release",
            book_data=self._book_data(),
            release_data=self._release_data(),
        )

        with pytest.raises(TypeError, match="release_data must be an object when provided"):
            user_db.update_request(created["id"], release_data="not-an-object")

    def test_reopen_failed_request_resets_fulfilled_request_for_reapproval(self, user_db):
        user = user_db.create_user(username="alice")
        created = user_db.create_request(
            user_id=user["id"],
            content_type="ebook",
            request_level="release",
            policy_mode="request_release",
            book_data=self._book_data(),
            release_data=self._release_data(),
            status="fulfilled",
            delivery_state="queued",
            reviewed_by=user["id"],
            reviewed_at="2026-01-01T00:00:00+00:00",
            delivery_updated_at="2026-01-01T00:00:01+00:00",
        )

        reopened = user_db.reopen_failed_request(
            created["id"],
            failure_reason=" Download timed out ",
        )

        assert reopened is not None
        assert reopened["status"] == "pending"
        assert reopened["delivery_state"] == "none"
        assert reopened["delivery_updated_at"] is None
        assert reopened["release_data"] is None
        assert reopened["last_failure_reason"] == "Download timed out"
        assert reopened["reviewed_by"] is None
        assert reopened["reviewed_at"] is None

    def test_reopen_failed_request_requires_reason_for_non_failure_states(self, user_db):
        user = user_db.create_user(username="alice")
        created = user_db.create_request(
            user_id=user["id"],
            content_type="ebook",
            request_level="release",
            policy_mode="request_release",
            book_data=self._book_data(),
            release_data=self._release_data(),
            status="fulfilled",
            delivery_state="queued",
        )

        reopened = user_db.reopen_failed_request(created["id"])
        assert reopened is None

    def test_reopen_failed_request_allows_failure_states_without_reason(self, user_db):
        user = user_db.create_user(username="alice")
        created = user_db.create_request(
            user_id=user["id"],
            content_type="ebook",
            request_level="release",
            policy_mode="request_release",
            book_data=self._book_data(),
            release_data=self._release_data(),
            status="fulfilled",
            delivery_state="error",
        )

        reopened = user_db.reopen_failed_request(created["id"])
        assert reopened is not None
        assert reopened["status"] == "pending"
        assert reopened["last_failure_reason"] is None

    def test_count_pending_requests(self, user_db):
        alice = user_db.create_user(username="alice")
        bob = user_db.create_user(username="bob")

        user_db.create_request(
            user_id=alice["id"],
            content_type="ebook",
            request_level="book",
            policy_mode="request_book",
            book_data=self._book_data(),
            status="pending",
        )
        user_db.create_request(
            user_id=alice["id"],
            content_type="ebook",
            request_level="book",
            policy_mode="request_book",
            book_data=self._book_data(),
            status="fulfilled",
        )
        user_db.create_request(
            user_id=bob["id"],
            content_type="ebook",
            request_level="book",
            policy_mode="request_book",
            book_data=self._book_data(),
            status="pending",
        )

        assert user_db.count_pending_requests() == 2
        assert user_db.count_user_pending_requests(alice["id"]) == 1
        assert user_db.count_user_pending_requests(bob["id"]) == 1

    def test_delete_user_cascades_download_requests(self, user_db):
        user = user_db.create_user(username="alice")
        created = user_db.create_request(
            user_id=user["id"],
            content_type="ebook",
            request_level="book",
            policy_mode="request_book",
            book_data=self._book_data(),
        )

        user_db.delete_user(user["id"])

        assert user_db.get_request(created["id"]) is None

    def test_delete_user_cleans_up_activity_view_state(self, user_db, db_path):
        from shelfmark.core.activity_view_state_service import ActivityViewStateService

        activity_view_state_service = ActivityViewStateService(db_path)

        alice = user_db.create_user(username="alice")
        bob = user_db.create_user(username="bob")
        alice_request = user_db.create_request(
            user_id=alice["id"],
            content_type="ebook",
            request_level="book",
            policy_mode="request_book",
            book_data=self._book_data(),
            status="rejected",
        )
        bob_request = user_db.create_request(
            user_id=bob["id"],
            content_type="ebook",
            request_level="book",
            policy_mode="request_book",
            book_data=self._book_data(),
            status="rejected",
        )

        activity_view_state_service.dismiss(
            viewer_scope=f"user:{alice['id']}",
            item_type="request",
            item_key=f"request:{alice_request['id']}",
        )
        activity_view_state_service.dismiss(
            viewer_scope="admin:shared",
            item_type="request",
            item_key=f"request:{alice_request['id']}",
        )
        activity_view_state_service.dismiss(
            viewer_scope="admin:shared",
            item_type="request",
            item_key=f"request:{bob_request['id']}",
        )

        user_db.delete_user(alice["id"])

        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            "SELECT viewer_scope, item_key FROM activity_view_state ORDER BY viewer_scope, item_key"
        ).fetchall()
        conn.close()

        assert rows == [("admin:shared", f"request:{bob_request['id']}")]
