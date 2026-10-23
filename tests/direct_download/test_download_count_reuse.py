"""Download counts on AA search results are fetched once per md5, over kept-alive connections.

`search_books` runs once per title variant, and the variants of one book mostly find the
same files, so each md5 used to be looked up again for every variant (and again whenever
the release list was reopened), each lookup on a brand new connection.
"""

from unittest.mock import MagicMock

import pytest
import requests

from shelfmark.release_sources import BrowseRecord
from shelfmark.release_sources.direct_download import annas_archive as aa


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    aa._download_count_cache.clear()
    monkeypatch.setattr(aa.network, "get_aa_base_url", lambda: "https://annas-archive.gl")
    yield
    aa._download_count_cache.clear()


def _records(*ids: str) -> list[BrowseRecord]:
    return [
        BrowseRecord(id=record_id, title=record_id, source="direct_download") for record_id in ids
    ]


def _count_lookups(monkeypatch, *, fail: bool = False) -> tuple[list[str], list[object]]:
    looked_up: list[str] = []
    sessions_built: list[object] = []

    def fake_request(_self, method, url, *_args, **_kwargs):
        looked_up.append(url.rsplit("/", 1)[-1])
        if fail:
            raise requests.exceptions.ConnectionError("offline")
        return MagicMock(status_code=200, json=MagicMock(return_value={"downloads_total": 7}))

    real_init = requests.Session.__init__

    def counting_init(self, *args, **kwargs):
        sessions_built.append(self)
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(requests.Session, "request", fake_request)
    monkeypatch.setattr(requests.Session, "__init__", counting_init)
    return looked_up, sessions_built


def test_overlapping_title_variants_look_each_md5_up_once(monkeypatch):
    looked_up, sessions_built = _count_lookups(monkeypatch)

    first = _records("aaa", "bbb")
    second = _records("bbb", "ccc")
    aa._enrich_search_results_with_downloads(first)
    aa._enrich_search_results_with_downloads(second)

    assert sorted(looked_up) == ["aaa", "bbb", "ccc"]
    assert sessions_built == [], "lookups should share one session instead of one each"
    for record in first + second:
        assert record.info == {"Downloads": ["7"]}


def test_cached_count_expires(monkeypatch):
    looked_up, _ = _count_lookups(monkeypatch)
    now = [1_000.0]
    monkeypatch.setattr("shelfmark.core.cache.time.time", lambda: now[0])

    aa._enrich_search_results_with_downloads(_records("aaa"))
    now[0] += aa._DOWNLOAD_COUNT_TTL - 1
    aa._enrich_search_results_with_downloads(_records("aaa"))
    assert looked_up == ["aaa"]

    now[0] += 2
    aa._enrich_search_results_with_downloads(_records("aaa"))
    assert looked_up == ["aaa", "aaa"]


def test_failed_lookup_is_not_cached(monkeypatch):
    looked_up, _ = _count_lookups(monkeypatch, fail=True)

    records = _records("aaa")
    aa._enrich_search_results_with_downloads(records)
    aa._enrich_search_results_with_downloads(_records("aaa"))

    assert looked_up == ["aaa", "aaa"]
    assert records[0].info is None or "Downloads" not in records[0].info
