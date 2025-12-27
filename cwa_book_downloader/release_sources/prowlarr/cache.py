"""
Prowlarr release cache.

Stores search results so the handler can look up releases by source_id.
This keeps all Prowlarr-specific data within the plugin.
"""

import time
from threading import Lock
from typing import Dict, Optional

from cwa_book_downloader.core.logger import setup_logger

logger = setup_logger(__name__)

# Cache TTL in seconds (1 hour - releases should be downloaded within this time)
RELEASE_CACHE_TTL = 3600

# Internal cache storage: source_id -> (release_dict, timestamp)
_cache: Dict[str, tuple] = {}
_cache_lock = Lock()


def cache_release(source_id: str, release_data: dict) -> None:
    """
    Cache a release by its source_id.

    Args:
        source_id: The unique identifier for this release (GUID)
        release_data: The full Prowlarr API result dict
    """
    with _cache_lock:
        _cache[source_id] = (release_data, time.time())


def get_release(source_id: str) -> Optional[dict]:
    """
    Get a cached release by source_id.

    Args:
        source_id: The unique identifier for the release

    Returns:
        The cached release dict, or None if not found or expired
    """
    with _cache_lock:
        if source_id not in _cache:
            logger.debug(f"Prowlarr release not in cache: {source_id}")
            return None

        release_data, cached_at = _cache[source_id]
        age = time.time() - cached_at

        if age > RELEASE_CACHE_TTL:
            # Expired - remove from cache
            del _cache[source_id]
            logger.debug(f"Prowlarr release expired: {source_id}")
            return None

        return release_data


def remove_release(source_id: str) -> None:
    """
    Remove a release from the cache (e.g., after successful download).

    Args:
        source_id: The unique identifier for the release
    """
    with _cache_lock:
        if source_id in _cache:
            del _cache[source_id]
            logger.debug(f"Removed Prowlarr release from cache: {source_id}")


def cleanup_expired() -> int:
    """
    Remove all expired entries from the cache.

    Returns:
        Number of entries removed
    """
    current_time = time.time()
    removed = 0

    with _cache_lock:
        expired_ids = [
            source_id
            for source_id, (_, cached_at) in _cache.items()
            if current_time - cached_at > RELEASE_CACHE_TTL
        ]
        for source_id in expired_ids:
            del _cache[source_id]
            removed += 1

    if removed:
        logger.debug(f"Cleaned up {removed} expired Prowlarr cache entries")

    return removed


def get_cache_stats() -> dict:
    """
    Get cache statistics for debugging.

    Returns:
        Dict with cache stats
    """
    with _cache_lock:
        return {
            "size": len(_cache),
            "entries": list(_cache.keys()),
        }
