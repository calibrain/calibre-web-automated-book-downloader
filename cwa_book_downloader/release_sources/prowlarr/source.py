"""
Prowlarr release source implementation.

Implements the ReleaseSource interface to search Prowlarr indexers
for book releases (torrents and usenet).
"""

import re
from typing import List, Optional

from cwa_book_downloader.core.config import config
from cwa_book_downloader.core.logger import setup_logger
from cwa_book_downloader.metadata_providers import BookMetadata
from cwa_book_downloader.release_sources import (
    Release,
    ReleaseSource,
    register_source,
    ReleaseColumnConfig,
    ColumnSchema,
    ColumnRenderType,
    ColumnAlign,
    ColumnColorHint,
    LeadingCellConfig,
    LeadingCellType,
)
from cwa_book_downloader.release_sources.prowlarr.api import ProwlarrClient
from cwa_book_downloader.release_sources.prowlarr.cache import cache_release
from cwa_book_downloader.release_sources.prowlarr.utils import get_protocol_display

logger = setup_logger(__name__)


def _parse_size(size_bytes: Optional[int]) -> Optional[str]:
    """Convert bytes to human-readable size string."""
    if size_bytes is None or size_bytes <= 0:
        return None

    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(size_bytes)
    unit_index = 0

    while size >= 1024 and unit_index < len(units) - 1:
        size /= 1024
        unit_index += 1

    if unit_index == 0:
        return f"{int(size)} {units[unit_index]}"

    return f"{size:.1f} {units[unit_index]}"


# Common ebook formats in priority order
EBOOK_FORMATS = ["epub", "mobi", "azw3", "azw", "pdf", "cbz", "cbr", "fb2", "djvu", "lit", "pdb", "txt"]

# Common audiobook formats
AUDIOBOOK_FORMATS = ["m4b", "mp3", "m4a", "flac", "ogg", "wma", "aac", "wav", "opus"]

# Combined list for format detection (audiobook formats first for priority)
ALL_BOOK_FORMATS = AUDIOBOOK_FORMATS + EBOOK_FORMATS


def _extract_format(title: str) -> Optional[str]:
    """
    Extract format from release title with smart parsing.

    Supports both ebook formats (epub, mobi, etc.) and audiobook formats (m4b, mp3, etc.).

    Priority:
    1. File extension at end of title or in quotes (e.g., ".azw3", ".m4b")
    2. Format keyword in brackets/parentheses (e.g., "[EPUB]", "(MP3)")
    3. Format as standalone word (not part of another word)
    """
    title_lower = title.lower()

    # 1. Look for file extensions (most reliable) - pattern: .format at word boundary or end
    #    This catches ".azw3", ".epub", ".m4b", ".mp3", etc.
    for fmt in ALL_BOOK_FORMATS:
        # Match .format at end of string or followed by non-alphanumeric
        pattern = rf'\.{fmt}(?:["\'\s\]\)]|$)'
        if re.search(pattern, title_lower):
            return fmt

    # 2. Look for format in brackets/parentheses (common in release names)
    #    e.g., "[EPUB]", "(PDF)", "{mobi}", "[M4B]", "(MP3)"
    for fmt in ALL_BOOK_FORMATS:
        pattern = rf'[\[\(\{{]{fmt}[\]\)\}}]'
        if re.search(pattern, title_lower):
            return fmt

    # 3. Look for format as standalone word (not part of another word)
    #    e.g., "epub" but not "republic", "m4b" but not "m4b123"
    for fmt in ALL_BOOK_FORMATS:
        # Match format as whole word
        pattern = rf'\b{fmt}\b'
        if re.search(pattern, title_lower):
            return fmt

    return None


def _extract_language(title: str) -> Optional[str]:
    """
    Extract language from release title.

    Common patterns:
    - [German], (French), {Spanish}
    - German, French, etc. as standalone words
    - Language codes like [DE], [FR], [ES]
    """
    title_lower = title.lower()

    # Common language names and their codes
    languages = {
        "english": "en", "eng": "en", "[en]": "en", "(en)": "en",
        "german": "de", "deutsch": "de", "[de]": "de", "(de)": "de", "ger": "de",
        "french": "fr", "français": "fr", "[fr]": "fr", "(fr)": "fr", "fra": "fr",
        "spanish": "es", "español": "es", "[es]": "es", "(es)": "es", "spa": "es",
        "italian": "it", "italiano": "it", "[it]": "it", "(it)": "it", "ita": "it",
        "portuguese": "pt", "[pt]": "pt", "(pt)": "pt", "por": "pt",
        "dutch": "nl", "nederlands": "nl", "[nl]": "nl", "(nl)": "nl", "nld": "nl",
        "russian": "ru", "[ru]": "ru", "(ru)": "ru", "rus": "ru",
        "polish": "pl", "polski": "pl", "[pl]": "pl", "(pl)": "pl", "pol": "pl",
        "chinese": "zh", "[zh]": "zh", "(zh)": "zh", "chi": "zh",
        "japanese": "ja", "[ja]": "ja", "(ja)": "ja", "jpn": "ja",
        "korean": "ko", "[ko]": "ko", "(ko)": "ko", "kor": "ko",
    }

    for lang_pattern, lang_code in languages.items():
        if lang_pattern in title_lower:
            return lang_code

    return None


# Prowlarr category IDs for content type detection
# See: https://wiki.servarr.com/prowlarr/cardigann-yml-definition#categories
AUDIOBOOK_CATEGORY_IDS = {3000, 3030}  # 3000 = Audio, 3030 = Audio/Audiobook
EBOOK_CATEGORY_IDS = {7000, 7020}  # 7000 = Books, 7020 = Books/Ebook


def _detect_content_type_from_categories(categories: list, fallback: str = "book") -> str:
    """
    Detect content type from Prowlarr category IDs.

    Prowlarr returns categories as a list of dicts with 'id' and 'name' keys,
    or sometimes just category IDs directly.

    Args:
        categories: List of category objects from Prowlarr result
        fallback: Content type to use if categories don't indicate a specific type

    Returns:
        "audiobook" if audiobook categories detected, "book" otherwise
    """
    # Normalize fallback - convert "ebook" to "book" for display consistency
    if fallback == "ebook":
        fallback = "book"

    if not categories:
        return fallback

    # Extract category IDs from the nested structure
    cat_ids = set()
    for cat in categories:
        if isinstance(cat, dict):
            cat_id = cat.get("id")
            if cat_id is not None:
                cat_ids.add(cat_id)
        elif isinstance(cat, int):
            cat_ids.add(cat)

    # Check for audiobook categories first (more specific)
    if cat_ids & AUDIOBOOK_CATEGORY_IDS:
        return "audiobook"

    # Check for ebook categories
    if cat_ids & EBOOK_CATEGORY_IDS:
        return "book"

    # Fall back to normalized content_type if no recognized categories
    return fallback


def _prowlarr_result_to_release(result: dict, search_content_type: str = "ebook") -> Release:
    """
    Convert a Prowlarr search result to a Release object.

    Uses structured fields from Prowlarr when available:
    - protocol: Direct from Prowlarr
    - fileName: For format detection (more reliable than title)
    - categories: To detect content type (audiobook vs ebook)
    - grabs: Download count

    Args:
        result: Raw Prowlarr API result
        search_content_type: Content type from search, used as fallback if
                            categories don't indicate a specific type
    """
    title = result.get("title", "Unknown")
    size_bytes = result.get("size")
    download_url = result.get("downloadUrl") or result.get("magnetUrl")
    info_url = result.get("infoUrl") or result.get("guid")
    indexer = result.get("indexer", "Unknown")
    protocol = get_protocol_display(result)
    seeders = result.get("seeders")
    leechers = result.get("leechers")
    # Format peers display string: "seeders / leechers"
    peers_display = f"{seeders} / {leechers}" if (seeders is not None and leechers is not None) else None
    grabs = result.get("grabs")

    # For format detection, prefer fileName over title (often cleaner)
    file_name = result.get("fileName", "")
    format_detected = _extract_format(file_name) if file_name else None
    if not format_detected:
        format_detected = _extract_format(title)

    # Extract language from title (Prowlarr doesn't provide this structured)
    language = _extract_language(title)

    # Detect content type from categories (per-result), with search type as fallback
    categories = result.get("categories", [])
    content_type = _detect_content_type_from_categories(categories, search_content_type)

    # Build the source_id from GUID or generate from indexer + title
    source_id = result.get("guid") or f"{indexer}:{hash(title)}"

    # Cache the raw Prowlarr result so handler can look it up by source_id
    cache_release(source_id, result)

    return Release(
        source="prowlarr",
        source_id=source_id,
        title=title,
        format=format_detected,
        language=language,
        size=_parse_size(size_bytes),
        size_bytes=size_bytes,
        download_url=download_url,
        info_url=info_url,
        protocol=protocol,
        indexer=indexer,
        seeders=seeders if protocol == "torrent" else None,
        peers=peers_display if protocol == "torrent" else None,
        content_type=content_type,  # Detected per-result from categories
        extra={
            "publish_date": result.get("publishDate"),
            "categories": categories,
            "indexer_id": result.get("indexerId"),
            "files": result.get("files"),
            "grabs": grabs,
        },
    )


@register_source("prowlarr")
class ProwlarrSource(ReleaseSource):
    """
    Prowlarr release source.

    Searches Prowlarr indexers for book releases (torrents and usenet).
    Supports both ebooks (category 7000) and audiobooks (category 3030).
    """

    name = "prowlarr"
    display_name = "Prowlarr"
    supported_content_types = ["ebook", "audiobook"]  # Explicitly declare support for both

    def __init__(self):
        self.last_search_type: Optional[str] = None

    @classmethod
    def get_column_config(cls) -> ReleaseColumnConfig:
        """Column configuration for Prowlarr releases."""
        return ReleaseColumnConfig(
            columns=[
                ColumnSchema(
                    key="indexer",
                    label="Indexer",
                    render_type=ColumnRenderType.TEXT,
                    align=ColumnAlign.LEFT,
                    width="minmax(80px, 1fr)",
                    hide_mobile=True,
                    sortable=True,
                ),
                ColumnSchema(
                    key="protocol",
                    label="Type",
                    render_type=ColumnRenderType.BADGE,
                    align=ColumnAlign.CENTER,
                    width="60px",
                    hide_mobile=False,
                    color_hint=ColumnColorHint(type="map", value="download_type"),
                    uppercase=True,
                ),
                ColumnSchema(
                    key="peers",
                    label="Peers",
                    render_type=ColumnRenderType.PEERS,
                    align=ColumnAlign.CENTER,
                    width="70px",
                    hide_mobile=True,
                    fallback="-",
                    sortable=True,
                    sort_key="seeders",
                ),
                ColumnSchema(
                    key="content_type",
                    label="Type",
                    render_type=ColumnRenderType.BADGE,
                    align=ColumnAlign.CENTER,
                    width="90px",
                    hide_mobile=False,
                    color_hint=ColumnColorHint(type="map", value="content_type"),
                    uppercase=True,
                    fallback="-",
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
            grid_template="minmax(0,2fr) minmax(80px,1fr) 60px 70px 90px 80px",
            leading_cell=LeadingCellConfig(type=LeadingCellType.NONE),  # No leading cell for Prowlarr
            supported_filters=[],  # Prowlarr has unreliable format/language metadata; content_type is auto-detected
        )

    def _get_client(self) -> Optional[ProwlarrClient]:
        """Get a configured Prowlarr client or None if not configured."""
        url = config.get("PROWLARR_URL", "")
        api_key = config.get("PROWLARR_API_KEY", "")

        if not url or not api_key:
            return None

        return ProwlarrClient(url, api_key)

    def _get_selected_indexer_ids(self) -> Optional[List[int]]:
        """
        Get list of selected indexer IDs from config.

        Returns None if no indexers are selected (search all).
        Returns list of IDs if specific indexers are selected.
        """
        selected = config.get("PROWLARR_INDEXERS", "")
        if not selected:
            return None

        # Handle both list (from JSON config) and string (from env var)
        try:
            if isinstance(selected, list):
                # Already a list from JSON config
                ids = [int(x) for x in selected if x]
            else:
                # Comma-separated string from env var
                ids = [int(x.strip()) for x in selected.split(",") if x.strip()]
            return ids if ids else None
        except (ValueError, TypeError) as e:
            logger.warning(f"Invalid PROWLARR_INDEXERS format: {selected} ({e})")
            return None

    def search(
        self,
        book: BookMetadata,
        expand_search: bool = False,
        languages: Optional[List[str]] = None,
        content_type: str = "ebook"
    ) -> List[Release]:
        """
        Search Prowlarr for releases matching the book.

        Makes separate API calls for each selected indexer to ensure
        all indexers are properly queried regardless of their capabilities.

        Args:
            book: Book metadata to search for
            expand_search: If True, skip category filtering (broader search)
            languages: Ignored - Prowlarr doesn't support language filtering
            content_type: "ebook" or "audiobook" - determines search categories

        Returns:
            List of Release objects
        """
        client = self._get_client()
        if not client:
            logger.warning("Prowlarr not configured - skipping search")
            return []

        # Build search query
        query_parts = []
        if book.title:
            query_parts.append(book.title)
        if book.authors:
            # Use first author only - authors may be a list or a single string
            # that contains multiple comma-separated names (from frontend)
            first_author = book.authors[0]
            # If first author contains comma, split and use only the primary author
            if "," in first_author:
                first_author = first_author.split(",")[0].strip()
            query_parts.append(first_author)

        query = " ".join(query_parts)
        if not query:
            # Try ISBN as fallback
            query = book.isbn_13 or book.isbn_10 or ""

        if not query:
            logger.warning("No search query available for book")
            return []

        # Get selected indexer IDs from config
        indexer_ids = self._get_selected_indexer_ids()

        if not indexer_ids:
            logger.warning("No indexers selected - configure indexers in Prowlarr settings")
            return []

        # Get search categories based on content type
        # Audiobooks use 3030 (Audio/Audiobook), ebooks use 7000 (Books)
        if content_type == "audiobook":
            search_categories = [3030]  # Audio/Audiobook category
        else:
            search_categories = [7000]  # Books category

        if expand_search:
            # Expand search: search all indexers without category filtering
            categories = None
            self.last_search_type = "expanded"
        else:
            categories = search_categories
            self.last_search_type = "categories"

        logger.debug(f"Searching Prowlarr: query='{query}', indexers={indexer_ids}, categories={categories}")

        all_results = []
        try:
            for indexer_id in indexer_ids:
                try:
                    raw_results = client.search(query=query, indexer_ids=[indexer_id], categories=categories)
                    if raw_results:
                        all_results.extend(raw_results)
                except Exception as e:
                    logger.warning(f"Search failed for indexer {indexer_id}: {e}")

            # Auto-expand: if no results with categories and auto-expand enabled, retry without categories
            auto_expand_enabled = config.get("PROWLARR_AUTO_EXPAND", False)
            logger.debug(f"Auto-expand check: no_results={not all_results}, has_categories={bool(categories)}, auto_expand_enabled={auto_expand_enabled}")
            if not all_results and categories and auto_expand_enabled:
                logger.info("Prowlarr: no results with category filter, auto-expanding search")
                for indexer_id in indexer_ids:
                    try:
                        raw_results = client.search(query=query, indexer_ids=[indexer_id], categories=None)
                        if raw_results:
                            all_results.extend(raw_results)
                    except Exception as e:
                        logger.warning(f"Expanded search failed for indexer {indexer_id}: {e}")
                self.last_search_type = "expanded"

            results = [_prowlarr_result_to_release(r, content_type) for r in all_results]

            if results:
                torrent_count = sum(1 for r in results if r.protocol == "torrent")
                nzb_count = sum(1 for r in results if r.protocol == "nzb")
                indexers = sorted(set(r.indexer for r in results if r.indexer))
                indexer_str = ", ".join(indexers) if indexers else "unknown"
                logger.info(f"Prowlarr: {len(results)} results ({torrent_count} torrent, {nzb_count} nzb) from {indexer_str}")
            else:
                logger.debug("Prowlarr: no results found")

            return results

        except Exception as e:
            logger.error(f"Prowlarr search failed: {e}")
            return []

    def is_available(self) -> bool:
        """Check if Prowlarr is enabled and configured."""
        if not config.get("PROWLARR_ENABLED", False):
            return False
        url = config.get("PROWLARR_URL", "")
        api_key = config.get("PROWLARR_API_KEY", "")
        return bool(url and api_key)
