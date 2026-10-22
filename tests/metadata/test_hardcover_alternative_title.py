from typing import Any

import pytest

from shelfmark.core.cache import get_metadata_cache
from shelfmark.metadata_providers import MetadataSearchOptions
from shelfmark.metadata_providers.hardcover import HardcoverProvider

# Hardcover indexes one book per work and keeps its translations' titles in
# `alternative_titles`. The hit's `highlights` say which field - and which entry of
# an array field - matched the query. Trimmed from a live response for
# "Der Weg der Könige".
WAY_OF_KINGS = {
    "id": 978066,
    "title": "The Way of Kings, Part 1",
    "subtitle": "The Stormlight Archive Book One",
    "alternative_titles": [
        "Der Weg der Könige",
        "The Way of Kings Part One",
        "La Voie des rois, tome 1",
    ],
    "author_names": ["Brandon Sanderson"],
    "release_year": 2010,
}


def _hit(*highlighted: tuple[str, list[int]]) -> dict[str, Any]:
    return {
        "document": WAY_OF_KINGS,
        "highlights": [{"field": field, "indices": indices} for field, indices in highlighted],
    }


def _search(monkeypatch, query: str, hit: dict[str, Any]):
    provider = HardcoverProvider(api_key="test-token")
    monkeypatch.setattr(
        provider,
        "_execute_query",
        lambda _query, _variables: {"search": {"results": {"hits": [hit], "found": 1}}},
    )
    books = provider.search(MetadataSearchOptions(query=query, limit=5))
    assert len(books) == 1
    return books[0]


@pytest.fixture(autouse=True)
def _fresh_search_cache():
    """Each test stubs a different response for the same book; don't reuse results."""
    get_metadata_cache().clear()
    yield
    get_metadata_cache().clear()


class TestHardcoverMatchedAlternativeTitle:
    def test_uses_the_translated_title_that_matched(self, monkeypatch):
        """Searching a translation by name should surface that translation's title.

        The work's own title is the English original. Showing it - and handing it to
        the release search and the download - would put the user's German search
        back into English.
        """
        book = _search(monkeypatch, "Der Weg der Könige", _hit(("alternative_titles", [0])))

        assert book.title == "Der Weg der Könige"
        assert book.search_title == "Der Weg der Könige"
        # The subtitle belongs to the English title; it is not the translation's.
        assert book.subtitle is None

    def test_keeps_the_work_title_when_the_title_itself_matched(self, monkeypatch):
        book = _search(
            monkeypatch,
            "The Way of Kings Part 1",
            _hit(("title", []), ("alternative_titles", [1])),
        )

        assert book.title == "The Way of Kings, Part 1"
        assert book.subtitle == "The Stormlight Archive Book One"

    def test_keeps_the_work_title_without_highlights(self, monkeypatch):
        book = _search(monkeypatch, "Sanderson", {"document": WAY_OF_KINGS})

        assert book.title == "The Way of Kings, Part 1"
