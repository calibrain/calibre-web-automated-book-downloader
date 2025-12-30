"""
Unit tests for the Prowlarr download handler.

These tests mock the download clients to test the handler logic
without requiring running services.
"""

import os
import tempfile
from pathlib import Path
from threading import Event
from typing import List, Optional, Tuple
from unittest.mock import MagicMock, patch, PropertyMock
import pytest

from cwa_book_downloader.core.models import DownloadTask
from cwa_book_downloader.release_sources.prowlarr.handler import ProwlarrHandler
from cwa_book_downloader.release_sources.prowlarr.utils import get_protocol
from cwa_book_downloader.release_sources.prowlarr.clients import (
    DownloadStatus,
    DownloadState,
)


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

    def test_get_protocol_case_insensitive(self):
        """Test protocol detection is case insensitive."""
        assert get_protocol({"protocol": "TORRENT"}) == "torrent"
        assert get_protocol({"protocol": "Usenet"}) == "usenet"
        assert get_protocol({"protocol": "USENET"}) == "usenet"


class TestProwlarrHandlerDownloadErrors:
    """Tests for error handling in ProwlarrHandler.download()."""

    def test_download_fails_without_cached_release(self):
        """Test that download fails when release is not in cache."""
        with patch(
            "cwa_book_downloader.release_sources.prowlarr.handler.get_release",
            return_value=None,
        ):
            handler = ProwlarrHandler()
            task = DownloadTask(
                task_id="non-existent-id",
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
        with patch(
            "cwa_book_downloader.release_sources.prowlarr.handler.get_release",
            return_value={
                "protocol": "torrent",
                "title": "Test Release",
                # No downloadUrl or magnetUrl
            },
        ):
            handler = ProwlarrHandler()
            task = DownloadTask(
                task_id="no-url-release",
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

    def test_download_fails_unknown_protocol(self):
        """Test that download fails with unknown protocol."""
        with patch(
            "cwa_book_downloader.release_sources.prowlarr.handler.get_release",
            return_value={
                "protocol": "ftp",
                "downloadUrl": "ftp://example.com/file.zip",
            },
        ):
            handler = ProwlarrHandler()
            task = DownloadTask(
                task_id="unknown-protocol",
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
            assert "protocol" in recorder.last_message.lower()

    def test_download_fails_no_client_configured(self):
        """Test that download fails when no client is configured."""
        with patch(
            "cwa_book_downloader.release_sources.prowlarr.handler.get_release",
            return_value={
                "protocol": "torrent",
                "downloadUrl": "magnet:?xt=urn:btih:abc123",
            },
        ), patch(
            "cwa_book_downloader.release_sources.prowlarr.handler.get_client",
            return_value=None,
        ), patch(
            "cwa_book_downloader.release_sources.prowlarr.handler.list_configured_clients",
            return_value=[],
        ):
            handler = ProwlarrHandler()
            task = DownloadTask(
                task_id="no-client",
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
            assert "client" in recorder.last_message.lower()


class TestProwlarrHandlerExistingDownload:
    """Tests for handling existing downloads."""

    def test_uses_existing_complete_download(self):
        """Test that handler uses existing complete download."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            # Create a test file
            source_file = Path(tmp_dir) / "source" / "book.epub"
            source_file.parent.mkdir(parents=True)
            source_file.write_text("test content")

            staging_dir = Path(tmp_dir) / "staging"
            staging_dir.mkdir()

            mock_client = MagicMock()
            mock_client.name = "test_client"
            mock_client.find_existing.return_value = (
                "existing_id",
                DownloadStatus(
                    progress=100,
                    state=DownloadState.COMPLETE,
                    message="Complete",
                    complete=True,
                    file_path=str(source_file),
                ),
            )
            mock_client.get_download_path.return_value = str(source_file)

            with patch(
                "cwa_book_downloader.release_sources.prowlarr.handler.get_release",
                return_value={
                    "protocol": "torrent",
                    "magnetUrl": "magnet:?xt=urn:btih:abc123",
                },
            ), patch(
                "cwa_book_downloader.release_sources.prowlarr.handler.get_client",
                return_value=mock_client,
            ), patch(
                "cwa_book_downloader.release_sources.prowlarr.handler.remove_release",
            ), patch(
                "cwa_book_downloader.download.orchestrator.get_staging_dir",
                return_value=staging_dir,
            ):
                handler = ProwlarrHandler()
                task = DownloadTask(
                    task_id="existing-complete",
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

                assert result is not None
                assert "resolving" in recorder.statuses
                # Should NOT have called add_download
                mock_client.add_download.assert_not_called()


class TestProwlarrHandlerPolling:
    """Tests for download polling behavior."""

    def test_polls_until_complete(self):
        """Test that handler polls until download is complete."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            source_file = Path(tmp_dir) / "source" / "book.epub"
            source_file.parent.mkdir(parents=True)
            source_file.write_text("test content")

            staging_dir = Path(tmp_dir) / "staging"
            staging_dir.mkdir()

            poll_count = [0]

            def mock_get_status(download_id):
                poll_count[0] += 1
                if poll_count[0] >= 3:
                    return DownloadStatus(
                        progress=100,
                        state=DownloadState.COMPLETE,
                        message="Complete",
                        complete=True,
                        file_path=str(source_file),
                    )
                return DownloadStatus(
                    progress=poll_count[0] * 30,
                    state=DownloadState.DOWNLOADING,
                    message=None,
                    complete=False,
                    file_path=None,
                    download_speed=1024000,
                    eta=60,
                )

            mock_client = MagicMock()
            mock_client.name = "test_client"
            mock_client.find_existing.return_value = None
            mock_client.add_download.return_value = "download_id"
            mock_client.get_status.side_effect = mock_get_status
            mock_client.get_download_path.return_value = str(source_file)

            with patch(
                "cwa_book_downloader.release_sources.prowlarr.handler.get_release",
                return_value={
                    "protocol": "torrent",
                    "magnetUrl": "magnet:?xt=urn:btih:abc123",
                },
            ), patch(
                "cwa_book_downloader.release_sources.prowlarr.handler.get_client",
                return_value=mock_client,
            ), patch(
                "cwa_book_downloader.release_sources.prowlarr.handler.remove_release",
            ), patch(
                "cwa_book_downloader.download.orchestrator.get_staging_dir",
                return_value=staging_dir,
            ), patch(
                "cwa_book_downloader.release_sources.prowlarr.handler.POLL_INTERVAL",
                0.01,  # Speed up tests
            ):
                handler = ProwlarrHandler()
                task = DownloadTask(
                    task_id="poll-test",
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

                assert result is not None
                assert poll_count[0] >= 3
                assert len(recorder.progress_values) >= 3

    def test_handles_error_during_download(self):
        """Test that handler handles error state during download."""
        mock_client = MagicMock()
        mock_client.name = "test_client"
        mock_client.find_existing.return_value = None
        mock_client.add_download.return_value = "download_id"
        mock_client.get_status.return_value = DownloadStatus(
            progress=50,
            state=DownloadState.ERROR,
            message="Disk full",
            complete=False,
            file_path=None,
        )

        with patch(
            "cwa_book_downloader.release_sources.prowlarr.handler.get_release",
            return_value={
                "protocol": "torrent",
                "magnetUrl": "magnet:?xt=urn:btih:abc123",
            },
        ), patch(
            "cwa_book_downloader.release_sources.prowlarr.handler.get_client",
            return_value=mock_client,
        ), patch(
            "cwa_book_downloader.release_sources.prowlarr.handler.POLL_INTERVAL",
            0.01,
        ):
            handler = ProwlarrHandler()
            task = DownloadTask(
                task_id="error-test",
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
            mock_client.remove.assert_called_once()


class TestProwlarrHandlerCancellation:
    """Tests for download cancellation."""

    def test_cancellation_removes_download(self):
        """Test that cancellation removes the download from client."""
        mock_client = MagicMock()
        mock_client.name = "test_client"
        mock_client.find_existing.return_value = None
        mock_client.add_download.return_value = "download_id"
        mock_client.get_status.return_value = DownloadStatus(
            progress=50,
            state=DownloadState.DOWNLOADING,
            message="Downloading",
            complete=False,
            file_path=None,
        )

        with patch(
            "cwa_book_downloader.release_sources.prowlarr.handler.get_release",
            return_value={
                "protocol": "torrent",
                "magnetUrl": "magnet:?xt=urn:btih:abc123",
            },
        ), patch(
            "cwa_book_downloader.release_sources.prowlarr.handler.get_client",
            return_value=mock_client,
        ), patch(
            "cwa_book_downloader.release_sources.prowlarr.handler.POLL_INTERVAL",
            0.01,
        ):
            handler = ProwlarrHandler()
            task = DownloadTask(
                task_id="cancel-test",
                source="prowlarr",
                title="Test Book",
            )
            cancel_flag = Event()
            recorder = ProgressRecorder()

            # Set cancel immediately
            cancel_flag.set()

            result = handler.download(
                task=task,
                cancel_flag=cancel_flag,
                progress_callback=recorder.progress_callback,
                status_callback=recorder.status_callback,
            )

            assert result is None
            assert "cancelled" in recorder.statuses
            mock_client.remove.assert_called_with("download_id", delete_files=True)


class TestProwlarrHandlerCancel:
    """Tests for ProwlarrHandler.cancel()."""

    def test_cancel_removes_from_cache(self):
        """Test that cancel removes release from cache."""
        with patch(
            "cwa_book_downloader.release_sources.prowlarr.handler.remove_release"
        ) as mock_remove:
            handler = ProwlarrHandler()
            result = handler.cancel("test-task-id")

            assert result is True
            mock_remove.assert_called_once_with("test-task-id")

    def test_cancel_handles_missing_task(self):
        """Test that cancel handles non-existent task gracefully."""
        with patch(
            "cwa_book_downloader.release_sources.prowlarr.handler.remove_release"
        ):
            handler = ProwlarrHandler()
            result = handler.cancel("nonexistent-task-id")

            assert result is True


class TestProwlarrHandlerFileStaging:
    """Tests for file staging behavior."""

    def test_stages_single_file(self):
        """Test staging a single file download."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            source_file = Path(tmp_dir) / "source" / "book.epub"
            source_file.parent.mkdir(parents=True)
            source_file.write_text("test content")

            staging_dir = Path(tmp_dir) / "staging"
            staging_dir.mkdir()

            mock_client = MagicMock()
            mock_client.name = "test_client"
            mock_client.find_existing.return_value = None
            mock_client.add_download.return_value = "download_id"
            mock_client.get_status.return_value = DownloadStatus(
                progress=100,
                state=DownloadState.COMPLETE,
                message="Complete",
                complete=True,
                file_path=str(source_file),
            )
            mock_client.get_download_path.return_value = str(source_file)

            with patch(
                "cwa_book_downloader.release_sources.prowlarr.handler.get_release",
                return_value={
                    "protocol": "torrent",
                    "magnetUrl": "magnet:?xt=urn:btih:abc123",
                },
            ), patch(
                "cwa_book_downloader.release_sources.prowlarr.handler.get_client",
                return_value=mock_client,
            ), patch(
                "cwa_book_downloader.release_sources.prowlarr.handler.remove_release",
            ), patch(
                "cwa_book_downloader.download.orchestrator.get_staging_dir",
                return_value=staging_dir,
            ), patch(
                "cwa_book_downloader.release_sources.prowlarr.handler.POLL_INTERVAL",
                0.01,
            ):
                handler = ProwlarrHandler()
                task = DownloadTask(
                    task_id="staging-test",
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

                assert result is not None
                staged_file = Path(result)
                assert staged_file.exists()
                assert staged_file.read_text() == "test content"

    def test_stages_directory(self):
        """Test staging a directory download."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            source_dir = Path(tmp_dir) / "source" / "book_folder"
            source_dir.mkdir(parents=True)
            (source_dir / "book.epub").write_text("epub content")
            (source_dir / "cover.jpg").write_bytes(b"image data")

            staging_dir = Path(tmp_dir) / "staging"
            staging_dir.mkdir()

            mock_client = MagicMock()
            mock_client.name = "test_client"
            mock_client.find_existing.return_value = None
            mock_client.add_download.return_value = "download_id"
            mock_client.get_status.return_value = DownloadStatus(
                progress=100,
                state=DownloadState.COMPLETE,
                message="Complete",
                complete=True,
                file_path=str(source_dir),
            )
            mock_client.get_download_path.return_value = str(source_dir)

            with patch(
                "cwa_book_downloader.release_sources.prowlarr.handler.get_release",
                return_value={
                    "protocol": "torrent",
                    "magnetUrl": "magnet:?xt=urn:btih:abc123",
                },
            ), patch(
                "cwa_book_downloader.release_sources.prowlarr.handler.get_client",
                return_value=mock_client,
            ), patch(
                "cwa_book_downloader.release_sources.prowlarr.handler.remove_release",
            ), patch(
                "cwa_book_downloader.download.orchestrator.get_staging_dir",
                return_value=staging_dir,
            ), patch(
                "cwa_book_downloader.release_sources.prowlarr.handler.POLL_INTERVAL",
                0.01,
            ):
                handler = ProwlarrHandler()
                task = DownloadTask(
                    task_id="dir-staging-test",
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

                assert result is not None
                staged_dir = Path(result)
                assert staged_dir.is_dir()
                assert (staged_dir / "book.epub").exists()
                assert (staged_dir / "cover.jpg").exists()

    def test_handles_duplicate_filename(self):
        """Test handling of duplicate filename during staging."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            source_file = Path(tmp_dir) / "source" / "book.epub"
            source_file.parent.mkdir(parents=True)
            source_file.write_text("new content")

            staging_dir = Path(tmp_dir) / "staging"
            staging_dir.mkdir()
            # Create existing file with same name
            (staging_dir / "book.epub").write_text("old content")

            mock_client = MagicMock()
            mock_client.name = "test_client"
            mock_client.find_existing.return_value = None
            mock_client.add_download.return_value = "download_id"
            mock_client.get_status.return_value = DownloadStatus(
                progress=100,
                state=DownloadState.COMPLETE,
                message="Complete",
                complete=True,
                file_path=str(source_file),
            )
            mock_client.get_download_path.return_value = str(source_file)

            with patch(
                "cwa_book_downloader.release_sources.prowlarr.handler.get_release",
                return_value={
                    "protocol": "torrent",
                    "magnetUrl": "magnet:?xt=urn:btih:abc123",
                },
            ), patch(
                "cwa_book_downloader.release_sources.prowlarr.handler.get_client",
                return_value=mock_client,
            ), patch(
                "cwa_book_downloader.release_sources.prowlarr.handler.remove_release",
            ), patch(
                "cwa_book_downloader.download.orchestrator.get_staging_dir",
                return_value=staging_dir,
            ), patch(
                "cwa_book_downloader.release_sources.prowlarr.handler.POLL_INTERVAL",
                0.01,
            ):
                handler = ProwlarrHandler()
                task = DownloadTask(
                    task_id="dup-staging-test",
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

                assert result is not None
                # Should have a different name (with counter)
                staged_file = Path(result)
                assert staged_file.exists()
                assert staged_file.name != "book.epub"
                assert staged_file.read_text() == "new content"
