"""The metadata search and book endpoints carry per-format library ownership."""

from __future__ import annotations

import importlib
from unittest.mock import patch

import pytest

from shelfmark.metadata_providers import BookMetadata, SearchResult


@pytest.fixture(scope="module")
def main_module():
    with patch("shelfmark.download.orchestrator.start"):
        import shelfmark.main as main

        importlib.reload(main)
        return main


@pytest.fixture
def client(main_module):
    return main_module.app.test_client()


class _StubProvider:
    name = "stub"
    display_name = "Stub"
    search_fields: list[object] = []

    def __init__(self, books: list[BookMetadata]) -> None:
        self._books = books

    def is_available(self) -> bool:
        return True

    def search_paginated(self, options):
        return SearchResult(books=self._books, page=1, total_found=len(self._books), has_more=False)

    def get_book(self, book_id: str) -> BookMetadata | None:
        return next((b for b in self._books if b.provider_id == book_id), None)


def _search(main_module, client, ownership):
    book = BookMetadata(provider="stub", provider_id="42", title="Dungeon Crawler Carl")
    stub = _StubProvider([book])
    with (
        patch.object(main_module, "get_auth_mode", return_value="none"),
        patch("shelfmark.metadata_providers.is_provider_registered", return_value=True),
        patch("shelfmark.metadata_providers.is_provider_enabled", return_value=True),
        patch("shelfmark.metadata_providers.get_provider_kwargs", return_value={}),
        patch("shelfmark.metadata_providers.get_provider", return_value=stub),
        patch("shelfmark.metadata_providers.get_configured_provider", return_value=stub),
        patch("shelfmark.core.library_index.ownership", ownership),
    ):
        response = client.get("/api/metadata/search?query=carl&provider=stub")
    assert response.status_code == 200, response.get_json()
    return response.get_json()["books"][0]


def test_search_results_carry_library_ownership(main_module, client):
    result = _search(main_module, client, lambda book: {"ebook": True, "audiobook": False})

    assert result["library"] == {"ebook": True, "audiobook": False}


def test_search_results_omit_library_when_no_check_is_enabled(main_module, client):
    result = _search(main_module, client, lambda book: None)

    assert "library" not in result


def test_search_passes_the_logged_in_user_to_the_provider(main_module, client):
    # Providers may resolve per-user settings, e.g. DNB's book languages
    seen = []

    class _RecordingProvider(_StubProvider):
        def search_paginated(self, options):
            seen.append(options.user_id)
            return super().search_paginated(options)

    with client.session_transaction() as session:
        session["db_user_id"] = 7
    with (
        patch.object(main_module, "get_auth_mode", return_value="none"),
        patch("shelfmark.metadata_providers.is_provider_registered", return_value=True),
        patch("shelfmark.metadata_providers.is_provider_enabled", return_value=True),
        patch("shelfmark.metadata_providers.get_provider_kwargs", return_value={}),
        patch("shelfmark.metadata_providers.get_provider", return_value=_RecordingProvider([])),
    ):
        response = client.get("/api/metadata/search?query=carl&provider=stub")

    assert response.status_code == 200, response.get_json()
    assert seen == [7]
