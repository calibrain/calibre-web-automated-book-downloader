"""Unit tests for the Newznab API client."""

from unittest.mock import MagicMock, patch

import requests

from shelfmark.release_sources.newznab.api import NewznabClient

# ── helpers ────────────────────────────────────────────────────────────────────

NZB_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
     xmlns:newznab="http://www.newznab.com/DTD/2010/feeds/attributes/">
  <channel>
    <title>My Indexer</title>
    <item>
      <title>Some Book (2024) [EPUB]</title>
      <guid>https://indexer.example.com/nzb/1</guid>
      <link>https://indexer.example.com/nzb/1?apikey=secret</link>
      <pubDate>Sun, 01 Jan 2024 00:00:00 +0000</pubDate>
      <size>2097152</size>
      <enclosure url="https://indexer.example.com/nzb/1?apikey=secret"
                 type="application/x-nzb" />
      <category>7000</category>
      <newznab:attr name="grabs" value="12" />
    </item>
  </channel>
</rss>"""

CAPS_XML = """<?xml version="1.0"?>
<caps>
  <server title="NZBHydra2" version="5.0.0"/>
</caps>"""


def _make_response(text: str, status: int = 200) -> MagicMock:
    r = MagicMock(spec=requests.Response)
    r.status_code = status
    r.text = text
    r.ok = status < 400
    r.raise_for_status = MagicMock()
    if status >= 400:
        r.raise_for_status.side_effect = requests.exceptions.HTTPError(response=r)
    return r


# ── URL construction ────────────────────────────────────────────────────────────


class TestApiUrl:
    def test_appends_api_path(self):
        client = NewznabClient("http://nzbhydra:5076", "key")
        assert client._api_url() == "http://nzbhydra:5076/api"

    def test_does_not_double_append(self):
        client = NewznabClient("http://nzbhydra:5076/api", "key")
        assert client._api_url() == "http://nzbhydra:5076/api"

    def test_strips_trailing_slash(self):
        client = NewznabClient("http://nzbhydra:5076/", "key")
        assert client._api_url() == "http://nzbhydra:5076/api"


# ── test_connection ─────────────────────────────────────────────────────────────


class TestTestConnection:
    def test_success_returns_true_with_title(self):
        client = NewznabClient("http://nzbhydra:5076", "key")
        with patch.object(client, "_get", return_value=_make_response(CAPS_XML)):
            ok, msg = client.test_connection()
        assert ok is True
        assert "NZBHydra2" in msg

    def test_connection_error_returns_false(self):
        client = NewznabClient("http://nzbhydra:5076", "key")
        with patch.object(
            client,
            "_get",
            side_effect=requests.exceptions.ConnectionError("refused"),
        ):
            ok, msg = client.test_connection()
        assert ok is False
        assert "connect" in msg.lower()

    def test_401_returns_api_key_error(self):
        client = NewznabClient("http://nzbhydra:5076", "key")
        fake_resp = _make_response("", status=401)
        with patch.object(
            client,
            "_get",
            side_effect=requests.exceptions.HTTPError(response=fake_resp),
        ):
            ok, msg = client.test_connection()
        assert ok is False
        assert "api key" in msg.lower()

    def test_generic_exception_returns_false(self):
        client = NewznabClient("http://nzbhydra:5076", "key")
        with patch.object(
            client,
            "_get",
            side_effect=requests.exceptions.Timeout("oops"),
        ):
            ok, msg = client.test_connection()
        assert ok is False
        assert "oops" in msg.lower()

    def test_caps_without_title_still_succeeds(self):
        client = NewznabClient("http://nzbhydra:5076", "key")
        caps_no_title = "<?xml version='1.0'?><caps/>"
        with patch.object(client, "_get", return_value=_make_response(caps_no_title)):
            ok, msg = client.test_connection()
        assert ok is True
        assert msg  # some non-empty message


# ── search ──────────────────────────────────────────────────────────────────────


class TestSearch:
    def test_empty_query_returns_empty(self):
        client = NewznabClient("http://nzbhydra:5076", "key")
        results = client.search(query="")
        assert results == []

    def test_parses_nzb_xml(self):
        client = NewznabClient("http://nzbhydra:5076", "key")
        with patch.object(client, "_get", return_value=_make_response(NZB_XML)):
            results = client.search(query="Some Book")
        assert len(results) == 1
        r = results[0]
        assert r["title"] == "Some Book (2024) [EPUB]"
        assert r["protocol"] == "usenet"
        assert r["size"] == 2097152
        assert r["downloadUrl"] == "https://indexer.example.com/nzb/1?apikey=secret"

    def test_sends_category_param(self):
        client = NewznabClient("http://nzbhydra:5076", "key")
        captured: list = []

        def fake_get(params, accept_xml=False):
            captured.append(params.copy())
            return _make_response(NZB_XML)

        with patch.object(client, "_get", side_effect=fake_get):
            client.search(query="book", categories=[7000, 3030])

        assert len(captured) == 1
        assert captured[0]["cat"] == "7000,3030"

    def test_omits_category_when_none(self):
        client = NewznabClient("http://nzbhydra:5076", "key")
        captured: list = []

        def fake_get(params, accept_xml=False):
            captured.append(params.copy())
            return _make_response(NZB_XML)

        with patch.object(client, "_get", side_effect=fake_get):
            client.search(query="book", categories=None)

        assert "cat" not in captured[0]

    def test_returns_empty_on_request_error(self):
        client = NewznabClient("http://nzbhydra:5076", "key")
        with patch.object(
            client,
            "_get",
            side_effect=requests.exceptions.ConnectionError("down"),
        ):
            results = client.search(query="book")
        assert results == []

    def test_returns_empty_on_malformed_xml(self):
        client = NewznabClient("http://nzbhydra:5076", "key")
        with patch.object(client, "_get", return_value=_make_response("not xml at all")):
            results = client.search(query="book")
        assert results == []

    def test_includes_apikey_in_request(self):
        """The apikey is injected by _get() into the outgoing HTTP request."""
        client = NewznabClient("http://nzbhydra:5076", "mykey")
        captured_params: list = []

        def fake_session_get(url, params=None, **kwargs):
            captured_params.append(dict(params or {}))
            r = _make_response(NZB_XML)
            return r

        with patch.object(client._session, "get", side_effect=fake_session_get):
            client.search(query="test")

        assert len(captured_params) == 1
        assert captured_params[0].get("apikey") == "mykey"

    def test_uses_book_search_type_when_specified(self):
        client = NewznabClient("http://nzbhydra:5076", "key")
        captured: list = []

        def fake_get(params, accept_xml=False):
            captured.append(params.copy())
            return _make_response(NZB_XML)

        with patch.object(client, "_get", side_effect=fake_get):
            client.search(query="book", search_type="book")

        assert captured[0]["t"] == "book"


class TestRequestLogging:
    def test_debug_line_formats_and_leaves_the_api_key_out(self):
        client = NewznabClient("http://nzbhydra:5076", "s3cret")
        response = MagicMock(status_code=200, text=CAPS_XML, content=CAPS_XML.encode())
        response.raise_for_status = MagicMock()

        with (
            patch.object(client._session, "get", return_value=response),
            patch("shelfmark.release_sources.newznab.api.logger") as log,
        ):
            client._get({"t": "caps"})

        fmt, *args = log.debug.call_args.args
        line = fmt % tuple(args)
        assert "'apikey'" in line
        assert "s3cret" not in line
