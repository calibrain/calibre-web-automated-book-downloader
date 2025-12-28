"""Flask app - routes, WebSocket handlers, and middleware."""

import io
import logging
import os
import sqlite3
import time
from datetime import datetime, timedelta
from functools import wraps
from typing import Any, Dict, Tuple, Union

from flask import Flask, jsonify, request, send_file, send_from_directory, session
from flask_cors import CORS
from flask_socketio import SocketIO, emit
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash
from werkzeug.wrappers import Response

from cwa_book_downloader.download import orchestrator as backend
from cwa_book_downloader.release_sources.direct_download import SearchUnavailable
from cwa_book_downloader.config.settings import _SUPPORTED_BOOK_LANGUAGE
from cwa_book_downloader.config.env import (
    BUILD_VERSION, CWA_DB_PATH, DEBUG, FLASK_HOST, FLASK_PORT,
    RELEASE_VERSION, USING_EXTERNAL_BYPASSER,
)
from cwa_book_downloader.core.config import config as app_config
from cwa_book_downloader.core.logger import setup_logger
from cwa_book_downloader.core.models import SearchFilters
from cwa_book_downloader.api.websocket import ws_manager

logger = setup_logger(__name__)

# Project root is one level up from this package
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_DIST = os.path.join(PROJECT_ROOT, 'frontend-dist')

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app)  # type: ignore
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0  # Disable caching
app.config['APPLICATION_ROOT'] = '/'

# Socket.IO async mode.
# We run this app under Gunicorn with a gevent websocket worker (even when DEBUG=true),
# so Socket.IO should always use gevent here.
async_mode = 'gevent'

# Initialize Flask-SocketIO with reverse proxy support
socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode=async_mode,
    logger=False,
    engineio_logger=False,
    # Reverse proxy / Traefik compatibility settings
    path='/socket.io',
    ping_timeout=60,  # Time to wait for pong response
    ping_interval=25,  # Send ping every 25 seconds
    # Allow both websocket and polling for better compatibility
    transports=['websocket', 'polling'],
    # Enable CORS for all origins (you can restrict this in production)
    allow_upgrades=True,
    # Important for proxies that buffer
    http_compression=True
)

# Initialize WebSocket manager
ws_manager.init_app(app, socketio)
logger.info(f"Flask-SocketIO initialized with async_mode='{async_mode}'")

# Ensure all plugins are loaded before starting the download coordinator.
# This prevents a race condition where the download loop could try to process
# a queued task before its handler (e.g., prowlarr) is registered.
try:
    import cwa_book_downloader.metadata_providers  # noqa: F401
    import cwa_book_downloader.release_sources  # noqa: F401
    logger.debug("Plugin modules loaded successfully")
except ImportError as e:
    logger.warning(f"Failed to import plugin modules: {e}")

# Start download coordinator
backend.start()

# Rate limiting for login attempts
# Structure: {username: {'count': int, 'lockout_until': datetime}}
failed_login_attempts: Dict[str, Dict[str, Any]] = {}
MAX_LOGIN_ATTEMPTS = 10
LOCKOUT_DURATION_MINUTES = 30

def cleanup_old_lockouts() -> None:
    """Remove expired lockout entries to prevent memory buildup."""
    current_time = datetime.now()
    expired_users = [
        username for username, data in failed_login_attempts.items()
        if 'lockout_until' in data and data['lockout_until'] < current_time
    ]
    for username in expired_users:
        logger.info(f"Lockout expired for user: {username}")
        del failed_login_attempts[username]

def is_account_locked(username: str) -> bool:
    """Check if an account is currently locked due to failed login attempts."""
    cleanup_old_lockouts()
    
    if username not in failed_login_attempts:
        return False
    
    lockout_until = failed_login_attempts[username].get('lockout_until')
    if lockout_until and datetime.now() < lockout_until:
        return True
    
    return False

def record_failed_login(username: str, ip_address: str) -> bool:
    """
    Record a failed login attempt and lock account if threshold is reached.
    Returns True if account is now locked, False otherwise.
    """
    if username not in failed_login_attempts:
        failed_login_attempts[username] = {'count': 0}
    
    failed_login_attempts[username]['count'] += 1
    count = failed_login_attempts[username]['count']
    
    logger.warning(f"Failed login attempt {count}/{MAX_LOGIN_ATTEMPTS} for user '{username}' from IP {ip_address}")
    
    if count >= MAX_LOGIN_ATTEMPTS:
        lockout_until = datetime.now() + timedelta(minutes=LOCKOUT_DURATION_MINUTES)
        failed_login_attempts[username]['lockout_until'] = lockout_until
        logger.warning(f"Account locked for user '{username}' until {lockout_until.strftime('%Y-%m-%d %H:%M:%S')} due to {count} failed login attempts")
        return True
    
    return False

def clear_failed_logins(username: str) -> None:
    """Clear failed login attempts for a user after successful login."""
    if username in failed_login_attempts:
        del failed_login_attempts[username]
        logger.debug(f"Cleared failed login attempts for user: {username}")


def get_auth_mode() -> str:
    """
    Determine which authentication mode is active.

    Priority order:
    1. Built-in credentials (if configured) -> "builtin"
    2. CWA database (if CWA_DB_PATH is set and exists) -> "cwa"
    3. No auth required -> "none"

    Returns:
        str: "builtin", "cwa", or "none"
    """
    from cwa_book_downloader.core.settings_registry import load_config_file

    # Check for built-in credentials first (they take priority)
    try:
        security_config = load_config_file("security")
        username = security_config.get("BUILTIN_USERNAME")
        password_hash = security_config.get("BUILTIN_PASSWORD_HASH")
        if username and password_hash:
            return "builtin"
    except Exception:
        pass  # If config can't be loaded, fall through to other methods

    # Check for CWA database
    if CWA_DB_PATH and os.path.isfile(CWA_DB_PATH):
        return "cwa"

    # No auth configured
    return "none"


# Enable CORS in development mode for local frontend development
if DEBUG:
    CORS(app, resources={
        r"/*": {
            "origins": ["http://localhost:5173", "http://127.0.0.1:5173"],
            "supports_credentials": True,
            "allow_headers": ["Content-Type", "Authorization"],
            "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"]
        }
    })

# Custom log filter to exclude routine status endpoint polling and WebSocket noise
class StatusEndpointFilter(logging.Filter):
    """Filter out routine status endpoint requests and WebSocket upgrade errors to reduce log noise."""
    def filter(self, record):
        if hasattr(record, 'getMessage'):
            message = record.getMessage()
            # Exclude GET /api/status requests (polling noise)
            if 'GET /api/status' in message:
                return False
            # Exclude WebSocket upgrade errors (benign - falls back to polling)
            if 'write() before start_response' in message:
                return False
            # Exclude the Error on request line that precedes WebSocket errors
            if 'Error on request:' in message and record.levelno == logging.ERROR:
                return False
        return True


class WebSocketErrorFilter(logging.Filter):
    """Filter out WebSocket upgrade errors that occur in Werkzeug dev server.
    
    These errors are benign - Flask-SocketIO automatically falls back to polling transport.
    The error occurs because Werkzeug's built-in server doesn't fully support WebSocket upgrades.
    """
    def filter(self, record):
        # Filter out the AssertionError traceback for WebSocket upgrades
        if record.levelno == logging.ERROR:
            message = record.getMessage() if hasattr(record, 'getMessage') else str(record.msg)
            # Filter out the full traceback that includes the WebSocket assertion error
            if 'write() before start_response' in message:
                return False
            # Also filter the "Error on request" header that precedes it
            if hasattr(record, 'exc_info') and record.exc_info:
                exc_type = record.exc_info[0]
                if exc_type and exc_type.__name__ == 'AssertionError':
                    # Check if it's the WebSocket-related assertion
                    exc_value = record.exc_info[1]
                    if exc_value and 'write() before start_response' in str(exc_value):
                        return False
        return True

# Flask logger
app.logger.handlers = logger.handlers
app.logger.setLevel(logger.level)
# Also handle Werkzeug's logger
werkzeug_logger = logging.getLogger('werkzeug')
werkzeug_logger.handlers = logger.handlers
werkzeug_logger.setLevel(logger.level)
# Add filters to suppress routine status endpoint polling logs and WebSocket upgrade errors
werkzeug_logger.addFilter(StatusEndpointFilter())
werkzeug_logger.addFilter(WebSocketErrorFilter())

# Set up authentication defaults
# The secret key will reset every time we restart, which will
# require users to authenticate again

# Session cookie security - set to 'true' if exclusively using HTTPS
session_cookie_secure_env = os.getenv('SESSION_COOKIE_SECURE', 'false').lower()
SESSION_COOKIE_SECURE = session_cookie_secure_env in ['true', 'yes', '1']

app.config.update(
    SECRET_KEY = os.urandom(64),
    SESSION_COOKIE_HTTPONLY = True,
    SESSION_COOKIE_SAMESITE = 'Lax',
    SESSION_COOKIE_SECURE = SESSION_COOKIE_SECURE,
    PERMANENT_SESSION_LIFETIME = 604800  # 7 days in seconds
)

logger.info(f"Session cookie secure setting: {SESSION_COOKIE_SECURE} (from env: {session_cookie_secure_env})")

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        auth_mode = get_auth_mode()

        # If no authentication is configured, allow access
        if auth_mode == "none":
            return f(*args, **kwargs)

        # If CWA mode and database path is invalid, return error
        if auth_mode == "cwa" and CWA_DB_PATH and not os.path.isfile(CWA_DB_PATH):
            logger.error(f"CWA_DB_PATH is set to {CWA_DB_PATH} but this is not a valid path")
            return jsonify({"error": "Internal Server Error"}), 500

        # Check if user has a valid session
        if 'user_id' not in session:
            return jsonify({"error": "Unauthorized"}), 401

        return f(*args, **kwargs)
    return decorated_function


# Serve frontend static files
@app.route('/assets/<path:filename>')
def serve_frontend_assets(filename: str) -> Response:
    """
    Serve static assets from the built frontend.
    """
    return send_from_directory(os.path.join(FRONTEND_DIST, 'assets'), filename)

@app.route('/')
def index() -> Response:
    """
    Serve the React frontend application.
    Authentication is handled by the React app itself.
    """
    return send_from_directory(FRONTEND_DIST, 'index.html')

@app.route('/logo.png')
def logo() -> Response:
    """
    Serve logo from built frontend assets.
    """
    return send_from_directory(FRONTEND_DIST, 'logo.png', mimetype='image/png')

@app.route('/favicon.ico')
@app.route('/favico<path:_>')
def favicon(_: Any = None) -> Response:
    """
    Serve favicon from built frontend assets.
    """
    return send_from_directory(FRONTEND_DIST, 'favicon.ico', mimetype='image/vnd.microsoft.icon')

# Register bypasser warmup callback for when first WebSocket client connects
# and shutdown callback for when all clients disconnect
if not USING_EXTERNAL_BYPASSER:
    from cwa_book_downloader.bypass.internal_bypasser import warmup as bypasser_warmup, shutdown_if_idle as bypasser_shutdown
    ws_manager.register_on_first_connect(bypasser_warmup)
    ws_manager.register_on_all_disconnect(bypasser_shutdown)
    logger.info("Registered Cloudflare bypasser warmup/shutdown on WebSocket connect/disconnect")

if DEBUG:
    import subprocess
    if USING_EXTERNAL_BYPASSER:
        STOP_GUI = lambda: None
    else:
        from cwa_book_downloader.bypass.internal_bypasser import _reset_driver as STOP_GUI
    @app.route('/api/debug', methods=['GET'])
    @login_required
    def debug() -> Union[Response, Tuple[Response, int]]:
        """
        This will run the /app/genDebug.sh script, which will generate a debug zip with all the logs
        The file will be named /tmp/cwa-book-downloader-debug.zip
        And then return it to the user
        """
        try:
            # Run the debug script
            logger.info("Debug endpoint called, stopping GUI and generating debug info...")
            STOP_GUI()
            time.sleep(1)
            result = subprocess.run(['/app/genDebug.sh'], capture_output=True, text=True, check=True)
            if result.returncode != 0:
                raise Exception(f"Debug script failed: {result.stderr}")
            logger.info(f"Debug script executed: {result.stdout}")
            debug_file_path = result.stdout.strip().split('\n')[-1]
            if not os.path.exists(debug_file_path):
                logger.error(f"Debug zip file not found at: {debug_file_path}")
                return jsonify({"error": "Failed to generate debug information"}), 500
                
            logger.info(f"Sending debug file: {debug_file_path}")
            # Return the file to the user
            return send_file(
                debug_file_path,
                mimetype='application/zip',
                download_name=os.path.basename(debug_file_path),
                as_attachment=True
            )
        except subprocess.CalledProcessError as e:
            logger.error_trace(f"Debug script error: {e}, stdout: {e.stdout}, stderr: {e.stderr}")
            return jsonify({"error": f"Debug script failed: {e.stderr}"}), 500
        except Exception as e:
            logger.error_trace(f"Debug endpoint error: {e}")
            return jsonify({"error": str(e)}), 500

if DEBUG:
    @app.route('/api/restart', methods=['GET'])
    @login_required
    def restart() -> Union[Response, Tuple[Response, int]]:
        """
        Restart the application
        """
        os._exit(0)

@app.route('/api/search', methods=['GET'])
@login_required
def api_search() -> Union[Response, Tuple[Response, int]]:
    """
    Search for books matching the provided query.

    Query Parameters:
        query (str): Search term (ISBN, title, author, etc.)
        isbn (str): Book ISBN
        author (str): Book Author
        title (str): Book Title
        lang (str): Book Language
        sort (str): Order to sort results
        content (str): Content type of book
        format (str): File format filter (pdf, epub, mobi, azw3, fb2, djvu, cbz, cbr)

    Returns:
        flask.Response: JSON array of matching books or error response.
    """
    query = request.args.get('query', '')

    filters = SearchFilters(
        isbn = request.args.getlist('isbn'),
        author = request.args.getlist('author'),
        title = request.args.getlist('title'),
        lang = request.args.getlist('lang'),
        sort = request.args.get('sort'),
        content = request.args.getlist('content'),
        format = request.args.getlist('format'),
    )

    if not query and not any(vars(filters).values()):
        return jsonify([])

    try:
        books = backend.search_books(query, filters)
        return jsonify(books)
    except SearchUnavailable as e:
        logger.warning(f"Search unavailable: {e}")
        return jsonify({"error": str(e)}), 503
    except Exception as e:
        logger.error_trace(f"Search error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/info', methods=['GET'])
@login_required
def api_info() -> Union[Response, Tuple[Response, int]]:
    """
    Get detailed book information.

    Query Parameters:
        id (str): Book identifier (MD5 hash)

    Returns:
        flask.Response: JSON object with book details, or an error message.
    """
    book_id = request.args.get('id', '')
    if not book_id:
        return jsonify({"error": "No book ID provided"}), 400

    try:
        book = backend.get_book_info(book_id)
        if book:
            return jsonify(book)
        return jsonify({"error": "Book not found"}), 404
    except Exception as e:
        logger.error_trace(f"Info error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/download', methods=['GET'])
@login_required
def api_download() -> Union[Response, Tuple[Response, int]]:
    """
    Queue a book for download.

    Query Parameters:
        id (str): Book identifier (MD5 hash)

    Returns:
        flask.Response: JSON status object indicating success or failure.
    """
    book_id = request.args.get('id', '')
    if not book_id:
        return jsonify({"error": "No book ID provided"}), 400

    try:
        priority = int(request.args.get('priority', 0))
        success = backend.queue_book(book_id, priority)
        if success:
            return jsonify({"status": "queued", "priority": priority})
        return jsonify({"error": "Failed to queue book"}), 500
    except Exception as e:
        logger.error_trace(f"Download error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/releases/download', methods=['POST'])
@login_required
def api_download_release() -> Union[Response, Tuple[Response, int]]:
    """
    Queue a release for download.

    This endpoint is used when downloading from the ReleaseModal where the
    frontend already has all the release data from the search results.

    Request Body (JSON):
        source (str): Release source (e.g., "direct_download")
        source_id (str): ID within the source (e.g., AA MD5 hash)
        title (str): Book title
        format (str, optional): File format
        size (str, optional): Human-readable size
        extra (dict, optional): Additional metadata

    Returns:
        flask.Response: JSON status object indicating success or failure.
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400

        if 'source_id' not in data:
            return jsonify({"error": "source_id is required"}), 400

        priority = data.get('priority', 0)
        success = backend.queue_release(data, priority)

        if success:
            return jsonify({"status": "queued", "priority": priority})
        return jsonify({"error": "Failed to queue release"}), 500
    except Exception as e:
        logger.error_trace(f"Release download error: {e}")
        return jsonify({"error": str(e)}), 500


def _is_settings_enabled() -> bool:
    """Check if the config directory is mounted and writable."""
    from cwa_book_downloader.config.env import _is_config_dir_writable
    return _is_config_dir_writable()


@app.route('/api/config', methods=['GET'])
@login_required
def api_config() -> Union[Response, Tuple[Response, int]]:
    """
    Get application configuration for frontend.

    Uses the dynamic config singleton to ensure settings changes
    are reflected without requiring a container restart.
    """
    try:
        from cwa_book_downloader.metadata_providers import (
            get_provider_sort_options,
            get_provider_search_fields,
            get_provider_default_sort,
        )

        config = {
            "calibre_web_url": app_config.get("CALIBRE_WEB_URL", ""),
            "debug": app_config.get("DEBUG", False),
            "build_version": BUILD_VERSION,
            "release_version": RELEASE_VERSION,
            "book_languages": _SUPPORTED_BOOK_LANGUAGE,
            "default_language": app_config.BOOK_LANGUAGE,
            "supported_formats": app_config.SUPPORTED_FORMATS,
            "search_mode": app_config.get("SEARCH_MODE", "direct"),
            "metadata_sort_options": get_provider_sort_options(),
            "metadata_search_fields": get_provider_search_fields(),
            "default_release_source": app_config.get("DEFAULT_RELEASE_SOURCE", "direct_download"),
            "auto_open_downloads_sidebar": app_config.get("AUTO_OPEN_DOWNLOADS_SIDEBAR", True),
            "download_to_browser": app_config.get("DOWNLOAD_TO_BROWSER", False),
            "settings_enabled": _is_settings_enabled(),
            # Default sort orders
            "default_sort": app_config.get("AA_DEFAULT_SORT", "relevance"),  # For direct mode (Anna's Archive)
            "metadata_default_sort": get_provider_default_sort(),  # For universal mode
        }
        return jsonify(config)
    except Exception as e:
        logger.error_trace(f"Config error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/health', methods=['GET'])
def api_health() -> Union[Response, Tuple[Response, int]]:
    """
    Health check endpoint for container orchestration.
    No authentication required.
    
    Returns:
        flask.Response: JSON with status "ok".
    """
    return jsonify({"status": "ok"})

@app.route('/api/status', methods=['GET'])
@login_required
def api_status() -> Union[Response, Tuple[Response, int]]:
    """
    Get current download queue status.

    Returns:
        flask.Response: JSON object with queue status.
    """
    try:
        status = backend.queue_status()
        return jsonify(status)
    except Exception as e:
        logger.error_trace(f"Status error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/localdownload', methods=['GET'])
@login_required
def api_local_download() -> Union[Response, Tuple[Response, int]]:
    """
    Download an EPUB file from local storage if available.

    Query Parameters:
        id (str): Book identifier (MD5 hash)

    Returns:
        flask.Response: The EPUB file if found, otherwise an error response.
    """
    book_id = request.args.get('id', '')
    if not book_id:
        return jsonify({"error": "No book ID provided"}), 400

    try:
        file_data, book_info = backend.get_book_data(book_id)
        if file_data is None:
            # Book data not found or not available
            return jsonify({"error": "File not found"}), 404
        file_name = book_info.get_filename()
        # Prepare the file for sending to the client
        data = io.BytesIO(file_data)
        return send_file(
            data,
            download_name=file_name,
            as_attachment=True
        )

    except Exception as e:
        logger.error_trace(f"Local download error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/covers/<cover_id>', methods=['GET'])
def api_cover(cover_id: str) -> Union[Response, Tuple[Response, int]]:
    """
    Serve a cached book cover image.

    This endpoint proxies and caches cover images from external sources.
    Images are cached to disk for faster subsequent requests.

    Path Parameters:
        cover_id (str): Cover identifier (book ID or composite key for universal mode)

    Query Parameters:
        url (str): Base64-encoded original image URL (required on first request)

    Returns:
        flask.Response: Binary image data with appropriate Content-Type, or 404.
    """
    try:
        import base64
        from cwa_book_downloader.core.image_cache import get_image_cache
        from cwa_book_downloader.config.env import is_covers_cache_enabled

        # Check if caching is enabled
        if not is_covers_cache_enabled():
            return jsonify({"error": "Cover caching is disabled"}), 404

        cache = get_image_cache()

        # Try to get from cache first
        cached = cache.get(cover_id)
        if cached:
            image_data, content_type = cached
            response = app.response_class(
                response=image_data,
                status=200,
                mimetype=content_type
            )
            response.headers['Cache-Control'] = 'public, max-age=86400'
            response.headers['X-Cache'] = 'HIT'
            return response

        # Cache miss - get URL from query parameter
        encoded_url = request.args.get('url')
        if not encoded_url:
            return jsonify({"error": "Cover URL not provided"}), 404

        try:
            original_url = base64.urlsafe_b64decode(encoded_url).decode()
        except Exception as e:
            logger.warning(f"Failed to decode cover URL: {e}")
            return jsonify({"error": "Invalid cover URL encoding"}), 400

        # Fetch and cache the image
        result = cache.fetch_and_cache(cover_id, original_url)
        if not result:
            return jsonify({"error": "Failed to fetch cover image"}), 404

        image_data, content_type = result
        response = app.response_class(
            response=image_data,
            status=200,
            mimetype=content_type
        )
        response.headers['Cache-Control'] = 'public, max-age=86400'
        response.headers['X-Cache'] = 'MISS'
        return response

    except Exception as e:
        logger.error_trace(f"Cover fetch error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/download/<book_id>/cancel', methods=['DELETE'])
@login_required
def api_cancel_download(book_id: str) -> Union[Response, Tuple[Response, int]]:
    """
    Cancel a download.

    Path Parameters:
        book_id (str): Book identifier to cancel

    Returns:
        flask.Response: JSON status indicating success or failure.
    """
    try:
        success = backend.cancel_download(book_id)
        if success:
            return jsonify({"status": "cancelled", "book_id": book_id})
        return jsonify({"error": "Failed to cancel download or book not found"}), 404
    except Exception as e:
        logger.error_trace(f"Cancel download error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/queue/<book_id>/priority', methods=['PUT'])
@login_required
def api_set_priority(book_id: str) -> Union[Response, Tuple[Response, int]]:
    """
    Set priority for a queued book.

    Path Parameters:
        book_id (str): Book identifier

    Request Body:
        priority (int): New priority level (lower number = higher priority)

    Returns:
        flask.Response: JSON status indicating success or failure.
    """
    try:
        data = request.get_json()
        if not data or 'priority' not in data:
            return jsonify({"error": "Priority not provided"}), 400
            
        priority = int(data['priority'])
        success = backend.set_book_priority(book_id, priority)
        
        if success:
            return jsonify({"status": "updated", "book_id": book_id, "priority": priority})
        return jsonify({"error": "Failed to update priority or book not found"}), 404
    except ValueError:
        return jsonify({"error": "Invalid priority value"}), 400
    except Exception as e:
        logger.error_trace(f"Set priority error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/queue/reorder', methods=['POST'])
@login_required
def api_reorder_queue() -> Union[Response, Tuple[Response, int]]:
    """
    Bulk reorder queue by setting new priorities.

    Request Body:
        book_priorities (dict): Mapping of book_id to new priority

    Returns:
        flask.Response: JSON status indicating success or failure.
    """
    try:
        data = request.get_json()
        if not data or 'book_priorities' not in data:
            return jsonify({"error": "book_priorities not provided"}), 400
            
        book_priorities = data['book_priorities']
        if not isinstance(book_priorities, dict):
            return jsonify({"error": "book_priorities must be a dictionary"}), 400
            
        # Validate all priorities are integers
        for book_id, priority in book_priorities.items():
            if not isinstance(priority, int):
                return jsonify({"error": f"Invalid priority for book {book_id}"}), 400
                
        success = backend.reorder_queue(book_priorities)
        
        if success:
            return jsonify({"status": "reordered", "updated_count": len(book_priorities)})
        return jsonify({"error": "Failed to reorder queue"}), 500
    except Exception as e:
        logger.error_trace(f"Reorder queue error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/queue/order', methods=['GET'])
@login_required
def api_queue_order() -> Union[Response, Tuple[Response, int]]:
    """
    Get current queue order for display.

    Returns:
        flask.Response: JSON array of queued books with their order and priorities.
    """
    try:
        queue_order = backend.get_queue_order()
        return jsonify({"queue": queue_order})
    except Exception as e:
        logger.error_trace(f"Queue order error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/downloads/active', methods=['GET'])
@login_required
def api_active_downloads() -> Union[Response, Tuple[Response, int]]:
    """
    Get list of currently active downloads.

    Returns:
        flask.Response: JSON array of active download book IDs.
    """
    try:
        active_downloads = backend.get_active_downloads()
        return jsonify({"active_downloads": active_downloads})
    except Exception as e:
        logger.error_trace(f"Active downloads error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/queue/clear', methods=['DELETE'])
@login_required
def api_clear_completed() -> Union[Response, Tuple[Response, int]]:
    """
    Clear all completed, errored, or cancelled books from tracking.

    Returns:
        flask.Response: JSON with count of removed books.
    """
    try:
        removed_count = backend.clear_completed()
        
        # Broadcast status update after clearing
        if ws_manager:
            ws_manager.broadcast_status_update(backend.queue_status())
        
        return jsonify({"status": "cleared", "removed_count": removed_count})
    except Exception as e:
        logger.error_trace(f"Clear completed error: {e}")
        return jsonify({"error": str(e)}), 500

@app.errorhandler(404)
def not_found_error(error: Exception) -> Union[Response, Tuple[Response, int]]:
    """
    Handle 404 (Not Found) errors.

    Args:
        error (HTTPException): The 404 error raised by Flask.

    Returns:
        flask.Response: JSON error message with 404 status.
    """
    logger.warning(f"404 error: {request.url} : {error}")
    return jsonify({"error": "Resource not found"}), 404

@app.errorhandler(500)
def internal_error(error: Exception) -> Union[Response, Tuple[Response, int]]:
    """
    Handle 500 (Internal Server) errors.

    Args:
        error (HTTPException): The 500 error raised by Flask.

    Returns:
        flask.Response: JSON error message with 500 status.
    """
    logger.error_trace(f"500 error: {error}")
    return jsonify({"error": "Internal server error"}), 500

def _failed_login_response(username: str, ip_address: str) -> Tuple[Response, int]:
    """
    Handle a failed login attempt by recording it and returning the appropriate response.
    """
    is_now_locked = record_failed_login(username, ip_address)

    if is_now_locked:
        return jsonify({
            "error": f"Account locked due to {MAX_LOGIN_ATTEMPTS} failed login attempts. Try again in {LOCKOUT_DURATION_MINUTES} minutes."
        }), 429

    attempts_remaining = MAX_LOGIN_ATTEMPTS - failed_login_attempts[username]['count']
    if attempts_remaining <= 5:
        return jsonify({
            "error": f"Invalid username or password. {attempts_remaining} attempts remaining."
        }), 401

    return jsonify({"error": "Invalid username or password."}), 401


@app.route('/api/auth/login', methods=['POST'])
def api_login() -> Union[Response, Tuple[Response, int]]:
    """
    Login endpoint that validates credentials and creates a session.
    Supports both built-in credentials and CWA database authentication.
    Includes rate limiting: 10 failed attempts = 30 minute lockout.

    Request Body:
        username (str): Username
        password (str): Password
        remember_me (bool): Whether to extend session duration

    Returns:
        flask.Response: JSON with success status or error message.
    """
    from cwa_book_downloader.core.settings_registry import load_config_file

    try:
        # Get client IP address (handles reverse proxy forwarding)
        ip_address = request.headers.get('X-Forwarded-For', request.remote_addr)
        if ip_address and ',' in ip_address:
            # X-Forwarded-For can contain multiple IPs, take the first one
            ip_address = ip_address.split(',')[0].strip()

        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400

        username = data.get('username', '').strip()
        password = data.get('password', '')
        remember_me = data.get('remember_me', False)

        if not username or not password:
            return jsonify({"error": "Username and password are required"}), 400

        # Check if account is locked due to failed login attempts
        if is_account_locked(username):
            lockout_until = failed_login_attempts[username].get('lockout_until')
            remaining_time = (lockout_until - datetime.now()).total_seconds() / 60
            logger.warning(f"Login attempt blocked for locked account '{username}' from IP {ip_address}")
            return jsonify({
                "error": f"Account temporarily locked due to multiple failed login attempts. Try again in {int(remaining_time)} minutes."
            }), 429

        auth_mode = get_auth_mode()

        # If no authentication is configured, authentication always succeeds
        if auth_mode == "none":
            session['user_id'] = username
            session.permanent = remember_me
            clear_failed_logins(username)
            logger.info(f"Login successful for user '{username}' from IP {ip_address} (no auth configured)")
            return jsonify({"success": True})

        # Built-in authentication mode
        if auth_mode == "builtin":
            try:
                security_config = load_config_file("security")
                stored_username = security_config.get("BUILTIN_USERNAME", "")
                stored_hash = security_config.get("BUILTIN_PASSWORD_HASH", "")

                # Check credentials
                if username == stored_username and check_password_hash(stored_hash, password):
                    session['user_id'] = username
                    session.permanent = remember_me
                    clear_failed_logins(username)
                    logger.info(f"Login successful for user '{username}' from IP {ip_address} (builtin auth, remember_me={remember_me})")
                    return jsonify({"success": True})
                else:
                    return _failed_login_response(username, ip_address)

            except Exception as e:
                logger.error_trace(f"Built-in auth error: {e}")
                return jsonify({"error": "Authentication system error"}), 500

        # CWA database authentication mode
        if auth_mode == "cwa":
            # Validate CWA database path
            if not os.path.isfile(CWA_DB_PATH):
                logger.error(f"CWA_DB_PATH is set to {CWA_DB_PATH} but this is not a valid path")
                return jsonify({"error": "Database configuration error"}), 500

            try:
                db_path = os.fspath(CWA_DB_PATH)
                db_uri = f"file:{db_path}?mode=ro&immutable=1"
                conn = sqlite3.connect(db_uri, uri=True)
                cur = conn.cursor()
                cur.execute("SELECT password FROM user WHERE name = ?", (username,))
                row = cur.fetchone()
                conn.close()

                # Check if user exists and password is correct
                if not row or not row[0] or not check_password_hash(row[0], password):
                    return _failed_login_response(username, ip_address)

                # Successful authentication - create session and clear failed attempts
                session['user_id'] = username
                session.permanent = remember_me
                clear_failed_logins(username)
                logger.info(f"Login successful for user '{username}' from IP {ip_address} (CWA auth, remember_me={remember_me})")
                return jsonify({"success": True})

            except Exception as e:
                logger.error_trace(f"CWA database error during login: {e}")
                return jsonify({"error": "Authentication system error"}), 500

        # Should not reach here, but handle gracefully
        return jsonify({"error": "Unknown authentication mode"}), 500

    except Exception as e:
        logger.error_trace(f"Login error: {e}")
        return jsonify({"error": "Login failed"}), 500

@app.route('/api/auth/logout', methods=['POST'])
def api_logout() -> Union[Response, Tuple[Response, int]]:
    """
    Logout endpoint that clears the session.
    
    Returns:
        flask.Response: JSON with success status.
    """
    try:
        # Get client IP address (handles reverse proxy forwarding)
        ip_address = request.headers.get('X-Forwarded-For', request.remote_addr)
        if ip_address and ',' in ip_address:
            ip_address = ip_address.split(',')[0].strip()
        
        username = session.get('user_id', 'unknown')
        session.clear()
        logger.info(f"Logout successful for user '{username}' from IP {ip_address}")
        return jsonify({"success": True})
    except Exception as e:
        logger.error_trace(f"Logout error: {e}")
        return jsonify({"error": "Logout failed"}), 500

@app.route('/api/auth/check', methods=['GET'])
def api_auth_check() -> Union[Response, Tuple[Response, int]]:
    """
    Check if user has a valid session.

    Returns:
        flask.Response: JSON with authentication status, whether auth is required,
        and which auth mode is active.
    """
    try:
        auth_mode = get_auth_mode()

        # If no authentication is configured, access is allowed
        if auth_mode == "none":
            return jsonify({
                "authenticated": True,
                "auth_required": False,
                "auth_mode": "none"
            })

        # Check if user has a valid session
        is_authenticated = 'user_id' in session
        return jsonify({
            "authenticated": is_authenticated,
            "auth_required": True,
            "auth_mode": auth_mode
        })
    except Exception as e:
        logger.error_trace(f"Auth check error: {e}")
        return jsonify({
            "authenticated": False,
            "auth_required": True,
            "auth_mode": "unknown"
        })


@app.route('/api/metadata/providers', methods=['GET'])
@login_required
def api_metadata_providers() -> Union[Response, Tuple[Response, int]]:
    """
    Get list of available metadata providers.

    Returns:
        flask.Response: JSON with list of providers and their status.
    """
    try:
        from cwa_book_downloader.metadata_providers import (
            list_providers,
            get_provider,
            get_provider_kwargs,
        )

        configured_metadata_provider = app_config.get("METADATA_PROVIDER", "")
        providers = []
        for info in list_providers():
            provider_info = {
                "name": info["name"],
                "display_name": info["display_name"],
                "requires_auth": info["requires_auth"],
                "configured": False,
                "available": False,
            }

            # Check if provider is configured and available
            try:
                kwargs = get_provider_kwargs(info["name"])
                provider = get_provider(info["name"], **kwargs)
                provider_info["available"] = provider.is_available()
                provider_info["configured"] = (info["name"] == configured_metadata_provider)
            except Exception:
                pass

            providers.append(provider_info)

        return jsonify({
            "providers": providers,
            "configured_provider": configured_metadata_provider or None
        })
    except Exception as e:
        logger.error_trace(f"Metadata providers error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/metadata/search', methods=['GET'])
@login_required
def api_metadata_search() -> Union[Response, Tuple[Response, int]]:
    """
    Search for books using the configured metadata provider.

    Query Parameters:
        query (str): Search query (required)
        limit (int): Maximum number of results (default: 40, max: 100)
        sort (str): Sort order - relevance, popularity, rating, newest, oldest (default: relevance)
        [dynamic fields]: Provider-specific search fields passed as query params

    Returns:
        flask.Response: JSON with list of books from metadata provider.
    """
    try:
        from cwa_book_downloader.metadata_providers import (
            get_configured_provider,
            MetadataSearchOptions,
            SortOrder,
            CheckboxSearchField,
            NumberSearchField,
        )
        from dataclasses import asdict

        query = request.args.get('query', '').strip()

        try:
            limit = min(int(request.args.get('limit', 40)), 100)
        except ValueError:
            limit = 40

        try:
            page = max(1, int(request.args.get('page', 1)))
        except ValueError:
            page = 1

        # Parse sort parameter
        sort_value = request.args.get('sort', 'relevance').lower()
        try:
            sort_order = SortOrder(sort_value)
        except ValueError:
            sort_order = SortOrder.RELEVANCE

        provider = get_configured_provider()
        if not provider:
            return jsonify({
                "error": "No metadata provider configured",
                "message": "No metadata provider configured. Enable one in Settings."
            }), 503

        if not provider.is_available():
            return jsonify({
                "error": f"Metadata provider '{provider.name}' is not available",
                "message": f"{provider.display_name} is not available. Check configuration in Settings."
            }), 503

        # Extract custom search field values from query params
        fields: Dict[str, Any] = {}
        for search_field in provider.search_fields:
            value = request.args.get(search_field.key)
            if value is not None:
                # Strip string values to handle whitespace-only input
                value = value.strip()
                if value != "":
                    # Parse value based on field type
                    if isinstance(search_field, CheckboxSearchField):
                        fields[search_field.key] = value.lower() in ('true', '1', 'yes', 'on')
                    elif isinstance(search_field, NumberSearchField):
                        try:
                            fields[search_field.key] = int(value)
                        except ValueError:
                            pass  # Skip invalid numbers
                    else:
                        fields[search_field.key] = value

        # Require either a query or at least one field value
        if not query and not fields:
            return jsonify({"error": "Either 'query' or search field values are required"}), 400

        options = MetadataSearchOptions(query=query, limit=limit, page=page, sort=sort_order, fields=fields)
        search_result = provider.search_paginated(options)

        # Convert BookMetadata objects to dicts
        books_data = [asdict(book) for book in search_result.books]

        # Transform cover_url to local proxy URLs when caching is enabled
        from cwa_book_downloader.config.env import is_covers_cache_enabled
        if is_covers_cache_enabled():
            import base64
            for book_dict in books_data:
                if book_dict.get('cover_url'):
                    # Encode original URL in the proxy request itself - no need for persistent mapping
                    cache_id = f"{book_dict['provider']}_{book_dict['provider_id']}"
                    encoded_url = base64.urlsafe_b64encode(book_dict['cover_url'].encode()).decode()
                    book_dict['cover_url'] = f"/api/covers/{cache_id}?url={encoded_url}"

        return jsonify({
            "books": books_data,
            "provider": provider.name,
            "query": query,
            "page": search_result.page,
            "total_found": search_result.total_found,
            "has_more": search_result.has_more
        })
    except Exception as e:
        logger.error_trace(f"Metadata search error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/metadata/book/<provider>/<book_id>', methods=['GET'])
@login_required
def api_metadata_book(provider: str, book_id: str) -> Union[Response, Tuple[Response, int]]:
    """
    Get detailed book information from a metadata provider.

    Path Parameters:
        provider (str): Provider name (e.g., "hardcover", "openlibrary")
        book_id (str): Book ID in the provider's system

    Returns:
        flask.Response: JSON with book details.
    """
    try:
        from cwa_book_downloader.metadata_providers import (
            get_provider,
            is_provider_registered,
            get_provider_kwargs,
        )
        from dataclasses import asdict

        if not is_provider_registered(provider):
            return jsonify({"error": f"Unknown metadata provider: {provider}"}), 400

        # Get provider instance with appropriate configuration
        kwargs = get_provider_kwargs(provider)
        prov = get_provider(provider, **kwargs)

        if not prov.is_available():
            return jsonify({"error": f"Provider '{provider}' is not available"}), 503

        book = prov.get_book(book_id)
        if not book:
            return jsonify({"error": "Book not found"}), 404

        book_dict = asdict(book)

        # Transform cover_url to local proxy URL when caching is enabled
        from cwa_book_downloader.config.env import is_covers_cache_enabled
        if is_covers_cache_enabled() and book_dict.get('cover_url'):
            import base64
            cache_id = f"{provider}_{book_id}"
            encoded_url = base64.urlsafe_b64encode(book_dict['cover_url'].encode()).decode()
            book_dict['cover_url'] = f"/api/covers/{cache_id}?url={encoded_url}"

        return jsonify(book_dict)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error_trace(f"Metadata book error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/releases', methods=['GET'])
@login_required
def api_releases() -> Union[Response, Tuple[Response, int]]:
    """
    Search for downloadable releases of a book.

    This endpoint takes book metadata and searches available release sources
    (e.g., Anna's Archive, Libgen) for downloadable files.

    Query Parameters:
        provider (str): Metadata provider name (required)
        book_id (str): Book ID from metadata provider (required)
        source (str): Release source to search (optional, default: all)

    Returns:
        flask.Response: JSON with list of available releases.
    """
    try:
        from cwa_book_downloader.metadata_providers import (
            get_provider,
            is_provider_registered,
            get_provider_kwargs,
        )
        from cwa_book_downloader.release_sources import get_source, list_available_sources, serialize_column_config
        from dataclasses import asdict

        provider = request.args.get('provider', '').strip()
        book_id = request.args.get('book_id', '').strip()
        source_filter = request.args.get('source', '').strip()
        # Accept title/author from frontend to avoid re-fetching metadata
        title_param = request.args.get('title', '').strip()
        author_param = request.args.get('author', '').strip()
        expand_search = request.args.get('expand_search', '').lower() == 'true'
        # Accept language codes for filtering (comma-separated)
        languages_param = request.args.get('languages', '').strip()
        languages = [lang.strip() for lang in languages_param.split(',') if lang.strip()] if languages_param else None

        if not provider or not book_id:
            return jsonify({"error": "Parameters 'provider' and 'book_id' are required"}), 400

        if not is_provider_registered(provider):
            return jsonify({"error": f"Unknown metadata provider: {provider}"}), 400

        # Get book metadata from provider
        kwargs = get_provider_kwargs(provider)
        prov = get_provider(provider, **kwargs)
        book = prov.get_book(book_id)

        if not book:
            return jsonify({"error": "Book not found in metadata provider"}), 404

        # Override title from frontend if available (search results may have better data)
        # Note: We intentionally DON'T override authors here - get_book() now returns
        # filtered authors (primary authors only, excluding translators/narrators),
        # which gives better release search results than the unfiltered search data
        if title_param:
            book.title = title_param

        # Determine which release sources to search
        if source_filter:
            sources_to_search = [source_filter]
        else:
            # Search all available sources
            sources_to_search = [src["name"] for src in list_available_sources()]

        # Search each source for releases
        all_releases = []
        errors = []
        source_instances = {}  # Keep source instances for column config

        for source_name in sources_to_search:
            try:
                source = get_source(source_name)
                source_instances[source_name] = source
                logger.debug(f"Searching {source_name} for '{book.title}' by {book.authors} (expand={expand_search})")
                releases = source.search(book, expand_search=expand_search, languages=languages)
                all_releases.extend(releases)
            except ValueError:
                errors.append(f"Unknown source: {source_name}")
            except Exception as e:
                logger.warning(f"Release search failed for source {source_name}: {e}")
                errors.append(f"{source_name}: {str(e)}")

        # Convert Release objects to dicts
        releases_data = [asdict(release) for release in all_releases]

        # Get column config from the first source searched
        # Reuse the same instance to get any dynamic data (e.g., online_servers for IRC)
        column_config = None
        if sources_to_search and sources_to_search[0] in source_instances:
            try:
                first_source = source_instances[sources_to_search[0]]
                column_config = serialize_column_config(first_source.get_column_config())
            except Exception as e:
                logger.warning(f"Failed to get column config: {e}")

        # Convert book to dict and transform cover_url
        book_dict = asdict(book)
        from cwa_book_downloader.config.env import is_covers_cache_enabled
        if is_covers_cache_enabled() and book_dict.get('cover_url'):
            import base64
            cache_id = f"{provider}_{book_id}"
            encoded_url = base64.urlsafe_b64encode(book_dict['cover_url'].encode()).decode()
            book_dict['cover_url'] = f"/api/covers/{cache_id}?url={encoded_url}"

        # Get search info from direct_download source (if it was searched)
        search_info = {}
        if "direct_download" in source_instances:
            dd_source = source_instances["direct_download"]
            if hasattr(dd_source, 'last_search_type'):
                search_info["direct_download"] = {
                    "search_type": dd_source.last_search_type
                }

        response = {
            "releases": releases_data,
            "book": book_dict,
            "sources_searched": sources_to_search,
            "column_config": column_config,
            "search_info": search_info,
        }

        if errors:
            response["errors"] = errors

        # If no releases found and there were errors, return 503 with error message
        # This matches the behavior of /api/search when Anna's Archive is unreachable
        if not releases_data and errors:
            # Use the first error message (typically the most relevant)
            error_message = errors[0]
            # Strip the source prefix if present (e.g., "direct_download: message" -> "message")
            if ": " in error_message:
                error_message = error_message.split(": ", 1)[1]
            return jsonify({"error": error_message}), 503

        return jsonify(response)
    except Exception as e:
        logger.error_trace(f"Releases search error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/release-sources', methods=['GET'])
@login_required
def api_release_sources() -> Union[Response, Tuple[Response, int]]:
    """
    Get available release sources from the plugin registry.

    Returns:
        flask.Response: JSON list of available release sources.
    """
    try:
        from cwa_book_downloader.release_sources import list_available_sources
        sources = list_available_sources()
        return jsonify(sources)
    except Exception as e:
        logger.error_trace(f"Release sources error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/settings', methods=['GET'])
@login_required
def api_settings_get_all() -> Union[Response, Tuple[Response, int]]:
    """
    Get all settings tabs with their fields and current values.

    Returns:
        flask.Response: JSON with all settings tabs.
    """
    try:
        from cwa_book_downloader.core.settings_registry import serialize_all_settings

        # Ensure settings are registered by importing settings modules
        # This triggers the @register_settings decorators
        import cwa_book_downloader.config.settings  # noqa: F401
        import cwa_book_downloader.config.security  # noqa: F401

        data = serialize_all_settings(include_values=True)
        return jsonify(data)
    except Exception as e:
        logger.error_trace(f"Settings get error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/settings/<tab_name>', methods=['GET'])
@login_required
def api_settings_get_tab(tab_name: str) -> Union[Response, Tuple[Response, int]]:
    """
    Get settings for a specific tab.

    Path Parameters:
        tab_name (str): Settings tab name (e.g., "general", "hardcover")

    Returns:
        flask.Response: JSON with tab settings and values.
    """
    try:
        from cwa_book_downloader.core.settings_registry import (
            get_settings_tab,
            serialize_tab,
        )

        # Ensure settings are registered
        import cwa_book_downloader.config.settings  # noqa: F401
        import cwa_book_downloader.config.security  # noqa: F401

        tab = get_settings_tab(tab_name)
        if not tab:
            return jsonify({"error": f"Unknown settings tab: {tab_name}"}), 404

        return jsonify(serialize_tab(tab, include_values=True))
    except Exception as e:
        logger.error_trace(f"Settings get tab error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/settings/<tab_name>', methods=['PUT'])
@login_required
def api_settings_update_tab(tab_name: str) -> Union[Response, Tuple[Response, int]]:
    """
    Update settings for a specific tab.

    Path Parameters:
        tab_name (str): Settings tab name

    Request Body:
        JSON object with setting keys and values to update.

    Returns:
        flask.Response: JSON with update result.
    """
    try:
        from cwa_book_downloader.core.settings_registry import (
            get_settings_tab,
            update_settings,
        )

        # Ensure settings are registered
        import cwa_book_downloader.config.settings  # noqa: F401
        import cwa_book_downloader.config.security  # noqa: F401

        tab = get_settings_tab(tab_name)
        if not tab:
            return jsonify({"error": f"Unknown settings tab: {tab_name}"}), 404

        values = request.get_json()
        if values is None or not isinstance(values, dict):
            return jsonify({"error": "Request body must be a JSON object"}), 400

        # If no values to update, return success with empty updated list
        if not values:
            return jsonify({"success": True, "message": "No changes to save", "updated": []})

        result = update_settings(tab_name, values)

        if result["success"]:
            return jsonify(result)
        else:
            return jsonify(result), 400
    except Exception as e:
        logger.error_trace(f"Settings update error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/settings/<tab_name>/action/<action_key>', methods=['POST'])
@login_required
def api_settings_execute_action(tab_name: str, action_key: str) -> Union[Response, Tuple[Response, int]]:
    """
    Execute a settings action (e.g., test connection).

    Path Parameters:
        tab_name (str): Settings tab name
        action_key (str): Action key to execute

    Request Body (optional):
        JSON object with current form values (unsaved)

    Returns:
        flask.Response: JSON with action result.
    """
    try:
        from cwa_book_downloader.core.settings_registry import execute_action

        # Ensure settings are registered
        import cwa_book_downloader.config.settings  # noqa: F401
        import cwa_book_downloader.config.security  # noqa: F401

        # Get current form values if provided (for testing with unsaved values)
        current_values = request.get_json(silent=True) or {}

        result = execute_action(tab_name, action_key, current_values)

        if result["success"]:
            return jsonify(result)
        else:
            return jsonify(result), 400
    except Exception as e:
        logger.error_trace(f"Settings action error: {e}")
        return jsonify({"error": str(e)}), 500


# Catch-all route for React Router (must be last)
# This handles client-side routing by serving index.html for any unmatched routes
@app.route('/<path:path>')
def catch_all(path: str) -> Response:
    """
    Serve the React app for any route not matched by API endpoints.
    This allows React Router to handle client-side routing.
    Authentication is handled by the React app itself.
    """
    # If the request is for an API endpoint or static file, let it 404
    if path.startswith('api/') or path.startswith('assets/'):
        return jsonify({"error": "Resource not found"}), 404
    # Otherwise serve the React app
    return send_from_directory(FRONTEND_DIST, 'index.html')

# WebSocket event handlers
@socketio.on('connect')
def handle_connect():
    """Handle client connection."""
    logger.info("WebSocket client connected")
    
    # Track the connection (triggers warmup callbacks on first connect)
    ws_manager.client_connected()
    
    # Send initial status to the newly connected client
    try:
        status = backend.queue_status()
        emit('status_update', status)
    except Exception as e:
        logger.error(f"Error sending initial status: {e}")

@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnection."""  
    logger.info("WebSocket client disconnected")
    
    # Track the disconnection
    ws_manager.client_disconnected()

@socketio.on('request_status')
def handle_status_request():
    """Handle manual status request from client."""
    try:
        status = backend.queue_status()
        emit('status_update', status)
    except Exception as e:
        logger.error(f"Error handling status request: {e}")
        emit('error', {'message': 'Failed to get status'})

logger.log_resource_usage()

if __name__ == '__main__':
    logger.info(f"Starting Flask application with WebSocket support on {FLASK_HOST}:{FLASK_PORT} (debug={DEBUG})")
    socketio.run(
        app,
        host=FLASK_HOST,
        port=FLASK_PORT,
        debug=DEBUG,
        allow_unsafe_werkzeug=True  # For development only
    )
