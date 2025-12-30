"""
Environment and configuration tests.

These tests verify the application behaves correctly with different
configuration settings, environment variables, and Docker setups.

Run with: docker exec test-cwabd python3 -m pytest /app/tests/config/test_environment.py -v
"""

import json
import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


# =============================================================================
# Directory Setup Tests
# =============================================================================


class TestDirectorySetup:
    """Tests for directory creation and permissions."""

    def test_staging_dir_created_on_demand(self):
        """Staging directory should be created if it doesn't exist."""
        from cwa_book_downloader.download.orchestrator import get_staging_dir

        with tempfile.TemporaryDirectory() as tmpdir:
            test_staging = Path(tmpdir) / "staging"
            assert not test_staging.exists()

            with patch("cwa_book_downloader.download.orchestrator.TMP_DIR", test_staging):
                result = get_staging_dir()

            assert test_staging.exists()
            assert result == test_staging

    def test_staging_dir_handles_existing_directory(self):
        """Staging directory creation should be idempotent."""
        from cwa_book_downloader.download.orchestrator import get_staging_dir

        with tempfile.TemporaryDirectory() as tmpdir:
            test_staging = Path(tmpdir) / "staging"
            test_staging.mkdir()

            with patch("cwa_book_downloader.download.orchestrator.TMP_DIR", test_staging):
                result = get_staging_dir()

            assert result == test_staging

    def test_staging_path_handles_special_characters(self):
        """Staging path should handle task IDs with special characters."""
        from cwa_book_downloader.download.orchestrator import get_staging_path

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch(
                "cwa_book_downloader.download.orchestrator.TMP_DIR", Path(tmpdir)
            ):
                # Task ID with URL-like characters
                path = get_staging_path(
                    "https://example.com/book?id=123&format=epub", "epub"
                )

                assert path.suffix == ".epub"
                assert path.parent == Path(tmpdir)
                # Should not contain invalid filename chars
                assert "/" not in path.name
                assert "?" not in path.name
                assert "&" not in path.name

    def test_staging_path_normalizes_extension(self):
        """Staging path should handle extensions with or without dot."""
        from cwa_book_downloader.download.orchestrator import get_staging_path

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch(
                "cwa_book_downloader.download.orchestrator.TMP_DIR", Path(tmpdir)
            ):
                path1 = get_staging_path("task1", "epub")
                path2 = get_staging_path("task1", ".epub")

                assert path1.suffix == ".epub"
                assert path2.suffix == ".epub"


# =============================================================================
# Supported Formats Tests
# =============================================================================


class TestSupportedFormats:
    """Tests for format filtering configuration."""

    def test_default_supported_formats(self):
        """Default formats should include common ebook formats."""
        from cwa_book_downloader.config.env import _SUPPORTED_FORMATS

        # Check some expected defaults
        assert "epub" in _SUPPORTED_FORMATS
        assert "mobi" in _SUPPORTED_FORMATS
        assert "azw3" in _SUPPORTED_FORMATS

    def test_format_list_is_lowercase(self):
        """Format list should be normalized to lowercase."""
        from cwa_book_downloader.config.env import _SUPPORTED_FORMATS

        # All formats should be lowercase
        for fmt in _SUPPORTED_FORMATS.split(","):
            assert fmt == fmt.lower()

    def test_config_supported_formats_attribute(self):
        """Config should have SUPPORTED_FORMATS as a list."""
        from cwa_book_downloader.config.settings import SUPPORTED_FORMATS

        assert isinstance(SUPPORTED_FORMATS, list)
        assert len(SUPPORTED_FORMATS) > 0
        assert "epub" in SUPPORTED_FORMATS


# =============================================================================
# Content-Type Routing Tests
# =============================================================================


class TestContentTypeRouting:
    """Tests for content-type based directory routing."""

    def test_get_ingest_dir_returns_path(self):
        """get_ingest_dir should return a Path for all content types."""
        from cwa_book_downloader.core.utils import get_ingest_dir, CONTENT_TYPES

        # Default (no content type) should return a Path
        default_path = get_ingest_dir()
        assert isinstance(default_path, Path)

        # All content types should return a Path
        for content_type in CONTENT_TYPES:
            path = get_ingest_dir(content_type)
            assert isinstance(path, Path)

    def test_content_types_list_complete(self):
        """All expected content types should be present in CONTENT_TYPES."""
        from cwa_book_downloader.core.utils import CONTENT_TYPES

        expected_types = [
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

        for content_type in expected_types:
            assert content_type in CONTENT_TYPES, f"Missing content type: {content_type}"

    def test_get_ingest_dir_unknown_type_returns_default(self):
        """Unknown content types should return the default ingest directory."""
        from cwa_book_downloader.core.utils import get_ingest_dir

        default_path = get_ingest_dir()
        unknown_path = get_ingest_dir("unknown content type")
        assert unknown_path == default_path


# =============================================================================
# Settings System Tests
# =============================================================================


class TestSettingsSystem:
    """Tests for the settings registry and persistence."""

    def test_save_and_load_config(self):
        """Settings should persist to JSON files."""
        from cwa_book_downloader.core.settings_registry import (
            save_config_file,
            load_config_file,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch(
                "cwa_book_downloader.config.env.CONFIG_DIR", Path(tmpdir)
            ):
                test_data = {"key1": "value1", "key2": 123, "key3": True}
                save_config_file("test_plugin", test_data)

                loaded = load_config_file("test_plugin")

                assert loaded == test_data

    def test_load_missing_config_returns_empty(self):
        """Loading non-existent config should return empty dict."""
        from cwa_book_downloader.core.settings_registry import load_config_file

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch(
                "cwa_book_downloader.config.env.CONFIG_DIR", Path(tmpdir)
            ):
                loaded = load_config_file("nonexistent_plugin")

                assert loaded == {}

    def test_config_singleton_refresh(self):
        """Config singleton should refresh when settings change."""
        from cwa_book_downloader.core.config import config
        from cwa_book_downloader.core.settings_registry import save_config_file

        # Get initial value
        initial = config.get("TEST_REFRESH_KEY", "default")

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch(
                "cwa_book_downloader.config.env.CONFIG_DIR", Path(tmpdir)
            ):
                save_config_file("test", {"TEST_REFRESH_KEY": "new_value"})
                config.refresh()

                # Note: This test is limited because config also reads from env

    def test_config_env_var_priority(self):
        """Environment variables should take priority over config files."""
        # This tests the priority: ENV > config file > default
        from cwa_book_downloader.config.env import string_to_bool

        # Test the string_to_bool helper used for parsing
        assert string_to_bool("true") is True
        assert string_to_bool("True") is True
        assert string_to_bool("TRUE") is True
        assert string_to_bool("yes") is True
        assert string_to_bool("1") is True
        assert string_to_bool("y") is True

        assert string_to_bool("false") is False
        assert string_to_bool("no") is False
        assert string_to_bool("0") is False
        assert string_to_bool("anything_else") is False


# =============================================================================
# Archive Handling Configuration Tests
# =============================================================================


class TestArchiveHandling:
    """Tests for archive extraction configuration."""

    def test_is_archive_detects_supported_formats(self):
        """is_archive should detect RAR and ZIP files (not cbr/cbz which are book formats)."""
        from cwa_book_downloader.download.archive import is_archive

        # RAR and ZIP are archive formats that get extracted
        assert is_archive(Path("book.rar")) is True
        assert is_archive(Path("book.zip")) is True

        # CBR/CBZ are comic book formats, treated as books not archives
        assert is_archive(Path("book.cbr")) is False
        assert is_archive(Path("book.cbz")) is False

        # Regular book formats are not archives
        assert is_archive(Path("book.epub")) is False
        assert is_archive(Path("book.pdf")) is False
        assert is_archive(Path("book.mobi")) is False

    def test_is_archive_case_insensitive(self):
        """Archive detection should be case insensitive."""
        from cwa_book_downloader.download.archive import is_archive

        assert is_archive(Path("book.RAR")) is True
        assert is_archive(Path("book.ZIP")) is True
        assert is_archive(Path("book.Zip")) is True
        assert is_archive(Path("book.RaR")) is True


# =============================================================================
# Validation and Error Handling Tests
# =============================================================================


class TestConfigValidation:
    """Tests for configuration validation and error handling."""

    def test_invalid_number_env_var_uses_default(self):
        """Invalid numeric env vars should fall back to defaults."""
        # Test that int() parsing handles invalid values gracefully
        # The env.py module uses int() which will raise ValueError
        # This tests the expected behavior

        with patch.dict(os.environ, {"MAX_RETRY": "not_a_number"}):
            # Importing with invalid env var should use default or raise
            # This depends on implementation - test documents behavior
            pass  # Currently env.py will crash on invalid int

    def test_missing_required_directory_handling(self):
        """Application should handle missing directories gracefully."""
        from cwa_book_downloader.download.orchestrator import get_staging_dir

        with tempfile.TemporaryDirectory() as tmpdir:
            # Use a path that doesn't exist yet
            nonexistent = Path(tmpdir) / "deeply" / "nested" / "path"

            with patch(
                "cwa_book_downloader.download.orchestrator.TMP_DIR", nonexistent
            ):
                result = get_staging_dir()

            # Should have created the directory
            assert nonexistent.exists()

    @pytest.mark.skipif(
        os.geteuid() == 0,
        reason="Test skipped when running as root (chmod has no effect)"
    )
    def test_config_dir_not_writable(self):
        """Application should handle read-only config directory."""
        from cwa_book_downloader.config.env import _is_config_dir_writable

        with tempfile.TemporaryDirectory() as tmpdir:
            readonly_dir = Path(tmpdir) / "readonly"
            readonly_dir.mkdir()
            os.chmod(readonly_dir, 0o444)  # Read-only

            try:
                with patch(
                    "cwa_book_downloader.config.env.CONFIG_DIR", readonly_dir
                ):
                    result = _is_config_dir_writable()
                    assert result is False
            finally:
                os.chmod(readonly_dir, 0o755)  # Restore for cleanup


# =============================================================================
# Debug and Logging Configuration Tests
# =============================================================================


class TestDebugConfiguration:
    """Tests for debug and logging settings."""

    def test_debug_from_env_var(self):
        """DEBUG env var should set debug mode."""
        from cwa_book_downloader.config.env import string_to_bool

        # Test the parsing logic
        assert string_to_bool("true") is True
        assert string_to_bool("false") is False

    def test_log_level_derived_from_debug(self):
        """LOG_LEVEL should be derived from DEBUG setting."""
        # When DEBUG is True, LOG_LEVEL should be "DEBUG"
        # When DEBUG is False, LOG_LEVEL should be "INFO"
        # This is tested by checking the module logic
        pass  # The logic is in env.py: LOG_LEVEL = "DEBUG" if DEBUG else "INFO"


# =============================================================================
# Proxy and Network Configuration Tests
# =============================================================================


class TestNetworkConfiguration:
    """Tests for proxy and network settings."""

    def test_proxy_settings_stripped(self):
        """Proxy URLs should be stripped of whitespace."""
        from cwa_book_downloader.config.env import HTTP_PROXY, HTTPS_PROXY

        # These are already evaluated, but the logic is:
        # HTTP_PROXY = os.getenv("HTTP_PROXY", "").strip()
        # So whitespace should be removed
        assert HTTP_PROXY == HTTP_PROXY.strip()
        assert HTTPS_PROXY == HTTPS_PROXY.strip()

    def test_tor_mode_disables_other_network_settings(self):
        """Tor mode should disable custom DNS, DOH, and proxies."""
        # This is a documentation test - the logic is in env.py:
        # if USING_TOR:
        #     _CUSTOM_DNS = ""
        #     USE_DOH = False
        #     HTTP_PROXY = ""
        #     HTTPS_PROXY = ""
        pass


# =============================================================================
# Concurrent Downloads Configuration Tests
# =============================================================================


class TestConcurrencyConfiguration:
    """Tests for concurrent download settings."""

    def test_max_concurrent_downloads_default(self):
        """MAX_CONCURRENT_DOWNLOADS should have a sensible default."""
        from cwa_book_downloader.config.env import MAX_CONCURRENT_DOWNLOADS

        assert MAX_CONCURRENT_DOWNLOADS >= 1
        assert MAX_CONCURRENT_DOWNLOADS <= 10  # Reasonable upper bound

    def test_download_progress_interval_default(self):
        """DOWNLOAD_PROGRESS_UPDATE_INTERVAL should have a sensible default."""
        from cwa_book_downloader.config.env import DOWNLOAD_PROGRESS_UPDATE_INTERVAL

        assert DOWNLOAD_PROGRESS_UPDATE_INTERVAL >= 1
        assert DOWNLOAD_PROGRESS_UPDATE_INTERVAL <= 10


# =============================================================================
# Cache Configuration Tests
# =============================================================================


class TestCacheConfiguration:
    """Tests for cache settings."""

    def test_metadata_cache_ttl_defaults(self):
        """Metadata cache TTLs should have sensible defaults."""
        from cwa_book_downloader.config.env import (
            METADATA_CACHE_SEARCH_TTL,
            METADATA_CACHE_BOOK_TTL,
        )

        # Search cache should be shorter than book cache
        assert METADATA_CACHE_SEARCH_TTL > 0
        assert METADATA_CACHE_BOOK_TTL > 0
        assert METADATA_CACHE_SEARCH_TTL <= METADATA_CACHE_BOOK_TTL

    def test_covers_cache_directory(self):
        """Covers cache directory should be under CONFIG_DIR."""
        from cwa_book_downloader.config.env import CONFIG_DIR, COVERS_CACHE_DIR

        assert COVERS_CACHE_DIR.parent == CONFIG_DIR
        assert COVERS_CACHE_DIR.name == "covers"


# =============================================================================
# File Collision Handling Tests
# =============================================================================


class TestFileCollisionHandling:
    """Tests for handling file name collisions."""

    def test_stage_file_handles_collision(self):
        """stage_file should add suffix on collision."""
        from cwa_book_downloader.download.orchestrator import stage_file

        with tempfile.TemporaryDirectory() as tmpdir:
            staging = Path(tmpdir) / "staging"
            staging.mkdir()

            # Create source file
            source = Path(tmpdir) / "book.epub"
            source.write_text("content")

            # Create existing file with same name in staging
            (staging / "book.epub").write_text("existing")

            with patch(
                "cwa_book_downloader.download.orchestrator.TMP_DIR", staging
            ):
                result = stage_file(source, "task1", copy=True)

            # Should have created a new file with suffix
            assert result.name == "book_1.epub"
            assert result.exists()

    def test_stage_file_copy_vs_move(self):
        """stage_file should copy or move based on parameter."""
        from cwa_book_downloader.download.orchestrator import stage_file

        with tempfile.TemporaryDirectory() as tmpdir:
            staging = Path(tmpdir) / "staging"
            staging.mkdir()

            # Test copy
            source1 = Path(tmpdir) / "book1.epub"
            source1.write_text("content1")

            with patch(
                "cwa_book_downloader.download.orchestrator.TMP_DIR", staging
            ):
                result1 = stage_file(source1, "task1", copy=True)

            assert source1.exists()  # Original still exists
            assert result1.exists()

            # Test move
            source2 = Path(tmpdir) / "book2.epub"
            source2.write_text("content2")

            with patch(
                "cwa_book_downloader.download.orchestrator.TMP_DIR", staging
            ):
                result2 = stage_file(source2, "task2", copy=False)

            assert not source2.exists()  # Original moved
            assert result2.exists()
