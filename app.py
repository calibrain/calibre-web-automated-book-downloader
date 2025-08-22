"""Flask web application for book download service with URL rewrite support."""

import logging
import io, re, os
import sqlite3
from functools import wraps
from flask import Flask, request, jsonify, render_template, send_file, send_from_directory
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash
from werkzeug.wrappers import Response
from flask import url_for as flask_url_for
import typing

from logger import setup_logger
from config import _SUPPORTED_BOOK_LANGUAGE, BOOK_LANGUAGE
from env import FLASK_HOST, FLASK_PORT, APP_ENV, CWA_DB_PATH, DEBUG
import backend

from models import SearchFilters

logger = setup_logger(__name__)
app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app)  # type: ignore
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0  # Disable caching
app.config['APPLICATION_ROOT'] = '/'

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
app.config.update(
    SECRET_KEY = os.urandom(64)
)

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # If the CWA_DB_PATH variable exists, but isn't a valid
        # path, return a server error
        if CWA_DB_PATH is not None and not os.path.isfile(CWA_DB_PATH):
            logger.error(f"CWA_DB_PATH is set to {CWA_DB_PATH} but this is not a valid path")
            return Response("Internal Server Error", 500)
        if not authenticate():
            return Response(
                response="Unauthorized",
                status=401,
                headers={
                    "WWW-Authenticate": 'Basic realm="Calibre-Web-Automated-Book-Downloader"',
                },
            )
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
    if endpoint == 'static':
        # For static files, add /request prefix
        url = flask_url_for(endpoint, **values)
        return f"/request{url}"
    return flask_url_for(endpoint, **values)

@app.route('/')
@login_required
def index() -> str:
    """
    Render main page with search and status table.
    """
    return render_template('index.html', book_languages=_SUPPORTED_BOOK_LANGUAGE, default_language=BOOK_LANGUAGE, debug=DEBUG)

@app.route('/favico<path:_>')
@app.route('/request/favico<path:_>')
@app.route('/request/static/favico<path:_>')
def favicon(_ : typing.Any) -> Response:
    return send_from_directory(os.path.join(app.root_path, 'static', 'media'),
        'favicon.ico', mimetype='image/vnd.microsoft.icon')

from typing import Union, Tuple

if DEBUG:
    import subprocess
    import time
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
        success = backend.queue_book(book_id)
        if success:
            return jsonify({"status": "queued"})
        return jsonify({"error": "Failed to queue book"}), 500
    except Exception as e:
        logger.error_trace(f"Download error: {e}")
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

def authenticate() -> bool:
    """
    Helper function that validates Basic credentials
    against a Calibre-Web app.db SQLite database

    Database structure:
    - Table 'user' with columns: 'name' (username), 'password'
    """

    # If the database doesn't exist, the user is always authenticated
    if not CWA_DB_PATH:
        return True

    # If no authorization object exists, return false to prompt
    # a request to the user
    if not request.authorization:
        return False

    username = request.authorization.get("username")
    password = request.authorization.get("password")

    # Validate credentials against database
    try:
        # Open database in true read-only mode to avoid journal/WAL writes on RO mounts
        db_path = os.fspath(CWA_DB_PATH)
        db_uri = f"file:{db_path}?mode=ro&immutable=1"
        conn = sqlite3.connect(db_uri, uri=True)
        cur = conn.cursor()
        cur.execute("SELECT password FROM user WHERE name = ?", (username,))
        row = cur.fetchone()
        conn.close()

        # Check if user exists and password is correct
        if not row or not row[0] or not check_password_hash(row[0], password):
            logger.error("User not found or password check failed")
            return False

    except Exception as e:
        logger.error_trace(f"CWA DB or authentication send_from_directory: {e}")
        return False

    logger.info(f"Authentication successful for user {username}")
    return True

# Register all routes with /request prefix
register_dual_routes(app)

logger.log_resource_usage()

if __name__ == '__main__':
    logger.info(f"Starting Flask application on {FLASK_HOST}:{FLASK_PORT} IN {APP_ENV} mode")
    app.run(
        host=FLASK_HOST,
        port=FLASK_PORT,
        debug=DEBUG 
    )
