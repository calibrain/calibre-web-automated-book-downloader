"""Flask app - routes, WebSocket handlers, and middleware."""

import binascii
import logging
import os
import re
import sqlite3
import time
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from functools import wraps
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, NoReturn, cast

from flask import Flask, g, jsonify, request, send_file, send_from_directory, session
from flask_cors import CORS
from flask_socketio import SocketIO, emit
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash
from werkzeug.wrappers import Response

from shelfmark.api.websocket import ws_manager
from shelfmark.config.env import (
    BUILD_VERSION,
    CONFIG_DIR,
    CWA_DB_PATH,
    DISABLE_LOCAL_AUTH,
    FLASK_HOST,
    FLASK_PORT,
    HIDE_LOCAL_AUTH,
    OIDC_AUTO_REDIRECT,
    RELEASE_VERSION,
    SESSION_COOKIE_NAME,
    SESSION_COOKIE_SECURE_ENV,
    _is_config_dir_writable,
    string_to_bool,
)
from shelfmark.config.security import _migrate_security_settings
from shelfmark.config.settings import (
    _SUPPORTED_BOOK_LANGUAGE,
    migrate_audiobook_format_settings,
)
from shelfmark.core import api_key as api_key_module  # module access lets tests monkeypatch the key
from shelfmark.core import search_deadline
from shelfmark.core.activity_view_state_service import ActivityViewStateService
from shelfmark.core.auth_modes import (
    get_auth_check_admin_status,
    is_settings_or_onboarding_path,
    load_active_auth_mode,
    requires_admin_for_settings_access,
)
from shelfmark.core.config import config as app_config
from shelfmark.core.cwa_user_sync import upsert_cwa_user
from shelfmark.core.download_history_service import DownloadHistoryService
from shelfmark.core.external_user_linking import upsert_external_user
from shelfmark.core.logger import setup_logger
from shelfmark.core.models import TERMINAL_QUEUE_STATUSES, QueueStatus, SearchFilters
from shelfmark.core.notifications import (
    NotificationContext,
    NotificationEvent,
    notify_admin,
    notify_user,
)
from shelfmark.core.prefix_middleware import PrefixMiddleware
from shelfmark.core.release_inspect_routes import register_release_inspect_routes
from shelfmark.core.request_helpers import (
    coerce_bool,
    emit_ws_event,
    get_session_db_user_id,
    load_users_request_policy_settings,
    normalize_optional_text,
    normalize_positive_int,
)
from shelfmark.core.request_policy import (
    PolicyMode,
    get_source_content_type_capabilities,
    merge_request_policy_settings,
    normalize_content_type,
    normalize_source,
    resolve_policy_mode,
)
from shelfmark.core.requests_service import (
    reopen_failed_request,
    sync_delivery_states_from_queue_status,
)
from shelfmark.core.user_db import UserDB
from shelfmark.core.utils import AUDIOBOOK_FORMATS, normalize_base_path
from shelfmark.core.viewer_redaction import (
    configure_public_id_key,
    is_public_download_id,
    match_public_download_id,
    redact_status,
    viewer_download_id,
)
from shelfmark.download import orchestrator as backend
from shelfmark.download import warmup
from shelfmark.release_sources import (
    BrowseRecord,
    Release,
    SourceUnavailableError,
    get_source_display_name,
)

if TYPE_CHECKING:
    from shelfmark.metadata_providers import BookMetadata, MetadataProvider

logger = setup_logger(__name__)
FLASK_SECRET_KEY_MIN_BYTES = 32
_OPERATIONAL_ERRORS = (OSError, RuntimeError, TypeError, ValueError, sqlite3.Error)
_IMPORT_OPERATIONAL_ERRORS = (ImportError, *_OPERATIONAL_ERRORS)


def _is_debug_enabled() -> bool:
    debug_value = app_config.get("DEBUG", False)
    if isinstance(debug_value, str):
        return string_to_bool(debug_value)
    return bool(debug_value)


def _raise_runtime_error(message: str) -> NoReturn:
    raise RuntimeError(message)


# Project root is the repository root above the package directory.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIST = PROJECT_ROOT / "frontend-dist"

BASE_PATH = normalize_base_path(normalize_optional_text(app_config.get("URL_BASE", "")))

app = Flask(__name__)
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0  # Disable caching
app.config["APPLICATION_ROOT"] = BASE_PATH or "/"
wsgi_app = cast(Any, ProxyFix(app.wsgi_app, x_host=1, x_port=1))
if BASE_PATH:
    wsgi_app = cast(Any, PrefixMiddleware(wsgi_app, BASE_PATH, bypass_paths={"/api/health"}))
app.wsgi_app = wsgi_app

# Socket.IO async mode.
# We run this app under Gunicorn with a gevent websocket worker (even when DEBUG=true),
# so Socket.IO should always use gevent here.
async_mode = "gevent"
socketio_cors_allowed_origins = "*"

# Initialize Flask-SocketIO with reverse proxy support
socketio_path = f"{BASE_PATH}/socket.io" if BASE_PATH else "/socket.io"
socketio_init_kwargs: dict[str, Any] = {
    "cors_allowed_origins": socketio_cors_allowed_origins,
    "async_mode": async_mode,
    "logger": False,
    "engineio_logger": False,
    # Reverse proxy / Traefik compatibility settings
    "path": socketio_path,
    "ping_timeout": 60,
    "ping_interval": 25,
    # Allow both websocket and polling for better compatibility
    "transports": ["websocket", "polling"],
    # Enable CORS for all origins (you can restrict this in production)
    "allow_upgrades": True,
    # Important for proxies that buffer
    "http_compression": True,
}
socketio = SocketIO(app, **socketio_init_kwargs)

# Initialize WebSocket manager
ws_manager.init_app(app, socketio)


def _user_room_queue_status(user_id: int | None = None) -> dict[str, dict[str, Any]]:
    """Queue status for a non-admin user room, with release details hidden (#1418)."""
    return redact_status(backend.queue_status(user_id=user_id))


ws_manager.set_queue_status_fn(_user_room_queue_status)
logger.info("Flask-SocketIO initialized with async_mode='%s'", async_mode)
logger.info("Socket.IO CORS allowed origins: %s", socketio_cors_allowed_origins)

# Ensure all plugins are loaded before starting the download coordinator.
# This prevents a race condition where the download loop could try to process
# a queued task before its handler (e.g., prowlarr) is registered.
try:
    import_module("shelfmark.metadata_providers")
    import_module("shelfmark.release_sources")
    logger.debug("Plugin modules loaded successfully")
except ImportError as e:
    logger.warning("Failed to import plugin modules: %s", e)

# Migrate legacy security settings if needed
_migrate_security_settings()

# Widen audiobook formats for installs that still carry the old m4b/mp3-only default
migrate_audiobook_format_settings()

# Initialize user database and register multi-user routes
# If CONFIG_DIR doesn't exist or is read-only, multi-user features will be disabled
_user_db_path = str(Path(os.environ.get("CONFIG_DIR", "/config")) / "users.db")
user_db: UserDB | None = None
download_history_service: DownloadHistoryService | None = None
activity_view_state_service: ActivityViewStateService | None = None
try:
    user_db = UserDB(_user_db_path)
    user_db.initialize()
    download_history_service = DownloadHistoryService(_user_db_path)
    activity_view_state_service = ActivityViewStateService(_user_db_path)
    import_module("shelfmark.config.users_settings")
    from shelfmark.core.admin_routes import register_admin_routes
    from shelfmark.core.oidc_routes import register_oidc_routes
    from shelfmark.core.self_user_routes import register_self_user_routes

    register_oidc_routes(app, user_db)
    register_admin_routes(app, user_db)
    register_self_user_routes(app, user_db)
except (sqlite3.OperationalError, OSError) as e:
    logger.warning(
        "User database initialization failed: %s. Multi-user authentication features will be disabled. Ensure CONFIG_DIR (%s) exists and is writable.",
        e,
        os.environ.get("CONFIG_DIR", "/config"),
    )
    user_db = None
    download_history_service = None
    activity_view_state_service = None


def _warn_if_local_admin_missing() -> None:
    """Log a recovery hint when builtin/OIDC auth is active without a local admin."""
    if user_db is None or DISABLE_LOCAL_AUTH:
        return
    auth_mode = load_active_auth_mode()
    if auth_mode not in ("builtin", "oidc"):
        return
    try:
        if user_db.has_admin_with_password():
            return
    except sqlite3.Error:
        return
    logger.warning(
        "AUTH_METHOD=%s is active but no local admin account with a password exists. If no "
        "admin can sign in, start once with AUTH_METHOD=none (keep Shelfmark off the public "
        "internet meanwhile), create a local admin under Settings > Users, then remove the "
        "override.",
        auth_mode,
    )


_warn_if_local_admin_missing()

# Nothing is downloading yet, so anything the last process left "active" or "queued" was
# interrupted. Close those rows out and put the recently interrupted requests back in the
# queue, before the coordinator starts. Assumes one worker process (entrypoint.sh).
if user_db is not None and download_history_service is not None:
    try:
        from shelfmark.core.startup_reconcile import reconcile_interrupted_downloads

        _reconciled = reconcile_interrupted_downloads(user_db, download_history_service)
        if _reconciled["marked"] or _reconciled["reopened"]:
            logger.info(
                "Startup: closed out %d interrupted download(s), reopened %d request(s)",
                _reconciled["marked"],
                _reconciled["reopened"],
            )
    except (ImportError, OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
        logger.warning("Could not reconcile interrupted downloads at startup: %s", exc)

# Start download coordinator
backend.start()

# Pre-solve the direct-download source's protection challenge in the background so the
# first user search does not pay for a cold Chrome bypass. Never blocks startup.
warmup.start()

# Rate limiting for login attempts
# Map usernames to their failed-attempt counters and lockout timestamps.
failed_login_attempts: dict[str, dict[str, Any]] = {}
MAX_LOGIN_ATTEMPTS = 10
LOCKOUT_DURATION_MINUTES = 30
LOGIN_ATTEMPT_WARNING_THRESHOLD = 5


def cleanup_old_lockouts() -> None:
    """Remove expired lockout entries to prevent memory buildup."""
    current_time = datetime.now(UTC)
    expired_users = []
    for username in list(failed_login_attempts):
        lockout_until = _get_lockout_until(username, repair_if_locked=True)
        if lockout_until is not None and lockout_until < current_time:
            expired_users.append(username)
    for username in expired_users:
        logger.info("Lockout expired for user: %s", username)
        del failed_login_attempts[username]


def _get_lockout_until(username: str, *, repair_if_locked: bool = False) -> datetime | None:
    """Return a valid lockout timestamp for the user when one exists.

    When a user has already crossed the lockout threshold but the timestamp is
    missing or malformed, optionally repair the state to keep the lockout in
    force rather than silently letting the user through.
    """
    lockout_state = failed_login_attempts.get(username)
    if lockout_state is None:
        return None

    lockout_until = lockout_state.get("lockout_until")
    if isinstance(lockout_until, datetime):
        return lockout_until

    attempt_count = lockout_state.get("count")
    if repair_if_locked and isinstance(attempt_count, int) and attempt_count >= MAX_LOGIN_ATTEMPTS:
        repaired_lockout_until = datetime.now(UTC) + timedelta(minutes=LOCKOUT_DURATION_MINUTES)
        lockout_state["lockout_until"] = repaired_lockout_until
        logger.warning("Repaired missing lockout timestamp for locked account '%s'", username)
        return repaired_lockout_until

    if lockout_until is not None:
        logger.warning("Ignoring invalid lockout timestamp for user '%s'", username)

    return None


def is_account_locked(username: str) -> bool:
    """Check if an account is currently locked due to failed login attempts."""
    cleanup_old_lockouts()

    if username not in failed_login_attempts:
        return False

    lockout_until = _get_lockout_until(username, repair_if_locked=True)
    return lockout_until is not None and datetime.now(UTC) < lockout_until


def record_failed_login(username: str, ip_address: str) -> bool:
    """Record a failed login attempt and lock account if threshold is reached.

    Returns True if account is now locked, False otherwise.
    """
    if username not in failed_login_attempts:
        failed_login_attempts[username] = {"count": 0}

    failed_login_attempts[username]["count"] += 1
    count = failed_login_attempts[username]["count"]

    logger.warning(
        "Failed login attempt %s/%s for user '%s' from IP %s",
        count,
        MAX_LOGIN_ATTEMPTS,
        username,
        ip_address,
    )

    if count >= MAX_LOGIN_ATTEMPTS:
        lockout_until = datetime.now(UTC) + timedelta(minutes=LOCKOUT_DURATION_MINUTES)
        failed_login_attempts[username]["lockout_until"] = lockout_until
        logger.warning(
            "Account locked for user '%s' until %s due to %s failed login attempts",
            username,
            lockout_until.strftime("%Y-%m-%d %H:%M:%S"),
            count,
        )
        return True

    return False


def clear_failed_logins(username: str) -> None:
    """Clear failed login attempts for a user after successful login."""
    if username in failed_login_attempts:
        del failed_login_attempts[username]
        logger.debug("Cleared failed login attempts for user: %s", username)


def get_client_ip() -> str:
    """Extract client IP address from request, handling reverse proxy forwarding."""
    ip_address = request.headers.get("X-Forwarded-For", request.remote_addr) or "unknown"
    # X-Forwarded-For can contain multiple IPs, take the first one
    if "," in ip_address:
        ip_address = ip_address.split(",")[0].strip()
    return ip_address


def get_auth_mode() -> str:
    """Determine which authentication mode is active.

    Returns "none" only when AUTH_METHOD is explicitly "none"; an unreadable or
    unrecognized AUTH_METHOD fails closed.
    """
    return load_active_auth_mode()


_AUDIOBOOK_CATEGORY_RANGE = (3030, 3049)
_AUDIOBOOK_FORMAT_HINTS = frozenset(AUDIOBOOK_FORMATS)


def _contains_audiobook_format_hint(value: Any) -> bool:
    if not isinstance(value, str):
        return False

    normalized = value.strip().lower()
    if not normalized:
        return False

    tokens = [token for token in re.split(r"[^a-z0-9]+", normalized) if token]
    return any(token in _AUDIOBOOK_FORMAT_HINTS for token in tokens)


def _resolve_release_content_type(data: dict[str, Any], source: Any) -> tuple[str, bool]:
    """Resolve release content type for policy checks and queue payload normalization."""
    extra = data.get("extra")
    if not isinstance(extra, dict):
        extra = {}

    explicit_content_type = data.get("content_type")
    if explicit_content_type is None:
        explicit_content_type = extra.get("content_type")
    if explicit_content_type is not None:
        return normalize_content_type(explicit_content_type), False

    categories = extra.get("categories")
    if isinstance(categories, list):
        min_cat, max_cat = _AUDIOBOOK_CATEGORY_RANGE
        for raw_category in categories:
            try:
                category_id = int(raw_category)
            except TypeError, ValueError:
                continue
            if min_cat <= category_id <= max_cat:
                return "audiobook", True

    candidates: list[Any] = [
        data.get("format"),
        extra.get("format"),
        extra.get("formats_display"),
        data.get("title"),
    ]
    formats = extra.get("formats")
    if isinstance(formats, list):
        candidates.extend(formats)
    else:
        candidates.append(formats)

    if any(_contains_audiobook_format_hint(candidate) for candidate in candidates):
        return "audiobook", True

    capabilities = get_source_content_type_capabilities()
    supported = capabilities.get(normalize_source(source))
    if supported and len(supported) == 1:
        return normalize_content_type(next(iter(supported))), True

    return "ebook", False


def _resolve_policy_mode_for_current_user(*, source: Any, content_type: Any) -> PolicyMode | None:
    """Resolve policy mode for current session, or None when policy guard is bypassed."""
    auth_mode = get_auth_mode()
    if auth_mode == "none":
        return None
    if session.get("is_admin", False):
        return None
    if user_db is None:
        return None

    global_settings = load_users_request_policy_settings()
    db_user_id = session.get("db_user_id")
    user_settings: dict[str, Any] | None = None
    if db_user_id is not None:
        try:
            user_settings = user_db.get_user_settings(int(db_user_id))
        except TypeError, ValueError:
            user_settings = None

    effective = merge_request_policy_settings(global_settings, user_settings)
    if not coerce_bool(effective.get("REQUESTS_ENABLED"), default=False):
        return None

    resolved_mode = resolve_policy_mode(
        source=source,
        content_type=content_type,
        global_settings=global_settings,
        user_settings=user_settings,
    )
    logger.debug(
        "download policy resolve user=%s db_user_id=%s is_admin=%s source=%s content_type=%s mode=%s",
        session.get("user_id"),
        db_user_id,
        bool(session.get("is_admin", False)),
        source,
        content_type,
        resolved_mode.value,
    )
    return resolved_mode


def _policy_block_response(mode: PolicyMode) -> tuple[Response, int]:
    logger.debug(
        "download policy guard user=%s db_user_id=%s mode=%s",
        session.get("user_id"),
        session.get("db_user_id"),
        mode.value,
    )
    if mode == PolicyMode.BLOCKED:
        return (
            jsonify(
                {
                    "error": "Download not allowed by policy",
                    "code": "policy_blocked",
                    "required_mode": PolicyMode.BLOCKED.value,
                }
            ),
            403,
        )
    return (
        jsonify(
            {
                "error": "Download not allowed by policy",
                "code": "policy_requires_request",
                "required_mode": mode.value,
            }
        ),
        403,
    )


def _resolve_download_user_context(
    db_user_id: Any,
    username: Any,
    on_behalf_of_user_id: Any,
) -> tuple[Any, Any, tuple[Response, int] | None]:
    """Resolve download queue user context, including optional admin on-behalf overrides."""
    if on_behalf_of_user_id in (None, ""):
        return db_user_id, username, None

    if not session.get("is_admin", False):
        return db_user_id, username, (jsonify({"error": "Admin required"}), 403)

    if user_db is None:
        return db_user_id, username, (jsonify({"error": "User database unavailable"}), 503)

    try:
        target_user_id = int(on_behalf_of_user_id)
    except TypeError, ValueError:
        return db_user_id, username, (jsonify({"error": "Invalid on_behalf_of_user_id"}), 400)

    if target_user_id <= 0:
        return db_user_id, username, (jsonify({"error": "Invalid on_behalf_of_user_id"}), 400)

    target_user = user_db.get_user(user_id=target_user_id)
    if not target_user:
        return db_user_id, username, (jsonify({"error": "User not found"}), 404)

    return target_user["id"], target_user["username"], None


def _emit_request_updates(rows: list[dict[str, Any]]) -> None:
    """Defer request update emission until the runtime hook is available."""
    _emit_request_update_events(rows)


def _resolve_auth_mode_for_routes() -> str:
    """Resolve auth mode lazily so tests and runtime patches still take effect."""
    return get_auth_mode()


def _queue_release_for_routes(*args: Any, **kwargs: Any) -> Any:
    """Queue a release via the current backend instance."""
    return backend.queue_release(*args, **kwargs)


def _queue_status_for_routes(user_id: int | None = None) -> dict[str, dict[str, Any]]:
    """Read queue status via the current backend instance."""
    return backend.queue_status(user_id=user_id)


if user_db is not None:
    try:
        from shelfmark.core.activity_routes import register_activity_routes
        from shelfmark.core.request_routes import register_request_routes

        register_request_routes(
            app,
            user_db,
            resolve_auth_mode=_resolve_auth_mode_for_routes,
            queue_release=_queue_release_for_routes,
            ws_manager=ws_manager,
        )
        if download_history_service is not None and activity_view_state_service is not None:
            register_activity_routes(
                app,
                user_db,
                activity_view_state_service=activity_view_state_service,
                download_history_service=download_history_service,
                resolve_auth_mode=_resolve_auth_mode_for_routes,
                queue_status=_queue_status_for_routes,
                sync_request_delivery_states=sync_delivery_states_from_queue_status,
                emit_request_updates=_emit_request_updates,
                ws_manager=ws_manager,
            )
    except _IMPORT_OPERATIONAL_ERRORS as e:
        logger.warning("Failed to register request routes: %s", e)


# Enable CORS in development mode for local frontend development
if _is_debug_enabled():
    CORS(
        app,
        resources={
            r"/*": {
                "origins": ["http://localhost:5173", "http://127.0.0.1:5173"],
                "supports_credentials": True,
                "allow_headers": ["Content-Type", "Authorization", "X-Api-Key"],
                "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
            }
        },
    )


# Custom log filter to exclude routine status endpoint polling and WebSocket noise
class LogNoiseFilter(logging.Filter):
    """Filter out routine status endpoint requests and WebSocket upgrade errors to reduce log noise.

    WebSocket upgrade errors are benign - Flask-SocketIO automatically falls back to polling transport.
    The error occurs because Werkzeug's built-in server doesn't fully support WebSocket upgrades.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """Return whether a log record should be emitted."""
        message = record.getMessage() if hasattr(record, "getMessage") else str(record.msg)

        # Exclude GET /api/status requests (polling noise)
        if "GET /api/status" in message:
            return False

        # Exclude WebSocket upgrade errors (benign - falls back to polling)
        if "write() before start_response" in message:
            return False

        # Exclude the Error on request line that precedes WebSocket errors
        if record.levelno == logging.ERROR:
            if "Error on request:" in message:
                return False
            # Filter WebSocket-related AssertionError tracebacks
            if hasattr(record, "exc_info") and record.exc_info:
                exc_type, exc_value = record.exc_info[0], record.exc_info[1]
                if (
                    exc_type
                    and exc_type.__name__ == "AssertionError"
                    and exc_value
                    and "write() before start_response" in str(exc_value)
                ):
                    return False

        return True


# Flask logger
app.logger.handlers = logger.handlers
app.logger.setLevel(logger.level)
# Also handle Werkzeug's logger
werkzeug_logger = logging.getLogger("werkzeug")
werkzeug_logger.handlers = logger.handlers
werkzeug_logger.setLevel(logger.level)
# Add filter to suppress routine status endpoint polling logs and WebSocket upgrade errors
werkzeug_logger.addFilter(LogNoiseFilter())

# Set up authentication defaults
SESSION_COOKIE_SECURE = string_to_bool(SESSION_COOKIE_SECURE_ENV)


def _load_or_create_secret_key() -> bytes:
    """Load a persisted Flask secret key from config, or create one."""
    secret_path = CONFIG_DIR / ".flask_secret"

    try:
        if secret_path.exists():
            secret_key = secret_path.read_bytes()
            if len(secret_key) >= FLASK_SECRET_KEY_MIN_BYTES:
                return secret_key
            logger.warning(
                "Invalid persisted Flask secret key at %s (length=%s). Regenerating.",
                secret_path,
                len(secret_key),
            )
    except OSError as exc:
        logger.warning("Failed to read Flask secret key at %s: %s", secret_path, exc)

    secret_key = os.urandom(64)
    try:
        secret_path.parent.mkdir(parents=True, exist_ok=True)
        secret_path.write_bytes(secret_key)
        secret_path.chmod(0o600)
    except OSError as exc:
        logger.warning(
            "Failed to persist Flask secret key at %s. Sessions may reset on restart: %s",
            secret_path,
            exc,
        )

    return secret_key


app.config.update(
    SECRET_KEY=_load_or_create_secret_key(),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=SESSION_COOKIE_SECURE,
    SESSION_COOKIE_NAME=SESSION_COOKIE_NAME,
    PERMANENT_SESSION_LIFETIME=604800,  # 7 days in seconds
)
configure_public_id_key(app.config["SECRET_KEY"])

logger.info(
    "Session cookie secure setting: %s (from env: %s)",
    SESSION_COOKIE_SECURE,
    SESSION_COOKIE_SECURE_ENV,
)
logger.info("Session cookie name: %s", SESSION_COOKIE_NAME)


def _proxy_default_is_admin(db: UserDB) -> bool:
    """Role for a proxy user seen for the first time when no admin group is configured.

    The first account ever provisioned is an admin so the instance is never left without
    one; later accounts follow PROXY_AUTH_DEFAULT_ROLE (default: user).
    """
    if not db.has_admin():
        return True
    role = str(app_config.get("PROXY_AUTH_DEFAULT_ROLE", "user") or "user").strip().lower()
    return role == "admin"


_API_KEY_EXEMPT_PREFIXES = ("/api/auth/",)
_API_KEY_EXEMPT_PATHS = frozenset({"/api/health"})
_READ_ONLY_KEY_PATHS = frozenset({"/api/stats"})


@app.before_request
def api_key_auth_middleware() -> Response | tuple[Response, int] | None:
    """Authenticate requests that present the configured SHELFMARK_API_KEY.

    Both Authorization: Bearer and X-Api-Key are checked, and either
    matching authenticates the request as an admin for this request only:
    any session cookie is ignored and none is written back. Checking both
    means a reverse proxy's own Authorization header never shadows an
    operator-supplied X-Api-Key. No matching candidate is ignored so bearer
    tokens forwarded by reverse proxies keep working; the request then
    continues on the normal session path.
    """
    if not request.path.startswith("/api/"):
        return None
    if request.path in _API_KEY_EXEMPT_PATHS or request.path.startswith(_API_KEY_EXEMPT_PREFIXES):
        return None
    if not api_key_module.SHELFMARK_API_KEY and not api_key_module.SHELFMARK_API_KEY_READONLY:
        return None

    candidates = api_key_module.extract_api_key_candidates(
        request.headers.get("Authorization"), request.headers.get("X-Api-Key")
    )
    if not candidates:
        return None

    scopes = {api_key_module.key_scope(candidate) for candidate in candidates}
    scope = "admin" if "admin" in scopes else "readonly" if "readonly" in scopes else None
    if scope is None:
        return None
    if get_auth_mode() == "none":
        return None

    # Mark the request as keyed before the lookup so the after-request cookie
    # reset also covers the error path below.
    g.api_key_auth = True

    if scope == "readonly":
        # A read-only key is a dashboard credential: one GET endpoint, no session.
        g.api_key_scope = "readonly"
        if request.method != "GET" or request.path not in _READ_ONLY_KEY_PATHS:
            return jsonify({"error": "This API key is read-only"}), 403
        return None
    g.api_key_scope = "admin"

    try:
        admin = user_db.get_first_admin() if user_db is not None else None
    except _OPERATIONAL_ERRORS:
        logger.exception("API key auth middleware error")
        return jsonify({"error": "Authentication error"}), 500

    session.clear()
    session["user_id"] = admin["username"] if admin else "api"
    session["is_admin"] = True
    if admin:
        session["db_user_id"] = admin["id"]
    session.permanent = False
    # Identity is per request; never persist it as a cookie.
    session.modified = False
    return None


@app.before_request
def proxy_auth_middleware() -> Response | tuple[Response, int] | None:
    """Middleware to handle proxy authentication.

    When AUTH_METHOD is set to "proxy", this middleware automatically
    authenticates users based on headers set by the reverse proxy.
    """
    auth_mode = get_auth_mode()

    # Only run for proxy auth mode
    if auth_mode != "proxy":
        return None

    # A request already authenticated by SHELFMARK_API_KEY needs no proxy headers.
    if g.get("api_key_auth"):
        return None

    # Skip for public endpoints that don't need auth
    if request.path == "/api/health":
        return None

    def get_proxy_header(header_name: str) -> str | None:
        """Resolve proxy auth values from headers with WSGI env fallbacks."""
        value = request.headers.get(header_name)
        if value:
            return value

        env_key = f"HTTP_{header_name.upper().replace('-', '_')}"
        value = request.environ.get(env_key)
        if value:
            return value

        # Some proxies set authenticated username in REMOTE_USER (not as a header).
        if header_name.lower().replace("_", "-") == "remote-user":
            return request.environ.get("REMOTE_USER")

        return None

    try:
        user_header = (
            normalize_optional_text(app_config.get("PROXY_AUTH_USER_HEADER", "X-Auth-User"))
            or "X-Auth-User"
        )

        # Extract username from proxy header
        username = get_proxy_header(user_header)

        if not username:
            if request.path.startswith("/api/auth/"):
                return None

            logger.warning("Proxy auth enabled but no username found in header '%s'", user_header)
            return jsonify({"error": "Authentication required. Proxy header not set."}), 401

        # Resolve admin role for proxy sessions.
        # If an admin group is configured, derive from groups header.
        # Otherwise preserve the existing DB role for known users; a first-time user
        # is an admin only while the instance has none (so nobody is locked out),
        # after that PROXY_AUTH_DEFAULT_ROLE decides (default: user).
        admin_group_header = (
            normalize_optional_text(
                app_config.get("PROXY_AUTH_ADMIN_GROUP_HEADER", "X-Auth-Groups")
            )
            or "X-Auth-Groups"
        )
        admin_group_name = (
            normalize_optional_text(app_config.get("PROXY_AUTH_ADMIN_GROUP_NAME", "")) or ""
        )
        is_admin = True

        if admin_group_name:
            groups_header = get_proxy_header(admin_group_header) or ""
            user_groups_delimiter = "," if "," in groups_header else "|"
            user_groups = [
                g.strip() for g in groups_header.split(user_groups_delimiter) if g.strip()
            ]
            is_admin = admin_group_name in user_groups
        elif user_db is not None:
            existing_db_user = user_db.get_user(username=username)
            if existing_db_user:
                is_admin = existing_db_user.get("role") == "admin"
            else:
                is_admin = _proxy_default_is_admin(user_db)

        # Create or update session
        previous_username = session.get("user_id")
        if previous_username and previous_username != username:
            # Header identity changed mid-session; force reprovision for the new user.
            session.pop("db_user_id", None)

        session["user_id"] = username
        session["is_admin"] = is_admin

        # Provision proxy-authenticated users into users.db for multi-user features.
        # Re-provision when db_user_id is missing/stale/mismatched to avoid broken
        # sessions after DB resets or auth-mode transitions.
        if user_db is not None:
            raw_db_user_id = session.get("db_user_id")
            session_db_user = None

            if raw_db_user_id is not None:
                try:
                    session_db_user = user_db.get_user(user_id=int(raw_db_user_id))
                except TypeError, ValueError:
                    session_db_user = None

            session_db_username = (
                str(session_db_user.get("username") or "").strip() if session_db_user else ""
            )
            needs_db_user_sync = (
                raw_db_user_id is None or session_db_user is None or session_db_username != username
            )

            if needs_db_user_sync:
                role = "admin" if is_admin else "user"
                db_user, _ = upsert_external_user(
                    user_db,
                    auth_source="proxy",
                    username=username,
                    role=role,
                    collision_strategy="takeover",
                    context="proxy_request",
                )
                if db_user is None:
                    _raise_runtime_error("Unexpected proxy user sync result: no user returned")

                session["db_user_id"] = db_user["id"]

        session.permanent = False
    except _OPERATIONAL_ERRORS:
        logger.exception("Proxy auth middleware error")
        return jsonify({"error": "Authentication error"}), 500
    else:
        return None


@app.after_request
def set_security_headers(response: Response) -> Response:
    """Add baseline security headers to every response."""
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self' ws: wss:",
    )
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault("Cross-Origin-Embedder-Policy", "credentialless")
    return response


@app.after_request
def strip_cookie_for_api_key_requests(response: Response) -> Response:
    """Keyed requests never mint or refresh a session cookie, even if a handler dirties the session."""
    if g.get("api_key_auth"):
        # Setting `permanent` mutates the session dict (re-marking it
        # modified), so it must be reset before `modified`, not after.
        session.permanent = False
        session.modified = False
    return response


def login_required(
    f: Callable[..., Response | tuple[Response, int]],
) -> Callable[..., Response | tuple[Response, int]]:
    """Require authentication for a Flask route."""

    @wraps(f)
    def decorated_function(*args: object, **kwargs: object) -> Response | tuple[Response, int]:
        auth_mode = get_auth_mode()

        # If no authentication is configured, allow access
        if auth_mode == "none":
            return f(*args, **kwargs)

        # If CWA mode and database disappeared after startup, return error
        if auth_mode == "cwa" and CWA_DB_PATH and not CWA_DB_PATH.exists():
            logger.error("CWA database at %s is no longer accessible", CWA_DB_PATH)
            return jsonify({"error": "Internal Server Error"}), 500

        # Check if user has a valid session
        if "user_id" not in session:
            return jsonify({"error": "Unauthorized"}), 401

        # Check admin access for settings/onboarding endpoints.
        if is_settings_or_onboarding_path(request.path):
            try:
                if requires_admin_for_settings_access(request.path, {}) and not session.get(
                    "is_admin", False
                ):
                    return jsonify({"error": "Admin access required"}), 403

            except RuntimeError, TypeError, ValueError:
                logger.exception("Admin access check error")
                return jsonify({"error": "Internal Server Error"}), 500

        return f(*args, **kwargs)

    return decorated_function


_BASE_TAG = '<base href="/" data-shelfmark-base />'


def _base_href() -> str:
    if not BASE_PATH:
        return "/"
    return f"{BASE_PATH}/"


def _serve_index_html() -> Response:
    """Serve index.html with an adjusted base tag for subpath deployments."""
    index_path = FRONTEND_DIST / "index.html"
    try:
        with index_path.open(encoding="utf-8") as handle:
            html = handle.read()
    except OSError:
        return send_from_directory(FRONTEND_DIST, "index.html")

    if BASE_PATH and _BASE_TAG in html:
        html = html.replace(_BASE_TAG, f'<base href="{_base_href()}" data-shelfmark-base />', 1)

    return Response(html, mimetype="text/html")


# Serve frontend static files
@app.route("/assets/<path:filename>")
def serve_frontend_assets(filename: str) -> Response:
    """Serve static assets from the built frontend."""
    return send_from_directory(FRONTEND_DIST / "assets", filename)


@app.route("/")
def index() -> Response:
    """Serve the React frontend application.

    Authentication is handled by the React app itself.
    """
    return _serve_index_html()


@app.route("/theme-init.js")
def theme_init_js() -> Response:
    """Serve the blocking theme-init script."""
    return send_from_directory(FRONTEND_DIST, "theme-init.js", mimetype="application/javascript")


@app.route("/logo.png")
def logo() -> Response:
    """Serve logo from built frontend assets."""
    return send_from_directory(FRONTEND_DIST, "logo.png", mimetype="image/png")


@app.route("/favicon.ico")
@app.route("/favico<path:_>")
def favicon(_: Any = None) -> Response:
    """Serve favicon from built frontend assets."""
    return send_from_directory(FRONTEND_DIST, "favicon.ico", mimetype="image/vnd.microsoft.icon")


if _is_debug_enabled():
    import subprocess

    def _stop_gui() -> None:
        return None

    if app_config.get("USING_EXTERNAL_BYPASSER", False):
        pass
    else:
        from shelfmark.bypass.internal_bypasser import _cleanup_orphan_processes

        def _stop_gui() -> None:
            _cleanup_orphan_processes()

    @app.route("/api/debug", methods=["GET"])
    @login_required
    def debug() -> Response | tuple[Response, int]:
        """Run `/app/genDebug.sh`, generate a debug zip, and return it.

        The file is written to `/tmp/shelfmark-debug.zip` before being returned.
        """
        try:
            logger.info("Debug endpoint called, stopping GUI and generating debug info...")
            _stop_gui()
            time.sleep(1)
            result = subprocess.run(
                ["/app/genDebug.sh"], capture_output=True, text=True, check=True
            )
            if result.returncode != 0:
                _raise_runtime_error(f"Debug script failed: {result.stderr}")
            logger.info("Debug script executed: %s", result.stdout)
            debug_file_path = result.stdout.strip().split("\n")[-1]
            if not Path(debug_file_path).exists():
                logger.error("Debug zip file not found at: %s", debug_file_path)
                return jsonify({"error": "Failed to generate debug information"}), 500

            logger.info("Sending debug file: %s", debug_file_path)
            return send_file(
                debug_file_path,
                mimetype="application/zip",
                download_name=Path(debug_file_path).name,
                as_attachment=True,
            )
        except subprocess.CalledProcessError as e:
            logger.error_trace(f"Debug script error: {e}, stdout: {e.stdout}, stderr: {e.stderr}")
            return jsonify({"error": f"Debug script failed: {e.stderr}"}), 500
        except _OPERATIONAL_ERRORS as e:
            logger.error_trace(f"Debug endpoint error: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route("/api/restart", methods=["GET"])
    @login_required
    def restart() -> Response | tuple[Response, int]:
        """Restart the application."""
        os._exit(0)


def _parse_search_filters_from_request() -> SearchFilters:
    """Parse direct/source browse filters from query parameters."""
    return SearchFilters(
        isbn=request.args.getlist("isbn"),
        author=request.args.getlist("author"),
        title=request.args.getlist("title"),
        lang=request.args.getlist("lang"),
        sort=request.args.get("sort"),
        content=request.args.getlist("content"),
        format=request.args.getlist("format"),
    )


def _build_source_query_book(query_text: str, filters: SearchFilters) -> BookMetadata:
    """Build a synthetic book context for source-native browse searches."""
    from shelfmark.metadata_providers import BookMetadata

    author_values = [value.strip() for value in (filters.author or []) if str(value).strip()]
    title_values = [value.strip() for value in (filters.title or []) if str(value).strip()]
    isbn_values = [value.strip() for value in (filters.isbn or []) if str(value).strip()]
    title = (
        title_values[0]
        if title_values
        else query_text
        or (isbn_values[0] if isbn_values else "")
        or (author_values[0] if author_values else "Direct Search")
    )
    author = author_values[0] if author_values else ""

    return BookMetadata(
        provider="manual",
        provider_id=query_text or title,
        provider_display_name="Manual Search",
        title=title,
        search_title=title,
        search_author=author or None,
        authors=author_values,
    )


def _serialize_browse_record(record: BrowseRecord) -> dict:
    """Serialize a source-native browse record for the frontend."""
    result = {key: value for key, value in record.__dict__.items() if value is not None}

    preview = result.get("preview")
    if isinstance(preview, str) and preview:
        from shelfmark.core.utils import transform_cover_url

        result["preview"] = transform_cover_url(preview, record.id)

    return result


def _serialize_release(release: Release) -> dict:
    """Serialize a release for the frontend, normalizing preview URLs."""
    from dataclasses import asdict

    from shelfmark.core.utils import transform_cover_url

    result = asdict(release)
    extra = result.get("extra")
    if isinstance(extra, dict):
        preview = extra.get("preview")
        if isinstance(preview, str) and preview:
            extra = dict(extra)
            extra["preview"] = transform_cover_url(preview, release.source_id)
            result["extra"] = extra

    return result


register_release_inspect_routes(app, login_required)


@app.route("/api/releases/download", methods=["POST"])
@login_required
def api_download_release() -> Response | tuple[Response, int]:
    """Queue a release for download.

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
        data = request.get_json(silent=True)
        if not data:
            return jsonify({"error": "No data provided"}), 400

        if "source_id" not in data:
            return jsonify({"error": "source_id is required"}), 400
        if "source" not in data:
            return jsonify({"error": "source is required"}), 400

        source = data["source"]
        resolved_content_type, inferred_content_type = _resolve_release_content_type(data, source)
        policy_mode = _resolve_policy_mode_for_current_user(
            source=source,
            content_type=resolved_content_type,
        )
        if policy_mode is not None and policy_mode != PolicyMode.DOWNLOAD:
            return _policy_block_response(policy_mode)

        release_payload = data
        if inferred_content_type and data.get("content_type") is None:
            release_payload = dict(data)
            release_payload["content_type"] = resolved_content_type

        logger.info(
            "Download request received. keys=%s downloads=%s extra.downloads=%s",
            list(data.keys()),
            data.get("downloads"),
            data.get("extra", {}).get("downloads") if isinstance(data.get("extra"), dict) else None,
        )

        priority = data.get("priority", 0)
        # Per-user download overrides
        db_user_id = session.get("db_user_id")
        _username = session.get("user_id")
        db_user_id, _username, on_behalf_error = _resolve_download_user_context(
            db_user_id,
            _username,
            data.get("on_behalf_of_user_id"),
        )
        if on_behalf_error:
            return on_behalf_error
        success, error_msg = backend.queue_release(
            release_payload,
            priority,
            user_id=db_user_id,
            username=_username,
        )

        if success:
            return jsonify({"status": "queued", "priority": priority})
        return jsonify({"error": error_msg or "Failed to queue release"}), 500
    except _OPERATIONAL_ERRORS as e:
        logger.error_trace(f"Release download error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/config", methods=["GET"])
@login_required
def api_config() -> Response | tuple[Response, int]:
    """Get application configuration for frontend.

    Uses the dynamic config singleton to ensure settings changes
    are reflected without requiring a container restart.
    """
    try:
        from shelfmark.config.env import _is_config_dir_writable
        from shelfmark.core.onboarding import is_onboarding_complete as _get_onboarding_complete
        from shelfmark.metadata_providers import (
            get_provider_default_sort,
            get_provider_search_fields,
            get_provider_sort_options,
        )

        db_user_id = get_session_db_user_id(session)

        search_mode = app_config.get("SEARCH_MODE", "universal", user_id=db_user_id)
        default_release_source = app_config.get(
            "DEFAULT_RELEASE_SOURCE",
            "",
            user_id=db_user_id,
        )
        default_release_source_audiobook = app_config.get(
            "DEFAULT_RELEASE_SOURCE_AUDIOBOOK",
            "",
            user_id=db_user_id,
        )
        configured_metadata_provider = normalize_optional_text(
            app_config.get(
                "METADATA_PROVIDER",
                "",
                user_id=db_user_id,
            )
        )
        _configured_metadata_provider_audiobook = normalize_optional_text(
            app_config.get(
                "METADATA_PROVIDER_AUDIOBOOK",
                "",
                user_id=db_user_id,
            )
        )
        metadata_ui_provider = (
            configured_metadata_provider or _configured_metadata_provider_audiobook
        )

        config = {
            "calibre_web_url": app_config.get("CALIBRE_WEB_URL", ""),
            "audiobook_library_url": app_config.get("AUDIOBOOK_LIBRARY_URL", ""),
            "search_page_title": app_config.get("SEARCH_PAGE_TITLE", "Shelfmark"),
            "debug": app_config.get("DEBUG", False),
            "build_version": BUILD_VERSION,
            "release_version": RELEASE_VERSION,
            "book_languages": _SUPPORTED_BOOK_LANGUAGE,
            "default_language": app_config.get("BOOK_LANGUAGE", ["en"], user_id=db_user_id),
            "default_content_type": app_config.get(
                "DEFAULT_CONTENT_TYPE", "ebook", user_id=db_user_id
            ),
            "supported_formats": app_config.SUPPORTED_FORMATS,
            "supported_audiobook_formats": app_config.SUPPORTED_AUDIOBOOK_FORMATS,
            "search_mode": search_mode,
            "metadata_sort_options": get_provider_sort_options(metadata_ui_provider),
            "metadata_search_fields": get_provider_search_fields(metadata_ui_provider),
            "default_release_source": default_release_source,
            "default_release_source_audiobook": default_release_source_audiobook,
            "show_release_source_links": app_config.get("SHOW_RELEASE_SOURCE_LINKS", True),
            "show_combined_selector": app_config.get(
                "SHOW_COMBINED_SELECTOR", True, user_id=db_user_id
            ),
            "force_combined_search": app_config.get(
                "FORCE_COMBINED_SEARCH", False, user_id=db_user_id
            ),
            "books_output_mode": app_config.get("BOOKS_OUTPUT_MODE", "folder"),
            "auto_open_downloads_sidebar": app_config.get("AUTO_OPEN_DOWNLOADS_SIDEBAR", True),
            "hardcover_auto_remove_on_download": app_config.get(
                "HARDCOVER_AUTO_REMOVE_ON_DOWNLOAD", True
            ),
            "download_to_browser_content_types": app_config.get(
                "DOWNLOAD_TO_BROWSER_CONTENT_TYPES",
                [],
                user_id=db_user_id,
            ),
            # The client must not give up before this budget does. `/api/releases`
            # answers a spent budget with a message naming the real cause (a protection
            # challenge nobody could solve); a browser that aborted first replaces it
            # with a generic network/proxy error and RELEASE_SEARCH_TIMEOUT becomes a
            # setting the user can raise with no visible effect. See issue #1285.
            "release_search_timeout": search_deadline.budget_seconds(),
            "settings_enabled": _is_config_dir_writable(),
            "onboarding_complete": _get_onboarding_complete(),
            # Default sort orders
            "default_sort": app_config.get(
                "AA_DEFAULT_SORT", ""
            ),  # For direct mode (Anna's Archive) — empty means use local downloads sort
            "metadata_default_sort": get_provider_default_sort(
                metadata_ui_provider
            ),  # For universal mode
        }
        return jsonify(config)
    except _IMPORT_OPERATIONAL_ERRORS as e:
        logger.error_trace(f"Config error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/stats", methods=["GET"])
def api_stats() -> Response | tuple[Response, int]:
    """Counters for dashboards. Reachable with the read-only API key, an admin key or an admin session."""
    from shelfmark.core import stats

    scope = g.get("api_key_scope")
    if get_auth_mode() != "none" and scope is None:
        if "user_id" not in session:
            return jsonify({"error": "Unauthorized"}), 401
        if not session.get("is_admin", False):
            return jsonify({"error": "Admin access required"}), 403

    if user_db is None:
        return jsonify({"error": "User database unavailable"}), 503
    try:
        payload = stats.collect(user_db.db_path)
    except sqlite3.Error:
        logger.exception("stats: could not read the database")
        return jsonify({"error": "Internal Server Error"}), 500
    return jsonify(payload)


@app.route("/api/health", methods=["GET"])
def api_health() -> Response | tuple[Response, int]:
    """Health check endpoint for container orchestration.

    No authentication required.

    Returns:
        flask.Response: JSON with status "ok" and optional degraded features.

    """
    response: dict[str, object] = {"status": "ok"}

    # Report degraded features
    if not backend.WEBSOCKET_AVAILABLE:
        response["degraded"] = {"websocket": "WebSocket unavailable - real-time updates disabled"}

    return jsonify(response)


def _resolve_status_scope(*, require_authenticated: bool = True) -> tuple[bool, int | None, bool]:
    """Resolve queue-status visibility from session state.

    Returns:
        (is_admin, db_user_id, can_access_status)

    """
    auth_mode = get_auth_mode()
    if auth_mode == "none":
        return True, None, True

    if require_authenticated and "user_id" not in session:
        return False, None, False

    is_admin = bool(session.get("is_admin", False))
    if is_admin:
        return True, None, True

    db_user_id = get_session_db_user_id(session)

    if db_user_id is None:
        return False, None, False

    return False, db_user_id, True


def _queue_status_to_final_activity_status(status: QueueStatus) -> str | None:
    return status.value if status in TERMINAL_QUEUE_STATUSES else None


def _queue_status_to_notification_event(status: QueueStatus) -> NotificationEvent | None:
    if status == QueueStatus.COMPLETE:
        return NotificationEvent.DOWNLOAD_COMPLETE
    if status == QueueStatus.ERROR:
        return NotificationEvent.DOWNLOAD_FAILED
    return None


def _notify_admin_for_terminal_download_status(
    *, task_id: str, status: QueueStatus, task: Any
) -> None:
    event = _queue_status_to_notification_event(status)
    if event is None:
        return

    raw_owner_user_id = getattr(task, "user_id", None)
    try:
        owner_user_id = int(raw_owner_user_id) if raw_owner_user_id is not None else None
    except TypeError, ValueError:
        owner_user_id = None

    content_type = normalize_optional_text(getattr(task, "content_type", None))
    context = NotificationContext(
        event=event,
        title=str(getattr(task, "title", "Unknown title") or "Unknown title"),
        author=str(getattr(task, "author", "Unknown author") or "Unknown author"),
        username=normalize_optional_text(getattr(task, "username", None)),
        content_type=normalize_content_type(content_type) if content_type is not None else None,
        format=normalize_optional_text(getattr(task, "format", None)),
        source=normalize_source(getattr(task, "source", None)),
        error_message=(
            normalize_optional_text(getattr(task, "status_message", None))
            if event == NotificationEvent.DOWNLOAD_FAILED
            else None
        ),
    )
    try:
        notify_admin(event, context)
    except (RuntimeError, TypeError, ValueError) as exc:
        logger.warning(
            "Failed to trigger admin notification for download %s (%s): %s",
            task_id,
            status.value,
            exc,
        )
    if owner_user_id is None:
        return
    try:
        notify_user(owner_user_id, event, context)
    except (RuntimeError, TypeError, ValueError) as exc:
        logger.warning(
            "Failed to trigger user notification for download %s (%s, user_id=%s): %s",
            task_id,
            status.value,
            owner_user_id,
            exc,
        )


def _emit_activity_update_for_task(*, payload: dict[str, Any], task: Any) -> None:
    owner_user_id = normalize_positive_int(getattr(task, "user_id", None))
    emit_ws_event(
        ws_manager,
        event_name="activity_update",
        room="admins",
        payload=payload,
    )
    if owner_user_id is None:
        return
    emit_ws_event(
        ws_manager,
        event_name="activity_update",
        room=f"user_{owner_user_id}",
        payload=payload,
    )


def _record_download_queued(task_id: str, task: Any) -> None:
    """Persist initial download record when a task enters the queue."""
    if download_history_service is None:
        return

    owner_user_id = normalize_positive_int(getattr(task, "user_id", None))
    request_id = normalize_positive_int(getattr(task, "request_id", None))
    origin = "requested" if request_id else "direct"

    source_name = normalize_source(getattr(task, "source", None))
    source_display = get_source_display_name(source_name)

    try:
        download_history_service.record_download(
            task_id=task_id,
            user_id=owner_user_id,
            username=normalize_optional_text(getattr(task, "username", None)),
            request_id=request_id,
            source=source_name,
            source_display_name=source_display,
            title=str(getattr(task, "title", "Unknown title") or "Unknown title"),
            author=normalize_optional_text(getattr(task, "author", None)),
            file_format=normalize_optional_text(getattr(task, "format", None)),
            size=normalize_optional_text(getattr(task, "size", None)),
            preview=normalize_optional_text(getattr(task, "preview", None)),
            content_type=normalize_optional_text(getattr(task, "content_type", None)),
            downloads=getattr(task, "downloads", None),
            origin=origin,
            retry_payload=backend.serialize_task_for_retry(task),
        )
    except _OPERATIONAL_ERRORS as exc:
        logger.warning("Failed to record download at queue time for task %s: %s", task_id, exc)
        return

    if activity_view_state_service is None:
        return

    try:
        cleared_view_state = 0
        cleared_view_state += activity_view_state_service.clear_item_for_all_viewers(
            item_type="download",
            item_key=f"download:{task_id}",
        )
        if request_id is not None:
            cleared_view_state += activity_view_state_service.clear_item_for_all_viewers(
                item_type="request",
                item_key=f"request:{request_id}",
            )
        if cleared_view_state > 0:
            _emit_activity_update_for_task(
                task=task,
                payload={
                    "kind": "activity_reset",
                    "task_id": task_id,
                },
            )
    except _OPERATIONAL_ERRORS as exc:
        logger.warning("Failed to reset activity viewer state for task %s: %s", task_id, exc)


def _record_download_terminal_snapshot(task_id: str, status: QueueStatus, task: Any) -> None:
    _notify_admin_for_terminal_download_status(task_id=task_id, status=status, task=task)

    final_status = _queue_status_to_final_activity_status(status)
    if final_status is None:
        return

    finalized_download = False
    if download_history_service is not None:
        try:
            download_history_service.finalize_download(
                task_id=task_id,
                final_status=final_status,
                status_message=normalize_optional_text(getattr(task, "status_message", None)),
                download_path=normalize_optional_text(getattr(task, "download_path", None)),
                retry_payload=backend.serialize_task_for_retry(task),
            )
            finalized_download = True
        except _OPERATIONAL_ERRORS as exc:
            logger.warning("Failed to finalize download history for task %s: %s", task_id, exc)

    if finalized_download:
        _emit_activity_update_for_task(
            task=task,
            payload={
                "kind": "download_terminal",
                "task_id": task_id,
                "status": final_status,
            },
        )

    if user_db is None or status != QueueStatus.ERROR:
        return

    request_id = normalize_positive_int(getattr(task, "request_id", None))
    if request_id is None:
        return
    if backend.can_retry_download_task(task, status):
        return

    raw_error_message = getattr(task, "status_message", None)
    fallback_reason = (
        raw_error_message.strip()
        if isinstance(raw_error_message, str) and raw_error_message.strip()
        else "Download failed"
    )
    try:
        reopened_request = reopen_failed_request(
            user_db,
            request_id=request_id,
            failure_reason=fallback_reason,
        )
        if reopened_request is not None:
            if activity_view_state_service is not None:
                activity_view_state_service.clear_item_for_all_viewers(
                    item_type="request",
                    item_key=f"request:{request_id}",
                )
            _emit_request_update_events([reopened_request])
    except _OPERATIONAL_ERRORS as exc:
        logger.warning(
            "Failed to reopen request %s after terminal download error %s: %s",
            request_id,
            task_id,
            exc,
        )


def _task_owned_by_actor(
    task: Any, *, actor_user_id: int | None, actor_username: str | None
) -> bool:
    raw_task_user_id = getattr(task, "user_id", None)
    try:
        task_user_id = int(raw_task_user_id) if raw_task_user_id is not None else None
    except TypeError, ValueError:
        task_user_id = None

    if actor_user_id is not None and task_user_id is not None:
        return task_user_id == actor_user_id

    task_username = getattr(task, "username", None)
    if isinstance(task_username, str) and task_username.strip() and isinstance(actor_username, str):
        return task_username.strip() == actor_username.strip()

    return False


def _download_row_owned_by_actor(
    row: dict[str, Any],
    *,
    actor_user_id: int | None,
    actor_username: str | None,
) -> bool:
    owner_user_id = normalize_positive_int(row.get("user_id"))
    if actor_user_id is not None and owner_user_id is not None:
        return owner_user_id == actor_user_id

    row_username = normalize_optional_text(row.get("username"))
    if row_username is not None and isinstance(actor_username, str):
        return row_username == actor_username.strip()

    return False


def _resolve_queue_actor() -> tuple[bool, int | None, str | None, Response | None]:
    is_admin, db_user_id, can_access_status = _resolve_status_scope()
    actor_username = session.get("user_id")
    normalized_actor_username = actor_username if isinstance(actor_username, str) else None

    if not is_admin and (not can_access_status or db_user_id is None):
        return (
            is_admin,
            db_user_id,
            normalized_actor_username,
            jsonify({"error": "User identity unavailable", "code": "user_identity_unavailable"}),
        )

    return is_admin, db_user_id, normalized_actor_username, None


def _queue_task_visible_to_actor(
    task_id: str,
    *,
    is_admin: bool,
    actor_user_id: int | None,
    actor_username: str | None,
) -> bool:
    if is_admin:
        return True

    task = backend.book_queue.get_task(task_id)
    if task is None:
        return False

    return _task_owned_by_actor(
        task,
        actor_user_id=actor_user_id,
        actor_username=actor_username,
    )


def _resolve_viewer_download_id(book_id: str) -> str:
    """Map a non-admin's opaque download id back to the task it names (#1418).

    Only the caller's own request-linked downloads are searched, so an id that
    doesn't resolve comes back unchanged and fails the route's usual lookup.
    """
    if not is_public_download_id(book_id):
        return book_id
    is_admin, db_user_id, _ = _resolve_status_scope()
    if is_admin or db_user_id is None:
        return book_id
    candidates = [
        task_id
        for tasks in backend.book_queue.get_status(user_id=db_user_id).values()
        for task_id, task in tasks.items()
        if getattr(task, "request_id", None) is not None
    ]
    if download_history_service is not None:
        candidates.extend(download_history_service.list_request_task_ids(user_id=db_user_id))
    return match_public_download_id(book_id, candidates) or book_id


def _viewer_queue_task_id(task_id: str) -> str:
    """Return the id a non-admin sees for a live queue task."""
    task = backend.book_queue.get_task(task_id)
    return viewer_download_id(task_id, getattr(task, "request_id", None))


backend.book_queue.set_queue_hook(_record_download_queued)
backend.book_queue.set_terminal_status_hook(_record_download_terminal_snapshot)


def _emit_request_update_events(updated_requests: list[dict[str, Any]]) -> None:
    """Broadcast request_update events for rows changed by delivery-state sync."""
    if not updated_requests or ws_manager is None:
        return

    for updated in updated_requests:
        payload = {
            "request_id": updated["id"],
            "status": updated["status"],
            "delivery_state": updated.get("delivery_state"),
            "title": (updated.get("book_data") or {}).get("title") or "Unknown title",
        }
        emit_ws_event(
            ws_manager,
            event_name="request_update",
            room=f"user_{updated['user_id']}",
            payload=payload,
        )
        emit_ws_event(
            ws_manager,
            event_name="request_update",
            room="admins",
            payload=payload,
        )


@app.route("/api/status", methods=["GET"])
@login_required
def api_status() -> Response | tuple[Response, int]:
    """Get current download queue status.

    Returns:
        flask.Response: JSON object with queue status.

    """
    try:
        is_admin, db_user_id, can_access_status = _resolve_status_scope()
        if not can_access_status:
            return jsonify({})

        user_id = None if is_admin else db_user_id
        status = backend.queue_status(user_id=user_id)
        if user_db is not None:
            updated_requests = sync_delivery_states_from_queue_status(
                user_db,
                queue_status=status,
                user_id=user_id,
            )
            _emit_request_update_events(updated_requests)
        return jsonify(status if is_admin else redact_status(status))
    except _OPERATIONAL_ERRORS as e:
        logger.error_trace(f"Status error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/localdownload", methods=["GET"])
@login_required
def api_local_download() -> Response | tuple[Response, int]:
    """Download an EPUB file from local storage if available.

    Query Parameters:
        id (str): Book identifier (MD5 hash)

    Returns:
        flask.Response: The EPUB file if found, otherwise an error response.

    """
    book_id = request.args.get("id", "")
    if not book_id:
        return jsonify({"error": "No book ID provided"}), 400
    book_id = _resolve_viewer_download_id(book_id)

    try:
        # The path, not the bytes: send_file streams it off disk, where reading it
        # first cost two resident copies of the whole book.
        file_path, book_info = backend.get_book_path(book_id)
        if file_path is None:
            # Fallback for dismissed/history entries where queue task may no longer exist.
            if download_history_service is not None:
                is_admin, db_user_id, can_access_status = _resolve_status_scope()
                if can_access_status:
                    history_row = download_history_service.get_by_task_id(book_id)
                    if history_row is not None:
                        owner_user_id = history_row.get("user_id")
                        if is_admin or owner_user_id == db_user_id:
                            download_path = DownloadHistoryService._resolve_existing_download_path(
                                history_row.get("download_path")
                            )
                            if download_path:
                                return send_file(
                                    download_path,
                                    download_name=Path(download_path).name,
                                    as_attachment=True,
                                )

            # Book data not found or not available
            return jsonify({"error": "File not found"}), 404

        is_admin, db_user_id, can_access_status = _resolve_status_scope()
        if not is_admin:
            actor_username = session.get("user_id")
            if not can_access_status or not _task_owned_by_actor(
                book_info,
                actor_user_id=db_user_id,
                actor_username=actor_username if isinstance(actor_username, str) else None,
            ):
                return jsonify({"error": "File not found"}), 404

        file_name = book_info.get_filename() if book_info is not None else Path(book_id).name
        return send_file(file_path, download_name=file_name, as_attachment=True)

    except _OPERATIONAL_ERRORS as e:
        logger.error_trace(f"Local download error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/covers/<cover_id>", methods=["GET"])
@login_required
def api_cover(cover_id: str) -> Response | tuple[Response, int]:
    """Serve a cached book cover image.

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

        from shelfmark.config.env import is_covers_cache_enabled
        from shelfmark.core.image_cache import get_image_cache

        # Check if caching is enabled
        if not is_covers_cache_enabled():
            return jsonify({"error": "Cover caching is disabled"}), 404

        cache = get_image_cache()

        # Try to get from cache first
        cached = cache.get(cover_id)
        if cached:
            image_data, content_type = cached
            response = app.response_class(response=image_data, status=200, mimetype=content_type)
            response.headers["Cache-Control"] = "public, max-age=86400"
            response.headers["X-Cache"] = "HIT"
            return response

        # Cache miss - get URL from query parameter
        encoded_url = request.args.get("url")
        if not encoded_url:
            return jsonify({"error": "Cover URL not provided"}), 404

        try:
            original_url = base64.urlsafe_b64decode(encoded_url).decode()
        except (binascii.Error, UnicodeDecodeError) as e:
            logger.warning("Failed to decode cover URL: %s", e)
            return jsonify({"error": "Invalid cover URL encoding"}), 400

        # Fetch and cache the image
        result = cache.fetch_and_cache(cover_id, original_url)
        if not result:
            return jsonify({"error": "Failed to fetch cover image"}), 404

        image_data, content_type = result
        response = app.response_class(response=image_data, status=200, mimetype=content_type)
        response.headers["Cache-Control"] = "public, max-age=86400"
        response.headers["X-Cache"] = "MISS"
    except _IMPORT_OPERATIONAL_ERRORS as e:
        logger.error_trace(f"Cover fetch error: {e}")
        return jsonify({"error": str(e)}), 500
    else:
        return response


@app.route("/api/download/<path:book_id>/cancel", methods=["DELETE"])
@login_required
def api_cancel_download(book_id: str) -> Response | tuple[Response, int]:
    """Cancel a download.

    Path Parameters:
        book_id (str): Book identifier to cancel

    Returns:
        flask.Response: JSON status indicating success or failure.

    """
    viewer_book_id = book_id
    book_id = _resolve_viewer_download_id(book_id)
    try:
        task = backend.book_queue.get_task(book_id)
        if task is None:
            return jsonify({"error": "Failed to cancel download or book not found"}), 404

        is_admin, db_user_id, can_access_status = _resolve_status_scope()
        if not is_admin:
            if not can_access_status or db_user_id is None:
                return jsonify(
                    {"error": "User identity unavailable", "code": "user_identity_unavailable"}
                ), 403

            actor_username = session.get("user_id")
            normalized_actor_username = actor_username if isinstance(actor_username, str) else None
            if not _task_owned_by_actor(
                task,
                actor_user_id=db_user_id,
                actor_username=normalized_actor_username,
            ):
                return jsonify({"error": "Forbidden", "code": "download_not_owned"}), 403

            if getattr(task, "request_id", None) is not None:
                return jsonify(
                    {"error": "Forbidden", "code": "requested_download_cancel_forbidden"}
                ), 403

        success = backend.cancel_download(book_id)
        if success:
            return jsonify({"status": "cancelled", "book_id": viewer_book_id})
        return jsonify({"error": "Failed to cancel download or book not found"}), 404
    except _OPERATIONAL_ERRORS as e:
        logger.error_trace(f"Cancel download error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/download/<path:book_id>/retry", methods=["POST"])
@login_required
def api_retry_download(book_id: str) -> Response | tuple[Response, int]:
    """Retry a failed download."""
    viewer_book_id = book_id
    book_id = _resolve_viewer_download_id(book_id)
    try:
        task = backend.book_queue.get_task(book_id)
        history_row = None
        if task is None and download_history_service is not None:
            history_row = download_history_service.get_by_task_id(book_id)
        if task is None and history_row is None:
            return jsonify({"error": "Download not found"}), 404

        is_admin, db_user_id, can_access_status = _resolve_status_scope()
        actor_username = session.get("user_id")
        normalized_actor_username = actor_username if isinstance(actor_username, str) else None
        if not is_admin:
            if not can_access_status or db_user_id is None:
                return jsonify(
                    {"error": "User identity unavailable", "code": "user_identity_unavailable"}
                ), 403

            if task is not None:
                if not _task_owned_by_actor(
                    task,
                    actor_user_id=db_user_id,
                    actor_username=normalized_actor_username,
                ):
                    return jsonify({"error": "Forbidden", "code": "download_not_owned"}), 403
            elif history_row is None or not _download_row_owned_by_actor(
                history_row,
                actor_user_id=db_user_id,
                actor_username=normalized_actor_username,
            ):
                return jsonify({"error": "Forbidden", "code": "download_not_owned"}), 403

        if task is not None:
            task_status = backend.book_queue.get_task_status(book_id)
            if getattr(
                task, "request_id", None
            ) is not None and not backend.can_retry_download_task(task, task_status):
                return jsonify(
                    {"error": "Forbidden", "code": "requested_download_retry_forbidden"}
                ), 403
            success, error = backend.retry_download(book_id)
        else:
            if history_row is None:
                logger.error("Download history row disappeared while retrying task %s", book_id)
                return jsonify({"error": "Download history not found"}), 404
            request_id = normalize_positive_int(history_row.get("request_id"))
            retry_payload = history_row.get("retry_payload")
            final_status = history_row.get("final_status")
            if request_id is not None:
                history_service = download_history_service
                if history_service is None:
                    logger.error(
                        "Download history service unavailable while retrying task %s", book_id
                    )
                    return jsonify({"error": "Download history unavailable"}), 500
                if not history_service.is_retry_available(history_row):
                    return jsonify(
                        {"error": "Forbidden", "code": "requested_download_retry_forbidden"}
                    ), 403
            success, error = backend.retry_persisted_download(
                retry_payload,
                final_status=final_status,
            )

        if success:
            return jsonify({"status": "queued", "book_id": viewer_book_id})

        if error == "Download not found":
            return jsonify({"error": error}), 404

        return jsonify({"error": error or "Download cannot be retried"}), 409
    except _OPERATIONAL_ERRORS as e:
        logger.error_trace(f"Retry download error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/queue/<path:book_id>/priority", methods=["PUT"])
@login_required
def api_set_priority(book_id: str) -> Response | tuple[Response, int]:
    """Set priority for a queued book.

    Path Parameters:
        book_id (str): Book identifier

    Request Body:
        priority (int): New priority level (lower number = higher priority)

    Returns:
        flask.Response: JSON status indicating success or failure.

    """
    try:
        data = request.get_json(silent=True)
        if not data or "priority" not in data:
            return jsonify({"error": "Priority not provided"}), 400

        priority = int(data["priority"])

        is_admin, db_user_id, actor_username, identity_error = _resolve_queue_actor()
        if identity_error is not None:
            return identity_error, 403

        viewer_book_id = book_id
        book_id = _resolve_viewer_download_id(book_id)
        task = backend.book_queue.get_task(book_id)
        if task is None:
            return jsonify({"error": "Failed to update priority or book not found"}), 404

        if not is_admin and not _task_owned_by_actor(
            task,
            actor_user_id=db_user_id,
            actor_username=actor_username,
        ):
            return jsonify({"error": "Forbidden", "code": "download_not_owned"}), 403

        success = backend.set_book_priority(book_id, priority)

        if success:
            return jsonify({"status": "updated", "book_id": viewer_book_id, "priority": priority})
        return jsonify({"error": "Failed to update priority or book not found"}), 404
    except ValueError:
        return jsonify({"error": "Invalid priority value"}), 400
    except _OPERATIONAL_ERRORS as e:
        logger.error_trace(f"Set priority error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/queue/reorder", methods=["POST"])
@login_required
def api_reorder_queue() -> Response | tuple[Response, int]:
    """Bulk reorder queue by setting new priorities.

    Request Body:
        book_priorities (dict): Mapping of book_id to new priority

    Returns:
        flask.Response: JSON status indicating success or failure.

    """
    try:
        data = request.get_json(silent=True)
        if not data or "book_priorities" not in data:
            return jsonify({"error": "book_priorities not provided"}), 400

        book_priorities = data["book_priorities"]
        if not isinstance(book_priorities, dict):
            return jsonify({"error": "book_priorities must be a dictionary"}), 400

        # Validate all priorities are integers
        for book_id, priority in book_priorities.items():
            if not isinstance(priority, int):
                return jsonify({"error": f"Invalid priority for book {book_id}"}), 400

        is_admin, db_user_id, actor_username, identity_error = _resolve_queue_actor()
        if identity_error is not None:
            return identity_error, 403

        if not is_admin:
            book_priorities = {
                _resolve_viewer_download_id(str(book_id)): priority
                for book_id, priority in book_priorities.items()
            }
            owned_book_priorities = {}
            for book_id in book_priorities:
                task = backend.book_queue.get_task(str(book_id))
                if task is None:
                    continue
                if not _task_owned_by_actor(
                    task, actor_user_id=db_user_id, actor_username=actor_username
                ):
                    return jsonify({"error": "Forbidden", "code": "download_not_owned"}), 403
                owned_book_priorities[book_id] = book_priorities[book_id]
            book_priorities = owned_book_priorities

        success = backend.reorder_queue(book_priorities)

        if success:
            return jsonify({"status": "reordered", "updated_count": len(book_priorities)})
        return jsonify({"error": "Failed to reorder queue"}), 500
    except _OPERATIONAL_ERRORS as e:
        logger.error_trace(f"Reorder queue error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/queue/order", methods=["GET"])
@login_required
def api_queue_order() -> Response | tuple[Response, int]:
    """Get current queue order for display.

    Returns:
        flask.Response: JSON array of queued books with their order and priorities.

    """
    try:
        queue_order = backend.get_queue_order()
        is_admin, db_user_id, actor_username, identity_error = _resolve_queue_actor()
        if identity_error is not None:
            return identity_error, 403
        if not is_admin:
            queue_order = [
                {**item, "id": _viewer_queue_task_id(str(item.get("id", "")))}
                for item in queue_order
                if _queue_task_visible_to_actor(
                    str(item.get("id", "")),
                    is_admin=False,
                    actor_user_id=db_user_id,
                    actor_username=actor_username,
                )
            ]
        return jsonify({"queue": queue_order})
    except _OPERATIONAL_ERRORS as e:
        logger.error_trace(f"Queue order error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/downloads/active", methods=["GET"])
@login_required
def api_active_downloads() -> Response | tuple[Response, int]:
    """Get list of currently active downloads.

    Returns:
        flask.Response: JSON array of active download book IDs.

    """
    try:
        active_downloads = backend.get_active_downloads()
        is_admin, db_user_id, actor_username, identity_error = _resolve_queue_actor()
        if identity_error is not None:
            return identity_error, 403
        if not is_admin:
            active_downloads = [
                _viewer_queue_task_id(task_id)
                for task_id in active_downloads
                if _queue_task_visible_to_actor(
                    task_id,
                    is_admin=False,
                    actor_user_id=db_user_id,
                    actor_username=actor_username,
                )
            ]
        return jsonify({"active_downloads": active_downloads})
    except _OPERATIONAL_ERRORS as e:
        logger.error_trace(f"Active downloads error: {e}")
        return jsonify({"error": str(e)}), 500


@app.errorhandler(404)
def not_found_error(error: Exception) -> Response | tuple[Response, int]:
    """Handle 404 (Not Found) errors.

    Args:
        error (HTTPException): The 404 error raised by Flask.

    Returns:
        flask.Response: JSON error message with 404 status.

    """
    logger.warning("404 error: %s : %s", request.url, error)
    return jsonify({"error": "Resource not found"}), 404


@app.errorhandler(500)
def internal_error(error: Exception) -> Response | tuple[Response, int]:
    """Handle 500 (Internal Server) errors.

    Args:
        error (HTTPException): The 500 error raised by Flask.

    Returns:
        flask.Response: JSON error message with 500 status.

    """
    logger.error_trace(f"500 error: {error}")
    return jsonify({"error": "Internal server error"}), 500


def _failed_login_response(username: str, ip_address: str) -> tuple[Response, int]:
    """Handle a failed login attempt by recording it and returning the appropriate response."""
    is_now_locked = record_failed_login(username, ip_address)

    if is_now_locked:
        return jsonify(
            {
                "error": f"Account locked due to {MAX_LOGIN_ATTEMPTS} failed login attempts. Try again in {LOCKOUT_DURATION_MINUTES} minutes."
            }
        ), 429

    attempts_remaining = MAX_LOGIN_ATTEMPTS - failed_login_attempts[username]["count"]
    if attempts_remaining <= LOGIN_ATTEMPT_WARNING_THRESHOLD:
        return jsonify(
            {"error": f"Invalid username or password. {attempts_remaining} attempts remaining."}
        ), 401

    return jsonify({"error": "Invalid username or password."}), 401


@app.route("/api/auth/login", methods=["POST"])
def api_login() -> Response | tuple[Response, int]:
    """Login endpoint that validates credentials and creates a session.

    Supports both built-in credentials and CWA database authentication.
    Includes rate limiting: 10 failed attempts = 30 minute lockout.

    Request Body:
        username (str): Username
        password (str): Password
        remember_me (bool): Whether to extend session duration

    Returns:
        flask.Response: JSON with success status or error message.

    """
    try:
        ip_address = get_client_ip()
        data = request.get_json(silent=True)
        if not data:
            return jsonify({"error": "No data provided"}), 400

        auth_mode = get_auth_mode()
        if auth_mode == "proxy":
            return jsonify({"error": "Proxy authentication is enabled"}), 401

        if auth_mode in ("builtin", "oidc") and DISABLE_LOCAL_AUTH:
            return jsonify({"error": "Local authentication is disabled"}), 403

        if auth_mode == "oidc" and HIDE_LOCAL_AUTH:
            return jsonify({"error": "Local authentication is disabled"}), 403

        username = data.get("username", "").strip()
        password = data.get("password", "")
        remember_me = data.get("remember_me", False)

        if not username or not password:
            return jsonify({"error": "Username and password are required"}), 400

        # Check if account is locked due to failed login attempts
        if is_account_locked(username):
            lockout_until = _get_lockout_until(username, repair_if_locked=True)
            if lockout_until is None:
                logger.error("Locked account '%s' is missing a lockout timestamp", username)
                return jsonify(
                    {
                        "error": f"Account temporarily locked due to multiple failed login attempts. Try again in {LOCKOUT_DURATION_MINUTES} minutes."
                    }
                ), 429
            remaining_time = (lockout_until - datetime.now(UTC)).total_seconds() / 60
            logger.warning(
                "Login attempt blocked for locked account '%s' from IP %s", username, ip_address
            )
            return jsonify(
                {
                    "error": f"Account temporarily locked due to multiple failed login attempts. Try again in {int(remaining_time)} minutes."
                }
            ), 429

        # If no authentication is configured, authentication always succeeds
        if auth_mode == "none":
            session["user_id"] = username
            session.permanent = remember_me
            clear_failed_logins(username)
            logger.info(
                "Login successful for user '%s' from IP %s (no auth configured)",
                username,
                ip_address,
            )
            return jsonify({"success": True})

        # Password authentication (builtin and OIDC modes)
        # OIDC mode also allows password login as a fallback so admins don't get locked out
        if auth_mode in ("builtin", "oidc"):
            if user_db is None:
                logger.error("User database not available for %s auth", auth_mode)
                return jsonify({"error": "Authentication service unavailable"}), 503
            try:
                db_user = user_db.get_user(username=username)

                if not db_user:
                    return _failed_login_response(username, ip_address)

                # Authenticate against DB user
                if db_user:
                    if not db_user.get("password_hash") or not check_password_hash(
                        db_user["password_hash"], password
                    ):
                        return _failed_login_response(username, ip_address)

                    is_admin = db_user["role"] == "admin"
                    session["user_id"] = username
                    session["db_user_id"] = db_user["id"]
                    session["is_admin"] = is_admin
                    session.permanent = remember_me
                    clear_failed_logins(username)
                    logger.info(
                        "Login successful for user '%s' from IP %s (%s auth, is_admin=%s, remember_me=%s)",
                        username,
                        ip_address,
                        auth_mode,
                        is_admin,
                        remember_me,
                    )
                    return jsonify({"success": True})

                return _failed_login_response(username, ip_address)

            except _OPERATIONAL_ERRORS as e:
                logger.error_trace(f"Built-in auth error: {e}")
                return jsonify({"error": "Authentication system error"}), 500

        # CWA database authentication mode
        if auth_mode == "cwa":
            # Verify database still exists (it was validated at startup)
            if not CWA_DB_PATH or not CWA_DB_PATH.exists():
                logger.error("CWA database at %s is no longer accessible", CWA_DB_PATH)
                return jsonify({"error": "Database configuration error"}), 500

            try:
                db_path = os.fspath(CWA_DB_PATH)
                db_uri = f"file:{db_path}?mode=ro&immutable=1"
                conn = sqlite3.connect(db_uri, uri=True)
                cur = conn.cursor()
                cur.execute("SELECT password, role, email FROM user WHERE name = ?", (username,))
                row = cur.fetchone()
                conn.close()

                # Check if user exists and password is correct
                if not row or not row[0] or not check_password_hash(row[0], password):
                    return _failed_login_response(username, ip_address)

                # Check if user has admin role (ROLE_ADMIN = 1, bit flag)
                user_role = row[1] if row[1] is not None else 0
                is_admin = (user_role & 1) == 1
                cwa_email = row[2] or None

                db_user_id = None
                if user_db is not None:
                    role = "admin" if is_admin else "user"
                    db_user, _ = upsert_cwa_user(
                        user_db,
                        cwa_username=username,
                        cwa_email=cwa_email,
                        role=role,
                        context="cwa_login",
                    )
                    db_user_id = db_user["id"]

                # Successful authentication - create session and clear failed attempts
                session["user_id"] = username
                session["is_admin"] = is_admin
                if db_user_id is not None:
                    session["db_user_id"] = db_user_id
                session.permanent = remember_me
                clear_failed_logins(username)
                logger.info(
                    "Login successful for user '%s' from IP %s (CWA auth, is_admin=%s, remember_me=%s)",
                    username,
                    ip_address,
                    is_admin,
                    remember_me,
                )
                return jsonify({"success": True})

            except _OPERATIONAL_ERRORS as e:
                logger.error_trace(f"CWA database error during login: {e}")
                return jsonify({"error": "Authentication system error"}), 500

        # Should not reach here, but handle gracefully
        return jsonify({"error": "Unknown authentication mode"}), 500

    except _OPERATIONAL_ERRORS as e:
        logger.error_trace(f"Login error: {e}")
        return jsonify({"error": "Login failed"}), 500


@app.route("/api/auth/logout", methods=["POST"])
def api_logout() -> Response | tuple[Response, int]:
    """Logout endpoint that clears the session.

    For proxy auth, returns the logout URL if configured.

    Returns:
        flask.Response: JSON with success status and optional logout_url.

    """
    try:
        auth_mode = get_auth_mode()
        ip_address = get_client_ip()
        username = session.get("user_id", "unknown")
        session.clear()
        logger.info("Logout successful for user '%s' from IP %s", username, ip_address)

        # For proxy auth, include logout URL if configured
        if auth_mode == "proxy":
            logout_url = app_config.get("PROXY_AUTH_LOGOUT_URL", "")
            if logout_url:
                return jsonify({"success": True, "logout_url": logout_url})

        return jsonify({"success": True})
    except _OPERATIONAL_ERRORS as e:
        logger.error_trace(f"Logout error: {e}")
        return jsonify({"error": "Logout failed"}), 500


@app.route("/api/auth/check", methods=["GET"])
def api_auth_check() -> Response | tuple[Response, int]:
    """Check if user has a valid session.

    Returns:
        flask.Response: JSON with authentication status, whether auth is required,
        which auth mode is active, and whether user has admin privileges.

    """
    try:
        auth_mode = get_auth_mode()

        # If no authentication is configured, access is allowed (full admin)
        if auth_mode == "none":
            return jsonify(
                {
                    "authenticated": True,
                    "auth_required": False,
                    "auth_mode": "none",
                    "is_admin": True,
                }
            )

        # Check if user has a valid session
        is_authenticated = "user_id" in session

        is_admin = get_auth_check_admin_status(auth_mode, {}, session)

        display_name = None
        if is_authenticated and session.get("db_user_id") and user_db is not None:
            try:
                db_user = user_db.get_user(user_id=session["db_user_id"])
                if db_user:
                    display_name = db_user.get("display_name") or None
            except (sqlite3.Error, TypeError, ValueError) as exc:
                logger.debug("Could not load display name for session user: %s", exc)

        response_data = {
            "authenticated": is_authenticated,
            "auth_required": True,
            "auth_mode": auth_mode,
            "is_admin": is_admin if is_authenticated else False,
            "username": session.get("user_id") if is_authenticated else None,
            "display_name": display_name,
        }

        # Add logout URL for proxy auth if configured
        if auth_mode == "proxy" and app_config.get("PROXY_AUTH_USER_HEADER", ""):
            logout_url = app_config.get("PROXY_AUTH_LOGOUT_URL", "")
            if logout_url:
                response_data["logout_url"] = logout_url

        if auth_mode in ("builtin", "oidc") and DISABLE_LOCAL_AUTH:
            response_data["hide_local_auth"] = True

        # Add custom OIDC button label and SSO enforcement flags if configured
        if auth_mode == "oidc":
            oidc_button_label = app_config.get("OIDC_BUTTON_LABEL", "")
            if oidc_button_label:
                response_data["oidc_button_label"] = oidc_button_label
            if HIDE_LOCAL_AUTH:
                response_data["hide_local_auth"] = True
            if OIDC_AUTO_REDIRECT:
                response_data["oidc_auto_redirect"] = True

        return jsonify(response_data)
    except _OPERATIONAL_ERRORS as e:
        logger.error_trace(f"Auth check error: {e}")
        return jsonify(
            {
                "authenticated": False,
                "auth_required": True,
                "auth_mode": "unknown",
                "is_admin": False,
            }
        )


@app.route("/api/metadata/providers", methods=["GET"])
@login_required
def api_metadata_providers() -> Response | tuple[Response, int]:
    """Get list of available metadata providers.

    Returns:
        flask.Response: JSON with list of providers and their status.

    """
    try:
        from shelfmark.metadata_providers import (
            get_configured_provider_name,
            get_provider,
            get_provider_kwargs,
            list_providers,
        )

        app_config.refresh()
        db_user_id = get_session_db_user_id(session)

        configured_metadata_provider = get_configured_provider_name(
            content_type="ebook",
            user_id=db_user_id,
            fallback_to_main=True,
        )
        configured_audiobook_metadata_provider = get_configured_provider_name(
            content_type="audiobook",
            user_id=db_user_id,
            fallback_to_main=False,
        )
        configured_combined_metadata_provider = get_configured_provider_name(
            content_type="combined",
            user_id=db_user_id,
            fallback_to_main=False,
        )
        providers = []
        for info in list_providers():
            enabled_key = f"{info['name'].upper()}_ENABLED"
            provider_info = {
                "name": info["name"],
                "display_name": info["display_name"],
                "requires_auth": info["requires_auth"],
                "enabled": app_config.get(enabled_key, False) is True,
                "available": False,
            }

            try:
                kwargs = get_provider_kwargs(info["name"])
                provider = get_provider(info["name"], **kwargs)
                provider_info["available"] = provider.is_available()
            except _OPERATIONAL_ERRORS as exc:
                logger.debug(
                    "Metadata provider %s availability check failed: %s",
                    info["name"],
                    exc,
                )

            providers.append(provider_info)

        return jsonify(
            {
                "providers": providers,
                "configured_provider": configured_metadata_provider or None,
                "configured_provider_audiobook": configured_audiobook_metadata_provider or None,
                "configured_provider_combined": configured_combined_metadata_provider or None,
            }
        )
    except _IMPORT_OPERATIONAL_ERRORS as e:
        logger.error_trace(f"Metadata providers error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/metadata/config", methods=["GET"])
@login_required
def api_metadata_config() -> Response | tuple[Response, int]:
    """Return provider-specific metadata search config for the active session."""
    try:
        from shelfmark.metadata_providers import (
            get_configured_provider_name,
            get_provider,
            get_provider_capabilities,
            get_provider_default_sort,
            get_provider_kwargs,
            get_provider_search_fields,
            get_provider_sort_options,
            is_provider_registered,
        )

        app_config.refresh()
        content_type = request.args.get("content_type", "ebook").strip()
        provider_name = request.args.get("provider", "").strip()

        db_user_id = get_session_db_user_id(session)

        if not provider_name:
            provider_name = get_configured_provider_name(
                content_type=content_type,
                user_id=db_user_id,
                fallback_to_main=True,
            )

        if not provider_name:
            return jsonify(
                {
                    "provider": None,
                    "display_name": None,
                    "enabled": False,
                    "available": False,
                    "search_fields": [],
                    "capabilities": [],
                    "sort_options": [{"value": "relevance", "label": "Most relevant"}],
                    "default_sort": "relevance",
                }
            )

        if not is_provider_registered(provider_name):
            return jsonify({"error": f"Unknown metadata provider: {provider_name}"}), 400

        kwargs = get_provider_kwargs(provider_name)
        provider = get_provider(provider_name, **kwargs)
        enabled_key = f"{provider_name.upper()}_ENABLED"
        provider_enabled = app_config.get(enabled_key, False) is True
        provider_available = provider.is_available()

        return jsonify(
            {
                "provider": provider_name,
                "display_name": provider.display_name,
                "enabled": provider_enabled,
                "available": provider_available,
                "search_fields": get_provider_search_fields(provider_name),
                "capabilities": get_provider_capabilities(provider_name),
                "sort_options": get_provider_sort_options(provider_name),
                "default_sort": get_provider_default_sort(provider_name, user_id=db_user_id),
            }
        )
    except _IMPORT_OPERATIONAL_ERRORS as e:
        logger.error_trace(f"Metadata config error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/metadata/search", methods=["GET"])
@login_required
def api_metadata_search() -> Response | tuple[Response, int]:
    """Search for books using the configured metadata provider.

    Query Parameters:
        query (str): Search query (required)
        limit (int): Maximum number of results (default: 40, max: 100)
        sort (str): Sort order - relevance, popularity, rating, newest, oldest (default: relevance)
        [dynamic fields]: Provider-specific search fields passed as query params

    Returns:
        flask.Response: JSON with list of books from metadata provider.

    """
    try:
        from dataclasses import asdict

        from shelfmark.metadata_providers import (
            CheckboxSearchField,
            MetadataSearchOptions,
            NumberSearchField,
            SortOrder,
            get_configured_provider,
            get_provider,
            get_provider_kwargs,
            is_provider_enabled,
            is_provider_registered,
        )

        query = request.args.get("query", "").strip()
        content_type = request.args.get("content_type", "ebook").strip()
        provider_name = request.args.get("provider", "").strip()

        try:
            limit = min(int(request.args.get("limit", 40)), 100)
        except ValueError:
            limit = 40

        try:
            page = max(1, int(request.args.get("page", 1)))
        except ValueError:
            page = 1

        # Parse sort parameter
        sort_value = request.args.get("sort", "relevance").lower()
        try:
            sort_order = SortOrder(sort_value)
        except ValueError:
            sort_order = SortOrder.RELEVANCE

        db_user_id = get_session_db_user_id(session)

        if provider_name:
            if not is_provider_registered(provider_name):
                return jsonify(
                    {
                        "error": f"Unknown metadata provider: {provider_name}",
                        "message": f"Unknown metadata provider: {provider_name}",
                    }
                ), 400
            if not is_provider_enabled(provider_name):
                return jsonify(
                    {
                        "error": f"Metadata provider '{provider_name}' is not enabled",
                        "message": f"{provider_name} is not enabled. Enable it in Settings first.",
                    }
                ), 503

            kwargs = get_provider_kwargs(provider_name)
            provider = get_provider(provider_name, **kwargs)
        else:
            provider = get_configured_provider(content_type=content_type, user_id=db_user_id)

        if not provider:
            return jsonify(
                {
                    "error": "No metadata provider configured",
                    "message": "No metadata provider configured. Enable one in Settings.",
                }
            ), 503

        if not provider.is_available():
            return jsonify(
                {
                    "error": f"Metadata provider '{provider.name}' is not available",
                    "message": f"{provider.display_name} is not available. Check configuration in Settings.",
                }
            ), 503

        # Extract custom search field values from query params
        fields: dict[str, Any] = {}
        for search_field in provider.search_fields:
            value = request.args.get(search_field.key)
            if value is not None:
                # Strip string values to handle whitespace-only input
                value = value.strip()
                if value != "":
                    # Parse value based on field type
                    if isinstance(search_field, CheckboxSearchField):
                        fields[search_field.key] = value.lower() in ("true", "1", "yes", "on")
                    elif isinstance(search_field, NumberSearchField):
                        with suppress(ValueError):
                            fields[search_field.key] = int(value)
                    else:
                        fields[search_field.key] = value

        # Require either a query or at least one field value
        if not query and not fields:
            return jsonify({"error": "Either 'query' or search field values are required"}), 400

        options = MetadataSearchOptions(
            query=query, limit=limit, page=page, sort=sort_order, fields=fields
        )
        search_result = provider.search_paginated(options)

        # Convert BookMetadata objects to dicts
        books_data = [asdict(book) for book in search_result.books]

        # Transform cover_url to local proxy URLs when caching is enabled
        from shelfmark.core.utils import transform_cover_url

        for book_dict in books_data:
            if book_dict.get("cover_url"):
                cache_id = f"{book_dict['provider']}_{book_dict['provider_id']}"
                book_dict["cover_url"] = transform_cover_url(book_dict["cover_url"], cache_id)
        for book, book_dict in zip(search_result.books, books_data, strict=True):
            _attach_library_ownership(book, book_dict)

        response_data = {
            "books": books_data,
            "provider": provider.name,
            "query": query,
            "page": search_result.page,
            "total_found": search_result.total_found,
            "has_more": search_result.has_more,
        }
        if search_result.source_url:
            response_data["source_url"] = search_result.source_url
        if search_result.source_title:
            response_data["source_title"] = search_result.source_title
        return jsonify(response_data)
    except _IMPORT_OPERATIONAL_ERRORS as e:
        logger.error_trace(f"Metadata search error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/metadata/field-options", methods=["GET"])
@login_required
def api_metadata_field_options() -> Response:
    """Return dynamic search-field options for a metadata provider."""
    try:
        from shelfmark.metadata_providers import (
            get_configured_provider,
            get_provider,
            get_provider_kwargs,
            is_provider_registered,
        )

        field_key = request.args.get("field", "").strip()
        provider_name = request.args.get("provider", "").strip()
        content_type = request.args.get("content_type", "ebook").strip()
        query_text = request.args.get("query", "").strip()

        if not field_key:
            return jsonify({"options": []})

        db_user_id = get_session_db_user_id(session)

        provider = None
        if provider_name:
            if not is_provider_registered(provider_name):
                return jsonify({"options": []})
            kwargs = get_provider_kwargs(provider_name)
            provider = get_provider(provider_name, **kwargs)
        else:
            provider = get_configured_provider(content_type=content_type, user_id=db_user_id)

        if not provider or not provider.is_available():
            return jsonify({"options": []})

        options = provider.get_search_field_options(field_key, query=query_text or None)
        return jsonify({"options": options})
    except _OPERATIONAL_ERRORS as e:
        logger.warning("Metadata field options endpoint error: %s", e)
        return jsonify({"options": []})


def _attach_library_ownership(book: Any, book_dict: dict[str, Any]) -> None:
    """Add per-format ownership under ``library`` when a library check is enabled."""
    from shelfmark.core import library_index

    owned = library_index.ownership(book)
    if owned is not None:
        book_dict["library"] = owned


def _resolve_metadata_provider(provider_name: str) -> MetadataProvider:
    """Validate, instantiate and return a ready metadata provider.

    Raises appropriate HTTP-friendly exceptions on failure.
    """
    from shelfmark.metadata_providers import (
        get_provider,
        get_provider_kwargs,
        is_provider_registered,
    )

    if not is_provider_registered(provider_name):
        msg = f"Unknown metadata provider: {provider_name}"
        raise ValueError(msg)

    kwargs = get_provider_kwargs(provider_name)
    prov = get_provider(provider_name, **kwargs)

    if not prov.is_available():
        msg = f"Provider '{provider_name}' is not available"
        raise RuntimeError(msg)

    return prov


@app.route("/api/metadata/book/<provider>/<book_id>", methods=["GET"])
@login_required
def api_metadata_book(provider: str, book_id: str) -> Response | tuple[Response, int]:
    """Get detailed book information from a metadata provider.

    Path Parameters:
        provider (str): Provider name (e.g., "hardcover", "openlibrary")
        book_id (str): Book ID in the provider's system

    Returns:
        flask.Response: JSON with book details.

    """
    try:
        from dataclasses import asdict

        prov = _resolve_metadata_provider(provider)

        book = prov.get_book(book_id)
        if not book:
            return jsonify({"error": "Book not found"}), 404

        book_dict = asdict(book)
        _attach_library_ownership(book, book_dict)

        # Transform cover_url to local proxy URL when caching is enabled
        from shelfmark.core.utils import transform_cover_url

        if book_dict.get("cover_url"):
            cache_id = f"{provider}_{book_id}"
            book_dict["cover_url"] = transform_cover_url(book_dict["cover_url"], cache_id)

        return jsonify(book_dict)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 503
    except (OSError, TypeError, sqlite3.Error) as e:
        logger.error_trace(f"Metadata book error: {e}")
        return jsonify({"error": str(e)}), 500


def _handle_target_errors(
    fallback_message: str,
) -> Callable[
    [Callable[..., Response | tuple[Response, int]]], Callable[..., Response | tuple[Response, int]]
]:
    """Wrap a metadata-target route with standard error handling."""

    def decorator(
        fn: Callable[..., Response | tuple[Response, int]],
    ) -> Callable[..., Response | tuple[Response, int]]:
        @wraps(fn)
        def wrapper(*args: object, **kwargs: object) -> Response | tuple[Response, int]:
            try:
                return fn(*args, **kwargs)
            except (NotImplementedError, ValueError) as e:
                return jsonify({"error": str(e)}), 400
            except RuntimeError as e:
                return jsonify({"error": str(e)}), 502
            except (OSError, TypeError, sqlite3.Error) as e:
                logger.error_trace(f"{fallback_message}: {e}")
                return jsonify({"error": fallback_message}), 500

        return wrapper

    return decorator


@app.route("/api/metadata/book/<provider>/<book_id>/targets", methods=["GET"])
@login_required
@_handle_target_errors("Failed to load book targets")
def api_metadata_book_targets(provider: str, book_id: str) -> Response | tuple[Response, int]:
    """Get provider-managed list/status targets for a specific book."""
    prov = _resolve_metadata_provider(provider)
    return jsonify({"options": prov.get_book_targets(book_id)})


@app.route("/api/metadata/book/<provider>/targets/batch", methods=["POST"])
@login_required
@_handle_target_errors("Failed to load book targets")
def api_metadata_book_targets_batch(provider: str) -> Response | tuple[Response, int]:
    """Get provider-managed list/status targets for multiple books."""
    prov = _resolve_metadata_provider(provider)

    payload = request.get_json(silent=True) or {}
    raw_ids = payload.get("book_ids", []) if isinstance(payload, dict) else []
    if not isinstance(raw_ids, list) or not raw_ids:
        return jsonify({"error": "book_ids must be a non-empty array"}), 400

    book_ids = [str(bid) for bid in raw_ids[:50]]

    return jsonify({"results": prov.get_book_targets_batch(book_ids)})


@app.route("/api/metadata/book/<provider>/<book_id>/targets", methods=["PUT"])
@login_required
@_handle_target_errors("Failed to update book targets")
def api_metadata_book_targets_update(
    provider: str, book_id: str
) -> Response | tuple[Response, int]:
    """Set whether a book belongs to a provider-managed list or shelf."""
    prov = _resolve_metadata_provider(provider)

    payload = request.get_json(silent=True) or {}
    target = str(payload.get("target", "")).strip() if isinstance(payload, dict) else ""
    selected = payload.get("selected") if isinstance(payload, dict) else None
    if not target:
        return jsonify({"error": "target is required"}), 400
    if not isinstance(selected, bool):
        return jsonify({"error": "selected must be a boolean"}), 400

    result = prov.set_book_target_state(book_id, target, selected=selected)
    response: dict = {
        "success": True,
        "changed": bool(result.get("changed", True)),
        "selected": selected,
    }
    deselected = result.get("deselected_target")
    if isinstance(deselected, str) and deselected:
        response["deselected_target"] = deselected
    return jsonify(response)


@app.route("/api/releases", methods=["GET"])
@login_required
def api_releases() -> Response | tuple[Response, int]:
    """Search for downloadable releases of a book.

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
        from dataclasses import asdict

        from shelfmark.core.release_search import search_source_releases
        from shelfmark.metadata_providers import (
            BookMetadata,
            get_provider,
            get_provider_kwargs,
            is_provider_registered,
        )
        from shelfmark.release_sources import (
            apply_column_visibility,
            browse_record_to_book_metadata,
            get_source,
            list_available_sources,
            serialize_column_config,
            source_results_are_releases,
        )

        def _search_source_releases(
            source_name: str, search_book: BookMetadata
        ) -> tuple[Any | None, list[Any], str | None]:
            """Search one source and return any error message instead of raising."""
            return search_source_releases(
                source_name,
                search_book,
                languages=(browse_filters.lang if source_query_filters is not None else languages),
                manual_query=(query_text if source_query_filters is not None else manual_query),
                indexers=indexers,
                expand_search=expand_search,
                content_type=content_type,
                source_filters=source_query_filters,
                user_id=db_user_id,
            )

        provider = request.args.get("provider", "").strip()
        book_id = request.args.get("book_id", "").strip()
        source_filter = request.args.get("source", "").strip()
        query_text = request.args.get("query", "").strip()
        # Accept title/author from frontend to avoid re-fetching metadata
        title_param = request.args.get("title", "").strip()
        author_param = request.args.get("author", "").strip()
        expand_search = request.args.get("expand_search", "").lower() == "true"
        # Accept language codes for filtering (comma-separated)
        languages_param = request.args.get("languages", "").strip()
        languages = (
            [lang.strip() for lang in languages_param.split(",") if lang.strip()]
            if languages_param
            else None
        )
        # Without an explicit filter the plan falls back to this user's default languages.
        db_user_id = get_session_db_user_id(session)
        # Content type for audiobook vs ebook search
        content_type = request.args.get("content_type", "ebook").strip()

        manual_query = request.args.get("manual_query", "").strip()

        # Accept indexer names for Prowlarr filtering (comma-separated)
        indexers_param = request.args.get("indexers", "").strip()
        indexers = (
            [idx.strip() for idx in indexers_param.split(",") if idx.strip()]
            if indexers_param
            else None
        )
        browse_filters = _parse_search_filters_from_request()
        has_browse_filters = bool(query_text or any(vars(browse_filters).values()))

        source_query_filters = None
        is_source_provider = bool(provider) and source_results_are_releases(provider)

        book: BookMetadata

        if not provider or not book_id:
            if not source_filter or not has_browse_filters:
                return jsonify({"error": "Parameters 'provider' and 'book_id' are required"}), 400
            if not source_results_are_releases(source_filter):
                return jsonify(
                    {"error": f"Source does not support browse release search: {source_filter}"}
                ), 400

            book = _build_source_query_book(query_text, browse_filters)
            source_query_filters = browse_filters
        elif is_source_provider:
            # Source-backed browse flows can reopen the release modal with provider=<source name>.
            # In that flow, treat the source-native record as release-search context instead of
            # requiring a metadata provider registration.
            source = get_source(provider)
            direct_record = source.get_record(book_id)
            if direct_record is None:
                return jsonify({"error": "Book not found in release source"}), 404

            book = browse_record_to_book_metadata(
                direct_record,
                title_override=title_param or None,
                author_override=author_param or None,
            )
        elif provider == "manual":
            resolved_title = title_param or manual_query or "Manual Search"
            resolved_author = author_param or ""
            # The release modal sends `authors.join(', ')` as `author`, so the commas here
            # are joins between contributors, not part of one name. This split is the only
            # place that knows that, so `search_author` comes from it rather than from the
            # joined text - see issue #1252.
            authors = [a.strip() for a in resolved_author.split(",") if a.strip()]

            book = BookMetadata(
                provider="manual",
                provider_id=book_id,
                provider_display_name="Manual Search",
                title=resolved_title,
                search_title=resolved_title,
                search_author=authors[0] if authors else None,
                authors=authors,
            )
        else:
            if not is_provider_registered(provider):
                return jsonify({"error": f"Unknown metadata provider: {provider}"}), 400

            # Get book metadata from provider
            kwargs = get_provider_kwargs(provider)
            prov = get_provider(provider, **kwargs)
            resolved_book = prov.get_book(book_id)

            if not resolved_book:
                return jsonify({"error": "Book not found in metadata provider"}), 404
            book = resolved_book

            # Override title from frontend if available (search results may have better data)
            # Note: We intentionally DON'T override authors here - get_book() now returns
            # filtered authors (primary authors only, excluding translators/narrators),
            # which gives better release search results than the unfiltered search data
            if title_param:
                book.title = title_param

        # Determine which release sources to search
        if source_query_filters is not None or source_filter:
            sources_to_search = [source_filter]
        elif is_source_provider:
            # Source-backed browse flows stay within the source that produced the record.
            sources_to_search = [provider]
        else:
            # Search only enabled sources
            sources_to_search = [src["name"] for src in list_available_sources() if src["enabled"]]

        # Search each source for releases.
        #
        # Under a wall-clock budget: this endpoint is synchronous, and the bypass path it
        # can reach used to be allowed minutes per URL with nothing bounding the request
        # as a whole. A search that ran into an unsolvable protection challenge therefore
        # outlived every reverse proxy in front of it and surfaced to the user as
        # "Server unavailable (504)" - a gateway timeout that blames their proxy for a
        # challenge failure. The budget is shared across sources, so a stuck first source
        # cannot spend the whole request on its own. See issue #1276.
        all_releases = []
        errors = []
        source_instances = {}  # Keep source instances for column config

        # A real search is under way, so a warm-up still sitting on its start-up delay
        # should stand down rather than queue its throwaway solve in front of this one.
        warmup.note_user_search()

        with search_deadline.search_deadline():
            for source_name in sources_to_search:
                if search_deadline.expired():
                    logger.warning("Release search budget spent; %s not searched", source_name)
                    errors.append(f"{source_name}: {search_deadline.deadline_message()}")
                    continue

                source, releases, error = _search_source_releases(source_name, book)
                if source is not None:
                    source_instances[source_name] = source
                    all_releases.extend(releases)
                if error is not None:
                    errors.append(error)

        # Convert Release objects to dicts
        releases_data = [_serialize_release(release) for release in all_releases]

        # Get column config from the first source searched
        # Reuse the same instance to get any dynamic data (e.g., online_servers for IRC)
        column_config = None
        if sources_to_search and sources_to_search[0] in source_instances:
            try:
                first_source = source_instances[sources_to_search[0]]
                column_config = serialize_column_config(
                    apply_column_visibility(
                        first_source.get_column_config(),
                        content_type=normalize_content_type(content_type),
                        is_setting_enabled=lambda key: bool(
                            app_config.get(key, True, user_id=db_user_id)
                        ),
                    )
                )
            except _OPERATIONAL_ERRORS as e:
                logger.warning("Failed to get column config: %s", e)

        # Convert book to dict and transform cover_url
        book_dict = asdict(book)
        _attach_library_ownership(book, book_dict)
        from shelfmark.core.utils import transform_cover_url

        if book_dict.get("cover_url"):
            cache_id = f"{provider}_{book_id}"
            book_dict["cover_url"] = transform_cover_url(book_dict["cover_url"], cache_id)

        search_info = {}
        for source_name, source_instance in source_instances.items():
            info: dict[str, str | int | None] = {}
            if hasattr(source_instance, "last_search_type") and source_instance.last_search_type:
                info["search_type"] = source_instance.last_search_type
            if hasattr(source_instance, "total_results"):
                info["total_results"] = source_instance.total_results
            if hasattr(source_instance, "filtered_out_count"):
                info["filtered_out_count"] = source_instance.filtered_out_count
            if info:
                search_info[source_name] = info

        response = {
            "releases": releases_data,
            "book": book_dict,
            "sources_searched": sources_to_search,
            "column_config": column_config,
            "search_info": search_info,
        }

        if errors:
            response["errors"] = errors

        # If no releases found and there were errors, return 503 with the first
        # source failure message so direct-mode source query searches surface the
        # same unavailable-state messaging as release modal searches.
        if not releases_data and errors:
            # Use the first error message (typically the most relevant)
            error_message = errors[0]
            # Strip the source prefix if present (e.g., "direct_download: message" -> "message")
            if ": " in error_message:
                error_message = error_message.split(": ", 1)[1]
            return jsonify({"error": error_message}), 503

        return jsonify(response)
    except SourceUnavailableError as e:
        logger.warning("Release search unavailable: %s", e)
        return jsonify({"error": str(e)}), 503
    except _IMPORT_OPERATIONAL_ERRORS as e:
        logger.error_trace(f"Releases search error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/release-sources", methods=["GET"])
@login_required
def api_release_sources() -> Response | tuple[Response, int]:
    """Get available release sources from the plugin registry.

    Returns:
        flask.Response: JSON list of available release sources.

    """
    try:
        from shelfmark.release_sources import list_available_sources

        sources = list_available_sources()
        return jsonify(sources)
    except _IMPORT_OPERATIONAL_ERRORS as e:
        logger.error_trace(f"Release sources error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/release-sources/<source_name>/records/<path:record_id>", methods=["GET"])
@login_required
def api_release_source_record(source_name: str, record_id: str) -> Response | tuple[Response, int]:
    """Resolve a source-native browse record for a release source."""
    try:
        from shelfmark.release_sources import get_source

        source = get_source(source_name)
        record = source.get_record(record_id)
        if record is None:
            return jsonify({"error": "Record not found"}), 404
        return jsonify(_serialize_browse_record(record))
    except ValueError:
        return jsonify({"error": f"Unknown release source: {source_name}"}), 400
    except SourceUnavailableError as e:
        logger.warning("Release source record unavailable: %s", e)
        return jsonify({"error": str(e)}), 503
    except _OPERATIONAL_ERRORS as e:
        logger.error_trace(f"Release source record error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/settings", methods=["GET"])
@login_required
def api_settings_get_all() -> Response | tuple[Response, int]:
    """Get all settings tabs with their fields and current values.

    Returns:
        flask.Response: JSON with all settings tabs.

    """
    try:
        import_module("shelfmark.config.notifications_settings")
        import_module("shelfmark.config.security")

        # Ensure settings are registered by importing settings modules
        # This triggers the @register_settings decorators
        import_module("shelfmark.config.settings")
        import_module("shelfmark.config.users_settings")
        from shelfmark.core.settings_registry import serialize_all_settings

        data = serialize_all_settings(include_values=True)
        return jsonify(data)
    except _IMPORT_OPERATIONAL_ERRORS as e:
        logger.error_trace(f"Settings get error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/settings/<tab_name>", methods=["GET"])
@login_required
def api_settings_get_tab(tab_name: str) -> Response | tuple[Response, int]:
    """Get settings for a specific tab.

    Path Parameters:
        tab_name (str): Settings tab name (e.g., "general", "hardcover")

    Returns:
        flask.Response: JSON with tab settings and values.

    """
    try:
        import_module("shelfmark.config.notifications_settings")
        import_module("shelfmark.config.security")

        # Ensure settings are registered
        import_module("shelfmark.config.settings")
        import_module("shelfmark.config.users_settings")
        from shelfmark.core.settings_registry import (
            get_settings_tab,
            serialize_tab,
        )

        tab = get_settings_tab(tab_name)
        if not tab:
            return jsonify({"error": f"Unknown settings tab: {tab_name}"}), 404

        return jsonify(serialize_tab(tab, include_values=True))
    except _IMPORT_OPERATIONAL_ERRORS as e:
        logger.error_trace(f"Settings get tab error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/settings/<tab_name>", methods=["PUT"])
@login_required
def api_settings_update_tab(tab_name: str) -> Response | tuple[Response, int]:
    """Update settings for a specific tab.

    Path Parameters:
        tab_name (str): Settings tab name

    Request Body:
        JSON object with setting keys and values to update.

    Returns:
        flask.Response: JSON with update result.

    """
    try:
        import_module("shelfmark.config.notifications_settings")
        import_module("shelfmark.config.security")

        # Ensure settings are registered
        import_module("shelfmark.config.settings")
        import_module("shelfmark.config.users_settings")
        from shelfmark.core.settings_registry import (
            get_settings_tab,
            update_settings,
        )

        tab = get_settings_tab(tab_name)
        if not tab:
            return jsonify({"error": f"Unknown settings tab: {tab_name}"}), 404

        values = request.get_json(silent=True)
        if values is None or not isinstance(values, dict):
            return jsonify({"error": "Request body must be a JSON object"}), 400

        # If no values to update, return success with empty updated list
        if not values:
            return jsonify({"success": True, "message": "No changes to save", "updated": []})

        result = update_settings(tab_name, values)

        if result["success"]:
            return jsonify(result)
        return jsonify(result), 400
    except _IMPORT_OPERATIONAL_ERRORS as e:
        logger.error_trace(f"Settings update error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/settings/<tab_name>/action/<action_key>", methods=["POST"])
@login_required
def api_settings_execute_action(tab_name: str, action_key: str) -> Response | tuple[Response, int]:
    """Execute a settings action (e.g., test connection).

    Path Parameters:
        tab_name (str): Settings tab name
        action_key (str): Action key to execute

    Request Body (optional):
        JSON object with current form values (unsaved)

    Returns:
        flask.Response: JSON with action result.

    """
    try:
        import_module("shelfmark.config.notifications_settings")
        import_module("shelfmark.config.security")

        # Ensure settings are registered
        import_module("shelfmark.config.settings")
        import_module("shelfmark.config.users_settings")
        from shelfmark.core.settings_registry import execute_action

        # Get current form values if provided (for testing with unsaved values)
        current_values = request.get_json(silent=True) or {}

        result = execute_action(tab_name, action_key, current_values)

        if result["success"]:
            return jsonify(result)
        return jsonify(result), 400
    except _IMPORT_OPERATIONAL_ERRORS as e:
        logger.error_trace(f"Settings action error: {e}")
        return jsonify({"error": str(e)}), 500


# =============================================================================
# Onboarding API
# =============================================================================


@app.route("/api/onboarding", methods=["GET"])
@login_required
def api_onboarding_get() -> Response | tuple[Response, int]:
    """Get onboarding configuration including steps, fields, and current values.

    Returns:
        flask.Response: JSON with onboarding steps and values.

    """
    try:
        # Ensure settings are registered
        import_module("shelfmark.config.settings")
        from shelfmark.core.onboarding import get_onboarding_config

        config = get_onboarding_config()
        return jsonify(config)
    except _IMPORT_OPERATIONAL_ERRORS as e:
        logger.error_trace(f"Onboarding get error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/onboarding", methods=["POST"])
@login_required
def api_onboarding_save() -> Response | tuple[Response, int]:
    """Save onboarding settings and mark as complete.

    Request Body:
        JSON object with all onboarding field values

    Returns:
        flask.Response: JSON with success/error status.

    """
    try:
        # Ensure settings are registered
        import_module("shelfmark.config.settings")
        from shelfmark.core.onboarding import save_onboarding_settings

        data = request.get_json(silent=True)
        if not data:
            return jsonify({"success": False, "message": "No data provided"}), 400

        result = save_onboarding_settings(data)

        if result["success"]:
            return jsonify(result)
        return jsonify(result), 400
    except _IMPORT_OPERATIONAL_ERRORS as e:
        logger.error_trace(f"Onboarding save error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/onboarding/skip", methods=["POST"])
@login_required
def api_onboarding_skip() -> Response | tuple[Response, int]:
    """Skip onboarding and mark as complete without saving any settings.

    Returns:
        flask.Response: JSON with success status.

    """
    try:
        from shelfmark.core.onboarding import mark_onboarding_complete

        mark_onboarding_complete()
        return jsonify({"success": True, "message": "Onboarding skipped"})
    except _IMPORT_OPERATIONAL_ERRORS as e:
        logger.error_trace(f"Onboarding skip error: {e}")
        return jsonify({"error": str(e)}), 500


# Catch-all route for React Router (must be last)
# This handles client-side routing by serving index.html for any unmatched routes
@app.route("/<path:path>")
def catch_all(path: str) -> Response | tuple[Response, int]:
    """Serve the React app for any route not matched by API endpoints.

    This allows React Router to handle client-side routing.
    Authentication is handled by the React app itself.
    """
    # If the request is for an API endpoint or static file, let it 404
    if path.startswith(("api/", "assets/")):
        return jsonify({"error": "Resource not found"}), 404
    # Otherwise serve the React app
    return _serve_index_html()


def _get_request_sid() -> str | None:
    """Return the Socket.IO session id for the active request when available."""
    sid = getattr(request, "sid", None)
    return sid if isinstance(sid, str) and sid else None


# WebSocket event handlers
@socketio.on("connect")
def handle_connect() -> None:
    """Handle client connection."""
    logger.info("WebSocket client connected")

    # Track the connection (triggers warmup callbacks on first connect)
    ws_manager.client_connected()

    # Join appropriate room based on authenticated user session
    is_admin, db_user_id, can_access_status = _resolve_status_scope()
    sid = _get_request_sid()
    if sid is None:
        logger.warning("Socket.IO connect event missing sid")
        return
    ws_manager.join_user_room(sid, is_admin=is_admin, db_user_id=db_user_id)

    # Send initial status to the newly connected client (filtered)
    try:
        if not can_access_status:
            emit("status_update", {})
            return

        user_id = None if is_admin else db_user_id
        status = backend.queue_status(user_id=user_id)
        emit("status_update", status if is_admin else redact_status(status))
    except _OPERATIONAL_ERRORS:
        logger.exception("Error sending initial status")


@socketio.on("disconnect")
def handle_disconnect() -> None:
    """Handle client disconnection."""
    logger.info("WebSocket client disconnected")

    # Leave room
    sid = _get_request_sid()
    if sid is not None:
        ws_manager.leave_user_room(sid)

    # Track the disconnection
    ws_manager.client_disconnected()


@socketio.on("request_status")
def handle_status_request() -> None:
    """Handle manual status request from client."""
    try:
        is_admin, db_user_id, can_access_status = _resolve_status_scope()
        sid = _get_request_sid()
        if sid is None:
            logger.warning("Socket.IO request_status event missing sid")
            emit("status_update", {})
            return
        ws_manager.sync_user_room(sid, is_admin=is_admin, db_user_id=db_user_id)

        if not can_access_status:
            emit("status_update", {})
            return

        user_id = None if is_admin else db_user_id
        status = backend.queue_status(user_id=user_id)
        emit("status_update", status if is_admin else redact_status(status))
    except _OPERATIONAL_ERRORS:
        logger.exception("Error handling status request")
        emit("error", {"message": "Failed to get status"})


logger.log_resource_usage()

# Warn if config directory is not writable (settings won't persist)
if not _is_config_dir_writable():
    logger.warning(
        "Config directory %s is not writable. Settings will not persist. Mount a config volume to enable settings persistence (see docs for details).",
        CONFIG_DIR,
    )

if __name__ == "__main__":
    debug_enabled = _is_debug_enabled()
    logger.info(
        "Starting Flask application with WebSocket support on %s:%s (debug=%s)",
        FLASK_HOST,
        FLASK_PORT,
        debug_enabled,
    )
    socketio.run(
        app,
        host=FLASK_HOST,
        port=FLASK_PORT,
        debug=debug_enabled,
        allow_unsafe_werkzeug=True,  # For development only
    )
