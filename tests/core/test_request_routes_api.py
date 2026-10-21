"""API tests for request routes and policy enforcement guards."""

from __future__ import annotations

import importlib
import uuid
from unittest.mock import patch

import pytest

from shelfmark.core.notifications import NotificationEvent


@pytest.fixture(scope="module")
def main_module():
    """Import `shelfmark.main` with background startup disabled."""
    with patch("shelfmark.download.orchestrator.start"):
        import shelfmark.main as main

        importlib.reload(main)
        return main


@pytest.fixture
def client(main_module):
    return main_module.app.test_client()


def _set_session(client, *, user_id: str, db_user_id: int | None, is_admin: bool) -> None:
    with client.session_transaction() as sess:
        sess["user_id"] = user_id
        sess["is_admin"] = is_admin
        if db_user_id is not None:
            sess["db_user_id"] = db_user_id
        elif "db_user_id" in sess:
            del sess["db_user_id"]


def _create_user(main_module, *, prefix: str, role: str = "user") -> dict:
    username = f"{prefix}-{uuid.uuid4().hex[:8]}"
    return main_module.user_db.create_user(username=username, role=role)


def _policy(
    *,
    requests_enabled: bool = True,
    default_ebook: str = "download",
    default_audiobook: str = "download",
    max_pending_requests_per_user: int = 20,
    requests_allow_notes: bool = True,
    rules: list[dict] | None = None,
) -> dict:
    return {
        "REQUESTS_ENABLED": requests_enabled,
        "REQUEST_POLICY_DEFAULT_EBOOK": default_ebook,
        "REQUEST_POLICY_DEFAULT_AUDIOBOOK": default_audiobook,
        "MAX_PENDING_REQUESTS_PER_USER": max_pending_requests_per_user,
        "REQUESTS_ALLOW_NOTES": requests_allow_notes,
        "REQUEST_POLICY_RULES": rules or [],
    }


def _assert_emit_call(mock_emit, index: int, event: str, payload: dict, room: str) -> None:
    call = mock_emit.call_args_list[index]
    assert call.args == (event, payload)
    assert call.kwargs == {"to": room}


class TestDownloadPolicyGuards:
    def test_release_download_endpoint_blocks_before_queue_when_policy_requires_request(
        self, main_module, client
    ):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module,
                "load_users_request_policy_settings",
                return_value=_policy(default_ebook="request_release"),
            ):
                with patch(
                    "shelfmark.core.request_routes.load_users_request_policy_settings",
                    return_value=_policy(default_ebook="request_release"),
                ):
                    with patch.object(main_module.backend, "queue_release") as mock_queue_release:
                        resp = client.post(
                            "/api/releases/download",
                            json={
                                "source": "direct_download",
                                "source_id": "book-123",
                                "search_mode": "direct",
                            },
                        )

        assert resp.status_code == 403
        assert resp.json["code"] == "policy_requires_request"
        assert resp.json["required_mode"] == "request_release"
        mock_queue_release.assert_not_called()

    def test_release_download_endpoint_applies_policy_when_session_lacks_is_admin(
        self, main_module, client
    ):
        user = _create_user(main_module, prefix="reader")
        with client.session_transaction() as sess:
            sess["user_id"] = user["username"]
            sess["db_user_id"] = user["id"]
            sess.pop("is_admin", None)

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module,
                "load_users_request_policy_settings",
                return_value=_policy(default_ebook="request_release"),
            ):
                with patch.object(
                    main_module.backend, "queue_release", return_value=(True, None)
                ) as mock_queue_release:
                    resp = client.post(
                        "/api/releases/download",
                        json={
                            "source": "direct_download",
                            "source_id": "book-123",
                            "search_mode": "direct",
                        },
                    )

        assert resp.status_code == 403
        assert resp.json["code"] == "policy_requires_request"
        assert resp.json["required_mode"] == "request_release"
        mock_queue_release.assert_not_called()

    def test_release_download_endpoint_blocks_before_queue_when_policy_blocked(
        self, main_module, client
    ):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module,
                "load_users_request_policy_settings",
                return_value=_policy(default_ebook="blocked"),
            ):
                with patch(
                    "shelfmark.core.request_routes.load_users_request_policy_settings",
                    return_value=_policy(default_ebook="blocked"),
                ):
                    with patch.object(main_module.backend, "queue_release") as mock_queue_release:
                        resp = client.post(
                            "/api/releases/download",
                            json={
                                "source": "direct_download",
                                "source_id": "rel-1",
                                "content_type": "ebook",
                            },
                        )

        assert resp.status_code == 403
        assert resp.json["code"] == "policy_blocked"
        assert resp.json["required_mode"] == "blocked"
        mock_queue_release.assert_not_called()

    def test_admin_bypasses_policy_guards(self, main_module, client):
        admin = _create_user(main_module, prefix="admin", role="admin")
        _set_session(client, user_id=admin["username"], db_user_id=admin["id"], is_admin=True)

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module,
                "load_users_request_policy_settings",
                return_value=_policy(default_ebook="blocked"),
            ):
                with patch(
                    "shelfmark.core.request_routes.load_users_request_policy_settings",
                    return_value=_policy(default_ebook="blocked"),
                ):
                    with patch.object(
                        main_module.backend, "queue_release", return_value=(True, None)
                    ) as mock_queue_release:
                        resp = client.post(
                            "/api/releases/download",
                            json={
                                "source": "direct_download",
                                "source_id": "book-123",
                                "search_mode": "direct",
                            },
                        )

        assert resp.status_code == 200
        assert resp.json["status"] == "queued"
        mock_queue_release.assert_called_once()

    def test_no_auth_mode_bypasses_policy_guards(self, main_module, client):
        with patch.object(main_module, "get_auth_mode", return_value="none"):
            with patch.object(
                main_module,
                "load_users_request_policy_settings",
                return_value=_policy(default_ebook="blocked"),
            ):
                with patch(
                    "shelfmark.core.request_routes.load_users_request_policy_settings",
                    return_value=_policy(default_ebook="blocked"),
                ):
                    with patch.object(
                        main_module.backend, "queue_release", return_value=(True, None)
                    ) as mock_queue_release:
                        resp = client.post(
                            "/api/releases/download",
                            json={
                                "source": "direct_download",
                                "source_id": "book-123",
                                "search_mode": "direct",
                            },
                        )

        assert resp.status_code == 200
        assert resp.json["status"] == "queued"
        mock_queue_release.assert_called_once()


class TestRequestRoutes:
    def test_request_endpoints_are_unavailable_in_no_auth_mode(self, main_module, client):
        with patch.object(main_module, "get_auth_mode", return_value="none"):
            resp = client.get("/api/requests")

        assert resp.status_code == 403
        assert resp.json["code"] == "requests_unavailable"

    def test_request_policy_endpoint_returns_effective_policy(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)
        policy = _policy(default_ebook="request_release")

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module, "load_users_request_policy_settings", return_value=policy
            ):
                with patch(
                    "shelfmark.core.request_routes.load_users_request_policy_settings",
                    return_value=policy,
                ):
                    resp = client.get("/api/request-policy")

        assert resp.status_code == 200
        assert resp.json["requests_enabled"] is True
        assert resp.json["defaults"]["ebook"] == "request_release"
        assert "source_modes" in resp.json

    def test_request_policy_endpoint_normalizes_direct_request_book_to_request_release(
        self, main_module, client
    ):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)
        policy = _policy(default_ebook="request_book")

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module, "load_users_request_policy_settings", return_value=policy
            ):
                with patch(
                    "shelfmark.core.request_routes.load_users_request_policy_settings",
                    return_value=policy,
                ):
                    with patch(
                        "shelfmark.core.request_routes.get_source_content_type_capabilities",
                        return_value={"direct_download": {"ebook"}},
                    ):
                        resp = client.get("/api/request-policy")

        assert resp.status_code == 200
        assert resp.json["defaults"]["ebook"] == "request_book"
        assert resp.json["source_modes"][0]["source"] == "direct_download"
        assert resp.json["source_modes"][0]["modes"]["ebook"] == "request_release"

    def test_create_list_and_cancel_request(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)
        policy = _policy(default_ebook="request_book")

        payload = {
            "book_data": {
                "title": "The Pragmatic Programmer",
                "author": "Andrew Hunt",
                "content_type": "ebook",
                "provider": "openlibrary",
                "provider_id": "ol-1",
            },
            "context": {
                "source": "direct_download",
                "content_type": "ebook",
                "request_level": "book",
            },
            "note": "Please add this",
        }

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module, "load_users_request_policy_settings", return_value=policy
            ):
                with patch(
                    "shelfmark.core.request_routes.load_users_request_policy_settings",
                    return_value=policy,
                ):
                    create_resp = client.post("/api/requests", json=payload)
                    list_resp = client.get("/api/requests")

                    assert create_resp.status_code == 201
                    request_id = create_resp.json["id"]
                    assert create_resp.json["status"] == "pending"
                    assert any(item["id"] == request_id for item in list_resp.json)

                    cancel_resp = client.delete(f"/api/requests/{request_id}")

        assert cancel_resp.status_code == 200
        assert cancel_resp.json["status"] == "cancelled"
        updated = main_module.user_db.get_request(request_id)
        assert updated is not None
        assert updated["user_id"] == user["id"]
        assert updated["status"] == "cancelled"

    def test_download_policy_queues_release_without_creating_request(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)
        policy = _policy(default_ebook="download")

        payload = {
            "book_data": {
                "title": "Policy Download",
                "author": "Shelfmark",
                "content_type": "ebook",
                "provider": "openlibrary",
                "provider_id": "policy-download-1",
            },
            "context": {
                "source": "prowlarr",
                "content_type": "ebook",
                "request_level": "release",
            },
            "release_data": {
                "source": "prowlarr",
                "source_id": "policy-download-release-1",
                "title": "Policy Download.epub",
            },
        }

        captured: dict[str, object] = {}

        def fake_queue_release(release_data, priority, user_id=None, username=None):
            captured["release_data"] = release_data
            captured["priority"] = priority
            captured["user_id"] = user_id
            captured["username"] = username
            return True, None

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module, "load_users_request_policy_settings", return_value=policy
            ):
                with patch(
                    "shelfmark.core.request_routes.load_users_request_policy_settings",
                    return_value=policy,
                ):
                    with patch.object(
                        main_module.backend, "queue_release", side_effect=fake_queue_release
                    ):
                        with patch(
                            "shelfmark.core.request_routes.notify_admin"
                        ) as mock_notify_admin:
                            with patch(
                                "shelfmark.core.request_routes.notify_user"
                            ) as mock_notify_user:
                                resp = client.post("/api/requests", json=payload)

        assert resp.status_code == 200
        assert resp.json["kind"] == "download"
        assert resp.json["status"] == "queued"
        assert resp.json["title"] == "Policy Download"
        assert resp.json["source"] == "prowlarr"
        assert resp.json["source_id"] == "policy-download-release-1"
        assert captured["priority"] == 0
        assert captured["user_id"] == user["id"]
        assert captured["username"] == user["username"]
        assert captured["release_data"]["source_id"] == "policy-download-release-1"
        assert main_module.user_db.list_requests(user_id=user["id"]) == []
        mock_notify_admin.assert_not_called()
        mock_notify_user.assert_not_called()

    def test_download_policy_rejects_mismatched_context_and_release_source(
        self, main_module, client
    ):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)
        policy = _policy(
            default_ebook="download",
            rules=[{"source": "direct_download", "content_type": "*", "mode": "blocked"}],
        )

        payload = {
            "book_data": {
                "title": "Policy Source Mismatch",
                "author": "Shelfmark",
                "content_type": "ebook",
                "provider": "openlibrary",
                "provider_id": "policy-source-mismatch-1",
            },
            "context": {
                "source": "prowlarr",
                "content_type": "ebook",
                "request_level": "release",
            },
            "release_data": {
                "source": "direct_download",
                "source_id": "blocked-release-1",
                "title": "Blocked Release.epub",
            },
        }

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module, "load_users_request_policy_settings", return_value=policy
            ):
                with patch(
                    "shelfmark.core.request_routes.load_users_request_policy_settings",
                    return_value=policy,
                ):
                    with patch.object(main_module.backend, "queue_release") as mock_queue:
                        resp = client.post("/api/requests", json=payload)

        assert resp.status_code == 400
        assert resp.json["code"] == "policy_source_mismatch"
        assert resp.json["error"] == "Policy context source must match release_data.source"
        assert main_module.user_db.list_requests(user_id=user["id"]) == []
        mock_queue.assert_not_called()

    def test_release_result_source_rejects_mismatch_before_normalization(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)
        policy = _policy(
            default_ebook="download",
            rules=[{"source": "prowlarr", "content_type": "*", "mode": "blocked"}],
        )

        payload = {
            "book_data": {
                "title": "Release Result Source Mismatch",
                "author": "Shelfmark",
                "content_type": "ebook",
                "provider": "openlibrary",
                "provider_id": "release-result-mismatch-1",
            },
            "context": {
                "source": "direct_download",
                "content_type": "ebook",
                "request_level": "release",
            },
            "release_data": {
                "source": "prowlarr",
                "source_id": "blocked-prowlarr-release-1",
                "title": "Blocked Prowlarr Release.epub",
            },
        }

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module, "load_users_request_policy_settings", return_value=policy
            ):
                with patch(
                    "shelfmark.core.request_routes.load_users_request_policy_settings",
                    return_value=policy,
                ):
                    with patch.object(main_module.backend, "queue_release") as mock_queue:
                        resp = client.post("/api/requests", json=payload)

        assert resp.status_code == 400
        assert resp.json["code"] == "policy_source_mismatch"
        assert resp.json["error"] == "Policy context source must match release_data.source"
        assert main_module.user_db.list_requests(user_id=user["id"]) == []
        mock_queue.assert_not_called()

    def test_batch_rejects_release_result_source_mismatch_before_creating_any_requests(
        self, main_module, client
    ):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)
        policy = _policy(
            default_ebook="request_release",
            rules=[{"source": "prowlarr", "content_type": "*", "mode": "blocked"}],
        )

        payloads = [
            {
                "book_data": {
                    "title": "Batch Valid Direct",
                    "author": "Shelfmark",
                    "content_type": "ebook",
                    "provider": "openlibrary",
                    "provider_id": "batch-valid-direct-1",
                },
                "context": {
                    "source": "direct_download",
                    "content_type": "ebook",
                    "request_level": "release",
                },
                "release_data": {
                    "source": "direct_download",
                    "source_id": "batch-valid-direct-release-1",
                    "title": "Batch Valid Direct.epub",
                },
            },
            {
                "book_data": {
                    "title": "Batch Release Result Mismatch",
                    "author": "Shelfmark",
                    "content_type": "ebook",
                    "provider": "openlibrary",
                    "provider_id": "batch-release-result-mismatch-1",
                },
                "context": {
                    "source": "direct_download",
                    "content_type": "ebook",
                    "request_level": "release",
                },
                "release_data": {
                    "source": "prowlarr",
                    "source_id": "batch-blocked-prowlarr-release-1",
                    "title": "Batch Blocked Prowlarr.epub",
                },
            },
        ]

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module, "load_users_request_policy_settings", return_value=policy
            ):
                with patch(
                    "shelfmark.core.request_routes.load_users_request_policy_settings",
                    return_value=policy,
                ):
                    with patch.object(main_module.backend, "queue_release") as mock_queue:
                        resp = client.post("/api/requests/batch", json={"requests": payloads})

        assert resp.status_code == 400
        assert resp.json["code"] == "policy_source_mismatch"
        assert resp.json["error"] == "Policy context source must match release_data.source"
        assert main_module.user_db.list_requests(user_id=user["id"]) == []
        mock_queue.assert_not_called()

    def test_batch_download_policy_queues_releases_without_creating_requests(
        self, main_module, client
    ):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)
        policy = _policy(default_ebook="download")

        payloads = [
            {
                "book_data": {
                    "title": "Batch Download One",
                    "author": "Shelfmark",
                    "content_type": "ebook",
                    "provider": "openlibrary",
                    "provider_id": "batch-download-1",
                },
                "context": {
                    "source": "prowlarr",
                    "content_type": "ebook",
                    "request_level": "release",
                },
                "release_data": {
                    "source": "prowlarr",
                    "source_id": "batch-download-release-1",
                    "title": "Batch Download One.epub",
                },
            },
            {
                "book_data": {
                    "title": "Batch Download Two",
                    "author": "Shelfmark",
                    "content_type": "ebook",
                    "provider": "openlibrary",
                    "provider_id": "batch-download-2",
                },
                "context": {
                    "source": "prowlarr",
                    "content_type": "ebook",
                    "request_level": "release",
                },
                "release_data": {
                    "source": "prowlarr",
                    "source_id": "batch-download-release-2",
                    "title": "Batch Download Two.epub",
                },
            },
        ]

        queued: list[tuple[str, int, int | None, str | None]] = []

        def fake_queue_release(release_data, priority, user_id=None, username=None):
            queued.append((release_data["source_id"], priority, user_id, username))
            return True, None

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module, "load_users_request_policy_settings", return_value=policy
            ):
                with patch(
                    "shelfmark.core.request_routes.load_users_request_policy_settings",
                    return_value=policy,
                ):
                    with patch.object(
                        main_module.backend, "queue_release", side_effect=fake_queue_release
                    ):
                        with patch(
                            "shelfmark.core.request_routes.notify_admin"
                        ) as mock_notify_admin:
                            resp = client.post("/api/requests/batch", json={"requests": payloads})

        assert resp.status_code == 200
        assert [row["kind"] for row in resp.json] == ["download", "download"]
        assert [row["source_id"] for row in resp.json] == [
            "batch-download-release-1",
            "batch-download-release-2",
        ]
        assert queued == [
            ("batch-download-release-1", 0, user["id"], user["username"]),
            ("batch-download-release-2", 0, user["id"], user["username"]),
        ]
        assert main_module.user_db.list_requests(user_id=user["id"]) == []
        mock_notify_admin.assert_not_called()

    def test_admin_can_create_request_on_behalf_of_another_user(self, main_module, client):
        admin = _create_user(main_module, prefix="admin", role="admin")
        target_user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=admin["username"], db_user_id=admin["id"], is_admin=True)
        policy = _policy(default_ebook="request_book")

        payload = {
            "book_data": {
                "title": "Shared Request",
                "author": "Shelfmark",
                "content_type": "ebook",
                "provider": "openlibrary",
                "provider_id": "shared-1",
            },
            "context": {
                "source": "*",
                "content_type": "ebook",
                "request_level": "book",
            },
            "on_behalf_of_user_id": target_user["id"],
        }

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module, "load_users_request_policy_settings", return_value=policy
            ):
                with patch(
                    "shelfmark.core.request_routes.load_users_request_policy_settings",
                    return_value=policy,
                ):
                    resp = client.post("/api/requests", json=payload)

        assert resp.status_code == 201
        assert resp.json["user_id"] == target_user["id"]
        created = main_module.user_db.get_request(resp.json["id"])
        assert created is not None
        assert created["user_id"] == target_user["id"]

    def test_non_admin_cannot_create_request_on_behalf_of_another_user(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        target_user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)
        policy = _policy(default_ebook="request_book")

        payload = {
            "book_data": {
                "title": "Shared Request",
                "author": "Shelfmark",
                "content_type": "ebook",
                "provider": "openlibrary",
                "provider_id": "shared-2",
            },
            "context": {
                "source": "*",
                "content_type": "ebook",
                "request_level": "book",
            },
            "on_behalf_of_user_id": target_user["id"],
        }

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module, "load_users_request_policy_settings", return_value=policy
            ):
                with patch(
                    "shelfmark.core.request_routes.load_users_request_policy_settings",
                    return_value=policy,
                ):
                    resp = client.post("/api/requests", json=payload)

        assert resp.status_code == 403
        assert resp.json["error"] == "Admin required"
        assert main_module.user_db.list_requests(user_id=user["id"]) == []
        assert main_module.user_db.list_requests(user_id=target_user["id"]) == []

    def test_admin_on_behalf_of_unknown_user_returns_404(self, main_module, client):
        admin = _create_user(main_module, prefix="admin", role="admin")
        _set_session(client, user_id=admin["username"], db_user_id=admin["id"], is_admin=True)
        policy = _policy(default_ebook="request_book")

        payload = {
            "book_data": {
                "title": "Missing Target",
                "author": "Shelfmark",
                "content_type": "ebook",
                "provider": "openlibrary",
                "provider_id": "missing-target",
            },
            "context": {
                "source": "*",
                "content_type": "ebook",
                "request_level": "book",
            },
            "on_behalf_of_user_id": 9_999_999,
        }

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module, "load_users_request_policy_settings", return_value=policy
            ):
                with patch(
                    "shelfmark.core.request_routes.load_users_request_policy_settings",
                    return_value=policy,
                ):
                    resp = client.post("/api/requests", json=payload)

        assert resp.status_code == 404
        assert resp.json["error"] == "User not found"

    def test_batch_create_requests_is_atomic(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)
        policy = _policy(default_ebook="request_book", default_audiobook="request_book")

        duplicate_request = {
            "book_data": {
                "title": "Duplicate Title",
                "author": "Same Author",
                "content_type": "ebook",
                "provider": "openlibrary",
                "provider_id": "dup-1",
            },
            "context": {
                "source": "*",
                "content_type": "ebook",
                "request_level": "book",
            },
        }

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module, "load_users_request_policy_settings", return_value=policy
            ):
                with patch(
                    "shelfmark.core.request_routes.load_users_request_policy_settings",
                    return_value=policy,
                ):
                    resp = client.post(
                        "/api/requests/batch",
                        json={"requests": [duplicate_request, duplicate_request]},
                    )

        assert resp.status_code == 409
        assert resp.json["code"] == "duplicate_pending_request"
        assert main_module.user_db.list_requests(user_id=user["id"]) == []

    @pytest.mark.parametrize("bad_item", [1, "book", None, ["x"]])
    def test_batch_rejects_non_object_request_items(self, main_module, client, bad_item):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            resp = client.post("/api/requests/batch", json={"requests": [bad_item]})

        assert resp.status_code == 400
        assert "requests must contain objects" in resp.json["error"]

    def test_batch_non_object_item_rejects_whole_batch(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)
        policy = _policy(default_ebook="request_book")

        valid_request = {
            "book_data": {
                "title": "Batch Valid Alongside Bad Item",
                "author": "Shelfmark",
                "content_type": "ebook",
                "provider": "openlibrary",
                "provider_id": "batch-valid-alongside-bad-1",
            },
            "context": {
                "source": "*",
                "content_type": "ebook",
                "request_level": "book",
            },
        }

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module, "load_users_request_policy_settings", return_value=policy
            ):
                with patch(
                    "shelfmark.core.request_routes.load_users_request_policy_settings",
                    return_value=policy,
                ):
                    resp = client.post(
                        "/api/requests/batch",
                        json={"requests": [valid_request, "not-an-object"]},
                    )

        assert resp.status_code == 400
        assert "requests must contain objects" in resp.json["error"]
        assert main_module.user_db.list_requests(user_id=user["id"]) == []

    def test_create_request_emits_websocket_events(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)
        policy = _policy(default_ebook="request_book")

        payload = {
            "book_data": {
                "title": "Eventful Book",
                "author": "Event Author",
                "content_type": "ebook",
                "provider": "openlibrary",
                "provider_id": "ol-event",
            },
            "context": {
                "source": "direct_download",
                "content_type": "ebook",
                "request_level": "book",
            },
        }

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module, "load_users_request_policy_settings", return_value=policy
            ):
                with patch(
                    "shelfmark.core.request_routes.load_users_request_policy_settings",
                    return_value=policy,
                ):
                    with patch.object(main_module.ws_manager, "is_enabled", return_value=True):
                        with patch.object(main_module.ws_manager.socketio, "emit") as mock_emit:
                            resp = client.post("/api/requests", json=payload)

        assert resp.status_code == 201
        request_id = resp.json["id"]

        assert mock_emit.call_count == 2
        expected_payload = {
            "request_id": request_id,
            "status": "pending",
            "title": "Eventful Book",
        }
        _assert_emit_call(mock_emit, 0, "new_request", expected_payload, "admins")
        _assert_emit_call(mock_emit, 1, "request_update", expected_payload, f"user_{user['id']}")

    def test_create_request_triggers_admin_notification(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)
        policy = _policy(default_ebook="request_book")

        payload = {
            "book_data": {
                "title": "Notify Create Book",
                "author": "Notify Create Author",
                "content_type": "ebook",
                "provider": "openlibrary",
                "provider_id": "ol-notify-create",
            },
            "context": {
                "source": "direct_download",
                "content_type": "ebook",
                "request_level": "book",
            },
        }

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module, "load_users_request_policy_settings", return_value=policy
            ):
                with patch(
                    "shelfmark.core.request_routes.load_users_request_policy_settings",
                    return_value=policy,
                ):
                    with patch("shelfmark.core.request_routes.notify_admin") as mock_notify:
                        with patch("shelfmark.core.request_routes.notify_user") as mock_notify_user:
                            resp = client.post("/api/requests", json=payload)

        assert resp.status_code == 201
        mock_notify.assert_called_once()
        event, context = mock_notify.call_args.args
        assert event == NotificationEvent.REQUEST_CREATED
        assert context.title == "Notify Create Book"
        assert context.author == "Notify Create Author"
        assert context.username == user["username"]
        mock_notify_user.assert_called_once()
        user_id, user_event, user_context = mock_notify_user.call_args.args
        assert user_id == user["id"]
        assert user_event == NotificationEvent.REQUEST_CREATED
        assert user_context.title == "Notify Create Book"

    def test_create_request_succeeds_when_notification_dispatch_raises(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)
        policy = _policy(default_ebook="request_book")

        payload = {
            "book_data": {
                "title": "Resilient Notify Create Book",
                "author": "Resilient Notify Create Author",
                "content_type": "ebook",
                "provider": "openlibrary",
                "provider_id": "ol-notify-resilience",
            },
            "context": {
                "source": "direct_download",
                "content_type": "ebook",
                "request_level": "book",
            },
        }

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module, "load_users_request_policy_settings", return_value=policy
            ):
                with patch(
                    "shelfmark.core.request_routes.load_users_request_policy_settings",
                    return_value=policy,
                ):
                    with patch(
                        "shelfmark.core.request_routes.notify_admin",
                        side_effect=RuntimeError("admin notification unavailable"),
                    ) as mock_notify_admin:
                        with patch(
                            "shelfmark.core.request_routes.notify_user",
                            side_effect=RuntimeError("user notification unavailable"),
                        ) as mock_notify_user:
                            resp = client.post("/api/requests", json=payload)

        assert resp.status_code == 201
        assert resp.json["status"] == "pending"
        mock_notify_admin.assert_called_once()
        mock_notify_user.assert_called_once()

    def test_cancel_request_emits_to_user_and_admin_rooms(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)
        policy = _policy(default_ebook="request_book")

        payload = {
            "book_data": {
                "title": "Cancelable Book",
                "author": "Cancelable Author",
                "content_type": "ebook",
                "provider": "openlibrary",
                "provider_id": "ol-cancel",
            },
            "context": {
                "source": "direct_download",
                "content_type": "ebook",
                "request_level": "book",
            },
        }

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module, "load_users_request_policy_settings", return_value=policy
            ):
                with patch(
                    "shelfmark.core.request_routes.load_users_request_policy_settings",
                    return_value=policy,
                ):
                    with patch.object(main_module.ws_manager, "is_enabled", return_value=True):
                        with patch.object(main_module.ws_manager.socketio, "emit") as mock_emit:
                            create_resp = client.post("/api/requests", json=payload)
                            request_id = create_resp.json["id"]

                            mock_emit.reset_mock()
                            cancel_resp = client.delete(f"/api/requests/{request_id}")

        assert create_resp.status_code == 201
        assert cancel_resp.status_code == 200
        assert cancel_resp.json["status"] == "cancelled"

        assert mock_emit.call_count == 2
        expected_payload = {
            "request_id": request_id,
            "status": "cancelled",
            "title": "Cancelable Book",
        }
        _assert_emit_call(mock_emit, 0, "request_update", expected_payload, f"user_{user['id']}")
        _assert_emit_call(mock_emit, 1, "request_update", expected_payload, "admins")

    def test_create_request_level_payload_mismatch_returns_400(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)
        policy = _policy(default_ebook="request_release")

        payload = {
            "book_data": {
                "title": "Clean Code",
                "author": "Robert Martin",
                "content_type": "ebook",
                "provider": "openlibrary",
                "provider_id": "ol-2",
            },
            "context": {
                "source": "prowlarr",
                "content_type": "ebook",
                "request_level": "book",
            },
            "release_data": {
                "source": "prowlarr",
                "source_id": "rel-2",
                "title": "Clean Code.epub",
            },
        }

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module, "load_users_request_policy_settings", return_value=policy
            ):
                with patch(
                    "shelfmark.core.request_routes.load_users_request_policy_settings",
                    return_value=policy,
                ):
                    resp = client.post("/api/requests", json=payload)

        assert resp.status_code == 400
        assert "request_level=book requires null release_data" in resp.json["error"]

    def test_duplicate_pending_request_returns_409(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)
        policy = _policy(default_ebook="request_book")

        payload = {
            "book_data": {
                "title": "Domain-Driven Design",
                "author": "Eric Evans",
                "content_type": "ebook",
                "provider": "openlibrary",
                "provider_id": "ol-3",
            },
            "context": {
                "source": "direct_download",
                "content_type": "ebook",
                "request_level": "book",
            },
        }

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module, "load_users_request_policy_settings", return_value=policy
            ):
                with patch(
                    "shelfmark.core.request_routes.load_users_request_policy_settings",
                    return_value=policy,
                ):
                    first_resp = client.post("/api/requests", json=payload)
                    second_resp = client.post("/api/requests", json=payload)

        assert first_resp.status_code == 201
        assert second_resp.status_code == 409
        assert second_resp.json["code"] == "duplicate_pending_request"

    def test_create_request_enforces_max_pending_limit(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)
        policy = _policy(default_ebook="request_book", max_pending_requests_per_user=1)

        payload_1 = {
            "book_data": {
                "title": "Book A",
                "author": "Author A",
                "content_type": "ebook",
                "provider": "openlibrary",
                "provider_id": "ol-a",
            },
            "context": {
                "source": "direct_download",
                "content_type": "ebook",
                "request_level": "book",
            },
        }
        payload_2 = {
            "book_data": {
                "title": "Book B",
                "author": "Author B",
                "content_type": "ebook",
                "provider": "openlibrary",
                "provider_id": "ol-b",
            },
            "context": {
                "source": "direct_download",
                "content_type": "ebook",
                "request_level": "book",
            },
        }

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module, "load_users_request_policy_settings", return_value=policy
            ):
                with patch(
                    "shelfmark.core.request_routes.load_users_request_policy_settings",
                    return_value=policy,
                ):
                    first_resp = client.post("/api/requests", json=payload_1)
                    second_resp = client.post("/api/requests", json=payload_2)

        assert first_resp.status_code == 201
        assert second_resp.status_code == 409
        assert second_resp.json["code"] == "max_pending_reached"

    def test_create_request_strips_note_when_notes_disabled(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)
        policy = _policy(default_ebook="request_book", requests_allow_notes=False)

        payload = {
            "book_data": {
                "title": "No Notes Book",
                "author": "No Notes Author",
                "content_type": "ebook",
                "provider": "openlibrary",
                "provider_id": "ol-nonote",
            },
            "context": {
                "source": "direct_download",
                "content_type": "ebook",
                "request_level": "book",
            },
            "note": "This should be dropped",
        }

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module, "load_users_request_policy_settings", return_value=policy
            ):
                with patch(
                    "shelfmark.core.request_routes.load_users_request_policy_settings",
                    return_value=policy,
                ):
                    resp = client.post("/api/requests", json=payload)

        assert resp.status_code == 201
        assert resp.json["note"] is None

    def test_request_book_policy_requires_book_level_request(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)
        policy = _policy(default_ebook="request_book")

        payload = {
            "book_data": {
                "title": "Refactoring",
                "author": "Martin Fowler",
                "content_type": "ebook",
                "provider": "openlibrary",
                "provider_id": "ol-4",
            },
            "context": {
                "source": "prowlarr",
                "content_type": "ebook",
                "request_level": "release",
            },
            "release_data": {
                "source": "prowlarr",
                "source_id": "rel-4",
                "title": "Refactoring.epub",
            },
        }

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module, "load_users_request_policy_settings", return_value=policy
            ):
                with patch(
                    "shelfmark.core.request_routes.load_users_request_policy_settings",
                    return_value=policy,
                ):
                    resp = client.post("/api/requests", json=payload)

        assert resp.status_code == 403
        assert resp.json["code"] == "policy_requires_request"
        assert resp.json["required_mode"] == "request_book"

    def test_request_book_policy_allows_direct_release_level_request(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)
        policy = _policy(default_ebook="request_book")

        payload = {
            "book_data": {
                "title": "Direct Result",
                "author": "Direct Author",
                "content_type": "ebook",
                "provider": "direct_download",
                "provider_id": "dd-1",
            },
            "context": {
                "source": "direct_download",
                "content_type": "ebook",
                "request_level": "release",
            },
            "release_data": {
                "source": "direct_download",
                "source_id": "dd-1",
                "title": "Direct Result.epub",
                "format": "epub",
                "size": "2 MB",
            },
        }

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module, "load_users_request_policy_settings", return_value=policy
            ):
                with patch(
                    "shelfmark.core.request_routes.load_users_request_policy_settings",
                    return_value=policy,
                ):
                    resp = client.post("/api/requests", json=payload)

        assert resp.status_code == 201
        assert resp.json["request_level"] == "release"
        assert resp.json["policy_mode"] == "request_release"
        assert resp.json["release_data"]["source"] == "direct_download"
        assert resp.json["release_data"]["source_id"] == "dd-1"

    def test_non_admin_cannot_access_admin_request_routes(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            resp = client.get("/api/admin/requests")

        assert resp.status_code == 403
        assert resp.json["error"] == "Admin access required"

    def test_admin_reject_and_terminal_conflict(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        admin = _create_user(main_module, prefix="admin", role="admin")
        policy = _policy(default_ebook="request_book")

        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)
        create_payload = {
            "book_data": {
                "title": "Working Effectively with Legacy Code",
                "author": "Michael Feathers",
                "content_type": "ebook",
                "provider": "openlibrary",
                "provider_id": "ol-5",
            },
            "context": {
                "source": "direct_download",
                "content_type": "ebook",
                "request_level": "book",
            },
        }

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module, "load_users_request_policy_settings", return_value=policy
            ):
                with patch(
                    "shelfmark.core.request_routes.load_users_request_policy_settings",
                    return_value=policy,
                ):
                    create_resp = client.post("/api/requests", json=create_payload)
                    request_id = create_resp.json["id"]

                    _set_session(
                        client, user_id=admin["username"], db_user_id=admin["id"], is_admin=True
                    )
                    count_resp = client.get("/api/admin/requests/count")
                    reject_resp = client.post(
                        f"/api/admin/requests/{request_id}/reject",
                        json={"admin_note": "Declined"},
                    )
                    reject_again_resp = client.post(
                        f"/api/admin/requests/{request_id}/reject",
                        json={"admin_note": "Declined again"},
                    )

        assert count_resp.status_code == 200
        assert count_resp.json["pending"] >= 1
        assert reject_resp.status_code == 200
        assert reject_resp.json["status"] == "rejected"
        assert reject_again_resp.status_code == 409
        assert reject_again_resp.json["code"] == "stale_transition"
        updated = main_module.user_db.get_request(request_id)
        assert updated is not None
        assert updated["user_id"] == user["id"]
        assert updated["status"] == "rejected"

    def test_admin_reopen_undoes_a_rejection(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        admin = _create_user(main_module, prefix="admin", role="admin")
        policy = _policy(default_ebook="request_book")

        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)
        create_payload = {
            "book_data": {
                "title": "My Dog Thinks It's a Cat",
                "author": "Some Author",
                "content_type": "ebook",
                "provider": "openlibrary",
                "provider_id": "ol-reopen",
            },
            "context": {
                "source": "direct_download",
                "content_type": "ebook",
                "request_level": "book",
            },
        }

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module, "load_users_request_policy_settings", return_value=policy
            ):
                with patch(
                    "shelfmark.core.request_routes.load_users_request_policy_settings",
                    return_value=policy,
                ):
                    create_resp = client.post("/api/requests", json=create_payload)
                    request_id = create_resp.json["id"]

                    reopen_as_user_resp = client.post(f"/api/admin/requests/{request_id}/reopen")

                    _set_session(
                        client, user_id=admin["username"], db_user_id=admin["id"], is_admin=True
                    )
                    reopen_pending_resp = client.post(f"/api/admin/requests/{request_id}/reopen")
                    client.post(
                        f"/api/admin/requests/{request_id}/reject",
                        json={"admin_note": "Sounds like a dumb book"},
                    )
                    main_module.activity_view_state_service.dismiss(
                        viewer_scope="admin:shared",
                        item_type="request",
                        item_key=f"request:{request_id}",
                    )
                    with patch.object(main_module.ws_manager, "is_enabled", return_value=True):
                        with patch.object(main_module.ws_manager.socketio, "emit") as mock_emit:
                            reopen_resp = client.post(
                                f"/api/admin/requests/{request_id}/reopen",
                                json={"admin_note": "Therapy book, fair enough"},
                            )
                    reopen_again_resp = client.post(f"/api/admin/requests/{request_id}/reopen")

        assert create_resp.status_code == 201
        assert reopen_as_user_resp.status_code == 403
        assert reopen_pending_resp.status_code == 409
        assert reopen_pending_resp.json["code"] == "stale_transition"
        assert reopen_resp.status_code == 200
        assert reopen_resp.json["status"] == "pending"
        assert reopen_resp.json["admin_note"] == "Therapy book, fair enough"
        assert reopen_again_resp.status_code == 409
        assert mock_emit.call_count == 2
        expected_payload = {
            "request_id": request_id,
            "status": "pending",
            "title": "My Dog Thinks It's a Cat",
        }
        _assert_emit_call(mock_emit, 0, "request_update", expected_payload, f"user_{user['id']}")
        _assert_emit_call(mock_emit, 1, "request_update", expected_payload, "admins")
        updated = main_module.user_db.get_request(request_id)
        assert updated["status"] == "pending"
        assert updated["reviewed_by"] is None
        hidden_keys = {
            row["item_key"]
            for row in main_module.activity_view_state_service.list_hidden(
                viewer_scope="admin:shared"
            )
        }
        assert f"request:{request_id}" not in hidden_keys

    def test_admin_reject_emits_update_to_user_and_admin_rooms(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        admin = _create_user(main_module, prefix="admin", role="admin")
        policy = _policy(default_ebook="request_book")

        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)
        create_payload = {
            "book_data": {
                "title": "Reject Emit Book",
                "author": "Reject Emit Author",
                "content_type": "ebook",
                "provider": "openlibrary",
                "provider_id": "ol-reject-emit",
            },
            "context": {
                "source": "direct_download",
                "content_type": "ebook",
                "request_level": "book",
            },
        }

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module, "load_users_request_policy_settings", return_value=policy
            ):
                with patch(
                    "shelfmark.core.request_routes.load_users_request_policy_settings",
                    return_value=policy,
                ):
                    create_resp = client.post("/api/requests", json=create_payload)
                    request_id = create_resp.json["id"]

                    _set_session(
                        client, user_id=admin["username"], db_user_id=admin["id"], is_admin=True
                    )
                    with patch.object(main_module.ws_manager, "is_enabled", return_value=True):
                        with patch.object(main_module.ws_manager.socketio, "emit") as mock_emit:
                            reject_resp = client.post(
                                f"/api/admin/requests/{request_id}/reject",
                                json={"admin_note": "Rejected with event fanout"},
                            )

        assert create_resp.status_code == 201
        assert reject_resp.status_code == 200
        assert reject_resp.json["status"] == "rejected"

        assert mock_emit.call_count == 2
        expected_payload = {
            "request_id": request_id,
            "status": "rejected",
            "title": "Reject Emit Book",
        }
        _assert_emit_call(mock_emit, 0, "request_update", expected_payload, f"user_{user['id']}")
        _assert_emit_call(mock_emit, 1, "request_update", expected_payload, "admins")

    def test_admin_reject_triggers_admin_notification(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        admin = _create_user(main_module, prefix="admin", role="admin")
        policy = _policy(default_ebook="request_book")

        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)
        create_payload = {
            "book_data": {
                "title": "Reject Notify Book",
                "author": "Reject Notify Author",
                "content_type": "ebook",
                "provider": "openlibrary",
                "provider_id": "ol-reject-notify",
            },
            "context": {
                "source": "direct_download",
                "content_type": "ebook",
                "request_level": "book",
            },
        }

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module, "load_users_request_policy_settings", return_value=policy
            ):
                with patch(
                    "shelfmark.core.request_routes.load_users_request_policy_settings",
                    return_value=policy,
                ):
                    create_resp = client.post("/api/requests", json=create_payload)
                    request_id = create_resp.json["id"]

                    _set_session(
                        client, user_id=admin["username"], db_user_id=admin["id"], is_admin=True
                    )
                    with patch("shelfmark.core.request_routes.notify_admin") as mock_notify:
                        with patch("shelfmark.core.request_routes.notify_user") as mock_notify_user:
                            reject_resp = client.post(
                                f"/api/admin/requests/{request_id}/reject",
                                json={"admin_note": "Needs better metadata"},
                            )

        assert create_resp.status_code == 201
        assert reject_resp.status_code == 200
        mock_notify.assert_called_once()
        event, context = mock_notify.call_args.args
        assert event == NotificationEvent.REQUEST_REJECTED
        assert context.title == "Reject Notify Book"
        assert context.admin_note == "Needs better metadata"
        assert context.username == user["username"]
        mock_notify_user.assert_called_once()
        user_id, user_event, user_context = mock_notify_user.call_args.args
        assert user_id == user["id"]
        assert user_event == NotificationEvent.REQUEST_REJECTED
        assert user_context.admin_note == "Needs better metadata"

    def test_admin_fulfil_queues_for_requesting_user(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        admin = _create_user(main_module, prefix="admin", role="admin")
        policy = _policy(default_ebook="request_release")

        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)
        create_payload = {
            "book_data": {
                "title": "Patterns of Enterprise Application Architecture",
                "author": "Martin Fowler",
                "content_type": "ebook",
                "provider": "openlibrary",
                "provider_id": "ol-6",
            },
            "context": {
                "source": "prowlarr",
                "content_type": "ebook",
                "request_level": "release",
            },
            "release_data": {
                "source": "prowlarr",
                "source_id": "rel-6",
                "title": "POEAA.epub",
            },
        }

        captured: dict[str, object] = {}

        def fake_queue_release(release_data, priority, user_id=None, username=None):
            captured["release_data"] = release_data
            captured["priority"] = priority
            captured["user_id"] = user_id
            captured["username"] = username
            return True, None

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module, "load_users_request_policy_settings", return_value=policy
            ):
                with patch(
                    "shelfmark.core.request_routes.load_users_request_policy_settings",
                    return_value=policy,
                ):
                    create_resp = client.post("/api/requests", json=create_payload)
                    request_id = create_resp.json["id"]

                    _set_session(
                        client, user_id=admin["username"], db_user_id=admin["id"], is_admin=True
                    )
                    with patch.object(
                        main_module.backend, "queue_release", side_effect=fake_queue_release
                    ):
                        fulfil_resp = client.post(
                            f"/api/admin/requests/{request_id}/fulfil",
                            json={"admin_note": "Approved"},
                        )

        assert fulfil_resp.status_code == 200
        assert fulfil_resp.json["status"] == "fulfilled"
        assert captured["priority"] == 0
        assert captured["user_id"] == user["id"]
        assert captured["username"] == user["username"]

    def test_admin_fulfil_emits_update_to_user_and_admin_rooms(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        admin = _create_user(main_module, prefix="admin", role="admin")
        policy = _policy(default_ebook="request_release")

        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)
        create_payload = {
            "book_data": {
                "title": "Fulfil Emit Book",
                "author": "Fulfil Emit Author",
                "content_type": "ebook",
                "provider": "openlibrary",
                "provider_id": "ol-fulfil-emit",
            },
            "context": {
                "source": "prowlarr",
                "content_type": "ebook",
                "request_level": "release",
            },
            "release_data": {
                "source": "prowlarr",
                "source_id": "rel-fulfil-emit",
                "title": "Fulfil Emit Book.epub",
            },
        }

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module, "load_users_request_policy_settings", return_value=policy
            ):
                with patch(
                    "shelfmark.core.request_routes.load_users_request_policy_settings",
                    return_value=policy,
                ):
                    create_resp = client.post("/api/requests", json=create_payload)
                    request_id = create_resp.json["id"]

                    _set_session(
                        client, user_id=admin["username"], db_user_id=admin["id"], is_admin=True
                    )
                    with patch.object(
                        main_module.backend, "queue_release", return_value=(True, None)
                    ):
                        with patch.object(main_module.ws_manager, "is_enabled", return_value=True):
                            with patch.object(main_module.ws_manager.socketio, "emit") as mock_emit:
                                fulfil_resp = client.post(
                                    f"/api/admin/requests/{request_id}/fulfil",
                                    json={"admin_note": "Approved with event fanout"},
                                )

        assert create_resp.status_code == 201
        assert fulfil_resp.status_code == 200
        assert fulfil_resp.json["status"] == "fulfilled"

        assert mock_emit.call_count == 2
        expected_payload = {
            "request_id": request_id,
            "status": "fulfilled",
            "title": "Fulfil Emit Book",
        }
        _assert_emit_call(mock_emit, 0, "request_update", expected_payload, f"user_{user['id']}")
        _assert_emit_call(mock_emit, 1, "request_update", expected_payload, "admins")

    def test_admin_fulfil_triggers_admin_notification(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        admin = _create_user(main_module, prefix="admin", role="admin")
        policy = _policy(default_ebook="request_release")

        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)
        create_payload = {
            "book_data": {
                "title": "Fulfil Notify Book",
                "author": "Fulfil Notify Author",
                "content_type": "ebook",
                "provider": "openlibrary",
                "provider_id": "ol-fulfil-notify",
            },
            "context": {
                "source": "prowlarr",
                "content_type": "ebook",
                "request_level": "release",
            },
            "release_data": {
                "source": "prowlarr",
                "source_id": "rel-fulfil-notify",
                "title": "Fulfil Notify Book.epub",
            },
        }

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module, "load_users_request_policy_settings", return_value=policy
            ):
                with patch(
                    "shelfmark.core.request_routes.load_users_request_policy_settings",
                    return_value=policy,
                ):
                    create_resp = client.post("/api/requests", json=create_payload)
                    request_id = create_resp.json["id"]

                    _set_session(
                        client, user_id=admin["username"], db_user_id=admin["id"], is_admin=True
                    )
                    with patch.object(
                        main_module.backend, "queue_release", return_value=(True, None)
                    ):
                        with patch("shelfmark.core.request_routes.notify_admin") as mock_notify:
                            with patch(
                                "shelfmark.core.request_routes.notify_user"
                            ) as mock_notify_user:
                                fulfil_resp = client.post(
                                    f"/api/admin/requests/{request_id}/fulfil",
                                    json={"admin_note": "Approved"},
                                )

        assert create_resp.status_code == 201
        assert fulfil_resp.status_code == 200
        mock_notify.assert_called_once()
        event, context = mock_notify.call_args.args
        assert event == NotificationEvent.REQUEST_FULFILLED
        assert context.title == "Fulfil Notify Book"
        assert context.username == user["username"]
        mock_notify_user.assert_called_once()
        user_id, user_event, user_context = mock_notify_user.call_args.args
        assert user_id == user["id"]
        assert user_event == NotificationEvent.REQUEST_FULFILLED
        assert user_context.title == "Fulfil Notify Book"

    def test_admin_fulfil_book_level_request_requires_release_data(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        admin = _create_user(main_module, prefix="admin", role="admin")
        policy = _policy(default_ebook="request_book")

        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)
        create_payload = {
            "book_data": {
                "title": "Designing Data-Intensive Applications",
                "author": "Martin Kleppmann",
                "content_type": "ebook",
                "provider": "openlibrary",
                "provider_id": "ol-7",
            },
            "context": {
                "source": "prowlarr",
                "content_type": "ebook",
                "request_level": "book",
            },
        }

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module, "load_users_request_policy_settings", return_value=policy
            ):
                with patch(
                    "shelfmark.core.request_routes.load_users_request_policy_settings",
                    return_value=policy,
                ):
                    create_resp = client.post("/api/requests", json=create_payload)
                    request_id = create_resp.json["id"]

                    _set_session(
                        client, user_id=admin["username"], db_user_id=admin["id"], is_admin=True
                    )
                    fulfil_resp = client.post(f"/api/admin/requests/{request_id}/fulfil", json={})

        assert fulfil_resp.status_code == 400
        assert "release_data is required to fulfil requests" in fulfil_resp.json["error"]

    def test_admin_fulfil_book_level_request_manual_approval(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        admin = _create_user(main_module, prefix="admin", role="admin")
        policy = _policy(default_ebook="request_book")

        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)
        create_payload = {
            "book_data": {
                "title": "Manual Approval Book",
                "author": "Manual Admin",
                "content_type": "ebook",
                "provider": "openlibrary",
                "provider_id": "ol-manual-approval",
            },
            "context": {
                "source": "prowlarr",
                "content_type": "ebook",
                "request_level": "book",
            },
        }

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module, "load_users_request_policy_settings", return_value=policy
            ):
                with patch(
                    "shelfmark.core.request_routes.load_users_request_policy_settings",
                    return_value=policy,
                ):
                    create_resp = client.post("/api/requests", json=create_payload)
                    request_id = create_resp.json["id"]

                    _set_session(
                        client, user_id=admin["username"], db_user_id=admin["id"], is_admin=True
                    )
                    with patch.object(
                        main_module.backend, "queue_release", return_value=(True, None)
                    ) as mock_queue:
                        fulfil_resp = client.post(
                            f"/api/admin/requests/{request_id}/fulfil",
                            json={"manual_approval": True, "admin_note": "Added manually"},
                        )

        assert create_resp.status_code == 201
        assert fulfil_resp.status_code == 200
        assert fulfil_resp.json["status"] == "fulfilled"
        assert fulfil_resp.json["delivery_state"] == "complete"
        assert fulfil_resp.json["release_data"] is None
        assert fulfil_resp.json["admin_note"] == "Added manually"
        mock_queue.assert_not_called()

    def test_admin_fulfil_book_level_request_with_release_data(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        admin = _create_user(main_module, prefix="admin", role="admin")
        policy = _policy(default_ebook="request_book")

        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)
        create_payload = {
            "book_data": {
                "title": "Book Level Fulfil",
                "author": "QA Author",
                "content_type": "ebook",
                "provider": "openlibrary",
                "provider_id": "ol-book-fulfil",
            },
            "context": {
                "source": "prowlarr",
                "content_type": "ebook",
                "request_level": "book",
            },
        }

        captured: dict[str, object] = {}

        def fake_queue_release(release_data, priority, user_id=None, username=None):
            captured["release_data"] = release_data
            captured["priority"] = priority
            captured["user_id"] = user_id
            captured["username"] = username
            return True, None

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module, "load_users_request_policy_settings", return_value=policy
            ):
                with patch(
                    "shelfmark.core.request_routes.load_users_request_policy_settings",
                    return_value=policy,
                ):
                    create_resp = client.post("/api/requests", json=create_payload)
                    request_id = create_resp.json["id"]

                    _set_session(
                        client, user_id=admin["username"], db_user_id=admin["id"], is_admin=True
                    )
                    with patch.object(
                        main_module.backend, "queue_release", side_effect=fake_queue_release
                    ):
                        fulfil_resp = client.post(
                            f"/api/admin/requests/{request_id}/fulfil",
                            json={
                                "release_data": {
                                    "source": "prowlarr",
                                    "source_id": "book-level-picked-release",
                                    "title": "Book Level Fulfil.epub",
                                }
                            },
                        )

        assert create_resp.status_code == 201
        assert fulfil_resp.status_code == 200
        assert fulfil_resp.json["status"] == "fulfilled"
        assert fulfil_resp.json["request_level"] == "book"
        assert fulfil_resp.json["release_data"]["source_id"] == "book-level-picked-release"
        assert captured["release_data"]["source_id"] == "book-level-picked-release"
        assert captured["priority"] == 0
        assert captured["user_id"] == user["id"]
        assert captured["username"] == user["username"]

    def test_admin_fulfil_uses_real_queue_and_preserves_requesting_identity(
        self, main_module, client
    ):
        class AvailableSource:
            display_name = "Direct Download"

            def is_available(self):
                return True

        user = _create_user(main_module, prefix="reader")
        other_user = _create_user(main_module, prefix="reader")
        admin = _create_user(main_module, prefix="admin", role="admin")
        policy = _policy(default_ebook="request_release")
        source_id = f"real-queue-{uuid.uuid4().hex[:10]}"

        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)
        create_payload = {
            "book_data": {
                "title": "Building Microservices",
                "author": "Sam Newman",
                "content_type": "ebook",
                "provider": "openlibrary",
                "provider_id": "ol-8",
            },
            "context": {
                "source": "direct_download",
                "content_type": "ebook",
                "request_level": "release",
            },
            "release_data": {
                "source": "direct_download",
                "source_id": source_id,
                "title": "Building Microservices.epub",
            },
        }

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module, "load_users_request_policy_settings", return_value=policy
            ):
                with patch(
                    "shelfmark.core.request_routes.load_users_request_policy_settings",
                    return_value=policy,
                ):
                    with patch.object(
                        main_module.backend,
                        "get_source",
                        return_value=AvailableSource(),
                    ):
                        create_resp = client.post("/api/requests", json=create_payload)
                        request_id = create_resp.json["id"]

                        _set_session(
                            client,
                            user_id=admin["username"],
                            db_user_id=admin["id"],
                            is_admin=True,
                        )
                        fulfil_resp = client.post(
                            f"/api/admin/requests/{request_id}/fulfil", json={}
                        )

        assert fulfil_resp.status_code == 200
        assert fulfil_resp.json["status"] == "fulfilled"

        user_status = main_module.backend.queue_status(user_id=user["id"])
        assert source_id in user_status["queued"]
        assert user_status["queued"][source_id]["username"] == user["username"]

        other_status = main_module.backend.queue_status(user_id=other_user["id"])
        assert source_id not in other_status["queued"]


class TestRequestCreationEdgeCases:
    """Edge cases for POST /api/requests."""

    def test_no_json_body_returns_400(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)
        policy = _policy(default_ebook="request_book")

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module, "load_users_request_policy_settings", return_value=policy
            ):
                with patch(
                    "shelfmark.core.request_routes.load_users_request_policy_settings",
                    return_value=policy,
                ):
                    resp = client.post("/api/requests", content_type="text/plain", data="garbage")

        assert resp.status_code == 400
        assert "No data provided" in resp.json["error"]

    def test_missing_book_data_returns_400(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)
        policy = _policy(default_ebook="request_book")

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module, "load_users_request_policy_settings", return_value=policy
            ):
                with patch(
                    "shelfmark.core.request_routes.load_users_request_policy_settings",
                    return_value=policy,
                ):
                    resp = client.post(
                        "/api/requests", json={"context": {"source": "direct_download"}}
                    )

        assert resp.status_code == 400
        assert "book_data must be an object" in resp.json["error"]

    def test_non_dict_context_returns_400(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)
        policy = _policy(default_ebook="request_book")

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module, "load_users_request_policy_settings", return_value=policy
            ):
                with patch(
                    "shelfmark.core.request_routes.load_users_request_policy_settings",
                    return_value=policy,
                ):
                    resp = client.post(
                        "/api/requests",
                        json={
                            "context": "not-a-dict",
                            "book_data": {
                                "title": "X",
                                "author": "Y",
                                "provider": "z",
                                "provider_id": "1",
                            },
                        },
                    )

        assert resp.status_code == 400
        assert "context must be an object" in resp.json["error"]

    def test_book_data_missing_required_fields_returns_400(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)
        policy = _policy(default_ebook="request_book")

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module, "load_users_request_policy_settings", return_value=policy
            ):
                with patch(
                    "shelfmark.core.request_routes.load_users_request_policy_settings",
                    return_value=policy,
                ):
                    resp = client.post(
                        "/api/requests",
                        json={
                            "book_data": {"title": "Only a title"},
                            "context": {
                                "source": "direct_download",
                                "content_type": "ebook",
                                "request_level": "book",
                            },
                        },
                    )

        assert resp.status_code == 400
        assert "missing required field" in resp.json["error"]

    def test_book_data_payload_too_large_returns_400(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)
        policy = _policy(default_ebook="request_book")

        payload = {
            "book_data": {
                "title": "Oversized Book",
                "author": "Big Payload",
                "content_type": "ebook",
                "provider": "openlibrary",
                "provider_id": "ol-big",
                "description": "x" * 12000,
            },
            "context": {
                "source": "direct_download",
                "content_type": "ebook",
                "request_level": "book",
            },
        }

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module, "load_users_request_policy_settings", return_value=policy
            ):
                with patch(
                    "shelfmark.core.request_routes.load_users_request_policy_settings",
                    return_value=policy,
                ):
                    resp = client.post("/api/requests", json=payload)

        assert resp.status_code == 400
        assert "book_data must be <= 10240 bytes" in resp.json["error"]

    def test_disabled_requests_returns_403(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)
        policy = _policy(requests_enabled=False, default_ebook="request_book")

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module, "load_users_request_policy_settings", return_value=policy
            ):
                with patch(
                    "shelfmark.core.request_routes.load_users_request_policy_settings",
                    return_value=policy,
                ):
                    resp = client.post(
                        "/api/requests",
                        json={
                            "book_data": {
                                "title": "T",
                                "author": "A",
                                "provider": "p",
                                "provider_id": "1",
                            },
                            "context": {
                                "source": "direct_download",
                                "content_type": "ebook",
                                "request_level": "book",
                            },
                        },
                    )

        assert resp.status_code == 403
        assert resp.json["code"] == "requests_unavailable"

    def test_download_policy_without_concrete_release_returns_400(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)
        policy = _policy(default_ebook="download")

        payload = {
            "book_data": {
                "title": "Needs Release Selection",
                "author": "Shelfmark",
                "content_type": "ebook",
                "provider": "openlibrary",
                "provider_id": "needs-release-selection",
            },
            "context": {
                "source": "*",
                "content_type": "ebook",
                "request_level": "book",
            },
        }

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module, "load_users_request_policy_settings", return_value=policy
            ):
                with patch(
                    "shelfmark.core.request_routes.load_users_request_policy_settings",
                    return_value=policy,
                ):
                    with patch.object(main_module.backend, "queue_release") as mock_queue_release:
                        resp = client.post("/api/requests", json=payload)

        assert resp.status_code == 400
        assert resp.json["code"] == "policy_requires_download"
        assert resp.json["required_mode"] == "download"
        mock_queue_release.assert_not_called()
        assert main_module.user_db.list_requests(user_id=user["id"]) == []

    def test_blocked_policy_returns_403(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)
        policy = _policy(default_ebook="blocked")

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module, "load_users_request_policy_settings", return_value=policy
            ):
                with patch(
                    "shelfmark.core.request_routes.load_users_request_policy_settings",
                    return_value=policy,
                ):
                    resp = client.post(
                        "/api/requests",
                        json={
                            "book_data": {
                                "title": "T",
                                "author": "A",
                                "provider": "p",
                                "provider_id": "1",
                                "content_type": "ebook",
                            },
                            "context": {
                                "source": "direct_download",
                                "content_type": "ebook",
                                "request_level": "book",
                            },
                        },
                    )

        assert resp.status_code == 403
        assert resp.json["code"] == "policy_blocked"
        assert resp.json["required_mode"] == "blocked"

    def test_direct_requests_are_forced_to_release_level(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)
        policy = _policy(default_ebook="request_book")

        payload = {
            "book_data": {
                "title": "Auto Infer Book",
                "author": "Test Author",
                "content_type": "ebook",
                "provider": "openlibrary",
                "provider_id": "ol-auto-1",
            },
            "context": {"source": "direct_download", "content_type": "ebook"},
        }

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module, "load_users_request_policy_settings", return_value=policy
            ):
                with patch(
                    "shelfmark.core.request_routes.load_users_request_policy_settings",
                    return_value=policy,
                ):
                    resp = client.post("/api/requests", json=payload)

        assert resp.status_code == 201
        assert resp.json["request_level"] == "release"
        assert resp.json["policy_mode"] == "request_release"
        assert resp.json["release_data"]["source"] == "direct_download"
        assert resp.json["release_data"]["source_id"] == "ol-auto-1"
        assert resp.json["release_data"]["search_mode"] == "direct"

    def test_auto_infers_release_level_when_release_data_present(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)
        policy = _policy(default_ebook="request_release")

        payload = {
            "book_data": {
                "title": "Auto Infer Release",
                "author": "Test Author",
                "content_type": "ebook",
                "provider": "openlibrary",
                "provider_id": "ol-auto-2",
            },
            "context": {"source": "prowlarr", "content_type": "ebook"},
            "release_data": {"source": "prowlarr", "source_id": "auto-r", "title": "File.epub"},
        }

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module, "load_users_request_policy_settings", return_value=policy
            ):
                with patch(
                    "shelfmark.core.request_routes.load_users_request_policy_settings",
                    return_value=policy,
                ):
                    resp = client.post("/api/requests", json=payload)

        assert resp.status_code == 201
        assert resp.json["request_level"] == "release"
        assert resp.json["release_data"]["source_id"] == "auto-r"

    def test_release_level_request_with_request_release_policy(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)
        policy = _policy(default_ebook="request_release")

        payload = {
            "book_data": {
                "title": "Release Level Test",
                "author": "RLT Author",
                "content_type": "ebook",
                "provider": "openlibrary",
                "provider_id": "ol-rl",
            },
            "context": {"source": "prowlarr", "content_type": "ebook", "request_level": "release"},
            "release_data": {"source": "prowlarr", "source_id": "rl-1", "title": "RL.epub"},
        }

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module, "load_users_request_policy_settings", return_value=policy
            ):
                with patch(
                    "shelfmark.core.request_routes.load_users_request_policy_settings",
                    return_value=policy,
                ):
                    resp = client.post("/api/requests", json=payload)

        assert resp.status_code == 201
        assert resp.json["policy_mode"] == "request_release"
        assert resp.json["request_level"] == "release"

    def test_without_db_user_id_returns_403(self, main_module, client):
        with client.session_transaction() as sess:
            sess["user_id"] = "some-user"
            sess["is_admin"] = False
            if "db_user_id" in sess:
                del sess["db_user_id"]

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            resp = client.post(
                "/api/requests",
                json={
                    "book_data": {"title": "T", "author": "A", "provider": "p", "provider_id": "1"},
                    "context": {"source": "direct_download"},
                },
            )

        assert resp.status_code == 403
        assert resp.json["code"] == "user_identity_unavailable"

    def test_audiobook_content_type_request(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)
        policy = _policy(default_audiobook="request_release")

        payload = {
            "book_data": {
                "title": "Audiobook Test",
                "author": "AB Author",
                "content_type": "audiobook",
                "provider": "hardcover",
                "provider_id": "hc-ab",
            },
            "context": {
                "source": "prowlarr",
                "content_type": "audiobook",
                "request_level": "release",
            },
            "release_data": {"source": "prowlarr", "source_id": "ab-1", "title": "AB.m4b"},
        }

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module, "load_users_request_policy_settings", return_value=policy
            ):
                with patch(
                    "shelfmark.core.request_routes.load_users_request_policy_settings",
                    return_value=policy,
                ):
                    resp = client.post("/api/requests", json=payload)

        assert resp.status_code == 201
        assert resp.json["content_type"] == "audiobook"


class TestRequestListAndFilterEdgeCases:
    """Edge cases for GET /api/requests and GET /api/admin/requests."""

    def _seed_requests(self, main_module, client, user, policy, count=3):
        """Create multiple requests and return their IDs."""
        ids = []
        for i in range(count):
            payload = {
                "book_data": {
                    "title": f"Seed Book {uuid.uuid4().hex[:6]}",
                    "author": f"Author {i}",
                    "content_type": "ebook",
                    "provider": "openlibrary",
                    "provider_id": f"ol-seed-{uuid.uuid4().hex[:6]}",
                },
                "context": {
                    "source": "direct_download",
                    "content_type": "ebook",
                    "request_level": "book",
                },
            }
            resp = client.post("/api/requests", json=payload)
            assert resp.status_code == 201
            ids.append(resp.json["id"])
        return ids

    def test_list_requests_empty_result(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            resp = client.get("/api/requests")

        assert resp.status_code == 200
        assert resp.json == []

    def test_list_requests_with_status_filter(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _create_user(main_module, prefix="admin", role="admin")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)
        policy = _policy(default_ebook="request_book")

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module, "load_users_request_policy_settings", return_value=policy
            ):
                with patch(
                    "shelfmark.core.request_routes.load_users_request_policy_settings",
                    return_value=policy,
                ):
                    ids = self._seed_requests(main_module, client, user, policy, count=3)

                    # Cancel the first request.
                    client.delete(f"/api/requests/{ids[0]}")

                    # Filter: only pending.
                    pending_resp = client.get("/api/requests?status=pending")
                    cancelled_resp = client.get("/api/requests?status=cancelled")

        assert pending_resp.status_code == 200
        pending_ids = {r["id"] for r in pending_resp.json}
        assert ids[0] not in pending_ids
        assert ids[1] in pending_ids

        assert cancelled_resp.status_code == 200
        cancelled_ids = {r["id"] for r in cancelled_resp.json}
        assert ids[0] in cancelled_ids

    def test_list_requests_with_pagination(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)
        policy = _policy(default_ebook="request_book")

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module, "load_users_request_policy_settings", return_value=policy
            ):
                with patch(
                    "shelfmark.core.request_routes.load_users_request_policy_settings",
                    return_value=policy,
                ):
                    self._seed_requests(main_module, client, user, policy, count=5)

                    page1 = client.get("/api/requests?limit=2&offset=0")
                    page2 = client.get("/api/requests?limit=2&offset=2")

        assert page1.status_code == 200
        assert len(page1.json) == 2

        assert page2.status_code == 200
        assert len(page2.json) == 2

        # Pages should not overlap.
        page1_ids = {r["id"] for r in page1.json}
        page2_ids = {r["id"] for r in page2.json}
        assert page1_ids.isdisjoint(page2_ids)

    def test_user_only_sees_own_requests(self, main_module, client):
        alice = _create_user(main_module, prefix="alice")
        bob = _create_user(main_module, prefix="bob")
        policy = _policy(default_ebook="request_book")

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module, "load_users_request_policy_settings", return_value=policy
            ):
                with patch(
                    "shelfmark.core.request_routes.load_users_request_policy_settings",
                    return_value=policy,
                ):
                    # Alice creates a request.
                    _set_session(
                        client, user_id=alice["username"], db_user_id=alice["id"], is_admin=False
                    )
                    client.post(
                        "/api/requests",
                        json={
                            "book_data": {
                                "title": "Alice Book",
                                "author": "A",
                                "provider": "p",
                                "provider_id": "a1",
                                "content_type": "ebook",
                            },
                            "context": {
                                "source": "direct_download",
                                "content_type": "ebook",
                                "request_level": "book",
                            },
                        },
                    )

                    # Bob creates a request.
                    _set_session(
                        client, user_id=bob["username"], db_user_id=bob["id"], is_admin=False
                    )
                    client.post(
                        "/api/requests",
                        json={
                            "book_data": {
                                "title": "Bob Book",
                                "author": "B",
                                "provider": "p",
                                "provider_id": "b1",
                                "content_type": "ebook",
                            },
                            "context": {
                                "source": "direct_download",
                                "content_type": "ebook",
                                "request_level": "book",
                            },
                        },
                    )

                    # Bob lists — should only see his.
                    bob_list = client.get("/api/requests")

                    # Alice lists — should only see hers.
                    _set_session(
                        client, user_id=alice["username"], db_user_id=alice["id"], is_admin=False
                    )
                    alice_list = client.get("/api/requests")

        assert len(bob_list.json) == 1
        assert bob_list.json[0]["book_data"]["title"] == "Bob Book"

        assert len(alice_list.json) == 1
        assert alice_list.json[0]["book_data"]["title"] == "Alice Book"

    def test_admin_list_includes_username(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        admin = _create_user(main_module, prefix="admin", role="admin")
        policy = _policy(default_ebook="request_book")

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module, "load_users_request_policy_settings", return_value=policy
            ):
                with patch(
                    "shelfmark.core.request_routes.load_users_request_policy_settings",
                    return_value=policy,
                ):
                    _set_session(
                        client, user_id=user["username"], db_user_id=user["id"], is_admin=False
                    )
                    create_resp = client.post(
                        "/api/requests",
                        json={
                            "book_data": {
                                "title": "Admin View",
                                "author": "AV",
                                "provider": "p",
                                "provider_id": "av1",
                                "content_type": "ebook",
                            },
                            "context": {
                                "source": "direct_download",
                                "content_type": "ebook",
                                "request_level": "book",
                            },
                        },
                    )
                    request_id = create_resp.json["id"]

                    _set_session(
                        client, user_id=admin["username"], db_user_id=admin["id"], is_admin=True
                    )
                    resp = client.get("/api/admin/requests")

        assert resp.status_code == 200
        matching = [r for r in resp.json if r["id"] == request_id]
        assert len(matching) == 1
        assert matching[0]["username"] == user["username"]

    def test_admin_list_with_status_filter(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        admin = _create_user(main_module, prefix="admin", role="admin")
        policy = _policy(default_ebook="request_book")

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module, "load_users_request_policy_settings", return_value=policy
            ):
                with patch(
                    "shelfmark.core.request_routes.load_users_request_policy_settings",
                    return_value=policy,
                ):
                    _set_session(
                        client, user_id=user["username"], db_user_id=user["id"], is_admin=False
                    )
                    create_resp = client.post(
                        "/api/requests",
                        json={
                            "book_data": {
                                "title": f"FilterTest-{uuid.uuid4().hex[:6]}",
                                "author": "FT",
                                "provider": "p",
                                "provider_id": f"ft-{uuid.uuid4().hex[:6]}",
                                "content_type": "ebook",
                            },
                            "context": {
                                "source": "direct_download",
                                "content_type": "ebook",
                                "request_level": "book",
                            },
                        },
                    )
                    request_id = create_resp.json["id"]

                    _set_session(
                        client, user_id=admin["username"], db_user_id=admin["id"], is_admin=True
                    )
                    client.post(f"/api/admin/requests/{request_id}/reject", json={})

                    pending_resp = client.get("/api/admin/requests?status=pending")
                    rejected_resp = client.get("/api/admin/requests?status=rejected")

        pending_ids = {r["id"] for r in pending_resp.json}
        rejected_ids = {r["id"] for r in rejected_resp.json}
        assert request_id not in pending_ids
        assert request_id in rejected_ids


class TestCancelEdgeCases:
    """Edge cases for DELETE /api/requests/<id>."""

    def test_cancel_nonexistent_request_returns_404(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            resp = client.delete("/api/requests/99999")

        assert resp.status_code == 404

    def test_cancel_other_users_request_returns_403(self, main_module, client):
        alice = _create_user(main_module, prefix="alice")
        bob = _create_user(main_module, prefix="bob")
        policy = _policy(default_ebook="request_book")

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module, "load_users_request_policy_settings", return_value=policy
            ):
                with patch(
                    "shelfmark.core.request_routes.load_users_request_policy_settings",
                    return_value=policy,
                ):
                    _set_session(
                        client, user_id=alice["username"], db_user_id=alice["id"], is_admin=False
                    )
                    create_resp = client.post(
                        "/api/requests",
                        json={
                            "book_data": {
                                "title": "Alice Only",
                                "author": "A",
                                "provider": "p",
                                "provider_id": "ao1",
                                "content_type": "ebook",
                            },
                            "context": {
                                "source": "direct_download",
                                "content_type": "ebook",
                                "request_level": "book",
                            },
                        },
                    )
                    request_id = create_resp.json["id"]

                    _set_session(
                        client, user_id=bob["username"], db_user_id=bob["id"], is_admin=False
                    )
                    cancel_resp = client.delete(f"/api/requests/{request_id}")

        assert cancel_resp.status_code == 403

    def test_cancel_already_cancelled_returns_409(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)
        policy = _policy(default_ebook="request_book")

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module, "load_users_request_policy_settings", return_value=policy
            ):
                with patch(
                    "shelfmark.core.request_routes.load_users_request_policy_settings",
                    return_value=policy,
                ):
                    create_resp = client.post(
                        "/api/requests",
                        json={
                            "book_data": {
                                "title": "Cancel Twice",
                                "author": "CT",
                                "provider": "p",
                                "provider_id": "ct1",
                                "content_type": "ebook",
                            },
                            "context": {
                                "source": "direct_download",
                                "content_type": "ebook",
                                "request_level": "book",
                            },
                        },
                    )
                    request_id = create_resp.json["id"]

                    first = client.delete(f"/api/requests/{request_id}")
                    second = client.delete(f"/api/requests/{request_id}")

        assert first.status_code == 200
        assert second.status_code == 409
        assert second.json["code"] == "stale_transition"


class TestAdminFulfilEdgeCases:
    """Edge cases for POST /api/admin/requests/<id>/fulfil."""

    def test_fulfil_nonexistent_request_returns_404(self, main_module, client):
        admin = _create_user(main_module, prefix="admin", role="admin")
        _set_session(client, user_id=admin["username"], db_user_id=admin["id"], is_admin=True)

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            resp = client.post(
                "/api/admin/requests/99999/fulfil",
                json={
                    "release_data": {"source": "dd", "source_id": "r1", "title": "f.epub"},
                },
            )

        assert resp.status_code == 404

    def test_fulfil_with_queue_failure_returns_409(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        admin = _create_user(main_module, prefix="admin", role="admin")
        policy = _policy(default_ebook="request_release")

        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module, "load_users_request_policy_settings", return_value=policy
            ):
                with patch(
                    "shelfmark.core.request_routes.load_users_request_policy_settings",
                    return_value=policy,
                ):
                    create_resp = client.post(
                        "/api/requests",
                        json={
                            "book_data": {
                                "title": "Queue Fail",
                                "author": "QF",
                                "provider": "p",
                                "provider_id": "qf1",
                                "content_type": "ebook",
                            },
                            "context": {
                                "source": "prowlarr",
                                "content_type": "ebook",
                                "request_level": "release",
                            },
                            "release_data": {
                                "source": "prowlarr",
                                "source_id": "qf-r",
                                "title": "QF.epub",
                            },
                        },
                    )
                    request_id = create_resp.json["id"]

                    _set_session(
                        client, user_id=admin["username"], db_user_id=admin["id"], is_admin=True
                    )
                    with patch.object(
                        main_module.backend, "queue_release", return_value=(False, "Client offline")
                    ):
                        fulfil_resp = client.post(
                            f"/api/admin/requests/{request_id}/fulfil", json={}
                        )

        assert fulfil_resp.status_code == 409
        assert fulfil_resp.json["code"] == "queue_failed"

    def test_fulfil_with_admin_override_release_data(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        admin = _create_user(main_module, prefix="admin", role="admin")
        policy = _policy(default_ebook="request_release")

        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)

        captured = {}

        def capture_queue(release_data, priority, **kwargs):
            captured["release_data"] = release_data
            return True, None

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module, "load_users_request_policy_settings", return_value=policy
            ):
                with patch(
                    "shelfmark.core.request_routes.load_users_request_policy_settings",
                    return_value=policy,
                ):
                    create_resp = client.post(
                        "/api/requests",
                        json={
                            "book_data": {
                                "title": "Override RD",
                                "author": "OR",
                                "provider": "p",
                                "provider_id": "or1",
                                "content_type": "ebook",
                            },
                            "context": {
                                "source": "prowlarr",
                                "content_type": "ebook",
                                "request_level": "release",
                            },
                            "release_data": {
                                "source": "prowlarr",
                                "source_id": "original-r",
                                "title": "Original.epub",
                            },
                        },
                    )
                    request_id = create_resp.json["id"]

                    _set_session(
                        client, user_id=admin["username"], db_user_id=admin["id"], is_admin=True
                    )
                    with patch.object(
                        main_module.backend, "queue_release", side_effect=capture_queue
                    ):
                        fulfil_resp = client.post(
                            f"/api/admin/requests/{request_id}/fulfil",
                            json={
                                "release_data": {
                                    "source": "direct_download",
                                    "source_id": "better-r",
                                    "title": "Better.epub",
                                },
                            },
                        )

        assert fulfil_resp.status_code == 200
        assert captured["release_data"]["source_id"] == "better-r"
        assert fulfil_resp.json["release_data"]["source_id"] == "better-r"

    def test_admin_without_db_user_id_returns_403(self, main_module, client):
        with client.session_transaction() as sess:
            sess["user_id"] = "admin-user"
            sess["is_admin"] = True
            if "db_user_id" in sess:
                del sess["db_user_id"]

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            resp = client.post("/api/admin/requests/1/fulfil", json={})

        assert resp.status_code == 403
        assert "Admin user identity unavailable" in resp.json["error"]

    def test_fulfil_with_non_boolean_manual_approval_returns_400(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        admin = _create_user(main_module, prefix="admin", role="admin")
        policy = _policy(default_ebook="request_release")

        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module, "load_users_request_policy_settings", return_value=policy
            ):
                with patch(
                    "shelfmark.core.request_routes.load_users_request_policy_settings",
                    return_value=policy,
                ):
                    create_resp = client.post(
                        "/api/requests",
                        json={
                            "book_data": {
                                "title": "Manual Flag Validation",
                                "author": "QA",
                                "provider": "p",
                                "provider_id": "mf1",
                                "content_type": "ebook",
                            },
                            "context": {
                                "source": "prowlarr",
                                "content_type": "ebook",
                                "request_level": "release",
                            },
                            "release_data": {
                                "source": "prowlarr",
                                "source_id": "mf-r",
                                "title": "MF.epub",
                            },
                        },
                    )
                    request_id = create_resp.json["id"]

                    _set_session(
                        client, user_id=admin["username"], db_user_id=admin["id"], is_admin=True
                    )
                    fulfil_resp = client.post(
                        f"/api/admin/requests/{request_id}/fulfil",
                        json={"manual_approval": "yes"},
                    )

        assert create_resp.status_code == 201
        assert fulfil_resp.status_code == 400
        assert "manual_approval must be a boolean" in fulfil_resp.json["error"]


class TestAdminRejectEdgeCases:
    """Edge cases for POST /api/admin/requests/<id>/reject."""

    def test_reject_nonexistent_request_returns_404(self, main_module, client):
        admin = _create_user(main_module, prefix="admin", role="admin")
        _set_session(client, user_id=admin["username"], db_user_id=admin["id"], is_admin=True)

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            resp = client.post("/api/admin/requests/99999/reject", json={})

        assert resp.status_code == 404

    def test_reject_already_fulfilled_returns_409(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        admin = _create_user(main_module, prefix="admin", role="admin")
        policy = _policy(default_ebook="request_release")

        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module, "load_users_request_policy_settings", return_value=policy
            ):
                with patch(
                    "shelfmark.core.request_routes.load_users_request_policy_settings",
                    return_value=policy,
                ):
                    create_resp = client.post(
                        "/api/requests",
                        json={
                            "book_data": {
                                "title": "Rej After Ful",
                                "author": "RAF",
                                "provider": "p",
                                "provider_id": "raf1",
                                "content_type": "ebook",
                            },
                            "context": {
                                "source": "prowlarr",
                                "content_type": "ebook",
                                "request_level": "release",
                            },
                            "release_data": {
                                "source": "prowlarr",
                                "source_id": "raf-r",
                                "title": "RAF.epub",
                            },
                        },
                    )
                    request_id = create_resp.json["id"]

                    _set_session(
                        client, user_id=admin["username"], db_user_id=admin["id"], is_admin=True
                    )
                    with patch.object(
                        main_module.backend, "queue_release", return_value=(True, None)
                    ):
                        client.post(f"/api/admin/requests/{request_id}/fulfil", json={})

                    reject_resp = client.post(f"/api/admin/requests/{request_id}/reject", json={})

        assert reject_resp.status_code == 409
        assert reject_resp.json["code"] == "stale_transition"


class TestAdminCountEdgeCases:
    """Edge cases for GET /api/admin/requests/count."""

    def test_count_reflects_all_statuses(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        admin = _create_user(main_module, prefix="admin", role="admin")
        policy = _policy(default_ebook="request_book")

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module, "load_users_request_policy_settings", return_value=policy
            ):
                with patch(
                    "shelfmark.core.request_routes.load_users_request_policy_settings",
                    return_value=policy,
                ):
                    _set_session(
                        client, user_id=user["username"], db_user_id=user["id"], is_admin=False
                    )

                    # Create 3 requests.
                    ids = []
                    for _i in range(3):
                        resp = client.post(
                            "/api/requests",
                            json={
                                "book_data": {
                                    "title": f"Count Test {uuid.uuid4().hex[:6]}",
                                    "author": "CT",
                                    "provider": "p",
                                    "provider_id": f"ct-{uuid.uuid4().hex[:6]}",
                                    "content_type": "ebook",
                                },
                                "context": {
                                    "source": "direct_download",
                                    "content_type": "ebook",
                                    "request_level": "book",
                                },
                            },
                        )
                        ids.append(resp.json["id"])

                    # Cancel one.
                    client.delete(f"/api/requests/{ids[0]}")

                    # Admin rejects one.
                    _set_session(
                        client, user_id=admin["username"], db_user_id=admin["id"], is_admin=True
                    )
                    client.post(f"/api/admin/requests/{ids[1]}/reject", json={})

                    count_resp = client.get("/api/admin/requests/count")

        assert count_resp.status_code == 200
        by_status = count_resp.json["by_status"]
        # At least 1 of each status we created.
        assert by_status["cancelled"] >= 1
        assert by_status["rejected"] >= 1
        assert by_status["pending"] >= 1
        assert count_resp.json["pending"] == by_status["pending"]
        assert count_resp.json["total"] == sum(by_status.values())


class TestPolicyEndpointEdgeCases:
    """Edge cases for GET /api/request-policy."""

    def test_admin_view_shows_is_admin_true(self, main_module, client):
        admin = _create_user(main_module, prefix="admin", role="admin")
        _set_session(client, user_id=admin["username"], db_user_id=admin["id"], is_admin=True)
        policy = _policy(default_ebook="download")

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module, "load_users_request_policy_settings", return_value=policy
            ):
                with patch(
                    "shelfmark.core.request_routes.load_users_request_policy_settings",
                    return_value=policy,
                ):
                    resp = client.get("/api/request-policy")

        assert resp.status_code == 200
        assert resp.json["is_admin"] is True

    def test_policy_endpoint_reflects_per_user_overrides(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)

        # Global says download, but user override sets request_release for ebook.
        global_policy = _policy(default_ebook="download", default_audiobook="download")
        main_module.user_db.set_user_settings(
            user["id"], {"REQUEST_POLICY_DEFAULT_EBOOK": "request_release"}
        )

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module, "load_users_request_policy_settings", return_value=global_policy
            ):
                with patch(
                    "shelfmark.core.request_routes.load_users_request_policy_settings",
                    return_value=global_policy,
                ):
                    resp = client.get("/api/request-policy")

        assert resp.status_code == 200
        assert resp.json["defaults"]["ebook"] == "request_release"
        assert resp.json["defaults"]["audiobook"] == "download"

    def test_policy_endpoint_without_session_returns_401(self, main_module, client):
        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            resp = client.get("/api/request-policy")

        assert resp.status_code == 401

    def test_policy_endpoint_includes_allow_notes_from_effective_settings(
        self, main_module, client
    ):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)
        policy = _policy(default_ebook="download", requests_allow_notes=False)

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module, "load_users_request_policy_settings", return_value=policy
            ):
                with patch(
                    "shelfmark.core.request_routes.load_users_request_policy_settings",
                    return_value=policy,
                ):
                    resp = client.get("/api/request-policy")

        assert resp.status_code == 200
        assert resp.json["allow_notes"] is False

    def test_policy_endpoint_allow_notes_reflects_per_user_override(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)

        global_policy = _policy(default_ebook="download", requests_allow_notes=False)
        main_module.user_db.set_user_settings(user["id"], {"REQUESTS_ALLOW_NOTES": True})

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module, "load_users_request_policy_settings", return_value=global_policy
            ):
                with patch(
                    "shelfmark.core.request_routes.load_users_request_policy_settings",
                    return_value=global_policy,
                ):
                    resp = client.get("/api/request-policy")

        assert resp.status_code == 200
        assert resp.json["allow_notes"] is True


class TestDownloadPolicyGuardsExtended:
    """Extended policy enforcement tests for download endpoints."""

    def test_download_allowed_when_requests_disabled(self, main_module, client):
        """When REQUESTS_ENABLED is false, policy is not enforced — downloads pass through."""
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)

        # Even though default is blocked, requests are disabled so policy doesn't apply.
        policy = _policy(requests_enabled=False, default_ebook="blocked")

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module, "load_users_request_policy_settings", return_value=policy
            ):
                with patch(
                    "shelfmark.core.request_routes.load_users_request_policy_settings",
                    return_value=policy,
                ):
                    with patch.object(
                        main_module.backend, "queue_release", return_value=(True, None)
                    ):
                        resp = client.post(
                            "/api/releases/download",
                            json={
                                "source": "direct_download",
                                "source_id": "book-pass",
                                "search_mode": "direct",
                            },
                        )

        assert resp.status_code == 200
        assert resp.json["status"] == "queued"

    def test_download_allowed_when_policy_mode_is_download(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)
        policy = _policy(default_ebook="download")

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module, "load_users_request_policy_settings", return_value=policy
            ):
                with patch(
                    "shelfmark.core.request_routes.load_users_request_policy_settings",
                    return_value=policy,
                ):
                    with patch.object(
                        main_module.backend, "queue_release", return_value=(True, None)
                    ):
                        resp = client.post(
                            "/api/releases/download",
                            json={
                                "source": "direct_download",
                                "source_id": "book-free",
                                "search_mode": "direct",
                            },
                        )

        assert resp.status_code == 200
        assert resp.json["status"] == "queued"

    def test_release_download_blocks_with_request_release_policy(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)
        policy = _policy(default_ebook="request_release")

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module, "load_users_request_policy_settings", return_value=policy
            ):
                with patch(
                    "shelfmark.core.request_routes.load_users_request_policy_settings",
                    return_value=policy,
                ):
                    with patch.object(main_module.backend, "queue_release") as mock_queue:
                        resp = client.post(
                            "/api/releases/download",
                            json={
                                "source": "prowlarr",
                                "source_id": "rel-blocked",
                                "content_type": "ebook",
                            },
                        )

        assert resp.status_code == 403
        assert resp.json["code"] == "policy_requires_request"
        assert resp.json["required_mode"] == "request_release"
        mock_queue.assert_not_called()

    def test_release_download_infers_audiobook_type_from_format_when_content_type_missing(
        self, main_module, client
    ):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)
        policy = _policy(default_ebook="download", default_audiobook="blocked")

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module, "load_users_request_policy_settings", return_value=policy
            ):
                with patch(
                    "shelfmark.core.request_routes.load_users_request_policy_settings",
                    return_value=policy,
                ):
                    with patch.object(main_module.backend, "queue_release") as mock_queue:
                        resp = client.post(
                            "/api/releases/download",
                            json={
                                "source": "prowlarr",
                                "source_id": "audio-rel",
                                "title": "Some Audio [m4b]",
                                "format": "m4b",
                            },
                        )

        assert resp.status_code == 403
        assert resp.json["code"] == "policy_blocked"
        assert resp.json["required_mode"] == "blocked"
        mock_queue.assert_not_called()

    def test_release_download_blocks_with_request_book_policy(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)
        policy = _policy(default_ebook="request_book")

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module, "load_users_request_policy_settings", return_value=policy
            ):
                with patch(
                    "shelfmark.core.request_routes.load_users_request_policy_settings",
                    return_value=policy,
                ):
                    with patch.object(main_module.backend, "queue_release") as mock_queue:
                        resp = client.post(
                            "/api/releases/download",
                            json={
                                "source": "direct_download",
                                "source_id": "rel-rbook",
                                "content_type": "ebook",
                            },
                        )

        assert resp.status_code == 403
        assert resp.json["code"] == "policy_requires_request"
        assert resp.json["required_mode"] == "request_release"
        mock_queue.assert_not_called()

    def test_release_download_with_per_source_matrix_rule(self, main_module, client):
        """Prowlarr blocked by matrix rule, but DD still allowed."""
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)
        policy = _policy(
            default_ebook="download",
            rules=[{"source": "prowlarr", "content_type": "*", "mode": "blocked"}],
        )

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module, "load_users_request_policy_settings", return_value=policy
            ):
                with patch(
                    "shelfmark.core.request_routes.load_users_request_policy_settings",
                    return_value=policy,
                ):
                    with patch.object(main_module.backend, "queue_release") as mock_queue:
                        # Prowlarr should be blocked.
                        prowlarr_resp = client.post(
                            "/api/releases/download",
                            json={
                                "source": "prowlarr",
                                "source_id": "prowlarr-rel",
                                "content_type": "ebook",
                            },
                        )

                    with patch.object(
                        main_module.backend, "queue_release", return_value=(True, None)
                    ) as mock_queue_dd:
                        # DD should still be allowed.
                        dd_resp = client.post(
                            "/api/releases/download",
                            json={
                                "source": "direct_download",
                                "source_id": "dd-rel",
                                "content_type": "ebook",
                            },
                        )

        assert prowlarr_resp.status_code == 403
        assert prowlarr_resp.json["code"] == "policy_blocked"
        mock_queue.assert_not_called()

        assert dd_resp.status_code == 200
        mock_queue_dd.assert_called_once()

    def test_per_user_override_unlocks_blocked_source(self, main_module, client):
        """Global blocks prowlarr, per-user override unlocks it."""
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)

        global_policy = _policy(
            default_ebook="download",
            rules=[{"source": "prowlarr", "content_type": "*", "mode": "blocked"}],
        )
        # User override: unblock prowlarr.
        main_module.user_db.set_user_settings(
            user["id"],
            {
                "REQUEST_POLICY_RULES": [
                    {"source": "prowlarr", "content_type": "*", "mode": "download"},
                ],
            },
        )

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module, "load_users_request_policy_settings", return_value=global_policy
            ):
                with patch(
                    "shelfmark.core.request_routes.load_users_request_policy_settings",
                    return_value=global_policy,
                ):
                    with patch.object(
                        main_module.backend, "queue_release", return_value=(True, None)
                    ):
                        resp = client.post(
                            "/api/releases/download",
                            json={
                                "source": "prowlarr",
                                "source_id": "prowlarr-unlocked",
                                "content_type": "ebook",
                            },
                        )

        assert resp.status_code == 200
