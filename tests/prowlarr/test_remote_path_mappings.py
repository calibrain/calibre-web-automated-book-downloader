"""Tests for remote path mappings.

This focuses on integration of mapping logic into the Prowlarr handler.
"""

import errno
import tempfile
from pathlib import Path
from threading import Event
from unittest.mock import MagicMock, patch

from shelfmark.core.models import DownloadTask
from shelfmark.download.clients import DownloadState, DownloadStatus
from shelfmark.release_sources.prowlarr.handler import ProwlarrHandler


class ProgressRecorder:
    def __init__(self):
        self.progress_values = []
        self.status_updates = []

    def progress_callback(self, progress: float):
        self.progress_values.append(progress)

    def status_callback(self, status: str, message: str | None):
        self.status_updates.append((status, message))


def test_remaps_completed_path_when_remote_path_missing():
    with tempfile.TemporaryDirectory() as tmp_dir:
        local_file = Path(tmp_dir) / "local" / "book.epub"
        local_file.parent.mkdir(parents=True)
        local_file.write_text("test content")

        remote_path = "/remote/downloads/book.epub"

        mock_client = MagicMock()
        mock_client.name = "qbittorrent"
        mock_client.find_existing.return_value = None
        mock_client.add_download.return_value = "download_id"
        mock_client.get_status.return_value = DownloadStatus(
            progress=100,
            state=DownloadState.COMPLETE,
            message="Complete",
            complete=True,
            file_path=remote_path,
        )
        mock_client.get_download_path.return_value = remote_path

        def config_get(key: str, default=""):
            if key == "PROWLARR_REMOTE_PATH_MAPPINGS":
                return [
                    {
                        "host": "qbittorrent",
                        "remotePath": "/remote/downloads",
                        "localPath": str(local_file.parent),
                    }
                ]
            return default

        with (
            patch(
                "shelfmark.release_sources.prowlarr.handler.get_release",
                return_value={
                    "protocol": "torrent",
                    "magnetUrl": "magnet:?xt=urn:btih:abc123",
                },
            ),
            patch(
                "shelfmark.release_sources.prowlarr.handler.get_client",
                return_value=mock_client,
            ),
            patch(
                "shelfmark.release_sources.prowlarr.handler.remove_release",
            ),
            patch(
                "shelfmark.release_sources.prowlarr.handler.config.get",
                side_effect=config_get,
            ),
            patch(
                "shelfmark.release_sources.prowlarr.handler.POLL_INTERVAL",
                0.01,
            ),
        ):
            handler = ProwlarrHandler()
            task = DownloadTask(task_id="poll-mapping-test", source="prowlarr", title="Test Book")
            cancel_flag = Event()
            recorder = ProgressRecorder()

            result = handler.download(
                task=task,
                cancel_flag=cancel_flag,
                progress_callback=recorder.progress_callback,
                status_callback=recorder.status_callback,
            )

            assert result == str(local_file)
            assert task.original_download_path == str(local_file)


def test_remap_prefers_mapping_when_original_exists():
    with tempfile.TemporaryDirectory() as tmp_dir:
        remote_dir = Path(tmp_dir) / "remote" / "downloads"
        remote_dir.mkdir(parents=True)
        remote_file = remote_dir / "book.epub"
        remote_file.write_text("remote content")

        local_dir = Path(tmp_dir) / "local" / "downloads"
        local_dir.mkdir(parents=True)
        local_file = local_dir / "book.epub"
        local_file.write_text("local content")

        remote_path = str(remote_file)

        mock_client = MagicMock()
        mock_client.name = "qbittorrent"
        mock_client.find_existing.return_value = None
        mock_client.add_download.return_value = "download_id"
        mock_client.get_status.return_value = DownloadStatus(
            progress=100,
            state=DownloadState.COMPLETE,
            message="Complete",
            complete=True,
            file_path=remote_path,
        )
        mock_client.get_download_path.return_value = remote_path

        def config_get(key: str, default=""):
            if key == "PROWLARR_REMOTE_PATH_MAPPINGS":
                return [
                    {
                        "host": "qbittorrent",
                        "remotePath": str(remote_dir),
                        "localPath": str(local_dir),
                    }
                ]
            return default

        with (
            patch(
                "shelfmark.release_sources.prowlarr.handler.get_release",
                return_value={
                    "protocol": "torrent",
                    "magnetUrl": "magnet:?xt=urn:btih:abc123",
                },
            ),
            patch(
                "shelfmark.release_sources.prowlarr.handler.get_client",
                return_value=mock_client,
            ),
            patch(
                "shelfmark.release_sources.prowlarr.handler.remove_release",
            ),
            patch(
                "shelfmark.release_sources.prowlarr.handler.config.get",
                side_effect=config_get,
            ),
            patch(
                "shelfmark.release_sources.prowlarr.handler.POLL_INTERVAL",
                0.01,
            ),
        ):
            handler = ProwlarrHandler()
            task = DownloadTask(task_id="poll-mapping-prefer", source="prowlarr", title="Test Book")
            cancel_flag = Event()
            recorder = ProgressRecorder()

            result = handler.download(
                task=task,
                cancel_flag=cancel_flag,
                progress_callback=recorder.progress_callback,
                status_callback=recorder.status_callback,
            )

            assert result == str(local_file)
            assert task.original_download_path == str(local_file)


def test_remap_fails_when_mapping_exists_but_path_missing():
    with tempfile.TemporaryDirectory() as tmp_dir:
        remote_dir = Path(tmp_dir) / "remote" / "downloads"
        remote_dir.mkdir(parents=True)
        remote_file = remote_dir / "book.epub"
        remote_file.write_text("remote content")

        local_dir = Path(tmp_dir) / "local" / "downloads"
        local_dir.mkdir(parents=True)
        local_file = local_dir / "book.epub"
        remote_path = str(remote_file)

        mock_client = MagicMock()
        mock_client.name = "qbittorrent"
        mock_client.find_existing.return_value = None
        mock_client.add_download.return_value = "download_id"
        mock_client.get_status.return_value = DownloadStatus(
            progress=100,
            state=DownloadState.COMPLETE,
            message="Complete",
            complete=True,
            file_path=remote_path,
        )
        mock_client.get_download_path.return_value = remote_path

        def config_get(key: str, default=""):
            if key == "PROWLARR_REMOTE_PATH_MAPPINGS":
                return [
                    {
                        "host": "qbittorrent",
                        "remotePath": str(remote_dir),
                        "localPath": str(local_dir),
                    }
                ]
            if key == "DOWNLOAD_CLIENT_COMPLETED_PATH_TIMEOUT":
                return 0
            return default

        with (
            patch(
                "shelfmark.release_sources.prowlarr.handler.get_release",
                return_value={
                    "protocol": "torrent",
                    "magnetUrl": "magnet:?xt=urn:btih:abc123",
                },
            ),
            patch(
                "shelfmark.release_sources.prowlarr.handler.get_client",
                return_value=mock_client,
            ),
            patch(
                "shelfmark.release_sources.prowlarr.handler.remove_release",
            ),
            patch(
                "shelfmark.release_sources.prowlarr.handler.config.get",
                side_effect=config_get,
            ),
            patch(
                "shelfmark.release_sources.prowlarr.handler.POLL_INTERVAL",
                0.01,
            ),
        ):
            handler = ProwlarrHandler()
            task = DownloadTask(
                task_id="poll-mapping-missing", source="prowlarr", title="Test Book"
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
            assert any(status == "error" for status, _ in recorder.status_updates)
            assert not local_file.exists()


def test_wait_for_completed_path_allows_delayed_remapped_file(monkeypatch, tmp_path):
    import shelfmark.download.clients.base_handler as base_handler

    local_file = tmp_path / "local" / "downloads" / "book.epub"
    remote_path = "/remote/downloads/book.epub"

    mock_client = MagicMock()
    mock_client.name = "qbittorrent"
    mock_client.get_download_path.return_value = remote_path

    def config_get(key: str, default=""):
        if key == "PROWLARR_REMOTE_PATH_MAPPINGS":
            return [
                {
                    "host": "qbittorrent",
                    "remotePath": "/remote/downloads",
                    "localPath": str(local_file.parent),
                }
            ]
        if key == "DOWNLOAD_CLIENT_COMPLETED_PATH_TIMEOUT":
            return 1
        return default

    probe_calls = 0
    original_probe = base_handler._probe_completed_path

    def delayed_probe(path: Path):
        nonlocal probe_calls
        probe_calls += 1
        if path == local_file and probe_calls == 2:
            local_file.parent.mkdir(parents=True)
            local_file.write_text("synced")
        return original_probe(path)

    handler = ProwlarrHandler()
    recorder = ProgressRecorder()
    monkeypatch.setattr(base_handler.config, "get", config_get)
    monkeypatch.setattr(base_handler, "_probe_completed_path", delayed_probe)
    monkeypatch.setattr(handler, "_completed_path_retry_interval", lambda: 0.01)

    resolved_path, error = handler._wait_for_completed_path(
        mock_client,
        "download_id",
        cancel_flag=Event(),
        status_callback=recorder.status_callback,
    )

    assert resolved_path == local_file
    assert error is None
    assert ("locating", "Waiting for completed files...") in recorder.status_updates


def test_waits_for_supported_file_to_stop_growing_in_remapped_directory(monkeypatch, tmp_path):
    import shelfmark.download.clients.base_handler as base_handler

    local_dir = tmp_path / "local" / "downloads" / "book"
    local_dir.mkdir(parents=True)
    (local_dir / "book.cue").write_text("cue")
    (local_dir / "cover.jpg").write_text("cover")

    mock_client = MagicMock()
    mock_client.name = "qbittorrent"
    mock_client.get_download_path.return_value = "/remote/downloads/book"

    def config_get(key: str, default=""):
        if key == "PROWLARR_REMOTE_PATH_MAPPINGS":
            return [
                {
                    "host": "qbittorrent",
                    "remotePath": "/remote/downloads",
                    "localPath": str(local_dir.parent),
                }
            ]
        if key == "DOWNLOAD_CLIENT_COMPLETED_PATH_TIMEOUT":
            return 1
        return default

    media_file = local_dir / "book.m4b"
    sleep_calls = 0

    def advance_sync(_seconds: float) -> None:
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls == 1:
            media_file.write_bytes(b"a")
        elif sleep_calls == 2:
            media_file.write_bytes(b"audio")

    handler = ProwlarrHandler()
    recorder = ProgressRecorder()
    monkeypatch.setattr(base_handler.config, "get", config_get)
    monkeypatch.setattr(handler, "_completed_path_retry_interval", lambda: 0.01)
    monkeypatch.setattr(base_handler.time, "sleep", advance_sync)

    resolved_path, error = handler._wait_for_completed_path(
        mock_client,
        "download_id",
        cancel_flag=None,
        status_callback=recorder.status_callback,
        content_type="audiobook",
    )

    assert resolved_path == local_dir
    assert error is None
    assert sleep_calls == 3
    assert recorder.status_updates.count(("locating", "Waiting for completed files...")) == 3


def test_wait_for_completed_path_fails_fast_for_unsafe_mapping(monkeypatch, tmp_path):
    import shelfmark.download.clients.base_handler as base_handler

    remote_dir = tmp_path / "remote" / "downloads"
    local_dir = tmp_path / "local" / "downloads"
    raw_path = f"{remote_dir}/../escape/book.epub"

    mock_client = MagicMock()
    mock_client.name = "qbittorrent"
    mock_client.get_download_path.return_value = raw_path

    def config_get(key: str, default=""):
        if key == "PROWLARR_REMOTE_PATH_MAPPINGS":
            return [
                {
                    "host": "qbittorrent",
                    "remotePath": str(remote_dir),
                    "localPath": str(local_dir),
                }
            ]
        if key == "DOWNLOAD_CLIENT_COMPLETED_PATH_TIMEOUT":
            return 900
        return default

    handler = ProwlarrHandler()
    recorder = ProgressRecorder()
    monkeypatch.setattr(base_handler.config, "get", config_get)
    monkeypatch.setattr(handler, "_completed_path_retry_interval", lambda: 999)

    resolved_path, error = handler._wait_for_completed_path(
        mock_client,
        "download_id",
        cancel_flag=Event(),
        status_callback=recorder.status_callback,
    )

    assert resolved_path is None
    assert error is not None
    assert "rejected unsafe path" in error
    assert mock_client.get_download_path.call_count == 1
    assert recorder.status_updates == []


def test_wait_for_completed_path_fails_fast_for_permission_error(monkeypatch):
    import shelfmark.download.clients.base_handler as base_handler

    raw_path = "/downloads/book.epub"

    mock_client = MagicMock()
    mock_client.name = "qbittorrent"
    mock_client.get_download_path.return_value = raw_path

    def config_get(key: str, default=""):
        if key == "PROWLARR_REMOTE_PATH_MAPPINGS":
            return []
        if key == "DOWNLOAD_CLIENT_COMPLETED_PATH_TIMEOUT":
            return 900
        return default

    probe_calls = 0

    def permission_denied_probe(_path: Path):
        nonlocal probe_calls
        probe_calls += 1
        return False, PermissionError(errno.EACCES, "Permission denied", raw_path)

    handler = ProwlarrHandler()
    recorder = ProgressRecorder()
    monkeypatch.setattr(base_handler.config, "get", config_get)
    monkeypatch.setattr(base_handler, "_probe_completed_path", permission_denied_probe)
    monkeypatch.setattr(handler, "_completed_path_retry_interval", lambda: 999)

    resolved_path, error = handler._wait_for_completed_path(
        mock_client,
        "download_id",
        cancel_flag=Event(),
        status_callback=recorder.status_callback,
    )

    assert resolved_path is None
    assert error is not None
    assert "not accessible" in error
    assert probe_calls == 1
    assert recorder.status_updates == []


def test_remaps_windows_path_to_linux():
    """Test that Windows paths from external download clients are correctly remapped."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        # Create a local file that represents the mounted path
        local_file = Path(tmp_dir) / "downloads" / "Le Fay" / "book.epub"
        local_file.parent.mkdir(parents=True)
        local_file.write_text("test content")

        # Windows path as reported by qBittorrent running on Windows
        windows_path = r"D:\Torrents\Le Fay\book.epub"

        mock_client = MagicMock()
        mock_client.name = "qbittorrent"
        mock_client.find_existing.return_value = None
        mock_client.add_download.return_value = "download_id"
        mock_client.get_status.return_value = DownloadStatus(
            progress=100,
            state=DownloadState.COMPLETE,
            message="Complete",
            complete=True,
            file_path=windows_path,
        )
        mock_client.get_download_path.return_value = windows_path

        def config_get(key: str, default=""):
            if key == "PROWLARR_REMOTE_PATH_MAPPINGS":
                return [
                    {
                        "host": "qbittorrent",
                        # User enters Windows path in settings (with backslashes)
                        "remotePath": r"D:\Torrents",
                        "localPath": str(Path(tmp_dir) / "downloads"),
                    }
                ]
            return default

        with (
            patch(
                "shelfmark.release_sources.prowlarr.handler.get_release",
                return_value={
                    "protocol": "torrent",
                    "magnetUrl": "magnet:?xt=urn:btih:abc123",
                },
            ),
            patch(
                "shelfmark.release_sources.prowlarr.handler.get_client",
                return_value=mock_client,
            ),
            patch(
                "shelfmark.release_sources.prowlarr.handler.remove_release",
            ),
            patch(
                "shelfmark.release_sources.prowlarr.handler.config.get",
                side_effect=config_get,
            ),
            patch(
                "shelfmark.release_sources.prowlarr.handler.POLL_INTERVAL",
                0.01,
            ),
        ):
            handler = ProwlarrHandler()
            task = DownloadTask(task_id="windows-path-test", source="prowlarr", title="Test Book")
            cancel_flag = Event()
            recorder = ProgressRecorder()

            result = handler.download(
                task=task,
                cancel_flag=cancel_flag,
                progress_callback=recorder.progress_callback,
                status_callback=recorder.status_callback,
            )

            assert result == str(local_file)
            assert task.original_download_path == str(local_file)


def test_windows_path_case_insensitive_matching():
    """Test that Windows path matching is case-insensitive.

    Users may enter paths in different case than what the download client reports.
    For example, user enters 'd:\\torrents' but qBittorrent reports 'D:\\Torrents'.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        # Create a local file that represents the mounted path
        local_file = Path(tmp_dir) / "downloads" / "Le Fay" / "book.epub"
        local_file.parent.mkdir(parents=True)
        local_file.write_text("test content")

        # qBittorrent reports path with different case than user's setting
        windows_path = r"D:\Torrents\Le Fay\book.epub"  # Mixed case

        mock_client = MagicMock()
        mock_client.name = "qbittorrent"
        mock_client.find_existing.return_value = None
        mock_client.add_download.return_value = "download_id"
        mock_client.get_status.return_value = DownloadStatus(
            progress=100,
            state=DownloadState.COMPLETE,
            message="Complete",
            complete=True,
            file_path=windows_path,
        )
        mock_client.get_download_path.return_value = windows_path

        def config_get(key: str, default=""):
            if key == "PROWLARR_REMOTE_PATH_MAPPINGS":
                return [
                    {
                        "host": "qbittorrent",
                        # User enters lowercase (as shown in UI screenshot)
                        "remotePath": r"d:\torrents",
                        "localPath": str(Path(tmp_dir) / "downloads"),
                    }
                ]
            return default

        with (
            patch(
                "shelfmark.release_sources.prowlarr.handler.get_release",
                return_value={
                    "protocol": "torrent",
                    "magnetUrl": "magnet:?xt=urn:btih:abc123",
                },
            ),
            patch(
                "shelfmark.release_sources.prowlarr.handler.get_client",
                return_value=mock_client,
            ),
            patch(
                "shelfmark.release_sources.prowlarr.handler.remove_release",
            ),
            patch(
                "shelfmark.release_sources.prowlarr.handler.config.get",
                side_effect=config_get,
            ),
            patch(
                "shelfmark.release_sources.prowlarr.handler.POLL_INTERVAL",
                0.01,
            ),
        ):
            handler = ProwlarrHandler()
            task = DownloadTask(
                task_id="case-insensitive-test", source="prowlarr", title="Test Book"
            )
            cancel_flag = Event()
            recorder = ProgressRecorder()

            result = handler.download(
                task=task,
                cancel_flag=cancel_flag,
                progress_callback=recorder.progress_callback,
                status_callback=recorder.status_callback,
            )

            assert result == str(local_file)
            assert task.original_download_path == str(local_file)


def test_resolve_download_path_rejects_unsafe_mapping_remainder_even_if_original_exists(
    tmp_path,
):
    remote_dir = tmp_path / "remote" / "downloads"
    local_dir = tmp_path / "local" / "downloads"
    escaped_file = tmp_path / "escape" / "book.epub"
    remote_dir.mkdir(parents=True)
    local_dir.mkdir(parents=True)
    escaped_file.parent.mkdir(parents=True)
    escaped_file.write_text("escaped content")

    raw_path = f"{remote_dir}/../../escape/book.epub"

    mock_client = MagicMock()
    mock_client.name = "qbittorrent"
    mock_client.get_download_path.return_value = raw_path

    def config_get(key: str, default=""):
        if key == "PROWLARR_REMOTE_PATH_MAPPINGS":
            return [
                {
                    "host": "qbittorrent",
                    "remotePath": str(remote_dir),
                    "localPath": str(local_dir),
                }
            ]
        return default

    with patch("shelfmark.download.clients.base_handler.config.get", side_effect=config_get):
        handler = ProwlarrHandler()

        resolved_path, error = handler._resolve_download_path_once(
            mock_client,
            "download_id",
            log_details=False,
        )

    assert Path(raw_path).exists()
    assert resolved_path is None
    assert error is not None
    assert "rejected unsafe path" in error


def test_delete_local_download_data_skips_unsafe_mapping_remainder_even_if_original_exists(
    tmp_path,
):
    remote_dir = tmp_path / "remote" / "downloads"
    local_dir = tmp_path / "local" / "downloads"
    escaped_file = tmp_path / "escape" / "book.epub"
    remote_dir.mkdir(parents=True)
    local_dir.mkdir(parents=True)
    escaped_file.parent.mkdir(parents=True)
    escaped_file.write_text("escaped content")

    raw_path = f"{remote_dir}/../../escape/book.epub"

    mock_client = MagicMock()
    mock_client.name = "nzbget"
    mock_client.get_download_path.return_value = raw_path

    def config_get(key: str, default=""):
        if key == "PROWLARR_REMOTE_PATH_MAPPINGS":
            return [
                {
                    "host": "nzbget",
                    "remotePath": str(remote_dir),
                    "localPath": str(local_dir),
                }
            ]
        return default

    with patch("shelfmark.download.clients.base_handler.config.get", side_effect=config_get):
        handler = ProwlarrHandler()
        handler._delete_local_download_data(mock_client, "download_id")

    assert Path(raw_path).exists()
    assert escaped_file.exists()
    assert escaped_file.read_text() == "escaped content"
