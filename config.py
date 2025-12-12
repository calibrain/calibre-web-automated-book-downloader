"""Configuration settings for the book downloader application."""

import os
from pathlib import Path
import json
import env
from logger import setup_logger

logger = setup_logger(__name__)

for key, value in env.__dict__.items():
    if not key.startswith('_'):
        if key == "AA_DONATOR_KEY" and value.strip() != "":
            value = "REDACTED"
        logger.info(f"{key}: {value}")

with open("data/book-languages.json") as file:
    _SUPPORTED_BOOK_LANGUAGE = json.load(file)

# Directory settings
BASE_DIR = Path(__file__).resolve().parent
logger.info(f"BASE_DIR: {BASE_DIR}")
if env.ENABLE_LOGGING:
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
_doh_server = ""
_custom_dns_list = None  # For auto mode

if _custom_dns == "auto":
    # Auto mode - will be handled by DNS auto manager in network.py
    # Initialize with first provider (Cloudflare) for now
    CUSTOM_DNS = ["1.1.1.1", "1.0.0.1", "2606:4700:4700:0000:0000:0000:0000:1111", "2606:4700:4700:0000:0000:0000:0000:1001"]
    _doh_server = "https://cloudflare-dns.com/dns-query"
    _custom_dns_list = None  # No custom DNS in auto mode
elif _custom_dns == "google":
    CUSTOM_DNS = ["8.8.8.8", "8.8.4.4", "2001:4860:4860:0000:0000:0000:0000:8888", "2001:4860:4860:0000:0000:0000:0000:8844"]
    _doh_server = "https://dns.google/dns-query"
elif _custom_dns == "quad9":
    CUSTOM_DNS = ["9.9.9.9", "149.112.112.112", "2620:00fe:0000:0000:0000:0000:0000:00fe", "2620:00fe:0000:0000:0000:0000:0000:0009"]
    _doh_server = "https://dns.quad9.net/dns-query"
elif _custom_dns == "cloudflare":
    CUSTOM_DNS = ["1.1.1.1", "1.0.0.1", "2606:4700:4700:0000:0000:0000:0000:1111", "2606:4700:4700:0000:0000:0000:0000:1001"]
    _doh_server = "https://cloudflare-dns.com/dns-query"
elif _custom_dns == "opendns":
    CUSTOM_DNS = ["208.67.222.222", "208.67.220.220", "2620:0119:0035:0000:0000:0000:0000:0035", "2620:0119:0053:0000:0000:0000:0000:0053"]
    _doh_server = "https://doh.opendns.com/dns-query"
else:
    # Custom DNS IPs
    _custom_dns_ip = _custom_dns.split(",")
    CUSTOM_DNS = [dns.strip() for dns in _custom_dns_ip if dns.replace(":", "").replace(".", "").strip().isdigit()]
    _custom_dns_list = CUSTOM_DNS.copy()  # Store for auto mode if needed

logger.info(f"CUSTOM_DNS: {CUSTOM_DNS}")
DOH_SERVER = _doh_server
if env.USE_DOH:
    DOH_SERVER = _doh_server
else:
    DOH_SERVER = ""
logger.info(f"DOH_SERVER: {DOH_SERVER}")

# Warn about external bypasser DNS limitations
if env.USING_EXTERNAL_BYPASSER and env.USE_CF_BYPASS:
    logger.warning(
        "Using external bypasser (FlareSolverr). Note: FlareSolverr uses its own DNS resolution, "
        "not this application's custom DNS settings. If you experience DNS-related blocks, "
        "configure DNS at the Docker/system level for your FlareSolverr container, "
        "or consider using the internal bypasser which integrates with the app's DNS system."
    )

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

# Debugging settings
if not env.USING_EXTERNAL_BYPASSER:
    # Virtual display settings for debugging internal cloudflare bypasser
    VIRTUAL_SCREEN_SIZE = (1024, 768)
    RECORDING_DIR = env.LOG_DIR / "recording"
    if env.DEBUG:
        RECORDING_DIR.mkdir(parents=True, exist_ok=True)
