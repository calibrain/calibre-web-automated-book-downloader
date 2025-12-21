"""Core settings registration and derived configuration values."""

import os
from pathlib import Path
import json

from cwa_book_downloader.config import env
from cwa_book_downloader.core.logger import setup_logger

logger = setup_logger(__name__)

# Log configuration values at DEBUG level, filtering out module imports and functions
logger.debug("Environment configuration:")
for key, value in env.__dict__.items():
    # Skip private attributes, modules, types, and callables (functions)
    if key.startswith('_'):
        continue
    if isinstance(value, type) or callable(value):
        continue
    # Don't log module objects (they have __name__ attribute)
    if hasattr(value, '__name__') and hasattr(value, '__file__'):
        continue
    # Redact sensitive values
    if key == "AA_DONATOR_KEY" and isinstance(value, str) and value.strip():
        value = "REDACTED"
    if key == "HARDCOVER_API_KEY" and isinstance(value, str) and value.strip():
        value = "REDACTED"
    logger.debug(f"  {key}: {value}")

# Load supported book languages from data file
# Path is relative to the package root, not this file
_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
with open(_DATA_DIR / "book-languages.json") as file:
    _SUPPORTED_BOOK_LANGUAGE = json.load(file)

# Directory settings
BASE_DIR = Path(__file__).resolve().parent.parent.parent
logger.debug(f"BASE_DIR: {BASE_DIR}")
if env.ENABLE_LOGGING:
    env.LOG_DIR.mkdir(exist_ok=True)

# Create necessary directories
env.TMP_DIR.mkdir(exist_ok=True)
env.INGEST_DIR.mkdir(exist_ok=True)

CROSS_FILE_SYSTEM = os.stat(env.TMP_DIR).st_dev != os.stat(env.INGEST_DIR).st_dev
logger.debug(f"STAT TMP_DIR: {os.stat(env.TMP_DIR)}")
logger.debug(f"STAT INGEST_DIR: {os.stat(env.INGEST_DIR)}")
logger.debug(f"CROSS_FILE_SYSTEM: {CROSS_FILE_SYSTEM}")

# Network settings - DNS configuration is managed by network.py
# These are placeholder values that will be set when network.init() is called
# The authoritative DNS state lives in network.py and is configured via set_dns_provider()
# Actual DNS provider is determined from config singleton (settings UI) or ENV var
CUSTOM_DNS: list[str] = []
DOH_SERVER: str = ""

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
logger.debug(f"PROXIES: {PROXIES}")

# Anna's Archive settings
AA_BASE_URL = env._AA_BASE_URL
AA_AVAILABLE_URLS = ["https://annas-archive.org", "https://annas-archive.se", "https://annas-archive.li"]
AA_AVAILABLE_URLS.extend(env._AA_ADDITIONAL_URLS.split(","))
AA_AVAILABLE_URLS = [url.strip() for url in AA_AVAILABLE_URLS if url.strip()]

# File format settings
SUPPORTED_FORMATS = env._SUPPORTED_FORMATS.split(",")
logger.debug(f"SUPPORTED_FORMATS: {SUPPORTED_FORMATS}")

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


from cwa_book_downloader.core.settings_registry import (
    register_settings,
    register_group,
    TextField,
    PasswordField,
    NumberField,
    CheckboxField,
    SelectField,
    MultiSelectField,
    HeadingField,
    ActionButton,
)


register_group(
    "direct_download",
    "Anna's Archive",
    icon="download",
    order=20
)

register_group(
    "metadata_providers",
    "Metadata Providers",
    icon="book",
    order=12  # Between Network (10) and Advanced (15)
)


# Build format options from supported formats
_FORMAT_OPTIONS = [
    {"value": "epub", "label": "EPUB"},
    {"value": "mobi", "label": "MOBI"},
    {"value": "azw3", "label": "AZW3"},
    {"value": "pdf", "label": "PDF"},
    {"value": "fb2", "label": "FB2"},
    {"value": "djvu", "label": "DJVU"},
    {"value": "cbz", "label": "CBZ"},
    {"value": "cbr", "label": "CBR"},
    {"value": "txt", "label": "TXT"},
    {"value": "rtf", "label": "RTF"},
    {"value": "doc", "label": "DOC"},
    {"value": "docx", "label": "DOCX"},
]


def _get_metadata_provider_options():
    """Build metadata provider options dynamically from enabled providers only."""
    from cwa_book_downloader.metadata_providers import list_providers, is_provider_enabled

    options = []
    for provider in list_providers():
        # Only show providers that are enabled
        if is_provider_enabled(provider["name"]):
            options.append({"value": provider["name"], "label": provider["display_name"]})

    # If no providers enabled, show a placeholder option
    if not options:
        options = [
            {"value": "", "label": "No providers enabled"},
        ]

    return options


def _get_release_source_options():
    """Build release source options dynamically from registered sources."""
    from cwa_book_downloader.release_sources import list_available_sources

    return [
        {"value": source["name"], "label": source["display_name"]}
        for source in list_available_sources()
    ]

# Build language options from supported languages
_LANGUAGE_OPTIONS = [{"value": lang["code"], "label": lang["language"]} for lang in _SUPPORTED_BOOK_LANGUAGE]


def _clear_covers_cache(current_values: dict) -> dict:
    """Clear the cover image cache."""
    try:
        from cwa_book_downloader.core.image_cache import get_image_cache, reset_image_cache

        cache = get_image_cache()
        count = cache.clear()

        # Reset the singleton so it reinitializes with fresh state
        reset_image_cache()

        return {
            "success": True,
            "message": f"Cleared {count} cached cover images.",
        }
    except Exception as e:
        logger.error(f"Failed to clear cover cache: {e}")
        return {
            "success": False,
            "message": f"Failed to clear cache: {str(e)}",
        }


@register_settings("general", "General", icon="settings", order=0)
def general_settings():
    """Core application settings."""
    return [
        SelectField(
            key="SEARCH_MODE",
            label="Search Mode",
            description="How you want to search for and download books.",
            options=[
                {
                    "value": "direct",
                    "label": "Direct (Anna's Archive)",
                    "description": "Search Anna's Archive and download directly. Works out of the box.",
                },
                {
                    "value": "universal",
                    "label": "Universal",
                    "description": "Metadata-based search with downloads from all sources.",
                },
            ],
            default="direct",
        ),
        SelectField(
            key="METADATA_PROVIDER",
            label="Metadata Provider for Universal Search",
            description="Choose which metadata provider to use for book searches.",
            options=_get_metadata_provider_options,  # Callable - evaluated lazily to avoid circular imports
            default="openlibrary",
            show_when={"field": "SEARCH_MODE", "value": "universal"},
        ),
        SelectField(
            key="DEFAULT_RELEASE_SOURCE",
            label="Default Release Source",
            description="The release source tab to open by default in the release modal.",
            options=_get_release_source_options,  # Callable - evaluated lazily to avoid circular imports
            default="direct_download",
            env_supported=False,  # UI-only setting, not configurable via ENV
            show_when={"field": "SEARCH_MODE", "value": "universal"},
        ),
        MultiSelectField(
            key="SUPPORTED_FORMATS",
            label="Supported Formats",
            description="Book formats to include in search results.",
            options=_FORMAT_OPTIONS,
            default=["epub", "mobi", "azw3", "fb2", "djvu", "cbz", "cbr"],
        ),
        MultiSelectField(
            key="BOOK_LANGUAGE",
            label="Default Book Languages",
            description="Default language filter for searches. Can be overridden in advanced search options.",
            options=_LANGUAGE_OPTIONS,
            default=["en"],
        ),
        CheckboxField(
            key="USE_BOOK_TITLE",
            label="Use Book Title as Filename",
            description="Save files using book title instead of ID. May cause issues with special characters.",
            default=False,
        ),
        TextField(
            key="CALIBRE_WEB_URL",
            label="Book Management App URL",
            description="Adds a navigation button to your book manager instance (Calibre-Web Automated, Booklore, etc).",
            placeholder="http://calibre-web:8083",
        ),
        NumberField(
            key="MAX_CONCURRENT_DOWNLOADS",
            label="Max Concurrent Downloads",
            description="Maximum number of simultaneous downloads.",
            default=3,
            min_value=1,
            max_value=10,
            requires_restart=True,
        ),
        NumberField(
            key="STATUS_TIMEOUT",
            label="Status Timeout (seconds)",
            description="How long to keep completed/failed downloads in the queue display.",
            default=3600,
            min_value=60,
            max_value=86400,
        ),
    ]


@register_settings("network", "Network", icon="globe", order=10)
def network_settings():
    """Network and connectivity settings."""
    # Check if Tor variant is available and if Tor is currently enabled
    tor_available = env.TOR_VARIANT_AVAILABLE
    tor_enabled = env.USING_TOR

    # When Tor is enabled (only possible in Tor variant), DNS/proxy settings are overridden
    # The Tor variant uses iptables to force ALL traffic through Tor - it cannot be disabled
    tor_overrides_network = tor_available  # If Tor variant, network settings are always managed by Tor

    return [
        CheckboxField(
            key="USING_TOR",
            label="Tor Routing",
            description=(
                "All traffic is routed through Tor in this container variant. This cannot be changed."
                if tor_available
                else "Tor routing is not available in this container variant."
            ),
            default=tor_available,  # Reflects actual state: True if Tor variant, False otherwise
            disabled=True,  # Always disabled - Tor state is determined by container variant
            disabled_reason=(
                "Tor routing is always active in the Tor container variant."
                if tor_available
                else "Requires the Tor container variant (calibre-web-automated-book-downloader-tor)."
            ),
        ),
        SelectField(
            key="CUSTOM_DNS",
            label="DNS Provider",
            description=(
                "Managed by Tor when Tor routing is enabled."
                if tor_overrides_network
                else "DNS provider for domain resolution. 'Auto' rotates through providers on failure."
            ),
            options=[
                {"value": "auto", "label": "Auto (Recommended)"},
                {"value": "system", "label": "System"},
                {"value": "google", "label": "Google"},
                {"value": "cloudflare", "label": "Cloudflare"},
                {"value": "quad9", "label": "Quad9"},
                {"value": "opendns", "label": "OpenDNS"},
                {"value": "manual", "label": "Manual"},
            ],
            default="auto",
            disabled=tor_overrides_network,
            disabled_reason="DNS is managed by Tor when Tor routing is enabled.",
        ),
        TextField(
            key="CUSTOM_DNS_MANUAL",
            label="Manual DNS Servers",
            description="Comma-separated list of DNS server IP addresses (e.g., 8.8.8.8, 1.1.1.1).",
            placeholder="8.8.8.8, 1.1.1.1",
            disabled=tor_overrides_network,
            disabled_reason="DNS is managed by Tor when Tor routing is enabled.",
            show_when={"field": "CUSTOM_DNS", "value": "manual"},
        ),
        CheckboxField(
            key="USE_DOH",
            label="Use DNS over HTTPS",
            description=(
                "Not applicable when Tor routing is enabled."
                if tor_overrides_network
                else "Use encrypted DNS queries for improved reliability and privacy."
            ),
            default=True,
            disabled=tor_overrides_network,
            disabled_reason="DNS over HTTPS is not used when Tor routing is enabled.",
            # Hide for manual and system (no DoH endpoint available for custom IPs or system DNS)
            show_when={"field": "CUSTOM_DNS", "value": ["auto", "google", "cloudflare", "quad9", "opendns"]},
            # Disable for auto (always uses DoH)
            disabled_when={
                "field": "CUSTOM_DNS",
                "value": "auto",
                "reason": "Auto mode always uses DNS over HTTPS for reliable provider rotation.",
            },
        ),
        TextField(
            key="HTTP_PROXY",
            label="HTTP Proxy",
            description=(
                "Not applicable when Tor routing is enabled."
                if tor_overrides_network
                else "HTTP proxy URL (e.g., http://proxy:8080). Leave empty for direct connection."
            ),
            placeholder="http://proxy:8080",
            disabled=tor_overrides_network,
            disabled_reason="Proxy settings are not used when Tor routing is enabled.",
        ),
        TextField(
            key="HTTPS_PROXY",
            label="HTTPS Proxy",
            description=(
                "Not applicable when Tor routing is enabled."
                if tor_overrides_network
                else "HTTPS proxy URL. Leave empty for direct connection."
            ),
            placeholder="http://proxy:8080",
            disabled=tor_overrides_network,
            disabled_reason="Proxy settings are not used when Tor routing is enabled.",
        ),
    ]


@register_settings("ingest_directories", "Ingest Directories", icon="folder", order=5)
def ingest_directory_settings():
    """Configure where different content types are saved."""
    return [
        TextField(
            key="INGEST_DIR",
            label="Default Ingest Directory",
            description="Default directory for all downloads. Used when no specific directory is set.",
            default="/cwa-book-ingest",
            required=True,
        ),
        HeadingField(
            key="content_type_directories_heading",
            title="Content-Type Directories",
            description="Override the default directory for specific content types. Leave empty to use the default.",
        ),
        TextField(
            key="INGEST_DIR_BOOK_FICTION",
            label="Fiction Books",
            placeholder="/cwa-book-ingest/fiction",
        ),
        TextField(
            key="INGEST_DIR_BOOK_NON_FICTION",
            label="Non-Fiction Books",
            placeholder="/cwa-book-ingest/non-fiction",
        ),
        TextField(
            key="INGEST_DIR_BOOK_UNKNOWN",
            label="Unknown Books",
            placeholder="/cwa-book-ingest/unknown",
        ),
        TextField(
            key="INGEST_DIR_MAGAZINE",
            label="Magazines",
            placeholder="/cwa-book-ingest/magazines",
        ),
        TextField(
            key="INGEST_DIR_COMIC_BOOK",
            label="Comic Books",
            placeholder="/cwa-book-ingest/comics",
        ),
        TextField(
            key="INGEST_DIR_AUDIOBOOK",
            label="Audiobooks",
            placeholder="/cwa-book-ingest/audiobooks",
        ),
        TextField(
            key="INGEST_DIR_STANDARDS_DOCUMENT",
            label="Standards Documents",
            placeholder="/cwa-book-ingest/standards",
        ),
        TextField(
            key="INGEST_DIR_MUSICAL_SCORE",
            label="Musical Scores",
            placeholder="/cwa-book-ingest/scores",
        ),
        TextField(
            key="INGEST_DIR_OTHER",
            label="Other",
            placeholder="/cwa-book-ingest/other",
        ),
    ]


@register_settings("download_sources", "Download Sources", icon="download", order=21, group="direct_download")
def download_source_settings():
    """Settings for download source behavior."""
    return [
        SelectField(
            key="AA_BASE_URL",
            label="Anna's Archive URL",
            description="Primary Anna's Archive mirror to use. 'auto' selects automatically.",
            options=[
                {"value": "auto", "label": "Auto (Recommended)"},
                {"value": "https://annas-archive.org", "label": "annas-archive.org"},
                {"value": "https://annas-archive.se", "label": "annas-archive.se"},
                {"value": "https://annas-archive.li", "label": "annas-archive.li"},
            ],
            default="auto",
        ),
        TextField(
            key="AA_ADDITIONAL_URLS",
            label="Additional AA Mirrors",
            description="Comma-separated list of additional Anna's Archive mirror URLs.",
            placeholder="https://example.com,https://another.com",
        ),
        PasswordField(
            key="AA_DONATOR_KEY",
            label="Anna's Archive Donator Key",
            description="Optional donator key for faster downloads from Anna's Archive.",
        ),
        CheckboxField(
            key="ALLOW_USE_WELIB",
            label="Allow Welib Downloads",
            description="Enable Welib as a fallback download source.",
            default=True,
        ),
        CheckboxField(
            key="PRIORITIZE_WELIB",
            label="Prioritize Welib",
            description="Try Welib before other slow download sources.",
            default=False,
            show_when={"field": "ALLOW_USE_WELIB", "value": True},
        ),
        NumberField(
            key="MAX_RETRY",
            label="Max Retries",
            description="Maximum retry attempts for failed downloads.",
            default=10,
            min_value=1,
            max_value=50,
        ),
        NumberField(
            key="DEFAULT_SLEEP",
            label="Retry Delay (seconds)",
            description="Wait time between download retry attempts.",
            default=5,
            min_value=1,
            max_value=60,
        ),
    ]


@register_settings("cloudflare_bypass", "Cloudflare Bypass", icon="shield", order=22, group="direct_download")
def cloudflare_bypass_settings():
    """Settings for Cloudflare bypass behavior."""
    return [
        CheckboxField(
            key="USE_CF_BYPASS",
            label="Enable Cloudflare Bypass",
            description="Attempt to bypass Cloudflare protection on download sites.",
            default=True,
            requires_restart=True,
        ),
        CheckboxField(
            key="BYPASS_WARMUP_ON_CONNECT",
            label="Warmup on Connect",
            description="Pre-warm the bypasser when user connects to Web App UI",
            default=True,
        ),
        NumberField(
            key="BYPASS_RELEASE_INACTIVE_MIN",
            label="Release Inactive (minutes)",
            description="Release bypasser resources after this many minutes of inactivity.",
            default=5,
            min_value=1,
            max_value=60,
        ),
        CheckboxField(
            key="USING_EXTERNAL_BYPASSER",
            label="Use External Bypasser",
            description="Use FlareSolverr or similar external service instead of built-in bypasser.",
            default=False,
            requires_restart=True,
        ),
        TextField(
            key="EXT_BYPASSER_URL",
            label="External Bypasser URL",
            description="URL of the external bypasser service (e.g., FlareSolverr).",
            default="http://flaresolverr:8191",
            placeholder="http://flaresolverr:8191",
            requires_restart=True,
            show_when={"field": "USING_EXTERNAL_BYPASSER", "value": True},
        ),
        TextField(
            key="EXT_BYPASSER_PATH",
            label="External Bypasser Path",
            description="API path for the external bypasser.",
            default="/v1",
            placeholder="/v1",
            requires_restart=True,
            show_when={"field": "USING_EXTERNAL_BYPASSER", "value": True},
        ),
        NumberField(
            key="EXT_BYPASSER_TIMEOUT",
            label="External Bypasser Timeout (ms)",
            description="Timeout for external bypasser requests in milliseconds.",
            default=60000,
            min_value=10000,
            max_value=300000,
            requires_restart=True,
            show_when={"field": "USING_EXTERNAL_BYPASSER", "value": True},
        ),
    ]


@register_settings("advanced", "Advanced", icon="cog", order=15)
def advanced_settings():
    """Advanced settings for power users."""
    return [
        TextField(
            key="CUSTOM_SCRIPT",
            label="Custom Script Path",
            description="Path to a script to run after each successful download. Must be executable.",
            placeholder="/path/to/script.sh",
        ),
        CheckboxField(
            key="DEBUG",
            label="Debug Mode",
            description="Enable verbose logging. Not recommended for normal use.",
            default=False,
            requires_restart=True,
        ),
        SelectField(
            key="LOG_LEVEL",
            label="Log Level",
            description="Logging verbosity level.",
            options=[
                {"value": "DEBUG", "label": "Debug"},
                {"value": "INFO", "label": "Info"},
                {"value": "WARNING", "label": "Warning"},
                {"value": "ERROR", "label": "Error"},
            ],
            default="INFO",
            requires_restart=True,
        ),
        CheckboxField(
            key="ENABLE_LOGGING",
            label="Enable File Logging",
            description="Write logs to file in addition to console output.",
            default=True,
            requires_restart=True,
        ),
        NumberField(
            key="MAIN_LOOP_SLEEP_TIME",
            label="Queue Check Interval (seconds)",
            description="How often the download queue is checked for new items.",
            default=5,
            min_value=1,
            max_value=60,
            requires_restart=True,
        ),
        NumberField(
            key="DOWNLOAD_PROGRESS_UPDATE_INTERVAL",
            label="Progress Update Interval (seconds)",
            description="How often download progress is broadcast to the UI.",
            default=1,
            min_value=1,
            max_value=10,
            requires_restart=True,
        ),
        HeadingField(
            key="covers_cache_heading",
            title="Cover Image Cache",
            description="Cache book cover images locally for faster loading. Works for both Direct Download and Universal mode.",
        ),
        CheckboxField(
            key="COVERS_CACHE_ENABLED",
            label="Enable Cover Cache",
            description="Cache book covers on the server for faster loading.",
            default=True,
        ),
        NumberField(
            key="COVERS_CACHE_TTL",
            label="Cache TTL (days)",
            description="How long to keep cached covers. Set to 0 to keep forever (recommended for static artwork).",
            default=0,
            min_value=0,
            max_value=365,
        ),
        NumberField(
            key="COVERS_CACHE_MAX_SIZE_MB",
            label="Max Cache Size (MB)",
            description="Maximum disk space for cached covers. Oldest images are removed when limit is reached.",
            default=500,
            min_value=50,
            max_value=5000,
        ),
        ActionButton(
            key="clear_covers_cache",
            label="Clear Cover Cache",
            description="Delete all cached cover images.",
            style="danger",
            callback=_clear_covers_cache,
        ),
    ]
