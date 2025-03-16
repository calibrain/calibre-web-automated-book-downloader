"""Configuration settings for the book downloader application."""

import os
from pathlib import Path
import json


with open("data/book-languages.json") as file:
    _SUPPORTED_BOOK_LANGUAGE = json.load(file)

# Directory settings
BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = Path("/var/log/cwa-book-downloader")
LOG_DIR.mkdir(exist_ok=True)

TMP_DIR = Path(os.getenv("TMP_DIR", "/tmp/cwa-book-downloader"))

INGEST_DIR = Path(os.getenv("INGEST_DIR", "/cwa-book-ingest"))
STATUS_TIMEOUT = int(os.getenv("STATUS_TIMEOUT", 3600))

# Create necessary directories
TMP_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)
INGEST_DIR.mkdir(exist_ok=True)

# Network settings
MAX_RETRY = int(os.getenv("MAX_RETRY", 3))
DEFAULT_SLEEP = int(os.getenv("DEFAULT_SLEEP", 5))
USE_CF_BYPASS = os.getenv("USE_CF_BYPASS", "true").lower() in ["true", "yes", "1", "y"]

# Proxy settings
PROXIES = {}
http_proxy = os.getenv("HTTP_PROXY", "").strip()
https_proxy = os.getenv("HTTPS_PROXY", "").strip()
if http_proxy:
    PROXIES["http"] = http_proxy
if https_proxy:
    PROXIES["https"] = https_proxy
if not PROXIES:
    PROXIES = {}

# Anna's Archive settings
aa_available_urls = ["https://annas-archive.org", "https://annas-archive.se", "https://annas-archive.li"]
AA_DONATOR_KEY = os.getenv("AA_DONATOR_KEY", "").strip()
AA_BASE_URL = os.getenv("AA_BASE_URL", "auto").strip("/")
if AA_BASE_URL == "auto":
    for url in aa_available_urls:
        try:
            import requests
            response = requests.get(url)
            if response.status_code == 200:
                AA_BASE_URL = url
                break
        except Exception as e:
            print(f"Error checking {url}: {e}")
if AA_BASE_URL == "auto":
    AA_BASE_URL = aa_available_urls[0]

# File format settings
SUPPORTED_FORMATS = os.getenv("SUPPORTED_FORMATS", "epub,mobi,azw3,fb2,djvu,cbz,cbr").split(",")

BOOK_LANGUAGE = os.getenv("BOOK_LANGUAGE", "en").lower().split(',')
BOOK_LANGUAGE = [l for l in BOOK_LANGUAGE if l in [lang['code'] for lang in _SUPPORTED_BOOK_LANGUAGE]]
if len(BOOK_LANGUAGE) == 0:
    BOOK_LANGUAGE = ['en']

# Custom script settings
CUSTOM_SCRIPT = os.getenv("CUSTOM_SCRIPT", "").strip()
# check if the script is valid
if CUSTOM_SCRIPT:
    if not os.path.exists(CUSTOM_SCRIPT):
        CUSTOM_SCRIPT = ""
    elif not os.access(CUSTOM_SCRIPT, os.X_OK):
        CUSTOM_SCRIPT = ""

# API settings
FLASK_HOST = os.getenv("FLASK_HOST",  "0.0.0.0")
FLASK_PORT = int(os.getenv("FLASK_PORT", 5003))
FLASK_DEBUG = os.getenv("FLASK_DEBUG", "False").lower() == "true"

# Logging settings
ENABLE_LOGGING = os.getenv("ENABLE_LOGGING", "true").lower() in ["true", "yes", "1", "y"]
LOG_FILE = LOG_DIR / "cwa-bookd-downloader.log"
MAIN_LOOP_SLEEP_TIME = int(os.getenv("MAIN_LOOP_SLEEP_TIME", 5))

# Docker settings
DOCKERMODE = os.getenv('DOCKERMODE', 'false').lower().strip() in ['true', '1', 'yes', 'y']
if DOCKERMODE:
    from pyvirtualdisplay import Display
    display = Display(visible=False, size=(800, 600))
    display.start()