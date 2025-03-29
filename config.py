"""Configuration settings for the book downloader application."""

import os
from pathlib import Path
import json
import env
from logger import setup_logger

logger = setup_logger(__name__)

for key, value in env.__dict__.items():
    if not key.startswith('_'):
        logger.info(f"{key}: {value}")

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
_custom_dns = env._CUSTOM_DNS.lower().strip()
if _custom_dns == "google":
    CUSTOM_DNS = ["8.8.8.8", "8.8.4.4", "2001:4860:4860::8888", "2001:4860:4860::8844"]
elif _custom_dns == "quad9":
    CUSTOM_DNS = ["9.9.9.9", "149.112.112.112", "2620:fe::fe", "26620:fe::9"]
elif _custom_dns == "cloudflare":
    CUSTOM_DNS = ["1.1.1.1", "1.0.0.1", "2606:4700:4700::1111", "2606:4700:4700::1001"]
elif _custom_dns == "opendns":
    CUSTOM_DNS = ["208.67.222.222", "208.67.220.220", "2620:119:35::35", "2620:119:53::53"]
else:
    _custom_dns_ip = _custom_dns.split(",")
    CUSTOM_DNS = [dns.strip() for dns in _custom_dns_ip if dns.replace(":", "").replace(".", "").strip().isdigit()]
logger.info(f"CUSTOM_DNS: {CUSTOM_DNS}")

# Proxy settings
PROXIES = {}
if env.HTTP_PROXY:
    PROXIES["http"] = env.HTTP_PROXY
if env.HTTPS_PROXY:
    PROXIES["https"] = env.HTTPS_PROXY
logger.info(f"PROXIES: {PROXIES}")

# Anna's Archive settings
AA_BASE_URL = env._AA_BASE_URL
AA_AVAILABLE_URLS = ["https://annas-archive.org", "https://annas-archive.se", "https://annas-archive.li"]
AA_AVAILABLE_URLS.extend(env._AA_ADDITIONAL_URLS.split(","))
AA_AVAILABLE_URLS = [url.strip() for url in AA_AVAILABLE_URLS if url.strip()]

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
        logger.warn(f"CUSTOM_SCRIPT {CUSTOM_SCRIPT} does not exist")
        CUSTOM_SCRIPT = ""
    elif not os.access(CUSTOM_SCRIPT, os.X_OK):
        logger.warn(f"CUSTOM_SCRIPT {CUSTOM_SCRIPT} is not executable")
        CUSTOM_SCRIPT = ""

# Docker settings
if env.DOCKERMODE and env.USE_CF_BYPASS:
    from pyvirtualdisplay import Display
    display = Display(visible=False, size=(800, 600))
    display.start()
    logger.info("Display started")
