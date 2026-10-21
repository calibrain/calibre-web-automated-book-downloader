from __future__ import annotations

from typing import Any

import pytest

from shelfmark.core.cache import get_metadata_cache
from shelfmark.metadata_providers import openlibrary as ol


class FakeResponse:
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data
        self.status_code = 200

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self.data


def _provider(
    monkeypatch: pytest.MonkeyPatch, works: dict[str, dict[str, Any]]
) -> tuple[ol.OpenLibraryProvider, list[str]]:
    get_metadata_cache().clear()
    prov = ol.OpenLibraryProvider()
    requested: list[str] = []

    def fake_get(url: str, **_: Any) -> FakeResponse:
        work_id = url.rsplit("/", 1)[-1].removesuffix(".json")
        requested.append(work_id)
        return FakeResponse(works[work_id])

    monkeypatch.setattr(prov.session, "get", fake_get)
    monkeypatch.setattr(prov, "_get_author_name", lambda _key: "George Orwell")
    return prov, requested


class TestWorkRedirects:
    def test_get_book_follows_a_merged_work(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Wikidata and old links still name OL1168091W; Open Library merged it into OL1168083W.
        prov, _ = _provider(
            monkeypatch,
            {
                "OL1168091W": {"type": {"key": "/type/redirect"}, "location": "/works/OL1168083W"},
                "OL1168083W": {
                    "title": "Nineteen Eighty-Four",
                    "authors": [{"author": {"key": "/authors/OL118077A"}}],
                },
            },
        )

        book = prov.get_book("OL1168091W")

        assert book is not None
        assert (book.provider_id, book.title) == ("OL1168083W", "Nineteen Eighty-Four")
        assert book.authors == ["George Orwell"]

    def test_a_three_hop_chain_reaches_the_work(self, monkeypatch: pytest.MonkeyPatch) -> None:
        chain = {
            "OL1W": {"type": {"key": "/type/redirect"}, "location": "/works/OL2W"},
            "OL2W": {"type": {"key": "/type/redirect"}, "location": "/works/OL3W"},
            "OL3W": {"type": {"key": "/type/redirect"}, "location": "/works/OL4W"},
            "OL4W": {"title": "Animal Farm", "authors": []},
        }
        prov, requested = _provider(monkeypatch, chain)

        book = prov.get_book("OL1W")

        assert book is not None
        assert book.provider_id == "OL4W"
        assert requested == ["OL1W", "OL2W", "OL3W", "OL4W"]

    def test_redirect_chains_are_bounded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        loop = {
            "OL1W": {"type": {"key": "/type/redirect"}, "location": "/works/OL2W"},
            "OL2W": {"type": {"key": "/type/redirect"}, "location": "/works/OL1W"},
        }
        prov, requested = _provider(monkeypatch, loop)

        assert prov.get_book("OL1W") is None
        assert len(requested) == 1 + ol.MAX_WORK_REDIRECTS

    def test_real_work_needs_one_request(self, monkeypatch: pytest.MonkeyPatch) -> None:
        prov, requested = _provider(monkeypatch, {"OL1W": {"title": "Animal Farm", "authors": []}})

        book = prov.get_book("OL1W")

        assert book is not None
        assert book.provider_id == "OL1W"
        assert requested == ["OL1W"]
