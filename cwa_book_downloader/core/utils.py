"""
Shared utility functions for the CWA Book Downloader.

Provides common helper functions used across the application.
"""

import base64
from pathlib import Path
from typing import Optional


CONTENT_TYPES = [
    "book (fiction)",
    "book (non-fiction)",
    "book (unknown)",
    "magazine",
    "comic book",
    "audiobook",
    "standards document",
    "musical score",
    "other",
]

# Maps AA content types to their config keys for content-type routing
# Used when AA_CONTENT_TYPE_ROUTING is enabled
_AA_CONTENT_TYPE_TO_CONFIG_KEY = {
    "book (fiction)": "AA_CONTENT_TYPE_DIR_FICTION",
    "book (non-fiction)": "AA_CONTENT_TYPE_DIR_NON_FICTION",
    "book (unknown)": "AA_CONTENT_TYPE_DIR_UNKNOWN",
    "magazine": "AA_CONTENT_TYPE_DIR_MAGAZINE",
    "comic book": "AA_CONTENT_TYPE_DIR_COMIC",
    "audiobook": "AA_CONTENT_TYPE_DIR_AUDIOBOOK",
    "standards document": "AA_CONTENT_TYPE_DIR_STANDARDS",
    "musical score": "AA_CONTENT_TYPE_DIR_MUSICAL_SCORE",
    "other": "AA_CONTENT_TYPE_DIR_OTHER",
}

# Legacy mapping - kept for backwards compatibility during migration
_LEGACY_CONTENT_TYPE_TO_CONFIG_KEY = {
    "book (fiction)": "INGEST_DIR_BOOK_FICTION",
    "book (non-fiction)": "INGEST_DIR_BOOK_NON_FICTION",
    "book (unknown)": "INGEST_DIR_BOOK_UNKNOWN",
    "magazine": "INGEST_DIR_MAGAZINE",
    "comic book": "INGEST_DIR_COMIC_BOOK",
    "audiobook": "INGEST_DIR_AUDIOBOOK",
    "standards document": "INGEST_DIR_STANDARDS_DOCUMENT",
    "musical score": "INGEST_DIR_MUSICAL_SCORE",
    "other": "INGEST_DIR_OTHER",
}


def get_destination(is_audiobook: bool = False) -> Path:
    """Get the base destination directory.

    Args:
        is_audiobook: If True, returns audiobook destination (with fallback to books destination)

    Returns:
        Path to the destination directory
    """
    from cwa_book_downloader.core.config import config

    if is_audiobook:
        # Audiobook destination with fallback to main destination
        audiobook_dest = config.get("DESTINATION_AUDIOBOOK", "")
        if audiobook_dest:
            return Path(audiobook_dest)

    # Main destination (also fallback for audiobooks)
    # Check new setting first, then legacy INGEST_DIR
    destination = config.get("DESTINATION", "") or config.get("INGEST_DIR", "/cwa-book-ingest")
    return Path(destination)


def get_aa_content_type_dir(content_type: Optional[str] = None) -> Optional[Path]:
    """Get override directory for Anna's Archive content-type routing.

    Only returns a path if AA_CONTENT_TYPE_ROUTING is enabled AND
    a custom directory is configured for the given content type.

    Args:
        content_type: The AA content type (e.g., "book (fiction)", "magazine")

    Returns:
        Path to the override directory if configured, None otherwise
    """
    from cwa_book_downloader.core.config import config

    # Check if content-type routing is enabled
    if not config.get("AA_CONTENT_TYPE_ROUTING", False):
        # Also check legacy setting for backwards compatibility
        if not config.get("USE_CONTENT_TYPE_DIRECTORIES", False):
            return None

    if not content_type:
        return None

    # Normalize content type for lookup
    content_type_lower = content_type.lower().strip()

    # Try new AA-specific config keys first
    config_key = _AA_CONTENT_TYPE_TO_CONFIG_KEY.get(content_type_lower)
    if config_key:
        custom_dir = config.get(config_key, "")
        if custom_dir:
            return Path(custom_dir)

    # Fall back to legacy config keys for backwards compatibility
    legacy_key = _LEGACY_CONTENT_TYPE_TO_CONFIG_KEY.get(content_type_lower)
    if legacy_key:
        custom_dir = config.get(legacy_key, "")
        if custom_dir:
            return Path(custom_dir)

    return None


def get_ingest_dir(content_type: Optional[str] = None) -> Path:
    """Get the ingest directory for a content type, falling back to default.

    DEPRECATED: Use get_destination() and get_aa_content_type_dir() instead.
    Kept for backwards compatibility during migration.
    """
    from cwa_book_downloader.core.config import config

    # Check new DESTINATION setting first, then legacy INGEST_DIR
    default_ingest_dir = Path(config.get("DESTINATION", "") or config.get("INGEST_DIR", "/cwa-book-ingest"))

    if not content_type:
        return default_ingest_dir

    # Check for content-type override
    override_dir = get_aa_content_type_dir(content_type)
    if override_dir:
        return override_dir

    return default_ingest_dir


def transform_cover_url(cover_url: Optional[str], cache_id: str) -> Optional[str]:
    """
    Transform an external cover URL to a local proxy URL when caching is enabled.

    When cover caching is enabled, external cover image URLs are transformed
    to local proxy URLs that cache the images on first access. This reduces
    external requests and provides a consistent caching layer.

    Args:
        cover_url: Original cover URL (external or already local)
        cache_id: Unique identifier for the cache entry (e.g., "provider_bookid")

    Returns:
        Transformed URL if caching enabled and URL is external, otherwise original URL
    """
    if not cover_url:
        return cover_url

    # Skip if already a local URL (starts with /)
    if cover_url.startswith('/'):
        return cover_url

    # Check if cover caching is enabled
    from cwa_book_downloader.config.env import is_covers_cache_enabled
    if not is_covers_cache_enabled():
        return cover_url

    # Encode the original URL and create a proxy URL
    encoded_url = base64.urlsafe_b64encode(cover_url.encode()).decode()
    return f"/api/covers/{cache_id}?url={encoded_url}"
