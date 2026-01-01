"""Tests for download post-processing functionality.

Covers:
- _atomic_copy: atomic file copy with collision handling
- _post_process_download: main post-processing logic
- process_directory: directory processing with archive extraction
- Custom script execution
"""

import os
import pytest
import shutil
import tempfile
from pathlib import Path
from threading import Event
from unittest.mock import MagicMock, patch, call

from cwa_book_downloader.core.models import DownloadTask, SearchMode


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def sample_task():
    """Create a sample DownloadTask for testing."""
    return DownloadTask(
        task_id="test123",
        source="direct_download",
        title="The Way of Kings",
        author="Brandon Sanderson",
        format="epub",
        search_mode=SearchMode.UNIVERSAL,
    )


@pytest.fixture
def sample_direct_task():
    """Create a sample DownloadTask in Direct mode."""
    return DownloadTask(
        task_id="test123",
        source="direct_download",
        title="The Way of Kings",
        author="Brandon Sanderson",
        format="epub",
        search_mode=SearchMode.DIRECT,
    )


@pytest.fixture
def temp_dirs(tmp_path):
    """Create temp, staging, and ingest directories."""
    staging = tmp_path / "staging"
    ingest = tmp_path / "ingest"
    staging.mkdir()
    ingest.mkdir()
    return {
        "base": tmp_path,
        "staging": staging,
        "ingest": ingest,
    }


# =============================================================================
# _atomic_copy Tests
# =============================================================================

class TestAtomicCopy:
    """Tests for _atomic_copy() function."""

    def test_copies_file(self, tmp_path):
        """Copies file to destination."""
        from cwa_book_downloader.download.orchestrator import _atomic_copy

        source = tmp_path / "source.txt"
        source.write_text("content")
        dest = tmp_path / "dest.txt"

        result = _atomic_copy(source, dest)

        assert result == dest
        assert result.exists()
        assert result.read_text() == "content"
        # Source should still exist (copy, not move)
        assert source.exists()

    def test_preserves_source(self, tmp_path):
        """Source file is preserved after copy."""
        from cwa_book_downloader.download.orchestrator import _atomic_copy

        source = tmp_path / "source.txt"
        source.write_text("original content")
        dest = tmp_path / "dest.txt"

        _atomic_copy(source, dest)

        assert source.exists()
        assert source.read_text() == "original content"

    def test_handles_collision_with_counter(self, tmp_path):
        """Appends counter suffix when destination exists."""
        from cwa_book_downloader.download.orchestrator import _atomic_copy

        source = tmp_path / "source.txt"
        source.write_text("new content")
        dest = tmp_path / "dest.txt"
        dest.write_text("existing")

        result = _atomic_copy(source, dest)

        assert result == tmp_path / "dest_1.txt"
        assert result.read_text() == "new content"
        # Original destination preserved
        assert dest.read_text() == "existing"

    def test_multiple_collisions(self, tmp_path):
        """Increments counter until finding free slot."""
        from cwa_book_downloader.download.orchestrator import _atomic_copy

        source = tmp_path / "source.txt"
        source.write_text("new")
        (tmp_path / "dest.txt").touch()
        (tmp_path / "dest_1.txt").touch()
        (tmp_path / "dest_2.txt").touch()

        result = _atomic_copy(source, tmp_path / "dest.txt")

        assert result == tmp_path / "dest_3.txt"

    def test_preserves_extension(self, tmp_path):
        """Keeps extension when adding counter suffix."""
        from cwa_book_downloader.download.orchestrator import _atomic_copy

        source = tmp_path / "book.epub"
        source.write_bytes(b"epub content")
        (tmp_path / "book.epub").touch()

        result = _atomic_copy(source, tmp_path / "book.epub")

        assert result.suffix == ".epub"
        assert result.name == "book_1.epub"

    def test_creates_distinct_file(self, tmp_path):
        """Copy creates a distinct file (not hardlink)."""
        from cwa_book_downloader.download.orchestrator import _atomic_copy

        source = tmp_path / "source.txt"
        source.write_text("content")
        dest = tmp_path / "dest.txt"

        result = _atomic_copy(source, dest)

        # Should be different inodes (not a hardlink)
        assert os.stat(source).st_ino != os.stat(result).st_ino

    def test_copy_preserves_permissions(self, tmp_path):
        """Copy preserves file permissions (copy2 behavior)."""
        from cwa_book_downloader.download.orchestrator import _atomic_copy

        source = tmp_path / "source.txt"
        source.write_text("content")
        os.chmod(source, 0o644)

        dest = tmp_path / "dest.txt"
        result = _atomic_copy(source, dest)

        # Permissions should be preserved
        assert (os.stat(result).st_mode & 0o777) == 0o644

    def test_atomic_no_partial_file(self, tmp_path):
        """If copy fails, no partial file remains."""
        from cwa_book_downloader.download.orchestrator import _atomic_copy

        source = tmp_path / "source.txt"
        source.write_text("content")
        dest = tmp_path / "dest.txt"

        # Simulate shutil.copy2 failure mid-copy
        with patch('shutil.copy2', side_effect=IOError("Disk full")):
            with pytest.raises(IOError):
                _atomic_copy(source, dest)

        # No partial file should exist
        assert not dest.exists()

    def test_max_attempts_exceeded(self, tmp_path):
        """Raises after max collision attempts."""
        from cwa_book_downloader.download.orchestrator import _atomic_copy

        source = tmp_path / "source.txt"
        source.write_text("content")

        # Create 100 existing files
        for i in range(100):
            if i == 0:
                (tmp_path / "dest.txt").touch()
            else:
                (tmp_path / f"dest_{i}.txt").touch()

        with pytest.raises(RuntimeError, match="Could not copy file after 100 attempts"):
            _atomic_copy(source, tmp_path / "dest.txt", max_attempts=100)


# =============================================================================
# process_directory Tests
# =============================================================================

class TestProcessDirectory:
    """Tests for process_directory() function."""

    def test_finds_book_files(self, temp_dirs, sample_task):
        """Finds and moves book files to ingest."""
        from cwa_book_downloader.download.orchestrator import process_directory

        directory = temp_dirs["staging"] / "download"
        directory.mkdir()
        (directory / "book.epub").write_bytes(b"epub content")

        with patch('cwa_book_downloader.download.orchestrator.config') as mock_config:
            mock_config.USE_BOOK_TITLE = False
            mock_config.get = MagicMock(return_value=["epub"])

            final_paths, error = process_directory(
                directory=directory,
                ingest_dir=temp_dirs["ingest"],
                task=sample_task,
            )

        assert error is None
        assert len(final_paths) == 1
        assert final_paths[0].exists()
        assert final_paths[0].name == "book.epub"
        # Source directory cleaned up
        assert not directory.exists()

    def test_multiple_book_files(self, temp_dirs, sample_task):
        """Handles multiple book files in directory."""
        from cwa_book_downloader.download.orchestrator import process_directory

        directory = temp_dirs["staging"] / "download"
        directory.mkdir()
        (directory / "book1.epub").write_bytes(b"epub1")
        (directory / "book2.epub").write_bytes(b"epub2")

        with patch('cwa_book_downloader.download.orchestrator.config') as mock_config:
            mock_config.USE_BOOK_TITLE = False
            mock_config.get = MagicMock(return_value=["epub"])

            final_paths, error = process_directory(
                directory=directory,
                ingest_dir=temp_dirs["ingest"],
                task=sample_task,
            )

        assert error is None
        assert len(final_paths) == 2

    def test_no_book_files_returns_error(self, temp_dirs, sample_task):
        """Returns error when no book files found."""
        from cwa_book_downloader.download.orchestrator import process_directory

        directory = temp_dirs["staging"] / "download"
        directory.mkdir()
        # Use a file type that isn't trackable (not epub, pdf, txt, etc.)
        (directory / "readme.log").write_text("not a book")

        with patch('cwa_book_downloader.download.orchestrator.config') as mock_config:
            mock_config.get = MagicMock(return_value=["epub"])

            final_paths, error = process_directory(
                directory=directory,
                ingest_dir=temp_dirs["ingest"],
                task=sample_task,
            )

        assert final_paths == []
        assert error is not None
        assert "No book files found" in error

    def test_unsupported_format_error_message(self, temp_dirs, sample_task):
        """Returns helpful error when files exist but format unsupported."""
        from cwa_book_downloader.download.orchestrator import process_directory

        directory = temp_dirs["staging"] / "download"
        directory.mkdir()
        (directory / "book.pdf").write_bytes(b"pdf content")

        with patch('cwa_book_downloader.download.orchestrator.config') as mock_config:
            mock_config.get = MagicMock(return_value=["epub"])  # PDF not supported

            final_paths, error = process_directory(
                directory=directory,
                ingest_dir=temp_dirs["ingest"],
                task=sample_task,
            )

        assert final_paths == []
        assert "format not supported" in error
        assert ".pdf" in error

    def test_extracts_archive_when_no_books(self, temp_dirs, sample_task):
        """Extracts archives when no direct book files found."""
        from cwa_book_downloader.download.orchestrator import process_directory

        directory = temp_dirs["staging"] / "download"
        directory.mkdir()

        # Create a mock archive file
        archive = directory / "books.zip"
        archive.write_bytes(b"PK...")  # ZIP signature

        with patch('cwa_book_downloader.download.orchestrator.config') as mock_config, \
             patch('cwa_book_downloader.download.orchestrator.is_archive', return_value=True), \
             patch('cwa_book_downloader.download.orchestrator.process_archive') as mock_extract:

            mock_config.get = MagicMock(return_value=["epub"])

            # Mock successful extraction
            mock_result = MagicMock()
            mock_result.success = True
            mock_result.final_paths = [temp_dirs["ingest"] / "extracted.epub"]
            mock_result.error = None
            mock_extract.return_value = mock_result

            final_paths, error = process_directory(
                directory=directory,
                ingest_dir=temp_dirs["ingest"],
                task=sample_task,
            )

        assert error is None
        mock_extract.assert_called_once()

    def test_prefers_book_files_over_archives(self, temp_dirs, sample_task):
        """Uses book files directly when present, ignores archives."""
        from cwa_book_downloader.download.orchestrator import process_directory

        directory = temp_dirs["staging"] / "download"
        directory.mkdir()
        (directory / "book.epub").write_bytes(b"epub content")
        (directory / "extra.zip").write_bytes(b"PK...")  # Archive ignored

        with patch('cwa_book_downloader.download.orchestrator.config') as mock_config, \
             patch('cwa_book_downloader.download.orchestrator.process_archive') as mock_extract:

            mock_config.USE_BOOK_TITLE = False
            mock_config.get = MagicMock(return_value=["epub"])

            final_paths, error = process_directory(
                directory=directory,
                ingest_dir=temp_dirs["ingest"],
                task=sample_task,
            )

        assert error is None
        assert len(final_paths) == 1
        mock_extract.assert_not_called()

    def test_uses_book_title_for_single_file(self, temp_dirs, sample_task):
        """Uses formatted title for single file when USE_BOOK_TITLE enabled."""
        from cwa_book_downloader.download.orchestrator import process_directory

        directory = temp_dirs["staging"] / "download"
        directory.mkdir()
        (directory / "random_name.epub").write_bytes(b"content")

        with patch('cwa_book_downloader.download.orchestrator.config') as mock_config:
            mock_config.USE_BOOK_TITLE = True
            mock_config.get = MagicMock(return_value=["epub"])

            final_paths, error = process_directory(
                directory=directory,
                ingest_dir=temp_dirs["ingest"],
                task=sample_task,
            )

        assert error is None
        assert len(final_paths) == 1
        # Should use task title, not original filename
        assert "The Way of Kings" in final_paths[0].name

    def test_preserves_filenames_for_multifile(self, temp_dirs, sample_task):
        """Preserves original filenames for multi-file downloads."""
        from cwa_book_downloader.download.orchestrator import process_directory

        directory = temp_dirs["staging"] / "download"
        directory.mkdir()
        (directory / "Part 1.epub").write_bytes(b"part1")
        (directory / "Part 2.epub").write_bytes(b"part2")

        with patch('cwa_book_downloader.download.orchestrator.config') as mock_config:
            mock_config.USE_BOOK_TITLE = True  # Ignored for multi-file
            mock_config.get = MagicMock(return_value=["epub"])

            final_paths, error = process_directory(
                directory=directory,
                ingest_dir=temp_dirs["ingest"],
                task=sample_task,
            )

        assert error is None
        names = [p.name for p in final_paths]
        assert "Part 1.epub" in names
        assert "Part 2.epub" in names

    def test_nested_directory_files(self, temp_dirs, sample_task):
        """Finds book files in nested subdirectories."""
        from cwa_book_downloader.download.orchestrator import process_directory

        directory = temp_dirs["staging"] / "download"
        subdir = directory / "subdir"
        subdir.mkdir(parents=True)
        (subdir / "book.epub").write_bytes(b"content")

        with patch('cwa_book_downloader.download.orchestrator.config') as mock_config:
            mock_config.USE_BOOK_TITLE = False
            mock_config.get = MagicMock(return_value=["epub"])

            final_paths, error = process_directory(
                directory=directory,
                ingest_dir=temp_dirs["ingest"],
                task=sample_task,
            )

        assert error is None
        assert len(final_paths) == 1

    def test_cleans_up_on_error(self, temp_dirs, sample_task):
        """Cleans up directory even on error."""
        from cwa_book_downloader.download.orchestrator import process_directory

        directory = temp_dirs["staging"] / "download"
        directory.mkdir()
        (directory / "book.epub").write_bytes(b"content")

        with patch('cwa_book_downloader.download.orchestrator.config') as mock_config, \
             patch('cwa_book_downloader.download.orchestrator._atomic_move', side_effect=Exception("Move failed")):

            mock_config.USE_BOOK_TITLE = False
            mock_config.get = MagicMock(return_value=["epub"])

            final_paths, error = process_directory(
                directory=directory,
                ingest_dir=temp_dirs["ingest"],
                task=sample_task,
            )

        assert final_paths == []
        assert "Move failed" in error
        # Directory should be cleaned up
        assert not directory.exists()


# =============================================================================
# _post_process_download Tests
# =============================================================================

class TestPostProcessDownload:
    """Tests for _post_process_download() function."""

    def test_simple_file_move_to_ingest(self, temp_dirs, sample_direct_task):
        """Simple file is moved to ingest directory."""
        from cwa_book_downloader.download.orchestrator import _post_process_download

        temp_file = temp_dirs["staging"] / "book.epub"
        temp_file.write_bytes(b"epub content")

        status_cb = MagicMock()
        cancel_flag = Event()

        with patch('cwa_book_downloader.download.orchestrator.config') as mock_config, \
             patch('cwa_book_downloader.download.orchestrator.get_ingest_dir', return_value=temp_dirs["ingest"]):

            mock_config.USE_BOOK_TITLE = False
            mock_config.CUSTOM_SCRIPT = None
            mock_config.get = MagicMock(return_value=None)

            result = _post_process_download(
                temp_file=temp_file,
                task=sample_direct_task,
                cancel_flag=cancel_flag,
                status_callback=status_cb,
            )

        assert result is not None
        result_path = Path(result)
        assert result_path.exists()
        assert result_path.parent == temp_dirs["ingest"]
        assert not temp_file.exists()  # Moved
        status_cb.assert_called_with("complete", "Complete")

    def test_uses_formatted_filename(self, temp_dirs, sample_direct_task):
        """Uses task title when USE_BOOK_TITLE enabled."""
        from cwa_book_downloader.download.orchestrator import _post_process_download

        temp_file = temp_dirs["staging"] / "random.epub"
        temp_file.write_bytes(b"content")

        status_cb = MagicMock()
        cancel_flag = Event()

        with patch('cwa_book_downloader.download.orchestrator.config') as mock_config, \
             patch('cwa_book_downloader.download.orchestrator.get_ingest_dir', return_value=temp_dirs["ingest"]):

            mock_config.USE_BOOK_TITLE = True
            mock_config.CUSTOM_SCRIPT = None
            mock_config.get = MagicMock(return_value=None)

            result = _post_process_download(
                temp_file=temp_file,
                task=sample_direct_task,
                cancel_flag=cancel_flag,
                status_callback=status_cb,
            )

        result_path = Path(result)
        assert "The Way of Kings" in result_path.name

    def test_library_mode_for_universal(self, temp_dirs, sample_task):
        """Universal mode tries library mode when configured."""
        from cwa_book_downloader.download.orchestrator import _post_process_download

        library = temp_dirs["base"] / "library"
        library.mkdir()
        temp_file = temp_dirs["staging"] / "book.epub"
        temp_file.write_bytes(b"content")

        status_cb = MagicMock()
        cancel_flag = Event()

        with patch('cwa_book_downloader.download.orchestrator.config') as mock_config, \
             patch('cwa_book_downloader.download.orchestrator.get_ingest_dir', return_value=temp_dirs["ingest"]):

            mock_config.USE_BOOK_TITLE = True
            mock_config.CUSTOM_SCRIPT = None
            mock_config.get = MagicMock(side_effect=lambda key, default=None: {
                "PROCESSING_MODE": "library",
                "LIBRARY_PATH": str(library),
                "LIBRARY_TEMPLATE": "{Author}/{Title}",
            }.get(key, default))

            result = _post_process_download(
                temp_file=temp_file,
                task=sample_task,
                cancel_flag=cancel_flag,
                status_callback=status_cb,
            )

        assert result is not None
        result_path = Path(result)
        assert library in result_path.parents or result_path.parent == library
        status_cb.assert_called_with("complete", "Complete (library mode)")

    def test_direct_mode_skips_library(self, temp_dirs, sample_direct_task):
        """Direct mode skips library mode even when configured."""
        from cwa_book_downloader.download.orchestrator import _post_process_download

        library = temp_dirs["base"] / "library"
        library.mkdir()
        temp_file = temp_dirs["staging"] / "book.epub"
        temp_file.write_bytes(b"content")

        status_cb = MagicMock()
        cancel_flag = Event()

        with patch('cwa_book_downloader.download.orchestrator.config') as mock_config, \
             patch('cwa_book_downloader.download.orchestrator.get_ingest_dir', return_value=temp_dirs["ingest"]):

            mock_config.USE_BOOK_TITLE = False
            mock_config.CUSTOM_SCRIPT = None
            mock_config.get = MagicMock(side_effect=lambda key, default=None: {
                "PROCESSING_MODE": "library",  # Configured but ignored
                "LIBRARY_PATH": str(library),
            }.get(key, default))

            result = _post_process_download(
                temp_file=temp_file,
                task=sample_direct_task,
                cancel_flag=cancel_flag,
                status_callback=status_cb,
            )

        result_path = Path(result)
        # Should go to ingest, not library
        assert result_path.parent == temp_dirs["ingest"]

    def test_cancellation_before_ingest(self, temp_dirs, sample_direct_task):
        """Respects cancellation before final move."""
        from cwa_book_downloader.download.orchestrator import _post_process_download

        temp_file = temp_dirs["staging"] / "book.epub"
        temp_file.write_bytes(b"content")

        status_cb = MagicMock()
        cancel_flag = Event()
        cancel_flag.set()  # Already cancelled

        with patch('cwa_book_downloader.download.orchestrator.config') as mock_config, \
             patch('cwa_book_downloader.download.orchestrator.get_ingest_dir', return_value=temp_dirs["ingest"]):

            mock_config.USE_BOOK_TITLE = False
            mock_config.CUSTOM_SCRIPT = None
            mock_config.get = MagicMock(return_value=None)

            result = _post_process_download(
                temp_file=temp_file,
                task=sample_direct_task,
                cancel_flag=cancel_flag,
                status_callback=status_cb,
            )

        assert result is None
        # File should be cleaned up
        assert not temp_file.exists()

    def test_archive_extraction(self, temp_dirs, sample_direct_task):
        """Archives are extracted."""
        from cwa_book_downloader.download.orchestrator import _post_process_download

        archive = temp_dirs["staging"] / "book.zip"
        archive.write_bytes(b"PK...")

        status_cb = MagicMock()
        cancel_flag = Event()

        with patch('cwa_book_downloader.download.orchestrator.config') as mock_config, \
             patch('cwa_book_downloader.download.orchestrator.get_ingest_dir', return_value=temp_dirs["ingest"]), \
             patch('cwa_book_downloader.download.orchestrator.is_archive', return_value=True), \
             patch('cwa_book_downloader.download.orchestrator.process_archive') as mock_extract:

            mock_config.CUSTOM_SCRIPT = None
            mock_config.get = MagicMock(return_value=None)

            mock_result = MagicMock()
            mock_result.success = True
            mock_result.final_paths = [temp_dirs["ingest"] / "book.epub"]
            mock_result.message = "Extracted 1 file"
            mock_extract.return_value = mock_result

            result = _post_process_download(
                temp_file=archive,
                task=sample_direct_task,
                cancel_flag=cancel_flag,
                status_callback=status_cb,
            )

        assert result is not None
        mock_extract.assert_called_once()
        status_cb.assert_called_with("complete", "Extracted 1 file")

    def test_directory_processing(self, temp_dirs, sample_direct_task):
        """Directories are processed via process_directory."""
        from cwa_book_downloader.download.orchestrator import _post_process_download

        directory = temp_dirs["staging"] / "download"
        directory.mkdir()
        (directory / "book.epub").write_bytes(b"content")

        status_cb = MagicMock()
        cancel_flag = Event()

        with patch('cwa_book_downloader.download.orchestrator.config') as mock_config, \
             patch('cwa_book_downloader.download.orchestrator.get_ingest_dir', return_value=temp_dirs["ingest"]), \
             patch('cwa_book_downloader.download.orchestrator.is_archive', return_value=False):

            mock_config.USE_BOOK_TITLE = False
            mock_config.CUSTOM_SCRIPT = None
            mock_config.get = MagicMock(return_value=["epub"])

            result = _post_process_download(
                temp_file=directory,
                task=sample_direct_task,
                cancel_flag=cancel_flag,
                status_callback=status_cb,
            )

        assert result is not None
        status_cb.assert_called_with("complete", "Complete")

    def test_torrent_staging_for_ingest_mode(self, temp_dirs, sample_task):
        """Torrent files are copied to staging before ingest."""
        from cwa_book_downloader.download.orchestrator import _post_process_download

        # Simulate torrent client download location
        torrent_path = temp_dirs["base"] / "downloads" / "book.epub"
        torrent_path.parent.mkdir()
        torrent_path.write_bytes(b"content")

        sample_task.original_download_path = str(torrent_path)

        status_cb = MagicMock()
        cancel_flag = Event()

        with patch('cwa_book_downloader.download.orchestrator.config') as mock_config, \
             patch('cwa_book_downloader.download.orchestrator.get_ingest_dir', return_value=temp_dirs["ingest"]), \
             patch('cwa_book_downloader.download.orchestrator.get_staging_dir', return_value=temp_dirs["staging"]), \
             patch('cwa_book_downloader.download.orchestrator.is_archive', return_value=False):

            mock_config.USE_BOOK_TITLE = False
            mock_config.CUSTOM_SCRIPT = None
            mock_config.get = MagicMock(return_value=None)

            result = _post_process_download(
                temp_file=torrent_path,
                task=sample_task,
                cancel_flag=cancel_flag,
                status_callback=status_cb,
            )

        assert result is not None
        # Original torrent file should still exist
        assert torrent_path.exists()
        # Result should be in ingest
        assert Path(result).parent == temp_dirs["ingest"]

    def test_audiobook_uses_dedicated_ingest(self, temp_dirs, sample_task):
        """Audiobooks use dedicated ingest directory when configured."""
        from cwa_book_downloader.download.orchestrator import _post_process_download

        audiobook_ingest = temp_dirs["base"] / "audiobook_ingest"
        audiobook_ingest.mkdir()
        temp_file = temp_dirs["staging"] / "audiobook.mp3"
        temp_file.write_bytes(b"audio")

        sample_task.content_type = "audiobook"

        status_cb = MagicMock()
        cancel_flag = Event()

        with patch('cwa_book_downloader.download.orchestrator.config') as mock_config, \
             patch('cwa_book_downloader.download.orchestrator.get_ingest_dir', return_value=temp_dirs["ingest"]), \
             patch('cwa_book_downloader.download.orchestrator.is_archive', return_value=False):

            mock_config.USE_BOOK_TITLE = False
            mock_config.CUSTOM_SCRIPT = None
            mock_config.get = MagicMock(side_effect=lambda key, default=None: {
                "INGEST_DIR_AUDIOBOOK": str(audiobook_ingest),
            }.get(key, default))

            result = _post_process_download(
                temp_file=temp_file,
                task=sample_task,
                cancel_flag=cancel_flag,
                status_callback=status_cb,
            )

        result_path = Path(result)
        assert result_path.parent == audiobook_ingest


# =============================================================================
# Custom Script Execution Tests
# =============================================================================

class TestCustomScriptExecution:
    """Tests for custom script execution in post-processing."""

    def test_runs_custom_script(self, temp_dirs, sample_direct_task):
        """Runs custom script when configured."""
        from cwa_book_downloader.download.orchestrator import _post_process_download
        import subprocess

        temp_file = temp_dirs["staging"] / "book.epub"
        temp_file.write_bytes(b"content")

        status_cb = MagicMock()
        cancel_flag = Event()

        with patch('cwa_book_downloader.download.orchestrator.config') as mock_config, \
             patch('cwa_book_downloader.download.orchestrator.get_ingest_dir', return_value=temp_dirs["ingest"]), \
             patch('cwa_book_downloader.download.orchestrator.is_archive', return_value=False), \
             patch('subprocess.run') as mock_run:

            mock_config.USE_BOOK_TITLE = False
            mock_config.CUSTOM_SCRIPT = "/path/to/script.sh"
            mock_config.get = MagicMock(return_value=None)

            mock_run.return_value = MagicMock(stdout="", returncode=0)

            result = _post_process_download(
                temp_file=temp_file,
                task=sample_direct_task,
                cancel_flag=cancel_flag,
                status_callback=status_cb,
            )

        assert result is not None
        mock_run.assert_called_once()
        call_args = mock_run.call_args
        assert call_args[0][0] == ["/path/to/script.sh", str(temp_file)]

    def test_script_not_found_error(self, temp_dirs, sample_direct_task):
        """Returns error when script not found."""
        from cwa_book_downloader.download.orchestrator import _post_process_download

        temp_file = temp_dirs["staging"] / "book.epub"
        temp_file.write_bytes(b"content")

        status_cb = MagicMock()
        cancel_flag = Event()

        with patch('cwa_book_downloader.download.orchestrator.config') as mock_config, \
             patch('cwa_book_downloader.download.orchestrator.get_ingest_dir', return_value=temp_dirs["ingest"]), \
             patch('cwa_book_downloader.download.orchestrator.is_archive', return_value=False), \
             patch('subprocess.run', side_effect=FileNotFoundError("not found")):

            mock_config.USE_BOOK_TITLE = False
            mock_config.CUSTOM_SCRIPT = "/nonexistent/script.sh"
            mock_config.get = MagicMock(return_value=None)

            result = _post_process_download(
                temp_file=temp_file,
                task=sample_direct_task,
                cancel_flag=cancel_flag,
                status_callback=status_cb,
            )

        assert result is None
        status_cb.assert_called_with("error", "Custom script not found: /nonexistent/script.sh")

    def test_script_not_executable_error(self, temp_dirs, sample_direct_task):
        """Returns error when script not executable."""
        from cwa_book_downloader.download.orchestrator import _post_process_download

        temp_file = temp_dirs["staging"] / "book.epub"
        temp_file.write_bytes(b"content")

        status_cb = MagicMock()
        cancel_flag = Event()

        with patch('cwa_book_downloader.download.orchestrator.config') as mock_config, \
             patch('cwa_book_downloader.download.orchestrator.get_ingest_dir', return_value=temp_dirs["ingest"]), \
             patch('cwa_book_downloader.download.orchestrator.is_archive', return_value=False), \
             patch('subprocess.run', side_effect=PermissionError("not executable")):

            mock_config.USE_BOOK_TITLE = False
            mock_config.CUSTOM_SCRIPT = "/path/to/script.sh"
            mock_config.get = MagicMock(return_value=None)

            result = _post_process_download(
                temp_file=temp_file,
                task=sample_direct_task,
                cancel_flag=cancel_flag,
                status_callback=status_cb,
            )

        assert result is None
        status_cb.assert_called_with("error", "Custom script not executable: /path/to/script.sh")

    def test_script_timeout_error(self, temp_dirs, sample_direct_task):
        """Returns error when script times out."""
        from cwa_book_downloader.download.orchestrator import _post_process_download
        import subprocess

        temp_file = temp_dirs["staging"] / "book.epub"
        temp_file.write_bytes(b"content")

        status_cb = MagicMock()
        cancel_flag = Event()

        with patch('cwa_book_downloader.download.orchestrator.config') as mock_config, \
             patch('cwa_book_downloader.download.orchestrator.get_ingest_dir', return_value=temp_dirs["ingest"]), \
             patch('cwa_book_downloader.download.orchestrator.is_archive', return_value=False), \
             patch('subprocess.run', side_effect=subprocess.TimeoutExpired("script", 300)):

            mock_config.USE_BOOK_TITLE = False
            mock_config.CUSTOM_SCRIPT = "/path/to/script.sh"
            mock_config.get = MagicMock(return_value=None)

            result = _post_process_download(
                temp_file=temp_file,
                task=sample_direct_task,
                cancel_flag=cancel_flag,
                status_callback=status_cb,
            )

        assert result is None
        status_cb.assert_called_with("error", "Custom script timed out")

    def test_script_nonzero_exit_error(self, temp_dirs, sample_direct_task):
        """Returns error when script exits non-zero."""
        from cwa_book_downloader.download.orchestrator import _post_process_download
        import subprocess

        temp_file = temp_dirs["staging"] / "book.epub"
        temp_file.write_bytes(b"content")

        status_cb = MagicMock()
        cancel_flag = Event()

        with patch('cwa_book_downloader.download.orchestrator.config') as mock_config, \
             patch('cwa_book_downloader.download.orchestrator.get_ingest_dir', return_value=temp_dirs["ingest"]), \
             patch('cwa_book_downloader.download.orchestrator.is_archive', return_value=False), \
             patch('subprocess.run') as mock_run:

            mock_config.USE_BOOK_TITLE = False
            mock_config.CUSTOM_SCRIPT = "/path/to/script.sh"
            mock_config.get = MagicMock(return_value=None)

            error = subprocess.CalledProcessError(1, "script", stderr="Something failed")
            mock_run.side_effect = error

            result = _post_process_download(
                temp_file=temp_file,
                task=sample_direct_task,
                cancel_flag=cancel_flag,
                status_callback=status_cb,
            )

        assert result is None
        status_cb.assert_called_with("error", "Custom script failed: Something failed")


# =============================================================================
# Integration-style Tests
# =============================================================================

class TestDownloadProcessingIntegration:
    """Integration tests for the full download processing pipeline."""

    def test_full_direct_download_flow(self, temp_dirs, sample_direct_task):
        """Full flow: download → staging → ingest."""
        from cwa_book_downloader.download.orchestrator import _post_process_download

        # Simulate downloaded file
        temp_file = temp_dirs["staging"] / "download.epub"
        temp_file.write_bytes(b"epub content")

        status_cb = MagicMock()
        cancel_flag = Event()

        with patch('cwa_book_downloader.download.orchestrator.config') as mock_config, \
             patch('cwa_book_downloader.download.orchestrator.get_ingest_dir', return_value=temp_dirs["ingest"]):

            mock_config.USE_BOOK_TITLE = True
            mock_config.CUSTOM_SCRIPT = None
            mock_config.get = MagicMock(return_value=None)

            result = _post_process_download(
                temp_file=temp_file,
                task=sample_direct_task,
                cancel_flag=cancel_flag,
                status_callback=status_cb,
            )

        assert result is not None
        result_path = Path(result)

        # File is in ingest
        assert result_path.parent == temp_dirs["ingest"]
        # File has formatted name
        assert "The Way of Kings" in result_path.name
        # Staging file is gone
        assert not temp_file.exists()
        # Final status reported
        status_cb.assert_called_with("complete", "Complete")

    def test_full_universal_library_flow(self, temp_dirs, sample_task):
        """Full flow: download → library mode with organization."""
        from cwa_book_downloader.download.orchestrator import _post_process_download

        library = temp_dirs["base"] / "library"
        library.mkdir()
        temp_file = temp_dirs["staging"] / "book.epub"
        temp_file.write_bytes(b"content")

        status_cb = MagicMock()
        cancel_flag = Event()

        with patch('cwa_book_downloader.download.orchestrator.config') as mock_config, \
             patch('cwa_book_downloader.download.orchestrator.get_ingest_dir', return_value=temp_dirs["ingest"]):

            mock_config.USE_BOOK_TITLE = True
            mock_config.CUSTOM_SCRIPT = None
            mock_config.get = MagicMock(side_effect=lambda key, default=None: {
                "PROCESSING_MODE": "library",
                "LIBRARY_PATH": str(library),
                "LIBRARY_TEMPLATE": "{Author}/{Title}",
            }.get(key, default))

            result = _post_process_download(
                temp_file=temp_file,
                task=sample_task,
                cancel_flag=cancel_flag,
                status_callback=status_cb,
            )

        result_path = Path(result)

        # File is in library with author folder
        assert result_path.parent.name == "Brandon Sanderson"
        assert "The Way of Kings" in result_path.name
        # Staging file cleaned up
        assert not temp_file.exists()
        status_cb.assert_called_with("complete", "Complete (library mode)")

    def test_library_fallback_to_ingest(self, temp_dirs, sample_task):
        """Falls back to ingest when library mode fails."""
        from cwa_book_downloader.download.orchestrator import _post_process_download

        temp_file = temp_dirs["staging"] / "book.epub"
        temp_file.write_bytes(b"content")

        status_cb = MagicMock()
        cancel_flag = Event()

        with patch('cwa_book_downloader.download.orchestrator.config') as mock_config, \
             patch('cwa_book_downloader.download.orchestrator.get_ingest_dir', return_value=temp_dirs["ingest"]):

            mock_config.USE_BOOK_TITLE = False
            mock_config.CUSTOM_SCRIPT = None
            mock_config.get = MagicMock(side_effect=lambda key, default=None: {
                "PROCESSING_MODE": "library",
                "LIBRARY_PATH": None,  # Not configured
            }.get(key, default))

            result = _post_process_download(
                temp_file=temp_file,
                task=sample_task,
                cancel_flag=cancel_flag,
                status_callback=status_cb,
            )

        result_path = Path(result)
        # Falls back to ingest
        assert result_path.parent == temp_dirs["ingest"]
