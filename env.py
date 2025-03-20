import os
from pathlib import Path

def string_to_bool(s: str) -> bool:
    return s.lower() in ["true", "yes", "1", "y"]

LOG_DIR = Path("/var/log/cwa-book-downloader")
TMP_DIR = Path(os.getenv("TMP_DIR", "/tmp/cwa-book-downloader"))
INGEST_DIR = Path(os.getenv("INGEST_DIR", "/cwa-book-ingest"))
STATUS_TIMEOUT = int(os.getenv("STATUS_TIMEOUT", "3600"))
USE_BOOK_TITLE = string_to_bool(os.getenv("USE_BOOK_TITLE", "false"))
MAX_RETRY = int(os.getenv("MAX_RETRY", "3"))
DEFAULT_SLEEP = int(os.getenv("DEFAULT_SLEEP", "5"))
USE_CF_BYPASS = string_to_bool(os.getenv("USE_CF_BYPASS", "true"))
HTTP_PROXY = os.getenv("HTTP_PROXY", "").strip()
HTTPS_PROXY = os.getenv("HTTPS_PROXY", "").strip()
AA_DONATOR_KEY = os.getenv("AA_DONATOR_KEY", "").strip()
_AA_BASE_URL = os.getenv("AA_BASE_URL", "auto").strip()
AA_ADDITIONAL_URLS = os.getenv("AA_ADITIINAL_URLS", "")
_SUPPORTED_FORMATS = os.getenv("SUPPORTED_FORMATS", "epub,mobi,azw3,fb2,djvu,cbz,cbr").lower()
_BOOK_LANGUAGE = os.getenv("BOOK_LANGUAGE", "en").lower()
_CUSTOM_SCRIPT = os.getenv("CUSTOM_SCRIPT", "").strip()
FLASK_HOST = os.getenv("FLASK_HOST", "0.0.0.0")
FLASK_PORT = int(os.getenv("FLASK_PORT", "5003"))
FLASK_DEBUG = string_to_bool(os.getenv("FLASK_DEBUG", "False"))
ENABLE_LOGGING = string_to_bool(os.getenv("ENABLE_LOGGING", "true"))
MAIN_LOOP_SLEEP_TIME = int(os.getenv("MAIN_LOOP_SLEEP_TIME", "5"))
DOCKERMODE = string_to_bool(os.getenv("DOCKERMODE", "false"))

# Logging settings
LOG_FILE = LOG_DIR / "cwa-bookd-downloader.log"