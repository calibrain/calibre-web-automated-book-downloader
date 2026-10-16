from __future__ import annotations

from typing import Any

import pytest
import requests

from shelfmark.core.cache import get_metadata_cache
from shelfmark.core.search_plan import build_release_search_plan
from shelfmark.metadata_providers import MetadataSearchOptions, SearchType, wikidata
from shelfmark.metadata_providers import openlibrary as ol

TITLES = {
    "en": "The Secret History",
    "uk": "Таємна історія",
    "pl": "Tajemna historia",
    "pt-br": "A História Secreta",
    "zh-hans": "校园秘史",
}


class FakeResponse:
    def __init__(self, data: dict[str, Any], status_code: int = 200) -> None:
        self.data = data
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)

    def json(self) -> dict[str, Any]:
        return self.data


class FakeOpenLibrary:
    """Works by id and search results by (title, author) params; records every request."""

    def __init__(
        self,
        works: dict[str, dict[str, Any]] | None = None,
        docs: dict[tuple[str, str], list[dict[str, Any]]] | None = None,
    ) -> None:
        self.works = works or {}
        self.docs = docs or {}
        self.requests: list[str] = []

    def __call__(self, url: str, params: dict[str, Any] | None = None, **_: Any) -> FakeResponse:
        params = params or {}
        if url.endswith("/search.json"):
            key = (params.get("title", ""), params.get("author", ""))
            self.requests.append(f"search:{key[0]}|{key[1]}|{params.get('q', '')}")
            return FakeResponse({"docs": self.docs.get(key, [])})
        work_id = url.rsplit("/", 1)[-1].removesuffix(".json")
        self.requests.append(f"work:{work_id}")
        if work_id not in self.works:
            return FakeResponse({"error": "notfound"}, status_code=404)
        return FakeResponse(self.works[work_id])


SECRET_HISTORY = {
    "title": "The Secret History",
    "authors": [{"author": {"key": "/authors/OL5A"}}],
}


def _provider(monkeypatch: pytest.MonkeyPatch, api: FakeOpenLibrary) -> ol.OpenLibraryProvider:
    get_metadata_cache().clear()
    prov = ol.OpenLibraryProvider()
    monkeypatch.setattr(prov.session, "get", api)
    monkeypatch.setattr(prov, "_get_author_name", lambda _key: "Donna Tartt")
    return prov


def _enable(monkeypatch: pytest.MonkeyPatch, *, on: bool) -> None:
    monkeypatch.setattr(ol, "_wikidata_enabled", lambda: on)


def _never(*_: Any, **__: Any) -> Any:
    raise AssertionError("Wikidata must not be asked")


def _work(**kwargs: Any) -> wikidata.WikidataWork:
    base: dict[str, Any] = {
        "qid": "Q21",
        "ol_work_ids": ("OL1W",),
        "titles": {"uk": "Таємна історія", "en": "The Secret History"},
        "author": "Donna Tartt",
        "author_names": ("Donna Tartt", "Донна Тартт"),
    }
    return wikidata.WikidataWork(**{**base, **kwargs})


class TestGetBook:
    def test_adds_titles_in_plain_language_codes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        prov = _provider(monkeypatch, FakeOpenLibrary({"OL1W": SECRET_HISTORY}))
        _enable(monkeypatch, on=True)
        seen: list[tuple[Any, ...]] = []

        def titles(*args: Any) -> dict[str, str]:
            seen.append(args)
            return TITLES

        monkeypatch.setattr(wikidata, "localized_titles", titles)

        book = prov.get_book("OL1W")

        assert book is not None
        assert seen == [(("OL1W",), "The Secret History", "Donna Tartt", ("OL5A",))]
        # The original title is not repeated; regional variants are not language codes.
        assert book.titles_by_language == {"uk": "Таємна історія", "pl": "Tajemna historia"}

    def test_merged_id_is_offered_to_wikidata(self, monkeypatch: pytest.MonkeyPatch) -> None:
        api = FakeOpenLibrary(
            {
                "OL9W": {"type": {"key": "/type/redirect"}, "location": "/works/OL1W"},
                "OL1W": SECRET_HISTORY,
            }
        )
        prov = _provider(monkeypatch, api)
        _enable(monkeypatch, on=True)
        seen: list[tuple[Any, ...]] = []
        monkeypatch.setattr(wikidata, "localized_titles", lambda *a: seen.append(a) or {})

        book = prov.get_book("OL9W")

        assert book is not None
        assert seen[0][0] == ("OL1W", "OL9W")

    def test_off_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        prov = _provider(monkeypatch, FakeOpenLibrary({"OL1W": SECRET_HISTORY}))
        _enable(monkeypatch, on=False)
        monkeypatch.setattr(wikidata, "localized_titles", _never)

        book = prov.get_book("OL1W")

        assert book is not None
        assert book.titles_by_language == {}

    def test_wikidata_outage_keeps_the_book(self, monkeypatch: pytest.MonkeyPatch) -> None:
        prov = _provider(monkeypatch, FakeOpenLibrary({"OL1W": SECRET_HISTORY}))
        _enable(monkeypatch, on=True)

        def down(*_: Any) -> dict[str, str]:
            raise wikidata.WikidataUnavailableError("timeout")

        monkeypatch.setattr(wikidata, "localized_titles", down)

        book = prov.get_book("OL1W")

        assert book is not None
        assert book.title == "The Secret History"
        assert book.titles_by_language == {}

    def test_switching_the_setting_applies_to_cached_books(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        prov = _provider(monkeypatch, FakeOpenLibrary({"OL1W": SECRET_HISTORY}))
        monkeypatch.setattr(wikidata, "localized_titles", lambda *_: TITLES)

        _enable(monkeypatch, on=False)
        before = prov.get_book("OL1W")
        _enable(monkeypatch, on=True)
        after = prov.get_book("OL1W")

        assert before is not None
        assert after is not None
        assert before.titles_by_language == {}
        assert after.titles_by_language == {"uk": "Таємна історія", "pl": "Tajemna historia"}

    def test_wikidata_outage_is_not_remembered(self, monkeypatch: pytest.MonkeyPatch) -> None:
        prov = _provider(monkeypatch, FakeOpenLibrary({"OL1W": SECRET_HISTORY}))
        _enable(monkeypatch, on=True)

        def down(*_: Any) -> dict[str, str]:
            raise wikidata.WikidataUnavailableError("timeout")

        monkeypatch.setattr(wikidata, "localized_titles", down)
        during = prov.get_book("OL1W")
        monkeypatch.setattr(wikidata, "localized_titles", lambda *_: TITLES)
        after = prov.get_book("OL1W")

        assert during is not None
        assert after is not None
        assert during.titles_by_language == {}
        assert "uk" in after.titles_by_language

    def test_translated_title_reaches_the_release_queries(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        prov = _provider(monkeypatch, FakeOpenLibrary({"OL1W": SECRET_HISTORY}))
        _enable(monkeypatch, on=True)
        monkeypatch.setattr(wikidata, "localized_titles", lambda *_: TITLES)
        book = prov.get_book("OL1W")
        assert book is not None

        plan = build_release_search_plan(book, languages=["en", "uk"])

        assert [v.title for v in plan.title_variants] == ["The Secret History", "Таємна історія"]


class TestSearch:
    def test_non_latin_query_found_through_wikidata(self, monkeypatch: pytest.MonkeyPatch) -> None:
        prov = _provider(monkeypatch, FakeOpenLibrary({"OL1W": SECRET_HISTORY}))
        _enable(monkeypatch, on=True)
        # The work's titles come with it: no second Wikidata lookup.
        monkeypatch.setattr(wikidata, "localized_titles", _never)
        asked: list[str] = []

        def find(text: str, limit: int) -> list[wikidata.WikidataWork]:
            asked.append(text)
            return [_work()]

        monkeypatch.setattr(wikidata, "find_works", find)

        books = prov.search(MetadataSearchOptions(query="Тартт Таємна історія"))

        assert asked == ["Тартт Таємна історія"]
        assert [b.provider_id for b in books] == ["OL1W"]
        assert books[0].titles_by_language == {"uk": "Таємна історія"}

    def test_work_without_open_library_link_found_by_title_and_surname(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        docs = [
            {"key": "/works/OL32W", "title": "Zakhar Berkut", "author_name": ["Petro Kolomiets"]},
            {"key": "/works/OL856502W", "title": "Zakhar Berkut", "author_name": ["Іван Франко"]},
        ]
        api = FakeOpenLibrary(
            {"OL856502W": {"title": "Zakhar Berkut", "authors": []}},
            {("Zakhar Berkut", "Franko"): docs},
        )
        prov = _provider(monkeypatch, api)
        _enable(monkeypatch, on=True)
        franko = _work(
            qid="Q12106092",
            ol_work_ids=(),
            titles={"uk": "Захар Беркут", "en": "Zakhar Berkut"},
            author="Ivan Franko",
            author_names=("Ivan Franko", "Іван Франко"),
        )
        monkeypatch.setattr(wikidata, "find_works", lambda *_: [franko])

        books = prov.search(MetadataSearchOptions(query="Франко Захар Беркут"))

        assert [b.provider_id for b in books] == ["OL856502W"]
        assert books[0].titles_by_language == {"uk": "Захар Беркут"}
        # Open Library spells authors its own way: only the surname goes into the query.
        assert "search:Zakhar Berkut|Franko|" in api.requests

    def test_next_link_tried_when_the_first_work_is_gone(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        prov = _provider(monkeypatch, FakeOpenLibrary({"OL1W": SECRET_HISTORY}))
        _enable(monkeypatch, on=True)
        monkeypatch.setattr(
            wikidata, "find_works", lambda *_: [_work(ol_work_ids=("OL404W", "OL1W"))]
        )

        books = prov.search(MetadataSearchOptions(query="Таємна історія"))

        assert [b.provider_id for b in books] == ["OL1W"]

    @pytest.mark.parametrize(
        ("en_title", "open_library_requests"),
        [
            # Wikidata items without an English translation carry a non-Latin "en" label.
            ("Тіні забутих предків", 1),
            # A Latin title Open Library does not know: two lookups, neither falls back.
            ("Shadows of Forgotten Ancestors", 3),
        ],
    )
    def test_lookup_on_behalf_of_wikidata_never_recurses(
        self, monkeypatch: pytest.MonkeyPatch, en_title: str, open_library_requests: int
    ) -> None:
        api = FakeOpenLibrary()
        prov = _provider(monkeypatch, api)
        _enable(monkeypatch, on=True)
        work = _work(
            ol_work_ids=(),
            titles={"en": en_title, "uk": "Тіні забутих предків"},
            author="Mykhailo Kotsiubynskyi",
            author_names=("Mykhailo Kotsiubynskyi",),
        )
        calls: list[str] = []

        def find(text: str, limit: int) -> list[wikidata.WikidataWork]:
            calls.append(text)
            return [work]

        monkeypatch.setattr(wikidata, "find_works", find)

        assert prov.search(MetadataSearchOptions(query="Тіні забутих предків")) == []
        assert calls == ["Тіні забутих предків"]
        assert len(api.requests) == open_library_requests

    @pytest.mark.parametrize("query", ["The Secret History", "Tajemna historia"])
    def test_latin_query_never_asks_wikidata(
        self, monkeypatch: pytest.MonkeyPatch, query: str
    ) -> None:
        prov = _provider(monkeypatch, FakeOpenLibrary())
        _enable(monkeypatch, on=True)
        monkeypatch.setattr(wikidata, "find_works", _never)

        assert prov.search(MetadataSearchOptions(query=query)) == []

    def test_author_search_never_asks_wikidata(self, monkeypatch: pytest.MonkeyPatch) -> None:
        prov = _provider(monkeypatch, FakeOpenLibrary())
        _enable(monkeypatch, on=True)
        monkeypatch.setattr(wikidata, "find_works", _never)

        options = MetadataSearchOptions(query="Донна Тартт", search_type=SearchType.AUTHOR)
        assert prov.search(options) == []

    def test_setting_off_never_asks_wikidata(self, monkeypatch: pytest.MonkeyPatch) -> None:
        prov = _provider(monkeypatch, FakeOpenLibrary())
        _enable(monkeypatch, on=False)
        monkeypatch.setattr(wikidata, "find_works", _never)

        assert prov.search(MetadataSearchOptions(query="Таємна історія")) == []
