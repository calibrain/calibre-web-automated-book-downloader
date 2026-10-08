"""WebSocket manager for real-time status updates."""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Any

from flask_socketio import SocketIO, join_room, leave_room

from shelfmark.core.viewer_redaction import viewer_download_id

if TYPE_CHECKING:
    from collections.abc import Callable

    from flask import Flask

logger = logging.getLogger(__name__)


class WebSocketManager:
    """Manages WebSocket connections and broadcasts."""

    def __init__(self) -> None:
        """Initialize in-memory connection and room tracking."""
        self.socketio: SocketIO | None = None
        self._enabled = False
        self._connection_count = 0
        self._connection_lock = threading.Lock()
        self._user_rooms: dict[str, int] = {}  # room_name -> ref count
        self._sid_rooms: dict[str, str] = {}  # sid -> room_name
        self._rooms_lock = threading.Lock()
        self._queue_status_fn: Callable | None = None  # Reference to queue_status()

    def init_app(self, app: Flask, socketio: SocketIO) -> None:
        """Initialize the WebSocket manager with Flask-SocketIO instance."""
        self.socketio = socketio
        self._enabled = True
        logger.info("WebSocket manager initialized")

    def client_connected(self) -> None:
        """Track a new client connection. Call this from the connect event handler."""
        with self._connection_lock:
            self._connection_count += 1
            current_count = self._connection_count

        logger.debug("Client connected. Active connections: %s", current_count)

    def client_disconnected(self) -> None:
        """Track a client disconnection. Call this from the disconnect event handler."""
        with self._connection_lock:
            self._connection_count = max(0, self._connection_count - 1)
            current_count = self._connection_count

        logger.debug("Client disconnected. Active connections: %s", current_count)

    def is_enabled(self) -> bool:
        """Check if WebSocket is enabled and ready."""
        return self._enabled and self.socketio is not None

    def _get_socketio(self) -> SocketIO | None:
        if not self._enabled:
            return None
        return self.socketio

    def set_queue_status_fn(self, fn: Callable) -> None:
        """Set the queue_status function reference for per-room filtering."""
        self._queue_status_fn = fn

    def _increment_user_room_locked(self, room: str) -> None:
        self._user_rooms[room] = self._user_rooms.get(room, 0) + 1

    def _decrement_user_room_locked(self, room: str) -> None:
        count = self._user_rooms.get(room, 1) - 1
        if count <= 0:
            self._user_rooms.pop(room, None)
        else:
            self._user_rooms[room] = count

    def _set_sid_room_locked(self, sid: str, room: str | None) -> None:
        current_room = self._sid_rooms.get(sid)
        if current_room == room:
            return

        if current_room is not None:
            leave_room(current_room, sid=sid)
            if current_room.startswith("user_"):
                self._decrement_user_room_locked(current_room)
            self._sid_rooms.pop(sid, None)

        if room is not None:
            join_room(room, sid=sid)
            self._sid_rooms[sid] = room
            if room.startswith("user_"):
                self._increment_user_room_locked(room)

    def sync_user_room(
        self,
        sid: str,
        is_admin: bool,
        db_user_id: int | None = None,
    ) -> None:
        """Ensure a SID is in exactly one room matching the current session scope."""
        room: str | None = None
        if is_admin:
            room = "admins"
        elif db_user_id is not None:
            room = f"user_{db_user_id}"

        with self._rooms_lock:
            self._set_sid_room_locked(sid, room)

    def join_user_room(
        self,
        sid: str,
        is_admin: bool,
        db_user_id: int | None = None,
    ) -> None:
        """Join the appropriate room based on user role."""
        self.sync_user_room(sid, is_admin=is_admin, db_user_id=db_user_id)

    def leave_user_room(
        self,
        sid: str,
        *,
        is_admin: bool = False,
        db_user_id: int | None = None,
    ) -> None:
        """Leave whichever room the SID currently belongs to."""
        del is_admin, db_user_id  # Backward-compatible signature; routing is SID-based.
        with self._rooms_lock:
            self._set_sid_room_locked(sid, None)

    def broadcast_status_update(self, status_data: dict[str, Any]) -> None:
        """Broadcast status update to all connected clients, filtered by user room."""
        socketio = self._get_socketio()
        if socketio is None:
            return

        try:
            # Admins (and no-auth users) get full status
            socketio.emit("status_update", status_data, to="admins")

            # Each user room gets filtered status
            with self._rooms_lock:
                active_rooms = list(self._user_rooms.keys())

            if active_rooms and self._queue_status_fn:
                for room in active_rooms:
                    self._broadcast_status_update_to_room(room)

            logger.debug("Broadcasted status update to all rooms")
        except Exception:
            logger.exception("Error broadcasting status update")

    def _broadcast_status_update_to_room(self, room: str) -> None:
        """Broadcast status update to one user room."""
        socketio = self._get_socketio()
        if socketio is None:
            return

        try:
            # Extract user_id from room name "user_123"
            uid = int(room.split("_", 1)[1])
            filtered = self._queue_status_fn(user_id=uid) if self._queue_status_fn else None
            if filtered is not None:
                socketio.emit("status_update", filtered, to=room)
        except Exception:
            logger.exception("Failed to send status update for room %s", room)

    def broadcast_download_progress(
        self,
        book_id: str,
        progress: float,
        status: str,
        user_id: int | None = None,
        request_id: int | None = None,
    ) -> None:
        """Broadcast download progress update for a specific book."""
        socketio = self._get_socketio()
        if socketio is None:
            return

        try:
            data = {"book_id": book_id, "progress": progress, "status": status}
            # Admins always see all progress
            socketio.emit("download_progress", data, to="admins")
            # If task belongs to a specific user, send to their room too, under the id
            # their status uses: an opaque one for a request-linked download (#1418).
            if user_id is not None:
                room = f"user_{user_id}"
                with self._rooms_lock:
                    if room in self._user_rooms:
                        user_data = {**data, "book_id": viewer_download_id(book_id, request_id)}
                        socketio.emit("download_progress", user_data, to=room)
            logger.debug("Broadcasted progress for book %s: %s%%", book_id, progress)
        except Exception:
            logger.exception("Error broadcasting download progress")

    def broadcast_search_status(
        self,
        source: str,
        provider: str,
        book_id: str,
        message: str,
        phase: str = "searching",
    ) -> None:
        """Broadcast search status update for a release source search."""
        socketio = self._get_socketio()
        if socketio is None:
            return

        try:
            data = {
                "source": source,
                "provider": provider,
                "book_id": book_id,
                "message": message,
                "phase": phase,
            }
            socketio.emit("search_status", data)
        except Exception:
            logger.exception("Error broadcasting search status")


# Global WebSocket manager instance
ws_manager = WebSocketManager()
