"""Request lifecycle helpers and service-level validation."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from shelfmark.core.models import QueueStatus
from shelfmark.core.request_helpers import (
    extract_release_source_id,
    normalize_positive_int,
)
from shelfmark.core.request_policy import normalize_content_type
from shelfmark.core.request_validation import (
    DELIVERY_STATE_NONE,
    RequestStatus,
    normalize_policy_mode,
    validate_request_level_payload,
)

MAX_REQUEST_NOTE_LENGTH = 1000
MAX_REQUEST_JSON_BLOB_BYTES = 10 * 1024


if TYPE_CHECKING:
    from collections.abc import Callable

    from shelfmark.core.user_db import UserDB


class RequestServiceError(ValueError):
    """Structured error raised by request lifecycle service methods."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 400,
        code: str | None = None,
        required_mode: str | None = None,
    ) -> None:
        """Initialize the error with HTTP metadata for API callers."""
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.required_mode = required_mode


def _normalize_match_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().lower()


def normalize_note(note: object) -> str | None:
    """Validate request notes and normalize empty strings to None."""
    if note is None:
        return None
    if not isinstance(note, str):
        msg = "note must be a string"
        raise RequestServiceError(msg, status_code=400)
    normalized = note.strip()
    if len(normalized) > MAX_REQUEST_NOTE_LENGTH:
        msg_0 = f"note must be <= {MAX_REQUEST_NOTE_LENGTH} characters"
        raise RequestServiceError(
            msg_0,
            status_code=400,
        )
    return normalized or None


def _validate_book_data(book_data: object) -> dict[str, Any]:
    if not isinstance(book_data, dict):
        msg = "book_data must be an object"
        raise RequestServiceError(msg, status_code=400)

    required_fields = ("title", "author", "provider", "provider_id")
    missing = [
        field for field in required_fields if not _normalize_match_text(book_data.get(field))
    ]
    if missing:
        msg_0 = f"book_data missing required field(s): {', '.join(missing)}"
        raise RequestServiceError(
            msg_0,
            status_code=400,
        )
    return dict(book_data)


def _validate_json_blob_size(field: str, payload: object) -> None:
    if payload is None:
        return

    try:
        serialized = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        msg = f"{field} must be JSON-serializable"
        raise RequestServiceError(msg, status_code=400) from exc

    payload_size = len(serialized.encode("utf-8"))
    if payload_size > MAX_REQUEST_JSON_BLOB_BYTES:
        msg = f"{field} must be <= {MAX_REQUEST_JSON_BLOB_BYTES} bytes"
        raise RequestServiceError(
            msg,
            status_code=400,
            code="request_payload_too_large",
        )


def _find_duplicate_pending_request(
    user_db: UserDB,
    *,
    user_id: int,
    title: str,
    author: str,
    content_type: str,
) -> dict[str, Any] | None:
    pending_rows = user_db.list_requests(user_id=user_id, status=RequestStatus.PENDING)
    for row in pending_rows:
        row_book_data = row.get("book_data") or {}
        if not isinstance(row_book_data, dict):
            continue

        row_title = _normalize_match_text(row_book_data.get("title"))
        row_author = _normalize_match_text(row_book_data.get("author"))
        row_content_type = normalize_content_type(
            row.get("content_type") or row_book_data.get("content_type")
        )
        if row_title == title and row_author == author and row_content_type == content_type:
            return row
    return None


def _now_timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _normalize_admin_note(admin_note: object) -> str | None:
    if admin_note is None:
        return None
    if not isinstance(admin_note, str):
        msg = "admin_note must be a string"
        raise RequestServiceError(msg, status_code=400)
    return admin_note.strip() or None


def _prepare_request_create(
    *,
    user_id: int,
    source_hint: str | None,
    content_type: object,
    request_level: object,
    policy_mode: object,
    book_data: object,
    release_data: object = None,
    note: object = None,
) -> dict[str, Any]:
    validated_book_data = _validate_book_data(book_data)
    normalized_note = normalize_note(note)
    normalized_content_type = normalize_content_type(
        content_type or validated_book_data.get("content_type")
    )
    validated_book_data["content_type"] = normalized_content_type

    try:
        normalized_request_level = validate_request_level_payload(request_level, release_data)
        normalized_policy_mode = normalize_policy_mode(policy_mode)
    except (ValueError, TypeError) as exc:
        raise RequestServiceError(str(exc), status_code=400) from exc

    _validate_json_blob_size("book_data", validated_book_data)
    _validate_json_blob_size("release_data", release_data)

    return {
        "user_id": user_id,
        "source_hint": source_hint,
        "content_type": normalized_content_type,
        "request_level": normalized_request_level,
        "policy_mode": normalized_policy_mode,
        "book_data": validated_book_data,
        "release_data": release_data,
        "note": normalized_note,
    }


def sync_delivery_states_from_queue_status(
    user_db: UserDB,
    *,
    queue_status: dict[str, dict[str, Any]],
    user_id: int | None = None,
) -> list[dict[str, Any]]:
    """Persist delivery-state transitions for fulfilled requests based on queue status."""
    fulfilled_rows = user_db.list_requests(user_id=user_id, status=RequestStatus.FULFILLED)
    if not fulfilled_rows:
        return []

    unique_request_ids_by_source: dict[str, int] = {}
    ambiguous_source_ids: set[str] = set()
    for row in fulfilled_rows:
        source_id = extract_release_source_id(row.get("release_data"))
        if source_id is None:
            continue
        if source_id in unique_request_ids_by_source:
            ambiguous_source_ids.add(source_id)
            continue
        unique_request_ids_by_source[source_id] = int(row["id"])
    for source_id in ambiguous_source_ids:
        unique_request_ids_by_source.pop(source_id, None)

    request_delivery_states: dict[int, str] = {}
    request_delivery_payloads: dict[int, dict[str, Any]] = {}
    for status_key in QueueStatus:
        status_bucket = queue_status.get(status_key)
        if not isinstance(status_bucket, dict):
            continue
        for source_id, task_payload in status_bucket.items():
            request_id = None
            if isinstance(task_payload, dict):
                request_id = normalize_positive_int(task_payload.get("request_id"))
            if request_id is None:
                request_id = unique_request_ids_by_source.get(str(source_id).strip())
            if request_id is None:
                continue
            request_delivery_states[request_id] = status_key
            if isinstance(task_payload, dict):
                request_delivery_payloads[request_id] = dict(task_payload)

    if not request_delivery_states:
        return []
    updated: list[dict[str, Any]] = []

    for row in fulfilled_rows:
        request_id = int(row["id"])
        delivery_state = request_delivery_states.get(request_id)
        if delivery_state is None:
            continue

        task_payload = request_delivery_payloads.get(request_id) or {}
        retry_available = task_payload.get("retry_available")
        if delivery_state == QueueStatus.ERROR and retry_available is False:
            raw_status_message = task_payload.get("status_message")
            failure_reason = (
                raw_status_message.strip()
                if isinstance(raw_status_message, str) and raw_status_message.strip()
                else "Download failed"
            )
            reopened = user_db.reopen_failed_request(
                request_id,
                failure_reason=failure_reason,
            )
            if reopened is not None:
                updated.append(reopened)
                continue

        if row.get("delivery_state", DELIVERY_STATE_NONE) == delivery_state:
            continue

        updated.append(
            user_db.update_request(
                request_id,
                delivery_state=delivery_state,
                delivery_updated_at=_now_timestamp(),
            )
        )

    return updated


def create_request(
    user_db: UserDB,
    *,
    user_id: int,
    source_hint: str | None,
    content_type: object,
    request_level: object,
    policy_mode: object,
    book_data: object,
    release_data: object = None,
    note: object = None,
    max_pending_per_user: int | None = None,
) -> dict[str, Any]:
    """Create a pending request after service-level validation."""
    prepared_request = _prepare_request_create(
        user_id=user_id,
        source_hint=source_hint,
        content_type=content_type,
        request_level=request_level,
        policy_mode=policy_mode,
        book_data=book_data,
        release_data=release_data,
        note=note,
    )

    if max_pending_per_user is not None:
        pending_count = user_db.count_user_pending_requests(user_id)
        if pending_count >= max_pending_per_user:
            msg = "Maximum pending requests reached for this user"
            raise RequestServiceError(
                msg,
                status_code=409,
                code="max_pending_reached",
            )

    duplicate = _find_duplicate_pending_request(
        user_db,
        user_id=user_id,
        title=_normalize_match_text(prepared_request["book_data"].get("title")),
        author=_normalize_match_text(prepared_request["book_data"].get("author")),
        content_type=prepared_request["content_type"],
    )
    if duplicate is not None:
        msg = "Duplicate pending request exists for this title/author/content_type"
        raise RequestServiceError(
            msg,
            status_code=409,
            code="duplicate_pending_request",
        )

    try:
        return user_db.create_request(**prepared_request)
    except (ValueError, TypeError) as exc:
        raise RequestServiceError(str(exc), status_code=400) from exc


def create_requests(
    user_db: UserDB,
    *,
    requests: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Create multiple pending requests atomically after validation."""
    if not isinstance(requests, list) or len(requests) == 0:
        msg = "requests must contain at least one request"
        raise RequestServiceError(msg, status_code=400)

    prepared_requests: list[dict[str, Any]] = []
    pending_counts_by_user: dict[int, int] = {}
    seen_request_keys: set[tuple[int, str, str, str]] = set()

    for request in requests:
        if not isinstance(request, dict):
            msg = "requests must contain objects"
            raise RequestServiceError(msg, status_code=400)

        user_id = int(request["user_id"])
        prepared_request = _prepare_request_create(
            user_id=user_id,
            source_hint=request.get("source_hint"),
            content_type=request.get("content_type"),
            request_level=request.get("request_level"),
            policy_mode=request.get("policy_mode"),
            book_data=request.get("book_data"),
            release_data=request.get("release_data"),
            note=request.get("note"),
        )

        request_key = (
            user_id,
            _normalize_match_text(prepared_request["book_data"].get("title")),
            _normalize_match_text(prepared_request["book_data"].get("author")),
            prepared_request["content_type"],
        )
        if request_key in seen_request_keys:
            msg = "Duplicate pending request exists for this title/author/content_type"
            raise RequestServiceError(
                msg,
                status_code=409,
                code="duplicate_pending_request",
            )
        seen_request_keys.add(request_key)

        max_pending_per_user = request.get("max_pending_per_user")
        if max_pending_per_user is not None:
            existing_pending = pending_counts_by_user.get(user_id)
            if existing_pending is None:
                existing_pending = user_db.count_user_pending_requests(user_id)
            if existing_pending >= max_pending_per_user:
                msg = "Maximum pending requests reached for this user"
                raise RequestServiceError(
                    msg,
                    status_code=409,
                    code="max_pending_reached",
                )
            pending_counts_by_user[user_id] = existing_pending + 1

        duplicate = _find_duplicate_pending_request(
            user_db,
            user_id=user_id,
            title=request_key[1],
            author=request_key[2],
            content_type=request_key[3],
        )
        if duplicate is not None:
            msg = "Duplicate pending request exists for this title/author/content_type"
            raise RequestServiceError(
                msg,
                status_code=409,
                code="duplicate_pending_request",
            )

        prepared_requests.append(prepared_request)

    try:
        return user_db.create_requests(prepared_requests)
    except ValueError as exc:
        raise RequestServiceError(str(exc), status_code=400) from exc


def ensure_request_access(
    user_db: UserDB,
    *,
    request_id: int,
    actor_user_id: int | None,
    is_admin: bool,
) -> dict[str, Any]:
    """Get request by ID and enforce ownership for non-admin actors."""
    request_row = user_db.get_request(request_id)
    if request_row is None:
        msg = "Request not found"
        raise RequestServiceError(msg, status_code=404)

    if not is_admin and (actor_user_id is None or request_row["user_id"] != actor_user_id):
        msg = "Forbidden"
        raise RequestServiceError(msg, status_code=403)

    return request_row


def _require_pending(request_row: dict[str, Any]) -> None:
    if request_row["status"] != RequestStatus.PENDING:
        msg = "Request is already in a terminal state"
        raise RequestServiceError(
            msg,
            status_code=409,
            code="stale_transition",
        )


def cancel_request(
    user_db: UserDB,
    *,
    request_id: int,
    actor_user_id: int,
) -> dict[str, Any]:
    """Cancel a pending request owned by the actor."""
    request_row = ensure_request_access(
        user_db,
        request_id=request_id,
        actor_user_id=actor_user_id,
        is_admin=False,
    )
    _require_pending(request_row)

    try:
        return user_db.update_request(
            request_id,
            expected_current_status=RequestStatus.PENDING,
            status=RequestStatus.CANCELLED,
        )
    except TypeError as exc:
        raise RequestServiceError(str(exc), status_code=400) from exc
    except ValueError as exc:
        raise RequestServiceError(str(exc), status_code=409, code="stale_transition") from exc


def reject_request(
    user_db: UserDB,
    *,
    request_id: int,
    admin_user_id: int,
    admin_note: object = None,
) -> dict[str, Any]:
    """Reject a pending request as admin."""
    request_row = ensure_request_access(
        user_db,
        request_id=request_id,
        actor_user_id=admin_user_id,
        is_admin=True,
    )
    _require_pending(request_row)

    normalized_admin_note = _normalize_admin_note(admin_note)

    try:
        return user_db.update_request(
            request_id,
            expected_current_status=RequestStatus.PENDING,
            status=RequestStatus.REJECTED,
            admin_note=normalized_admin_note,
            reviewed_by=admin_user_id,
            reviewed_at=_now_timestamp(),
        )
    except TypeError as exc:
        raise RequestServiceError(str(exc), status_code=400) from exc
    except ValueError as exc:
        raise RequestServiceError(str(exc), status_code=409, code="stale_transition") from exc


def reopen_request(
    user_db: UserDB,
    *,
    request_id: int,
    admin_user_id: int,
    admin_note: object = None,
) -> dict[str, Any]:
    """Move a rejected request back to pending as admin, so the rejection can be undone."""
    request_row = ensure_request_access(
        user_db,
        request_id=request_id,
        actor_user_id=admin_user_id,
        is_admin=True,
    )
    if request_row["status"] != RequestStatus.REJECTED:
        msg = "Only a rejected request can be reopened"
        raise RequestServiceError(msg, status_code=409, code="stale_transition")

    normalized_admin_note = _normalize_admin_note(admin_note)

    # The user may have asked again after the rejection; two pending requests for the
    # same book would both be worked.
    book_data = request_row.get("book_data") or {}
    if isinstance(book_data, dict):
        duplicate = _find_duplicate_pending_request(
            user_db,
            user_id=request_row["user_id"],
            title=_normalize_match_text(book_data.get("title")),
            author=_normalize_match_text(book_data.get("author")),
            content_type=normalize_content_type(
                request_row.get("content_type") or book_data.get("content_type")
            ),
        )
        if duplicate is not None:
            msg = "Duplicate pending request exists for this title/author/content_type"
            raise RequestServiceError(msg, status_code=409, code="duplicate_pending_request")

    reopened = user_db.reopen_rejected_request(request_id, admin_note=normalized_admin_note)
    if reopened is None:
        msg = "Request state changed before update"
        raise RequestServiceError(msg, status_code=409, code="stale_transition")
    return reopened


def fulfil_request(
    user_db: UserDB,
    *,
    request_id: int,
    admin_user_id: int,
    queue_release: Callable[..., tuple[bool, str | None]],
    release_data: object = None,
    admin_note: object = None,
    manual_approval: object = False,
) -> dict[str, Any]:
    """Fulfil a pending request and queue the release under requesting-user identity."""
    request_row = ensure_request_access(
        user_db,
        request_id=request_id,
        actor_user_id=admin_user_id,
        is_admin=True,
    )
    _require_pending(request_row)

    normalized_admin_note = _normalize_admin_note(admin_note)

    if not isinstance(manual_approval, bool):
        msg = "manual_approval must be a boolean"
        raise RequestServiceError(msg, status_code=400)

    selected_release_data = (
        release_data if release_data is not None else request_row.get("release_data")
    )
    if selected_release_data is not None and not isinstance(selected_release_data, dict):
        msg = "release_data must be an object"
        raise RequestServiceError(msg, status_code=400)

    if selected_release_data is None and manual_approval:
        try:
            return user_db.update_request(
                request_id,
                expected_current_status=RequestStatus.PENDING,
                status=RequestStatus.FULFILLED,
                release_data=None,
                delivery_state=QueueStatus.COMPLETE,
                delivery_updated_at=_now_timestamp(),
                last_failure_reason=None,
                admin_note=normalized_admin_note,
                reviewed_by=admin_user_id,
                reviewed_at=_now_timestamp(),
            )
        except TypeError as exc:
            raise RequestServiceError(str(exc), status_code=400) from exc
        except ValueError as exc:
            raise RequestServiceError(str(exc), status_code=409, code="stale_transition") from exc

    if selected_release_data is None:
        msg = "release_data is required to fulfil requests"
        raise RequestServiceError(
            msg,
            status_code=400,
        )

    _validate_json_blob_size("release_data", selected_release_data)

    requester = user_db.get_user(user_id=request_row["user_id"])
    if requester is None:
        msg = "Requesting user not found"
        raise RequestServiceError(msg, status_code=404)

    original_release_data = request_row.get("release_data")
    try:
        claimed_request = user_db.update_request(
            request_id,
            expected_current_status=RequestStatus.PENDING,
            status=RequestStatus.FULFILLED,
            release_data=selected_release_data,
            delivery_state=QueueStatus.QUEUED,
            delivery_updated_at=_now_timestamp(),
            last_failure_reason=None,
            admin_note=normalized_admin_note,
            reviewed_by=admin_user_id,
            reviewed_at=_now_timestamp(),
        )
    except TypeError as exc:
        raise RequestServiceError(str(exc), status_code=400) from exc
    except ValueError as exc:
        raise RequestServiceError(str(exc), status_code=409, code="stale_transition") from exc

    queued_release_data = dict(selected_release_data)
    queued_release_data["_request_id"] = request_id

    try:
        success, error = queue_release(
            queued_release_data,
            0,
            user_id=request_row["user_id"],
            username=requester.get("username"),
        )
    except Exception:
        user_db.rollback_request_fulfilment(
            request_id,
            release_data=original_release_data,
            last_failure_reason="Queue dispatch raised an exception",
        )
        raise
    if not success:
        user_db.rollback_request_fulfilment(
            request_id,
            release_data=original_release_data,
            last_failure_reason=error,
        )
        raise RequestServiceError(
            error or "Failed to queue release",
            status_code=409,
            code="queue_failed",
        )

    return claimed_request


def reopen_failed_request(
    user_db: UserDB,
    *,
    request_id: int,
    failure_reason: str | None = None,
) -> dict[str, Any] | None:
    """Reopen a failed fulfilled request so admins can re-approve with a new release."""
    return user_db.reopen_failed_request(
        request_id,
        failure_reason=failure_reason,
    )
