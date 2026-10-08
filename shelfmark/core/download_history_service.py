"""Persistence helpers for canonical download activity rows."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, SupportsIndex, SupportsInt, TypeGuard

from shelfmark.core.logger import setup_logger
from shelfmark.core.models import TERMINAL_QUEUE_STATUSES
from shelfmark.core.request_helpers import (
    normalize_optional_positive_int,
    normalize_optional_text,
    now_utc_iso,
)

logger = setup_logger(__name__)


VALID_TERMINAL_STATUSES = frozenset(s.value for s in TERMINAL_QUEUE_STATUSES)
ACTIVE_DOWNLOAD_STATUS = "active"
VALID_ORIGINS = frozenset({"direct", "requested"})


def _is_convertible_to_int(
    value: object,
) -> TypeGuard[str | bytes | bytearray | SupportsInt | SupportsIndex]:
    """Return True when *value* can be safely passed to ``int``."""
    return (
        isinstance(value, (str, bytes, bytearray))
        or hasattr(value, "__int__")
        or hasattr(value, "__index__")
    )


def _coerce_int_value(value: object) -> int:
    """Normalize int-like values and raise TypeError for unsupported inputs."""
    if isinstance(value, bool) or not _is_convertible_to_int(value):
        msg = "limit must be an integer"
        raise TypeError(msg)
    return int(value)


def _normalize_task_id(task_id: object) -> str:
    normalized = normalize_optional_text(task_id)
    if normalized is None:
        msg = "task_id must be a non-empty string"
        raise ValueError(msg)
    return normalized


def _normalize_origin(origin: object) -> str:
    normalized = normalize_optional_text(origin)
    if normalized is None:
        return "direct"
    lowered = normalized.lower()
    if lowered not in VALID_ORIGINS:
        msg = "origin must be one of: direct, requested"
        raise ValueError(msg)
    return lowered


def _normalize_final_status(final_status: object) -> str:
    normalized = normalize_optional_text(final_status)
    if normalized is None:
        msg = "final_status must be a non-empty string"
        raise ValueError(msg)
    lowered = normalized.lower()
    if lowered not in VALID_TERMINAL_STATUSES:
        msg = "final_status must be one of: complete, error, cancelled"
        raise ValueError(msg)
    return lowered


def _normalize_limit(value: object, *, default: int, minimum: int, maximum: int) -> int:
    if value is None:
        return default
    try:
        parsed = _coerce_int_value(value)
    except (TypeError, ValueError) as exc:
        msg = "limit must be an integer"
        raise ValueError(msg) from exc
    if parsed < minimum:
        return minimum
    if parsed > maximum:
        return maximum
    return parsed


class DownloadHistoryService:
    """Service for persisted canonical download activity rows."""

    def __init__(self, db_path: str) -> None:
        """Initialize the service with the SQLite history database path."""
        self._db_path = db_path
        self._lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @classmethod
    def _normalize_row_dict(cls, row: dict[str, Any] | None) -> dict[str, Any] | None:
        if row is None:
            return None
        normalized = dict(row)
        normalized["retry_payload"] = cls._deserialize_retry_payload(
            normalized.get("retry_payload")
        )
        return normalized

    @classmethod
    def _row_to_dict(cls, row: sqlite3.Row | None) -> dict[str, Any] | None:
        return cls._normalize_row_dict(dict(row) if row is not None else None)

    @staticmethod
    def _to_item_key(task_id: str) -> str:
        return f"download:{task_id}"

    @staticmethod
    def _resolve_existing_download_path(value: object) -> str | None:
        normalized = normalize_optional_text(value)
        if normalized is None:
            return None
        return normalized if Path(normalized).exists() else None

    @staticmethod
    def _serialize_retry_payload(payload: object) -> str | None:
        if payload is None:
            return None
        try:
            return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            msg = "retry_payload must be JSON-serializable"
            raise ValueError(msg) from exc

    @staticmethod
    def _deserialize_retry_payload(value: object) -> dict[str, Any] | None:
        if isinstance(value, dict):
            return dict(value)
        normalized = normalize_optional_text(value)
        if normalized is None:
            return None
        try:
            parsed = json.loads(normalized)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None

    @staticmethod
    def _has_staged_retry_source(retry_payload: dict[str, Any]) -> bool:
        staged_path = retry_payload.get("staged_path")
        normalized_staged_path = normalize_optional_text(staged_path)
        if normalized_staged_path is None:
            return False
        return Path(normalized_staged_path).exists()

    @staticmethod
    def _can_retry_without_staged_source(retry_payload: dict[str, Any]) -> bool:
        return bool(retry_payload.get("can_retry_without_staged_source", True))

    @staticmethod
    def is_retry_available(row: dict[str, Any]) -> bool:
        """Return whether a persisted download row can be retried."""
        final_status = (
            str(row.get("retry_final_status") or row.get("final_status") or "").strip().lower()
        )
        retry_payload = DownloadHistoryService._deserialize_retry_payload(row.get("retry_payload"))
        if retry_payload is None:
            return False

        has_staged_retry_source = DownloadHistoryService._has_staged_retry_source(retry_payload)
        can_retry_without_staged_source = DownloadHistoryService._can_retry_without_staged_source(
            retry_payload
        )
        request_id = normalize_optional_positive_int(row.get("request_id"), "request_id")
        if request_id is None:
            if final_status in {ACTIVE_DOWNLOAD_STATUS, "cancelled"}:
                return can_retry_without_staged_source
            if final_status == "error":
                return has_staged_retry_source or can_retry_without_staged_source
            return False

        if final_status in {ACTIVE_DOWNLOAD_STATUS, "cancelled"}:
            return can_retry_without_staged_source

        if final_status != "error":
            return False

        return has_staged_retry_source

    @staticmethod
    def to_download_payload(row: dict[str, Any]) -> dict[str, Any]:
        """Build the sidebar/history download payload for a persisted row."""
        return {
            "id": row.get("task_id"),
            "title": row.get("title"),
            "author": row.get("author"),
            "format": row.get("format"),
            "size": row.get("size"),
            "preview": row.get("preview"),
            "content_type": row.get("content_type"),
            "source": row.get("source"),
            "source_display_name": row.get("source_display_name"),
            "downloads": row.get("downloads"),
            "status_message": row.get("status_message"),
            "download_path": DownloadHistoryService._resolve_existing_download_path(
                row.get("download_path")
            ),
            "added_time": DownloadHistoryService._iso_to_epoch(row.get("queued_at")),
            "user_id": row.get("user_id"),
            "username": row.get("username"),
            "request_id": row.get("request_id"),
            "retry_available": DownloadHistoryService.is_retry_available(row),
        }

    @staticmethod
    def _iso_to_epoch(value: object) -> float | None:
        if not isinstance(value, str) or not value.strip():
            return None
        normalized = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.timestamp()

    @classmethod
    def to_history_row(cls, row: dict[str, Any], *, dismissed_at: str) -> dict[str, Any]:
        """Build the activity-history payload for a persisted download row."""
        task_id = str(row.get("task_id") or "").strip()
        item_key = cls._to_item_key(task_id)
        download_payload = cls.to_download_payload(row)
        # Clear stale progress messages for non-error terminal states.
        if row.get("final_status") in ("complete", "cancelled"):
            download_payload["status_message"] = None
        return {
            "id": item_key,
            "user_id": row.get("user_id"),
            "item_type": "download",
            "item_key": item_key,
            "dismissed_at": dismissed_at,
            "snapshot": {
                "kind": "download",
                "download": download_payload,
            },
            "origin": row.get("origin"),
            "final_status": row.get("final_status"),
            "terminal_at": row.get("terminal_at"),
            "request_id": row.get("request_id"),
            "source_id": task_id or None,
        }

    def record_download(
        self,
        *,
        task_id: str,
        user_id: int | None,
        username: str | None,
        request_id: int | None,
        source: str,
        source_display_name: str | None,
        title: str,
        author: str | None,
        file_format: str | None,
        size: str | None,
        preview: str | None,
        content_type: str | None,
        downloads: int | None,
        origin: str,
        retry_payload: dict[str, Any] | None = None,
    ) -> None:
        """Record a download at queue time with final_status='active'.

        On first queue: inserts a new row.
        On retry (row already exists): resets the row back to 'active'
        so the normal finalize path works when the retry completes.
        """
        normalized_task_id = _normalize_task_id(task_id)
        normalized_user_id = normalize_optional_positive_int(user_id, "user_id")
        normalized_request_id = normalize_optional_positive_int(request_id, "request_id")
        normalized_source = normalize_optional_text(source)
        if normalized_source is None:
            msg = "source must be a non-empty string"
            raise ValueError(msg)
        normalized_title = normalize_optional_text(title)
        if normalized_title is None:
            msg = "title must be a non-empty string"
            raise ValueError(msg)
        normalized_origin = _normalize_origin(origin)
        normalized_retry_payload = self._serialize_retry_payload(retry_payload)
        recorded_at = now_utc_iso()

        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                INSERT INTO download_history (
                    task_id, user_id, username, request_id,
                    source, source_display_name,
                    title, author, format, size, preview, content_type,
                    origin, final_status,
                    status_message, download_path, retry_payload,
                    queued_at, terminal_at, downloads
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', NULL, NULL, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    final_status = 'active',
                    status_message = NULL,
                    download_path = NULL,
                    retry_payload = excluded.retry_payload,
                    terminal_at = ?,
                    downloads = excluded.downloads
                """,
                    (
                        normalized_task_id,
                        normalized_user_id,
                        normalize_optional_text(username),
                        normalized_request_id,
                        normalized_source,
                        normalize_optional_text(source_display_name),
                        normalized_title,
                        normalize_optional_text(author),
                        normalize_optional_text(file_format),
                        normalize_optional_text(size),
                        normalize_optional_text(preview),
                        normalize_optional_text(content_type),
                        normalized_origin,
                        normalized_retry_payload,
                        recorded_at,
                        recorded_at,
                        downloads,
                        recorded_at,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def finalize_download(
        self,
        *,
        task_id: str,
        final_status: str,
        status_message: str | None = None,
        download_path: str | None = None,
        retry_payload: dict[str, Any] | None = None,
    ) -> None:
        """Update an existing download row to its terminal state."""
        normalized_task_id = _normalize_task_id(task_id)
        normalized_final_status = _normalize_final_status(final_status)
        normalized_status_message = normalize_optional_text(status_message)
        normalized_download_path = normalize_optional_text(download_path)
        normalized_retry_payload = self._serialize_retry_payload(retry_payload)
        effective_terminal_at = now_utc_iso()

        with self._lock:
            conn = self._connect()
            try:
                cursor = conn.execute(
                    """
                    UPDATE download_history
                    SET final_status = ?,
                        status_message = ?,
                        download_path = ?,
                        retry_payload = COALESCE(?, retry_payload),
                        terminal_at = ?
                    WHERE task_id = ? AND final_status = 'active'
                    """,
                    (
                        normalized_final_status,
                        normalized_status_message,
                        normalized_download_path,
                        normalized_retry_payload,
                        effective_terminal_at,
                        normalized_task_id,
                    ),
                )
                rowcount = int(cursor.rowcount) if cursor.rowcount is not None else 0
                if rowcount < 1:
                    logger.warning(
                        "finalize_download: no active row found for task_id=%s (may have been missed at queue time)",
                        normalized_task_id,
                    )
                conn.commit()
            finally:
                conn.close()

    def get_by_task_id(self, task_id: str) -> dict[str, Any] | None:
        """Return a persisted download row for the given task id."""
        normalized_task_id = _normalize_task_id(task_id)
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM download_history WHERE task_id = ?",
                (normalized_task_id,),
            ).fetchone()
            return self._row_to_dict(row)
        finally:
            conn.close()

    def list_active(self) -> list[dict[str, Any]]:
        """Return every row still marked active, oldest first."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM download_history WHERE final_status = 'active' ORDER BY id"
            ).fetchall()
            result: list[dict[str, Any]] = []
            for row in rows:
                normalized = self._normalize_row_dict(dict(row))
                if normalized is not None:
                    result.append(normalized)
            return result
        finally:
            conn.close()

    def list_request_ids(self) -> set[int]:
        """Return the id of every request that has at least one download row."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT DISTINCT request_id FROM download_history WHERE request_id IS NOT NULL"
            ).fetchall()
            return {int(row["request_id"]) for row in rows}
        finally:
            conn.close()

    def list_request_task_ids(self, *, user_id: int) -> list[str]:
        """Return the task ids of every request-linked download owned by ``user_id``.

        A non-admin addresses these downloads by an opaque id, so routes resolve it
        against this set, which also confines the lookup to the user's own downloads.
        """
        normalized_user_id = normalize_optional_positive_int(user_id, "user_id")
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT task_id FROM download_history WHERE user_id = ? AND request_id IS NOT NULL",
                (normalized_user_id,),
            ).fetchall()
            return [str(row["task_id"]) for row in rows]
        finally:
            conn.close()

    def list_recent(
        self,
        *,
        user_id: int | None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Return recent persisted download rows, optionally scoped to one user."""
        normalized_user_id = normalize_optional_positive_int(user_id, "user_id")
        normalized_limit = _normalize_limit(limit, default=200, minimum=1, maximum=1000)
        query = "SELECT * FROM download_history"
        params: list[Any] = []
        if normalized_user_id is not None:
            query += " WHERE user_id = ?"
            params.append(normalized_user_id)
        query += " ORDER BY terminal_at DESC, id DESC LIMIT ?"
        params.append(normalized_limit)

        conn = self._connect()
        try:
            rows = conn.execute(query, params).fetchall()
            result: list[dict[str, Any]] = []
            for row in rows:
                normalized = self._normalize_row_dict(dict(row))
                if normalized is not None:
                    result.append(normalized)
            return result
        finally:
            conn.close()
