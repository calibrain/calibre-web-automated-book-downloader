"""Notifications settings tab registration."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

from shelfmark.core.config import config as app_config
from shelfmark.core.notifications import (
    NotificationEvent,
    expand_user_route_placeholders,
    send_test_notification,
    user_route_url_problem,
)
from shelfmark.core.settings_registry import (
    ActionButton,
    HeadingField,
    SettingsField,
    TableField,
    load_config_file,
    register_on_save,
    register_settings,
)

_URL_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*$")

_ROUTE_EVENT_ALL = "all"
_ADMIN_EVENT_OPTIONS = [
    {"value": NotificationEvent.REQUEST_CREATED.value, "label": "New request submitted"},
    {"value": NotificationEvent.REQUEST_FULFILLED.value, "label": "Request approved"},
    {"value": NotificationEvent.REQUEST_REJECTED.value, "label": "Request rejected"},
    {"value": NotificationEvent.DOWNLOAD_COMPLETE.value, "label": "Download complete"},
    {"value": NotificationEvent.DOWNLOAD_FAILED.value, "label": "Download failed"},
]
_ROUTE_EVENT_OPTIONS = [
    {"value": _ROUTE_EVENT_ALL, "label": "All"},
    *_ADMIN_EVENT_OPTIONS,
]
_ROUTE_EVENT_ORDER = [option["value"] for option in _ROUTE_EVENT_OPTIONS]
_ROUTE_EVENT_INDEX = {event: index for index, event in enumerate(_ROUTE_EVENT_ORDER)}
_ALLOWED_ROUTE_EVENTS = set(_ROUTE_EVENT_ORDER)

_DEFAULT_ROUTE_ROWS = [{"event": [_ROUTE_EVENT_ALL], "url": ""}]


def _looks_like_apprise_url(url: str) -> bool:
    split = urlsplit(url)
    if not split.scheme:
        return False
    if not _URL_SCHEME_RE.match(split.scheme):
        return False
    return " " not in url


def _coerce_route_rows(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    if isinstance(value, dict):
        return [value]
    return []


def _coerce_route_event_values(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, (tuple, set)):
        return list(value)
    return [value]


def _normalize_route_events(value: Any) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()

    for raw_event in _coerce_route_event_values(value):
        event = str(raw_event or "").strip().lower()
        if not event or event not in _ALLOWED_ROUTE_EVENTS:
            continue
        if event in seen:
            continue
        seen.add(event)
        normalized.append(event)

    if _ROUTE_EVENT_ALL in seen:
        return [_ROUTE_EVENT_ALL]

    return sorted(normalized, key=lambda event: _ROUTE_EVENT_INDEX[event])


def _normalize_routes(value: Any) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[tuple[str, ...], str]] = set()

    for row in _coerce_route_rows(value):
        events = _normalize_route_events(row.get("event"))
        if not events:
            continue

        url = str(row.get("url") or "").strip()
        key = (tuple(events), url)
        if key in seen:
            continue
        seen.add(key)

        normalized.append({"event": events, "url": url})

    return normalized


def _count_invalid_route_events(value: Any) -> int:
    invalid = 0
    for row in _coerce_route_rows(value):
        raw_events = _coerce_route_event_values(row.get("event"))
        if not raw_events:
            invalid += 1
            continue

        for raw_event in raw_events:
            event = str(raw_event or "").strip().lower()
            if not event or event not in _ALLOWED_ROUTE_EVENTS:
                invalid += 1
    return invalid


def _count_invalid_route_urls(routes: list[dict[str, Any]]) -> int:
    return sum(1 for row in routes if row["url"] and not _looks_like_apprise_url(row["url"]))


def _ensure_default_route_row(routes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return routes or [dict(row) for row in _DEFAULT_ROUTE_ROWS]


def _extract_unique_route_urls(routes: list[dict[str, Any]]) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for row in routes:
        url = row.get("url", "")
        if not url:
            continue
        if url in seen:
            continue
        seen.add(url)
        urls.append(url)
    return urls


def build_notification_test_result(
    routes_input: Any,
    *,
    scope_label: str,
    user_scope: bool = False,
    user_id: int | None = None,
) -> dict[str, Any]:
    """Validate routes and return a test-notification result payload.

    ``user_scope`` is for a user's own routes, which may only use the services and public hosts
    that ``user_route_url_problem`` allows. ``user_id`` fills in ``{User}``/``{Email}`` first, so
    the check sees the URL that would actually be sent.
    """
    invalid_event_count = _count_invalid_route_events(routes_input)
    if invalid_event_count:
        return {
            "success": False,
            "message": (
                f"Found {invalid_event_count} invalid {scope_label} notification route event value(s). "
                "Fix route events before running a test."
            ),
        }

    normalized_routes = _normalize_routes(routes_input)
    invalid_url_count = _count_invalid_route_urls(normalized_routes)
    if invalid_url_count:
        return {
            "success": False,
            "message": (
                f"Found {invalid_url_count} invalid {scope_label} notification URL(s). "
                "Fix route URLs before running a test."
            ),
        }

    urls = _extract_unique_route_urls(normalized_routes)
    if not urls:
        return {
            "success": False,
            "message": f"Add at least one {scope_label} notification URL route first.",
        }

    if user_id is not None:
        urls = expand_user_route_placeholders(urls, user_id)
        if not urls:
            return {
                "success": False,
                "message": (
                    "Every route was skipped while filling in {User}/{Email}: the account has "
                    "no value for it, or a placeholder is in the host part of the URL."
                ),
            }

    if user_scope:
        for url in urls:
            problem = user_route_url_problem(url)
            if problem is not None:
                return {
                    "success": False,
                    "message": f"Personal notification route refused: {problem}.",
                }

    return send_test_notification(urls)


def normalize_notification_routes(value: Any) -> list[dict[str, Any]]:
    """Normalize route table rows for notification preferences."""
    return _normalize_routes(value)


def is_valid_notification_url(url: str) -> bool:
    """Shared URL validation for notifications preferences."""
    return _looks_like_apprise_url(url)


def _on_save_notifications(values: dict[str, Any]) -> dict[str, Any]:
    existing = load_config_file("notifications")
    effective: dict[str, Any] = dict(existing)
    effective.update(values)

    admin_routes_input = effective.get("ADMIN_NOTIFICATION_ROUTES", [])
    invalid_admin_event_count = _count_invalid_route_events(admin_routes_input)
    if invalid_admin_event_count:
        return {
            "error": True,
            "message": (
                f"Found {invalid_admin_event_count} invalid global notification route event value(s)."
            ),
            "values": values,
        }

    normalized_admin_routes = _normalize_routes(admin_routes_input)
    invalid_admin_url_count = _count_invalid_route_urls(normalized_admin_routes)
    if invalid_admin_url_count:
        return {
            "error": True,
            "message": (
                f"Found {invalid_admin_url_count} invalid global notification URL(s). "
                "Use URL values with a valid scheme, e.g. discord://... or ntfys://..."
            ),
            "values": values,
        }

    user_routes_input = effective.get("USER_NOTIFICATION_ROUTES", [])
    invalid_user_event_count = _count_invalid_route_events(user_routes_input)
    if invalid_user_event_count:
        return {
            "error": True,
            "message": (
                f"Found {invalid_user_event_count} invalid personal notification route event value(s)."
            ),
            "values": values,
        }

    normalized_user_routes = _normalize_routes(user_routes_input)
    invalid_user_url_count = _count_invalid_route_urls(normalized_user_routes)
    if invalid_user_url_count:
        return {
            "error": True,
            "message": (
                f"Found {invalid_user_url_count} invalid personal notification URL(s). "
                "Use URL values with a valid scheme, e.g. discord://... or ntfys://..."
            ),
            "values": values,
        }

    admin_routes_touched = "ADMIN_NOTIFICATION_ROUTES" in values
    if admin_routes_touched:
        values["ADMIN_NOTIFICATION_ROUTES"] = _ensure_default_route_row(normalized_admin_routes)

    user_routes_touched = "USER_NOTIFICATION_ROUTES" in values
    if user_routes_touched:
        values["USER_NOTIFICATION_ROUTES"] = _ensure_default_route_row(normalized_user_routes)

    return {"error": False, "values": values}


def _test_admin_notification_action(current_values: dict[str, Any]) -> dict[str, Any]:
    effective: dict[str, Any] = {
        "ADMIN_NOTIFICATION_ROUTES": app_config.get("ADMIN_NOTIFICATION_ROUTES", []),
    }
    if isinstance(current_values, dict):
        effective.update(current_values)

    routes_input = effective.get("ADMIN_NOTIFICATION_ROUTES", [])
    return build_notification_test_result(routes_input, scope_label="global")


register_on_save("notifications", _on_save_notifications)


@register_settings("notifications", "Notifications", icon="bell", order=7)
def notifications_settings() -> list[SettingsField]:
    """Global notifications settings."""
    return [
        HeadingField(
            key="notifications_heading",
            title="Global Notifications",
            description=(
                "Global notifications send selected events for all users to configured routes. "
                "Users can manage personal notifications in User Preferences."
            ),
        ),
        TableField(
            key="ADMIN_NOTIFICATION_ROUTES",
            label="",
            description=(
                "Create one route per URL. Start with All, then add event-specific routes "
                "for targeted delivery. Need format examples? "
                "[View Apprise URL formats](https://appriseit.com/services/)."
            ),
            columns=[
                {
                    "key": "event",
                    "label": "Event",
                    "type": "multiselect",
                    "options": _ROUTE_EVENT_OPTIONS,
                    "defaultValue": [_ROUTE_EVENT_ALL],
                    "placeholder": "Select events...",
                },
                {
                    "key": "url",
                    "label": "Notification URL",
                    "type": "text",
                    "placeholder": "e.g. ntfys://ntfy.sh/shelfmark",
                },
            ],
            default=[dict(row) for row in _DEFAULT_ROUTE_ROWS],
            add_label="Add Route",
            empty_message="No routes configured.",
        ),
        ActionButton(
            key="test_admin_notification",
            label="Test Notification",
            description="Send a test notification to all configured global route URLs.",
            style="primary",
            callback=_test_admin_notification_action,
        ),
        TableField(
            key="USER_NOTIFICATION_ROUTES",
            label="",
            description=(
                "Create one route per URL. Start with All, then add event-specific routes "
                "for targeted delivery. Use {User} or {Email} in the path or query to fill in "
                "each user's account details, e.g. ntfys://ntfy.sh/shelfmark-{User}, or "
                "mailtos://...?to={Email} in the admin default. Need format examples? "
                "[View Apprise URL formats](https://appriseit.com/services/)."
            ),
            columns=[
                {
                    "key": "event",
                    "label": "Event",
                    "type": "multiselect",
                    "options": _ROUTE_EVENT_OPTIONS,
                    "defaultValue": [_ROUTE_EVENT_ALL],
                    "placeholder": "Select events...",
                },
                {
                    "key": "url",
                    "label": "Notification URL",
                    "type": "text",
                    "placeholder": "e.g. ntfys://ntfy.sh/username-topic",
                },
            ],
            default=[dict(row) for row in _DEFAULT_ROUTE_ROWS],
            add_label="Add Route",
            empty_message="No routes configured.",
            user_overridable=True,
            hidden_in_ui=True,
        ),
    ]
