"""Debrid-Link Download Client settings and connection-action tests."""

from unittest.mock import MagicMock

from shelfmark.core.settings_registry import ActionButton, PasswordField


def _field(fields, key):
    return next(field for field in fields if field.key == key)


def test_debridlink_fields_are_registered_with_conditional_visibility():
    from shelfmark.download.clients.settings import prowlarr_clients_settings

    fields = prowlarr_clients_settings()
    client_field = _field(fields, "PROWLARR_TORRENT_CLIENT")
    api_key_field = _field(fields, "DEBRIDLINK_API_KEY")
    test_action = _field(fields, "test_debridlink")

    assert {option["value"] for option in client_field.options} >= {"debridlink"}
    assert isinstance(api_key_field, PasswordField)
    assert api_key_field.show_when == {"field": "PROWLARR_TORRENT_CLIENT", "value": "debridlink"}
    assert isinstance(test_action, ActionButton)
    assert test_action.show_when == {"field": "PROWLARR_TORRENT_CLIENT", "value": "debridlink"}


def test_debridlink_connection_action_uses_unsaved_api_key(monkeypatch):
    from shelfmark.core.config import config
    from shelfmark.download.clients import settings as settings_module

    client = MagicMock()
    client.test_connection.return_value = (True, "Connected")
    client_type = MagicMock(return_value=client)
    monkeypatch.setattr("shelfmark.download.clients.debridlink.DebridLinkClient", client_type)
    monkeypatch.setattr(config, "get", lambda _key, default="": "saved-key")

    result = settings_module._test_debridlink_connection({"DEBRIDLINK_API_KEY": "unsaved-key"})

    assert result == {"success": True, "message": "Connected"}
    assert client._api_key == "unsaved-key"


def test_debridlink_connection_action_requires_api_key():
    from shelfmark.download.clients.settings import _test_debridlink_connection

    assert _test_debridlink_connection({"DEBRIDLINK_API_KEY": ""}) == {
        "success": False,
        "message": "Debrid-Link API Key is required",
    }
