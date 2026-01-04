"""Bootstrap environment variables. No local dependencies - import first."""

import json
import os
import shutil
from pathlib import Path


def string_to_bool(s: str) -> bool:
    """Convert string to boolean."""
    return s.lower() in ["true", "yes", "1", "y"]


def _read_debug_from_config() -> bool:
    """Read DEBUG from env var or config file (import-time safe)."""
    env_debug = os.environ.get("DEBUG")
    if env_debug is not None:
        return string_to_bool(env_debug)

    # Try to read from config file
    config_dir = Path(os.getenv("CONFIG_DIR", "/config"))
    config_file = config_dir / "plugins" / "advanced.json"

    if config_file.exists():
        try:
            with open(config_file, "r") as f:
                config = json.load(f)
                if "DEBUG" in config:
                    return bool(config["DEBUG"])
        except (json.JSONDecodeError, OSError):
            pass

    return False


def _is_sqlite_file(path: Path) -> bool:
    """Check if a file is a valid SQLite database by reading magic bytes."""
    try:
        with open(path, "rb") as f:
            header = f.read(16)
            return header[:16] == b"SQLite format 3\x00"
    except (OSError, PermissionError):
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
        return True
    except (OSError, PermissionError):
        return False


def is_covers_cache_enabled() -> bool:
    """Check if cover caching is enabled (requires setting + writable config dir)."""
    from cwa_book_downloader.core.config import config
    setting_enabled = config.get("COVERS_CACHE_ENABLED", True)
    return setting_enabled and _is_config_dir_writable()


# =============================================================================
# Bootstrap paths - needed before settings registry is available
# =============================================================================

CONFIG_DIR = Path(os.getenv("CONFIG_DIR", "/config"))
LOG_ROOT = Path(os.getenv("LOG_ROOT", "/var/log/"))
LOG_DIR = LOG_ROOT / "cwa-book-downloader"
LOG_FILE = LOG_DIR / "cwa-book-downloader.log"
TMP_DIR = Path(os.getenv("TMP_DIR", "/tmp/cwa-book-downloader"))
INGEST_DIR = Path(os.getenv("INGEST_DIR", "/cwa-book-ingest"))


# =============================================================================
# Logger configuration - needed before settings registry is available
# =============================================================================

DEBUG = _read_debug_from_config()
LOG_LEVEL = "DEBUG" if DEBUG else "INFO"
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
CWA_DB_PATH = _resolve_cwa_db_path()


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
# Debug/development settings
# =============================================================================

# Debug: skip specific download sources for testing fallback chains
# Comma-separated values: aa-fast, aa-slow-nowait, aa-slow-wait, libgen, zlib, welib
_DEBUG_SKIP_SOURCES_RAW = os.getenv("DEBUG_SKIP_SOURCES", "").strip().lower()
DEBUG_SKIP_SOURCES = set(s.strip() for s in _DEBUG_SKIP_SOURCES_RAW.split(",") if s.strip())


# =============================================================================
# Legacy migration support - will be removed in future version
# =============================================================================

# Legacy welib settings - replaced by SOURCE_PRIORITY OrderableListField
# Kept for migration: if set, used to build initial SOURCE_PRIORITY config
_LEGACY_PRIORITIZE_WELIB = string_to_bool(os.getenv("PRIORITIZE_WELIB", "false"))
_LEGACY_ALLOW_USE_WELIB = string_to_bool(os.getenv("ALLOW_USE_WELIB", "true"))
