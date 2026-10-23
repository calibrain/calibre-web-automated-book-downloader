"""Unit tests for the Jackett release source, client and handler."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from shelfmark.core.config import config
from shelfmark.core.models import DownloadTask
from shelfmark.core.search_plan import ReleaseSearchPlan, ReleaseSearchVariant
from shelfmark.metadata_providers import BookMetadata
from shelfmark.release_sources import ReleaseProtocol, SourceUnavailableError
from shelfmark.release_sources.jackett.api import JackettClient, JackettSearchError
from shelfmark.release_sources.jackett.handler import JackettHandler
from shelfmark.release_sources.jackett.source import JackettSource
from shelfmark.release_sources.prowlarr.cache import cache_release, remove_release

TORZNAB_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:torznab="http://torznab.com/schemas/2015/feed">
 <channel>
  <item>
   <title>Frank Herbert - Dune.epub</title>
   <guid>https://tracker.example/details/42</guid>
   <jackettindexer id="mytracker">My Tracker</jackettindexer>
   <link>http://jackett:9117/dl/mytracker/?jackett_apikey=k&amp;path=abc</link>
   <size>1048576</size>
   <enclosure url="http://jackett:9117/dl/mytracker/?path=abc" length="1048576" type="application/x-bittorrent"/>
   <category>7020</category>
   <torznab:attr name="seeders" value="12"/>
   <torznab:attr name="peers" value="15"/>
   <torznab:attr name="magneturl" value="magnet:?xt=urn:btih:abc"/>
   <torznab:attr name="infohash" value="abc"/>
  </item>
 </channel>
</rss>"""

INDEXERS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<indexers>
  <indexer id="a" configured="true"><title>A</title></indexer>
  <indexer id="b" configured="false"><title>B</title></indexer>
</indexers>"""

ERROR_XML = '<error code="100" description="Invalid API Key" />'


def _response(text: str = "", status: int = 200, json_data=None) -> MagicMock:
    resp = MagicMock()
    resp.text = text
    resp.ok = status < 400
    resp.status_code = status
    resp.json.return_value = json_data
    if status >= 400:
        resp.raise_for_status.side_effect = requests.exceptions.HTTPError(response=resp)
    return resp


def _client() -> JackettClient:
    return JackettClient("http://jackett:9117", "key", indexer_timeout=30)


def _book() -> BookMetadata:
    return BookMetadata(
        provider="hardcover", provider_id="1", title="Dune", authors=["Frank Herbert"]
    )


def _plan(**kwargs) -> ReleaseSearchPlan:
    variants = [ReleaseSearchVariant(title="Dune", author="Frank Herbert")]
    return ReleaseSearchPlan(
        languages=None,
        isbn_candidates=[],
        author="Frank Herbert",
        title_variants=variants,
        grouped_title_variants=variants,
        **kwargs,
    )


@pytest.fixture
def jackett_config(monkeypatch):
    values = {
        "JACKETT_ENABLED": True,
        "JACKETT_URL": "http://jackett:9117",
        "JACKETT_API_KEY": "key",
    }
    monkeypatch.setattr(config, "get", lambda key, default=None: values.get(key, default))
    return values


class TestClient:
    def test_search_parses_jackett_items(self):
        client = _client()
        with patch.object(client._session, "get", return_value=_response(TORZNAB_XML)) as get:
            results = client.torznab_search(query="Dune", categories=[7000])

        url = get.call_args.args[0]
        assert url == "http://jackett:9117/api/v2.0/indexers/all/results/torznab/api"
        params = get.call_args.kwargs["params"]
        assert params["apikey"] == "key"
        assert params["cat"] == "7000"
        assert results[0]["indexer"] == "My Tracker"
        assert results[0]["indexerKey"] == "mytracker"
        assert results[0]["protocol"] == "torrent"

    def test_error_document_raises(self):
        client = _client()
        with (
            patch.object(client._session, "get", return_value=_response(ERROR_XML)),
            pytest.raises(JackettSearchError, match="Invalid API Key"),
        ):
            client.torznab_search(query="Dune")

    def test_timeout_raises_search_error(self):
        client = _client()
        with (
            patch.object(client._session, "get", side_effect=requests.exceptions.Timeout()),
            pytest.raises(JackettSearchError, match="did not respond"),
        ):
            client.torznab_search(query="Dune")

    def test_connection_reports_invalid_key(self):
        client = _client()
        with patch.object(client._session, "get", return_value=_response(ERROR_XML)):
            assert client.test_connection() == (False, "Invalid API key")

    def test_connection_success_counts_indexers(self):
        client = _client()
        responses = [
            _response("<caps/>"),
            _response(INDEXERS_XML),
        ]
        with patch.object(client._session, "get", side_effect=responses):
            ok, message = client.test_connection()
        assert ok
        assert "1 indexers" in message

    def test_get_indexers_uses_torznab_listing_and_skips_unconfigured(self):
        client = _client()
        with patch.object(client._session, "get", return_value=_response(INDEXERS_XML)) as get:
            indexers = client.get_indexers()

        assert get.call_args.kwargs["params"]["t"] == "indexers"
        assert "/api/v2.0/indexers/all/results/torznab/api" in get.call_args.args[0]
        assert indexers == [{"id": "a", "name": "A"}]

    def test_get_indexers_raises_on_non_xml_when_requested(self):
        client = _client()
        with (
            patch.object(client._session, "get", return_value=_response("<html>login")),
            pytest.raises(ValueError, match="Unexpected"),
        ):
            client.get_indexers(raise_on_error=True)


class TestSource:
    def test_search_builds_namespaced_torrent_release(self, jackett_config):
        client = MagicMock()
        client.indexer_timeout = 30
        client.torznab_search.return_value = _parsed_results()
        with patch.object(JackettSource, "_get_client", return_value=client):
            releases = JackettSource().search(_book(), _plan())

        assert len(releases) == 1
        release = releases[0]
        assert release.source == "jackett"
        assert release.source_id == "jackett:mytracker:https://tracker.example/details/42"
        assert release.protocol == ReleaseProtocol.TORRENT
        assert release.indexer == "My Tracker"
        assert release.peers == "12 / 3"
        # Searches the aggregate when no indexers are selected.
        assert client.torznab_search.call_args.kwargs["indexer_id"] == "all"
        remove_release(release.source_id)

    def test_selected_indexers_are_searched_individually(self, jackett_config):
        jackett_config["JACKETT_INDEXERS"] = ["a", "b"]
        client = MagicMock()
        client.indexer_timeout = 30
        client.torznab_search.return_value = []
        with patch.object(JackettSource, "_get_client", return_value=client):
            JackettSource().search(_book(), _plan())

        searched = [c.kwargs["indexer_id"] for c in client.torznab_search.call_args_list]
        assert searched == ["a", "b"]

    def test_filter_names_resolve_to_ids(self, jackett_config):
        client = MagicMock()
        client.indexer_timeout = 30
        client.get_indexers.return_value = [{"id": "a", "name": "Alpha"}]
        client.torznab_search.return_value = []
        with patch.object(JackettSource, "_get_client", return_value=client):
            JackettSource().search(_book(), _plan(indexers=["Alpha"]))

        assert client.torznab_search.call_args.kwargs["indexer_id"] == "a"

    def test_filter_matching_no_indexers_searches_nothing(self, jackett_config):
        client = MagicMock()
        client.indexer_timeout = 30
        client.get_indexers.return_value = [{"id": "a", "name": "Alpha"}]
        with patch.object(JackettSource, "_get_client", return_value=client):
            results = JackettSource().search(_book(), _plan(indexers=["Gone"]))

        assert results == []
        client.torznab_search.assert_not_called()

    def test_all_failures_raise_instead_of_returning_empty(self, jackett_config):
        client = MagicMock()
        client.indexer_timeout = 30
        client.torznab_search.side_effect = JackettSearchError("boom")
        with (
            patch.object(JackettSource, "_get_client", return_value=client),
            pytest.raises(SourceUnavailableError),
        ):
            JackettSource().search(_book(), _plan())

    def test_auto_expand_retries_without_categories(self, jackett_config):
        jackett_config["JACKETT_AUTO_EXPAND"] = True
        client = MagicMock()
        client.indexer_timeout = 30
        client.torznab_search.return_value = []
        with patch.object(JackettSource, "_get_client", return_value=client):
            JackettSource().search(_book(), _plan())

        cats = [c.kwargs["categories"] for c in client.torznab_search.call_args_list]
        assert cats == [[7000], None]

    def test_is_available_requires_enabled_url_and_key(self, jackett_config):
        assert JackettSource().is_available()
        jackett_config["JACKETT_API_KEY"] = ""
        assert not JackettSource().is_available()


def _parsed_results() -> list[dict]:
    from shelfmark.release_sources.prowlarr.torznab import parse_torznab_xml

    return parse_torznab_xml(TORZNAB_XML)


class TestHandler:
    def _task(self) -> DownloadTask:
        return DownloadTask(task_id="jackett:mytracker:1", source="jackett", title="Dune")

    def test_resolves_magnet_for_torrent(self):
        result = {**_parsed_results()[0], "magnetUrl": "magnet:?xt=urn:btih:abc"}
        cache_release("jackett:mytracker:1", result)
        status = MagicMock()
        with patch(
            "shelfmark.release_sources.jackett.handler.client_prefers_torrent_file",
            return_value=False,
        ):
            request = JackettHandler()._resolve_download(self._task(), status)
        remove_release("jackett:mytracker:1")

        assert request is not None
        assert request.protocol == "torrent"
        assert request.url.startswith("magnet:")
        assert request.expected_hash == "abc"

    def test_cache_miss_reports_error(self):
        status = MagicMock()
        assert JackettHandler()._resolve_download(self._task(), status) is None
        status.assert_called_once()
        assert status.call_args.args[0] == "error"

    def test_cache_miss_restores_from_retry_fields(self):
        task = self._task()
        task.retry_download_url = "http://jackett:9117/dl/mytracker/?path=abc"
        task.retry_download_protocol = "torrent"
        request = JackettHandler()._resolve_download(task, MagicMock())
        assert request is not None
        assert request.url == task.retry_download_url
