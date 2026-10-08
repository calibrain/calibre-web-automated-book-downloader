"""SQLite user database for multi-user support."""

import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any, ClassVar

from shelfmark.core.activity_view_state_service import user_viewer_scope
from shelfmark.core.auth_modes import AUTH_SOURCE_BUILTIN, AUTH_SOURCE_SET
from shelfmark.core.logger import setup_logger
from shelfmark.core.models import QueueStatus
from shelfmark.core.request_validation import (
    DELIVERY_STATE_NONE,
    RequestStatus,
    normalize_delivery_state,
    normalize_policy_mode,
    normalize_request_level,
    normalize_request_status,
    validate_request_level_payload,
    validate_status_transition,
)

logger = setup_logger(__name__)

_CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS users (
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

CREATE TABLE IF NOT EXISTS user_settings (
    user_id       INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    settings_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS download_requests (
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

CREATE INDEX IF NOT EXISTS idx_download_requests_user_status_created_at
ON download_requests (user_id, status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_download_requests_status_created_at
ON download_requests (status, created_at DESC);

CREATE TABLE IF NOT EXISTS download_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT UNIQUE NOT NULL,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    username TEXT,
    request_id INTEGER,
    source TEXT NOT NULL,
    source_display_name TEXT,
    title TEXT NOT NULL,
    author TEXT,
    format TEXT,
    size TEXT,
    preview TEXT,
    content_type TEXT,
    origin TEXT NOT NULL DEFAULT 'direct',
    final_status TEXT NOT NULL,
    status_message TEXT,
    download_path TEXT,
    retry_payload TEXT,
    queued_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    terminal_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    downloads INTEGER
);

CREATE INDEX IF NOT EXISTS idx_download_history_user_status
ON download_history (user_id, final_status, terminal_at DESC);

CREATE INDEX IF NOT EXISTS idx_download_history_recent
ON download_history (user_id, terminal_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS activity_view_state (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    viewer_scope TEXT NOT NULL,
    item_type TEXT NOT NULL,
    item_key TEXT NOT NULL,
    dismissed_at TIMESTAMP,
    cleared_at TIMESTAMP,
    UNIQUE(viewer_scope, item_type, item_key)
);

CREATE INDEX IF NOT EXISTS idx_activity_view_state_history
ON activity_view_state (viewer_scope, dismissed_at DESC, id DESC)
WHERE dismissed_at IS NOT NULL AND cleared_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_activity_view_state_hidden
ON activity_view_state (viewer_scope, item_type, item_key)
WHERE dismissed_at IS NOT NULL;
"""


def _require_loaded_user(user: dict[str, Any] | None) -> dict[str, Any]:
    """Return a loaded user row or raise when the DB insert result is inconsistent."""
    if user is None:
        msg = "Failed to load newly created user"
        raise RuntimeError(msg)
    return user


def get_users_db_path(config_dir: str | None = None) -> str:
    """Return the configured users database path."""
    root = config_dir or os.environ.get("CONFIG_DIR", "/config")
    return str(Path(root) / "users.db")


def sync_builtin_admin_user(
    username: str,
    password_hash: str,
    db_path: str | None = None,
) -> None:
    """Ensure a local admin user exists for configured builtin credentials."""
    normalized_username = (username or "").strip()
    normalized_hash = password_hash or ""
    if not normalized_username or not normalized_hash:
        return

    user_db = UserDB(db_path or get_users_db_path())
    user_db.initialize()

    existing = user_db.get_user(username=normalized_username)
    if existing:
        existing_auth_source = (
            str(existing.get("auth_source") or AUTH_SOURCE_BUILTIN).strip().lower()
        )
        if existing_auth_source != AUTH_SOURCE_BUILTIN:
            logger.warning(
                "Skipped builtin admin sync for username '%s' because it belongs to auth_source='%s'",
                normalized_username,
                existing_auth_source,
            )
            return
        updates: dict[str, Any] = {}
        if existing.get("password_hash") != normalized_hash:
            updates["password_hash"] = normalized_hash
        if existing.get("role") != "admin":
            updates["role"] = "admin"
        if existing.get("auth_source") != AUTH_SOURCE_BUILTIN:
            updates["auth_source"] = AUTH_SOURCE_BUILTIN
        if updates:
            user_db.update_user(existing["id"], **updates)
            logger.info("Updated local admin user '%s' from builtin settings", normalized_username)
        return

    user_db.create_user(
        username=normalized_username,
        password_hash=normalized_hash,
        auth_source=AUTH_SOURCE_BUILTIN,
        role="admin",
    )
    logger.info("Created local admin user '%s' from builtin settings", normalized_username)


class UserDB:
    """Thread-safe SQLite user database."""

    _VALID_AUTH_SOURCES: ClassVar[frozenset[str]] = frozenset(AUTH_SOURCE_SET)

    def __init__(self, db_path: str) -> None:
        """Initialize the user database wrapper for the given SQLite path."""
        self._db_path = db_path
        self._lock = threading.Lock()

    @property
    def db_path(self) -> str:
        """Filesystem path of the SQLite database."""
        return self._db_path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def initialize(self) -> None:
        """Create database and tables if they don't exist."""
        with self._lock:
            conn = self._connect()
            try:
                conn.executescript(_CREATE_TABLES_SQL)
                self._migrate_auth_source_column(conn)
                self._migrate_request_delivery_columns(conn)
                self._migrate_download_history_queued_at(conn)
                self._migrate_download_history_retry_payload(conn)
                self._migrate_download_history_downloads(conn)
                conn.commit()
                # WAL mode must be changed outside an open transaction.
                conn.execute("PRAGMA journal_mode=WAL")
            finally:
                conn.close()

    def _migrate_auth_source_column(self, conn: sqlite3.Connection) -> None:
        """Ensure users.auth_source exists and backfill historical rows."""
        columns = conn.execute("PRAGMA table_info(users)").fetchall()
        column_names = {str(col["name"]) for col in columns}

        if "auth_source" not in column_names:
            conn.execute("ALTER TABLE users ADD COLUMN auth_source TEXT NOT NULL DEFAULT 'builtin'")

        # Backfill OIDC-origin users created before auth_source existed.
        conn.execute("UPDATE users SET auth_source = 'oidc' WHERE oidc_subject IS NOT NULL")
        # Defensive cleanup for any legacy null/blank values.
        conn.execute(
            "UPDATE users SET auth_source = 'builtin' WHERE auth_source IS NULL OR auth_source = ''"
        )

    def _migrate_request_delivery_columns(self, conn: sqlite3.Connection) -> None:
        """Ensure request delivery-state columns exist and backfill historical rows."""
        columns = conn.execute("PRAGMA table_info(download_requests)").fetchall()
        column_names = {str(col["name"]) for col in columns}

        if "delivery_state" not in column_names:
            conn.execute(
                "ALTER TABLE download_requests ADD COLUMN delivery_state TEXT NOT NULL DEFAULT 'none'"
            )
        if "delivery_updated_at" not in column_names:
            conn.execute("ALTER TABLE download_requests ADD COLUMN delivery_updated_at TIMESTAMP")
        if "last_failure_reason" not in column_names:
            conn.execute("ALTER TABLE download_requests ADD COLUMN last_failure_reason TEXT")

        conn.execute(
            """
            UPDATE download_requests
            SET delivery_state = 'none'
            WHERE delivery_state IS NULL OR TRIM(delivery_state) = '' OR delivery_state IN ('unknown', 'available', 'done')
            """
        )
        conn.execute(
            """
            UPDATE download_requests
            SET delivery_updated_at = COALESCE(delivery_updated_at, reviewed_at, created_at)
            WHERE delivery_state != 'none' AND delivery_updated_at IS NULL
            """
        )

    def _migrate_download_history_queued_at(self, conn: sqlite3.Connection) -> None:
        """Ensure download_history.queued_at exists for queue-time recording."""
        columns = conn.execute("PRAGMA table_info(download_history)").fetchall()
        column_names = {str(col["name"]) for col in columns}
        if "queued_at" not in column_names:
            conn.execute("ALTER TABLE download_history ADD COLUMN queued_at TIMESTAMP")
            conn.execute(
                "UPDATE download_history SET queued_at = CURRENT_TIMESTAMP WHERE queued_at IS NULL"
            )

    def _migrate_download_history_retry_payload(self, conn: sqlite3.Connection) -> None:
        """Ensure download_history.retry_payload exists for restart-safe retries."""
        columns = conn.execute("PRAGMA table_info(download_history)").fetchall()
        column_names = {str(col["name"]) for col in columns}
        if "retry_payload" not in column_names:
            conn.execute("ALTER TABLE download_history ADD COLUMN retry_payload TEXT")

    def _migrate_download_history_downloads(self, conn: sqlite3.Connection) -> None:
        """Ensure download_history.downloads exists for download count persistence."""
        columns = conn.execute("PRAGMA table_info(download_history)").fetchall()
        column_names = {str(col["name"]) for col in columns}
        if "downloads" not in column_names:
            conn.execute("ALTER TABLE download_history ADD COLUMN downloads INTEGER")

    def create_user(
        self,
        username: str,
        email: str | None = None,
        display_name: str | None = None,
        password_hash: str | None = None,
        oidc_subject: str | None = None,
        auth_source: str = "builtin",
        role: str = "user",
    ) -> dict[str, Any]:
        """Create a new user. Raises ValueError if username or oidc_subject already exists."""
        if auth_source not in self._VALID_AUTH_SOURCES:
            msg = f"Invalid auth_source: {auth_source}"
            raise ValueError(msg)
        with self._lock:
            conn = self._connect()
            try:
                cursor = conn.execute(
                    """INSERT INTO users (
                           username, email, display_name, password_hash, oidc_subject, auth_source, role
                       )
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        username,
                        email,
                        display_name,
                        password_hash,
                        oidc_subject,
                        auth_source,
                        role,
                    ),
                )
                conn.commit()
                user_id = cursor.lastrowid
                if not isinstance(user_id, int):
                    msg = "Failed to create user"
                    raise TypeError(msg)
                created_user = self._get_user_by_id(conn, user_id)
                return _require_loaded_user(created_user)
            except sqlite3.IntegrityError as e:
                msg = f"User already exists: {e}"
                raise ValueError(msg) from e
            finally:
                conn.close()

    def get_user(
        self,
        user_id: int | None = None,
        username: str | None = None,
        oidc_subject: str | None = None,
    ) -> dict[str, Any] | None:
        """Get a user by id, username, or oidc_subject. Returns None if not found."""
        conn = self._connect()
        try:
            if user_id is not None:
                return self._get_user_by_id(conn, user_id)
            if username is not None:
                row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
            elif oidc_subject is not None:
                row = conn.execute(
                    "SELECT * FROM users WHERE oidc_subject = ?", (oidc_subject,)
                ).fetchone()
            else:
                return None
            return dict(row) if row else None
        finally:
            conn.close()

    def _get_user_by_id(self, conn: sqlite3.Connection, user_id: int) -> dict[str, Any] | None:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None

    _ALLOWED_UPDATE_COLUMNS: ClassVar[frozenset[str]] = frozenset(
        {
            "username",
            "email",
            "display_name",
            "password_hash",
            "oidc_subject",
            "auth_source",
            "role",
        }
    )
    _USER_UPDATE_STATEMENTS: ClassVar[dict[str, str]] = {
        "username": "UPDATE users SET username = ? WHERE id = ?",
        "email": "UPDATE users SET email = ? WHERE id = ?",
        "display_name": "UPDATE users SET display_name = ? WHERE id = ?",
        "password_hash": "UPDATE users SET password_hash = ? WHERE id = ?",
        "oidc_subject": "UPDATE users SET oidc_subject = ? WHERE id = ?",
        "auth_source": "UPDATE users SET auth_source = ? WHERE id = ?",
        "role": "UPDATE users SET role = ? WHERE id = ?",
    }

    def update_user(self, user_id: int, **kwargs: object) -> None:
        """Update user fields. Raises ValueError if user not found or invalid column."""
        if not kwargs:
            return
        for k in kwargs:
            if k not in self._ALLOWED_UPDATE_COLUMNS:
                msg = f"Invalid column: {k}"
                raise ValueError(msg)
        if "auth_source" in kwargs and kwargs["auth_source"] not in self._VALID_AUTH_SOURCES:
            msg = f"Invalid auth_source: {kwargs['auth_source']}"
            raise ValueError(msg)
        with self._lock:
            conn = self._connect()
            try:
                # Verify user exists
                if not self._get_user_by_id(conn, user_id):
                    msg = f"User {user_id} not found"
                    raise ValueError(msg)
                for column, value in kwargs.items():
                    conn.execute(self._USER_UPDATE_STATEMENTS[column], (value, user_id))
                conn.commit()
            finally:
                conn.close()

    def delete_user(self, user_id: int) -> None:
        """Delete a user and their settings."""
        with self._lock:
            conn = self._connect()
            try:
                request_rows = conn.execute(
                    "SELECT id FROM download_requests WHERE user_id = ?",
                    (user_id,),
                ).fetchall()
                request_item_keys = [f"request:{row['id']}" for row in request_rows]
                if request_item_keys:
                    conn.executemany(
                        "DELETE FROM activity_view_state WHERE item_type = 'request' AND item_key = ?",
                        [(item_key,) for item_key in request_item_keys],
                    )
                conn.execute(
                    "DELETE FROM activity_view_state WHERE viewer_scope = ?",
                    (user_viewer_scope(user_id),),
                )
                conn.execute(
                    "UPDATE download_requests SET reviewed_by = NULL WHERE reviewed_by = ?",
                    (user_id,),
                )
                conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
                conn.commit()
            finally:
                conn.close()

    def list_users(self) -> list[dict[str, Any]]:
        """List all users."""
        conn = self._connect()
        try:
            rows = conn.execute("SELECT * FROM users ORDER BY id").fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_first_admin(self) -> dict[str, Any] | None:
        """Return the lowest-id admin user, or None. Used as the identity for SHELFMARK_API_KEY requests."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM users WHERE role = 'admin' ORDER BY id LIMIT 1"
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def has_admin(self) -> bool:
        """Return True when at least one admin user exists."""
        conn = self._connect()
        try:
            row = conn.execute("SELECT 1 FROM users WHERE role = 'admin' LIMIT 1").fetchone()
            return row is not None
        finally:
            conn.close()

    def has_admin_with_password(self) -> bool:
        """Return True when at least one admin user with a password hash exists."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT 1 FROM users WHERE role = 'admin'"
                " AND password_hash IS NOT NULL AND password_hash != ''"
                " LIMIT 1",
            ).fetchone()
            return row is not None
        finally:
            conn.close()

    def get_user_settings(self, user_id: int) -> dict[str, Any]:
        """Get per-user settings. Returns empty dict if none set."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT settings_json FROM user_settings WHERE user_id = ?", (user_id,)
            ).fetchone()
            if row:
                return json.loads(row["settings_json"])
            return {}
        finally:
            conn.close()

    def set_user_settings(self, user_id: int, settings: dict[str, Any]) -> None:
        """Merge settings into user's existing settings."""
        with self._lock:
            conn = self._connect()
            try:
                existing = {}
                row = conn.execute(
                    "SELECT settings_json FROM user_settings WHERE user_id = ?", (user_id,)
                ).fetchone()
                if row:
                    existing = json.loads(row["settings_json"])

                existing.update(settings)
                # Remove keys set to None (meaning "clear this override")
                existing = {k: v for k, v in existing.items() if v is not None}
                settings_json = json.dumps(existing)

                conn.execute(
                    """INSERT INTO user_settings (user_id, settings_json) VALUES (?, ?)
                       ON CONFLICT(user_id) DO UPDATE SET settings_json = ?""",
                    (user_id, settings_json, settings_json),
                )
                conn.commit()
            finally:
                conn.close()

    @staticmethod
    def _serialize_json(value: Any, field: str) -> str | None:
        if value is None:
            return None
        try:
            return json.dumps(value)
        except TypeError as exc:
            msg = f"{field} must be JSON-serializable"
            raise ValueError(msg) from exc

    @staticmethod
    def _parse_request_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None

        payload = dict(row)
        for key in ("book_data", "release_data"):
            raw_value = payload.get(key)
            if raw_value is None:
                payload[key] = None
                continue
            try:
                payload[key] = json.loads(raw_value)
            except ValueError, TypeError:
                payload[key] = None
        return payload

    def _insert_request(
        self,
        conn: sqlite3.Connection,
        *,
        user_id: int,
        content_type: str,
        request_level: str,
        policy_mode: str,
        book_data: dict[str, Any],
        release_data: dict[str, Any] | None = None,
        status: str = RequestStatus.PENDING,
        source_hint: str | None = None,
        note: str | None = None,
        admin_note: str | None = None,
        reviewed_by: int | None = None,
        reviewed_at: str | None = None,
        delivery_state: str = DELIVERY_STATE_NONE,
        delivery_updated_at: str | None = None,
    ) -> dict[str, Any]:
        cursor = conn.execute(
            """
            INSERT INTO download_requests (
                user_id,
                status,
                delivery_state,
                source_hint,
                content_type,
                request_level,
                policy_mode,
                book_data,
                release_data,
                note,
                admin_note,
                reviewed_by,
                reviewed_at,
                delivery_updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                status,
                delivery_state,
                source_hint,
                content_type,
                request_level,
                policy_mode,
                self._serialize_json(book_data, "book_data"),
                self._serialize_json(release_data, "release_data"),
                note,
                admin_note,
                reviewed_by,
                reviewed_at,
                delivery_updated_at,
            ),
        )
        request_id = cursor.lastrowid
        row = conn.execute(
            "SELECT * FROM download_requests WHERE id = ?",
            (request_id,),
        ).fetchone()
        parsed = self._parse_request_row(row)
        if parsed is None:
            msg = f"Request {request_id} not found after creation"
            raise ValueError(msg)
        return parsed

    def create_request(
        self,
        *,
        user_id: int,
        content_type: str,
        request_level: str,
        policy_mode: str,
        book_data: dict[str, Any],
        release_data: dict[str, Any] | None = None,
        status: str = RequestStatus.PENDING,
        source_hint: str | None = None,
        note: str | None = None,
        admin_note: str | None = None,
        reviewed_by: int | None = None,
        reviewed_at: str | None = None,
        delivery_state: str = DELIVERY_STATE_NONE,
        delivery_updated_at: str | None = None,
    ) -> dict[str, Any]:
        """Create a download request row and return the created record."""
        if not isinstance(book_data, dict):
            msg = "book_data must be an object"
            raise TypeError(msg)
        if release_data is not None and not isinstance(release_data, dict):
            msg = "release_data must be an object when provided"
            raise TypeError(msg)
        if not content_type:
            msg = "content_type is required"
            raise ValueError(msg)

        normalized_status = normalize_request_status(status)
        normalized_delivery_state = normalize_delivery_state(delivery_state)
        normalized_policy_mode = normalize_policy_mode(policy_mode)
        normalized_request_level = validate_request_level_payload(request_level, release_data)

        with self._lock:
            conn = self._connect()
            try:
                created = self._insert_request(
                    conn,
                    user_id=user_id,
                    content_type=content_type,
                    request_level=normalized_request_level,
                    policy_mode=normalized_policy_mode,
                    book_data=book_data,
                    release_data=release_data,
                    status=normalized_status,
                    source_hint=source_hint,
                    note=note,
                    admin_note=admin_note,
                    reviewed_by=reviewed_by,
                    reviewed_at=reviewed_at,
                    delivery_state=normalized_delivery_state,
                    delivery_updated_at=delivery_updated_at,
                )
                conn.commit()
                return created
            finally:
                conn.close()

    def create_requests(self, requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Create multiple request rows atomically and return them in input order."""
        with self._lock:
            conn = self._connect()
            try:
                created = [self._insert_request(conn, **request) for request in requests]
                conn.commit()
                return created
            finally:
                conn.close()

    def get_request(self, request_id: int) -> dict[str, Any] | None:
        """Get a request row by ID."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM download_requests WHERE id = ?",
                (request_id,),
            ).fetchone()
            return self._parse_request_row(row)
        finally:
            conn.close()

    def list_requests(
        self,
        *,
        user_id: int | None = None,
        status: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List requests with optional user/status filters."""
        where_clauses: list[str] = []
        params: list[Any] = []

        if user_id is not None:
            where_clauses.append("user_id = ?")
            params.append(user_id)

        if status is not None:
            where_clauses.append("status = ?")
            params.append(normalize_request_status(status))

        query = "SELECT * FROM download_requests"
        if where_clauses:
            query += " WHERE " + " AND ".join(where_clauses)
        query += " ORDER BY created_at DESC, id DESC"

        if limit is not None:
            query += " LIMIT ?"
            params.append(int(limit))
            if offset:
                query += " OFFSET ?"
                params.append(offset)
        elif offset:
            query += " LIMIT -1 OFFSET ?"
            params.append(offset)

        conn = self._connect()
        try:
            rows = conn.execute(query, params).fetchall()
            results: list[dict[str, Any]] = []
            for row in rows:
                parsed = self._parse_request_row(row)
                if parsed is not None:
                    results.append(parsed)
            return results
        finally:
            conn.close()

    _ALLOWED_REQUEST_UPDATE_COLUMNS: ClassVar[frozenset[str]] = frozenset(
        {
            "status",
            "source_hint",
            "content_type",
            "request_level",
            "policy_mode",
            "book_data",
            "release_data",
            "note",
            "admin_note",
            "reviewed_by",
            "reviewed_at",
            "delivery_state",
            "delivery_updated_at",
            "last_failure_reason",
        }
    )
    _REQUEST_UPDATE_STATEMENTS: ClassVar[dict[str, str]] = {
        "status": "UPDATE download_requests SET status = ? WHERE id = ?",
        "source_hint": "UPDATE download_requests SET source_hint = ? WHERE id = ?",
        "content_type": "UPDATE download_requests SET content_type = ? WHERE id = ?",
        "request_level": "UPDATE download_requests SET request_level = ? WHERE id = ?",
        "policy_mode": "UPDATE download_requests SET policy_mode = ? WHERE id = ?",
        "book_data": "UPDATE download_requests SET book_data = ? WHERE id = ?",
        "release_data": "UPDATE download_requests SET release_data = ? WHERE id = ?",
        "note": "UPDATE download_requests SET note = ? WHERE id = ?",
        "admin_note": "UPDATE download_requests SET admin_note = ? WHERE id = ?",
        "reviewed_by": "UPDATE download_requests SET reviewed_by = ? WHERE id = ?",
        "reviewed_at": "UPDATE download_requests SET reviewed_at = ? WHERE id = ?",
        "delivery_state": "UPDATE download_requests SET delivery_state = ? WHERE id = ?",
        "delivery_updated_at": "UPDATE download_requests SET delivery_updated_at = ? WHERE id = ?",
        "last_failure_reason": "UPDATE download_requests SET last_failure_reason = ? WHERE id = ?",
    }

    def update_request(
        self,
        request_id: int,
        expected_current_status: str | None = None,
        **kwargs: object,
    ) -> dict[str, Any]:
        """Update request fields and return the updated record."""
        if not kwargs:
            request = self.get_request(request_id)
            if request is None:
                msg = f"Request {request_id} not found"
                raise ValueError(msg)
            if expected_current_status is not None:
                normalized_expected_status = normalize_request_status(expected_current_status)
                if request["status"] != normalized_expected_status:
                    msg = "Request state changed before update"
                    raise ValueError(msg)
            return request

        for key in kwargs:
            if key not in self._ALLOWED_REQUEST_UPDATE_COLUMNS:
                msg = f"Invalid request column: {key}"
                raise ValueError(msg)

        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM download_requests WHERE id = ?",
                    (request_id,),
                ).fetchone()
                current = self._parse_request_row(row)
                if current is None:
                    msg = f"Request {request_id} not found"
                    raise ValueError(msg)

                if expected_current_status is not None:
                    normalized_expected_status = normalize_request_status(expected_current_status)
                    if current["status"] != normalized_expected_status:
                        msg = "Request state changed before update"
                        raise ValueError(msg)

                updates = dict(kwargs)

                if "status" in updates:
                    _, normalized_status = validate_status_transition(
                        current["status"],
                        updates["status"],
                    )
                    updates["status"] = normalized_status

                if "policy_mode" in updates:
                    updates["policy_mode"] = normalize_policy_mode(updates["policy_mode"])

                if "delivery_state" in updates:
                    updates["delivery_state"] = normalize_delivery_state(updates["delivery_state"])

                if "delivery_updated_at" in updates:
                    delivery_updated_at = updates["delivery_updated_at"]
                    if delivery_updated_at is not None and not isinstance(delivery_updated_at, str):
                        msg = "delivery_updated_at must be a string when provided"
                        raise TypeError(msg)

                if "content_type" in updates and not updates["content_type"]:
                    msg = "content_type is required"
                    raise ValueError(msg)

                if "request_level" in updates:
                    updates["request_level"] = normalize_request_level(updates["request_level"])

                if "book_data" in updates:
                    if not isinstance(updates["book_data"], dict):
                        msg = "book_data must be an object"
                        raise TypeError(msg)
                    updates["book_data"] = self._serialize_json(updates["book_data"], "book_data")

                if "release_data" in updates:
                    if updates["release_data"] is not None and not isinstance(
                        updates["release_data"], dict
                    ):
                        msg = "release_data must be an object when provided"
                        raise TypeError(msg)
                    updates["release_data"] = self._serialize_json(
                        updates["release_data"],
                        "release_data",
                    )

                for column, value in updates.items():
                    conn.execute(self._REQUEST_UPDATE_STATEMENTS[column], (value, request_id))
                conn.commit()

                updated_row = conn.execute(
                    "SELECT * FROM download_requests WHERE id = ?",
                    (request_id,),
                ).fetchone()
                parsed = self._parse_request_row(updated_row)
                if parsed is None:
                    msg = f"Request {request_id} not found after update"
                    raise ValueError(msg)
                return parsed
            finally:
                conn.close()

    def reopen_failed_request(
        self,
        request_id: int,
        *,
        failure_reason: str | None = None,
    ) -> dict[str, Any] | None:
        """Reopen a failed fulfilled request so admins can re-approve it."""
        normalized_failure_reason = None
        if isinstance(failure_reason, str):
            normalized_failure_reason = failure_reason.strip() or None

        with self._lock:
            conn = self._connect()
            try:
                current_row = conn.execute(
                    "SELECT * FROM download_requests WHERE id = ?",
                    (request_id,),
                ).fetchone()
                current_request = self._parse_request_row(current_row)
                if current_request is None:
                    return None

                if current_request.get("status") != RequestStatus.FULFILLED:
                    return None

                current_delivery_state = current_request.get("delivery_state", DELIVERY_STATE_NONE)

                # Terminal hook callbacks can run before delivery-state sync persists "error".
                # Allow reopening fulfilled requests unless they are already complete.
                if current_delivery_state == QueueStatus.COMPLETE:
                    return None
                if (
                    current_delivery_state not in {QueueStatus.ERROR, QueueStatus.CANCELLED}
                    and normalized_failure_reason is None
                ):
                    return None

                conn.execute(
                    """
                    UPDATE download_requests
                    SET status = 'pending',
                        delivery_state = 'none',
                        delivery_updated_at = NULL,
                        release_data = NULL,
                        last_failure_reason = ?,
                        reviewed_by = NULL,
                        reviewed_at = NULL
                    WHERE id = ?
                    """,
                    (normalized_failure_reason, request_id),
                )
                updated_row = conn.execute(
                    "SELECT * FROM download_requests WHERE id = ?",
                    (request_id,),
                ).fetchone()
                conn.commit()
                return self._parse_request_row(updated_row)
            finally:
                conn.close()

    def rollback_request_fulfilment(
        self,
        request_id: int,
        *,
        release_data: dict[str, Any] | None,
        last_failure_reason: str | None = None,
    ) -> dict[str, Any]:
        """Restore a request to pending after fulfilment claimed it but queueing failed."""
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM download_requests WHERE id = ?",
                    (request_id,),
                ).fetchone()
                current = self._parse_request_row(row)
                if current is None:
                    msg = f"Request {request_id} not found"
                    raise ValueError(msg)

                conn.execute(
                    """
                    UPDATE download_requests
                    SET status = 'pending',
                        release_data = ?,
                        admin_note = NULL,
                        reviewed_by = NULL,
                        reviewed_at = NULL,
                        delivery_state = 'none',
                        delivery_updated_at = NULL,
                        last_failure_reason = ?
                    WHERE id = ?
                    """,
                    (
                        self._serialize_json(release_data, "release_data"),
                        last_failure_reason,
                        request_id,
                    ),
                )
                updated_row = conn.execute(
                    "SELECT * FROM download_requests WHERE id = ?",
                    (request_id,),
                ).fetchone()
                conn.commit()
                parsed = self._parse_request_row(updated_row)
                if parsed is None:
                    msg = f"Request {request_id} not found after rollback"
                    raise ValueError(msg)
                return parsed
            finally:
                conn.close()

    def count_pending_requests(self) -> int:
        """Count all pending requests."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM download_requests WHERE status = 'pending'"
            ).fetchone()
            return int(row["count"]) if row else 0
        finally:
            conn.close()

    def count_user_pending_requests(self, user_id: int) -> int:
        """Count pending requests for a specific user."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM download_requests WHERE user_id = ? AND status = 'pending'",
                (user_id,),
            ).fetchone()
            return int(row["count"]) if row else 0
        finally:
            conn.close()
