"""Settings tab for Hardcover wishlist sync + automatic downloads."""

from __future__ import annotations

from typing import Any

from shelfmark.core.settings_registry import (
    ActionButton,
    CheckboxField,
    HeadingField,
    NumberField,
    OrderableListField,
    PasswordField,
    SelectField,
    SettingsField,
    TextField,
    register_settings,
)

_CONTENT_TYPE_OPTIONS = [
    {"value": "audiobook", "label": "Audiobooks"},
    {"value": "ebook", "label": "Ebooks"},
]


def _status_options() -> list[dict[str, str]]:
    from shelfmark.metadata_providers.hardcover import HARDCOVER_STATUSES

    return [{"value": str(status["id"]), "label": status["label"]} for status in HARDCOVER_STATUSES]


def _audiobook_source_options() -> list[dict[str, Any]]:
    """Orderable-list options: release sources that can return audiobooks."""
    from shelfmark.release_sources import list_available_sources

    options: list[dict[str, Any]] = []
    for src in list_available_sources():
        supported = src.get("supported_content_types") or ["ebook", "audiobook"]
        if "audiobook" not in supported:
            continue
        enabled = bool(src.get("enabled"))
        options.append(
            {
                "id": src["name"],
                "label": src.get("display_name") or src["name"],
                "description": None if enabled else "Source not configured / unavailable",
                "isLocked": not enabled,
                "disabledReason": None if enabled else "Source not configured / unavailable",
            }
        )
    return options


def _test_library_connection(current_values: dict[str, Any] | None = None) -> dict[str, Any]:
    """Action-button callback: verify Audiobookshelf connectivity + item count."""
    from shelfmark.core import library_index

    return library_index.test_connection()


def _sync_now(current_values: dict[str, Any] | None = None) -> dict[str, Any]:
    """Action-button callback: trigger an immediate sync + auto-download pass."""
    from shelfmark.core import hardcover_scheduler

    started = hardcover_scheduler.trigger_async(force=True)
    if not started:
        return {"success": False, "message": "A sync is already running. Try again shortly."}
    return {
        "success": True,
        "message": "Sync started. New wishlist books will appear as requests; "
        "matching audiobooks auto-download if enabled.",
    }


@register_settings("hardcover_sync", "Hardcover Sync", icon="book", order=8)
def hardcover_sync_settings() -> list[SettingsField]:
    """Configure Hardcover wishlist sync and automatic downloads."""
    return [
        HeadingField(
            key="hardcover_sync_heading",
            title="Hardcover Wishlist Sync",
            description=(
                "Automatically pull books from your Hardcover reading shelves into "
                "Shelfmark as requests, on a schedule."
            ),
        ),
        CheckboxField(
            key="HARDCOVER_SYNC_ENABLED",
            label="Enable scheduled sync",
            description="Periodically sync the selected Hardcover shelves into requests.",
            default=False,
        ),
        PasswordField(
            key="HARDCOVER_SYNC_TOKEN",
            label="Hardcover API Token",
            description=(
                "Bearer token for your Hardcover account. Leave blank to reuse the "
                "token from the Hardcover metadata provider."
            ),
            placeholder="Reuses metadata provider token if blank",
        ),
        SelectField(
            key="HARDCOVER_SYNC_STATUSES",
            label="Shelves to sync",
            description="Which Hardcover reading shelf to pull from.",
            options=_status_options,
            default="1",
        ),
        SelectField(
            key="HARDCOVER_SYNC_CONTENT_TYPE",
            label="Request as",
            description="Content type assigned to synced requests and targeted for downloads.",
            options=_CONTENT_TYPE_OPTIONS,
            default="audiobook",
        ),
        NumberField(
            key="HARDCOVER_SYNC_INTERVAL",
            label="Sync interval",
            description="How often the scheduled sync runs (minimum 1 minute).",
            default=6,
            min_value=1,
            max_value=10000,
        ),
        SelectField(
            key="HARDCOVER_SYNC_INTERVAL_UNIT",
            label="Interval unit",
            description="Whether the sync interval is measured in minutes or hours.",
            options=[
                {"value": "minutes", "label": "Minutes"},
                {"value": "hours", "label": "Hours"},
            ],
            default="hours",
        ),
        HeadingField(
            key="auto_download_heading",
            title="Automatic Downloads",
            description=(
                "When enabled, synced requests are auto-approved and downloaded from the "
                "first source below that yields a strict title/author/format match. If no "
                "confident match is found, the request is left pending for manual review."
            ),
        ),
        CheckboxField(
            key="AUTO_DOWNLOAD_ENABLED",
            label="Enable automatic downloads",
            description="Auto-approve and auto-download matching releases for synced requests.",
            default=False,
        ),
        OrderableListField(
            key="AUTO_DOWNLOAD_SOURCE_PRIORITY",
            label="Source priority",
            description=(
                "Drag to set which release sources are tried first. The first source with "
                "a strict match wins. Disabled sources are skipped."
            ),
            options=_audiobook_source_options,
            # Default left empty so registration never imports release_sources (which would
            # deadlock the registry lock). When empty, all available sources are used in
            # registry order (see auto_download._configured_source_priority).
            default=[],
        ),
        NumberField(
            key="AUTO_DOWNLOAD_MIN_SEEDERS",
            label="Minimum seeders (torrents)",
            description="Skip torrent releases with fewer seeders than this.",
            default=1,
            min_value=0,
            max_value=1000,
        ),
        HeadingField(
            key="library_check_heading",
            title="Library Check (Audiobookshelf)",
            description=(
                "Skip books you already own. When enabled, the sync and auto-download "
                "steps check your Audiobookshelf library and skip anything already there."
            ),
        ),
        CheckboxField(
            key="LIBRARY_CHECK_ENABLED",
            label="Skip books already in Audiobookshelf",
            description="Check the Audiobookshelf library before adding/downloading a book.",
            default=False,
        ),
        TextField(
            key="AUDIOBOOKSHELF_URL",
            label="Audiobookshelf URL",
            description=(
                "Base URL reachable from the Shelfmark container (not the public/Cloudflare "
                "URL). Often the host LAN IP, e.g. http://10.0.0.91:13378."
            ),
            placeholder="http://10.0.0.91:13378",
        ),
        PasswordField(
            key="AUDIOBOOKSHELF_TOKEN",
            label="Audiobookshelf API Token",
            description="API token from Audiobookshelf (Settings > Users > your user > API Token).",
        ),
        TextField(
            key="AUDIOBOOKSHELF_LIBRARY_IDS",
            label="Library IDs (optional)",
            description="Comma-separated library IDs to check. Leave blank for all book libraries.",
            placeholder="Blank = all book libraries",
        ),
        ActionButton(
            key="test_library_connection",
            label="Test library connection",
            description="Check that Shelfmark can reach Audiobookshelf and count the items.",
            callback=_test_library_connection,
        ),
        ActionButton(
            key="sync_now",
            label="Sync now",
            description="Run a sync + auto-download pass immediately (uses saved settings).",
            style="primary",
            callback=_sync_now,
        ),
    ]
