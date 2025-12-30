"""
Integration failure tests for download clients.

These tests verify error handling behavior against REAL running clients.
They require the Docker test stack to be running.

Run with: docker exec test-cwabd python3 -m pytest /app/tests/prowlarr/test_integration_failures.py -v -m integration

Key scenarios tested:
- Invalid magnet links / bad torrents
- Non-existent download IDs
- Duplicate downloads
- Client disconnection recovery
"""

import time
import pytest

from cwa_book_downloader.core.config import config
from cwa_book_downloader.core.settings_registry import save_config_file
from cwa_book_downloader.release_sources.prowlarr.clients import DownloadStatus, DownloadState


# Invalid magnet - valid format but non-existent torrent
INVALID_MAGNET = "magnet:?xt=urn:btih:0000000000000000000000000000000000000000&dn=nonexistent"

# Valid Ubuntu magnet for comparison tests
VALID_MAGNET = "magnet:?xt=urn:btih:3b245504cf5f11bbdbe1201cea6a6bf45aee1bc0&dn=ubuntu-22.04.3-live-server-amd64.iso"


# =============================================================================
# Configuration Setup Functions (copied from test_integration_clients.py)
# =============================================================================


def _setup_transmission_config():
    save_config_file("prowlarr_clients", {
        "PROWLARR_TORRENT_CLIENT": "transmission",
        "TRANSMISSION_URL": "http://transmission:9091",
        "TRANSMISSION_USERNAME": "admin",
        "TRANSMISSION_PASSWORD": "admin",
        "TRANSMISSION_CATEGORY": "test",
    })
    config.refresh()


def _setup_qbittorrent_config():
    save_config_file("prowlarr_clients", {
        "PROWLARR_TORRENT_CLIENT": "qbittorrent",
        "QBITTORRENT_URL": "http://qbittorrent:8080",
        "QBITTORRENT_USERNAME": "admin",
        "QBITTORRENT_PASSWORD": "admin123",
        "QBITTORRENT_CATEGORY": "test",
    })
    config.refresh()


def _setup_deluge_config():
    save_config_file("prowlarr_clients", {
        "PROWLARR_TORRENT_CLIENT": "deluge",
        "DELUGE_HOST": "deluge",
        "DELUGE_PORT": "58846",
        "DELUGE_USERNAME": "admin",
        "DELUGE_PASSWORD": "admin",
        "DELUGE_CATEGORY": "test",
    })
    config.refresh()


# =============================================================================
# Client Factory Functions
# =============================================================================


def _try_get_transmission_client():
    _setup_transmission_config()
    try:
        from cwa_book_downloader.release_sources.prowlarr.clients.transmission import TransmissionClient
        client = TransmissionClient()
        client.test_connection()
        return client
    except Exception:
        return None


def _try_get_qbittorrent_client():
    _setup_qbittorrent_config()
    try:
        from cwa_book_downloader.release_sources.prowlarr.clients.qbittorrent import QBittorrentClient
        client = QBittorrentClient()
        success, _ = client.test_connection()
        if success:
            return client
    except Exception:
        pass
    return None


def _try_get_deluge_client():
    _setup_deluge_config()
    try:
        from cwa_book_downloader.release_sources.prowlarr.clients.deluge import DelugeClient
        client = DelugeClient()
        success, _ = client.test_connection()
        if success:
            return client
    except Exception:
        pass
    return None


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(scope="module")
def transmission_client():
    client = _try_get_transmission_client()
    if client is None:
        pytest.skip("Transmission not available")
    return client


@pytest.fixture(scope="module")
def qbittorrent_client():
    client = _try_get_qbittorrent_client()
    if client is None:
        pytest.skip("qBittorrent not available")
    return client


@pytest.fixture(scope="module")
def deluge_client():
    client = _try_get_deluge_client()
    if client is None:
        pytest.skip("Deluge not available")
    return client


# =============================================================================
# Non-Existent Download ID Tests
# =============================================================================


@pytest.mark.integration
class TestNonExistentDownloads:
    """Tests for handling non-existent download IDs."""

    def test_transmission_get_status_nonexistent(self, transmission_client):
        """Transmission should handle non-existent download ID gracefully."""
        status = transmission_client.get_status("nonexistent-hash-12345")

        # Should return an error status, not crash
        assert isinstance(status, DownloadStatus)
        assert status.state == DownloadState.ERROR or status.complete is False

    def test_transmission_remove_nonexistent(self, transmission_client):
        """Transmission should handle removing non-existent download."""
        # Should not raise, should return False or True depending on implementation
        result = transmission_client.remove("nonexistent-hash-12345", delete_files=True)
        # Just verify it doesn't crash - implementations vary on return value
        assert isinstance(result, bool)

    def test_transmission_get_path_nonexistent(self, transmission_client):
        """Transmission should return None for non-existent download path."""
        path = transmission_client.get_download_path("nonexistent-hash-12345")
        assert path is None

    def test_qbittorrent_get_status_nonexistent(self, qbittorrent_client):
        """qBittorrent should handle non-existent download ID gracefully."""
        status = qbittorrent_client.get_status("0" * 40)  # Valid hash format but nonexistent

        assert isinstance(status, DownloadStatus)
        assert status.state == DownloadState.ERROR or status.complete is False

    def test_qbittorrent_remove_nonexistent(self, qbittorrent_client):
        """qBittorrent should handle removing non-existent download."""
        result = qbittorrent_client.remove("0" * 40, delete_files=True)
        assert isinstance(result, bool)

    def test_deluge_get_status_nonexistent(self, deluge_client):
        """Deluge should handle non-existent download ID gracefully."""
        status = deluge_client.get_status("0" * 40)

        assert isinstance(status, DownloadStatus)
        assert status.state == DownloadState.ERROR or status.complete is False

    def test_deluge_remove_nonexistent(self, deluge_client):
        """Deluge should handle removing non-existent download."""
        result = deluge_client.remove("0" * 40, delete_files=True)
        assert isinstance(result, bool)


# =============================================================================
# Duplicate Download Tests
# =============================================================================


@pytest.mark.integration
class TestDuplicateDownloads:
    """Tests for handling duplicate download requests."""

    def test_transmission_add_duplicate(self, transmission_client):
        """Transmission should handle adding same torrent twice."""
        client = transmission_client

        # Add first time
        download_id_1 = client.add_download(
            url=VALID_MAGNET,
            name="Duplicate Test 1",
        )
        time.sleep(2)

        try:
            # Add same magnet again - should either:
            # 1. Return the same ID (deduplicated)
            # 2. Raise an exception
            # 3. Return a new ID (some clients allow this)
            try:
                download_id_2 = client.add_download(
                    url=VALID_MAGNET,
                    name="Duplicate Test 2",
                )
                # If we get here, client allowed it
                # Clean up the duplicate if different
                if download_id_2 != download_id_1:
                    client.remove(download_id_2, delete_files=True)
            except Exception as e:
                # Expected - client rejected duplicate
                assert "duplicate" in str(e).lower() or "exists" in str(e).lower()
        finally:
            client.remove(download_id_1, delete_files=True)

    def test_qbittorrent_add_duplicate(self, qbittorrent_client):
        """qBittorrent should handle adding same torrent twice."""
        client = qbittorrent_client

        download_id_1 = client.add_download(
            url=VALID_MAGNET,
            name="Duplicate Test qBit 1",
        )
        time.sleep(3)

        try:
            try:
                download_id_2 = client.add_download(
                    url=VALID_MAGNET,
                    name="Duplicate Test qBit 2",
                )
                if download_id_2 != download_id_1:
                    client.remove(download_id_2, delete_files=True)
            except Exception:
                # Expected behavior
                pass
        finally:
            client.remove(download_id_1, delete_files=True)


# =============================================================================
# Invalid URL/Magnet Tests
# =============================================================================


@pytest.mark.integration
class TestInvalidUrls:
    """Tests for handling invalid URLs and magnets."""

    def test_transmission_completely_invalid_url(self, transmission_client):
        """Transmission should reject completely invalid URL."""
        with pytest.raises(Exception):
            transmission_client.add_download(
                url="not-a-valid-url-at-all",
                name="Invalid URL Test",
            )

    def test_transmission_malformed_magnet(self, transmission_client):
        """Transmission should reject malformed magnet link."""
        with pytest.raises(Exception):
            transmission_client.add_download(
                url="magnet:?xt=invalid",
                name="Malformed Magnet Test",
            )

    def test_qbittorrent_completely_invalid_url(self, qbittorrent_client):
        """qBittorrent should reject completely invalid URL."""
        with pytest.raises(Exception):
            qbittorrent_client.add_download(
                url="not-a-valid-url-at-all",
                name="Invalid URL Test",
            )

    def test_deluge_completely_invalid_url(self, deluge_client):
        """Deluge should reject completely invalid URL."""
        with pytest.raises(Exception):
            deluge_client.add_download(
                url="not-a-valid-url-at-all",
                name="Invalid URL Test",
            )


# =============================================================================
# find_existing Edge Cases
# =============================================================================


@pytest.mark.integration
class TestFindExistingEdgeCases:
    """Tests for find_existing behavior in edge cases."""

    def test_transmission_find_nonexistent(self, transmission_client):
        """find_existing should return None for non-existent torrent."""
        result = transmission_client.find_existing(INVALID_MAGNET)
        assert result is None

    def test_qbittorrent_find_nonexistent(self, qbittorrent_client):
        """find_existing should return None for non-existent torrent."""
        result = qbittorrent_client.find_existing(INVALID_MAGNET)
        assert result is None

    def test_deluge_find_nonexistent(self, deluge_client):
        """find_existing should return None for non-existent torrent."""
        result = deluge_client.find_existing(INVALID_MAGNET)
        assert result is None

    def test_transmission_find_with_invalid_url(self, transmission_client):
        """find_existing should handle invalid URL gracefully."""
        # Should return None, not crash
        result = transmission_client.find_existing("not-a-magnet-link")
        assert result is None

    def test_qbittorrent_find_with_invalid_url(self, qbittorrent_client):
        """find_existing should handle invalid URL gracefully."""
        result = qbittorrent_client.find_existing("not-a-magnet-link")
        assert result is None


# =============================================================================
# Connection Loss Simulation (where possible)
# =============================================================================


@pytest.mark.integration
class TestConnectionResilience:
    """Tests for connection resilience."""

    def test_transmission_recovers_after_session_id_change(self, transmission_client):
        """Transmission should handle session ID invalidation."""
        # First, make a successful call to establish session
        transmission_client.test_connection()

        # Manually invalidate the session ID if accessible
        if hasattr(transmission_client, '_session_id'):
            old_session = transmission_client._session_id
            transmission_client._session_id = "invalid-session-id"

            # Should auto-recover with a new session
            success, msg = transmission_client.test_connection()

            # Restore or verify it got a new one
            assert success or transmission_client._session_id != "invalid-session-id"

    def test_qbittorrent_handles_expired_cookie(self, qbittorrent_client):
        """qBittorrent should handle expired session cookie."""
        # First, make a successful call
        qbittorrent_client.test_connection()

        # Clear the session if accessible
        if hasattr(qbittorrent_client, '_session'):
            qbittorrent_client._session.cookies.clear()

        # Should re-authenticate automatically
        success, msg = qbittorrent_client.test_connection()
        assert success


# =============================================================================
# State Transition Tests
# =============================================================================


@pytest.mark.integration
class TestStateTransitions:
    """Tests verifying correct state reporting during download lifecycle."""

    def test_transmission_initial_state_is_valid(self, transmission_client):
        """Newly added torrent should have valid initial state."""
        client = transmission_client

        download_id = client.add_download(
            url=VALID_MAGNET,
            name="State Test",
        )

        try:
            # Check immediately
            status = client.get_status(download_id)

            assert isinstance(status, DownloadStatus)
            assert 0 <= status.progress <= 100
            assert isinstance(status.complete, bool)

            # State should be one of the expected values
            valid_states = {
                DownloadState.DOWNLOADING,
                DownloadState.QUEUED,
                DownloadState.CHECKING,
                DownloadState.PAUSED,
                "downloading",
                "queued",
                "checking",
                "fetching_metadata",
            }
            assert status.state in valid_states or isinstance(status.state, DownloadState)
        finally:
            client.remove(download_id, delete_files=True)

    def test_qbittorrent_initial_state_is_valid(self, qbittorrent_client):
        """Newly added torrent should have valid initial state."""
        client = qbittorrent_client

        download_id = client.add_download(
            url=VALID_MAGNET,
            name="State Test qBit",
        )

        try:
            time.sleep(2)  # qBittorrent needs a moment
            status = client.get_status(download_id)

            assert isinstance(status, DownloadStatus)
            assert 0 <= status.progress <= 100
            assert isinstance(status.complete, bool)
        finally:
            client.remove(download_id, delete_files=True)

    def test_transmission_reports_metadata_fetching(self, transmission_client):
        """Transmission should report metadata fetching for magnet links."""
        client = transmission_client

        # Use the invalid magnet - it will stay in metadata fetching state
        download_id = client.add_download(
            url=INVALID_MAGNET,
            name="Metadata Test",
        )

        try:
            time.sleep(2)
            status = client.get_status(download_id)

            # For an invalid magnet, it should either:
            # 1. Be stuck in metadata fetching
            # 2. Have an error
            # 3. Show 0% progress
            assert status.progress == 0 or status.state == DownloadState.ERROR
        finally:
            client.remove(download_id, delete_files=True)


# =============================================================================
# Timeout Behavior Tests
# =============================================================================


@pytest.mark.integration
@pytest.mark.slow
class TestTimeoutBehavior:
    """Tests for timeout and stall detection."""

    def test_transmission_stalled_torrent_state(self, transmission_client):
        """Verify stalled torrent is reported correctly."""
        client = transmission_client

        # Add invalid magnet - will never find peers
        download_id = client.add_download(
            url=INVALID_MAGNET,
            name="Stall Test",
        )

        try:
            # Wait a bit for it to "stall"
            time.sleep(5)

            status = client.get_status(download_id)

            # Should still be at 0% or have stalled status
            assert status.progress == 0
            # State could be downloading (but no progress) or specific stall state
            assert not status.complete
        finally:
            client.remove(download_id, delete_files=True)
