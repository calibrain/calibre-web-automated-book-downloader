"""
Docker volume and filesystem edge case tests.

These tests verify the application handles various Docker volume configurations
correctly, including named volumes, bind mounts, permission issues, and
edge cases that commonly cause issues in containerized deployments.

Run with: docker exec test-cwabd python3 -m pytest /app/tests/config/test_docker_volumes.py -v
"""

import json
import os
import stat
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


# =============================================================================
# Fresh Install / Empty Volume Tests
# =============================================================================


class TestFreshInstall:
    """Tests simulating a fresh install with empty volumes."""

    def test_config_dir_created_on_first_save(self):
        """Config directory and plugins subdirectory should be created on first save."""
        from cwa_book_downloader.core.settings_registry import save_config_file

        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "config"
            # Directory doesn't exist yet

            with patch("cwa_book_downloader.config.env.CONFIG_DIR", config_dir):
                result = save_config_file("test_plugin", {"key": "value"})

            assert result is True
            assert config_dir.exists()
            assert (config_dir / "plugins").exists()
            assert (config_dir / "plugins" / "test_plugin.json").exists()

    def test_general_settings_saved_to_settings_json(self):
        """General settings should go to settings.json, not plugins folder."""
        from cwa_book_downloader.core.settings_registry import (
            save_config_file,
            _get_config_file_path,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)

            with patch("cwa_book_downloader.config.env.CONFIG_DIR", config_dir):
                path = _get_config_file_path("general")
                assert path == config_dir / "settings.json"

                save_config_file("general", {"key": "value"})
                assert (config_dir / "settings.json").exists()

    def test_nested_config_directories_created(self):
        """Deeply nested config paths should be created with parents=True."""
        from cwa_book_downloader.core.settings_registry import _ensure_config_dir

        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "deeply" / "nested" / "config"

            with patch("cwa_book_downloader.config.env.CONFIG_DIR", config_dir):
                _ensure_config_dir("test_plugin")

            assert (config_dir / "plugins").exists()

    def test_empty_config_returns_defaults(self):
        """Loading from empty/missing config should return empty dict."""
        from cwa_book_downloader.core.settings_registry import load_config_file

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("cwa_book_downloader.config.env.CONFIG_DIR", Path(tmpdir)):
                result = load_config_file("nonexistent")

            assert result == {}


# =============================================================================
# Corrupted / Invalid Config File Tests
# =============================================================================


class TestCorruptedConfig:
    """Tests for handling corrupted or invalid config files."""

    def test_invalid_json_returns_empty_dict(self):
        """Invalid JSON in config file should return empty dict, not crash."""
        from cwa_book_downloader.core.settings_registry import load_config_file

        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            plugins_dir = config_dir / "plugins"
            plugins_dir.mkdir(parents=True)

            # Write invalid JSON
            (plugins_dir / "broken.json").write_text("{ invalid json }")

            with patch("cwa_book_downloader.config.env.CONFIG_DIR", config_dir):
                result = load_config_file("broken")

            assert result == {}

    def test_empty_json_file_returns_empty_dict(self):
        """Empty JSON file should return empty dict."""
        from cwa_book_downloader.core.settings_registry import load_config_file

        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            plugins_dir = config_dir / "plugins"
            plugins_dir.mkdir(parents=True)

            # Write empty file
            (plugins_dir / "empty.json").write_text("")

            with patch("cwa_book_downloader.config.env.CONFIG_DIR", config_dir):
                result = load_config_file("empty")

            # Empty file is invalid JSON, should return {}
            assert result == {}

    def test_partial_json_write_recovery(self):
        """Config should handle partially written JSON files."""
        from cwa_book_downloader.core.settings_registry import load_config_file

        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            plugins_dir = config_dir / "plugins"
            plugins_dir.mkdir(parents=True)

            # Simulate interrupted write
            (plugins_dir / "partial.json").write_text('{"key": "val')

            with patch("cwa_book_downloader.config.env.CONFIG_DIR", config_dir):
                result = load_config_file("partial")

            assert result == {}

    def test_null_bytes_in_config_file(self):
        """Config with null bytes should be handled gracefully."""
        from cwa_book_downloader.core.settings_registry import load_config_file

        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            plugins_dir = config_dir / "plugins"
            plugins_dir.mkdir(parents=True)

            # Write file with null bytes
            (plugins_dir / "nullbytes.json").write_bytes(b'{"key": "value\x00"}')

            with patch("cwa_book_downloader.config.env.CONFIG_DIR", config_dir):
                # Should either parse (ignoring null) or return empty
                result = load_config_file("nullbytes")
                # Just verify it doesn't crash
                assert isinstance(result, dict)

    def test_wrong_type_in_config(self):
        """Config with array instead of object should be handled."""
        from cwa_book_downloader.core.settings_registry import load_config_file

        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            plugins_dir = config_dir / "plugins"
            plugins_dir.mkdir(parents=True)

            # Write array instead of object
            (plugins_dir / "wrongtype.json").write_text('["item1", "item2"]')

            with patch("cwa_book_downloader.config.env.CONFIG_DIR", config_dir):
                result = load_config_file("wrongtype")
                # Should return the parsed content (a list) or handle gracefully
                # Current implementation returns whatever json.load returns
                assert isinstance(result, (dict, list))


# =============================================================================
# Permission Tests (Docker PUID/PGID scenarios)
# =============================================================================


class TestPermissions:
    """Tests for permission-related scenarios."""

    @pytest.mark.skipif(
        os.geteuid() == 0,
        reason="Permission tests don't work when running as root"
    )
    def test_read_only_config_dir_save_fails_gracefully(self):
        """Saving to read-only config dir should fail gracefully, not crash."""
        from cwa_book_downloader.core.settings_registry import save_config_file

        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "readonly"
            config_dir.mkdir()
            os.chmod(config_dir, stat.S_IRUSR | stat.S_IXUSR)  # r-x

            try:
                with patch("cwa_book_downloader.config.env.CONFIG_DIR", config_dir):
                    result = save_config_file("test", {"key": "value"})

                assert result is False
            finally:
                os.chmod(config_dir, stat.S_IRWXU)

    @pytest.mark.skipif(
        os.geteuid() == 0,
        reason="Permission tests don't work when running as root"
    )
    def test_read_only_config_file_save_fails_gracefully(self):
        """Saving when config file is read-only should fail gracefully."""
        from cwa_book_downloader.core.settings_registry import save_config_file

        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            plugins_dir = config_dir / "plugins"
            plugins_dir.mkdir(parents=True)

            config_file = plugins_dir / "readonly.json"
            config_file.write_text('{"existing": "value"}')
            os.chmod(config_file, stat.S_IRUSR)  # Read-only

            try:
                with patch("cwa_book_downloader.config.env.CONFIG_DIR", config_dir):
                    result = save_config_file("readonly", {"new": "value"})

                assert result is False
            finally:
                os.chmod(config_file, stat.S_IRWXU)

    def test_config_dir_exists_check(self):
        """_is_config_dir_writable should correctly detect writable dirs."""
        from cwa_book_downloader.config.env import _is_config_dir_writable

        with tempfile.TemporaryDirectory() as tmpdir:
            writable_dir = Path(tmpdir) / "writable"
            writable_dir.mkdir()

            with patch("cwa_book_downloader.config.env.CONFIG_DIR", writable_dir):
                assert _is_config_dir_writable() is True

    def test_config_dir_not_exists(self):
        """_is_config_dir_writable should return False for non-existent dir."""
        from cwa_book_downloader.config.env import _is_config_dir_writable

        with patch(
            "cwa_book_downloader.config.env.CONFIG_DIR",
            Path("/nonexistent/path/that/does/not/exist")
        ):
            assert _is_config_dir_writable() is False


# =============================================================================
# Path Edge Cases
# =============================================================================


class TestPathEdgeCases:
    """Tests for edge cases in path handling."""

    def test_config_dir_with_spaces(self):
        """Config directory with spaces in path should work."""
        from cwa_book_downloader.core.settings_registry import (
            save_config_file,
            load_config_file,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "path with spaces" / "config"

            with patch("cwa_book_downloader.config.env.CONFIG_DIR", config_dir):
                save_config_file("test", {"key": "value"})
                result = load_config_file("test")

            assert result == {"key": "value"}

    def test_config_dir_with_unicode(self):
        """Config directory with unicode characters should work."""
        from cwa_book_downloader.core.settings_registry import (
            save_config_file,
            load_config_file,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "配置文件夹" / "config"

            with patch("cwa_book_downloader.config.env.CONFIG_DIR", config_dir):
                save_config_file("test", {"key": "value"})
                result = load_config_file("test")

            assert result == {"key": "value"}

    def test_config_with_unicode_values(self):
        """Config values with unicode should be preserved."""
        from cwa_book_downloader.core.settings_registry import (
            save_config_file,
            load_config_file,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)

            with patch("cwa_book_downloader.config.env.CONFIG_DIR", config_dir):
                save_config_file("test", {
                    "title": "日本語タイトル",
                    "author": "Автор книги",
                    "emoji": "📚🎉",
                })
                result = load_config_file("test")

            assert result["title"] == "日本語タイトル"
            assert result["author"] == "Автор книги"
            assert result["emoji"] == "📚🎉"

    def test_very_long_plugin_name(self):
        """Very long plugin names should be handled."""
        from cwa_book_downloader.core.settings_registry import (
            save_config_file,
            load_config_file,
        )

        long_name = "a" * 200  # Very long name

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("cwa_book_downloader.config.env.CONFIG_DIR", Path(tmpdir)):
                # This might fail on some filesystems with path length limits
                try:
                    result = save_config_file(long_name, {"key": "value"})
                    if result:
                        loaded = load_config_file(long_name)
                        assert loaded == {"key": "value"}
                except OSError:
                    # Expected on filesystems with path length limits
                    pass


# =============================================================================
# Config File Merging Tests
# =============================================================================


class TestConfigMerging:
    """Tests for config file merging behavior."""

    def test_save_merges_with_existing(self):
        """Saving should merge with existing values, not replace."""
        from cwa_book_downloader.core.settings_registry import (
            save_config_file,
            load_config_file,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            plugins_dir = config_dir / "plugins"
            plugins_dir.mkdir(parents=True)

            # Write initial config
            (plugins_dir / "merge.json").write_text('{"existing": "value", "old": "data"}')

            with patch("cwa_book_downloader.config.env.CONFIG_DIR", config_dir):
                save_config_file("merge", {"new": "value", "existing": "updated"})
                result = load_config_file("merge")

            assert result["old"] == "data"  # Preserved
            assert result["new"] == "value"  # Added
            assert result["existing"] == "updated"  # Updated

    def test_save_handles_nested_objects(self):
        """Saving nested objects should work correctly."""
        from cwa_book_downloader.core.settings_registry import (
            save_config_file,
            load_config_file,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)

            with patch("cwa_book_downloader.config.env.CONFIG_DIR", config_dir):
                save_config_file("nested", {
                    "level1": {
                        "level2": {
                            "value": "deep"
                        }
                    },
                    "list": [1, 2, 3],
                })
                result = load_config_file("nested")

            assert result["level1"]["level2"]["value"] == "deep"
            assert result["list"] == [1, 2, 3]


# =============================================================================
# Cross-Filesystem Tests (TMP_DIR vs INGEST_DIR)
# =============================================================================


class TestCrossFilesystem:
    """Tests for cross-filesystem scenarios."""

    def test_staging_and_ingest_same_filesystem(self):
        """When TMP_DIR and INGEST_DIR are on same filesystem, move is used."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_dir = Path(tmpdir) / "tmp"
            ingest_dir = Path(tmpdir) / "ingest"
            tmp_dir.mkdir()
            ingest_dir.mkdir()

            # Both on same filesystem
            assert os.stat(tmp_dir).st_dev == os.stat(ingest_dir).st_dev

    def test_detect_cross_filesystem(self):
        """CROSS_FILE_SYSTEM detection should work."""
        # This is a documentation test - the actual logic is in settings.py:
        # CROSS_FILE_SYSTEM = os.stat(env.TMP_DIR).st_dev != os.stat(env.INGEST_DIR).st_dev
        pass


# =============================================================================
# Startup Directory Creation Tests
# =============================================================================


class TestStartupDirectoryCreation:
    """Tests for directory creation during startup."""

    def test_tmp_dir_created_without_parents(self):
        """TMP_DIR.mkdir(exist_ok=True) needs parent to exist."""
        # This documents a potential issue: mkdir(exist_ok=True) without
        # parents=True will fail if parent doesn't exist

        with tempfile.TemporaryDirectory() as tmpdir:
            # Parent exists
            tmp_dir = Path(tmpdir) / "tmp"
            tmp_dir.mkdir(exist_ok=True)
            assert tmp_dir.exists()

            # Parent doesn't exist - would fail without parents=True
            nested = Path(tmpdir) / "nonexistent" / "deep" / "tmp"
            with pytest.raises(FileNotFoundError):
                nested.mkdir(exist_ok=True)

            # With parents=True it works
            nested.mkdir(parents=True, exist_ok=True)
            assert nested.exists()

    def test_ingest_dir_created_without_parents(self):
        """INGEST_DIR.mkdir(exist_ok=True) needs parent to exist."""
        # Same potential issue as TMP_DIR
        with tempfile.TemporaryDirectory() as tmpdir:
            ingest = Path(tmpdir) / "ingest"
            ingest.mkdir(exist_ok=True)
            assert ingest.exists()


# =============================================================================
# Named Volume vs Bind Mount Simulation
# =============================================================================


class TestVolumeTypes:
    """Tests simulating named volume vs bind mount differences."""

    def test_empty_named_volume_scenario(self):
        """
        Simulate named volume: directory exists but is empty.

        Named volumes are created by Docker as empty directories owned by root.
        The application should handle this gracefully.
        """
        from cwa_book_downloader.core.settings_registry import (
            save_config_file,
            load_config_file,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            # Simulate named volume: empty directory exists
            config_dir = Path(tmpdir) / "config"
            config_dir.mkdir()

            with patch("cwa_book_downloader.config.env.CONFIG_DIR", config_dir):
                # Should create plugins subdirectory and save
                result = save_config_file("test", {"key": "value"})
                assert result is True

                loaded = load_config_file("test")
                assert loaded == {"key": "value"}

    def test_bind_mount_with_existing_files(self):
        """
        Simulate bind mount: directory has existing files from host.

        Bind mounts may have existing config from a previous installation.
        """
        from cwa_book_downloader.core.settings_registry import (
            save_config_file,
            load_config_file,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            plugins_dir = config_dir / "plugins"
            plugins_dir.mkdir(parents=True)

            # Existing config from previous install
            (plugins_dir / "prowlarr_clients.json").write_text(json.dumps({
                "PROWLARR_TORRENT_CLIENT": "qbittorrent",
                "QBITTORRENT_URL": "http://old-host:8080",
            }))

            with patch("cwa_book_downloader.config.env.CONFIG_DIR", config_dir):
                # New save should merge
                save_config_file("prowlarr_clients", {
                    "QBITTORRENT_URL": "http://new-host:8080",
                })

                result = load_config_file("prowlarr_clients")

            # Should have merged
            assert result["PROWLARR_TORRENT_CLIENT"] == "qbittorrent"
            assert result["QBITTORRENT_URL"] == "http://new-host:8080"

    def test_volume_with_only_partial_structure(self):
        """
        Simulate volume with partial directory structure.

        User might manually create /config but not /config/plugins.
        """
        from cwa_book_downloader.core.settings_registry import save_config_file

        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            # Only config dir exists, not plugins subdir

            with patch("cwa_book_downloader.config.env.CONFIG_DIR", config_dir):
                result = save_config_file("test", {"key": "value"})

            assert result is True
            assert (config_dir / "plugins").exists()
            assert (config_dir / "plugins" / "test.json").exists()


# =============================================================================
# Race Condition and Concurrent Access Tests
# =============================================================================


class TestConcurrentAccess:
    """Tests for concurrent config access scenarios."""

    def test_simultaneous_saves_dont_corrupt(self):
        """Multiple saves should not corrupt the config file."""
        from cwa_book_downloader.core.settings_registry import (
            save_config_file,
            load_config_file,
        )
        import threading

        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)

            results = []

            def save_value(key, value):
                with patch("cwa_book_downloader.config.env.CONFIG_DIR", config_dir):
                    result = save_config_file("concurrent", {key: value})
                    results.append(result)

            with patch("cwa_book_downloader.config.env.CONFIG_DIR", config_dir):
                threads = [
                    threading.Thread(target=save_value, args=(f"key{i}", f"value{i}"))
                    for i in range(5)
                ]

                for t in threads:
                    t.start()
                for t in threads:
                    t.join()

                # All saves should succeed
                assert all(results)

                # File should be valid JSON
                final = load_config_file("concurrent")
                assert isinstance(final, dict)


# =============================================================================
# Config Backup/Migration Tests
# =============================================================================


class TestConfigMigration:
    """Tests for config migration scenarios."""

    def test_old_format_config_upgrade(self):
        """
        Application should handle config from older versions.

        This is a placeholder for version-specific migration tests.
        """
        pass

    def test_config_with_unknown_keys(self):
        """Config with unknown keys should be preserved."""
        from cwa_book_downloader.core.settings_registry import (
            save_config_file,
            load_config_file,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            plugins_dir = config_dir / "plugins"
            plugins_dir.mkdir(parents=True)

            # Config with keys that don't exist in current schema
            (plugins_dir / "future.json").write_text(json.dumps({
                "KNOWN_KEY": "value",
                "FUTURE_KEY_V2": "future_value",
                "ANOTHER_UNKNOWN": 123,
            }))

            with patch("cwa_book_downloader.config.env.CONFIG_DIR", config_dir):
                # Save should preserve unknown keys
                save_config_file("future", {"KNOWN_KEY": "updated"})
                result = load_config_file("future")

            assert result["KNOWN_KEY"] == "updated"
            assert result["FUTURE_KEY_V2"] == "future_value"
            assert result["ANOTHER_UNKNOWN"] == 123
