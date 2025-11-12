"""Flask web application for book download service with URL rewrite support."""

import logging
import io, re, os
import sqlite3
import time
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, request, jsonify, send_file, send_from_directory, session
from flask_cors import CORS
from flask_socketio import SocketIO, emit
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash
from werkzeug.wrappers import Response
from flask import url_for as flask_url_for
import typing

from logger import setup_logger
from config import _SUPPORTED_BOOK_LANGUAGE, BOOK_LANGUAGE, SUPPORTED_FORMATS
from env import FLASK_HOST, FLASK_PORT, APP_ENV, CWA_DB_PATH, DEBUG, USING_EXTERNAL_BYPASSER, BUILD_VERSION, RELEASE_VERSION, CALIBRE_WEB_URL
import backend

from models import SearchFilters
from websocket_manager import ws_manager

logger = setup_logger(__name__)
app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app)  # type: ignore
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0  # Disable caching
app.config['APPLICATION_ROOT'] = '/'

# Determine async mode based on environment
# In production with Gunicorn + gevent worker, use 'gevent'
# In development with Flask dev server, use 'threading'
if APP_ENV == 'prod':
    async_mode = 'gevent'
else:
    async_mode = 'threading'

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

# Rate limiting for login attempts
# Structure: {username: {'count': int, 'lockout_until': datetime}}
failed_login_attempts: typing.Dict[str, typing.Dict[str, typing.Any]] = {}
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

# Flask logger
app.logger.handlers = logger.handlers
app.logger.setLevel(logger.level)
# Also handle Werkzeug's logger
werkzeug_logger = logging.getLogger('werkzeug')
werkzeug_logger.handlers = logger.handlers
werkzeug_logger.setLevel(logger.level)

# Set up authentication defaults
# The secret key will reset every time we restart, which will
# require users to authenticate again

# Auto-detect HTTPS for secure cookies
# Can be overridden with SESSION_COOKIE_SECURE environment variable
session_cookie_secure_env = os.getenv('SESSION_COOKIE_SECURE', 'auto').lower()
if session_cookie_secure_env == 'auto':
    # Auto-detect: check if we're behind a reverse proxy with HTTPS
    # This will be determined per-request, but default to False for local HTTP
    SESSION_COOKIE_SECURE = False
elif session_cookie_secure_env in ['true', 'yes', '1']:
    SESSION_COOKIE_SECURE = True
else:
    SESSION_COOKIE_SECURE = False

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
        # If the CWA_DB_PATH variable exists, but isn't a valid
        # path, return a server error
        if CWA_DB_PATH is not None and not os.path.isfile(CWA_DB_PATH):
            logger.error(f"CWA_DB_PATH is set to {CWA_DB_PATH} but this is not a valid path")
            return jsonify({"error": "Internal Server Error"}), 500
        
        # If no database is configured, allow access
        if not CWA_DB_PATH:
            return f(*args, **kwargs)
        
        # Check if user has a valid session
        if 'user_id' not in session:
            return jsonify({"error": "Unauthorized"}), 401
        
        return f(*args, **kwargs)
    return decorated_function

def register_dual_routes(app : Flask) -> None:
    """
    Register each route both with and without the /request prefix.
    This function should be called after all routes are defined.
    """
    # Store original url_map rules
    rules = list(app.url_map.iter_rules())
    
    # Add /request prefix to each rule
    for rule in rules:
        if rule.rule != '/request/' and rule.rule != '/request':  # Skip if it's already a request route
            # Create new routes with /request prefix, both with and without trailing slash
            base_rule = rule.rule[:-1] if rule.rule.endswith('/') else rule.rule
            if base_rule == '':  # Special case for root path
                app.add_url_rule('/request', f"root_request", 
                               view_func=app.view_functions[rule.endpoint],
                               methods=rule.methods)
                app.add_url_rule('/request/', f"root_request_slash", 
                               view_func=app.view_functions[rule.endpoint],
                               methods=rule.methods)
            else:
                app.add_url_rule(f"/request{base_rule}", 
                               f"{rule.endpoint}_request",
                               view_func=app.view_functions[rule.endpoint],
                               methods=rule.methods)
                app.add_url_rule(f"/request{base_rule}/", 
                               f"{rule.endpoint}_request_slash",
                               view_func=app.view_functions[rule.endpoint],
                               methods=rule.methods)
    app.jinja_env.globals['url_for'] = url_for_with_request

def url_for_with_request(endpoint : str, **values : typing.Any) -> str:
    """Generate URLs with /request prefix by default."""
    if endpoint == 'static' or endpoint == 'serve_frontend_assets':
        # For static files, add /request prefix
        url = flask_url_for(endpoint, **values)
        return f"/request{url}"
    return flask_url_for(endpoint, **values)

# Serve frontend static files
@app.route('/assets/<path:filename>')
def serve_frontend_assets(filename: str) -> Response:
    """
    Serve static assets from the built frontend.
    """
    return send_from_directory(os.path.join(app.root_path, 'frontend-dist', 'assets'), filename)

@app.route('/')
def index() -> Response:
    """
    Serve the React frontend application.
    Authentication is handled by the React app itself.
    """
    return send_from_directory(os.path.join(app.root_path, 'frontend-dist'), 'index.html')

@app.route('/logo.png')
def logo() -> Response:
    """
    Serve logo from built frontend assets.
    """
    return send_from_directory(os.path.join(app.root_path, 'frontend-dist'),
        'logo.png', mimetype='image/png')

@app.route('/favicon.ico')
@app.route('/favico<path:_>')
@app.route('/request/favico<path:_>')
@app.route('/request/static/favico<path:_>')
def favicon(_ : typing.Any = None) -> Response:
    """
    Serve favicon from built frontend assets.
    """
    return send_from_directory(os.path.join(app.root_path, 'frontend-dist'),
        'favicon.ico', mimetype='image/vnd.microsoft.icon')

from typing import Union, Tuple

if DEBUG:
    import subprocess
    import time
    if USING_EXTERNAL_BYPASSER:
        STOP_GUI = lambda: None  # No-op for external bypasser
    else:
        from cloudflare_bypasser import _reset_driver as STOP_GUI
    @app.route('/debug', methods=['GET'])
    @login_required
    def debug() -> Union[Response, Tuple[Response, int]]:
        """
        This will run the /app/debug.sh script, which will generate a debug zip with all the logs
        The file will be named /tmp/cwa-book-downloader-debug.zip
        And then return it to the user
        """
        try:
            # Run the debug script
            STOP_GUI()
            time.sleep(1)
            result = subprocess.run(['/app/genDebug.sh'], capture_output=True, text=True, check=True)
            if result.returncode != 0:
                raise Exception(f"Debug script failed: {result.stderr}")
            logger.info(f"Debug script executed: {result.stdout}")
            debug_file_path = result.stdout.strip().split('\n')[-1]
            if not os.path.exists(debug_file_path):
                logger.error("Debug zip file not found after running debug script")
                return jsonify({"error": "Failed to generate debug information"}), 500
                
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

@app.route('/api/config', methods=['GET'])
@login_required
def api_config() -> Union[Response, Tuple[Response, int]]:
    """
    Get application configuration for frontend.
    """
    try:
        config = {
            "calibre_web_url": CALIBRE_WEB_URL,
            "debug": DEBUG,
            "app_env": APP_ENV,
            "build_version": BUILD_VERSION,
            "release_version": RELEASE_VERSION,
            "book_languages": _SUPPORTED_BOOK_LANGUAGE,
            "default_language": BOOK_LANGUAGE,
            "supported_formats": SUPPORTED_FORMATS
        }
        return jsonify(config)
    except Exception as e:
        logger.error_trace(f"Config error: {e}")
        return jsonify({"error": str(e)}), 500

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
        # Santize the file name
        file_name = book_info.title
        file_name = re.sub(r'[\\/:*?"<>|]', '_', file_name.strip())[:245]
        file_extension = book_info.format
        # Prepare the file for sending to the client
        data = io.BytesIO(file_data)
        return send_file(
            data,
            download_name=f"{file_name}.{file_extension}",
            as_attachment=True
        )

    except Exception as e:
        logger.error_trace(f"Local download error: {e}")
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
        if ws_manager and ws_manager.is_enabled():
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

@app.route('/api/auth/login', methods=['POST'])
def api_login() -> Union[Response, Tuple[Response, int]]:
    """
    Login endpoint that validates credentials and creates a session.
    Includes rate limiting: 10 failed attempts = 30 minute lockout.
    
    Request Body:
        username (str): Username
        password (str): Password
        remember_me (bool): Whether to extend session duration
        
    Returns:
        flask.Response: JSON with success status or error message.
    """
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
        
        # If the database doesn't exist, authentication always succeeds
        if not CWA_DB_PATH:
            session['user_id'] = username
            session.permanent = remember_me
            clear_failed_logins(username)
            logger.info(f"Login successful for user '{username}' from IP {ip_address} (no DB configured)")
            return jsonify({"success": True})
        
        # If the CWA_DB_PATH variable exists, but isn't a valid path, return error
        if not os.path.isfile(CWA_DB_PATH):
            logger.error(f"CWA_DB_PATH is set to {CWA_DB_PATH} but this is not a valid path")
            return jsonify({"error": "Database configuration error"}), 500
        
        # Validate credentials against database
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
                # Record failed login attempt
                is_now_locked = record_failed_login(username, ip_address)
                
                if is_now_locked:
                    return jsonify({
                        "error": f"Account locked due to {MAX_LOGIN_ATTEMPTS} failed login attempts. Try again in {LOCKOUT_DURATION_MINUTES} minutes."
                    }), 429
                else:
                    attempts_remaining = MAX_LOGIN_ATTEMPTS - failed_login_attempts[username]['count']
                    # Only show attempts remaining when 5 or fewer attempts remain (after 6+ failed attempts)
                    if attempts_remaining <= 5:
                        return jsonify({
                            "error": f"Invalid username or password. {attempts_remaining} attempts remaining."
                        }), 401
                    else:
                        return jsonify({
                            "error": "Invalid username or password."
                        }), 401
            
            # Successful authentication - create session and clear failed attempts
            session['user_id'] = username
            session.permanent = remember_me
            clear_failed_logins(username)
            logger.info(f"Login successful for user '{username}' from IP {ip_address} (remember_me={remember_me})")
            return jsonify({"success": True})
            
        except Exception as e:
            logger.error_trace(f"Database error during login: {e}")
            return jsonify({"error": "Authentication system error"}), 500
            
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
        flask.Response: JSON with authentication status and whether auth is required.
    """
    try:
        # If no database is configured, authentication is not required
        if not CWA_DB_PATH:
            return jsonify({
                "authenticated": True,
                "auth_required": False
            })
        
        # Check if user has a valid session
        is_authenticated = 'user_id' in session
        return jsonify({
            "authenticated": is_authenticated,
            "auth_required": True
        })
    except Exception as e:
        logger.error_trace(f"Auth check error: {e}")
        return jsonify({
            "authenticated": False,
            "auth_required": True
        })

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
    return send_from_directory(os.path.join(app.root_path, 'frontend-dist'), 'index.html')

# Register all routes with /request prefix
register_dual_routes(app)

# WebSocket event handlers
@socketio.on('connect')
def handle_connect():
    """Handle client connection."""
    logger.info("WebSocket client connected")
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
    logger.info(f"Starting Flask application with WebSocket support on {FLASK_HOST}:{FLASK_PORT} IN {APP_ENV} mode")
    socketio.run(
        app,
        host=FLASK_HOST,
        port=FLASK_PORT,
        debug=DEBUG,
        allow_unsafe_werkzeug=True  # For development only
    )
