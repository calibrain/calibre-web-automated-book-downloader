"""GET /api/releases searches for the title the user picked, not the provider's original.

A search result can carry a translated title the provider's own book record does not
use: Hardcover lists "Kinder des Nebels" only as an alternative title of "Mistborn:
The Final Empire", and its `get_book()` derives `search_title="The Final Empire"`
from the original. The release modal sends the title it shows; that title has to win
over the provider's `search_title`, or a German search looks for English releases.
"""

from __future__ import annotations

import importlib
from unittest.mock import patch

import pytest

from shelfmark.metadata_providers import BookMetadata


@pytest.fixture(scope="module")
def main_module():
    with patch("shelfmark.download.orchestrator.start"):
        import shelfmark.main as main

        importlib.reload(main)
        return main


@pytest.fixture
def client(main_module):
    return main_module.app.test_client()


def _authenticate(client) -> None:
    with client.session_transaction() as sess:
        sess["user_id"] = "alice"
        sess["is_admin"] = False
        sess["db_user_id"] = 7


class _Provider:
    def get_book(self, book_id: str) -> BookMetadata:
        return BookMetadata(
            provider="hardcover",
            provider_id=book_id,
            provider_display_name="Hardcover",
            title="Mistborn: The Final Empire",
            search_title="The Final Empire",
            authors=["Brandon Sanderson"],
        )


def _searched_titles(client, main_module, title: str) -> list[str]:
    """Return the title variants the release source was asked to search for."""
    searched: list[str] = []

    class _Source:
        def search(self, book, plan, *, expand_search=False, content_type="ebook"):
            searched.extend(variant.title for variant in plan.title_variants)
            return []

        def get_column_config(self):
            from shelfmark.release_sources import _default_column_config

            return _default_column_config()

    with (
        patch.object(main_module, "get_auth_mode", return_value="none"),
        patch("shelfmark.metadata_providers.is_provider_registered", return_value=True),
        patch("shelfmark.metadata_providers.get_provider_kwargs", return_value={}),
        patch("shelfmark.metadata_providers.get_provider", return_value=_Provider()),
        patch(
            "shelfmark.release_sources.list_available_sources",
            return_value=[{"name": "direct_download", "enabled": True}],
        ),
        patch("shelfmark.release_sources.get_source", return_value=_Source()),
        patch("shelfmark.release_sources.source_results_are_releases", return_value=False),
    ):
        resp = client.get(
            "/api/releases",
            query_string={"provider": "hardcover", "book_id": "369692", "title": title},
        )
    assert resp.status_code == 200
    return searched


def test_searches_the_translated_title_the_user_picked(client, main_module):
    _authenticate(client)

    searched = _searched_titles(client, main_module, "Kinder des Nebels")

    assert searched[0] == "Kinder des Nebels"
    assert "The Final Empire" not in searched


def test_keeps_the_provider_search_title_for_the_original_title(client, main_module):
    _authenticate(client)

    searched = _searched_titles(client, main_module, "Mistborn: The Final Empire")

    assert searched[0] == "The Final Empire"
