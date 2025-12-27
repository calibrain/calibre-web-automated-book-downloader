"""
Unit tests for the SABnzbd client.

These tests mock the requests library to test the client logic
without requiring a running SABnzbd instance.
"""

from unittest.mock import MagicMock, patch
import pytest

from cwa_book_downloader.release_sources.prowlarr.clients import DownloadStatus


class TestSABnzbdClientIsConfigured:
    """Tests for SABnzbdClient.is_configured()."""

    def test_is_configured_when_all_set(self, monkeypatch):
        """Test is_configured returns True when properly configured."""
        config_values = {
            "PROWLARR_USENET_CLIENT": "sabnzbd",
            "SABNZBD_URL": "http://localhost:8080",
            "SABNZBD_API_KEY": "abc123",
        }
        monkeypatch.setattr(
            "cwa_book_downloader.release_sources.prowlarr.clients.sabnzbd.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        from cwa_book_downloader.release_sources.prowlarr.clients.sabnzbd import (
            SABnzbdClient,
        )

        assert SABnzbdClient.is_configured() is True

    def test_is_configured_wrong_client(self, monkeypatch):
        """Test is_configured returns False when different client selected."""
        config_values = {
            "PROWLARR_USENET_CLIENT": "nzbget",
            "SABNZBD_URL": "http://localhost:8080",
            "SABNZBD_API_KEY": "abc123",
        }
        monkeypatch.setattr(
            "cwa_book_downloader.release_sources.prowlarr.clients.sabnzbd.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        from cwa_book_downloader.release_sources.prowlarr.clients.sabnzbd import (
            SABnzbdClient,
        )

        assert SABnzbdClient.is_configured() is False

    def test_is_configured_no_url(self, monkeypatch):
        """Test is_configured returns False when URL not set."""
        config_values = {
            "PROWLARR_USENET_CLIENT": "sabnzbd",
            "SABNZBD_URL": "",
            "SABNZBD_API_KEY": "abc123",
        }
        monkeypatch.setattr(
            "cwa_book_downloader.release_sources.prowlarr.clients.sabnzbd.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        from cwa_book_downloader.release_sources.prowlarr.clients.sabnzbd import (
            SABnzbdClient,
        )

        assert SABnzbdClient.is_configured() is False

    def test_is_configured_no_api_key(self, monkeypatch):
        """Test is_configured returns False when API key not set."""
        config_values = {
            "PROWLARR_USENET_CLIENT": "sabnzbd",
            "SABNZBD_URL": "http://localhost:8080",
            "SABNZBD_API_KEY": "",
        }
        monkeypatch.setattr(
            "cwa_book_downloader.release_sources.prowlarr.clients.sabnzbd.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        from cwa_book_downloader.release_sources.prowlarr.clients.sabnzbd import (
            SABnzbdClient,
        )

        assert SABnzbdClient.is_configured() is False


class TestSABnzbdClientTestConnection:
    """Tests for SABnzbdClient.test_connection()."""

    def test_test_connection_success(self, monkeypatch):
        """Test successful connection."""
        config_values = {
            "SABNZBD_URL": "http://localhost:8080",
            "SABNZBD_API_KEY": "abc123",
            "SABNZBD_CATEGORY": "cwabd",
        }
        monkeypatch.setattr(
            "cwa_book_downloader.release_sources.prowlarr.clients.sabnzbd.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {"version": "4.2.1"}

        with patch(
            "cwa_book_downloader.release_sources.prowlarr.clients.sabnzbd.requests.get",
            return_value=mock_response,
        ):
            from cwa_book_downloader.release_sources.prowlarr.clients.sabnzbd import (
                SABnzbdClient,
            )

            client = SABnzbdClient()
            success, message = client.test_connection()

            assert success is True
            assert "4.2.1" in message

    def test_test_connection_failure(self, monkeypatch):
        """Test failed connection."""
        import requests

        config_values = {
            "SABNZBD_URL": "http://localhost:8080",
            "SABNZBD_API_KEY": "wrong",
            "SABNZBD_CATEGORY": "cwabd",
        }
        monkeypatch.setattr(
            "cwa_book_downloader.release_sources.prowlarr.clients.sabnzbd.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        with patch(
            "cwa_book_downloader.release_sources.prowlarr.clients.sabnzbd.requests.get",
            side_effect=requests.exceptions.ConnectionError("Connection refused"),
        ):
            from cwa_book_downloader.release_sources.prowlarr.clients.sabnzbd import (
                SABnzbdClient,
            )

            client = SABnzbdClient()
            success, message = client.test_connection()

            assert success is False
            assert "connect" in message.lower()


class TestSABnzbdClientGetStatus:
    """Tests for SABnzbdClient.get_status()."""

    def test_get_status_downloading(self, monkeypatch):
        """Test status for downloading NZB."""
        config_values = {
            "SABNZBD_URL": "http://localhost:8080",
            "SABNZBD_API_KEY": "abc123",
            "SABNZBD_CATEGORY": "cwabd",
        }
        monkeypatch.setattr(
            "cwa_book_downloader.release_sources.prowlarr.clients.sabnzbd.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        def mock_api_call(mode, params=None):
            if mode == "queue":
                return {
                    "queue": {
                        "slots": [
                            {
                                "nzo_id": "SABnzbd_nzo_abc123",
                                "status": "Downloading",
                                "percentage": "50",
                                "timeleft": "0:05:30",
                                "kbpersec": "1000",
                                "speed": "1 MB/s",
                            }
                        ]
                    }
                }
            return {}

        from cwa_book_downloader.release_sources.prowlarr.clients.sabnzbd import (
            SABnzbdClient,
        )

        with patch.object(SABnzbdClient, "__init__", lambda x: None):
            client = SABnzbdClient()
            client.url = "http://localhost:8080"
            client.api_key = "abc123"
            client._category = "cwabd"
            client._api_call = mock_api_call

            status = client.get_status("SABnzbd_nzo_abc123")

            assert status.progress == 50.0
            assert status.state_value == "downloading"
            assert status.complete is False
            assert status.eta == 330  # 5 min 30 sec
            assert status.download_speed == 1024000  # 1000 KB/s in bytes

    def test_get_status_complete_in_history(self, monkeypatch):
        """Test status for completed NZB in history."""
        config_values = {
            "SABNZBD_URL": "http://localhost:8080",
            "SABNZBD_API_KEY": "abc123",
            "SABNZBD_CATEGORY": "cwabd",
        }
        monkeypatch.setattr(
            "cwa_book_downloader.release_sources.prowlarr.clients.sabnzbd.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        def mock_api_call(mode, params=None):
            if mode == "queue":
                return {"queue": {"slots": []}}
            if mode == "history":
                return {
                    "history": {
                        "slots": [
                            {
                                "nzo_id": "SABnzbd_nzo_abc123",
                                "status": "Completed",
                                "storage": "/downloads/complete/book",
                            }
                        ]
                    }
                }
            return {}

        from cwa_book_downloader.release_sources.prowlarr.clients.sabnzbd import (
            SABnzbdClient,
        )

        with patch.object(SABnzbdClient, "__init__", lambda x: None):
            client = SABnzbdClient()
            client.url = "http://localhost:8080"
            client.api_key = "abc123"
            client._category = "cwabd"
            client._api_call = mock_api_call

            status = client.get_status("SABnzbd_nzo_abc123")

            assert status.progress == 100.0
            assert status.state_value == "complete"
            assert status.complete is True
            assert status.file_path == "/downloads/complete/book"

    def test_get_status_failed(self, monkeypatch):
        """Test status for failed NZB."""
        config_values = {
            "SABNZBD_URL": "http://localhost:8080",
            "SABNZBD_API_KEY": "abc123",
            "SABNZBD_CATEGORY": "cwabd",
        }
        monkeypatch.setattr(
            "cwa_book_downloader.release_sources.prowlarr.clients.sabnzbd.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        def mock_api_call(mode, params=None):
            if mode == "queue":
                return {"queue": {"slots": []}}
            if mode == "history":
                return {
                    "history": {
                        "slots": [
                            {
                                "nzo_id": "SABnzbd_nzo_abc123",
                                "status": "Failed",
                                "fail_message": "Download failed - not enough servers",
                            }
                        ]
                    }
                }
            return {}

        from cwa_book_downloader.release_sources.prowlarr.clients.sabnzbd import (
            SABnzbdClient,
        )

        with patch.object(SABnzbdClient, "__init__", lambda x: None):
            client = SABnzbdClient()
            client.url = "http://localhost:8080"
            client.api_key = "abc123"
            client._category = "cwabd"
            client._api_call = mock_api_call

            status = client.get_status("SABnzbd_nzo_abc123")

            assert status.state_value == "error"
            assert "failed" in status.message.lower()

    def test_get_status_not_found(self, monkeypatch):
        """Test status for non-existent NZB."""
        config_values = {
            "SABNZBD_URL": "http://localhost:8080",
            "SABNZBD_API_KEY": "abc123",
            "SABNZBD_CATEGORY": "cwabd",
        }
        monkeypatch.setattr(
            "cwa_book_downloader.release_sources.prowlarr.clients.sabnzbd.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        def mock_api_call(mode, params=None):
            if mode == "queue":
                return {"queue": {"slots": []}}
            if mode == "history":
                return {"history": {"slots": []}}
            return {}

        from cwa_book_downloader.release_sources.prowlarr.clients.sabnzbd import (
            SABnzbdClient,
        )

        with patch.object(SABnzbdClient, "__init__", lambda x: None):
            client = SABnzbdClient()
            client.url = "http://localhost:8080"
            client.api_key = "abc123"
            client._category = "cwabd"
            client._api_call = mock_api_call

            status = client.get_status("nonexistent")

            assert status.state_value == "error"
            assert "not found" in status.message.lower()

    def test_get_status_queued(self, monkeypatch):
        """Test status for queued NZB."""
        config_values = {
            "SABNZBD_URL": "http://localhost:8080",
            "SABNZBD_API_KEY": "abc123",
            "SABNZBD_CATEGORY": "cwabd",
        }
        monkeypatch.setattr(
            "cwa_book_downloader.release_sources.prowlarr.clients.sabnzbd.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        def mock_api_call(mode, params=None):
            if mode == "queue":
                return {
                    "queue": {
                        "slots": [
                            {
                                "nzo_id": "SABnzbd_nzo_abc123",
                                "status": "Queued",
                                "percentage": "0",
                                "timeleft": "",
                                "kbpersec": "",
                            }
                        ]
                    }
                }
            return {}

        from cwa_book_downloader.release_sources.prowlarr.clients.sabnzbd import (
            SABnzbdClient,
        )

        with patch.object(SABnzbdClient, "__init__", lambda x: None):
            client = SABnzbdClient()
            client.url = "http://localhost:8080"
            client.api_key = "abc123"
            client._category = "cwabd"
            client._api_call = mock_api_call

            status = client.get_status("SABnzbd_nzo_abc123")

            assert status.state_value == "queued"

    def test_get_status_extracting(self, monkeypatch):
        """Test status for extracting NZB."""
        config_values = {
            "SABNZBD_URL": "http://localhost:8080",
            "SABNZBD_API_KEY": "abc123",
            "SABNZBD_CATEGORY": "cwabd",
        }
        monkeypatch.setattr(
            "cwa_book_downloader.release_sources.prowlarr.clients.sabnzbd.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        def mock_api_call(mode, params=None):
            if mode == "queue":
                return {
                    "queue": {
                        "slots": [
                            {
                                "nzo_id": "SABnzbd_nzo_abc123",
                                "status": "Extracting",
                                "percentage": "100",
                                "timeleft": "",
                                "kbpersec": "",
                            }
                        ]
                    }
                }
            return {}

        from cwa_book_downloader.release_sources.prowlarr.clients.sabnzbd import (
            SABnzbdClient,
        )

        with patch.object(SABnzbdClient, "__init__", lambda x: None):
            client = SABnzbdClient()
            client.url = "http://localhost:8080"
            client.api_key = "abc123"
            client._category = "cwabd"
            client._api_call = mock_api_call

            status = client.get_status("SABnzbd_nzo_abc123")

            assert status.state_value == "processing"


class TestSABnzbdClientAddDownload:
    """Tests for SABnzbdClient.add_download()."""

    def test_add_download_success(self, monkeypatch):
        """Test adding an NZB from URL."""
        config_values = {
            "SABNZBD_URL": "http://localhost:8080",
            "SABNZBD_API_KEY": "abc123",
            "SABNZBD_CATEGORY": "cwabd",
        }
        monkeypatch.setattr(
            "cwa_book_downloader.release_sources.prowlarr.clients.sabnzbd.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "status": True,
            "nzo_ids": ["SABnzbd_nzo_xyz789"],
        }

        with patch(
            "cwa_book_downloader.release_sources.prowlarr.clients.sabnzbd.requests.get",
            return_value=mock_response,
        ):
            from cwa_book_downloader.release_sources.prowlarr.clients.sabnzbd import (
                SABnzbdClient,
            )

            client = SABnzbdClient()
            result = client.add_download(
                "https://example.com/download.nzb",
                "Test Book",
            )

            assert result == "SABnzbd_nzo_xyz789"

    def test_add_download_no_nzo_id(self, monkeypatch):
        """Test add_download when SABnzbd returns no nzo_id."""
        config_values = {
            "SABNZBD_URL": "http://localhost:8080",
            "SABNZBD_API_KEY": "abc123",
            "SABNZBD_CATEGORY": "cwabd",
        }
        monkeypatch.setattr(
            "cwa_book_downloader.release_sources.prowlarr.clients.sabnzbd.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "status": True,
            "nzo_ids": [],
        }

        with patch(
            "cwa_book_downloader.release_sources.prowlarr.clients.sabnzbd.requests.get",
            return_value=mock_response,
        ):
            from cwa_book_downloader.release_sources.prowlarr.clients.sabnzbd import (
                SABnzbdClient,
            )

            client = SABnzbdClient()
            with pytest.raises(Exception) as exc_info:
                client.add_download("https://example.com/download.nzb", "Test")

            assert "nzo_id" in str(exc_info.value).lower()


class TestSABnzbdClientRemove:
    """Tests for SABnzbdClient.remove()."""

    def test_remove_from_queue_success(self, monkeypatch):
        """Test successful removal from queue."""
        config_values = {
            "SABNZBD_URL": "http://localhost:8080",
            "SABNZBD_API_KEY": "abc123",
            "SABNZBD_CATEGORY": "cwabd",
        }
        monkeypatch.setattr(
            "cwa_book_downloader.release_sources.prowlarr.clients.sabnzbd.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        def mock_api_call(mode, params=None):
            if mode == "queue":
                return {"status": True}
            return {}

        from cwa_book_downloader.release_sources.prowlarr.clients.sabnzbd import (
            SABnzbdClient,
        )

        with patch.object(SABnzbdClient, "__init__", lambda x: None):
            client = SABnzbdClient()
            client.url = "http://localhost:8080"
            client.api_key = "abc123"
            client._category = "cwabd"
            client._api_call = mock_api_call

            result = client.remove("SABnzbd_nzo_abc123", delete_files=True)

            assert result is True

    def test_remove_from_history(self, monkeypatch):
        """Test removal from history when not in queue."""
        config_values = {
            "SABNZBD_URL": "http://localhost:8080",
            "SABNZBD_API_KEY": "abc123",
            "SABNZBD_CATEGORY": "cwabd",
        }
        monkeypatch.setattr(
            "cwa_book_downloader.release_sources.prowlarr.clients.sabnzbd.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        call_count = {"queue": 0, "history": 0}

        def mock_api_call(mode, params=None):
            if mode == "queue" and params and params.get("name") == "delete":
                call_count["queue"] += 1
                return {"status": False}  # Not in queue
            if mode == "history" and params and params.get("name") == "delete":
                call_count["history"] += 1
                return {"status": True}  # Found in history
            return {}

        from cwa_book_downloader.release_sources.prowlarr.clients.sabnzbd import (
            SABnzbdClient,
        )

        with patch.object(SABnzbdClient, "__init__", lambda x: None):
            client = SABnzbdClient()
            client.url = "http://localhost:8080"
            client.api_key = "abc123"
            client._category = "cwabd"
            client._api_call = mock_api_call

            result = client.remove("SABnzbd_nzo_abc123")

            assert result is True
            assert call_count["history"] == 1


class TestSABnzbdClientFindExisting:
    """Tests for SABnzbdClient.find_existing()."""

    def test_find_existing_in_queue(self, monkeypatch):
        """Test finding existing NZB in queue."""
        config_values = {
            "SABNZBD_URL": "http://localhost:8080",
            "SABNZBD_API_KEY": "abc123",
            "SABNZBD_CATEGORY": "cwabd",
        }
        monkeypatch.setattr(
            "cwa_book_downloader.release_sources.prowlarr.clients.sabnzbd.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        def mock_api_call(mode, params=None):
            if mode == "queue":
                return {
                    "queue": {
                        "slots": [
                            {
                                "nzo_id": "SABnzbd_nzo_found",
                                "filename": "Test_Book.nzb",
                                "status": "Downloading",
                                "percentage": "50",
                                "timeleft": "",
                                "kbpersec": "",
                            }
                        ]
                    }
                }
            return {"history": {"slots": []}}

        from cwa_book_downloader.release_sources.prowlarr.clients.sabnzbd import (
            SABnzbdClient,
        )

        with patch.object(SABnzbdClient, "__init__", lambda x: None):
            client = SABnzbdClient()
            client.url = "http://localhost:8080"
            client.api_key = "abc123"
            client._category = "cwabd"
            client._api_call = mock_api_call

            result = client.find_existing("https://example.com/Test_Book.nzb")

            assert result is not None
            nzo_id, status = result
            assert nzo_id == "SABnzbd_nzo_found"

    def test_find_existing_in_history(self, monkeypatch):
        """Test finding existing NZB in history."""
        config_values = {
            "SABNZBD_URL": "http://localhost:8080",
            "SABNZBD_API_KEY": "abc123",
            "SABNZBD_CATEGORY": "cwabd",
        }
        monkeypatch.setattr(
            "cwa_book_downloader.release_sources.prowlarr.clients.sabnzbd.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        def mock_api_call(mode, params=None):
            if mode == "queue":
                return {"queue": {"slots": []}}
            if mode == "history":
                return {
                    "history": {
                        "slots": [
                            {
                                "nzo_id": "SABnzbd_nzo_history",
                                "name": "Test Book",
                                "status": "Completed",
                                "storage": "/downloads/Test Book",
                            }
                        ]
                    }
                }
            return {}

        from cwa_book_downloader.release_sources.prowlarr.clients.sabnzbd import (
            SABnzbdClient,
        )

        with patch.object(SABnzbdClient, "__init__", lambda x: None):
            client = SABnzbdClient()
            client.url = "http://localhost:8080"
            client.api_key = "abc123"
            client._category = "cwabd"
            client._api_call = mock_api_call

            result = client.find_existing("https://example.com/Test%20Book.nzb")

            assert result is not None
            nzo_id, status = result
            assert nzo_id == "SABnzbd_nzo_history"

    def test_find_existing_not_found(self, monkeypatch):
        """Test find_existing when NZB not found."""
        config_values = {
            "SABNZBD_URL": "http://localhost:8080",
            "SABNZBD_API_KEY": "abc123",
            "SABNZBD_CATEGORY": "cwabd",
        }
        monkeypatch.setattr(
            "cwa_book_downloader.release_sources.prowlarr.clients.sabnzbd.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        def mock_api_call(mode, params=None):
            if mode == "queue":
                return {"queue": {"slots": []}}
            if mode == "history":
                return {"history": {"slots": []}}
            return {}

        from cwa_book_downloader.release_sources.prowlarr.clients.sabnzbd import (
            SABnzbdClient,
        )

        with patch.object(SABnzbdClient, "__init__", lambda x: None):
            client = SABnzbdClient()
            client.url = "http://localhost:8080"
            client.api_key = "abc123"
            client._category = "cwabd"
            client._api_call = mock_api_call

            result = client.find_existing("https://example.com/unknown.nzb")

            assert result is None
