"""WebSocket manager for real-time status updates."""

import logging
from typing import Optional, Dict, Any
from flask_socketio import SocketIO, emit

logger = logging.getLogger(__name__)

class WebSocketManager:
    """Manages WebSocket connections and broadcasts."""
    
    def __init__(self):
        self.socketio: Optional[SocketIO] = None
        self._enabled = False
        
    def init_app(self, app, socketio: SocketIO):
        """Initialize the WebSocket manager with Flask-SocketIO instance."""
        self.socketio = socketio
        self._enabled = True
        logger.info("WebSocket manager initialized")
        
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
