"""Automatic release selection + download for pending requests.

Given a pending request (typically synced from a Hardcover wishlist), this module
searches release sources in a user-configured priority order, applies a strict
title/author/format match guard, picks the best candidate from the first source
that yields a match, and queues it via the normal request-fulfilment path.

Design goals:
- **Strict by default.** Bias toward leaving a request pending (manual review) over
  grabbing the wrong file. Title and author must both match; the format must be a
  real audiobook format (no ebook-only fallbacks when targeting audiobooks).
- **Source priority is primary.** Walk the configured source order top-down and take
  the first source that produces at least one strict match.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from shelfmark.core.config import config as app_config
from shelfmark.core.logger import setup_logger
from shelfmark.core.release_search import search_book_releases
from shelfmark.core.request_helpers import coerce_int
from shelfmark.core.text_match import (
    DEFAULT_TITLE_MATCH_THRESHOLD,
    author_surname,
    title_tokens_match,
)
from shelfmark.core.text_match import (
    tokens as _tokens,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from shelfmark.core.user_db import UserDB
    from shelfmark.metadata_providers import BookMetadata
    from shelfmark.release_sources import Release

logger = setup_logger(__name__)

# Fraction of significant book-title tokens that must appear in the release title.
TITLE_MATCH_THRESHOLD = DEFAULT_TITLE_MATCH_THRESHOLD

DEFAULT_AUDIOBOOK_FORMATS = ("m4b", "mp3")
EBOOK_FORMAT_MARKERS = (
    "epub",
    "mobi",
    "azw3",
    "azw",
    "pdf",
    "fb2",
    "djvu",
    "cbz",
    "cbr",
)
DEFAULT_EBOOK_FORMATS = ("epub", "mobi", "azw3", "fb2", "djvu", "cbz", "cbr")
# Ebook format ranking for tie-breaking within a single source.
_EBOOK_FORMAT_RANK = {"epub": 4, "kepub": 3, "azw3": 3, "mobi": 2, "azw": 2, "fb2": 1, "pdf": 1}
# Audiobook signals we recognise in free-text release titles in addition to formats.
AUDIOBOOK_TITLE_MARKERS = ("audiobook", "unabridged", "m4b", "audio book")

# Audiobook format ranking for tie-breaking within a single source.
_FORMAT_RANK = {"m4b": 3, "m4a": 2, "mp3": 1}

# Multi-book packs masquerade as a match because the wanted title appears inside theirs
# ("Jack Reacher 1-28 + Short Stories - Complete to date"). Any of these in the release
# title, when the requested title itself does not carry it, marks the release as a pack.
_PACK_MARKERS = re.compile(
    r"\b(?:complete|collection|boxset|box set|omnibus|anthology|bundle|megapack"
    r"|all \d+ (?:audio)?books|books? \d{1,3}\s*(?:-|–|to|thru|through)\s*\d{1,3})\b"
    r"|\(#?\d{1,3}\s*[-–]\s*\d{1,3}\)"  # "(#1-24)", "(1-22)"
    r"|\b\d{1,3}\s*[-–]\s*\d{1,3}\s*\+"  # "1-28 +"
    r"|\+\s*short stories",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class AutoDownloadOutcome:
    """Result of attempting to auto-download a single request."""

    request_id: int
    status: str  # "queued" | "no_match" | "skipped" | "error"
    detail: str = ""
    source: str | None = None


def _configured_formats(key: str, default: tuple[str, ...]) -> set[str]:
    configured = app_config.get(key, list(default))
    values: list[object]
    if isinstance(configured, str):
        values = [configured]
    elif isinstance(configured, (list, tuple, set)):
        values = list(configured)
    else:
        values = []
    formats = {str(fmt).strip().lower() for fmt in values if str(fmt).strip()}
    return formats or set(default)


def _audiobook_formats() -> set[str]:
    return _configured_formats("SUPPORTED_AUDIOBOOK_FORMATS", DEFAULT_AUDIOBOOK_FORMATS)


def _ebook_formats() -> set[str]:
    return _configured_formats("SUPPORTED_FORMATS", DEFAULT_EBOOK_FORMATS)


def _author_surname_tokens(book: BookMetadata) -> list[str]:
    """Return distinctive author tokens (prefer the surname of the primary author)."""
    author = book.search_author or (book.authors[0] if book.authors else "")
    surname = author_surname(author)
    return [surname] if surname else []


def _title_match(book: BookMetadata, release_title: str) -> bool:
    return title_tokens_match(
        book.search_title or book.title,
        set(_tokens(release_title)),
        TITLE_MATCH_THRESHOLD,
    )


def _author_match(book: BookMetadata, release: Release) -> bool:
    surname = _author_surname_tokens(book)
    if not surname:
        return True  # No author metadata to verify against; don't block on it.
    haystack = set(_tokens(release.title)) | set(_tokens(release.indexer))
    extra_author = release.extra.get("author") if isinstance(release.extra, dict) else None
    haystack |= set(_tokens(extra_author if isinstance(extra_author, str) else None))
    return all(tok in haystack for tok in surname)


def _audiobook_signal(release: Release, audiobook_formats: set[str]) -> bool:
    fmt = (release.format or "").strip().lower()
    title_l = (release.title or "").lower()
    return (
        fmt in audiobook_formats
        or (release.content_type or "").lower() == "audiobook"
        or any(marker in title_l for marker in AUDIOBOOK_TITLE_MARKERS)
        or any(f".{af}" in title_l or f" {af}" in title_l for af in audiobook_formats)
    )


def _format_match(
    release: Release,
    content_type: str,
    audiobook_formats: set[str],
    ebook_formats: set[str],
) -> bool:
    """Require a format signal for the requested content type and reject the other kind."""
    fmt = (release.format or "").strip().lower()
    if content_type == "audiobook":
        # Reject things that are clearly an ebook and nothing else.
        return _audiobook_signal(release, audiobook_formats) and fmt not in EBOOK_FORMAT_MARKERS

    title_l = (release.title or "").lower()
    has_ebook_signal = (
        fmt in ebook_formats
        or (release.content_type or "").lower() == "ebook"
        or any(f".{ef}" in title_l for ef in ebook_formats)
    )
    return has_ebook_signal and not _audiobook_signal(release, audiobook_formats)


def _looks_like_pack(release_title: str | None, book_title: str | None) -> bool:
    """True when the release advertises a multi-book pack the requested title does not."""
    wanted = (book_title or "").lower()
    for match in _PACK_MARKERS.finditer(release_title or ""):
        if match.group(0).lower() not in wanted:
            return True
    return False


def _seeders_ok(release: Release, min_seeders: int) -> bool:
    from shelfmark.release_sources import ReleaseProtocol

    if release.protocol != ReleaseProtocol.TORRENT:
        return True  # Non-torrent protocols have no seeder concept.
    if release.seeders is None:
        return True  # The source reports no count (e.g. AudiobookBay); nothing to judge.
    return release.seeders >= min_seeders


def strict_match(
    release: Release,
    book: BookMetadata,
    *,
    content_type: str = "audiobook",
    min_seeders: int = 1,
    audiobook_formats: set[str] | None = None,
    ebook_formats: set[str] | None = None,
) -> bool:
    """Return True only if the release confidently matches the requested book and format."""
    audio = audiobook_formats if audiobook_formats is not None else _audiobook_formats()
    ebook = ebook_formats if ebook_formats is not None else _ebook_formats()
    return (
        _title_match(book, release.title or "")
        and _author_match(book, release)
        and not _looks_like_pack(release.title, book.search_title or book.title)
        and _format_match(release, content_type, audio, ebook)
        and _seeders_ok(release, min_seeders)
    )


def _release_sort_key(release: Release, content_type: str) -> tuple[int, int, int]:
    fmt = (release.format or "").strip().lower()
    if content_type == "audiobook":
        if fmt not in _FORMAT_RANK and "m4b" in (release.title or "").lower():
            fmt = "m4b"
        rank = _FORMAT_RANK.get(fmt, 0)
    else:
        rank = _EBOOK_FORMAT_RANK.get(fmt, 0)
    return (rank, release.seeders or 0, release.size_bytes or 0)


def pick_best_release(releases: list[Release], content_type: str = "audiobook") -> Release | None:
    if not releases:
        return None
    return max(releases, key=lambda release: _release_sort_key(release, content_type))


def build_release_data(release: Release, book: BookMetadata, content_type: str) -> dict[str, Any]:
    """Build a queue_release-compatible payload from a chosen Release."""
    author = book.search_author or (book.authors[0] if book.authors else None)
    extra = release.extra if isinstance(release.extra, dict) else {}
    # Carry the book's cover so the download shows artwork immediately; the orchestrator
    # reads release_data["preview"] and proxies it (orchestrator.queue_release / task_to_dict).
    preview = book.cover_url or extra.get("preview")
    payload: dict[str, Any] = {
        "source": release.source,
        "source_id": release.source_id,
        "title": release.title,
        "author": author,
        "year": book.publish_year,
        "preview": preview,
        "format": release.format,
        "size": release.size,
        "size_bytes": release.size_bytes,
        "download_url": release.download_url,
        "info_url": release.info_url,
        "protocol": release.protocol.value if release.protocol else None,
        "indexer": release.indexer,
        "seeders": release.seeders,
        "language": release.language,
        "content_type": release.content_type or content_type,
        "series_name": book.series_name,
        "series_position": book.series_position,
        "subtitle": book.subtitle,
        "extra": dict(extra),
    }
    return {key: value for key, value in payload.items() if value is not None}


_SOURCE_PRIORITY_KEYS = {
    "audiobook": "AUTO_DOWNLOAD_SOURCE_PRIORITY",
    "ebook": "AUTO_DOWNLOAD_EBOOK_SOURCE_PRIORITY",
}


def _configured_source_priority(content_type: str) -> list[str]:
    """Return enabled release-source names for ``content_type`` in configured priority order."""
    from shelfmark.release_sources import list_available_sources

    def _supports(src: dict[str, Any]) -> bool:
        supported = src.get("supported_content_types") or ["ebook", "audiobook"]
        return content_type in supported

    available = [src for src in list_available_sources() if _supports(src)]
    available_by_name = {src["name"]: src for src in available}

    raw = app_config.get(
        _SOURCE_PRIORITY_KEYS.get(content_type, "AUTO_DOWNLOAD_SOURCE_PRIORITY"), []
    )
    ordered: list[str] = []
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = item.get("id")
            if not isinstance(name, str) or name not in available_by_name:
                continue
            if not bool(item.get("enabled", True)):
                continue
            if not available_by_name[name].get("enabled", False):
                continue  # Source itself not usable (unconfigured/unavailable).
            ordered.append(name)

    if ordered:
        return ordered
    # Fallback: every usable source for this content type, registry order.
    return [src["name"] for src in available if src.get("enabled")]


def auto_download_request(
    user_db: UserDB,
    request_row: dict[str, Any],
    *,
    sources: list[str],
    content_type: str,
    min_seeders: int,
    queue_release: Callable[..., tuple[bool, str | None]],
    admin_user_id: int,
) -> AutoDownloadOutcome:
    """Search, strict-match, and queue a single pending request."""
    from shelfmark.core.requests_service import RequestServiceError, fulfil_request
    from shelfmark.metadata_providers import (
        get_provider,
        get_provider_kwargs,
        is_provider_registered,
    )

    request_id = int(request_row["id"])
    book_data = request_row.get("book_data") or {}
    if not isinstance(book_data, dict):
        return AutoDownloadOutcome(request_id, "skipped", "no book_data")

    provider_name = book_data.get("provider")
    provider_id = book_data.get("provider_id") or book_data.get("id")
    if not provider_name or not provider_id or not is_provider_registered(provider_name):
        return AutoDownloadOutcome(request_id, "skipped", "no usable provider/id")

    try:
        prov = get_provider(provider_name, **get_provider_kwargs(provider_name))
        book = prov.get_book(str(provider_id))
    except Exception as exc:  # noqa: BLE001 - provider/network errors shouldn't kill the loop
        logger.warning("auto-download: metadata lookup failed for request %s: %s", request_id, exc)
        return AutoDownloadOutcome(request_id, "error", f"metadata lookup failed: {exc}")

    if book is None:
        return AutoDownloadOutcome(request_id, "skipped", "book not found in provider")

    # Final guard: skip if the book is already in a library that is checked for this content type.
    from shelfmark.core import library_index

    try:
        in_library = library_index.is_in_library(book, content_type, strict=True)
    except library_index.LibraryUnavailableError as exc:
        # Unattended: do not download blind. The request stays pending for the next cycle.
        logger.warning(
            "auto-download: request %s held, library check unavailable: %s", request_id, exc
        )
        return AutoDownloadOutcome(request_id, "error", f"library check unavailable: {exc}")
    if in_library:
        logger.info(
            "auto-download: request %s (%s) already in library; skipping",
            request_id,
            book.title,
        )
        return AutoDownloadOutcome(request_id, "in_library", "already in library")

    audiobook_formats = _audiobook_formats()
    ebook_formats = _ebook_formats()

    # Walk sources in priority order; take the first source with a strict match.
    for source_name in sources:
        _all, by_source, _errors = search_book_releases(
            book,
            sources=[source_name],
            content_type=content_type,
            expand_search=True,
        )
        candidates = [
            release
            for release in by_source.get(source_name, [])
            if strict_match(
                release,
                book,
                content_type=content_type,
                min_seeders=min_seeders,
                audiobook_formats=audiobook_formats,
                ebook_formats=ebook_formats,
            )
        ]
        chosen = pick_best_release(candidates, content_type)
        if chosen is None:
            continue

        release_data = build_release_data(chosen, book, content_type)
        try:
            fulfil_request(
                user_db,
                request_id=request_id,
                admin_user_id=admin_user_id,
                queue_release=queue_release,
                release_data=release_data,
            )
        except RequestServiceError as exc:
            logger.warning("auto-download: fulfil failed for request %s: %s", request_id, exc)
            return AutoDownloadOutcome(request_id, "error", str(exc), source=source_name)
        logger.info(
            "auto-download: queued request %s from %s (%s)",
            request_id,
            source_name,
            chosen.title,
        )
        return AutoDownloadOutcome(request_id, "queued", chosen.title, source=source_name)

    logger.info("auto-download: no strict match for request %s (%s)", request_id, book.title)
    return AutoDownloadOutcome(request_id, "no_match", "no strict match across sources")


def auto_download_pending(
    user_db: UserDB,
    *,
    queue_release: Callable[..., tuple[bool, str | None]],
    provider_filter: str | None = "hardcover",
    admin_user_id: int | None = None,
) -> dict[str, int]:
    """Run the auto-download pass over all eligible pending requests.

    Each request is handled for its own content type (ebook or audiobook): its source
    priority list, format guard and library check all follow the request. Returns a summary
    count dict. No-ops (returns zeros) unless AUTO_DOWNLOAD_ENABLED. ``admin_user_id``
    defaults to the lowest-id admin, who fulfils the requests.
    """
    zeros = {"queued": 0, "no_match": 0, "in_library": 0, "skipped": 0, "error": 0}
    if not bool(app_config.get("AUTO_DOWNLOAD_ENABLED", False)):
        return zeros

    if admin_user_id is None:
        from shelfmark.core.hardcover_sync import resolve_request_owner

        admin_user_id = resolve_request_owner(user_db)
    if admin_user_id is None:
        logger.warning("auto-download: no admin user exists to fulfil requests")
        return zeros

    min_seeders = coerce_int(app_config.get("AUTO_DOWNLOAD_MIN_SEEDERS", 1), 1)
    sources_by_type: dict[str, list[str]] = {}

    pending = user_db.list_requests(status="pending")
    summary = dict(zeros)

    for row in pending:
        book_data = row.get("book_data") or {}
        if provider_filter and (
            not isinstance(book_data, dict) or book_data.get("provider") != provider_filter
        ):
            continue
        # Only act on requests that haven't already been dispatched.
        if str(row.get("delivery_state") or "none").lower() not in {"none", ""}:
            continue

        content_type = str(
            row.get("content_type")
            or (book_data.get("content_type") if isinstance(book_data, dict) else None)
            or "ebook"
        ).lower()
        if content_type not in sources_by_type:
            sources_by_type[content_type] = _configured_source_priority(content_type)
            if not sources_by_type[content_type]:
                logger.warning(
                    "auto-download: no usable %s release sources configured", content_type
                )
        sources = sources_by_type[content_type]
        if not sources:
            summary["skipped"] += 1
            continue

        outcome = auto_download_request(
            user_db,
            row,
            sources=sources,
            content_type=content_type,
            min_seeders=min_seeders,
            queue_release=queue_release,
            admin_user_id=admin_user_id,
        )
        summary[outcome.status] = summary.get(outcome.status, 0) + 1

    logger.info("auto-download pass complete: %s", summary)
    return summary
