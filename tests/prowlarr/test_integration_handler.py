"""
Integration tests for the Prowlarr download handler.

These tests verify the end-to-end download flow through the Prowlarr handler.

Run with: docker exec test-cwabd python3 -m pytest /app/tests/prowlarr/test_integration_handler.py -v -m integration
"""

import time
from threading import Event
from typing import List, Optional, Tuple
import pytest

from cwa_book_downloader.core.config import config
from cwa_book_downloader.core.settings_registry import save_config_file
from cwa_book_downloader.core.models import DownloadTask
from cwa_book_downloader.release_sources.prowlarr.handler import ProwlarrHandler
from cwa_book_downloader.release_sources.prowlarr.utils import get_protocol
from cwa_book_downloader.release_sources.prowlarr.cache import cache_release, get_release, remove_release, _cache


# Test magnet link
TEST_MAGNET = "magnet:?xt=urn:btih:3b245504cf5f11bbdbe1201cea6a6bf45aee1bc0&dn=ubuntu-22.04.3-live-server-amd64.iso"


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


def _is_transmission_available():
    """Check if Transmission is available."""
    _setup_transmission_config()
    try:
        from cwa_book_downloader.release_sources.prowlarr.clients.transmission import TransmissionClient
        client = TransmissionClient()
        success, _ = client.test_connection()
        return success
    except Exception:
        return False


class ProgressRecorder:
    """Records progress and status updates during download."""

    def __init__(self):
        self.progress_values: List[float] = []
        self.status_updates: List[Tuple[str, Optional[str]]] = []

    def progress_callback(self, progress: float):
        self.progress_values.append(progress)

    def status_callback(self, status: str, message: Optional[str]):
        self.status_updates.append((status, message))

    @property
    def last_status(self) -> Optional[str]:
        return self.status_updates[-1][0] if self.status_updates else None

    @property
    def last_message(self) -> Optional[str]:
        return self.status_updates[-1][1] if self.status_updates else None

    @property
    def statuses(self) -> List[str]:
        return [s[0] for s in self.status_updates]


class TestGetProtocol:
    """Tests for the get_protocol function."""

    def test_get_protocol_torrent(self):
        """Test detecting torrent protocol."""
        result = {"protocol": "torrent"}
        assert get_protocol(result) == "torrent"

    def test_get_protocol_usenet(self):
        """Test detecting usenet protocol."""
        result = {"protocol": "usenet"}
        assert get_protocol(result) == "usenet"

    def test_get_protocol_unknown(self):
        """Test unknown protocol."""
        result = {"protocol": "ftp"}
        assert get_protocol(result) == "unknown"

    def test_get_protocol_empty(self):
        """Test empty protocol."""
        result = {}
        assert get_protocol(result) == "unknown"


@pytest.mark.integration
class TestHandlerCacheOperations:
    """Tests for handler cache-related behavior."""

    def test_download_fails_without_cached_release(self):
        """Test that download fails when release is not in cache."""
        _setup_transmission_config()
        handler = ProwlarrHandler()

        task = DownloadTask(
            task_id="non-existent-id-12345",
            source="prowlarr",
            title="Test Book",
        )
        cancel_flag = Event()
        recorder = ProgressRecorder()

        result = handler.download(
            task=task,
            cancel_flag=cancel_flag,
            progress_callback=recorder.progress_callback,
            status_callback=recorder.status_callback,
        )

        assert result is None
        assert recorder.last_status == "error"
        assert "cache" in recorder.last_message.lower()

    def test_download_fails_without_download_url(self):
        """Test that download fails when release has no download URL."""
        _setup_transmission_config()
        handler = ProwlarrHandler()

        task_id = "no-url-release-test"
        cache_release(task_id, {
            "protocol": "torrent",
            "title": "Test Release",
            # No downloadUrl or magnetUrl
        })

        try:
            task = DownloadTask(
                task_id=task_id,
                source="prowlarr",
                title="Test Book",
            )
            cancel_flag = Event()
            recorder = ProgressRecorder()

            result = handler.download(
                task=task,
                cancel_flag=cancel_flag,
                progress_callback=recorder.progress_callback,
                status_callback=recorder.status_callback,
            )

            assert result is None
            assert recorder.last_status == "error"
            assert "url" in recorder.last_message.lower()
        finally:
            remove_release(task_id)

    def test_cancel_removes_from_cache(self):
        """Test that cancel removes release from cache."""
        handler = ProwlarrHandler()

        task_id = "cancel-test-id-unique"
        cache_release(task_id, {"title": "Test"})

        assert get_release(task_id) is not None

        result = handler.cancel(task_id)

        assert result is True
        assert get_release(task_id) is None

    def test_cancel_handles_missing_task(self):
        """Test that cancel handles non-existent task gracefully."""
        handler = ProwlarrHandler()

        result = handler.cancel("definitely-non-existent-task-id")

        assert result is True  # Should still return True


@pytest.fixture(scope="module")
def transmission_available():
    """Check if Transmission is available, skip if not."""
    if not _is_transmission_available():
        pytest.skip("Transmission not available - ensure docker-compose.test-clients.yml is running")
    return True


@pytest.mark.integration
class TestProwlarrHandlerWithTransmission:
    """Integration tests for ProwlarrHandler with Transmission."""

    def test_download_starts_and_can_be_cancelled(self, transmission_available):
        """Test that download starts and can be cancelled."""
        _setup_transmission_config()
        handler = ProwlarrHandler()

        # Cache a valid release
        task_id = f"test-cancel-release-{time.time()}"
        cache_release(task_id, {
            "protocol": "torrent",
            "title": "Ubuntu Test ISO",
            "magnetUrl": TEST_MAGNET,
        })

        task = DownloadTask(
            task_id=task_id,
            source="prowlarr",
            title="Ubuntu Test ISO",
        )
        cancel_flag = Event()
        recorder = ProgressRecorder()

        # Start download in a thread and cancel after a short delay
        import threading

        def cancel_after_delay():
            time.sleep(4)  # Let it start
            cancel_flag.set()

        cancel_thread = threading.Thread(target=cancel_after_delay)
        cancel_thread.start()

        result = handler.download(
            task=task,
            cancel_flag=cancel_flag,
            progress_callback=recorder.progress_callback,
            status_callback=recorder.status_callback,
        )

        cancel_thread.join()

        # Download was cancelled
        assert result is None
        # Should have some status updates
        assert len(recorder.status_updates) > 0
        # Should see resolving or downloading status (not just error)
        assert "resolving" in recorder.statuses or "downloading" in recorder.statuses or "cancelled" in recorder.statuses

    def test_handler_sends_to_transmission(self, transmission_available):
        """Test that handler properly sends downloads to Transmission."""
        _setup_transmission_config()
        handler = ProwlarrHandler()

        task_id = f"transmission-test-{time.time()}"
        cache_release(task_id, {
            "protocol": "torrent",
            "title": "Integration Test Torrent",
            "magnetUrl": TEST_MAGNET,
        })

        task = DownloadTask(
            task_id=task_id,
            source="prowlarr",
            title="Integration Test Torrent",
        )
        cancel_flag = Event()
        recorder = ProgressRecorder()

        import threading

        def cancel_soon():
            time.sleep(5)
            cancel_flag.set()

        t = threading.Thread(target=cancel_soon)
        t.start()

        handler.download(
            task=task,
            cancel_flag=cancel_flag,
            progress_callback=recorder.progress_callback,
            status_callback=recorder.status_callback,
        )

        t.join()

        # Should have seen resolving status (means it tried to send to client)
        assert "resolving" in recorder.statuses or "downloading" in recorder.statuses
