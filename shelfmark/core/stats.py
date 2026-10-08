"""Dashboard counters for ``/api/stats``: what was added, what is queued, what went wrong.

Counts only, no titles or user names, read straight from the request and history tables
on a read-only connection.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

_FORMATS = ("ebook", "audiobook")


def _since(now: datetime, days: int) -> str:
    return (now - timedelta(days=days)).isoformat()


def _added(conn: sqlite3.Connection, now: datetime) -> dict[str, int]:
    out: dict[str, int] = {}
    for days in (7, 30):
        rows = conn.execute(
            """
            SELECT LOWER(COALESCE(content_type, '')) AS kind, COUNT(*) AS n
            FROM download_history
            WHERE final_status = 'complete' AND datetime(terminal_at) >= datetime(?)
            GROUP BY kind
            """,
            (_since(now, days),),
        ).fetchall()
        by_kind = {row["kind"]: int(row["n"]) for row in rows}
        for kind in _FORMATS:
            out[f"{kind}_{days}d"] = by_kind.get(kind, 0)
        out[f"total_{days}d"] = sum(by_kind.values())
    return out


def _count(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> int:
    row = conn.execute(sql, params).fetchone()
    return int(row[0]) if row else 0


def collect(db_path: str, *, now: datetime | None = None) -> dict[str, Any]:
    """Counters for the dashboard, as of ``now`` (UTC)."""
    now = now or datetime.now(UTC)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        week = _since(now, 7)
        awaiting = _count(
            conn,
            "SELECT COUNT(*) FROM download_requests WHERE status = 'pending' AND delivery_state = 'none'",
        )
        queued = _count(
            conn, "SELECT COUNT(*) FROM download_requests WHERE delivery_state = 'queued'"
        )
        by_delivery = {
            state: _count(
                conn, "SELECT COUNT(*) FROM download_requests WHERE delivery_state = ?", (state,)
            )
            for state in ("complete", "error", "cancelled")
        }
        return {
            "generated_at": now.isoformat(),
            "added": _added(conn, now),
            "queue": {
                "active": _count(
                    conn, "SELECT COUNT(*) FROM download_history WHERE final_status = 'active'"
                ),
                "awaiting_pickup": awaiting,
                "queued": queued,
            },
            "requests": {
                "pending": _count(
                    conn, "SELECT COUNT(*) FROM download_requests WHERE status = 'pending'"
                ),
                "delivered": by_delivery["complete"],
                "failed": by_delivery["error"],
                "cancelled": by_delivery["cancelled"],
                "rejected": _count(
                    conn, "SELECT COUNT(*) FROM download_requests WHERE status = 'rejected'"
                ),
            },
            "errors": {
                "failed_7d": _count(
                    conn,
                    """
                    SELECT COUNT(*) FROM download_history
                    WHERE final_status = 'error' AND datetime(terminal_at) >= datetime(?)
                    """,
                    (week,),
                ),
            },
        }
    finally:
        conn.close()
