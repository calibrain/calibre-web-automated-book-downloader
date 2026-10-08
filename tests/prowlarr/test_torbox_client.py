"""TorBox download client lifecycle and API contract tests."""

from io import BytesIO
from threading import Event
from unittest.mock import MagicMock

import pytest
import requests

from shelfmark.download.clients import DownloadState
from shelfmark.download.clients.torbox import TorBoxClient, _DownloadState
from shelfmark.download.clients.torrent_utils import DebridTorrentFile

API_KEY = "torbox-api-key"
MAGNET = "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567&dn=Dune"


def _response(data, *, success=True, error=None, detail="OK", status_code=200):
    response = MagicMock(status_code=status_code)
    response.json.return_value = {
        "success": success,
        "error": error,
        "detail": detail,
        "data": data,
    }
    return response


def _client(monkeypatch):
    monkeypatch.setattr(
        "shelfmark.download.clients.torbox.config.get",
        lambda key, default="": {"TORBOX_API_KEY": API_KEY}.get(key, default),
    )
    return TorBoxClient()


def _state(tmp_path):
    return _DownloadState(torrent_id="42", name="Dune", target_dir=tmp_path)


class TestTorBoxConfiguration:
    def test_is_configured_requires_selected_client_and_api_key(self, monkeypatch):
        values = {"PROWLARR_TORRENT_CLIENT": "torbox", "TORBOX_API_KEY": API_KEY}
        monkeypatch.setattr(
            "shelfmark.download.clients.torbox.config.get",
            lambda key, default="": values.get(key, default),
        )

        assert TorBoxClient.is_configured() is True

        values["TORBOX_API_KEY"] = ""
        assert TorBoxClient.is_configured() is False

    def test_connection_reports_valid_free_plan(self, monkeypatch):
        client = _client(monkeypatch)
        get = MagicMock(return_value=_response({"email": "reader@example.com", "plan": 0}))
        monkeypatch.setattr("shelfmark.download.clients.torbox.requests.get", get)

        assert client.test_connection() == (
            True,
            "Connected to TorBox as 'reader@example.com' (Free plan)",
        )
        assert get.call_args.kwargs["headers"] == {"Authorization": f"Bearer {API_KEY}"}
        assert get.call_args.kwargs["params"] == {"settings": "false"}

    def test_connection_returns_provider_detail_without_api_key(self, monkeypatch):
        client = _client(monkeypatch)
        get = MagicMock(
            return_value=_response(
                None,
                success=False,
                error="BAD_TOKEN",
                detail="Your token is invalid or has expired.",
            )
        )
        monkeypatch.setattr("shelfmark.download.clients.torbox.requests.get", get)

        success, message = client.test_connection()

        assert success is False
        assert "BAD_TOKEN" in message
        assert API_KEY not in message


class TestTorBoxCreation:
    def test_magnet_creation_sends_magnet_and_returns_torrent_id(self, monkeypatch, tmp_path):
        client = _client(monkeypatch)
        monkeypatch.setattr("shelfmark.download.clients.torbox.TMP_DIR", tmp_path)
        post = MagicMock(return_value=_response({"torrent_id": 42}))
        monkeypatch.setattr("shelfmark.download.clients.torbox.requests.post", post)

        assert client.add_download(MAGNET, "Dune") == "42"

        assert post.call_args.args[0].endswith("/torrents/createtorrent")
        assert post.call_args.kwargs["data"] == {"name": "Dune", "magnet": MAGNET}
        assert post.call_args.kwargs["files"] is None
        assert (tmp_path / "torbox_42").is_dir()

    def test_torrent_file_creation_sends_binary_multipart_upload(self, monkeypatch, tmp_path):
        client = _client(monkeypatch)
        monkeypatch.setattr("shelfmark.download.clients.torbox.TMP_DIR", tmp_path)
        monkeypatch.setattr(
            "shelfmark.download.clients.torbox.resolve_debrid_upload",
            lambda *_args, **_kwargs: DebridTorrentFile(torrent_data=b"torrent"),
        )
        post = MagicMock(return_value=_response({"torrent_id": 42}))
        monkeypatch.setattr("shelfmark.download.clients.torbox.requests.post", post)

        client.add_download("https://prowlarr.example/download", "Dune")

        assert post.call_args.kwargs["data"] == {"name": "Dune"}
        assert post.call_args.kwargs["files"] == {
            "file": ("release.torrent", b"torrent", "application/x-bittorrent")
        }

    def test_creation_surfaces_torbox_error_detail(self, monkeypatch):
        client = _client(monkeypatch)
        post = MagicMock(
            return_value=_response(
                None,
                success=False,
                error="ACTIVE_LIMIT",
                detail="You have reached your active download limit.",
            )
        )
        monkeypatch.setattr("shelfmark.download.clients.torbox.requests.post", post)

        with pytest.raises(RuntimeError, match=r"ACTIVE_LIMIT.*active download limit"):
            client.add_download(MAGNET, "Dune")

    def test_creation_rejects_unsafe_torrent_id_before_creating_files(self, monkeypatch, tmp_path):
        client = _client(monkeypatch)
        monkeypatch.setattr("shelfmark.download.clients.torbox.TMP_DIR", tmp_path)
        post = MagicMock(return_value=_response({"torrent_id": "x/../../escape"}))
        monkeypatch.setattr("shelfmark.download.clients.torbox.requests.post", post)

        with pytest.raises(RuntimeError, match="invalid torrent ID"):
            client.add_download(MAGNET, "Dune")

        assert not (tmp_path.parent / "escape").exists()


class TestTorBoxStatus:
    def test_fractional_and_percentage_progress_are_normalized(self):
        assert TorBoxClient._normalize_remote_progress(0.25) == 25.0
        assert TorBoxClient._normalize_remote_progress(25) == 25.0
        assert TorBoxClient._normalize_remote_progress("invalid") == 0.0

    def test_completed_state_without_finished_flag_remains_pollable(self, tmp_path):
        client = TorBoxClient.__new__(TorBoxClient)
        state = _state(tmp_path)

        status = client._handle_torrent_status(
            {"download_state": "completed", "progress": 100, "name": "Dune"}, state
        )

        assert status.state == DownloadState.DOWNLOADING
        assert status.progress == 50.0
        assert state.phase == "waiting_torbox"

    def test_finished_torrent_starts_retrieval_once(self, monkeypatch, tmp_path):
        client = TorBoxClient.__new__(TorBoxClient)
        state = _state(tmp_path)
        start = MagicMock()
        monkeypatch.setattr(client, "_maybe_start_download_thread", start)
        torrent = {
            "download_state": "cached",
            "download_finished": True,
            "download_present": True,
            "files": [{"id": 1, "name": "Dune.epub"}],
        }

        status = client._handle_torrent_status(torrent, state)

        assert status.progress == 50.0
        assert status.state == DownloadState.DOWNLOADING
        start.assert_called_once_with(state, torrent["files"])

    def test_starting_file_retrieval_preserves_completed_torrent_progress(
        self, monkeypatch, tmp_path
    ):
        client = TorBoxClient.__new__(TorBoxClient)
        state = _state(tmp_path)
        thread = MagicMock()
        thread.is_alive.return_value = False
        monkeypatch.setattr(
            "shelfmark.download.clients.torbox.threading.Thread", lambda **_kwargs: thread
        )

        client._maybe_start_download_thread(state, [{"id": 1, "name": "Dune.epub"}])

        assert state.phase == "downloading_http"
        assert state.progress == 50.0
        thread.start.assert_called_once()

    def test_finished_torrent_waits_for_content_to_become_present(self, monkeypatch, tmp_path):
        client = TorBoxClient.__new__(TorBoxClient)
        state = _state(tmp_path)
        start = MagicMock()
        monkeypatch.setattr(client, "_maybe_start_download_thread", start)
        torrent = {
            "download_state": "completed",
            "download_finished": True,
            "download_present": False,
            "progress": 1,
            "files": [{"id": 1, "name": "Dune.epub"}],
        }

        status = client._handle_torrent_status(torrent, state)

        assert status.state == DownloadState.DOWNLOADING
        assert status.progress == 50.0
        assert state.phase == "waiting_torbox"
        start.assert_not_called()

        status = client._handle_torrent_status({**torrent, "download_present": True}, state)

        assert status.state == DownloadState.DOWNLOADING
        start.assert_called_once_with(state, torrent["files"])

    def test_finished_torrent_without_available_content_errors_after_grace(
        self, monkeypatch, tmp_path
    ):
        client = TorBoxClient.__new__(TorBoxClient)
        state = _state(tmp_path)
        now = [1000.0]
        monkeypatch.setattr("shelfmark.download.clients.torbox.time.monotonic", lambda: now[0])
        torrent = {"download_finished": True, "download_present": False}

        assert client._handle_torrent_status(torrent, state).state == DownloadState.DOWNLOADING
        now[0] += 119.0
        assert client._handle_torrent_status(torrent, state).state == DownloadState.DOWNLOADING
        now[0] += 1.0
        status = client._handle_torrent_status(torrent, state)

        assert status.state == DownloadState.ERROR
        assert "unavailable" in status.message
        assert state.phase == "error"

    def test_explicit_error_state_is_terminal(self, tmp_path):
        client = TorBoxClient.__new__(TorBoxClient)
        state = _state(tmp_path)

        status = client._handle_torrent_status({"download_state": "error"}, state)

        assert status.state == DownloadState.ERROR
        assert state.error_message == "TorBox status error: error"

    def test_status_request_bypasses_torbox_cache(self, monkeypatch, tmp_path):
        client = _client(monkeypatch)
        monkeypatch.setattr("shelfmark.download.clients.torbox.TMP_DIR", tmp_path)
        get = MagicMock(
            return_value=_response([{"id": 42, "download_state": "downloading", "progress": 20}])
        )
        monkeypatch.setattr("shelfmark.download.clients.torbox.requests.get", get)

        status = client.get_status("42")

        assert status.progress == 10.0
        assert get.call_args.kwargs["params"] == {"id": "42", "bypass_cache": "true"}

    def test_status_normalizes_torrent_id_before_rehydrating_state(self, monkeypatch, tmp_path):
        client = _client(monkeypatch)
        monkeypatch.setattr("shelfmark.download.clients.torbox.TMP_DIR", tmp_path)
        get = MagicMock(
            return_value=_response([{"id": 42, "download_state": "downloading", "progress": 20}])
        )
        monkeypatch.setattr("shelfmark.download.clients.torbox.requests.get", get)

        try:
            client.get_status("0042")

            assert get.call_args.kwargs["params"] == {"id": "42", "bypass_cache": "true"}
            assert "42" in TorBoxClient._downloads
        finally:
            TorBoxClient._downloads.pop("42", None)

    def test_status_rejects_unsafe_torrent_id_before_rehydrating_state(self, monkeypatch, tmp_path):
        client = _client(monkeypatch)
        monkeypatch.setattr("shelfmark.download.clients.torbox.TMP_DIR", tmp_path)
        get = MagicMock()
        monkeypatch.setattr("shelfmark.download.clients.torbox.requests.get", get)

        with pytest.raises(RuntimeError, match="invalid torrent ID"):
            client.get_status("x/../../escape")

        get.assert_not_called()
        assert not (tmp_path.parent / "escape").exists()


class TestTorBoxFileRetrieval:
    def test_safe_relative_path_rejects_absolute_and_traversal_paths(self, tmp_path):
        with pytest.raises(RuntimeError, match="unsafe"):
            TorBoxClient._safe_relative_path({"name": "/Dune.epub"}, tmp_path)
        with pytest.raises(RuntimeError, match="unsafe"):
            TorBoxClient._safe_relative_path({"name": r"books\..\Dune.epub"}, tmp_path)
        with pytest.raises(RuntimeError, match="unsafe"):
            TorBoxClient._safe_relative_path({"name": r"C:\books\Dune.epub"}, tmp_path)
        with pytest.raises(RuntimeError, match="unsafe"):
            TorBoxClient._safe_relative_path({"name": r"C:books\Dune.epub"}, tmp_path)

    def test_process_downloads_supported_file_into_safe_relative_path(self, monkeypatch, tmp_path):
        client = _client(monkeypatch)
        state = _state(tmp_path)

        class StreamingBuffer(BytesIO):
            def getvalue(self):
                raise AssertionError("file retrieval must stream the download buffer")

        monkeypatch.setattr(
            client, "_request_download_link", lambda *_args: "https://cdn.example/Dune"
        )
        buffer = StreamingBuffer(b"book content")
        buffer.seek(0, 2)
        download = MagicMock(return_value=buffer)
        monkeypatch.setattr(
            "shelfmark.download.clients.torbox.download_url",
            download,
        )

        client._process_and_download(state, [{"id": 1, "name": "books/Dune.EPUB"}])

        assert (tmp_path / "books" / "Dune.EPUB").read_bytes() == b"book content"
        assert state.phase == "complete"
        assert state.progress == 100.0
        assert download.call_args.kwargs["referer"] == "https://torbox.app/"

    def test_remove_cancels_active_retrieval_before_removing_files(self, monkeypatch, tmp_path):
        client = _client(monkeypatch)
        target_dir = tmp_path / "torbox_42"
        target_dir.mkdir()
        state = _DownloadState(torrent_id="42", name="Dune", target_dir=target_dir)
        started = Event()

        monkeypatch.setattr(
            client, "_request_download_link", lambda *_args: "https://cdn.example/Dune"
        )

        def wait_for_cancellation(_url, *, cancel_flag, **_kwargs):
            started.set()
            assert cancel_flag.wait(timeout=1)
            return None

        monkeypatch.setattr("shelfmark.download.clients.torbox.download_url", wait_for_cancellation)
        monkeypatch.setattr(
            "shelfmark.download.clients.torbox.requests.post",
            MagicMock(return_value=_response(None)),
        )
        TorBoxClient._downloads["42"] = state

        try:
            client._maybe_start_download_thread(state, [{"id": 1, "name": "Dune.epub"}])
            assert started.wait(timeout=1)

            assert client.remove("42") is True
        finally:
            TorBoxClient._downloads.pop("42", None)

        assert not target_dir.exists()
        assert not state.download_thread or not state.download_thread.is_alive()

    def test_file_link_request_keeps_token_out_of_error_message(self, monkeypatch):
        client = _client(monkeypatch)
        get = MagicMock(
            return_value=_response(
                None,
                success=False,
                error="BAD_TOKEN",
                detail="Token rejected",
            )
        )
        monkeypatch.setattr("shelfmark.download.clients.torbox.requests.get", get)

        with pytest.raises(RuntimeError) as excinfo:
            client._request_download_link("42", 7)

        assert API_KEY not in str(excinfo.value)
        assert get.call_args.kwargs["params"]["token"] == API_KEY

    def test_file_link_accepts_an_https_url(self, monkeypatch):
        client = _client(monkeypatch)
        signed_url = "HtTpS://cdn.example/Dune?token=signed"
        monkeypatch.setattr(
            "shelfmark.download.clients.torbox.requests.get",
            MagicMock(return_value=_response(signed_url)),
        )

        assert client._request_download_link("42", 7) == signed_url

    @staticmethod
    def _link_request(monkeypatch, data: object) -> MagicMock:
        get = MagicMock(return_value=_response(data))
        monkeypatch.setattr("shelfmark.download.clients.torbox.requests.get", get)
        return get

    def test_file_link_rejects_http_urls(self, monkeypatch):
        client = _client(monkeypatch)
        self._link_request(monkeypatch, "http://cdn.example/Dune?token=signed")

        with pytest.raises(RuntimeError, match="invalid download link"):
            client._request_download_link("42", 7)

    def test_file_link_rejects_non_url_data(self, monkeypatch):
        client = _client(monkeypatch)
        self._link_request(monkeypatch, "not a link")

        with pytest.raises(RuntimeError, match="invalid download link"):
            client._request_download_link("42", 7)

    def test_file_link_rejects_hostless_urls(self, monkeypatch):
        client = _client(monkeypatch)
        self._link_request(monkeypatch, "https:///Dune")

        with pytest.raises(RuntimeError, match="invalid download link"):
            client._request_download_link("42", 7)

    def test_file_link_transport_failure_keeps_token_out_of_state_and_logs(
        self, monkeypatch, tmp_path
    ):
        client = _client(monkeypatch)
        logger = MagicMock()
        monkeypatch.setattr("shelfmark.download.clients.torbox.logger", logger)
        monkeypatch.setattr(
            "shelfmark.download.clients.torbox.requests.get",
            MagicMock(side_effect=requests.exceptions.ConnectionError(f"token={API_KEY}")),
        )
        state = _state(tmp_path)

        client._process_and_download(state, [{"id": 1, "name": "Dune.epub"}])

        assert state.error_message == "TorBox file-link request failed: ConnectionError"
        assert API_KEY not in str(logger.mock_calls)


class TestTorBoxCleanup:
    def test_remove_cleans_local_state_when_torbox_rejects_deletion(self, monkeypatch, tmp_path):
        client = _client(monkeypatch)
        target_dir = tmp_path / "torbox_42"
        target_dir.mkdir()
        state = _DownloadState(torrent_id="42", name="Dune", target_dir=target_dir)
        monkeypatch.setattr("shelfmark.download.clients.torbox.TMP_DIR", tmp_path)
        monkeypatch.setattr(
            "shelfmark.download.clients.torbox.requests.post",
            MagicMock(
                return_value=_response(
                    None, success=False, error="NOT_OWNER", detail="You are not the owner."
                )
            ),
        )
        TorBoxClient._downloads["42"] = state

        try:
            assert client.remove("42") is False
        finally:
            TorBoxClient._downloads.pop("42", None)

        assert not target_dir.exists()

    def test_remove_rejects_unsafe_torrent_id_before_deleting_files(self, monkeypatch, tmp_path):
        client = _client(monkeypatch)
        monkeypatch.setattr("shelfmark.download.clients.torbox.TMP_DIR", tmp_path)
        escaped_dir = tmp_path.parent / "escape"
        escaped_dir.mkdir()
        post = MagicMock()
        monkeypatch.setattr("shelfmark.download.clients.torbox.requests.post", post)

        with pytest.raises(RuntimeError, match="invalid torrent ID"):
            client.remove("x/../../escape")

        post.assert_not_called()
        assert escaped_dir.is_dir()


class TestTorBoxTorrentSelection:
    def test_list_entries_must_match_the_requested_torrent_id(self):
        torrent = {"id": 42, "download_state": "downloading"}

        assert TorBoxClient._extract_torrent([torrent], "42") is torrent

    def test_list_entry_with_a_different_id_is_rejected(self):
        with pytest.raises(RuntimeError, match="torrent 42 was not found"):
            TorBoxClient._extract_torrent([{"id": 99, "download_state": "downloading"}], "42")

    def test_sole_list_entry_without_an_id_is_rejected(self):
        with pytest.raises(RuntimeError, match="torrent 42 was not found"):
            TorBoxClient._extract_torrent([{"download_state": "downloading"}], "42")


class TestTorBoxRemoveWorkerTimeout:
    def test_remove_defers_cleanup_when_worker_thread_survives_join_timeout(
        self, monkeypatch, tmp_path
    ):
        client = _client(monkeypatch)
        target_dir = tmp_path / "torbox_42"
        target_dir.mkdir()
        (target_dir / "Dune.epub").write_bytes(b"book")
        state = _DownloadState(torrent_id="42", name="Dune", target_dir=target_dir)
        thread = MagicMock()
        thread.is_alive.return_value = True
        state.download_thread = thread
        post = MagicMock(return_value=_response(None))
        monkeypatch.setattr("shelfmark.download.clients.torbox.requests.post", post)
        monkeypatch.setattr("shelfmark.download.clients.torbox._WORKER_JOIN_TIMEOUT", 0.01)
        TorBoxClient._downloads["42"] = state

        try:
            assert client.remove("42") is False
            assert state.cancel_event.is_set()
            thread.join.assert_called_once()
            (timeout_arg,) = thread.join.call_args.args
            assert timeout_arg > 0
            assert "42" in TorBoxClient._downloads
            assert target_dir.exists()
        finally:
            TorBoxClient._downloads.pop("42", None)
