"""Tests for hardlinking and staging functionality.

Two approaches to preserve torrent files for seeding:

1. **Ingest mode**: Copy to staging → Move to ingest
   - Uses `stage_file(copy=True)` to preserve original
   - Less efficient (creates temp copy)

2. **Library mode with hardlink**: Hardlink from torrent path → library
   - Uses `_atomic_hardlink()` - same inode, no extra disk space
   - More efficient but requires same filesystem
"""

import os
import pytest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from cwa_book_downloader.core.naming import same_filesystem


class TestStageFile:
    """Tests for stage_file() - the ingest mode approach for torrents."""

    def test_copy_mode_preserves_original(self, tmp_path):
        """copy=True preserves original file (for torrent seeding)."""
        from cwa_book_downloader.download.orchestrator import stage_file, get_staging_dir

        source = tmp_path / "downloads" / "book.epub"
        source.parent.mkdir()
        source.write_bytes(b"content")

        with patch('cwa_book_downloader.download.orchestrator.TMP_DIR', tmp_path / "staging"):
            staged = stage_file(source, "task123", copy=True)

        assert staged.exists()
        assert source.exists()  # Original preserved
        assert staged.read_bytes() == b"content"

    def test_move_mode_removes_original(self, tmp_path):
        """copy=False moves file (original deleted)."""
        from cwa_book_downloader.download.orchestrator import stage_file

        source = tmp_path / "downloads" / "book.epub"
        source.parent.mkdir()
        source.write_bytes(b"content")

        with patch('cwa_book_downloader.download.orchestrator.TMP_DIR', tmp_path / "staging"):
            staged = stage_file(source, "task123", copy=False)

        assert staged.exists()
        assert not source.exists()  # Original deleted

    def test_handles_filename_collision(self, tmp_path):
        """Adds counter suffix on collision."""
        from cwa_book_downloader.download.orchestrator import stage_file

        staging = tmp_path / "staging"
        staging.mkdir()
        (staging / "book.epub").touch()  # Pre-existing file

        source = tmp_path / "downloads" / "book.epub"
        source.parent.mkdir()
        source.write_bytes(b"new content")

        with patch('cwa_book_downloader.download.orchestrator.TMP_DIR', staging):
            staged = stage_file(source, "task123", copy=True)

        assert staged.name == "book_1.epub"


class TestSameFilesystem:
    """Tests for same_filesystem() detection."""

    def test_same_directory(self, tmp_path):
        """Two paths in same temp directory are on same filesystem."""
        file1 = tmp_path / "file1.txt"
        file2 = tmp_path / "file2.txt"
        file1.touch()
        file2.touch()

        assert same_filesystem(file1, file2) is True

    def test_same_filesystem_different_dirs(self, tmp_path):
        """Subdirectories of same temp are on same filesystem."""
        dir1 = tmp_path / "dir1"
        dir2 = tmp_path / "dir2"
        dir1.mkdir()
        dir2.mkdir()

        assert same_filesystem(dir1, dir2) is True

    def test_nonexistent_paths_same_parent(self, tmp_path):
        """Non-existent paths with same parent are on same filesystem."""
        path1 = tmp_path / "nonexistent1"
        path2 = tmp_path / "nonexistent2"

        assert same_filesystem(path1, path2) is True

    def test_nonexistent_nested_paths(self, tmp_path):
        """Deeply nested non-existent paths check parent filesystem."""
        path1 = tmp_path / "a" / "b" / "c" / "file.txt"
        path2 = tmp_path / "x" / "y" / "z" / "file.txt"

        assert same_filesystem(path1, path2) is True

    def test_string_paths(self, tmp_path):
        """Accepts string paths as well as Path objects."""
        file1 = tmp_path / "file1.txt"
        file1.touch()

        assert same_filesystem(str(file1), str(tmp_path)) is True

    def test_permission_error_returns_false(self, tmp_path):
        """Returns False when permission denied (safe fallback)."""
        with patch('os.stat', side_effect=PermissionError("denied")):
            assert same_filesystem(tmp_path, tmp_path) is False

    def test_oserror_returns_false(self, tmp_path):
        """Returns False on OS errors (safe fallback)."""
        with patch('os.stat', side_effect=OSError("error")):
            assert same_filesystem(tmp_path, tmp_path) is False


class TestAtomicHardlink:
    """Tests for _atomic_hardlink() function."""

    def test_creates_hardlink(self, tmp_path):
        """Creates hardlink to source file."""
        from cwa_book_downloader.download.orchestrator import _atomic_hardlink

        source = tmp_path / "source.txt"
        source.write_text("content")
        dest = tmp_path / "dest.txt"

        result = _atomic_hardlink(source, dest)

        assert result == dest
        assert result.exists()
        assert result.read_text() == "content"
        # Verify it's a hardlink (same inode)
        assert os.stat(source).st_ino == os.stat(result).st_ino

    def test_handles_collision_with_counter(self, tmp_path):
        """Appends counter suffix when destination exists."""
        from cwa_book_downloader.download.orchestrator import _atomic_hardlink

        source = tmp_path / "source.txt"
        source.write_text("new content")
        dest = tmp_path / "dest.txt"
        dest.write_text("existing")

        result = _atomic_hardlink(source, dest)

        assert result == tmp_path / "dest_1.txt"
        assert result.read_text() == "new content"
        assert dest.read_text() == "existing"

    def test_multiple_collisions(self, tmp_path):
        """Increments counter until finding free slot."""
        from cwa_book_downloader.download.orchestrator import _atomic_hardlink

        source = tmp_path / "source.txt"
        source.write_text("new")
        (tmp_path / "dest.txt").touch()
        (tmp_path / "dest_1.txt").touch()
        (tmp_path / "dest_2.txt").touch()

        result = _atomic_hardlink(source, tmp_path / "dest.txt")

        assert result == tmp_path / "dest_3.txt"

    def test_preserves_extension(self, tmp_path):
        """Keeps extension when adding counter suffix."""
        from cwa_book_downloader.download.orchestrator import _atomic_hardlink

        source = tmp_path / "book.epub"
        source.write_bytes(b"epub content")
        (tmp_path / "book.epub").touch()

        result = _atomic_hardlink(source, tmp_path / "book.epub")

        assert result.suffix == ".epub"
        assert result.name == "book_1.epub"


class TestAtomicMove:
    """Tests for _atomic_move() function."""

    def test_moves_file(self, tmp_path):
        """Moves file from source to destination."""
        from cwa_book_downloader.download.orchestrator import _atomic_move

        source = tmp_path / "source.txt"
        source.write_text("content")
        dest = tmp_path / "dest.txt"

        result = _atomic_move(source, dest)

        assert result == dest
        assert not source.exists()
        assert result.read_text() == "content"

    def test_handles_collision(self, tmp_path):
        """Appends counter on collision."""
        from cwa_book_downloader.download.orchestrator import _atomic_move

        source = tmp_path / "source.txt"
        source.write_text("new")
        dest = tmp_path / "dest.txt"
        dest.write_text("existing")

        result = _atomic_move(source, dest)

        assert result == tmp_path / "dest_1.txt"
        assert not source.exists()
        assert dest.read_text() == "existing"
        assert result.read_text() == "new"

    def test_cross_filesystem_fallback(self):
        """Falls back to copy when cross-filesystem."""
        from cwa_book_downloader.download.orchestrator import _atomic_move
        import errno

        with tempfile.TemporaryDirectory() as dir1, tempfile.TemporaryDirectory() as dir2:
            source = Path(dir1) / "source.txt"
            source.write_text("content")
            dest = Path(dir2) / "dest.txt"

            # This should work even if dirs are on different filesystems
            # (uses fallback to copy)
            result = _atomic_move(source, dest)

            assert not source.exists()
            assert result.read_text() == "content"


class TestHardlinkWithLibraryMode:
    """Tests for hardlinking in library mode context."""

    @pytest.fixture
    def mock_config(self):
        """Mock config for library mode."""
        with patch('cwa_book_downloader.download.orchestrator.config') as mock:
            mock.get = MagicMock(side_effect=lambda key, default=None: {
                "LIBRARY_PATH": None,
                "LIBRARY_PATH_AUDIOBOOK": None,
                "LIBRARY_TEMPLATE": "{Author}/{Title}",
                "LIBRARY_TEMPLATE_AUDIOBOOK": "{Author}/{Title}",
                "TORRENT_HARDLINK": True,
                "PROCESSING_MODE": "library",
                "PROCESSING_MODE_AUDIOBOOK": "library",
            }.get(key, default))
            yield mock

    @pytest.fixture
    def sample_task(self):
        """Create a sample DownloadTask for testing."""
        from cwa_book_downloader.core.models import DownloadTask, SearchMode

        return DownloadTask(
            task_id="test123",
            source="prowlarr",
            title="The Way of Kings",
            author="Brandon Sanderson",
            format="epub",
            search_mode=SearchMode.UNIVERSAL,
        )

    def test_transfer_file_hardlink(self, tmp_path, sample_task):
        """Single file transferred via hardlink."""
        from cwa_book_downloader.download.orchestrator import _transfer_file_to_library

        library = tmp_path / "library"
        library.mkdir()
        source = tmp_path / "downloads" / "book.epub"
        source.parent.mkdir()
        source.write_bytes(b"epub content")
        temp_file = tmp_path / "staging" / "book.epub"
        temp_file.parent.mkdir()
        temp_file.write_bytes(b"staged content")

        status_cb = MagicMock()

        result = _transfer_file_to_library(
            source_path=source,
            library_base=str(library),
            template="{Author}/{Title}",
            metadata={"Author": "Brandon Sanderson", "Title": "Mistborn"},
            task=sample_task,
            temp_file=temp_file,
            status_callback=status_cb,
            use_hardlink=True,
        )

        assert result is not None
        result_path = Path(result)
        assert result_path.exists()
        assert result_path.parent.name == "Brandon Sanderson"
        assert result_path.name == "Mistborn.epub"
        # Source should still exist (hardlink)
        assert source.exists()
        # Temp file should be cleaned up
        assert not temp_file.exists()
        status_cb.assert_called_with("complete", "Complete")

    def test_transfer_file_move(self, tmp_path, sample_task):
        """Single file transferred via move."""
        from cwa_book_downloader.download.orchestrator import _transfer_file_to_library

        library = tmp_path / "library"
        library.mkdir()
        source = tmp_path / "staging" / "book.epub"
        source.parent.mkdir()
        source.write_bytes(b"epub content")

        status_cb = MagicMock()

        result = _transfer_file_to_library(
            source_path=source,
            library_base=str(library),
            template="{Author}/{Title}",
            metadata={"Author": "Brandon Sanderson", "Title": "Mistborn"},
            task=sample_task,
            temp_file=source,
            status_callback=status_cb,
            use_hardlink=False,
        )

        assert result is not None
        result_path = Path(result)
        assert result_path.exists()
        # Source should NOT exist (moved)
        assert not source.exists()
        status_cb.assert_called_with("complete", "Complete")

    def test_transfer_directory_hardlink_multifile(self, tmp_path, sample_task):
        """Directory with multiple files transferred via hardlinks."""
        from cwa_book_downloader.download.orchestrator import _transfer_directory_to_library

        library = tmp_path / "library"
        library.mkdir()
        source_dir = tmp_path / "downloads" / "audiobook"
        source_dir.mkdir(parents=True)

        # Create source audio files
        (source_dir / "Part 1.mp3").write_bytes(b"audio1")
        (source_dir / "Part 2.mp3").write_bytes(b"audio2")
        (source_dir / "Part 10.mp3").write_bytes(b"audio10")

        # Create temp staging dir
        temp_dir = tmp_path / "staging" / "audiobook"
        temp_dir.mkdir(parents=True)
        (temp_dir / "Part 1.mp3").write_bytes(b"staged1")
        (temp_dir / "Part 2.mp3").write_bytes(b"staged2")
        (temp_dir / "Part 10.mp3").write_bytes(b"staged10")

        sample_task.content_type = "audiobook"
        status_cb = MagicMock()

        with patch('cwa_book_downloader.download.orchestrator._get_supported_formats', return_value=["mp3"]):
            result = _transfer_directory_to_library(
                source_dir=source_dir,
                library_base=str(library),
                template="{Author}/{Title}{ - PartNumber}",  # Correct token format
                metadata={"Author": "Brandon Sanderson", "Title": "The Way of Kings"},
                task=sample_task,
                temp_file=temp_dir,
                status_callback=status_cb,
                use_hardlink=True,
            )

        assert result is not None
        result_path = Path(result)
        assert result_path.parent.name == "Brandon Sanderson"

        # Check all 3 files created with sequential part numbers
        author_dir = library / "Brandon Sanderson"
        files = sorted(author_dir.glob("*.mp3"))
        assert len(files) == 3
        assert files[0].name == "The Way of Kings - 01.mp3"
        assert files[1].name == "The Way of Kings - 02.mp3"
        assert files[2].name == "The Way of Kings - 03.mp3"

        # Source files should still exist (hardlinks)
        assert (source_dir / "Part 1.mp3").exists()
        assert (source_dir / "Part 2.mp3").exists()
        assert (source_dir / "Part 10.mp3").exists()

        # Temp dir should be cleaned up
        assert not temp_dir.exists()

    def test_transfer_directory_move(self, tmp_path, sample_task):
        """Directory transferred via move (non-torrent)."""
        from cwa_book_downloader.download.orchestrator import _transfer_directory_to_library

        library = tmp_path / "library"
        library.mkdir()
        source_dir = tmp_path / "staging" / "audiobook"
        source_dir.mkdir(parents=True)

        (source_dir / "Chapter 01.mp3").write_bytes(b"audio1")
        (source_dir / "Chapter 02.mp3").write_bytes(b"audio2")

        sample_task.content_type = "audiobook"
        status_cb = MagicMock()

        with patch('cwa_book_downloader.download.orchestrator._get_supported_formats', return_value=["mp3"]):
            result = _transfer_directory_to_library(
                source_dir=source_dir,
                library_base=str(library),
                template="{Author}/{Title}{ - Part PartNumber}",
                metadata={"Author": "Brandon Sanderson", "Title": "The Way of Kings"},
                task=sample_task,
                temp_file=source_dir,
                status_callback=status_cb,
                use_hardlink=False,
            )

        assert result is not None
        author_dir = library / "Brandon Sanderson"
        files = list(author_dir.glob("*.mp3"))
        assert len(files) == 2

        # Source dir should be cleaned up
        assert not source_dir.exists()

    def test_single_file_in_directory_no_part_number(self, tmp_path, sample_task):
        """Single file in directory doesn't get part number."""
        from cwa_book_downloader.download.orchestrator import _transfer_directory_to_library

        library = tmp_path / "library"
        library.mkdir()
        source_dir = tmp_path / "downloads" / "book"
        source_dir.mkdir(parents=True)
        (source_dir / "book.epub").write_bytes(b"content")

        temp_dir = tmp_path / "staging" / "book"
        temp_dir.mkdir(parents=True)
        (temp_dir / "book.epub").write_bytes(b"staged")

        status_cb = MagicMock()

        with patch('cwa_book_downloader.download.orchestrator._get_supported_formats', return_value=["epub"]):
            result = _transfer_directory_to_library(
                source_dir=source_dir,
                library_base=str(library),
                template="{Author}/{Title}{ - PartNumber}",  # Correct token format
                metadata={"Author": "Brandon Sanderson", "Title": "Mistborn"},
                task=sample_task,
                temp_file=temp_dir,
                status_callback=status_cb,
                use_hardlink=True,
            )

        result_path = Path(result)
        # Single file should NOT have part number (conditional prefix stripped)
        assert result_path.name == "Mistborn.epub"


class TestHardlinkDecisionLogic:
    """Tests for the decision to use hardlinks vs moves."""

    @pytest.fixture
    def sample_task(self):
        from cwa_book_downloader.core.models import DownloadTask, SearchMode

        return DownloadTask(
            task_id="test123",
            source="prowlarr",
            title="Test Book",
            author="Test Author",
            format="epub",
            search_mode=SearchMode.UNIVERSAL,
        )

    def test_hardlink_enabled_same_filesystem(self, tmp_path, sample_task):
        """Hardlink used when enabled and same filesystem."""
        from cwa_book_downloader.download.orchestrator import _process_organize_mode

        library = tmp_path / "library"
        library.mkdir()
        staging = tmp_path / "staging"
        staging.mkdir()
        source = tmp_path / "downloads" / "book.epub"
        source.parent.mkdir()
        source.write_bytes(b"content")
        staged = staging / "book.epub"
        staged.write_bytes(b"staged")

        # Task has original_download_path (torrent scenario)
        sample_task.original_download_path = str(source)

        status_cb = MagicMock()

        with patch('cwa_book_downloader.download.orchestrator.config') as mock_config:
            mock_config.get = MagicMock(side_effect=lambda key, default=None: {
                "LIBRARY_PATH": str(library),
                "LIBRARY_TEMPLATE": "{Author}/{Title}",
                "TORRENT_HARDLINK": True,
                "PROCESSING_MODE": "library",
            }.get(key, default))

            result = _process_organize_mode(staged, sample_task, status_cb)

        assert result is not None
        # Source should still exist (hardlinked)
        assert source.exists()

    def test_hardlink_disabled_falls_back_to_move(self, tmp_path, sample_task):
        """Move used when hardlink disabled in config."""
        from cwa_book_downloader.download.orchestrator import _process_organize_mode

        library = tmp_path / "library"
        library.mkdir()
        source = tmp_path / "downloads" / "book.epub"
        source.parent.mkdir()
        source.write_bytes(b"content")
        staged = tmp_path / "staging" / "book.epub"
        staged.parent.mkdir()
        staged.write_bytes(b"staged")

        sample_task.original_download_path = str(source)

        status_cb = MagicMock()

        with patch('cwa_book_downloader.download.orchestrator.config') as mock_config:
            mock_config.get = MagicMock(side_effect=lambda key, default=None: {
                "LIBRARY_PATH": str(library),
                "LIBRARY_TEMPLATE": "{Author}/{Title}",
                "TORRENT_HARDLINK": False,  # Disabled
                "PROCESSING_MODE": "library",
            }.get(key, default))

            result = _process_organize_mode(staged, sample_task, status_cb)

        assert result is not None
        # Staged file should be moved (not exist)
        assert not staged.exists()

    def test_no_original_path_uses_staging(self, tmp_path, sample_task):
        """Without original_download_path, moves from staging."""
        from cwa_book_downloader.download.orchestrator import _process_organize_mode

        library = tmp_path / "library"
        library.mkdir()
        staged = tmp_path / "staging" / "book.epub"
        staged.parent.mkdir()
        staged.write_bytes(b"content")

        # No original_download_path (direct download scenario)
        sample_task.original_download_path = None

        status_cb = MagicMock()

        with patch('cwa_book_downloader.download.orchestrator.config') as mock_config:
            mock_config.get = MagicMock(side_effect=lambda key, default=None: {
                "LIBRARY_PATH": str(library),
                "LIBRARY_TEMPLATE": "{Author}/{Title}",
                "TORRENT_HARDLINK": True,
                "PROCESSING_MODE": "library",
            }.get(key, default))

            result = _process_organize_mode(staged, sample_task, status_cb)

        assert result is not None
        # Staged file should be moved
        assert not staged.exists()


class TestHardlinkInodeVerification:
    """Tests that verify hardlinks share the same inode."""

    def test_hardlink_shares_inode(self, tmp_path):
        """Hardlinked files share same inode."""
        from cwa_book_downloader.download.orchestrator import _atomic_hardlink

        source = tmp_path / "source.txt"
        source.write_text("shared content")
        dest = tmp_path / "dest.txt"

        result = _atomic_hardlink(source, dest)

        source_inode = os.stat(source).st_ino
        dest_inode = os.stat(result).st_ino
        assert source_inode == dest_inode

    def test_hardlink_reflects_changes(self, tmp_path):
        """Changes to source reflect in hardlink."""
        from cwa_book_downloader.download.orchestrator import _atomic_hardlink

        source = tmp_path / "source.txt"
        source.write_text("original")
        dest = tmp_path / "dest.txt"

        result = _atomic_hardlink(source, dest)

        # Modify source
        source.write_text("modified")

        # Hardlink should see the change
        assert result.read_text() == "modified"

    def test_hardlink_count_increases(self, tmp_path):
        """Link count increases with each hardlink."""
        from cwa_book_downloader.download.orchestrator import _atomic_hardlink

        source = tmp_path / "source.txt"
        source.write_text("content")

        # Initial link count is 1
        assert os.stat(source).st_nlink == 1

        dest1 = _atomic_hardlink(source, tmp_path / "link1.txt")
        assert os.stat(source).st_nlink == 2

        dest2 = _atomic_hardlink(source, tmp_path / "link2.txt")
        assert os.stat(source).st_nlink == 3


class TestTorrentOptimization:
    """Tests for optimized torrent handling - skip staging when possible."""

    @pytest.fixture
    def sample_task(self):
        from cwa_book_downloader.core.models import DownloadTask, SearchMode

        return DownloadTask(
            task_id="test123",
            source="prowlarr",
            title="Test Book",
            author="Test Author",
            format="epub",
            search_mode=SearchMode.UNIVERSAL,
        )

    def test_is_torrent_source_true(self, tmp_path, sample_task):
        """Detects when source is the torrent client path."""
        from cwa_book_downloader.download.orchestrator import _is_torrent_source

        torrent_path = tmp_path / "downloads" / "book.epub"
        torrent_path.parent.mkdir()
        torrent_path.touch()
        sample_task.original_download_path = str(torrent_path)

        assert _is_torrent_source(torrent_path, sample_task) is True

    def test_is_torrent_source_false_no_original(self, tmp_path, sample_task):
        """Returns False when no original_download_path set."""
        from cwa_book_downloader.download.orchestrator import _is_torrent_source

        some_path = tmp_path / "staging" / "book.epub"
        sample_task.original_download_path = None

        assert _is_torrent_source(some_path, sample_task) is False

    def test_is_torrent_source_false_different_path(self, tmp_path, sample_task):
        """Returns False when paths don't match."""
        from cwa_book_downloader.download.orchestrator import _is_torrent_source

        torrent_path = tmp_path / "downloads" / "book.epub"
        staging_path = tmp_path / "staging" / "book.epub"
        sample_task.original_download_path = str(torrent_path)

        assert _is_torrent_source(staging_path, sample_task) is False

    def test_library_mode_torrent_no_hardlink_copies(self, tmp_path, sample_task):
        """Library mode copies (not moves) torrent files when hardlink unavailable."""
        from cwa_book_downloader.download.orchestrator import _transfer_file_to_library

        library = tmp_path / "library"
        library.mkdir()
        torrent_path = tmp_path / "downloads" / "book.epub"
        torrent_path.parent.mkdir()
        torrent_path.write_bytes(b"content")

        # Set up as torrent source
        sample_task.original_download_path = str(torrent_path)

        status_cb = MagicMock()

        result = _transfer_file_to_library(
            source_path=torrent_path,
            library_base=str(library),
            template="{Author}/{Title}",
            metadata={"Author": "Test Author", "Title": "Test Book"},
            task=sample_task,
            temp_file=torrent_path,
            status_callback=status_cb,
            use_hardlink=False,  # No hardlink
        )

        assert result is not None
        assert Path(result).exists()
        # Original should still exist (copied, not moved)
        assert torrent_path.exists()

    def test_library_mode_non_torrent_moves(self, tmp_path, sample_task):
        """Library mode moves (not copies) non-torrent files."""
        from cwa_book_downloader.download.orchestrator import _transfer_file_to_library

        library = tmp_path / "library"
        library.mkdir()
        staging_path = tmp_path / "staging" / "book.epub"
        staging_path.parent.mkdir()
        staging_path.write_bytes(b"content")

        # No original_download_path = not a torrent
        sample_task.original_download_path = None

        status_cb = MagicMock()

        result = _transfer_file_to_library(
            source_path=staging_path,
            library_base=str(library),
            template="{Author}/{Title}",
            metadata={"Author": "Test Author", "Title": "Test Book"},
            task=sample_task,
            temp_file=staging_path,
            status_callback=status_cb,
            use_hardlink=False,
        )

        assert result is not None
        assert Path(result).exists()
        # Original should be gone (moved)
        assert not staging_path.exists()

    def test_directory_torrent_copies_all_files(self, tmp_path, sample_task):
        """Multi-file torrent directory copies all files to library."""
        from cwa_book_downloader.download.orchestrator import _transfer_directory_to_library

        library = tmp_path / "library"
        library.mkdir()
        torrent_dir = tmp_path / "downloads" / "audiobook"
        torrent_dir.mkdir(parents=True)
        (torrent_dir / "part1.mp3").write_bytes(b"audio1")
        (torrent_dir / "part2.mp3").write_bytes(b"audio2")

        sample_task.original_download_path = str(torrent_dir)
        sample_task.content_type = "audiobook"
        status_cb = MagicMock()

        with patch('cwa_book_downloader.download.orchestrator._get_supported_formats', return_value=["mp3"]):
            result = _transfer_directory_to_library(
                source_dir=torrent_dir,
                library_base=str(library),
                template="{Author}/{Title}{ - PartNumber}",
                metadata={"Author": "Test Author", "Title": "Test Book"},
                task=sample_task,
                temp_file=torrent_dir,
                status_callback=status_cb,
                use_hardlink=False,
            )

        assert result is not None
        # Original files should still exist
        assert (torrent_dir / "part1.mp3").exists()
        assert (torrent_dir / "part2.mp3").exists()
        # Library files should exist
        author_dir = library / "Test Author"
        assert len(list(author_dir.glob("*.mp3"))) == 2


class TestEdgeCases:
    """Edge cases and error handling."""

    def test_empty_directory_returns_none(self, tmp_path):
        """Empty source directory returns None."""
        from cwa_book_downloader.download.orchestrator import _transfer_directory_to_library
        from cwa_book_downloader.core.models import DownloadTask, SearchMode

        task = DownloadTask(
            task_id="test",
            source="prowlarr",
            title="Test",
            author="Author",
            format="epub",
            search_mode=SearchMode.UNIVERSAL,
        )

        library = tmp_path / "library"
        library.mkdir()
        source_dir = tmp_path / "empty"
        source_dir.mkdir()

        status_cb = MagicMock()

        with patch('cwa_book_downloader.download.orchestrator._get_supported_formats', return_value=["epub"]):
            result = _transfer_directory_to_library(
                source_dir=source_dir,
                library_base=str(library),
                template="{Title}",
                metadata={"Title": "Test"},
                task=task,
                temp_file=source_dir,
                status_callback=status_cb,
                use_hardlink=False,
            )

        assert result is None

    def test_nonexistent_source_for_hardlink(self, tmp_path):
        """Missing source file prevents hardlink creation."""
        from cwa_book_downloader.download.orchestrator import _process_organize_mode
        from cwa_book_downloader.core.models import DownloadTask, SearchMode

        task = DownloadTask(
            task_id="test",
            source="prowlarr",
            title="Test",
            author="Author",
            format="epub",
            search_mode=SearchMode.UNIVERSAL,
            original_download_path=str(tmp_path / "nonexistent.epub"),
        )

        library = tmp_path / "library"
        library.mkdir()
        staged = tmp_path / "staging" / "book.epub"
        staged.parent.mkdir()
        staged.write_bytes(b"content")

        status_cb = MagicMock()

        with patch('cwa_book_downloader.download.orchestrator.config') as mock_config:
            mock_config.get = MagicMock(side_effect=lambda key, default=None: {
                "LIBRARY_PATH": str(library),
                "LIBRARY_TEMPLATE": "{Title}",
                "TORRENT_HARDLINK": True,
                "PROCESSING_MODE": "library",
            }.get(key, default))

            result = _process_organize_mode(staged, task, status_cb)

        # Should fall back to move since original doesn't exist
        assert result is not None
        assert not staged.exists()

    def test_permission_denied_library_path(self, tmp_path):
        """Handles permission denied on library path."""
        from cwa_book_downloader.download.orchestrator import _process_organize_mode
        from cwa_book_downloader.core.models import DownloadTask, SearchMode

        task = DownloadTask(
            task_id="test",
            source="prowlarr",
            title="Test",
            author="Author",
            format="epub",
            search_mode=SearchMode.UNIVERSAL,
        )

        staged = tmp_path / "staging" / "book.epub"
        staged.parent.mkdir()
        staged.write_bytes(b"content")

        status_cb = MagicMock()

        with patch('cwa_book_downloader.download.orchestrator.config') as mock_config:
            mock_config.get = MagicMock(side_effect=lambda key, default=None: {
                "LIBRARY_PATH": "/nonexistent/protected/path",
                "LIBRARY_TEMPLATE": "{Title}",
                "PROCESSING_MODE": "library",
            }.get(key, default))

            result = _process_organize_mode(staged, task, status_cb)

        # Should return None (fall back to ingest)
        assert result is None
