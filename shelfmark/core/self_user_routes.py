"""Self-service user account routes."""

import sqlite3
from dataclasses import dataclass
from functools import wraps
from typing import TYPE_CHECKING, Any

from flask import Flask, Response, g, jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash

from shelfmark.core.admin_settings_routes import (
    build_user_notification_test_response,
    validate_user_settings,
)
from shelfmark.core.auth_modes import (
    AUTH_SOURCE_BUILTIN,
    AUTH_SOURCE_CWA,
    AUTH_SOURCE_OIDC,
    AUTH_SOURCE_PROXY,
    is_user_active_for_auth_mode,
    load_active_auth_mode,
    normalize_auth_source,
)
from shelfmark.core.config import config as app_config
from shelfmark.core.logger import setup_logger
from shelfmark.core.user_settings_overrides import (
    build_user_preferences_payload as _build_user_preferences_payload,
)
from shelfmark.core.user_settings_overrides import (
    get_ordered_user_overridable_fields as _get_ordered_user_overridable_fields,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from shelfmark.core.user_db import UserDB

logger = setup_logger(__name__)

MIN_PASSWORD_LENGTH = 4


@dataclass(frozen=True)
class PasswordAttemptLimiter:
    """The login lockout, lent to the password change so it cannot be used to guess a password.

    ``record_failure`` returns True when the account is now locked.
    """

    is_locked: Callable[[str], bool]
    record_failure: Callable[[str], bool]
    clear: Callable[[str], None]


_VISIBLE_SELF_SETTINGS_SECTIONS_KEY = "VISIBLE_SELF_SETTINGS_SECTIONS"
_SELF_SETTINGS_SECTION_DELIVERY = "delivery"
_SELF_SETTINGS_SECTION_SEARCH = "search"
_SELF_SETTINGS_SECTION_NOTIFICATIONS = "notifications"
_VALID_SELF_SETTINGS_SECTIONS = (
    _SELF_SETTINGS_SECTION_DELIVERY,
    _SELF_SETTINGS_SECTION_SEARCH,
    _SELF_SETTINGS_SECTION_NOTIFICATIONS,
)
_DEFAULT_VISIBLE_SELF_SETTINGS_SECTIONS = list(_VALID_SELF_SETTINGS_SECTIONS)
_USER_PREFERENCES_FALLBACK_ERRORS = (ImportError, OSError, RuntimeError, TypeError, sqlite3.Error)
_CONFIG_REFRESH_ERRORS = (ImportError, OSError, RuntimeError, TypeError, ValueError)


def _get_current_user(
    user_db: UserDB,
) -> tuple[int | None, dict[str, Any] | None, tuple[Response, int] | None]:
    raw_user_id = session.get("db_user_id")
    if raw_user_id is None:
        return None, None, (jsonify({"error": "Invalid user context"}), 400)
    try:
        user_id = int(raw_user_id)
    except TypeError, ValueError:
        return None, None, (jsonify({"error": "Invalid user context"}), 400)

    user = user_db.get_user(user_id=user_id)
    if not user:
        return None, None, (jsonify({"error": "User not found"}), 404)
    return user_id, user, None


def _get_self_edit_capabilities(user: Mapping[str, Any]) -> dict[str, Any]:
    auth_source = normalize_auth_source(
        user.get("auth_source"),
        user.get("oidc_subject"),
    )

    return {
        "authSource": auth_source,
        "canSetPassword": auth_source == AUTH_SOURCE_BUILTIN,
        "canEditRole": False,
        "canEditEmail": auth_source in {AUTH_SOURCE_BUILTIN, AUTH_SOURCE_PROXY},
        "canEditDisplayName": auth_source != AUTH_SOURCE_OIDC,
    }


def _serialize_self_user(user: Mapping[str, Any], auth_mode: str) -> dict[str, Any]:
    payload = dict(user)
    payload.pop("password_hash", None)
    payload["auth_source"] = normalize_auth_source(
        payload.get("auth_source"),
        payload.get("oidc_subject"),
    )
    payload["is_active"] = is_user_active_for_auth_mode(payload, auth_mode)
    payload["edit_capabilities"] = _get_self_edit_capabilities(payload)
    return payload


def _build_optional_user_preferences(
    user_db: UserDB,
    *,
    user_id: int,
    tab_name: str,
    missing_tab_error: str,
    preference_label: str,
) -> tuple[dict[str, Any] | None, tuple[Response, int] | None]:
    try:
        # Self-service: the user only gets to see values they set themselves.
        return (
            _build_user_preferences_payload(user_db, user_id, tab_name, for_self_service=True),
            None,
        )
    except ValueError as exc:
        if str(exc) == missing_tab_error:
            return None, (jsonify({"error": missing_tab_error}), 500)
        logger.warning(
            "Failed to build user %s preferences for user_id=%s: %s",
            preference_label,
            user_id,
            exc,
        )
        return None, None
    except _USER_PREFERENCES_FALLBACK_ERRORS as exc:
        logger.warning(
            "Failed to build user %s preferences for user_id=%s: %s",
            preference_label,
            user_id,
            exc,
        )
        return None, None


def _normalize_visible_self_settings_sections(raw_sections: object) -> list[str]:
    """Normalize users.VISIBLE_SELF_SETTINGS_SECTIONS to a safe ordered list."""
    if raw_sections is None:
        return list(_DEFAULT_VISIBLE_SELF_SETTINGS_SECTIONS)

    if isinstance(raw_sections, str):
        candidate_sections = [s.strip() for s in raw_sections.split(",") if s.strip()]
    elif isinstance(raw_sections, (list, tuple, set)):
        candidate_sections = [
            str(section).strip() for section in raw_sections if str(section).strip()
        ]
    else:
        return list(_DEFAULT_VISIBLE_SELF_SETTINGS_SECTIONS)

    normalized_sections: list[str] = []
    for section in candidate_sections:
        if section in _VALID_SELF_SETTINGS_SECTIONS and section not in normalized_sections:
            normalized_sections.append(section)

    if not normalized_sections and candidate_sections:
        # Invalid non-empty config should fail-safe to showing defaults.
        return list(_DEFAULT_VISIBLE_SELF_SETTINGS_SECTIONS)

    return normalized_sections


def _get_visible_self_settings_sections() -> list[str]:
    raw_sections = app_config.get(
        _VISIBLE_SELF_SETTINGS_SECTIONS_KEY,
        list(_DEFAULT_VISIBLE_SELF_SETTINGS_SECTIONS),
    )
    return _normalize_visible_self_settings_sections(raw_sections)


def _get_allowed_self_settings_keys(visible_sections: list[str]) -> set[str]:
    allowed_keys: set[str] = set()
    visible_sections_set = set(visible_sections)

    if _SELF_SETTINGS_SECTION_DELIVERY in visible_sections_set:
        allowed_keys |= {key for key, _field in _get_ordered_user_overridable_fields("downloads")}

    if _SELF_SETTINGS_SECTION_SEARCH in visible_sections_set:
        allowed_keys |= {key for key, _field in _get_ordered_user_overridable_fields("search_mode")}

    if _SELF_SETTINGS_SECTION_NOTIFICATIONS in visible_sections_set:
        allowed_keys |= {
            key for key, _field in _get_ordered_user_overridable_fields("notifications")
        }

    return allowed_keys


def _check_current_password(
    user: Mapping[str, Any],
    current_password: object,
    attempts: PasswordAttemptLimiter | None,
) -> tuple[Response, int] | None:
    """An error response unless ``current_password`` is the user's password.

    A wrong guess counts toward the same lockout as a failed login.
    """
    username = str(user.get("username") or "")
    if attempts is not None and attempts.is_locked(username):
        return jsonify(
            {
                "error": "Too many failed attempts",
                "message": "This account is locked for a while after too many wrong passwords.",
            }
        ), 429
    stored_hash = user.get("password_hash")
    if (
        not isinstance(current_password, str)
        or not current_password
        or not isinstance(stored_hash, str)
        or not check_password_hash(stored_hash, current_password)
    ):
        if attempts is not None:
            attempts.record_failure(username)
        return jsonify(
            {
                "error": "Current password is incorrect",
                "message": "Enter your current password to set a new one.",
            }
        ), 400
    if attempts is not None:
        attempts.clear(username)
    return None


def register_self_user_routes(
    app: Flask,
    user_db: UserDB,
    *,
    password_attempts: PasswordAttemptLimiter | None = None,
) -> None:
    """Register self-service user endpoints."""

    def _require_authenticated_user(
        f: Callable[..., Response | tuple[Response, int]],
    ) -> Callable[..., Response | tuple[Response, int]]:
        """Require an authenticated session linked to a local user row.

        Caches the resolved auth_mode in ``g.auth_mode`` for the request.
        """

        @wraps(f)
        def decorated(*args: object, **kwargs: object) -> Response | tuple[Response, int]:
            auth_mode = load_active_auth_mode()
            g.auth_mode = auth_mode
            if auth_mode != "none" and "user_id" not in session:
                return jsonify({"error": "Authentication required"}), 401
            if "db_user_id" not in session:
                return jsonify(
                    {"error": "Authenticated session is missing local user context"}
                ), 403
            return f(*args, **kwargs)

        return decorated

    @app.route("/api/users/me/edit-context", methods=["GET"])
    @_require_authenticated_user
    def users_me_edit_context() -> Response | tuple[Response, int]:
        user_id, user, user_error = _get_current_user(user_db)
        if user_error:
            return user_error
        if user_id is None or user is None:
            return jsonify({"error": "User not found"}), 404

        serialized_user = _serialize_self_user(user, g.auth_mode)
        serialized_user["settings"] = user_db.get_user_settings(user_id)
        visible_self_settings_sections = _get_visible_self_settings_sections()

        delivery_preferences = None
        if _SELF_SETTINGS_SECTION_DELIVERY in visible_self_settings_sections:
            delivery_preferences, error_response = _build_optional_user_preferences(
                user_db,
                user_id=user_id,
                tab_name="downloads",
                missing_tab_error="Downloads settings tab not found",
                preference_label="delivery",
            )
            if error_response:
                return error_response

        search_preferences = None
        if _SELF_SETTINGS_SECTION_SEARCH in visible_self_settings_sections:
            search_preferences, error_response = _build_optional_user_preferences(
                user_db,
                user_id=user_id,
                tab_name="search_mode",
                missing_tab_error="Search mode settings tab not found",
                preference_label="search",
            )
            if error_response:
                return error_response

        notification_preferences = None
        if _SELF_SETTINGS_SECTION_NOTIFICATIONS in visible_self_settings_sections:
            notification_preferences, error_response = _build_optional_user_preferences(
                user_db,
                user_id=user_id,
                tab_name="notifications",
                missing_tab_error="Notifications settings tab not found",
                preference_label="notification",
            )
            if error_response:
                return error_response

        user_overridable_keys = sorted(
            set(delivery_preferences.get("keys", []) if delivery_preferences else [])
            | set(search_preferences.get("keys", []) if search_preferences else [])
            | set(notification_preferences.get("keys", []) if notification_preferences else [])
        )

        return jsonify(
            {
                "user": serialized_user,
                "deliveryPreferences": delivery_preferences,
                "searchPreferences": search_preferences,
                "notificationPreferences": notification_preferences,
                "userOverridableKeys": user_overridable_keys,
                "visibleUserSettingsSections": visible_self_settings_sections,
            }
        )

    @app.route("/api/users/me/notification-preferences/test", methods=["POST"])
    @_require_authenticated_user
    def users_me_test_notification_preferences() -> Response | tuple[Response, int]:
        user_id, _user, user_error = _get_current_user(user_db)
        if user_error:
            return user_error
        if user_id is None:
            return jsonify({"error": "User not found"}), 404

        payload = request.get_json(silent=True)
        result, status_code = build_user_notification_test_response(
            user_id=user_id,
            payload=payload,
        )
        return jsonify(result), status_code

    @app.route("/api/users/me", methods=["PUT"])
    @_require_authenticated_user
    def users_me_update() -> Response | tuple[Response, int]:
        user_id, user, user_error = _get_current_user(user_db)
        if user_error:
            return user_error
        if user_id is None or user is None:
            return jsonify({"error": "User not found"}), 404

        data = request.get_json() or {}
        if not isinstance(data, dict):
            return jsonify({"error": "Request body must be a JSON object"}), 400

        capabilities = _get_self_edit_capabilities(user)
        auth_source = capabilities["authSource"]

        password = data.get("password", "")
        password_hash: str | None = None
        if password:
            if not isinstance(password, str):
                return jsonify({"error": "Password must be a string"}), 400
            if not capabilities["canSetPassword"]:
                return jsonify(
                    {
                        "error": f"Cannot set password for {auth_source.upper()} users",
                        "message": "Password authentication is only available for local users.",
                    }
                ), 400
            if len(password) < MIN_PASSWORD_LENGTH:
                return jsonify(
                    {"error": f"Password must be at least {MIN_PASSWORD_LENGTH} characters"}
                ), 400
            current_error = _check_current_password(
                user, data.get("current_password"), password_attempts
            )
            if current_error is not None:
                return current_error
            password_hash = generate_password_hash(password)

        user_fields: dict[str, Any] = {}
        if "email" in data:
            incoming_email = data.get("email")
            if incoming_email is None:
                user_fields["email"] = None
            else:
                user_fields["email"] = str(incoming_email).strip() or None
        if "display_name" in data:
            incoming_display_name = data.get("display_name")
            user_fields["display_name"] = (
                str(incoming_display_name).strip() or None
                if incoming_display_name is not None
                else None
            )

        email_changed = "email" in user_fields and user_fields["email"] != user.get("email")
        display_name_changed = "display_name" in user_fields and user_fields[
            "display_name"
        ] != user.get("display_name")

        if email_changed and not capabilities["canEditEmail"]:
            if auth_source == AUTH_SOURCE_CWA:
                return jsonify(
                    {
                        "error": "Cannot change email for CWA users",
                        "message": "Email is synced from Calibre-Web.",
                    }
                ), 400
            return jsonify(
                {
                    "error": "Cannot change email for OIDC users",
                    "message": "Email is managed by your identity provider.",
                }
            ), 400

        if display_name_changed and not capabilities["canEditDisplayName"]:
            return jsonify(
                {
                    "error": "Cannot change display name for OIDC users",
                    "message": "Display name is managed by your identity provider.",
                }
            ), 400

        for field in ("email", "display_name"):
            if field in user_fields and user_fields[field] == user.get(field):
                user_fields.pop(field)

        validated_settings: dict[str, Any] | None = None
        if "settings" in data:
            settings_payload = data["settings"]
            if not isinstance(settings_payload, dict):
                return jsonify({"error": "Settings must be an object"}), 400

            visible_self_settings_sections = _get_visible_self_settings_sections()
            allowed_user_settings_keys = _get_allowed_self_settings_keys(
                visible_self_settings_sections
            )
            disallowed_keys = sorted(
                key for key in settings_payload if key not in allowed_user_settings_keys
            )
            if disallowed_keys:
                return jsonify(
                    {
                        "error": "Some settings are admin-only",
                        "details": [
                            f"Setting not user-overridable: {key}" for key in disallowed_keys
                        ],
                    }
                ), 400

            validated_settings, validation_errors = validate_user_settings(
                settings_payload, self_service_user=user
            )
            if validation_errors:
                return jsonify(
                    {
                        "error": "Invalid settings payload",
                        "message": "; ".join(validation_errors),
                        "details": validation_errors,
                    }
                ), 400

        # Apply the writes only once the whole payload has been accepted.
        if password_hash is not None:
            user_fields["password_hash"] = password_hash

        if user_fields:
            user_db.update_user(user_id, **user_fields)

        if validated_settings is not None:
            user_db.set_user_settings(user_id, validated_settings)
            try:
                app_config.refresh(force=True)
            except _CONFIG_REFRESH_ERRORS as exc:
                logger.warning(
                    "Updated settings for user %s but failed to refresh runtime config: %s",
                    user_id,
                    exc,
                )

        updated = user_db.get_user(user_id=user_id)
        if not updated:
            return jsonify({"error": "User not found"}), 404

        result = _serialize_self_user(updated, g.auth_mode)
        result["settings"] = user_db.get_user_settings(user_id)
        logger.info("User %s updated their own account", user_id)
        return jsonify(result)
