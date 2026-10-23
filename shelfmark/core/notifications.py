"""Apprise notification dispatch for global and per-user events."""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol, TypeGuard
from urllib.parse import quote, urlsplit

try:
    import apprise
except ImportError:  # pragma: no cover - exercised in tests via monkeypatch
    apprise = None  # type: ignore[assignment]

from shelfmark.core.config import config as app_config
from shelfmark.core.logger import setup_logger
from shelfmark.core.request_helpers import is_plain_email_address, normalize_positive_int

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

logger = setup_logger(__name__)

# Small pool for non-blocking dispatch. Notification sends are I/O bound and infrequent.
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="Notify")
_ROUTE_EVENT_ALL = "all"
_APPRISE_APP_ID = "Shelfmark"
_APPRISE_APP_DESC = "Shelfmark notifications"
_APPRISE_LOGO_URL = (
    "https://raw.githubusercontent.com/calibrain/shelfmark/main/src/frontend/public/logo.png"
)
_APPRISE_LOGGER_NAME = "apprise"
_APPRISE_DISPATCH_ERRORS = (RuntimeError, TypeError, ValueError)
_USER_ROUTE_PLACEHOLDER_PATTERN = re.compile(r"\{(user|email)\}", re.IGNORECASE)
# Apprise splits a decoded value on these, so "John Smith" or '"a,b@x.com"@y.com' would reach
# extra topics or recipients (e.g. add recipients to an admin-set mailto route).
_UNSAFE_PLACEHOLDER_VALUE = re.compile(r'[\s,;/\\"]')
# Percent-encoding leaves dots alone, so a username of ".." would walk up a webhook URL's path.
_DOTS_ONLY_PLACEHOLDER_VALUE = re.compile(r"\.+")


class _ApprisePluginWithUrl(Protocol):
    app_id: object

    def url(self, *, privacy: bool = False) -> str:
        _ = privacy
        return ""


class _AppriseClient(Protocol):
    asset: object

    def add(self, plugin: object) -> object: ...

    def notify(self, *, title: str, body: str, notify_type: object) -> object: ...


def _is_apprise_client(candidate: object) -> TypeGuard[_AppriseClient]:
    return callable(getattr(candidate, "add", None)) and callable(
        getattr(candidate, "notify", None)
    )


def _has_plugin_url(candidate: object) -> TypeGuard[_ApprisePluginWithUrl]:
    return callable(getattr(candidate, "url", None))


class NotificationEvent(StrEnum):
    """Global notification event identifiers."""

    REQUEST_CREATED = "request_created"
    REQUEST_FULFILLED = "request_fulfilled"
    REQUEST_REJECTED = "request_rejected"
    DOWNLOAD_COMPLETE = "download_complete"
    DOWNLOAD_FAILED = "download_failed"


@dataclass
class NotificationContext:
    """Context used to render notification templates."""

    event: NotificationEvent
    title: str
    author: str
    username: str | None = None
    content_type: str | None = None
    format: str | None = None
    source: str | None = None
    admin_note: str | None = None
    error_message: str | None = None


def _normalize_urls(value: object) -> list[str]:
    if value is None:
        return []

    raw_values: list[Any]
    if isinstance(value, list):
        raw_values = value
    elif isinstance(value, str):
        # Support legacy/manual configs.
        raw_values = [segment for part in value.splitlines() for segment in part.split(",")]
    else:
        raw_values = [value]

    normalized: list[str] = []
    seen: set[str] = set()
    for raw_url in raw_values:
        url = str(raw_url or "").strip()
        if not url:
            continue
        # Strip invisible/non-ASCII characters that can sneak in via copy-paste
        # (zero-width spaces, smart quotes, non-breaking spaces, etc.).
        # These pass Apprise URL validation but cause UnicodeEncodeError when
        # requests tries to latin-1 encode credentials for Basic Auth headers.
        url = url.encode("ascii", errors="ignore").decode("ascii").strip()
        if not url:
            continue
        if url in seen:
            continue
        seen.add(url)
        normalized.append(url)
    return normalized


def _extract_url_schemes(urls: Iterable[str]) -> list[str]:
    schemes: list[str] = []
    seen: set[str] = set()
    for raw_url in urls:
        scheme = urlsplit(str(raw_url or "")).scheme.lower()
        if not scheme or scheme in seen:
            continue
        seen.add(scheme)
        schemes.append(scheme)
    return schemes


# The capture active in the current context. Apprise 2 delivers on its own worker
# threads but runs them in a copy of the caller's context, so a context variable
# (unlike the thread id) still tells this dispatch's records apart from those of
# a dispatch running concurrently on another thread.
_active_apprise_capture: ContextVar[_AppriseLogCapture | None] = ContextVar(
    "active_apprise_capture", default=None
)


class _AppriseLogCapture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.records: list[tuple[int, str, str, str]] = []

    def emit(self, record: logging.LogRecord) -> None:
        if _active_apprise_capture.get() is not self:
            return

        message = record.getMessage()
        if message:
            exception_summary = ""
            if record.exc_info and record.exc_info[0]:
                exc_type = getattr(record.exc_info[0], "__name__", "Exception")
                exc = record.exc_info[1]
                exception_summary = f"{exc_type}: {exc}"
            elif record.exc_text:
                exception_summary = str(record.exc_text).strip()

            self.records.append((record.levelno, record.name, str(message), exception_summary))


@contextmanager
def _capture_apprise_logs(
    *, min_level: int = logging.INFO
) -> Iterator[list[tuple[int, str, str, str]]]:
    apprise_logger = logging.getLogger(_APPRISE_LOGGER_NAME)
    previous_level = apprise_logger.level
    handler = _AppriseLogCapture()
    token = _active_apprise_capture.set(handler)
    apprise_logger.addHandler(handler)

    if previous_level == logging.NOTSET or previous_level > min_level:
        apprise_logger.setLevel(min_level)

    try:
        yield handler.records
    finally:
        apprise_logger.removeHandler(handler)
        apprise_logger.setLevel(previous_level)
        _active_apprise_capture.reset(token)


def _log_apprise_records(records: Iterable[tuple[int, str, str, str]]) -> None:
    seen: set[tuple[int, str, str, str]] = set()
    for level, source, raw_message, raw_exception_summary in records:
        message = str(raw_message or "").strip()
        source_name = str(source or "").strip() or _APPRISE_LOGGER_NAME
        exception_summary = str(raw_exception_summary or "").strip()
        key = (int(level), source_name, message, exception_summary)
        if not message or key in seen:
            continue
        seen.add(key)

        full_message = message if not exception_summary else f"{message} ({exception_summary})"

        if level >= logging.ERROR:
            logger.error("Apprise source [%s]: %s", source_name, full_message)
        elif level >= logging.WARNING:
            logger.warning("Apprise source [%s]: %s", source_name, full_message)
        else:
            logger.info("Apprise source [%s]: %s", source_name, full_message)


def _log_apprise_exception_debug(*, action: str, scheme: str, exc: Exception) -> None:
    logger.debug(
        "Apprise %s raised %s for scheme '%s': %s",
        action,
        type(exc).__name__,
        scheme,
        exc,
        exc_info=(type(exc), exc, exc.__traceback__),
    )


def _build_apprise_warning_detail(
    records: Iterable[tuple[int, str, str, str]],
    *,
    scheme: str,
) -> str | None:
    for level, source, raw_message, raw_exception_summary in records:
        if level < logging.WARNING:
            continue

        message = str(raw_message or "").strip()
        if not message:
            continue

        source_name = str(source or "").strip()
        exception_summary = str(raw_exception_summary or "").strip()
        full_message = message if not exception_summary else f"{message} ({exception_summary})"

        if source_name and source_name != _APPRISE_LOGGER_NAME:
            return f"{scheme}: {source_name}: {full_message}"
        return f"{scheme}: {full_message}"
    return None


def _normalize_routes(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []

    allowed_events = {_ROUTE_EVENT_ALL, *(event.value for event in NotificationEvent)}
    normalized: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for row in value:
        if not isinstance(row, dict):
            continue

        raw_events = row.get("event")
        if isinstance(raw_events, list):
            event_values = raw_events
        elif isinstance(raw_events, (tuple, set)):
            event_values = list(raw_events)
        else:
            event_values = [raw_events]

        url = str(row.get("url") or "").strip()
        if not url:
            continue

        row_events: list[str] = []
        for raw_event in event_values:
            event = str(raw_event or "").strip().lower()
            if event not in allowed_events:
                continue
            if event in row_events:
                continue
            row_events.append(event)

        if _ROUTE_EVENT_ALL in row_events:
            row_events = [_ROUTE_EVENT_ALL]

        for event in row_events:
            key = (event, url)
            if key in seen:
                continue
            seen.add(key)

            normalized.append({"event": event, "url": url})

    return normalized


def _resolve_admin_routes() -> list[dict[str, str]]:
    return _normalize_routes(app_config.get("ADMIN_NOTIFICATION_ROUTES", []))


def _normalize_user_id(value: object) -> int | None:
    return normalize_positive_int(value)


# A user's own routes are sent from the server, so they must not be able to reach hosts the user
# could not reach themselves. These services always talk to the vendor's own host, whatever the
# rest of the URL says.
_USER_ROUTE_FIXED_HOST_SCHEMES = frozenset({"discord", "slack", "tgram", "pover", "pbul"})
# These connect to the host written in the URL, so that host has to be a public one.
_USER_ROUTE_HOST_SCHEMES = frozenset({"ntfy", "ntfys", "gotify", "gotifys"})


def _is_public_address(address: str) -> bool:
    try:
        parsed = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped is not None:
        parsed = parsed.ipv4_mapped
    return parsed.is_global and not parsed.is_multicast


def _host_is_public(host: str) -> bool:
    """Whether every address the host resolves to is a public one. A host that does not resolve
    has nothing to connect to, so it passes."""
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return True
    return all(_is_public_address(str(info[4][0])) for info in infos)


def _apprise_host(url: str) -> str | None:
    """The host Apprise itself reads from the URL, which can differ from urlsplit's reading."""
    if apprise is None:
        return None
    try:
        plugin = apprise.Apprise.instantiate(url)
    except Exception:  # noqa: BLE001 - an unparsable URL is judged on urlsplit alone
        return None
    host = getattr(plugin, "host", None)
    return host if isinstance(host, str) and host else None


def user_route_url_problem(url: str) -> str | None:
    """Why a user may not use this URL for a personal route, or None when it is allowed."""
    scheme = urlsplit(url).scheme.lower()
    if scheme in _USER_ROUTE_FIXED_HOST_SCHEMES:
        return None
    if scheme not in _USER_ROUTE_HOST_SCHEMES:
        return f"'{scheme or url}' is not available for personal notification routes"
    hosts = {urlsplit(url).hostname, _apprise_host(url)} - {None}
    for host in hosts:
        if not _host_is_public(str(host)):
            return f"'{host}' is not a public address"
    return None


def _allowed_user_urls(urls: list[str]) -> list[str]:
    """The URLs a user's routes may send to; the rest are logged and dropped."""
    allowed: list[str] = []
    for url in urls:
        problem = user_route_url_problem(url)
        if problem is None:
            allowed.append(url)
        else:
            logger.warning("Dropping a personal notification route: %s", problem)
    return allowed


def _resolve_user_routes(user_id: int | None) -> tuple[list[dict[str, str]], bool]:
    """The user's routes, and whether they are the inherited admin default.

    Admin-set routes are trusted like the global ones, so they skip the personal-route checks.
    Both come from one config read, so a settings save in between cannot pair the user's own
    routes with the admin default's trust.
    """
    normalized_user_id = _normalize_user_id(user_id)
    if normalized_user_id is None:
        return [], False

    value, is_own = app_config.get_for_user(
        "USER_NOTIFICATION_ROUTES", [], user_id=normalized_user_id
    )
    return _normalize_routes(value), not is_own


def _get_route_user(user_id: int) -> dict[str, Any] | None:
    try:
        from shelfmark.core.user_db import UserDB, get_users_db_path

        return UserDB(get_users_db_path()).get_user(user_id=user_id)
    except (ImportError, OSError, sqlite3.Error) as exc:
        logger.warning("Failed to look up user_id=%s for notification routes: %s", user_id, exc)
        return None


def expand_user_route_placeholders(urls: list[str], user_id: int) -> list[str]:
    """Expand `{User}` and `{Email}` in per-user route URLs from the user's account.

    Placeholders are only filled in the path, query or fragment: a URL with one in its host part
    is dropped, because Apprise decodes percent-escapes there (so an email such as
    ``x%40127.0.0.1`` would still reach 127.0.0.1). Values are fully percent-encoded, and a
    value containing whitespace or any of ``, ; / \\ "`` is ignored, since Apprise would split it
    into several targets, and so is a value made only of dots, which would change the URL path.
    URLs that need a value the user does not have are dropped.
    Callers should still validate the returned URLs, not the templates.
    """
    if not any(_USER_ROUTE_PLACEHOLDER_PATTERN.search(url) for url in urls):
        return urls

    user = _get_route_user(user_id) or {}
    values: dict[str, str] = {}
    for name, field in (("user", "username"), ("email", "email")):
        value = str(user.get(field) or "").strip()
        if value and (
            _UNSAFE_PLACEHOLDER_VALUE.search(value)
            or _DOTS_ONLY_PLACEHOLDER_VALUE.fullmatch(value)
            or (name == "email" and not is_plain_email_address(value))
        ):
            logger.info(
                "Ignoring the %s on file for user_id=%s in notification routes: it could "
                "split into several targets or change the URL path",
                field,
                user_id,
            )
            value = ""
        values[name] = value

    expanded: list[str] = []
    for url in urls:
        if _USER_ROUTE_PLACEHOLDER_PATTERN.search(urlsplit(url).netloc):
            logger.warning(
                "Skipping notification route for user_id=%s: {User}/{Email} is not allowed "
                "in the host part of a URL",
                user_id,
            )
            continue
        needed = {match.lower() for match in _USER_ROUTE_PLACEHOLDER_PATTERN.findall(url)}
        missing = sorted(name for name in needed if not values[name])
        if missing:
            logger.info(
                "Skipping notification route for user_id=%s: no %s on file",
                user_id,
                ", ".join(missing),
            )
            continue
        expanded.append(
            _USER_ROUTE_PLACEHOLDER_PATTERN.sub(
                lambda match: quote(values[match.group(1).lower()], safe=""), url
            )
        )
    return expanded


def _resolve_route_urls_for_event(
    routes: list[dict[str, str]],
    event: NotificationEvent,
) -> list[str]:
    selected: list[str] = []
    seen: set[str] = set()
    event_value = event.value

    for row in routes:
        row_event = row.get("event", "")
        if row_event not in {_ROUTE_EVENT_ALL, event_value}:
            continue

        url = row.get("url", "")
        if not url or url in seen:
            continue

        seen.add(url)
        selected.append(url)

    return selected


def _resolve_notify_type(event: NotificationEvent) -> object:
    if apprise is None:
        fallback = {
            NotificationEvent.REQUEST_CREATED: "info",
            NotificationEvent.REQUEST_FULFILLED: "success",
            NotificationEvent.REQUEST_REJECTED: "warning",
            NotificationEvent.DOWNLOAD_COMPLETE: "success",
            NotificationEvent.DOWNLOAD_FAILED: "failure",
        }
        return fallback[event]

    mapping = {
        NotificationEvent.REQUEST_CREATED: apprise.NotifyType.INFO,
        NotificationEvent.REQUEST_FULFILLED: apprise.NotifyType.SUCCESS,
        NotificationEvent.REQUEST_REJECTED: apprise.NotifyType.WARNING,
        NotificationEvent.DOWNLOAD_COMPLETE: apprise.NotifyType.SUCCESS,
        NotificationEvent.DOWNLOAD_FAILED: apprise.NotifyType.FAILURE,
    }
    return mapping[event]


def _clean_text(value: object, fallback: str) -> str:
    text = str(value or "").strip()
    return text or fallback


def _render_message(context: NotificationContext) -> tuple[str, str]:
    event = context.event
    title = _clean_text(context.title, "Unknown title")
    author = _clean_text(context.author, "Unknown author")
    username = _clean_text(context.username, "A user")

    if event == NotificationEvent.REQUEST_CREATED:
        return "New Request", f'{username} requested "{title}" by {author}'
    if event == NotificationEvent.REQUEST_FULFILLED:
        return "Request Approved", f'Request for "{title}" by {author} was approved.'
    if event == NotificationEvent.REQUEST_REJECTED:
        note = _clean_text(context.admin_note, "")
        note_line = f"\nNote: {note}" if note else ""
        return (
            "Request Rejected",
            f'Request for "{title}" by {author} was rejected.{note_line}',
        )
    if event == NotificationEvent.DOWNLOAD_COMPLETE:
        return "Download Complete", f'"{title}" by {author} downloaded successfully.'

    error_message = _clean_text(context.error_message, "")
    error_line = f"\nError: {error_message}" if error_message else ""
    return "Download Failed", f'Failed to download "{title}" by {author}.{error_line}'


def _plugin_label(plugin: object, fallback_scheme: str) -> str:
    """Build a human-readable label from a validated Apprise plugin.

    Combines the URL scheme with the plugin's service name (app_id) and
    privacy-safe URL for richer diagnostics, e.g.
    ``"slack (Slack - slack://TokenA/To...n/To...n/)"``
    """
    parts: list[str] = [fallback_scheme]

    app_id = getattr(plugin, "app_id", None)
    if app_id and str(app_id) != fallback_scheme:
        privacy_url: str | None = None
        if _has_plugin_url(plugin):
            with suppress(Exception):
                privacy_url = plugin.url(privacy=True)

        suffix = str(app_id)
        if privacy_url:
            suffix = f"{suffix} - {privacy_url}"
        parts.append(f"({suffix})")

    return " ".join(parts)


def _apprise_proxy_env() -> dict[str, str]:
    """Build proxy env vars from app config so Apprise respects the proxy setting."""
    import os

    from shelfmark.core.config import config as _cfg

    mode = str(_cfg.get("PROXY_MODE", "") or "").lower()
    env: dict[str, str] = {}

    if mode == "http":
        http = str(_cfg.get("HTTP_PROXY", "") or "").strip()
        https = str(_cfg.get("HTTPS_PROXY", "") or "").strip() or http
        if http:
            env["HTTP_PROXY"] = http
            env["http_proxy"] = http
        if https:
            env["HTTPS_PROXY"] = https
            env["https_proxy"] = https
    elif mode == "socks5":
        socks = str(_cfg.get("SOCKS5_PROXY", "") or "").strip()
        if socks:
            env["HTTP_PROXY"] = socks
            env["http_proxy"] = socks
            env["HTTPS_PROXY"] = socks
            env["https_proxy"] = socks

    no_proxy = str(_cfg.get("NO_PROXY", "") or "").strip()
    if no_proxy and env:
        env["NO_PROXY"] = no_proxy
        env["no_proxy"] = no_proxy

    # Don't override if the user already set these in the environment directly
    return {k: v for k, v in env.items() if not os.environ.get(k)}


def _dispatch_to_apprise(
    urls: Iterable[str],
    *,
    title: str,
    body: str,
    notify_type: object,
) -> dict[str, Any]:
    import os

    normalized_urls = _normalize_urls(list(urls))
    url_schemes = _extract_url_schemes(normalized_urls)
    if not normalized_urls:
        return {"success": False, "message": "No notification URLs configured"}

    if apprise is None:
        return {"success": False, "message": "Apprise is not installed"}

    proxy_env = _apprise_proxy_env()
    if proxy_env:
        logger.debug("Applying proxy env for Apprise dispatch: %s", list(proxy_env.keys()))
        os.environ.update(proxy_env)

    valid_urls = 0
    invalid_urls = 0
    delivered_urls = 0
    failed_delivery_urls = 0
    failure_details: list[str] = []

    for url in normalized_urls:
        scheme = urlsplit(url).scheme or "unknown"
        apobj = _create_apprise_client()
        if apobj is None:
            return {"success": False, "message": "Apprise is not installed"}

        registration_failure_detail: str | None = None
        with _capture_apprise_logs(min_level=logging.INFO) as apprise_records:
            try:
                plugin = apprise.Apprise.instantiate(url, asset=getattr(apobj, "asset", None))
            except _APPRISE_DISPATCH_ERRORS as exc:
                logger.warning(
                    "Failed to register notification route URL for scheme '%s': %s",
                    scheme,
                    exc,
                )
                _log_apprise_exception_debug(
                    action="route registration",
                    scheme=scheme,
                    exc=exc,
                )
                registration_failure_detail = (
                    f"{scheme}: route registration failed ({type(exc).__name__}: {exc})"
                )
                failure_details.append(registration_failure_detail)
                plugin = None

            if plugin is None:
                invalid_urls += 1
                logger.warning("Apprise rejected notification route URL for scheme '%s'", scheme)
                _log_apprise_records(apprise_records)
                warning_detail = _build_apprise_warning_detail(apprise_records, scheme=scheme)
                if warning_detail:
                    failure_details.append(warning_detail)
                elif registration_failure_detail is None:
                    failure_details.append(f"{scheme}: route URL rejected by Apprise")
                continue

            plugin_label = _plugin_label(plugin, scheme)
            apobj.add(plugin)
            valid_urls += 1

            try:
                delivered = bool(apobj.notify(title=title, body=body, notify_type=notify_type))
            except _APPRISE_DISPATCH_ERRORS as exc:
                _log_apprise_records(apprise_records)
                failed_delivery_urls += 1
                logger.warning(
                    "Apprise notify raised %s for %s: %s",
                    type(exc).__name__,
                    plugin_label,
                    exc,
                )
                _log_apprise_exception_debug(action="notify", scheme=scheme, exc=exc)
                warning_detail = _build_apprise_warning_detail(apprise_records, scheme=scheme)
                if warning_detail:
                    failure_details.append(warning_detail)
                else:
                    failure_details.append(f"{scheme}: notify raised {type(exc).__name__}: {exc}")
                continue

        _log_apprise_records(apprise_records)
        if delivered:
            delivered_urls += 1
            logger.debug("Notification delivered via %s", plugin_label)
            continue

        failed_delivery_urls += 1
        logger.warning("Apprise notify returned False for %s", plugin_label)
        warning_detail = _build_apprise_warning_detail(apprise_records, scheme=scheme)
        if warning_detail:
            failure_details.append(warning_detail)
        else:
            failure_details.append(f"{scheme}: delivery failed")

    scheme_summary = ", ".join(url_schemes) if url_schemes else "unknown"
    if valid_urls == 0:
        logger.warning(
            "No valid Apprise notification routes after registration for scheme(s): %s",
            scheme_summary,
        )
        result: dict[str, Any] = {
            "success": False,
            "message": "No valid notification URLs configured",
        }
        if failure_details:
            result["details"] = failure_details
        return result

    if delivered_urls == 0:
        logger.warning(
            (
                "Apprise notify returned False for scheme(s): %s "
                "(valid_urls=%s invalid_urls=%s failed_deliveries=%s)"
            ),
            scheme_summary,
            valid_urls,
            invalid_urls,
            failed_delivery_urls,
        )
        result = {"success": False, "message": "Notification delivery failed"}
        if failure_details:
            result["details"] = failure_details
        return result

    message = f"Notification sent to {delivered_urls} URL(s)"
    failed_urls = invalid_urls + failed_delivery_urls
    if failed_urls:
        message += f" ({failed_urls} URL(s) failed)"
    result = {"success": True, "message": message}
    if failure_details:
        result["details"] = failure_details
    return result


def _create_apprise_client() -> _AppriseClient | None:
    if apprise is None:
        return None

    apprise_cls = getattr(apprise, "Apprise", None)
    if apprise_cls is None:
        return None

    apprise_asset_cls = getattr(apprise, "AppriseAsset", None)
    if apprise_asset_cls is None:
        client = apprise_cls()
        return client if _is_apprise_client(client) else None

    try:
        asset = apprise_asset_cls(
            app_id=_APPRISE_APP_ID,
            app_desc=_APPRISE_APP_DESC,
            image_url_logo=_APPRISE_LOGO_URL,
        )
    except TypeError:
        # Support older Apprise versions that do not expose image_url_logo.
        try:
            asset = apprise_asset_cls(
                app_id=_APPRISE_APP_ID,
                app_desc=_APPRISE_APP_DESC,
            )
        except TypeError:
            client = apprise_cls()
            return client if _is_apprise_client(client) else None

    try:
        client = apprise_cls(asset=asset)
    except TypeError:
        client = apprise_cls()
    return client if _is_apprise_client(client) else None


def _send_admin_event(
    event: NotificationEvent, context: NotificationContext, urls: list[str]
) -> dict[str, Any]:
    title, body = _render_message(context)
    notify_type = _resolve_notify_type(event)
    return _dispatch_to_apprise(urls, title=title, body=body, notify_type=notify_type)


def notify_admin(event: NotificationEvent, context: NotificationContext) -> None:
    """Send a global admin notification for an event if subscribed."""
    routes = _resolve_admin_routes()
    urls = _resolve_route_urls_for_event(routes, event)
    if not urls:
        return

    try:
        _executor.submit(_dispatch_admin_async, event, context, urls)
    except RuntimeError as exc:
        logger.warning("Failed to queue admin notification '%s': %s", event.value, exc)


def notify_user(
    user_id: int | None, event: NotificationEvent, context: NotificationContext
) -> None:
    """Send a per-user notification for an event if subscribed."""
    normalized_user_id = _normalize_user_id(user_id)
    if normalized_user_id is None:
        return

    routes, admin_default = _resolve_user_routes(normalized_user_id)
    urls = expand_user_route_placeholders(
        _resolve_route_urls_for_event(routes, event), normalized_user_id
    )
    if not admin_default:
        urls = _allowed_user_urls(urls)
    if not urls:
        return

    try:
        _executor.submit(
            _dispatch_user_async,
            normalized_user_id,
            event,
            context,
            urls,
            admin_default=admin_default,
        )
    except RuntimeError as exc:
        logger.warning(
            "Failed to queue user notification '%s' for user_id=%s: %s",
            event.value,
            normalized_user_id,
            exc,
        )


def _dispatch_admin_async(
    event: NotificationEvent, context: NotificationContext, urls: list[str]
) -> None:
    result = _send_admin_event(event, context, urls)
    if not result.get("success", False):
        logger.warning(
            "Admin notification failed for event '%s': %s",
            event.value,
            result.get("message"),
        )


def _dispatch_user_async(
    user_id: int,
    event: NotificationEvent,
    context: NotificationContext,
    urls: list[str],
    *,
    admin_default: bool = False,
) -> None:
    # Checked again at send time: the host may resolve differently than when it was queued.
    if not admin_default:
        urls = _allowed_user_urls(urls)
    if not urls:
        return
    result = _send_admin_event(event, context, urls)
    if not result.get("success", False):
        logger.warning(
            "User notification failed for event '%s' (user_id=%s): %s",
            event.value,
            user_id,
            result.get("message"),
        )


def send_test_notification(urls: list[str]) -> dict[str, Any]:
    """Send a synchronous test notification to the provided URLs."""
    normalized_urls = _normalize_urls(urls)
    if not normalized_urls:
        return {"success": False, "message": "No notification URLs configured"}

    test_context = NotificationContext(
        event=NotificationEvent.REQUEST_CREATED,
        title="Shelfmark Test Notification",
        author="Shelfmark",
        username="Shelfmark",
    )
    return _send_admin_event(NotificationEvent.REQUEST_CREATED, test_context, normalized_urls)
