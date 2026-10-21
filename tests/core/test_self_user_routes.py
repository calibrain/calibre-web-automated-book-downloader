"""Tests for self-service account edit context and update endpoints."""

import os
import tempfile
from typing import Any
from unittest.mock import patch

import pytest
from flask import Flask
from werkzeug.security import check_password_hash, generate_password_hash

from shelfmark.core.self_user_routes import PasswordAttemptLimiter, register_self_user_routes
from shelfmark.core.user_db import UserDB


@pytest.fixture
def db_path():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield os.path.join(tmpdir, "shelfmark.db")


@pytest.fixture
def user_db(db_path):
    db = UserDB(db_path)
    db.initialize()
    return db


@pytest.fixture
def app(user_db):
    test_app = Flask(__name__)
    test_app.config["SECRET_KEY"] = "test-secret"
    test_app.config["TESTING"] = True

    register_self_user_routes(test_app, user_db)
    return test_app


def _authed_client_for_user(app: Flask, user: dict) -> Any:
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = user["username"]
        sess["db_user_id"] = user["id"]
        sess["is_admin"] = False
    return client


def _visible_sections_config_get(
    visible_sections: object,
):
    def _get(key: str, default: object = None, user_id: int | None = None) -> object:
        del user_id
        if key == "VISIBLE_SELF_SETTINGS_SECTIONS":
            return visible_sections
        return default

    return _get


def test_users_me_edit_context_respects_visible_sections(app, user_db, monkeypatch):
    user = user_db.create_user(username="alice")
    user_db.set_user_settings(user["id"], {"DESTINATION": "/books/alice"})
    client = _authed_client_for_user(app, user)
    monkeypatch.delenv("INGEST_DIR", raising=False)

    with patch("shelfmark.core.self_user_routes.load_active_auth_mode", return_value="builtin"):
        with patch(
            "shelfmark.core.self_user_routes.app_config.get",
            side_effect=_visible_sections_config_get(["delivery"]),
        ):
            resp = client.get("/api/users/me/edit-context")

    assert resp.status_code == 200
    assert resp.json["visibleUserSettingsSections"] == ["delivery"]
    assert resp.json["deliveryPreferences"]["tab"] == "downloads"
    assert resp.json["deliveryPreferences"]["effective"]["DESTINATION"]["value"] == "/books/alice"
    assert resp.json["deliveryPreferences"]["effective"]["DESTINATION"]["source"] == "user_override"
    assert "DESTINATION" in resp.json["userOverridableKeys"]
    assert resp.json["notificationPreferences"] is None


def test_users_me_edit_context_includes_search_preferences_when_visible(app, user_db):
    user = user_db.create_user(username="alice")
    user_db.set_user_settings(
        user["id"],
        {
            "SEARCH_MODE": "universal",
            "METADATA_PROVIDER": "openlibrary",
            "BOOK_LANGUAGE": ["de", "en"],
        },
    )
    client = _authed_client_for_user(app, user)

    with patch("shelfmark.core.self_user_routes.load_active_auth_mode", return_value="builtin"):
        with patch(
            "shelfmark.core.self_user_routes.app_config.get",
            side_effect=_visible_sections_config_get(["delivery", "search"]),
        ):
            resp = client.get("/api/users/me/edit-context")

    assert resp.status_code == 200
    assert resp.json["visibleUserSettingsSections"] == ["delivery", "search"]
    assert resp.json["deliveryPreferences"]["tab"] == "downloads"
    assert resp.json["searchPreferences"]["tab"] == "search_mode"
    assert resp.json["searchPreferences"]["effective"]["SEARCH_MODE"]["value"] == "universal"
    assert resp.json["searchPreferences"]["effective"]["SEARCH_MODE"]["source"] == "user_override"
    assert resp.json["searchPreferences"]["effective"]["BOOK_LANGUAGE"]["value"] == ["de", "en"]
    assert resp.json["searchPreferences"]["effective"]["BOOK_LANGUAGE"]["source"] == (
        "user_override"
    )
    assert "SEARCH_MODE" in resp.json["userOverridableKeys"]
    assert "METADATA_PROVIDER" in resp.json["userOverridableKeys"]
    assert "BOOK_LANGUAGE" in resp.json["userOverridableKeys"]
    assert resp.json["notificationPreferences"] is None
    assert resp.json["userOverridableKeys"] == sorted(resp.json["userOverridableKeys"])


def test_users_me_edit_context_falls_back_to_default_sections_for_invalid_config(app, user_db):
    user = user_db.create_user(username="alice")
    client = _authed_client_for_user(app, user)

    with patch("shelfmark.core.self_user_routes.load_active_auth_mode", return_value="builtin"):
        with patch(
            "shelfmark.core.self_user_routes.app_config.get",
            side_effect=_visible_sections_config_get("bogus"),
        ):
            resp = client.get("/api/users/me/edit-context")

    assert resp.status_code == 200
    assert resp.json["visibleUserSettingsSections"] == ["delivery", "search", "notifications"]
    assert resp.json["deliveryPreferences"] is not None
    assert resp.json["searchPreferences"] is not None
    assert resp.json["notificationPreferences"] is not None


def test_users_me_update_rejects_hidden_section_settings(app, user_db):
    user = user_db.create_user(username="alice")
    client = _authed_client_for_user(app, user)

    with patch("shelfmark.core.self_user_routes.load_active_auth_mode", return_value="builtin"):
        with patch(
            "shelfmark.core.self_user_routes.app_config.get",
            side_effect=_visible_sections_config_get(["delivery"]),
        ):
            resp = client.put(
                "/api/users/me",
                json={
                    "settings": {
                        "USER_NOTIFICATION_ROUTES": [
                            {"event": "all", "url": "ntfys://ntfy.sh/alice"}
                        ],
                    }
                },
            )

    assert resp.status_code == 400
    assert resp.json["error"] == "Some settings are admin-only"
    assert "Setting not user-overridable: USER_NOTIFICATION_ROUTES" in resp.json["details"]


def test_users_me_update_accepts_visible_section_settings(app, user_db):
    user = user_db.create_user(username="alice")
    client = _authed_client_for_user(app, user)

    with patch("shelfmark.core.self_user_routes.load_active_auth_mode", return_value="builtin"):
        with patch(
            "shelfmark.core.self_user_routes.app_config.get",
            side_effect=_visible_sections_config_get(["delivery"]),
        ):
            resp = client.put(
                "/api/users/me",
                json={"settings": {"DESTINATION": "/books/alice"}},
            )

    assert resp.status_code == 200
    assert user_db.get_user_settings(user["id"]).get("DESTINATION") == "/books/alice"
    assert resp.json["settings"]["DESTINATION"] == "/books/alice"


def test_users_me_update_accepts_book_language_when_search_section_visible(app, user_db):
    user = user_db.create_user(username="alice")
    client = _authed_client_for_user(app, user)

    with patch("shelfmark.core.self_user_routes.load_active_auth_mode", return_value="builtin"):
        with patch(
            "shelfmark.core.self_user_routes.app_config.get",
            side_effect=_visible_sections_config_get(["search"]),
        ):
            resp = client.put(
                "/api/users/me",
                json={"settings": {"BOOK_LANGUAGE": ["German", "en"]}},
            )

    assert resp.status_code == 200
    assert user_db.get_user_settings(user["id"])["BOOK_LANGUAGE"] == ["de", "en"]


def test_users_me_update_rejects_book_language_when_search_section_hidden(app, user_db):
    user = user_db.create_user(username="alice")
    client = _authed_client_for_user(app, user)

    with patch("shelfmark.core.self_user_routes.load_active_auth_mode", return_value="builtin"):
        with patch(
            "shelfmark.core.self_user_routes.app_config.get",
            side_effect=_visible_sections_config_get(["delivery"]),
        ):
            resp = client.put(
                "/api/users/me",
                json={"settings": {"BOOK_LANGUAGE": ["de"]}},
            )

    assert resp.status_code == 400
    assert resp.json["error"] == "Some settings are admin-only"
    assert user_db.get_user_settings(user["id"]) == {}


def test_users_me_update_rejects_non_object_settings_payload(app, user_db):
    user = user_db.create_user(username="alice")
    client = _authed_client_for_user(app, user)

    with patch("shelfmark.core.self_user_routes.load_active_auth_mode", return_value="builtin"):
        with patch(
            "shelfmark.core.self_user_routes.app_config.get",
            side_effect=_visible_sections_config_get(["delivery"]),
        ):
            resp = client.put("/api/users/me", json={"settings": ["DESTINATION"]})

    assert resp.status_code == 400
    assert resp.json["error"] == "Settings must be an object"


def test_users_me_update_rejects_oidc_email_change(app, user_db):
    user = user_db.create_user(
        username="oidc-user",
        oidc_subject="oidc-sub-123",
        auth_source="oidc",
    )
    client = _authed_client_for_user(app, user)

    with patch("shelfmark.core.self_user_routes.load_active_auth_mode", return_value="builtin"):
        resp = client.put("/api/users/me", json={"email": "new@example.com"})

    assert resp.status_code == 400
    assert resp.json["error"] == "Cannot change email for OIDC users"
    assert user_db.get_user(user_id=user["id"])["email"] is None


def test_users_me_update_keeps_password_and_profile_when_settings_rejected(app, user_db):
    old_hash = generate_password_hash("oldpass99")
    user = user_db.create_user(
        username="alice",
        display_name="Alice Old",
        password_hash=old_hash,
    )
    client = _authed_client_for_user(app, user)

    with patch("shelfmark.core.self_user_routes.load_active_auth_mode", return_value="builtin"):
        with patch(
            "shelfmark.core.self_user_routes.app_config.get",
            side_effect=_visible_sections_config_get(["delivery"]),
        ):
            resp = client.put(
                "/api/users/me",
                json={
                    "password": "newpass99",
                    "current_password": "oldpass99",
                    "display_name": "Alice New",
                    "settings": {
                        "USER_NOTIFICATION_ROUTES": [
                            {"event": "all", "url": "ntfys://ntfy.sh/alice"}
                        ],
                    },
                },
            )

    assert resp.status_code == 400
    assert resp.json["error"] == "Some settings are admin-only"

    stored = user_db.get_user(user_id=user["id"])
    assert stored["password_hash"] == old_hash
    assert stored["display_name"] == "Alice Old"


def test_users_me_update_applies_password_profile_and_settings_together(app, user_db):
    old_hash = generate_password_hash("oldpass99")
    user = user_db.create_user(
        username="alice",
        display_name="Alice Old",
        password_hash=old_hash,
    )
    client = _authed_client_for_user(app, user)

    with patch("shelfmark.core.self_user_routes.load_active_auth_mode", return_value="builtin"):
        with patch(
            "shelfmark.core.self_user_routes.app_config.get",
            side_effect=_visible_sections_config_get(["delivery"]),
        ):
            resp = client.put(
                "/api/users/me",
                json={
                    "password": "newpass99",
                    "current_password": "oldpass99",
                    "display_name": "Alice New",
                    "settings": {"DESTINATION": "/books/alice"},
                },
            )

    assert resp.status_code == 200

    stored = user_db.get_user(user_id=user["id"])
    assert stored["password_hash"] != old_hash
    assert check_password_hash(stored["password_hash"], "newpass99")
    assert stored["display_name"] == "Alice New"
    assert user_db.get_user_settings(user["id"])["DESTINATION"] == "/books/alice"


def _put_password(client, payload):
    with patch("shelfmark.core.self_user_routes.load_active_auth_mode", return_value="builtin"):
        return client.put("/api/users/me", json=payload)


def _user_with_password(user_db, password="oldpass99"):
    return user_db.create_user(username="alice", password_hash=generate_password_hash(password))


def _stored_password_still(user_db, user, password):
    return check_password_hash(user_db.get_user(user_id=user["id"])["password_hash"], password)


def test_a_new_password_needs_the_current_one(app, user_db):
    user = _user_with_password(user_db)
    client = _authed_client_for_user(app, user)

    resp = _put_password(client, {"password": "newpass99"})

    assert resp.status_code == 400
    assert resp.json["error"] == "Current password is incorrect"
    assert _stored_password_still(user_db, user, "oldpass99")


def test_a_wrong_current_password_changes_nothing(app, user_db):
    user = _user_with_password(user_db)
    client = _authed_client_for_user(app, user)

    resp = _put_password(client, {"password": "newpass99", "current_password": "guess"})

    assert resp.status_code == 400
    assert _stored_password_still(user_db, user, "oldpass99")
    assert not _stored_password_still(user_db, user, "newpass99")


def test_the_right_current_password_sets_the_new_one(app, user_db):
    user = _user_with_password(user_db)
    client = _authed_client_for_user(app, user)

    resp = _put_password(client, {"password": "newpass99", "current_password": "oldpass99"})

    assert resp.status_code == 200
    assert _stored_password_still(user_db, user, "newpass99")
    assert not _stored_password_still(user_db, user, "oldpass99")


@pytest.mark.parametrize("password", [12345, ["newpass99"], {"a": 1}, True])
def test_a_password_that_is_not_a_string_is_refused(app, user_db, password):
    user = _user_with_password(user_db)
    client = _authed_client_for_user(app, user)

    resp = _put_password(client, {"password": password, "current_password": "oldpass99"})

    assert resp.status_code == 400
    assert _stored_password_still(user_db, user, "oldpass99")


@pytest.mark.parametrize("current", [12345, ["oldpass99"], None, ""])
def test_a_current_password_that_is_not_a_string_is_refused(app, user_db, current):
    user = _user_with_password(user_db)
    client = _authed_client_for_user(app, user)

    resp = _put_password(client, {"password": "newpass99", "current_password": current})

    assert resp.status_code == 400
    assert _stored_password_still(user_db, user, "oldpass99")


def test_a_profile_change_without_a_password_needs_no_current_password(app, user_db):
    user = _user_with_password(user_db)
    client = _authed_client_for_user(app, user)

    resp = _put_password(client, {"display_name": "Alice"})

    assert resp.status_code == 200
    assert _stored_password_still(user_db, user, "oldpass99")


class _Attempts:
    """Stands in for the login lockout and remembers what it was asked."""

    def __init__(self, locked=False):
        self.locked = locked
        self.failures = []
        self.cleared = []

    def limiter(self):
        return PasswordAttemptLimiter(
            is_locked=lambda username: self.locked,
            record_failure=lambda username: self.failures.append(username) or False,
            clear=self.cleared.append,
        )


@pytest.fixture
def limited_app(user_db):
    def _make(attempts):
        test_app = Flask(__name__)
        test_app.config["SECRET_KEY"] = "test-secret"
        test_app.config["TESTING"] = True
        register_self_user_routes(test_app, user_db, password_attempts=attempts.limiter())
        return test_app

    return _make


def test_a_wrong_current_password_counts_toward_the_login_lockout(limited_app, user_db):
    attempts = _Attempts()
    user = _user_with_password(user_db)
    client = _authed_client_for_user(limited_app(attempts), user)

    _put_password(client, {"password": "newpass99", "current_password": "guess"})

    assert attempts.failures == ["alice"]
    assert attempts.cleared == []


def test_a_locked_account_cannot_try_a_current_password(limited_app, user_db):
    attempts = _Attempts(locked=True)
    user = _user_with_password(user_db)
    client = _authed_client_for_user(limited_app(attempts), user)

    resp = _put_password(client, {"password": "newpass99", "current_password": "oldpass99"})

    assert resp.status_code == 429
    assert _stored_password_still(user_db, user, "oldpass99")
    assert attempts.failures == []


def test_the_right_current_password_clears_the_failures(limited_app, user_db):
    attempts = _Attempts()
    user = _user_with_password(user_db)
    client = _authed_client_for_user(limited_app(attempts), user)

    resp = _put_password(client, {"password": "newpass99", "current_password": "oldpass99"})

    assert resp.status_code == 200
    assert attempts.cleared == ["alice"]
