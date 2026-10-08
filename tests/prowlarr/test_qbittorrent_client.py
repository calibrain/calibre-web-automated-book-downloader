"""
Unit tests for the qBittorrent client.

These tests mock the qbittorrentapi library to test the client logic
without requiring a running qBittorrent instance.
"""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from shelfmark.download.clients import DownloadStatus
from shelfmark.download.clients.torrent_utils import TorrentInfo


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
        infohash_v1=None,
        infohash_v2=None,
    ):
        self.hash = hash_val
        self.name = name
        self.progress = progress
        self.state = state
        self.dlspeed = dlspeed
        self.eta = eta
        self.content_path = content_path
        self.infohash_v1 = infohash_v1
        self.infohash_v2 = infohash_v2

    def to_dict(self):
        """Convert to dict for JSON response mocking."""
        result = {
            "hash": self.hash,
            "name": self.name,
            "progress": self.progress,
            "state": self.state,
            "dlspeed": self.dlspeed,
            "eta": self.eta,
            "content_path": self.content_path,
        }
        if self.infohash_v1 is not None:
            result["infohash_v1"] = self.infohash_v1
        if self.infohash_v2 is not None:
            result["infohash_v2"] = self.infohash_v2
        return result


def create_mock_session_response(torrents, status_code=200):
    """Create a mock response for _session.get() calls."""
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.json.return_value = [
        t.to_dict() if isinstance(t, MockTorrent) else t for t in torrents
    ]
    mock_response.raise_for_status = MagicMock()
    return mock_response


def test_set_category_creates_and_assigns_category():
    from shelfmark.download.clients.qbittorrent import QBittorrentClient

    client = QBittorrentClient.__new__(QBittorrentClient)
    client._client = MagicMock()

    assert client.set_category("abc123", "imported") is True
    client._client.torrents_create_category.assert_called_once_with(name="imported")
    client._client.torrents_set_category.assert_called_once_with(
        torrent_hashes="abc123",
        category="imported",
    )


class TestQBittorrentClientIsConfigured:
    """Tests for QBittorrentClient.is_configured()."""

    def test_is_configured_when_all_set(self, monkeypatch):
        """Test is_configured returns True when properly configured."""
        config_values = {
            "PROWLARR_TORRENT_CLIENT": "qbittorrent",
            "QBITTORRENT_URL": "http://localhost:8080",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.qbittorrent.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        from shelfmark.download.clients.qbittorrent import (
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
            "shelfmark.download.clients.qbittorrent.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        from shelfmark.download.clients.qbittorrent import (
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
            "shelfmark.download.clients.qbittorrent.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        from shelfmark.download.clients.qbittorrent import (
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
            "shelfmark.download.clients.qbittorrent.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        mock_client_instance = MagicMock()
        mock_client_instance.app.web_api_version = "2.9.3"
        mock_client_class = MagicMock(return_value=mock_client_instance)

        # Mock the import inside the module
        with patch.dict("sys.modules", {"qbittorrentapi": MagicMock(Client=mock_client_class)}):
            # Need to reimport after patching
            import importlib

            import shelfmark.download.clients.qbittorrent as qb_module

            importlib.reload(qb_module)

            client = qb_module.QBittorrentClient()
            success, message = client.test_connection()

            assert success is True
            assert "2.9.3" in message

    def test_test_connection_failure(self, monkeypatch):
        """Test failed connection."""
        config_values = {
            "QBITTORRENT_URL": "localhost:8080",
            "QBITTORRENT_USERNAME": "admin",
            "QBITTORRENT_PASSWORD": "wrong",
            "QBITTORRENT_CATEGORY": "test",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.qbittorrent.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        mock_client_instance = MagicMock()
        mock_client_instance.auth_log_in.side_effect = RuntimeError("401 Unauthorized")
        mock_client_class = MagicMock(return_value=mock_client_instance)

        with patch.dict("sys.modules", {"qbittorrentapi": MagicMock(Client=mock_client_class)}):
            import importlib

            import shelfmark.download.clients.qbittorrent as qb_module

            importlib.reload(qb_module)

            client = qb_module.QBittorrentClient()
            success, message = client.test_connection()

            assert success is False
            assert "401" in message or "failed" in message.lower()


class TestQBittorrentClientScheme:
    """qbittorrent-api probes for a scheme unless told to keep the configured one (#1417)."""

    @pytest.mark.parametrize(
        ("url", "forced"),
        [
            ("https://qbittorrent.example.com", True),
            ("HTTPS://qbittorrent.example.com:443", True),
            ("http://localhost:8080", False),
            ("localhost:8080", False),
        ],
    )
    def test_only_an_explicit_https_scheme_is_kept(self, monkeypatch, url, forced):
        """https:// must not fall back to plain HTTP; http:// and bare hosts keep the probe."""
        config_values = {
            "QBITTORRENT_URL": url,
            "QBITTORRENT_USERNAME": "admin",
            "QBITTORRENT_PASSWORD": "password",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.qbittorrent.config.get",
            lambda key, default="": config_values.get(key, default),
        )
        mock_client_class = MagicMock(return_value=MagicMock())

        with patch.dict("sys.modules", {"qbittorrentapi": MagicMock(Client=mock_client_class)}):
            import importlib

            import shelfmark.download.clients.qbittorrent as qb_module

            importlib.reload(qb_module)
            qb_module.QBittorrentClient()

        assert mock_client_class.call_args.kwargs["FORCE_SCHEME_FROM_HOST"] is forced


class TestQBittorrentClientApiKeyAuth:
    """Tests for API key authentication (qBittorrent 5.2.0+)."""

    API_KEY = "qbt_0123456789abcdefghijklmnopqr"

    @contextmanager
    def _build_client(self, monkeypatch, api_key, mock_client_instance=None):
        """Construct the client against a stubbed qbittorrentapi, yielding it and the stub."""
        config_values = {
            "QBITTORRENT_URL": "http://localhost:8080",
            "QBITTORRENT_USERNAME": "admin",
            "QBITTORRENT_PASSWORD": "password",
            "QBITTORRENT_API_KEY": api_key,
            "QBITTORRENT_CATEGORY": "test",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.qbittorrent.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        mock_client_class = MagicMock(return_value=mock_client_instance or MagicMock())

        with patch.dict("sys.modules", {"qbittorrentapi": MagicMock(Client=mock_client_class)}):
            import importlib

            import shelfmark.download.clients.qbittorrent as qb_module

            importlib.reload(qb_module)
            yield qb_module.QBittorrentClient(), mock_client_class

    @pytest.mark.parametrize(
        ("api_key", "expected_kwarg"),
        [(API_KEY, API_KEY), ("", None)],
    )
    def test_api_key_forwarded_to_qbittorrentapi(self, monkeypatch, api_key, expected_kwarg):
        """A configured key is handed to qbittorrent-api; without one it falls back to password."""
        with self._build_client(monkeypatch, api_key) as (_client, mock_client_class):
            assert mock_client_class.call_args.kwargs["api_key"] == expected_kwarg

    @pytest.mark.parametrize(("api_key", "logs_in"), [(API_KEY, False), ("", True)])
    def test_direct_requests_log_in_only_for_cookie_auth(self, monkeypatch, api_key, logs_in):
        """Bearer auth is stateless, so only cookie auth needs a login before direct calls."""
        mock_client_instance = MagicMock()
        mock_client_instance._session.get.return_value = create_mock_session_response(
            [MockTorrent(progress=0.5, state="downloading")], status_code=200
        )

        with self._build_client(monkeypatch, api_key, mock_client_instance) as (client, _):
            status = client.get_status("abc123")

            assert status.progress == 50.0
            assert mock_client_instance.auth_log_in.called is logs_in

    @pytest.mark.parametrize(("api_key", "expected_requests"), [(API_KEY, 1), ("", 2)])
    def test_403_retried_only_when_a_login_can_refresh_it(
        self, monkeypatch, api_key, expected_requests
    ):
        """A bearer token cannot be refreshed, so re-issuing a 403 would just waste a request."""
        mock_client_instance = MagicMock()
        mock_client_instance._session.get.return_value = create_mock_session_response(
            [], status_code=403
        )

        with self._build_client(monkeypatch, api_key, mock_client_instance) as (client, _):
            status = client.get_status("abc123")

            assert status.state_value == "error"
            assert mock_client_instance._session.get.call_count == expected_requests


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
            "shelfmark.download.clients.qbittorrent.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        mock_torrent = MockTorrent(progress=0.5, state="downloading", dlspeed=1024000, eta=3600)
        mock_client_instance = MagicMock()
        # Mock the session.get for _get_torrents_info
        mock_client_instance._session.get.return_value = create_mock_session_response(
            [mock_torrent], status_code=200
        )
        mock_client_class = MagicMock(return_value=mock_client_instance)

        with patch.dict("sys.modules", {"qbittorrentapi": MagicMock(Client=mock_client_class)}):
            import importlib

            import shelfmark.download.clients.qbittorrent as qb_module

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
            "shelfmark.download.clients.qbittorrent.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        mock_torrent = MockTorrent(
            progress=1.0,
            state="uploading",
            content_path="/downloads/completed.epub",
        )
        mock_client_instance = MagicMock()
        # Mock the session.get for _get_torrents_info
        mock_client_instance._session.get.return_value = create_mock_session_response(
            [mock_torrent], status_code=200
        )
        mock_client_class = MagicMock(return_value=mock_client_instance)

        with patch.dict("sys.modules", {"qbittorrentapi": MagicMock(Client=mock_client_class)}):
            import importlib

            import shelfmark.download.clients.qbittorrent as qb_module

            importlib.reload(qb_module)

            client = qb_module.QBittorrentClient()
            status = client.get_status("abc123")

            assert status.progress == 100.0
            assert status.complete is True
            assert status.file_path == "/downloads/completed.epub"

    def test_get_status_complete_returns_content_path(self, monkeypatch):
        """Completed torrents return content_path as-is."""
        config_values = {
            "QBITTORRENT_URL": "http://localhost:8080",
            "QBITTORRENT_USERNAME": "admin",
            "QBITTORRENT_PASSWORD": "password",
            "QBITTORRENT_CATEGORY": "test",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.qbittorrent.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        mock_torrent = MockTorrent(
            hash_val="abc123",
            progress=1.0,
            state="uploading",
            content_path="/downloads/shelfmark/Ground State - Craig Alanson/Ground State - Craig Alanson.epub",
        )

        mock_client_instance = MagicMock()
        info_payload = mock_torrent.to_dict() | {"save_path": "/downloads/shelfmark"}
        mock_client_instance._session.get.return_value = create_mock_session_response(
            [info_payload], status_code=200
        )
        mock_client_class = MagicMock(return_value=mock_client_instance)

        with patch.dict("sys.modules", {"qbittorrentapi": MagicMock(Client=mock_client_class)}):
            import importlib

            import shelfmark.download.clients.qbittorrent as qb_module

            importlib.reload(qb_module)

            client = qb_module.QBittorrentClient()
            status = client.get_status("abc123")

            assert status.complete is True
            assert (
                status.file_path
                == "/downloads/shelfmark/Ground State - Craig Alanson/Ground State - Craig Alanson.epub"
            )

    def test_get_status_paused_up_complete(self, monkeypatch):
        """qBittorrent-compatible clients may report completed items as pausedUP."""
        config_values = {
            "QBITTORRENT_URL": "http://localhost:8080",
            "QBITTORRENT_USERNAME": "admin",
            "QBITTORRENT_PASSWORD": "password",
            "QBITTORRENT_CATEGORY": "test",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.qbittorrent.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        mock_torrent = MockTorrent(
            hash_val="abc123",
            progress=1.0,
            state="pausedUP",
            content_path="/downloads/completed.epub",
        )
        mock_client_instance = MagicMock()
        info_payload = mock_torrent.to_dict() | {"save_path": "/downloads"}
        mock_client_instance._session.get.return_value = create_mock_session_response(
            [info_payload], status_code=200
        )
        mock_client_class = MagicMock(return_value=mock_client_instance)

        with patch.dict("sys.modules", {"qbittorrentapi": MagicMock(Client=mock_client_class)}):
            import importlib

            import shelfmark.download.clients.qbittorrent as qb_module

            importlib.reload(qb_module)

            client = qb_module.QBittorrentClient()
            status = client.get_status("abc123")

            assert status.complete is True
            assert status.state_value == "complete"
            assert status.file_path == "/downloads/completed.epub"

    def test_get_status_complete_derives_when_content_path_equals_save_path(self, monkeypatch):
        """Keep get_status() and get_download_path() consistent."""
        config_values = {
            "QBITTORRENT_URL": "http://localhost:8080",
            "QBITTORRENT_USERNAME": "admin",
            "QBITTORRENT_PASSWORD": "password",
            "QBITTORRENT_CATEGORY": "test",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.qbittorrent.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        # content_path == save_path is treated as a path error
        mock_torrent = MockTorrent(
            hash_val="abc123",
            progress=1.0,
            state="uploading",
            content_path="/downloads",
            name="Some Torrent",
        )
        # Ensure the torrent info payload contains save_path too
        info_payload = mock_torrent.to_dict() | {"save_path": "/downloads"}

        def response(kind: str):
            r = MagicMock()
            r.status_code = 200
            r.raise_for_status = MagicMock()
            if kind == "info":
                r.json.return_value = [info_payload]
            elif kind == "properties":
                r.json.return_value = {"save_path": "/downloads"}
            elif kind == "files":
                r.json.return_value = [{"name": "Some Torrent/book.epub"}]
            else:
                raise AssertionError("unknown")
            return r

        mock_client_instance = MagicMock()

        def get_side_effect(url, params=None, timeout=None):
            if url.endswith("/api/v2/torrents/info"):
                return response("info")
            if url.endswith("/api/v2/torrents/properties"):
                return response("properties")
            if url.endswith("/api/v2/torrents/files"):
                return response("files")
            raise AssertionError(f"unexpected url: {url}")

        mock_client_instance._session.get.side_effect = get_side_effect
        mock_client_class = MagicMock(return_value=mock_client_instance)

        with patch.dict("sys.modules", {"qbittorrentapi": MagicMock(Client=mock_client_class)}):
            import importlib

            import shelfmark.download.clients.qbittorrent as qb_module

            importlib.reload(qb_module)

            client = qb_module.QBittorrentClient()
            status = client.get_status("abc123")

            assert status.complete is True
            assert status.file_path == "/downloads/Some Torrent"

    @pytest.mark.parametrize(
        "file_name",
        ["/escape/book.epub", "../escape/book.epub", "C:\\escape\\book.epub"],
    )
    def test_get_status_falls_back_after_unsafe_derived_file_path(self, monkeypatch, file_name):
        """Unsafe file-derived paths do not block the safe legacy fallback."""
        config_values = {
            "QBITTORRENT_URL": "http://localhost:8080",
            "QBITTORRENT_USERNAME": "admin",
            "QBITTORRENT_PASSWORD": "password",
            "QBITTORRENT_CATEGORY": "test",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.qbittorrent.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        mock_torrent = MockTorrent(
            hash_val="abc123",
            progress=1.0,
            state="uploading",
            content_path="/downloads",
            name="Some Torrent",
        )
        info_payload = mock_torrent.to_dict() | {"save_path": "/downloads"}

        def response(kind: str):
            r = MagicMock()
            r.status_code = 200
            r.raise_for_status = MagicMock()
            if kind == "info":
                r.json.return_value = [info_payload]
            elif kind == "properties":
                r.json.return_value = {"save_path": "/downloads"}
            elif kind == "files":
                r.json.return_value = [{"name": file_name}]
            else:
                raise AssertionError("unknown")
            return r

        mock_client_instance = MagicMock()

        def get_side_effect(url, params=None, timeout=None):
            if url.endswith("/api/v2/torrents/info"):
                return response("info")
            if url.endswith("/api/v2/torrents/properties"):
                return response("properties")
            if url.endswith("/api/v2/torrents/files"):
                return response("files")
            raise AssertionError(f"unexpected url: {url}")

        mock_client_instance._session.get.side_effect = get_side_effect
        mock_client_class = MagicMock(return_value=mock_client_instance)

        with patch.dict("sys.modules", {"qbittorrentapi": MagicMock(Client=mock_client_class)}):
            import importlib

            import shelfmark.download.clients.qbittorrent as qb_module

            importlib.reload(qb_module)

            client = qb_module.QBittorrentClient()
            status = client.get_status("abc123")
            path = client.get_download_path("abc123")

            assert status.complete is True
            assert status.file_path == "/downloads/Some Torrent"
            assert path == "/downloads/Some Torrent"

    @pytest.mark.parametrize("torrent_name", ["/escape", "../escape", "C:\\escape"])
    def test_get_status_rejects_unsafe_legacy_name_path(self, monkeypatch, torrent_name):
        """Legacy save_path/name fallback must reject absolute and traversal names."""
        config_values = {
            "QBITTORRENT_URL": "http://localhost:8080",
            "QBITTORRENT_USERNAME": "admin",
            "QBITTORRENT_PASSWORD": "password",
            "QBITTORRENT_CATEGORY": "test",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.qbittorrent.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        mock_torrent = MockTorrent(
            hash_val="abc123",
            progress=1.0,
            state="uploading",
            content_path="/downloads",
            name=torrent_name,
        )
        info_payload = mock_torrent.to_dict() | {"save_path": "/downloads"}
        mock_client_instance = MagicMock()
        mock_client_instance._session.get.side_effect = [
            create_mock_session_response([info_payload], status_code=200),
            create_mock_session_response([], status_code=404),
        ]
        mock_client_class = MagicMock(return_value=mock_client_instance)

        with patch.dict("sys.modules", {"qbittorrentapi": MagicMock(Client=mock_client_class)}):
            import importlib

            import shelfmark.download.clients.qbittorrent as qb_module

            importlib.reload(qb_module)

            client = qb_module.QBittorrentClient()
            status = client.get_status("abc123")

            assert status.complete is True
            assert status.file_path is None

    def test_get_status_accepts_safe_legacy_name_path(self, monkeypatch):
        """Normal relative torrent names still build under save_path."""
        config_values = {
            "QBITTORRENT_URL": "http://localhost:8080",
            "QBITTORRENT_USERNAME": "admin",
            "QBITTORRENT_PASSWORD": "password",
            "QBITTORRENT_CATEGORY": "test",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.qbittorrent.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        mock_torrent = MockTorrent(
            hash_val="abc123",
            progress=1.0,
            state="uploading",
            content_path="/downloads",
            name="Some Torrent",
        )
        info_payload = mock_torrent.to_dict() | {"save_path": "/downloads"}
        mock_client_instance = MagicMock()
        mock_client_instance._session.get.side_effect = [
            create_mock_session_response([info_payload], status_code=200),
            create_mock_session_response([], status_code=404),
        ]
        mock_client_class = MagicMock(return_value=mock_client_instance)

        with patch.dict("sys.modules", {"qbittorrentapi": MagicMock(Client=mock_client_class)}):
            import importlib

            import shelfmark.download.clients.qbittorrent as qb_module

            importlib.reload(qb_module)

            client = qb_module.QBittorrentClient()
            status = client.get_status("abc123")

            assert status.complete is True
            assert status.file_path == "/downloads/Some Torrent"

    def test_get_status_not_found(self, monkeypatch):
        """Test status for non-existent torrent."""
        config_values = {
            "QBITTORRENT_URL": "http://localhost:8080",
            "QBITTORRENT_USERNAME": "admin",
            "QBITTORRENT_PASSWORD": "password",
            "QBITTORRENT_CATEGORY": "test",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.qbittorrent.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        mock_client_instance = MagicMock()
        mock_client_instance._session.get.return_value = create_mock_session_response(
            [], status_code=200
        )
        mock_client_class = MagicMock(return_value=mock_client_instance)

        with patch.dict("sys.modules", {"qbittorrentapi": MagicMock(Client=mock_client_class)}):
            import importlib

            import shelfmark.download.clients.qbittorrent as qb_module

            importlib.reload(qb_module)

            client = qb_module.QBittorrentClient()
            status = client.get_status("nonexistent")

            assert status.state_value == "error"
            assert status.message is not None
            assert "not found" in status.message.lower()

    def test_get_status_rejects_unrelated_exact_result(self, monkeypatch):
        """Reject an unrelated record when the client ignores the hash filter."""
        config_values = {
            "QBITTORRENT_URL": "http://localhost:8080",
            "QBITTORRENT_USERNAME": "admin",
            "QBITTORRENT_PASSWORD": "password",
            "QBITTORRENT_CATEGORY": "test",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.qbittorrent.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        mock_client_instance = MagicMock()
        mock_client_instance._session.get.return_value = create_mock_session_response(
            [MockTorrent(hash_val="different")]
        )
        mock_client_class = MagicMock(return_value=mock_client_instance)

        with patch.dict("sys.modules", {"qbittorrentapi": MagicMock(Client=mock_client_class)}):
            import importlib

            import shelfmark.download.clients.qbittorrent as qb_module

            importlib.reload(qb_module)

            client = qb_module.QBittorrentClient()
            status = client.get_status("requested")

            assert status.state_value == "error"
            assert status.message is not None
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
            "shelfmark.download.clients.qbittorrent.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        mock_torrent = MockTorrent(progress=0.3, state="stalledDL")
        mock_client_instance = MagicMock()
        # Mock the session.get for _get_torrents_info
        mock_client_instance._session.get.return_value = create_mock_session_response(
            [mock_torrent], status_code=200
        )
        mock_client_class = MagicMock(return_value=mock_client_instance)

        with patch.dict("sys.modules", {"qbittorrentapi": MagicMock(Client=mock_client_class)}):
            import importlib

            import shelfmark.download.clients.qbittorrent as qb_module

            importlib.reload(qb_module)

            client = qb_module.QBittorrentClient()
            status = client.get_status("abc123")

            assert status.state_value == "downloading"
            assert status.message is not None
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
            "shelfmark.download.clients.qbittorrent.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        mock_torrent = MockTorrent(progress=0.5, state="pausedDL")
        mock_client_instance = MagicMock()
        # Mock the session.get for _get_torrents_info
        mock_client_instance._session.get.return_value = create_mock_session_response(
            [mock_torrent], status_code=200
        )
        mock_client_class = MagicMock(return_value=mock_client_instance)

        with patch.dict("sys.modules", {"qbittorrentapi": MagicMock(Client=mock_client_class)}):
            import importlib

            import shelfmark.download.clients.qbittorrent as qb_module

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
            "shelfmark.download.clients.qbittorrent.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        mock_torrent = MockTorrent(progress=0.1, state="error")
        mock_client_instance = MagicMock()
        # Mock the session.get for _get_torrents_info
        mock_client_instance._session.get.return_value = create_mock_session_response(
            [mock_torrent], status_code=200
        )
        mock_client_class = MagicMock(return_value=mock_client_instance)

        with patch.dict("sys.modules", {"qbittorrentapi": MagicMock(Client=mock_client_class)}):
            import importlib

            import shelfmark.download.clients.qbittorrent as qb_module

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
            "shelfmark.download.clients.qbittorrent.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        mock_torrent = MockTorrent(hash_val="3b245504cf5f11bbdbe1201cea6a6bf45aee1bc0")
        mock_client_instance = MagicMock()
        mock_client_instance.torrents_add.return_value = "Ok."
        mock_client_instance.torrents_info.return_value = [mock_torrent]
        mock_client_instance._session.get.return_value = create_mock_session_response(
            [mock_torrent], status_code=200
        )
        mock_client_class = MagicMock(return_value=mock_client_instance)

        with patch.dict("sys.modules", {"qbittorrentapi": MagicMock(Client=mock_client_class)}):
            import importlib

            import shelfmark.download.clients.qbittorrent as qb_module

            importlib.reload(qb_module)

            client = qb_module.QBittorrentClient()
            magnet = "magnet:?xt=urn:btih:3b245504cf5f11bbdbe1201cea6a6bf45aee1bc0&dn=test"
            result = client.add_download(magnet, "Test Download")

            assert result == "3b245504cf5f11bbdbe1201cea6a6bf45aee1bc0"
            assert mock_client_instance._session.get.call_count >= 1

    @pytest.mark.parametrize("metadata_state", ["metaDL", "forcedMetaDL"])
    def test_add_waits_for_metadata_and_returns_current_hash(self, monkeypatch, metadata_state):
        """Return qBittorrent's current hash after hybrid metadata resolves."""
        config_values = {
            "QBITTORRENT_URL": "http://localhost:8080",
            "QBITTORRENT_USERNAME": "admin",
            "QBITTORRENT_PASSWORD": "password",
            "QBITTORRENT_CATEGORY": "books",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.qbittorrent.config.get",
            lambda key, default="": config_values.get(key, default),
        )
        monkeypatch.setattr(
            "shelfmark.download.clients.qbittorrent.time.sleep", lambda _seconds: None
        )

        v1_hash = "edf46c7f938a3c678081734d7bff8b9c652ba5e5"
        v2_hash = "0bed5f40753b342cb143e83c2b21924cc8474731"
        full_v2_hash = "0bed5f40753b342cb143e83c2b21924cc847473134e44d1bd300bdc58c13010f"
        metadata_torrent = MockTorrent(
            hash_val=v1_hash,
            state=metadata_state,
            infohash_v1=v1_hash,
        )
        resolved_torrent = MockTorrent(
            hash_val=v2_hash,
            state="downloading",
            infohash_v1=v1_hash,
            infohash_v2=full_v2_hash,
        )
        mock_client_instance = MagicMock()
        mock_client_instance.torrents_add.return_value = "Ok."
        mock_client_instance._session.get.side_effect = [
            create_mock_session_response([metadata_torrent]),
            create_mock_session_response([]),
            create_mock_session_response([resolved_torrent]),
        ]
        mock_client_class = MagicMock(return_value=mock_client_instance)

        with patch.dict("sys.modules", {"qbittorrentapi": MagicMock(Client=mock_client_class)}):
            import importlib

            import shelfmark.download.clients.qbittorrent as qb_module

            importlib.reload(qb_module)

            client = qb_module.QBittorrentClient()
            magnet = f"magnet:?xt=urn:btih:{v1_hash}&dn=test"
            result = client.add_download(magnet, "Test Download", category="audiobooks")

            assert result == v2_hash
            assert [
                call.kwargs["params"] for call in mock_client_instance._session.get.call_args_list
            ] == [
                {"hashes": v1_hash},
                {"hashes": v1_hash},
                {"category": "audiobooks"},
            ]

    def test_add_keeps_torrent_when_metadata_never_resolves(self, monkeypatch):
        """Return the info hash rather than abandon a magnet whose metadata is slow."""
        config_values = {
            "QBITTORRENT_URL": "http://localhost:8080",
            "QBITTORRENT_USERNAME": "admin",
            "QBITTORRENT_PASSWORD": "password",
            "QBITTORRENT_CATEGORY": "books",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.qbittorrent.config.get",
            lambda key, default="": config_values.get(key, default),
        )
        monkeypatch.setattr(
            "shelfmark.download.clients.qbittorrent.time.sleep", lambda _seconds: None
        )

        v1_hash = "edf46c7f938a3c678081734d7bff8b9c652ba5e5"
        metadata_torrent = MockTorrent(
            hash_val=v1_hash,
            state="metaDL",
            infohash_v1=v1_hash,
        )
        mock_client_instance = MagicMock()
        mock_client_instance.torrents_add.return_value = "Ok."
        mock_client_instance._session.get.return_value = create_mock_session_response(
            [metadata_torrent]
        )
        mock_client_class = MagicMock(return_value=mock_client_instance)

        with patch.dict("sys.modules", {"qbittorrentapi": MagicMock(Client=mock_client_class)}):
            import importlib

            import shelfmark.download.clients.qbittorrent as qb_module

            importlib.reload(qb_module)

            client = qb_module.QBittorrentClient()
            magnet = f"magnet:?xt=urn:btih:{v1_hash}&dn=test"

            assert client.add_download(magnet, "Test Download") == v1_hash

    def test_get_status_resolves_hash_after_metadata_switch(self, monkeypatch):
        """Track a torrent by its v1 hash after qBittorrent re-keys it to v2."""
        config_values = {
            "QBITTORRENT_URL": "http://localhost:8080",
            "QBITTORRENT_USERNAME": "admin",
            "QBITTORRENT_PASSWORD": "password",
            "QBITTORRENT_CATEGORY": "books",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.qbittorrent.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        v1_hash = "edf46c7f938a3c678081734d7bff8b9c652ba5e5"
        v2_hash = "0bed5f40753b342cb143e83c2b21924cc8474731"
        full_v2_hash = "0bed5f40753b342cb143e83c2b21924cc847473134e44d1bd300bdc58c13010f"
        resolved_torrent = MockTorrent(
            hash_val=v2_hash,
            state="downloading",
            infohash_v1=v1_hash,
            infohash_v2=full_v2_hash,
        )
        mock_client_instance = MagicMock()
        mock_client_instance._session.get.side_effect = [
            create_mock_session_response([]),
            create_mock_session_response([resolved_torrent]),
        ]
        mock_client_class = MagicMock(return_value=mock_client_instance)

        with patch.dict("sys.modules", {"qbittorrentapi": MagicMock(Client=mock_client_class)}):
            import importlib

            import shelfmark.download.clients.qbittorrent as qb_module

            importlib.reload(qb_module)

            client = qb_module.QBittorrentClient()
            status = client.get_status(v1_hash)

            assert status.state.value == "downloading"

    def test_status_polls_reuse_resolved_hash_after_metadata_switch(self, monkeypatch):
        """Scan for the re-keyed hash once, then poll it directly."""
        config_values = {
            "QBITTORRENT_URL": "http://localhost:8080",
            "QBITTORRENT_USERNAME": "admin",
            "QBITTORRENT_PASSWORD": "password",
            "QBITTORRENT_CATEGORY": "books",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.qbittorrent.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        v1_hash = "edf46c7f938a3c678081734d7bff8b9c652ba5e5"
        v2_hash = "0bed5f40753b342cb143e83c2b21924cc8474731"
        full_v2_hash = "0bed5f40753b342cb143e83c2b21924cc847473134e44d1bd300bdc58c13010f"
        resolved_torrent = MockTorrent(
            hash_val=v2_hash,
            state="downloading",
            infohash_v1=v1_hash,
            infohash_v2=full_v2_hash,
        )
        mock_client_instance = MagicMock()
        mock_client_instance._session.get.side_effect = [
            create_mock_session_response([]),
            create_mock_session_response([resolved_torrent]),
            create_mock_session_response([resolved_torrent]),
        ]
        mock_client_class = MagicMock(return_value=mock_client_instance)

        with patch.dict("sys.modules", {"qbittorrentapi": MagicMock(Client=mock_client_class)}):
            import importlib

            import shelfmark.download.clients.qbittorrent as qb_module

            importlib.reload(qb_module)

            client = qb_module.QBittorrentClient()

            assert client.get_status(v1_hash).state_value == "downloading"
            assert client.get_status(v1_hash).state_value == "downloading"

            # The second poll goes straight to the hash the first one resolved,
            # rather than listing every torrent again.
            assert [
                call.kwargs["params"] for call in mock_client_instance._session.get.call_args_list
            ] == [
                {"hashes": v1_hash},
                {"category": "books"},
                {"hashes": v2_hash},
            ]

    def test_remove_forgets_resolved_hash(self, monkeypatch):
        """Drop the remembered hash on removal so a re-add is resolved afresh."""
        config_values = {
            "QBITTORRENT_URL": "http://localhost:8080",
            "QBITTORRENT_USERNAME": "admin",
            "QBITTORRENT_PASSWORD": "password",
            "QBITTORRENT_CATEGORY": "books",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.qbittorrent.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        v1_hash = "edf46c7f938a3c678081734d7bff8b9c652ba5e5"
        v2_hash = "0bed5f40753b342cb143e83c2b21924cc8474731"
        full_v2_hash = "0bed5f40753b342cb143e83c2b21924cc847473134e44d1bd300bdc58c13010f"
        resolved_torrent = MockTorrent(
            hash_val=v2_hash,
            state="downloading",
            infohash_v1=v1_hash,
            infohash_v2=full_v2_hash,
        )
        mock_client_instance = MagicMock()
        mock_client_instance._session.get.side_effect = [
            create_mock_session_response([]),
            create_mock_session_response([resolved_torrent]),
            create_mock_session_response([resolved_torrent]),
        ]
        mock_client_class = MagicMock(return_value=mock_client_instance)

        with patch.dict("sys.modules", {"qbittorrentapi": MagicMock(Client=mock_client_class)}):
            import importlib

            import shelfmark.download.clients.qbittorrent as qb_module

            importlib.reload(qb_module)

            client = qb_module.QBittorrentClient()
            client.get_status(v1_hash)

            assert client.remove(v1_hash) is True

            # The delete addressed the current primary hash, and the entry is gone.
            assert (
                mock_client_instance.torrents_delete.call_args.kwargs["torrent_hashes"] == v2_hash
            )
            assert client._primary_hashes == {}

    def test_add_download_uses_expected_hash_without_fetch(self, monkeypatch):
        """Skip proxy fetch when expected hash is provided for URL torrents."""
        config_values = {
            "QBITTORRENT_URL": "http://localhost:8080",
            "QBITTORRENT_USERNAME": "admin",
            "QBITTORRENT_PASSWORD": "password",
            "QBITTORRENT_CATEGORY": "test",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.qbittorrent.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        expected_hash = "3b245504cf5f11bbdbe1201cea6a6bf45aee1bc0"
        mock_torrent = MockTorrent(hash_val=expected_hash)
        mock_client_instance = MagicMock()
        mock_client_instance.torrents_add.return_value = "Ok."
        mock_client_instance.torrents_info.return_value = [mock_torrent]
        mock_client_instance._session.get.return_value = create_mock_session_response(
            [mock_torrent], status_code=200
        )
        mock_client_class = MagicMock(return_value=mock_client_instance)

        with patch.dict("sys.modules", {"qbittorrentapi": MagicMock(Client=mock_client_class)}):
            import importlib

            import shelfmark.download.clients.qbittorrent as qb_module

            importlib.reload(qb_module)

            with patch(
                "shelfmark.download.clients.qbittorrent.extract_torrent_info",
                autospec=True,
            ) as mock_extract:
                mock_extract.return_value = TorrentInfo(
                    info_hash=expected_hash,
                    torrent_data=None,
                    is_magnet=False,
                    magnet_url=None,
                )

                client = qb_module.QBittorrentClient()
                result = client.add_download(
                    "http://example.com/test.torrent",
                    "Test Download",
                    expected_hash=expected_hash,
                )

                assert result == expected_hash
                mock_extract.assert_called_once_with(
                    "http://example.com/test.torrent",
                    expected_hash=expected_hash,
                )

    def test_add_download_discovers_hash_when_extraction_fails(self, monkeypatch):
        """Regression for #1012: a URL add without a hash adopts the new torrent's hash.

        qBittorrent fetches .torrent URLs itself, so the add succeeds even when
        the prefetch could not determine the info_hash; the client must recover
        the hash from the torrent that appears instead of raising.
        """
        config_values = {
            "QBITTORRENT_URL": "http://localhost:8080",
            "QBITTORRENT_USERNAME": "admin",
            "QBITTORRENT_PASSWORD": "password",
            "QBITTORRENT_CATEGORY": "books",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.qbittorrent.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        discovered_hash = "3b245504cf5f11bbdbe1201cea6a6bf45aee1bc0"
        existing = MockTorrent(
            hash_val="a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2", name="Existing Torrent"
        )
        added = MockTorrent(hash_val=discovered_hash, name="Test Download")

        mock_client_instance = MagicMock()
        mock_client_instance.torrents_add.return_value = "Ok."

        torrents_before_add = create_mock_session_response([existing])
        torrents_after_add = create_mock_session_response([existing, added])

        def session_get(request_url, params=None, timeout=None):
            if params == {"hashes": discovered_hash}:
                return create_mock_session_response([added])
            if mock_client_instance.torrents_add.called:
                return torrents_after_add
            return torrents_before_add

        mock_client_instance._session.get.side_effect = session_get
        mock_client_class = MagicMock(return_value=mock_client_instance)

        with patch.dict("sys.modules", {"qbittorrentapi": MagicMock(Client=mock_client_class)}):
            import importlib

            import shelfmark.download.clients.qbittorrent as qb_module

            importlib.reload(qb_module)

            with patch(
                "shelfmark.download.clients.qbittorrent.extract_torrent_info",
                autospec=True,
            ) as mock_extract:
                mock_extract.return_value = TorrentInfo(
                    info_hash=None,
                    torrent_data=None,
                    is_magnet=False,
                    magnet_url=None,
                )

                client = qb_module.QBittorrentClient()
                result = client.add_download(
                    "http://tracker.example/download/book.torrent", "Test Download"
                )

            assert result == discovered_hash
            add_kwargs = mock_client_instance.torrents_add.call_args.kwargs
            assert add_kwargs["urls"] == "http://tracker.example/download/book.torrent"

    def test_add_download_raises_when_hash_never_discovered(self, monkeypatch):
        """Keep failing loudly when no hash is known and no new torrent appears."""
        config_values = {
            "QBITTORRENT_URL": "http://localhost:8080",
            "QBITTORRENT_USERNAME": "admin",
            "QBITTORRENT_PASSWORD": "password",
            "QBITTORRENT_CATEGORY": "books",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.qbittorrent.config.get",
            lambda key, default="": config_values.get(key, default),
        )
        monkeypatch.setattr(
            "shelfmark.download.clients.qbittorrent.time.sleep", lambda _seconds: None
        )

        mock_client_instance = MagicMock()
        mock_client_instance.torrents_add.return_value = "Ok."
        mock_client_instance._session.get.return_value = create_mock_session_response([])
        mock_client_class = MagicMock(return_value=mock_client_instance)

        with patch.dict("sys.modules", {"qbittorrentapi": MagicMock(Client=mock_client_class)}):
            import importlib

            import shelfmark.download.clients.qbittorrent as qb_module

            importlib.reload(qb_module)

            with patch(
                "shelfmark.download.clients.qbittorrent.extract_torrent_info",
                autospec=True,
            ) as mock_extract:
                mock_extract.return_value = TorrentInfo(
                    info_hash=None,
                    torrent_data=None,
                    is_magnet=False,
                    magnet_url=None,
                )

                client = qb_module.QBittorrentClient()
                with pytest.raises(RuntimeError, match="Could not determine torrent hash"):
                    client.add_download(
                        "http://tracker.example/download/book.torrent", "Test Download"
                    )

    def test_add_download_error_includes_fetch_failure_reason(self, monkeypatch):
        """Surface why the .torrent prefetch failed instead of only the hash error.

        Regression for #1111: a Prowlarr proxy fetch that fails (e.g. HTTP 500
        because the tracker rejected the request) was reported as a bare
        "Could not determine torrent hash from URL", hiding the actual cause.
        """
        config_values = {
            "QBITTORRENT_URL": "http://localhost:8080",
            "QBITTORRENT_USERNAME": "admin",
            "QBITTORRENT_PASSWORD": "password",
            "QBITTORRENT_CATEGORY": "books",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.qbittorrent.config.get",
            lambda key, default="": config_values.get(key, default),
        )
        monkeypatch.setattr(
            "shelfmark.download.clients.qbittorrent.time.sleep", lambda _seconds: None
        )

        mock_client_instance = MagicMock()
        mock_client_instance.torrents_add.return_value = "Ok."
        mock_client_instance._session.get.return_value = create_mock_session_response([])
        mock_client_class = MagicMock(return_value=mock_client_instance)

        with patch.dict("sys.modules", {"qbittorrentapi": MagicMock(Client=mock_client_class)}):
            import importlib

            import shelfmark.download.clients.qbittorrent as qb_module

            importlib.reload(qb_module)

            with patch(
                "shelfmark.download.clients.qbittorrent.extract_torrent_info",
                autospec=True,
            ) as mock_extract:
                mock_extract.return_value = TorrentInfo(
                    info_hash=None,
                    torrent_data=None,
                    is_magnet=False,
                    magnet_url=None,
                    fetch_error="500 Server Error: Internal Server Error for url: http://prowlarr:9696/26/download",
                )

                client = qb_module.QBittorrentClient()
                with pytest.raises(RuntimeError) as exc_info:
                    client.add_download(
                        "http://prowlarr:9696/26/download?apikey=key&link=token",
                        "Test Download",
                    )

                assert "Could not determine torrent hash from URL" in str(exc_info.value)
                assert "500 Server Error" in str(exc_info.value)

    def test_add_download_creates_category(self, monkeypatch):
        """Test that add_download creates category if needed."""
        config_values = {
            "QBITTORRENT_URL": "http://localhost:8080",
            "QBITTORRENT_USERNAME": "admin",
            "QBITTORRENT_PASSWORD": "password",
            "QBITTORRENT_CATEGORY": "books",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.qbittorrent.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        # Use a valid 40-character hex hash
        valid_hash = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"
        mock_torrent = MockTorrent(hash_val=valid_hash)
        mock_client_instance = MagicMock()
        mock_client_instance.torrents_add.return_value = "Ok."
        mock_client_instance.torrents_info.return_value = [mock_torrent]
        mock_client_instance._session.get.return_value = create_mock_session_response(
            [mock_torrent], status_code=200
        )
        mock_client_class = MagicMock(return_value=mock_client_instance)

        with patch.dict("sys.modules", {"qbittorrentapi": MagicMock(Client=mock_client_class)}):
            import importlib

            import shelfmark.download.clients.qbittorrent as qb_module

            importlib.reload(qb_module)

            client = qb_module.QBittorrentClient()
            magnet = f"magnet:?xt=urn:btih:{valid_hash}&dn=test"
            client.add_download(magnet, "Test")

            mock_client_instance.torrents_create_category.assert_called_once_with(name="books")

    def test_add_download_accepts_empty_success_response(self, monkeypatch):
        """qBittorrent-compatible shims may return HTTP 200 with an empty body."""
        config_values = {
            "QBITTORRENT_URL": "http://localhost:8080",
            "QBITTORRENT_USERNAME": "admin",
            "QBITTORRENT_PASSWORD": "password",
            "QBITTORRENT_CATEGORY": "books",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.qbittorrent.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        valid_hash = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"
        mock_torrent = MockTorrent(hash_val=valid_hash)
        mock_client_instance = MagicMock()
        mock_client_instance.torrents_add.return_value = ""
        mock_client_instance._session.get.return_value = create_mock_session_response(
            [mock_torrent], status_code=200
        )
        mock_client_class = MagicMock(return_value=mock_client_instance)

        with patch.dict("sys.modules", {"qbittorrentapi": MagicMock(Client=mock_client_class)}):
            import importlib

            import shelfmark.download.clients.qbittorrent as qb_module

            importlib.reload(qb_module)

            client = qb_module.QBittorrentClient()
            magnet = f"magnet:?xt=urn:btih:{valid_hash}&dn=test"
            result = client.add_download(magnet, "Test")

            assert result == valid_hash
            assert mock_client_instance._session.get.call_count >= 1

    def test_add_download_explicit_failure_response_raises(self, monkeypatch):
        """Explicit failure responses should still fail fast."""
        config_values = {
            "QBITTORRENT_URL": "http://localhost:8080",
            "QBITTORRENT_USERNAME": "admin",
            "QBITTORRENT_PASSWORD": "password",
            "QBITTORRENT_CATEGORY": "books",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.qbittorrent.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        valid_hash = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"
        mock_client_instance = MagicMock()
        mock_client_instance.torrents_add.return_value = "Fails."
        mock_client_class = MagicMock(return_value=mock_client_instance)

        with patch.dict("sys.modules", {"qbittorrentapi": MagicMock(Client=mock_client_class)}):
            import importlib

            import shelfmark.download.clients.qbittorrent as qb_module

            importlib.reload(qb_module)

            client = qb_module.QBittorrentClient()
            magnet = f"magnet:?xt=urn:btih:{valid_hash}&dn=test"

            with pytest.raises(RuntimeError, match="Failed to add torrent: Fails\\."):
                client.add_download(magnet, "Test")

    def test_add_download_omits_empty_category(self, monkeypatch):
        """Avoid sending empty category values to qBittorrent-compatible clients."""
        config_values = {
            "QBITTORRENT_URL": "http://localhost:8080",
            "QBITTORRENT_USERNAME": "admin",
            "QBITTORRENT_PASSWORD": "password",
            "QBITTORRENT_CATEGORY": "",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.qbittorrent.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        valid_hash = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"
        mock_torrent = MockTorrent(hash_val=valid_hash)
        mock_client_instance = MagicMock()
        mock_client_instance.torrents_add.return_value = "Ok."
        mock_client_instance._session.get.return_value = create_mock_session_response(
            [mock_torrent], status_code=200
        )
        mock_client_class = MagicMock(return_value=mock_client_instance)

        with patch.dict("sys.modules", {"qbittorrentapi": MagicMock(Client=mock_client_class)}):
            import importlib

            import shelfmark.download.clients.qbittorrent as qb_module

            importlib.reload(qb_module)

            client = qb_module.QBittorrentClient()
            magnet = f"magnet:?xt=urn:btih:{valid_hash}&dn=test"
            client.add_download(magnet, "Test")

            mock_client_instance.torrents_create_category.assert_not_called()
            call_kwargs = mock_client_instance.torrents_add.call_args.kwargs
            assert "category" not in call_kwargs

    def test_add_download_uses_configured_download_dir(self, monkeypatch):
        """Test that add_download passes configured download directory."""
        config_values = {
            "QBITTORRENT_URL": "http://localhost:8080",
            "QBITTORRENT_USERNAME": "admin",
            "QBITTORRENT_PASSWORD": "password",
            "QBITTORRENT_CATEGORY": "books",
            "QBITTORRENT_DOWNLOAD_DIR": "/downloads/books",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.qbittorrent.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        valid_hash = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"
        mock_torrent = MockTorrent(hash_val=valid_hash)
        mock_client_instance = MagicMock()
        mock_client_instance.torrents_add.return_value = "Ok."
        mock_client_instance._session.get.return_value = create_mock_session_response(
            [mock_torrent], status_code=200
        )
        mock_client_class = MagicMock(return_value=mock_client_instance)

        with patch.dict("sys.modules", {"qbittorrentapi": MagicMock(Client=mock_client_class)}):
            import importlib

            import shelfmark.download.clients.qbittorrent as qb_module

            importlib.reload(qb_module)

            with patch(
                "shelfmark.download.clients.qbittorrent.extract_torrent_info",
                autospec=True,
            ) as mock_extract:
                mock_extract.return_value = TorrentInfo(
                    info_hash=valid_hash,
                    torrent_data=None,
                    is_magnet=True,
                    magnet_url=f"magnet:?xt=urn:btih:{valid_hash}&dn=test",
                )

                client = qb_module.QBittorrentClient()
                client.add_download("magnet:?xt=urn:btih:test&dn=test", "Test")

                call_kwargs = mock_client_instance.torrents_add.call_args.kwargs
                assert call_kwargs.get("save_path") == "/downloads/books"


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
            "shelfmark.download.clients.qbittorrent.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        mock_client_instance = MagicMock()
        mock_client_class = MagicMock(return_value=mock_client_instance)

        with patch.dict("sys.modules", {"qbittorrentapi": MagicMock(Client=mock_client_class)}):
            import importlib

            import shelfmark.download.clients.qbittorrent as qb_module

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
            "shelfmark.download.clients.qbittorrent.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        mock_client_instance = MagicMock()
        mock_client_instance.torrents_delete.side_effect = RuntimeError("Not found")
        mock_client_class = MagicMock(return_value=mock_client_instance)

        with patch.dict("sys.modules", {"qbittorrentapi": MagicMock(Client=mock_client_class)}):
            import importlib

            import shelfmark.download.clients.qbittorrent as qb_module

            importlib.reload(qb_module)

            client = qb_module.QBittorrentClient()
            result = client.remove("abc123")

            assert result is False


class TestQBittorrentClientGetDownloadPath:
    """Tests for QBittorrentClient.get_download_path()."""

    def test_get_download_path_prefers_content_path(self, monkeypatch):
        config_values = {
            "QBITTORRENT_URL": "http://localhost:8080",
            "QBITTORRENT_USERNAME": "admin",
            "QBITTORRENT_PASSWORD": "password",
            "QBITTORRENT_CATEGORY": "test",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.qbittorrent.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        mock_torrent = MockTorrent(
            hash_val="abc123",
            content_path="/downloads/some/book.epub",
        )
        mock_client_instance = MagicMock()
        mock_client_instance._session.get.return_value = create_mock_session_response(
            [mock_torrent], status_code=200
        )
        mock_client_class = MagicMock(return_value=mock_client_instance)

        with patch.dict("sys.modules", {"qbittorrentapi": MagicMock(Client=mock_client_class)}):
            import importlib

            import shelfmark.download.clients.qbittorrent as qb_module

            importlib.reload(qb_module)

            client = qb_module.QBittorrentClient()
            path = client.get_download_path("abc123")

            assert path == "/downloads/some/book.epub"

    def test_get_download_path_returns_content_path_when_complete(self, monkeypatch):
        """Completed torrents return content_path as-is, preserving subdirectories."""
        config_values = {
            "QBITTORRENT_URL": "http://localhost:8080",
            "QBITTORRENT_USERNAME": "admin",
            "QBITTORRENT_PASSWORD": "password",
            "QBITTORRENT_CATEGORY": "test",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.qbittorrent.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        mock_torrent = MockTorrent(
            hash_val="abc123",
            progress=1.0,
            state="uploading",
            content_path="/downloads/shelfmark/BookFolder/book.epub",
        )

        mock_client_instance = MagicMock()
        info_payload = mock_torrent.to_dict() | {"save_path": "/downloads/shelfmark"}
        mock_client_instance._session.get.return_value = create_mock_session_response(
            [info_payload], status_code=200
        )
        mock_client_class = MagicMock(return_value=mock_client_instance)

        with patch.dict("sys.modules", {"qbittorrentapi": MagicMock(Client=mock_client_class)}):
            import importlib

            import shelfmark.download.clients.qbittorrent as qb_module

            importlib.reload(qb_module)

            client = qb_module.QBittorrentClient()
            path = client.get_download_path("abc123")

            assert path == "/downloads/shelfmark/BookFolder/book.epub"

    def test_get_download_path_does_not_accept_content_path_equal_save_path(self, monkeypatch):
        """content_path == save_path indicates a path error."""
        config_values = {
            "QBITTORRENT_URL": "http://localhost:8080",
            "QBITTORRENT_USERNAME": "admin",
            "QBITTORRENT_PASSWORD": "password",
            "QBITTORRENT_CATEGORY": "test",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.qbittorrent.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        mock_torrent = MockTorrent(
            hash_val="abc123",
            content_path="/downloads",
        )
        # emulate qbit reporting save_path too
        setattr(mock_torrent, "save_path", "/downloads")

        def response(kind: str):
            r = MagicMock()
            r.status_code = 200
            r.raise_for_status = MagicMock()
            if kind == "info":
                r.json.return_value = [mock_torrent.to_dict() | {"save_path": "/downloads"}]
            elif kind == "properties":
                r.json.return_value = {"save_path": "/downloads"}
            elif kind == "files":
                r.json.return_value = [{"name": "Some Torrent/book.epub"}]
            else:
                raise AssertionError("unknown")
            return r

        mock_client_instance = MagicMock()

        def get_side_effect(url, params=None, timeout=None):
            if url.endswith("/api/v2/torrents/info"):
                return response("info")
            if url.endswith("/api/v2/torrents/properties"):
                return response("properties")
            if url.endswith("/api/v2/torrents/files"):
                return response("files")
            raise AssertionError(f"unexpected url: {url}")

        mock_client_instance._session.get.side_effect = get_side_effect
        mock_client_class = MagicMock(return_value=mock_client_instance)

        with patch.dict("sys.modules", {"qbittorrentapi": MagicMock(Client=mock_client_class)}):
            import importlib

            import shelfmark.download.clients.qbittorrent as qb_module

            importlib.reload(qb_module)

            client = qb_module.QBittorrentClient()
            path = client.get_download_path("abc123")

            assert path == "/downloads/Some Torrent"

    def test_get_download_path_derives_from_files_when_missing_content_path(self, monkeypatch):
        config_values = {
            "QBITTORRENT_URL": "http://localhost:8080",
            "QBITTORRENT_USERNAME": "admin",
            "QBITTORRENT_PASSWORD": "password",
            "QBITTORRENT_CATEGORY": "test",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.qbittorrent.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        # Simulate emulator: no content_path, but we can derive from properties+files
        mock_torrent = MockTorrent(
            hash_val="abc123",
            content_path="",
            name="Some Torrent",
        )

        def json_for(response_kind: str):
            if response_kind == "info":
                return [mock_torrent.to_dict()]
            if response_kind == "properties":
                return {"save_path": "/downloads"}
            if response_kind == "files":
                return [{"name": "Some Torrent/book.epub"}]
            raise AssertionError("unknown")

        def response(kind: str):
            r = MagicMock()
            r.status_code = 200
            r.raise_for_status = MagicMock()
            r.json.return_value = json_for(kind)
            return r

        mock_client_instance = MagicMock()

        def get_side_effect(url, params=None, timeout=None):
            if url.endswith("/api/v2/torrents/info"):
                return response("info")
            if url.endswith("/api/v2/torrents/properties"):
                return response("properties")
            if url.endswith("/api/v2/torrents/files"):
                return response("files")
            raise AssertionError(f"unexpected url: {url}")

        mock_client_instance._session.get.side_effect = get_side_effect
        mock_client_class = MagicMock(return_value=mock_client_instance)

        with patch.dict("sys.modules", {"qbittorrentapi": MagicMock(Client=mock_client_class)}):
            import importlib

            import shelfmark.download.clients.qbittorrent as qb_module

            importlib.reload(qb_module)

            client = qb_module.QBittorrentClient()
            path = client.get_download_path("abc123")

            assert path == "/downloads/Some Torrent"


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
            "shelfmark.download.clients.qbittorrent.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        mock_torrent = MockTorrent(
            hash_val="3b245504cf5f11bbdbe1201cea6a6bf45aee1bc0",
            progress=0.5,
            state="downloading",
        )
        mock_client_instance = MagicMock()
        # Mock the session.get for _get_torrents_info
        mock_client_instance._session.get.return_value = create_mock_session_response(
            [mock_torrent], status_code=200
        )
        mock_client_class = MagicMock(return_value=mock_client_instance)

        with patch.dict("sys.modules", {"qbittorrentapi": MagicMock(Client=mock_client_class)}):
            import importlib

            import shelfmark.download.clients.qbittorrent as qb_module

            importlib.reload(qb_module)

            client = qb_module.QBittorrentClient()
            magnet = "magnet:?xt=urn:btih:3b245504cf5f11bbdbe1201cea6a6bf45aee1bc0&dn=test"
            result = client.find_existing(magnet)

            assert result is not None
            download_id, status = result
            assert download_id == "3b245504cf5f11bbdbe1201cea6a6bf45aee1bc0"
            assert isinstance(status, DownloadStatus)

    def test_find_existing_waits_for_metadata(self, monkeypatch):
        """Wait for an existing hybrid torrent to expose its current primary hash."""
        config_values = {
            "QBITTORRENT_URL": "http://localhost:8080",
            "QBITTORRENT_USERNAME": "admin",
            "QBITTORRENT_PASSWORD": "password",
            "QBITTORRENT_CATEGORY": "books",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.qbittorrent.config.get",
            lambda key, default="": config_values.get(key, default),
        )
        monkeypatch.setattr(
            "shelfmark.download.clients.qbittorrent.time.sleep", lambda _seconds: None
        )

        v1_hash = "edf46c7f938a3c678081734d7bff8b9c652ba5e5"
        v2_hash = "0bed5f40753b342cb143e83c2b21924cc8474731"
        full_v2_hash = "0bed5f40753b342cb143e83c2b21924cc847473134e44d1bd300bdc58c13010f"
        metadata_torrent = MockTorrent(
            hash_val=v1_hash,
            state="metaDL",
            infohash_v1=v1_hash,
        )
        unrelated_torrent = MockTorrent(hash_val="a" * 40)
        resolved_torrent = MockTorrent(
            hash_val=v2_hash,
            state="downloading",
            infohash_v1=v1_hash,
            infohash_v2=full_v2_hash,
        )
        mock_client_instance = MagicMock()
        mock_client_instance._session.get.side_effect = [
            create_mock_session_response([metadata_torrent]),
            create_mock_session_response([]),
            create_mock_session_response([unrelated_torrent]),
            create_mock_session_response([unrelated_torrent]),
            create_mock_session_response([resolved_torrent]),
            create_mock_session_response([resolved_torrent]),
        ]
        mock_client_class = MagicMock(return_value=mock_client_instance)

        with patch.dict("sys.modules", {"qbittorrentapi": MagicMock(Client=mock_client_class)}):
            import importlib

            import shelfmark.download.clients.qbittorrent as qb_module

            importlib.reload(qb_module)

            client = qb_module.QBittorrentClient()
            magnet = f"magnet:?xt=urn:btih:{v1_hash}&dn=test"
            result = client.find_existing(magnet, category="audiobooks")

            assert result is not None
            download_id, status = result
            assert download_id == v2_hash
            assert status.state_value == "downloading"
            assert [
                call.kwargs["params"] for call in mock_client_instance._session.get.call_args_list
            ] == [
                {"hashes": v1_hash},
                {"hashes": v1_hash},
                {"category": "audiobooks"},
                {"category": "books"},
                {},
                {"hashes": v2_hash},
            ]

    def test_find_existing_keeps_torrent_whose_metadata_is_pending(self, monkeypatch):
        """Join a magnet still fetching metadata instead of adding a duplicate."""
        config_values = {
            "QBITTORRENT_URL": "http://localhost:8080",
            "QBITTORRENT_USERNAME": "admin",
            "QBITTORRENT_PASSWORD": "password",
            "QBITTORRENT_CATEGORY": "books",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.qbittorrent.config.get",
            lambda key, default="": config_values.get(key, default),
        )
        monkeypatch.setattr(
            "shelfmark.download.clients.qbittorrent.time.sleep", lambda _seconds: None
        )

        v1_hash = "edf46c7f938a3c678081734d7bff8b9c652ba5e5"
        metadata_torrent = MockTorrent(
            hash_val=v1_hash,
            state="metaDL",
            infohash_v1=v1_hash,
        )
        mock_client_instance = MagicMock()
        mock_client_instance._session.get.return_value = create_mock_session_response(
            [metadata_torrent]
        )
        mock_client_class = MagicMock(return_value=mock_client_instance)

        with patch.dict("sys.modules", {"qbittorrentapi": MagicMock(Client=mock_client_class)}):
            import importlib

            import shelfmark.download.clients.qbittorrent as qb_module

            importlib.reload(qb_module)

            client = qb_module.QBittorrentClient()
            magnet = f"magnet:?xt=urn:btih:{v1_hash}&dn=test"
            result = client.find_existing(magnet)

            assert result is not None
            download_id, status = result
            assert download_id == v1_hash
            assert status.state_value == "downloading"
            assert status.message == "Fetching metadata"

    def test_find_existing_not_found(self, monkeypatch):
        """Test finding non-existent torrent."""
        config_values = {
            "QBITTORRENT_URL": "http://localhost:8080",
            "QBITTORRENT_USERNAME": "admin",
            "QBITTORRENT_PASSWORD": "password",
            "QBITTORRENT_CATEGORY": "test",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.qbittorrent.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        mock_client_instance = MagicMock()
        # First call: hashes query returns empty. Second call (category listing) also empty.
        mock_client_instance._session.get.side_effect = [
            create_mock_session_response([], status_code=200),
            create_mock_session_response([], status_code=200),
            create_mock_session_response([], status_code=200),
        ]
        mock_client_class = MagicMock(return_value=mock_client_instance)

        with patch.dict("sys.modules", {"qbittorrentapi": MagicMock(Client=mock_client_class)}):
            import importlib

            import shelfmark.download.clients.qbittorrent as qb_module

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
            "shelfmark.download.clients.qbittorrent.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        mock_client_instance = MagicMock()
        mock_client_class = MagicMock(return_value=mock_client_instance)

        with patch.dict("sys.modules", {"qbittorrentapi": MagicMock(Client=mock_client_class)}):
            import importlib

            import shelfmark.download.clients.qbittorrent as qb_module

            importlib.reload(qb_module)

            client = qb_module.QBittorrentClient()
            result = client.find_existing("not-a-magnet-link")

            assert result is None


class TestHashesMatch:
    """Tests for _hashes_match() - Amarr compatibility."""

    def test_identical_hashes_match(self):
        from shelfmark.download.clients.qbittorrent import _hashes_match

        assert _hashes_match("abc123", "abc123") is True
        assert _hashes_match("ABC123", "abc123") is True

    def test_different_hashes_dont_match(self):
        from shelfmark.download.clients.qbittorrent import _hashes_match

        assert _hashes_match("abc123", "def456") is False

    def test_amarr_padded_hash_matches_ed2k_hash(self):
        from shelfmark.download.clients.qbittorrent import _hashes_match

        ed2k_hash = "0320c47b3baa01f8d5f42cd7c05ce28d"  # 32 chars
        padded_hash = "0320c47b3baa01f8d5f42cd7c05ce28d00000000"  # 40 chars
        assert _hashes_match(padded_hash, ed2k_hash) is True
        assert _hashes_match(ed2k_hash, padded_hash) is True

    def test_non_zero_padded_40_char_hash_doesnt_match(self):
        from shelfmark.download.clients.qbittorrent import _hashes_match

        bittorrent_hash = "3b245504cf5f11bbdbe1201cea6a6bf45aee1bc0"
        partial_hash = "3b245504cf5f11bbdbe1201cea6a6bf4"
        assert _hashes_match(bittorrent_hash, partial_hash) is False

    def test_matching_is_case_insensitive(self):
        from shelfmark.download.clients.qbittorrent import _hashes_match

        ed2k_hash = "0320C47B3BAA01F8D5F42CD7C05CE28D"
        padded_hash = "0320c47b3baa01f8d5f42cd7c05ce28d00000000"
        assert _hashes_match(padded_hash, ed2k_hash) is True

    def test_wrong_length_hashes_dont_match(self):
        from shelfmark.download.clients.qbittorrent import _hashes_match

        assert _hashes_match("a" * 40, "b" * 30) is False
        assert _hashes_match("a" * 38, "b" * 32) is False


class TestTorrentMatchesId:
    """Tests for matching qBittorrent's available torrent identities."""

    @pytest.mark.parametrize(
        ("torrent", "download_id"),
        [
            (MockTorrent(hash_val="current"), "current"),
            (MockTorrent(hash_val="other", infohash_v1="v1"), "v1"),
            (MockTorrent(hash_val="other", infohash_v2="v2"), "v2"),
        ],
    )
    def test_matches_each_identity(self, torrent, download_id):
        """Match IDs exposed through hash, infohash_v1, or infohash_v2."""
        from shelfmark.download.clients.qbittorrent import _torrent_matches_download_id

        assert _torrent_matches_download_id(torrent, download_id) is True

    def test_rejects_unrelated_or_missing_identities(self):
        """Reject unrelated IDs and records without identity fields."""
        from shelfmark.download.clients.qbittorrent import _torrent_matches_download_id

        assert _torrent_matches_download_id(MockTorrent(hash_val="different"), "requested") is False
        assert _torrent_matches_download_id(object(), "requested") is False
