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

_CONTENT_TYPE_TO_CONFIG_KEY = {
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


def get_ingest_dir(content_type: Optional[str] = None) -> Path:
    """Get the ingest directory for a content type, falling back to default."""
    from cwa_book_downloader.core.config import config

    default_ingest_dir = Path(config.get("INGEST_DIR", "/cwa-book-ingest"))

    if not content_type:
        return default_ingest_dir

    # Normalize content type for lookup
    content_type_lower = content_type.lower().strip()

    # Look up the config key for this content type
    config_key = _CONTENT_TYPE_TO_CONFIG_KEY.get(content_type_lower)
    if not config_key:
        return default_ingest_dir

    # Get the custom directory from config (empty string means use default)
    custom_dir = config.get(config_key, "")
    if custom_dir:
        return Path(custom_dir)

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
