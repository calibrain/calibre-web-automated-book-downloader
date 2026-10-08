"""Bootstrap environment variables. No local dependencies - import first."""

import json
import os
import shutil
import tempfile
from pathlib import Path

LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


def string_to_bool(s: str) -> bool:
    """Convert string to boolean."""
    return s.lower() in ["true", "yes", "1", "y"]


def _read_advanced_config(key: str) -> object | None:
    """Read a key from the advanced settings file (import-time safe)."""
    config_dir = Path(os.getenv("CONFIG_DIR", "/config"))
    config_file = config_dir / "plugins" / "advanced.json"

    if config_file.exists():
        try:
            with config_file.open() as f:
                config = json.load(f)
                if key in config:
                    return config[key]
        except json.JSONDecodeError, OSError:
            pass

    return None


def _read_debug_from_config() -> bool:
    """Read DEBUG from env var or config file (import-time safe)."""
    env_debug = os.environ.get("DEBUG")
    if env_debug is not None:
        return string_to_bool(env_debug)

    value = _read_advanced_config("DEBUG")
    if value is not None:
        return bool(value)

    return False


def normalize_log_level(raw: str | None) -> str:
    """Normalize a log level name, falling back to INFO when unrecognized."""
    if raw is None:
        return "INFO"

    normalized = raw.strip().upper()
    # "WARN" is a logging alias, but gunicorn only accepts "warning".
    if normalized == "WARN":
        normalized = "WARNING"

    if normalized not in LOG_LEVELS:
        return "INFO"

    return normalized


def _read_log_level_from_config(debug: bool) -> str:
    """Resolve the app log level from DEBUG, env var, or config file.

    DEBUG wins when enabled, mirroring how entrypoint.sh picks gunicorn's level.
    Otherwise LOG_LEVEL is read from the env var, then the settings file, and
    falls back to INFO when unset or unrecognized.
    """
    if debug:
        return "DEBUG"

    raw = os.environ.get("LOG_LEVEL")
    if raw is None:
        value = _read_advanced_config("LOG_LEVEL")
        raw = value if isinstance(value, str) else None

    return normalize_log_level(raw)


def _is_sqlite_file(path: Path) -> bool:
    """Check if a file is a valid SQLite database by reading magic bytes."""
    try:
        with path.open("rb") as f:
            header = f.read(16)
            return header[:16] == b"SQLite format 3\x00"
    except OSError, PermissionError:
        return False


def _resolve_cwa_db_path() -> Path | None:
    """Resolve CWA database path from env var or default location."""
    env_path = os.getenv("CWA_DB_PATH")
    if env_path:
        path = Path(env_path)
        if path.exists() and path.is_file() and _is_sqlite_file(path):
            return path

    # Check default mount path
    default_path = Path("/auth/app.db")
    if default_path.exists() and default_path.is_file() and _is_sqlite_file(default_path):
        return default_path

    return None


def _is_config_dir_writable() -> bool:
    """Check if the config directory exists and is writable."""
    try:
        if not CONFIG_DIR.exists() or not CONFIG_DIR.is_dir():
            return False
        test_file = CONFIG_DIR / ".write_test"
        test_file.touch()
        test_file.unlink()
    except OSError, PermissionError:
        return False
    else:
        return True


def is_covers_cache_enabled() -> bool:
    """Check if cover caching is enabled (requires setting + writable config dir)."""
    from shelfmark.core.config import config

    setting_enabled = config.get("COVERS_CACHE_ENABLED", True)
    if isinstance(setting_enabled, str):
        return string_to_bool(setting_enabled) and _is_config_dir_writable()
    return bool(setting_enabled) and _is_config_dir_writable()


# =============================================================================
# Bootstrap paths - needed before settings registry is available
# =============================================================================

CONFIG_DIR = Path(os.getenv("CONFIG_DIR", "/config"))
LOG_ROOT = Path(os.getenv("LOG_ROOT", "/var/log/"))
LOG_DIR = LOG_ROOT / "shelfmark"
LOG_FILE = LOG_DIR / "shelfmark.log"
TMP_DIR = Path(os.getenv("TMP_DIR", (Path(tempfile.gettempdir()) / "shelfmark").as_posix()))
INGEST_DIR = Path(os.getenv("INGEST_DIR", "/books"))


# =============================================================================
# Logger configuration - needed before settings registry is available
# =============================================================================

DEBUG = _read_debug_from_config()
LOG_LEVEL = _read_log_level_from_config(DEBUG)
ENABLE_LOGGING = string_to_bool(os.getenv("ENABLE_LOGGING", "true"))


# =============================================================================
# Flask configuration - needed before app starts
# =============================================================================

FLASK_HOST = os.getenv("FLASK_HOST", "0.0.0.0")
FLASK_PORT = int(os.getenv("FLASK_PORT", "8084"))


# =============================================================================
# Authentication
# =============================================================================

SESSION_COOKIE_SECURE_ENV = os.getenv("SESSION_COOKIE_SECURE", "false")
SESSION_COOKIE_NAME = "shelfmark_session"
CWA_DB_PATH = _resolve_cwa_db_path()
HIDE_LOCAL_AUTH = string_to_bool(os.getenv("HIDE_LOCAL_AUTH", "false"))
DISABLE_LOCAL_AUTH = string_to_bool(os.getenv("DISABLE_LOCAL_AUTH", "false"))
# Optional static API key. When set, requests carrying it as a Bearer token
# (or X-Api-Key) are authenticated as an admin for that request only.
SHELFMARK_API_KEY = os.getenv("SHELFMARK_API_KEY", "").strip()
# Optional second key that can only read /api/stats (dashboards). Never an admin.
SHELFMARK_API_KEY_READONLY = os.getenv("SHELFMARK_API_KEY_READONLY", "").strip()
OIDC_AUTO_REDIRECT = string_to_bool(os.getenv("OIDC_AUTO_REDIRECT", "false"))


# =============================================================================
# Version information from Docker build
# =============================================================================

BUILD_VERSION = os.getenv("BUILD_VERSION", "N/A")
RELEASE_VERSION = os.getenv("RELEASE_VERSION", "N/A")


# =============================================================================
# Capability detection - runtime checks, not user-configurable
# =============================================================================

DOCKERMODE = string_to_bool(os.getenv("DOCKERMODE", "false"))
TOR_VARIANT_AVAILABLE = shutil.which("tor") is not None
USING_TOR = string_to_bool(os.getenv("USING_TOR", "false"))


# =============================================================================
# Onboarding
# =============================================================================

# Set to false to skip the onboarding wizard entirely (useful for ephemeral storage)
ONBOARDING = string_to_bool(os.getenv("ONBOARDING", "true"))


# =============================================================================
# Debug/development settings
# =============================================================================

# Debug: skip specific download sources for testing fallback chains
# Comma-separated values: aa-fast, aa-slow-nowait, aa-slow-wait, libgen, zlib, welib
_DEBUG_SKIP_SOURCES_RAW = os.getenv("DEBUG_SKIP_SOURCES", "").strip().lower()
DEBUG_SKIP_SOURCES = {s.strip() for s in _DEBUG_SKIP_SOURCES_RAW.split(",") if s.strip()}

# Debug: keep DDoS-Guard's __ddg8_/__ddg9_/__ddg10_ in the clearance store instead of
# dropping them after a solve.
#
# Which of DDoS-Guard's cookies actually *are* clearance is not settled. The store treats
# the trio as describing one check (client IP, timestamp, token) and drops them, on the
# reasoning that replaying a stale IP/timestamp is what re-arms the ?check=1 loop - see
# shelfmark.bypass.cookie_store. Field reports on issue #1276 point the other way: every
# request after a successful solve was challenged again, which is only consistent with
# what the store keeps not being sufficient clearance on its own.
#
# Deliberately env-only and off by default: this is a knob for reproducing the question
# against a live host, not a setting to offer users. Set it to true, solve once, and watch
# whether the next search still logs "Redirect loop detected".
DDG_REPLAY_PER_CHECK_COOKIES = string_to_bool(os.getenv("DDG_REPLAY_PER_CHECK_COOKIES", "false"))


# =============================================================================
# Legacy migration support - will be removed in future version
# =============================================================================

# Legacy welib settings - replaced by SOURCE_PRIORITY OrderableListField
# Kept for migration: if set, used to build initial SOURCE_PRIORITY config
_LEGACY_PRIORITIZE_WELIB = string_to_bool(os.getenv("PRIORITIZE_WELIB", "false"))
_LEGACY_ALLOW_USE_WELIB = string_to_bool(os.getenv("ALLOW_USE_WELIB", "true"))
