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


def _extract_format(title: str) -> Optional[str]:
    """
    Extract format from release title with smart parsing.

    Priority:
    1. File extension at end of title or in quotes (e.g., ".azw3")
    2. File extension anywhere in title
    3. Format keyword in brackets/parentheses (e.g., "[EPUB]", "(PDF)")
    4. Fallback to first format keyword found
    """
    title_lower = title.lower()

    # 1. Look for file extensions (most reliable) - pattern: .format at word boundary or end
    #    This catches ".azw3", ".epub", etc.
    for fmt in EBOOK_FORMATS:
        # Match .format at end of string or followed by non-alphanumeric
        pattern = rf'\.{fmt}(?:["\'\s\]\)]|$)'
        if re.search(pattern, title_lower):
            return fmt

    # 2. Look for format in brackets/parentheses (common in release names)
    #    e.g., "[EPUB]", "(PDF)", "{mobi}"
    for fmt in EBOOK_FORMATS:
        pattern = rf'[\[\(\{{]{fmt}[\]\)\}}]'
        if re.search(pattern, title_lower):
            return fmt

    # 3. Look for format as standalone word (not part of another word)
    #    e.g., "epub" but not "republic"
    for fmt in EBOOK_FORMATS:
        # Match format as whole word
        pattern = rf'\b{fmt}\b'
        if re.search(pattern, title_lower):
            return fmt

    return None


def _get_protocol(result: dict) -> str:
    """
    Get protocol from Prowlarr result.

    Uses the protocol field directly if available, otherwise infers from URL.
    Returns user-friendly labels: "torrent" or "nzb".
    """
    # Prowlarr provides protocol directly - use it
    protocol = result.get("protocol", "").lower()
    if protocol == "usenet":
        return "nzb"
    if protocol == "torrent":
        return "torrent"

    # Fallback: infer from download URL
    download_url = result.get("downloadUrl") or result.get("magnetUrl") or ""
    url_lower = download_url.lower()
    if url_lower.startswith("magnet:") or ".torrent" in url_lower:
        return "torrent"
    if ".nzb" in url_lower:
        return "nzb"

    return "unknown"


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


def _prowlarr_result_to_release(result: dict) -> Release:
    """
    Convert a Prowlarr search result to a Release object.

    Uses structured fields from Prowlarr when available:
    - protocol: Direct from Prowlarr
    - fileName: For format detection (more reliable than title)
    - categories: To confirm ebook type
    - grabs: Download count
    """
    title = result.get("title", "Unknown")
    size_bytes = result.get("size")
    download_url = result.get("downloadUrl") or result.get("magnetUrl")
    info_url = result.get("infoUrl") or result.get("guid")
    indexer = result.get("indexer", "Unknown")
    protocol = _get_protocol(result)
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
        extra={
            "publish_date": result.get("publishDate"),
            "categories": result.get("categories", []),
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
    """

    name = "prowlarr"
    display_name = "Prowlarr"

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
                ),
                ColumnSchema(
                    key="format",
                    label="Format",
                    render_type=ColumnRenderType.BADGE,
                    align=ColumnAlign.CENTER,
                    width="70px",
                    hide_mobile=False,
                    color_hint=ColumnColorHint(type="map", value="format"),
                    uppercase=True,
                ),
                ColumnSchema(
                    key="size",
                    label="Size",
                    render_type=ColumnRenderType.SIZE,
                    align=ColumnAlign.CENTER,
                    width="80px",
                    hide_mobile=False,
                ),
            ],
            grid_template="minmax(0,2fr) minmax(80px,1fr) 60px 70px 70px 80px",
            leading_cell=LeadingCellConfig(type=LeadingCellType.NONE),  # No leading cell for Prowlarr
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
        languages: Optional[List[str]] = None
    ) -> List[Release]:
        """
        Search Prowlarr for releases matching the book.

        Makes separate API calls for each selected indexer to ensure
        all indexers are properly queried regardless of their capabilities.

        Args:
            book: Book metadata to search for
            expand_search: Ignored - Prowlarr always uses title+author search
            languages: Ignored - Prowlarr doesn't support language filtering

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

        # Book categories: 7000 (Books parent), 7020 (EBook), 7030 (Comics), etc.
        # We search the parent category which includes all subcategories
        book_categories = [7000]

        logger.debug(f"Searching Prowlarr: query='{query}', indexers={indexer_ids}")

        all_results = []
        try:
            # Make separate API call for each indexer
            for indexer_id in indexer_ids:
                try:
                    raw_results = client.search(query=query, indexer_ids=[indexer_id], categories=book_categories)
                    if raw_results:
                        all_results.extend(raw_results)
                except Exception as e:
                    logger.warning(f"Search failed for indexer {indexer_id}: {e}")
                    continue

            results = [_prowlarr_result_to_release(r) for r in all_results]

            # Log consolidated summary
            if results:
                torrent_count = sum(1 for r in results if r.protocol == "torrent")
                nzb_count = sum(1 for r in results if r.protocol == "nzb")
                # Get unique indexer names
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
