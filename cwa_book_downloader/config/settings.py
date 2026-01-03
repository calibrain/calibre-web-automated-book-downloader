"""Core settings registration and derived configuration values."""

import os
from pathlib import Path
import json

from cwa_book_downloader.config import env
from cwa_book_downloader.core.logger import setup_logger

logger = setup_logger(__name__)

# Log configuration values at DEBUG level
_SENSITIVE_KEYS = {"AA_DONATOR_KEY", "HARDCOVER_API_KEY"}
logger.debug("Environment configuration:")
for key, value in env.__dict__.items():
    # Skip private attributes, modules, types, and callables
    if key.startswith('_') or isinstance(value, type) or callable(value):
        continue
    if hasattr(value, '__name__') and hasattr(value, '__file__'):
        continue
    # Redact sensitive values
    if key in _SENSITIVE_KEYS and isinstance(value, str) and value.strip():
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

# DNS placeholders - actual values set by network.init() from config/ENV
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

# Anna's Archive settings
AA_BASE_URL = env._AA_BASE_URL
AA_AVAILABLE_URLS = ["https://annas-archive.org", "https://annas-archive.se", "https://annas-archive.li"]
AA_AVAILABLE_URLS.extend(env._AA_ADDITIONAL_URLS.split(","))
AA_AVAILABLE_URLS = [url.strip() for url in AA_AVAILABLE_URLS if url.strip()]

# File format settings
SUPPORTED_FORMATS = env._SUPPORTED_FORMATS.split(",")
logger.debug(f"SUPPORTED_FORMATS: {SUPPORTED_FORMATS}")
SUPPORTED_AUDIOBOOK_FORMATS = env._SUPPORTED_AUDIOBOOK_FORMATS.split(",")
logger.debug(f"SUPPORTED_AUDIOBOOK_FORMATS: {SUPPORTED_AUDIOBOOK_FORMATS}")

# Filter book languages to only supported codes
_VALID_LANGUAGE_CODES = {lang['code'] for lang in _SUPPORTED_BOOK_LANGUAGE}
BOOK_LANGUAGE = [lang for lang in env._BOOK_LANGUAGE.split(',') if lang in _VALID_LANGUAGE_CODES]
if not BOOK_LANGUAGE:
    BOOK_LANGUAGE = ['en']

# Custom script settings with validation logic
CUSTOM_SCRIPT = env._CUSTOM_SCRIPT
if CUSTOM_SCRIPT:
    if not os.path.exists(CUSTOM_SCRIPT):
        logger.warning(f"CUSTOM_SCRIPT {CUSTOM_SCRIPT} does not exist")
        CUSTOM_SCRIPT = ""
    elif not os.access(CUSTOM_SCRIPT, os.X_OK):
        logger.warning(f"CUSTOM_SCRIPT {CUSTOM_SCRIPT} is not executable")
        CUSTOM_SCRIPT = ""

# Debugging settings
if not env.USING_EXTERNAL_BYPASSER:
    # Recording directory for debugging internal cloudflare bypasser
    RECORDING_DIR = env.LOG_DIR / "recording"


from cwa_book_downloader.core.settings_registry import (
    register_settings,
    register_group,
    TextField,
    PasswordField,
    NumberField,
    CheckboxField,
    SelectField,
    MultiSelectField,
    OrderableListField,
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


# Anna's Archive sort options (for Direct mode)
_AA_SORT_OPTIONS = [
    {"value": "relevance", "label": "Most relevant"},
    {"value": "newest", "label": "Newest (publication year)"},
    {"value": "oldest", "label": "Oldest (publication year)"},
    {"value": "largest", "label": "Largest (filesize)"},
    {"value": "smallest", "label": "Smallest (filesize)"},
    {"value": "newest_added", "label": "Newest (open sourced)"},
    {"value": "oldest_added", "label": "Oldest (open sourced)"},
]

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
    {"value": "zip", "label": "ZIP"},
    {"value": "rar", "label": "RAR"},
]

_AUDIOBOOK_FORMAT_OPTIONS = [
    {"value": "m4b", "label": "M4B"},
    {"value": "mp3", "label": "MP3"},
    {"value": "zip", "label": "ZIP"},
    {"value": "rar", "label": "RAR"},
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


def _get_metadata_provider_options_with_none():
    """Build metadata provider options with a 'Use main provider' option first."""
    return [{"value": "", "label": "Use book provider"}] + _get_metadata_provider_options()


def _get_release_source_options():
    """Build release source options dynamically from registered sources."""
    from cwa_book_downloader.release_sources import list_available_sources

    return [
        {"value": source["name"], "label": source["display_name"]}
        for source in list_available_sources()
    ]

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


def _clear_metadata_cache(current_values: dict) -> dict:
    """Clear the in-memory metadata cache."""
    try:
        from cwa_book_downloader.core.cache import get_metadata_cache

        cache = get_metadata_cache()
        stats_before = cache.stats()
        cache.clear()

        return {
            "success": True,
            "message": f"Cleared {stats_before['size']} cached entries.",
        }
    except Exception as e:
        logger.error(f"Failed to clear metadata cache: {e}")
        return {
            "success": False,
            "message": f"Failed to clear cache: {str(e)}",
        }


@register_settings("general", "General", icon="settings", order=0)
def general_settings():
    """Core application settings."""
    return [
        TextField(
            key="CALIBRE_WEB_URL",
            label="Book Management App URL",
            description="Adds a navigation button to your book manager instance (Calibre-Web Automated, Booklore, etc).",
            placeholder="http://calibre-web:8083",
        ),
        HeadingField(
            key="search_defaults_heading",
            title="Default Search Filters",
            description="Default filters applied to searches. Can be overridden using advanced search options.",
        ),
        MultiSelectField(
            key="SUPPORTED_FORMATS",
            label="Supported Book Formats",
            description="Book formats to include in search results. ZIP/RAR archives are extracted automatically and book files are used if found.",
            options=_FORMAT_OPTIONS,
            default=["epub", "mobi", "azw3", "fb2", "djvu", "cbz", "cbr"],
        ),
        MultiSelectField(
            key="SUPPORTED_AUDIOBOOK_FORMATS",
            label="Supported Audiobook Formats",
            description="Audiobook formats to include in search results. ZIP/RAR archives are extracted automatically and audiobook files are used if found.",
            options=_AUDIOBOOK_FORMAT_OPTIONS,
            default=["m4b", "mp3"],
        ),
        MultiSelectField(
            key="BOOK_LANGUAGE",
            label="Default Book Languages",
            description="Default language filter for searches.",
            options=_LANGUAGE_OPTIONS,
            default=["en"],
        ),
    ]


@register_settings("search_mode", "Search Mode", icon="search", order=1)
def search_mode_settings():
    """Configure how you search for and download books."""
    return [
        HeadingField(
            key="search_mode_heading",
            title="Search Mode",
            description="Direct mode searches Anna's Archive and downloads immediately. Universal mode searches book metadata first, letting you choose from multiple release sources including Anna's Archive and Prowlarr.",
        ),
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
                    "description": "Metadata-based search with downloads from all sources. Book and Audiobook support.",
                },
            ],
            default="direct",
        ),
        SelectField(
            key="AA_DEFAULT_SORT",
            label="Default Sort Order",
            description="Default sort order for Anna's Archive search results.",
            options=_AA_SORT_OPTIONS,
            default="relevance",
            env_supported=False,  # UI-only setting
            show_when={"field": "SEARCH_MODE", "value": "direct"},
        ),
        HeadingField(
            key="universal_mode_heading",
            title="Universal Mode Settings",
            description="Configure metadata providers and release sources for Universal search mode.",
            show_when={"field": "SEARCH_MODE", "value": "universal"},
        ),
        SelectField(
            key="METADATA_PROVIDER",
            label="Book Metadata Provider",
            description="Choose which metadata provider to use for book searches.",
            options=_get_metadata_provider_options,  # Callable - evaluated lazily to avoid circular imports
            default="openlibrary",
            show_when={"field": "SEARCH_MODE", "value": "universal"},
        ),
        SelectField(
            key="METADATA_PROVIDER_AUDIOBOOK",
            label="Audiobook Metadata Provider",
            description="Metadata provider for audiobook searches. Uses the book provider if not set.",
            options=_get_metadata_provider_options_with_none,  # Callable - includes "Use main provider" option
            default="",
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
            key="PROXY_MODE",
            label="Proxy Mode",
            description=(
                "Not applicable when Tor routing is enabled."
                if tor_overrides_network
                else "Choose proxy type. SOCKS5 handles all traffic through a single proxy."
            ),
            options=[
                {"value": "none", "label": "None (Direct Connection)"},
                {"value": "http", "label": "HTTP/HTTPS Proxy"},
                {"value": "socks5", "label": "SOCKS5 Proxy"},
            ],
            default="none",
            disabled=tor_overrides_network,
            disabled_reason="Proxy settings are not used when Tor routing is enabled.",
        ),
        TextField(
            key="HTTP_PROXY",
            label="HTTP Proxy",
            description="HTTP proxy URL (e.g., http://proxy:8080)",
            placeholder="http://proxy:8080",
            disabled=tor_overrides_network,
            disabled_reason="Proxy settings are not used when Tor routing is enabled.",
            show_when={"field": "PROXY_MODE", "value": "http"},
        ),
        TextField(
            key="HTTPS_PROXY",
            label="HTTPS Proxy",
            description="HTTPS proxy URL (leave empty to use HTTP proxy for HTTPS)",
            placeholder="http://proxy:8080",
            disabled=tor_overrides_network,
            disabled_reason="Proxy settings are not used when Tor routing is enabled.",
            show_when={"field": "PROXY_MODE", "value": "http"},
        ),
        TextField(
            key="SOCKS5_PROXY",
            label="SOCKS5 Proxy",
            description="SOCKS5 proxy URL. Supports auth: socks5://user:pass@host:port",
            placeholder="socks5://localhost:1080",
            disabled=tor_overrides_network,
            disabled_reason="Proxy settings are not used when Tor routing is enabled.",
            show_when={"field": "PROXY_MODE", "value": "socks5"},
        ),
    ]


@register_settings("downloads", "Downloads", icon="folder", order=5)
def download_settings():
    """Configure download behavior and file locations."""
    return [
        # === BOOKS SECTION ===
        # Visible for ALL modes (Direct + Universal)
        HeadingField(
            key="books_heading",
            title="Books",
            description="Configure where ebooks, comics, and magazines are saved.",
        ),
        TextField(
            key="DESTINATION",
            label="Destination",
            description="Directory where downloaded files are saved.",
            default="/cwa-book-ingest",
            required=True,
        ),
        SelectField(
            key="FILE_ORGANIZATION",
            label="File Organization",
            description="Choose how downloaded book files are named and organized. ",
            options=[
                {
                    "value": "none",
                    "label": "None",
                    "description": "Keep original filename from source"
                },
                {
                    "value": "rename",
                    "label": "Rename",
                    "description": "Rename files using a template"
                },
                {
                    "value": "organize",
                    "label": "Organize",
                    "description": "Create folders and rename files using a template. Do not use with ingest folders."
                },
            ],
            default="rename",
        ),
        # Rename mode template - filename only
        TextField(
            key="TEMPLATE_RENAME",
            label="Naming Template",
            description="Variables: {Author}, {Title}, {Year}. Universal adds: {Series}, {SeriesPosition}, {Subtitle}",
            default="{Author} - {Title} ({Year})",
            placeholder="{Author} - {Title} ({Year})",
            show_when={"field": "FILE_ORGANIZATION", "value": "rename"},
        ),
        # Organize mode template - folders allowed
        TextField(
            key="TEMPLATE_ORGANIZE",
            label="Path Template",
            description="Use / to create folders. Variables: {Author}, {Title}, {Year}. Universal adds: {Series}, {SeriesPosition}, {Subtitle}",
            default="{Author}/{Title} ({Year})",
            placeholder="{Author}/{Series/}{Title} ({Year})",
            show_when={"field": "FILE_ORGANIZATION", "value": "organize"},
        ),
        CheckboxField(
            key="HARDLINK_TORRENTS",
            label="Hardlink Book Torrents",
            description="Create hardlinks instead of copying. Preserves seeding but archives won't be extracted. Don't use if destination is a library ingest folder.",
            default=False,
            universal_only=True,
        ),

        # === AUDIOBOOKS SECTION ===
        # Universal mode only
        HeadingField(
            key="audiobooks_heading",
            title="Audiobooks",
            description="Configure where audiobooks are saved.",
            universal_only=True,
        ),
        TextField(
            key="DESTINATION_AUDIOBOOK",
            label="Destination",
            description="Leave empty to use Books destination.",
            placeholder="/audiobooks",
            universal_only=True,
        ),
        SelectField(
            key="FILE_ORGANIZATION_AUDIOBOOK",
            label="File Organization",
            description="Choose how downloaded audiobook files are named and organized.",
            options=[
                {"value": "none", "label": "None", "description": "Keep original filename from source"},
                {"value": "rename", "label": "Rename", "description": "Rename files using a template"},
                {"value": "organize", "label": "Organize", "description": "Create folders and rename files using a template. Recommended for Audiobookshelf. Do not use with ingest folders."},
            ],
            default="rename",
            universal_only=True,
        ),
        # Rename mode template - filename only
        TextField(
            key="TEMPLATE_AUDIOBOOK_RENAME",
            label="Naming Template",
            description="Variables: {Author}, {Title}, {Year}, {Series}, {SeriesPosition}, {Subtitle}, {PartNumber}",
            default="{Author} - {Title}",
            placeholder="{Author} - {Title}{ - Part }{PartNumber}",
            show_when={"field": "FILE_ORGANIZATION_AUDIOBOOK", "value": "rename"},
            universal_only=True,
        ),
        # Organize mode template - folders allowed
        TextField(
            key="TEMPLATE_AUDIOBOOK_ORGANIZE",
            label="Path Template",
            description="Use / to create folders. Variables: {Author}, {Title}, {Year}, {Series}, {SeriesPosition}, {Subtitle}, {PartNumber}",
            default="{Author}/{Title}",
            placeholder="{Author}/{Series/}{Title}{ - Part }{PartNumber}",
            show_when={"field": "FILE_ORGANIZATION_AUDIOBOOK", "value": "organize"},
            universal_only=True,
        ),
        CheckboxField(
            key="HARDLINK_TORRENTS_AUDIOBOOK",
            label="Hardlink Audiobook Torrents",
            description="Create hardlinks instead of copying. Preserves seeding but archives won't be extracted. Don't use if destination is a library ingest folder.",
            default=True,
            universal_only=True,
        ),

        # === OPTIONS SECTION ===
        HeadingField(
            key="options_heading",
            title="Options",
        ),
        CheckboxField(
            key="AUTO_OPEN_DOWNLOADS_SIDEBAR",
            label="Auto-Open Downloads Sidebar",
            description="Automatically open the downloads sidebar when a new download is queued.",
            default=False,
            env_supported=False,  # UI-only setting
        ),
        CheckboxField(
            key="DOWNLOAD_TO_BROWSER",
            label="Download to Browser",
            description="Automatically download completed files to your browser.",
            default=False,
            env_supported=False,  # UI-only setting
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


def _get_source_priority_options():
    """Build source priority options with dynamic disabled states."""
    from cwa_book_downloader.core.config import config

    has_donator_key = bool(config.get("AA_DONATOR_KEY", ""))
    use_cf_bypass = config.get("USE_CF_BYPASS", True)
    using_external_bypasser = config.get("USING_EXTERNAL_BYPASSER", False)
    has_internal_bypasser = use_cf_bypass and not using_external_bypasser

    return [
        {
            "id": "aa-fast",
            "label": "Anna's Archive (Fast)",
            "description": "Fast downloads for donators",
            "isLocked": not has_donator_key,
            "disabledReason": "Requires AA Donator Key" if not has_donator_key else None,
        },
        {
            "id": "welib",
            "label": "Welib",
            "description": "Alternative mirror with good availability",
            "isLocked": not has_internal_bypasser,
            "disabledReason": "Requires internal bypasser" if not has_internal_bypasser else None,
        },
        {
            "id": "aa-slow-nowait",
            "label": "Anna's Archive (Slowest, No Waitlist)",
            "description": "Partner servers without countdown",
        },
        {
            "id": "aa-slow-wait",
            "label": "Anna's Archive (Slow, Waitlist)",
            "description": "Partner servers with countdown timer",
        },
        {
            "id": "libgen",
            "label": "Libgen",
            "description": "Library Genesis mirrors",
        },
        {
            "id": "zlib",
            "label": "Z-Library",
            "description": "Z-Library mirrors (requires Cloudflare bypass)",
            "isLocked": not has_internal_bypasser,
            "disabledReason": "Requires internal bypasser" if not has_internal_bypasser else None,
        },
    ]


def _get_default_source_priority():
    """Default source priority order, respecting legacy env vars.

    ALLOW_USE_WELIB (default true) controls whether welib is enabled.
    PRIORITIZE_WELIB (default false) controls whether welib is moved to position 1.
    """
    from cwa_book_downloader.config.env import _LEGACY_PRIORITIZE_WELIB, _LEGACY_ALLOW_USE_WELIB

    welib_entry = {"id": "welib", "enabled": _LEGACY_ALLOW_USE_WELIB}

    priority = [
        {"id": "aa-fast", "enabled": True},
        {"id": "aa-slow-nowait", "enabled": True},
        {"id": "aa-slow-wait", "enabled": True},
        {"id": "libgen", "enabled": True},
    ]

    if _LEGACY_PRIORITIZE_WELIB:
        priority.insert(1, welib_entry)  # After aa-fast
    else:
        priority.append(welib_entry)  # Before zlib

    # Z-Library last - it's quite brittle
    priority.append({"id": "zlib", "enabled": True})

    return priority


@register_settings("download_sources", "Download Sources", icon="download", order=21, group="direct_download")
def download_source_settings():
    """Settings for download source behavior."""
    return [
        HeadingField(
            key="source_priority_heading",
            title="Source Priority",
            description="Configure which download sources to use and in what order.",
        ),
        OrderableListField(
            key="SOURCE_PRIORITY",
            label="Download Source Order",
            description="Drag to reorder. Sources are tried from top to bottom until a download succeeds.",
            options=_get_source_priority_options,
            default=_get_default_source_priority(),
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
        HeadingField(
            key="aa_settings_heading",
            title="Anna's Archive",
            description="Configure Anna's Archive mirror and donator settings.",
        ),
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
        HeadingField(
            key="content_type_routing_heading",
            title="Content-Type Routing",
            description="Route downloads to different folders based on content type. Only applies to Anna's Archive downloads.",
        ),
        CheckboxField(
            key="AA_CONTENT_TYPE_ROUTING",
            label="Enable Content-Type Routing",
            description="Override destination based on Anna's Archive content type metadata.",
            default=False,
        ),
        TextField(
            key="AA_CONTENT_TYPE_DIR_FICTION",
            label="Fiction Books",
            placeholder="/cwa-book-ingest/fiction",
            show_when={"field": "AA_CONTENT_TYPE_ROUTING", "value": True},
        ),
        TextField(
            key="AA_CONTENT_TYPE_DIR_NON_FICTION",
            label="Non-Fiction Books",
            placeholder="/cwa-book-ingest/non-fiction",
            show_when={"field": "AA_CONTENT_TYPE_ROUTING", "value": True},
        ),
        TextField(
            key="AA_CONTENT_TYPE_DIR_UNKNOWN",
            label="Unknown Books",
            placeholder="/cwa-book-ingest/unknown",
            show_when={"field": "AA_CONTENT_TYPE_ROUTING", "value": True},
        ),
        TextField(
            key="AA_CONTENT_TYPE_DIR_MAGAZINE",
            label="Magazines",
            placeholder="/cwa-book-ingest/magazines",
            show_when={"field": "AA_CONTENT_TYPE_ROUTING", "value": True},
        ),
        TextField(
            key="AA_CONTENT_TYPE_DIR_COMIC",
            label="Comic Books",
            placeholder="/cwa-book-ingest/comics",
            show_when={"field": "AA_CONTENT_TYPE_ROUTING", "value": True},
        ),
        TextField(
            key="AA_CONTENT_TYPE_DIR_STANDARDS",
            label="Standards Documents",
            placeholder="/cwa-book-ingest/standards",
            show_when={"field": "AA_CONTENT_TYPE_ROUTING", "value": True},
        ),
        TextField(
            key="AA_CONTENT_TYPE_DIR_MUSICAL_SCORE",
            label="Musical Scores",
            placeholder="/cwa-book-ingest/scores",
            show_when={"field": "AA_CONTENT_TYPE_ROUTING", "value": True},
        ),
        TextField(
            key="AA_CONTENT_TYPE_DIR_OTHER",
            label="Other",
            placeholder="/cwa-book-ingest/other",
            show_when={"field": "AA_CONTENT_TYPE_ROUTING", "value": True},
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
            description="Use FlareSolverr or similar external service instead of built-in bypasser. Caution: May have limitations with custom DNS, Tor and proxies. You may experience slower downloads and and poorer reliability compared to the internal bypasser.",
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
            description="Enable verbose logging to console and file. Not recommended for normal use.",
            default=False,
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
        HeadingField(
            key="metadata_cache_heading",
            title="Metadata Cache",
            description="Cache book metadata from providers (Hardcover, Open Library) to reduce API calls and speed up repeated searches.",
        ),
        CheckboxField(
            key="METADATA_CACHE_ENABLED",
            label="Enable Metadata Caching",
            description="When disabled, all metadata searches hit the provider API directly.",
            default=True,
        ),
        NumberField(
            key="METADATA_CACHE_SEARCH_TTL",
            label="Search Results Cache (seconds)",
            description="How long to cache search results. Default: 300 (5 minutes). Max: 604800 (7 days).",
            default=300,
            min_value=60,
            max_value=604800,
            show_when={"field": "METADATA_CACHE_ENABLED", "value": True},
        ),
        NumberField(
            key="METADATA_CACHE_BOOK_TTL",
            label="Book Details Cache (seconds)",
            description="How long to cache individual book details. Default: 600 (10 minutes). Max: 604800 (7 days).",
            default=600,
            min_value=60,
            max_value=604800,
            show_when={"field": "METADATA_CACHE_ENABLED", "value": True},
        ),
        ActionButton(
            key="clear_metadata_cache",
            label="Clear Metadata Cache",
            description="Clear all cached search results and book details.",
            style="danger",
            callback=_clear_metadata_cache,
        ),
    ]
