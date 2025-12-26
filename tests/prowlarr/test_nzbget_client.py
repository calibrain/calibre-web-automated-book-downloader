"""
Unit tests for the NZBGet client.

These tests mock the requests library to test the client logic
without requiring a running NZBGet instance.
"""

from unittest.mock import MagicMock, patch
import pytest

from cwa_book_downloader.release_sources.prowlarr.clients import DownloadStatus


class TestNZBGetClientIsConfigured:
    """Tests for NZBGetClient.is_configured()."""

    def test_is_configured_when_all_set(self, monkeypatch):
        """Test is_configured returns True when properly configured."""
        config_values = {
            "PROWLARR_USENET_CLIENT": "nzbget",
            "NZBGET_URL": "http://localhost:6789",
        }
        monkeypatch.setattr(
            "cwa_book_downloader.release_sources.prowlarr.clients.nzbget.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        from cwa_book_downloader.release_sources.prowlarr.clients.nzbget import (
            NZBGetClient,
        )

        assert NZBGetClient.is_configured() is True

    def test_is_configured_wrong_client(self, monkeypatch):
        """Test is_configured returns False when different client selected."""
        config_values = {
            "PROWLARR_USENET_CLIENT": "sabnzbd",
            "NZBGET_URL": "http://localhost:6789",
        }
        monkeypatch.setattr(
            "cwa_book_downloader.release_sources.prowlarr.clients.nzbget.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        from cwa_book_downloader.release_sources.prowlarr.clients.nzbget import (
            NZBGetClient,
        )

        assert NZBGetClient.is_configured() is False

    def test_is_configured_no_url(self, monkeypatch):
        """Test is_configured returns False when URL not set."""
        config_values = {
            "PROWLARR_USENET_CLIENT": "nzbget",
            "NZBGET_URL": "",
        }
        monkeypatch.setattr(
            "cwa_book_downloader.release_sources.prowlarr.clients.nzbget.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        from cwa_book_downloader.release_sources.prowlarr.clients.nzbget import (
            NZBGetClient,
        )

        assert NZBGetClient.is_configured() is False


class TestNZBGetClientTestConnection:
    """Tests for NZBGetClient.test_connection()."""

    def test_test_connection_success(self, monkeypatch):
        """Test successful connection."""
        config_values = {
            "NZBGET_URL": "http://localhost:6789",
            "NZBGET_USERNAME": "nzbget",
            "NZBGET_PASSWORD": "tegbzn6789",
            "NZBGET_CATEGORY": "Books",
        }
        monkeypatch.setattr(
            "cwa_book_downloader.release_sources.prowlarr.clients.nzbget.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {"result": {"Version": "21.1"}}

        with patch(
            "cwa_book_downloader.release_sources.prowlarr.clients.nzbget.requests.post",
            return_value=mock_response,
        ):
            from cwa_book_downloader.release_sources.prowlarr.clients.nzbget import (
                NZBGetClient,
            )

            client = NZBGetClient()
            success, message = client.test_connection()

            assert success is True
            assert "21.1" in message

    def test_test_connection_failure(self, monkeypatch):
        """Test failed connection."""
        import requests

        config_values = {
            "NZBGET_URL": "http://localhost:6789",
            "NZBGET_USERNAME": "nzbget",
            "NZBGET_PASSWORD": "wrong",
            "NZBGET_CATEGORY": "Books",
        }
        monkeypatch.setattr(
            "cwa_book_downloader.release_sources.prowlarr.clients.nzbget.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        with patch(
            "cwa_book_downloader.release_sources.prowlarr.clients.nzbget.requests.post",
            side_effect=requests.exceptions.ConnectionError("Connection refused"),
        ):
            from cwa_book_downloader.release_sources.prowlarr.clients.nzbget import (
                NZBGetClient,
            )

            client = NZBGetClient()
            success, message = client.test_connection()

            assert success is False
            assert "connect" in message.lower()

    def test_test_connection_timeout(self, monkeypatch):
        """Test connection timeout."""
        import requests

        config_values = {
            "NZBGET_URL": "http://localhost:6789",
            "NZBGET_USERNAME": "nzbget",
            "NZBGET_PASSWORD": "password",
            "NZBGET_CATEGORY": "Books",
        }
        monkeypatch.setattr(
            "cwa_book_downloader.release_sources.prowlarr.clients.nzbget.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        with patch(
            "cwa_book_downloader.release_sources.prowlarr.clients.nzbget.requests.post",
            side_effect=requests.exceptions.Timeout("Timeout"),
        ):
            from cwa_book_downloader.release_sources.prowlarr.clients.nzbget import (
                NZBGetClient,
            )

            client = NZBGetClient()
            success, message = client.test_connection()

            assert success is False
            assert "timed" in message.lower()  # "Connection timed out"


class TestNZBGetClientRPCCall:
    """Tests for NZBGetClient._rpc_call()."""

    def test_rpc_call_success(self, monkeypatch):
        """Test successful RPC call."""
        config_values = {
            "NZBGET_URL": "http://localhost:6789",
            "NZBGET_USERNAME": "nzbget",
            "NZBGET_PASSWORD": "password",
            "NZBGET_CATEGORY": "Books",
        }
        monkeypatch.setattr(
            "cwa_book_downloader.release_sources.prowlarr.clients.nzbget.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {"result": "test_result"}

        with patch(
            "cwa_book_downloader.release_sources.prowlarr.clients.nzbget.requests.post",
            return_value=mock_response,
        ) as mock_post:
            from cwa_book_downloader.release_sources.prowlarr.clients.nzbget import (
                NZBGetClient,
            )

            client = NZBGetClient()
            result = client._rpc_call("testmethod", ["arg1", "arg2"])

            assert result == "test_result"
            # Verify the request was made correctly
            call_args = mock_post.call_args
            assert call_args.kwargs["auth"] == ("nzbget", "password")

    def test_rpc_call_error_response(self, monkeypatch):
        """Test RPC call with error response."""
        config_values = {
            "NZBGET_URL": "http://localhost:6789",
            "NZBGET_USERNAME": "nzbget",
            "NZBGET_PASSWORD": "password",
            "NZBGET_CATEGORY": "Books",
        }
        monkeypatch.setattr(
            "cwa_book_downloader.release_sources.prowlarr.clients.nzbget.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {"error": {"message": "Invalid method"}}

        with patch(
            "cwa_book_downloader.release_sources.prowlarr.clients.nzbget.requests.post",
            return_value=mock_response,
        ):
            from cwa_book_downloader.release_sources.prowlarr.clients.nzbget import (
                NZBGetClient,
            )

            client = NZBGetClient()
            with pytest.raises(Exception) as exc_info:
                client._rpc_call("invalid_method")

            assert "Invalid method" in str(exc_info.value)


class TestNZBGetClientGetStatus:
    """Tests for NZBGetClient.get_status()."""

    def test_get_status_downloading(self, monkeypatch):
        """Test status for downloading NZB."""
        config_values = {
            "NZBGET_URL": "http://localhost:6789",
            "NZBGET_USERNAME": "nzbget",
            "NZBGET_PASSWORD": "password",
            "NZBGET_CATEGORY": "Books",
        }
        monkeypatch.setattr(
            "cwa_book_downloader.release_sources.prowlarr.clients.nzbget.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        def mock_rpc_call(method, params=None):
            if method == "listgroups":
                return [
                    {
                        "NZBID": 123,
                        "FileSizeHi": 0,
                        "FileSizeLo": 100000000,  # 100MB
                        "RemainingSizeHi": 0,
                        "RemainingSizeLo": 50000000,  # 50MB remaining
                        "Status": "DOWNLOADING",
                        "DownloadRate": 1024000,
                        "RemainingSec": 50,
                    }
                ]
            return []

        from cwa_book_downloader.release_sources.prowlarr.clients.nzbget import (
            NZBGetClient,
        )

        with patch.object(NZBGetClient, "__init__", lambda x: None):
            client = NZBGetClient()
            client.url = "http://localhost:6789"
            client.username = "nzbget"
            client.password = "password"
            client._category = "Books"
            client._rpc_call = mock_rpc_call

            status = client.get_status("123")

            assert status.progress == 50.0
            assert status.state_value == "downloading"
            assert status.complete is False
            assert status.download_speed == 1024000
            assert status.eta == 50

    def test_get_status_complete_in_history(self, monkeypatch):
        """Test status for completed NZB in history."""
        config_values = {
            "NZBGET_URL": "http://localhost:6789",
            "NZBGET_USERNAME": "nzbget",
            "NZBGET_PASSWORD": "password",
            "NZBGET_CATEGORY": "Books",
        }
        monkeypatch.setattr(
            "cwa_book_downloader.release_sources.prowlarr.clients.nzbget.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        def mock_rpc_call(method, params=None):
            if method == "listgroups":
                return []  # Not in queue
            if method == "history":
                return [
                    {
                        "NZBID": 123,
                        "Status": "SUCCESS",
                        "DestDir": "/downloads/completed/book",
                    }
                ]
            return []

        from cwa_book_downloader.release_sources.prowlarr.clients.nzbget import (
            NZBGetClient,
        )

        with patch.object(NZBGetClient, "__init__", lambda x: None):
            client = NZBGetClient()
            client.url = "http://localhost:6789"
            client.username = "nzbget"
            client.password = "password"
            client._category = "Books"
            client._rpc_call = mock_rpc_call

            status = client.get_status("123")

            assert status.progress == 100.0
            assert status.state_value == "complete"
            assert status.complete is True
            assert status.file_path == "/downloads/completed/book"

    def test_get_status_failed_in_history(self, monkeypatch):
        """Test status for failed NZB in history."""
        config_values = {
            "NZBGET_URL": "http://localhost:6789",
            "NZBGET_USERNAME": "nzbget",
            "NZBGET_PASSWORD": "password",
            "NZBGET_CATEGORY": "Books",
        }
        monkeypatch.setattr(
            "cwa_book_downloader.release_sources.prowlarr.clients.nzbget.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        def mock_rpc_call(method, params=None):
            if method == "listgroups":
                return []
            if method == "history":
                return [
                    {
                        "NZBID": 123,
                        "Status": "FAILURE/PAR",
                        "DestDir": "",
                    }
                ]
            return []

        from cwa_book_downloader.release_sources.prowlarr.clients.nzbget import (
            NZBGetClient,
        )

        with patch.object(NZBGetClient, "__init__", lambda x: None):
            client = NZBGetClient()
            client.url = "http://localhost:6789"
            client.username = "nzbget"
            client.password = "password"
            client._category = "Books"
            client._rpc_call = mock_rpc_call

            status = client.get_status("123")

            assert status.state_value == "error"
            assert "failed" in status.message.lower()

    def test_get_status_not_found(self, monkeypatch):
        """Test status for non-existent NZB."""
        config_values = {
            "NZBGET_URL": "http://localhost:6789",
            "NZBGET_USERNAME": "nzbget",
            "NZBGET_PASSWORD": "password",
            "NZBGET_CATEGORY": "Books",
        }
        monkeypatch.setattr(
            "cwa_book_downloader.release_sources.prowlarr.clients.nzbget.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        def mock_rpc_call(method, params=None):
            return []  # Empty queue and history

        from cwa_book_downloader.release_sources.prowlarr.clients.nzbget import (
            NZBGetClient,
        )

        with patch.object(NZBGetClient, "__init__", lambda x: None):
            client = NZBGetClient()
            client.url = "http://localhost:6789"
            client.username = "nzbget"
            client.password = "password"
            client._category = "Books"
            client._rpc_call = mock_rpc_call

            status = client.get_status("999")

            assert status.state_value == "error"
            assert "not found" in status.message.lower()

    def test_get_status_queued(self, monkeypatch):
        """Test status for queued NZB."""
        config_values = {
            "NZBGET_URL": "http://localhost:6789",
            "NZBGET_USERNAME": "nzbget",
            "NZBGET_PASSWORD": "password",
            "NZBGET_CATEGORY": "Books",
        }
        monkeypatch.setattr(
            "cwa_book_downloader.release_sources.prowlarr.clients.nzbget.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        def mock_rpc_call(method, params=None):
            if method == "listgroups":
                return [
                    {
                        "NZBID": 123,
                        "FileSizeHi": 0,
                        "FileSizeLo": 100000000,
                        "RemainingSizeHi": 0,
                        "RemainingSizeLo": 100000000,
                        "Status": "QUEUED",
                        "DownloadRate": 0,
                        "RemainingSec": 0,
                    }
                ]
            return []

        from cwa_book_downloader.release_sources.prowlarr.clients.nzbget import (
            NZBGetClient,
        )

        with patch.object(NZBGetClient, "__init__", lambda x: None):
            client = NZBGetClient()
            client.url = "http://localhost:6789"
            client.username = "nzbget"
            client.password = "password"
            client._category = "Books"
            client._rpc_call = mock_rpc_call

            status = client.get_status("123")

            assert status.state_value == "queued"


class TestNZBGetClientAddDownload:
    """Tests for NZBGetClient.add_download()."""

    def test_add_download_success(self, monkeypatch):
        """Test adding an NZB from URL."""
        config_values = {
            "NZBGET_URL": "http://localhost:6789",
            "NZBGET_USERNAME": "nzbget",
            "NZBGET_PASSWORD": "password",
            "NZBGET_CATEGORY": "Books",
        }
        monkeypatch.setattr(
            "cwa_book_downloader.release_sources.prowlarr.clients.nzbget.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        # Mock requests.get for fetching NZB
        mock_get_response = MagicMock()
        mock_get_response.content = b"<nzb>test</nzb>"

        # Mock the RPC call result
        mock_post_response = MagicMock()
        mock_post_response.json.return_value = {"result": 456}

        with patch(
            "cwa_book_downloader.release_sources.prowlarr.clients.nzbget.requests.get",
            return_value=mock_get_response,
        ), patch(
            "cwa_book_downloader.release_sources.prowlarr.clients.nzbget.requests.post",
            return_value=mock_post_response,
        ):
            from cwa_book_downloader.release_sources.prowlarr.clients.nzbget import (
                NZBGetClient,
            )

            client = NZBGetClient()
            result = client.add_download(
                "https://example.com/download.nzb",
                "Test Book",
            )

            assert result == "456"

    def test_add_download_fetch_failure(self, monkeypatch):
        """Test handling of NZB fetch failure."""
        import requests

        config_values = {
            "NZBGET_URL": "http://localhost:6789",
            "NZBGET_USERNAME": "nzbget",
            "NZBGET_PASSWORD": "password",
            "NZBGET_CATEGORY": "Books",
        }
        monkeypatch.setattr(
            "cwa_book_downloader.release_sources.prowlarr.clients.nzbget.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        with patch(
            "cwa_book_downloader.release_sources.prowlarr.clients.nzbget.requests.get",
            side_effect=requests.RequestException("Failed to fetch"),
        ):
            from cwa_book_downloader.release_sources.prowlarr.clients.nzbget import (
                NZBGetClient,
            )

            client = NZBGetClient()
            with pytest.raises(Exception) as exc_info:
                client.add_download("https://example.com/download.nzb", "Test")

            assert "fetch" in str(exc_info.value).lower()


class TestNZBGetClientRemove:
    """Tests for NZBGetClient.remove()."""

    def test_remove_success(self, monkeypatch):
        """Test successful NZB removal."""
        config_values = {
            "NZBGET_URL": "http://localhost:6789",
            "NZBGET_USERNAME": "nzbget",
            "NZBGET_PASSWORD": "password",
            "NZBGET_CATEGORY": "Books",
        }
        monkeypatch.setattr(
            "cwa_book_downloader.release_sources.prowlarr.clients.nzbget.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        def mock_rpc_call(method, params=None):
            if method == "editqueue":
                return True
            return None

        from cwa_book_downloader.release_sources.prowlarr.clients.nzbget import (
            NZBGetClient,
        )

        with patch.object(NZBGetClient, "__init__", lambda x: None):
            client = NZBGetClient()
            client.url = "http://localhost:6789"
            client.username = "nzbget"
            client.password = "password"
            client._category = "Books"
            client._rpc_call = mock_rpc_call

            result = client.remove("123", delete_files=True)

            assert result is True

    def test_remove_with_delete_files(self, monkeypatch):
        """Test removal uses correct command based on delete_files."""
        config_values = {
            "NZBGET_URL": "http://localhost:6789",
            "NZBGET_USERNAME": "nzbget",
            "NZBGET_PASSWORD": "password",
            "NZBGET_CATEGORY": "Books",
        }
        monkeypatch.setattr(
            "cwa_book_downloader.release_sources.prowlarr.clients.nzbget.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        calls = []

        def mock_rpc_call(method, params=None):
            if method == "editqueue":
                calls.append((method, params))
                return True
            return None

        from cwa_book_downloader.release_sources.prowlarr.clients.nzbget import (
            NZBGetClient,
        )

        with patch.object(NZBGetClient, "__init__", lambda x: None):
            client = NZBGetClient()
            client.url = "http://localhost:6789"
            client.username = "nzbget"
            client.password = "password"
            client._category = "Books"
            client._rpc_call = mock_rpc_call

            # Test with delete_files=True
            client.remove("123", delete_files=True)
            assert calls[-1][1][0] == "GroupFinalDelete"

            # Test with delete_files=False
            client.remove("456", delete_files=False)
            assert calls[-1][1][0] == "GroupDelete"
