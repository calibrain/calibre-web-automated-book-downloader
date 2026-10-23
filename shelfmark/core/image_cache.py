"""Disk-based image cache with LRU eviction."""

import hashlib
import json
import threading
import time
from http import HTTPStatus
from io import BytesIO
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin, urlparse

import requests

from shelfmark.core.logger import setup_logger
from shelfmark.core.request_helpers import coerce_int
from shelfmark.download.network import get_ssl_verify, hostname_resolves_publicly

if TYPE_CHECKING:
    from pathlib import Path

logger = setup_logger(__name__)

# Image type detection via magic bytes
IMAGE_SIGNATURES = {
    b"\xff\xd8\xff": ("image/jpeg", "jpg"),
    b"\x89PNG\r\n\x1a\n": ("image/png", "png"),
    b"GIF87a": ("image/gif", "gif"),
    b"GIF89a": ("image/gif", "gif"),
    b"RIFF": ("image/webp", "webp"),  # WebP starts with RIFF
}

# HTTP headers for image fetching
FETCH_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/129.0.0.0 Safari/537.36",
    "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

# Maximum image size to fetch (5 MB)
MAX_IMAGE_SIZE = 5 * 1024 * 1024
MAX_REDIRECTS = 5

# Negative cache TTL (for failed fetches) - 1 hour
NEGATIVE_CACHE_TTL = 3600

# Transient failure cache TTL (for timeouts/connection errors) - 60 seconds
# Short enough to retry soon, long enough to prevent spam during one page view
TRANSIENT_CACHE_TTL = 60

# A hit only moves the entry's last-access time, which orders LRU eviction. The whole
# index is rewritten to disk, so that is done at most this often instead of per hit.
# A crash inside the window loses some access times, nothing else.
ACCESS_FLUSH_SECONDS = 30

_MIN_WEBP_HEADER_LENGTH = 12
HTTP_NOT_FOUND = HTTPStatus.NOT_FOUND


def cover_cache_key(url: str) -> str:
    """Return the cache key for a cover fetched from `url`.

    The key is derived from the URL alone, so a request can only ever fill the
    entry for the image it names. The book id in the proxy path is not part of
    it: any caller could otherwise pick another book's id and have the image
    they chose served to everyone under it. One URL also maps to one entry no
    matter which id the proxy link carries.
    """
    return hashlib.sha256(url.strip().encode()).hexdigest()[:32]


def _detect_image_type(data: bytes) -> tuple[str, str] | None:
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
    if data.startswith(b"RIFF") and len(data) > _MIN_WEBP_HEADER_LENGTH and data[8:12] == b"WEBP":
        return "image/webp", "webp"

    return None


class ImageCacheService:
    """Persistent image cache with LRU eviction and TTL support."""

    def __init__(self, cache_dir: Path, max_size_mb: int = 500, ttl_seconds: int = 0) -> None:
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
        self._index: dict[str, dict[str, Any]] = {}
        self._last_saved = time.monotonic()

        # Stats tracking
        self._hits = 0
        self._misses = 0

        # Ensure cache directory exists
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # Load existing index and sync with files on disk (once at startup)
        self._load_index()
        self._sync_index_with_files()

    def _load_index(self) -> None:
        """Load cache index from disk."""
        if not self.index_path.exists():
            self._index = {}
            return

        try:
            with self.index_path.open() as f:
                self._index = json.load(f)
        except OSError, json.JSONDecodeError:
            self._index = {}

    def _sync_index_with_files(self) -> None:
        """Sync cache index with actual files on disk.

        - Adds entries for files that exist but aren't in index
        - Removes entries for files that no longer exist (non-negative only)
        - Preserves negative cache entries (they have no files)
        """
        image_extensions = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
        added_count = 0
        removed_count = 0

        # Build set of files that exist on disk
        existing_files: dict[str, Path] = {}
        for file_path in self.cache_dir.iterdir():
            if not file_path.is_file():
                continue
            if file_path.suffix.lower() not in image_extensions:
                continue
            existing_files[file_path.stem] = file_path

        # Add files that aren't in the index
        for cache_id, file_path in existing_files.items():
            if cache_id in self._index:
                continue

            ext = file_path.suffix.lstrip(".")
            stat = file_path.stat()

            # Detect content type
            try:
                with file_path.open("rb") as f:
                    header = f.read(16)
                detected = _detect_image_type(header)
                content_type = detected[0] if detected else f"image/{ext}"
            except OSError:
                content_type = f"image/{ext}"

            self._index[cache_id] = {
                "ext": ext,
                "content_type": content_type,
                "size": stat.st_size,
                "cached_at": stat.st_mtime,
                "accessed_at": stat.st_mtime,
            }
            added_count += 1

        # Remove index entries for missing files (skip negative cache entries)
        stale_entries = []
        for cache_id, entry in self._index.items():
            if entry.get("negative", False):
                continue  # Negative entries don't have files
            if cache_id not in existing_files:
                stale_entries.append(cache_id)

        for cache_id in stale_entries:
            del self._index[cache_id]
            removed_count += 1

        if added_count > 0 or removed_count > 0:
            self._save_index()

    def _save_index(self) -> None:
        """Save cache index to disk."""
        try:
            # Write to temp file first, then rename for atomicity
            temp_path = self.index_path.with_suffix(".tmp")
            with temp_path.open("w") as f:
                json.dump(self._index, f)
            temp_path.rename(self.index_path)
            self._last_saved = time.monotonic()
        except OSError:
            pass

    def _get_image_path(self, cache_id: str, ext: str) -> Path:
        """Get the file path for a cached image."""
        return self.cache_dir / f"{cache_id}.{ext}"

    def _is_expired(self, entry: dict[str, Any]) -> bool:
        """Check if a cache entry is expired."""
        if self.ttl_seconds == 0:
            return False
        return (time.time() - entry.get("cached_at", 0)) > self.ttl_seconds

    def _is_negative_expired(self, entry: dict[str, Any]) -> bool:
        """Check if a negative cache entry is expired.

        Transient failures (timeouts) expire after TRANSIENT_CACHE_TTL (60s).
        Permanent failures (404s) expire after NEGATIVE_CACHE_TTL (1 hour).
        """
        if not entry.get("negative", False):
            return False

        cached_at = entry.get("cached_at", 0)
        ttl = TRANSIENT_CACHE_TTL if entry.get("transient", False) else NEGATIVE_CACHE_TTL
        return (time.time() - cached_at) > ttl

    def _calculate_total_size(self) -> int:
        """Calculate total size of cached images."""
        return sum(entry.get("size", 0) for entry in self._index.values())

    def _evict_if_needed(self, required_space: int = 0) -> None:
        """Evict old entries if cache is over size limit.

        Uses LRU eviction based on accessed_at timestamp.
        """
        current_size = self._calculate_total_size()
        target_size = self.max_size_bytes - required_space

        if current_size <= target_size:
            return

        # Sort entries by accessed_at (oldest first)
        sorted_entries = sorted(self._index.items(), key=lambda x: x[1].get("accessed_at", 0))

        evicted_count = 0
        for cache_id, entry in sorted_entries:
            if current_size <= target_size:
                break

            # Delete the image file
            ext = entry.get("ext", "jpg")
            image_path = self._get_image_path(cache_id, ext)
            try:
                if image_path.exists():
                    image_path.unlink()
            except OSError:
                pass

            # Update tracking
            current_size -= entry.get("size", 0)
            del self._index[cache_id]
            evicted_count += 1

        if evicted_count > 0:
            self._save_index()

    def get(self, cache_id: str) -> tuple[bytes, str] | None:
        """Get a cached image.

        Args:
            cache_id: Cache key (book ID or composite key)

        Returns:
            Tuple of (image_data, content_type) or None if not cached/expired

        """
        with self._lock:
            entry = self._index.get(cache_id)

            # Try reloading from disk if not found (handles multiprocess case)
            if not entry:
                self._load_index()
                entry = self._index.get(cache_id)
                if not entry:
                    self._misses += 1
                    return None

            # Check for negative cache (failed fetch)
            if entry.get("negative", False):
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
                ext = entry.get("ext", "jpg")
                image_path = self._get_image_path(cache_id, ext)
                try:
                    if image_path.exists():
                        image_path.unlink()
                except OSError:
                    pass
                del self._index[cache_id]
                self._save_index()
                self._misses += 1
                return None

            # Try to read the cached image
            ext = entry.get("ext", "jpg")
            content_type = entry.get("content_type", "image/jpeg")
            image_path = self._get_image_path(cache_id, ext)
            result: tuple[bytes, str] | None = None

            try:
                if not image_path.exists():
                    # File missing, remove from index
                    del self._index[cache_id]
                    self._save_index()
                    self._misses += 1
                    return None

                with image_path.open("rb") as f:
                    data = f.read()

                # Update accessed time. Written out with the next save, or here once
                # enough time has passed, not once per hit.
                entry["accessed_at"] = time.time()
                if time.monotonic() - self._last_saved >= ACCESS_FLUSH_SECONDS:
                    self._save_index()
                result = data, content_type

            except OSError:
                self._misses += 1
                return None
            else:
                self._hits += 1
                return result

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
            # Fall back to content-type header
            elif "jpeg" in content_type or "jpg" in content_type:
                ext = "jpg"
            elif "png" in content_type:
                ext = "png"
            elif "gif" in content_type:
                ext = "gif"
            elif "webp" in content_type:
                ext = "webp"
            else:
                ext = "jpg"  # Default

            image_size = len(data)

            # Evict if needed to make room
            self._evict_if_needed(image_size)

            # Write image to disk
            image_path = self._get_image_path(cache_id, ext)
            try:
                with image_path.open("wb") as f:
                    f.write(data)
            except OSError:
                return False

            # Update index
            now = time.time()
            self._index[cache_id] = {
                "ext": ext,
                "content_type": content_type,
                "size": image_size,
                "cached_at": now,
                "accessed_at": now,
                "negative": False,
            }
            self._save_index()
            return True

    def put_negative(self, cache_id: str, *, transient: bool = False) -> None:
        """Store a negative cache entry (failed fetch).

        Args:
            cache_id: Cache key
            transient: If True, uses shorter TTL (for timeouts/connection errors)

        """
        with self._lock:
            self._index[cache_id] = {
                "negative": True,
                "transient": transient,
                "cached_at": time.time(),
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
            if not entry.get("negative", False):
                ext = entry.get("ext", "jpg")
                image_path = self._get_image_path(cache_id, ext)
                try:
                    if image_path.exists():
                        image_path.unlink()
                except OSError:
                    pass

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
                if not entry.get("negative", False):
                    ext = entry.get("ext", "jpg")
                    image_path = self._get_image_path(cache_id, ext)
                    try:
                        if image_path.exists():
                            image_path.unlink()
                    except OSError:
                        pass

            # Clear index
            self._index = {}
            self._save_index()

            # Reset stats
            self._hits = 0
            self._misses = 0

            return count

    def stats(self) -> dict[str, Any]:
        """Get cache statistics.

        Returns:
            Dict with size, count, hit rate, etc.

        """
        with self._lock:
            total_size = self._calculate_total_size()
            entry_count = len(self._index)
            negative_count = sum(1 for e in self._index.values() if e.get("negative", False))
            total_requests = self._hits + self._misses
            hit_rate = (self._hits / total_requests * 100) if total_requests > 0 else 0

            return {
                "entry_count": entry_count,
                "negative_count": negative_count,
                "total_size_bytes": total_size,
                "total_size_mb": round(total_size / (1024 * 1024), 2),
                "max_size_mb": self.max_size_bytes / (1024 * 1024),
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": round(hit_rate, 1),
            }

    @staticmethod
    def _prepare_safe_url(url: str) -> str | None:
        """Prepare and validate a URL before fetching it."""
        if "\\" in url or any(ord(char) < 32 for char in url):
            return None

        try:
            prepared = requests.Request("GET", url).prepare()
            prepared_url = prepared.url
            if not isinstance(prepared_url, str):
                return None
            parsed = urlparse(prepared_url)
            hostname = parsed.hostname
        except requests.exceptions.RequestException, ValueError:
            return None

        if not prepared_url:
            return None

        if "\\" in prepared_url or any(ord(char) < 32 for char in prepared_url):
            return None

        netloc_lower = parsed.netloc.lower()
        if "%2f" in netloc_lower or "%5c" in netloc_lower:
            return None

        if parsed.scheme not in ("http", "https"):
            return None
        if not hostname:
            return None

        if not hostname_resolves_publicly(hostname, use_custom_dns=True):
            return None

        return prepared_url

    @staticmethod
    def _is_safe_url(url: str) -> bool:
        """Check that a URL is safe to fetch (no SSRF to internal resources)."""
        return ImageCacheService._prepare_safe_url(url) is not None

    def _fetch_safe_response(self, url: str) -> requests.Response | None:
        """Fetch a URL after validating the initial URL and each redirect."""
        current_url = self._prepare_safe_url(url)
        if not current_url:
            logger.warning("Blocked request to disallowed URL: %s", url)
            return None

        for _ in range(MAX_REDIRECTS + 1):
            response = requests.get(
                current_url,
                timeout=(5, 10),
                headers=FETCH_HEADERS,
                stream=True,
                verify=get_ssl_verify(current_url),
                allow_redirects=False,
            )

            if not response.is_redirect:
                return response

            location = response.headers.get("location")
            response.close()
            if not location:
                return None

            redirect_url = urljoin(current_url, location)
            next_url = self._prepare_safe_url(redirect_url)
            if not next_url:
                logger.warning("Blocked redirect to disallowed URL: %s", redirect_url)
                return None
            current_url = next_url

        return None

    def fetch_and_cache(self, cache_id: str, url: str) -> tuple[bytes, str] | None:
        """Fetch an image from URL and cache it.

        Args:
            cache_id: Cache key
            url: URL to fetch from

        Returns:
            Tuple of (image_data, content_type) or None on failure

        """
        cached_data: tuple[bytes, str] | None = None
        try:
            response = self._fetch_safe_response(url)
            if response is None:
                return None
            response.raise_for_status()

            # Validate content type
            content_type = response.headers.get("content-type", "")
            if not content_type.startswith("image/"):
                self.put_negative(cache_id)
                return None

            # Read with size limit
            data = BytesIO()
            for chunk in response.iter_content(chunk_size=8192):
                data.write(chunk)
                if data.tell() > MAX_IMAGE_SIZE:
                    self.put_negative(cache_id)
                    return None

            image_data = data.getvalue()

            if not image_data:
                self.put_negative(cache_id)
                return None

            # Store in cache
            if self.put(cache_id, image_data, content_type):
                # Get the actual content type from detection
                detected = _detect_image_type(image_data)
                if detected:
                    content_type = detected[0]
                cached_data = image_data, content_type

        except requests.exceptions.Timeout:
            self.put_negative(cache_id, transient=True)
            return None
        except requests.exceptions.ConnectionError:
            self.put_negative(cache_id, transient=True)
            return None
        except requests.exceptions.HTTPError as e:
            is_404 = e.response is not None and e.response.status_code == HTTP_NOT_FOUND
            self.put_negative(cache_id, transient=not is_404)
            return None
        except requests.exceptions.RequestException:
            return None
        else:
            return cached_data


# Singleton instance (initialized lazily when config is available)
_instance: ImageCacheService | None = None
_instance_lock = threading.Lock()


def get_image_cache() -> ImageCacheService:
    """Get the singleton image cache instance.

    Lazily initializes using config values.
    """
    global _instance

    if _instance is None:
        with _instance_lock:
            if _instance is None:
                from shelfmark.config.env import CONFIG_DIR
                from shelfmark.core.config import config

                cache_dir = CONFIG_DIR / "covers"
                max_size_mb = coerce_int(config.get("COVERS_CACHE_MAX_SIZE_MB", 500), 500)
                ttl_days = coerce_int(config.get("COVERS_CACHE_TTL", 0), 0)
                ttl_seconds = ttl_days * 86400 if ttl_days > 0 else 0

                _instance = ImageCacheService(
                    cache_dir=cache_dir,
                    max_size_mb=max_size_mb,
                    ttl_seconds=ttl_seconds,
                )
                logger.debug(
                    "Initialized image cache: %s (max %sMB, TTL %s days)",
                    cache_dir,
                    max_size_mb,
                    ttl_days,
                )

    return _instance


def reset_image_cache() -> None:
    """Reset the singleton instance (for testing or config changes)."""
    global _instance
    with _instance_lock:
        _instance = None
