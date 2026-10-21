"""Socket.IO handshakes are limited to the app's own origin (plus configured extras)."""

from __future__ import annotations

from flask import Flask
from flask_socketio import SocketIO

from shelfmark.core.socketio_origins import build_cors_allowed_origins

_HANDSHAKE = "/socket.io/?EIO=4&transport=polling"


def _client(extra: list[str] | None = None):
    app = Flask(__name__)
    SocketIO(
        app,
        async_mode="threading",
        cors_allowed_origins=build_cors_allowed_origins(extra or []),
    )
    return app.test_client()


def _handshake(client, origin: str | None, **headers: str) -> int:
    request_headers = dict(headers)
    if origin is not None:
        request_headers["Origin"] = origin
    return client.get(
        _HANDSHAKE, headers=request_headers, base_url="http://shelf.example"
    ).status_code


def test_same_origin_handshake_is_accepted():
    assert _handshake(_client(), "http://shelf.example") == 200


def test_foreign_origin_handshake_is_refused():
    assert _handshake(_client(), "https://evil.example") == 400


def test_sibling_subdomain_is_refused():
    assert _handshake(_client(), "http://other.example") == 400


def test_non_browser_client_without_origin_is_accepted():
    assert _handshake(_client(), None) == 200


def test_forwarded_proto_and_host_origin_is_accepted():
    status = _handshake(
        _client(),
        "https://books.example.org",
        **{"X-Forwarded-Proto": "https", "X-Forwarded-Host": "books.example.org"},
    )
    assert status == 200


def test_https_origin_without_forwarded_headers_is_refused_by_default():
    assert _handshake(_client(), "https://shelf.example") == 400


def test_extra_origin_is_accepted_and_others_still_refused():
    client = _client(["https://shelf.example"])
    assert _handshake(client, "https://shelf.example") == 200
    assert _handshake(client, "http://shelf.example") == 200
    assert _handshake(client, "https://evil.example") == 400


def test_default_is_the_library_same_origin_check():
    assert build_cors_allowed_origins([]) is None


def test_app_does_not_allow_any_origin():
    from shelfmark import main as main_module

    assert main_module.socketio.server.eio.cors_allowed_origins is None
