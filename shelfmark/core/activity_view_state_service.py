"""Persistence helpers for per-viewer activity visibility state."""

from __future__ import annotations

import sqlite3
import threading
from typing import Any

from shelfmark.core.request_helpers import now_utc_iso
from shelfmark.core.utils import DB_BUSY_TIMEOUT_SECONDS

VALID_ACTIVITY_ITEM_TYPES = frozenset({"download", "request"})
ADMIN_VIEWER_SCOPE = "admin:shared"
NOAUTH_VIEWER_SCOPE = "noauth:shared"
USER_VIEWER_SCOPE_PREFIX = "user:"


def user_viewer_scope(user_id: int) -> str:
    """Build the persisted viewer scope string for a specific user."""
    if not isinstance(user_id, int) or user_id < 1:
        msg = "user_id must be a positive integer"
        raise ValueError(msg)
    return f"{USER_VIEWER_SCOPE_PREFIX}{user_id}"


def normalize_viewer_scope(viewer_scope: object) -> str:
    """Validate and normalize a persisted viewer scope string."""
    if not isinstance(viewer_scope, str) or not viewer_scope.strip():
        msg = "viewer_scope must be a non-empty string"
        raise ValueError(msg)

    normalized = viewer_scope.strip()
    if normalized in {ADMIN_VIEWER_SCOPE, NOAUTH_VIEWER_SCOPE}:
        return normalized

    if not normalized.startswith(USER_VIEWER_SCOPE_PREFIX):
        msg = "viewer_scope must be one of: admin:shared, noauth:shared, or user:<id>"
        raise ValueError(msg)

    raw_user_id = normalized[len(USER_VIEWER_SCOPE_PREFIX) :].strip()
    try:
        parsed_user_id = int(raw_user_id)
    except (TypeError, ValueError) as exc:
        msg = "viewer_scope user id must be a positive integer"
        raise ValueError(msg) from exc

    return user_viewer_scope(parsed_user_id)


def _normalize_item_type(item_type: object) -> str:
    if not isinstance(item_type, str) or not item_type.strip():
        msg = "item_type must be a non-empty string"
        raise ValueError(msg)
    normalized = item_type.strip().lower()
    if normalized not in VALID_ACTIVITY_ITEM_TYPES:
        msg = "item_type must be one of: download, request"
        raise ValueError(msg)
    return normalized


def _normalize_item_key(item_key: object, *, item_type: str) -> str:
    if not isinstance(item_key, str) or not item_key.strip():
        msg = "item_key must be a non-empty string"
        raise ValueError(msg)

    normalized = item_key.strip()
    expected_prefix = f"{item_type}:"
    if not normalized.startswith(expected_prefix):
        msg_0 = f"item_key must be in the format {expected_prefix}<id>"
        raise ValueError(msg_0)
    if not normalized.split(":", 1)[1].strip():
        msg_0 = f"item_key must be in the format {expected_prefix}<id>"
        raise ValueError(msg_0)
    return normalized


class ActivityViewStateService:
    """Service for per-viewer activity dismissal and history visibility."""

    def __init__(self, db_path: str) -> None:
        """Initialize the service with the SQLite state database path."""
        self._db_path = db_path
        self._lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=DB_BUSY_TIMEOUT_SECONDS)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def list_hidden(
        self,
        *,
        viewer_scope: str,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return dismissed rows for a viewer, including cleared history entries."""
        normalized_scope = normalize_viewer_scope(viewer_scope)
        normalized_limit = None if limit is None else max(1, int(limit))
        query = """
            SELECT item_type, item_key, dismissed_at, cleared_at
            FROM activity_view_state
            WHERE viewer_scope = ?
              AND dismissed_at IS NOT NULL
            ORDER BY COALESCE(cleared_at, dismissed_at) DESC, id DESC
        """
        params: list[Any] = [normalized_scope]
        if normalized_limit is not None:
            query += "\nLIMIT ?"
            params.append(normalized_limit)

        conn = self._connect()
        try:
            rows = conn.execute(query, params).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def list_history(
        self,
        *,
        viewer_scope: str,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Return active dismissal history rows for a viewer."""
        normalized_scope = normalize_viewer_scope(viewer_scope)
        normalized_limit = max(1, min(int(limit), 5000))
        normalized_offset = max(0, int(offset))

        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT item_type, item_key, dismissed_at
                FROM activity_view_state
                WHERE viewer_scope = ?
                  AND dismissed_at IS NOT NULL
                  AND cleared_at IS NULL
                ORDER BY dismissed_at DESC, id DESC
                LIMIT ? OFFSET ?
                """,
                (normalized_scope, normalized_limit, normalized_offset),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def dismiss(
        self,
        *,
        viewer_scope: str,
        item_type: str,
        item_key: str,
    ) -> int:
        """Mark a single activity item as dismissed for a viewer."""
        normalized_scope = normalize_viewer_scope(viewer_scope)
        normalized_type = _normalize_item_type(item_type)
        normalized_key = _normalize_item_key(item_key, item_type=normalized_type)
        dismissed_at = now_utc_iso()

        with self._lock:
            conn = self._connect()
            try:
                cursor = conn.execute(
                    """
                    INSERT INTO activity_view_state (
                        viewer_scope,
                        item_type,
                        item_key,
                        dismissed_at,
                        cleared_at
                    )
                    VALUES (?, ?, ?, ?, NULL)
                    ON CONFLICT(viewer_scope, item_type, item_key) DO UPDATE SET
                        dismissed_at = excluded.dismissed_at,
                        cleared_at = NULL
                    """,
                    (normalized_scope, normalized_type, normalized_key, dismissed_at),
                )
                conn.commit()
                rowcount = int(cursor.rowcount) if cursor.rowcount is not None else 0
                return max(rowcount, 0)
            finally:
                conn.close()

    def dismiss_many(
        self,
        *,
        viewer_scope: str,
        items: list[dict[str, str]],
    ) -> int:
        """Mark multiple activity items as dismissed for a viewer."""
        normalized_scope = normalize_viewer_scope(viewer_scope)
        if not items:
            return 0

        seen: set[tuple[str, str]] = set()
        normalized_items: list[tuple[str, str]] = []
        for item in items:
            normalized_type = _normalize_item_type(item.get("item_type"))
            normalized_key = _normalize_item_key(item.get("item_key"), item_type=normalized_type)
            marker = (normalized_type, normalized_key)
            if marker in seen:
                continue
            seen.add(marker)
            normalized_items.append(marker)

        if not normalized_items:
            return 0

        dismissed_at = now_utc_iso()
        with self._lock:
            conn = self._connect()
            try:
                total = 0
                for normalized_type, normalized_key in normalized_items:
                    cursor = conn.execute(
                        """
                        INSERT INTO activity_view_state (
                            viewer_scope,
                            item_type,
                            item_key,
                            dismissed_at,
                            cleared_at
                        )
                        VALUES (?, ?, ?, ?, NULL)
                        ON CONFLICT(viewer_scope, item_type, item_key) DO UPDATE SET
                            dismissed_at = excluded.dismissed_at,
                            cleared_at = NULL
                        """,
                        (
                            normalized_scope,
                            normalized_type,
                            normalized_key,
                            dismissed_at,
                        ),
                    )
                    rowcount = int(cursor.rowcount) if cursor.rowcount is not None else 0
                    total += max(rowcount, 0)
                conn.commit()
                return total
            finally:
                conn.close()

    def clear_history(self, *, viewer_scope: str) -> int:
        """Mark all dismissed items as cleared for a viewer."""
        normalized_scope = normalize_viewer_scope(viewer_scope)
        cleared_at = now_utc_iso()

        with self._lock:
            conn = self._connect()
            try:
                cursor = conn.execute(
                    """
                    UPDATE activity_view_state
                    SET cleared_at = ?
                    WHERE viewer_scope = ?
                      AND dismissed_at IS NOT NULL
                      AND cleared_at IS NULL
                    """,
                    (cleared_at, normalized_scope),
                )
                conn.commit()
                rowcount = int(cursor.rowcount) if cursor.rowcount is not None else 0
                return max(rowcount, 0)
            finally:
                conn.close()

    def clear_item_for_all_viewers(self, *, item_type: str, item_key: str) -> int:
        """Delete a dismissed item record for every viewer."""
        normalized_type = _normalize_item_type(item_type)
        normalized_key = _normalize_item_key(item_key, item_type=normalized_type)

        with self._lock:
            conn = self._connect()
            try:
                cursor = conn.execute(
                    """
                    DELETE FROM activity_view_state
                    WHERE item_type = ? AND item_key = ?
                    """,
                    (normalized_type, normalized_key),
                )
                conn.commit()
                rowcount = int(cursor.rowcount) if cursor.rowcount is not None else 0
                return max(rowcount, 0)
            finally:
                conn.close()

    def delete_viewer_scope(self, *, viewer_scope: str) -> int:
        """Delete all activity-view state rows for a viewer scope."""
        normalized_scope = normalize_viewer_scope(viewer_scope)

        with self._lock:
            conn = self._connect()
            try:
                cursor = conn.execute(
                    "DELETE FROM activity_view_state WHERE viewer_scope = ?",
                    (normalized_scope,),
                )
                conn.commit()
                rowcount = int(cursor.rowcount) if cursor.rowcount is not None else 0
                return max(rowcount, 0)
            finally:
                conn.close()

    def delete_items(self, *, item_type: str, item_keys: list[str]) -> int:
        """Delete multiple dismissed item records for a given item type."""
        normalized_type = _normalize_item_type(item_type)
        normalized_keys = [
            _normalize_item_key(item_key, item_type=normalized_type) for item_key in item_keys
        ]
        if not normalized_keys:
            return 0

        with self._lock:
            conn = self._connect()
            try:
                cursor = conn.executemany(
                    "DELETE FROM activity_view_state WHERE item_type = ? AND item_key = ?",
                    [(normalized_type, normalized_key) for normalized_key in normalized_keys],
                )
                conn.commit()
                rowcount = int(cursor.rowcount) if cursor.rowcount is not None else 0
                return max(rowcount, 0)
            finally:
                conn.close()
