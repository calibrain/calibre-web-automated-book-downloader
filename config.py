"""Configuration settings for the book downloader application."""

import os
from pathlib import Path
import json
import env
from logger import setup_logger

logger = setup_logger(__name__)

with open("data/book-languages.json") as file:
    _SUPPORTED_BOOK_LANGUAGE = json.load(file)

# Directory settings
BASE_DIR = Path(__file__).resolve().parent
logger.info(f"BASE_DIR: {BASE_DIR}")
env.LOG_DIR.mkdir(exist_ok=True)

# Create necessary directories
env.TMP_DIR.mkdir(exist_ok=True)
env.INGEST_DIR.mkdir(exist_ok=True)

CROSS_FILE_SYSTEM = os.stat(env.TMP_DIR).st_dev != os.stat(env.INGEST_DIR).st_dev
logger.info(f"STAT TMP_DIR: {os.stat(env.TMP_DIR)}")
logger.info(f"STAT INGEST_DIR: {os.stat(env.INGEST_DIR)}")
logger.info(f"CROSS_FILE_SYSTEM: {CROSS_FILE_SYSTEM}")

# Network settings

# Proxy settings
PROXIES = {}
if env.HTTP_PROXY:
    PROXIES["http"] = env.HTTP_PROXY
if env.HTTPS_PROXY:
    PROXIES["https"] = env.HTTPS_PROXY
logger.info(f"PROXIES: {PROXIES}")

# Anna's Archive settings
aa_available_urls = ["https://annas-archive.org", "https://annas-archive.se", "https://annas-archive.li"]
aa_additional_urls = env.AA_ADDITIONAL_URLS.split(",")
aa_available_urls.extend(aa_additional_urls)

AA_BASE_URL = env._AA_BASE_URL
if AA_BASE_URL == "auto":
    logger.info(f"AA_BASE_URL: auto, checking available urls {aa_available_urls}")
    for url in aa_available_urls:
        try:
            import requests
            response = requests.get(url)
            if response.status_code == 200:
                AA_BASE_URL = url
                break
        except Exception as e:
            logger.error(f"Error checking {url}: {e}")
    if AA_BASE_URL == "auto":
        AA_BASE_URL = aa_available_urls[0]
logger.info(f"AA_BASE_URL: {AA_BASE_URL}")

# File format settings
SUPPORTED_FORMATS = env._SUPPORTED_FORMATS.split(",")
logger.info(f"SUPPORTED_FORMATS: {SUPPORTED_FORMATS}")

# Complex language processing logic kept in config.py
BOOK_LANGUAGE = env._BOOK_LANGUAGE.split(',')
BOOK_LANGUAGE = [l for l in BOOK_LANGUAGE if l in [lang['code'] for lang in _SUPPORTED_BOOK_LANGUAGE]]
if len(BOOK_LANGUAGE) == 0:
    BOOK_LANGUAGE = ['en']

# Custom script settings with validation logic
CUSTOM_SCRIPT = env._CUSTOM_SCRIPT
if CUSTOM_SCRIPT:
    if not os.path.exists(CUSTOM_SCRIPT):
        logger.error(f"CUSTOM_SCRIPT {CUSTOM_SCRIPT} does not exist")
        CUSTOM_SCRIPT = ""
    elif not os.access(CUSTOM_SCRIPT, os.X_OK):
        logger.error(f"CUSTOM_SCRIPT {CUSTOM_SCRIPT} is not executable")
        CUSTOM_SCRIPT = ""

# Docker settings
if env.DOCKERMODE and env.USE_CF_BYPASS:
    from pyvirtualdisplay import Display
    display = Display(visible=False, size=(800, 600))
    display.start()