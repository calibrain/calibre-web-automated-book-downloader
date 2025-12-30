"""
Integration tests for download clients.

These tests require the Docker test stack to be running:
    docker compose -f docker-compose.test-clients.yml up -d

Run with: docker exec test-cwabd python3 -m pytest /app/tests/prowlarr/test_integration_clients.py -v -m integration

These tests use the actual Docker stack configuration. Before running:
1. Start the test stack: docker compose -f docker-compose.test-clients.yml up -d
2. Configure clients via the cwabd UI at http://localhost:8084/settings
"""

import subprocess
import time
import pytest

from cwa_book_downloader.core.config import config
from cwa_book_downloader.core.settings_registry import save_config_file
from cwa_book_downloader.release_sources.prowlarr.clients import DownloadStatus


# Test magnet link (Ubuntu ISO - legal, small metadata)
TEST_MAGNET = "magnet:?xt=urn:btih:3b245504cf5f11bbdbe1201cea6a6bf45aee1bc0&dn=ubuntu-22.04.3-live-server-amd64.iso"


# ============ Configuration Setup Functions ============

def _setup_transmission_config():
    """Set up Transmission configuration via config files and refresh config."""
    save_config_file("prowlarr_clients", {
        "PROWLARR_TORRENT_CLIENT": "transmission",
        "TRANSMISSION_URL": "http://transmission:9091",
        "TRANSMISSION_USERNAME": "admin",
        "TRANSMISSION_PASSWORD": "admin",
        "TRANSMISSION_CATEGORY": "test",
    })
    config.refresh()


def _setup_qbittorrent_config():
    """Set up qBittorrent configuration via config files and refresh config."""
    save_config_file("prowlarr_clients", {
        "PROWLARR_TORRENT_CLIENT": "qbittorrent",
        "QBITTORRENT_URL": "http://qbittorrent:8080",
        "QBITTORRENT_USERNAME": "admin",
        "QBITTORRENT_PASSWORD": "admin123",
        "QBITTORRENT_CATEGORY": "test",
    })
    config.refresh()


def _setup_deluge_config():
    """Set up Deluge configuration via config files and refresh config."""
    save_config_file("prowlarr_clients", {
        "PROWLARR_TORRENT_CLIENT": "deluge",
        "DELUGE_HOST": "deluge",
        "DELUGE_PORT": "58846",
        "DELUGE_USERNAME": "admin",
        "DELUGE_PASSWORD": "admin",
        "DELUGE_CATEGORY": "test",
    })
    config.refresh()


def _setup_nzbget_config():
    """Set up NZBGet configuration via config files and refresh config."""
    save_config_file("prowlarr_clients", {
        "PROWLARR_USENET_CLIENT": "nzbget",
        "NZBGET_URL": "http://nzbget:6789",
        "NZBGET_USERNAME": "nzbget",
        "NZBGET_PASSWORD": "tegbzn6789",
        "NZBGET_CATEGORY": "test",
    })
    config.refresh()


def _setup_sabnzbd_config():
    """Set up SABnzbd configuration via config files and refresh config."""
    api_key = _get_sabnzbd_api_key()
    if not api_key:
        return False
    save_config_file("prowlarr_clients", {
        "PROWLARR_USENET_CLIENT": "sabnzbd",
        "SABNZBD_URL": "http://sabnzbd:8080",
        "SABNZBD_API_KEY": api_key,
        "SABNZBD_CATEGORY": "test",
    })
    config.refresh()
    return True


def _get_sabnzbd_api_key():
    """Extract SABnzbd API key from config file."""
    import re
    # Try mounted config paths (from docker-compose volumes)
    config_paths = [
        "/sabnzbd-config/sabnzbd.ini",
        "/config/sabnzbd.ini",
    ]
    for config_path in config_paths:
        try:
            with open(config_path, "r") as f:
                content = f.read()
                match = re.search(r"api_key\s*=\s*(\S+)", content)
                if match:
                    return match.group(1)
        except Exception:
            continue
    return None


# ============ Client Factory Functions ============

def _try_get_transmission_client():
    """Try to get a working Transmission client, or None if unavailable."""
    _setup_transmission_config()
    try:
        from cwa_book_downloader.release_sources.prowlarr.clients.transmission import TransmissionClient
        client = TransmissionClient()
        client.test_connection()
        return client
    except Exception:
        return None


def _try_get_qbittorrent_client():
    """Try to get a working qBittorrent client, or None if unavailable."""
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
    """Try to get a working Deluge client, or None if unavailable."""
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


def _try_get_nzbget_client():
    """Try to get a working NZBGet client, or None if unavailable."""
    _setup_nzbget_config()
    try:
        from cwa_book_downloader.release_sources.prowlarr.clients.nzbget import NZBGetClient
        client = NZBGetClient()
        client.test_connection()
        return client
    except Exception:
        return None


def _try_get_sabnzbd_client():
    """Try to get a working SABnzbd client, or None if unavailable."""
    if not _setup_sabnzbd_config():
        return None
    try:
        from cwa_book_downloader.release_sources.prowlarr.clients.sabnzbd import SABnzbdClient
        client = SABnzbdClient()
        success, _ = client.test_connection()
        if success:
            return client
    except Exception:
        pass
    return None


# ============ Fixtures ============

@pytest.fixture(scope="module")
def transmission_client():
    """Get Transmission client if available, skip test otherwise."""
    client = _try_get_transmission_client()
    if client is None:
        pytest.skip("Transmission not available - ensure docker-compose.test-clients.yml is running")
    return client


@pytest.fixture(scope="module")
def qbittorrent_client():
    """Get qBittorrent client if available, skip test otherwise."""
    client = _try_get_qbittorrent_client()
    if client is None:
        pytest.skip("qBittorrent not available - ensure docker-compose.test-clients.yml is running and check temp password")
    return client


@pytest.fixture(scope="module")
def deluge_client():
    """Get Deluge client if available, skip test otherwise."""
    client = _try_get_deluge_client()
    if client is None:
        pytest.skip("Deluge not available - ensure docker-compose.test-clients.yml is running")
    return client


@pytest.fixture(scope="module")
def nzbget_client():
    """Get NZBGet client if available, skip test otherwise."""
    client = _try_get_nzbget_client()
    if client is None:
        pytest.skip("NZBGet not available - ensure docker-compose.test-clients.yml is running")
    return client


@pytest.fixture(scope="module")
def sabnzbd_client():
    """Get SABnzbd client if available, skip test otherwise."""
    client = _try_get_sabnzbd_client()
    if client is None:
        pytest.skip("SABnzbd not available - ensure docker-compose.test-clients.yml is running and setup wizard completed")
    return client


@pytest.mark.integration
class TestTransmissionIntegration:
    """Integration tests for Transmission client.

    Uses the Docker test stack's Transmission instance (http://transmission:9091).
    """

    def test_test_connection(self, transmission_client):
        """Test connection to Transmission."""
        success, message = transmission_client.test_connection()

        assert success, f"Connection failed: {message}"
        assert "Transmission" in message

    def test_add_and_remove_torrent(self, transmission_client):
        """Test adding and removing a torrent."""
        client = transmission_client

        # Add torrent
        download_id = client.add_download(
            url=TEST_MAGNET,
            name="Test Ubuntu ISO",
        )

        assert download_id is not None

        # Wait a moment
        time.sleep(2)

        try:
            # Check status
            status = client.get_status(download_id)
            assert isinstance(status, DownloadStatus)
            assert status.progress >= 0
        finally:
            # Remove it
            result = client.remove(download_id, delete_files=True)
            assert result is True

    def test_find_existing_torrent(self, transmission_client):
        """Test finding an existing torrent."""
        client = transmission_client

        # Add torrent
        download_id = client.add_download(
            url=TEST_MAGNET,
            name="Test Ubuntu ISO Find",
        )
        time.sleep(2)

        try:
            result = client.find_existing(TEST_MAGNET)
            assert result is not None
            found_id, status = result
            assert found_id == download_id
            assert isinstance(status, DownloadStatus)
        finally:
            client.remove(download_id, delete_files=True)

    def test_status_fields(self, transmission_client):
        """Test that status contains all required fields."""
        client = transmission_client

        download_id = client.add_download(
            url=TEST_MAGNET,
            name="Test Status Fields",
        )
        time.sleep(2)

        try:
            status = client.get_status(download_id)

            # Check all required fields exist
            assert hasattr(status, "progress")
            assert hasattr(status, "state")
            assert hasattr(status, "message")
            assert hasattr(status, "complete")
            assert hasattr(status, "file_path")
            assert hasattr(status, "download_speed")
            assert hasattr(status, "eta")

            # Progress should be a number between 0 and 100
            assert 0 <= status.progress <= 100

            # State should be a known value
            valid_states = {"downloading", "complete", "error", "seeding", "paused", "queued", "fetching_metadata"}
            assert status.state in valid_states

            # Complete should be boolean
            assert isinstance(status.complete, bool)
        finally:
            client.remove(download_id, delete_files=True)


@pytest.mark.integration
class TestQBittorrentIntegration:
    """Integration tests for qBittorrent client.

    Uses the Docker test stack's qBittorrent instance (http://qbittorrent:8080).
    Note: qBittorrent generates a temporary password on startup.
    """

    def test_test_connection(self, qbittorrent_client):
        """Test connection to qBittorrent."""
        success, message = qbittorrent_client.test_connection()

        assert success, f"Connection failed: {message}"
        assert "qBittorrent" in message

    def test_add_and_remove_torrent(self, qbittorrent_client):
        """Test adding and removing a torrent."""
        client = qbittorrent_client

        download_id = client.add_download(
            url=TEST_MAGNET,
            name="Test Ubuntu ISO qBit",
        )

        assert download_id is not None

        time.sleep(3)  # qBittorrent needs a moment to process

        try:
            status = client.get_status(download_id)
            assert isinstance(status, DownloadStatus)
            assert status.progress >= 0
        finally:
            result = client.remove(download_id, delete_files=True)
            assert result is True

    def test_find_existing_torrent(self, qbittorrent_client):
        """Test finding an existing torrent."""
        client = qbittorrent_client

        download_id = client.add_download(
            url=TEST_MAGNET,
            name="Test Ubuntu ISO Find qBit",
        )
        time.sleep(3)

        try:
            result = client.find_existing(TEST_MAGNET)
            assert result is not None
            found_id, status = result
            assert found_id == download_id
            assert isinstance(status, DownloadStatus)
        finally:
            client.remove(download_id, delete_files=True)

    def test_status_fields(self, qbittorrent_client):
        """Test that status contains all required fields."""
        client = qbittorrent_client

        download_id = client.add_download(
            url=TEST_MAGNET,
            name="Test Status Fields qBit",
        )
        time.sleep(3)

        try:
            status = client.get_status(download_id)

            assert hasattr(status, "progress")
            assert hasattr(status, "state")
            assert hasattr(status, "message")
            assert hasattr(status, "complete")
            assert hasattr(status, "file_path")

            assert 0 <= status.progress <= 100

            valid_states = {"downloading", "complete", "error", "seeding", "paused", "queued", "fetching_metadata", "stalled"}
            assert status.state in valid_states

            assert isinstance(status.complete, bool)
        finally:
            client.remove(download_id, delete_files=True)


@pytest.mark.integration
class TestDelugeIntegration:
    """Integration tests for Deluge client.

    Uses the Docker test stack's Deluge instance (deluge:58846).
    Default credentials from auth file: admin/admin
    """

    def test_test_connection(self, deluge_client):
        """Test connection to Deluge."""
        success, message = deluge_client.test_connection()

        assert success, f"Connection failed: {message}"
        assert "Deluge" in message

    def test_add_and_remove_torrent(self, deluge_client):
        """Test adding and removing a torrent."""
        client = deluge_client

        download_id = client.add_download(
            url=TEST_MAGNET,
            name="Test Ubuntu ISO Deluge",
        )

        assert download_id is not None

        time.sleep(3)

        try:
            status = client.get_status(download_id)
            assert isinstance(status, DownloadStatus)
            assert status.progress >= 0
        finally:
            result = client.remove(download_id, delete_files=True)
            assert result is True

    def test_find_existing_torrent(self, deluge_client):
        """Test finding an existing torrent."""
        client = deluge_client

        download_id = client.add_download(
            url=TEST_MAGNET,
            name="Test Ubuntu ISO Find Deluge",
        )
        time.sleep(3)

        try:
            result = client.find_existing(TEST_MAGNET)
            assert result is not None
            found_id, status = result
            assert found_id == download_id
            assert isinstance(status, DownloadStatus)
        finally:
            client.remove(download_id, delete_files=True)

    def test_status_fields(self, deluge_client):
        """Test that status contains all required fields."""
        client = deluge_client

        download_id = client.add_download(
            url=TEST_MAGNET,
            name="Test Status Fields Deluge",
        )
        time.sleep(3)

        try:
            status = client.get_status(download_id)

            assert hasattr(status, "progress")
            assert hasattr(status, "state")
            assert hasattr(status, "message")
            assert hasattr(status, "complete")
            assert hasattr(status, "file_path")

            assert 0 <= status.progress <= 100

            valid_states = {"downloading", "complete", "error", "seeding", "paused", "queued", "fetching_metadata", "checking"}
            assert status.state in valid_states

            assert isinstance(status.complete, bool)
        finally:
            client.remove(download_id, delete_files=True)


@pytest.mark.integration
class TestNZBGetIntegration:
    """Integration tests for NZBGet client.

    Uses the Docker test stack's NZBGet instance (http://nzbget:6789).
    Default credentials: nzbget/tegbzn6789
    """

    def test_test_connection(self, nzbget_client):
        """Test connection to NZBGet."""
        success, message = nzbget_client.test_connection()

        assert success, f"Connection failed: {message}"
        assert "NZBGet" in message


@pytest.mark.integration
class TestSABnzbdIntegration:
    """Integration tests for SABnzbd client.

    Uses the Docker test stack's SABnzbd instance (http://sabnzbd:8080).
    Requires API key from config after setup wizard completion.
    """

    def test_test_connection(self, sabnzbd_client):
        """Test connection to SABnzbd."""
        success, message = sabnzbd_client.test_connection()

        assert success, f"Connection failed: {message}"
        assert "SABnzbd" in message
