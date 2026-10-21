"""Jackett settings registration."""

from typing import Any

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
from shelfmark.release_sources.jackett.api import _JACKETT_CLIENT_ERRORS
from shelfmark.release_sources.prowlarr.api import (
    DEFAULT_INDEXER_TIMEOUT_SECONDS,
    MAX_INDEXER_TIMEOUT_SECONDS,
    MIN_INDEXER_TIMEOUT_SECONDS,
)
from shelfmark.release_sources.prowlarr.settings import _resolve_setting_text

_SETTINGS_ERRORS = (*_JACKETT_CLIENT_ERRORS, AttributeError)


def _get_indexer_options() -> list[dict[str, str]]:
    """Fetch configured indexers from Jackett for the multi-select field."""
    from shelfmark.core.config import config
    from shelfmark.core.logger import setup_logger
    from shelfmark.release_sources.jackett.api import JackettClient

    raw_url = normalize_optional_text(config.get("JACKETT_URL", "")) or ""
    api_key = normalize_optional_text(config.get("JACKETT_API_KEY", "")) or ""
    url = normalize_http_url(raw_url) if raw_url else ""
    if not url or not api_key:
        return []

    try:
        indexers = JackettClient(url, api_key).get_indexers(raise_on_error=True)
    except _SETTINGS_ERRORS:
        setup_logger(__name__).exception("Failed to fetch Jackett indexers")
        return []

    return [{"value": idx["id"], "label": idx["name"]} for idx in indexers]


def _test_jackett_connection(current_values: dict[str, Any] | None = None) -> dict[str, Any]:
    """Test the Jackett connection using current form values."""
    from shelfmark.release_sources.jackett.api import JackettClient

    current_values = current_values or {}

    raw_url = _resolve_setting_text(current_values, "JACKETT_URL")
    api_key = _resolve_setting_text(current_values, "JACKETT_API_KEY")

    if not raw_url:
        return {"success": False, "message": "Jackett URL is required"}

    url = normalize_http_url(raw_url)
    if not url:
        return {"success": False, "message": "Jackett URL is invalid"}
    if not api_key:
        return {"success": False, "message": "API key is required"}

    try:
        success, message = JackettClient(url, api_key).test_connection()
    except _SETTINGS_ERRORS as e:
        return {"success": False, "message": f"Connection failed: {e!s}"}
    else:
        return {"success": success, "message": message}


@register_settings(
    name="jackett_config",
    display_name="Jackett",
    icon="download",
    order=42,
)
def jackett_config_settings() -> list[SettingsField]:
    """Jackett connection and indexer settings."""
    enabled = {"field": "JACKETT_ENABLED", "value": True}
    return [
        HeadingField(
            key="jackett_heading",
            title="Jackett Integration",
            description="Search for books across your torrent indexers via Jackett.",
            link_url="https://github.com/Jackett/Jackett",
            link_text="github.com/Jackett/Jackett",
        ),
        CheckboxField(
            key="JACKETT_ENABLED",
            label="Enable Jackett source",
            default=False,
            description="Enable searching for books via Jackett indexers",
        ),
        TextField(
            key="JACKETT_URL",
            label="Jackett URL",
            description="Base URL of your Jackett instance",
            placeholder="http://jackett:9117",
            required=True,
            show_when=enabled,
        ),
        PasswordField(
            key="JACKETT_API_KEY",
            label="API Key",
            description="Shown at the top right of the Jackett dashboard",
            required=True,
            show_when=enabled,
        ),
        ActionButton(
            key="test_jackett",
            label="Test Connection",
            description="Verify your Jackett configuration",
            style="primary",
            callback=_test_jackett_connection,
            show_when=enabled,
        ),
        MultiSelectField(
            key="JACKETT_INDEXERS",
            label="Indexers to Search",
            description="Select which indexers to search. Leave empty to search all configured indexers.",
            options=_get_indexer_options,
            default=[],
            show_when=enabled,
        ),
        NumberField(
            key="JACKETT_INDEXER_TIMEOUT",
            label="Indexer Search Timeout (seconds)",
            description=(
                "How long to wait for Jackett to answer a search. Searching all indexers waits "
                "for the slowest one, and indexers behind FlareSolverr can need 90 seconds or "
                "more on a cold Cloudflare challenge."
            ),
            default=DEFAULT_INDEXER_TIMEOUT_SECONDS,
            min_value=MIN_INDEXER_TIMEOUT_SECONDS,
            max_value=MAX_INDEXER_TIMEOUT_SECONDS,
            show_when=enabled,
        ),
        NumberField(
            key="JACKETT_EBOOK_MAX_SIZE_MB",
            label="Maximum Ebook Size (MB)",
            description=(
                "Hide ebook results larger than this. 0 shows everything, and a result with no "
                "reported size is always shown. Audiobook searches are not limited."
            ),
            default=0,
            min_value=0,
            max_value=100000,
            show_when=enabled,
        ),
        CheckboxField(
            key="JACKETT_AUTO_EXPAND",
            label="Auto-expand search on no results",
            default=False,
            description="Automatically retry search without category filtering if no results are found",
            show_when=enabled,
        ),
        CheckboxField(
            key="JACKETT_HIDE_UNSUPPORTED_FORMATS",
            label="Hide results in formats you did not enable",
            default=True,
            description=(
                "Hide a result whose title ends in a file extension that is not in Supported "
                "Book Formats, such as a .doc or .rtf file. Titles without an extension, "
                "archives and audiobook searches are never hidden."
            ),
            show_when=enabled,
        ),
    ]
