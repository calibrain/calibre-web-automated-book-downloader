"""Baseline guardrail tests for shared release download and queue endpoints."""

from __future__ import annotations

import importlib
import uuid
from unittest.mock import patch

import pytest

from shelfmark.core.models import DownloadTask


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


def _set_authenticated_session(
    client,
    *,
    user_id: str = "alice",
    db_user_id: int | None = 7,
    is_admin: bool = False,
) -> None:
    with client.session_transaction() as sess:
        sess["user_id"] = user_id
        sess["is_admin"] = is_admin
        if db_user_id is not None:
            sess["db_user_id"] = db_user_id


def _create_user(main_module, *, prefix: str, role: str = "user") -> dict:
    username = f"{prefix}-{uuid.uuid4().hex[:8]}"
    return main_module.user_db.create_user(username=username, role=role)


class TestReleaseDownloadEndpointGuardrails:
    def test_empty_json_payload_returns_400(self, main_module, client):
        with patch.object(main_module, "get_auth_mode", return_value="none"):
            with patch.object(main_module.backend, "queue_release") as mock_queue_release:
                resp = client.post("/api/releases/download", json={})

        assert resp.status_code == 400
        assert resp.get_json() == {"error": "No data provided"}
        mock_queue_release.assert_not_called()

    def test_missing_source_id_returns_400(self, main_module, client):
        payload = {
            "source": "direct_download",
            "title": "Example",
        }
        with patch.object(main_module, "get_auth_mode", return_value="none"):
            with patch.object(main_module.backend, "queue_release") as mock_queue_release:
                resp = client.post("/api/releases/download", json=payload)

        assert resp.status_code == 400
        assert resp.get_json() == {"error": "source_id is required"}
        mock_queue_release.assert_not_called()

    def test_missing_source_returns_400(self, main_module, client):
        payload = {
            "source_id": "release-xyz",
            "title": "Example",
        }
        with patch.object(main_module, "get_auth_mode", return_value="none"):
            with patch.object(main_module.backend, "queue_release") as mock_queue_release:
                resp = client.post("/api/releases/download", json=payload)

        assert resp.status_code == 400
        assert resp.get_json() == {"error": "source is required"}
        mock_queue_release.assert_not_called()

    def test_success_returns_queued_payload_and_forwards_user_context(self, main_module, client):
        captured: dict[str, object] = {}

        def fake_queue_release(release_data, priority, user_id=None, username=None):
            captured.update(
                {
                    "release_data": release_data,
                    "priority": priority,
                    "user_id": user_id,
                    "username": username,
                }
            )
            return True, None

        _set_authenticated_session(
            client,
            user_id="bob",
            db_user_id=19,
            is_admin=False,
        )
        payload = {
            "source": "direct_download",
            "source_id": "release-xyz",
            "title": "Release Title",
            "priority": 3,
            "search_mode": "direct",
        }

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module.backend, "queue_release", side_effect=fake_queue_release):
                resp = client.post("/api/releases/download", json=payload)

        assert resp.status_code == 200
        assert resp.get_json() == {"status": "queued", "priority": 3}
        assert captured["release_data"] == {**payload, "content_type": "ebook"}
        assert captured["priority"] == 3
        assert captured["user_id"] == 19
        assert captured["username"] == "bob"

    def test_missing_content_type_infers_audiobook_from_format(self, main_module, client):
        captured: dict[str, object] = {}

        def fake_queue_release(release_data, priority, user_id=None, username=None):
            captured.update(
                {
                    "release_data": release_data,
                    "priority": priority,
                    "user_id": user_id,
                    "username": username,
                }
            )
            return True, None

        payload = {
            "source": "prowlarr",
            "source_id": "release-audio",
            "title": "Audio Title [m4b]",
            "format": "m4b",
            "priority": 1,
        }

        with patch.object(main_module, "get_auth_mode", return_value="none"):
            with patch.object(main_module.backend, "queue_release", side_effect=fake_queue_release):
                resp = client.post("/api/releases/download", json=payload)

        assert resp.status_code == 200
        assert resp.get_json() == {"status": "queued", "priority": 1}
        assert captured["release_data"] == {**payload, "content_type": "audiobook"}
        assert captured["priority"] == 1

    def test_non_json_payload_returns_400(self, main_module, client):
        with patch.object(main_module, "get_auth_mode", return_value="none"):
            with patch.object(main_module.backend, "queue_release") as mock_queue_release:
                resp = client.post(
                    "/api/releases/download",
                    data="not-json",
                    content_type="text/plain",
                )

        body = resp.get_json()
        assert resp.status_code == 400
        assert body == {"error": "No data provided"}
        mock_queue_release.assert_not_called()

    def test_admin_can_queue_release_on_behalf_of_another_user(self, main_module, client):
        target_user = _create_user(main_module, prefix="target")
        admin_user = _create_user(main_module, prefix="admin", role="admin")
        captured: dict[str, object] = {}

        def fake_queue_release(release_data, priority, user_id=None, username=None):
            captured.update(
                {
                    "release_data": release_data,
                    "priority": priority,
                    "user_id": user_id,
                    "username": username,
                }
            )
            return True, None

        _set_authenticated_session(
            client,
            user_id=admin_user["username"],
            db_user_id=admin_user["id"],
            is_admin=True,
        )
        payload = {
            "source": "direct_download",
            "source_id": "release-admin-on-behalf",
            "title": "Release Title",
            "priority": 2,
            "on_behalf_of_user_id": target_user["id"],
        }

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module.backend, "queue_release", side_effect=fake_queue_release):
                resp = client.post("/api/releases/download", json=payload)

        assert resp.status_code == 200
        assert resp.get_json() == {"status": "queued", "priority": 2}
        assert captured["priority"] == 2
        assert captured["user_id"] == target_user["id"]
        assert captured["username"] == target_user["username"]
        assert captured["release_data"] == {
            **payload,
            "content_type": "ebook",
        }

    def test_non_admin_cannot_queue_release_on_behalf_of_user(self, main_module, client):
        target_user = _create_user(main_module, prefix="target")
        actor_user = _create_user(main_module, prefix="actor")
        _set_authenticated_session(
            client,
            user_id=actor_user["username"],
            db_user_id=actor_user["id"],
            is_admin=False,
        )
        payload = {
            "source": "direct_download",
            "source_id": "release-forbidden",
            "title": "Release Title",
            "on_behalf_of_user_id": target_user["id"],
        }

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module.backend, "queue_release") as mock_queue_release:
                resp = client.post("/api/releases/download", json=payload)

        assert resp.status_code == 403
        assert resp.get_json() == {"error": "Admin required"}
        mock_queue_release.assert_not_called()

    @pytest.mark.parametrize("raw_user_id", ["abc", "-1", "0"])
    def test_invalid_on_behalf_user_id_returns_400_for_release_download(
        self, main_module, client, raw_user_id
    ):
        admin_user = _create_user(main_module, prefix="admin", role="admin")
        _set_authenticated_session(
            client,
            user_id=admin_user["username"],
            db_user_id=admin_user["id"],
            is_admin=True,
        )
        payload = {
            "source": "direct_download",
            "source_id": "release-invalid",
            "title": "Release Title",
            "on_behalf_of_user_id": raw_user_id,
        }

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module.backend, "queue_release") as mock_queue_release:
                resp = client.post("/api/releases/download", json=payload)

        assert resp.status_code == 400
        assert resp.get_json() == {"error": "Invalid on_behalf_of_user_id"}
        mock_queue_release.assert_not_called()

    def test_unknown_on_behalf_user_returns_404_for_release_download(self, main_module, client):
        admin_user = _create_user(main_module, prefix="admin", role="admin")
        _set_authenticated_session(
            client,
            user_id=admin_user["username"],
            db_user_id=admin_user["id"],
            is_admin=True,
        )
        payload = {
            "source": "direct_download",
            "source_id": "release-missing-user",
            "title": "Release Title",
            "on_behalf_of_user_id": 99999999,
        }

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module.backend, "queue_release") as mock_queue_release:
                resp = client.post("/api/releases/download", json=payload)

        assert resp.status_code == 404
        assert resp.get_json() == {"error": "User not found"}
        mock_queue_release.assert_not_called()

    def test_on_behalf_release_download_returns_503_when_user_db_unavailable(
        self, main_module, client
    ):
        admin_user = _create_user(main_module, prefix="admin", role="admin")
        _set_authenticated_session(
            client,
            user_id=admin_user["username"],
            db_user_id=admin_user["id"],
            is_admin=True,
        )
        payload = {
            "source": "direct_download",
            "source_id": "release-user-db-missing",
            "title": "Release Title",
            "on_behalf_of_user_id": 7,
        }

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module, "user_db", None):
                with patch.object(main_module.backend, "queue_release") as mock_queue_release:
                    resp = client.post("/api/releases/download", json=payload)

        assert resp.status_code == 503
        assert resp.get_json() == {"error": "User database unavailable"}
        mock_queue_release.assert_not_called()


class TestCancelDownloadEndpointGuardrails:
    def test_owner_can_cancel_direct_download(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_authenticated_session(
            client,
            user_id=user["username"],
            db_user_id=user["id"],
            is_admin=False,
        )
        task = DownloadTask(
            task_id="direct-task-1",
            source="direct_download",
            title="Direct Task",
            user_id=user["id"],
            username=user["username"],
        )

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module.backend.book_queue, "get_task", return_value=task):
                with patch.object(
                    main_module.backend, "cancel_download", return_value=True
                ) as mock_cancel:
                    resp = client.delete("/api/download/direct-task-1/cancel")

        assert resp.status_code == 200
        assert resp.get_json() == {"status": "cancelled", "book_id": "direct-task-1"}
        mock_cancel.assert_called_once_with("direct-task-1")

    def test_non_owner_cannot_cancel_download(self, main_module, client):
        owner = _create_user(main_module, prefix="owner")
        actor = _create_user(main_module, prefix="actor")
        _set_authenticated_session(
            client,
            user_id=actor["username"],
            db_user_id=actor["id"],
            is_admin=False,
        )
        task = DownloadTask(
            task_id="owned-task-1",
            source="direct_download",
            title="Owned Task",
            user_id=owner["id"],
            username=owner["username"],
        )

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module.backend.book_queue, "get_task", return_value=task):
                with patch.object(
                    main_module.backend, "cancel_download", return_value=True
                ) as mock_cancel:
                    resp = client.delete("/api/download/owned-task-1/cancel")

        assert resp.status_code == 403
        assert resp.get_json()["code"] == "download_not_owned"
        mock_cancel.assert_not_called()

    def test_owner_cannot_cancel_graduated_request_download(self, main_module, client):
        user = _create_user(main_module, prefix="requester")
        _set_authenticated_session(
            client,
            user_id=user["username"],
            db_user_id=user["id"],
            is_admin=False,
        )
        request_row = main_module.user_db.create_request(
            user_id=user["id"],
            content_type="ebook",
            request_level="release",
            policy_mode="request_release",
            book_data={
                "title": "Requested Book",
                "author": "Request Author",
                "provider": "openlibrary",
                "provider_id": "req-guard-1",
            },
            release_data={
                "source": "prowlarr",
                "source_id": "requested-task-1",
                "title": "Requested Book.epub",
            },
            status="fulfilled",
            delivery_state="queued",
        )
        task = DownloadTask(
            task_id="requested-task-1",
            source="prowlarr",
            title="Requested Book",
            user_id=user["id"],
            username=user["username"],
            request_id=request_row["id"],
        )

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module.backend.book_queue, "get_task", return_value=task):
                with patch.object(
                    main_module.backend, "cancel_download", return_value=True
                ) as mock_cancel:
                    resp = client.delete("/api/download/requested-task-1/cancel")

        assert resp.status_code == 403
        assert resp.get_json()["code"] == "requested_download_cancel_forbidden"
        mock_cancel.assert_not_called()

    def test_admin_can_cancel_graduated_request_download(self, main_module, client):
        admin = _create_user(main_module, prefix="admin", role="admin")
        requester = _create_user(main_module, prefix="requester")
        _set_authenticated_session(
            client,
            user_id=admin["username"],
            db_user_id=admin["id"],
            is_admin=True,
        )
        main_module.user_db.create_request(
            user_id=requester["id"],
            content_type="ebook",
            request_level="release",
            policy_mode="request_release",
            book_data={
                "title": "Admin Requested Book",
                "author": "Admin Request Author",
                "provider": "openlibrary",
                "provider_id": "req-guard-2",
            },
            release_data={
                "source": "prowlarr",
                "source_id": "requested-task-2",
                "title": "Admin Requested Book.epub",
            },
            status="fulfilled",
            delivery_state="queued",
        )
        task = DownloadTask(
            task_id="requested-task-2",
            source="prowlarr",
            title="Admin Requested Book",
            user_id=requester["id"],
            username=requester["username"],
        )

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module.backend.book_queue, "get_task", return_value=task):
                with patch.object(
                    main_module.backend, "cancel_download", return_value=True
                ) as mock_cancel:
                    resp = client.delete("/api/download/requested-task-2/cancel")

        assert resp.status_code == 200
        assert resp.get_json() == {"status": "cancelled", "book_id": "requested-task-2"}
        mock_cancel.assert_called_once_with("requested-task-2")


class TestRetryDownloadEndpointGuardrails:
    def test_retry_returns_404_when_task_missing(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_authenticated_session(
            client,
            user_id=user["username"],
            db_user_id=user["id"],
            is_admin=False,
        )

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module.backend.book_queue, "get_task", return_value=None):
                with patch.object(main_module.backend, "retry_download") as mock_retry:
                    resp = client.post("/api/download/missing-task/retry")

        assert resp.status_code == 404
        assert resp.get_json() == {"error": "Download not found"}
        mock_retry.assert_not_called()

    def test_owner_can_retry_direct_download(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_authenticated_session(
            client,
            user_id=user["username"],
            db_user_id=user["id"],
            is_admin=False,
        )
        task = DownloadTask(
            task_id="direct-task-retry-1",
            source="direct_download",
            title="Direct Task",
            user_id=user["id"],
            username=user["username"],
        )

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module.backend.book_queue, "get_task", return_value=task):
                with patch.object(
                    main_module.backend, "retry_download", return_value=(True, None)
                ) as mock_retry:
                    resp = client.post("/api/download/direct-task-retry-1/retry")

        assert resp.status_code == 200
        assert resp.get_json() == {"status": "queued", "book_id": "direct-task-retry-1"}
        mock_retry.assert_called_once_with("direct-task-retry-1")

    def test_owner_can_retry_persisted_direct_download_when_live_task_is_missing(
        self, main_module, client
    ):
        user = _create_user(main_module, prefix="reader")
        _set_authenticated_session(
            client,
            user_id=user["username"],
            db_user_id=user["id"],
            is_admin=False,
        )

        retry_payload = {
            "task_id": "persisted-direct-retry-1",
            "source": "direct_download",
            "title": "Persisted Direct Task",
            "user_id": user["id"],
            "username": user["username"],
            "search_mode": "direct",
        }
        main_module.download_history_service.record_download(
            task_id="persisted-direct-retry-1",
            user_id=user["id"],
            username=user["username"],
            request_id=None,
            source="direct_download",
            source_display_name="Direct Download",
            title="Persisted Direct Task",
            author="Direct Author",
            file_format="epub",
            size="1 MB",
            preview=None,
            content_type="ebook",
            downloads=None,
            origin="direct",
            retry_payload=retry_payload,
        )

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module.backend.book_queue, "get_task", return_value=None):
                with patch.object(
                    main_module.backend,
                    "retry_persisted_download",
                    return_value=(True, None),
                ) as mock_retry:
                    resp = client.post("/api/download/persisted-direct-retry-1/retry")

        assert resp.status_code == 200
        assert resp.get_json() == {"status": "queued", "book_id": "persisted-direct-retry-1"}
        assert mock_retry.call_args.args[0] == retry_payload
        assert mock_retry.call_args.kwargs["final_status"] == "active"

    def test_non_owner_cannot_retry_download(self, main_module, client):
        owner = _create_user(main_module, prefix="owner")
        actor = _create_user(main_module, prefix="actor")
        _set_authenticated_session(
            client,
            user_id=actor["username"],
            db_user_id=actor["id"],
            is_admin=False,
        )
        task = DownloadTask(
            task_id="owned-task-retry-1",
            source="direct_download",
            title="Owned Task",
            user_id=owner["id"],
            username=owner["username"],
        )

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module.backend.book_queue, "get_task", return_value=task):
                with patch.object(
                    main_module.backend, "retry_download", return_value=(True, None)
                ) as mock_retry:
                    resp = client.post("/api/download/owned-task-retry-1/retry")

        assert resp.status_code == 403
        assert resp.get_json()["code"] == "download_not_owned"
        mock_retry.assert_not_called()

    def test_retry_forbidden_for_request_id_linked_download(self, main_module, client):
        user = _create_user(main_module, prefix="requester")
        _set_authenticated_session(
            client,
            user_id=user["username"],
            db_user_id=user["id"],
            is_admin=False,
        )
        task = DownloadTask(
            task_id="requested-retry-1",
            source="prowlarr",
            title="Requested Book",
            user_id=user["id"],
            username=user["username"],
            request_id=123,
        )

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module.backend.book_queue, "get_task", return_value=task):
                with patch.object(
                    main_module.backend, "retry_download", return_value=(True, None)
                ) as mock_retry:
                    resp = client.post("/api/download/requested-retry-1/retry")

        assert resp.status_code == 403
        assert resp.get_json()["code"] == "requested_download_retry_forbidden"
        mock_retry.assert_not_called()

    def test_retry_forbidden_for_graduated_request_download(self, main_module, client):
        user = _create_user(main_module, prefix="requester")
        _set_authenticated_session(
            client,
            user_id=user["username"],
            db_user_id=user["id"],
            is_admin=False,
        )
        request_row = main_module.user_db.create_request(
            user_id=user["id"],
            content_type="ebook",
            request_level="release",
            policy_mode="request_release",
            book_data={
                "title": "Requested Book",
                "author": "Request Author",
                "provider": "openlibrary",
                "provider_id": "req-retry-1",
            },
            release_data={
                "source": "prowlarr",
                "source_id": "requested-retry-2",
                "title": "Requested Book.epub",
            },
            status="fulfilled",
            delivery_state="error",
        )
        task = DownloadTask(
            task_id="requested-retry-2",
            source="prowlarr",
            title="Requested Book",
            user_id=user["id"],
            username=user["username"],
            request_id=request_row["id"],
        )

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module.backend.book_queue, "get_task", return_value=task):
                with patch.object(
                    main_module.backend, "retry_download", return_value=(True, None)
                ) as mock_retry:
                    resp = client.post("/api/download/requested-retry-2/retry")

        assert resp.status_code == 403
        assert resp.get_json()["code"] == "requested_download_retry_forbidden"
        mock_retry.assert_not_called()

    def test_retry_allows_request_linked_postprocess_error_with_staged_file(
        self, main_module, client, tmp_path
    ):
        user = _create_user(main_module, prefix="requester")
        _set_authenticated_session(
            client,
            user_id=user["username"],
            db_user_id=user["id"],
            is_admin=False,
        )
        staged_file = tmp_path / "requested-postprocess.epub"
        staged_file.write_text("staged")
        task = DownloadTask(
            task_id="requested-retry-postprocess-1",
            source="prowlarr",
            title="Requested Book",
            user_id=user["id"],
            username=user["username"],
            request_id=123,
            staged_path=str(staged_file),
        )

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module.backend.book_queue, "get_task", return_value=task):
                with patch.object(
                    main_module.backend.book_queue,
                    "get_task_status",
                    return_value=main_module.QueueStatus.ERROR,
                ):
                    with patch.object(
                        main_module.backend, "retry_download", return_value=(True, None)
                    ) as mock_retry:
                        resp = client.post("/api/download/requested-retry-postprocess-1/retry")

        assert resp.status_code == 200
        assert resp.get_json() == {"status": "queued", "book_id": "requested-retry-postprocess-1"}
        mock_retry.assert_called_once_with("requested-retry-postprocess-1")

    def test_retry_allows_persisted_request_postprocess_error_with_staged_file(
        self, main_module, client, tmp_path
    ):
        user = _create_user(main_module, prefix="requester")
        _set_authenticated_session(
            client,
            user_id=user["username"],
            db_user_id=user["id"],
            is_admin=False,
        )

        staged_file = tmp_path / "persisted-request-postprocess.epub"
        staged_file.write_text("staged")
        retry_payload = {
            "task_id": "persisted-request-retry-1",
            "source": "prowlarr",
            "title": "Persisted Requested Book",
            "user_id": user["id"],
            "username": user["username"],
            "request_id": 123,
            "search_mode": "universal",
            "staged_path": str(staged_file),
        }
        main_module.download_history_service.record_download(
            task_id="persisted-request-retry-1",
            user_id=user["id"],
            username=user["username"],
            request_id=123,
            source="prowlarr",
            source_display_name="Prowlarr",
            title="Persisted Requested Book",
            author="Request Author",
            file_format="epub",
            size="1 MB",
            preview=None,
            content_type="ebook",
            downloads=None,
            origin="requested",
            retry_payload=retry_payload,
        )
        main_module.download_history_service.finalize_download(
            task_id="persisted-request-retry-1",
            final_status="error",
            status_message="Output routing failed",
            retry_payload=retry_payload,
        )

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module.backend.book_queue, "get_task", return_value=None):
                with patch.object(
                    main_module.backend,
                    "retry_persisted_download",
                    return_value=(True, None),
                ) as mock_retry:
                    resp = client.post("/api/download/persisted-request-retry-1/retry")

        assert resp.status_code == 200
        assert resp.get_json() == {"status": "queued", "book_id": "persisted-request-retry-1"}
        assert mock_retry.call_args.args[0] == retry_payload
        assert mock_retry.call_args.kwargs["final_status"] == "error"

    def test_retry_returns_409_for_non_retryable_state(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_authenticated_session(
            client,
            user_id=user["username"],
            db_user_id=user["id"],
            is_admin=False,
        )
        task = DownloadTask(
            task_id="direct-task-retry-409",
            source="direct_download",
            title="Direct Task",
            user_id=user["id"],
            username=user["username"],
        )

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module.backend.book_queue, "get_task", return_value=task):
                with patch.object(
                    main_module.backend,
                    "retry_download",
                    return_value=(False, "Download is not in an error state"),
                ) as mock_retry:
                    resp = client.post("/api/download/direct-task-retry-409/retry")

        assert resp.status_code == 409
        assert resp.get_json() == {"error": "Download is not in an error state"}
        mock_retry.assert_called_once_with("direct-task-retry-409")


class TestStatusEndpointGuardrails:
    def test_no_auth_allows_without_session_and_returns_status(self, main_module, client):
        observed: dict[str, object] = {}
        expected_status = {
            "queued": {"book-1": {"title": "One"}},
            "downloading": {},
            "completed": {},
            "failed": {},
            "cancelled": {},
        }

        def fake_queue_status(user_id=None):
            observed["user_id"] = user_id
            return expected_status

        with patch.object(main_module, "get_auth_mode", return_value="none"):
            with patch.object(main_module.backend, "queue_status", side_effect=fake_queue_status):
                resp = client.get("/api/status")

        assert resp.status_code == 200
        assert resp.get_json() == expected_status
        assert observed["user_id"] is None

    def test_auth_enabled_without_session_returns_401(self, main_module, client):
        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            resp = client.get("/api/status")

        assert resp.status_code == 401
        assert resp.get_json() == {"error": "Unauthorized"}

    def test_non_admin_status_is_scoped_to_db_user(self, main_module, client):
        observed: dict[str, object] = {}

        def fake_queue_status(user_id=None):
            observed["user_id"] = user_id
            return {"queued": {}, "downloading": {}, "completed": {}, "failed": {}, "cancelled": {}}

        _set_authenticated_session(
            client,
            user_id="reader",
            db_user_id=55,
            is_admin=False,
        )
        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module.backend, "queue_status", side_effect=fake_queue_status):
                resp = client.get("/api/status")

        assert resp.status_code == 200
        assert observed["user_id"] == 55

    def test_admin_status_is_unscoped(self, main_module, client):
        observed: dict[str, object] = {}

        def fake_queue_status(user_id=None):
            observed["user_id"] = user_id
            return {"queued": {}, "downloading": {}, "completed": {}, "failed": {}, "cancelled": {}}

        _set_authenticated_session(
            client,
            user_id="admin",
            db_user_id=1,
            is_admin=True,
        )
        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module.backend, "queue_status", side_effect=fake_queue_status):
                resp = client.get("/api/status")

        assert resp.status_code == 200
        assert observed["user_id"] is None


class TestErrorTextStaysOutOfResponses:
    """A failing status or config read must not hand the exception text to the caller."""

    def test_status_failure_returns_a_fixed_message(self, main_module, client):
        def boom(user_id=None):
            msg = "cannot open /srv/shelfmark/queue.db"
            raise OSError(msg)

        with patch.object(main_module, "get_auth_mode", return_value="none"):
            with patch.object(main_module.backend, "queue_status", side_effect=boom):
                resp = client.get("/api/status")

        assert resp.status_code == 500
        assert resp.get_json() == {"error": "Could not read the download status"}

    def test_config_failure_returns_a_fixed_message(self, main_module, client):
        def boom(*_args, **_kwargs):
            msg = "cannot read /config/plugins/general.json"
            raise OSError(msg)

        with patch.object(main_module, "get_auth_mode", return_value="none"):
            with patch.object(main_module, "get_session_db_user_id", side_effect=boom):
                resp = client.get("/api/config")

        assert resp.status_code == 500
        assert resp.get_json() == {"error": "Could not load the configuration"}


class TestQueueManagementEndpointGuardrails:
    def test_non_owner_cannot_set_priority(self, main_module, client):
        owner = _create_user(main_module, prefix="owner")
        actor = _create_user(main_module, prefix="actor")
        _set_authenticated_session(
            client,
            user_id=actor["username"],
            db_user_id=actor["id"],
            is_admin=False,
        )
        task = DownloadTask(
            task_id="owned-priority-1",
            source="direct_download",
            title="Owned Task",
            user_id=owner["id"],
            username=owner["username"],
        )

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module.backend.book_queue, "get_task", return_value=task):
                with patch.object(main_module.backend, "set_book_priority") as mock_set_priority:
                    resp = client.put("/api/queue/owned-priority-1/priority", json={"priority": 1})

        assert resp.status_code == 403
        assert resp.get_json()["code"] == "download_not_owned"
        mock_set_priority.assert_not_called()

    def test_owner_can_set_priority(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_authenticated_session(
            client,
            user_id=user["username"],
            db_user_id=user["id"],
            is_admin=False,
        )
        task = DownloadTask(
            task_id="reader-priority-1",
            source="direct_download",
            title="Reader Task",
            user_id=user["id"],
            username=user["username"],
        )

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module.backend.book_queue, "get_task", return_value=task):
                with patch.object(
                    main_module.backend, "set_book_priority", return_value=True
                ) as mock_set_priority:
                    resp = client.put("/api/queue/reader-priority-1/priority", json={"priority": 2})

        assert resp.status_code == 200
        assert resp.get_json() == {
            "status": "updated",
            "book_id": "reader-priority-1",
            "priority": 2,
        }
        mock_set_priority.assert_called_once_with("reader-priority-1", 2)

    def test_non_owner_cannot_reorder_other_users_task(self, main_module, client):
        owner = _create_user(main_module, prefix="owner")
        actor = _create_user(main_module, prefix="actor")
        _set_authenticated_session(
            client,
            user_id=actor["username"],
            db_user_id=actor["id"],
            is_admin=False,
        )
        owned_task = DownloadTask(
            task_id="actor-reorder-1",
            source="direct_download",
            title="Actor Task",
            user_id=actor["id"],
            username=actor["username"],
        )
        other_task = DownloadTask(
            task_id="owner-reorder-1",
            source="direct_download",
            title="Owner Task",
            user_id=owner["id"],
            username=owner["username"],
        )

        def fake_get_task(task_id):
            return {
                "actor-reorder-1": owned_task,
                "owner-reorder-1": other_task,
            }.get(task_id)

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module.backend.book_queue, "get_task", side_effect=fake_get_task
            ):
                with patch.object(main_module.backend, "reorder_queue") as mock_reorder:
                    resp = client.post(
                        "/api/queue/reorder",
                        json={
                            "book_priorities": {
                                "actor-reorder-1": 1,
                                "owner-reorder-1": 0,
                            }
                        },
                    )

        assert resp.status_code == 403
        assert resp.get_json()["code"] == "download_not_owned"
        mock_reorder.assert_not_called()

    def test_non_admin_queue_order_is_scoped_to_owned_tasks(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        other = _create_user(main_module, prefix="other")
        _set_authenticated_session(
            client,
            user_id=user["username"],
            db_user_id=user["id"],
            is_admin=False,
        )
        user_task = DownloadTask(
            task_id="reader-order-1",
            source="direct_download",
            title="Reader Task",
            user_id=user["id"],
            username=user["username"],
        )
        other_task = DownloadTask(
            task_id="other-order-1",
            source="direct_download",
            title="Other Task",
            user_id=other["id"],
            username=other["username"],
        )

        def fake_get_task(task_id):
            return {
                "reader-order-1": user_task,
                "other-order-1": other_task,
            }.get(task_id)

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module.backend,
                "get_queue_order",
                return_value=[
                    {"id": "reader-order-1", "title": "Reader Task", "priority": 0},
                    {"id": "other-order-1", "title": "Other Task", "priority": 1},
                ],
            ):
                with patch.object(
                    main_module.backend.book_queue, "get_task", side_effect=fake_get_task
                ):
                    resp = client.get("/api/queue/order")

        assert resp.status_code == 200
        assert resp.get_json()["queue"] == [
            {"id": "reader-order-1", "title": "Reader Task", "priority": 0}
        ]

    def test_non_admin_active_downloads_are_scoped_to_owned_tasks(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        other = _create_user(main_module, prefix="other")
        _set_authenticated_session(
            client,
            user_id=user["username"],
            db_user_id=user["id"],
            is_admin=False,
        )
        user_task = DownloadTask(
            task_id="reader-active-1",
            source="direct_download",
            title="Reader Task",
            user_id=user["id"],
            username=user["username"],
        )
        other_task = DownloadTask(
            task_id="other-active-1",
            source="direct_download",
            title="Other Task",
            user_id=other["id"],
            username=other["username"],
        )

        def fake_get_task(task_id):
            return {
                "reader-active-1": user_task,
                "other-active-1": other_task,
            }.get(task_id)

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module.backend,
                "get_active_downloads",
                return_value=["reader-active-1", "other-active-1"],
            ):
                with patch.object(
                    main_module.backend.book_queue, "get_task", side_effect=fake_get_task
                ):
                    resp = client.get("/api/downloads/active")

        assert resp.status_code == 200
        assert resp.get_json() == {"active_downloads": ["reader-active-1"]}
