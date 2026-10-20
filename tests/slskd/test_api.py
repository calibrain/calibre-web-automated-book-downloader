"""Unit tests for the slskd API client."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from shelfmark.release_sources.slskd.api import (
    SEARCH_FINISH_MARGIN_SECONDS,
    SEARCH_SAVE_WAIT_SECONDS,
    SlskdAuthError,
    SlskdBatchUnsupportedError,
    SlskdClient,
    SlskdError,
    _flatten_transfers,
    transfer_is_complete,
    transfer_succeeded,
)

# ── helpers ────────────────────────────────────────────────────────────────────


def _make_response(payload=None, status: int = 200, *, raw: bytes | None = None) -> MagicMock:
    r = MagicMock(spec=requests.Response)
    r.status_code = status
    r.ok = status < 400
    if raw is not None:
        r.content = raw
        r.json.side_effect = ValueError("not json")
    elif payload is None:
        r.content = b""
        r.json.side_effect = ValueError("no content")
    else:
        r.content = b"{}"
        r.json.return_value = payload
    r.raise_for_status = MagicMock()
    if status >= 400:
        r.raise_for_status.side_effect = requests.exceptions.HTTPError(response=r)
    return r


def _app_payload(*, logged_in: bool = True, state: str = "Connected, LoggedIn") -> dict:
    return {
        "version": {"full": "0.26.0.0 (0.26.0.0+e42a525d)", "current": "0.26.0.0"},
        "server": {"state": state, "isConnected": True, "isLoggedIn": logged_in},
    }


TRANSFERS_PAYLOAD = {
    "username": "peer",
    "directories": [
        {
            "directory": "@@share\\Books\\Author",
            "fileCount": 2,
            "files": [
                {"id": "t1", "filename": "@@share\\Books\\Author\\one.epub", "state": "InProgress"},
                {
                    "id": "t2",
                    "filename": "@@share\\Books\\Author\\two.epub",
                    "state": "Completed, Succeeded",
                },
            ],
        }
    ],
}


# ── URL construction / auth ────────────────────────────────────────────────────


class TestApiUrl:
    def test_appends_api_prefix(self):
        client = SlskdClient("http://slskd:5030", "key")
        assert client._api_url("application") == "http://slskd:5030/api/v0/application"

    def test_strips_trailing_slash(self):
        client = SlskdClient("http://slskd:5030/", "key")
        assert client._api_url("/searches") == "http://slskd:5030/api/v0/searches"

    def test_does_not_double_prefix(self):
        client = SlskdClient("http://slskd:5030/api/v0", "key")
        assert client._api_url("options") == "http://slskd:5030/api/v0/options"

    def test_adds_scheme_when_missing(self):
        client = SlskdClient("slskd:5030", "key")
        assert client.base_url.startswith("http")


class TestRequest:
    def test_sends_api_key_header(self):
        client = SlskdClient("http://slskd:5030", "secret")
        with patch.object(
            client._session, "request", return_value=_make_response(_app_payload())
        ) as req:
            client.get_application()
        kwargs = req.call_args.kwargs
        assert kwargs["headers"]["X-API-Key"] == "secret"
        assert kwargs["method"] == "GET" if "method" in kwargs else req.call_args.args[0] == "GET"

    def test_omits_header_without_key(self):
        client = SlskdClient("http://slskd:5030", "")
        with patch.object(
            client._session, "request", return_value=_make_response(_app_payload())
        ) as req:
            client.get_application()
        assert "X-API-Key" not in req.call_args.kwargs["headers"]

    def test_401_raises_auth_error(self):
        client = SlskdClient("http://slskd:5030", "bad")
        with (
            patch.object(client._session, "request", return_value=_make_response(status=401)),
            pytest.raises(SlskdAuthError),
        ):
            client.get_application()

    def test_500_raises_http_error(self):
        client = SlskdClient("http://slskd:5030", "key")
        with (
            patch.object(client._session, "request", return_value=_make_response(status=500)),
            pytest.raises(requests.exceptions.HTTPError),
        ):
            client.get_application()

    def test_non_json_body_raises_slskd_error(self):
        client = SlskdClient("http://slskd:5030", "key")
        with (
            patch.object(client._session, "request", return_value=_make_response(raw=b"<html>")),
            pytest.raises(SlskdError),
        ):
            client.get_application()


# ── test_connection ────────────────────────────────────────────────────────────


class TestTestConnection:
    def test_success_when_logged_in(self):
        client = SlskdClient("http://slskd:5030", "key")
        with patch.object(client, "get_application", return_value=_app_payload()):
            ok, msg = client.test_connection()
        assert ok is True
        assert "0.26.0" in msg
        assert "logged in" in msg.lower()

    def test_failure_when_not_logged_in(self):
        client = SlskdClient("http://slskd:5030", "key")
        payload = _app_payload(logged_in=False, state="Disconnected")
        with patch.object(client, "get_application", return_value=payload):
            ok, msg = client.test_connection()
        assert ok is False
        assert "Disconnected" in msg
        assert "not logged in" in msg

    def test_invalid_api_key(self):
        client = SlskdClient("http://slskd:5030", "key")
        with patch.object(client, "get_application", side_effect=SlskdAuthError("no")):
            ok, msg = client.test_connection()
        assert ok is False
        assert msg == "Invalid API key"

    def test_connection_error(self):
        client = SlskdClient("http://slskd:5030", "key")
        with patch.object(
            client, "get_application", side_effect=requests.exceptions.ConnectionError()
        ):
            ok, msg = client.test_connection()
        assert ok is False
        assert "Could not connect" in msg

    def test_http_error(self):
        client = SlskdClient("http://slskd:5030", "key")
        err = requests.exceptions.HTTPError(response=_make_response(status=502))
        with patch.object(client, "get_application", side_effect=err):
            ok, msg = client.test_connection()
        assert ok is False
        assert "502" in msg


# ── options ────────────────────────────────────────────────────────────────────


class TestGetDownloadDirectory:
    def test_reads_directories_downloads(self):
        client = SlskdClient("http://slskd:5030", "key")
        payload = {"directories": {"downloads": "/downloads/complete", "incomplete": "/x"}}
        with patch.object(client, "_request", return_value=_make_response(payload)):
            assert client.get_download_directory() == "/downloads/complete"

    def test_returns_none_when_missing(self):
        client = SlskdClient("http://slskd:5030", "key")
        with patch.object(client, "_request", return_value=_make_response({"web": {}})):
            assert client.get_download_directory() is None


# ── searches ───────────────────────────────────────────────────────────────────


class TestSearch:
    def test_start_search_posts_expected_payload(self):
        client = SlskdClient("http://slskd:5030", "key")
        with patch.object(
            client, "_request", return_value=_make_response({"id": "abc", "state": "InProgress"})
        ) as req:
            search_id = client.start_search("dune herbert", timeout_ms=5000, response_limit=50)
        assert search_id == "abc"
        method, path = req.call_args.args
        assert (method, path) == ("POST", "searches")
        body = req.call_args.kwargs["json"]
        assert body["searchText"] == "dune herbert"
        assert body["searchTimeout"] == 5000
        assert body["responseLimit"] == 50

    def test_start_search_without_id_raises(self):
        client = SlskdClient("http://slskd:5030", "key")
        with (
            patch.object(client, "_request", return_value=_make_response({})),
            pytest.raises(SlskdError),
        ):
            client.start_search("x", timeout_ms=1000, response_limit=10)

    @staticmethod
    def _search(states, *, responses=None, wait_seconds=15, poll_interval=1.0):
        """Run client.search over scripted slskd states on a fake clock.

        Returns (result, calls, clock): ``calls`` lists the client calls in order, and each
        sleep advances the clock, so a search "taking" minutes costs nothing.
        """
        client = SlskdClient("http://slskd:5030", "key")
        clock = [0.0]
        calls: list[str] = []
        seq = iter(states)
        last = [None]

        def _state(_id):
            calls.append("state")
            try:
                last[0] = next(seq)
            except StopIteration:
                pass
            return last[0]

        def _responses(_id):
            calls.append("responses")
            return list(responses or [])

        def _sleep(seconds):
            clock[0] += seconds

        with (
            patch.object(client, "start_search", return_value="abc"),
            patch.object(client, "get_search", side_effect=_state),
            patch.object(client, "get_search_responses", side_effect=_responses),
            patch.object(client, "delete_search", side_effect=lambda _id: calls.append("delete")),
            patch("shelfmark.release_sources.slskd.api.time.sleep", side_effect=_sleep),
            patch(
                "shelfmark.release_sources.slskd.api.time.monotonic", side_effect=lambda: clock[0]
            ),
        ):
            result = client.search(
                "dune", wait_seconds=wait_seconds, response_limit=10, poll_interval=poll_interval
            )
        return result, calls, clock[0]

    def test_search_waits_for_slskd_to_save_the_responses_then_deletes(self):
        running = {"isComplete": False, "state": "InProgress"}
        # Complete, but slskd has not written the responses yet: no endedAt.
        complete = {"isComplete": True, "state": "Completed, TimedOut", "responseCount": 3}
        saved = {**complete, "endedAt": "2026-10-12T05:00:00Z"}
        responses = [{"username": "peer", "files": []}]

        result, calls, _ = self._search([running, complete, complete, saved], responses=responses)

        assert result == responses
        # Nothing is read or deleted before endedAt appears, and the delete comes last.
        assert calls == ["state"] * 4 + ["responses", "delete"]

    def test_a_busy_search_that_outlasts_the_wait_is_not_cut_short(self):
        running = {"isComplete": False, "state": "InProgress"}
        saved = {"isComplete": True, "endedAt": "2026-10-12T05:00:00Z", "responseCount": 40}
        responses = [{"username": "peer"}]

        # Still running 40 s in, against a 15 s wait (peers keep resetting slskd's timer).
        result, calls, elapsed = self._search([running] * 40 + [saved], responses=responses)

        assert result == responses
        assert elapsed > 15
        assert calls[-2:] == ["responses", "delete"]

    def test_a_search_that_never_finishes_is_left_running_not_deleted(self):
        running = {"isComplete": False, "state": "InProgress"}

        result, calls, elapsed = self._search([running])

        assert result == []
        assert "delete" not in calls
        assert "responses" not in calls
        assert elapsed >= 15 + SEARCH_FINISH_MARGIN_SECONDS  # it was given the whole margin

    def test_a_search_with_no_responses_is_deleted_once_slskd_has_finished_it(self):
        saved = {"isComplete": True, "endedAt": "2026-10-12T05:00:00Z", "responseCount": 0}

        result, calls, _ = self._search([saved])

        assert result == []
        assert calls == ["state", "responses", "delete"]

    def test_a_slskd_without_endedat_is_read_after_the_save_wait(self):
        complete = {"isComplete": True, "state": "Completed, TimedOut", "responseCount": 2}
        responses = [{"username": "peer"}]

        result, calls, elapsed = self._search([complete], responses=responses)

        assert result == responses
        assert elapsed >= SEARCH_SAVE_WAIT_SECONDS
        assert calls[-2:] == ["responses", "delete"]

    def test_a_slskd_without_endedat_that_heard_peers_but_saved_nothing_is_left_alone(self):
        complete = {"isComplete": True, "state": "Completed, TimedOut", "responseCount": 2}

        result, calls, _ = self._search([complete], responses=[])

        assert result == []
        assert "delete" not in calls

    def test_search_does_not_delete_when_polling_fails(self):
        client = SlskdClient("http://slskd:5030", "key")
        with (
            patch.object(client, "start_search", return_value="abc"),
            patch.object(client, "get_search", side_effect=requests.exceptions.Timeout()),
            patch.object(client, "delete_search") as delete,
            pytest.raises(requests.exceptions.Timeout),
        ):
            client.search("dune", wait_seconds=1, response_limit=10)
        delete.assert_not_called()  # slskd may still be writing the search

    def test_empty_query_short_circuits(self):
        client = SlskdClient("http://slskd:5030", "key")
        with patch.object(client, "start_search") as start:
            assert client.search("   ", wait_seconds=1, response_limit=10) == []
        start.assert_not_called()

    def test_get_search_responses_filters_non_dicts(self):
        client = SlskdClient("http://slskd:5030", "key")
        with patch.object(
            client, "_request", return_value=_make_response([{"username": "a"}, "junk", 3])
        ):
            assert client.get_search_responses("abc") == [{"username": "a"}]

    def test_delete_search_swallows_errors(self):
        client = SlskdClient("http://slskd:5030", "key")
        with patch.object(client, "_request", side_effect=requests.exceptions.ConnectionError()):
            client.delete_search("abc")  # must not raise


# ── transfers ──────────────────────────────────────────────────────────────────


class TestTransfers:
    def test_enqueue_returns_enqueued_records(self):
        client = SlskdClient("http://slskd:5030", "key")
        payload = {
            "enqueued": [{"id": "t1", "filename": "a\\b.epub", "state": "Queued, Locally"}],
            "failed": [],
        }
        with patch.object(client, "_request", return_value=_make_response(payload)) as req:
            result = client.enqueue_downloads("peer name", [{"filename": "a\\b.epub", "size": 5}])
        assert result == payload["enqueued"]
        method, path = req.call_args.args
        assert method == "POST"
        assert path == "transfers/downloads/peer%20name"
        assert req.call_args.kwargs["json"] == [{"filename": "a\\b.epub", "size": 5}]

    def test_enqueue_raises_when_slskd_reports_failures(self):
        client = SlskdClient("http://slskd:5030", "key")
        payload = {"enqueued": [], "failed": [{"filename": "x", "reason": "Not shared"}]}
        with (
            patch.object(client, "_request", return_value=_make_response(payload)),
            pytest.raises(SlskdError, match="Not shared"),
        ):
            client.enqueue_downloads("peer", [{"filename": "x", "size": 1}])

    def test_enqueue_falls_back_to_listing_on_empty_body(self):
        # Older slskd versions answer 201 with no body.
        client = SlskdClient("http://slskd:5030", "key")
        with (
            patch.object(client, "_request", return_value=_make_response(None, status=201)),
            patch.object(
                client, "list_downloads", return_value=_flatten_transfers(TRANSFERS_PAYLOAD)
            ),
        ):
            result = client.enqueue_downloads(
                "peer", [{"filename": "@@share\\Books\\Author\\two.epub", "size": 1}]
            )
        assert [t["id"] for t in result] == ["t2"]

    def test_list_downloads_flattens_directories(self):
        client = SlskdClient("http://slskd:5030", "key")
        with patch.object(client, "_request", return_value=_make_response(TRANSFERS_PAYLOAD)):
            transfers = client.list_downloads("peer")
        assert [t["id"] for t in transfers] == ["t1", "t2"]

    def test_list_downloads_404_is_empty(self):
        client = SlskdClient("http://slskd:5030", "key")
        err = requests.exceptions.HTTPError(response=_make_response(status=404))
        with patch.object(client, "_request", side_effect=err):
            assert client.list_downloads("peer") == []

    def test_get_transfer_404_is_none(self):
        client = SlskdClient("http://slskd:5030", "key")
        err = requests.exceptions.HTTPError(response=_make_response(status=404))
        with patch.object(client, "_request", side_effect=err):
            assert client.get_transfer("peer", "t1") is None

    def test_get_transfer_returns_record(self):
        client = SlskdClient("http://slskd:5030", "key")
        record = {"id": "t1", "state": "InProgress"}
        with patch.object(client, "_request", return_value=_make_response(record)) as req:
            assert client.get_transfer("peer", "t1") == record
        assert req.call_args.args == ("GET", "transfers/downloads/peer/t1")

    def test_cancel_transfer_sends_remove_flag(self):
        client = SlskdClient("http://slskd:5030", "key")
        with patch.object(client, "_request", return_value=_make_response(None, 204)) as req:
            assert client.cancel_transfer("peer", "t1", remove=True) is True
        assert req.call_args.args == ("DELETE", "transfers/downloads/peer/t1")
        assert req.call_args.kwargs["params"] == {"remove": "true"}

    def test_cancel_transfer_404_is_false(self):
        client = SlskdClient("http://slskd:5030", "key")
        err = requests.exceptions.HTTPError(response=_make_response(status=404))
        with patch.object(client, "_request", side_effect=err):
            assert client.cancel_transfer("peer", "t1", remove=False) is False


# ── helpers ────────────────────────────────────────────────────────────────────


class TestHelpers:
    def test_flatten_handles_list_of_users(self):
        data = [TRANSFERS_PAYLOAD, {"username": "other", "directories": []}]
        assert [t["id"] for t in _flatten_transfers(data)] == ["t1", "t2"]

    def test_flatten_ignores_garbage(self):
        assert _flatten_transfers(None) == []
        assert _flatten_transfers({"directories": "nope"}) == []
        assert _flatten_transfers({"directories": [{"files": "nope"}, 3]}) == []

    @pytest.mark.parametrize(
        ("state", "complete", "succeeded"),
        [
            ("Completed, Succeeded", True, True),
            ("Completed, Errored", True, False),
            ("Completed, Cancelled", True, False),
            ("InProgress", False, False),
            ("Queued, Remotely", False, False),
            (None, False, False),
        ],
    )
    def test_state_predicates(self, state, complete, succeeded):
        assert transfer_is_complete(state) is complete
        assert transfer_succeeded(state) is succeeded


# ── batch downloads, batches by id, events (fork additions) ─────────────────────


class TestBatchDownloads:
    def _client(self):
        return SlskdClient("http://slskd:5030", "key")

    def test_enqueue_batch_posts_files_into_a_relative_destination(self):
        client = self._client()
        reply = {
            "batch": {
                "id": "b-1",
                "transfers": [{"id": "t1", "filename": "a.epub", "state": "Requested"}],
            },
            "failures": [],
        }
        with patch.object(
            client._session, "request", return_value=_make_response(reply, 201)
        ) as request:
            result = client.enqueue_batch(
                "peer", [{"filename": "a.epub", "size": 7}], "shelfmark/task-1"
            )

        _, url = request.call_args.args[:2]
        assert url.endswith("/api/v0/transfers/downloads/batches")
        assert request.call_args.kwargs["json"] == {
            "username": "peer",
            "files": [{"filename": "a.epub", "size": 7}],
            "options": {"destination": "shelfmark/task-1"},
        }
        assert result["batch_id"] == "b-1"
        assert [t["id"] for t in result["transfers"]] == ["t1"]
        assert result["failures"] == []

    def test_enqueue_batch_returns_the_files_slskd_refused(self):
        client = self._client()
        reply = {
            "batch": {"id": "b-1", "transfers": []},
            "failures": [{"filename": "a.epub", "message": "already queued"}],
        }
        with patch.object(client._session, "request", return_value=_make_response(reply, 207)):
            result = client.enqueue_batch("peer", [{"filename": "a.epub", "size": 7}], "d")

        assert result["failures"] == [{"filename": "a.epub", "message": "already queued"}]

    @pytest.mark.parametrize("status", [404, 405])
    def test_a_slskd_without_the_batch_endpoint_is_reported_as_unsupported(self, status):
        client = self._client()
        with (
            patch.object(client._session, "request", return_value=_make_response(status=status)),
            pytest.raises(SlskdBatchUnsupportedError),
        ):
            client.enqueue_batch("peer", [{"filename": "a.epub", "size": 7}], "d")

    def test_other_enqueue_errors_are_not_mistaken_for_an_old_slskd(self):
        client = self._client()
        with (
            patch.object(client._session, "request", return_value=_make_response(status=400)),
            pytest.raises(requests.exceptions.HTTPError),
        ):
            client.enqueue_batch("peer", [{"filename": "a.epub", "size": 7}], "d")

    def test_get_batch_returns_the_batch_and_none_when_it_is_gone(self):
        client = self._client()
        body = {"id": "b-1", "transfers": [{"id": "t1"}]}
        with patch.object(
            client._session,
            "request",
            side_effect=[_make_response(body), _make_response(status=404)],
        ) as request:
            found = client.get_batch("b-1")
            gone = client.get_batch("b-2")

        assert request.call_args_list[0].args[1].endswith("/transfers/downloads/batches/b-1")
        assert found == body
        assert gone is None

    def test_get_events_reads_the_newest_events(self):
        client = self._client()
        events = [{"type": "DownloadFileComplete", "data": "{}"}]
        with patch.object(
            client._session, "request", return_value=_make_response(events)
        ) as request:
            got = client.get_events(limit=50)

        assert got == events
        assert request.call_args.args[1].endswith("/api/v0/events")
        assert request.call_args.kwargs["params"] == {"offset": 0, "limit": 50}

    def test_get_events_tolerates_a_slskd_without_the_endpoint(self):
        client = self._client()
        with patch.object(client._session, "request", return_value=_make_response(status=404)):
            assert client.get_events() == []
