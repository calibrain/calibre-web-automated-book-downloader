"""Request API routes and policy snapshot endpoint."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from flask import Flask, Response, jsonify, request, session

from shelfmark.core.logger import setup_logger
from shelfmark.core.notifications import (
    NotificationContext,
    NotificationEvent,
    notify_admin,
    notify_user,
)
from shelfmark.core.request_helpers import (
    coerce_bool,
    coerce_int,
    emit_ws_event,
    load_users_request_policy_settings,
    normalize_optional_text,
    normalize_positive_int,
    populate_request_usernames,
)
from shelfmark.core.request_policy import (
    REQUEST_POLICY_DEFAULT_FALLBACK_MODE,
    PolicyMode,
    get_source_content_type_capabilities,
    merge_request_policy_settings,
    normalize_content_type,
    normalize_source,
    parse_policy_mode,
    resolve_policy_mode,
)
from shelfmark.core.request_validation import RequestStatus
from shelfmark.core.requests_service import (
    RequestServiceError,
    cancel_request,
    create_request,
    create_requests,
    fulfil_request,
    reject_request,
    reopen_request,
)
from shelfmark.core.viewer_redaction import redact_request_row

if TYPE_CHECKING:
    from collections.abc import Callable

    from flask.typing import ResponseReturnValue

    from shelfmark.core.user_db import UserDB

logger = setup_logger(__name__)
_NOTIFICATION_TRIGGER_ERRORS = (RuntimeError, TypeError, ValueError)


def _error_response(
    message: str,
    status_code: int,
    *,
    code: str | None = None,
    required_mode: str | None = None,
) -> tuple[Response, int]:
    payload: dict[str, Any] = {"error": message}
    if code is not None:
        payload["code"] = code
    if required_mode is not None:
        payload["required_mode"] = required_mode
    return jsonify(payload), status_code


def _require_request_endpoints_available(
    resolve_auth_mode: Callable[[], str],
) -> tuple[Response, int] | None:
    auth_mode = resolve_auth_mode()
    if auth_mode == "none":
        return _error_response(
            "Request workflow is unavailable in no-auth mode",
            403,
            code="requests_unavailable",
        )
    if "user_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401
    return None


def _require_db_user_id() -> tuple[int | None, ResponseReturnValue | None]:
    """Return the logged-in DB user id or a ready-made error response."""
    raw_user_id = session.get("db_user_id")
    if raw_user_id is None:
        return None, _error_response(
            "User identity is unavailable for request workflow",
            403,
            code="user_identity_unavailable",
        )
    normalized_user_id = normalize_positive_int(raw_user_id)
    if normalized_user_id is None:
        return None, _error_response(
            "User identity is unavailable for request workflow",
            403,
            code="user_identity_unavailable",
        )
    return normalized_user_id, None


def _require_admin_user_id() -> tuple[int | None, ResponseReturnValue | None]:
    if not session.get("is_admin", False):
        return None, (jsonify({"error": "Admin access required"}), 403)
    raw_admin_id = session.get("db_user_id")
    if raw_admin_id is None:
        return None, (jsonify({"error": "Admin user identity unavailable"}), 403)
    normalized_admin_user_id = normalize_positive_int(raw_admin_id)
    if normalized_admin_user_id is None:
        return None, (jsonify({"error": "Admin user identity unavailable"}), 403)
    return normalized_admin_user_id, None


def _resolve_effective_policy(
    user_db: UserDB,
    *,
    db_user_id: int | None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], bool]:
    global_settings = load_users_request_policy_settings()
    user_settings = user_db.get_user_settings(db_user_id) if db_user_id is not None else {}
    effective = merge_request_policy_settings(global_settings, user_settings)
    requests_enabled = coerce_bool(effective.get("REQUESTS_ENABLED"), default=False)
    return global_settings, user_settings, effective, requests_enabled


def _resolve_title_from_book_data(book_data: object) -> str:
    if isinstance(book_data, dict):
        title = normalize_optional_text(book_data.get("title"))
        if title is not None:
            return title
    return "Unknown title"


def _normalize_optional_source_id(value: object) -> str | None:
    """Normalize source identifiers while allowing integer provider ids."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        value = str(value)
    return normalize_optional_text(value)


def _build_release_result_data_from_book_data(
    *,
    source: str,
    book_data: dict[str, Any],
    content_type: str,
) -> dict[str, Any]:
    """Build release-level payload fields for sources whose browse results are releases."""
    source_id = _normalize_optional_source_id(
        book_data.get("provider_id")
    ) or _normalize_optional_source_id(book_data.get("id"))
    payload: dict[str, Any] = {
        "source": source,
        "source_id": source_id,
        "title": book_data.get("title"),
        "author": book_data.get("author"),
        "year": book_data.get("year"),
        "format": book_data.get("format"),
        "size": book_data.get("size"),
        "preview": book_data.get("preview"),
        "content_type": content_type,
        "source_url": book_data.get("source_url"),
        "search_mode": "direct",
    }
    return {key: value for key, value in payload.items() if value is not None}


def _source_results_are_releases(source: str) -> bool:
    normalized_source = normalize_source(source)
    if normalized_source in {"", "*"}:
        return False
    from shelfmark.release_sources import source_results_are_releases

    return source_results_are_releases(normalized_source)


def _normalize_release_result_request_payload(
    *,
    source: str,
    request_level: object,
    book_data: object,
    release_data: object,
    content_type: str,
) -> tuple[Any, Any]:
    """Concrete-release browse results are always handled as release-level requests."""
    if not _source_results_are_releases(source):
        return request_level, release_data

    normalized_release_data = release_data
    if normalized_release_data is None and isinstance(book_data, dict):
        normalized_release_data = _build_release_result_data_from_book_data(
            source=source,
            book_data=book_data,
            content_type=content_type,
        )
    elif isinstance(normalized_release_data, dict):
        normalized_release_data = dict(normalized_release_data)

    if isinstance(normalized_release_data, dict):
        normalized_release_data["source"] = source
        if normalized_release_data.get("content_type") is None:
            normalized_release_data["content_type"] = content_type

        normalized_source_id = _normalize_optional_source_id(
            normalized_release_data.get("source_id")
        )
        if normalized_source_id is not None:
            normalized_release_data["source_id"] = normalized_source_id
        elif isinstance(book_data, dict):
            fallback_source_id = _normalize_optional_source_id(
                book_data.get("provider_id")
            ) or _normalize_optional_source_id(book_data.get("id"))
            if fallback_source_id is not None:
                normalized_release_data["source_id"] = fallback_source_id

    return "release", normalized_release_data


def _validate_release_source_matches_policy_context(
    *,
    source: str,
    release_data: object,
) -> None:
    if not isinstance(release_data, dict):
        return

    release_source = normalize_source(release_data.get("source"))
    if release_source in {"", "*"} or release_source == source:
        return

    msg = "Policy context source must match release_data.source"
    raise RequestServiceError(
        msg,
        status_code=400,
        code="policy_source_mismatch",
    )


def _resolve_request_title(request_row: dict[str, Any]) -> str:
    return _resolve_title_from_book_data(request_row.get("book_data"))


def _format_user_label(username: str | None, user_id: int | None = None) -> str:
    normalized_username = normalize_optional_text(username)
    if normalized_username is not None:
        return normalized_username
    if user_id is not None and user_id > 0:
        return f"user#{user_id}"
    return "unknown user"


def _format_requester_label(user_db: UserDB, request_row: dict[str, Any]) -> str:
    """Resolve a display label for the user who created a request."""
    user_id = normalize_positive_int(request_row.get("user_id"))
    if user_id is not None:
        requester = user_db.get_user(user_id=user_id)
        if isinstance(requester, dict):
            username = normalize_optional_text(requester.get("username"))
            if username is not None:
                return username
    return _format_user_label(None, user_id)


def _resolve_request_user_context(
    user_db: UserDB,
    *,
    actor_user_id: int,
    actor_username: str | None,
    on_behalf_of_user_id: object,
) -> tuple[int, str | None, str]:
    if on_behalf_of_user_id in (None, ""):
        actor_label = _format_user_label(actor_username, actor_user_id)
        return actor_user_id, actor_username, actor_label

    if not session.get("is_admin", False):
        msg = "Admin required"
        raise RequestServiceError(msg, status_code=403)

    target_user_id = normalize_positive_int(on_behalf_of_user_id)
    if target_user_id is None:
        msg = "Invalid on_behalf_of_user_id"
        raise RequestServiceError(msg, status_code=400)

    target_user = user_db.get_user(user_id=target_user_id)
    if not target_user:
        msg = "User not found"
        raise RequestServiceError(msg, status_code=404)

    target_username = normalize_optional_text(target_user.get("username"))
    actor_label = _format_user_label(actor_username, actor_user_id)
    target_label = _format_user_label(target_username, target_user_id)
    return target_user_id, target_username, f"{actor_label} on behalf of {target_label}"


def _prepare_request_create_arguments(
    user_db: UserDB,
    data: dict[str, Any],
) -> dict[str, Any]:
    db_user_id, db_gate = _require_db_user_id()
    if db_gate is not None or db_user_id is None:
        msg = "User identity is unavailable for request workflow"
        raise RequestServiceError(
            msg,
            status_code=403,
            code="user_identity_unavailable",
        )

    actor_username = normalize_optional_text(session.get("user_id"))
    target_user_id, _, actor_label = _resolve_request_user_context(
        user_db,
        actor_user_id=db_user_id,
        actor_username=actor_username,
        on_behalf_of_user_id=data.get("on_behalf_of_user_id"),
    )

    context = data.get("context") or {}
    if not isinstance(context, dict):
        msg = "context must be an object"
        raise RequestServiceError(msg, status_code=400)

    source = normalize_source(context.get("source"))
    release_data = data.get("release_data")
    request_level = context.get("request_level")
    if request_level is None:
        request_level = "book" if release_data is None else "release"

    book_data = data.get("book_data")
    if not isinstance(book_data, dict):
        msg = "book_data must be an object"
        raise RequestServiceError(msg, status_code=400)
    request_title = _resolve_title_from_book_data(book_data)

    content_type = normalize_content_type(
        context.get("content_type") or data.get("content_type") or book_data.get("content_type")
    )
    _validate_release_source_matches_policy_context(
        source=source,
        release_data=release_data,
    )
    request_level, release_data = _normalize_release_result_request_payload(
        source=source,
        request_level=request_level,
        book_data=book_data,
        release_data=release_data,
        content_type=content_type,
    )
    _validate_release_source_matches_policy_context(
        source=source,
        release_data=release_data,
    )

    global_settings, user_settings, effective, requests_enabled = _resolve_effective_policy(
        user_db,
        db_user_id=target_user_id,
    )
    if not requests_enabled:
        msg = "Request workflow is disabled by policy"
        raise RequestServiceError(
            msg,
            status_code=403,
            code="requests_unavailable",
        )

    max_pending = coerce_int(
        effective.get("MAX_PENDING_REQUESTS_PER_USER"),
        default=20,
    )
    max_pending = max(max_pending, 1)
    max_pending = min(max_pending, 1000)
    allow_notes = coerce_bool(effective.get("REQUESTS_ALLOW_NOTES"), default=True)
    note_value = data.get("note") if allow_notes else None

    resolved_mode = resolve_policy_mode(
        source=source,
        content_type=content_type,
        global_settings=global_settings,
        user_settings=user_settings,
    )
    logger.debug(
        "request create policy actor=%s target_user_id=%s source=%s content_type=%s request_level=%s resolved_mode=%s",
        actor_label,
        target_user_id,
        source,
        content_type,
        request_level,
        resolved_mode.value,
    )

    if resolved_mode == PolicyMode.BLOCKED:
        msg = "Requesting is blocked by policy"
        raise RequestServiceError(
            msg,
            status_code=403,
            code="policy_blocked",
            required_mode=PolicyMode.BLOCKED.value,
        )

    requested_level = str(request_level).strip().lower() if isinstance(request_level, str) else ""
    if resolved_mode == PolicyMode.REQUEST_BOOK and requested_level != "book":
        msg = "Policy requires book-level requests"
        raise RequestServiceError(
            msg,
            status_code=403,
            code="policy_requires_request",
            required_mode=PolicyMode.REQUEST_BOOK.value,
        )

    return {
        "create_args": {
            "user_id": target_user_id,
            "source_hint": source,
            "content_type": content_type,
            "request_level": request_level,
            "policy_mode": resolved_mode.value,
            "book_data": book_data,
            "release_data": release_data,
            "note": note_value,
            "max_pending_per_user": max_pending,
        },
        "actor_label": actor_label,
        "request_title": request_title,
        "resolved_mode": resolved_mode,
    }


def _resolve_request_source_and_format(
    request_row: dict[str, Any],
) -> tuple[str, str | None]:
    release_data = request_row.get("release_data")
    if isinstance(release_data, dict):
        source = normalize_source(release_data.get("source") or request_row.get("source_hint"))
        release_format = normalize_optional_text(
            release_data.get("format")
            or release_data.get("filetype")
            or release_data.get("extension")
        )
        return source, release_format
    return normalize_source(request_row.get("source_hint")), None


def _build_queued_download_result(
    *,
    create_args: dict[str, Any],
    request_title: str,
) -> dict[str, Any]:
    release_data = create_args.get("release_data")
    source = create_args.get("source_hint")
    source_id: str | None = None

    if isinstance(release_data, dict):
        source = release_data.get("source") or source
        source_id = _normalize_optional_source_id(release_data.get("source_id"))

    return {
        "kind": "download",
        "status": "queued",
        "priority": 0,
        "title": request_title,
        "source": normalize_source(source),
        "source_id": source_id,
        "content_type": create_args.get("content_type"),
    }


def _queue_prepared_download_submission(
    user_db: UserDB,
    *,
    queue_release: Callable[..., tuple[bool, str | None]],
    create_args: dict[str, Any],
    request_title: str,
) -> dict[str, Any]:
    release_data = create_args.get("release_data")
    if not isinstance(release_data, dict):
        msg = "Download policy requires a concrete release"
        raise RequestServiceError(
            msg,
            status_code=400,
            code="policy_requires_download",
            required_mode=PolicyMode.DOWNLOAD.value,
        )

    requester = user_db.get_user(user_id=create_args["user_id"])
    if requester is None:
        msg = "Requesting user not found"
        raise RequestServiceError(msg, status_code=404)

    success, error = queue_release(
        dict(release_data),
        0,
        user_id=create_args["user_id"],
        username=requester.get("username"),
    )
    if not success:
        raise RequestServiceError(
            error or "Failed to queue release",
            status_code=409,
            code="queue_failed",
        )

    return _build_queued_download_result(
        create_args=create_args,
        request_title=request_title,
    )


def _notify_admin_for_request_event(
    user_db: UserDB,
    *,
    event: NotificationEvent,
    request_row: dict[str, Any],
) -> None:
    book_data = request_row.get("book_data")
    if not isinstance(book_data, dict):
        book_data = {}

    source, release_format = _resolve_request_source_and_format(request_row)
    context = NotificationContext(
        event=event,
        title=str(book_data.get("title") or "Unknown title"),
        author=str(book_data.get("author") or "Unknown author"),
        username=_format_requester_label(user_db, request_row),
        content_type=normalize_content_type(
            request_row.get("content_type") or book_data.get("content_type")
        ),
        format=release_format,
        source=source,
        admin_note=normalize_optional_text(request_row.get("admin_note")),
        error_message=None,
    )

    owner_user_id = normalize_positive_int(request_row.get("user_id"))
    try:
        notify_admin(event, context)
    except _NOTIFICATION_TRIGGER_ERRORS as exc:
        logger.warning(
            "Failed to trigger admin notification for request event '%s': %s",
            event.value,
            exc,
        )
    if owner_user_id is None:
        return
    try:
        notify_user(owner_user_id, event, context)
    except _NOTIFICATION_TRIGGER_ERRORS as exc:
        logger.warning(
            "Failed to trigger user notification for request event '%s' (user_id=%s): %s",
            event.value,
            owner_user_id,
            exc,
        )


def register_request_routes(
    app: Flask,
    user_db: UserDB,
    *,
    resolve_auth_mode: Callable[[], str],
    queue_release: Callable[..., tuple[bool, str | None]],
    ws_manager: object | None = None,
) -> None:
    """Register request policy and request lifecycle routes."""

    @app.route("/api/request-policy", methods=["GET"])
    def api_request_policy() -> ResponseReturnValue:
        auth_gate = _require_request_endpoints_available(resolve_auth_mode)
        if auth_gate is not None:
            return auth_gate

        is_admin = bool(session.get("is_admin", False))
        db_user_id: int | None = None
        if not is_admin:
            db_user_id, db_gate = _require_db_user_id()
            if db_gate is not None:
                return db_gate
        else:
            db_user_id = normalize_positive_int(session.get("db_user_id"))

        global_settings, user_settings, effective, requests_enabled = _resolve_effective_policy(
            user_db,
            db_user_id=db_user_id,
        )

        default_ebook_mode = parse_policy_mode(effective.get("REQUEST_POLICY_DEFAULT_EBOOK"))
        default_audio_mode = parse_policy_mode(effective.get("REQUEST_POLICY_DEFAULT_AUDIOBOOK"))

        source_capabilities = get_source_content_type_capabilities()
        from shelfmark.release_sources import source_results_are_releases

        source_modes = []
        for source_name in sorted(source_capabilities):
            supported_types = sorted(
                source_capabilities[source_name],
                key=lambda ct: (ct != "ebook", ct),
            )
            modes = {
                content_type: resolve_policy_mode(
                    source=source_name,
                    content_type=content_type,
                    global_settings=global_settings,
                    user_settings=user_settings,
                ).value
                for content_type in supported_types
            }
            source_modes.append(
                {
                    "source": source_name,
                    "supported_content_types": supported_types,
                    "browse_results_are_releases": source_results_are_releases(source_name),
                    "modes": modes,
                }
            )

        return jsonify(
            {
                "requests_enabled": requests_enabled,
                "is_admin": is_admin,
                "allow_notes": coerce_bool(effective.get("REQUESTS_ALLOW_NOTES"), default=True),
                "defaults": {
                    "ebook": (
                        default_ebook_mode.value
                        if default_ebook_mode is not None
                        else REQUEST_POLICY_DEFAULT_FALLBACK_MODE.value
                    ),
                    "audiobook": (
                        default_audio_mode.value
                        if default_audio_mode is not None
                        else REQUEST_POLICY_DEFAULT_FALLBACK_MODE.value
                    ),
                },
                "rules": effective.get("REQUEST_POLICY_RULES", []),
                "source_modes": source_modes,
            }
        )

    @app.route("/api/requests", methods=["POST"])
    def api_create_request() -> ResponseReturnValue:
        auth_gate = _require_request_endpoints_available(resolve_auth_mode)
        if auth_gate is not None:
            return auth_gate

        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"error": "No data provided"}), 400

        try:
            prepared = _prepare_request_create_arguments(user_db, data)
            if prepared["resolved_mode"] == PolicyMode.DOWNLOAD:
                queued = _queue_prepared_download_submission(
                    user_db,
                    queue_release=queue_release,
                    create_args=prepared["create_args"],
                    request_title=prepared["request_title"],
                )
                logger.info(
                    "Policy download queued for '%s' by %s",
                    prepared["request_title"],
                    prepared["actor_label"],
                )
                return jsonify(queued), 200
            created = create_request(user_db, **prepared["create_args"])
        except RequestServiceError as exc:
            return _error_response(
                str(exc),
                exc.status_code,
                code=exc.code,
                required_mode=exc.required_mode,
            )

        event_payload = {
            "request_id": created["id"],
            "status": created["status"],
            "title": _resolve_request_title(created),
        }
        logger.info(
            "Request created #%s for '%s' by %s",
            created["id"],
            event_payload["title"],
            prepared["actor_label"],
        )
        emit_ws_event(
            ws_manager,
            event_name="new_request",
            payload=event_payload,
            room="admins",
        )
        emit_ws_event(
            ws_manager,
            event_name="request_update",
            payload=event_payload,
            room=f"user_{created['user_id']}",
        )

        _notify_admin_for_request_event(
            user_db,
            event=NotificationEvent.REQUEST_CREATED,
            request_row=created,
        )

        return jsonify(created), 201

    @app.route("/api/requests/batch", methods=["POST"])
    def api_create_requests_batch() -> ResponseReturnValue:
        auth_gate = _require_request_endpoints_available(resolve_auth_mode)
        if auth_gate is not None:
            return auth_gate

        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"error": "No data provided"}), 400

        raw_requests = data.get("requests")
        if not isinstance(raw_requests, list) or len(raw_requests) == 0:
            return jsonify({"error": "requests must contain at least one request"}), 400
        if not all(isinstance(raw_request, dict) for raw_request in raw_requests):
            return jsonify({"error": "requests must contain objects"}), 400

        try:
            prepared_requests = [
                _prepare_request_create_arguments(user_db, raw_request)
                for raw_request in raw_requests
            ]
        except RequestServiceError as exc:
            return _error_response(
                str(exc),
                exc.status_code,
                code=exc.code,
                required_mode=exc.required_mode,
            )

        request_prepared_items: list[tuple[int, dict[str, Any]]] = []
        download_prepared_items: list[tuple[int, dict[str, Any]]] = []

        for index, prepared in enumerate(prepared_requests):
            if prepared["resolved_mode"] == PolicyMode.DOWNLOAD:
                download_prepared_items.append((index, prepared))
                continue

            request_prepared_items.append((index, prepared))

        created_rows: list[dict[str, Any]] = []
        if request_prepared_items:
            try:
                created_rows = create_requests(
                    user_db,
                    requests=[prepared["create_args"] for _, prepared in request_prepared_items],
                )
            except RequestServiceError as exc:
                return _error_response(
                    str(exc),
                    exc.status_code,
                    code=exc.code,
                    required_mode=exc.required_mode,
                )

        results_by_index: dict[int, dict[str, Any]] = {}

        for (index, prepared), created in zip(
            request_prepared_items,
            created_rows,
            strict=True,
        ):
            event_payload = {
                "request_id": created["id"],
                "status": created["status"],
                "title": _resolve_request_title(created),
            }
            logger.info(
                "Request created #%s for '%s' by %s",
                created["id"],
                event_payload["title"],
                prepared["actor_label"],
            )
            emit_ws_event(
                ws_manager,
                event_name="new_request",
                payload=event_payload,
                room="admins",
            )
            emit_ws_event(
                ws_manager,
                event_name="request_update",
                payload=event_payload,
                room=f"user_{created['user_id']}",
            )
            _notify_admin_for_request_event(
                user_db,
                event=NotificationEvent.REQUEST_CREATED,
                request_row=created,
            )
            results_by_index[index] = created

        for index, prepared in download_prepared_items:
            try:
                results_by_index[index] = _queue_prepared_download_submission(
                    user_db,
                    queue_release=queue_release,
                    create_args=prepared["create_args"],
                    request_title=prepared["request_title"],
                )
            except RequestServiceError as exc:
                return _error_response(
                    str(exc),
                    exc.status_code,
                    code=exc.code,
                    required_mode=exc.required_mode,
                )
            logger.info(
                "Policy download queued for '%s' by %s",
                prepared["request_title"],
                prepared["actor_label"],
            )

        ordered_results = [results_by_index[index] for index in range(len(prepared_requests))]
        status_code = 201 if request_prepared_items else 200
        return jsonify(ordered_results), status_code

    @app.route("/api/requests", methods=["GET"])
    def api_list_requests() -> ResponseReturnValue:
        auth_gate = _require_request_endpoints_available(resolve_auth_mode)
        if auth_gate is not None:
            return auth_gate

        db_user_id, db_gate = _require_db_user_id()
        if db_gate is not None:
            return db_gate
        if db_user_id is None:
            return _error_response(
                "User identity is unavailable for request workflow",
                403,
                code="user_identity_unavailable",
            )

        status = request.args.get("status")
        limit = request.args.get("limit", type=int)
        offset = request.args.get("offset", type=int, default=0) or 0

        try:
            rows = user_db.list_requests(
                user_id=db_user_id,
                status=status,
                limit=limit,
                offset=offset,
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        if not session.get("is_admin", False):
            # A fulfilled request carries the release an admin picked (#1418).
            rows = [redact_request_row(row) for row in rows]
        return jsonify(rows)

    @app.route("/api/requests/<int:request_id>", methods=["DELETE"])
    def api_cancel_request(request_id: int) -> ResponseReturnValue:
        auth_gate = _require_request_endpoints_available(resolve_auth_mode)
        if auth_gate is not None:
            return auth_gate

        db_user_id, db_gate = _require_db_user_id()
        if db_gate is not None:
            return db_gate
        if db_user_id is None:
            return _error_response(
                "User identity is unavailable for request workflow",
                403,
                code="user_identity_unavailable",
            )

        try:
            updated = cancel_request(
                user_db,
                request_id=request_id,
                actor_user_id=db_user_id,
            )
        except RequestServiceError as exc:
            return _error_response(str(exc), exc.status_code, code=exc.code)

        event_payload = {
            "request_id": updated["id"],
            "status": updated["status"],
            "title": _resolve_request_title(updated),
        }
        actor_label = _format_user_label(
            normalize_optional_text(session.get("user_id")), db_user_id
        )
        logger.info(
            "Request cancelled #%s for '%s' by %s",
            updated["id"],
            event_payload["title"],
            actor_label,
        )
        emit_ws_event(
            ws_manager,
            event_name="request_update",
            payload=event_payload,
            room=f"user_{db_user_id}",
        )
        emit_ws_event(
            ws_manager,
            event_name="request_update",
            payload=event_payload,
            room="admins",
        )

        if not session.get("is_admin", False):
            updated = redact_request_row(updated)
        return jsonify(updated)

    @app.route("/api/admin/requests", methods=["GET"])
    def api_admin_list_requests() -> ResponseReturnValue:
        auth_gate = _require_request_endpoints_available(resolve_auth_mode)
        if auth_gate is not None:
            return auth_gate
        if not session.get("is_admin", False):
            return jsonify({"error": "Admin access required"}), 403

        status = request.args.get("status")
        limit = request.args.get("limit", type=int)
        offset = request.args.get("offset", type=int, default=0) or 0

        try:
            rows = user_db.list_requests(status=status, limit=limit, offset=offset)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        populate_request_usernames(rows, user_db)

        return jsonify(rows)

    @app.route("/api/admin/requests/count", methods=["GET"])
    def api_admin_request_counts() -> ResponseReturnValue:
        auth_gate = _require_request_endpoints_available(resolve_auth_mode)
        if auth_gate is not None:
            return auth_gate
        if not session.get("is_admin", False):
            return jsonify({"error": "Admin access required"}), 403

        by_status = {status: len(user_db.list_requests(status=status)) for status in RequestStatus}
        return jsonify(
            {
                "pending": by_status[RequestStatus.PENDING],
                "total": sum(by_status.values()),
                "by_status": by_status,
            }
        )

    @app.route("/api/admin/requests/<int:request_id>/fulfil", methods=["POST"])
    def api_admin_fulfil_request(request_id: int) -> ResponseReturnValue:
        auth_gate = _require_request_endpoints_available(resolve_auth_mode)
        if auth_gate is not None:
            return auth_gate

        admin_user_id, admin_gate = _require_admin_user_id()
        if admin_gate is not None:
            return admin_gate
        if admin_user_id is None:
            return jsonify({"error": "Admin user identity unavailable"}), 403

        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            return jsonify({"error": "Invalid payload"}), 400

        try:
            updated = fulfil_request(
                user_db,
                request_id=request_id,
                admin_user_id=admin_user_id,
                queue_release=queue_release,
                release_data=data.get("release_data"),
                admin_note=data.get("admin_note"),
                manual_approval=data.get("manual_approval", False),
            )
        except RequestServiceError as exc:
            return _error_response(str(exc), exc.status_code, code=exc.code)

        event_payload = {
            "request_id": updated["id"],
            "status": updated["status"],
            "title": _resolve_request_title(updated),
        }
        admin_label = _format_user_label(
            normalize_optional_text(session.get("user_id")), admin_user_id
        )
        requester_label = _format_requester_label(user_db, updated)
        logger.info(
            "Request fulfilled #%s for '%s' by %s (requested by %s)",
            updated["id"],
            event_payload["title"],
            admin_label,
            requester_label,
        )
        emit_ws_event(
            ws_manager,
            event_name="request_update",
            payload=event_payload,
            room=f"user_{updated['user_id']}",
        )
        emit_ws_event(
            ws_manager,
            event_name="request_update",
            payload=event_payload,
            room="admins",
        )

        _notify_admin_for_request_event(
            user_db,
            event=NotificationEvent.REQUEST_FULFILLED,
            request_row=updated,
        )

        return jsonify(updated)

    @app.route("/api/admin/requests/<int:request_id>/reject", methods=["POST"])
    def api_admin_reject_request(request_id: int) -> ResponseReturnValue:
        auth_gate = _require_request_endpoints_available(resolve_auth_mode)
        if auth_gate is not None:
            return auth_gate

        admin_user_id, admin_gate = _require_admin_user_id()
        if admin_gate is not None:
            return admin_gate
        if admin_user_id is None:
            return jsonify({"error": "Admin user identity unavailable"}), 403

        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            return jsonify({"error": "Invalid payload"}), 400

        try:
            updated = reject_request(
                user_db,
                request_id=request_id,
                admin_user_id=admin_user_id,
                admin_note=data.get("admin_note"),
            )
        except RequestServiceError as exc:
            return _error_response(str(exc), exc.status_code, code=exc.code)

        event_payload = {
            "request_id": updated["id"],
            "status": updated["status"],
            "title": _resolve_request_title(updated),
        }
        admin_label = _format_user_label(
            normalize_optional_text(session.get("user_id")), admin_user_id
        )
        requester_label = _format_requester_label(user_db, updated)
        logger.info(
            "Request rejected #%s for '%s' by %s (requested by %s)",
            updated["id"],
            event_payload["title"],
            admin_label,
            requester_label,
        )
        emit_ws_event(
            ws_manager,
            event_name="request_update",
            payload=event_payload,
            room=f"user_{updated['user_id']}",
        )
        emit_ws_event(
            ws_manager,
            event_name="request_update",
            payload=event_payload,
            room="admins",
        )

        _notify_admin_for_request_event(
            user_db,
            event=NotificationEvent.REQUEST_REJECTED,
            request_row=updated,
        )

        return jsonify(updated)

    @app.route("/api/admin/requests/<int:request_id>/reopen", methods=["POST"])
    def api_admin_reopen_request(request_id: int) -> ResponseReturnValue:
        auth_gate = _require_request_endpoints_available(resolve_auth_mode)
        if auth_gate is not None:
            return auth_gate

        admin_user_id, admin_gate = _require_admin_user_id()
        if admin_gate is not None:
            return admin_gate
        if admin_user_id is None:
            return jsonify({"error": "Admin user identity unavailable"}), 403

        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            return jsonify({"error": "Invalid payload"}), 400

        try:
            updated = reopen_request(
                user_db,
                request_id=request_id,
                admin_user_id=admin_user_id,
                admin_note=data.get("admin_note"),
            )
        except RequestServiceError as exc:
            return _error_response(str(exc), exc.status_code, code=exc.code)

        event_payload = {
            "request_id": updated["id"],
            "status": updated["status"],
            "title": _resolve_request_title(updated),
        }
        admin_label = _format_user_label(
            normalize_optional_text(session.get("user_id")), admin_user_id
        )
        requester_label = _format_requester_label(user_db, updated)
        logger.info(
            "Request reopened #%s for '%s' by %s (requested by %s)",
            updated["id"],
            event_payload["title"],
            admin_label,
            requester_label,
        )
        emit_ws_event(
            ws_manager,
            event_name="request_update",
            payload=event_payload,
            room=f"user_{updated['user_id']}",
        )
        emit_ws_event(
            ws_manager,
            event_name="request_update",
            payload=event_payload,
            room="admins",
        )

        return jsonify(updated)
