"""Tests for core notification rendering and dispatch helpers."""

import contextvars
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import apprise
import pytest

from shelfmark.core import notifications as notifications_module


class _FakeExecutor:
    def __init__(self):
        self.calls = []

    def submit(self, fn, *args, **kwargs):
        self.calls.append((fn, args, kwargs))
        return object()


class _FakeNotifyType:
    INFO = "INFO"
    SUCCESS = "SUCCESS"
    WARNING = "WARNING"
    FAILURE = "FAILURE"


class _FakePlugin:
    """Fake Apprise plugin returned by instantiate()."""

    def __init__(self, raw_url: str):
        self._url = raw_url
        self.app_id = "FakePlugin"

    def url(self, privacy=False):
        return self._url


class _FakeAppriseClient:
    def __init__(self):
        self.add_calls = []
        self.instantiate_calls: list[dict[str, object | None]] = []
        self.notify_calls = []
        self.notify_result = True
        self.notify_results_by_url: dict[str, bool] = {}
        self.reject_urls: set[str] = set()
        self.instantiate_exceptions_by_url: dict[str, Exception] = {}
        self.notify_exceptions_by_url: dict[str, Exception] = {}
        self.notify_warning_messages: list[str] = []
        self.notify_info_messages: list[str] = []
        self._active_url: str | None = None

    def add(self, plugin):
        url = getattr(plugin, "_url", str(plugin))
        self.add_calls.append(url)
        self._active_url = url
        return True

    def notify(self, **kwargs):
        self.notify_calls.append(kwargs)
        for message in self.notify_info_messages:
            logging.getLogger("apprise.plugins.pushover").info(message)
        for message in self.notify_warning_messages:
            logging.getLogger("apprise.plugins.pushover").warning(message)
        if self._active_url and self._active_url in self.notify_exceptions_by_url:
            raise self.notify_exceptions_by_url[self._active_url]
        if self._active_url and self._active_url in self.notify_results_by_url:
            return self.notify_results_by_url[self._active_url]
        return self.notify_result


class _FakeAppriseClass:
    """Fake for apprise.Apprise that acts as both constructor and has instantiate()."""

    def __init__(self, module):
        self._module = module

    def __call__(self, *args, **kwargs):
        self._module.apprise_kwargs = kwargs
        asset = kwargs.get("asset")
        self._module.asset_kwargs = getattr(asset, "kwargs", None)
        self._module.client.asset = asset
        return self._module.client

    def instantiate(self, url, asset=None, tag=None, suppress_exceptions=True):
        _ = (tag, suppress_exceptions)
        client = self._module.client
        client.instantiate_calls.append(
            {
                "url": url,
                "asset_kwargs": getattr(asset, "kwargs", None),
            }
        )
        if url in client.instantiate_exceptions_by_url:
            raise client.instantiate_exceptions_by_url[url]
        if url in client.reject_urls:
            return None
        return _FakePlugin(url)


class _FakeAppriseModule:
    NotifyType = _FakeNotifyType
    asset_kwargs: dict[str, str] | None = None

    def __init__(self):
        self.client = _FakeAppriseClient()
        self.apprise_kwargs = {}
        self.Apprise = _FakeAppriseClass(self)

    class AppriseAsset:
        def __init__(self, **kwargs):
            self.kwargs = kwargs


def test_render_message_includes_admin_note_for_rejection():
    context = notifications_module.NotificationContext(
        event=notifications_module.NotificationEvent.REQUEST_REJECTED,
        title="Example Book",
        author="Example Author",
        admin_note="Missing metadata",
    )

    title, body = notifications_module._render_message(context)

    assert title == "Request Rejected"
    assert "Missing metadata" in body


def test_render_message_includes_error_line_for_download_failure():
    context = notifications_module.NotificationContext(
        event=notifications_module.NotificationEvent.DOWNLOAD_FAILED,
        title="Example Book",
        author="Example Author",
        error_message="Connection timeout",
    )

    title, body = notifications_module._render_message(context)

    assert title == "Download Failed"
    assert "Connection timeout" in body


def test_render_message_uses_request_approved_copy():
    context = notifications_module.NotificationContext(
        event=notifications_module.NotificationEvent.REQUEST_FULFILLED,
        title="Example Book",
        author="Example Author",
    )

    title, body = notifications_module._render_message(context)

    assert title == "Request Approved"
    assert "was approved." in body


def test_notify_admin_submits_non_blocking_when_route_matches_event(monkeypatch):
    fake_executor = _FakeExecutor()
    monkeypatch.setattr(notifications_module, "_executor", fake_executor)
    monkeypatch.setattr(
        notifications_module,
        "_resolve_admin_routes",
        lambda: [{"event": "request_created", "url": "discord://Webhook/Token"}],
    )

    context = notifications_module.NotificationContext(
        event=notifications_module.NotificationEvent.REQUEST_CREATED,
        title="Example Book",
        author="Example Author",
        username="reader",
    )

    notifications_module.notify_admin(
        notifications_module.NotificationEvent.REQUEST_CREATED,
        context,
    )

    assert len(fake_executor.calls) == 1


def test_notify_admin_skips_when_no_route_matches_event(monkeypatch):
    fake_executor = _FakeExecutor()
    monkeypatch.setattr(notifications_module, "_executor", fake_executor)
    monkeypatch.setattr(
        notifications_module,
        "_resolve_admin_routes",
        lambda: [{"event": "download_failed", "url": "discord://Webhook/Token"}],
    )

    context = notifications_module.NotificationContext(
        event=notifications_module.NotificationEvent.REQUEST_CREATED,
        title="Example Book",
        author="Example Author",
    )

    notifications_module.notify_admin(
        notifications_module.NotificationEvent.REQUEST_CREATED,
        context,
    )

    assert fake_executor.calls == []


def test_send_admin_event_passes_expected_title_body_and_notify_type(monkeypatch):
    fake_apprise = _FakeAppriseModule()
    monkeypatch.setattr(notifications_module, "apprise", fake_apprise)

    context = notifications_module.NotificationContext(
        event=notifications_module.NotificationEvent.REQUEST_REJECTED,
        title="Example Book",
        author="Example Author",
        admin_note="Rule blocked this source",
    )

    result = notifications_module._send_admin_event(
        notifications_module.NotificationEvent.REQUEST_REJECTED,
        context,
        ["discord://Webhook/Token"],
    )

    assert result["success"] is True
    assert fake_apprise.client.notify_calls
    notify_kwargs = fake_apprise.client.notify_calls[0]
    assert notify_kwargs["title"] == "Request Rejected"
    assert "Rule blocked this source" in notify_kwargs["body"]
    assert notify_kwargs["notify_type"] == _FakeNotifyType.WARNING


def test_dispatch_to_apprise_uses_shelfmark_asset_defaults(monkeypatch):
    fake_apprise = _FakeAppriseModule()
    monkeypatch.setattr(notifications_module, "apprise", fake_apprise)

    result = notifications_module._dispatch_to_apprise(
        ["ntfys://ntfy.sh/shelfmark"],
        title="Test",
        body="Body",
        notify_type=_FakeNotifyType.INFO,
    )

    assert result["success"] is True
    assert fake_apprise.asset_kwargs is not None
    assert fake_apprise.asset_kwargs["app_id"] == "Shelfmark"
    assert "logo.png" in fake_apprise.asset_kwargs["image_url_logo"]


def test_dispatch_to_apprise_passes_shelfmark_asset_to_instantiate(monkeypatch):
    fake_apprise = _FakeAppriseModule()
    monkeypatch.setattr(notifications_module, "apprise", fake_apprise)

    result = notifications_module._dispatch_to_apprise(
        ["ntfys://ntfy.sh/shelfmark"],
        title="Test",
        body="Body",
        notify_type=_FakeNotifyType.INFO,
    )

    assert result["success"] is True
    assert fake_apprise.client.instantiate_calls
    instantiate_call = fake_apprise.client.instantiate_calls[0]
    asset_kwargs = instantiate_call["asset_kwargs"]
    assert isinstance(asset_kwargs, dict)
    assert asset_kwargs["app_id"] == "Shelfmark"
    assert "logo.png" in asset_kwargs["image_url_logo"]


def test_dispatch_to_apprise_logs_captured_apprise_info_messages(monkeypatch):
    fake_apprise = _FakeAppriseModule()
    fake_apprise.client.notify_info_messages = ["Sent Pushover notification to ALL_DEVICES."]
    monkeypatch.setattr(notifications_module, "apprise", fake_apprise)

    info_messages: list[str] = []

    def _fake_info(message, *args, **kwargs):
        _ = kwargs
        info_messages.append(message % args if args else str(message))

    monkeypatch.setattr(notifications_module.logger, "info", _fake_info)

    result = notifications_module._dispatch_to_apprise(
        ["ntfys://ntfy.sh/shelfmark"],
        title="Test",
        body="Body",
        notify_type=_FakeNotifyType.INFO,
    )

    assert result["success"] is True
    assert any(
        "Apprise source [apprise.plugins.pushover]: Sent Pushover notification to ALL_DEVICES."
        in message
        for message in info_messages
    )


def test_dispatch_to_apprise_notify_false_returns_generic_failure_and_logs(monkeypatch):
    fake_apprise = _FakeAppriseModule()
    fake_apprise.client.notify_result = False
    monkeypatch.setattr(notifications_module, "apprise", fake_apprise)

    warning_messages: list[str] = []

    def _fake_warning(message, *args, **kwargs):
        _ = kwargs
        warning_messages.append(message % args if args else str(message))

    monkeypatch.setattr(notifications_module.logger, "warning", _fake_warning)

    result = notifications_module._dispatch_to_apprise(
        ["pover://user_key@app_token"],
        title="Test",
        body="Body",
        notify_type=_FakeNotifyType.INFO,
    )

    assert result["success"] is False
    assert result["message"] == "Notification delivery failed"
    assert result["details"] == ["pover: delivery failed"]
    assert any("scheme(s): pover" in message for message in warning_messages)


def test_dispatch_to_apprise_partial_success_returns_success(monkeypatch):
    fake_apprise = _FakeAppriseModule()
    fake_apprise.client.notify_results_by_url = {
        "gotifys://gotify.example/token": False,
        "ntfys://ntfy.sh/shelfmark": True,
    }
    monkeypatch.setattr(notifications_module, "apprise", fake_apprise)

    result = notifications_module._dispatch_to_apprise(
        ["gotifys://gotify.example/token", "ntfys://ntfy.sh/shelfmark"],
        title="Test",
        body="Body",
        notify_type=_FakeNotifyType.INFO,
    )

    assert result["success"] is True
    assert result["message"] == "Notification sent to 1 URL(s) (1 URL(s) failed)"
    assert result["details"] == ["gotifys: delivery failed"]


def test_dispatch_to_apprise_logs_captured_apprise_warning_messages(monkeypatch):
    fake_apprise = _FakeAppriseModule()
    fake_apprise.client.notify_result = False
    fake_apprise.client.notify_warning_messages = [
        "Failed to send Pushover notification to ALL_DEVICES: Unauthorized - Invalid Token., error=401."
    ]
    monkeypatch.setattr(notifications_module, "apprise", fake_apprise)

    warning_messages: list[str] = []

    def _fake_warning(message, *args, **kwargs):
        _ = kwargs
        warning_messages.append(message % args if args else str(message))

    monkeypatch.setattr(notifications_module.logger, "warning", _fake_warning)

    result = notifications_module._dispatch_to_apprise(
        ["pover://user_key@app_token"],
        title="Test",
        body="Body",
        notify_type=_FakeNotifyType.INFO,
    )

    assert result["success"] is False
    assert any(
        "pover: apprise.plugins.pushover: Failed to send Pushover notification" in detail
        for detail in result.get("details", [])
    )
    assert any(
        "Apprise source [apprise.plugins.pushover]: Failed to send Pushover notification" in msg
        for msg in warning_messages
    )


class _ServerErrorHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        self.send_response(500)
        self.end_headers()

    def log_message(self, format, *args):  # noqa: A002
        return


def test_dispatch_to_apprise_reports_failure_logged_on_apprise_worker_thread():
    """Apprise 2 delivers on its own worker threads, which log the plugin's reason."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ServerErrorHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = notifications_module._dispatch_to_apprise(
            [f"json://127.0.0.1:{server.server_address[1]}/hook"],
            title="Test",
            body="Body",
            notify_type=apprise.NotifyType.INFO,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert result["success"] is False
    assert len(result["details"]) == 1
    assert result["details"][0].startswith("json: ")
    assert "error=500" in result["details"][0]


def test_dispatch_to_apprise_ignores_apprise_records_of_other_dispatches(monkeypatch):
    fake_apprise = _FakeAppriseModule()
    monkeypatch.setattr(notifications_module, "apprise", fake_apprise)

    def _log_warning(message):
        logging.getLogger("apprise.plugins.json").warning(message)

    def _notify(**kwargs):
        _ = kwargs
        # A dispatch running concurrently on another thread, in its own context.
        other = threading.Thread(
            target=_log_warning,
            args=("Another dispatch failed",),
            context=contextvars.Context(),
        )
        other.start()
        other.join()
        # Apprise's worker thread, running in a copy of this dispatch's context.
        worker = threading.Thread(
            target=contextvars.copy_context().run,
            args=(_log_warning, "This dispatch failed"),
        )
        worker.start()
        worker.join()
        return False

    monkeypatch.setattr(fake_apprise.client, "notify", _notify)

    result = notifications_module._dispatch_to_apprise(
        ["json://example.invalid/hook"],
        title="Test",
        body="Body",
        notify_type=_FakeNotifyType.INFO,
    )

    assert result["details"] == ["json: apprise.plugins.json: This dispatch failed"]


def test_dispatch_to_apprise_logs_add_exception_at_debug_with_trace(monkeypatch):
    fake_apprise = _FakeAppriseModule()
    fake_apprise.client.instantiate_exceptions_by_url = {
        "ntfys://ntfy.sh/shelfmark": RuntimeError("add exploded"),
    }
    monkeypatch.setattr(notifications_module, "apprise", fake_apprise)

    debug_calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    def _fake_debug(message, *args, **kwargs):
        debug_calls.append((str(message), args, kwargs))

    monkeypatch.setattr(notifications_module.logger, "debug", _fake_debug)

    result = notifications_module._dispatch_to_apprise(
        ["ntfys://ntfy.sh/shelfmark"],
        title="Test",
        body="Body",
        notify_type=_FakeNotifyType.INFO,
    )

    assert result["success"] is False
    assert result["message"] == "No valid notification URLs configured"
    assert result["details"] == ["ntfys: route registration failed (RuntimeError: add exploded)"]
    assert any(
        "Apprise route registration raised RuntimeError" in (message % args if args else message)
        and kwargs.get("exc_info")
        == (
            RuntimeError,
            fake_apprise.client.instantiate_exceptions_by_url["ntfys://ntfy.sh/shelfmark"],
            fake_apprise.client.instantiate_exceptions_by_url[
                "ntfys://ntfy.sh/shelfmark"
            ].__traceback__,
        )
        for message, args, kwargs in debug_calls
    )


def test_dispatch_to_apprise_logs_notify_exception_at_debug_with_trace(monkeypatch):
    fake_apprise = _FakeAppriseModule()
    fake_apprise.client.notify_exceptions_by_url = {
        "ntfys://ntfy.sh/shelfmark": RuntimeError("notify exploded"),
    }
    monkeypatch.setattr(notifications_module, "apprise", fake_apprise)

    debug_calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    def _fake_debug(message, *args, **kwargs):
        debug_calls.append((str(message), args, kwargs))

    monkeypatch.setattr(notifications_module.logger, "debug", _fake_debug)

    result = notifications_module._dispatch_to_apprise(
        ["ntfys://ntfy.sh/shelfmark"],
        title="Test",
        body="Body",
        notify_type=_FakeNotifyType.INFO,
    )

    assert result["success"] is False
    assert result["message"] == "Notification delivery failed"
    assert result["details"] == ["ntfys: notify raised RuntimeError: notify exploded"]
    assert any(
        "Apprise notify raised RuntimeError" in (message % args if args else message)
        and kwargs.get("exc_info")
        == (
            RuntimeError,
            fake_apprise.client.notify_exceptions_by_url["ntfys://ntfy.sh/shelfmark"],
            fake_apprise.client.notify_exceptions_by_url["ntfys://ntfy.sh/shelfmark"].__traceback__,
        )
        for message, args, kwargs in debug_calls
    )


def test_resolve_admin_routes_returns_empty_when_no_routes(monkeypatch):
    def _fake_get(key, default=None):
        if key == "ADMIN_NOTIFICATION_ROUTES":
            return []
        return default

    monkeypatch.setattr(notifications_module.app_config, "get", _fake_get)

    routes = notifications_module._resolve_admin_routes()

    assert routes == []


def test_resolve_user_routes_uses_user_overrides(monkeypatch):
    def _fake_get_for_user(key, default=None, user_id=None):
        if user_id != 42:
            return default, False
        values = {
            "USER_NOTIFICATION_ROUTES": [
                {"event": "all", "url": " ntfys://ntfy.sh/alice "},
                {"event": "download_failed", "url": "ntfys://ntfy.sh/errors"},
                {"event": "download_failed", "url": "ntfys://ntfy.sh/errors"},
            ],
        }
        return values.get(key, default), True

    monkeypatch.setattr(notifications_module.app_config, "get_for_user", _fake_get_for_user)

    routes, admin_default = notifications_module._resolve_user_routes(42)

    assert routes == [
        {"event": "all", "url": "ntfys://ntfy.sh/alice"},
        {"event": "download_failed", "url": "ntfys://ntfy.sh/errors"},
    ]
    assert admin_default is False


def test_notify_user_submits_non_blocking_when_route_matches_event(monkeypatch):
    fake_executor = _FakeExecutor()
    monkeypatch.setattr(notifications_module, "_executor", fake_executor)
    monkeypatch.setattr(
        notifications_module,
        "_resolve_user_routes",
        lambda _user_id: ([{"event": "download_failed", "url": "discord://Webhook/Token"}], False),
    )

    context = notifications_module.NotificationContext(
        event=notifications_module.NotificationEvent.DOWNLOAD_FAILED,
        title="Example Book",
        author="Example Author",
        username="reader",
    )

    notifications_module.notify_user(
        7,
        notifications_module.NotificationEvent.DOWNLOAD_FAILED,
        context,
    )

    assert len(fake_executor.calls) == 1


def test_notify_user_skips_when_user_id_is_invalid(monkeypatch):
    fake_executor = _FakeExecutor()
    monkeypatch.setattr(notifications_module, "_executor", fake_executor)

    context = notifications_module.NotificationContext(
        event=notifications_module.NotificationEvent.DOWNLOAD_COMPLETE,
        title="Example Book",
        author="Example Author",
    )

    notifications_module.notify_user(
        None,
        notifications_module.NotificationEvent.DOWNLOAD_COMPLETE,
        context,
    )

    assert fake_executor.calls == []


def test_resolve_route_urls_for_event_includes_all_and_specific_rows():
    routes = [
        {"event": "all", "url": "ntfys://ntfy.sh/all"},
        {"event": "download_failed", "url": "ntfys://ntfy.sh/errors"},
        {"event": "request_created", "url": "ntfys://ntfy.sh/requests"},
    ]

    urls = notifications_module._resolve_route_urls_for_event(
        routes,
        notifications_module.NotificationEvent.DOWNLOAD_FAILED,
    )

    assert urls == [
        "ntfys://ntfy.sh/all",
        "ntfys://ntfy.sh/errors",
    ]


def test_resolve_route_urls_for_event_deduplicates_matching_urls():
    routes = [
        {"event": "all", "url": "ntfys://ntfy.sh/shared"},
        {"event": "download_failed", "url": "ntfys://ntfy.sh/shared"},
        {"event": "download_failed", "url": "ntfys://ntfy.sh/errors"},
    ]

    urls = notifications_module._resolve_route_urls_for_event(
        routes,
        notifications_module.NotificationEvent.DOWNLOAD_FAILED,
    )

    assert urls == [
        "ntfys://ntfy.sh/shared",
        "ntfys://ntfy.sh/errors",
    ]


def test_resolve_admin_routes_expands_multiselect_event_rows(monkeypatch):
    def _fake_get(key, default=None):
        if key == "ADMIN_NOTIFICATION_ROUTES":
            return [
                {"event": ["request_created", "download_failed"], "url": "ntfys://ntfy.sh/multi"},
                {"event": ["all", "download_complete"], "url": "ntfys://ntfy.sh/all"},
            ]
        return default

    monkeypatch.setattr(notifications_module.app_config, "get", _fake_get)

    routes = notifications_module._resolve_admin_routes()

    assert routes == [
        {"event": "request_created", "url": "ntfys://ntfy.sh/multi"},
        {"event": "download_failed", "url": "ntfys://ntfy.sh/multi"},
        {"event": "all", "url": "ntfys://ntfy.sh/all"},
    ]


def test_resolve_user_routes_expands_multiselect_event_rows(monkeypatch):
    def _fake_get_for_user(key, default=None, user_id=None):
        if key != "USER_NOTIFICATION_ROUTES" or user_id != 7:
            return default, False
        return [
            {
                "event": ["download_complete", "request_fulfilled"],
                "url": "ntfys://ntfy.sh/user-main",
            },
            {"event": ["all", "download_failed"], "url": "ntfys://ntfy.sh/user-all"},
        ], True

    monkeypatch.setattr(notifications_module.app_config, "get_for_user", _fake_get_for_user)

    routes, _ = notifications_module._resolve_user_routes(7)

    assert routes == [
        {"event": "download_complete", "url": "ntfys://ntfy.sh/user-main"},
        {"event": "request_fulfilled", "url": "ntfys://ntfy.sh/user-main"},
        {"event": "all", "url": "ntfys://ntfy.sh/user-all"},
    ]


def test_expand_user_route_placeholders_fills_account_details(monkeypatch):
    monkeypatch.setattr(
        notifications_module,
        "_get_route_user",
        lambda user_id: {"username": "alice", "email": "alice+books@example.com"},
    )

    urls = notifications_module.expand_user_route_placeholders(
        [
            "mailtos://smtp.example.com?to={Email}",
            "ntfys://ntfy.sh/shelfmark-{user}",
            "ntfys://ntfy.sh/static",
        ],
        7,
    )

    assert urls == [
        "mailtos://smtp.example.com?to=alice%2Bbooks%40example.com",
        "ntfys://ntfy.sh/shelfmark-alice",
        "ntfys://ntfy.sh/static",
    ]


def test_expand_user_route_placeholders_refuses_placeholder_in_host(monkeypatch):
    # Apprise decodes %40 in the host, so x%40127.0.0.1 would still reach loopback.
    monkeypatch.setattr(
        notifications_module,
        "_get_route_user",
        lambda user_id: {"username": "mallory", "email": "x@127.0.0.1"},
    )

    urls = notifications_module.expand_user_route_placeholders(
        ["ntfys://{Email}/alerts", "ntfys://{User}.example.com/alerts", "ntfys://ntfy.sh/{User}"],
        7,
    )

    assert urls == ["ntfys://ntfy.sh/mallory"]


def test_notify_user_does_not_queue_placeholder_host_pointing_at_loopback(monkeypatch):
    fake_executor = _FakeExecutor()
    monkeypatch.setattr(notifications_module, "_executor", fake_executor)
    monkeypatch.setattr(
        notifications_module,
        "_resolve_user_routes",
        lambda _user_id: ([{"event": "all", "url": "ntfys://{Email}/alerts"}], False),
    )
    monkeypatch.setattr(
        notifications_module,
        "_get_route_user",
        lambda user_id: {"username": "mallory", "email": "x@127.0.0.1"},
    )

    context = notifications_module.NotificationContext(
        event=notifications_module.NotificationEvent.DOWNLOAD_FAILED,
        title="Example Book",
        author="Example Author",
        username="mallory",
    )
    notifications_module.notify_user(
        7, notifications_module.NotificationEvent.DOWNLOAD_FAILED, context
    )

    assert fake_executor.calls == []


@pytest.mark.parametrize(
    ("username", "email", "expected"),
    [
        # A list would let a user add recipients to an admin-set mailto route.
        ("mallory", "a@example.com,b@example.com", ["ntfys://ntfy.sh/mallory"]),
        # Valid syntax, but Apprise decodes %2C and sends to a@evil.com and b@evil.com.
        ("mallory", '"x,a@evil.com,b@evil.com"@example.com', ["ntfys://ntfy.sh/mallory"]),
        # Apprise would publish to two topics, "...-John" and "Smith".
        ("John Smith", "john@example.com", ["mailtos://smtp.example.com?to=john%40example.com"]),
        ("a/b", "x\\y@example.com", []),
        # A slash walks the topic path: this would publish to a topic called "admin".
        ("bob/../admin", "bob@example.com", ["mailtos://smtp.example.com?to=bob%40example.com"]),
        # A newline splits like any other whitespace.
        ("eve\nadmin", "eve@example.com\nb@example.com", []),
    ],
)
def test_expand_user_route_placeholders_ignores_values_apprise_would_split(
    monkeypatch, username, email, expected
):
    monkeypatch.setattr(
        notifications_module,
        "_get_route_user",
        lambda user_id: {"username": username, "email": email},
    )

    urls = notifications_module.expand_user_route_placeholders(
        ["mailtos://smtp.example.com?to={Email}", "ntfys://ntfy.sh/{User}"],
        7,
    )

    assert urls == expected


@pytest.mark.parametrize(
    ("username", "expected"),
    [
        # Left unencoded, ".." would turn /notify/.. into /, another path on the same host.
        (".", []),
        ("..", []),
        ("...", []),
        ("john.doe", ["json://hook.example/notify/john.doe"]),
        ("..john", ["json://hook.example/notify/..john"]),
    ],
)
def test_expand_user_route_placeholders_ignores_dots_only_values(monkeypatch, username, expected):
    monkeypatch.setattr(
        notifications_module,
        "_get_route_user",
        lambda user_id: {"username": username, "email": "user@example.com"},
    )

    urls = notifications_module.expand_user_route_placeholders(
        ["json://hook.example/notify/{User}"],
        7,
    )

    assert urls == expected


def test_expand_user_route_placeholders_drops_routes_missing_a_value(monkeypatch):
    monkeypatch.setattr(
        notifications_module,
        "_get_route_user",
        lambda user_id: {"username": "carol", "email": None},
    )

    urls = notifications_module.expand_user_route_placeholders(
        ["mailtos://smtp.example.com?to={Email}", "ntfys://ntfy.sh/{User}"],
        7,
    )

    assert urls == ["ntfys://ntfy.sh/carol"]


def test_expand_user_route_placeholders_skips_lookup_without_placeholders(monkeypatch):
    def _fail_lookup(user_id):
        raise AssertionError("user lookup should not run")

    monkeypatch.setattr(notifications_module, "_get_route_user", _fail_lookup)

    urls = ["ntfys://ntfy.sh/static"]
    assert notifications_module.expand_user_route_placeholders(urls, 7) == urls


class TestAppriseProxyEnv:
    """Regression tests for issue #956 — proxy settings ignored for notifications."""

    def _patch_config(self, monkeypatch, values):
        from shelfmark.core import config as config_module

        def _fake_get(key, default="", **_kwargs):
            return values.get(key, default)

        monkeypatch.setattr(config_module.config, "get", _fake_get)

    def test_http_proxy_mode_injects_proxy_env(self, monkeypatch):
        self._patch_config(
            monkeypatch,
            {
                "PROXY_MODE": "http",
                "HTTP_PROXY": "http://proxy.example.com:8080",
                "HTTPS_PROXY": "",
                "NO_PROXY": "",
            },
        )
        monkeypatch.delenv("HTTP_PROXY", raising=False)
        monkeypatch.delenv("HTTPS_PROXY", raising=False)

        result = notifications_module._apprise_proxy_env()

        assert result["HTTP_PROXY"] == "http://proxy.example.com:8080"
        assert result["HTTPS_PROXY"] == "http://proxy.example.com:8080"

    def test_socks5_proxy_mode_injects_socks_env(self, monkeypatch):
        self._patch_config(
            monkeypatch,
            {
                "PROXY_MODE": "socks5",
                "SOCKS5_PROXY": "socks5://proxy.example.com:1080",
                "NO_PROXY": "",
            },
        )
        monkeypatch.delenv("HTTP_PROXY", raising=False)
        monkeypatch.delenv("HTTPS_PROXY", raising=False)

        result = notifications_module._apprise_proxy_env()

        assert result["HTTP_PROXY"] == "socks5://proxy.example.com:1080"
        assert result["HTTPS_PROXY"] == "socks5://proxy.example.com:1080"

    def test_no_proxy_mode_returns_empty_dict(self, monkeypatch):
        self._patch_config(monkeypatch, {"PROXY_MODE": ""})

        result = notifications_module._apprise_proxy_env()

        assert result == {}

    def test_does_not_override_already_set_env_vars(self, monkeypatch):
        self._patch_config(
            monkeypatch,
            {
                "PROXY_MODE": "http",
                "HTTP_PROXY": "http://new-proxy.example.com:8080",
                "NO_PROXY": "",
            },
        )
        monkeypatch.setenv("HTTP_PROXY", "http://existing-proxy.example.com:3128")

        result = notifications_module._apprise_proxy_env()

        assert "HTTP_PROXY" not in result
