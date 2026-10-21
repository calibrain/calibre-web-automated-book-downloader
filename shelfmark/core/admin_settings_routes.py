"""Admin settings-introspection routes and settings validation helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from flask import Flask, jsonify, request

from shelfmark.config.notifications_settings import (
    build_notification_test_result,
    is_valid_notification_url,
    normalize_notification_routes,
)
from shelfmark.config.users_settings import (
    SEARCH_PREFERENCE_VALIDATABLE_KEYS,
    validate_search_preference_value,
)
from shelfmark.core.config import config as app_config
from shelfmark.core.request_policy import parse_policy_mode, validate_policy_rules
from shelfmark.core.settings_registry import load_config_file
from shelfmark.core.user_settings_overrides import (
    build_user_preferences_payload as _build_user_preferences_payload,
)
from shelfmark.core.user_settings_overrides import (
    get_ordered_user_overridable_fields as _get_ordered_user_overridable_fields,
)
from shelfmark.core.user_settings_overrides import (
    get_settings_registry as _get_settings_registry,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from flask.typing import ResponseReturnValue

    from shelfmark.core.user_db import UserDB


def validate_user_settings(
    settings: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Validate and normalize per-user settings overrides."""
    settings_registry = _get_settings_registry()
    field_map = settings_registry.get_settings_field_map()
    overridable_map = settings_registry.get_user_overridable_fields()

    valid: dict[str, Any] = {}
    errors: list[str] = []
    for key, value in settings.items():
        if key not in field_map:
            errors.append(f"Unknown setting: {key}")
        elif key not in overridable_map:
            errors.append(f"Setting not user-overridable: {key}")
        else:
            # null means "clear the per-user override; use global default"
            if value is None:
                valid[key] = None
                continue

            if (
                key in {"REQUEST_POLICY_DEFAULT_EBOOK", "REQUEST_POLICY_DEFAULT_AUDIOBOOK"}
                and parse_policy_mode(value) is None
            ):
                errors.append(f"Invalid policy mode for {key}: {value}")
                continue

            if key == "REQUEST_POLICY_RULES":
                normalized_rules, rule_errors = validate_policy_rules(value)
                if rule_errors:
                    errors.extend(rule_errors)
                    continue
                valid[key] = normalized_rules
                continue

            if key == "USER_NOTIFICATION_ROUTES":
                normalized_routes = normalize_notification_routes(value)
                invalid_count = sum(
                    1
                    for row in normalized_routes
                    if row.get("url") and not is_valid_notification_url(str(row.get("url")))
                )
                if invalid_count:
                    errors.append(
                        f"Invalid value for {key}: found {invalid_count} invalid URL(s). "
                        "Use URL values with a valid scheme, e.g. discord://... or ntfys://..."
                    )
                    continue
                valid[key] = normalized_routes
                continue

            normalized_search_value, search_validation_error = validate_search_preference_value(
                key, value
            )
            if search_validation_error:
                errors.append(search_validation_error)
                continue
            # Every key the search validator recognises keeps its normalized value;
            # a hand-maintained subset here silently dropped normalization for the rest.
            if key in SEARCH_PREFERENCE_VALIDATABLE_KEYS:
                valid[key] = normalized_search_value
                continue

            if key == "DOWNLOAD_TO_BROWSER_CONTENT_TYPES":
                if not isinstance(value, list):
                    errors.append(f"Invalid value for {key}: must be a list")
                    continue

                candidate_values = [
                    str(entry).strip().lower() for entry in value if str(entry).strip()
                ]
                normalized_values: list[str] = []
                has_invalid_value = False
                for entry in candidate_values:
                    if entry not in {"book", "audiobook"}:
                        errors.append(
                            f"Invalid value for {key}: unsupported content type '{entry}'"
                        )
                        has_invalid_value = True
                        continue
                    if entry not in normalized_values:
                        normalized_values.append(entry)

                if has_invalid_value:
                    continue

                valid[key] = normalized_values
                continue

            valid[key] = value

    return valid, errors


def build_user_notification_test_response(
    *,
    user_id: int,
    payload: object,
) -> tuple[dict[str, Any], int]:
    """Build a notification test response using effective per-user routes."""
    from shelfmark.core.config import config as app_config

    routes_input = app_config.get("USER_NOTIFICATION_ROUTES", [], user_id=user_id)
    if isinstance(payload, dict):
        if "USER_NOTIFICATION_ROUTES" in payload:
            routes_input = payload.get("USER_NOTIFICATION_ROUTES")
        elif "routes" in payload:
            routes_input = payload.get("routes")

    result = build_notification_test_result(routes_input, scope_label="personal", user_scope=True)
    status_code = 200 if result.get("success", False) else 400
    return result, status_code


def register_admin_settings_routes(
    app: Flask,
    user_db: UserDB,
    require_admin: Callable[
        [Callable[..., ResponseReturnValue]], Callable[..., ResponseReturnValue]
    ],
) -> None:
    """Register admin endpoints for user-specific settings and defaults."""

    @app.route("/api/admin/download-defaults", methods=["GET"])
    @require_admin
    def admin_download_defaults() -> ResponseReturnValue:
        defaults = {
            key: ("" if (value := app_config.get(key, field.default)) is None else value)
            for key, field in _get_ordered_user_overridable_fields("downloads")
        }

        defaults["OIDC_ADMIN_GROUP"] = app_config.get("OIDC_ADMIN_GROUP", "")
        defaults["OIDC_USE_ADMIN_GROUP"] = app_config.get("OIDC_USE_ADMIN_GROUP", True)
        defaults["OIDC_AUTO_PROVISION"] = app_config.get("OIDC_AUTO_PROVISION", True)
        return jsonify(defaults)

    @app.route("/api/admin/booklore-options", methods=["GET"])
    @require_admin
    def admin_booklore_options() -> ResponseReturnValue:
        from shelfmark.core import admin_routes

        return jsonify(
            {
                "libraries": admin_routes.get_booklore_library_options(),
                "paths": admin_routes.get_booklore_path_options(),
            }
        )

    @app.route("/api/admin/users/<int:user_id>/delivery-preferences", methods=["GET"])
    @require_admin
    def admin_get_delivery_preferences(user_id: int) -> ResponseReturnValue:
        user = user_db.get_user(user_id=user_id)
        if not user:
            return jsonify({"error": "User not found"}), 404

        try:
            payload = _build_user_preferences_payload(user_db, user_id, "downloads")
        except ValueError:
            return jsonify({"error": "Downloads settings tab not found"}), 500

        return jsonify(payload)

    @app.route("/api/admin/users/<int:user_id>/search-preferences", methods=["GET"])
    @require_admin
    def admin_get_search_preferences(user_id: int) -> ResponseReturnValue:
        user = user_db.get_user(user_id=user_id)
        if not user:
            return jsonify({"error": "User not found"}), 404

        try:
            payload = _build_user_preferences_payload(user_db, user_id, "search_mode")
        except ValueError:
            return jsonify({"error": "Search mode settings tab not found"}), 500

        return jsonify(payload)

    @app.route("/api/admin/users/<int:user_id>/notification-preferences", methods=["GET"])
    @require_admin
    def admin_get_notification_preferences(user_id: int) -> ResponseReturnValue:
        user = user_db.get_user(user_id=user_id)
        if not user:
            return jsonify({"error": "User not found"}), 404

        try:
            payload = _build_user_preferences_payload(user_db, user_id, "notifications")
        except ValueError:
            return jsonify({"error": "Notifications settings tab not found"}), 500

        return jsonify(payload)

    @app.route("/api/admin/users/<int:user_id>/notification-preferences/test", methods=["POST"])
    @require_admin
    def admin_test_notification_preferences(user_id: int) -> ResponseReturnValue:
        user = user_db.get_user(user_id=user_id)
        if not user:
            return jsonify({"error": "User not found"}), 404

        payload = request.get_json(silent=True)
        result, status_code = build_user_notification_test_response(
            user_id=user_id,
            payload=payload,
        )
        return jsonify(result), status_code

    @app.route("/api/admin/settings/overrides-summary", methods=["GET"])
    @require_admin
    def admin_settings_overrides_summary() -> ResponseReturnValue:
        settings_registry = _get_settings_registry()

        tab_name = (request.args.get("tab") or "downloads").strip()
        if not settings_registry.get_settings_tab(tab_name):
            return jsonify({"error": f"Unknown settings tab: {tab_name}"}), 404

        overridable_keys = list(settings_registry.get_user_overridable_fields(tab_name=tab_name))
        keys_payload: dict[str, dict[str, Any]] = {}

        for user_record in user_db.list_users():
            user_settings = user_db.get_user_settings(user_record["id"])
            if not isinstance(user_settings, dict):
                continue

            for key in overridable_keys:
                if key not in user_settings or user_settings[key] is None:
                    continue
                entry = keys_payload.setdefault(key, {"count": 0, "users": []})
                entry["users"].append(
                    {
                        "userId": user_record["id"],
                        "username": user_record["username"],
                        "value": user_settings[key],
                    }
                )

        for summary in keys_payload.values():
            summary["count"] = len(summary["users"])

        return jsonify({"tab": tab_name, "keys": keys_payload})

    @app.route("/api/admin/users/<int:user_id>/effective-settings", methods=["GET"])
    @require_admin
    def admin_get_effective_settings(user_id: int) -> ResponseReturnValue:
        user = user_db.get_user(user_id=user_id)
        if not user:
            return jsonify({"error": "User not found"}), 404

        from shelfmark.core.config import config as app_config
        from shelfmark.core.settings_registry import is_value_from_env

        field_map = _get_settings_registry().get_user_overridable_fields()
        user_settings = user_db.get_user_settings(user_id)
        tab_config_cache: dict[str, dict[str, Any]] = {}
        effective: dict[str, dict[str, Any]] = {}

        for key, (field, tab_name) in sorted(field_map.items()):
            value = app_config.get(key, field.default, user_id=user_id)
            source = "default"

            if field.env_supported and is_value_from_env(field):
                source = "env_var"
            elif key in user_settings and user_settings[key] is not None:
                source = "user_override"
                value = user_settings[key]
            else:
                tab_config = tab_config_cache.setdefault(tab_name, load_config_file(tab_name))
                if key in tab_config:
                    source = "global_config"

            effective[key] = {"value": value, "source": source}

        return jsonify(effective)
