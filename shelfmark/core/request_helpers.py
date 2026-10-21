"""Shared request-related helper functions used by routes and services."""

from __future__ import annotations

from datetime import UTC, datetime
from email.utils import parseaddr
from typing import Any, Protocol, SupportsIndex, SupportsInt, TypeGuard

from shelfmark.core.config import config as app_config
from shelfmark.core.logger import setup_logger

_logger = setup_logger(__name__)

type _ConvertibleToInt = str | bytes | bytearray | SupportsInt | SupportsIndex


class _MappingWithGet(Protocol):
    """Minimal mapping protocol for session-like objects."""

    def get(self, key: str, default: object = None, /) -> object: ...


class _UserDBLike(Protocol):
    """Minimal user DB protocol for username population helpers."""

    def get_user(self, *, user_id: int) -> dict[str, Any] | None: ...


def _is_mapping_with_get(candidate: object) -> TypeGuard[_MappingWithGet]:
    """Return True when *candidate* exposes a mapping-style get method."""
    return callable(getattr(candidate, "get", None))


def _is_user_db_like(candidate: object) -> TypeGuard[_UserDBLike]:
    """Return True when *candidate* exposes the user lookup API we need."""
    return callable(getattr(candidate, "get_user", None))


def _is_convertible_to_int(value: object) -> TypeGuard[_ConvertibleToInt]:
    """Return True when *value* can be passed to ``int`` safely."""
    return (
        isinstance(value, (str, bytes, bytearray))
        or hasattr(value, "__int__")
        or hasattr(value, "__index__")
    )


def now_utc_iso() -> str:
    """Return the current UTC time as a seconds-precision ISO 8601 string."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def emit_ws_event(
    ws_manager: object,
    *,
    event_name: str,
    payload: dict[str, Any],
    room: str,
) -> None:
    """Emit a WebSocket event via the shared manager, swallowing failures."""
    if ws_manager is None:
        return
    try:
        socketio = getattr(ws_manager, "socketio", None)
        is_enabled = getattr(ws_manager, "is_enabled", None)
        if socketio is None or not callable(is_enabled) or not is_enabled():
            return
        socketio.emit(event_name, payload, to=room)
    except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
        _logger.warning(
            "Failed to emit WebSocket event '%s' to room '%s': %s",
            event_name,
            room,
            exc,
        )


def load_users_request_policy_settings() -> dict[str, Any]:
    """Load global request-policy settings from the users config file."""
    from shelfmark.core.request_policy import REQUEST_POLICY_KEYS

    return {key: app_config.get(key) for key in REQUEST_POLICY_KEYS}


def coerce_bool(value: object, *, default: bool = False) -> bool:
    """Coerce arbitrary values into booleans with string-friendly semantics."""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off", ""}:
            return False
    return bool(value)


def get_session_db_user_id(session_obj: object) -> int | None:
    """Extract and coerce `db_user_id` from a Flask session to ``int | None``."""
    raw = session_obj.get("db_user_id") if _is_mapping_with_get(session_obj) else None
    try:
        return int(raw) if raw is not None and _is_convertible_to_int(raw) else None
    except TypeError, ValueError:
        return None


def coerce_int(value: object, default: int) -> int:
    """Best-effort integer coercion with fallback to default."""
    if not _is_convertible_to_int(value):
        return default
    try:
        return int(value)
    except TypeError, ValueError:
        return default


def is_plain_email_address(value: str) -> bool:
    """Return True for a bare address like ``reader@example.com`` (no display name)."""
    parsed = parseaddr(value or "")[1]
    return bool(parsed) and "@" in parsed and parsed == value


def normalize_optional_text(value: object) -> str | None:
    """Return a trimmed string or None for empty/non-string input."""
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def normalize_positive_int(value: object) -> int | None:
    """Parse *value* as a positive integer, returning ``None`` on failure."""
    if not _is_convertible_to_int(value):
        return None
    try:
        parsed = int(value)
    except TypeError, ValueError:
        return None
    return parsed if parsed > 0 else None


def normalize_optional_positive_int(value: object, field_name: str = "value") -> int | None:
    """Parse *value* as a positive integer or ``None``.

    Raises ``ValueError`` when *value* is present but not a valid
    positive integer.
    """
    if value is None:
        return None
    if not _is_convertible_to_int(value):
        msg = f"{field_name} must be a positive integer when provided"
        raise ValueError(msg)
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        msg = f"{field_name} must be a positive integer when provided"
        raise ValueError(msg) from exc
    if parsed < 1:
        msg = f"{field_name} must be a positive integer when provided"
        raise ValueError(msg)
    return parsed


def populate_request_usernames(rows: list[dict[str, Any]], user_db: object) -> None:
    """Add 'username' to each request row by looking up user_id."""
    if not _is_user_db_like(user_db):
        return

    cache: dict[int, str] = {}
    for row in rows:
        requester_id = normalize_positive_int(row.get("user_id"))
        if requester_id is None:
            row["username"] = ""
            continue
        if requester_id not in cache:
            requester = user_db.get_user(user_id=requester_id)
            cache[requester_id] = requester.get("username", "") if requester else ""
        row["username"] = cache[requester_id]


def extract_release_source_id(release_data: object) -> str | None:
    """Extract and normalize release_data.source_id."""
    if not isinstance(release_data, dict):
        return None
    source_id = release_data.get("source_id")
    if not isinstance(source_id, str):
        return None
    normalized = source_id.strip()
    return normalized or None
