"""
Unit tests for the Transmission client.

These tests mock the transmission-rpc library to test the client logic
without requiring a running Transmission instance.
"""

from unittest.mock import MagicMock, patch
from datetime import timedelta
import pytest
import sys

from cwa_book_downloader.release_sources.prowlarr.clients import DownloadStatus


class MockTorrentStatus:
    """Mock for Transmission's torrent status enum."""

    def __init__(self, value):
        self.value = value


class MockTorrent:
    """Mock Transmission torrent object."""

    def __init__(
        self,
        hash_string="abc123",
        name="Test Torrent",
        percent_done=0.5,
        status="downloading",
        rate_download=1024000,
        eta=None,
        download_dir="/downloads",
    ):
        self.hashString = hash_string
        self.name = name
        self.percent_done = percent_done
        self.status = MockTorrentStatus(status)
        self.rate_download = rate_download
        self.download_dir = download_dir
        if eta is not None:
            self.eta = timedelta(seconds=eta)
        else:
            self.eta = None


class MockSession:
    """Mock Transmission session object."""

    def __init__(self, version="4.0.0"):
        self.version = version


def make_config_getter(values):
    """Create a config.get function that returns values from a dict."""
    def getter(key, default=""):
        return values.get(key, default)
    return getter


def create_mock_transmission_rpc_module():
    """Create a mock transmission_rpc module."""
    mock_module = MagicMock()
    mock_module.Client = MagicMock()
    return mock_module


class TestTransmissionClientIsConfigured:
    """Tests for TransmissionClient.is_configured()."""

    def test_is_configured_when_all_set(self, monkeypatch):
        """Test is_configured returns True when properly configured."""
        config_values = {
            "PROWLARR_TORRENT_CLIENT": "transmission",
            "TRANSMISSION_URL": "http://localhost:9091",
        }
        monkeypatch.setattr(
            "cwa_book_downloader.release_sources.prowlarr.clients.transmission.config.get",
            make_config_getter(config_values),
        )

        from cwa_book_downloader.release_sources.prowlarr.clients.transmission import (
            TransmissionClient,
        )

        assert TransmissionClient.is_configured() is True

    def test_is_configured_wrong_client(self, monkeypatch):
        """Test is_configured returns False when different client selected."""
        config_values = {
            "PROWLARR_TORRENT_CLIENT": "qbittorrent",
            "TRANSMISSION_URL": "http://localhost:9091",
        }
        monkeypatch.setattr(
            "cwa_book_downloader.release_sources.prowlarr.clients.transmission.config.get",
            make_config_getter(config_values),
        )

        from cwa_book_downloader.release_sources.prowlarr.clients.transmission import (
            TransmissionClient,
        )

        assert TransmissionClient.is_configured() is False

    def test_is_configured_no_url(self, monkeypatch):
        """Test is_configured returns False when URL not set."""
        config_values = {
            "PROWLARR_TORRENT_CLIENT": "transmission",
            "TRANSMISSION_URL": "",
        }
        monkeypatch.setattr(
            "cwa_book_downloader.release_sources.prowlarr.clients.transmission.config.get",
            make_config_getter(config_values),
        )

        from cwa_book_downloader.release_sources.prowlarr.clients.transmission import (
            TransmissionClient,
        )

        assert TransmissionClient.is_configured() is False


class TestTransmissionClientTestConnection:
    """Tests for TransmissionClient.test_connection()."""

    def test_test_connection_success(self, monkeypatch):
        """Test successful connection."""
        config_values = {
            "TRANSMISSION_URL": "http://localhost:9091",
            "TRANSMISSION_USERNAME": "admin",
            "TRANSMISSION_PASSWORD": "password",
            "TRANSMISSION_CATEGORY": "test",
        }
        monkeypatch.setattr(
            "cwa_book_downloader.release_sources.prowlarr.clients.transmission.config.get",
            make_config_getter(config_values),
        )

        mock_client_instance = MagicMock()
        mock_client_instance.get_session.return_value = MockSession(version="4.0.5")

        mock_transmission_rpc = create_mock_transmission_rpc_module()
        mock_transmission_rpc.Client.return_value = mock_client_instance

        with patch.dict("sys.modules", {"transmission_rpc": mock_transmission_rpc}):
            # Force reimport to use mock
            if "cwa_book_downloader.release_sources.prowlarr.clients.transmission" in sys.modules:
                del sys.modules["cwa_book_downloader.release_sources.prowlarr.clients.transmission"]

            from cwa_book_downloader.release_sources.prowlarr.clients.transmission import (
                TransmissionClient,
            )

            client = TransmissionClient()
            success, message = client.test_connection()

            assert success is True
            assert "4.0.5" in message

    def test_test_connection_failure(self, monkeypatch):
        """Test failed connection."""
        config_values = {
            "TRANSMISSION_URL": "http://localhost:9091",
            "TRANSMISSION_USERNAME": "admin",
            "TRANSMISSION_PASSWORD": "wrong",
            "TRANSMISSION_CATEGORY": "test",
        }
        monkeypatch.setattr(
            "cwa_book_downloader.release_sources.prowlarr.clients.transmission.config.get",
            make_config_getter(config_values),
        )

        mock_client_instance = MagicMock()
        mock_client_instance.get_session.side_effect = Exception("Connection refused")

        mock_transmission_rpc = create_mock_transmission_rpc_module()
        mock_transmission_rpc.Client.return_value = mock_client_instance

        with patch.dict("sys.modules", {"transmission_rpc": mock_transmission_rpc}):
            if "cwa_book_downloader.release_sources.prowlarr.clients.transmission" in sys.modules:
                del sys.modules["cwa_book_downloader.release_sources.prowlarr.clients.transmission"]

            from cwa_book_downloader.release_sources.prowlarr.clients.transmission import (
                TransmissionClient,
            )

            client = TransmissionClient()
            success, message = client.test_connection()

            assert success is False
            assert "failed" in message.lower()


class TestTransmissionClientGetStatus:
    """Tests for TransmissionClient.get_status()."""

    def test_get_status_downloading(self, monkeypatch):
        """Test status for downloading torrent."""
        config_values = {
            "TRANSMISSION_URL": "http://localhost:9091",
            "TRANSMISSION_USERNAME": "admin",
            "TRANSMISSION_PASSWORD": "password",
            "TRANSMISSION_CATEGORY": "test",
        }
        monkeypatch.setattr(
            "cwa_book_downloader.release_sources.prowlarr.clients.transmission.config.get",
            make_config_getter(config_values),
        )

        mock_torrent = MockTorrent(percent_done=0.5, status="downloading", rate_download=1024000)
        mock_client_instance = MagicMock()
        mock_client_instance.get_torrent.return_value = mock_torrent

        mock_transmission_rpc = create_mock_transmission_rpc_module()
        mock_transmission_rpc.Client.return_value = mock_client_instance

        with patch.dict("sys.modules", {"transmission_rpc": mock_transmission_rpc}):
            if "cwa_book_downloader.release_sources.prowlarr.clients.transmission" in sys.modules:
                del sys.modules["cwa_book_downloader.release_sources.prowlarr.clients.transmission"]

            from cwa_book_downloader.release_sources.prowlarr.clients.transmission import (
                TransmissionClient,
            )

            client = TransmissionClient()
            status = client.get_status("abc123")

            assert status.progress == 50.0
            assert status.state_value == "downloading"
            assert status.complete is False
            assert status.download_speed == 1024000

    def test_get_status_seeding(self, monkeypatch):
        """Test status for seeding (complete) torrent."""
        config_values = {
            "TRANSMISSION_URL": "http://localhost:9091",
            "TRANSMISSION_USERNAME": "admin",
            "TRANSMISSION_PASSWORD": "password",
            "TRANSMISSION_CATEGORY": "test",
        }
        monkeypatch.setattr(
            "cwa_book_downloader.release_sources.prowlarr.clients.transmission.config.get",
            make_config_getter(config_values),
        )

        mock_torrent = MockTorrent(
            percent_done=1.0,
            status="seeding",
            download_dir="/downloads",
        )
        mock_client_instance = MagicMock()
        mock_client_instance.get_torrent.return_value = mock_torrent

        mock_transmission_rpc = create_mock_transmission_rpc_module()
        mock_transmission_rpc.Client.return_value = mock_client_instance

        with patch.dict("sys.modules", {"transmission_rpc": mock_transmission_rpc}):
            if "cwa_book_downloader.release_sources.prowlarr.clients.transmission" in sys.modules:
                del sys.modules["cwa_book_downloader.release_sources.prowlarr.clients.transmission"]

            from cwa_book_downloader.release_sources.prowlarr.clients.transmission import (
                TransmissionClient,
            )

            client = TransmissionClient()
            status = client.get_status("abc123")

            assert status.progress == 100.0
            assert status.complete is True
            assert "/downloads/Test Torrent" in status.file_path

    def test_get_status_not_found(self, monkeypatch):
        """Test status for non-existent torrent."""
        config_values = {
            "TRANSMISSION_URL": "http://localhost:9091",
            "TRANSMISSION_USERNAME": "admin",
            "TRANSMISSION_PASSWORD": "password",
            "TRANSMISSION_CATEGORY": "test",
        }
        monkeypatch.setattr(
            "cwa_book_downloader.release_sources.prowlarr.clients.transmission.config.get",
            make_config_getter(config_values),
        )

        mock_client_instance = MagicMock()
        mock_client_instance.get_torrent.side_effect = KeyError("not found")

        mock_transmission_rpc = create_mock_transmission_rpc_module()
        mock_transmission_rpc.Client.return_value = mock_client_instance

        with patch.dict("sys.modules", {"transmission_rpc": mock_transmission_rpc}):
            if "cwa_book_downloader.release_sources.prowlarr.clients.transmission" in sys.modules:
                del sys.modules["cwa_book_downloader.release_sources.prowlarr.clients.transmission"]

            from cwa_book_downloader.release_sources.prowlarr.clients.transmission import (
                TransmissionClient,
            )

            client = TransmissionClient()
            status = client.get_status("nonexistent")

            assert status.state_value == "error"
            assert "not found" in status.message.lower()

    def test_get_status_paused(self, monkeypatch):
        """Test status for paused torrent."""
        config_values = {
            "TRANSMISSION_URL": "http://localhost:9091",
            "TRANSMISSION_USERNAME": "admin",
            "TRANSMISSION_PASSWORD": "password",
            "TRANSMISSION_CATEGORY": "test",
        }
        monkeypatch.setattr(
            "cwa_book_downloader.release_sources.prowlarr.clients.transmission.config.get",
            make_config_getter(config_values),
        )

        mock_torrent = MockTorrent(percent_done=0.3, status="stopped")
        mock_client_instance = MagicMock()
        mock_client_instance.get_torrent.return_value = mock_torrent

        mock_transmission_rpc = create_mock_transmission_rpc_module()
        mock_transmission_rpc.Client.return_value = mock_client_instance

        with patch.dict("sys.modules", {"transmission_rpc": mock_transmission_rpc}):
            if "cwa_book_downloader.release_sources.prowlarr.clients.transmission" in sys.modules:
                del sys.modules["cwa_book_downloader.release_sources.prowlarr.clients.transmission"]

            from cwa_book_downloader.release_sources.prowlarr.clients.transmission import (
                TransmissionClient,
            )

            client = TransmissionClient()
            status = client.get_status("abc123")

            assert status.state_value == "paused"

    def test_get_status_with_eta(self, monkeypatch):
        """Test status includes ETA when available."""
        config_values = {
            "TRANSMISSION_URL": "http://localhost:9091",
            "TRANSMISSION_USERNAME": "admin",
            "TRANSMISSION_PASSWORD": "password",
            "TRANSMISSION_CATEGORY": "test",
        }
        monkeypatch.setattr(
            "cwa_book_downloader.release_sources.prowlarr.clients.transmission.config.get",
            make_config_getter(config_values),
        )

        mock_torrent = MockTorrent(percent_done=0.5, status="downloading", eta=3600)
        mock_client_instance = MagicMock()
        mock_client_instance.get_torrent.return_value = mock_torrent

        mock_transmission_rpc = create_mock_transmission_rpc_module()
        mock_transmission_rpc.Client.return_value = mock_client_instance

        with patch.dict("sys.modules", {"transmission_rpc": mock_transmission_rpc}):
            if "cwa_book_downloader.release_sources.prowlarr.clients.transmission" in sys.modules:
                del sys.modules["cwa_book_downloader.release_sources.prowlarr.clients.transmission"]

            from cwa_book_downloader.release_sources.prowlarr.clients.transmission import (
                TransmissionClient,
            )

            client = TransmissionClient()
            status = client.get_status("abc123")

            assert status.eta == 3600


class TestTransmissionClientAddDownload:
    """Tests for TransmissionClient.add_download()."""

    def test_add_download_magnet_success(self, monkeypatch):
        """Test adding a magnet link."""
        config_values = {
            "TRANSMISSION_URL": "http://localhost:9091",
            "TRANSMISSION_USERNAME": "admin",
            "TRANSMISSION_PASSWORD": "password",
            "TRANSMISSION_CATEGORY": "test",
        }
        monkeypatch.setattr(
            "cwa_book_downloader.release_sources.prowlarr.clients.transmission.config.get",
            make_config_getter(config_values),
        )

        mock_torrent = MockTorrent(hash_string="3b245504cf5f11bbdbe1201cea6a6bf45aee1bc0")
        mock_client_instance = MagicMock()
        mock_client_instance.add_torrent.return_value = mock_torrent

        mock_transmission_rpc = create_mock_transmission_rpc_module()
        mock_transmission_rpc.Client.return_value = mock_client_instance

        with patch.dict("sys.modules", {"transmission_rpc": mock_transmission_rpc}):
            if "cwa_book_downloader.release_sources.prowlarr.clients.transmission" in sys.modules:
                del sys.modules["cwa_book_downloader.release_sources.prowlarr.clients.transmission"]

            from cwa_book_downloader.release_sources.prowlarr.clients.transmission import (
                TransmissionClient,
            )

            client = TransmissionClient()
            magnet = "magnet:?xt=urn:btih:3B245504CF5F11BBDBE1201CEA6A6BF45AEE1BC0&dn=test"
            result = client.add_download(magnet, "Test Download")

            assert result == "3b245504cf5f11bbdbe1201cea6a6bf45aee1bc0"
            mock_client_instance.add_torrent.assert_called_once()

    def test_add_download_uses_labels(self, monkeypatch):
        """Test that add_download sets labels/category."""
        config_values = {
            "TRANSMISSION_URL": "http://localhost:9091",
            "TRANSMISSION_USERNAME": "admin",
            "TRANSMISSION_PASSWORD": "password",
            "TRANSMISSION_CATEGORY": "mybooks",
        }
        monkeypatch.setattr(
            "cwa_book_downloader.release_sources.prowlarr.clients.transmission.config.get",
            make_config_getter(config_values),
        )

        mock_torrent = MockTorrent(hash_string="abc123")
        mock_client_instance = MagicMock()
        mock_client_instance.add_torrent.return_value = mock_torrent

        mock_transmission_rpc = create_mock_transmission_rpc_module()
        mock_transmission_rpc.Client.return_value = mock_client_instance

        with patch.dict("sys.modules", {"transmission_rpc": mock_transmission_rpc}):
            if "cwa_book_downloader.release_sources.prowlarr.clients.transmission" in sys.modules:
                del sys.modules["cwa_book_downloader.release_sources.prowlarr.clients.transmission"]

            from cwa_book_downloader.release_sources.prowlarr.clients.transmission import (
                TransmissionClient,
            )

            client = TransmissionClient()
            magnet = "magnet:?xt=urn:btih:abc123&dn=test"
            client.add_download(magnet, "Test")

            # Verify labels were passed
            call_kwargs = mock_client_instance.add_torrent.call_args
            assert call_kwargs.kwargs.get("labels") == ["mybooks"]


class TestTransmissionClientRemove:
    """Tests for TransmissionClient.remove()."""

    def test_remove_success(self, monkeypatch):
        """Test successful torrent removal."""
        config_values = {
            "TRANSMISSION_URL": "http://localhost:9091",
            "TRANSMISSION_USERNAME": "admin",
            "TRANSMISSION_PASSWORD": "password",
            "TRANSMISSION_CATEGORY": "test",
        }
        monkeypatch.setattr(
            "cwa_book_downloader.release_sources.prowlarr.clients.transmission.config.get",
            make_config_getter(config_values),
        )

        mock_client_instance = MagicMock()

        mock_transmission_rpc = create_mock_transmission_rpc_module()
        mock_transmission_rpc.Client.return_value = mock_client_instance

        with patch.dict("sys.modules", {"transmission_rpc": mock_transmission_rpc}):
            if "cwa_book_downloader.release_sources.prowlarr.clients.transmission" in sys.modules:
                del sys.modules["cwa_book_downloader.release_sources.prowlarr.clients.transmission"]

            from cwa_book_downloader.release_sources.prowlarr.clients.transmission import (
                TransmissionClient,
            )

            client = TransmissionClient()
            result = client.remove("abc123", delete_files=True)

            assert result is True
            mock_client_instance.remove_torrent.assert_called_once_with(
                "abc123", delete_data=True
            )

    def test_remove_failure(self, monkeypatch):
        """Test failed torrent removal."""
        config_values = {
            "TRANSMISSION_URL": "http://localhost:9091",
            "TRANSMISSION_USERNAME": "admin",
            "TRANSMISSION_PASSWORD": "password",
            "TRANSMISSION_CATEGORY": "test",
        }
        monkeypatch.setattr(
            "cwa_book_downloader.release_sources.prowlarr.clients.transmission.config.get",
            make_config_getter(config_values),
        )

        mock_client_instance = MagicMock()
        mock_client_instance.remove_torrent.side_effect = Exception("Not found")

        mock_transmission_rpc = create_mock_transmission_rpc_module()
        mock_transmission_rpc.Client.return_value = mock_client_instance

        with patch.dict("sys.modules", {"transmission_rpc": mock_transmission_rpc}):
            if "cwa_book_downloader.release_sources.prowlarr.clients.transmission" in sys.modules:
                del sys.modules["cwa_book_downloader.release_sources.prowlarr.clients.transmission"]

            from cwa_book_downloader.release_sources.prowlarr.clients.transmission import (
                TransmissionClient,
            )

            client = TransmissionClient()
            result = client.remove("abc123")

            assert result is False


class TestTransmissionClientFindExisting:
    """Tests for TransmissionClient.find_existing()."""

    def test_find_existing_found(self, monkeypatch):
        """Test finding existing torrent by magnet hash."""
        config_values = {
            "TRANSMISSION_URL": "http://localhost:9091",
            "TRANSMISSION_USERNAME": "admin",
            "TRANSMISSION_PASSWORD": "password",
            "TRANSMISSION_CATEGORY": "test",
        }
        monkeypatch.setattr(
            "cwa_book_downloader.release_sources.prowlarr.clients.transmission.config.get",
            make_config_getter(config_values),
        )

        mock_torrent = MockTorrent(
            hash_string="3b245504cf5f11bbdbe1201cea6a6bf45aee1bc0",
            percent_done=0.5,
            status="downloading",
        )
        mock_client_instance = MagicMock()
        mock_client_instance.get_torrent.return_value = mock_torrent

        mock_transmission_rpc = create_mock_transmission_rpc_module()
        mock_transmission_rpc.Client.return_value = mock_client_instance

        with patch.dict("sys.modules", {"transmission_rpc": mock_transmission_rpc}):
            if "cwa_book_downloader.release_sources.prowlarr.clients.transmission" in sys.modules:
                del sys.modules["cwa_book_downloader.release_sources.prowlarr.clients.transmission"]

            from cwa_book_downloader.release_sources.prowlarr.clients.transmission import (
                TransmissionClient,
            )

            client = TransmissionClient()
            magnet = "magnet:?xt=urn:btih:3b245504cf5f11bbdbe1201cea6a6bf45aee1bc0&dn=test"
            result = client.find_existing(magnet)

            assert result is not None
            download_id, status = result
            assert download_id == "3b245504cf5f11bbdbe1201cea6a6bf45aee1bc0"
            assert isinstance(status, DownloadStatus)

    def test_find_existing_not_found(self, monkeypatch):
        """Test finding non-existent torrent."""
        config_values = {
            "TRANSMISSION_URL": "http://localhost:9091",
            "TRANSMISSION_USERNAME": "admin",
            "TRANSMISSION_PASSWORD": "password",
            "TRANSMISSION_CATEGORY": "test",
        }
        monkeypatch.setattr(
            "cwa_book_downloader.release_sources.prowlarr.clients.transmission.config.get",
            make_config_getter(config_values),
        )

        mock_client_instance = MagicMock()
        mock_client_instance.get_torrent.side_effect = KeyError("not found")

        mock_transmission_rpc = create_mock_transmission_rpc_module()
        mock_transmission_rpc.Client.return_value = mock_client_instance

        with patch.dict("sys.modules", {"transmission_rpc": mock_transmission_rpc}):
            if "cwa_book_downloader.release_sources.prowlarr.clients.transmission" in sys.modules:
                del sys.modules["cwa_book_downloader.release_sources.prowlarr.clients.transmission"]

            from cwa_book_downloader.release_sources.prowlarr.clients.transmission import (
                TransmissionClient,
            )

            client = TransmissionClient()
            magnet = "magnet:?xt=urn:btih:abc123&dn=test"
            result = client.find_existing(magnet)

            assert result is None
