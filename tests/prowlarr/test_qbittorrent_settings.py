"""qBittorrent download client settings fields and test-connection callback."""

import types
from unittest.mock import patch

import pytest
from qbittorrentapi import LoginFailed

from shelfmark.core.settings_registry import PasswordField

API_KEY = "qbt_0123456789abcdefghijklmnopqr"


def make_config_getter(values):
    """Create a config.get function that returns values from a dict."""

    def getter(key, default=""):
        return values.get(key, default)

    return getter


def _get_field(fields, key):
    """Find a field by key."""
    return next((f for f in fields if f.key == key), None)


def fake_qbittorrentapi(*, web_api_version="2.15.1", reject_auth=False, captured=None):
    """Build a stand-in qbittorrentapi module recording Client kwargs into `captured`."""

    class FakeClient:
        def __init__(self, **kwargs):
            if captured is not None:
                captured.update(kwargs)
            self.app = types.SimpleNamespace(web_api_version=web_api_version)

        def auth_log_in(self):
            if reject_auth:
                raise LoginFailed

    module = types.ModuleType("qbittorrentapi")
    module.Client = FakeClient
    return module


def _run_test_connection(monkeypatch, current_values, fake_module):
    """Invoke the Test Connection callback against a stubbed qbittorrentapi."""
    from shelfmark.core.config import config as config_obj
    from shelfmark.download.clients import settings as settings_module

    monkeypatch.setattr(config_obj, "get", make_config_getter(current_values))
    monkeypatch.setattr(settings_module, "get_ssl_verify", lambda _url: True)

    with patch.dict("sys.modules", {"qbittorrentapi": fake_module}):
        return settings_module._test_qbittorrent_connection(current_values=current_values)


def test_api_key_field_is_registered():
    """The API key is offered alongside the other qBittorrent credentials."""
    from shelfmark.download.clients.settings import prowlarr_clients_settings

    field = _get_field(prowlarr_clients_settings(), "QBITTORRENT_API_KEY")

    assert isinstance(field, PasswordField)
    assert field.show_when == {"field": "PROWLARR_TORRENT_CLIENT", "value": "qbittorrent"}


@pytest.mark.parametrize(
    ("api_key", "expected_kwarg", "expected_suffix"),
    [(API_KEY, API_KEY, " using the API key"), ("", None, "")],
)
def test_settings_test_connection_forwards_api_key(
    monkeypatch, api_key, expected_kwarg, expected_suffix
):
    """The Test Connection button authenticates with the key when set, and says which it used."""
    captured = {}
    result = _run_test_connection(
        monkeypatch,
        {
            "QBITTORRENT_URL": "http://localhost:8080",
            "QBITTORRENT_USERNAME": "admin",
            "QBITTORRENT_PASSWORD": "password",
            "QBITTORRENT_API_KEY": api_key,
        },
        fake_qbittorrentapi(captured=captured),
    )

    assert captured["api_key"] == expected_kwarg
    assert result == {
        "success": True,
        "message": f"Connected to qBittorrent (API v2.15.1){expected_suffix}",
    }


@pytest.mark.parametrize(
    ("api_key", "rejected"),
    [(API_KEY, "API key"), ("", "username or password")],
)
def test_settings_test_connection_names_rejected_credential(monkeypatch, api_key, rejected):
    """LoginFailed carries no message of its own, so the callback names the credential."""
    result = _run_test_connection(
        monkeypatch,
        {
            "QBITTORRENT_URL": "http://localhost:8080",
            "QBITTORRENT_USERNAME": "admin",
            "QBITTORRENT_PASSWORD": "password",
            "QBITTORRENT_API_KEY": api_key,
        },
        fake_qbittorrentapi(reject_auth=True),
    )

    assert result == {"success": False, "message": f"qBittorrent rejected the {rejected}"}


@pytest.mark.parametrize(
    ("url", "forced"),
    [("https://qbittorrent.example.com", True), ("http://localhost:8080", False)],
)
def test_settings_test_connection_keeps_an_explicit_https_scheme(monkeypatch, url, forced):
    """The button connects the same way downloads do, so https:// can't drop to HTTP (#1417)."""
    captured = {}
    _run_test_connection(
        monkeypatch,
        {
            "QBITTORRENT_URL": url,
            "QBITTORRENT_USERNAME": "admin",
            "QBITTORRENT_PASSWORD": "password",
        },
        fake_qbittorrentapi(captured=captured),
    )

    assert captured["FORCE_SCHEME_FROM_HOST"] is forced
