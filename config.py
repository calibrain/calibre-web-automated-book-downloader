"""Configuration settings for the book downloader application."""

import os
from pathlib import Path

_SUPPORTED_BOOK_LANGUAGE = ['en','zh','ru','es','fr','de','it','pt','pl','bg','nl','ja','ar','he','hu','la','cs','ko','tr','uk','id','ro','el','lt','bn','zh‑Hant','af','ca','sv','th','hi','ga','lv','kn','sr','bo','da','fa','hr','sk','jv','vi','ur','fi','no','rw','ta','be','kk','mn','ka','sl','eo','gl','mr','fil','gu','ml','ky','qu','az','sw','ba','pa','ms','te','sq','ug','hy','shn']

# Directory settings
BASE_DIR = Path(__file__).resolve().parent
LOG_DIR =  "/var/logs"
LOG_DIR = Path(LOG_DIR)

TMP_DIR = os.getenv("TMP_DIR", "/tmp/cwa-book-downloader")
TMP_DIR = Path(TMP_DIR)

INGEST_DIR = os.getenv("INGEST_DIR", "/cwa-book-ingest")
INGEST_DIR = Path(INGEST_DIR)
STATUS_TIMEOUT = int(os.getenv("STATUS_TIMEOUT", 3600))

# Create necessary directories
TMP_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)
INGEST_DIR.mkdir(exist_ok=True)

# Network settings
MAX_RETRY = int(os.getenv("MAX_RETRY", 3))
DEFAULT_SLEEP = int(os.getenv("DEFAULT_SLEEP", 5))
CLOUDFLARE_PROXY = os.getenv("CLOUDFLARE_PROXY_URL", "http://localhost:8000")
AA_DONATOR_KEY = os.getenv("AA_DONATOR_KEY", None)

# File format settings
SUPPORTED_FORMATS = os.getenv("SUPPORTED_FORMATS", "epub,mobi,azw3,fb2,djvu,cbz,cbr")
SUPPORTED_FORMATS = SUPPORTED_FORMATS.split(",")

BOOK_LANGUAGE = os.getenv("BOOK_LANGUAGE", "en")
BOOK_LANGUAGE = BOOK_LANGUAGE.lower().split(',')
BOOK_LANGUAGE = [l for l in BOOK_LANGUAGE if l in _SUPPORTED_BOOK_LANGUAGE]
if len(BOOK_LANGUAGE) == 0:
    BOOK_LANGUAGE = ['en']

# API settings
FLASK_HOST = os.getenv("FLASK_HOST",  "0.0.0.0")
FLASK_PORT = int(os.getenv("FLASK_PORT", 5003))
FLASK_DEBUG = os.getenv("FLASK_DEBUG", "False").lower() == "true"

# Logging settings
LOG_FILE = f"{LOG_DIR}/cwa-bookd-ownloader.log"
MAIN_LOOP_SLEEP_TIME = int(os.getenv("MAIN_LOOP_SLEEP_TIME", 5))
