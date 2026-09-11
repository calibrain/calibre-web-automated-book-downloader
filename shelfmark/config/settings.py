"""Core settings registration and derived configuration values."""

from pathlib import Path
from typing import Any

from shelfmark.config import env
from shelfmark.config.booklore_settings import (
    check_booklore_connection,
    get_booklore_library_options,
    get_booklore_path_options,
)
from shelfmark.config.download_settings_handlers import (
    check_audiobook_destination,
    check_books_destination,
)
from shelfmark.config.email_settings import check_email_connection
from shelfmark.config.migrations import migrate_audiobook_formats
from shelfmark.core.languages import supported_book_languages
from shelfmark.core.logger import setup_logger
from shelfmark.core.settings_registry import (
    ActionButton,
    CheckboxField,
    CustomComponentField,
    HeadingField,
    MultiSelectField,
    NumberField,
    OrderableListField,
    PasswordField,
    SelectField,
    SettingsField,
    TableField,
    TagListField,
    TextField,
    load_config_file,
    register_group,
    register_on_save,
    register_settings,
)
from shelfmark.core.utils import ARCHIVE_FORMATS, AUDIOBOOK_FORMATS

_DOWNLOAD_CLIENT_COMPLETED_PATH_TIMEOUT_DEFAULT = 60
_DOWNLOAD_CLIENT_COMPLETED_PATH_TIMEOUT_MAX = 3600


def _on_save_advanced(values: dict[str, Any]) -> dict[str, Any]:
    """Validate advanced settings before persisting."""
    from shelfmark.core.logger import setup_logger

    logger = setup_logger(__name__)

    timeout_key = "DOWNLOAD_CLIENT_COMPLETED_PATH_TIMEOUT"
    if timeout_key in values:
        raw_timeout = values.get(timeout_key)
        if isinstance(raw_timeout, bool):
            return {
                "error": True,
                "message": "Completed Path Wait must be a number of seconds",
                "values": values,
            }
        if raw_timeout is None:
            return {
                "error": True,
                "message": "Completed Path Wait must be a number of seconds",
                "values": values,
            }
        try:
            timeout_seconds = int(raw_timeout)
        except TypeError, ValueError:
            return {
                "error": True,
                "message": "Completed Path Wait must be a number of seconds",
                "values": values,
            }
        if timeout_seconds < 0 or timeout_seconds > _DOWNLOAD_CLIENT_COMPLETED_PATH_TIMEOUT_MAX:
            return {
                "error": True,
                "message": (
                    "Completed Path Wait must be between 0 and "
                    f"{_DOWNLOAD_CLIENT_COMPLETED_PATH_TIMEOUT_MAX} seconds"
                ),
                "values": values,
            }
        values[timeout_key] = timeout_seconds

    mappings = values.get("PROWLARR_REMOTE_PATH_MAPPINGS")
    if mappings is None:
        return {"error": False, "values": values}

    if not isinstance(mappings, list):
        return {
            "error": True,
            "message": "Remote path mappings must be a list",
            "values": values,
        }

    logger.debug("Processing %d remote path mapping entries", len(mappings))

    cleaned = []
    for i, entry in enumerate(mappings):
        if not isinstance(entry, dict):
            logger.debug("Skipping entry %d: not a dict", i)
            continue

        host = str(entry.get("host", "") or "").strip().lower()
        remote_path = str(entry.get("remotePath", "") or "").strip()
        local_path = str(entry.get("localPath", "") or "").strip()

        if not host or not remote_path or not local_path:
            logger.debug(
                "Skipping entry %d: missing field(s) - host=%s, remotePath=%s, localPath=%s",
                i,
                host,
                remote_path,
                local_path,
            )
            continue

        if not local_path.startswith("/"):
            return {
                "error": True,
                "message": f"Local Path must be an absolute path (got: {local_path})",
                "values": values,
            }

        cleaned.append({"host": host, "remotePath": remote_path, "localPath": local_path})

    logger.info("Saved %d remote path mapping(s)", len(cleaned))
    if cleaned:
        for m in cleaned:
            logger.debug(
                "  Mapping: %s -> %s (client: %s)", m["remotePath"], m["localPath"], m["host"]
            )

    values["PROWLARR_REMOTE_PATH_MAPPINGS"] = cleaned
    return {"error": False, "values": values}


logger = setup_logger(__name__)


def migrate_audiobook_format_settings() -> None:
    """Bring installs created before the audiobook format sets were unified up to date."""
    from shelfmark.core.settings_registry import load_config_file, save_config_file

    migrate_audiobook_formats(
        load_general_config=lambda: load_config_file("general"),
        save_general_config=lambda values: save_config_file("general", values),
        widened_formats=[*AUDIOBOOK_FORMATS, *ARCHIVE_FORMATS],
        logger=logger,
    )


_SMTP_PORT_MAX = 65535
_EMAIL_ATTACHMENT_LIMIT_MB_MAX = 600

# Log bootstrap configuration values at DEBUG level
logger.debug("Bootstrap configuration:")
for key in ["CONFIG_DIR", "LOG_DIR", "TMP_DIR", "INGEST_DIR", "DEBUG", "DOCKERMODE"]:
    if hasattr(env, key):
        logger.debug("  %s: %s", key, getattr(env, key))

# Selectable book languages, without the resolution aliases clients do not need.
_SUPPORTED_BOOK_LANGUAGE = supported_book_languages()

# Directory settings
BASE_DIR = Path(__file__).resolve().parent.parent.parent
logger.debug("BASE_DIR: %s", BASE_DIR)
if env.ENABLE_LOGGING:
    env.LOG_DIR.mkdir(exist_ok=True)

# Create staging directory (destination is created by orchestrator using config value)
env.TMP_DIR.mkdir(exist_ok=True)

# DNS placeholders - actual values set by network.init() from config/ENV
CUSTOM_DNS: list[str] = []
DOH_SERVER: str = ""

# Recording directory for debugging internal cloudflare bypasser
RECORDING_DIR = env.LOG_DIR / "recording"


def _log_external_bypasser_warning() -> None:
    """Log warning about external bypasser DNS limitations (called after config is available)."""
    from shelfmark.core.config import config

    if config.get("USING_EXTERNAL_BYPASSER", False) and config.get("USE_CF_BYPASS", True):
        logger.warning(
            "Using external bypasser (FlareSolverr). Note: FlareSolverr uses its own DNS resolution, "
            "not this application's custom DNS settings. If you experience DNS-related blocks, "
            "configure DNS at the Docker/system level for your FlareSolverr container, "
            "or consider using the internal bypasser which integrates with the app's DNS system."
        )


register_group("direct_download", "Direct Download", icon="download", order=20)

register_group(
    "metadata_providers",
    "Metadata Providers",
    icon="book",
    order=12,  # Between Network (10) and Advanced (15)
)


# Direct mode sort options
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
    {"value": fmt, "label": fmt.upper()} for fmt in (*AUDIOBOOK_FORMATS, *ARCHIVE_FORMATS)
]

_DOWNLOAD_TO_BROWSER_CONTENT_TYPE_OPTIONS = [
    {
        "value": "book",
        "label": "Books",
        "description": "Automatically download completed book files to this browser.",
    },
    {
        "value": "audiobook",
        "label": "Audiobooks",
        "description": "Automatically download completed audiobook files to this browser.",
    },
]

_DOWNLOAD_TO_BROWSER_CONTENT_TYPE_VALUES = {
    option["value"] for option in _DOWNLOAD_TO_BROWSER_CONTENT_TYPE_OPTIONS
}


def _get_metadata_provider_options() -> list[dict[str, str]]:
    """Build metadata provider options dynamically from enabled providers only."""
    from shelfmark.metadata_providers import is_provider_enabled, list_providers

    options = [
        {"value": provider["name"], "label": provider["display_name"]}
        for provider in list_providers()
        if is_provider_enabled(provider["name"])
    ]

    # If no providers enabled, show a placeholder option
    if not options:
        options = [
            {"value": "", "label": "No providers enabled"},
        ]

    return options


def _get_metadata_provider_options_with_none() -> list[dict[str, str]]:
    """Build metadata provider options with a 'Use main provider' option first."""
    return [{"value": "", "label": "Use book provider"}, *_get_metadata_provider_options()]


def _get_release_source_options_for_content_type(content_type: str) -> list[dict[str, str]]:
    """Build release source options dynamically for a specific content type."""
    from shelfmark.release_sources import list_available_sources

    return [
        {"value": source["name"], "label": source["display_name"]}
        for source in list_available_sources()
        if source.get("enabled", True)
        and source.get("can_be_default", True)
        and content_type in source.get("supported_content_types", ["ebook", "audiobook"])
    ]


def _get_book_release_source_options() -> list[dict[str, str]]:
    """Build default release source options for book searches."""
    return [
        {"value": "", "label": "Use first available source"},
        *_get_release_source_options_for_content_type("ebook"),
    ]


def _get_audiobook_release_source_options() -> list[dict[str, str]]:
    """Build default release source options for audiobook searches."""
    return [
        {"value": "", "label": "Use book release source"},
        *_get_release_source_options_for_content_type("audiobook"),
    ]


_LANGUAGE_OPTIONS = [
    {"value": lang["code"], "label": lang["language"]} for lang in _SUPPORTED_BOOK_LANGUAGE
]


def _string_setting(value: object) -> str:
    """Normalize free-form string settings used by select option builders."""
    return value if isinstance(value, str) else str(value or "")


def _get_aa_base_url_options() -> list[dict[str, str]]:
    """Build AA URL options dynamically from user-supplied mirrors."""
    from shelfmark.core.config import config
    from shelfmark.core.mirrors import get_aa_mirrors
    from shelfmark.core.utils import normalize_http_url

    options = [{"value": "auto", "label": "Auto (Recommended)"}]

    all_mirrors = get_aa_mirrors()

    configured_url = normalize_http_url(
        _string_setting(config.get("AA_BASE_URL", "auto")),
        default_scheme="https",
        allow_special=("auto",),
    )
    if configured_url and configured_url != "auto" and configured_url not in all_mirrors:
        all_mirrors = [configured_url, *all_mirrors]

    for url in all_mirrors:
        domain = url.replace("https://", "").replace("http://", "")
        label = domain
        if configured_url and url == configured_url:
            label = f"{domain} (configured)"
        options.append({"value": url, "label": label})

    return options


def _clear_covers_cache(current_values: dict) -> dict:
    """Clear the cover image cache."""
    try:
        from shelfmark.core.image_cache import get_image_cache, reset_image_cache

        cache = get_image_cache()
        count = cache.clear()

        # Reset the singleton so it reinitializes with fresh state
        reset_image_cache()

    except Exception as e:
        logger.exception("Failed to clear cover cache")
        return {
            "success": False,
            "message": f"Failed to clear cache: {e!s}",
        }
    else:
        return {
            "success": True,
            "message": f"Cleared {count} cached cover images.",
        }


def _clear_metadata_cache(current_values: dict) -> dict:
    """Clear the in-memory metadata cache."""
    try:
        from shelfmark.core.cache import get_metadata_cache

        cache = get_metadata_cache()
        stats_before = cache.stats()
        cache.clear()

        return {
            "success": True,
            "message": f"Cleared {stats_before['size']} cached entries.",
        }
    except Exception as e:
        logger.exception("Failed to clear metadata cache")
        return {
            "success": False,
            "message": f"Failed to clear cache: {e!s}",
        }


@register_settings("general", "General", icon="settings", order=0)
def general_settings() -> list[SettingsField]:
    """Core application settings."""
    return [
        TextField(
            key="SEARCH_PAGE_TITLE",
            label="Search Page Title",
            description="Title shown above the main search box on the homepage.",
            default="Shelfmark",
            placeholder="Shelfmark",
        ),
        TextField(
            key="CALIBRE_WEB_URL",
            label="Library URL",
            description="Adds a navigation button to your book library (Calibre-Web Automated, Grimmory, etc).",
            placeholder="http://calibre-web:8083",
        ),
        TextField(
            key="AUDIOBOOK_LIBRARY_URL",
            label="Audiobook Library URL",
            description="Adds a separate navigation button for your audiobook library (Audiobookshelf, Plex, etc). When both URLs are set, icons are shown instead of text.",
            placeholder="http://audiobookshelf:8080",
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
            default=[*AUDIOBOOK_FORMATS, *ARCHIVE_FORMATS],
        ),
    ]


@register_settings("search_mode", "Search Mode", icon="search", order=1)
def search_mode_settings() -> list[SettingsField]:
    """Configure how you search for and download books."""
    return [
        HeadingField(
            key="search_mode_heading",
            title="Search Mode",
            description=(
                "Direct mode uses the optional Direct Download source. Universal mode uses "
                "metadata search with whichever release sources you have enabled."
            ),
        ),
        SelectField(
            key="SEARCH_MODE",
            label="Search Mode",
            description="How you want to search for and download books.",
            options=[
                {
                    "value": "direct",
                    "label": "Direct",
                    "description": (
                        "Search with the Direct Download source. Requires enabling the source "
                        "and adding your own mirror URLs."
                    ),
                },
                {
                    "value": "universal",
                    "label": "Universal",
                    "description": "Metadata-based search with downloads from all sources. Book and Audiobook support.",
                },
            ],
            default="universal",
            user_overridable=True,
        ),
        MultiSelectField(
            key="BOOK_LANGUAGE",
            label="Default Book Languages",
            description=(
                "Default language filter for searches. Users can override this for their "
                "own account."
            ),
            options=_LANGUAGE_OPTIONS,
            default=["en"],
            user_overridable=True,
        ),
        SelectField(
            key="AA_DEFAULT_SORT",
            label="Default Sort Order",
            description="Default sort order for search results.",
            options=_AA_SORT_OPTIONS,
            default="relevance",
            show_when={"field": "SEARCH_MODE", "value": "direct"},
        ),
        CheckboxField(
            key="SHOW_RELEASE_SOURCE_LINKS",
            label="Show Release Source Links",
            description=(
                "Show clickable release-source links in release and details modals. "
                "Metadata provider links stay enabled."
            ),
            default=True,
        ),
        CheckboxField(
            key="SHOW_COMBINED_SELECTOR",
            label="Show Combined Download Selector",
            description="Show the option to search for and download both a book and audiobook together.",
            default=True,
            show_when={"field": "SEARCH_MODE", "value": "universal"},
            user_overridable=True,
        ),
        CheckboxField(
            key="FORCE_COMBINED_SEARCH",
            label="Always Use Combined Search",
            description="Force combined search whenever it's available. Locks the combined toggle on.",
            default=False,
            show_when={"field": "SEARCH_MODE", "value": "universal"},
            user_overridable=True,
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
            user_overridable=True,
        ),
        SelectField(
            key="METADATA_PROVIDER_AUDIOBOOK",
            label="Audiobook Metadata Provider",
            description="Metadata provider for audiobook searches. Uses the book provider if not set.",
            options=_get_metadata_provider_options_with_none,  # Callable - includes "Use main provider" option
            default="",
            show_when={"field": "SEARCH_MODE", "value": "universal"},
            user_overridable=True,
        ),
        SelectField(
            key="METADATA_PROVIDER_COMBINED",
            label="Combined Mode Metadata Provider",
            description="Metadata provider for combined mode searches. Uses the book provider if not set.",
            options=_get_metadata_provider_options_with_none,  # Callable - includes "Use main provider" option
            default="",
            show_when={"field": "SEARCH_MODE", "value": "universal"},
            user_overridable=True,
        ),
        SelectField(
            key="DEFAULT_RELEASE_SOURCE",
            label="Default Book Release Source",
            description=(
                "The release source tab to open by default in the release modal for books. "
                "Leave unset to use the first available source."
            ),
            options=_get_book_release_source_options,  # Callable - evaluated lazily to avoid circular imports
            default="",
            show_when={"field": "SEARCH_MODE", "value": "universal"},
            user_overridable=True,
        ),
        SelectField(
            key="DEFAULT_RELEASE_SOURCE_AUDIOBOOK",
            label="Default Audiobook Release Source",
            description="The release source tab to open by default in the release modal for audiobooks. Uses the book release source if not set.",
            options=_get_audiobook_release_source_options,  # Callable - evaluated lazily to avoid circular imports
            default="",
            show_when={"field": "SEARCH_MODE", "value": "universal"},
            user_overridable=True,
        ),
    ]


@register_settings("network", "Network", icon="globe", order=10)
def network_settings() -> list[SettingsField]:
    """Network and connectivity settings."""
    # Avoid querying the live config singleton while settings are still being
    # registered, which can recurse back into this module during import.
    tor_enabled = env.USING_TOR

    # When Tor is enabled, DNS/proxy settings are overridden by iptables rules
    # Tor uses iptables to force ALL traffic through Tor
    tor_overrides_network = tor_enabled  # Only override when Tor is actually active

    return [
        SelectField(
            key="CERTIFICATE_VALIDATION",
            label="Certificate Validation",
            description="Controls SSL/TLS certificate verification for outbound connections. Disable for self-signed certificates on internal services (e.g. OIDC providers, Prowlarr).",
            options=[
                {"value": "enabled", "label": "Enabled (Recommended)"},
                {"value": "disabled_local", "label": "Disabled for Local Addresses"},
                {"value": "disabled", "label": "Disabled"},
            ],
            default="enabled",
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
            show_when={
                "field": "CUSTOM_DNS",
                "value": ["auto", "google", "cloudflare", "quad9", "opendns"],
            },
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
                "All traffic is routed through Tor. Requires container restart to change."
                if tor_enabled
                else "Route all traffic through Tor for enhanced privacy. Requires root startup."
            ),
            default=tor_enabled,  # Reflects actual state from env var
            disabled=True,  # Tor state requires container restart
            disabled_reason=(
                "Tor routing is active. Set USING_TOR=false and restart to disable."
                if tor_enabled
                else "Set USING_TOR=true env var and restart as root."
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
        TextField(
            key="NO_PROXY",
            label="No Proxy",
            description="Comma-separated hosts to bypass proxy (e.g., localhost,127.0.0.1,10.*,*.local)",
            disabled=tor_overrides_network,
            disabled_reason="Proxy settings are not used when Tor routing is enabled.",
            show_when={"field": "PROXY_MODE", "value": ["http", "socks5"]},
        ),
    ]


def _contains_path_separators(value: Any) -> bool:
    return isinstance(value, str) and ("/" in value or "\\" in value)


def _on_save_downloads(values: dict[str, Any]) -> dict[str, Any]:
    """Validate download settings before persisting."""
    existing = load_config_file("downloads")
    effective: dict[str, Any] = dict(existing)
    effective.update(values)

    if "DOWNLOAD_TO_BROWSER_CONTENT_TYPES" in effective:
        raw_content_types = effective.get("DOWNLOAD_TO_BROWSER_CONTENT_TYPES")
        if raw_content_types is None:
            normalized_content_types: list[str] = []
        elif isinstance(raw_content_types, list):
            normalized_content_types = [
                str(value).strip().lower() for value in raw_content_types if str(value).strip()
            ]
        else:
            return {
                "error": True,
                "message": "Download to Browser must be a list.",
                "values": values,
            }

        deduped_content_types: list[str] = []
        for content_type in normalized_content_types:
            if content_type not in _DOWNLOAD_TO_BROWSER_CONTENT_TYPE_VALUES:
                allowed = ", ".join(sorted(_DOWNLOAD_TO_BROWSER_CONTENT_TYPE_VALUES))
                return {
                    "error": True,
                    "message": (
                        "Download to Browser contains an unsupported content type "
                        f"'{content_type}'. Supported values: {allowed}"
                    ),
                    "values": values,
                }
            if content_type not in deduped_content_types:
                deduped_content_types.append(content_type)

        values["DOWNLOAD_TO_BROWSER_CONTENT_TYPES"] = deduped_content_types
        effective["DOWNLOAD_TO_BROWSER_CONTENT_TYPES"] = deduped_content_types

    # Books: only validate templates when saving to a folder.
    books_output_mode = effective.get("BOOKS_OUTPUT_MODE", "folder")
    if books_output_mode == "folder" and effective.get("FILE_ORGANIZATION", "rename") == "rename":
        template = effective.get("TEMPLATE_RENAME", "")
        if _contains_path_separators(template):
            return {
                "error": True,
                "message": "Books Naming Template cannot contain '/' or '\\' in Rename mode. Use Organize mode to create folders.",
                "values": values,
            }

    # Audiobooks are always folder output.
    if effective.get("FILE_ORGANIZATION_AUDIOBOOK", "rename") in {
        "rename",
        "rename_and_group",
    }:
        template = effective.get("TEMPLATE_AUDIOBOOK_RENAME", "")
        if _contains_path_separators(template):
            return {
                "error": True,
                "message": "Audiobooks Naming Template cannot contain '/' or '\\' in Rename mode. Use Organize mode to create folders.",
                "values": values,
            }

    # Email output (SMTP) validation.
    if books_output_mode == "email":
        from email.utils import parseaddr

        def _is_plain_email_address(addr: str) -> bool:
            parsed = parseaddr(addr or "")[1]
            return bool(parsed) and "@" in parsed and parsed == addr

        # Preferred model: single recipient for global default and per-user override.
        raw_recipient = str(effective.get("EMAIL_RECIPIENT", "") or "").strip()

        # Optional global fallback: validate only when a default recipient is provided.
        if raw_recipient and not _is_plain_email_address(raw_recipient):
            return {
                "error": True,
                "message": "Email recipient must be a valid plain email address.",
                "values": values,
            }

        smtp_host = str(effective.get("EMAIL_SMTP_HOST", "") or "").strip()
        if not smtp_host:
            return {"error": True, "message": "SMTP host is required", "values": values}

        security = str(effective.get("EMAIL_SMTP_SECURITY", "starttls") or "").strip().lower()
        if security not in {"none", "starttls", "ssl"}:
            return {
                "error": True,
                "message": "SMTP security must be one of: none, starttls, ssl",
                "values": values,
            }

        try:
            port = int(effective.get("EMAIL_SMTP_PORT", 587))
        except TypeError, ValueError:
            return {"error": True, "message": "SMTP port must be a number", "values": values}

        if port < 1 or port > _SMTP_PORT_MAX:
            return {
                "error": True,
                "message": f"SMTP port must be between 1 and {_SMTP_PORT_MAX}",
                "values": values,
            }

        try:
            timeout_seconds = int(effective.get("EMAIL_SMTP_TIMEOUT_SECONDS", 60))
        except TypeError, ValueError:
            return {
                "error": True,
                "message": "SMTP timeout (seconds) must be a number",
                "values": values,
            }

        if timeout_seconds < 1:
            return {
                "error": True,
                "message": "SMTP timeout (seconds) must be >= 1",
                "values": values,
            }

        username = str(effective.get("EMAIL_SMTP_USERNAME", "") or "").strip()
        password = effective.get("EMAIL_SMTP_PASSWORD", "") or ""
        if username and not password:
            return {
                "error": True,
                "message": "SMTP password is required when username is set",
                "values": values,
            }

        try:
            attachment_limit_mb = int(effective.get("EMAIL_ATTACHMENT_SIZE_LIMIT_MB", 25))
        except TypeError, ValueError:
            return {
                "error": True,
                "message": "Attachment size limit (MB) must be a number",
                "values": values,
            }

        if attachment_limit_mb < 1 or attachment_limit_mb > _EMAIL_ATTACHMENT_LIMIT_MB_MAX:
            return {
                "error": True,
                "message": (
                    "Attachment size limit (MB) must be between 1 and "
                    f"{_EMAIL_ATTACHMENT_LIMIT_MB_MAX}"
                ),
                "values": values,
            }

        from_addr = str(effective.get("EMAIL_FROM", "") or "").strip()
        if not from_addr:
            # If From is empty, default to the SMTP username when it looks like an email address.
            username_email = parseaddr(username)[1]
            if username_email and "@" in username_email:
                from_addr = f"Shelfmark <{username_email}>"
                values["EMAIL_FROM"] = from_addr
            else:
                return {
                    "error": True,
                    "message": "From address is required (or set SMTP username to an email address).",
                    "values": values,
                }
        else:
            from_email = parseaddr(from_addr)[1]
            if not from_email or "@" not in from_email:
                return {
                    "error": True,
                    "message": "From address must be a valid email address",
                    "values": values,
                }

        # Persist any normalization/coercion for fields that may have been edited this save.
        if "EMAIL_RECIPIENT" in values:
            values["EMAIL_RECIPIENT"] = raw_recipient
        if "EMAIL_SMTP_SECURITY" in values:
            values["EMAIL_SMTP_SECURITY"] = security
        if "EMAIL_SMTP_PORT" in values:
            values["EMAIL_SMTP_PORT"] = port
        if "EMAIL_SMTP_TIMEOUT_SECONDS" in values:
            values["EMAIL_SMTP_TIMEOUT_SECONDS"] = timeout_seconds
        if "EMAIL_ATTACHMENT_SIZE_LIMIT_MB" in values:
            values["EMAIL_ATTACHMENT_SIZE_LIMIT_MB"] = attachment_limit_mb

    return {"error": False, "values": values}


def _naming_template_field(
    *,
    key: str,
    label: str,
    description: str,
    default: str,
    placeholder: str,
    show_when: dict[str, Any] | list[dict[str, Any]],
    universal_only: bool = False,
) -> CustomComponentField:
    return CustomComponentField(
        key=f"{key.lower()}_editor",
        label=label,
        component="naming_template",
        show_when=show_when,
        universal_only=universal_only,
        wrap_in_field_wrapper=True,
        value_fields=[
            TextField(
                key=key,
                label=label,
                description=description,
                default=default,
                placeholder=placeholder,
                show_when=show_when,
                universal_only=universal_only,
            )
        ],
    )


@register_settings("downloads", "Downloads", icon="folder", order=5)
def download_settings() -> list[SettingsField]:
    """Configure download behavior and file locations."""
    return [
        # === BOOKS SECTION ===
        # Visible for ALL modes (Direct + Universal)
        HeadingField(
            key="books_heading",
            title="Books",
            description="Configure where ebooks, comics, and magazines are saved.",
        ),
        SelectField(
            key="BOOKS_OUTPUT_MODE",
            label="Output Mode",
            description="Choose where completed book files are sent.",
            options=[
                {
                    "value": "folder",
                    "label": "Folder",
                    "description": "Save files to the destination folder",
                },
                {
                    "value": "email",
                    "label": "Email (SMTP)",
                    "description": "Send files as an email attachment",
                },
                {
                    "value": "booklore",
                    "label": "Grimmory (API)",
                    "description": "Upload files directly to Grimmory",
                },
            ],
            default="folder",
            user_overridable=True,
        ),
        TextField(
            key="DESTINATION",
            label="Destination",
            description="Directory where downloaded files are saved. Use {User} for per-user folders (e.g. /books/{User}).",
            default="/books",
            required=True,
            env_var="INGEST_DIR",  # Legacy env var name for backwards compatibility
            user_overridable=True,
            show_when={
                "field": "BOOKS_OUTPUT_MODE",
                "value": "folder",
            },
        ),
        ActionButton(
            key="test_destination",
            label="Test Destination",
            description="Check that Shelfmark can create and write to this destination.",
            style="primary",
            callback=check_books_destination,
            show_when={
                "field": "BOOKS_OUTPUT_MODE",
                "value": "folder",
            },
        ),
        SelectField(
            key="FILE_ORGANIZATION",
            label="File Organization",
            description="Choose how downloaded book files are named and organized.",
            options=[
                {
                    "value": "none",
                    "label": "None",
                    "description": "Keep original filename from source",
                },
                {
                    "value": "rename",
                    "label": "Rename Only",
                    "description": "Rename single-file downloads; multi-file keeps original names.",
                },
                {
                    "value": "organize",
                    "label": "Rename and Organize",
                    "description": "Create folders and rename files using a template. Do not use with ingest folders.",
                },
            ],
            default="rename",
            show_when={
                "field": "BOOKS_OUTPUT_MODE",
                "value": "folder",
            },
        ),
        # Rename mode template - filename only
        _naming_template_field(
            key="TEMPLATE_RENAME",
            label="Naming Template",
            description=(
                "Variables: {Author}, {FirstAuthor} (first of several authors), {Title}, {Year}, {Language}, {User}, {OriginalName} "
                "(source filename without extension). Universal adds: {Series}, "
                "{SeriesPosition}, {Subtitle}, {PrimaryTitle}. Use arbitrary prefix/suffix: "
                "{Vol. SeriesPosition - } outputs 'Vol. 2 - ' when set, nothing when empty. "
                "Rename templates are filename-only (no '/' or '\\'); use Organize for folders. "
                "Applies to single-file downloads."
            ),
            default="{Author} - {Title} ({Year})",
            placeholder="{Author} - {Title} ({Year})",
            show_when=[
                {"field": "BOOKS_OUTPUT_MODE", "value": "folder"},
                {"field": "FILE_ORGANIZATION", "value": "rename"},
            ],
        ),
        # Organize mode template - folders allowed
        _naming_template_field(
            key="TEMPLATE_ORGANIZE",
            label="Path Template",
            description=(
                "Use / to create folders. Variables: {Author}, {FirstAuthor} (first of several authors), {Title}, {Year}, {Language}, {User}, "
                "{OriginalName} (source filename without extension). Universal adds: {Series}, "
                "{SeriesPosition}, {Subtitle}, {PrimaryTitle}. Use arbitrary prefix/suffix: "
                "{Vol. SeriesPosition - } outputs 'Vol. 2 - ' when set, nothing when empty."
            ),
            default="{Author}/{Title} ({Year})",
            placeholder="{Author}/{Series/}{Title} ({Year})",
            show_when=[
                {"field": "BOOKS_OUTPUT_MODE", "value": "folder"},
                {"field": "FILE_ORGANIZATION", "value": "organize"},
            ],
        ),
        CheckboxField(
            key="HARDLINK_TORRENTS",
            label="Hardlink Book Torrents",
            description="Create hardlinks instead of copying. Preserves seeding but archives won't be extracted. Don't use if destination is a library ingest folder.",
            default=False,
            universal_only=True,
            show_when={
                "field": "BOOKS_OUTPUT_MODE",
                "value": "folder",
            },
        ),
        HeadingField(
            key="booklore_heading",
            title="Grimmory",
            description="Upload books directly to Grimmory (Formerly Booklore) via API. Audiobooks always use folder mode.",
            show_when={"field": "BOOKS_OUTPUT_MODE", "value": "booklore"},
        ),
        TextField(
            key="BOOKLORE_HOST",
            label="Grimmory URL",
            description="Base URL of your Grimmory instance",
            placeholder="http://booklore:6060",
            required=True,
            show_when={"field": "BOOKS_OUTPUT_MODE", "value": "booklore"},
        ),
        TextField(
            key="BOOKLORE_USERNAME",
            label="Username",
            description="Grimmory account username",
            required=True,
            show_when={"field": "BOOKS_OUTPUT_MODE", "value": "booklore"},
        ),
        PasswordField(
            key="BOOKLORE_PASSWORD",
            label="Password",
            description="Grimmory account password",
            required=True,
            show_when={"field": "BOOKS_OUTPUT_MODE", "value": "booklore"},
        ),
        SelectField(
            key="BOOKLORE_DESTINATION",
            label="Upload Destination",
            description="Choose whether uploads go directly to a specific library path or to Bookdrop for review.",
            options=[
                {
                    "value": "library",
                    "label": "Specific Library",
                    "description": "Upload directly into the selected library path.",
                },
                {
                    "value": "bookdrop",
                    "label": "Bookdrop",
                    "description": "Upload into Bookdrop and review metadata before importing to a library.",
                },
            ],
            default="library",
            show_when={"field": "BOOKS_OUTPUT_MODE", "value": "booklore"},
        ),
        SelectField(
            key="BOOKLORE_LIBRARY_ID",
            label="Library",
            description="Grimmory library to upload into.",
            options=get_booklore_library_options,
            required=True,
            user_overridable=True,
            show_when=[
                {"field": "BOOKS_OUTPUT_MODE", "value": "booklore"},
                {"field": "BOOKLORE_DESTINATION", "value": "library"},
            ],
        ),
        SelectField(
            key="BOOKLORE_PATH_ID",
            label="Path",
            description="Grimmory library path for uploads.",
            options=get_booklore_path_options,
            required=True,
            filter_by_field="BOOKLORE_LIBRARY_ID",
            user_overridable=True,
            show_when=[
                {"field": "BOOKS_OUTPUT_MODE", "value": "booklore"},
                {"field": "BOOKLORE_DESTINATION", "value": "library"},
            ],
        ),
        ActionButton(
            key="test_booklore",
            label="Test Connection",
            description="Verify your Grimmory configuration",
            style="primary",
            callback=check_booklore_connection,
            show_when={"field": "BOOKS_OUTPUT_MODE", "value": "booklore"},
        ),
        HeadingField(
            key="email_heading",
            title="Email",
            description="Send books as email attachments via SMTP. Audiobooks always use folder mode.",
            show_when={"field": "BOOKS_OUTPUT_MODE", "value": "email"},
        ),
        TextField(
            key="EMAIL_RECIPIENT",
            label="Default Email Recipient",
            description="Optional fallback email address when no per-user email recipient override is configured.",
            placeholder="reader@example.com",
            user_overridable=True,
            show_when={"field": "BOOKS_OUTPUT_MODE", "value": "email"},
        ),
        NumberField(
            key="EMAIL_ATTACHMENT_SIZE_LIMIT_MB",
            label="Attachment Size Limit (MB)",
            description="Maximum total attachment size per email. Email encoding adds overhead; keep this below your provider's limit.",
            default=25,
            min_value=1,
            max_value=600,
            show_when={"field": "BOOKS_OUTPUT_MODE", "value": "email"},
        ),
        TextField(
            key="EMAIL_SMTP_HOST",
            label="SMTP Host",
            description="SMTP server hostname or IP (e.g., smtp.gmail.com).",
            placeholder="smtp.example.com",
            required=True,
            show_when={"field": "BOOKS_OUTPUT_MODE", "value": "email"},
        ),
        NumberField(
            key="EMAIL_SMTP_PORT",
            label="SMTP Port",
            description="SMTP server port (587 is typical for STARTTLS, 465 for SSL).",
            default=587,
            min_value=1,
            max_value=65535,
            show_when={"field": "BOOKS_OUTPUT_MODE", "value": "email"},
        ),
        SelectField(
            key="EMAIL_SMTP_SECURITY",
            label="SMTP Security",
            description="Transport security mode for SMTP.",
            options=[
                {"value": "none", "label": "None", "description": "No TLS (not recommended)."},
                {
                    "value": "starttls",
                    "label": "STARTTLS",
                    "description": "Upgrade to TLS after connecting (recommended).",
                },
                {"value": "ssl", "label": "SSL/TLS", "description": "Connect using TLS (SMTPS)."},
            ],
            default="starttls",
            show_when={"field": "BOOKS_OUTPUT_MODE", "value": "email"},
        ),
        TextField(
            key="EMAIL_SMTP_USERNAME",
            label="Username",
            description="SMTP username (leave empty for no authentication).",
            placeholder="user@example.com",
            show_when={"field": "BOOKS_OUTPUT_MODE", "value": "email"},
        ),
        PasswordField(
            key="EMAIL_SMTP_PASSWORD",
            label="Password",
            description="SMTP password (required if Username is set).",
            show_when={"field": "BOOKS_OUTPUT_MODE", "value": "email"},
        ),
        TextField(
            key="EMAIL_FROM",
            label="From Address",
            description="From address used for the email. You can include a display name (e.g., Shelfmark <mail@example.com>). Leave blank to default to the SMTP username (when it is an email address).",
            placeholder="Shelfmark <mail@example.com>",
            show_when={"field": "BOOKS_OUTPUT_MODE", "value": "email"},
        ),
        TextField(
            key="EMAIL_SUBJECT_TEMPLATE",
            label="Subject Template",
            description="Email subject. Variables: {Author}, {Title}, {PrimaryTitle}, {Year}, {Series}, {SeriesPosition}, {Subtitle}, {Format}.",
            default="{Title}",
            placeholder="{Title}",
            show_when={"field": "BOOKS_OUTPUT_MODE", "value": "email"},
        ),
        NumberField(
            key="EMAIL_SMTP_TIMEOUT_SECONDS",
            label="SMTP Timeout (seconds)",
            description="How long to wait for SMTP operations before failing.",
            default=60,
            min_value=1,
            max_value=600,
            show_when={"field": "BOOKS_OUTPUT_MODE", "value": "email"},
        ),
        CheckboxField(
            key="EMAIL_ALLOW_UNVERIFIED_TLS",
            label="Allow Unverified TLS",
            description="Disable TLS certificate verification (not recommended).",
            default=False,
            show_when={"field": "BOOKS_OUTPUT_MODE", "value": "email"},
        ),
        ActionButton(
            key="test_email",
            label="Test SMTP Connection",
            description="Verify your SMTP configuration (connect + optional login).",
            style="primary",
            callback=check_email_connection,
            show_when={"field": "BOOKS_OUTPUT_MODE", "value": "email"},
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
            description="Directory where downloaded audiobook files are saved. Leave empty to use the Books destination.",
            user_overridable=True,
            universal_only=True,
        ),
        ActionButton(
            key="test_destination_audiobook",
            label="Test Destination",
            description="Check that Shelfmark can create and write to this audiobook destination.",
            style="primary",
            callback=check_audiobook_destination,
            universal_only=True,
        ),
        SelectField(
            key="FILE_ORGANIZATION_AUDIOBOOK",
            label="File Organization",
            description="Choose how downloaded audiobook files are named and organized.",
            options=[
                {
                    "value": "none",
                    "label": "None",
                    "description": "Keep original filename from source",
                },
                {
                    "value": "rename",
                    "label": "Rename Only",
                    "description": "Rename single-file downloads; multi-file keeps original names.",
                },
                {
                    "value": "organize",
                    "label": "Rename and Organize",
                    "description": "Create folders and rename files using a template. Recommended for Audiobookshelf. Do not use with ingest folders.",
                },
                {
                    "value": "rename_and_group",
                    "label": "Rename and Group",
                    "description": "Rename single-file downloads; keep multi-file downloads grouped in their source folder. Do not use with ingest folders.",
                },
            ],
            default="rename",
            universal_only=True,
        ),
        # Rename mode template - filename only
        _naming_template_field(
            key="TEMPLATE_AUDIOBOOK_RENAME",
            label="Naming Template",
            description=(
                "Variables: {Author}, {FirstAuthor} (first of several authors), {Title}, {Year}, {Language}, {User}, {OriginalName} "
                "(source filename without extension), {Series}, {SeriesPosition}, {Subtitle}, "
                "{PrimaryTitle}, {PartNumber}. Use arbitrary prefix/suffix: "
                "{Vol. SeriesPosition - } outputs 'Vol. 2 - ' when set, nothing when empty. "
                "Rename templates are filename-only (no '/' or '\\'); use Organize for folders. "
                "Applies to single-file downloads."
            ),
            default="{Author} - {Title}",
            placeholder="{Author} - {Title}{ - Part }{PartNumber}",
            show_when={
                "field": "FILE_ORGANIZATION_AUDIOBOOK",
                "value": ["rename", "rename_and_group"],
            },
            universal_only=True,
        ),
        # Organize mode template - folders allowed
        _naming_template_field(
            key="TEMPLATE_AUDIOBOOK_ORGANIZE",
            label="Path Template",
            description=(
                "Use / to create folders. Variables: {Author}, {FirstAuthor} (first of several authors), {Title}, {Year}, {Language}, {User}, "
                "{OriginalName} (source filename without extension), {Series}, {SeriesPosition}, "
                "{Subtitle}, {PrimaryTitle}, {PartNumber}. Use arbitrary prefix/suffix: "
                "{Vol. SeriesPosition - } outputs 'Vol. 2 - ' when set, nothing when empty."
            ),
            default="{Author}/{Title}/{Title}",
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
        ),
        MultiSelectField(
            key="DOWNLOAD_TO_BROWSER_CONTENT_TYPES",
            label="Download to Browser",
            description="Automatically download completed files to your browser for the selected content types.",
            options=_DOWNLOAD_TO_BROWSER_CONTENT_TYPE_OPTIONS,
            default=[],
            variant="dropdown",
            user_overridable=True,
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


# Register the on_save handler for this tab
register_on_save("downloads", _on_save_downloads)


def _get_fast_source_options() -> list[dict[str, str | bool | int | None]]:
    """Fast download sources - configurable list shown in settings."""
    from shelfmark.core.config import config
    from shelfmark.core.mirrors import get_download_source_missing_mirror_reason

    has_donator_key = bool(config.get("AA_DONATOR_KEY", ""))
    aa_fast_reason = get_download_source_missing_mirror_reason("aa-fast")
    if not aa_fast_reason and not has_donator_key:
        aa_fast_reason = "Requires Donator Key"
    libgen_reason = get_download_source_missing_mirror_reason("libgen")

    return [
        {
            "id": "aa-fast",
            "label": "AA Fast Downloads",
            "description": "Fast downloads for donators",
            "isPinned": True,
            "isLocked": aa_fast_reason is not None,
            "disabledReason": aa_fast_reason,
        },
        {
            "id": "libgen",
            "label": "Library Genesis",
            "description": "Instant downloads, no bypass needed",
            "isPinned": True,
            "isLocked": libgen_reason is not None,
            "disabledReason": libgen_reason,
        },
    ]


def _get_fast_source_defaults() -> list[dict[str, str | bool]]:
    """Default values for fast sources display."""
    return [
        {"id": "aa-fast", "enabled": True},
        {"id": "libgen", "enabled": True},
    ]


def _get_slow_source_options() -> list[dict[str, str | bool | None]]:
    """Slow download sources - configurable order. All require bypasser."""
    from shelfmark.core.config import config
    from shelfmark.core.mirrors import get_download_source_missing_mirror_reason

    bypass_enabled = config.get("USE_CF_BYPASS", True)

    def _get_reason(source_id: str) -> str | None:
        mirror_reason = get_download_source_missing_mirror_reason(source_id)
        if mirror_reason:
            return mirror_reason
        if not bypass_enabled:
            return "Requires Cloudflare bypass"
        return None

    return [
        {
            "id": "aa-slow-nowait",
            "label": "AA Slow Downloads (No Waitlist)",
            "description": "Partner servers",
            "isLocked": _get_reason("aa-slow-nowait") is not None,
            "disabledReason": _get_reason("aa-slow-nowait"),
        },
        {
            "id": "aa-slow-wait",
            "label": "AA Slow Downloads (Waitlist)",
            "description": "Partner servers with countdown timer",
            "isLocked": _get_reason("aa-slow-wait") is not None,
            "disabledReason": _get_reason("aa-slow-wait"),
        },
        {
            "id": "welib",
            "label": "Welib",
            "description": "Alternative mirror",
            "isLocked": _get_reason("welib") is not None,
            "disabledReason": _get_reason("welib"),
        },
        {
            "id": "zlib",
            "label": "Zlib",
            "description": "Alternative mirror",
            "isLocked": _get_reason("zlib") is not None,
            "disabledReason": _get_reason("zlib"),
        },
    ]


def _get_slow_source_defaults() -> list[dict[str, str | bool]]:
    """Default source priority order for slow sources."""
    from shelfmark.config.env import _LEGACY_ALLOW_USE_WELIB

    return [
        {"id": "aa-slow-nowait", "enabled": True},
        {"id": "aa-slow-wait", "enabled": True},
        {"id": "welib", "enabled": _LEGACY_ALLOW_USE_WELIB},
        {"id": "zlib", "enabled": True},
    ]


@register_settings(
    "download_sources", "Download Sources", icon="download", order=21, group="direct_download"
)
def download_source_settings() -> list[SettingsField]:
    """Return settings for download source behavior."""
    return [
        CheckboxField(
            key="DIRECT_DOWNLOAD_ENABLED",
            label="Enable Direct Download Source",
            description=(
                "Show Direct Download in release-source lists and allow Direct mode "
                "searches. Add your own mirror URLs in the Mirrors tab before using it."
            ),
            default=False,
        ),
        CheckboxField(
            key="DIRECT_DOWNLOAD_LANGUAGE_FROM_PATH",
            label="Detect Language From Distant Path",
            description=(
                "When language metadata is missing or unknown, parse the distant path "
                "(file path shown in search results) for language tags like [BD FR] or [En]. "
                "Also enables local language filtering so lgli files without AA language "
                "metadata are not excluded before the distant path can be checked."
            ),
            default=False,
        ),
        PasswordField(
            key="AA_DONATOR_KEY",
            label="Account Donator Key",
            description="Enables fast download access on AA. Get this from your donator account page.",
        ),
        HeadingField(
            key="source_priority_heading",
            title="Source Priority",
            description="Sources are tried in order until a download succeeds. Mirror-backed entries unlock automatically when you configure their mirrors.",
        ),
        OrderableListField(
            key="FAST_SOURCES_DISPLAY",
            label="Fast downloads",
            description="Always tried first, no waiting or bypass required.",
            options=_get_fast_source_options,
            default=_get_fast_source_defaults(),
        ),
        OrderableListField(
            key="SOURCE_PRIORITY",
            label="Slow downloads",
            description="Fallback sources, may have waiting. Requires bypasser. Drag to reorder.",
            options=_get_slow_source_options,
            default=_get_slow_source_defaults(),
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
        NumberField(
            key="RELEASE_SEARCH_TIMEOUT",
            label="Release Search Timeout (seconds)",
            description=(
                "How long one release search may run before it gives up and reports why. "
                "A first search on a cold start pays for a browser solve, so leave room "
                "for one. If you use a reverse proxy, its read timeout should be at least "
                "this high or it will cut the search off with a 504 first."
            ),
            default=300,
            min_value=30,
            max_value=1800,
        ),
        HeadingField(
            key="content_type_routing_heading",
            title="Content-Type Routing",
            description="Route downloads to different folders based on content type. Only applies to Direct download source.",
        ),
        CheckboxField(
            key="AA_CONTENT_TYPE_ROUTING",
            label="Enable Content-Type Routing",
            description="Override destination based on content type metadata.",
            default=False,
        ),
        TextField(
            key="AA_CONTENT_TYPE_DIR_FICTION",
            label="Fiction Books",
            placeholder="/books/fiction",
            show_when={"field": "AA_CONTENT_TYPE_ROUTING", "value": True},
        ),
        TextField(
            key="AA_CONTENT_TYPE_DIR_NON_FICTION",
            label="Non-Fiction Books",
            placeholder="/books/non-fiction",
            show_when={"field": "AA_CONTENT_TYPE_ROUTING", "value": True},
        ),
        TextField(
            key="AA_CONTENT_TYPE_DIR_UNKNOWN",
            label="Unknown Books",
            placeholder="/books/unknown",
            show_when={"field": "AA_CONTENT_TYPE_ROUTING", "value": True},
        ),
        TextField(
            key="AA_CONTENT_TYPE_DIR_MAGAZINE",
            label="Magazines",
            placeholder="/books/magazines",
            show_when={"field": "AA_CONTENT_TYPE_ROUTING", "value": True},
        ),
        TextField(
            key="AA_CONTENT_TYPE_DIR_COMIC",
            label="Comic Books",
            placeholder="/books/comics",
            show_when={"field": "AA_CONTENT_TYPE_ROUTING", "value": True},
        ),
        TextField(
            key="AA_CONTENT_TYPE_DIR_STANDARDS",
            label="Standards Documents",
            placeholder="/books/standards",
            show_when={"field": "AA_CONTENT_TYPE_ROUTING", "value": True},
        ),
        TextField(
            key="AA_CONTENT_TYPE_DIR_MUSICAL_SCORE",
            label="Musical Scores",
            placeholder="/books/scores",
            show_when={"field": "AA_CONTENT_TYPE_ROUTING", "value": True},
        ),
        TextField(
            key="AA_CONTENT_TYPE_DIR_OTHER",
            label="Other",
            placeholder="/books/other",
            show_when={"field": "AA_CONTENT_TYPE_ROUTING", "value": True},
        ),
    ]


@register_settings(
    "cloudflare_bypass", "Cloudflare Bypass", icon="shield", order=22, group="direct_download"
)
def cloudflare_bypass_settings() -> list[SettingsField]:
    """Return settings for Cloudflare bypass behavior."""
    return [
        CheckboxField(
            key="USE_CF_BYPASS",
            label="Enable Cloudflare Bypass",
            description="Attempt to bypass Cloudflare protection on download sites.",
            default=True,
            requires_restart=True,
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
        NumberField(
            key="BYPASS_PAGE_SOURCE_TIMEOUT",
            label="Page Read Timeout (seconds)",
            description=(
                "How long to wait for a solved page to produce its content before the "
                "bypass is retried. Raise it if solves succeed but searches still fail."
            ),
            default=20,
            min_value=1,
            max_value=120,
            show_when={"field": "USING_EXTERNAL_BYPASSER", "value": False},
        ),
        NumberField(
            key="BYPASS_BROWSER_IDLE_TIMEOUT",
            label="Bypasser Idle Timeout (seconds)",
            description=(
                "How long the bypass helper process may sit unused before it is shut down. "
                "Higher keeps more searches fast, lower frees memory sooner."
            ),
            default=180,
            min_value=30,
            max_value=3600,
            requires_restart=True,
            show_when={"field": "USING_EXTERNAL_BYPASSER", "value": False},
        ),
    ]


def _on_save_mirrors(values: dict[str, Any]) -> dict[str, Any]:
    """Normalize mirror list settings before persisting."""
    from shelfmark.core.utils import normalize_http_url

    mirror_list_keys = {
        "AA_MIRROR_URLS",
        "LIBGEN_MIRROR_URLS",
        "ZLIB_MIRROR_URLS",
        "WELIB_MIRROR_URLS",
    }

    for key in mirror_list_keys:
        raw_urls = values.get(key)
        if raw_urls is None:
            continue

        if isinstance(raw_urls, str):
            parts = [p.strip() for p in raw_urls.split(",") if p.strip()]
        elif isinstance(raw_urls, list):
            parts = [str(p).strip() for p in raw_urls if str(p).strip()]
        else:
            parts = []

        normalized: list[str] = []
        for url in parts:
            if url.lower() == "auto":
                continue
            norm = normalize_http_url(url, default_scheme="https")
            if norm and norm not in normalized:
                normalized.append(norm)

        values[key] = normalized

    return {"error": False, "values": values}


# Register the on_save handler for this tab
register_on_save("mirrors", _on_save_mirrors)


@register_settings("mirrors", "Mirrors", icon="globe", order=23, group="direct_download")
def mirror_settings() -> list[SettingsField]:
    """Configure download source mirrors."""
    return [
        # === PRIMARY SOURCE ===
        HeadingField(
            key="aa_mirrors_heading",
            title="Anna's Archive",
            description=(
                "Add your own Anna's Archive mirror URLs here. Auto mode will try them in the "
                "order listed below."
            ),
        ),
        SelectField(
            key="AA_BASE_URL",
            label="Primary Mirror",
            description=(
                "Select Auto to try mirrors from your list on startup and fail over on errors. "
                "Choosing a specific mirror pins Shelfmark to that URL."
            ),
            options=_get_aa_base_url_options,
            default="auto",
        ),
        TagListField(
            key="AA_MIRROR_URLS",
            label="Mirrors",
            description=(
                "List the Anna's Archive mirror URLs you want Shelfmark to use. Type a URL and "
                "press Enter to add it. Order matters when Auto is selected."
            ),
            placeholder="https://your-aa-mirror.example",
            default=[],
        ),
        # === LIBGEN ===
        TagListField(
            key="LIBGEN_MIRROR_URLS",
            label="LibGen",
            description="Mirrors are tried in the order you add them until one works.",
            placeholder="https://your-libgen-mirror.example",
        ),
        # === Z-LIBRARY ===
        TagListField(
            key="ZLIB_MIRROR_URLS",
            label="Z-Library",
            description="Only the first mirror in the list is used.",
            placeholder="https://your-zlibrary-mirror.example",
        ),
        # === WELIB ===
        TagListField(
            key="WELIB_MIRROR_URLS",
            label="Welib",
            description="Only the first mirror in the list is used.",
            placeholder="https://your-welib-mirror.example",
        ),
    ]


@register_settings("advanced", "Advanced", icon="cog", order=15)
def advanced_settings() -> list[SettingsField]:
    """Advanced settings for power users."""
    return [
        TextField(
            key="URL_BASE",
            label="Base Path",
            description="Optional URL path prefix. Use a path like /shelfmark (no hostname). Leave blank for root.",
            placeholder="/shelfmark",
            requires_restart=True,
        ),
        CheckboxField(
            key="DEBUG",
            label="Debug Mode",
            description="Enable verbose logging to console and file. Not recommended for normal use.",
            default=False,
            requires_restart=True,
        ),
        SelectField(
            key="LOG_LEVEL",
            label="Log Level",
            description=(
                "Lowest severity written to the console and log file. "
                "Ignored while Debug Mode is on, which forces Debug."
            ),
            options=[
                {"value": "DEBUG", "label": "Debug", "description": "Everything, very noisy."},
                {"value": "INFO", "label": "Info", "description": "Normal activity (default)."},
                {"value": "WARNING", "label": "Warning", "description": "Warnings and problems."},
                {"value": "ERROR", "label": "Error", "description": "Failures only."},
                {"value": "CRITICAL", "label": "Critical", "description": "Fatal errors only."},
            ],
            default="INFO",
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
        TextField(
            key="CUSTOM_SCRIPT",
            label="Custom Script Path",
            description="Path to a script to run after each successful download. Must be executable.",
            placeholder="/path/to/script.sh",
        ),
        SelectField(
            key="CUSTOM_SCRIPT_PATH_MODE",
            label="Custom Script Path Mode",
            description="Pass the path to the custom script as an absolute path or relative to the destination folder.",
            options=[
                {
                    "value": "absolute",
                    "label": "Absolute",
                    "description": "Pass the full destination path (default).",
                },
                {
                    "value": "relative",
                    "label": "Relative",
                    "description": "Pass the path relative to the destination folder.",
                },
            ],
            default="absolute",
        ),
        CheckboxField(
            key="CUSTOM_SCRIPT_JSON_PAYLOAD",
            label="Custom Script JSON Payload",
            description="Send a JSON payload to the script via stdin. Useful for multi-file imports (audiobooks) or richer metadata without relying on path parsing.",
            default=False,
        ),
        HeadingField(
            key="remote_path_mappings_heading",
            title="Remote Path Mappings",
            description="Map download client paths to paths inside Shelfmark. Needed when volume mounts differ between containers.",
        ),
        NumberField(
            key="DOWNLOAD_CLIENT_COMPLETED_PATH_TIMEOUT",
            label="Completed Path Wait (seconds)",
            description=(
                "How long to wait after a torrent or usenet client reports completion "
                "for the completed file path to become visible to Shelfmark. Increase "
                "this for seedbox or remote-sync workflows."
            ),
            default=_DOWNLOAD_CLIENT_COMPLETED_PATH_TIMEOUT_DEFAULT,
            min_value=0,
            max_value=_DOWNLOAD_CLIENT_COMPLETED_PATH_TIMEOUT_MAX,
        ),
        TableField(
            key="PROWLARR_REMOTE_PATH_MAPPINGS",
            label="Path Mappings",
            columns=[
                {
                    "key": "host",
                    "label": "Client",
                    "type": "select",
                    "options": [
                        {"value": "qbittorrent", "label": "qBittorrent"},
                        {"value": "transmission", "label": "Transmission"},
                        {"value": "deluge", "label": "Deluge"},
                        {"value": "rtorrent", "label": "rTorrent"},
                        {"value": "nzbget", "label": "NZBGet"},
                        {"value": "sabnzbd", "label": "SABnzbd"},
                    ],
                    "defaultValue": "qbittorrent",
                },
                {
                    "key": "remotePath",
                    "label": "Remote Path",
                    "type": "path",
                },
                {
                    "key": "localPath",
                    "label": "Local Path",
                    "type": "path",
                },
            ],
            default=[],
            add_label="Add Mapping",
            empty_message="No mappings configured.",
            env_supported=False,
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


register_on_save("advanced", _on_save_advanced)
