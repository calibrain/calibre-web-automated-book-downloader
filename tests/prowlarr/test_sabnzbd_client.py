"""
Unit tests for the SABnzbd client.

These tests mock the requests library to test the client logic
without requiring a running SABnzbd instance.
"""

from unittest.mock import MagicMock, patch

import pytest


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
            "shelfmark.download.clients.sabnzbd.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        from shelfmark.download.clients.sabnzbd import (
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
            "shelfmark.download.clients.sabnzbd.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        from shelfmark.download.clients.sabnzbd import (
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
            "shelfmark.download.clients.sabnzbd.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        from shelfmark.download.clients.sabnzbd import (
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
            "shelfmark.download.clients.sabnzbd.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        from shelfmark.download.clients.sabnzbd import (
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
            "SABNZBD_CATEGORY": "books",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.sabnzbd.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {"version": "4.2.1"}

        with patch(
            "shelfmark.download.clients.sabnzbd.requests.get",
            return_value=mock_response,
        ):
            from shelfmark.download.clients.sabnzbd import (
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
            "SABNZBD_CATEGORY": "books",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.sabnzbd.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        with patch(
            "shelfmark.download.clients.sabnzbd.requests.get",
            side_effect=requests.exceptions.ConnectionError("Connection refused"),
        ):
            from shelfmark.download.clients.sabnzbd import (
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
            "SABNZBD_CATEGORY": "books",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.sabnzbd.config.get",
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

        from shelfmark.download.clients.sabnzbd import (
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
            "SABNZBD_CATEGORY": "books",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.sabnzbd.config.get",
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
                                "storage": "/downloads/complete/book/Sorted/Subfolder",
                                "name": "book",
                            }
                        ]
                    }
                }
            return {}

        from shelfmark.download.clients.sabnzbd import (
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
            assert status.file_path == "/downloads/complete/book"  # resolved to job root

    def test_get_status_complete_empty_storage(self, monkeypatch):
        """Test status for completed NZB with empty storage path.

        This can happen if SABnzbd category is misconfigured or files are
        deleted after completion. The file_path should be empty string.
        """
        config_values = {
            "SABNZBD_URL": "http://localhost:8080",
            "SABNZBD_API_KEY": "abc123",
            "SABNZBD_CATEGORY": "books",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.sabnzbd.config.get",
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
                                "storage": "",  # Empty storage path
                            }
                        ]
                    }
                }
            return {}

        from shelfmark.download.clients.sabnzbd import (
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
            assert status.file_path == ""  # Empty, not None

    def test_get_status_failed(self, monkeypatch):
        """Test status for failed NZB."""
        config_values = {
            "SABNZBD_URL": "http://localhost:8080",
            "SABNZBD_API_KEY": "abc123",
            "SABNZBD_CATEGORY": "books",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.sabnzbd.config.get",
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

        from shelfmark.download.clients.sabnzbd import (
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
            "SABNZBD_CATEGORY": "books",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.sabnzbd.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        def mock_api_call(mode, params=None):
            if mode == "queue":
                return {"queue": {"slots": []}}
            if mode == "history":
                return {"history": {"slots": []}}
            return {}

        from shelfmark.download.clients.sabnzbd import (
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
            "SABNZBD_CATEGORY": "books",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.sabnzbd.config.get",
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

        from shelfmark.download.clients.sabnzbd import (
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
            "SABNZBD_CATEGORY": "books",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.sabnzbd.config.get",
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

        from shelfmark.download.clients.sabnzbd import (
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
            "SABNZBD_CATEGORY": "books",
            "PROWLARR_URL": "https://example.com",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.sabnzbd.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        from shelfmark.download.clients.sabnzbd import (
            SABnzbdClient,
        )

        with (
            patch.object(
                SABnzbdClient,
                "_fetch_nzb_content",
                return_value=b"nzbdata",
            ),
            patch.object(
                SABnzbdClient,
                "_api_post_file",
                return_value={"status": True, "nzo_ids": ["SABnzbd_nzo_xyz789"]},
            ),
        ):
            client = SABnzbdClient()
            result = client.add_download(
                "https://example.com/download.nzb",
                "Test Book",
            )

            assert result == "SABnzbd_nzo_xyz789"

    def test_add_download_uses_configured_category_and_nzb_filename(self, monkeypatch):
        """Test add_download posts the expected SABnzbd payload."""
        config_values = {
            "SABNZBD_URL": "http://localhost:8080",
            "SABNZBD_API_KEY": "abc123",
            "SABNZBD_CATEGORY": "books",
            "PROWLARR_URL": "https://example.com",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.sabnzbd.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        mock_get_response = MagicMock()
        mock_get_response.content = b"<nzb>test</nzb>"

        mock_post_response = MagicMock()
        mock_post_response.json.return_value = {"status": True, "nzo_ids": ["SABnzbd_nzo_xyz789"]}

        with (
            patch(
                "shelfmark.download.clients.sabnzbd.requests.get",
                return_value=mock_get_response,
            ),
            patch(
                "shelfmark.download.clients.sabnzbd.requests.post",
                return_value=mock_post_response,
            ) as mock_post,
        ):
            from shelfmark.download.clients.sabnzbd import SABnzbdClient

            client = SABnzbdClient()
            result = client.add_download("https://example.com/download.nzb.gz", "Test Book")

        assert result == "SABnzbd_nzo_xyz789"
        assert mock_post.call_args.kwargs["params"]["mode"] == "addfile"
        assert mock_post.call_args.kwargs["params"]["cat"] == "books"
        assert mock_post.call_args.kwargs["params"]["nzbname"] == "Test Book"
        assert mock_post.call_args.kwargs["files"]["name"][0] == "Test Book.nzb.gz"

    def test_add_download_no_nzo_id(self, monkeypatch):
        """Test add_download when SABnzbd returns no nzo_id."""
        config_values = {
            "SABNZBD_URL": "http://localhost:8080",
            "SABNZBD_API_KEY": "abc123",
            "SABNZBD_CATEGORY": "books",
            "PROWLARR_URL": "https://example.com",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.sabnzbd.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        from shelfmark.download.clients.sabnzbd import (
            SABnzbdClient,
        )

        with (
            patch.object(
                SABnzbdClient,
                "_fetch_nzb_content",
                return_value=b"nzbdata",
            ),
            patch.object(
                SABnzbdClient,
                "_api_post_file",
                return_value={"status": True, "nzo_ids": []},
            ),
            patch.object(
                SABnzbdClient,
                "_api_call",
                return_value={"status": True, "nzo_ids": []},
            ),
        ):
            client = SABnzbdClient()
            with pytest.raises(Exception) as exc_info:
                client.add_download("https://example.com/download.nzb", "Test")

            assert "nzo_id" in str(exc_info.value).lower()

    def test_add_download_fallback_to_addurl(self, monkeypatch):
        """Test fallback to addurl when NZB fetch fails."""
        import requests

        config_values = {
            "SABNZBD_URL": "http://localhost:8080",
            "SABNZBD_API_KEY": "abc123",
            "SABNZBD_CATEGORY": "books",
            "PROWLARR_URL": "https://example.com",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.sabnzbd.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        from shelfmark.download.clients.sabnzbd import (
            SABnzbdClient,
        )

        with (
            patch.object(
                SABnzbdClient,
                "_fetch_nzb_content",
                side_effect=requests.RequestException("Fetch failed"),
            ),
            patch.object(
                SABnzbdClient,
                "_api_call",
                return_value={"status": True, "nzo_ids": ["SABnzbd_nzo_fallback"]},
            ) as mock_api_call,
        ):
            client = SABnzbdClient()
            result = client.add_download("https://example.com/download.nzb", "Test Book")

            assert result == "SABnzbd_nzo_fallback"
            assert mock_api_call.call_args[0][0] == "addurl"

    def test_add_download_does_not_prefetch_untrusted_nzb_url(self, monkeypatch):
        """Untrusted NZB URLs should be handed to SABnzbd without backend prefetch."""
        config_values = {
            "SABNZBD_URL": "http://localhost:8080",
            "SABNZBD_API_KEY": "abc123",
            "SABNZBD_CATEGORY": "books",
            "PROWLARR_URL": "https://prowlarr.example",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.sabnzbd.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {"status": True, "nzo_ids": ["SABnzbd_nzo_addurl"]}

        with patch(
            "shelfmark.download.clients.sabnzbd.requests.get",
            return_value=mock_response,
        ) as mock_get:
            from shelfmark.download.clients.sabnzbd import SABnzbdClient

            client = SABnzbdClient()
            result = client.add_download("https://attacker.example/download.nzb", "Test Book")

        assert result == "SABnzbd_nzo_addurl"
        called_urls = [call.args[0] for call in mock_get.call_args_list]
        assert "https://attacker.example/download.nzb" not in called_urls
        assert called_urls == ["http://localhost:8080/api"]

    def test_add_download_prefetches_named_newznab_indexer_url(self, monkeypatch):
        """Named Newznab origins should receive the same backend prefetch as legacy URLs."""
        config_values = {
            "SABNZBD_URL": "http://localhost:8080",
            "SABNZBD_API_KEY": "abc123",
            "SABNZBD_CATEGORY": "books",
            "NEWZNAB_INDEXERS": [
                {"name": "NZBGeek", "url": "https://geek.example", "api_key": "secret"}
            ],
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.sabnzbd.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        from shelfmark.download.clients.sabnzbd import SABnzbdClient

        with (
            patch.object(SABnzbdClient, "_fetch_nzb_content", return_value=b"nzbdata") as fetch,
            patch.object(
                SABnzbdClient,
                "_api_post_file",
                return_value={"status": True, "nzo_ids": ["SABnzbd_nzo_named"]},
            ),
        ):
            client = SABnzbdClient()
            result = client.add_download(
                "https://geek.example/download.nzb?apikey=secret",
                "Test Book",
            )

        assert result == "SABnzbd_nzo_named"
        fetch.assert_called_once_with("https://geek.example/download.nzb?apikey=secret")

    @pytest.mark.parametrize(
        ("indexer_url", "nzb_url", "expected"),
        [
            ("https://indexer.example.com", "https://indexer.example.com/get.nzb", True),
            ("https://indexer.example.com", "https://file.indexer.example.com/get.nzb", True),
            ("https://indexer.example.com", "https://a.b.indexer.example.com/get.nzb", True),
            ("https://indexer.example.com/api", "https://FILE.Indexer.Example.com/x", True),
            # Siblings and the parent share the registrable domain.
            ("https://api.example.com", "https://file.example.com/get.nzb", True),
            ("https://indexer.example.com", "https://example.com/get.nzb", True),
            ("https://api.indexer.co.uk", "https://dl.indexer.co.uk/get.nzb", True),
            # Other registrable domains stay untrusted, including under shared suffixes.
            ("https://indexer.example.com", "https://indexer.example.com.evil.test/x", False),
            ("https://indexer.example.com", "https://example.net/get.nzb", False),
            ("https://indexer.co.uk", "https://evil.co.uk/get.nzb", False),
            ("https://mine.duckdns.org", "https://evil.duckdns.org/get.nzb", False),
            ("http://prowlarr:9696", "http://other:9696/get.nzb", False),
            # Scheme and port must still match.
            ("https://indexer.example.com", "http://file.indexer.example.com/get.nzb", False),
            ("https://indexer.example.com", "https://file.indexer.example.com:8443/x", False),
            # IP literals never extend to "subdomains".
            ("http://10.0.0.5:9696", "http://10.0.0.5:9696/get.nzb", True),
            ("http://10.0.0.5:9696", "http://x.10.0.0.5:9696/get.nzb", False),
        ],
    )
    def test_can_prefetch_nzb_url_same_domain(self, monkeypatch, indexer_url, nzb_url, expected):
        """NZB downloads served from the configured indexer's domain are prefetched."""
        config_values = {
            "SABNZBD_URL": "http://localhost:8080",
            "SABNZBD_API_KEY": "abc123",
            "NEWZNAB_INDEXERS": [{"name": "Indexer", "url": indexer_url, "api_key": "k"}],
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.sabnzbd.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        from shelfmark.download.clients.sabnzbd import SABnzbdClient

        assert SABnzbdClient()._can_prefetch_nzb_url(nzb_url) is expected


class TestFetchNzbContentRedirects:
    """_fetch_nzb_content must not leak the Prowlarr API key across hosts."""

    @staticmethod
    def _client(monkeypatch):
        config_values = {
            "SABNZBD_URL": "http://localhost:8080",
            "SABNZBD_API_KEY": "abc123",
            "PROWLARR_URL": "https://prowlarr.example",
            "PROWLARR_API_KEY": "secret",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.sabnzbd.config.get",
            lambda key, default="": config_values.get(key, default),
        )
        from shelfmark.download.clients.sabnzbd import SABnzbdClient

        with patch.object(SABnzbdClient, "__init__", lambda x: None):
            client = SABnzbdClient()
        return client

    def test_strips_api_key_on_cross_host_redirect(self, monkeypatch):
        """Prowlarr often 302s to the indexer's own download link."""
        client = self._client(monkeypatch)
        redirect = MagicMock(
            status_code=302, headers={"Location": "https://indexer.example/get.nzb"}
        )
        final = MagicMock(status_code=200, content=b"nzbdata")
        with patch(
            "shelfmark.download.clients.sabnzbd.requests.get",
            MagicMock(side_effect=[redirect, final]),
        ) as mock_get:
            assert client._fetch_nzb_content("https://prowlarr.example/download?id=1") == b"nzbdata"
        assert mock_get.call_count == 2
        assert mock_get.call_args_list[0].kwargs["headers"].get("X-Api-Key") == "secret"
        assert "X-Api-Key" not in mock_get.call_args_list[1].kwargs["headers"]
        assert mock_get.call_args_list[0].kwargs.get("allow_redirects") is False

    def test_keeps_api_key_on_same_host_redirect(self, monkeypatch):
        client = self._client(monkeypatch)
        redirect = MagicMock(status_code=302, headers={"Location": "/download2?id=1"})
        final = MagicMock(status_code=200, content=b"nzbdata")
        with patch(
            "shelfmark.download.clients.sabnzbd.requests.get",
            MagicMock(side_effect=[redirect, final]),
        ) as mock_get:
            assert client._fetch_nzb_content("https://prowlarr.example/download?id=1") == b"nzbdata"
        assert mock_get.call_count == 2
        assert mock_get.call_args_list[1].args[0] == "https://prowlarr.example/download2?id=1"
        assert mock_get.call_args_list[1].kwargs["headers"].get("X-Api-Key") == "secret"


class TestSABnzbdClientRemove:
    """Tests for SABnzbdClient.remove()."""

    def test_remove_from_queue_success(self, monkeypatch):
        """Test successful removal from queue."""
        config_values = {
            "SABNZBD_URL": "http://localhost:8080",
            "SABNZBD_API_KEY": "abc123",
            "SABNZBD_CATEGORY": "books",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.sabnzbd.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        def mock_api_call(mode, params=None):
            if mode == "queue":
                return {"status": True}
            return {}

        from shelfmark.download.clients.sabnzbd import (
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
            "SABNZBD_CATEGORY": "books",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.sabnzbd.config.get",
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

        from shelfmark.download.clients.sabnzbd import (
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

    def test_remove_from_history_passes_archive_flag(self, monkeypatch):
        """Test remove forwards the archive flag to history deletes."""
        config_values = {
            "SABNZBD_URL": "http://localhost:8080",
            "SABNZBD_API_KEY": "abc123",
            "SABNZBD_CATEGORY": "books",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.sabnzbd.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        history_calls = []

        def mock_api_call(mode, params=None):
            if mode == "queue":
                return {"status": False}
            if mode == "history":
                history_calls.append(params or {})
                return {"status": True}
            return {}

        from shelfmark.download.clients.sabnzbd import SABnzbdClient

        with patch.object(SABnzbdClient, "__init__", lambda x: None):
            client = SABnzbdClient()
            client.url = "http://localhost:8080"
            client.api_key = "abc123"
            client._category = "cwabd"
            client._api_call = mock_api_call

            result = client.remove("SABnzbd_nzo_abc123", delete_files=True, archive=False)

        assert result is True
        assert history_calls == [
            {"name": "delete", "value": "SABnzbd_nzo_abc123", "del_files": 1, "archive": 0}
        ]


class TestSABnzbdClientFindExisting:
    """Tests for SABnzbdClient.find_existing()."""

    def test_find_existing_in_queue(self, monkeypatch):
        """Test finding existing NZB in queue."""
        config_values = {
            "SABNZBD_URL": "http://localhost:8080",
            "SABNZBD_API_KEY": "abc123",
            "SABNZBD_CATEGORY": "books",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.sabnzbd.config.get",
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
                                "cat": "cwabd",
                                "status": "Downloading",
                                "percentage": "50",
                                "timeleft": "",
                                "kbpersec": "",
                            }
                        ]
                    }
                }
            return {"history": {"slots": []}}

        from shelfmark.download.clients.sabnzbd import (
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
            nzo_id, _status = result
            assert nzo_id == "SABnzbd_nzo_found"

    def test_find_existing_in_history(self, monkeypatch):
        """Test finding existing NZB in history."""
        config_values = {
            "SABNZBD_URL": "http://localhost:8080",
            "SABNZBD_API_KEY": "abc123",
            "SABNZBD_CATEGORY": "books",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.sabnzbd.config.get",
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
                                "category": "cwabd",
                                "status": "Completed",
                                "storage": "/downloads/Test Book",
                            }
                        ]
                    }
                }
            return {}

        from shelfmark.download.clients.sabnzbd import (
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
            nzo_id, _status = result
            assert nzo_id == "SABnzbd_nzo_history"

    def test_find_existing_not_found(self, monkeypatch):
        """Test find_existing when NZB not found."""
        config_values = {
            "SABNZBD_URL": "http://localhost:8080",
            "SABNZBD_API_KEY": "abc123",
            "SABNZBD_CATEGORY": "books",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.sabnzbd.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        def mock_api_call(mode, params=None):
            if mode == "queue":
                return {"queue": {"slots": []}}
            if mode == "history":
                return {"history": {"slots": []}}
            return {}

        from shelfmark.download.clients.sabnzbd import (
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

    def test_find_existing_ignores_different_category(self, monkeypatch):
        """Test that find_existing ignores downloads in different categories.

        This tests the fix for issue #508 where SABnzbd's test download
        in the 'default' category was incorrectly matched.
        """
        config_values = {
            "SABNZBD_URL": "http://localhost:8080",
            "SABNZBD_API_KEY": "abc123",
            "SABNZBD_CATEGORY": "books",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.sabnzbd.config.get",
            lambda key, default="": config_values.get(key, default),
        )

        def mock_api_call(mode, params=None):
            if mode == "queue":
                return {
                    "queue": {
                        "slots": [
                            {
                                "nzo_id": "SABnzbd_nzo_test",
                                "filename": "test_download_1000MB",
                                "cat": "default",  # Different category
                                "status": "Downloading",
                                "percentage": "50",
                                "timeleft": "",
                                "kbpersec": "",
                            }
                        ]
                    }
                }
            if mode == "history":
                return {
                    "history": {
                        "slots": [
                            {
                                "nzo_id": "SABnzbd_nzo_old_test",
                                "name": "test_download_1000MB",
                                "category": "default",  # Different category
                                "status": "Completed",
                                "storage": "/downloads/default/test_download_1000MB",
                            }
                        ]
                    }
                }
            return {}

        from shelfmark.download.clients.sabnzbd import (
            SABnzbdClient,
        )

        with patch.object(SABnzbdClient, "__init__", lambda x: None):
            client = SABnzbdClient()
            client.url = "http://localhost:8080"
            client.api_key = "abc123"
            client._category = "books"
            client._api_call = mock_api_call

            # Even though "download" might match "test_download_1000MB",
            # it should be ignored because it's in the "default" category
            result = client.find_existing("https://example.com/download/Book.nzb")

            assert result is None
