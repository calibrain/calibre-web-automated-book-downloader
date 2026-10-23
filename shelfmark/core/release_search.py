"""Shared release-search helpers.

Extracted from the ``/api/releases`` route so the same per-source search logic can
be reused outside the HTTP route (for example by background automation) without
going through Flask. Behaviour for the HTTP route is preserved: the route delegates
its inner per-source search to :func:`search_source_releases`.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from shelfmark.core.logger import setup_logger
from shelfmark.core.search_plan import build_release_search_plan

if TYPE_CHECKING:
    from shelfmark.core.models import SearchFilters
    from shelfmark.metadata_providers import BookMetadata
    from shelfmark.release_sources import Release, ReleaseSource

logger = setup_logger(__name__)

# Mirror of main._OPERATIONAL_ERRORS so a misbehaving source can't crash a caller.
_OPERATIONAL_ERRORS = (OSError, RuntimeError, TypeError, ValueError, sqlite3.Error)


def search_source_releases(
    source_name: str,
    search_book: BookMetadata,
    *,
    languages: list[str] | None = None,
    manual_query: str | None = None,
    indexers: list[str] | None = None,
    expand_search: bool = False,
    content_type: str = "ebook",
    source_filters: SearchFilters | None = None,
    user_id: int | None = None,
) -> tuple[ReleaseSource | None, list[Release], str | None]:
    """Search a single release source, returning any error instead of raising.

    Returns ``(source, releases, error_message)``. On failure ``source`` is ``None``
    and ``error_message`` describes the problem. ``user_id`` lets the search plan
    pick up that user's default languages when no explicit filter is given.
    """
    from shelfmark.release_sources import SourceUnavailableError, get_source

    try:
        source = get_source(source_name)
    except ValueError:
        return None, [], f"Unknown source: {source_name}"

    try:
        plan = build_release_search_plan(
            search_book,
            languages=languages,
            manual_query=manual_query,
            indexers=indexers,
            source_filters=source_filters,
            user_id=user_id,
        )

        if plan.source_filters is not None:
            planned_query = plan.manual_query or plan.primary_query
            planned_query_type = "query"
        elif plan.manual_query:
            planned_query = plan.manual_query
            planned_query_type = "manual"
        elif not expand_search and plan.isbn_candidates:
            planned_query = plan.isbn_candidates[0]
            planned_query_type = "isbn"
        else:
            planned_query = plan.primary_query
            planned_query_type = "title_author"

        logger.debug(
            "Searching %s: %s='%s' (title='%s', authors=%s, expand=%s, content_type=%s)",
            source_name,
            planned_query_type,
            planned_query,
            search_book.title,
            search_book.authors,
            expand_search,
            content_type,
        )

        releases = source.search(
            search_book, plan, expand_search=expand_search, content_type=content_type
        )
    except (SourceUnavailableError, *_OPERATIONAL_ERRORS) as exc:
        logger.warning("Release search failed for source %s: %s", source_name, exc)
        return None, [], f"{source_name}: {exc!s}"
    else:
        return source, releases, None
