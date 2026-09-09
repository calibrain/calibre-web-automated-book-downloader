"""Indexers in Prowlarr failure back-off are skipped, not counted as failed searches."""

from datetime import UTC, datetime

import pytest
import requests

from shelfmark.metadata_providers import BookMetadata
from shelfmark.release_sources import SourceUnavailableError
from shelfmark.release_sources.prowlarr.api import ProwlarrClient, ProwlarrSearchError
from shelfmark.release_sources.prowlarr.source import ProwlarrSource

_NOW = datetime(2026, 9, 9, 14, 30, tzinfo=UTC)


class _Client:
    indexer_timeout = 90

    def __init__(self, results, disabled=None, failing=()):
        self.results = results
        self.disabled = disabled or {}
        self.failing = set(failing)
        self.calls: list[tuple[int, object]] = []

    def get_enabled_indexers_detailed(self, *, raise_on_error=False):
        del raise_on_error
        return [
            {
                "id": indexer_id,
                "enable": True,
                "capabilities": {"categories": [{"id": 7000, "subCategories": []}]},
            }
            for indexer_id in sorted(self.results)
        ]

    def get_disabled_indexers(self):
        return dict(self.disabled)

    def torznab_search(self, *, indexer_id, query, categories=None, search_type="book", **_):
        del query, search_type
        self.calls.append((indexer_id, categories))
        if indexer_id in self.failing:
            msg = f"indexer {indexer_id} did not respond within 90s"
            raise ProwlarrSearchError(msg)
        return self.results[indexer_id]

    def get_enriched_indexer_ids(self, restrict_to=None, indexers=None):
        del restrict_to, indexers
        return []

    def get_indexer_seed_settings(self, restrict_to=None):
        del restrict_to
        return {}


def _release(indexer_id: int) -> dict:
    return {
        "guid": f"g{indexer_id}",
        "title": "Dune",
        "indexerId": indexer_id,
        "indexer": f"indexer-{indexer_id}",
        "protocol": "torrent",
        "size": 1048576,
        "seeders": 5,
    }


def _search(monkeypatch, client, config_values=None):
    import shelfmark.release_sources.prowlarr.source as prowlarr_source
    from shelfmark.core.search_plan import build_release_search_plan

    values = {"PROWLARR_INDEXERS": "", "PROWLARR_AUTO_EXPAND": False}
    values.update(config_values or {})
    monkeypatch.setattr(
        prowlarr_source.config, "get", lambda key, default=None: values.get(key, default)
    )

    source = ProwlarrSource()
    monkeypatch.setattr(source, "_get_client", lambda: client)

    book = BookMetadata(
        provider="hardcover", provider_id="123", title="Dune", authors=["Frank Herbert"]
    )
    plan = build_release_search_plan(book, languages=["en"])
    return source.search(book, plan)


class TestDisabledIndexersAreSkipped:
    def test_disabled_indexer_is_not_queried_and_empty_answers_stay_no_results(self, monkeypatch):
        client = _Client({1: [], 2: []}, disabled={1: "2026-09-09T15:00:34Z"})

        assert _search(monkeypatch, client) == []
        assert client.calls == [(2, [7000])]

    def test_disabled_indexer_does_not_hide_the_others_results(self, monkeypatch):
        client = _Client({1: [], 2: [_release(2)]}, disabled={1: "2026-09-09T15:00:34Z"})

        releases = _search(monkeypatch, client)

        assert [r.indexer for r in releases] == ["indexer-2"]
        assert client.calls == [(2, [7000])]

    def test_every_indexer_disabled_reports_that_instead_of_no_results(self, monkeypatch):
        client = _Client(
            {1: [], 2: []},
            disabled={1: "2026-09-09T15:00:34Z", 2: "2026-09-09T15:10:00Z"},
        )

        with pytest.raises(SourceUnavailableError) as excinfo:
            _search(monkeypatch, client)

        assert "disabled by Prowlarr" in str(excinfo.value)
        assert "2026-09-09T15:10:00Z" in str(excinfo.value)
        assert client.calls == []

    def test_a_real_failure_elsewhere_is_still_reported(self, monkeypatch):
        client = _Client({1: [], 2: [], 3: []}, disabled={1: "2026-09-09T15:00:34Z"}, failing={2})

        with pytest.raises(SourceUnavailableError) as excinfo:
            _search(monkeypatch, client)

        assert "1 of 2 indexer searches failed" in str(excinfo.value)

    def test_auto_expand_skips_the_disabled_indexer_on_the_retry_too(self, monkeypatch):
        client = _Client({1: [], 2: []}, disabled={1: "2026-09-09T15:00:34Z"})

        assert _search(monkeypatch, client, {"PROWLARR_AUTO_EXPAND": True}) == []
        assert client.calls == [(2, [7000]), (2, None)]

    def test_auto_expand_does_not_retry_when_nothing_was_asked(self, monkeypatch):
        client = _Client({1: []}, disabled={1: "2026-09-09T15:00:34Z"})

        with pytest.raises(SourceUnavailableError):
            _search(monkeypatch, client, {"PROWLARR_AUTO_EXPAND": True})

        assert client.calls == []

    def test_status_lookup_failure_fails_open(self, monkeypatch):
        class _NoStatusClient(_Client):
            def get_disabled_indexers(self):
                raise requests.exceptions.ConnectionError("status unavailable")

        client = _NoStatusClient({1: [], 2: []})

        assert _search(monkeypatch, client) == []
        assert client.calls == [(1, [7000]), (2, [7000])]


class TestGetDisabledIndexers:
    def _client(self, monkeypatch, payload):
        client = ProwlarrClient("http://prowlarr:9696", "key")
        monkeypatch.setattr(client, "_request", lambda *args, **kwargs: payload)
        return client

    def test_reports_indexers_whose_back_off_has_not_ended(self, monkeypatch):
        client = self._client(
            monkeypatch,
            [
                {"indexerId": 2, "disabledTill": "2026-09-09T15:00:34Z"},
                {"indexerId": 3, "disabledTill": "2026-09-09T14:00:00Z"},
                {"indexerId": 4},
                {"indexerId": "x", "disabledTill": "2026-09-09T15:00:34Z"},
                {"indexerId": 5, "disabledTill": "not a date"},
            ],
        )

        assert client.get_disabled_indexers(now=_NOW) == {2: "2026-09-09T15:00:34Z"}

    def test_naive_timestamps_are_read_as_utc(self, monkeypatch):
        client = self._client(
            monkeypatch, [{"indexerId": 2, "disabledTill": "2026-09-09T15:00:34"}]
        )

        assert client.get_disabled_indexers(now=_NOW) == {2: "2026-09-09T15:00:34"}

    def test_request_failure_reports_nothing_disabled(self, monkeypatch):
        client = ProwlarrClient("http://prowlarr:9696", "key")

        def _boom(*args, **kwargs):
            raise requests.exceptions.ConnectionError("connection refused")

        monkeypatch.setattr(client, "_request", _boom)

        assert client.get_disabled_indexers(now=_NOW) == {}
