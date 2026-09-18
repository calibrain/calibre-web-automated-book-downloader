"""Sync a Hardcover reading shelf (e.g. "Want to Read") into Shelfmark requests.

This replaces the standalone ``scripts/sync_hardcover_wishlist.py`` with an in-app
service that reuses the registered Hardcover metadata provider (so synced requests
carry full metadata incl. covers) and the request-service validation/dedup path.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Any

from shelfmark.core.config import config as app_config
from shelfmark.core.logger import setup_logger

if TYPE_CHECKING:
    from shelfmark.core.user_db import UserDB
    from shelfmark.metadata_providers import BookMetadata

logger = setup_logger(__name__)

# Hardcover status_id -> shelf. Default sync target is "Want to Read" (1).
DEFAULT_SYNC_STATUSES = ("1",)
_PAGE_LIMIT = 25  # Hardcover API page size.
_MAX_PAGES = 40  # Safety bound (~1000 books) so a bad response can't loop forever.


def _configured_token() -> str:
    """Prefer a dedicated sync token, fall back to the provider API key."""
    token = app_config.get("HARDCOVER_SYNC_TOKEN", "") or app_config.get("HARDCOVER_API_KEY", "")
    return str(token or "").strip()


def _configured_statuses() -> list[int]:
    raw = app_config.get("HARDCOVER_SYNC_STATUSES", list(DEFAULT_SYNC_STATUSES))
    if isinstance(raw, str):
        raw = [raw]
    statuses: list[int] = []
    for value in raw or []:
        try:
            statuses.append(int(value))
        except (TypeError, ValueError):
            continue
    return statuses or [int(s) for s in DEFAULT_SYNC_STATUSES]


def _build_provider() -> Any | None:
    from shelfmark.metadata_providers import get_provider

    token = _configured_token()
    if not token:
        logger.warning("hardcover-sync: no Hardcover token configured")
        return None
    provider = get_provider("hardcover", api_key=token)
    if not provider.is_available():
        logger.warning("hardcover-sync: Hardcover provider unavailable (bad token?)")
        return None
    return provider


def _primary_author(book: BookMetadata) -> str:
    if book.search_author:
        return book.search_author
    if book.authors:
        return book.authors[0]
    return "Unknown"


def _book_to_book_data(book: BookMetadata, content_type: str) -> dict[str, Any]:
    book_data: dict[str, Any] = {
        "title": book.title,
        "author": _primary_author(book),
        "provider": "hardcover",
        "provider_id": str(book.provider_id),
        "content_type": content_type,
    }
    # Only set preview when we actually have a cover, so the lazy backfill in
    # _populate_requests_metadata can still try later if it's missing.
    if book.cover_url:
        book_data["preview"] = book.cover_url
    if book.publish_year:
        book_data["year"] = book.publish_year
    if book.subtitle:
        book_data["subtitle"] = book.subtitle
    return book_data


def _existing_provider_ids(user_db: UserDB) -> set[str]:
    ids: set[str] = set()
    for row in user_db.list_requests():
        book_data = row.get("book_data") or {}
        if isinstance(book_data, dict):
            pid = book_data.get("provider_id") or book_data.get("id")
            if pid is not None:
                ids.add(str(pid))
    return ids


def _already_downloaded(db_path: str | None, title: str, author: str) -> bool:
    """Best-effort check that a title/author isn't already in download_history."""
    if not db_path:
        return False
    try:
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute(
                "SELECT 1 FROM download_history "
                "WHERE LOWER(title) = ? AND LOWER(author) = ? LIMIT 1",
                (title.strip().lower(), author.strip().lower()),
            ).fetchone()
            return row is not None
        finally:
            conn.close()
    except sqlite3.Error as exc:
        logger.debug("hardcover-sync: history dedup query failed: %s", exc)
        return False


def _fetch_status_books(provider: Any, status_id: int) -> list[BookMetadata]:
    """Page through a Hardcover status shelf, returning all books."""
    books: list[BookMetadata] = []
    for page in range(1, _MAX_PAGES + 1):
        try:
            result = provider._fetch_current_user_books_by_status(status_id, page, _PAGE_LIMIT)
        except Exception as exc:  # noqa: BLE001 - network/provider errors shouldn't abort the sync
            logger.warning(
                "hardcover-sync: fetch failed (status=%s page=%s): %s", status_id, page, exc
            )
            break
        page_books = list(result.books or [])
        books.extend(page_books)
        if not result.has_more or not page_books:
            break
    return books


def sync_wishlist(
    user_db: UserDB,
    *,
    db_path: str | None = None,
    user_id: int = 1,
) -> dict[str, int]:
    """Sync configured Hardcover shelves into pending requests.

    Returns a summary dict: ``{"added", "skipped", "errors"}``. Requires a configured
    token; enable-gating is the caller's responsibility (see hardcover_scheduler).
    """
    summary = {"added": 0, "skipped": 0, "in_library": 0, "errors": 0}

    provider = _build_provider()
    if provider is None:
        summary["errors"] += 1
        return summary

    content_type = str(app_config.get("HARDCOVER_SYNC_CONTENT_TYPE", "audiobook") or "audiobook")
    library_check = bool(app_config.get("LIBRARY_CHECK_ENABLED", False))
    known_provider_ids = _existing_provider_ids(user_db)

    from shelfmark.core.library_index import is_in_library
    from shelfmark.core.requests_service import RequestServiceError, create_request

    for status_id in _configured_statuses():
        for book in _fetch_status_books(provider, status_id):
            provider_id = str(book.provider_id)
            author = _primary_author(book)

            if provider_id in known_provider_ids:
                summary["skipped"] += 1
                continue
            if _already_downloaded(db_path, book.title, author):
                summary["skipped"] += 1
                continue
            if library_check and is_in_library(book):
                summary["in_library"] += 1
                logger.info("hardcover-sync: '%s' already in Audiobookshelf; skipping", book.title)
                continue

            try:
                create_request(
                    user_db,
                    user_id=user_id,
                    source_hint=None,
                    content_type=content_type,
                    request_level="book",
                    policy_mode="request_book",
                    book_data=_book_to_book_data(book, content_type),
                    note=None,
                )
            except RequestServiceError as exc:
                # Duplicate / max-pending / validation: treat as skip, not failure.
                if exc.code in {"duplicate_pending_request", "max_pending_reached"}:
                    summary["skipped"] += 1
                else:
                    logger.warning("hardcover-sync: could not add '%s': %s", book.title, exc)
                    summary["errors"] += 1
                continue
            except Exception:
                logger.exception("hardcover-sync: unexpected error adding '%s'", book.title)
                summary["errors"] += 1
                continue

            known_provider_ids.add(provider_id)
            summary["added"] += 1
            logger.info("hardcover-sync: added request for '%s' by %s", book.title, author)

    logger.info("hardcover-sync complete: %s", summary)
    return summary
