"""Only an admin may use the metadata provider's own account (Hardcover lists and shelves)."""

from __future__ import annotations

import importlib
from unittest.mock import patch

import pytest

from shelfmark.metadata_providers.hardcover import HardcoverProvider


@pytest.fixture(scope="module")
def main_module():
    """Import `shelfmark.main` with background startup disabled."""
    with patch("shelfmark.download.orchestrator.start"):
        import shelfmark.main as main

        importlib.reload(main)
        return main


@pytest.fixture
def provider(monkeypatch):
    instance = HardcoverProvider(api_key="test-token")
    calls: dict[str, list] = {"searched": [], "targets": [], "batch": [], "set": [], "options": []}

    def _search_paginated(options):
        from shelfmark.metadata_providers import SearchResult

        calls["searched"].append(options)
        return SearchResult(books=[], page=options.page, total_found=0, has_more=False)

    monkeypatch.setattr(instance, "search_paginated", _search_paginated)
    monkeypatch.setattr(instance, "is_available", lambda: True)
    monkeypatch.setattr(
        instance,
        "get_book_targets",
        lambda book_id: calls["targets"].append(book_id) or [{"value": "list:1", "label": "Mine"}],
    )
    monkeypatch.setattr(
        instance,
        "get_book_targets_batch",
        lambda ids: calls["batch"].append(ids) or {i: [{"value": "list:1"}] for i in ids},
    )
    monkeypatch.setattr(
        instance,
        "set_book_target_state",
        lambda book_id, target, *, selected: (
            calls["set"].append((book_id, target, selected)) or {"changed": True}
        ),
    )
    monkeypatch.setattr(
        instance,
        "get_search_field_options",
        lambda field_key, query=None: (
            calls["options"].append(field_key) or [{"value": "status:1", "label": "Want to Read"}]
        ),
    )
    instance.calls = calls  # type: ignore[attr-defined]
    return instance


@pytest.fixture
def client(main_module, provider):
    with (
        patch("shelfmark.metadata_providers.get_provider", return_value=provider),
        patch("shelfmark.metadata_providers.is_provider_registered", return_value=True),
        patch("shelfmark.metadata_providers.is_provider_enabled", return_value=True),
        patch("shelfmark.metadata_providers.get_provider_kwargs", return_value={}),
    ):
        yield main_module.app.test_client()


def _session(client, *, is_admin: bool) -> None:
    with client.session_transaction() as sess:
        sess["user_id"] = "someone"
        sess["is_admin"] = is_admin


def _as(main_module, client, *, is_admin: bool, auth_mode: str = "builtin"):
    _session(client, is_admin=is_admin)
    return patch.object(main_module, "get_auth_mode", return_value=auth_mode)


class TestTargets:
    def test_a_user_sees_no_targets_and_the_account_is_not_asked(
        self, main_module, client, provider
    ) -> None:
        with _as(main_module, client, is_admin=False):
            single = client.get("/api/metadata/book/hardcover/42/targets")
            batch = client.post(
                "/api/metadata/book/hardcover/targets/batch", json={"book_ids": [1, 2]}
            )

        assert single.json == {"options": []}
        assert batch.json == {"results": {"1": [], "2": []}}
        assert provider.calls["targets"] == []
        assert provider.calls["batch"] == []

    def test_a_user_cannot_change_a_list(self, main_module, client, provider) -> None:
        with _as(main_module, client, is_admin=False):
            resp = client.put(
                "/api/metadata/book/hardcover/42/targets",
                json={"target": "list:1", "selected": True},
            )

        assert resp.status_code == 403
        assert provider.calls["set"] == []

    def test_an_admin_keeps_targets(self, main_module, client, provider) -> None:
        with _as(main_module, client, is_admin=True):
            single = client.get("/api/metadata/book/hardcover/42/targets")
            put = client.put(
                "/api/metadata/book/hardcover/42/targets",
                json={"target": "list:1", "selected": True},
            )

        assert single.json == {"options": [{"value": "list:1", "label": "Mine"}]}
        assert put.status_code == 200
        assert provider.calls["set"] == [("42", "list:1", True)]

    def test_with_no_authentication_the_one_user_keeps_targets(
        self, main_module, client, provider
    ) -> None:
        with _as(main_module, client, is_admin=False, auth_mode="none"):
            single = client.get("/api/metadata/book/hardcover/42/targets")

        assert single.json == {"options": [{"value": "list:1", "label": "Mine"}]}


class TestListField:
    def test_a_user_gets_no_list_options(self, main_module, client, provider) -> None:
        with _as(main_module, client, is_admin=False):
            resp = client.get("/api/metadata/field-options?provider=hardcover&field=hardcover_list")

        assert resp.json == {"options": []}
        assert provider.calls["options"] == []

    def test_a_user_still_gets_public_suggestions(self, main_module, client, provider) -> None:
        with _as(main_module, client, is_admin=False):
            resp = client.get("/api/metadata/field-options?provider=hardcover&field=author&query=x")

        assert resp.json["options"]
        assert provider.calls["options"] == ["author"]

    def test_an_admin_gets_the_list_options(self, main_module, client, provider) -> None:
        with _as(main_module, client, is_admin=True):
            resp = client.get("/api/metadata/field-options?provider=hardcover&field=hardcover_list")

        assert resp.json["options"]

    def test_a_user_cannot_browse_a_list_by_asking_for_it(
        self, main_module, client, provider
    ) -> None:
        with _as(main_module, client, is_admin=False):
            client.get("/api/metadata/search?provider=hardcover&hardcover_list=status:1&query=dune")

        (options,) = provider.calls["searched"]
        assert "hardcover_list" not in options.fields

    def test_a_user_can_still_search_with_public_fields(
        self, main_module, client, provider
    ) -> None:
        with _as(main_module, client, is_admin=False):
            client.get("/api/metadata/search?provider=hardcover&author=id:1&query=dune")

        (options,) = provider.calls["searched"]
        assert options.fields == {"author": "id:1"}

    def test_an_admin_can_browse_a_list(self, main_module, client, provider) -> None:
        with _as(main_module, client, is_admin=True):
            client.get("/api/metadata/search?provider=hardcover&hardcover_list=status:1&query=dune")

        (options,) = provider.calls["searched"]
        assert options.fields["hardcover_list"] == "status:1"
