"""Prowlarr settings registration."""

from typing import Any

import requests

from shelfmark.core.request_helpers import normalize_optional_text
from shelfmark.core.settings_registry import (
    ActionButton,
    CheckboxField,
    HeadingField,
    MultiSelectField,
    NumberField,
    PasswordField,
    SettingsField,
    TextField,
    register_settings,
)
from shelfmark.core.utils import normalize_http_url
from shelfmark.release_sources.prowlarr.api import (
    DEFAULT_INDEXER_TIMEOUT_SECONDS,
    MAX_INDEXER_TIMEOUT_SECONDS,
    MIN_INDEXER_TIMEOUT_SECONDS,
)

# ==================== Dynamic Options Loaders ====================

_PROWLARR_SETTINGS_ERRORS = (
    requests.exceptions.RequestException,
    AttributeError,
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
)


def _resolve_setting_text(current_values: dict[str, Any], key: str, *, default: str = "") -> str:
    """Prefer current form values, then fall back to persisted config text."""
    from shelfmark.core.config import config

    current_value = normalize_optional_text(current_values.get(key))
    if current_value is not None:
        return current_value

    config_value = normalize_optional_text(config.get(key, default))
    if config_value is not None:
        return config_value

    return default


def _get_indexer_options() -> list[dict[str, str]]:
    """Fetch available indexers from Prowlarr for the multi-select field.

    Returns list of {value: "id", label: "name (protocol)"} options.
    """
    from shelfmark.core.config import config
    from shelfmark.core.logger import setup_logger

    logger = setup_logger(__name__)

    raw_url = normalize_optional_text(config.get("PROWLARR_URL", "")) or ""
    api_key = normalize_optional_text(config.get("PROWLARR_API_KEY", "")) or ""

    if not raw_url or not api_key:
        return []

    url = normalize_http_url(raw_url)
    if not url:
        return []

    try:
        from shelfmark.release_sources.prowlarr.api import ProwlarrClient

        client = ProwlarrClient(url, api_key)
        indexers = client.get_enabled_indexers()

        options = []
        for idx in indexers:
            idx_id = idx.get("id")
            name = idx.get("name", "Unknown")
            protocol = idx.get("protocol", "")
            has_books = idx.get("has_books", False)

            # Add indicator for book support
            label = f"{name} ({protocol})"
            if has_books:
                label += " 📚"

            options.append(
                {
                    "value": str(idx_id),
                    "label": label,
                }
            )

    except _PROWLARR_SETTINGS_ERRORS:
        logger.exception("Failed to fetch Prowlarr indexers")
        return []

    else:
        return options


# ==================== Test Connection Callback ====================


def _test_prowlarr_connection(current_values: dict[str, Any] | None = None) -> dict[str, Any]:
    """Test the Prowlarr connection using current form values."""
    from shelfmark.release_sources.prowlarr.api import ProwlarrClient

    current_values = current_values or {}

    raw_url = _resolve_setting_text(current_values, "PROWLARR_URL")
    api_key = _resolve_setting_text(current_values, "PROWLARR_API_KEY")

    if not raw_url:
        return {"success": False, "message": "Prowlarr URL is required"}

    url = normalize_http_url(raw_url)
    if not url:
        return {"success": False, "message": "Prowlarr URL is invalid"}
    if not api_key:
        return {"success": False, "message": "API key is required"}

    try:
        client = ProwlarrClient(url, api_key)
        success, message = client.test_connection()
    except _PROWLARR_SETTINGS_ERRORS as e:
        return {"success": False, "message": f"Connection failed: {e!s}"}
    else:
        return {"success": success, "message": message}


def _test_mam_connection(current_values: dict[str, Any] | None = None) -> dict[str, Any]:
    """Check the MAM session ID by asking MAM which account it belongs to."""
    from shelfmark.release_sources.prowlarr.mam import MamClient, MamError, reset_failures

    current_values = current_values or {}
    mam_id = _resolve_setting_text(current_values, "PROWLARR_MAM_ID")
    if not mam_id:
        return {"success": False, "message": "MAM session ID is required"}

    try:
        username = MamClient(mam_id).get_username()
    except MamError as e:
        return {"success": False, "message": str(e)}
    except _PROWLARR_SETTINGS_ERRORS as e:
        return {"success": False, "message": f"Connection failed: {e!s}"}

    if not username:
        return {"success": False, "message": "MyAnonamouse did not return an account for this ID"}
    # A working session ends any back-off from earlier failed lookups.
    reset_failures()
    return {"success": True, "message": f"Connected to MyAnonamouse as {username}"}


# ==================== Configuration Tab ====================


@register_settings(
    name="prowlarr_config",
    display_name="Prowlarr",
    icon="download",
    order=41,
)
def prowlarr_config_settings() -> list[SettingsField]:
    """Prowlarr connection and indexer settings."""
    return [
        HeadingField(
            key="prowlarr_heading",
            title="Prowlarr Integration",
            description="Search for books across your indexers via Prowlarr.",
            link_url="https://prowlarr.com",
            link_text="prowlarr.com",
        ),
        CheckboxField(
            key="PROWLARR_ENABLED",
            label="Enable Prowlarr source",
            default=False,
            description="Enable searching for books via Prowlarr indexers",
        ),
        TextField(
            key="PROWLARR_URL",
            label="Prowlarr URL",
            description="Base URL of your Prowlarr instance",
            placeholder="http://prowlarr:9696",
            required=True,
            show_when={"field": "PROWLARR_ENABLED", "value": True},
        ),
        PasswordField(
            key="PROWLARR_API_KEY",
            label="API Key",
            description="Found in Prowlarr: Settings > General > API Key",
            required=True,
            show_when={"field": "PROWLARR_ENABLED", "value": True},
        ),
        ActionButton(
            key="test_prowlarr",
            label="Test Connection",
            description="Verify your Prowlarr configuration",
            style="primary",
            callback=_test_prowlarr_connection,
            show_when={"field": "PROWLARR_ENABLED", "value": True},
        ),
        MultiSelectField(
            key="PROWLARR_INDEXERS",
            label="Indexers to Search",
            description="Select which indexers to search. 📚 = has book categories. Leave empty to search all.",
            options=_get_indexer_options,
            default=[],
            show_when={"field": "PROWLARR_ENABLED", "value": True},
        ),
        NumberField(
            key="PROWLARR_INDEXER_TIMEOUT",
            label="Indexer Search Timeout (seconds)",
            description=(
                "How long to wait for a single indexer to answer a search. Indexers behind "
                "FlareSolverr can need 90 seconds or more while a cold Cloudflare challenge "
                "is solved; raise this if searches come back empty and the Prowlarr log "
                "shows the search still running."
            ),
            default=DEFAULT_INDEXER_TIMEOUT_SECONDS,
            min_value=MIN_INDEXER_TIMEOUT_SECONDS,
            max_value=MAX_INDEXER_TIMEOUT_SECONDS,
            show_when={"field": "PROWLARR_ENABLED", "value": True},
        ),
        NumberField(
            key="PROWLARR_EBOOK_MAX_SIZE_MB",
            label="Maximum Ebook Size (MB)",
            description=(
                "Hide ebook results larger than this. Expanded searches and general indexers can "
                "return TV series, courses and software next to the books. 0 shows everything, "
                "and a result with no reported size is always shown. Audiobook searches are not "
                "limited."
            ),
            default=0,
            min_value=0,
            max_value=100000,
            show_when={"field": "PROWLARR_ENABLED", "value": True},
        ),
        CheckboxField(
            key="PROWLARR_AUTO_EXPAND",
            label="Auto-expand search on no results",
            default=False,
            description="Automatically retry search without category filtering if no results are found",
            show_when={"field": "PROWLARR_ENABLED", "value": True},
        ),
        CheckboxField(
            key="PROWLARR_COLLAPSE_DUPLICATES",
            label="Show one row per release",
            default=True,
            description=(
                "Collapse a release that several indexer entries returned down to a single row, "
                "keeping the entry with the best Prowlarr priority. Turn this off to see every "
                "entry that carried it, which is what makes results from filter-specific entries "
                "(freeleech and the like) visible."
            ),
            show_when={"field": "PROWLARR_ENABLED", "value": True},
        ),
        CheckboxField(
            key="PROWLARR_HIDE_UNSUPPORTED_FORMATS",
            label="Hide results in formats you did not enable",
            default=True,
            description=(
                "Hide a result whose title ends in a file extension that is not in Supported "
                "Book Formats, such as a .doc or .rtf file. Downloading it would only fail at "
                "post-processing. Titles without an extension, archives and audiobook "
                "searches are never hidden."
            ),
            show_when={"field": "PROWLARR_ENABLED", "value": True},
        ),
        CheckboxField(
            key="PROWLARR_USE_SEED_PREFERENCES",
            label="Use Prowlarr seed preferences",
            default=False,
            description="Apply per-indexer seed time and ratio preferences from Prowlarr when sending torrents to the download client",
            show_when={"field": "PROWLARR_ENABLED", "value": True},
        ),
        HeadingField(
            key="prowlarr_mam_heading",
            title="MyAnonamouse Enrichment",
            description=(
                "Prowlarr drops the narrator and series from MyAnonamouse results. With a MAM "
                "session ID, Shelfmark looks them up (plus bitrate, from the uploader's tags) "
                "directly from MyAnonamouse. Create the session under Preferences > Security "
                "on MyAnonamouse, locked to the IP or ASN Shelfmark connects from. Choose "
                "which columns show under Search Mode > Release List Columns."
            ),
            link_url="https://www.myanonamouse.net/preferences/index.php?view=security",
            link_text="MAM security settings",
            show_when={"field": "PROWLARR_ENABLED", "value": True},
        ),
        PasswordField(
            key="PROWLARR_MAM_ID",
            label="MAM Session ID",
            description=(
                "The mam_id value MyAnonamouse shows when you create a session. MAM locks "
                "each session to one IP or ASN, so reusing Prowlarr's or another client's "
                "session often fails with a 403: you will likely need a separate session "
                "for Shelfmark if it reaches MAM from another IP (different host, VPN or "
                "proxy) or the existing session is ASN-locked to another network. Leave "
                "empty to turn enrichment off."
            ),
            show_when={"field": "PROWLARR_ENABLED", "value": True},
        ),
        ActionButton(
            key="test_prowlarr_mam",
            label="Test MAM Session",
            description=(
                "Check that MyAnonamouse accepts the session ID. A passing test also resumes "
                "enrichment after it stopped on repeated failures."
            ),
            style="primary",
            callback=_test_mam_connection,
            show_when={"field": "PROWLARR_ENABLED", "value": True},
        ),
    ]
