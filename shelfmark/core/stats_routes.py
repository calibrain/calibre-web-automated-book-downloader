"""Dashboard counters: ``GET /api/stats``."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from flask import g, jsonify, session

from shelfmark.core import stats
from shelfmark.core.logger import setup_logger

if TYPE_CHECKING:
    from collections.abc import Callable

    from flask import Flask
    from werkzeug.wrappers import Response

    from shelfmark.core.user_db import UserDB

logger = setup_logger(__name__)

STATS_PATH = "/api/stats"


def register_stats_routes(
    app: Flask,
    *,
    get_user_db: Callable[[], UserDB | None],
    resolve_auth_mode: Callable[[], str],
) -> None:
    """Register ``/api/stats``.

    Both arguments are called per request so a runtime patch of either still takes effect.
    """

    @app.route(STATS_PATH, methods=["GET"])
    def api_stats() -> Response | tuple[Response, int]:
        """Counters for dashboards. Reachable with the read-only API key, an admin key or an admin session."""
        scope = g.get("api_key_scope")
        if resolve_auth_mode() != "none" and scope is None:
            if "user_id" not in session:
                return jsonify({"error": "Unauthorized"}), 401
            if not session.get("is_admin", False):
                return jsonify({"error": "Admin access required"}), 403

        user_db = get_user_db()
        if user_db is None:
            return jsonify({"error": "User database unavailable"}), 503
        try:
            payload = stats.collect(user_db.db_path)
        except sqlite3.Error:
            logger.exception("stats: could not read the database")
            return jsonify({"error": "Internal Server Error"}), 500
        return jsonify(payload)
