"""Onboarding wizard configuration.

Defines the steps and fields for the first-run onboarding experience.
Reuses field definitions from the settings registry where possible.
"""

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from shelfmark.core.logger import setup_logger
from shelfmark.core.settings_registry import (
    HeadingField,
    MultiSelectField,
    SettingsField,
    get_setting_value,
    get_settings_field_map,
    get_settings_tab,
    save_config_file,
    serialize_field,
)

logger = setup_logger(__name__)


ONBOARDING_STORAGE_KEY = "onboarding_complete"
ONBOARDING_RELEASE_SOURCES_KEY = "ONBOARDING_RELEASE_SOURCES"
_ONBOARDING_VIRTUAL_KEYS = {ONBOARDING_RELEASE_SOURCES_KEY}


def _get_config_dir() -> Path:
    """Get the config directory path."""
    from shelfmark.config.env import CONFIG_DIR

    return Path(CONFIG_DIR)


def is_onboarding_complete() -> bool:
    """Check if onboarding has been completed."""
    from shelfmark.config.env import ONBOARDING

    # If onboarding is disabled via env var, treat as complete
    if not ONBOARDING:
        return True

    config_file = _get_config_dir() / "settings.json"
    if not config_file.exists():
        return False

    try:
        with config_file.open() as f:
            config = json.load(f)
            return config.get(ONBOARDING_STORAGE_KEY, False)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Could not read onboarding status from settings.json: %s", e)
        return False


def mark_onboarding_complete() -> bool:
    """Mark onboarding as complete."""
    try:
        return save_config_file("general", {ONBOARDING_STORAGE_KEY: True})
    except Exception:
        logger.exception("Failed to mark onboarding complete")
        return False


def _get_field_from_tab(tab_name: str, field_key: str) -> SettingsField | None:
    """Extract a specific field from a registered settings tab.

    Args:
        tab_name: Name of the settings tab (e.g., 'search_mode', 'hardcover')
        field_key: Key of the field to extract (e.g., 'SEARCH_MODE', 'HARDCOVER_API_KEY')

    Returns:
        The field if found, None otherwise

    """
    tab = get_settings_tab(tab_name)
    if not tab:
        logger.warning("Settings tab not found: %s", tab_name)
        return None

    for field in tab.fields:
        if hasattr(field, "key") and field.key == field_key:
            return field

    logger.warning("Field %s not found in tab %s", field_key, tab_name)
    return None


def _get_field_tab_name(field: SettingsField, fallback_tab_name: str) -> str:
    """Return the owning settings tab for a value field."""
    field_key = getattr(field, "key", None)
    if not field_key:
        return fallback_tab_name

    field_map = get_settings_field_map()
    field_entry = field_map.get(field_key)
    if field_entry is None:
        return fallback_tab_name

    return field_entry[1]


def _clone_field_with_overrides(field: SettingsField, **overrides: object) -> SettingsField:
    """Clone a field with optional attribute overrides.

    Useful for customizing labels, descriptions, or defaults for onboarding context.
    """
    return replace(field, **overrides)


def _get_fields_from_tab(
    tab_name: str,
    field_keys: list[str],
    *,
    strip_show_when_keys: set[str] | None = None,
) -> list[SettingsField]:
    """Return the requested fields from a settings tab in the supplied order."""
    fields: list[SettingsField] = []
    for field_key in field_keys:
        field = _get_field_from_tab(tab_name, field_key)
        if field:
            show_when = getattr(field, "show_when", None)
            stripped_show_when = _strip_show_when_keys(show_when, strip_show_when_keys or set())
            if stripped_show_when != show_when:
                field = replace(field, show_when=stripped_show_when)
            fields.append(field)
    return fields


def _strip_show_when_keys(
    show_when: dict[str, Any] | list[dict[str, Any]] | None,
    field_keys: set[str],
) -> dict[str, Any] | list[dict[str, Any]] | None:
    """Remove conditions tied to fields that onboarding handles implicitly."""
    if not show_when or not field_keys:
        return show_when

    if isinstance(show_when, list):
        remaining = [
            condition for condition in show_when if condition.get("field") not in field_keys
        ]
        return remaining or None

    if show_when.get("field") in field_keys:
        return None

    return show_when


def _is_release_source_selected(values: dict[str, Any], source_name: str) -> bool:
    """Return True when a release source has been chosen during onboarding."""
    raw_sources = values.get(ONBOARDING_RELEASE_SOURCES_KEY, [])
    if not isinstance(raw_sources, list):
        return False
    return source_name in raw_sources


def _evaluate_show_when_condition(condition: dict[str, Any], values: dict[str, Any]) -> bool:
    """Evaluate one onboarding show_when condition against submitted values."""
    current_value = values.get(condition["field"])
    expected_value = condition.get("value")

    if condition.get("notEmpty"):
        if isinstance(current_value, list):
            return len(current_value) > 0
        return current_value not in (None, "")

    if isinstance(current_value, list):
        if isinstance(expected_value, list):
            return all(item in current_value for item in expected_value)
        return expected_value in current_value

    if isinstance(expected_value, list):
        return current_value in expected_value

    return current_value == expected_value


def _is_step_visible(step_config: dict[str, Any], values: dict[str, Any]) -> bool:
    """Return True when a step should be included for the provided values."""
    show_when = step_config.get("show_when")
    if not show_when:
        return True
    return all(_evaluate_show_when_condition(condition, values) for condition in show_when)


def _is_field_visible(field: SettingsField, values: dict[str, Any]) -> bool:
    """Return True when a field should be included in the onboarding save."""
    if getattr(field, "hidden_in_ui", False):
        return False

    if getattr(field, "universal_only", False) and values.get("SEARCH_MODE") != "universal":
        return False

    show_when = getattr(field, "show_when", None)
    if not show_when:
        return True

    if isinstance(show_when, list):
        return all(_evaluate_show_when_condition(condition, values) for condition in show_when)

    return _evaluate_show_when_condition(show_when, values)


# =============================================================================
# Step Definitions
# =============================================================================


def get_search_mode_fields() -> list[SettingsField]:
    """Step 1: Choose search mode - uses actual SEARCH_MODE field from settings."""
    fields: list[SettingsField] = [
        HeadingField(
            key="welcome_heading",
            title="Welcome to Shelfmark",
            description="Let's configure how you want to search for and download books.",
        ),
    ]

    # Get the actual SEARCH_MODE field from settings
    search_mode_field = _get_field_from_tab("search_mode", "SEARCH_MODE")
    if search_mode_field:
        # Clone with onboarding-specific description
        fields.append(
            _clone_field_with_overrides(
                search_mode_field,
                description="Choose how you want to find books.",
            )
        )

    return fields


def get_metadata_provider_fields() -> list[SettingsField]:
    """Step 2: Choose metadata provider - uses actual METADATA_PROVIDER field."""
    fields: list[SettingsField] = [
        HeadingField(
            key="metadata_heading",
            title="Metadata Provider",
            description="Choose where to search for book information. You can enable more providers in Settings later.",
        ),
    ]

    # Get the actual METADATA_PROVIDER field from settings
    provider_field = _get_field_from_tab("search_mode", "METADATA_PROVIDER")
    if provider_field:
        # Custom options with Hardcover marked as recommended
        onboarding_options = [
            {
                "value": "hardcover",
                "label": "Hardcover (Recommended)",
                "description": "Modern book tracking platform with excellent metadata, ratings, and series information. Requires free API key.",
            },
            {
                "value": "openlibrary",
                "label": "Open Library",
                "description": "Free, open-source library catalog from the Internet Archive. No API key required.",
            },
            {
                "value": "googlebooks",
                "label": "Google Books",
                "description": "Google's book database with good coverage. Requires free API key.",
            },
        ]

        # Clone with onboarding-specific options and default
        fields.append(
            _clone_field_with_overrides(
                provider_field,
                default="hardcover",
                options=onboarding_options,
            )
        )

    return fields


def get_hardcover_setup_fields() -> list[SettingsField]:
    """Step 3a: Configure Hardcover - uses actual API key and test connection fields."""
    fields: list[SettingsField] = [
        HeadingField(
            key="hardcover_setup_heading",
            title="Hardcover Setup",
            description="Get your free API key from hardcover.app/account/api",
            link_url="https://hardcover.app/account/api",
            link_text="Get API Key",
        ),
    ]

    # Get the actual HARDCOVER_API_KEY field
    api_key_field = _get_field_from_tab("hardcover", "HARDCOVER_API_KEY")
    if api_key_field:
        fields.append(api_key_field)

    # Get the test connection button
    test_button = _get_field_from_tab("hardcover", "test_connection")
    if test_button:
        fields.append(test_button)

    return fields


def get_googlebooks_setup_fields() -> list[SettingsField]:
    """Step 3b: Configure Google Books - uses actual API key and test connection fields."""
    fields: list[SettingsField] = [
        HeadingField(
            key="googlebooks_setup_heading",
            title="Google Books Setup",
            description="Get your free API key from Google Cloud Console (APIs & Services > Credentials).",
            link_url="https://console.cloud.google.com/apis/library/books.googleapis.com",
            link_text="Get API Key",
        ),
    ]

    # Get the actual GOOGLEBOOKS_API_KEY field
    api_key_field = _get_field_from_tab("googlebooks", "GOOGLEBOOKS_API_KEY")
    if api_key_field:
        fields.append(api_key_field)

    # Get the test connection button
    test_button = _get_field_from_tab("googlebooks", "test_connection")
    if test_button:
        fields.append(test_button)

    return fields


def get_release_source_selection_fields() -> list[SettingsField]:
    """Choose which release sources to configure during onboarding."""
    fields: list[SettingsField] = [
        HeadingField(
            key="release_sources_heading",
            title="Release Sources",
            description=(
                "Choose the release sources you want to configure now. You can always add or "
                "change sources later in Settings."
            ),
        ),
        MultiSelectField(
            key=ONBOARDING_RELEASE_SOURCES_KEY,
            label="Sources to Set Up",
            description="Select one or more release sources to configure now.",
            default=[],
            variant="dropdown",
            env_supported=False,
            options=[
                {
                    "value": "direct_download",
                    "label": "Direct Download",
                    "description": "Configure your own mirror URLs for direct ebook downloads.",
                },
                {
                    "value": "prowlarr",
                    "label": "Prowlarr",
                    "description": "Search your torrent and Usenet indexers through Prowlarr.",
                },
                {
                    "value": "audiobookbay",
                    "label": "AudiobookBay",
                    "description": "Search AudiobookBay directly for audiobook releases.",
                },
                {
                    "value": "irc",
                    "label": "IRC",
                    "description": "Connect to IRC for ebook and audiobook release searches.",
                },
            ],
        ),
    ]
    return fields


def get_direct_download_setup_fields() -> list[SettingsField]:
    """Render trimmed direct-download essentials for onboarding."""
    fields: list[SettingsField] = [
        HeadingField(
            key="direct_download_setup_onboarding_heading",
            title="Choose at least one download source",
            description=(
                "Add at least one Anna's Archive or OceanofPDF mirror URL to enable Direct "
                "Download. You can configure additional mirrors later in Settings."
            ),
        )
    ]
    fields.extend(_get_fields_from_tab("mirrors", ["AA_MIRROR_URLS"]))
    donator_key = _get_field_from_tab("download_sources", "AA_DONATOR_KEY")
    if donator_key:
        fields.append(
            _clone_field_with_overrides(
                donator_key,
                label="Anna's Archive Donator Key (optional)",
            )
        )
    fields.extend(_get_fields_from_tab("mirrors", ["OCEANOFPDF_MIRROR_URLS"]))
    return fields


def get_direct_download_field_groups() -> list[dict[str, Any]]:
    """Group direct-download settings by provider for the onboarding UI."""
    return [
        {
            "id": "annas_archive",
            "title": "Anna's Archive",
            "description": "Configure mirror URLs and an optional donator API key.",
            "field_keys": ["AA_MIRROR_URLS", "AA_DONATOR_KEY"],
            "default_open": True,
        },
        {
            "id": "oceanofpdf",
            "title": "OceanOfPDF",
            "description": "Configure the mirror URL used for searches and downloads.",
            "field_keys": ["OCEANOFPDF_MIRROR_URLS"],
        },
    ]


def get_direct_download_bypass_fields() -> list[SettingsField]:
    """Render only the core Cloudflare bypass fields for onboarding."""
    return _get_fields_from_tab(
        "cloudflare_bypass",
        [
            "USE_CF_BYPASS",
            "USING_EXTERNAL_BYPASSER",
            "EXT_BYPASSER_URL",
            "EXT_BYPASSER_PATH",
        ],
    )


def get_prowlarr_fields() -> list[SettingsField]:
    """Render trimmed Prowlarr setup fields for onboarding."""
    return _get_fields_from_tab(
        "prowlarr_config",
        [
            "prowlarr_heading",
            "PROWLARR_URL",
            "PROWLARR_API_KEY",
            "test_prowlarr",
            "PROWLARR_INDEXERS",
        ],
        strip_show_when_keys={"PROWLARR_ENABLED"},
    )


def get_audiobookbay_fields() -> list[SettingsField]:
    """Render trimmed AudiobookBay setup fields for onboarding."""
    return [
        HeadingField(
            key="audiobookbay_onboarding_heading",
            title="AudiobookBay",
            description="Add the AudiobookBay domain you want Shelfmark to search.",
        ),
        *_get_fields_from_tab(
            "audiobookbay_config",
            ["ABB_HOSTNAME"],
            strip_show_when_keys={"ABB_ENABLED"},
        ),
    ]


def get_irc_fields() -> list[SettingsField]:
    """Render trimmed IRC setup fields for onboarding."""
    return _get_fields_from_tab(
        "irc",
        [
            "heading",
            "IRC_SERVER",
            "IRC_PORT",
            "IRC_USE_TLS",
            "IRC_CHANNEL",
            "IRC_NICK",
            "IRC_SEARCH_BOT",
        ],
    )


def get_onboarding_steps() -> list[dict[str, Any]]:
    """Return the full onboarding step configuration."""
    return [
        {
            "id": "search_mode",
            "title": "Search Mode",
            "tab": "search_mode",
            "get_fields": get_search_mode_fields,
        },
        {
            "id": "metadata_provider",
            "title": "Metadata Provider",
            "tab": "search_mode",
            "get_fields": get_metadata_provider_fields,
            "show_when": [{"field": "SEARCH_MODE", "value": "universal"}],
        },
        {
            "id": "hardcover_setup",
            "title": "Hardcover Setup",
            "tab": "hardcover",
            "get_fields": get_hardcover_setup_fields,
            "show_when": [
                {"field": "SEARCH_MODE", "value": "universal"},
                {"field": "METADATA_PROVIDER", "value": "hardcover"},
            ],
        },
        {
            "id": "googlebooks_setup",
            "title": "Google Books Setup",
            "tab": "googlebooks",
            "get_fields": get_googlebooks_setup_fields,
            "show_when": [
                {"field": "SEARCH_MODE", "value": "universal"},
                {"field": "METADATA_PROVIDER", "value": "googlebooks"},
            ],
        },
        {
            "id": "release_sources",
            "title": "Release Sources",
            "tab": "search_mode",
            "get_fields": get_release_source_selection_fields,
            "show_when": [{"field": "SEARCH_MODE", "value": "universal"}],
            "optional": True,
        },
        {
            "id": "direct_download_setup_direct_mode",
            "title": "Direct Download Setup",
            "tab": "download_sources",
            "get_fields": get_direct_download_setup_fields,
            "field_groups": get_direct_download_field_groups(),
            "show_when": [{"field": "SEARCH_MODE", "value": "direct"}],
        },
        {
            "id": "direct_download_cloudflare_bypass_direct_mode",
            "title": "Cloudflare Bypass",
            "tab": "cloudflare_bypass",
            "get_fields": get_direct_download_bypass_fields,
            "show_when": [{"field": "SEARCH_MODE", "value": "direct"}],
        },
        {
            "id": "direct_download_setup",
            "title": "Direct Download Setup",
            "tab": "download_sources",
            "get_fields": get_direct_download_setup_fields,
            "field_groups": get_direct_download_field_groups(),
            "show_when": [
                {"field": "SEARCH_MODE", "value": "universal"},
                {"field": ONBOARDING_RELEASE_SOURCES_KEY, "value": "direct_download"},
            ],
            "optional": True,
        },
        {
            "id": "direct_download_cloudflare_bypass",
            "title": "Cloudflare Bypass",
            "tab": "cloudflare_bypass",
            "get_fields": get_direct_download_bypass_fields,
            "show_when": [
                {"field": "SEARCH_MODE", "value": "universal"},
                {"field": ONBOARDING_RELEASE_SOURCES_KEY, "value": "direct_download"},
            ],
            "optional": True,
        },
        {
            "id": "prowlarr",
            "title": "Prowlarr",
            "tab": "prowlarr_config",
            "get_fields": get_prowlarr_fields,
            "show_when": [
                {"field": "SEARCH_MODE", "value": "universal"},
                {"field": ONBOARDING_RELEASE_SOURCES_KEY, "value": "prowlarr"},
            ],
            "optional": True,
        },
        {
            "id": "audiobookbay",
            "title": "AudiobookBay",
            "tab": "audiobookbay_config",
            "get_fields": get_audiobookbay_fields,
            "show_when": [
                {"field": "SEARCH_MODE", "value": "universal"},
                {"field": ONBOARDING_RELEASE_SOURCES_KEY, "value": "audiobookbay"},
            ],
            "optional": True,
        },
        {
            "id": "irc",
            "title": "IRC",
            "tab": "irc",
            "get_fields": get_irc_fields,
            "show_when": [
                {"field": "SEARCH_MODE", "value": "universal"},
                {"field": ONBOARDING_RELEASE_SOURCES_KEY, "value": "irc"},
            ],
            "optional": True,
        },
    ]


def get_onboarding_config() -> dict[str, Any]:
    """Get the full onboarding configuration including steps and current values."""
    steps = []
    all_values = {}

    for step_config in get_onboarding_steps():
        fields = step_config["get_fields"]()
        tab_name = step_config["tab"]

        # Serialize fields with current values
        serialized_fields = []
        for field in fields:
            field_tab_name = _get_field_tab_name(field, tab_name)
            serialized = serialize_field(field, field_tab_name, include_value=True)
            serialized_fields.append(serialized)

            # Collect values (skip HeadingFields)
            if hasattr(field, "env_supported") and getattr(field, "key", None):
                if field.key in _ONBOARDING_VIRTUAL_KEYS:
                    value = getattr(field, "default", "")
                else:
                    value = get_setting_value(field, field_tab_name)
                all_values[field.key] = (
                    value if value is not None else getattr(field, "default", "")
                )

        step = {
            "id": step_config["id"],
            "title": step_config["title"],
            "tab": tab_name,
            "fields": serialized_fields,
        }

        if "show_when" in step_config:
            step["showWhen"] = step_config["show_when"]
        if step_config.get("optional"):
            step["optional"] = True
        if "field_groups" in step_config:
            step["fieldGroups"] = [
                {
                    "id": group["id"],
                    "title": group["title"],
                    "description": group.get("description", ""),
                    "fieldKeys": group["field_keys"],
                    "defaultOpen": group.get("default_open", False),
                }
                for group in step_config["field_groups"]
            ]

        steps.append(step)

    return {
        "steps": steps,
        "values": all_values,
        "complete": is_onboarding_complete(),
    }


def save_onboarding_settings(values: dict[str, Any]) -> dict[str, Any]:
    """Save onboarding settings and mark as complete.

    Args:
        values: Dict of field key -> value

    Returns:
        Dict with success status and message

    """
    try:
        # Group values by their target tab
        tab_values: dict[str, dict[str, Any]] = {}

        for step_config in get_onboarding_steps():
            if not _is_step_visible(step_config, values):
                continue

            fields = step_config["get_fields"]()

            for field in fields:
                if isinstance(field, HeadingField):
                    continue

                key = field.key
                if key in _ONBOARDING_VIRTUAL_KEYS:
                    continue
                if not _is_field_visible(field, values):
                    continue
                if key in values:
                    tab_name = _get_field_tab_name(field, step_config["tab"])
                    if tab_name not in tab_values:
                        tab_values[tab_name] = {}
                    tab_values[tab_name][key] = values[key]

        # Save each tab's values
        for tab_name, tab_data in tab_values.items():
            if tab_data:
                save_config_file(tab_name, tab_data)
                logger.info("Saved onboarding settings to %s: %s", tab_name, list(tab_data.keys()))

        search_mode = values.get("SEARCH_MODE", "universal")
        if search_mode == "universal":
            provider = values.get("METADATA_PROVIDER", "hardcover")
            if provider:
                # Map provider name to its enabled key
                enabled_key_map = {
                    "hardcover": "HARDCOVER_ENABLED",
                    "openlibrary": "OPENLIBRARY_ENABLED",
                    "googlebooks": "GOOGLEBOOKS_ENABLED",
                }
                enabled_key = enabled_key_map.get(provider, f"{provider.upper()}_ENABLED")

                # Get existing provider config and add enabled flag
                provider_config = {enabled_key: True}

                # Include API key if provided for that provider
                if provider == "hardcover" and values.get("HARDCOVER_API_KEY"):
                    provider_config["HARDCOVER_API_KEY"] = values["HARDCOVER_API_KEY"]
                elif provider == "googlebooks" and values.get("GOOGLEBOOKS_API_KEY"):
                    provider_config["GOOGLEBOOKS_API_KEY"] = values["GOOGLEBOOKS_API_KEY"]

                save_config_file(provider, provider_config)
                logger.info(
                    "Enabled metadata provider: %s with keys: %s",
                    provider,
                    list(provider_config.keys()),
                )

        selected_release_sources = values.get(ONBOARDING_RELEASE_SOURCES_KEY, [])
        if not isinstance(selected_release_sources, list):
            selected_release_sources = []

        source_updates: dict[str, dict[str, Any]] = {}

        if search_mode == "direct":
            source_updates.setdefault("download_sources", {})["DIRECT_DOWNLOAD_ENABLED"] = True
        else:
            if _is_release_source_selected(values, "direct_download"):
                source_updates.setdefault("download_sources", {})["DIRECT_DOWNLOAD_ENABLED"] = True
            if _is_release_source_selected(values, "prowlarr"):
                source_updates.setdefault("prowlarr_config", {})["PROWLARR_ENABLED"] = True
            if _is_release_source_selected(values, "audiobookbay"):
                source_updates.setdefault("audiobookbay_config", {})["ABB_ENABLED"] = True

            if not values.get("DEFAULT_RELEASE_SOURCE"):
                for source_name in selected_release_sources:
                    if source_name in {"direct_download", "prowlarr", "irc"}:
                        source_updates.setdefault("search_mode", {})["DEFAULT_RELEASE_SOURCE"] = (
                            source_name
                        )
                        break

            if not values.get("DEFAULT_RELEASE_SOURCE_AUDIOBOOK"):
                for source_name in selected_release_sources:
                    if source_name in {"prowlarr", "audiobookbay", "irc"}:
                        source_updates.setdefault("search_mode", {})[
                            "DEFAULT_RELEASE_SOURCE_AUDIOBOOK"
                        ] = source_name
                        break

        for tab_name, tab_data in source_updates.items():
            if tab_data:
                save_config_file(tab_name, tab_data)
                logger.info(
                    "Enabled onboarding release source settings for %s: %s",
                    tab_name,
                    list(tab_data.keys()),
                )

        # Mark onboarding as complete
        mark_onboarding_complete()

        # Refresh config
        try:
            from shelfmark.core.config import config

            config.refresh()
        except ImportError as e:
            logger.debug("Could not refresh config after onboarding: %s", e)

    except Exception as e:
        logger.exception("Failed to save onboarding settings")
        return {"success": False, "message": str(e)}
    else:
        return {"success": True, "message": "Onboarding complete!"}
