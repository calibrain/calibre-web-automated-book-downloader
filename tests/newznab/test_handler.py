"""Unit tests for the Newznab download handler."""

from threading import Event
from unittest.mock import patch

from shelfmark.core.models import DownloadTask
from shelfmark.release_sources.newznab.handler import (
    NewznabHandler,
    _get_download_url,
    _get_protocol,
)

# ── helpers ────────────────────────────────────────────────────────────────────


class ProgressRecorder:
    def __init__(self):
        self.progress_values: list[float] = []
        self.status_updates: list[tuple[str, str | None]] = []

    def progress_callback(self, v: float):
        self.progress_values.append(v)

    def status_callback(self, status: str, message: str | None):
        self.status_updates.append((status, message))

    @property
    def last_status(self) -> str | None:
        return self.status_updates[-1][0] if self.status_updates else None

    @property
    def last_message(self) -> str | None:
        return self.status_updates[-1][1] if self.status_updates else None

    @property
    def statuses(self) -> list[str]:
        return [s[0] for s in self.status_updates]


# ── _get_protocol ────────────────────────────────────────────────────────────────


class TestGetProtocol:
    def test_explicit_usenet(self):
        assert _get_protocol({"protocol": "usenet"}) == "usenet"

    def test_explicit_torrent(self):
        assert _get_protocol({"protocol": "torrent"}) == "torrent"

    def test_magnet_url_infers_torrent(self):
        assert _get_protocol({"magnetUrl": "magnet:?xt=urn:btih:abc"}) == "torrent"

    def test_torrent_extension_infers_torrent(self):
        assert _get_protocol({"downloadUrl": "https://example.com/file.torrent"}) == "torrent"

    def test_nzb_extension_infers_usenet(self):
        assert _get_protocol({"downloadUrl": "https://example.com/file.nzb"}) == "usenet"

    def test_defaults_to_usenet_when_ambiguous(self):
        # Newznab is usenet-native; ambiguous URLs default to usenet.
        assert _get_protocol({"downloadUrl": "https://example.com/download/123"}) == "usenet"

    def test_empty_dict_defaults_to_usenet(self):
        assert _get_protocol({}) == "usenet"


# ── _get_download_url ──────────────────────────────────────────────────────────


class TestGetDownloadUrl:
    def test_usenet_prefers_download_url(self):
        result = {
            "protocol": "usenet",
            "downloadUrl": "https://example.com/nzb",
            "magnetUrl": "magnet:?xt=urn:btih:abc",
        }
        assert _get_download_url(result) == "https://example.com/nzb"

    def test_torrent_prefers_magnet(self):
        result = {
            "protocol": "torrent",
            "downloadUrl": "https://example.com/file.torrent",
            "magnetUrl": "magnet:?xt=urn:btih:abc",
        }
        assert _get_download_url(result) == "magnet:?xt=urn:btih:abc"

    def test_blackhole_prefers_torrent_file(self):
        result = {
            "protocol": "torrent",
            "downloadUrl": "https://example.com/file.torrent",
            "magnetUrl": "magnet:?xt=urn:btih:abc",
        }
        assert (
            _get_download_url(result, prefer_torrent_file=True)
            == "https://example.com/file.torrent"
        )

    def test_falls_back_to_download_url_when_no_magnet(self):
        result = {
            "protocol": "torrent",
            "downloadUrl": "https://example.com/file.torrent",
        }
        assert _get_download_url(result) == "https://example.com/file.torrent"


# ── error paths ────────────────────────────────────────────────────────────────


class TestHandlerErrors:
    def test_cache_miss_uses_persisted_retry_fields(self):
        with patch("shelfmark.release_sources.newznab.handler.get_release", return_value=None):
            handler = NewznabHandler()
            task = DownloadTask(
                task_id="retryable",
                source="newznab",
                title="Book",
                retry_download_url="https://indexer.example.com/nzb/42?apikey=secret",
                retry_download_protocol="usenet",
                retry_release_name="Book Release",
                retry_expected_hash="abc123",
            )
            recorder = ProgressRecorder()
            result = handler._resolve_download(task, recorder.status_callback)

        assert result is not None
        assert result.url == "https://indexer.example.com/nzb/42?apikey=secret"
        assert result.protocol == "usenet"
        assert result.release_name == "Book Release"
        assert result.expected_hash == "abc123"
        assert recorder.status_updates == []

    def test_cache_miss_returns_error(self):
        with patch("shelfmark.release_sources.newznab.handler.get_release", return_value=None):
            handler = NewznabHandler()
            task = DownloadTask(task_id="missing", source="newznab", title="Book")
            recorder = ProgressRecorder()
            result = handler.download(
                task=task,
                cancel_flag=Event(),
                progress_callback=recorder.progress_callback,
                status_callback=recorder.status_callback,
            )
        assert result is None
        assert recorder.last_status == "error"
        assert "cache" in (recorder.last_message or "").lower()

    def test_no_download_url_returns_error(self):
        with patch(
            "shelfmark.release_sources.newznab.handler.get_release",
            return_value={"protocol": "usenet", "title": "Book"},
        ):
            handler = NewznabHandler()
            task = DownloadTask(task_id="no-url", source="newznab", title="Book")
            recorder = ProgressRecorder()
            result = handler.download(
                task=task,
                cancel_flag=Event(),
                progress_callback=recorder.progress_callback,
                status_callback=recorder.status_callback,
            )
        assert result is None
        assert recorder.last_status == "error"
        assert "url" in (recorder.last_message or "").lower()

    def test_no_client_configured_returns_error(self):
        with (
            patch(
                "shelfmark.release_sources.newznab.handler.get_release",
                return_value={
                    "protocol": "usenet",
                    "downloadUrl": "https://example.com/nzb/1",
                },
            ),
            patch("shelfmark.release_sources.newznab.handler.get_client", return_value=None),
            patch(
                "shelfmark.release_sources.newznab.handler.list_configured_clients",
                return_value=[],
            ),
        ):
            handler = NewznabHandler()
            task = DownloadTask(task_id="no-client", source="newznab", title="Book")
            recorder = ProgressRecorder()
            result = handler.download(
                task=task,
                cancel_flag=Event(),
                progress_callback=recorder.progress_callback,
                status_callback=recorder.status_callback,
            )
        assert result is None
        assert recorder.last_status == "error"
        assert "client" in (recorder.last_message or "").lower()


# ── cancel ─────────────────────────────────────────────────────────────────────


class TestHandlerCancel:
    def test_cancel_removes_from_cache(self):
        with patch("shelfmark.release_sources.newznab.handler.remove_release") as mock_remove:
            result = NewznabHandler().cancel("task-123")
        assert result is True
        mock_remove.assert_called_once_with("task-123")

    def test_cancel_handles_absent_task(self):
        with patch("shelfmark.release_sources.newznab.handler.remove_release"):
            result = NewznabHandler().cancel("no-such-task")
        assert result is True
