"""AudiobookBay release source - searches AudiobookBay for audiobook torrents."""

import hashlib
import re
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from shelfmark.core.search_plan import ReleaseSearchPlan
    from shelfmark.metadata_providers import BookMetadata

from shelfmark.core.config import config
from shelfmark.core.languages import normalize_language
from shelfmark.core.logger import setup_logger
from shelfmark.core.text_match import strip_part_suffix, title_tokens_match, tokens
from shelfmark.release_sources import (
    ColumnAlign,
    ColumnColorHint,
    ColumnRenderType,
    ColumnSchema,
    Release,
    ReleaseColumnConfig,
    ReleaseProtocol,
    ReleaseSource,
    register_source,
)
from shelfmark.release_sources.audiobookbay import scraper
from shelfmark.release_sources.audiobookbay.utils import (
    normalize_hostname,
    normalize_search_punctuation,
    parse_size,
)

logger = setup_logger(__name__)
MIN_RELEVANCE_QUERY_WORD_LENGTH = 2


def _coerce_hostname_config(value: object) -> str:
    """Return a normalized ABB hostname from config."""
    return normalize_hostname(value if isinstance(value, str) else "")


def _coerce_positive_int(value: object, default: int) -> int:
    """Return a positive integer config value or the provided default."""
    if isinstance(value, bool):
        return default
    if isinstance(value, int) and value > 0:
        return value
    return default


# Map language names to ISO 639-1 codes (matching frontend color maps)


def _split_title_and_author(raw_title: str) -> tuple[str, str | None]:
    """Split titles in the form 'Title - Author' into title and author.

    Args:
        raw_title: The raw title string from the scrape.

    Returns:
        (title, author) where author is None if split is unavailable.

    """
    if not raw_title:
        return "", None

    cleaned_title = raw_title.strip()
    if " - " not in cleaned_title:
        return cleaned_title, None

    title_part, author_part = cleaned_title.rsplit(" - ", 1)
    title_part = title_part.strip()
    author_part = author_part.strip()
    if not title_part or not author_part:
        return cleaned_title, None

    return title_part, author_part


def _holds_title(rows: list[dict[str, str]], title: str) -> bool:
    """True when some scraped row's own title carries the searched title's words.

    ABB matches a query against whole posts, so a pack whose description lists the
    book comes back as readily as the book itself. Rows that only mention the title
    somewhere in their text are not a reason to stop trying simpler queries.
    """
    return any(
        title_tokens_match(title, set(tokens(normalize_search_punctuation(row.get("title", "")))))
        for row in rows
    )


def _map_language(language: str) -> str | None:
    """Map language name to ISO 639-1 code.

    Args:
        language: Language name (e.g., "English")

    Returns:
        ISO 639-1 code (e.g., "en"), or original string if no mapping found, or None if input is empty

    """
    if not language:
        return None

    # Fall back to the raw value so an unrecognised language is still shown
    # rather than silently dropped from the release row.
    return normalize_language(language) or language.lower().strip()


def _parse_bitrate_to_kbps(bitrate: str | None) -> int | None:
    """Parse bitrate string to an integer Kbps value.

    Args:
        bitrate: Human-readable bitrate (e.g., "128 Kbps")

    Returns:
        Bitrate value in Kbps as integer, or None if parsing fails.

    """
    if not bitrate:
        return None

    match = re.search(r"(\d+(?:\.\d+)?)\s*kbps", bitrate, re.IGNORECASE)
    if not match:
        return None

    try:
        return int(float(match.group(1)))
    except ValueError:
        return None


def _generate_source_id(detail_url: str) -> str:
    """Generate a unique source ID from detail URL."""
    return hashlib.blake2b(detail_url.encode(), digest_size=16).hexdigest()


@register_source("audiobookbay")
class AudiobookBaySource(ReleaseSource):
    """Release source for AudiobookBay audiobook torrents."""

    name = "audiobookbay"
    display_name = "AudiobookBay"
    supported_content_types: ClassVar[list[str]] = ["audiobook"]  # ONLY audiobooks

    def search(
        self,
        book: BookMetadata,
        plan: ReleaseSearchPlan,
        *,
        expand_search: bool = False,
        content_type: str = "ebook",
    ) -> list[Release]:
        """Search AudiobookBay for audiobook releases.

        Args:
            book: Book metadata
            plan: Search plan with query variants
            expand_search: Ignored (always searches)
            content_type: Must be "audiobook" for this source

        Returns:
            List of Release objects

        """
        # Only search for audiobooks
        if content_type != "audiobook":
            return []

        hostname = _coerce_hostname_config(config.get("ABB_HOSTNAME", ""))
        if not hostname:
            logger.debug("AudiobookBay hostname is not configured")
            return []
        max_pages = _coerce_positive_int(config.get("ABB_PAGE_LIMIT", 1), 1)
        exact_phrase = bool(config.get("ABB_EXACT_PHRASE", False))

        # Build search query candidates from plan. Each is tried only when the ones before
        # it brought back no row that holds the title (see ``_holds_title``).
        query_candidates: list[str] = []
        wanted_title = ""
        if plan.manual_query:
            query_candidates.append(plan.manual_query.strip())
            wanted_title = plan.manual_query.strip()
        elif plan.title_variants:
            variant = plan.title_variants[0]
            wanted_title = (variant.title or "").strip()
            # ABB ANDs every term over the whole post, so ", Part II" keeps the volume
            # itself out of the results while a pack whose description lists it gets in.
            # Without the part the volume's own post comes back, and the strict matcher
            # still tells Part One from Part Two.
            titles = [wanted_title]
            if (without_part := strip_part_suffix(wanted_title)) != wanted_title:
                titles.append(without_part)
            for title in titles:
                combined_query = f"{title} {variant.author}".strip()
                if combined_query:
                    query_candidates.append(combined_query)
                if title and title.lower() != combined_query.lower():
                    query_candidates.append(title)
        elif book.title:
            query_candidates.append(book.title.strip())
            wanted_title = book.title.strip()

        # Remove empty and duplicate queries while preserving order.
        deduped_queries: list[str] = []
        seen_queries: set[str] = set()
        for candidate in query_candidates:
            normalized = candidate.strip()
            if not normalized:
                continue
            key = normalized.lower()
            if key in seen_queries:
                continue
            seen_queries.add(key)
            deduped_queries.append(normalized)

        if not deduped_queries:
            logger.debug("No search query available")
            return []

        results: list[dict[str, str]] = []
        seen_links: set[str] = set()
        # Words of every query run, for the relevance check below. Both sides of that
        # comparison are punctuation-normalized: scraped titles carry the typographic
        # forms WordPress renders, queries carry the ASCII ones.
        query_words: set[str] = set()

        try:
            for index, query in enumerate(deduped_queries):
                query_lower = query.lower()
                logger.info("Searching AudiobookBay for: %s", query_lower)

                # Search AudiobookBay
                found = scraper.search_audiobookbay(
                    query=query_lower,
                    max_pages=max_pages,
                    hostname=hostname,
                    exact_phrase=exact_phrase,
                )

                # Fallback to broad matching if exact phrase returns nothing (manual or auto query).
                if exact_phrase and not found:
                    logger.info(
                        "No exact phrase results, retrying AudiobookBay search without quotes"
                    )
                    found = scraper.search_audiobookbay(
                        query=query_lower,
                        max_pages=max_pages,
                        hostname=hostname,
                        exact_phrase=False,
                    )

                query_words |= {
                    word.lower()
                    for word in normalize_search_punctuation(query_lower).split()
                    if len(word) > MIN_RELEVANCE_QUERY_WORD_LENGTH
                }
                for row in found:
                    link = row.get("link") or ""
                    if link in seen_links:
                        continue
                    seen_links.add(link)
                    results.append(row)

                if _holds_title(found, wanted_title):
                    break

                if index < len(deduped_queries) - 1:
                    logger.info(
                        "No AudiobookBay result holds '%s' for '%s', retrying with '%s'",
                        wanted_title,
                        query_lower,
                        deduped_queries[index + 1].lower(),
                    )

            releases = []
            for result in results:
                try:
                    raw_title = result["title"]
                    title, author = _split_title_and_author(raw_title)
                    title_for_filter = normalize_search_punctuation(raw_title).lower()

                    # Basic relevance check: ensure title contains at least one query word
                    # This filters out homepage "Latest" feed items that may leak through
                    if query_words and not any(word in title_for_filter for word in query_words):
                        logger.debug("Filtering out irrelevant result: %s", title)
                        continue

                    # Generate unique source ID
                    source_id = _generate_source_id(result["link"])

                    # Extract and parse metadata
                    format_type = result.get("format")
                    size_str = result.get("size")
                    size_bytes = parse_size(size_str) if size_str else None
                    language_raw = result.get("language")
                    language_code = _map_language(language_raw) if language_raw else "en"
                    bitrate = result.get("bitrate")
                    bitrate_kbps = _parse_bitrate_to_kbps(bitrate)

                    # Create Release object
                    release = Release(
                        source="audiobookbay",
                        source_id=source_id,
                        title=title,
                        format=format_type.lower() if format_type else None,
                        language=language_code,
                        size=size_str,
                        size_bytes=size_bytes,
                        download_url=result["link"],  # Detail page URL (used by handler)
                        info_url=result["link"],  # Make title clickable
                        protocol=ReleaseProtocol.TORRENT,
                        indexer="AudiobookBay",
                        seeders=None,  # Not available on search page
                        peers=None,
                        content_type="audiobook",
                        extra={
                            "preview": result.get("cover"),
                            "detail_url": result["link"],
                            "bitrate": bitrate,
                            "bitrate_value": bitrate_kbps,
                            "posted_date": result.get("posted_date"),
                            "title_raw": raw_title,
                            "language_raw": language_raw,  # Keep original for reference
                            "author": author,  # Parsed author from title pattern
                        },
                    )
                    releases.append(release)
                except (AttributeError, KeyError, TypeError, ValueError) as e:
                    logger.warning("Failed to create release from result: %s", e)
                    continue

        except Exception:
            logger.exception("AudiobookBay search error")
            return []

        else:
            logger.info("Found %s releases from AudiobookBay", len(releases))
            return releases

    def is_available(self) -> bool:
        """Check if AudiobookBay source is enabled and configured."""
        return config.get("ABB_ENABLED", False) is True and bool(
            _coerce_hostname_config(config.get("ABB_HOSTNAME", ""))
        )

    def get_column_config(self) -> ReleaseColumnConfig:
        """Get column configuration for AudiobookBay releases.

        Shows title, language, format, bitrate, and size columns.
        No seeders/peers since ABB doesn't show this on search page.
        """
        return ReleaseColumnConfig(
            columns=[
                ColumnSchema(
                    key="language",
                    label="Lang",
                    render_type=ColumnRenderType.BADGE,
                    align=ColumnAlign.CENTER,
                    width="60px",
                    hide_mobile=True,
                    color_hint=ColumnColorHint(type="map", value="language"),
                    uppercase=True,
                    fallback="",
                ),
                ColumnSchema(
                    key="format",
                    label="Format",
                    render_type=ColumnRenderType.BADGE,
                    align=ColumnAlign.CENTER,
                    width="80px",
                    hide_mobile=False,
                    color_hint=ColumnColorHint(type="map", value="format"),
                    uppercase=True,
                ),
                ColumnSchema(
                    key="extra.bitrate",
                    label="Bitrate",
                    render_type=ColumnRenderType.NUMBER,
                    align=ColumnAlign.CENTER,
                    width="72px",
                    hide_mobile=False,
                    fallback="",
                    sortable=True,
                    sort_key="extra.bitrate_value",
                    setting_key="SHOW_BITRATE_COLUMN",
                ),
                ColumnSchema(
                    key="size",
                    label="Size",
                    render_type=ColumnRenderType.SIZE,
                    align=ColumnAlign.CENTER,
                    width="80px",
                    hide_mobile=False,
                    sortable=True,
                    sort_key="size_bytes",
                ),
            ],
            grid_template="minmax(0,2fr) 60px 80px 72px 80px",
            supported_filters=["format", "language"],  # Enable format and language filters
        )
