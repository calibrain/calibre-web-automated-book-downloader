import os
from pathlib import Path

def string_to_bool(s: str) -> bool:
    return s.lower() in ["true", "yes", "1", "y"]

# Authentication and session settings
SESSION_COOKIE_SECURE_ENV = os.getenv("SESSION_COOKIE_SECURE", "false")

CWA_DB = os.getenv("CWA_DB_PATH")
CWA_DB_PATH = Path(CWA_DB) if CWA_DB else None
LOG_ROOT = Path(os.getenv("LOG_ROOT", "/var/log/"))
LOG_DIR = LOG_ROOT / "cwa-book-downloader"
TMP_DIR = Path(os.getenv("TMP_DIR", "/tmp/cwa-book-downloader"))
INGEST_DIR = Path(os.getenv("INGEST_DIR", "/cwa-book-ingest"))
INGEST_DIR_BOOK_FICTION = os.getenv("INGEST_DIR_BOOK_FICTION", "")
INGEST_DIR_BOOK_NON_FICTION = os.getenv("INGEST_DIR_BOOK_NON_FICTION", "")
INGEST_DIR_BOOK_UNKNOWN = os.getenv("INGEST_DIR_BOOK_UNKNOWN", "")
INGEST_DIR_MAGAZINE = os.getenv("INGEST_DIR_MAGAZINE", "")
INGEST_DIR_COMIC_BOOK = os.getenv("INGEST_DIR_COMIC_BOOK", "")
INGEST_DIR_AUDIOBOOK = os.getenv("INGEST_DIR_AUDIOBOOK", "")
INGEST_DIR_STANDARDS_DOCUMENT = os.getenv("INGEST_DIR_STANDARDS_DOCUMENT", "")
INGEST_DIR_MUSICAL_SCORE = os.getenv("INGEST_DIR_MUSICAL_SCORE", "")
INGEST_DIR_OTHER = os.getenv("INGEST_DIR_OTHER", "")
DOWNLOAD_PATHS = {
    "book (fiction)": Path(INGEST_DIR_BOOK_FICTION) if INGEST_DIR_BOOK_FICTION else INGEST_DIR,
    "book (non-fiction)": Path(INGEST_DIR_BOOK_NON_FICTION) if INGEST_DIR_BOOK_NON_FICTION else INGEST_DIR,
    "book (unknown)": Path(INGEST_DIR_BOOK_UNKNOWN) if INGEST_DIR_BOOK_UNKNOWN else INGEST_DIR,
    "magazine": Path(INGEST_DIR_MAGAZINE) if INGEST_DIR_MAGAZINE else INGEST_DIR,
    "comic book": Path(INGEST_DIR_COMIC_BOOK) if INGEST_DIR_COMIC_BOOK else INGEST_DIR,
    "audiobook": Path(INGEST_DIR_AUDIOBOOK) if INGEST_DIR_AUDIOBOOK else INGEST_DIR,
    "standards document": Path(INGEST_DIR_STANDARDS_DOCUMENT) if INGEST_DIR_STANDARDS_DOCUMENT else INGEST_DIR,
    "musical score": Path(INGEST_DIR_MUSICAL_SCORE) if INGEST_DIR_MUSICAL_SCORE else INGEST_DIR,
    "other": Path(INGEST_DIR_OTHER) if INGEST_DIR_OTHER else INGEST_DIR,
}

STATUS_TIMEOUT = int(os.getenv("STATUS_TIMEOUT", "3600"))
USE_BOOK_TITLE = string_to_bool(os.getenv("USE_BOOK_TITLE", "false"))
MAX_RETRY = int(os.getenv("MAX_RETRY", "10"))
DEFAULT_SLEEP = int(os.getenv("DEFAULT_SLEEP", "5"))
USE_CF_BYPASS = string_to_bool(os.getenv("USE_CF_BYPASS", "true"))
HTTP_PROXY = os.getenv("HTTP_PROXY", "").strip()
HTTPS_PROXY = os.getenv("HTTPS_PROXY", "").strip()
AA_DONATOR_KEY = os.getenv("AA_DONATOR_KEY", "").strip()
_AA_BASE_URL = os.getenv("AA_BASE_URL", "auto").strip()
_AA_ADDITIONAL_URLS = os.getenv("AA_ADDITIONAL_URLS", "").strip()
_SUPPORTED_FORMATS = os.getenv("SUPPORTED_FORMATS", "epub,mobi,azw3,fb2,djvu,cbz,cbr").lower()
_BOOK_LANGUAGE = os.getenv("BOOK_LANGUAGE", "en").lower()
_CUSTOM_SCRIPT = os.getenv("CUSTOM_SCRIPT", "").strip()
FLASK_HOST = os.getenv("FLASK_HOST", "0.0.0.0")
FLASK_PORT = int(os.getenv("FLASK_PORT", "8084"))
DEBUG = string_to_bool(os.getenv("DEBUG", "false"))
PRIORITIZE_WELIB = string_to_bool(os.getenv("PRIORITIZE_WELIB", "false"))
ALLOW_USE_WELIB = string_to_bool(os.getenv("ALLOW_USE_WELIB", "true"))

# Version information from Docker build
BUILD_VERSION = os.getenv("BUILD_VERSION", "N/A")
RELEASE_VERSION = os.getenv("RELEASE_VERSION", "N/A")

# If debug is true, we want to log everything
if DEBUG:
    LOG_LEVEL = "DEBUG"
else:
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
ENABLE_LOGGING = string_to_bool(os.getenv("ENABLE_LOGGING", "true"))
MAIN_LOOP_SLEEP_TIME = int(os.getenv("MAIN_LOOP_SLEEP_TIME", "5"))
MAX_CONCURRENT_DOWNLOADS = int(os.getenv("MAX_CONCURRENT_DOWNLOADS", "3"))
DOWNLOAD_PROGRESS_UPDATE_INTERVAL = int(os.getenv("DOWNLOAD_PROGRESS_UPDATE_INTERVAL", "5"))
DOCKERMODE = string_to_bool(os.getenv("DOCKERMODE", "false"))
_CUSTOM_DNS = os.getenv("CUSTOM_DNS", "").strip()
USE_DOH = string_to_bool(os.getenv("USE_DOH", "false"))
BYPASS_RELEASE_INACTIVE_MIN = int(os.getenv("BYPASS_RELEASE_INACTIVE_MIN", "5"))

# Logging settings
LOG_FILE = LOG_DIR / "cwa-book-downloader.log"

USING_EXTERNAL_BYPASSER = string_to_bool(os.getenv("USING_EXTERNAL_BYPASSER", "false"))
if USING_EXTERNAL_BYPASSER:
    EXT_BYPASSER_URL = os.getenv("EXT_BYPASSER_URL", "http://flaresolverr:8191").strip()
    EXT_BYPASSER_PATH = os.getenv("EXT_BYPASSER_PATH", "/v1").strip()
    EXT_BYPASSER_TIMEOUT = int(os.getenv("EXT_BYPASSER_TIMEOUT", "60000"))

USING_TOR = string_to_bool(os.getenv("USING_TOR", "false"))
# If using Tor, we don't need to set custom DNS, use DOH, or proxy
if USING_TOR:
    _CUSTOM_DNS = ""
    USE_DOH = False
    HTTP_PROXY = ""
    HTTPS_PROXY = ""

# Calibre-Web URL for navigation button
CALIBRE_WEB_URL = os.getenv("CALIBRE_WEB_URL", "").strip()
    