"""Libgen search settings registration."""

from shelfmark.core.settings_registry import (
    CheckboxField,
    NumberField,
    SettingsField,
    register_settings,
)


@register_settings("libgen_config", "Libgen Search", icon="download", order=46)
def libgen_config_settings() -> list[SettingsField]:
    """Libgen search configuration settings."""
    return [
        CheckboxField(
            key="LIBGEN_SEARCH_ENABLED",
            label="Enable Libgen Search",
            description=(
                "Search the Libgen catalogue directly, including CBZ/CBR comics and manga "
                "that Anna's Archive does not index. Uses the Libgen mirrors configured "
                "under Mirrors for both search and download."
            ),
            default=False,
        ),
        NumberField(
            key="LIBGEN_SEARCH_MAX_RESULTS",
            label="Max Results",
            description="Maximum number of results to request per search (1-100).",
            default=25,
            min_value=1,
            max_value=100,
            show_when={"field": "LIBGEN_SEARCH_ENABLED", "value": True},
        ),
    ]
