"""
Tests for the Prowlarr release cache.
"""

import time
import pytest

# Import the cache module
from cwa_book_downloader.release_sources.prowlarr import cache


class TestProwlarrCache:
    """Tests for release caching functionality."""

    def setup_method(self):
        """Clear cache before each test."""
        # Clear the internal cache
        cache._cache.clear()

    def test_cache_release_stores_data(self):
        """Test that cache_release stores data correctly."""
        release_data = {"title": "Test Book", "size": 1024}
        cache.cache_release("test-id", release_data)

        assert "test-id" in cache._cache
        stored_data, timestamp = cache._cache["test-id"]
        assert stored_data == release_data
        assert isinstance(timestamp, float)

    def test_get_release_returns_cached_data(self):
        """Test that get_release returns cached data."""
        release_data = {"title": "Test Book", "format": "epub"}
        cache.cache_release("get-test-id", release_data)

        result = cache.get_release("get-test-id")
        assert result == release_data

    def test_get_release_returns_none_for_missing_id(self):
        """Test that get_release returns None for non-existent IDs."""
        result = cache.get_release("non-existent-id")
        assert result is None

    def test_get_release_returns_none_for_expired(self, monkeypatch):
        """Test that get_release returns None for expired entries."""
        release_data = {"title": "Old Book"}
        cache.cache_release("expired-id", release_data)

        # Make the entry appear old by modifying the TTL check
        original_ttl = cache.RELEASE_CACHE_TTL
        monkeypatch.setattr(cache, "RELEASE_CACHE_TTL", 0)

        # Wait a tiny bit to ensure expiration
        time.sleep(0.01)

        result = cache.get_release("expired-id")
        assert result is None

        # Restore TTL
        monkeypatch.setattr(cache, "RELEASE_CACHE_TTL", original_ttl)

    def test_remove_release_deletes_entry(self):
        """Test that remove_release removes cached entries."""
        cache.cache_release("remove-id", {"title": "Book to Remove"})
        assert "remove-id" in cache._cache

        cache.remove_release("remove-id")
        assert "remove-id" not in cache._cache

    def test_remove_release_ignores_missing_ids(self):
        """Test that remove_release doesn't raise for missing IDs."""
        # Should not raise any exception
        cache.remove_release("never-existed")

    def test_cleanup_expired_removes_old_entries(self, monkeypatch):
        """Test that cleanup_expired removes old entries."""
        # Add some entries
        cache.cache_release("keep-id", {"title": "Keep This"})
        cache.cache_release("old-id", {"title": "Remove This"})

        # Make old-id appear expired by manipulating its timestamp
        cache._cache["old-id"] = ({"title": "Remove This"}, time.time() - 7200)  # 2 hours old

        removed = cache.cleanup_expired()

        assert removed == 1
        assert "keep-id" in cache._cache
        assert "old-id" not in cache._cache

    def test_get_cache_stats_returns_correct_info(self):
        """Test that get_cache_stats returns accurate information."""
        cache.cache_release("stat-1", {"title": "Book 1"})
        cache.cache_release("stat-2", {"title": "Book 2"})

        stats = cache.get_cache_stats()

        assert stats["size"] == 2
        assert "stat-1" in stats["entries"]
        assert "stat-2" in stats["entries"]

    def test_cache_is_thread_safe(self):
        """Test that cache operations are thread-safe."""
        import threading

        errors = []

        def cache_operations():
            try:
                for i in range(100):
                    cache.cache_release(f"thread-{threading.current_thread().name}-{i}", {"data": i})
                    cache.get_release(f"thread-{threading.current_thread().name}-{i}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=cache_operations, name=f"T{i}") for i in range(5)]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Thread errors: {errors}"
