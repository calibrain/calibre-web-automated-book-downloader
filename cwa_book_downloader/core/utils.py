"""
Shared utility functions for the CWA Book Downloader.

Provides common helper functions used across the application.
"""

import base64
from typing import Optional


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
