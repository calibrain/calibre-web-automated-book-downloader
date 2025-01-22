"""Flask web application for book download service with URL rewrite support."""

import logging
import io, re, os
from flask import Flask, request, jsonify, render_template, send_file, send_from_directory
from werkzeug.middleware.proxy_fix import ProxyFix
from flask import url_for as flask_url_for
from functools import partial

from logger import setup_logger
from config import FLASK_HOST, FLASK_PORT, FLASK_DEBUG, _SUPPORTED_BOOK_LANGUAGE, BOOK_LANGUAGE
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

def register_dual_routes(app):
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

def url_for_with_request(endpoint, **values):
    """Generate URLs with /request prefix by default."""
    if endpoint == 'static':
        # For static files, add /request prefix
        url = flask_url_for(endpoint, **values)
        return f"/request{url}"
    return flask_url_for(endpoint, **values)

@app.route('/')
def index():
    """
    Render main page with search and status table.
    """
    return render_template('index.html', book_languages=_SUPPORTED_BOOK_LANGUAGE, default_language=BOOK_LANGUAGE)

@app.route('/favico<path:_>')
@app.route('/request/favico<path:_>')
@app.route('/request/static/favico<path:_>')
def favicon(_):
    return send_from_directory(os.path.join(app.root_path, 'static', 'media'),
        'favicon.ico', mimetype='image/vnd.microsoft.icon')

@app.route('/api/search', methods=['GET'])
def api_search():
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

    Returns:
        flask.Response: JSON array of matching books or empty array if no query.
    """
    query = request.args.get('query', '')

    filters = SearchFilters(
        isbn = request.args.getlist('isbn'),
        author = request.args.getlist('author'),
        title = request.args.getlist('title'),
        lang = request.args.getlist('lang'),
        sort = request.args.get('sort'),
        content = request.args.getlist('content'),
    )

    if not query and not any(vars(filters).values()):
        return jsonify([])

    try:
        books = backend.search_books(query, filters)
        return jsonify(books)
    except Exception as e:
        logger.error(f"Search error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/info', methods=['GET'])
def api_info():
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
        logger.error(f"Info error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/download', methods=['GET'])
def api_download():
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
        logger.error(f"Download error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/status', methods=['GET'])
def api_status():
    """
    Get current download queue status.

    Returns:
        flask.Response: JSON object with queue status.
    """
    try:
        status = backend.queue_status()
        return jsonify(status)
    except Exception as e:
        logger.error(f"Status error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/localdownload', methods=['GET'])
def api_local_download():
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
        file_data, file_name = backend.get_book_data(book_id)
        if file_data is None:
            # Book data not found or not available
            return jsonify({"error": "File not found"}), 404
        # Santize the file name
        file_name = re.sub(r'[\\/:*?"<>|]', '_', file_name.strip())[:255]
        # Prepare the file for sending to the client
        epub_file = io.BytesIO(file_data)
        # Typically EPUB mime-type: 'application/epub+zip'
        return send_file(
            epub_file,
            mimetype='application/epub+zip',
            download_name=f"{file_name}.epub",
            as_attachment=True
        )

    except Exception as e:
        logger.error(f"Local download error: {e}")
        return jsonify({"error": str(e)}), 500

@app.errorhandler(404)
def not_found_error(error):
    """
    Handle 404 (Not Found) errors.

    Args:
        error (HTTPException): The 404 error raised by Flask.

    Returns:
        flask.Response: JSON error message with 404 status.
    """
    logger.warning(f"404 error: {request.url}")
    return jsonify({"error": "Resource not found"}), 404

@app.errorhandler(500)
def internal_error(error):
    """
    Handle 500 (Internal Server) errors.

    Args:
        error (HTTPException): The 500 error raised by Flask.

    Returns:
        flask.Response: JSON error message with 500 status.
    """
    logger.error(f"500 error: {error}")
    return jsonify({"error": "Internal server error"}), 500

if __name__ == '__main__':
    # Register all routes with /request prefix
    register_dual_routes(app)
    
    logger.info(f"Starting Flask application on {FLASK_HOST}:{FLASK_PORT}")
    app.run(
        host=FLASK_HOST,
        port=FLASK_PORT,
        debug=FLASK_DEBUG  # Disable debug mode in production
    )
