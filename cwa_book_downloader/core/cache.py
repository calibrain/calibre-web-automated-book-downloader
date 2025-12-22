"""Thread-safe in-memory cache with TTL support."""

import threading
import time
from dataclasses import dataclass
from functools import wraps
from typing import Any, Callable, Dict, Optional, TypeVar

from cwa_book_downloader.core.logger import setup_logger

logger = setup_logger(__name__)

T = TypeVar("T")


@dataclass
class CacheEntry:
    """A cached value with expiration time."""
    value: Any
    expires_at: float


class CacheService:
    """Thread-safe in-memory cache with TTL support."""

    def __init__(self, max_size: int = 1000):
        """Initialize cache service.

        Args:
            max_size: Maximum number of entries before oldest are evicted.
        """
        self._cache: Dict[str, CacheEntry] = {}
        self._lock = threading.Lock()
        self._max_size = max_size

    def get(self, key: str) -> Optional[Any]:
        """Get cached value if not expired.

        Args:
            key: Cache key to retrieve.

        Returns:
            Cached value or None if not found/expired.
        """
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None

            if time.time() > entry.expires_at:
                del self._cache[key]
                return None

            return entry.value

    def set(self, key: str, value: Any, ttl: int) -> None:
        """Cache value with TTL.

        Args:
            key: Cache key.
            value: Value to cache.
            ttl: Time to live in seconds.
        """
        with self._lock:
            # Evict oldest entries if at capacity
            if len(self._cache) >= self._max_size:
                self._evict_oldest()

            self._cache[key] = CacheEntry(
                value=value,
                expires_at=time.time() + ttl
            )

    def invalidate(self, key: str) -> bool:
        """Remove specific cache entry.

        Args:
            key: Cache key to remove.

        Returns:
            True if entry was removed, False if not found.
        """
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                return True
            return False

    def clear(self) -> None:
        """Clear all cache entries."""
        with self._lock:
            self._cache.clear()

    def cleanup_expired(self) -> int:
        """Remove all expired entries.

        Returns:
            Number of entries removed.
        """
        with self._lock:
            now = time.time()
            expired_keys = [
                key for key, entry in self._cache.items()
                if entry.expires_at < now
            ]
            for key in expired_keys:
                del self._cache[key]
            return len(expired_keys)

    def _evict_oldest(self) -> None:
        """Evict oldest entries (by expiration time) to make room.

        Called with lock held.
        """
        if not self._cache:
            return

        # Remove ~10% of entries, oldest first
        entries_to_remove = max(1, len(self._cache) // 10)
        sorted_entries = sorted(
            self._cache.items(),
            key=lambda x: x[1].expires_at
        )

        for key, _ in sorted_entries[:entries_to_remove]:
            del self._cache[key]

    def stats(self) -> Dict[str, int]:
        """Get cache statistics.

        Returns:
            Dict with size and max_size.
        """
        with self._lock:
            return {
                "size": len(self._cache),
                "max_size": self._max_size
            }


# Global cache instance for metadata providers
_metadata_cache = CacheService(max_size=1000)


def get_metadata_cache() -> CacheService:
    """Get the global metadata cache instance."""
    return _metadata_cache


def cache_key(*args, **kwargs) -> str:
    """Generate cache key from arguments.

    Args:
        *args: Positional arguments to include in key.
        **kwargs: Keyword arguments to include in key.

    Returns:
        String cache key.
    """
    parts = [str(arg) for arg in args]
    parts.extend(f"{k}={v}" for k, v in sorted(kwargs.items()))
    return ":".join(parts)


def cacheable(
    ttl: Optional[int] = None,
    ttl_key: Optional[str] = None,
    ttl_default: int = 300,
    key_prefix: str = ""
):
    """Decorator for caching function results.

    Args:
        ttl: Static time to live in seconds (use this OR ttl_key, not both).
        ttl_key: Config key to read TTL from (e.g., "METADATA_CACHE_SEARCH_TTL").
        ttl_default: Default TTL if ttl_key not found in config.
        key_prefix: Optional prefix for cache keys.

    Examples:
        @cacheable(ttl=300, key_prefix="hardcover:search")  # Static TTL
        @cacheable(ttl_key="METADATA_CACHE_SEARCH_TTL", key_prefix="hardcover:search")  # Dynamic TTL
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args, **kwargs) -> T:
            # Check if metadata caching is enabled
            from cwa_book_downloader.core.config import config

            if not config.get("METADATA_CACHE_ENABLED", True):
                # Caching disabled, execute function directly
                return func(*args, **kwargs)

            # Determine TTL: static or from config
            if ttl is not None:
                effective_ttl = ttl
            elif ttl_key:
                effective_ttl = config.get(ttl_key, ttl_default)
            else:
                effective_ttl = ttl_default

            # Generate cache key from function name and arguments
            # Skip 'self' argument if present (first arg of method)
            cache_args = args[1:] if args and hasattr(args[0], func.__name__) else args

            key = cache_key(
                key_prefix or func.__name__,
                *cache_args,
                **kwargs
            )

            # Check cache
            cached = _metadata_cache.get(key)
            if cached is not None:
                logger.debug(f"Cache hit: {key}")
                return cached

            # Execute function and cache result
            logger.debug(f"Cache miss: {key}")
            result = func(*args, **kwargs)

            # Only cache non-None results
            if result is not None:
                _metadata_cache.set(key, result, effective_ttl)

            return result

        return wrapper
    return decorator
