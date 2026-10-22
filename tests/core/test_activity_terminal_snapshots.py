"""Tests for terminal activity snapshot capture from queue transitions."""

from __future__ import annotations

import importlib
import uuid
from types import SimpleNamespace
from unittest.mock import ANY, patch

import pytest

from shelfmark.core.models import DownloadTask, QueueStatus
from shelfmark.core.notifications import NotificationEvent
from shelfmark.core.viewer_redaction import public_download_id


@pytest.fixture(scope="module")
def main_module():
    """Import `shelfmark.main` with background startup disabled."""
    with patch("shelfmark.download.orchestrator.start"):
        import shelfmark.main as main

        importlib.reload(main)
        return main


def _create_user(main_module, *, prefix: str) -> dict:
    username = f"{prefix}-{uuid.uuid4().hex[:8]}"
    return main_module.user_db.create_user(username=username, role="user")


def _read_download_history_row(main_module, task_id: str):
    conn = main_module.user_db._connect()
    try:
        return conn.execute(
            "SELECT * FROM download_history WHERE task_id = ?",
            (task_id,),
        ).fetchone()
    finally:
        conn.close()


def _create_release_request(main_module, user: dict, task_id: str) -> dict:
    return main_module.user_db.create_request(
        user_id=user["id"],
        content_type="ebook",
        request_level="release",
        policy_mode="request_release",
        book_data={
            "title": "Requested Activity",
            "author": "Activity Author",
            "provider": "openlibrary",
            "provider_id": "activity-req",
        },
        release_data={"source": "prowlarr", "source_id": task_id, "title": "Requested.epub"},
        status="fulfilled",
        delivery_state="queued",
    )


def _activity_update_payloads(mock_emit) -> dict[str, dict]:
    return {
        call.kwargs["to"]: call.args[1]
        for call in mock_emit.call_args_list
        if call.args[0] == "activity_update"
    }


class TestTerminalSnapshotCapture:
    def test_complete_transition_records_direct_snapshot(self, main_module):
        user = _create_user(main_module, prefix="snap-direct")
        task_id = f"direct-{uuid.uuid4().hex[:8]}"
        task = DownloadTask(
            task_id=task_id,
            source="direct_download",
            title="Direct Snapshot",
            user_id=user["id"],
            username=user["username"],
        )
        assert main_module.backend.book_queue.add(task) is True

        try:
            main_module.backend.book_queue.update_status(task_id, QueueStatus.COMPLETE)
            row = _read_download_history_row(main_module, task_id)
            assert row is not None

            row = _read_download_history_row(main_module, task_id)
            assert row is not None
            assert row["user_id"] == user["id"]
            assert row["task_id"] == task_id
            assert row["origin"] == "direct"
            assert row["final_status"] == "complete"
        finally:
            main_module.backend.book_queue.cancel_download(task_id)

    def test_complete_transition_records_requested_origin_for_graduated_request(self, main_module):
        user = _create_user(main_module, prefix="snap-requested")
        task_id = f"requested-{uuid.uuid4().hex[:8]}"
        request_row = main_module.user_db.create_request(
            user_id=user["id"],
            content_type="ebook",
            request_level="release",
            policy_mode="request_release",
            book_data={
                "title": "Requested Snapshot",
                "author": "Snapshot Author",
                "provider": "openlibrary",
                "provider_id": "snapshot-req",
            },
            release_data={
                "source": "prowlarr",
                "source_id": task_id,
                "title": "Requested Snapshot.epub",
            },
            status="fulfilled",
            delivery_state="queued",
        )
        task = DownloadTask(
            task_id=task_id,
            source="prowlarr",
            title="Requested Snapshot",
            user_id=user["id"],
            username=user["username"],
            request_id=request_row["id"],
        )
        assert main_module.backend.book_queue.add(task) is True

        try:
            main_module.backend.book_queue.update_status(task_id, QueueStatus.COMPLETE)
            row = _read_download_history_row(main_module, task_id)
            assert row is not None
            assert row["origin"] == "requested"
            assert row["request_id"] == request_row["id"]
            assert row["task_id"] == task_id
        finally:
            main_module.backend.book_queue.cancel_download(task_id)

    def test_complete_transition_snapshot_uses_latest_terminal_status_message(self, main_module):
        user = _create_user(main_module, prefix="snap-message")
        task_id = f"message-{uuid.uuid4().hex[:8]}"
        task = DownloadTask(
            task_id=task_id,
            source="direct_download",
            title="Message Snapshot",
            user_id=user["id"],
            username=user["username"],
        )
        assert main_module.backend.book_queue.add(task) is True

        try:
            # Simulate a stale in-progress message that used to leak into history snapshots.
            main_module.backend.book_queue.update_status_message(task_id, "Moving file")
            main_module.backend.update_download_status(task_id, "complete", "Complete")

            row = _read_download_history_row(main_module, task_id)
            assert row is not None
            assert row["status_message"] == "Complete"
        finally:
            main_module.backend.book_queue.cancel_download(task_id)

    def test_complete_transition_triggers_download_complete_notification(self, main_module):
        user = _create_user(main_module, prefix="snap-notify-complete")
        task_id = f"notify-complete-{uuid.uuid4().hex[:8]}"
        task = DownloadTask(
            task_id=task_id,
            source="direct_download",
            title="Notify Complete Snapshot",
            author="Notify Author",
            user_id=user["id"],
            username=user["username"],
        )
        assert main_module.backend.book_queue.add(task) is True

        try:
            with patch.object(main_module, "notify_admin") as mock_notify:
                with patch.object(main_module, "notify_user") as mock_notify_user:
                    main_module.backend.book_queue.update_status(task_id, QueueStatus.COMPLETE)

            mock_notify.assert_called_once()
            event, context = mock_notify.call_args.args
            assert event == NotificationEvent.DOWNLOAD_COMPLETE
            assert context.title == "Notify Complete Snapshot"
            assert context.author == "Notify Author"
            assert context.username == user["username"]
            mock_notify_user.assert_called_once()
            user_id, user_event, user_context = mock_notify_user.call_args.args
            assert user_id == user["id"]
            assert user_event == NotificationEvent.DOWNLOAD_COMPLETE
            assert user_context.title == "Notify Complete Snapshot"
        finally:
            main_module.backend.book_queue.cancel_download(task_id)

    def test_complete_transition_emits_activity_update_to_owner_and_admin_rooms(self, main_module):
        user = _create_user(main_module, prefix="snap-activity-update")
        task_id = f"activity-update-{uuid.uuid4().hex[:8]}"
        task = DownloadTask(
            task_id=task_id,
            source="direct_download",
            title="Activity Update Snapshot",
            user_id=user["id"],
            username=user["username"],
        )
        assert main_module.backend.book_queue.add(task) is True

        try:
            with patch.object(main_module.ws_manager, "is_enabled", return_value=True):
                with patch.object(main_module.ws_manager.socketio, "emit") as mock_emit:
                    main_module.backend.book_queue.update_status(task_id, QueueStatus.COMPLETE)

            mock_emit.assert_any_call(
                "activity_update",
                ANY,
                to="admins",
            )
            mock_emit.assert_any_call(
                "activity_update",
                ANY,
                to=f"user_{user['id']}",
            )
        finally:
            main_module.backend.book_queue.cancel_download(task_id)

    def test_activity_update_hides_request_linked_task_id_from_owner(self, main_module):
        user = _create_user(main_module, prefix="snap-activity-redact")
        task_id = f"7:https://tracker.example/download/1?passkey={uuid.uuid4().hex[:8]}"
        request_row = _create_release_request(main_module, user, task_id)
        task = DownloadTask(
            task_id=task_id,
            source="prowlarr",
            title="Requested Activity",
            user_id=user["id"],
            username=user["username"],
            request_id=request_row["id"],
        )
        assert main_module.backend.book_queue.add(task) is True

        try:
            with patch.object(main_module.ws_manager, "is_enabled", return_value=True):
                with patch.object(main_module.ws_manager.socketio, "emit") as mock_emit:
                    main_module.backend.book_queue.update_status(task_id, QueueStatus.COMPLETE)

            payloads = _activity_update_payloads(mock_emit)
            assert payloads["admins"]["task_id"] == task_id
            assert payloads[f"user_{user['id']}"] == {
                "kind": "download_terminal",
                "task_id": public_download_id(task_id),
                "status": "complete",
            }
        finally:
            main_module.backend.book_queue.cancel_download(task_id)

    def test_error_transition_triggers_download_failed_notification(self, main_module):
        user = _create_user(main_module, prefix="snap-notify-error")
        task_id = f"notify-error-{uuid.uuid4().hex[:8]}"
        task = DownloadTask(
            task_id=task_id,
            source="direct_download",
            title="Notify Error Snapshot",
            author="Notify Error Author",
            user_id=user["id"],
            username=user["username"],
        )
        assert main_module.backend.book_queue.add(task) is True

        try:
            main_module.backend.book_queue.update_status_message(task_id, "Resolver timed out")
            with patch.object(main_module, "notify_admin") as mock_notify:
                with patch.object(main_module, "notify_user") as mock_notify_user:
                    main_module.backend.book_queue.update_status(task_id, QueueStatus.ERROR)

            mock_notify.assert_called_once()
            event, context = mock_notify.call_args.args
            assert event == NotificationEvent.DOWNLOAD_FAILED
            assert context.title == "Notify Error Snapshot"
            assert context.error_message == "Resolver timed out"
            mock_notify_user.assert_called_once()
            user_id, user_event, user_context = mock_notify_user.call_args.args
            assert user_id == user["id"]
            assert user_event == NotificationEvent.DOWNLOAD_FAILED
            assert user_context.error_message == "Resolver timed out"
        finally:
            main_module.backend.book_queue.cancel_download(task_id)

    def test_error_transition_keeps_request_fulfilled_when_postprocess_retry_is_available(
        self,
        main_module,
        tmp_path,
    ):
        user = _create_user(main_module, prefix="snap-retryable-request")
        task_id = f"retryable-request-{uuid.uuid4().hex[:8]}"
        staged_file = tmp_path / "retryable-request.epub"
        staged_file.write_text("staged")
        request_row = main_module.user_db.create_request(
            user_id=user["id"],
            content_type="ebook",
            request_level="release",
            policy_mode="request_release",
            book_data={
                "title": "Retryable Request",
                "author": "Retry Author",
                "provider": "openlibrary",
                "provider_id": "retryable-request-1",
            },
            release_data={
                "source": "prowlarr",
                "source_id": task_id,
                "title": "Retryable Request.epub",
            },
            status="fulfilled",
            delivery_state="queued",
        )
        task = DownloadTask(
            task_id=task_id,
            source="prowlarr",
            title="Retryable Request",
            user_id=user["id"],
            username=user["username"],
            request_id=request_row["id"],
            staged_path=str(staged_file),
        )
        assert main_module.backend.book_queue.add(task) is True

        try:
            main_module.backend.book_queue.update_status_message(
                task_id, "Destination not writable"
            )
            with patch.object(main_module, "reopen_failed_request") as mock_reopen:
                main_module.backend.book_queue.update_status(task_id, QueueStatus.ERROR)

            mock_reopen.assert_not_called()
            persisted_request = next(
                row
                for row in main_module.user_db.list_requests(user_id=user["id"])
                if row["id"] == request_row["id"]
            )
            assert persisted_request["status"] == "fulfilled"
            assert persisted_request["release_data"] is not None

            history_row = _read_download_history_row(main_module, task_id)
            assert history_row is not None
            assert history_row["final_status"] == "error"
        finally:
            main_module.backend.book_queue.cancel_download(task_id)

    def test_queue_hook_records_active_row_at_queue_time(self, main_module):
        user = _create_user(main_module, prefix="snap-queue")
        task_id = f"queue-{uuid.uuid4().hex[:8]}"
        task = DownloadTask(
            task_id=task_id,
            source="direct_download",
            title="Queue Time Snapshot",
            author="Queue Author",
            user_id=user["id"],
            username=user["username"],
        )
        assert main_module.backend.book_queue.add(task) is True

        try:
            row = _read_download_history_row(main_module, task_id)
            assert row is not None
            assert row["final_status"] == "active"
            assert row["user_id"] == user["id"]
            assert row["task_id"] == task_id
            assert row["origin"] == "direct"
            assert row["title"] == "Queue Time Snapshot"
            assert row["author"] == "Queue Author"
            assert row["queued_at"] is not None
        finally:
            main_module.backend.book_queue.cancel_download(task_id)

    def test_queue_hook_records_requested_origin_for_request_linked_task(self, main_module):
        user = _create_user(main_module, prefix="snap-queue-req")
        task_id = f"queue-req-{uuid.uuid4().hex[:8]}"
        request_row = main_module.user_db.create_request(
            user_id=user["id"],
            content_type="ebook",
            request_level="release",
            policy_mode="request_release",
            book_data={
                "title": "Requested Queue",
                "author": "Request Author",
                "provider": "openlibrary",
                "provider_id": "queue-req-1",
            },
            release_data={
                "source": "prowlarr",
                "source_id": task_id,
                "title": "Requested Queue.epub",
            },
            status="fulfilled",
            delivery_state="queued",
        )
        task = DownloadTask(
            task_id=task_id,
            source="prowlarr",
            title="Requested Queue",
            user_id=user["id"],
            username=user["username"],
            request_id=request_row["id"],
        )
        assert main_module.backend.book_queue.add(task) is True

        try:
            row = _read_download_history_row(main_module, task_id)
            assert row is not None
            assert row["final_status"] == "active"
            assert row["origin"] == "requested"
            assert row["request_id"] == request_row["id"]
        finally:
            main_module.backend.book_queue.cancel_download(task_id)

    def test_finalize_updates_active_row_to_terminal(self, main_module):
        user = _create_user(main_module, prefix="snap-finalize")
        task_id = f"finalize-{uuid.uuid4().hex[:8]}"
        task = DownloadTask(
            task_id=task_id,
            source="direct_download",
            title="Finalize Snapshot",
            user_id=user["id"],
            username=user["username"],
        )
        assert main_module.backend.book_queue.add(task) is True

        try:
            # Verify active row exists
            row = _read_download_history_row(main_module, task_id)
            assert row is not None
            assert row["final_status"] == "active"

            # Transition to complete
            main_module.backend.book_queue.update_status(task_id, QueueStatus.COMPLETE)

            row = _read_download_history_row(main_module, task_id)
            assert row is not None
            assert row["final_status"] == "complete"
            # Metadata from queue-time should be preserved
            assert row["title"] == "Finalize Snapshot"
            assert row["user_id"] == user["id"]
        finally:
            main_module.backend.book_queue.cancel_download(task_id)

    def test_queue_hook_emits_activity_update_when_requeue_clears_view_state(self, main_module):
        user = _create_user(main_module, prefix="snap-reset")
        task_id = f"reset-{uuid.uuid4().hex[:8]}"
        main_module.activity_view_state_service.dismiss(
            viewer_scope=f"user:{user['id']}",
            item_type="download",
            item_key=f"download:{task_id}",
        )

        task = SimpleNamespace(
            user_id=user["id"],
            username=user["username"],
            request_id=None,
            source="direct_download",
            title="Reset Snapshot",
            author="Reset Author",
            format="epub",
            size="1 MB",
            preview=None,
            content_type="ebook",
        )

        with patch.object(main_module.ws_manager, "is_enabled", return_value=True):
            with patch.object(main_module.ws_manager.socketio, "emit") as mock_emit:
                main_module._record_download_queued(task_id, task)

        mock_emit.assert_any_call(
            "activity_update",
            ANY,
            to="admins",
        )
        mock_emit.assert_any_call(
            "activity_update",
            ANY,
            to=f"user_{user['id']}",
        )

    def test_queue_hook_reset_hides_request_linked_task_id_from_owner(self, main_module):
        user = _create_user(main_module, prefix="snap-reset-redact")
        task_id = f"7:https://tracker.example/download/2?passkey={uuid.uuid4().hex[:8]}"
        request_row = _create_release_request(main_module, user, task_id)
        main_module.activity_view_state_service.dismiss(
            viewer_scope=f"user:{user['id']}",
            item_type="download",
            item_key=f"download:{task_id}",
        )

        task = SimpleNamespace(
            user_id=user["id"],
            username=user["username"],
            request_id=request_row["id"],
            source="prowlarr",
            title="Requested Activity",
            author="Activity Author",
            format="epub",
            size="1 MB",
            preview=None,
            content_type="ebook",
        )

        with patch.object(main_module.ws_manager, "is_enabled", return_value=True):
            with patch.object(main_module.ws_manager.socketio, "emit") as mock_emit:
                main_module._record_download_queued(task_id, task)

        payloads = _activity_update_payloads(mock_emit)
        assert payloads["admins"] == {"kind": "activity_reset", "task_id": task_id}
        assert payloads[f"user_{user['id']}"] == {
            "kind": "activity_reset",
            "task_id": public_download_id(task_id),
        }

    def test_cancelled_transition_does_not_trigger_notification(self, main_module):
        user = _create_user(main_module, prefix="snap-notify-cancel")
        task_id = f"notify-cancel-{uuid.uuid4().hex[:8]}"
        task = DownloadTask(
            task_id=task_id,
            source="direct_download",
            title="Notify Cancel Snapshot",
            author="Notify Cancel Author",
            user_id=user["id"],
            username=user["username"],
        )
        assert main_module.backend.book_queue.add(task) is True

        try:
            with patch.object(main_module, "notify_admin") as mock_notify:
                with patch.object(main_module, "notify_user") as mock_notify_user:
                    main_module.backend.book_queue.update_status(task_id, QueueStatus.CANCELLED)

            mock_notify.assert_not_called()
            mock_notify_user.assert_not_called()
        finally:
            main_module.backend.book_queue.cancel_download(task_id)
