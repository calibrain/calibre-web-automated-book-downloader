"""Tests for user override precedence and effective user-preference payloads."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from shelfmark.core.config import config


def _download_field(key: str):
    import shelfmark.config.settings  # noqa: F401
    from shelfmark.core import settings_registry

    return settings_registry.get_settings_field_map()[key][0]


def test_get_prefers_env_over_user_override(monkeypatch):
    field = _download_field("DESTINATION")

    monkeypatch.setattr(config, "_ensure_loaded", lambda: None)
    monkeypatch.setattr(config, "_cache", {"DESTINATION": "/env/books"})
    monkeypatch.setattr(config, "_field_map", {"DESTINATION": (field, "downloads")})
    monkeypatch.setattr(config, "_get_user_override", lambda user_id, key: "/user/books")
    monkeypatch.setattr(
        "shelfmark.core.config._get_registry",
        lambda: SimpleNamespace(is_value_from_env=lambda field: True),
    )

    assert config.get("DESTINATION", "/default", user_id=10) == "/env/books"


def test_get_uses_user_override_when_not_from_env(monkeypatch):
    field = _download_field("DESTINATION")

    monkeypatch.setattr(config, "_ensure_loaded", lambda: None)
    monkeypatch.setattr(config, "_cache", {"DESTINATION": "/global/books"})
    monkeypatch.setattr(config, "_field_map", {"DESTINATION": (field, "downloads")})
    monkeypatch.setattr(config, "_get_user_override", lambda user_id, key: "/user/books")
    monkeypatch.setattr(
        "shelfmark.core.config._get_registry",
        lambda: SimpleNamespace(is_value_from_env=lambda field: False),
    )

    assert config.get("DESTINATION", "/default", user_id=10) == "/user/books"


def test_get_ignores_user_override_for_non_overridable_field(monkeypatch):
    field = _download_field("FILE_ORGANIZATION")

    monkeypatch.setattr(config, "_ensure_loaded", lambda: None)
    monkeypatch.setattr(config, "_cache", {"FILE_ORGANIZATION": "rename"})
    monkeypatch.setattr(
        config,
        "_field_map",
        {"FILE_ORGANIZATION": (field, "downloads")},
    )
    monkeypatch.setattr(config, "_get_user_override", lambda user_id, key: "organize")
    monkeypatch.setattr(
        "shelfmark.core.config._get_registry",
        lambda: SimpleNamespace(is_value_from_env=lambda field: False),
    )

    assert config.get("FILE_ORGANIZATION", "rename", user_id=10) == "rename"


def test_get_keeps_empty_string_override(monkeypatch):
    field = _download_field("DESTINATION_AUDIOBOOK")

    monkeypatch.setattr(config, "_ensure_loaded", lambda: None)
    monkeypatch.setattr(config, "_cache", {"DESTINATION_AUDIOBOOK": "/global/audiobooks"})
    monkeypatch.setattr(
        config,
        "_field_map",
        {"DESTINATION_AUDIOBOOK": (field, "downloads")},
    )
    monkeypatch.setattr(config, "_get_user_override", lambda user_id, key: "")
    monkeypatch.setattr(
        "shelfmark.core.config._get_registry",
        lambda: SimpleNamespace(is_value_from_env=lambda field: False),
    )

    assert config.get("DESTINATION_AUDIOBOOK", "/default", user_id=10) == ""


@pytest.mark.parametrize(
    ("override", "from_env", "expected"),
    [
        ("/user/books", False, ("/user/books", True)),
        ("", False, ("", True)),
        (None, False, ("/global/books", False)),
        ("/user/books", True, ("/global/books", False)),
    ],
)
def test_get_for_user_returns_get_value_and_whether_it_is_the_users_own(
    monkeypatch, override, from_env, expected
):
    field = _download_field("DESTINATION")

    monkeypatch.setattr(config, "_ensure_loaded", lambda: None)
    monkeypatch.setattr(config, "_cache", {"DESTINATION": "/global/books"})
    monkeypatch.setattr(config, "_field_map", {"DESTINATION": (field, "downloads")})
    monkeypatch.setattr(config, "_get_user_override", lambda user_id, key: override)
    monkeypatch.setattr(
        "shelfmark.core.config._get_registry",
        lambda: SimpleNamespace(is_value_from_env=lambda field: from_env),
    )

    assert config.get_for_user("DESTINATION", "/default", user_id=10) == expected
    assert config.get("DESTINATION", "/default", user_id=10) == expected[0]
    assert config.get_for_user("DESTINATION", "/default", user_id=None) == ("/global/books", False)


def test_build_user_preferences_payload_reports_effective_sources(monkeypatch):
    import shelfmark.config.settings  # noqa: F401
    from shelfmark.core import settings_registry
    from shelfmark.core.user_settings_overrides import build_user_preferences_payload

    user_db = SimpleNamespace(
        get_user_settings=lambda user_id: {
            "DESTINATION": "/user/books",
        }
    )

    def fake_get(key, default=None, user_id=None):
        values = {
            "DESTINATION": "/global/books",
            "BOOKS_OUTPUT_MODE": "folder",
            "EMAIL_RECIPIENT": "global@example.com",
        }
        return values.get(key, default)

    with (
        patch(
            "shelfmark.core.user_settings_overrides.get_settings_registry",
            return_value=settings_registry,
        ),
        patch(
            "shelfmark.core.user_settings_overrides.load_config_file",
            return_value={
                "DESTINATION": "/global/books",
                "BOOKS_OUTPUT_MODE": "folder",
                "EMAIL_RECIPIENT": "global@example.com",
            },
        ),
        patch.object(config, "get", side_effect=fake_get),
        patch.object(
            settings_registry,
            "is_value_from_env",
            side_effect=lambda field: field.key == "BOOKS_OUTPUT_MODE",
        ),
    ):
        payload = build_user_preferences_payload(user_db, 7, "downloads")

    assert payload["tab"] == "downloads"
    assert payload["userOverrides"] == {"DESTINATION": "/user/books"}
    assert payload["globalValues"]["DESTINATION"] == "/global/books"
    assert payload["effective"]["DESTINATION"] == {
        "value": "/user/books",
        "source": "user_override",
    }
    assert payload["effective"]["BOOKS_OUTPUT_MODE"] == {
        "value": "folder",
        "source": "env_var",
    }
    assert payload["effective"]["EMAIL_RECIPIENT"] == {
        "value": "global@example.com",
        "source": "global_config",
    }

    fields_by_key = {field["key"]: field for field in payload["fields"]}
    assert fields_by_key["DESTINATION"]["fromEnv"] is False
    assert fields_by_key["BOOKS_OUTPUT_MODE"]["fromEnv"] is True


def test_build_user_preferences_payload_masks_admin_globals_for_self_service():
    """Self-service mode hides global values the user did not set themselves."""
    import shelfmark.config.settings  # noqa: F401
    from shelfmark.core import settings_registry
    from shelfmark.core.user_settings_overrides import build_user_preferences_payload

    user_db = SimpleNamespace(get_user_settings=lambda user_id: {"DESTINATION": "/user/books"})
    admin_globals = {
        "DESTINATION": "/global/books",
        "DESTINATION_AUDIOBOOK": "/global/audiobooks",
        "BOOKS_OUTPUT_MODE": "booklore",
        "EMAIL_RECIPIENT": "global@example.com",
        "BOOKLORE_LIBRARY_ID": "3",
        "BOOKLORE_PATH_ID": "9",
        "DOWNLOAD_TO_BROWSER_CONTENT_TYPES": ["ebook"],
        "USER_NOTIFICATION_ROUTES": [{"event": ["all"], "url": "ntfys://ntfy.sh/secret-topic"}],
    }

    def fake_get(key, default=None, user_id=None):
        if user_id is not None and key == "DESTINATION":
            return "/user/books"
        return admin_globals.get(key, default)

    with (
        patch(
            "shelfmark.core.user_settings_overrides.get_settings_registry",
            return_value=settings_registry,
        ),
        patch(
            "shelfmark.core.user_settings_overrides.load_config_file",
            return_value=dict(admin_globals),
        ),
        patch.object(config, "get", side_effect=fake_get),
        patch.object(settings_registry, "is_value_from_env", return_value=False),
    ):
        admin_payload = build_user_preferences_payload(user_db, 7, "downloads")
        self_payload = build_user_preferences_payload(
            user_db, 7, "downloads", for_self_service=True
        )
        self_notifications = build_user_preferences_payload(
            user_db, 7, "notifications", for_self_service=True
        )

    # Admin routes keep the full picture.
    assert admin_payload["globalValues"]["DESTINATION"] == "/global/books"
    assert admin_payload["globalValues"]["EMAIL_RECIPIENT"] == "global@example.com"
    assert admin_payload["effective"]["EMAIL_RECIPIENT"]["value"] == "global@example.com"

    # Same keys and field metadata, but the admin's values are masked.
    assert self_payload["keys"] == admin_payload["keys"]
    assert self_payload["fields"] == admin_payload["fields"]
    assert self_payload["userOverrides"] == {"DESTINATION": "/user/books"}
    for masked_key in (
        "DESTINATION",
        "DESTINATION_AUDIOBOOK",
        "EMAIL_RECIPIENT",
        "BOOKLORE_LIBRARY_ID",
        "BOOKLORE_PATH_ID",
    ):
        assert self_payload["globalValues"][masked_key] is None, masked_key
    assert self_payload["effective"]["EMAIL_RECIPIENT"] == {
        "value": None,
        "source": "global_config",
    }
    assert self_payload["effective"]["DESTINATION"] == {
        "value": "/user/books",
        "source": "user_override",
    }
    # Select and multi-select switches drive the form layout and stay visible.
    assert self_payload["globalValues"]["BOOKS_OUTPUT_MODE"] == "booklore"
    assert self_payload["globalValues"]["DOWNLOAD_TO_BROWSER_CONTENT_TYPES"] == ["ebook"]

    assert self_notifications["globalValues"]["USER_NOTIFICATION_ROUTES"] is None
    assert self_notifications["effective"]["USER_NOTIFICATION_ROUTES"]["value"] is None
    assert "secret-topic" not in repr(self_notifications)


def test_build_user_preferences_payload_carries_the_language_default():
    """BOOK_LANGUAGE rides along with the other search preferences on its tab."""
    import shelfmark.config.settings  # noqa: F401
    from shelfmark.core.user_settings_overrides import build_user_preferences_payload

    user_db = SimpleNamespace(get_user_settings=lambda user_id: {"BOOK_LANGUAGE": ["de", "en"]})

    payload = build_user_preferences_payload(user_db, 7, "search_mode")

    assert payload["tab"] == "search_mode"
    assert {"SEARCH_MODE", "BOOK_LANGUAGE"}.issubset(payload["keys"])
    assert payload["userOverrides"] == {"BOOK_LANGUAGE": ["de", "en"]}
    assert payload["effective"]["BOOK_LANGUAGE"] == {
        "value": ["de", "en"],
        "source": "user_override",
    }

    fields_by_key = {field["key"]: field for field in payload["fields"]}
    assert fields_by_key["BOOK_LANGUAGE"]["type"] == "MultiSelectField"
    assert fields_by_key["BOOK_LANGUAGE"]["options"]
