"""
Unit tests for the rTorrent client.

These tests mock the xmlrpc library to test the client logic
without requiring a running rTorrent instance.
"""

import sys
from unittest.mock import MagicMock, patch

import pytest


def make_config_getter(values):
    """Create a config.get function that returns values from a dict."""

    def getter(key, default=""):
        return values.get(key, default)

    return getter


def create_mock_xmlrpc_module():
    """Create a mock xmlrpc.client module."""
    mock_module = MagicMock()
    mock_module.ServerProxy = MagicMock()
    return mock_module


def test_set_category_updates_custom1_label():
    from shelfmark.download.clients.rtorrent import RTorrentClient

    client = RTorrentClient.__new__(RTorrentClient)
    client._rpc = MagicMock()

    # rTorrent lookups are case sensitive and info hashes reach us lowercase.
    assert client.set_category("abc123", "imported") is True
    client._rpc.d.custom1.set.assert_called_once_with("ABC123", "imported")


class TestRTorrentClientIsConfigured:
    """Tests for RTorrentClient.is_configured()."""

    def test_is_configured_when_all_set(self, monkeypatch):
        """Test is_configured returns True when properly configured."""
        config_values = {
            "PROWLARR_TORRENT_CLIENT": "rtorrent",
            "RTORRENT_URL": "http://localhost:8080/RPC2",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.rtorrent.config.get",
            make_config_getter(config_values),
        )

        from shelfmark.download.clients.rtorrent import RTorrentClient

        assert RTorrentClient.is_configured() is True

    def test_is_configured_wrong_client(self, monkeypatch):
        """Test is_configured returns False when different client selected."""
        config_values = {
            "PROWLARR_TORRENT_CLIENT": "qbittorrent",
            "RTORRENT_URL": "http://localhost:8080/RPC2",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.rtorrent.config.get",
            make_config_getter(config_values),
        )

        from shelfmark.download.clients.rtorrent import RTorrentClient

        assert RTorrentClient.is_configured() is False

    def test_is_configured_no_url(self, monkeypatch):
        """Test is_configured returns False when URL not set."""
        config_values = {
            "PROWLARR_TORRENT_CLIENT": "rtorrent",
            "RTORRENT_URL": "",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.rtorrent.config.get",
            make_config_getter(config_values),
        )

        from shelfmark.download.clients.rtorrent import RTorrentClient

        assert RTorrentClient.is_configured() is False


class TestRTorrentClientTestConnection:
    """Tests for RTorrentClient.test_connection()."""

    def test_init_https_disabled_verification_uses_unverified_transport(self, monkeypatch):
        """HTTPS rTorrent with verify disabled should use a SafeTransport with custom SSL context."""
        config_values = {
            "RTORRENT_URL": "https://localhost:8080/RPC2",
            "RTORRENT_USERNAME": "",
            "RTORRENT_PASSWORD": "",
            "RTORRENT_DOWNLOAD_DIR": "/downloads",
            "RTORRENT_LABEL": "cwabd",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.rtorrent.config.get",
            make_config_getter(config_values),
        )

        mock_rpc = MagicMock()
        mock_xmlrpc = create_mock_xmlrpc_module()
        mock_xmlrpc.ServerProxy.return_value = mock_rpc

        with patch.dict("sys.modules", {"xmlrpc.client": mock_xmlrpc}):
            if "shelfmark.download.clients.rtorrent" in sys.modules:
                del sys.modules["shelfmark.download.clients.rtorrent"]

            from shelfmark.download.clients import rtorrent as rtorrent_module

            monkeypatch.setattr(rtorrent_module, "get_ssl_verify", lambda _url: False)
            rtorrent_module.RTorrentClient()

            assert mock_xmlrpc.SafeTransport.called is True
            assert "transport" in mock_xmlrpc.ServerProxy.call_args.kwargs

    def test_test_connection_success(self, monkeypatch):
        """Test successful connection."""
        config_values = {
            "RTORRENT_URL": "http://localhost:8080/RPC2",
            "RTORRENT_USERNAME": "",
            "RTORRENT_PASSWORD": "",
            "RTORRENT_DOWNLOAD_DIR": "/downloads",
            "RTORRENT_LABEL": "cwabd",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.rtorrent.config.get",
            make_config_getter(config_values),
        )

        mock_rpc = MagicMock()
        mock_rpc.system.client_version.return_value = "0.9.8"

        mock_xmlrpc = create_mock_xmlrpc_module()
        mock_xmlrpc.ServerProxy.return_value = mock_rpc

        with patch.dict("sys.modules", {"xmlrpc.client": mock_xmlrpc}):
            if "shelfmark.download.clients.rtorrent" in sys.modules:
                del sys.modules["shelfmark.download.clients.rtorrent"]

            from shelfmark.download.clients.rtorrent import (
                RTorrentClient,
            )

            client = RTorrentClient()
            success, message = client.test_connection()

            assert success is True
            assert "0.9.8" in message

    def test_test_connection_failure(self, monkeypatch):
        """Test failed connection."""
        config_values = {
            "RTORRENT_URL": "http://localhost:8080/RPC2",
            "RTORRENT_USERNAME": "",
            "RTORRENT_PASSWORD": "",
            "RTORRENT_DOWNLOAD_DIR": "/downloads",
            "RTORRENT_LABEL": "cwabd",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.rtorrent.config.get",
            make_config_getter(config_values),
        )

        mock_rpc = MagicMock()
        mock_rpc.system.client_version.side_effect = RuntimeError("Connection refused")

        mock_xmlrpc = create_mock_xmlrpc_module()
        mock_xmlrpc.ServerProxy.return_value = mock_rpc

        with patch.dict("sys.modules", {"xmlrpc.client": mock_xmlrpc}):
            if "shelfmark.download.clients.rtorrent" in sys.modules:
                del sys.modules["shelfmark.download.clients.rtorrent"]

            from shelfmark.download.clients.rtorrent import (
                RTorrentClient,
            )

            client = RTorrentClient()
            success, message = client.test_connection()

            assert success is False
            assert "failed" in message.lower()

    def test_test_connection_with_auth(self, monkeypatch):
        """Test connection with HTTP authentication."""
        config_values = {
            "RTORRENT_URL": "http://localhost:8080/RPC2",
            "RTORRENT_USERNAME": "testuser",
            "RTORRENT_PASSWORD": "testpass",
            "RTORRENT_DOWNLOAD_DIR": "/downloads",
            "RTORRENT_LABEL": "cwabd",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.rtorrent.config.get",
            make_config_getter(config_values),
        )

        mock_rpc = MagicMock()
        mock_rpc.system.client_version.return_value = "0.9.8"

        mock_xmlrpc = create_mock_xmlrpc_module()
        mock_xmlrpc.ServerProxy.return_value = mock_rpc

        with patch.dict("sys.modules", {"xmlrpc.client": mock_xmlrpc}):
            if "shelfmark.download.clients.rtorrent" in sys.modules:
                del sys.modules["shelfmark.download.clients.rtorrent"]

            from shelfmark.download.clients.rtorrent import (
                RTorrentClient,
            )

            client = RTorrentClient()
            success, message = client.test_connection()

            assert success is True
            assert "0.9.8" in message


class TestRTorrentClientAddDownload:
    """Tests for RTorrentClient.add_download()."""

    def test_add_download_magnet(self, monkeypatch):
        """Test adding a torrent via magnet link."""
        config_values = {
            "RTORRENT_URL": "http://localhost:8080/RPC2",
            "RTORRENT_USERNAME": "",
            "RTORRENT_PASSWORD": "",
            "RTORRENT_DOWNLOAD_DIR": "/downloads",
            "RTORRENT_LABEL": "cwabd",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.rtorrent.config.get",
            make_config_getter(config_values),
        )

        mock_rpc = MagicMock()
        mock_xmlrpc = create_mock_xmlrpc_module()
        mock_xmlrpc.ServerProxy.return_value = mock_rpc

        # Mock extract_torrent_info
        mock_torrent_info = MagicMock()
        mock_torrent_info.torrent_data = None
        mock_torrent_info.magnet_url = "magnet:?xt=urn:btih:abc123def456"
        mock_torrent_info.info_hash = "abc123def456"
        mock_torrent_info.is_magnet = True

        with patch.dict("sys.modules", {"xmlrpc.client": mock_xmlrpc}):
            with patch(
                "shelfmark.download.clients.torrent_utils.extract_torrent_info",
                return_value=mock_torrent_info,
            ):
                if "shelfmark.download.clients.rtorrent" in sys.modules:
                    del sys.modules["shelfmark.download.clients.rtorrent"]

                from shelfmark.download.clients.rtorrent import (
                    RTorrentClient,
                )

                client = RTorrentClient()
                result_hash = client.add_download(
                    "magnet:?xt=urn:btih:abc123def456", "Test Torrent"
                )

                assert result_hash == "abc123def456"
                mock_rpc.load.start.assert_called_once()
                args = mock_rpc.load.start.call_args[0]
                assert args[1] == "magnet:?xt=urn:btih:abc123def456"
                assert "d.custom1.set=cwabd" in args[2]
                assert "d.directory.set=/downloads" in args[2]

    def test_add_download_torrent_file(self, monkeypatch):
        """Test adding a torrent via raw torrent data."""
        config_values = {
            "RTORRENT_URL": "http://localhost:8080/RPC2",
            "RTORRENT_USERNAME": "",
            "RTORRENT_PASSWORD": "",
            "RTORRENT_DOWNLOAD_DIR": "/downloads",
            "RTORRENT_LABEL": "cwabd",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.rtorrent.config.get",
            make_config_getter(config_values),
        )

        mock_rpc = MagicMock()
        mock_xmlrpc = create_mock_xmlrpc_module()
        mock_xmlrpc.ServerProxy.return_value = mock_rpc

        # Mock extract_torrent_info
        mock_torrent_info = MagicMock()
        mock_torrent_info.torrent_data = b"raw_torrent_data"
        mock_torrent_info.magnet_url = None
        mock_torrent_info.info_hash = "abc123def456"
        mock_torrent_info.is_magnet = False

        with patch.dict("sys.modules", {"xmlrpc.client": mock_xmlrpc}):
            with patch(
                "shelfmark.download.clients.torrent_utils.extract_torrent_info",
                return_value=mock_torrent_info,
            ):
                if "shelfmark.download.clients.rtorrent" in sys.modules:
                    del sys.modules["shelfmark.download.clients.rtorrent"]

                from shelfmark.download.clients.rtorrent import (
                    RTorrentClient,
                )

                client = RTorrentClient()
                result_hash = client.add_download("http://example.com/test.torrent", "Test Torrent")

                assert result_hash == "abc123def456"
                mock_rpc.load.raw_start.assert_called_once()
                args = mock_rpc.load.raw_start.call_args[0]
                assert args[1] == b"raw_torrent_data"
                assert "d.custom1.set=cwabd" in args[2]

    def test_add_download_failure(self, monkeypatch):
        """Test failure when adding a download."""
        config_values = {
            "RTORRENT_URL": "http://localhost:8080/RPC2",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.rtorrent.config.get",
            make_config_getter(config_values),
        )

        mock_rpc = MagicMock()
        mock_rpc.load.start.side_effect = RuntimeError("RPC Error")
        mock_xmlrpc = create_mock_xmlrpc_module()
        mock_xmlrpc.ServerProxy.return_value = mock_rpc

        mock_torrent_info = MagicMock()
        mock_torrent_info.torrent_data = None
        mock_torrent_info.magnet_url = "magnet:..."
        mock_torrent_info.info_hash = "abc"

        with patch.dict("sys.modules", {"xmlrpc.client": mock_xmlrpc}):
            with patch(
                "shelfmark.download.clients.rtorrent.extract_torrent_info",
                return_value=mock_torrent_info,
            ):
                if "shelfmark.download.clients.rtorrent" in sys.modules:
                    del sys.modules["shelfmark.download.clients.rtorrent"]

                from shelfmark.download.clients.rtorrent import (
                    RTorrentClient,
                )

                client = RTorrentClient()
                with pytest.raises(RuntimeError) as excinfo:
                    client.add_download("magnet:...", "Test")

                assert "RPC Error" in str(excinfo.value)

    def test_add_download_discovers_hash_when_extraction_fails(self, monkeypatch):
        """Regression for #1012: a URL add without a hash adopts the new download's hash.

        rTorrent fetches .torrent URLs itself, so the add succeeds even when the
        prefetch could not determine the info_hash; the client must recover the
        hash from the download that appears instead of raising.
        """
        config_values = {
            "RTORRENT_URL": "http://localhost:8080/RPC2",
            "RTORRENT_USERNAME": "",
            "RTORRENT_PASSWORD": "",
            "RTORRENT_DOWNLOAD_DIR": "/downloads",
            "RTORRENT_LABEL": "cwabd",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.rtorrent.config.get",
            make_config_getter(config_values),
        )

        existing_hash = "A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4E5F6A1B2"
        discovered_hash = "3B245504CF5F11BBDBE1201CEA6A6BF45AEE1BC0"

        mock_rpc = MagicMock()
        # First multicall2 is the pre-add snapshot; the second is the discovery
        # poll after load.start, where the new download has appeared.
        mock_rpc.d.multicall2.side_effect = [
            [[existing_hash]],
            [
                [existing_hash, "Existing Torrent", "cwabd"],
                [discovered_hash, "Test Torrent", "cwabd"],
            ],
        ]
        mock_xmlrpc = create_mock_xmlrpc_module()
        mock_xmlrpc.ServerProxy.return_value = mock_rpc

        mock_torrent_info = MagicMock()
        mock_torrent_info.torrent_data = None
        mock_torrent_info.magnet_url = None
        mock_torrent_info.info_hash = None
        mock_torrent_info.is_magnet = False

        with patch.dict("sys.modules", {"xmlrpc.client": mock_xmlrpc}):
            with patch(
                "shelfmark.download.clients.torrent_utils.extract_torrent_info",
                return_value=mock_torrent_info,
            ):
                if "shelfmark.download.clients.rtorrent" in sys.modules:
                    del sys.modules["shelfmark.download.clients.rtorrent"]

                from shelfmark.download.clients.rtorrent import (
                    RTorrentClient,
                )

                client = RTorrentClient()
                result_hash = client.add_download(
                    "http://tracker.example/download/book.torrent", "Test Torrent"
                )

                assert result_hash == discovered_hash.lower()
                mock_rpc.load.start.assert_called_once()

    def test_add_download_raises_when_hash_never_discovered(self, monkeypatch):
        """Keep failing loudly when no hash is known and no new download appears."""
        config_values = {
            "RTORRENT_URL": "http://localhost:8080/RPC2",
            "RTORRENT_USERNAME": "",
            "RTORRENT_PASSWORD": "",
            "RTORRENT_DOWNLOAD_DIR": "/downloads",
            "RTORRENT_LABEL": "cwabd",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.rtorrent.config.get",
            make_config_getter(config_values),
        )

        existing_hash = "A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4E5F6A1B2"
        mock_rpc = MagicMock()
        mock_rpc.d.multicall2.return_value = [[existing_hash, "Existing Torrent", "cwabd"]]
        mock_xmlrpc = create_mock_xmlrpc_module()
        mock_xmlrpc.ServerProxy.return_value = mock_rpc

        mock_torrent_info = MagicMock()
        mock_torrent_info.torrent_data = None
        mock_torrent_info.magnet_url = None
        mock_torrent_info.info_hash = None
        mock_torrent_info.is_magnet = False

        with patch.dict("sys.modules", {"xmlrpc.client": mock_xmlrpc}):
            with patch(
                "shelfmark.download.clients.torrent_utils.extract_torrent_info",
                return_value=mock_torrent_info,
            ):
                if "shelfmark.download.clients.rtorrent" in sys.modules:
                    del sys.modules["shelfmark.download.clients.rtorrent"]

                from shelfmark.download.clients import rtorrent as rtorrent_module

                monkeypatch.setattr(rtorrent_module.time, "sleep", lambda _seconds: None)

                client = rtorrent_module.RTorrentClient()
                with pytest.raises(RuntimeError, match="Could not determine torrent hash"):
                    client.add_download(
                        "http://tracker.example/download/book.torrent", "Test Torrent"
                    )


class TestRTorrentClientAudiobookLabel:
    """Regression tests for issue #1025 — rTorrent audiobook label selection."""

    def _make_client(self, monkeypatch, config_values):
        monkeypatch.setattr(
            "shelfmark.download.clients.rtorrent.config.get",
            make_config_getter(config_values),
        )
        mock_rpc = MagicMock()
        mock_xmlrpc = create_mock_xmlrpc_module()
        mock_xmlrpc.ServerProxy.return_value = mock_rpc

        mock_torrent_info = MagicMock()
        mock_torrent_info.torrent_data = None
        mock_torrent_info.magnet_url = "magnet:?xt=urn:btih:abc123"
        mock_torrent_info.info_hash = "abc123"
        mock_torrent_info.is_magnet = True

        return mock_rpc, mock_xmlrpc, mock_torrent_info

    def test_uses_audiobook_label_when_content_type_is_audiobook(self, monkeypatch):
        config_values = {
            "RTORRENT_URL": "http://localhost:8080/RPC2",
            "RTORRENT_LABEL": "books",
            "RTORRENT_AUDIOBOOK_LABEL": "audiobooks",
            "RTORRENT_DOWNLOAD_DIR": "/downloads",
        }
        mock_rpc, mock_xmlrpc, mock_torrent_info = self._make_client(monkeypatch, config_values)

        with patch.dict("sys.modules", {"xmlrpc.client": mock_xmlrpc}):
            with patch(
                "shelfmark.download.clients.torrent_utils.extract_torrent_info",
                return_value=mock_torrent_info,
            ):
                if "shelfmark.download.clients.rtorrent" in sys.modules:
                    del sys.modules["shelfmark.download.clients.rtorrent"]
                from shelfmark.download.clients.rtorrent import RTorrentClient

                client = RTorrentClient()
                client.add_download(
                    "magnet:?xt=urn:btih:abc123", "Test Audiobook", content_type="audiobook"
                )

        args = mock_rpc.load.start.call_args[0]
        assert "d.custom1.set=audiobooks" in args[2]
        assert "d.custom1.set=books" not in args[2]

    def test_falls_back_to_book_label_when_audiobook_label_not_set(self, monkeypatch):
        config_values = {
            "RTORRENT_URL": "http://localhost:8080/RPC2",
            "RTORRENT_LABEL": "books",
            "RTORRENT_AUDIOBOOK_LABEL": "",
            "RTORRENT_DOWNLOAD_DIR": "/downloads",
        }
        mock_rpc, mock_xmlrpc, mock_torrent_info = self._make_client(monkeypatch, config_values)

        with patch.dict("sys.modules", {"xmlrpc.client": mock_xmlrpc}):
            with patch(
                "shelfmark.download.clients.torrent_utils.extract_torrent_info",
                return_value=mock_torrent_info,
            ):
                if "shelfmark.download.clients.rtorrent" in sys.modules:
                    del sys.modules["shelfmark.download.clients.rtorrent"]
                from shelfmark.download.clients.rtorrent import RTorrentClient

                client = RTorrentClient()
                client.add_download(
                    "magnet:?xt=urn:btih:abc123", "Test Audiobook", content_type="audiobook"
                )

        args = mock_rpc.load.start.call_args[0]
        assert "d.custom1.set=books" in args[2]

    def test_uses_book_label_for_non_audiobook_content(self, monkeypatch):
        config_values = {
            "RTORRENT_URL": "http://localhost:8080/RPC2",
            "RTORRENT_LABEL": "books",
            "RTORRENT_AUDIOBOOK_LABEL": "audiobooks",
            "RTORRENT_DOWNLOAD_DIR": "/downloads",
        }
        mock_rpc, mock_xmlrpc, mock_torrent_info = self._make_client(monkeypatch, config_values)

        with patch.dict("sys.modules", {"xmlrpc.client": mock_xmlrpc}):
            with patch(
                "shelfmark.download.clients.torrent_utils.extract_torrent_info",
                return_value=mock_torrent_info,
            ):
                if "shelfmark.download.clients.rtorrent" in sys.modules:
                    del sys.modules["shelfmark.download.clients.rtorrent"]
                from shelfmark.download.clients.rtorrent import RTorrentClient

                client = RTorrentClient()
                client.add_download("magnet:?xt=urn:btih:abc123", "Test Book")

        args = mock_rpc.load.start.call_args[0]
        assert "d.custom1.set=books" in args[2]
        assert "d.custom1.set=audiobooks" not in args[2]

    def test_uses_audiobook_label_for_compound_content_type(self, monkeypatch):
        """Issue #1235 — an exact "audiobook" match missed forms like "book (audiobook)"."""
        config_values = {
            "RTORRENT_URL": "http://localhost:8080/RPC2",
            "RTORRENT_LABEL": "books",
            "RTORRENT_AUDIOBOOK_LABEL": "audiobooks",
            "RTORRENT_DOWNLOAD_DIR": "/downloads",
        }
        mock_rpc, mock_xmlrpc, mock_torrent_info = self._make_client(monkeypatch, config_values)

        with patch.dict("sys.modules", {"xmlrpc.client": mock_xmlrpc}):
            with patch(
                "shelfmark.download.clients.torrent_utils.extract_torrent_info",
                return_value=mock_torrent_info,
            ):
                if "shelfmark.download.clients.rtorrent" in sys.modules:
                    del sys.modules["shelfmark.download.clients.rtorrent"]
                from shelfmark.download.clients.rtorrent import RTorrentClient

                client = RTorrentClient()
                client.add_download(
                    "magnet:?xt=urn:btih:abc123",
                    "Test Audiobook",
                    content_type="Book (Audiobook)",
                )

        args = mock_rpc.load.start.call_args[0]
        assert "d.custom1.set=audiobooks" in args[2]
        assert "d.custom1.set=books" not in args[2]


class TestRTorrentClientGetStatus:
    """Tests for RTorrentClient.get_status()."""

    def test_get_status_downloading(self, monkeypatch):
        """Test status for downloading torrent."""
        config_values = {
            "RTORRENT_URL": "http://localhost:8080/RPC2",
            "RTORRENT_USERNAME": "",
            "RTORRENT_PASSWORD": "",
            "RTORRENT_DOWNLOAD_DIR": "/downloads",
            "RTORRENT_LABEL": "cwabd",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.rtorrent.config.get",
            make_config_getter(config_values),
        )

        mock_rpc = MagicMock()
        mock_rpc.d.multicall2.return_value = [
            [
                "ABC123DEF456",
                2,
                524288000,
                1048576000,
                1024000,
                0,
                "cwabd",
                0,
            ]
        ]

        mock_xmlrpc = create_mock_xmlrpc_module()
        mock_xmlrpc.ServerProxy.return_value = mock_rpc

        with patch.dict("sys.modules", {"xmlrpc.client": mock_xmlrpc}):
            if "shelfmark.download.clients.rtorrent" in sys.modules:
                del sys.modules["shelfmark.download.clients.rtorrent"]

            from shelfmark.download.clients.rtorrent import (
                RTorrentClient,
            )

            client = RTorrentClient()
            status = client.get_status("abc123def456")

            assert status.progress == 50.0
            assert status.state_value == "downloading"
            assert status.complete is False
            assert status.download_speed == 1024000

    def test_get_status_seeding(self, monkeypatch):
        """Test status for seeding (complete) torrent."""
        config_values = {
            "RTORRENT_URL": "http://localhost:8080/RPC2",
            "RTORRENT_USERNAME": "",
            "RTORRENT_PASSWORD": "",
            "RTORRENT_DOWNLOAD_DIR": "/downloads",
            "RTORRENT_LABEL": "cwabd",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.rtorrent.config.get",
            make_config_getter(config_values),
        )

        mock_rpc = MagicMock()
        # First call: get_status multicall2, second call: _get_torrent_path multicall2
        mock_rpc.d.multicall2.side_effect = [
            [
                [
                    "ABC123DEF456",
                    4,
                    1048576000,
                    1048576000,
                    0,
                    2048000,
                    "cwabd",
                    1,
                ]
            ],
            [["ABC123DEF456", "/downloads/test-torrent"]],
        ]

        mock_xmlrpc = create_mock_xmlrpc_module()
        mock_xmlrpc.ServerProxy.return_value = mock_rpc

        with patch.dict("sys.modules", {"xmlrpc.client": mock_xmlrpc}):
            if "shelfmark.download.clients.rtorrent" in sys.modules:
                del sys.modules["shelfmark.download.clients.rtorrent"]

            from shelfmark.download.clients.rtorrent import (
                RTorrentClient,
            )

            client = RTorrentClient()
            status = client.get_status("abc123def456")

            assert status.progress == 100.0
            assert status.state_value == "complete"
            assert status.complete is True
            assert status.file_path == "/downloads/test-torrent"
            assert status.message == "Seeding"

    def test_get_status_torrent_not_found(self, monkeypatch):
        """Test status when torrent not found."""
        config_values = {
            "RTORRENT_URL": "http://localhost:8080/RPC2",
            "RTORRENT_USERNAME": "",
            "RTORRENT_PASSWORD": "",
            "RTORRENT_DOWNLOAD_DIR": "/downloads",
            "RTORRENT_LABEL": "cwabd",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.rtorrent.config.get",
            make_config_getter(config_values),
        )

        mock_rpc = MagicMock()
        mock_rpc.d.multicall2.return_value = []

        mock_xmlrpc = create_mock_xmlrpc_module()
        mock_xmlrpc.ServerProxy.return_value = mock_rpc

        with patch.dict("sys.modules", {"xmlrpc.client": mock_xmlrpc}):
            if "shelfmark.download.clients.rtorrent" in sys.modules:
                del sys.modules["shelfmark.download.clients.rtorrent"]

            from shelfmark.download.clients.rtorrent import (
                RTorrentClient,
            )

            client = RTorrentClient()
            status = client.get_status("nonexistent")

            assert status.state_value == "error"
            assert status.complete is False

    def test_get_status_with_eta(self, monkeypatch):
        """Test status with ETA calculation."""
        config_values = {
            "RTORRENT_URL": "http://localhost:8080/RPC2",
            "RTORRENT_USERNAME": "",
            "RTORRENT_PASSWORD": "",
            "RTORRENT_DOWNLOAD_DIR": "/downloads",
            "RTORRENT_LABEL": "cwabd",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.rtorrent.config.get",
            make_config_getter(config_values),
        )

        mock_rpc = MagicMock()
        mock_rpc.d.multicall2.return_value = [
            [
                "ABC123DEF456",
                2,
                524288000,
                1048576000,
                1048576,
                0,
                "cwabd",
                0,
            ]
        ]

        mock_xmlrpc = create_mock_xmlrpc_module()
        mock_xmlrpc.ServerProxy.return_value = mock_rpc

        with patch.dict("sys.modules", {"xmlrpc.client": mock_xmlrpc}):
            if "shelfmark.download.clients.rtorrent" in sys.modules:
                del sys.modules["shelfmark.download.clients.rtorrent"]

            from shelfmark.download.clients.rtorrent import (
                RTorrentClient,
            )

            client = RTorrentClient()
            status = client.get_status("abc123def456")

            assert status.eta is not None
            assert status.eta == 500


class TestRTorrentClientRemove:
    """Tests for RTorrentClient.remove()."""

    def test_remove_success(self, monkeypatch):
        """Test successful torrent removal."""
        config_values = {
            "RTORRENT_URL": "http://localhost:8080/RPC2",
            "RTORRENT_USERNAME": "",
            "RTORRENT_PASSWORD": "",
            "RTORRENT_DOWNLOAD_DIR": "/downloads",
            "RTORRENT_LABEL": "cwabd",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.rtorrent.config.get",
            make_config_getter(config_values),
        )

        mock_rpc = MagicMock()

        mock_xmlrpc = create_mock_xmlrpc_module()
        mock_xmlrpc.ServerProxy.return_value = mock_rpc

        with patch.dict("sys.modules", {"xmlrpc.client": mock_xmlrpc}):
            if "shelfmark.download.clients.rtorrent" in sys.modules:
                del sys.modules["shelfmark.download.clients.rtorrent"]

            from shelfmark.download.clients.rtorrent import (
                RTorrentClient,
            )

            client = RTorrentClient()
            result = client.remove("abc123def456", delete_files=False)

            assert result is True
            # rTorrent lookups are case sensitive and info hashes reach us lowercase.
            mock_rpc.d.stop.assert_called_once_with("ABC123DEF456")
            mock_rpc.d.erase.assert_called_once_with("ABC123DEF456")

    def test_remove_with_files_is_unsupported(self, monkeypatch):
        """Do not erase a torrent when rTorrent cannot delete its payload."""
        config_values = {
            "RTORRENT_URL": "http://localhost:8080/RPC2",
            "RTORRENT_USERNAME": "",
            "RTORRENT_PASSWORD": "",
            "RTORRENT_DOWNLOAD_DIR": "/downloads",
            "RTORRENT_LABEL": "cwabd",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.rtorrent.config.get",
            make_config_getter(config_values),
        )

        mock_rpc = MagicMock()
        mock_xmlrpc = create_mock_xmlrpc_module()
        mock_xmlrpc.ServerProxy.return_value = mock_rpc

        with patch.dict("sys.modules", {"xmlrpc.client": mock_xmlrpc}):
            if "shelfmark.download.clients.rtorrent" in sys.modules:
                del sys.modules["shelfmark.download.clients.rtorrent"]

            from shelfmark.download.clients.rtorrent import RTorrentClient

            client = RTorrentClient()
            assert client.remove("abc123def456", delete_files=True) is False
            mock_rpc.d.stop.assert_not_called()
            mock_rpc.d.erase.assert_not_called()
            mock_rpc.d.delete_tied.assert_not_called()

    def test_remove_failure(self, monkeypatch):
        """Test failed torrent removal."""
        config_values = {
            "RTORRENT_URL": "http://localhost:8080/RPC2",
            "RTORRENT_USERNAME": "",
            "RTORRENT_PASSWORD": "",
            "RTORRENT_DOWNLOAD_DIR": "/downloads",
            "RTORRENT_LABEL": "cwabd",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.rtorrent.config.get",
            make_config_getter(config_values),
        )

        mock_rpc = MagicMock()
        mock_rpc.d.stop.side_effect = RuntimeError("Connection lost")

        mock_xmlrpc = create_mock_xmlrpc_module()
        mock_xmlrpc.ServerProxy.return_value = mock_rpc

        with patch.dict("sys.modules", {"xmlrpc.client": mock_xmlrpc}):
            if "shelfmark.download.clients.rtorrent" in sys.modules:
                del sys.modules["shelfmark.download.clients.rtorrent"]

            from shelfmark.download.clients.rtorrent import (
                RTorrentClient,
            )

            client = RTorrentClient()
            result = client.remove("abc123def456")

            assert result is False


class TestRTorrentClientGetDownloadPath:
    """Tests for RTorrentClient.get_download_path()."""

    def test_get_download_path_success(self, monkeypatch):
        """Test getting download path successfully."""
        config_values = {
            "RTORRENT_URL": "http://localhost:8080/RPC2",
            "RTORRENT_USERNAME": "",
            "RTORRENT_PASSWORD": "",
            "RTORRENT_DOWNLOAD_DIR": "/downloads",
            "RTORRENT_LABEL": "cwabd",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.rtorrent.config.get",
            make_config_getter(config_values),
        )

        mock_rpc = MagicMock()
        mock_rpc.d.multicall2.return_value = [["ABC123DEF456", "/downloads/test-file"]]

        mock_xmlrpc = create_mock_xmlrpc_module()
        mock_xmlrpc.ServerProxy.return_value = mock_rpc

        with patch.dict("sys.modules", {"xmlrpc.client": mock_xmlrpc}):
            if "shelfmark.download.clients.rtorrent" in sys.modules:
                del sys.modules["shelfmark.download.clients.rtorrent"]

            from shelfmark.download.clients.rtorrent import (
                RTorrentClient,
            )

            client = RTorrentClient()
            path = client.get_download_path("abc123def456")

            assert path == "/downloads/test-file"

    def test_get_download_path_failure(self, monkeypatch):
        """Test getting download path when torrent not found."""
        config_values = {
            "RTORRENT_URL": "http://localhost:8080/RPC2",
            "RTORRENT_USERNAME": "",
            "RTORRENT_PASSWORD": "",
            "RTORRENT_DOWNLOAD_DIR": "/downloads",
            "RTORRENT_LABEL": "cwabd",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.rtorrent.config.get",
            make_config_getter(config_values),
        )

        mock_rpc = MagicMock()
        mock_rpc.d.multicall2.return_value = []

        mock_xmlrpc = create_mock_xmlrpc_module()
        mock_xmlrpc.ServerProxy.return_value = mock_rpc

        with patch.dict("sys.modules", {"xmlrpc.client": mock_xmlrpc}):
            if "shelfmark.download.clients.rtorrent" in sys.modules:
                del sys.modules["shelfmark.download.clients.rtorrent"]

            from shelfmark.download.clients.rtorrent import (
                RTorrentClient,
            )

            client = RTorrentClient()
            path = client.get_download_path("nonexistent")

            assert path is None
