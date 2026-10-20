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
    register_settings,
)

_CONTENT_TYPE_OPTIONS = [
    {"value": "audiobook", "label": "Audiobooks"},
    {"value": "ebook", "label": "Ebooks"},
    {"value": "both", "label": "Ebooks and audiobooks"},
]


def _status_options() -> list[dict[str, str]]:
    from shelfmark.metadata_providers.hardcover import HARDCOVER_STATUSES

    return [{"value": str(status["id"]), "label": status["label"]} for status in HARDCOVER_STATUSES]


def _source_options(content_type: str) -> list[dict[str, Any]]:
    """Orderable-list options: release sources that can return ``content_type``."""
    from shelfmark.release_sources import list_available_sources

    options: list[dict[str, Any]] = []
    for src in list_available_sources():
        supported = src.get("supported_content_types") or ["ebook", "audiobook"]
        if content_type not in supported:
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


def _audiobook_source_options() -> list[dict[str, Any]]:
    return _source_options("audiobook")


def _ebook_source_options() -> list[dict[str, Any]]:
    return _source_options("ebook")


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
            description=(
                'Content type for synced requests. "Ebooks and audiobooks" creates one request '
                "of each per shelf book; every request is checked against its own library and "
                "downloaded with its own format guard and source priority."
            ),
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
                "first source in the matching list below that yields a strict title/author/"
                "format match for the request's content type (ebook formats for ebook "
                "requests, audiobook formats for audiobook requests). If no confident match "
                "is found, the request is left pending for manual review."
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
            label="Audiobook source priority",
            description=(
                "Drag to set which release sources are tried first for audiobook requests. "
                "The first source with a strict match wins. Disabled sources are skipped."
            ),
            options=_audiobook_source_options,
            # Default left empty so registration never imports release_sources (which would
            # deadlock the registry lock). When empty, all available sources are used in
            # registry order (see auto_download._configured_source_priority).
            default=[],
        ),
        OrderableListField(
            key="AUTO_DOWNLOAD_EBOOK_SOURCE_PRIORITY",
            label="Ebook source priority",
            description=(
                "Drag to set which release sources are tried first for ebook requests. "
                "The first source with a strict match wins. Disabled sources are skipped."
            ),
            options=_ebook_source_options,
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
        NumberField(
            key="AUTO_DOWNLOAD_RETRY_DAYS",
            label="Retry failed downloads after (days)",
            description=(
                "A synced request whose download failed, or was interrupted (a restart, say), "
                "goes back to pending after this many days and is tried again, a few per pass. "
                "A release that failed is not picked again, and a request is given up on after "
                "three failed retries. A download the stall timer cancelled counts as failed; one "
                "cancelled by a person, and rejected requests, are never retried. "
                "0 turns retries off."
            ),
            default=7,
            min_value=0,
            max_value=365,
        ),
        NumberField(
            key="AUTO_DOWNLOAD_FAILURE_RETRY_MINUTES",
            label="Try another release after a failed download (minutes)",
            description=(
                "A download that failed, or that was given up on (stalled, no seeders), goes "
                "back to pending this many minutes later and another release is chosen, "
                "without waiting for the retry days above or the next sync cycle. The release "
                "that failed is not picked again, and a request is given up on after three "
                "failed attempts. 0 turns this off."
            ),
            default=0,
            min_value=0,
            max_value=1440,
        ),
        NumberField(
            key="AUTO_DOWNLOAD_RETRY_FAILED_RELEASE_DAYS",
            label="Try a failed release again after (days)",
            description=(
                "A release that failed or stalled is kept out of the search for this many "
                "days, then it may be picked again. A torrent that stalled can be revived "
                "by new seeds, so a failure should not shut a book out for good. A request "
                "is still given up on after three failed attempts."
            ),
            default=7,
            min_value=1,
            max_value=365,
        ),
        NumberField(
            key="AUTO_DOWNLOAD_BACKOFF_MAX_DAYS",
            label="Longest wait between searches for an unmatched request (days)",
            description=(
                "A request that no source has a match for is searched every cycle at first. "
                "After three passes in a row with no match it is searched once a day, then "
                "every two days, and at most this often apart. A match, or Sync now, starts it "
                "over. 0 searches every unmatched request every cycle."
            ),
            default=3,
            min_value=0,
            max_value=30,
        ),
        CheckboxField(
            key="AUTO_DOWNLOAD_EXISTENCE_CHECK_ENABLED",
            label="Mark audiobooks no store sells",
            description=(
                "An audiobook request that stays unmatched is looked up on Audible (US and "
                "UK) through your Audiobookshelf server. Either way it stays pending and its "
                "reason says what the store answered (with the ASIN when listed), for you to "
                "decline or keep; it is checked again after a week. A lookup that "
                "fails decides nothing. Needs the Audiobookshelf URL and token below."
            ),
            default=False,
        ),
        NumberField(
            key="AUTO_DOWNLOAD_EXISTENCE_CHECK_AFTER_PASSES",
            label="Look the book up after (unmatched passes)",
            description=(
                "How many consecutive passes with no strict match a request goes through "
                "before the store is asked. A pass that matched, or a reopen, starts the "
                "count over."
            ),
            default=3,
            min_value=1,
            max_value=100,
        ),
        NumberField(
            key="AUTO_DOWNLOAD_AUDIOBOOK_MIN_SIZE_PERCENT",
            label="Smallest acceptable audiobook copy (%)",
            description=(
                "A copy of an audiobook smaller than this share of the largest copy found for "
                "the same book is probably a lower-bitrate copy. It is tried after the others, "
                "and a single m4b that small is not preferred over a fuller set of chapter "
                "files. 0 turns the check off."
            ),
            default=67,
            min_value=0,
            max_value=100,
        ),
        ActionButton(
            key="sync_now",
            label="Sync now",
            description="Run a sync + auto-download pass immediately (uses saved settings).",
            style="primary",
            callback=_sync_now,
        ),
    ]
