"""Author agreement ranks Prowlarr results; it never narrows the query (#1293).

MyAnonamouse - the only indexer Shelfmark treats as enriched - used to receive
"{title} {author}". MAM ANDs its search terms, so any difference between the
metadata provider's author spelling and the tracker's ("Timothy Ferriss" vs
"Tim Ferriss") returned nothing at all and the UI reported the book as missing.
"""

import pytest

from shelfmark.core.author_match import (
    AUTHOR_MATCH,
    AUTHOR_MISMATCH,
    AUTHOR_UNKNOWN,
    author_affinity,
)
from shelfmark.metadata_providers import BookMetadata
from shelfmark.release_sources.prowlarr.source import ProwlarrSource

MAM_INDEXER_ID = 1


class TestAuthorAffinity:
    @pytest.mark.parametrize(
        ("wanted", "offered"),
        [
            ("Timothy Ferriss", "Tim Ferriss"),
            ("Tim Ferriss", "Timothy Ferriss"),
            ("T. Ferriss", "Timothy Ferriss"),
            ("Ursula K. Le Guin", "Ursula Le Guin"),
            ("Iain M. Banks", "Iain Banks"),
            ("Frank Herbert", "Frank Herbert, Brian Herbert"),
            ("Robert Jordan Jr.", "Robert Jordan"),
            ("homer", "Homer"),
        ],
    )
    def test_same_author_spelled_differently_agrees(self, wanted, offered):
        assert author_affinity(wanted, offered) == AUTHOR_MATCH

    @pytest.mark.parametrize(
        ("wanted", "offered"),
        [
            ("Timothy Ferriss", "Frank Herbert"),
            ("Frank Herbert", "Brian Herbert"),
            ("Homer", "Virgil"),
        ],
    )
    def test_different_author_disagrees(self, wanted, offered):
        assert author_affinity(wanted, offered) == AUTHOR_MISMATCH

    @pytest.mark.parametrize(
        ("wanted", "offered"),
        [
            ("Timothy Ferriss", None),
            ("Timothy Ferriss", ""),
            ("", "Tim Ferriss"),
            (None, "Tim Ferriss"),
            ("Timothy Ferriss", {"name": "Tim Ferriss"}),
        ],
    )
    def test_missing_metadata_is_neither_agreement_nor_disagreement(self, wanted, offered):
        # An indexer that reports no author must not sort below one that reports
        # the wrong author, so this tier sits between the two.
        assert author_affinity(wanted, offered) == AUTHOR_UNKNOWN
        assert AUTHOR_MATCH < AUTHOR_UNKNOWN < AUTHOR_MISMATCH

    def test_a_surname_alone_is_not_enough_for_a_full_name(self):
        # "Ferriss" appearing under some other given name is a different person.
        assert author_affinity("Timothy Ferriss", "Bruce Ferriss") == AUTHOR_MISMATCH


class _EnrichedIndexerClient:
    """Stands in for a Prowlarr with MyAnonamouse enabled."""

    def __init__(self, search_results=None):
        self.queries: list[str] = []
        self.search_results = search_results or []
        self.indexer_timeout = 90

    def get_enabled_indexers_detailed(self, *, raise_on_error=False):
        del raise_on_error
        return [
            {
                "id": MAM_INDEXER_ID,
                "enable": True,
                "implementation": "MyAnonamouse",
                "capabilities": {
                    "categories": [
                        {"id": 7000, "subCategories": []},
                        {"id": 3030, "subCategories": []},
                    ]
                },
            }
        ]

    def torznab_search(
        self, *, indexer_id, query, categories=None, search_type="book", limit=100, offset=0
    ):
        del indexer_id, categories, search_type, limit, offset
        self.queries.append(query)
        return self.search_results

    def get_enriched_indexer_ids(self, restrict_to=None, indexers=None):
        del restrict_to, indexers
        return [MAM_INDEXER_ID]

    def get_indexer_seed_settings(self, restrict_to=None):
        del restrict_to
        return {}


def _mam_result(guid: str, author: str | None) -> dict:
    return {
        "guid": guid,
        "title": "The Tao of Seneca",
        "author": author,
        "indexerId": MAM_INDEXER_ID,
        "indexer": "MyAnonamouse",
        "protocol": "torrent",
        "size": 1048576,
        "seeders": 10,
        "leechers": 1,
        "categories": [{"id": 7020}],
        "infoUrl": f"https://tracker.example/{guid}",
    }


def _search(monkeypatch, client, *, manual_query=None):
    import shelfmark.release_sources.prowlarr.source as prowlarr_source
    from shelfmark.core.search_plan import build_release_search_plan

    values = {"PROWLARR_INDEXERS": "", "PROWLARR_AUTO_EXPAND": False}
    monkeypatch.setattr(
        prowlarr_source.config, "get", lambda key, default=None: values.get(key, default)
    )

    source = ProwlarrSource()
    monkeypatch.setattr(source, "_get_client", lambda: client)

    book = BookMetadata(
        provider="hardcover",
        provider_id="123",
        title="The Tao of Seneca",
        authors=["Timothy Ferriss"],
    )
    plan = build_release_search_plan(book, languages=["en"], manual_query=manual_query)
    return source.search(book, plan, content_type="ebook")


class TestEnrichedIndexerQuery:
    def test_enriched_indexer_is_queried_without_the_author(self, monkeypatch):
        client = _EnrichedIndexerClient()

        _search(monkeypatch, client)

        assert client.queries == ["The Tao of Seneca"]
        assert not any("Ferriss" in query for query in client.queries)


class TestAuthorOrdering:
    def test_matching_author_leads_and_mismatch_stays_visible(self, monkeypatch):
        client = _EnrichedIndexerClient(
            search_results=[
                _mam_result("other-author", "Frank Herbert"),
                _mam_result("no-author", None),
                _mam_result("right-author", "Tim Ferriss"),
            ]
        )

        results = _search(monkeypatch, client)

        # Ranked, not filtered: the wrong author is last but still reachable.
        assert [r.extra["author"] for r in results] == ["Tim Ferriss", None, "Frank Herbert"]

    def test_manual_query_is_not_reordered_against_the_metadata_author(self, monkeypatch):
        client = _EnrichedIndexerClient(
            search_results=[
                _mam_result("other-author", "Frank Herbert"),
                _mam_result("right-author", "Tim Ferriss"),
            ]
        )

        results = _search(monkeypatch, client, manual_query="tao seneca")

        assert client.queries == ["tao seneca"]
        assert [r.extra["author"] for r in results] == ["Frank Herbert", "Tim Ferriss"]
