"""Disk-based image cache with LRU eviction."""

import json
import os
import threading
import time
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import requests

from cwa_book_downloader.core.logger import setup_logger

logger = setup_logger(__name__)

# Image type detection via magic bytes
IMAGE_SIGNATURES = {
    b'\xff\xd8\xff': ('image/jpeg', 'jpg'),
    b'\x89PNG\r\n\x1a\n': ('image/png', 'png'),
    b'GIF87a': ('image/gif', 'gif'),
    b'GIF89a': ('image/gif', 'gif'),
    b'RIFF': ('image/webp', 'webp'),  # WebP starts with RIFF
}

# HTTP headers for image fetching
FETCH_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/129.0.0.0 Safari/537.36',
    'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
}

# Maximum image size to fetch (5 MB)
MAX_IMAGE_SIZE = 5 * 1024 * 1024

# Negative cache TTL (for failed fetches) - 1 hour
NEGATIVE_CACHE_TTL = 3600


def _detect_image_type(data: bytes) -> Optional[Tuple[str, str]]:
    """Detect image type from magic bytes.

    Args:
        data: Image data bytes

    Returns:
        Tuple of (content_type, extension) or None if not recognized
    """
    for signature, (content_type, ext) in IMAGE_SIGNATURES.items():
        if data.startswith(signature):
            return content_type, ext

    # Special case for WebP - check for WEBP after RIFF
    if data.startswith(b'RIFF') and len(data) > 12 and data[8:12] == b'WEBP':
        return 'image/webp', 'webp'

    return None


class ImageCacheService:
    """Persistent image cache with LRU eviction and TTL support."""

    def __init__(self, cache_dir: Path, max_size_mb: int = 500, ttl_seconds: int = 0):
        """Initialize the image cache.

        Args:
            cache_dir: Directory to store cached images
            max_size_mb: Maximum cache size in megabytes
            ttl_seconds: Time-to-live in seconds (0 = forever)
        """
        self.cache_dir = cache_dir
        self.max_size_bytes = max_size_mb * 1024 * 1024
        self.ttl_seconds = ttl_seconds
        self.index_path = cache_dir / "cache_index.json"
        self._lock = threading.RLock()
        self._index: Dict[str, Dict[str, Any]] = {}

        # Stats tracking
        self._hits = 0
        self._misses = 0

        # Ensure cache directory exists
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # Load existing index
        self._load_index()

    def _load_index(self) -> None:
        """Load cache index from disk."""
        try:
            if self.index_path.exists():
                with open(self.index_path, 'r') as f:
                    self._index = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Failed to load cache index, starting fresh: {e}")
            self._index = {}

    def _save_index(self) -> None:
        """Save cache index to disk."""
        try:
            # Write to temp file first, then rename for atomicity
            temp_path = self.index_path.with_suffix('.tmp')
            with open(temp_path, 'w') as f:
                json.dump(self._index, f)
            temp_path.rename(self.index_path)
        except IOError as e:
            logger.error(f"Failed to save cache index: {e}")

    def _get_image_path(self, cache_id: str, ext: str) -> Path:
        """Get the file path for a cached image."""
        return self.cache_dir / f"{cache_id}.{ext}"

    def _is_expired(self, entry: Dict[str, Any]) -> bool:
        """Check if a cache entry is expired."""
        if self.ttl_seconds == 0:
            return False

        cached_at = entry.get('cached_at', 0)
        return (time.time() - cached_at) > self.ttl_seconds

    def _is_negative_expired(self, entry: Dict[str, Any]) -> bool:
        """Check if a negative cache entry is expired."""
        if not entry.get('negative', False):
            return False

        cached_at = entry.get('cached_at', 0)
        return (time.time() - cached_at) > NEGATIVE_CACHE_TTL

    def _calculate_total_size(self) -> int:
        """Calculate total size of cached images."""
        return sum(entry.get('size', 0) for entry in self._index.values())

    def _evict_if_needed(self, required_space: int = 0) -> None:
        """Evict old entries if cache is over size limit.

        Uses LRU eviction based on accessed_at timestamp.
        """
        current_size = self._calculate_total_size()
        target_size = self.max_size_bytes - required_space

        if current_size <= target_size:
            return

        # Sort entries by accessed_at (oldest first)
        sorted_entries = sorted(
            self._index.items(),
            key=lambda x: x[1].get('accessed_at', 0)
        )

        evicted_count = 0
        for cache_id, entry in sorted_entries:
            if current_size <= target_size:
                break

            # Delete the image file
            ext = entry.get('ext', 'jpg')
            image_path = self._get_image_path(cache_id, ext)
            try:
                if image_path.exists():
                    image_path.unlink()
            except IOError as e:
                logger.warning(f"Failed to delete cached image {cache_id}: {e}")

            # Update tracking
            current_size -= entry.get('size', 0)
            del self._index[cache_id]
            evicted_count += 1

        if evicted_count > 0:
            logger.info(f"Evicted {evicted_count} images from cache (LRU)")
            self._save_index()

    def get(self, cache_id: str) -> Optional[Tuple[bytes, str]]:
        """Get a cached image.

        Args:
            cache_id: Cache key (book ID or composite key)

        Returns:
            Tuple of (image_data, content_type) or None if not cached/expired
        """
        with self._lock:
            entry = self._index.get(cache_id)

            if not entry:
                # Try reloading from disk (handles multiprocess case)
                self._load_index()
                entry = self._index.get(cache_id)

            if not entry:
                self._misses += 1
                return None

            # Check for negative cache (failed fetch)
            if entry.get('negative', False):
                if self._is_negative_expired(entry):
                    # Negative cache expired, allow retry
                    del self._index[cache_id]
                    self._save_index()
                    self._misses += 1
                    return None
                # Still in negative cache, return None (don't retry)
                return None

            # Check for expired entry
            if self._is_expired(entry):
                # Remove expired entry
                ext = entry.get('ext', 'jpg')
                image_path = self._get_image_path(cache_id, ext)
                try:
                    if image_path.exists():
                        image_path.unlink()
                except IOError:
                    pass
                del self._index[cache_id]
                self._save_index()
                self._misses += 1
                return None

            # Try to read the cached image
            ext = entry.get('ext', 'jpg')
            content_type = entry.get('content_type', 'image/jpeg')
            image_path = self._get_image_path(cache_id, ext)

            try:
                if not image_path.exists():
                    # File missing, remove from index
                    del self._index[cache_id]
                    self._save_index()
                    self._misses += 1
                    return None

                with open(image_path, 'rb') as f:
                    data = f.read()

                # Update accessed time
                entry['accessed_at'] = time.time()
                self._save_index()

                self._hits += 1
                return data, content_type

            except IOError as e:
                logger.warning(f"Failed to read cached image {cache_id}: {e}")
                self._misses += 1
                return None

    def put(self, cache_id: str, data: bytes, content_type: str) -> bool:
        """Store an image in the cache.

        Args:
            cache_id: Cache key
            data: Image data bytes
            content_type: MIME type of the image

        Returns:
            True if stored successfully
        """
        with self._lock:
            # Detect image type for extension
            detected = _detect_image_type(data)
            if detected:
                content_type, ext = detected
            else:
                # Fall back to content-type header
                if 'jpeg' in content_type or 'jpg' in content_type:
                    ext = 'jpg'
                elif 'png' in content_type:
                    ext = 'png'
                elif 'gif' in content_type:
                    ext = 'gif'
                elif 'webp' in content_type:
                    ext = 'webp'
                else:
                    ext = 'jpg'  # Default

            image_size = len(data)

            # Evict if needed to make room
            self._evict_if_needed(image_size)

            # Write image to disk
            image_path = self._get_image_path(cache_id, ext)
            try:
                with open(image_path, 'wb') as f:
                    f.write(data)
            except IOError as e:
                logger.warning(f"Failed to write cached image {cache_id}: {e}")
                return False

            # Update index
            now = time.time()
            self._index[cache_id] = {
                'ext': ext,
                'content_type': content_type,
                'size': image_size,
                'cached_at': now,
                'accessed_at': now,
                'negative': False,
            }
            self._save_index()
            return True

    def put_negative(self, cache_id: str) -> None:
        """Store a negative cache entry (failed fetch).

        Args:
            cache_id: Cache key
        """
        with self._lock:
            self._index[cache_id] = {
                'negative': True,
                'cached_at': time.time(),
            }
            self._save_index()

    def delete(self, cache_id: str) -> bool:
        """Delete a single cache entry.

        Args:
            cache_id: Cache key

        Returns:
            True if entry existed and was deleted
        """
        with self._lock:
            entry = self._index.get(cache_id)
            if not entry:
                return False

            # Delete file if it exists
            if not entry.get('negative', False):
                ext = entry.get('ext', 'jpg')
                image_path = self._get_image_path(cache_id, ext)
                try:
                    if image_path.exists():
                        image_path.unlink()
                except IOError as e:
                    logger.warning(f"Failed to delete cached image {cache_id}: {e}")

            del self._index[cache_id]
            self._save_index()
            return True

    def clear(self) -> int:
        """Clear all cached images.

        Returns:
            Number of entries cleared
        """
        with self._lock:
            count = len(self._index)

            # Delete all image files
            for cache_id, entry in self._index.items():
                if not entry.get('negative', False):
                    ext = entry.get('ext', 'jpg')
                    image_path = self._get_image_path(cache_id, ext)
                    try:
                        if image_path.exists():
                            image_path.unlink()
                    except IOError:
                        pass

            # Clear index
            self._index = {}
            self._save_index()

            # Reset stats
            self._hits = 0
            self._misses = 0

            logger.info(f"Cleared {count} entries from image cache")
            return count

    def stats(self) -> Dict[str, Any]:
        """Get cache statistics.

        Returns:
            Dict with size, count, hit rate, etc.
        """
        with self._lock:
            total_size = self._calculate_total_size()
            entry_count = len(self._index)
            negative_count = sum(1 for e in self._index.values() if e.get('negative', False))
            total_requests = self._hits + self._misses
            hit_rate = (self._hits / total_requests * 100) if total_requests > 0 else 0

            return {
                'entry_count': entry_count,
                'negative_count': negative_count,
                'total_size_bytes': total_size,
                'total_size_mb': round(total_size / (1024 * 1024), 2),
                'max_size_mb': self.max_size_bytes / (1024 * 1024),
                'hits': self._hits,
                'misses': self._misses,
                'hit_rate': round(hit_rate, 1),
            }

    def fetch_and_cache(self, cache_id: str, url: str) -> Optional[Tuple[bytes, str]]:
        """Fetch an image from URL and cache it.

        Args:
            cache_id: Cache key
            url: URL to fetch from

        Returns:
            Tuple of (image_data, content_type) or None on failure
        """
        try:

            response = requests.get(
                url,
                timeout=(5, 10),
                headers=FETCH_HEADERS,
                stream=True,
            )
            response.raise_for_status()

            # Validate content type
            content_type = response.headers.get('content-type', '')
            if not content_type.startswith('image/'):
                logger.warning(f"Invalid content type for cover: {content_type}")
                self.put_negative(cache_id)
                return None

            # Read with size limit
            data = BytesIO()
            for chunk in response.iter_content(chunk_size=8192):
                data.write(chunk)
                if data.tell() > MAX_IMAGE_SIZE:
                    logger.warning(f"Cover image too large: {url}")
                    self.put_negative(cache_id)
                    return None

            image_data = data.getvalue()

            if not image_data:
                logger.warning(f"Empty image response: {url}")
                self.put_negative(cache_id)
                return None

            # Store in cache
            if self.put(cache_id, image_data, content_type):
                # Get the actual content type from detection
                detected = _detect_image_type(image_data)
                if detected:
                    content_type = detected[0]
                return image_data, content_type

            return None

        except requests.exceptions.Timeout:
            logger.warning(f"Timeout fetching cover: {url}")
            # Don't cache timeout - it's transient
            return None
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                self.put_negative(cache_id)
            else:
                logger.warning(f"HTTP error fetching cover: {e}")
            return None
        except Exception as e:
            logger.warning(f"Error fetching cover: {e}")
            return None


# Singleton instance (initialized lazily when config is available)
_instance: Optional[ImageCacheService] = None
_instance_lock = threading.Lock()


def get_image_cache() -> ImageCacheService:
    """Get the singleton image cache instance.

    Lazily initializes using config values.
    """
    global _instance

    if _instance is None:
        with _instance_lock:
            if _instance is None:
                from cwa_book_downloader.core.config import config
                from cwa_book_downloader.config.env import CONFIG_DIR

                cache_dir = CONFIG_DIR / "covers"
                max_size_mb = config.get("COVERS_CACHE_MAX_SIZE_MB", 500)
                ttl_days = config.get("COVERS_CACHE_TTL", 0)
                ttl_seconds = ttl_days * 86400 if ttl_days > 0 else 0

                _instance = ImageCacheService(
                    cache_dir=cache_dir,
                    max_size_mb=max_size_mb,
                    ttl_seconds=ttl_seconds,
                )
                logger.info(f"Initialized image cache: {cache_dir} (max {max_size_mb}MB, TTL {ttl_days} days)")

    return _instance


def reset_image_cache() -> None:
    """Reset the singleton instance (for testing or config changes)."""
    global _instance
    with _instance_lock:
        _instance = None
