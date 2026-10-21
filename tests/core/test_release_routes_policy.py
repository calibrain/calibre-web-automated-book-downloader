"""The request policy gates release listing, not only release download.

/api/releases, /api/releases/inspect and the release-source record route all reach
upstream sources through shared credentials (Prowlarr, qBittorrent, scrapers). A user
whose policy is request_book or blocked never sees a release list in the UI, so the
routes have to refuse them too; request_release still lists, because that user picks
the release they ask for.
"""

from __future__ import annotations

import importlib
import uuid
from unittest.mock import patch

import pytest

from shelfmark.download.postprocess.packs import PackFile
from shelfmark.release_sources import BrowseRecord


@pytest.fixture(scope="module")
def main_module():
    with patch("shelfmark.download.orchestrator.start"):
        import shelfmark.main as main

        importlib.reload(main)
        return main


@pytest.fixture
def client(main_module):
    return main_module.app.test_client()


def _login(client, main_module, *, is_admin: bool = False) -> dict:
    user = main_module.user_db.create_user(
        username=f"reader-{uuid.uuid4().hex[:8]}", role="admin" if is_admin else "user"
    )
    with client.session_transaction() as sess:
        sess["user_id"] = user["username"]
        sess["is_admin"] = is_admin
        sess["db_user_id"] = user["id"]
    return user


def _policy(
    *,
    default_ebook: str = "download",
    default_audiobook: str = "download",
    rules: list[dict] | None = None,
) -> dict:
    return {
        "REQUESTS_ENABLED": True,
        "REQUEST_POLICY_DEFAULT_EBOOK": default_ebook,
        "REQUEST_POLICY_DEFAULT_AUDIOBOOK": default_audiobook,
        "MAX_PENDING_REQUESTS_PER_USER": 20,
        "REQUESTS_ALLOW_NOTES": True,
        "REQUEST_POLICY_RULES": rules or [],
    }


def _with_policy(main_module, policy: dict):
    return (
        patch.object(main_module, "get_auth_mode", return_value="builtin"),
        patch.object(main_module, "load_users_request_policy_settings", return_value=policy),
    )


class _Source:
    def __init__(self, searched: list[str]) -> None:
        self._searched = searched

    def search(self, book, plan, *, expand_search=False, content_type="ebook"):
        self._searched.append(book.title)
        return []

    def get_record(self, record_id: str) -> BrowseRecord:
        self._searched.append(f"record:{record_id}")
        return BrowseRecord(id=record_id, title="Browsed", source="stub")

    def get_column_config(self):
        from shelfmark.release_sources import _default_column_config

        return _default_column_config()


def _list_releases(client, main_module, policy: dict, *, query: dict | None = None):
    """Drive /api/releases with stubbed sources; returns (response, titles searched)."""
    searched: list[str] = []
    sources = [
        {"name": "direct_download", "enabled": True},
        {"name": "prowlarr", "enabled": True},
    ]
    auth_patch, policy_patch = _with_policy(main_module, policy)
    with (
        auth_patch,
        policy_patch,
        patch("shelfmark.release_sources.list_available_sources", return_value=sources),
        patch("shelfmark.release_sources.get_source", return_value=_Source(searched)),
        patch("shelfmark.release_sources.source_results_are_releases", return_value=False),
    ):
        resp = client.get(
            "/api/releases",
            query_string={"provider": "manual", "book_id": "abc", "title": "Dune", **(query or {})},
        )
    return resp, searched


class TestReleaseListPolicy:
    def test_request_book_user_cannot_list_releases(self, client, main_module):
        _login(client, main_module)

        resp, searched = _list_releases(client, main_module, _policy(default_ebook="request_book"))

        assert resp.status_code == 403
        assert resp.json["code"] == "policy_requires_request"
        assert resp.json["required_mode"] == "request_book"
        assert searched == []

    def test_blocked_user_cannot_list_releases(self, client, main_module):
        _login(client, main_module)

        resp, searched = _list_releases(client, main_module, _policy(default_ebook="blocked"))

        assert resp.status_code == 403
        assert resp.json["code"] == "policy_blocked"
        assert searched == []

    def test_request_release_user_still_lists_releases(self, client, main_module):
        _login(client, main_module)

        resp, searched = _list_releases(
            client, main_module, _policy(default_ebook="request_release")
        )

        assert resp.status_code == 200
        assert searched == ["Dune", "Dune"]

    def test_policy_follows_the_requested_content_type(self, client, main_module):
        _login(client, main_module)
        policy = _policy(default_ebook="download", default_audiobook="blocked")

        ebook, _ = _list_releases(client, main_module, policy, query={"content_type": "ebook"})
        audiobook, searched = _list_releases(
            client, main_module, policy, query={"content_type": "audiobook"}
        )

        assert ebook.status_code == 200
        assert audiobook.status_code == 403
        assert searched == []

    def test_a_source_the_user_is_blocked_on_is_left_out(self, client, main_module):
        _login(client, main_module)
        policy = _policy(rules=[{"source": "prowlarr", "content_type": "*", "mode": "blocked"}])

        resp, searched = _list_releases(client, main_module, policy)

        assert resp.status_code == 200
        assert resp.json["sources_searched"] == ["direct_download"]
        assert searched == ["Dune"]

    def test_an_explicitly_requested_blocked_source_is_refused(self, client, main_module):
        _login(client, main_module)
        policy = _policy(rules=[{"source": "prowlarr", "content_type": "*", "mode": "blocked"}])

        resp, searched = _list_releases(client, main_module, policy, query={"source": "prowlarr"})

        assert resp.status_code == 403
        assert resp.json["code"] == "policy_blocked"
        assert searched == []

    def test_admin_is_not_gated(self, client, main_module):
        _login(client, main_module, is_admin=True)

        resp, searched = _list_releases(client, main_module, _policy(default_ebook="blocked"))

        assert resp.status_code == 200
        assert searched == ["Dune", "Dune"]


class _Handler:
    def __init__(self) -> None:
        self.calls = 0

    def list_files(self, release_data):
        self.calls += 1
        return [PackFile("Dune.epub", 100)]


def _inspect(client, main_module, policy: dict, *, content_type: str = "ebook"):
    handler = _Handler()
    auth_patch, policy_patch = _with_policy(main_module, policy)
    with (
        auth_patch,
        policy_patch,
        patch("shelfmark.core.release_inspect_routes.get_handler", return_value=handler),
    ):
        resp = client.post(
            "/api/releases/inspect",
            json={
                "source": "prowlarr",
                "source_id": "abc",
                "title": "Dune",
                "content_type": content_type,
            },
        )
    return resp, handler


class TestReleaseInspectPolicy:
    def test_request_book_user_cannot_inspect(self, client, main_module):
        _login(client, main_module)

        resp, handler = _inspect(client, main_module, _policy(default_ebook="request_book"))

        assert resp.status_code == 403
        assert resp.json["code"] == "policy_requires_request"
        assert handler.calls == 0

    def test_blocked_user_cannot_inspect(self, client, main_module):
        _login(client, main_module)

        resp, handler = _inspect(client, main_module, _policy(default_ebook="blocked"))

        assert resp.status_code == 403
        assert resp.json["code"] == "policy_blocked"
        assert handler.calls == 0

    def test_policy_uses_the_payload_content_type(self, client, main_module):
        _login(client, main_module)
        policy = _policy(default_ebook="blocked", default_audiobook="request_release")

        resp, handler = _inspect(client, main_module, policy, content_type="audiobook")

        assert resp.status_code == 200
        assert resp.json["inspected"] is True
        assert handler.calls == 1


def _get_record(
    client,
    main_module,
    policy: dict,
    *,
    capabilities: dict[str, set[str]],
    source: str = "audiobookbay",
):
    searched: list[str] = []
    auth_patch, policy_patch = _with_policy(main_module, policy)
    with (
        auth_patch,
        policy_patch,
        patch.object(
            main_module, "get_source_content_type_capabilities", return_value=capabilities
        ),
        patch("shelfmark.release_sources.get_source", return_value=_Source(searched)),
    ):
        resp = client.get(f"/api/release-sources/{source}/records/rec-1")
    return resp, searched


class TestReleaseSourceRecordPolicy:
    def test_blocked_user_cannot_resolve_a_record(self, client, main_module):
        _login(client, main_module)

        resp, searched = _get_record(
            client,
            main_module,
            _policy(default_audiobook="blocked"),
            capabilities={"audiobookbay": {"audiobook"}},
        )

        assert resp.status_code == 403
        assert resp.json["code"] == "policy_blocked"
        assert searched == []

    def test_request_book_user_cannot_resolve_a_record(self, client, main_module):
        _login(client, main_module)

        resp, searched = _get_record(
            client,
            main_module,
            _policy(default_audiobook="request_book"),
            capabilities={"audiobookbay": {"audiobook"}},
        )

        assert resp.status_code == 403
        assert resp.json["code"] == "policy_requires_request"
        assert resp.json["required_mode"] == "request_book"
        assert searched == []

    def test_request_book_becomes_request_release_for_a_release_source(self, client, main_module):
        """direct_download's browse rows are concrete releases, so request_book turns into
        request_release there (the policy's own rule) and the record stays reachable."""
        _login(client, main_module)

        resp, searched = _get_record(
            client,
            main_module,
            _policy(default_ebook="request_book"),
            capabilities={"direct_download": {"ebook"}},
            source="direct_download",
        )

        assert resp.status_code == 200
        assert resp.json["id"] == "rec-1"
        assert searched == ["record:rec-1"]

    def test_record_is_refused_only_when_every_content_type_is(self, client, main_module):
        _login(client, main_module)
        policy = _policy(default_ebook="download", default_audiobook="blocked")

        allowed, _ = _get_record(
            client, main_module, policy, capabilities={"audiobookbay": {"ebook", "audiobook"}}
        )
        refused, searched = _get_record(
            client, main_module, policy, capabilities={"audiobookbay": {"audiobook"}}
        )

        assert allowed.status_code == 200
        assert refused.status_code == 403
        assert searched == []
