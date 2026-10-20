"""Debrid-Link seedbox client tests.

Two things set this client apart from the other debrid services and both are
covered here: every v2 endpoint wraps its payload in a ``{success, value}``
envelope that can report failure over an HTTP 200, and a completed torrent
already carries a direct ``downloadUrl`` per file, so there is no unrestrict
step to mock.
"""

import hashlib
import io
import threading
from unittest.mock import MagicMock

import pytest
import requests

from shelfmark.download.clients import DownloadState, debridlink
from shelfmark.download.clients.debridlink import (
    DebridLinkAPIError,
    DebridLinkClient,
    _as_float,
    _DownloadState,
)
from shelfmark.download.clients.torrent_utils import bencode_encode

_PROWLARR_PROXY_URL = "https://prowlarr.example/api/v1/indexer/1/download?apikey=k&link=abc"
_MAGNET = "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567&dn=Dune"


def _valid_torrent() -> tuple[bytes, str]:
    info_dict = {
        b"name": b"book.epub",
        b"length": 100,
        b"piece length": 16384,
        b"pieces": b"\x00" * 20,
    }
    return (
        bencode_encode({b"info": info_dict}),
        hashlib.sha1(bencode_encode(info_dict)).hexdigest().lower(),
    )


def _mock_fetch(monkeypatch, *, content=b"", status_code=200, error=None):
    """Stand in for the .torrent prefetch inside extract_torrent_info."""
    if error is not None:
        mock_get = MagicMock(side_effect=error)
    else:
        response = MagicMock(status_code=status_code, content=content)
        response.raise_for_status = MagicMock()
        mock_get = MagicMock(return_value=response)
    monkeypatch.setattr("shelfmark.download.clients.torrent_utils.requests.get", mock_get)
    return mock_get


def _mock_request(monkeypatch, payload, *, status_code=200):
    """Patch requests.request with a single canned Debrid-Link envelope."""
    response = MagicMock(status_code=status_code)
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value=payload)
    request = MagicMock(return_value=response)
    monkeypatch.setattr("shelfmark.download.clients.debridlink.requests.request", request)
    return request


@pytest.fixture(autouse=True)
def _reset_shared_state():
    """The flood back-off and the download table are shared across instances."""
    DebridLinkClient._flood_until = 0.0
    DebridLinkClient._flood_backoff = 0.0
    DebridLinkClient._downloads.clear()
    yield
    DebridLinkClient._flood_until = 0.0
    DebridLinkClient._flood_backoff = 0.0
    DebridLinkClient._downloads.clear()


def _state(tmp_path, phase="waiting_dl"):
    return _DownloadState(torrent_id="DL1", name="Dune", target_dir=tmp_path, phase=phase)


def _buffer(data=b"data"):
    return io.BytesIO(data)


def _client(monkeypatch, api_key="dl-key"):
    monkeypatch.setattr(
        "shelfmark.download.clients.debridlink.config.get",
        lambda key, default="": {"DEBRIDLINK_API_KEY": api_key}.get(key, default),
    )
    return DebridLinkClient()


class TestEnvelope:
    """The body decides the outcome, and error codes survive a 4xx or 5xx."""

    def test_success_false_raises_even_on_http_200(self, monkeypatch):
        _mock_request(monkeypatch, {"success": False, "error": "badToken"})
        client = _client(monkeypatch)

        with pytest.raises(DebridLinkAPIError, match="failed: badToken") as err:
            client._request_value("GET", "/account/infos", operation="account check")
        assert err.value.code == "badToken"

    def test_the_error_code_is_read_from_a_4xx_body(self, monkeypatch):
        _mock_request(monkeypatch, {"success": False, "error": "badToken"}, status_code=401)
        client = _client(monkeypatch)

        with pytest.raises(DebridLinkAPIError, match="badToken") as err:
            client._request_value("GET", "/account/infos", operation="account check")
        assert err.value.code == "badToken"

    def test_a_5xx_without_a_body_keeps_the_status(self, monkeypatch):
        response = MagicMock(status_code=503)
        response.json = MagicMock(side_effect=ValueError("no json"))
        monkeypatch.setattr(
            "shelfmark.download.clients.debridlink.requests.request",
            MagicMock(return_value=response),
        )
        client = _client(monkeypatch)

        with pytest.raises(DebridLinkAPIError, match="HTTP 503") as err:
            client._request_value("GET", "/seedbox/list", operation="status check")
        assert err.value.code == "http503"

    def test_success_unwraps_the_value(self, monkeypatch):
        _mock_request(monkeypatch, {"success": True, "value": {"id": "DL1"}})
        client = _client(monkeypatch)

        assert client._request_value("GET", "/seedbox/list", operation="status check") == {
            "id": "DL1"
        }

    def test_a_non_object_body_is_rejected(self, monkeypatch):
        _mock_request(monkeypatch, ["unexpected"])
        client = _client(monkeypatch)

        with pytest.raises(RuntimeError, match="invalid response"):
            client._request_value("GET", "/account/infos", operation="account check")

    def test_a_network_failure_does_not_leak_the_url(self, monkeypatch):
        monkeypatch.setattr(
            "shelfmark.download.clients.debridlink.requests.request",
            MagicMock(side_effect=requests.ConnectionError("https://debrid-link.com/secret")),
        )
        client = _client(monkeypatch)

        with pytest.raises(RuntimeError) as err:
            client._request_value("GET", "/seedbox/list", operation="status check")
        assert "debrid-link.com" not in str(err.value)
        assert "ConnectionError" in str(err.value)

    def test_flood_detected_is_raised_with_its_code(self, monkeypatch):
        _mock_request(monkeypatch, {"success": False, "error": "floodDetected"}, status_code=429)
        client = _client(monkeypatch)

        with pytest.raises(DebridLinkAPIError) as err:
            client._request_value("GET", "/seedbox/list", operation="status check")
        assert err.value.code == "floodDetected"


class TestAdd:
    def test_magnet_is_sent_as_json(self, monkeypatch, tmp_path):
        monkeypatch.setattr("shelfmark.download.clients.debridlink.TMP_DIR", tmp_path)
        request = _mock_request(monkeypatch, {"success": True, "value": {"id": "DL1"}})
        client = _client(monkeypatch)

        assert client.add_download(_MAGNET, "Dune") == "DL1"

        request.assert_called_once()
        assert request.call_args.args[0] == "POST"
        assert request.call_args.args[1].endswith("/seedbox/add")
        assert request.call_args.kwargs["json"] == {"url": _MAGNET}
        assert "files" not in request.call_args.kwargs

    def test_proxy_url_is_uploaded_as_a_torrent_file(self, monkeypatch, tmp_path):
        monkeypatch.setattr("shelfmark.download.clients.debridlink.TMP_DIR", tmp_path)
        torrent_data, _ = _valid_torrent()
        _mock_fetch(monkeypatch, content=torrent_data)
        request = _mock_request(monkeypatch, {"success": True, "value": {"id": "DL2"}})
        client = _client(monkeypatch)

        assert client.add_download(_PROWLARR_PROXY_URL, "Dune") == "DL2"

        # A .torrent only goes up as multipart; the JSON body takes a magnet.
        assert "json" not in request.call_args.kwargs
        assert request.call_args.kwargs["files"]["file"][1] == torrent_data

    def test_missing_id_is_an_error_not_a_silent_success(self, monkeypatch, tmp_path):
        monkeypatch.setattr("shelfmark.download.clients.debridlink.TMP_DIR", tmp_path)
        _mock_request(monkeypatch, {"success": True, "value": {}})
        client = _client(monkeypatch)

        with pytest.raises(RuntimeError, match="No torrent ID returned"):
            client.add_download(_MAGNET, "Dune")

    def test_unresolvable_url_reports_the_reason(self, monkeypatch, tmp_path):
        monkeypatch.setattr("shelfmark.download.clients.debridlink.TMP_DIR", tmp_path)
        _mock_fetch(monkeypatch, error=OSError("tracker unreachable"))
        request = _mock_request(monkeypatch, {"success": True, "value": {"id": "DL3"}})
        client = _client(monkeypatch)

        with pytest.raises(ValueError, match="Could not resolve a torrent to send"):
            client.add_download(_PROWLARR_PROXY_URL, "Dune")

        request.assert_not_called()

    def test_without_an_api_key_it_refuses_before_any_call(self, monkeypatch):
        request = _mock_request(monkeypatch, {"success": True, "value": {"id": "DL4"}})
        client = _client(monkeypatch, api_key="")

        with pytest.raises(RuntimeError, match="API key is not configured"):
            client.add_download(_MAGNET, "Dune")

        request.assert_not_called()


class TestStatus:
    @staticmethod
    def _bare_client():
        return DebridLinkClient.__new__(DebridLinkClient)

    def test_partial_progress_is_halved_and_stays_downloading(self, tmp_path):
        client = self._bare_client()
        state = _state(tmp_path)

        status = client._handle_torrent_info(
            {"downloadPercent": 40, "name": "Dune.epub", "downloadSpeed": 1234},
            state,
        )

        # The seedbox fetch is the first half of the bar; HTTP is the second.
        assert status.progress == 20.0
        assert status.state == DownloadState.DOWNLOADING
        assert status.complete is False
        assert status.download_speed == 1234
        assert state.phase == "waiting_dl"

    def test_completion_hands_off_only_when_every_file_is_ready(self, tmp_path, monkeypatch):
        client = self._bare_client()
        state = _state(tmp_path)
        started = MagicMock()
        monkeypatch.setattr(client, "_maybe_start_download_thread", started)

        partial = {
            "downloadPercent": 100,
            "files": [
                {"name": "a.mp3", "downloadPercent": 100},
                {"name": "b.mp3", "downloadPercent": 80},
            ],
        }
        client._handle_torrent_info(partial, state)
        started.assert_not_called()

        partial["files"][1]["downloadPercent"] = 100
        status = client._handle_torrent_info(partial, state)

        assert status.progress == 50.0
        started.assert_called_once_with(state, partial["files"])

    def test_an_empty_file_list_is_not_treated_as_ready(self, tmp_path, monkeypatch):
        client = self._bare_client()
        started = MagicMock()
        monkeypatch.setattr(client, "_maybe_start_download_thread", started)

        client._handle_torrent_info({"downloadPercent": 100, "files": []}, _state(tmp_path))

        started.assert_not_called()

    @pytest.mark.parametrize(
        ("info", "expected_state", "expected_message"),
        [
            ({"status": 0}, DownloadState.PAUSED, "Paused on Debrid-Link"),
            ({"status": 1}, DownloadState.QUEUED, "Queued on Debrid-Link"),
            ({"status": 2}, DownloadState.CHECKING, "verifying"),
            ({"status": 4}, DownloadState.DOWNLOADING, "downloading torrent"),
            # The docs' own example value.
            ({"status": 6}, DownloadState.DOWNLOADING, "downloading torrent"),
            ({"status": 1, "srvMaint": True}, DownloadState.DOWNLOADING, "server maintenance"),
        ],
    )
    def test_documented_states_are_reported(self, tmp_path, info, expected_state, expected_message):
        status = self._bare_client()._handle_torrent_info(
            {"downloadPercent": 10, **info}, _state(tmp_path)
        )

        assert status.state == expected_state
        assert expected_message in (status.message or "")

    @pytest.mark.parametrize("done_status", [8, 100])
    def test_seeding_or_finished_counts_as_done(self, tmp_path, monkeypatch, done_status):
        client = self._bare_client()
        started = MagicMock()
        monkeypatch.setattr(client, "_maybe_start_download_thread", started)

        client._handle_torrent_info(
            {
                "status": done_status,
                "downloadPercent": 99,
                "files": [{"name": "a.epub", "downloadPercent": 100}],
            },
            _state(tmp_path),
        )

        started.assert_called_once()

    def test_a_torrent_held_for_file_selection_is_started_once(self, tmp_path, monkeypatch):
        # ``wait`` means "hold for file selection", not "queued": without a config
        # call the torrent never starts.
        client = _client(monkeypatch)
        request = _mock_request(monkeypatch, {"success": True, "value": []})
        state = _state(tmp_path)

        first = client._handle_torrent_info({"wait": True, "downloadPercent": 0}, state)
        client._handle_torrent_info({"wait": True, "downloadPercent": 0}, state)

        assert "Starting on Debrid-Link" in (first.message or "")
        assert request.call_count == 1
        args, kwargs = request.call_args
        assert args[0] == "POST"
        assert args[1].endswith("/seedbox/DL1/config")
        assert kwargs["json"] == {"files-unwanted": []}

    def test_retrieval_starts_at_half_progress_rather_than_zero(self, tmp_path, monkeypatch):
        client = self._bare_client()
        state = _state(tmp_path)
        DebridLinkClient._downloads["DL1"] = state
        release = threading.Event()
        monkeypatch.setattr(client, "_process_and_download", lambda _state, _files: release.wait(5))

        client._maybe_start_download_thread(state)
        try:
            status = client.get_status("DL1")
        finally:
            release.set()
            assert state.download_thread is not None
            state.download_thread.join(5)

        assert status.message == "Downloading files via HTTP..."
        assert status.progress == 50.0

    def test_retrieval_reuses_the_files_the_status_check_already_has(self, tmp_path, monkeypatch):
        client = self._bare_client()
        state = _state(tmp_path)
        DebridLinkClient._downloads["DL1"] = state
        refetched = MagicMock(side_effect=AssertionError("file list fetched a second time"))
        monkeypatch.setattr(client, "_fetch_file_list", refetched)
        seen: list[list[dict]] = []
        monkeypatch.setattr(
            client, "_process_and_download", lambda _state, files=None: seen.append(files)
        )

        ready = {
            "downloadPercent": 100,
            "files": [{"name": "Dune.epub", "downloadPercent": 100, "downloadUrl": "https://dl/x"}],
        }
        client._handle_torrent_info(ready, state)
        assert state.download_thread is not None
        state.download_thread.join(5)

        assert seen == [ready["files"]]
        refetched.assert_not_called()

    def test_a_vanished_torrent_becomes_an_error_rather_than_a_stall(self, monkeypatch, tmp_path):
        monkeypatch.setattr("shelfmark.download.clients.debridlink.TMP_DIR", tmp_path)
        _mock_request(monkeypatch, {"success": True, "value": []})
        client = _client(monkeypatch)

        status = client.get_status("GONE")

        assert status.state == DownloadState.ERROR
        assert "no longer on Debrid-Link" in (status.message or "")


class TestPolling:
    """The handler polls every couple of seconds; Debrid-Link must not see that."""

    def test_status_is_served_from_cache_between_refreshes(self, monkeypatch, tmp_path):
        monkeypatch.setattr("shelfmark.download.clients.debridlink.TMP_DIR", tmp_path)
        request = _mock_request(
            monkeypatch, {"success": True, "value": [{"id": "DL1", "downloadPercent": 30}]}
        )
        client = _client(monkeypatch)

        for _ in range(5):
            status = client.get_status("DL1")

        assert request.call_count == 1
        assert status.progress == 15.0

    def test_a_rate_limit_pauses_checks_instead_of_failing(self, monkeypatch, tmp_path):
        monkeypatch.setattr("shelfmark.download.clients.debridlink.TMP_DIR", tmp_path)
        request = _mock_request(
            monkeypatch, {"success": False, "error": "floodDetected"}, status_code=429
        )
        client = _client(monkeypatch)

        first = client.get_status("DL1")
        second = client.get_status("DL1")

        assert first.state == DownloadState.DOWNLOADING
        assert "rate limit" in (first.message or "")
        assert second.state == DownloadState.DOWNLOADING
        assert request.call_count == 1

    def test_the_rate_limit_back_off_doubles_and_stays_under_the_stall_window(
        self, monkeypatch, tmp_path
    ):
        # The orchestrator cancels a download after five minutes without a change,
        # so no single back-off may outlast that.
        monkeypatch.setattr("shelfmark.download.clients.debridlink.TMP_DIR", tmp_path)
        _mock_request(monkeypatch, {"success": False, "error": "floodDetected"}, status_code=429)
        clock = [1_000_000.0]
        monkeypatch.setattr(debridlink.time, "time", lambda: clock[0])
        client = _client(monkeypatch)

        waits = []
        for _ in range(4):
            client.get_status("DL1")
            waits.append(DebridLinkClient._flood_until - clock[0])
            clock[0] = DebridLinkClient._flood_until + 1

        assert waits == [60.0, 120.0, 240.0, 240.0]
        assert max(waits) < 300

    def test_a_successful_check_resets_the_back_off(self, monkeypatch, tmp_path):
        monkeypatch.setattr("shelfmark.download.clients.debridlink.TMP_DIR", tmp_path)
        clock = [1_000_000.0]
        monkeypatch.setattr(debridlink.time, "time", lambda: clock[0])
        client = _client(monkeypatch)

        _mock_request(monkeypatch, {"success": False, "error": "floodDetected"}, status_code=429)
        client.get_status("DL1")
        clock[0] = DebridLinkClient._flood_until + 1
        _mock_request(
            monkeypatch, {"success": True, "value": [{"id": "DL1", "downloadPercent": 30}]}
        )
        client.get_status("DL1")

        assert DebridLinkClient._flood_backoff == 0.0

    def test_a_flood_on_another_endpoint_does_not_pause_status_checks(self, monkeypatch, tmp_path):
        # floodDetected is per endpoint: hammering Test Connection must not stop polling.
        monkeypatch.setattr("shelfmark.download.clients.debridlink.TMP_DIR", tmp_path)
        _mock_request(monkeypatch, {"success": False, "error": "floodDetected"}, status_code=429)
        client = _client(monkeypatch)
        assert client.test_connection()[0] is False

        request = _mock_request(
            monkeypatch, {"success": True, "value": [{"id": "DL1", "downloadPercent": 30}]}
        )
        status = client.get_status("DL1")

        assert request.call_count == 1
        assert status.progress == 15.0

    def test_a_temporary_error_is_ridden_out_then_fails(self, monkeypatch, tmp_path):
        monkeypatch.setattr("shelfmark.download.clients.debridlink.TMP_DIR", tmp_path)
        _mock_request(monkeypatch, {"success": False, "error": "internalError"}, status_code=500)
        clock = [1000.0]
        monkeypatch.setattr(debridlink.time, "monotonic", lambda: clock[0])
        client = _client(monkeypatch)

        assert client.get_status("DL1").state == DownloadState.DOWNLOADING
        clock[0] += debridlink._TRANSIENT_GRACE_SECONDS + debridlink._MIN_REFRESH_SECONDS

        status = client.get_status("DL1")

        assert status.state == DownloadState.ERROR
        assert "internalError" in (status.message or "")

    def test_a_permanent_error_fails_at_once(self, monkeypatch, tmp_path):
        monkeypatch.setattr("shelfmark.download.clients.debridlink.TMP_DIR", tmp_path)
        _mock_request(monkeypatch, {"success": False, "error": "badToken"}, status_code=401)
        client = _client(monkeypatch)

        status = client.get_status("DL1")

        assert status.state == DownloadState.ERROR
        assert "badToken" in (status.message or "")


class TestFetchTorrent:
    def test_the_matching_id_is_picked_out_of_the_page(self, monkeypatch):
        _mock_request(
            monkeypatch,
            {
                "success": True,
                "value": [{"id": "OTHER"}, {"id": "DL1", "downloadPercent": 12}],
            },
        )
        client = _client(monkeypatch)

        assert client._fetch_torrent("DL1") == {"id": "DL1", "downloadPercent": 12}

    def test_an_absent_id_returns_none(self, monkeypatch):
        _mock_request(monkeypatch, {"success": True, "value": [{"id": "OTHER"}]})
        client = _client(monkeypatch)

        assert client._fetch_torrent("DL1") is None

    def test_a_bad_id_error_means_the_torrent_is_gone(self, monkeypatch):
        _mock_request(monkeypatch, {"success": False, "error": "badId"}, status_code=400)
        client = _client(monkeypatch)

        assert client._fetch_torrent("DL1") is None

    def test_the_status_check_asks_for_just_that_torrent(self, monkeypatch):
        request = _mock_request(monkeypatch, {"success": True, "value": []})
        client = _client(monkeypatch)

        client._fetch_torrent("DL1")

        assert request.call_args.kwargs["params"] == {"ids": "DL1"}

    def test_the_file_list_asks_by_ids_so_zips_are_expanded(self, monkeypatch):
        # The plain list shows a many-file torrent as one ZIP; the docs say
        # ``/seedbox/list?ids=TORRENT_ID`` lists every file. There is no ``id``.
        request = _mock_request(
            monkeypatch,
            {
                "success": True,
                "value": [{"id": "DL1", "files": [{"name": "a.mp3"}, {"name": "b.mp3"}]}],
            },
        )
        client = _client(monkeypatch)

        files = client._fetch_file_list("DL1")

        assert request.call_args.kwargs["params"] == {"ids": "DL1"}
        assert [f["name"] for f in files] == ["a.mp3", "b.mp3"]

    def test_the_file_list_of_a_vanished_torrent_is_an_error(self, monkeypatch):
        _mock_request(monkeypatch, {"success": False, "error": "badId"}, status_code=400)
        client = _client(monkeypatch)

        with pytest.raises(RuntimeError, match="no longer on Debrid-Link"):
            client._fetch_file_list("DL1")


class TestConnection:
    def test_missing_key_is_reported_without_a_call(self, monkeypatch):
        request = _mock_request(monkeypatch, {"success": True, "value": {}})
        client = _client(monkeypatch, api_key="")

        assert client.test_connection() == (False, "Debrid-Link API Key is required")
        request.assert_not_called()

    def test_an_expired_account_is_rejected(self, monkeypatch):
        _mock_request(
            monkeypatch,
            {"success": True, "value": {"username": "rob", "premiumLeft": 0}},
        )
        client = _client(monkeypatch)

        ok, message = client.test_connection()

        assert ok is False
        assert "does not have an active" in message

    def test_a_premium_account_is_accepted(self, monkeypatch):
        _mock_request(
            monkeypatch,
            {"success": True, "value": {"username": "rob", "premiumLeft": 86400}},
        )
        client = _client(monkeypatch)

        ok, message = client.test_connection()

        assert ok is True
        assert "rob" in message

    def test_an_api_failure_is_surfaced_not_raised(self, monkeypatch):
        _mock_request(monkeypatch, {"success": False, "error": "badToken"})
        client = _client(monkeypatch)

        ok, message = client.test_connection()

        assert ok is False
        assert "badToken" in message


class TestIsConfigured:
    @staticmethod
    def _config(monkeypatch, values):
        monkeypatch.setattr(
            "shelfmark.download.clients.debridlink.config.get",
            lambda key, default="": values.get(key, default),
        )

    def test_requires_both_the_selection_and_the_key(self, monkeypatch):
        self._config(
            monkeypatch,
            {"PROWLARR_TORRENT_CLIENT": "debridlink", "DEBRIDLINK_API_KEY": "dl-key"},
        )
        assert DebridLinkClient.is_configured() is True

    def test_another_client_selected_means_not_configured(self, monkeypatch):
        self._config(
            monkeypatch,
            {"PROWLARR_TORRENT_CLIENT": "torbox", "DEBRIDLINK_API_KEY": "dl-key"},
        )
        assert DebridLinkClient.is_configured() is False

    def test_no_key_means_not_configured(self, monkeypatch):
        self._config(
            monkeypatch,
            {"PROWLARR_TORRENT_CLIENT": "debridlink", "DEBRIDLINK_API_KEY": ""},
        )
        assert DebridLinkClient.is_configured() is False


class TestDownloadPipeline:
    @staticmethod
    def _run(tmp_path, monkeypatch, files, payload=b"data"):
        client = DebridLinkClient.__new__(DebridLinkClient)
        state = _state(tmp_path, phase="downloading_http")
        fetched: list[str] = []

        def fake_download(url, referer=None, cancel_flag=None):
            fetched.append(url)
            return _buffer(payload)

        monkeypatch.setattr("shelfmark.download.clients.debridlink.download_url", fake_download)
        client._process_and_download(state, files)
        return state, fetched

    def test_book_files_are_preferred_over_the_rest(self, tmp_path, monkeypatch):
        state, fetched = self._run(
            tmp_path,
            monkeypatch,
            [
                {"name": "cover.jpg", "downloadUrl": "https://dl.example/cover.jpg"},
                {"name": "Dune.epub", "downloadUrl": "https://dl.example/Dune.epub"},
            ],
        )

        assert fetched == ["https://dl.example/Dune.epub"]
        assert (tmp_path / "Dune.epub").read_bytes() == b"data"
        assert state.phase == "complete"
        assert state.progress == 100.0

    def test_everything_is_taken_when_nothing_looks_like_a_book(self, tmp_path, monkeypatch):
        state, _ = self._run(
            tmp_path, monkeypatch, [{"name": "readme.nfo", "downloadUrl": "https://dl.example/x"}]
        )

        assert (tmp_path / "readme.nfo").exists()
        assert state.phase == "complete"

    def test_a_zip_bundle_is_downloaded_as_a_book_file(self, tmp_path, monkeypatch):
        state, fetched = self._run(
            tmp_path,
            monkeypatch,
            [
                {"name": "notes.nfo", "downloadUrl": "https://dl.example/n"},
                {"name": "Dune.zip", "downloadUrl": "https://dl.example/z"},
            ],
        )

        assert fetched == ["https://dl.example/z"]
        assert state.phase == "complete"

    def test_no_links_is_an_error(self, tmp_path):
        client = DebridLinkClient.__new__(DebridLinkClient)
        state = _state(tmp_path, phase="downloading_http")

        client._process_and_download(state, [{"name": "Dune.epub"}])

        assert state.phase == "error"
        assert "No download links" in (state.error_message or "")

    @pytest.mark.parametrize("name", ["../escape.epub", "/etc/escape.epub", "a/../../x.epub"])
    def test_a_file_name_cannot_escape_the_download_folder(self, tmp_path, monkeypatch, name):
        target = tmp_path / "dl"
        target.mkdir()
        client = DebridLinkClient.__new__(DebridLinkClient)
        state = _state(target, phase="downloading_http")
        monkeypatch.setattr(
            "shelfmark.download.clients.debridlink.download_url",
            lambda url, referer=None, cancel_flag=None: _buffer(),
        )

        client._process_and_download(state, [{"name": name, "downloadUrl": "https://dl.example/x"}])

        assert state.phase == "error"
        assert "unsafe file path" in (state.error_message or "")
        assert not (tmp_path / "escape.epub").exists()

    def test_two_files_with_the_same_name_are_both_kept(self, tmp_path, monkeypatch):
        payloads = iter([b"one", b"two"])
        client = DebridLinkClient.__new__(DebridLinkClient)
        state = _state(tmp_path, phase="downloading_http")
        monkeypatch.setattr(
            "shelfmark.download.clients.debridlink.download_url",
            lambda url, referer=None, cancel_flag=None: _buffer(next(payloads)),
        )

        client._process_and_download(
            state,
            [
                {"name": "chapter.mp3", "downloadUrl": "https://dl.example/1"},
                {"name": "chapter.mp3", "downloadUrl": "https://dl.example/2"},
            ],
        )

        assert (tmp_path / "chapter.mp3").read_bytes() == b"one"
        assert (tmp_path / "chapter (2).mp3").read_bytes() == b"two"

    def test_a_failed_file_is_reported_without_its_signed_url(self, tmp_path, monkeypatch):
        client = DebridLinkClient.__new__(DebridLinkClient)
        state = _state(tmp_path, phase="downloading_http")
        monkeypatch.setattr(
            "shelfmark.download.clients.debridlink.download_url",
            lambda url, referer=None, cancel_flag=None: None,
        )

        client._process_and_download(
            state, [{"name": "Dune.epub", "downloadUrl": "https://seed1.debrid.link/dl/SIGNED"}]
        )

        assert state.phase == "error"
        assert "Dune.epub" in (state.error_message or "")
        assert "SIGNED" not in (state.error_message or "")

    def test_the_file_list_is_fetched_fresh_when_none_is_given(self, tmp_path, monkeypatch):
        client = DebridLinkClient.__new__(DebridLinkClient)
        state = _state(tmp_path, phase="downloading_http")
        listing = MagicMock(return_value=[{"name": "Dune.epub", "downloadUrl": "https://x/y"}])
        monkeypatch.setattr(client, "_fetch_file_list", listing)
        monkeypatch.setattr(
            "shelfmark.download.clients.debridlink.download_url",
            lambda url, referer=None, cancel_flag=None: _buffer(),
        )

        client._process_and_download(state)

        listing.assert_called_once_with("DL1")
        assert state.phase == "complete"

    def test_cancelling_stops_before_the_next_file(self, tmp_path, monkeypatch):
        client = DebridLinkClient.__new__(DebridLinkClient)
        state = _state(tmp_path, phase="downloading_http")
        fetched: list[str] = []

        def fake_download(url, referer=None, cancel_flag=None):
            fetched.append(url)
            state.cancel_event.set()
            return _buffer()

        monkeypatch.setattr("shelfmark.download.clients.debridlink.download_url", fake_download)

        client._process_and_download(
            state,
            [
                {"name": "a.mp3", "downloadUrl": "https://dl.example/a"},
                {"name": "b.mp3", "downloadUrl": "https://dl.example/b"},
            ],
        )

        assert fetched == ["https://dl.example/a"]
        assert state.phase != "complete"


class TestRemove:
    def test_remove_cancels_the_retrieval_and_cleans_up(self, monkeypatch, tmp_path):
        monkeypatch.setattr("shelfmark.download.clients.debridlink.TMP_DIR", tmp_path)
        _mock_request(monkeypatch, {"success": True, "value": None})
        client = _client(monkeypatch)
        state = _state(tmp_path / "debridlink_DL1", phase="downloading_http")
        state.target_dir.mkdir()
        DebridLinkClient._downloads["DL1"] = state

        assert client.remove("DL1") is True

        assert state.cancel_event.is_set()
        assert not state.target_dir.exists()
        assert "DL1" not in DebridLinkClient._downloads


class TestAsFloat:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (42, 42.0),
            (4.5, 4.5),
            ("17", 17.0),
            ("not a number", 0.0),
            (None, 0.0),
            (True, 0.0),
        ],
    )
    def test_api_numbers_are_coerced_without_raising(self, value, expected):
        assert _as_float(value) == expected
