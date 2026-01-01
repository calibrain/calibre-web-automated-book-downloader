"""
Unit tests for the qBittorrent client.

These tests mock the qbittorrentapi library to test the client logic
without requiring a running qBittorrent instance.
"""

import sys
from unittest.mock import MagicMock, patch
import pytest

from cwa_book_downloader.release_sources.prowlarr.clients import DownloadStatus


class MockTorrent:
    """Mock qBittorrent torrent object."""

    def __init__(
        self,
        hash_val="abc123",
        name="Test Torrent",
        progress=0.5,
        state="downloading",
        dlspeed=1024000,
        eta=3600,
        content_path="/downloads/test.txt",
    ):
        self.hash = hash_val
        self.name = name
        self.progress = progress
        self.state = state
        self.dlspeed = dlspeed
        self.eta = eta
        self.content_path = content_path

    def to_dict(self):
        """Convert to dict for JSON response mocking."""
        return {
            "hash": self.hash,
            "name": self.name,
            "progress": self.progress,
            "state": self.state,
            "dlspeed": self.dlspeed,
            "eta": self.eta,
            "content_path": self.content_path,
        }


def create_mock_session_response(torrents):
    """Create a mock response for _session.get() calls."""
    mock_response = MagicMock()
    mock_response.json.return_value = [t.to_dict() if isinstance(t, MockTorrent) else t for t in torrents]
    mock_response.raise_for_status = MagicMock()
    return mock_response


class TestQBittorrentClientIsConfigured:
    """Tests for QBittorrentClient.is_configured()."""

    def test_is_configured_when_all_set(self, monkeypatch):
        """Test is_configured returns True when properly configured."""
        config_values = {
            "PROWLARR_TORRENT_CLIENT": "qbittorrent",
            "QBITTORRENT_URL": "http://localhost:8080",
        }
        monkeypatch.setattr(
            "cwa_book_downloader.release_sources.prowlarr.clients.qbittorrent.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        from cwa_book_downloader.release_sources.prowlarr.clients.qbittorrent import (
            QBittorrentClient,
        )

        assert QBittorrentClient.is_configured() is True

    def test_is_configured_wrong_client(self, monkeypatch):
        """Test is_configured returns False when different client selected."""
        config_values = {
            "PROWLARR_TORRENT_CLIENT": "transmission",
            "QBITTORRENT_URL": "http://localhost:8080",
        }
        monkeypatch.setattr(
            "cwa_book_downloader.release_sources.prowlarr.clients.qbittorrent.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        from cwa_book_downloader.release_sources.prowlarr.clients.qbittorrent import (
            QBittorrentClient,
        )

        assert QBittorrentClient.is_configured() is False

    def test_is_configured_no_url(self, monkeypatch):
        """Test is_configured returns False when URL not set."""
        config_values = {
            "PROWLARR_TORRENT_CLIENT": "qbittorrent",
            "QBITTORRENT_URL": "",
        }
        monkeypatch.setattr(
            "cwa_book_downloader.release_sources.prowlarr.clients.qbittorrent.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        from cwa_book_downloader.release_sources.prowlarr.clients.qbittorrent import (
            QBittorrentClient,
        )

        assert QBittorrentClient.is_configured() is False


class TestQBittorrentClientTestConnection:
    """Tests for QBittorrentClient.test_connection()."""

    def test_test_connection_success(self, monkeypatch):
        """Test successful connection."""
        config_values = {
            "QBITTORRENT_URL": "http://localhost:8080",
            "QBITTORRENT_USERNAME": "admin",
            "QBITTORRENT_PASSWORD": "password",
            "QBITTORRENT_CATEGORY": "test",
        }
        monkeypatch.setattr(
            "cwa_book_downloader.release_sources.prowlarr.clients.qbittorrent.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        mock_client_instance = MagicMock()
        mock_client_instance.app.web_api_version = "2.9.3"
        mock_client_class = MagicMock(return_value=mock_client_instance)

        # Mock the import inside the module
        with patch.dict('sys.modules', {'qbittorrentapi': MagicMock(Client=mock_client_class)}):
            # Need to reimport after patching
            import importlib
            import cwa_book_downloader.release_sources.prowlarr.clients.qbittorrent as qb_module
            importlib.reload(qb_module)

            client = qb_module.QBittorrentClient()
            success, message = client.test_connection()

            assert success is True
            assert "2.9.3" in message

    def test_test_connection_failure(self, monkeypatch):
        """Test failed connection."""
        config_values = {
            "QBITTORRENT_URL": "http://localhost:8080",
            "QBITTORRENT_USERNAME": "admin",
            "QBITTORRENT_PASSWORD": "wrong",
            "QBITTORRENT_CATEGORY": "test",
        }
        monkeypatch.setattr(
            "cwa_book_downloader.release_sources.prowlarr.clients.qbittorrent.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        mock_client_instance = MagicMock()
        mock_client_instance.auth_log_in.side_effect = Exception("401 Unauthorized")
        mock_client_class = MagicMock(return_value=mock_client_instance)

        with patch.dict('sys.modules', {'qbittorrentapi': MagicMock(Client=mock_client_class)}):
            import importlib
            import cwa_book_downloader.release_sources.prowlarr.clients.qbittorrent as qb_module
            importlib.reload(qb_module)

            client = qb_module.QBittorrentClient()
            success, message = client.test_connection()

            assert success is False
            assert "401" in message or "failed" in message.lower()


class TestQBittorrentClientGetStatus:
    """Tests for QBittorrentClient.get_status()."""

    def test_get_status_downloading(self, monkeypatch):
        """Test status for downloading torrent."""
        config_values = {
            "QBITTORRENT_URL": "http://localhost:8080",
            "QBITTORRENT_USERNAME": "admin",
            "QBITTORRENT_PASSWORD": "password",
            "QBITTORRENT_CATEGORY": "test",
        }
        monkeypatch.setattr(
            "cwa_book_downloader.release_sources.prowlarr.clients.qbittorrent.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        mock_torrent = MockTorrent(progress=0.5, state="downloading", dlspeed=1024000, eta=3600)
        mock_client_instance = MagicMock()
        # Mock the session.get for _get_torrents_info
        mock_client_instance._session.get.return_value = create_mock_session_response([mock_torrent])
        mock_client_class = MagicMock(return_value=mock_client_instance)

        with patch.dict('sys.modules', {'qbittorrentapi': MagicMock(Client=mock_client_class)}):
            import importlib
            import cwa_book_downloader.release_sources.prowlarr.clients.qbittorrent as qb_module
            importlib.reload(qb_module)

            client = qb_module.QBittorrentClient()
            status = client.get_status("abc123")

            assert status.progress == 50.0
            assert status.state_value == "downloading"
            assert status.complete is False
            assert status.download_speed == 1024000
            assert status.eta == 3600

    def test_get_status_complete(self, monkeypatch):
        """Test status for completed torrent."""
        config_values = {
            "QBITTORRENT_URL": "http://localhost:8080",
            "QBITTORRENT_USERNAME": "admin",
            "QBITTORRENT_PASSWORD": "password",
            "QBITTORRENT_CATEGORY": "test",
        }
        monkeypatch.setattr(
            "cwa_book_downloader.release_sources.prowlarr.clients.qbittorrent.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        mock_torrent = MockTorrent(
            progress=1.0,
            state="uploading",
            content_path="/downloads/completed.epub",
        )
        mock_client_instance = MagicMock()
        # Mock the session.get for _get_torrents_info
        mock_client_instance._session.get.return_value = create_mock_session_response([mock_torrent])
        mock_client_class = MagicMock(return_value=mock_client_instance)

        with patch.dict('sys.modules', {'qbittorrentapi': MagicMock(Client=mock_client_class)}):
            import importlib
            import cwa_book_downloader.release_sources.prowlarr.clients.qbittorrent as qb_module
            importlib.reload(qb_module)

            client = qb_module.QBittorrentClient()
            status = client.get_status("abc123")

            assert status.progress == 100.0
            assert status.complete is True
            assert status.file_path == "/downloads/completed.epub"

    def test_get_status_not_found(self, monkeypatch):
        """Test status for non-existent torrent."""
        config_values = {
            "QBITTORRENT_URL": "http://localhost:8080",
            "QBITTORRENT_USERNAME": "admin",
            "QBITTORRENT_PASSWORD": "password",
            "QBITTORRENT_CATEGORY": "test",
        }
        monkeypatch.setattr(
            "cwa_book_downloader.release_sources.prowlarr.clients.qbittorrent.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        mock_client_instance = MagicMock()
        # Mock the session.get for _get_torrents_info - empty list
        mock_client_instance._session.get.return_value = create_mock_session_response([])
        mock_client_class = MagicMock(return_value=mock_client_instance)

        with patch.dict('sys.modules', {'qbittorrentapi': MagicMock(Client=mock_client_class)}):
            import importlib
            import cwa_book_downloader.release_sources.prowlarr.clients.qbittorrent as qb_module
            importlib.reload(qb_module)

            client = qb_module.QBittorrentClient()
            status = client.get_status("nonexistent")

            assert status.state_value == "error"
            assert "not found" in status.message.lower()

    def test_get_status_stalled(self, monkeypatch):
        """Test status for stalled torrent."""
        config_values = {
            "QBITTORRENT_URL": "http://localhost:8080",
            "QBITTORRENT_USERNAME": "admin",
            "QBITTORRENT_PASSWORD": "password",
            "QBITTORRENT_CATEGORY": "test",
        }
        monkeypatch.setattr(
            "cwa_book_downloader.release_sources.prowlarr.clients.qbittorrent.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        mock_torrent = MockTorrent(progress=0.3, state="stalledDL")
        mock_client_instance = MagicMock()
        # Mock the session.get for _get_torrents_info
        mock_client_instance._session.get.return_value = create_mock_session_response([mock_torrent])
        mock_client_class = MagicMock(return_value=mock_client_instance)

        with patch.dict('sys.modules', {'qbittorrentapi': MagicMock(Client=mock_client_class)}):
            import importlib
            import cwa_book_downloader.release_sources.prowlarr.clients.qbittorrent as qb_module
            importlib.reload(qb_module)

            client = qb_module.QBittorrentClient()
            status = client.get_status("abc123")

            assert status.state_value == "downloading"
            assert "stalled" in status.message.lower()

    def test_get_status_paused(self, monkeypatch):
        """Test status for paused torrent."""
        config_values = {
            "QBITTORRENT_URL": "http://localhost:8080",
            "QBITTORRENT_USERNAME": "admin",
            "QBITTORRENT_PASSWORD": "password",
            "QBITTORRENT_CATEGORY": "test",
        }
        monkeypatch.setattr(
            "cwa_book_downloader.release_sources.prowlarr.clients.qbittorrent.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        mock_torrent = MockTorrent(progress=0.5, state="pausedDL")
        mock_client_instance = MagicMock()
        # Mock the session.get for _get_torrents_info
        mock_client_instance._session.get.return_value = create_mock_session_response([mock_torrent])
        mock_client_class = MagicMock(return_value=mock_client_instance)

        with patch.dict('sys.modules', {'qbittorrentapi': MagicMock(Client=mock_client_class)}):
            import importlib
            import cwa_book_downloader.release_sources.prowlarr.clients.qbittorrent as qb_module
            importlib.reload(qb_module)

            client = qb_module.QBittorrentClient()
            status = client.get_status("abc123")

            assert status.state_value == "paused"

    def test_get_status_error_state(self, monkeypatch):
        """Test status for errored torrent."""
        config_values = {
            "QBITTORRENT_URL": "http://localhost:8080",
            "QBITTORRENT_USERNAME": "admin",
            "QBITTORRENT_PASSWORD": "password",
            "QBITTORRENT_CATEGORY": "test",
        }
        monkeypatch.setattr(
            "cwa_book_downloader.release_sources.prowlarr.clients.qbittorrent.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        mock_torrent = MockTorrent(progress=0.1, state="error")
        mock_client_instance = MagicMock()
        # Mock the session.get for _get_torrents_info
        mock_client_instance._session.get.return_value = create_mock_session_response([mock_torrent])
        mock_client_class = MagicMock(return_value=mock_client_instance)

        with patch.dict('sys.modules', {'qbittorrentapi': MagicMock(Client=mock_client_class)}):
            import importlib
            import cwa_book_downloader.release_sources.prowlarr.clients.qbittorrent as qb_module
            importlib.reload(qb_module)

            client = qb_module.QBittorrentClient()
            status = client.get_status("abc123")

            assert status.state_value == "error"


class TestQBittorrentClientAddDownload:
    """Tests for QBittorrentClient.add_download()."""

    def test_add_download_magnet_success(self, monkeypatch):
        """Test adding a magnet link."""
        config_values = {
            "QBITTORRENT_URL": "http://localhost:8080",
            "QBITTORRENT_USERNAME": "admin",
            "QBITTORRENT_PASSWORD": "password",
            "QBITTORRENT_CATEGORY": "test",
        }
        monkeypatch.setattr(
            "cwa_book_downloader.release_sources.prowlarr.clients.qbittorrent.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        mock_torrent = MockTorrent(hash_val="3b245504cf5f11bbdbe1201cea6a6bf45aee1bc0")
        mock_client_instance = MagicMock()
        mock_client_instance.torrents_add.return_value = "Ok."
        mock_client_instance.torrents_info.return_value = [mock_torrent]
        mock_client_class = MagicMock(return_value=mock_client_instance)

        with patch.dict('sys.modules', {'qbittorrentapi': MagicMock(Client=mock_client_class)}):
            import importlib
            import cwa_book_downloader.release_sources.prowlarr.clients.qbittorrent as qb_module
            importlib.reload(qb_module)

            client = qb_module.QBittorrentClient()
            magnet = "magnet:?xt=urn:btih:3b245504cf5f11bbdbe1201cea6a6bf45aee1bc0&dn=test"
            result = client.add_download(magnet, "Test Download")

            assert result == "3b245504cf5f11bbdbe1201cea6a6bf45aee1bc0"

    def test_add_download_creates_category(self, monkeypatch):
        """Test that add_download creates category if needed."""
        config_values = {
            "QBITTORRENT_URL": "http://localhost:8080",
            "QBITTORRENT_USERNAME": "admin",
            "QBITTORRENT_PASSWORD": "password",
            "QBITTORRENT_CATEGORY": "cwabd",
        }
        monkeypatch.setattr(
            "cwa_book_downloader.release_sources.prowlarr.clients.qbittorrent.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        # Use a valid 40-character hex hash
        valid_hash = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"
        mock_torrent = MockTorrent(hash_val=valid_hash)
        mock_client_instance = MagicMock()
        mock_client_instance.torrents_add.return_value = "Ok."
        mock_client_instance.torrents_info.return_value = [mock_torrent]
        mock_client_class = MagicMock(return_value=mock_client_instance)

        with patch.dict('sys.modules', {'qbittorrentapi': MagicMock(Client=mock_client_class)}):
            import importlib
            import cwa_book_downloader.release_sources.prowlarr.clients.qbittorrent as qb_module
            importlib.reload(qb_module)

            client = qb_module.QBittorrentClient()
            magnet = f"magnet:?xt=urn:btih:{valid_hash}&dn=test"
            client.add_download(magnet, "Test")

            mock_client_instance.torrents_create_category.assert_called_once_with(name="cwabd")


class TestQBittorrentClientRemove:
    """Tests for QBittorrentClient.remove()."""

    def test_remove_success(self, monkeypatch):
        """Test successful torrent removal."""
        config_values = {
            "QBITTORRENT_URL": "http://localhost:8080",
            "QBITTORRENT_USERNAME": "admin",
            "QBITTORRENT_PASSWORD": "password",
            "QBITTORRENT_CATEGORY": "test",
        }
        monkeypatch.setattr(
            "cwa_book_downloader.release_sources.prowlarr.clients.qbittorrent.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        mock_client_instance = MagicMock()
        mock_client_class = MagicMock(return_value=mock_client_instance)

        with patch.dict('sys.modules', {'qbittorrentapi': MagicMock(Client=mock_client_class)}):
            import importlib
            import cwa_book_downloader.release_sources.prowlarr.clients.qbittorrent as qb_module
            importlib.reload(qb_module)

            client = qb_module.QBittorrentClient()
            result = client.remove("abc123", delete_files=True)

            assert result is True
            mock_client_instance.torrents_delete.assert_called_once_with(
                torrent_hashes="abc123", delete_files=True
            )

    def test_remove_failure(self, monkeypatch):
        """Test failed torrent removal."""
        config_values = {
            "QBITTORRENT_URL": "http://localhost:8080",
            "QBITTORRENT_USERNAME": "admin",
            "QBITTORRENT_PASSWORD": "password",
            "QBITTORRENT_CATEGORY": "test",
        }
        monkeypatch.setattr(
            "cwa_book_downloader.release_sources.prowlarr.clients.qbittorrent.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        mock_client_instance = MagicMock()
        mock_client_instance.torrents_delete.side_effect = Exception("Not found")
        mock_client_class = MagicMock(return_value=mock_client_instance)

        with patch.dict('sys.modules', {'qbittorrentapi': MagicMock(Client=mock_client_class)}):
            import importlib
            import cwa_book_downloader.release_sources.prowlarr.clients.qbittorrent as qb_module
            importlib.reload(qb_module)

            client = qb_module.QBittorrentClient()
            result = client.remove("abc123")

            assert result is False


class TestQBittorrentClientFindExisting:
    """Tests for QBittorrentClient.find_existing()."""

    def test_find_existing_found(self, monkeypatch):
        """Test finding existing torrent by magnet hash."""
        config_values = {
            "QBITTORRENT_URL": "http://localhost:8080",
            "QBITTORRENT_USERNAME": "admin",
            "QBITTORRENT_PASSWORD": "password",
            "QBITTORRENT_CATEGORY": "test",
        }
        monkeypatch.setattr(
            "cwa_book_downloader.release_sources.prowlarr.clients.qbittorrent.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        mock_torrent = MockTorrent(
            hash_val="3b245504cf5f11bbdbe1201cea6a6bf45aee1bc0",
            progress=0.5,
            state="downloading",
        )
        mock_client_instance = MagicMock()
        # Mock the session.get for _get_torrents_info
        mock_client_instance._session.get.return_value = create_mock_session_response([mock_torrent])
        mock_client_class = MagicMock(return_value=mock_client_instance)

        with patch.dict('sys.modules', {'qbittorrentapi': MagicMock(Client=mock_client_class)}):
            import importlib
            import cwa_book_downloader.release_sources.prowlarr.clients.qbittorrent as qb_module
            importlib.reload(qb_module)

            client = qb_module.QBittorrentClient()
            magnet = "magnet:?xt=urn:btih:3b245504cf5f11bbdbe1201cea6a6bf45aee1bc0&dn=test"
            result = client.find_existing(magnet)

            assert result is not None
            download_id, status = result
            assert download_id == "3b245504cf5f11bbdbe1201cea6a6bf45aee1bc0"
            assert isinstance(status, DownloadStatus)

    def test_find_existing_not_found(self, monkeypatch):
        """Test finding non-existent torrent."""
        config_values = {
            "QBITTORRENT_URL": "http://localhost:8080",
            "QBITTORRENT_USERNAME": "admin",
            "QBITTORRENT_PASSWORD": "password",
            "QBITTORRENT_CATEGORY": "test",
        }
        monkeypatch.setattr(
            "cwa_book_downloader.release_sources.prowlarr.clients.qbittorrent.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        mock_client_instance = MagicMock()
        # Mock the session.get for _get_torrents_info - empty list
        mock_client_instance._session.get.return_value = create_mock_session_response([])
        mock_client_class = MagicMock(return_value=mock_client_instance)

        with patch.dict('sys.modules', {'qbittorrentapi': MagicMock(Client=mock_client_class)}):
            import importlib
            import cwa_book_downloader.release_sources.prowlarr.clients.qbittorrent as qb_module
            importlib.reload(qb_module)

            client = qb_module.QBittorrentClient()
            magnet = "magnet:?xt=urn:btih:abc123def456abc123def456abc123def456abc1&dn=test"
            result = client.find_existing(magnet)

            assert result is None

    def test_find_existing_invalid_url(self, monkeypatch):
        """Test find_existing with invalid URL returns None."""
        config_values = {
            "QBITTORRENT_URL": "http://localhost:8080",
            "QBITTORRENT_USERNAME": "admin",
            "QBITTORRENT_PASSWORD": "password",
            "QBITTORRENT_CATEGORY": "test",
        }
        monkeypatch.setattr(
            "cwa_book_downloader.release_sources.prowlarr.clients.qbittorrent.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        mock_client_instance = MagicMock()
        mock_client_class = MagicMock(return_value=mock_client_instance)

        with patch.dict('sys.modules', {'qbittorrentapi': MagicMock(Client=mock_client_class)}):
            import importlib
            import cwa_book_downloader.release_sources.prowlarr.clients.qbittorrent as qb_module
            importlib.reload(qb_module)

            client = qb_module.QBittorrentClient()
            result = client.find_existing("not-a-magnet-link")

            assert result is None


class TestHashesMatch:
    """Tests for _hashes_match() - Amarr compatibility."""

    def test_identical_hashes_match(self):
        from cwa_book_downloader.release_sources.prowlarr.clients.qbittorrent import _hashes_match
        assert _hashes_match("abc123", "abc123") is True
        assert _hashes_match("ABC123", "abc123") is True

    def test_different_hashes_dont_match(self):
        from cwa_book_downloader.release_sources.prowlarr.clients.qbittorrent import _hashes_match
        assert _hashes_match("abc123", "def456") is False

    def test_amarr_padded_hash_matches_ed2k_hash(self):
        from cwa_book_downloader.release_sources.prowlarr.clients.qbittorrent import _hashes_match
        ed2k_hash = "0320c47b3baa01f8d5f42cd7c05ce28d"  # 32 chars
        padded_hash = "0320c47b3baa01f8d5f42cd7c05ce28d00000000"  # 40 chars
        assert _hashes_match(padded_hash, ed2k_hash) is True
        assert _hashes_match(ed2k_hash, padded_hash) is True

    def test_non_zero_padded_40_char_hash_doesnt_match(self):
        from cwa_book_downloader.release_sources.prowlarr.clients.qbittorrent import _hashes_match
        bittorrent_hash = "3b245504cf5f11bbdbe1201cea6a6bf45aee1bc0"
        partial_hash = "3b245504cf5f11bbdbe1201cea6a6bf4"
        assert _hashes_match(bittorrent_hash, partial_hash) is False

    def test_matching_is_case_insensitive(self):
        from cwa_book_downloader.release_sources.prowlarr.clients.qbittorrent import _hashes_match
        ed2k_hash = "0320C47B3BAA01F8D5F42CD7C05CE28D"
        padded_hash = "0320c47b3baa01f8d5f42cd7c05ce28d00000000"
        assert _hashes_match(padded_hash, ed2k_hash) is True

    def test_wrong_length_hashes_dont_match(self):
        from cwa_book_downloader.release_sources.prowlarr.clients.qbittorrent import _hashes_match
        assert _hashes_match("a" * 40, "b" * 30) is False
        assert _hashes_match("a" * 38, "b" * 32) is False
