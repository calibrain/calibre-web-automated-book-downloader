"""Tests for request lifecycle validation helpers."""

import os
import tempfile

import pytest

from shelfmark.core.request_policy import PolicyMode
from shelfmark.core.request_validation import (
    normalize_delivery_state,
    normalize_policy_mode,
    normalize_request_level,
    normalize_request_status,
    validate_request_level_payload,
    validate_status_transition,
)
from shelfmark.core.requests_service import (
    MAX_REQUEST_JSON_BLOB_BYTES,
    MAX_REQUEST_NOTE_LENGTH,
    RequestServiceError,
    cancel_request,
    create_request,
    create_requests,
    fulfil_request,
    reject_request,
    reopen_failed_request,
    reopen_request,
    sync_delivery_states_from_queue_status,
)
from shelfmark.core.user_db import UserDB


@pytest.fixture
def user_db():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = UserDB(os.path.join(tmpdir, "users.db"))
        db.initialize()
        yield db


def _book_data(content_type: str = "ebook"):
    return {
        "title": "Example Book",
        "author": "Jane Doe",
        "content_type": content_type,
        "provider": "openlibrary",
        "provider_id": "ol-123",
    }


def _release_data():
    return {
        "source": "prowlarr",
        "source_id": "release-123",
        "title": "Example Book Release",
    }


def test_normalize_request_status_accepts_known_values():
    assert normalize_request_status("pending") == "pending"
    assert normalize_request_status("FULFILLED") == "fulfilled"
    assert normalize_request_status(" rejected ") == "rejected"
    assert normalize_request_status("cancelled") == "cancelled"


def test_normalize_request_status_rejects_unknown_values():
    with pytest.raises(ValueError, match="Invalid request status"):
        normalize_request_status("queued")


def test_normalize_delivery_state_accepts_known_values():
    assert normalize_delivery_state("none") == "none"
    assert normalize_delivery_state(" QUEUED ") == "queued"


def test_normalize_delivery_state_rejects_unknown_values():
    with pytest.raises(ValueError, match="Invalid delivery_state"):
        normalize_delivery_state("pending")


def test_normalize_policy_mode_accepts_strings_and_enum():
    assert normalize_policy_mode("download") == "download"
    assert normalize_policy_mode("REQUEST_BOOK") == "request_book"
    assert normalize_policy_mode(PolicyMode.BLOCKED) == "blocked"


def test_normalize_policy_mode_rejects_unknown_values():
    with pytest.raises(ValueError, match="Invalid policy_mode"):
        normalize_policy_mode("allow")


def test_normalize_request_level_accepts_valid_values():
    assert normalize_request_level("book") == "book"
    assert normalize_request_level(" RELEASE ") == "release"


def test_normalize_request_level_rejects_invalid_values():
    with pytest.raises(ValueError, match="Invalid request_level"):
        normalize_request_level("chapter")


def test_validate_request_level_payload_requires_release_data_for_release_level():
    validated_level = validate_request_level_payload("release", {"title": "x"})
    assert validated_level == "release"

    with pytest.raises(ValueError, match="request_level=release requires non-null release_data"):
        validate_request_level_payload("release", None)


def test_validate_request_level_payload_requires_null_release_data_for_book_level():
    validated_level = validate_request_level_payload("book", None)
    assert validated_level == "book"

    with pytest.raises(ValueError, match="request_level=book requires null release_data"):
        validate_request_level_payload("book", {"title": "x"})


def test_validate_status_transition_allows_pending_to_terminal():
    assert validate_status_transition("pending", "fulfilled") == ("pending", "fulfilled")
    assert validate_status_transition("pending", "rejected") == ("pending", "rejected")
    assert validate_status_transition("pending", "cancelled") == ("pending", "cancelled")


def test_validate_status_transition_rejects_terminal_mutation():
    with pytest.raises(ValueError, match="Terminal request statuses are immutable"):
        validate_status_transition("fulfilled", "rejected")

    # No-op re-write to same status is allowed.
    assert validate_status_transition("cancelled", "cancelled") == ("cancelled", "cancelled")


def test_create_request_rejects_overlong_note(user_db):
    user = user_db.create_user(username="alice")

    with pytest.raises(RequestServiceError, match="note must be <="):
        create_request(
            user_db,
            user_id=user["id"],
            source_hint="prowlarr",
            content_type="ebook",
            request_level="book",
            policy_mode="request_book",
            book_data=_book_data(),
            note="x" * (MAX_REQUEST_NOTE_LENGTH + 1),
        )


def test_create_request_rejects_duplicate_pending(user_db):
    user = user_db.create_user(username="alice")

    created = create_request(
        user_db,
        user_id=user["id"],
        source_hint="prowlarr",
        content_type="ebook",
        request_level="book",
        policy_mode="request_book",
        book_data=_book_data(),
    )
    assert created["status"] == "pending"

    with pytest.raises(RequestServiceError, match="Duplicate pending request exists"):
        create_request(
            user_db,
            user_id=user["id"],
            source_hint="prowlarr",
            content_type="ebook",
            request_level="book",
            policy_mode="request_book",
            book_data=_book_data(),
        )


def test_create_requests_rejects_duplicate_entries_and_is_atomic(user_db):
    user = user_db.create_user(username="alice")
    duplicate_request = {
        "user_id": user["id"],
        "source_hint": "direct_download",
        "content_type": "ebook",
        "request_level": "book",
        "policy_mode": "request_book",
        "book_data": _book_data(),
    }

    with pytest.raises(RequestServiceError) as exc_info:
        create_requests(user_db, requests=[duplicate_request, duplicate_request])

    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "duplicate_pending_request"
    assert user_db.list_requests(user_id=user["id"]) == []


def test_create_request_rejects_when_max_pending_limit_reached(user_db):
    user = user_db.create_user(username="alice")

    first_book = _book_data()
    second_book = {
        "title": "Another Book",
        "author": "Jane Doe",
        "content_type": "ebook",
        "provider": "openlibrary",
        "provider_id": "ol-456",
    }

    create_request(
        user_db,
        user_id=user["id"],
        source_hint="direct_download",
        content_type="ebook",
        request_level="book",
        policy_mode="request_book",
        book_data=first_book,
        max_pending_per_user=1,
    )

    with pytest.raises(RequestServiceError) as exc_info:
        create_request(
            user_db,
            user_id=user["id"],
            source_hint="prowlarr",
            content_type="ebook",
            request_level="book",
            policy_mode="request_book",
            book_data=second_book,
            max_pending_per_user=1,
        )
    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "max_pending_reached"


def test_create_request_rejects_oversized_book_data_payload(user_db):
    user = user_db.create_user(username="alice")
    oversized_book = _book_data()
    oversized_book["description"] = "x" * (MAX_REQUEST_JSON_BLOB_BYTES + 1)

    with pytest.raises(RequestServiceError) as exc_info:
        create_request(
            user_db,
            user_id=user["id"],
            source_hint="direct_download",
            content_type="ebook",
            request_level="book",
            policy_mode="request_book",
            book_data=oversized_book,
        )
    assert exc_info.value.status_code == 400
    assert exc_info.value.code == "request_payload_too_large"
    assert "book_data must be <=" in str(exc_info.value)


def test_create_request_rejects_oversized_release_data_payload(user_db):
    user = user_db.create_user(username="alice")
    oversized_release = _release_data()
    oversized_release["details"] = "x" * (MAX_REQUEST_JSON_BLOB_BYTES + 1)

    with pytest.raises(RequestServiceError) as exc_info:
        create_request(
            user_db,
            user_id=user["id"],
            source_hint="prowlarr",
            content_type="ebook",
            request_level="release",
            policy_mode="request_release",
            book_data=_book_data(),
            release_data=oversized_release,
        )
    assert exc_info.value.status_code == 400
    assert exc_info.value.code == "request_payload_too_large"
    assert "release_data must be <=" in str(exc_info.value)


def test_cancel_request_enforces_ownership(user_db):
    alice = user_db.create_user(username="alice")
    bob = user_db.create_user(username="bob")
    created = create_request(
        user_db,
        user_id=alice["id"],
        source_hint="prowlarr",
        content_type="ebook",
        request_level="book",
        policy_mode="request_book",
        book_data=_book_data(),
    )

    with pytest.raises(RequestServiceError, match="Forbidden"):
        cancel_request(
            user_db,
            request_id=created["id"],
            actor_user_id=bob["id"],
        )


def test_reject_request_marks_review_metadata(user_db):
    alice = user_db.create_user(username="alice")
    admin = user_db.create_user(username="admin", role="admin")
    created = create_request(
        user_db,
        user_id=alice["id"],
        source_hint="prowlarr",
        content_type="ebook",
        request_level="book",
        policy_mode="request_book",
        book_data=_book_data(),
    )

    rejected = reject_request(
        user_db,
        request_id=created["id"],
        admin_user_id=admin["id"],
        admin_note="Not available",
    )
    assert rejected["status"] == "rejected"
    assert rejected["reviewed_by"] == admin["id"]
    assert rejected["admin_note"] == "Not available"
    assert rejected["reviewed_at"] is not None


def test_fulfil_request_requires_release_data_for_book_level(user_db):
    alice = user_db.create_user(username="alice")
    admin = user_db.create_user(username="admin", role="admin")
    created = create_request(
        user_db,
        user_id=alice["id"],
        source_hint="prowlarr",
        content_type="ebook",
        request_level="book",
        policy_mode="request_book",
        book_data=_book_data(),
    )

    with pytest.raises(RequestServiceError, match="release_data is required to fulfil requests"):
        fulfil_request(
            user_db,
            request_id=created["id"],
            admin_user_id=admin["id"],
            queue_release=lambda *_args, **_kwargs: (True, None),
        )


def test_fulfil_request_manual_approval_allows_book_level_without_release(user_db):
    alice = user_db.create_user(username="alice")
    admin = user_db.create_user(username="admin", role="admin")
    created = create_request(
        user_db,
        user_id=alice["id"],
        source_hint="direct_download",
        content_type="ebook",
        request_level="book",
        policy_mode="request_book",
        book_data=_book_data(),
    )

    called = {"queue_called": False}

    def fake_queue_release(*_args, **_kwargs):
        called["queue_called"] = True
        return True, None

    fulfilled = fulfil_request(
        user_db,
        request_id=created["id"],
        admin_user_id=admin["id"],
        queue_release=fake_queue_release,
        manual_approval=True,
    )

    assert fulfilled["status"] == "fulfilled"
    assert fulfilled["delivery_state"] == "complete"
    assert fulfilled["delivery_updated_at"] is not None
    assert fulfilled["release_data"] is None
    assert fulfilled["reviewed_by"] == admin["id"]
    assert called["queue_called"] is False


def test_fulfil_request_rejects_oversized_release_override(user_db):
    alice = user_db.create_user(username="alice")
    admin = user_db.create_user(username="admin", role="admin")
    created = create_request(
        user_db,
        user_id=alice["id"],
        source_hint="prowlarr",
        content_type="ebook",
        request_level="release",
        policy_mode="request_release",
        book_data=_book_data(),
        release_data=_release_data(),
    )

    oversized_release = _release_data()
    oversized_release["metadata"] = "x" * (MAX_REQUEST_JSON_BLOB_BYTES + 1)

    called = {"queue_called": False}

    def fake_queue_release(*_args, **_kwargs):
        called["queue_called"] = True
        return True, None

    with pytest.raises(RequestServiceError) as exc_info:
        fulfil_request(
            user_db,
            request_id=created["id"],
            admin_user_id=admin["id"],
            queue_release=fake_queue_release,
            release_data=oversized_release,
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.code == "request_payload_too_large"
    assert called["queue_called"] is False


def test_fulfil_request_queues_as_requesting_user(user_db):
    alice = user_db.create_user(username="alice")
    admin = user_db.create_user(username="admin", role="admin")
    created = create_request(
        user_db,
        user_id=alice["id"],
        source_hint="prowlarr",
        content_type="ebook",
        request_level="release",
        policy_mode="request_release",
        book_data=_book_data(),
        release_data=_release_data(),
    )

    captured: dict[str, object] = {}

    def fake_queue_release(release_data, priority, user_id=None, username=None):
        captured["release_data"] = release_data
        captured["priority"] = priority
        captured["user_id"] = user_id
        captured["username"] = username
        return True, None

    fulfilled = fulfil_request(
        user_db,
        request_id=created["id"],
        admin_user_id=admin["id"],
        queue_release=fake_queue_release,
        admin_note="Approved",
    )

    assert fulfilled["status"] == "fulfilled"
    assert fulfilled["delivery_state"] == "queued"
    assert fulfilled["delivery_updated_at"] is not None
    assert fulfilled["reviewed_by"] == admin["id"]
    assert captured["priority"] == 0
    assert captured["user_id"] == alice["id"]
    assert captured["username"] == "alice"
    assert isinstance(captured["release_data"], dict)
    assert captured["release_data"]["_request_id"] == created["id"]
    assert "_request_id" not in (fulfilled["release_data"] or {})


def test_fulfil_request_claims_request_before_queue_dispatch(user_db):
    alice = user_db.create_user(username="alice")
    admin = user_db.create_user(username="admin", role="admin")
    created = create_request(
        user_db,
        user_id=alice["id"],
        source_hint="prowlarr",
        content_type="ebook",
        request_level="release",
        policy_mode="request_release",
        book_data=_book_data(),
        release_data=_release_data(),
    )

    def fake_queue_release(_release_data_arg, _priority, user_id=None, username=None):
        in_flight = user_db.get_request(created["id"])
        assert in_flight is not None
        assert in_flight["status"] == "fulfilled"
        assert in_flight["delivery_state"] == "queued"
        assert in_flight["reviewed_by"] == admin["id"]
        return True, None

    fulfilled = fulfil_request(
        user_db,
        request_id=created["id"],
        admin_user_id=admin["id"],
        queue_release=fake_queue_release,
    )

    assert fulfilled["status"] == "fulfilled"
    assert user_db.get_request(created["id"])["status"] == "fulfilled"


def test_fulfil_book_level_request_stores_selected_release_data(user_db):
    alice = user_db.create_user(username="alice")
    admin = user_db.create_user(username="admin", role="admin")
    created = create_request(
        user_db,
        user_id=alice["id"],
        source_hint="*",
        content_type="ebook",
        request_level="book",
        policy_mode="request_book",
        book_data=_book_data(),
    )

    selected_release = _release_data()
    selected_release["source_id"] = "admin-picked-book-release"

    captured: dict[str, object] = {}

    def fake_queue_release(release_data, priority, user_id=None, username=None):
        captured["release_data"] = release_data
        captured["priority"] = priority
        captured["user_id"] = user_id
        captured["username"] = username
        return True, None

    fulfilled = fulfil_request(
        user_db,
        request_id=created["id"],
        admin_user_id=admin["id"],
        queue_release=fake_queue_release,
        release_data=selected_release,
    )

    assert fulfilled["status"] == "fulfilled"
    assert fulfilled["delivery_state"] == "queued"
    assert fulfilled["delivery_updated_at"] is not None
    assert fulfilled["request_level"] == "book"
    assert fulfilled["release_data"]["source_id"] == "admin-picked-book-release"
    assert captured["release_data"]["source_id"] == "admin-picked-book-release"
    assert captured["user_id"] == alice["id"]
    assert captured["username"] == "alice"


def test_reopen_failed_request_reverts_to_pending_from_queued_and_clears_on_refulfil(user_db):
    alice = user_db.create_user(username="alice")
    admin = user_db.create_user(username="admin", role="admin")
    created = create_request(
        user_db,
        user_id=alice["id"],
        source_hint="prowlarr",
        content_type="ebook",
        request_level="release",
        policy_mode="request_release",
        book_data=_book_data(),
        release_data=_release_data(),
    )

    fulfilled = fulfil_request(
        user_db,
        request_id=created["id"],
        admin_user_id=admin["id"],
        queue_release=lambda *_args, **_kwargs: (True, None),
    )
    assert fulfilled["status"] == "fulfilled"
    assert fulfilled["delivery_state"] == "queued"
    assert fulfilled["reviewed_by"] == admin["id"]

    reopened = reopen_failed_request(
        user_db,
        request_id=created["id"],
        failure_reason="Download failed: Timeout",
    )
    assert reopened is not None
    assert reopened["status"] == "pending"
    assert reopened["delivery_state"] == "none"
    assert reopened["release_data"] is None
    assert reopened["last_failure_reason"] == "Download failed: Timeout"
    assert reopened["reviewed_by"] is None
    assert reopened["reviewed_at"] is None

    replacement_release = _release_data()
    replacement_release["source_id"] = "release-456"
    refulfilled = fulfil_request(
        user_db,
        request_id=created["id"],
        admin_user_id=admin["id"],
        queue_release=lambda *_args, **_kwargs: (True, None),
        release_data=replacement_release,
    )
    assert refulfilled["status"] == "fulfilled"
    assert refulfilled["release_data"]["source_id"] == "release-456"
    assert refulfilled["last_failure_reason"] is None


def test_reopen_failed_request_delegates_to_user_db_public_api():
    class StubUserDB:
        def __init__(self) -> None:
            self.calls: list[tuple[int, str | None]] = []

        def reopen_failed_request(
            self,
            request_id: int,
            *,
            failure_reason: str | None = None,
        ) -> dict[str, object]:
            self.calls.append((request_id, failure_reason))
            return {"id": request_id, "status": "pending"}

    user_db = StubUserDB()

    reopened = reopen_failed_request(
        user_db,  # type: ignore[arg-type]
        request_id=7,
        failure_reason="  Download failed: Timeout  ",
    )

    assert reopened == {"id": 7, "status": "pending"}
    # Service layer passes failure_reason through; normalization happens in UserDB.
    assert user_db.calls == [(7, "  Download failed: Timeout  ")]


def test_reopen_failed_request_does_not_reopen_completed_delivery(user_db):
    alice = user_db.create_user(username="alice")
    admin = user_db.create_user(username="admin", role="admin")
    created = create_request(
        user_db,
        user_id=alice["id"],
        source_hint="prowlarr",
        content_type="ebook",
        request_level="release",
        policy_mode="request_release",
        book_data=_book_data(),
        release_data=_release_data(),
    )

    fulfilled = fulfil_request(
        user_db,
        request_id=created["id"],
        admin_user_id=admin["id"],
        queue_release=lambda *_args, **_kwargs: (True, None),
    )
    assert fulfilled["status"] == "fulfilled"

    user_db.update_request(
        created["id"],
        delivery_state="complete",
        delivery_updated_at="2026-01-01T00:00:00+00:00",
    )

    reopened = reopen_failed_request(
        user_db,
        request_id=created["id"],
        failure_reason="Download failed: Timeout",
    )
    assert reopened is None


def test_sync_delivery_states_from_queue_status_updates_matching_fulfilled_requests(user_db):
    alice = user_db.create_user(username="alice")
    bob = user_db.create_user(username="bob")

    alice_request = user_db.create_request(
        user_id=alice["id"],
        source_hint="prowlarr",
        content_type="ebook",
        request_level="release",
        policy_mode="request_release",
        book_data=_book_data(),
        release_data={"source": "prowlarr", "source_id": "alice-rel", "title": "Alice Release"},
        status="fulfilled",
        delivery_state="queued",
    )
    bob_request = user_db.create_request(
        user_id=bob["id"],
        source_hint="prowlarr",
        content_type="ebook",
        request_level="release",
        policy_mode="request_release",
        book_data=_book_data(),
        release_data={"source": "prowlarr", "source_id": "bob-rel", "title": "Bob Release"},
        status="fulfilled",
        delivery_state="queued",
    )

    updated = sync_delivery_states_from_queue_status(
        user_db,
        queue_status={
            "downloading": {"alice-rel": {"id": "alice-rel"}},
            "complete": {"bob-rel": {"id": "bob-rel"}},
        },
        user_id=alice["id"],
    )

    assert [row["id"] for row in updated] == [alice_request["id"]]
    refreshed_alice = user_db.get_request(alice_request["id"])
    refreshed_bob = user_db.get_request(bob_request["id"])
    assert refreshed_alice["delivery_state"] == "downloading"
    assert refreshed_alice["delivery_updated_at"] is not None
    assert refreshed_bob["delivery_state"] == "queued"


def test_sync_delivery_states_from_queue_status_uses_request_id_for_duplicate_source_ids(user_db):
    alice = user_db.create_user(username="alice")

    older_request = user_db.create_request(
        user_id=alice["id"],
        source_hint="prowlarr",
        content_type="ebook",
        request_level="release",
        policy_mode="request_release",
        book_data=_book_data(),
        release_data={"source": "prowlarr", "source_id": "shared-rel", "title": "Shared Release"},
        status="fulfilled",
        delivery_state="complete",
    )
    newer_request = user_db.create_request(
        user_id=alice["id"],
        source_hint="prowlarr",
        content_type="ebook",
        request_level="release",
        policy_mode="request_release",
        book_data=_book_data(),
        release_data={"source": "prowlarr", "source_id": "shared-rel", "title": "Shared Release"},
        status="fulfilled",
        delivery_state="queued",
    )

    updated = sync_delivery_states_from_queue_status(
        user_db,
        queue_status={
            "downloading": {
                "shared-rel": {"id": "shared-rel", "request_id": newer_request["id"]},
            },
        },
        user_id=alice["id"],
    )

    assert [row["id"] for row in updated] == [newer_request["id"]]
    assert user_db.get_request(older_request["id"])["delivery_state"] == "complete"
    assert user_db.get_request(newer_request["id"])["delivery_state"] == "downloading"


def test_sync_delivery_states_reopens_fulfilled_request_when_error_is_not_retryable(user_db):
    user = user_db.create_user(username="alice")
    fulfilled_request = user_db.create_request(
        user_id=user["id"],
        source_hint="prowlarr",
        content_type="ebook",
        request_level="release",
        policy_mode="request_release",
        book_data=_book_data(),
        release_data={"source": "prowlarr", "source_id": "retry-gone-rel", "title": "Retry Gone"},
        status="fulfilled",
        delivery_state="queued",
    )

    updated = sync_delivery_states_from_queue_status(
        user_db,
        queue_status={
            "error": {
                "retry-gone-rel": {
                    "id": "retry-gone-rel",
                    "request_id": fulfilled_request["id"],
                    "retry_available": False,
                    "status_message": "Staged retry source no longer exists",
                },
            },
        },
        user_id=user["id"],
    )

    assert [row["id"] for row in updated] == [fulfilled_request["id"]]
    refreshed = user_db.get_request(fulfilled_request["id"])
    assert refreshed["status"] == "pending"
    assert refreshed["delivery_state"] == "none"
    assert refreshed["release_data"] is None
    assert refreshed["last_failure_reason"] == "Staged retry source no longer exists"


def test_sync_delivery_states_preserves_retryable_error_state(user_db):
    user = user_db.create_user(username="alice")
    fulfilled_request = user_db.create_request(
        user_id=user["id"],
        source_hint="prowlarr",
        content_type="ebook",
        request_level="release",
        policy_mode="request_release",
        book_data=_book_data(),
        release_data={"source": "prowlarr", "source_id": "retryable-rel", "title": "Retryable"},
        status="fulfilled",
        delivery_state="queued",
    )

    updated = sync_delivery_states_from_queue_status(
        user_db,
        queue_status={
            "error": {
                "retryable-rel": {
                    "id": "retryable-rel",
                    "request_id": fulfilled_request["id"],
                    "retry_available": True,
                    "status_message": "Temporary daemon failure",
                },
            },
        },
        user_id=user["id"],
    )

    assert [row["id"] for row in updated] == [fulfilled_request["id"]]
    refreshed = user_db.get_request(fulfilled_request["id"])
    assert refreshed["status"] == "fulfilled"
    assert refreshed["delivery_state"] == "error"
    assert refreshed["delivery_updated_at"] is not None
    assert refreshed["last_failure_reason"] is None


# ---------------------------------------------------------------------------
# book_data validation
# ---------------------------------------------------------------------------


def test_create_request_rejects_non_dict_book_data(user_db):
    user = user_db.create_user(username="alice")
    with pytest.raises(RequestServiceError, match="book_data must be an object"):
        create_request(
            user_db,
            user_id=user["id"],
            source_hint="prowlarr",
            content_type="ebook",
            request_level="book",
            policy_mode="request_book",
            book_data="not a dict",
        )


def test_create_request_rejects_book_data_missing_required_fields(user_db):
    user = user_db.create_user(username="alice")
    incomplete_data = {"title": "Some Book"}

    with pytest.raises(RequestServiceError, match="missing required field"):
        create_request(
            user_db,
            user_id=user["id"],
            source_hint="prowlarr",
            content_type="ebook",
            request_level="book",
            policy_mode="request_book",
            book_data=incomplete_data,
        )


def test_create_request_rejects_book_data_with_whitespace_only_fields(user_db):
    user = user_db.create_user(username="alice")
    data = {
        "title": "  ",
        "author": "Jane Doe",
        "provider": "openlibrary",
        "provider_id": "ol-1",
    }

    with pytest.raises(RequestServiceError, match="missing required field.*title"):
        create_request(
            user_db,
            user_id=user["id"],
            source_hint="prowlarr",
            content_type="ebook",
            request_level="book",
            policy_mode="request_book",
            book_data=data,
        )


# ---------------------------------------------------------------------------
# Note validation
# ---------------------------------------------------------------------------


def test_normalize_note_returns_none_for_empty_and_whitespace():
    from shelfmark.core.requests_service import normalize_note

    assert normalize_note(None) is None
    assert normalize_note("") is None
    assert normalize_note("   ") is None


def test_normalize_note_rejects_non_string_types():
    from shelfmark.core.requests_service import normalize_note

    with pytest.raises(RequestServiceError, match="note must be a string"):
        normalize_note(42)

    with pytest.raises(RequestServiceError, match="note must be a string"):
        normalize_note(["a list"])


def test_normalize_note_accepts_boundary_length():
    from shelfmark.core.requests_service import normalize_note

    exact = "x" * MAX_REQUEST_NOTE_LENGTH
    assert normalize_note(exact) == exact

    over = "x" * (MAX_REQUEST_NOTE_LENGTH + 1)
    with pytest.raises(RequestServiceError, match="note must be <="):
        normalize_note(over)


def test_create_request_empty_string_note_stored_as_none(user_db):
    user = user_db.create_user(username="alice")
    created = create_request(
        user_db,
        user_id=user["id"],
        source_hint="direct_download",
        content_type="ebook",
        request_level="book",
        policy_mode="request_book",
        book_data=_book_data(),
        note="   ",
    )
    assert created["note"] is None


# ---------------------------------------------------------------------------
# Duplicate detection edge cases
# ---------------------------------------------------------------------------


def test_duplicate_detection_is_case_insensitive(user_db):
    user = user_db.create_user(username="alice")
    create_request(
        user_db,
        user_id=user["id"],
        source_hint="direct_download",
        content_type="ebook",
        request_level="book",
        policy_mode="request_book",
        book_data=_book_data(),
    )

    upper_case_data = {
        "title": "EXAMPLE BOOK",
        "author": "JANE DOE",
        "content_type": "ebook",
        "provider": "openlibrary",
        "provider_id": "ol-999",
    }
    with pytest.raises(RequestServiceError, match="Duplicate pending request"):
        create_request(
            user_db,
            user_id=user["id"],
            source_hint="direct_download",
            content_type="ebook",
            request_level="book",
            policy_mode="request_book",
            book_data=upper_case_data,
        )


def test_duplicate_detection_trims_whitespace(user_db):
    user = user_db.create_user(username="alice")
    create_request(
        user_db,
        user_id=user["id"],
        source_hint="direct_download",
        content_type="ebook",
        request_level="book",
        policy_mode="request_book",
        book_data=_book_data(),
    )

    padded_data = {
        "title": "  Example Book  ",
        "author": "  Jane Doe  ",
        "content_type": "ebook",
        "provider": "openlibrary",
        "provider_id": "ol-999",
    }
    with pytest.raises(RequestServiceError, match="Duplicate pending request"):
        create_request(
            user_db,
            user_id=user["id"],
            source_hint="direct_download",
            content_type="ebook",
            request_level="book",
            policy_mode="request_book",
            book_data=padded_data,
        )


def test_different_content_type_is_not_duplicate(user_db):
    user = user_db.create_user(username="alice")
    create_request(
        user_db,
        user_id=user["id"],
        source_hint="direct_download",
        content_type="ebook",
        request_level="book",
        policy_mode="request_book",
        book_data=_book_data("ebook"),
    )

    audiobook_request = create_request(
        user_db,
        user_id=user["id"],
        source_hint="prowlarr",
        content_type="audiobook",
        request_level="book",
        policy_mode="request_book",
        book_data=_book_data("audiobook"),
    )
    assert audiobook_request["status"] == "pending"
    assert audiobook_request["content_type"] == "audiobook"


def test_different_user_is_not_duplicate(user_db):
    alice = user_db.create_user(username="alice")
    bob = user_db.create_user(username="bob")

    create_request(
        user_db,
        user_id=alice["id"],
        source_hint="direct_download",
        content_type="ebook",
        request_level="book",
        policy_mode="request_book",
        book_data=_book_data(),
    )

    bobs_request = create_request(
        user_db,
        user_id=bob["id"],
        source_hint="direct_download",
        content_type="ebook",
        request_level="book",
        policy_mode="request_book",
        book_data=_book_data(),
    )
    assert bobs_request["status"] == "pending"


def test_cancelled_request_allows_new_request_for_same_book(user_db):
    user = user_db.create_user(username="alice")
    first = create_request(
        user_db,
        user_id=user["id"],
        source_hint="direct_download",
        content_type="ebook",
        request_level="book",
        policy_mode="request_book",
        book_data=_book_data(),
    )

    cancel_request(user_db, request_id=first["id"], actor_user_id=user["id"])

    second = create_request(
        user_db,
        user_id=user["id"],
        source_hint="direct_download",
        content_type="ebook",
        request_level="book",
        policy_mode="request_book",
        book_data=_book_data(),
    )
    assert second["status"] == "pending"
    assert second["id"] != first["id"]


def test_rejected_request_allows_new_request_for_same_book(user_db):
    alice = user_db.create_user(username="alice")
    admin = user_db.create_user(username="admin", role="admin")
    first = create_request(
        user_db,
        user_id=alice["id"],
        source_hint="direct_download",
        content_type="ebook",
        request_level="book",
        policy_mode="request_book",
        book_data=_book_data(),
    )

    reject_request(user_db, request_id=first["id"], admin_user_id=admin["id"])

    second = create_request(
        user_db,
        user_id=alice["id"],
        source_hint="direct_download",
        content_type="ebook",
        request_level="book",
        policy_mode="request_book",
        book_data=_book_data(),
    )
    assert second["status"] == "pending"


# ---------------------------------------------------------------------------
# Cancel edge cases
# ---------------------------------------------------------------------------


def test_cancel_already_cancelled_request_returns_stale_transition(user_db):
    user = user_db.create_user(username="alice")
    created = create_request(
        user_db,
        user_id=user["id"],
        source_hint="direct_download",
        content_type="ebook",
        request_level="book",
        policy_mode="request_book",
        book_data=_book_data(),
    )

    cancel_request(user_db, request_id=created["id"], actor_user_id=user["id"])

    with pytest.raises(RequestServiceError) as exc_info:
        cancel_request(user_db, request_id=created["id"], actor_user_id=user["id"])
    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "stale_transition"


def test_cancel_fulfilled_request_returns_stale_transition(user_db):
    alice = user_db.create_user(username="alice")
    admin = user_db.create_user(username="admin", role="admin")
    created = create_request(
        user_db,
        user_id=alice["id"],
        source_hint="prowlarr",
        content_type="ebook",
        request_level="release",
        policy_mode="request_release",
        book_data=_book_data(),
        release_data=_release_data(),
    )

    fulfil_request(
        user_db,
        request_id=created["id"],
        admin_user_id=admin["id"],
        queue_release=lambda *a, **kw: (True, None),
    )

    with pytest.raises(RequestServiceError) as exc_info:
        cancel_request(user_db, request_id=created["id"], actor_user_id=alice["id"])
    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "stale_transition"


def test_cancel_rejected_request_returns_stale_transition(user_db):
    alice = user_db.create_user(username="alice")
    admin = user_db.create_user(username="admin", role="admin")
    created = create_request(
        user_db,
        user_id=alice["id"],
        source_hint="direct_download",
        content_type="ebook",
        request_level="book",
        policy_mode="request_book",
        book_data=_book_data(),
    )

    reject_request(user_db, request_id=created["id"], admin_user_id=admin["id"])

    with pytest.raises(RequestServiceError) as exc_info:
        cancel_request(user_db, request_id=created["id"], actor_user_id=alice["id"])
    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "stale_transition"


def test_cancel_nonexistent_request_returns_404(user_db):
    user = user_db.create_user(username="alice")
    with pytest.raises(RequestServiceError) as exc_info:
        cancel_request(user_db, request_id=99999, actor_user_id=user["id"])
    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# Reject edge cases
# ---------------------------------------------------------------------------


def test_reject_nonexistent_request_returns_404(user_db):
    admin = user_db.create_user(username="admin", role="admin")
    with pytest.raises(RequestServiceError) as exc_info:
        reject_request(user_db, request_id=99999, admin_user_id=admin["id"])
    assert exc_info.value.status_code == 404


def test_reject_already_fulfilled_returns_stale_transition(user_db):
    alice = user_db.create_user(username="alice")
    admin = user_db.create_user(username="admin", role="admin")
    created = create_request(
        user_db,
        user_id=alice["id"],
        source_hint="prowlarr",
        content_type="ebook",
        request_level="release",
        policy_mode="request_release",
        book_data=_book_data(),
        release_data=_release_data(),
    )

    fulfil_request(
        user_db,
        request_id=created["id"],
        admin_user_id=admin["id"],
        queue_release=lambda *a, **kw: (True, None),
    )

    with pytest.raises(RequestServiceError) as exc_info:
        reject_request(user_db, request_id=created["id"], admin_user_id=admin["id"])
    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "stale_transition"


def test_reject_request_non_string_admin_note_returns_error(user_db):
    alice = user_db.create_user(username="alice")
    admin = user_db.create_user(username="admin", role="admin")
    created = create_request(
        user_db,
        user_id=alice["id"],
        source_hint="direct_download",
        content_type="ebook",
        request_level="book",
        policy_mode="request_book",
        book_data=_book_data(),
    )

    with pytest.raises(RequestServiceError, match="admin_note must be a string"):
        reject_request(
            user_db,
            request_id=created["id"],
            admin_user_id=admin["id"],
            admin_note=42,
        )


def test_reject_request_empty_admin_note_stored_as_none(user_db):
    alice = user_db.create_user(username="alice")
    admin = user_db.create_user(username="admin", role="admin")
    created = create_request(
        user_db,
        user_id=alice["id"],
        source_hint="direct_download",
        content_type="ebook",
        request_level="book",
        policy_mode="request_book",
        book_data=_book_data(),
    )

    rejected = reject_request(
        user_db,
        request_id=created["id"],
        admin_user_id=admin["id"],
        admin_note="   ",
    )
    assert rejected["admin_note"] is None


# ---------------------------------------------------------------------------
# Fulfil edge cases
# ---------------------------------------------------------------------------


def test_fulfil_nonexistent_request_returns_404(user_db):
    admin = user_db.create_user(username="admin", role="admin")
    with pytest.raises(RequestServiceError) as exc_info:
        fulfil_request(
            user_db,
            request_id=99999,
            admin_user_id=admin["id"],
            queue_release=lambda *a, **kw: (True, None),
        )
    assert exc_info.value.status_code == 404


def test_fulfil_already_rejected_returns_stale_transition(user_db):
    alice = user_db.create_user(username="alice")
    admin = user_db.create_user(username="admin", role="admin")
    created = create_request(
        user_db,
        user_id=alice["id"],
        source_hint="direct_download",
        content_type="ebook",
        request_level="book",
        policy_mode="request_book",
        book_data=_book_data(),
    )

    reject_request(user_db, request_id=created["id"], admin_user_id=admin["id"])

    with pytest.raises(RequestServiceError) as exc_info:
        fulfil_request(
            user_db,
            request_id=created["id"],
            admin_user_id=admin["id"],
            queue_release=lambda *a, **kw: (True, None),
            release_data=_release_data(),
        )
    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "stale_transition"


def test_fulfil_queue_failure_returns_error(user_db):
    alice = user_db.create_user(username="alice")
    admin = user_db.create_user(username="admin", role="admin")
    created = create_request(
        user_db,
        user_id=alice["id"],
        source_hint="prowlarr",
        content_type="ebook",
        request_level="release",
        policy_mode="request_release",
        book_data=_book_data(),
        release_data=_release_data(),
    )

    def failing_queue(*args, **kwargs):
        return False, "Torrent client unreachable"

    with pytest.raises(RequestServiceError) as exc_info:
        fulfil_request(
            user_db,
            request_id=created["id"],
            admin_user_id=admin["id"],
            queue_release=failing_queue,
        )
    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "queue_failed"

    # Request should be rolled back to pending when queue dispatch fails.
    row = user_db.get_request(created["id"])
    assert row["status"] == "pending"
    assert row["delivery_state"] == "none"
    assert row["reviewed_by"] is None
    assert row["reviewed_at"] is None
    assert row["last_failure_reason"] == "Torrent client unreachable"
    assert row["release_data"]["source_id"] == "release-123"


def test_fulfil_request_rolls_back_when_queue_raises(user_db):
    alice = user_db.create_user(username="alice")
    admin = user_db.create_user(username="admin", role="admin")
    created = create_request(
        user_db,
        user_id=alice["id"],
        source_hint="prowlarr",
        content_type="ebook",
        request_level="release",
        policy_mode="request_release",
        book_data=_book_data(),
        release_data=_release_data(),
    )

    captured: dict[str, object] = {}

    def exploding_queue(release_data, priority, user_id=None, username=None):
        captured["release_data"] = release_data
        captured["priority"] = priority
        captured["user_id"] = user_id
        captured["username"] = username
        raise RuntimeError("daemon exploded")

    with pytest.raises(RuntimeError, match="daemon exploded"):
        fulfil_request(
            user_db,
            request_id=created["id"],
            admin_user_id=admin["id"],
            queue_release=exploding_queue,
        )

    assert captured["priority"] == 0
    assert captured["user_id"] == alice["id"]
    assert captured["username"] == "alice"
    assert captured["release_data"]["_request_id"] == created["id"]

    row = user_db.get_request(created["id"])
    assert row["status"] == "pending"
    assert row["delivery_state"] == "none"
    assert row["reviewed_by"] is None
    assert row["reviewed_at"] is None
    assert row["last_failure_reason"] == "Queue dispatch raised an exception"
    assert row["release_data"]["source_id"] == "release-123"


def test_fulfil_admin_can_override_release_data(user_db):
    alice = user_db.create_user(username="alice")
    admin = user_db.create_user(username="admin", role="admin")
    original_release = _release_data()
    created = create_request(
        user_db,
        user_id=alice["id"],
        source_hint="prowlarr",
        content_type="ebook",
        request_level="release",
        policy_mode="request_release",
        book_data=_book_data(),
        release_data=original_release,
    )

    admin_release = {
        "source": "direct_download",
        "source_id": "admin-picked-123",
        "title": "Better quality version.epub",
    }

    captured = {}

    def capture_queue(release_data, priority, **kwargs):
        captured["release_data"] = release_data
        return True, None

    fulfilled = fulfil_request(
        user_db,
        request_id=created["id"],
        admin_user_id=admin["id"],
        queue_release=capture_queue,
        release_data=admin_release,
    )

    assert fulfilled["status"] == "fulfilled"
    assert captured["release_data"]["source_id"] == "admin-picked-123"
    assert fulfilled["release_data"]["source_id"] == "admin-picked-123"


def test_fulfil_deleted_requester_returns_404(user_db):
    alice = user_db.create_user(username="alice")
    admin = user_db.create_user(username="admin", role="admin")
    created = create_request(
        user_db,
        user_id=alice["id"],
        source_hint="prowlarr",
        content_type="ebook",
        request_level="release",
        policy_mode="request_release",
        book_data=_book_data(),
        release_data=_release_data(),
    )

    # Delete the requesting user. CASCADE will also delete the request.
    user_db.delete_user(alice["id"])

    # The request was cascade-deleted, so we get 404 on the request itself.
    with pytest.raises(RequestServiceError) as exc_info:
        fulfil_request(
            user_db,
            request_id=created["id"],
            admin_user_id=admin["id"],
            queue_release=lambda *a, **kw: (True, None),
        )
    assert exc_info.value.status_code == 404


def test_fulfil_non_dict_release_data_returns_error(user_db):
    alice = user_db.create_user(username="alice")
    admin = user_db.create_user(username="admin", role="admin")
    created = create_request(
        user_db,
        user_id=alice["id"],
        source_hint="prowlarr",
        content_type="ebook",
        request_level="release",
        policy_mode="request_release",
        book_data=_book_data(),
        release_data=_release_data(),
    )

    with pytest.raises(RequestServiceError, match="release_data must be an object"):
        fulfil_request(
            user_db,
            request_id=created["id"],
            admin_user_id=admin["id"],
            queue_release=lambda *a, **kw: (True, None),
            release_data="not-a-dict",
        )


def test_fulfil_non_string_admin_note_returns_error(user_db):
    alice = user_db.create_user(username="alice")
    admin = user_db.create_user(username="admin", role="admin")
    created = create_request(
        user_db,
        user_id=alice["id"],
        source_hint="prowlarr",
        content_type="ebook",
        request_level="release",
        policy_mode="request_release",
        book_data=_book_data(),
        release_data=_release_data(),
    )

    with pytest.raises(RequestServiceError, match="admin_note must be a string"):
        fulfil_request(
            user_db,
            request_id=created["id"],
            admin_user_id=admin["id"],
            queue_release=lambda *a, **kw: (True, None),
            admin_note=["not", "a", "string"],
        )


def test_fulfil_non_boolean_manual_approval_returns_error(user_db):
    alice = user_db.create_user(username="alice")
    admin = user_db.create_user(username="admin", role="admin")
    created = create_request(
        user_db,
        user_id=alice["id"],
        source_hint="prowlarr",
        content_type="ebook",
        request_level="release",
        policy_mode="request_release",
        book_data=_book_data(),
        release_data=_release_data(),
    )

    with pytest.raises(RequestServiceError, match="manual_approval must be a boolean"):
        fulfil_request(
            user_db,
            request_id=created["id"],
            admin_user_id=admin["id"],
            queue_release=lambda *a, **kw: (True, None),
            manual_approval="yes",
        )


def test_fulfil_empty_admin_note_stored_as_none(user_db):
    alice = user_db.create_user(username="alice")
    admin = user_db.create_user(username="admin", role="admin")
    created = create_request(
        user_db,
        user_id=alice["id"],
        source_hint="prowlarr",
        content_type="ebook",
        request_level="release",
        policy_mode="request_release",
        book_data=_book_data(),
        release_data=_release_data(),
    )

    fulfilled = fulfil_request(
        user_db,
        request_id=created["id"],
        admin_user_id=admin["id"],
        queue_release=lambda *a, **kw: (True, None),
        admin_note="   ",
    )
    assert fulfilled["admin_note"] is None


# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------


def test_ensure_request_access_admin_can_access_any_request(user_db):
    from shelfmark.core.requests_service import ensure_request_access

    alice = user_db.create_user(username="alice")
    admin = user_db.create_user(username="admin", role="admin")
    created = create_request(
        user_db,
        user_id=alice["id"],
        source_hint="direct_download",
        content_type="ebook",
        request_level="book",
        policy_mode="request_book",
        book_data=_book_data(),
    )

    # Admin can access alice's request.
    row = ensure_request_access(
        user_db,
        request_id=created["id"],
        actor_user_id=admin["id"],
        is_admin=True,
    )
    assert row["id"] == created["id"]


def test_ensure_request_access_non_admin_cannot_access_others_request(user_db):
    from shelfmark.core.requests_service import ensure_request_access

    alice = user_db.create_user(username="alice")
    bob = user_db.create_user(username="bob")
    created = create_request(
        user_db,
        user_id=alice["id"],
        source_hint="direct_download",
        content_type="ebook",
        request_level="book",
        policy_mode="request_book",
        book_data=_book_data(),
    )

    with pytest.raises(RequestServiceError) as exc_info:
        ensure_request_access(
            user_db,
            request_id=created["id"],
            actor_user_id=bob["id"],
            is_admin=False,
        )
    assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
# Audiobook content type
# ---------------------------------------------------------------------------


def test_create_request_with_audiobook_content_type(user_db):
    user = user_db.create_user(username="alice")
    audiobook_data = {
        "title": "Project Hail Mary",
        "author": "Andy Weir",
        "content_type": "audiobook",
        "provider": "hardcover",
        "provider_id": "hc-456",
    }

    created = create_request(
        user_db,
        user_id=user["id"],
        source_hint="prowlarr",
        content_type="audiobook",
        request_level="release",
        policy_mode="request_release",
        book_data=audiobook_data,
        release_data={"source": "prowlarr", "source_id": "r-456", "title": "PHM.m4b"},
    )

    assert created["content_type"] == "audiobook"
    assert created["request_level"] == "release"
    assert created["release_data"]["source_id"] == "r-456"


# ---------------------------------------------------------------------------
# Content type normalization
# ---------------------------------------------------------------------------


def test_create_request_normalizes_content_type_from_book_data(user_db):
    user = user_db.create_user(username="alice")
    book = {
        "title": "Test Normalization",
        "author": "Jane Doe",
        "content_type": "AUDIOBOOKS",
        "provider": "openlibrary",
        "provider_id": "ol-norm",
    }

    created = create_request(
        user_db,
        user_id=user["id"],
        source_hint="prowlarr",
        content_type=None,
        request_level="book",
        policy_mode="request_book",
        book_data=book,
    )

    assert created["content_type"] == "audiobook"


def _rejected_request(user_db, *, request_level="book", release_data=None, note=None):
    alice = user_db.create_user(username="alice")
    admin = user_db.create_user(username="admin", role="admin")
    created = create_request(
        user_db,
        user_id=alice["id"],
        source_hint="prowlarr",
        content_type="ebook",
        request_level=request_level,
        policy_mode="request_release" if release_data else "request_book",
        book_data=_book_data(),
        release_data=release_data,
        note=note,
    )
    reject_request(
        user_db,
        request_id=created["id"],
        admin_user_id=admin["id"],
        admin_note="Sounds like a dumb book",
    )
    return alice, admin, created


def test_reopen_request_moves_rejected_back_to_pending(user_db):
    _alice, admin, created = _rejected_request(user_db)

    reopened = reopen_request(user_db, request_id=created["id"], admin_user_id=admin["id"])

    assert reopened["status"] == "pending"
    assert reopened["reviewed_by"] is None
    assert reopened["reviewed_at"] is None
    assert reopened["admin_note"] is None
    assert reopened["delivery_state"] == "none"


def test_reopen_request_keeps_what_the_user_asked_for(user_db):
    _alice, admin, created = _rejected_request(
        user_db,
        request_level="release",
        release_data=_release_data(),
        note="It is for my dog",
    )

    reopened = reopen_request(
        user_db,
        request_id=created["id"],
        admin_user_id=admin["id"],
        admin_note="  Fair enough  ",
    )

    assert reopened["request_level"] == "release"
    assert reopened["release_data"] == _release_data()
    assert reopened["note"] == "It is for my dog"
    assert reopened["admin_note"] == "Fair enough"


@pytest.mark.parametrize("status", ["pending", "cancelled"])
def test_reopen_request_refuses_a_request_that_is_not_rejected(user_db, status):
    alice = user_db.create_user(username="alice")
    admin = user_db.create_user(username="admin", role="admin")
    created = create_request(
        user_db,
        user_id=alice["id"],
        source_hint="prowlarr",
        content_type="ebook",
        request_level="book",
        policy_mode="request_book",
        book_data=_book_data(),
    )
    if status == "cancelled":
        cancel_request(user_db, request_id=created["id"], actor_user_id=alice["id"])

    with pytest.raises(RequestServiceError) as exc_info:
        reopen_request(user_db, request_id=created["id"], admin_user_id=admin["id"])

    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "stale_transition"
    assert user_db.get_request(created["id"])["status"] == status


def test_reopen_request_refuses_a_fulfilled_request(user_db):
    alice = user_db.create_user(username="alice")
    admin = user_db.create_user(username="admin", role="admin")
    created = create_request(
        user_db,
        user_id=alice["id"],
        source_hint="prowlarr",
        content_type="ebook",
        request_level="release",
        policy_mode="request_release",
        book_data=_book_data(),
        release_data=_release_data(),
    )
    fulfil_request(
        user_db,
        request_id=created["id"],
        admin_user_id=admin["id"],
        queue_release=lambda *a, **kw: (True, None),
    )

    with pytest.raises(RequestServiceError) as exc_info:
        reopen_request(user_db, request_id=created["id"], admin_user_id=admin["id"])

    assert exc_info.value.code == "stale_transition"
    assert user_db.get_request(created["id"])["status"] == "fulfilled"


def test_reopen_request_refuses_when_the_user_already_asked_again(user_db):
    alice, admin, created = _rejected_request(user_db)
    create_request(
        user_db,
        user_id=alice["id"],
        source_hint="prowlarr",
        content_type="ebook",
        request_level="book",
        policy_mode="request_book",
        book_data=_book_data(),
    )

    with pytest.raises(RequestServiceError) as exc_info:
        reopen_request(user_db, request_id=created["id"], admin_user_id=admin["id"])

    assert exc_info.value.code == "duplicate_pending_request"
    assert user_db.get_request(created["id"])["status"] == "rejected"


def test_reopen_request_rejects_a_non_string_admin_note(user_db):
    _alice, admin, created = _rejected_request(user_db)

    with pytest.raises(RequestServiceError) as exc_info:
        reopen_request(user_db, request_id=created["id"], admin_user_id=admin["id"], admin_note=42)

    assert exc_info.value.status_code == 400
    assert user_db.get_request(created["id"])["status"] == "rejected"


def test_reopen_rejected_request_returns_none_for_unknown_or_other_states(user_db):
    assert user_db.reopen_rejected_request(999_999) is None

    alice = user_db.create_user(username="alice")
    created = create_request(
        user_db,
        user_id=alice["id"],
        source_hint="prowlarr",
        content_type="ebook",
        request_level="book",
        policy_mode="request_book",
        book_data=_book_data(),
    )
    assert user_db.reopen_rejected_request(created["id"]) is None
