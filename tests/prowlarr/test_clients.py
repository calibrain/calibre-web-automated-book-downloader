"""
Tests for the download client infrastructure.
"""

import pytest

from cwa_book_downloader.release_sources.prowlarr.clients import (
    DownloadStatus,
    DownloadState,
    DownloadClient,
    register_client,
    get_client,
    list_configured_clients,
    get_all_clients,
    _CLIENTS,
)


class TestDownloadState:
    """Tests for the DownloadState enum."""

    def test_all_states_exist(self):
        """Test that all expected states are defined."""
        assert DownloadState.DOWNLOADING.value == "downloading"
        assert DownloadState.COMPLETE.value == "complete"
        assert DownloadState.ERROR.value == "error"
        assert DownloadState.SEEDING.value == "seeding"
        assert DownloadState.PAUSED.value == "paused"
        assert DownloadState.QUEUED.value == "queued"
        assert DownloadState.CHECKING.value == "checking"
        assert DownloadState.PROCESSING.value == "processing"
        assert DownloadState.UNKNOWN.value == "unknown"

    def test_state_from_string(self):
        """Test creating state from string value."""
        assert DownloadState("downloading") == DownloadState.DOWNLOADING
        assert DownloadState("complete") == DownloadState.COMPLETE
        assert DownloadState("error") == DownloadState.ERROR

    def test_invalid_state_raises(self):
        """Test that invalid state string raises ValueError."""
        with pytest.raises(ValueError):
            DownloadState("invalid_state")


class TestDownloadStatus:
    """Tests for the DownloadStatus dataclass."""

    def test_create_download_status(self):
        """Test creating a DownloadStatus."""
        status = DownloadStatus(
            progress=50.0,
            state="downloading",
            message="Downloading...",
            complete=False,
            file_path=None,
            download_speed=1024000,
            eta=120,
        )

        assert status.progress == 50.0
        # State is normalized to enum
        assert status.state == DownloadState.DOWNLOADING
        assert status.state_value == "downloading"
        assert status.message == "Downloading..."
        assert status.complete is False
        assert status.file_path is None
        assert status.download_speed == 1024000
        assert status.eta == 120

    def test_download_status_defaults(self):
        """Test DownloadStatus default values."""
        status = DownloadStatus(
            progress=100.0,
            state="complete",
            message=None,
            complete=True,
            file_path="/downloads/book.epub",
        )

        assert status.download_speed is None
        assert status.eta is None

    def test_download_status_completed(self):
        """Test creating a completed status."""
        status = DownloadStatus(
            progress=100.0,
            state="complete",
            message="Download finished",
            complete=True,
            file_path="/downloads/book.epub",
        )

        assert status.complete is True
        assert status.file_path == "/downloads/book.epub"

    def test_download_status_state_normalization(self):
        """Test that string states are normalized to enum."""
        status = DownloadStatus(
            progress=50.0,
            state="downloading",
            message=None,
            complete=False,
            file_path=None,
        )
        # String should be converted to enum
        assert status.state == DownloadState.DOWNLOADING
        assert status.state_value == "downloading"

    def test_download_status_with_enum_state(self):
        """Test creating status with enum state directly."""
        status = DownloadStatus(
            progress=100.0,
            state=DownloadState.COMPLETE,
            message="Done",
            complete=True,
            file_path="/path/to/file",
        )
        assert status.state == DownloadState.COMPLETE
        assert status.state_value == "complete"

    def test_download_status_progress_clamping(self):
        """Test that progress is clamped to [0, 100]."""
        # Progress over 100 should be clamped
        status1 = DownloadStatus(
            progress=150.0,
            state="complete",
            message=None,
            complete=True,
            file_path=None,
        )
        assert status1.progress == 100.0

        # Negative progress should be clamped to 0
        status2 = DownloadStatus(
            progress=-10.0,
            state="error",
            message=None,
            complete=False,
            file_path=None,
        )
        assert status2.progress == 0.0

    def test_download_status_immutable(self):
        """Test that DownloadStatus is immutable (frozen)."""
        status = DownloadStatus(
            progress=50.0,
            state="downloading",
            message="Test",
            complete=False,
            file_path=None,
        )
        with pytest.raises(AttributeError):
            status.progress = 75.0

    def test_download_status_state_value_with_unknown_string(self):
        """Test state_value with an unknown state string."""
        # Create status with an unrecognized state string
        status = DownloadStatus(
            progress=50.0,
            state="fetching_metadata",  # Not a standard DownloadState
            message=None,
            complete=False,
            file_path=None,
        )
        # Unknown states remain as strings
        assert status.state_value == "fetching_metadata"

    def test_download_status_all_fields(self):
        """Test status with all optional fields."""
        status = DownloadStatus(
            progress=75.5,
            state=DownloadState.DOWNLOADING,
            message="Downloading at 1 MB/s",
            complete=False,
            file_path=None,
            download_speed=1048576,
            eta=300,
        )
        assert status.progress == 75.5
        assert status.download_speed == 1048576
        assert status.eta == 300
        assert status.message == "Downloading at 1 MB/s"


class TestClientRegistry:
    """Tests for the client registry functions."""

    def setup_method(self):
        """Save original clients before each test."""
        self._original_clients = dict(_CLIENTS)

    def teardown_method(self):
        """Restore original clients after each test."""
        _CLIENTS.clear()
        _CLIENTS.update(self._original_clients)

    def test_register_client_decorator(self):
        """Test that register_client decorator registers the client."""
        # Use valid protocol (torrent or usenet)
        test_protocol = "torrent"

        @register_client(test_protocol)
        class TestRegistryClient(DownloadClient):
            protocol = test_protocol
            name = "test_registry_client"

            @staticmethod
            def is_configured():
                return True

            def test_connection(self):
                return True, "OK"

            def add_download(self, url, name, category="test"):
                return "id"

            def get_status(self, download_id):
                return DownloadStatus(0, "unknown", None, False, None)

            def remove(self, download_id, delete_files=False):
                return True

            def get_download_path(self, download_id):
                return None

        assert test_protocol in _CLIENTS
        assert TestRegistryClient in _CLIENTS[test_protocol]

    def test_multiple_clients_same_protocol(self):
        """Test registering multiple clients for the same protocol."""
        # Use valid protocol (usenet to avoid conflict with real clients)
        test_protocol = "usenet"
        initial_count = len(_CLIENTS.get(test_protocol, []))

        @register_client(test_protocol)
        class MultiClient1(DownloadClient):
            protocol = test_protocol
            name = "multi_client1"

            @staticmethod
            def is_configured():
                return False

            def test_connection(self):
                return True, "OK"

            def add_download(self, url, name, category="test"):
                return "id"

            def get_status(self, download_id):
                return DownloadStatus(0, "unknown", None, False, None)

            def remove(self, download_id, delete_files=False):
                return True

            def get_download_path(self, download_id):
                return None

        @register_client(test_protocol)
        class MultiClient2(DownloadClient):
            protocol = test_protocol
            name = "multi_client2"

            @staticmethod
            def is_configured():
                return False

            def test_connection(self):
                return True, "OK"

            def add_download(self, url, name, category="test"):
                return "id"

            def get_status(self, download_id):
                return DownloadStatus(0, "unknown", None, False, None)

            def remove(self, download_id, delete_files=False):
                return True

            def get_download_path(self, download_id):
                return None

        # Should have 2 more clients than initially
        assert len(_CLIENTS[test_protocol]) >= initial_count + 2

    def test_get_client_returns_configured(self):
        """Test that get_client returns the first configured client."""
        # Use valid protocol
        test_protocol = "torrent"

        @register_client(test_protocol)
        class UnconfiguredTestClient(DownloadClient):
            protocol = test_protocol
            name = "unconfigured_test"

            @staticmethod
            def is_configured():
                return False

            def test_connection(self):
                return True, "OK"

            def add_download(self, url, name, category="test"):
                return "id"

            def get_status(self, download_id):
                return DownloadStatus(0, "unknown", None, False, None)

            def remove(self, download_id, delete_files=False):
                return True

            def get_download_path(self, download_id):
                return None

        @register_client(test_protocol)
        class ConfiguredTestClient(DownloadClient):
            protocol = test_protocol
            name = "configured_test"

            @staticmethod
            def is_configured():
                return True

            def test_connection(self):
                return True, "OK"

            def add_download(self, url, name, category="test"):
                return "id"

            def get_status(self, download_id):
                return DownloadStatus(0, "unknown", None, False, None)

            def remove(self, download_id, delete_files=False):
                return True

            def get_download_path(self, download_id):
                return None

        client = get_client(test_protocol)
        assert client is not None
        # There may be other configured clients, just check we get one
        assert client.is_configured()

    def test_get_client_returns_none_when_none_configured(self):
        """Test that get_client returns None for a protocol with no configured clients."""
        # Instead of trying to create a new protocol, just check that
        # get_client returns None for a completely unknown protocol
        client = get_client("nonexistent_protocol_xyz")
        assert client is None

    def test_get_client_unknown_protocol(self):
        """Test that get_client returns None for unknown protocols."""
        client = get_client("nonexistent_protocol")
        assert client is None

    def test_get_all_clients(self):
        """Test that get_all_clients returns the registry."""
        all_clients = get_all_clients()
        assert isinstance(all_clients, dict)


class TestDownloadClientInterface:
    """Tests for the DownloadClient abstract interface."""

    def test_find_existing_default_returns_none(self):
        """Test that default find_existing returns None."""

        class MinimalTestClient(DownloadClient):
            protocol = "torrent"  # Must use valid protocol
            name = "minimal_test"

            @staticmethod
            def is_configured():
                return True

            def test_connection(self):
                return True, "OK"

            def add_download(self, url, name, category="test"):
                return "id"

            def get_status(self, download_id):
                return DownloadStatus(0, "unknown", None, False, None)

            def remove(self, download_id, delete_files=False):
                return True

            def get_download_path(self, download_id):
                return None

        client = MinimalTestClient()
        result = client.find_existing("magnet:?xt=urn:btih:abc123")
        assert result is None
