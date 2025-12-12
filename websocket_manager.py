"""WebSocket manager for real-time status updates."""

import logging
import threading
from typing import Optional, Dict, Any, Callable, List

from flask_socketio import SocketIO, emit

logger = logging.getLogger(__name__)

class WebSocketManager:
    """Manages WebSocket connections and broadcasts."""
    
    def __init__(self):
        self.socketio: Optional[SocketIO] = None
        self._enabled = False
        self._connection_count = 0
        self._connection_lock = threading.Lock()
        self._on_first_connect_callbacks: List[Callable[[], None]] = []
        self._on_all_disconnect_callbacks: List[Callable[[], None]] = []
        
    def init_app(self, app, socketio: SocketIO):
        """Initialize the WebSocket manager with Flask-SocketIO instance."""
        self.socketio = socketio
        self._enabled = True
        logger.info("WebSocket manager initialized")
    
    def register_on_first_connect(self, callback: Callable[[], None]):
        """Register a callback to be called when the first client connects.
        
        This is useful for warming up resources (like the Cloudflare bypasser)
        when a user starts using the web UI.
        """
        self._on_first_connect_callbacks.append(callback)
        logger.debug(f"Registered on_first_connect callback: {callback.__name__}")
    
    def register_on_all_disconnect(self, callback: Callable[[], None]):
        """Register a callback to be called when all clients disconnect.
        
        This can be used to trigger cleanup or resource release.
        """
        self._on_all_disconnect_callbacks.append(callback)
        logger.debug(f"Registered on_all_disconnect callback: {callback.__name__}")
    
    def client_connected(self):
        """Track a new client connection. Call this from the connect event handler."""
        with self._connection_lock:
            was_zero = self._connection_count == 0
            self._connection_count += 1
            current_count = self._connection_count
        
        logger.debug(f"Client connected. Active connections: {current_count}")
        
        # If this is the first connection, trigger warmup callbacks
        if was_zero:
            logger.info("First client connected, triggering warmup callbacks...")
            for callback in self._on_first_connect_callbacks:
                try:
                    # Run callbacks in a separate thread to not block the connection
                    thread = threading.Thread(target=callback, daemon=True)
                    thread.start()
                except Exception as e:
                    logger.error(f"Error in on_first_connect callback {callback.__name__}: {e}")
    
    def client_disconnected(self):
        """Track a client disconnection. Call this from the disconnect event handler."""
        with self._connection_lock:
            self._connection_count = max(0, self._connection_count - 1)
            current_count = self._connection_count
            is_now_zero = current_count == 0
        
        logger.debug(f"Client disconnected. Active connections: {current_count}")
        
        # If all clients have disconnected, trigger cleanup callbacks
        if is_now_zero:
            logger.info("All clients disconnected, triggering disconnect callbacks...")
            for callback in self._on_all_disconnect_callbacks:
                try:
                    callback()
                except Exception as e:
                    logger.error(f"Error in on_all_disconnect callback {callback.__name__}: {e}")
    
    def get_connection_count(self) -> int:
        """Get the current number of active WebSocket connections."""
        with self._connection_lock:
            return self._connection_count
    
    def has_active_connections(self) -> bool:
        """Check if there are any active WebSocket connections."""
        return self.get_connection_count() > 0
        
    def is_enabled(self) -> bool:
        """Check if WebSocket is enabled and ready."""
        return self._enabled and self.socketio is not None
    
    def broadcast_status_update(self, status_data: Dict[str, Any]):
        """Broadcast status update to all connected clients."""
        if not self.is_enabled():
            return
            
        try:
            # When calling socketio.emit() outside event handlers, it broadcasts by default
            self.socketio.emit('status_update', status_data)
            logger.debug(f"Broadcasted status update to all clients")
        except Exception as e:
            logger.error(f"Error broadcasting status update: {e}")
    
    def broadcast_download_progress(self, book_id: str, progress: float, status: str):
        """Broadcast download progress update for a specific book."""
        if not self.is_enabled():
            return
            
        try:
            data = {
                'book_id': book_id,
                'progress': progress,
                'status': status
            }
            # When calling socketio.emit() outside event handlers, it broadcasts by default
            self.socketio.emit('download_progress', data)
            logger.debug(f"Broadcasted progress for book {book_id}: {progress}%")
        except Exception as e:
            logger.error(f"Error broadcasting download progress: {e}")
    
    def broadcast_notification(self, message: str, notification_type: str = 'info'):
        """Broadcast a notification message to all clients."""
        if not self.is_enabled():
            return
            
        try:
            data = {
                'message': message,
                'type': notification_type
            }
            # When calling socketio.emit() outside event handlers, it broadcasts by default
            self.socketio.emit('notification', data)
            logger.debug(f"Broadcasted notification: {message}")
        except Exception as e:
            logger.error(f"Error broadcasting notification: {e}")

# Global WebSocket manager instance
ws_manager = WebSocketManager()
