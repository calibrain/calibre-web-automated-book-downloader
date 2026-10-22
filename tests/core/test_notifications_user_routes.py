"""A user's own notification routes may not reach private or local hosts."""

from __future__ import annotations

import os
import socket
import tempfile
from unittest.mock import patch

import pytest
from flask import Flask

from shelfmark.core import notifications
from shelfmark.core.notifications import (
    NotificationContext,
    NotificationEvent,
    user_route_url_problem,
)
from shelfmark.core.self_user_routes import register_self_user_routes
from shelfmark.core.user_db import UserDB


def _resolves(*addresses: str):
    def _getaddrinfo(host, port, *args, **kwargs):
        del host, port, args, kwargs
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (addr, 0)) for addr in addresses]

    return patch("shelfmark.core.notifications.socket.getaddrinfo", side_effect=_getaddrinfo)


class TestRouteUrlProblem:
    @pytest.mark.parametrize(
        "url",
        [
            "discord://1234567890/abcdefghijk",
            "slack://T1JJ3T3L2/A1BRTD4JD/TIiajkdnlazkcOXrIdevi7FQ/",
            "tgram://123456:abcdef/12345",
            "pover://user@token",
            "pbul://accesstoken",
        ],
    )
    def test_hosted_services_are_allowed(self, url):
        assert user_route_url_problem(url) is None

    @pytest.mark.parametrize(
        "url",
        [
            "json://127.0.0.1:8080/x",
            "jsons://example.com/x",
            "form://example.com/x",
            "xml://example.com/x",
            "https://example.com/hook",
            "mailto://user:pass@example.com",
            "syslog://",
            "file:///tmp/notes.txt",
            "script://run",
        ],
    )
    def test_generic_and_local_schemes_are_refused(self, url):
        assert user_route_url_problem(url) is not None

    @pytest.mark.parametrize(
        "url",
        [
            "ntfy://127.0.0.1/topic",
            "ntfy://10.10.10.5:8080/topic",
            "ntfys://192.168.1.2/topic",
            "ntfy://169.254.169.254/topic",
            "ntfy://100.64.0.1/topic",
            "ntfy://user:pass@172.16.0.9/topic",
            "gotify://localhost/token",
            "ntfy://[::1]/topic",
            "ntfy://[::ffff:127.0.0.1]/topic",
            "ntfy://2130706433/topic",
        ],
    )
    def test_a_private_or_local_host_is_refused(self, url):
        assert user_route_url_problem(url) is not None

    def test_a_public_host_is_allowed(self):
        with _resolves("93.184.216.34"):
            assert user_route_url_problem("ntfys://ntfy.example.com/topic") is None

    def test_a_name_that_resolves_to_a_private_address_is_refused(self):
        with _resolves("10.0.0.7"):
            assert user_route_url_problem("ntfys://ntfy.example.com/topic") is not None

    def test_one_private_answer_among_public_ones_is_refused(self):
        with _resolves("93.184.216.34", "127.0.0.1"):
            assert user_route_url_problem("ntfys://ntfy.example.com/topic") is not None

    def test_a_name_that_does_not_resolve_has_nothing_to_connect_to(self):
        with patch("shelfmark.core.notifications.socket.getaddrinfo", side_effect=OSError):
            assert user_route_url_problem("ntfys://nowhere.invalid/topic") is None


class TestSending:
    def _context(self):
        return NotificationContext(
            event=NotificationEvent.REQUEST_CREATED, title="T", author="A", username="u"
        )

    def test_a_refused_route_is_not_queued(self):
        routes = [
            {"event": "all", "url": "json://127.0.0.1:9/x"},
            {"event": "all", "url": "discord://1234567890/abcdefghijk"},
        ]
        with (
            patch.object(notifications, "_resolve_user_routes", return_value=(routes, False)),
            patch.object(notifications._executor, "submit") as submit,
        ):
            notifications.notify_user(5, NotificationEvent.REQUEST_CREATED, self._context())

        queued_urls = submit.call_args.args[-1]
        assert queued_urls == ["discord://1234567890/abcdefghijk"]

    def test_nothing_is_queued_when_every_route_is_refused(self):
        routes = [{"event": "all", "url": "json://127.0.0.1:9/x"}]
        with (
            patch.object(notifications, "_resolve_user_routes", return_value=(routes, False)),
            patch.object(notifications._executor, "submit") as submit,
        ):
            notifications.notify_user(5, NotificationEvent.REQUEST_CREATED, self._context())

        submit.assert_not_called()

    def test_the_admin_default_is_not_restricted(self):
        routes = [{"event": "all", "url": "mailtos://user:pass@10.0.0.5?to=a@example.com"}]
        with (
            patch.object(notifications, "_resolve_user_routes", return_value=(routes, True)),
            patch.object(notifications._executor, "submit") as submit,
        ):
            notifications.notify_user(5, NotificationEvent.REQUEST_CREATED, self._context())

        assert submit.call_args.args[-1] == ["mailtos://user:pass@10.0.0.5?to=a@example.com"]
        assert submit.call_args.kwargs == {"admin_default": True}

    def test_the_send_trusts_the_admin_default(self):
        with patch.object(notifications, "_send_admin_event") as send:
            notifications._dispatch_user_async(
                5,
                NotificationEvent.REQUEST_CREATED,
                self._context(),
                ["json://10.0.0.5:8080/hook"],
                admin_default=True,
            )

        assert send.call_args.args[2] == ["json://10.0.0.5:8080/hook"]

    def test_the_send_checks_again(self):
        with patch.object(notifications, "_send_admin_event") as send:
            notifications._dispatch_user_async(
                5,
                NotificationEvent.REQUEST_CREATED,
                self._context(),
                ["json://127.0.0.1:9/x", "discord://1234567890/abcdefghijk"],
            )

        assert send.call_args.args[2] == ["discord://1234567890/abcdefghijk"]

    def test_admin_routes_are_not_restricted(self):
        with (
            patch.object(
                notifications,
                "_resolve_admin_routes",
                return_value=[{"event": "all", "url": "json://10.0.0.5:8080/hook"}],
            ),
            patch.object(notifications._executor, "submit") as submit,
        ):
            notifications.notify_admin(NotificationEvent.REQUEST_CREATED, self._context())

        assert submit.call_args.args[-1] == ["json://10.0.0.5:8080/hook"]


@pytest.fixture
def client():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = UserDB(os.path.join(tmpdir, "shelfmark.db"))
        db.initialize()
        app = Flask(__name__)
        app.config["SECRET_KEY"] = "test-secret"
        app.config["TESTING"] = True
        register_self_user_routes(app, db)
        user = db.create_user(username="alice")
        test_client = app.test_client()
        with test_client.session_transaction() as sess:
            sess["user_id"] = "alice"
            sess["db_user_id"] = user["id"]
            sess["is_admin"] = False
        yield test_client


class TestTestEndpoint:
    URL = "/api/users/me/notification-preferences/test"

    def test_a_local_target_is_refused_and_nothing_is_sent(self, client):
        with patch("shelfmark.config.notifications_settings.send_test_notification") as send:
            resp = client.post(
                self.URL, json={"routes": [{"event": "all", "url": "json://127.0.0.1:9/x"}]}
            )

        assert resp.status_code == 400
        assert "refused" in resp.json["message"]
        send.assert_not_called()

    def test_a_private_host_on_an_allowed_scheme_is_refused(self, client):
        with patch("shelfmark.config.notifications_settings.send_test_notification") as send:
            resp = client.post(
                self.URL, json={"routes": [{"event": "all", "url": "ntfy://10.0.0.5/topic"}]}
            )

        assert resp.status_code == 400
        send.assert_not_called()

    def test_a_hosted_service_is_still_tested(self, client):
        with patch(
            "shelfmark.config.notifications_settings.send_test_notification",
            return_value={"success": True, "message": "ok"},
        ) as send:
            resp = client.post(
                self.URL,
                json={"routes": [{"event": "all", "url": "discord://1234567890/abcdefghijk"}]},
            )

        assert resp.status_code == 200
        send.assert_called_once()

    DEFAULT_ROUTE = "mailtos://user:pass@10.0.0.5?to=a@example.com"

    def _inherit_admin_default(self):
        from shelfmark.core.config import config as app_config

        real_get_for_user = app_config.get_for_user

        def _get_for_user(key, default=None, user_id=None):
            if key == "USER_NOTIFICATION_ROUTES":
                return [{"event": "all", "url": self.DEFAULT_ROUTE}], False
            return real_get_for_user(key, default, user_id=user_id)

        return patch.object(app_config, "get_for_user", side_effect=_get_for_user)

    # The settings screens always post the routes they show: the inherited default.
    @pytest.mark.parametrize("body", [{"routes": [{"event": ["all"], "url": DEFAULT_ROUTE}]}, {}])
    def test_the_inherited_admin_default_is_tested_without_the_personal_checks(self, client, body):
        with (
            self._inherit_admin_default(),
            patch(
                "shelfmark.config.notifications_settings.send_test_notification",
                return_value={"success": True, "message": "ok"},
            ) as send,
        ):
            resp = client.post(self.URL, json=body)

        assert resp.status_code == 200
        send.assert_called_once_with([self.DEFAULT_ROUTE])

    def test_edited_routes_are_checked_even_while_inheriting_the_default(self, client):
        edited = "mailtos://user:pass@10.0.0.5?to=b@example.com"
        with (
            self._inherit_admin_default(),
            patch("shelfmark.config.notifications_settings.send_test_notification") as send,
        ):
            resp = client.post(self.URL, json={"routes": [{"event": "all", "url": edited}]})

        assert resp.status_code == 400
        assert "refused" in resp.json["message"]
        send.assert_not_called()
