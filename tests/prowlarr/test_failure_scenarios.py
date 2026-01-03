"""
Failure scenario tests for Prowlarr handler and download clients.

These tests verify error handling behavior - what happens when things go wrong.
They use real clients where possible, with injected failures for edge cases.

Run with: docker exec test-cwabd python3 -m pytest /app/tests/prowlarr/test_failure_scenarios.py -v
"""

import time
from pathlib import Path
from threading import Event, Thread
from typing import List, Optional, Tuple
from unittest.mock import MagicMock, patch, PropertyMock
import tempfile
import shutil

import pytest

from cwa_book_downloader.core.models import DownloadTask
from cwa_book_downloader.release_sources.prowlarr.handler import ProwlarrHandler
from cwa_book_downloader.release_sources.prowlarr.clients import (
    DownloadClient,
    DownloadState,
    DownloadStatus,
)


# =============================================================================
# Test Fixtures and Helpers
# =============================================================================


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

    @property
    def had_error(self) -> bool:
        return "error" in self.statuses


class MockClient(DownloadClient):
    """Configurable mock client for testing failure scenarios."""

    protocol = "torrent"
    name = "mock"

    def __init__(self):
        self.downloads = {}
        self.status_sequence = []  # List of DownloadStatus to return in order
        self.status_index = 0
        self.add_download_error = None  # Exception to raise on add_download
        self.get_status_error = None  # Exception to raise on get_status
        self.remove_called = False
        self.remove_with_delete = False

    @staticmethod
    def is_configured() -> bool:
        return True

    def test_connection(self) -> Tuple[bool, str]:
        return True, "Mock client connected"

    def add_download(self, url: str, name: str, category: str = "cwabd") -> str:
        if self.add_download_error:
            raise self.add_download_error
        download_id = f"mock-{len(self.downloads)}"
        self.downloads[download_id] = {"url": url, "name": name}
        return download_id

    def get_status(self, download_id: str) -> DownloadStatus:
        if self.get_status_error:
            raise self.get_status_error

        if self.status_sequence:
            if self.status_index < len(self.status_sequence):
                status = self.status_sequence[self.status_index]
                self.status_index += 1
                return status
            # Return last status if we've exhausted the sequence
            return self.status_sequence[-1]

        # Default: return downloading status
        return DownloadStatus(
            progress=50,
            state=DownloadState.DOWNLOADING,
            message=None,
            complete=False,
            file_path=None,
        )

    def remove(self, download_id: str, delete_files: bool = False) -> bool:
        self.remove_called = True
        self.remove_with_delete = delete_files
        return True

    def get_download_path(self, download_id: str) -> Optional[str]:
        return "/downloads/test-file.epub"

    def find_existing(self, url: str) -> Optional[Tuple[str, DownloadStatus]]:
        return None


@pytest.fixture
def handler():
    return ProwlarrHandler()


@pytest.fixture
def mock_client():
    return MockClient()


@pytest.fixture
def recorder():
    return ProgressRecorder()


@pytest.fixture
def cancel_flag():
    return Event()


@pytest.fixture
def sample_task():
    return DownloadTask(
        task_id="test-task-123",
        source="prowlarr",
        title="Test Book",
    )


@pytest.fixture
def sample_release():
    return {
        "guid": "test-task-123",
        "title": "Test Book",
        "downloadUrl": "magnet:?xt=urn:btih:abc123",
        "protocol": "torrent",
        "indexer": "TestIndexer",
    }


# =============================================================================
# Error State Tests - Client Reports Error
# =============================================================================


class TestClientErrorStates:
    """Tests for when download clients report error states."""

    def test_client_returns_error_state_during_download(
        self, handler, mock_client, recorder, cancel_flag, sample_task, sample_release
    ):
        """Handler should abort when client reports error state."""
        # Simulate: downloading -> downloading -> error
        mock_client.status_sequence = [
            DownloadStatus(
                progress=10,
                state=DownloadState.DOWNLOADING,
                message=None,
                complete=False,
                file_path=None,
            ),
            DownloadStatus(
                progress=25,
                state=DownloadState.DOWNLOADING,
                message=None,
                complete=False,
                file_path=None,
            ),
            DownloadStatus(
                progress=0,
                state=DownloadState.ERROR,
                message="Tracker returned error: torrent not found",
                complete=False,
                file_path=None,
            ),
        ]

        with patch(
            "cwa_book_downloader.release_sources.prowlarr.handler.get_release",
            return_value=sample_release,
        ), patch(
            "cwa_book_downloader.release_sources.prowlarr.handler.get_client",
            return_value=mock_client,
        ):
            result = handler.download(
                task=sample_task,
                cancel_flag=cancel_flag,
                progress_callback=recorder.progress_callback,
                status_callback=recorder.status_callback,
            )

        assert result is None
        assert recorder.had_error
        assert "Tracker returned error" in recorder.last_message
        assert mock_client.remove_called
        assert mock_client.remove_with_delete  # Should delete files on error

    def test_client_returns_error_with_complete_flag(
        self, handler, mock_client, recorder, cancel_flag, sample_task, sample_release
    ):
        """Edge case: complete=True but state=ERROR should be treated as error."""
        mock_client.status_sequence = [
            DownloadStatus(
                progress=100,
                state=DownloadState.ERROR,
                message="Download corrupted",
                complete=True,  # Complete but errored
                file_path=None,
            ),
        ]

        with patch(
            "cwa_book_downloader.release_sources.prowlarr.handler.get_release",
            return_value=sample_release,
        ), patch(
            "cwa_book_downloader.release_sources.prowlarr.handler.get_client",
            return_value=mock_client,
        ):
            result = handler.download(
                task=sample_task,
                cancel_flag=cancel_flag,
                progress_callback=recorder.progress_callback,
                status_callback=recorder.status_callback,
            )

        assert result is None
        assert recorder.had_error

    def test_error_without_message_uses_default(
        self, handler, mock_client, recorder, cancel_flag, sample_task, sample_release
    ):
        """Error state without message should use default error text."""
        mock_client.status_sequence = [
            DownloadStatus(
                progress=0,
                state=DownloadState.ERROR,
                message=None,  # No message
                complete=False,
                file_path=None,
            ),
        ]

        with patch(
            "cwa_book_downloader.release_sources.prowlarr.handler.get_release",
            return_value=sample_release,
        ), patch(
            "cwa_book_downloader.release_sources.prowlarr.handler.get_client",
            return_value=mock_client,
        ):
            result = handler.download(
                task=sample_task,
                cancel_flag=cancel_flag,
                progress_callback=recorder.progress_callback,
                status_callback=recorder.status_callback,
            )

        assert result is None
        assert recorder.had_error
        assert recorder.last_message == "Download failed"


# =============================================================================
# Connection/Network Failure Tests
# =============================================================================


class TestConnectionFailures:
    """Tests for network and connection failures."""

    def test_add_download_fails(
        self, handler, mock_client, recorder, cancel_flag, sample_task, sample_release
    ):
        """Handler should report error when add_download throws."""
        mock_client.add_download_error = ConnectionError("Connection refused")

        with patch(
            "cwa_book_downloader.release_sources.prowlarr.handler.get_release",
            return_value=sample_release,
        ), patch(
            "cwa_book_downloader.release_sources.prowlarr.handler.get_client",
            return_value=mock_client,
        ):
            result = handler.download(
                task=sample_task,
                cancel_flag=cancel_flag,
                progress_callback=recorder.progress_callback,
                status_callback=recorder.status_callback,
            )

        assert result is None
        assert recorder.had_error
        assert "Connection refused" in recorder.last_message

    def test_get_status_fails_during_poll(
        self, handler, mock_client, recorder, cancel_flag, sample_task, sample_release
    ):
        """Handler should recover or error gracefully when get_status throws."""
        call_count = 0

        def failing_get_status(download_id):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return DownloadStatus(
                    progress=call_count * 10,
                    state=DownloadState.DOWNLOADING,
                    message=None,
                    complete=False,
                    file_path=None,
                )
            raise ConnectionError("Client went away")

        mock_client.get_status = failing_get_status

        with patch(
            "cwa_book_downloader.release_sources.prowlarr.handler.get_release",
            return_value=sample_release,
        ), patch(
            "cwa_book_downloader.release_sources.prowlarr.handler.get_client",
            return_value=mock_client,
        ):
            result = handler.download(
                task=sample_task,
                cancel_flag=cancel_flag,
                progress_callback=recorder.progress_callback,
                status_callback=recorder.status_callback,
            )

        assert result is None
        assert recorder.had_error
        assert "Client went away" in recorder.last_message

    def test_no_client_configured(
        self, handler, recorder, cancel_flag, sample_task, sample_release
    ):
        """Handler should report helpful error when no client is configured."""
        with patch(
            "cwa_book_downloader.release_sources.prowlarr.handler.get_release",
            return_value=sample_release,
        ), patch(
            "cwa_book_downloader.release_sources.prowlarr.handler.get_client",
            return_value=None,
        ), patch(
            "cwa_book_downloader.release_sources.prowlarr.handler.list_configured_clients",
            return_value=[],
        ):
            result = handler.download(
                task=sample_task,
                cancel_flag=cancel_flag,
                progress_callback=recorder.progress_callback,
                status_callback=recorder.status_callback,
            )

        assert result is None
        assert recorder.had_error
        assert "No download clients configured" in recorder.last_message


# =============================================================================
# Cancellation Tests
# =============================================================================


class TestCancellation:
    """Tests for download cancellation behavior."""

    def test_cancel_during_download(
        self, handler, mock_client, recorder, sample_task, sample_release
    ):
        """Cancellation should stop download and cleanup."""
        cancel_flag = Event()

        # Status sequence that keeps downloading
        mock_client.status_sequence = [
            DownloadStatus(
                progress=i * 10,
                state=DownloadState.DOWNLOADING,
                message=None,
                complete=False,
                file_path=None,
            )
            for i in range(20)
        ]

        def cancel_after_delay():
            time.sleep(0.3)
            cancel_flag.set()

        cancel_thread = Thread(target=cancel_after_delay)
        cancel_thread.start()

        with patch(
            "cwa_book_downloader.release_sources.prowlarr.handler.get_release",
            return_value=sample_release,
        ), patch(
            "cwa_book_downloader.release_sources.prowlarr.handler.get_client",
            return_value=mock_client,
        ), patch(
            "cwa_book_downloader.release_sources.prowlarr.handler.POLL_INTERVAL",
            0.1,
        ):
            result = handler.download(
                task=sample_task,
                cancel_flag=cancel_flag,
                progress_callback=recorder.progress_callback,
                status_callback=recorder.status_callback,
            )

        cancel_thread.join()

        assert result is None
        assert "cancelled" in recorder.statuses
        assert mock_client.remove_called
        assert mock_client.remove_with_delete

    def test_cancel_before_download_starts(
        self, handler, mock_client, recorder, sample_task, sample_release
    ):
        """Pre-set cancel flag should abort immediately."""
        cancel_flag = Event()
        cancel_flag.set()  # Already cancelled

        with patch(
            "cwa_book_downloader.release_sources.prowlarr.handler.get_release",
            return_value=sample_release,
        ), patch(
            "cwa_book_downloader.release_sources.prowlarr.handler.get_client",
            return_value=mock_client,
        ):
            result = handler.download(
                task=sample_task,
                cancel_flag=cancel_flag,
                progress_callback=recorder.progress_callback,
                status_callback=recorder.status_callback,
            )

        assert result is None
        # Should have been cancelled quickly
        assert mock_client.remove_called


# =============================================================================
# Cache/Release Not Found Tests
# =============================================================================


class TestCacheFailures:
    """Tests for release cache failures."""

    def test_release_not_in_cache(self, handler, recorder, cancel_flag, sample_task):
        """Handler should error when release is not found in cache."""
        with patch(
            "cwa_book_downloader.release_sources.prowlarr.handler.get_release",
            return_value=None,
        ):
            result = handler.download(
                task=sample_task,
                cancel_flag=cancel_flag,
                progress_callback=recorder.progress_callback,
                status_callback=recorder.status_callback,
            )

        assert result is None
        assert recorder.had_error
        assert "not found in cache" in recorder.last_message.lower()

    def test_release_missing_download_url(
        self, handler, recorder, cancel_flag, sample_task
    ):
        """Handler should error when release has no download URL."""
        release_no_url = {
            "guid": "test-task-123",
            "title": "Test Book",
            # No downloadUrl or magnetUrl
            "protocol": "torrent",
        }

        with patch(
            "cwa_book_downloader.release_sources.prowlarr.handler.get_release",
            return_value=release_no_url,
        ):
            result = handler.download(
                task=sample_task,
                cancel_flag=cancel_flag,
                progress_callback=recorder.progress_callback,
                status_callback=recorder.status_callback,
            )

        assert result is None
        assert recorder.had_error
        assert "No download URL" in recorder.last_message

    def test_unknown_protocol(self, handler, recorder, cancel_flag, sample_task):
        """Handler should error on unknown protocol."""
        release_unknown_protocol = {
            "guid": "test-task-123",
            "title": "Test Book",
            "downloadUrl": "ftp://example.com/book.epub",
            "protocol": "ftp",
        }

        with patch(
            "cwa_book_downloader.release_sources.prowlarr.handler.get_release",
            return_value=release_unknown_protocol,
        ):
            result = handler.download(
                task=sample_task,
                cancel_flag=cancel_flag,
                progress_callback=recorder.progress_callback,
                status_callback=recorder.status_callback,
            )

        assert result is None
        assert recorder.had_error
        assert "protocol" in recorder.last_message.lower()


# =============================================================================
# File Handling Failures
# =============================================================================


class TestFileHandlingFailures:
    """Tests for file staging/copying failures."""

    def test_download_path_not_found(
        self, handler, mock_client, recorder, cancel_flag, sample_task, sample_release
    ):
        """Handler should error when completed file path is not available."""
        mock_client.status_sequence = [
            DownloadStatus(
                progress=100,
                state=DownloadState.COMPLETE,
                message=None,
                complete=True,
                file_path=None,
            ),
        ]
        mock_client.get_download_path = lambda x: None  # No path available

        with patch(
            "cwa_book_downloader.release_sources.prowlarr.handler.get_release",
            return_value=sample_release,
        ), patch(
            "cwa_book_downloader.release_sources.prowlarr.handler.get_client",
            return_value=mock_client,
        ):
            result = handler.download(
                task=sample_task,
                cancel_flag=cancel_flag,
                progress_callback=recorder.progress_callback,
                status_callback=recorder.status_callback,
            )

        assert result is None
        assert recorder.had_error
        assert "locate" in recorder.last_message.lower()

    def test_permission_denied_on_move(
        self, handler, mock_client, recorder, cancel_flag, sample_task
    ):
        """Handler should report permission errors during file staging (usenet only - torrents skip staging)."""
        # Use usenet protocol - torrents skip staging and return original path directly
        # Default usenet action is "move", so we mock shutil.move
        usenet_release = {
            "guid": "test-task-123",
            "title": "Test Book",
            "downloadUrl": "https://indexer.example.com/download/123",
            "protocol": "usenet",
            "indexer": "TestIndexer",
        }
        mock_client.status_sequence = [
            DownloadStatus(
                progress=100,
                state=DownloadState.COMPLETE,
                message=None,
                complete=True,
                file_path="/downloads/test.epub",
            ),
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            source_file = Path(tmpdir) / "source.epub"
            source_file.write_text("test content")
            staging_dir = Path(tmpdir) / "staging"
            staging_dir.mkdir()

            mock_client.get_download_path = lambda x: str(source_file)

            with patch(
                "cwa_book_downloader.release_sources.prowlarr.handler.get_release",
                return_value=usenet_release,
            ), patch(
                "cwa_book_downloader.release_sources.prowlarr.handler.get_client",
                return_value=mock_client,
            ), patch(
                "cwa_book_downloader.release_sources.prowlarr.handler.shutil.move",
                side_effect=PermissionError("Permission denied"),
            ), patch(
                "cwa_book_downloader.download.orchestrator.get_staging_dir",
                return_value=staging_dir,
            ):
                result = handler.download(
                    task=sample_task,
                    cancel_flag=cancel_flag,
                    progress_callback=recorder.progress_callback,
                    status_callback=recorder.status_callback,
                )

        assert result is None
        assert recorder.had_error
        assert "permission" in recorder.last_message.lower()


# =============================================================================
# Progress Callback Tests
# =============================================================================


class TestProgressCallbacks:
    """Tests for progress reporting behavior."""

    def test_progress_values_are_in_order(
        self, handler, mock_client, recorder, cancel_flag, sample_task, sample_release
    ):
        """Progress values should generally increase (allowing for client quirks)."""
        mock_client.status_sequence = [
            DownloadStatus(
                progress=i,
                state=DownloadState.DOWNLOADING if i < 100 else DownloadState.COMPLETE,
                message=None,
                complete=i >= 100,
                file_path="/downloads/test.epub" if i >= 100 else None,
            )
            for i in [0, 10, 25, 50, 75, 90, 100]
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            source_file = Path(tmpdir) / "test.epub"
            source_file.write_text("test content")
            staging_dir = Path(tmpdir) / "staging"
            staging_dir.mkdir()

            mock_client.get_download_path = lambda x: str(source_file)

            with patch(
                "cwa_book_downloader.release_sources.prowlarr.handler.get_release",
                return_value=sample_release,
            ), patch(
                "cwa_book_downloader.release_sources.prowlarr.handler.get_client",
                return_value=mock_client,
            ), patch(
                "cwa_book_downloader.download.orchestrator.get_staging_dir",
                return_value=staging_dir,
            ), patch(
                "cwa_book_downloader.release_sources.prowlarr.handler.POLL_INTERVAL",
                0.01,
            ):
                result = handler.download(
                    task=sample_task,
                    cancel_flag=cancel_flag,
                    progress_callback=recorder.progress_callback,
                    status_callback=recorder.status_callback,
                )

        assert result is not None
        assert recorder.progress_values == [0, 10, 25, 50, 75, 90, 100]

    def test_progress_clamps_to_valid_range(
        self, handler, mock_client, recorder, cancel_flag, sample_task, sample_release
    ):
        """DownloadStatus should clamp progress to 0-100."""
        # Test that invalid progress values are handled
        status = DownloadStatus(
            progress=150,  # Over 100
            state=DownloadState.DOWNLOADING,
            message=None,
            complete=False,
            file_path=None,
        )
        assert status.progress == 100

        status = DownloadStatus(
            progress=-10,  # Negative
            state=DownloadState.DOWNLOADING,
            message=None,
            complete=False,
            file_path=None,
        )
        assert status.progress == 0


# =============================================================================
# Status Message Tests
# =============================================================================


class TestStatusMessages:
    """Tests for status message formatting."""

    def test_status_includes_speed_and_eta(
        self, handler, mock_client, recorder, cancel_flag, sample_task, sample_release
    ):
        """Status message should include speed and ETA when available."""
        mock_client.status_sequence = [
            DownloadStatus(
                progress=50,
                state=DownloadState.DOWNLOADING,
                message=None,  # No custom message
                complete=False,
                file_path=None,
                download_speed=5 * 1024 * 1024,  # 5 MB/s
                eta=120,  # 2 minutes
            ),
            DownloadStatus(
                progress=100,
                state=DownloadState.COMPLETE,
                message=None,
                complete=True,
                file_path="/downloads/test.epub",
            ),
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            source_file = Path(tmpdir) / "test.epub"
            source_file.write_text("test content")
            staging_dir = Path(tmpdir) / "staging"
            staging_dir.mkdir()

            mock_client.get_download_path = lambda x: str(source_file)

            with patch(
                "cwa_book_downloader.release_sources.prowlarr.handler.get_release",
                return_value=sample_release,
            ), patch(
                "cwa_book_downloader.release_sources.prowlarr.handler.get_client",
                return_value=mock_client,
            ), patch(
                "cwa_book_downloader.download.orchestrator.get_staging_dir",
                return_value=staging_dir,
            ), patch(
                "cwa_book_downloader.release_sources.prowlarr.handler.POLL_INTERVAL",
                0.01,
            ):
                handler.download(
                    task=sample_task,
                    cancel_flag=cancel_flag,
                    progress_callback=recorder.progress_callback,
                    status_callback=recorder.status_callback,
                )

        # Find the downloading status message
        downloading_msgs = [
            msg for status, msg in recorder.status_updates if status == "downloading"
        ]
        assert len(downloading_msgs) > 0
        msg = downloading_msgs[0]
        assert "50%" in msg
        assert "MB/s" in msg
        assert "2m" in msg

    def test_client_message_takes_priority(
        self, handler, mock_client, recorder, cancel_flag, sample_task, sample_release
    ):
        """Client-provided message should override generated message."""
        mock_client.status_sequence = [
            DownloadStatus(
                progress=0,
                state=DownloadState.DOWNLOADING,
                message="Fetching metadata from peers",  # Custom message
                complete=False,
                file_path=None,
            ),
            DownloadStatus(
                progress=100,
                state=DownloadState.COMPLETE,
                message=None,
                complete=True,
                file_path="/downloads/test.epub",
            ),
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            source_file = Path(tmpdir) / "test.epub"
            source_file.write_text("test content")
            staging_dir = Path(tmpdir) / "staging"
            staging_dir.mkdir()

            mock_client.get_download_path = lambda x: str(source_file)

            with patch(
                "cwa_book_downloader.release_sources.prowlarr.handler.get_release",
                return_value=sample_release,
            ), patch(
                "cwa_book_downloader.release_sources.prowlarr.handler.get_client",
                return_value=mock_client,
            ), patch(
                "cwa_book_downloader.download.orchestrator.get_staging_dir",
                return_value=staging_dir,
            ), patch(
                "cwa_book_downloader.release_sources.prowlarr.handler.POLL_INTERVAL",
                0.01,
            ):
                handler.download(
                    task=sample_task,
                    cancel_flag=cancel_flag,
                    progress_callback=recorder.progress_callback,
                    status_callback=recorder.status_callback,
                )

        # Check that custom message was used
        downloading_msgs = [
            msg for status, msg in recorder.status_updates if status == "downloading"
        ]
        assert "Fetching metadata from peers" in downloading_msgs


# =============================================================================
# Cleanup After Error Tests
# =============================================================================


class TestErrorCleanup:
    """Tests verifying proper cleanup after errors."""

    def test_cleanup_on_poll_exception(
        self, handler, mock_client, recorder, cancel_flag, sample_task, sample_release
    ):
        """Download should be removed from client after polling exception."""
        call_count = 0

        def exploding_get_status(download_id):
            nonlocal call_count
            call_count += 1
            if call_count > 2:
                raise RuntimeError("Client crashed")
            return DownloadStatus(
                progress=10,
                state=DownloadState.DOWNLOADING,
                message=None,
                complete=False,
                file_path=None,
            )

        mock_client.get_status = exploding_get_status

        with patch(
            "cwa_book_downloader.release_sources.prowlarr.handler.get_release",
            return_value=sample_release,
        ), patch(
            "cwa_book_downloader.release_sources.prowlarr.handler.get_client",
            return_value=mock_client,
        ), patch(
            "cwa_book_downloader.release_sources.prowlarr.handler.POLL_INTERVAL",
            0.01,
        ):
            result = handler.download(
                task=sample_task,
                cancel_flag=cancel_flag,
                progress_callback=recorder.progress_callback,
                status_callback=recorder.status_callback,
            )

        assert result is None
        assert mock_client.remove_called
        assert mock_client.remove_with_delete

    def test_cleanup_continues_even_if_remove_fails(
        self, handler, mock_client, recorder, cancel_flag, sample_task, sample_release
    ):
        """Handler should not crash if cleanup removal fails."""
        mock_client.status_sequence = [
            DownloadStatus(
                progress=0,
                state=DownloadState.ERROR,
                message="Download failed",
                complete=False,
                file_path=None,
            ),
        ]

        def failing_remove(download_id, delete_files=False):
            raise ConnectionError("Client not responding")

        mock_client.remove = failing_remove

        with patch(
            "cwa_book_downloader.release_sources.prowlarr.handler.get_release",
            return_value=sample_release,
        ), patch(
            "cwa_book_downloader.release_sources.prowlarr.handler.get_client",
            return_value=mock_client,
        ):
            # Should not raise, even though remove() fails
            result = handler.download(
                task=sample_task,
                cancel_flag=cancel_flag,
                progress_callback=recorder.progress_callback,
                status_callback=recorder.status_callback,
            )

        assert result is None
        assert recorder.had_error
