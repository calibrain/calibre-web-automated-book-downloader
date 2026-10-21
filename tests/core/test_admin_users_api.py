"""
Tests for admin user management API routes.

Tests CRUD endpoints for managing users from the admin panel.
"""

import os
import sqlite3
import tempfile
from unittest.mock import patch

import pytest
from flask import Flask

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
    from shelfmark.core.admin_routes import register_admin_routes

    test_app = Flask(__name__)
    test_app.config["SECRET_KEY"] = "test-secret"
    test_app.config["TESTING"] = True

    register_admin_routes(test_app, user_db)
    return test_app


@pytest.fixture
def admin_client(app):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = "admin"
        sess["is_admin"] = True
    return client


@pytest.fixture
def regular_client(app):
    """Non-admin client with auth mode set to builtin (auth-required)."""
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = "user"
        sess["is_admin"] = False
    with patch("shelfmark.core.admin_routes.load_active_auth_mode", return_value="builtin"):
        yield client


@pytest.fixture
def no_session_client(app):
    """Client with no session at all (unauthenticated, no-auth mode)."""
    return app.test_client()


@pytest.fixture
def no_session_auth_client(app):
    """Client with no session but auth mode enabled (should be rejected)."""
    client = app.test_client()
    with patch("shelfmark.core.admin_routes.load_active_auth_mode", return_value="builtin"):
        yield client


# ---------------------------------------------------------------------------
# GET /api/admin/users
# ---------------------------------------------------------------------------


class TestAdminUsersListEndpoint:
    """Tests for GET /api/admin/users."""

    def test_list_users_empty(self, admin_client):
        resp = admin_client.get("/api/admin/users")
        assert resp.status_code == 200
        assert resp.json == []

    def test_list_users_returns_all(self, admin_client, user_db):
        user_db.create_user(username="alice", email="alice@example.com")
        user_db.create_user(username="bob", email="bob@example.com")

        resp = admin_client.get("/api/admin/users")
        assert resp.status_code == 200
        assert len(resp.json) == 2
        usernames = [u["username"] for u in resp.json]
        assert "alice" in usernames
        assert "bob" in usernames

    def test_list_users_excludes_password_hash(self, admin_client, user_db):
        user_db.create_user(username="alice", password_hash="secret_hash")
        user_db.create_user(username="bob", password_hash="another_secret_hash")

        resp = admin_client.get("/api/admin/users")
        users = resp.json
        assert users
        assert all("password_hash" not in user for user in users)

    def test_list_users_includes_auth_source_and_is_active(self, admin_client, user_db):
        user_db.create_user(username="local_user", auth_source="builtin")
        user_db.create_user(
            username="oidc_user",
            oidc_subject="oidc-sub-123",
            auth_source="oidc",
        )
        user_db.create_user(username="proxy_user", auth_source="proxy")

        with patch("shelfmark.core.admin_routes.load_active_auth_mode", return_value="builtin"):
            resp = admin_client.get("/api/admin/users")

        assert resp.status_code == 200
        by_username = {u["username"]: u for u in resp.json}

        assert by_username["local_user"]["auth_source"] == "builtin"
        assert by_username["local_user"]["is_active"] is True
        assert by_username["local_user"]["edit_capabilities"]["canSetPassword"] is True
        assert by_username["local_user"]["edit_capabilities"]["canEditRole"] is True
        assert by_username["local_user"]["edit_capabilities"]["canEditEmail"] is True

        assert by_username["oidc_user"]["auth_source"] == "oidc"
        assert by_username["oidc_user"]["is_active"] is False
        assert by_username["oidc_user"]["edit_capabilities"]["canSetPassword"] is False
        assert by_username["oidc_user"]["edit_capabilities"]["canEditRole"] is False
        assert by_username["oidc_user"]["edit_capabilities"]["canEditEmail"] is False
        assert by_username["oidc_user"]["edit_capabilities"]["canEditDisplayName"] is False

        assert by_username["proxy_user"]["auth_source"] == "proxy"
        assert by_username["proxy_user"]["is_active"] is False
        assert by_username["proxy_user"]["edit_capabilities"]["canSetPassword"] is False
        assert by_username["proxy_user"]["edit_capabilities"]["canEditRole"] is False
        assert by_username["proxy_user"]["edit_capabilities"]["canEditEmail"] is True

    def test_list_users_requires_admin(self, regular_client):
        resp = regular_client.get("/api/admin/users")
        assert resp.status_code == 403

    def test_list_users_oidc_role_editable_when_group_auth_disabled(self, admin_client, user_db):
        user_db.create_user(
            username="oidc_user",
            oidc_subject="oidc-sub-123",
            auth_source="oidc",
        )

        with patch(
            "shelfmark.core.admin_routes.app_config.get",
            side_effect=lambda key, default=None, user_id=None: {
                "OIDC_USE_ADMIN_GROUP": False,
            }.get(key, default),
        ):
            resp = admin_client.get("/api/admin/users")

        assert resp.status_code == 200
        oidc_user = next(u for u in resp.json if u["username"] == "oidc_user")
        assert oidc_user["edit_capabilities"]["canEditRole"] is True

    def test_list_users_no_session_allows_access_in_no_auth(self, no_session_client):
        """No session + no-auth mode = admin access allowed."""
        resp = no_session_client.get("/api/admin/users")
        assert resp.status_code == 200

    def test_list_users_no_session_rejected_when_auth_enabled(self, no_session_auth_client):
        """No session + auth enabled = 401."""
        resp = no_session_auth_client.get("/api/admin/users")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /api/admin/users
# ---------------------------------------------------------------------------


class TestAdminUserCreateEndpoint:
    """Tests for POST /api/admin/users."""

    def test_create_user(self, admin_client, user_db):
        # Seed an existing user so alice doesn't get auto-promoted to admin
        user_db.create_user(username="seed_admin", role="admin")

        resp = admin_client.post(
            "/api/admin/users",
            json={"username": "alice", "password": "pass1234"},
        )
        assert resp.status_code == 201
        assert resp.json["username"] == "alice"
        assert resp.json["role"] == "user"
        assert "password_hash" not in resp.json

    def test_create_user_with_all_fields(self, admin_client):
        resp = admin_client.post(
            "/api/admin/users",
            json={
                "username": "alice",
                "password": "pass1234",
                "email": "alice@example.com",
                "display_name": "Alice W",
                "role": "admin",
            },
        )
        assert resp.status_code == 201
        data = resp.json
        assert data["username"] == "alice"
        assert data["email"] == "alice@example.com"
        assert data["display_name"] == "Alice W"
        assert data["role"] == "admin"

    def test_create_user_password_is_hashed(self, admin_client, user_db):
        admin_client.post(
            "/api/admin/users",
            json={"username": "alice", "password": "pass1234"},
        )
        user = user_db.get_user(username="alice")
        assert user["password_hash"] is not None
        assert user["password_hash"] != "pass1234"
        assert user["password_hash"].startswith("scrypt:") or user["password_hash"].startswith(
            "pbkdf2:"
        )

    def test_create_user_requires_admin(self, regular_client):
        resp = regular_client.post(
            "/api/admin/users",
            json={"username": "alice", "password": "pass1234"},
        )
        assert resp.status_code == 403

    def test_create_user_missing_username(self, admin_client):
        resp = admin_client.post(
            "/api/admin/users",
            json={"password": "pass1234"},
        )
        assert resp.status_code == 400
        assert "Username" in resp.json["error"]

    def test_create_user_empty_username(self, admin_client):
        resp = admin_client.post(
            "/api/admin/users",
            json={"username": "  ", "password": "pass1234"},
        )
        assert resp.status_code == 400

    def test_create_user_missing_password(self, admin_client):
        resp = admin_client.post(
            "/api/admin/users",
            json={"username": "alice"},
        )
        assert resp.status_code == 400
        assert "Password" in resp.json["error"]

    def test_create_user_short_password(self, admin_client):
        resp = admin_client.post(
            "/api/admin/users",
            json={"username": "alice", "password": "abc"},
        )
        assert resp.status_code == 400
        assert "4 characters" in resp.json["error"]

    def test_create_user_invalid_role(self, admin_client):
        resp = admin_client.post(
            "/api/admin/users",
            json={"username": "alice", "password": "pass1234", "role": "superadmin"},
        )
        assert resp.status_code == 400
        assert "Role" in resp.json["error"]

    def test_create_user_duplicate_username(self, admin_client, user_db):
        user_db.create_user(username="alice")

        resp = admin_client.post(
            "/api/admin/users",
            json={"username": "alice", "password": "pass1234"},
        )
        assert resp.status_code == 409
        assert "already exists" in resp.json["error"]

    def test_first_user_is_always_admin(self, admin_client, user_db):
        """First user created should be promoted to admin even if role=user."""
        assert len(user_db.list_users()) == 0

        resp = admin_client.post(
            "/api/admin/users",
            json={"username": "alice", "password": "pass1234", "role": "user"},
        )
        assert resp.status_code == 201
        assert resp.json["role"] == "admin"

    def test_second_user_keeps_requested_role(self, admin_client, user_db):
        """After the first user, role should be respected."""
        user_db.create_user(username="admin_user", role="admin")

        resp = admin_client.post(
            "/api/admin/users",
            json={"username": "bob", "password": "pass1234", "role": "user"},
        )
        assert resp.status_code == 201
        assert resp.json["role"] == "user"

    def test_create_user_trims_whitespace(self, admin_client):
        resp = admin_client.post(
            "/api/admin/users",
            json={
                "username": "  alice  ",
                "password": "pass1234",
                "email": "  alice@example.com  ",
                "display_name": "  Alice  ",
            },
        )
        assert resp.status_code == 201
        assert resp.json["username"] == "alice"
        assert resp.json["email"] == "alice@example.com"
        assert resp.json["display_name"] == "Alice"

    def test_create_user_default_role_is_user(self, admin_client, user_db):
        """When role is omitted and DB already has users, default to 'user'."""
        user_db.create_user(username="existing", role="admin")

        resp = admin_client.post(
            "/api/admin/users",
            json={"username": "bob", "password": "pass1234"},
        )
        assert resp.status_code == 201
        assert resp.json["role"] == "user"

    def test_create_user_rejected_in_proxy_mode(self, admin_client):
        with patch("shelfmark.core.admin_routes.load_active_auth_mode", return_value="proxy"):
            resp = admin_client.post(
                "/api/admin/users",
                json={"username": "alice", "password": "pass1234"},
            )

        assert resp.status_code == 400
        assert "Local user creation is disabled" in resp.json["error"]

    def test_create_user_rejected_in_cwa_mode(self, admin_client):
        with patch("shelfmark.core.admin_routes.load_active_auth_mode", return_value="cwa"):
            resp = admin_client.post(
                "/api/admin/users",
                json={"username": "alice", "password": "pass1234"},
            )

        assert resp.status_code == 400
        assert "Local user creation is disabled" in resp.json["error"]

    def test_create_user_allowed_in_oidc_mode(self, admin_client):
        with patch("shelfmark.core.admin_routes.load_active_auth_mode", return_value="oidc"):
            resp = admin_client.post(
                "/api/admin/users",
                json={"username": "alice", "password": "pass1234"},
            )

        assert resp.status_code == 201
        assert resp.json["username"] == "alice"

    def test_create_user_allowed_without_session_in_no_auth(self, no_session_client, user_db):
        resp = no_session_client.post(
            "/api/admin/users",
            json={"username": "alice", "password": "pass1234"},
        )

        assert resp.status_code == 201
        assert resp.json["username"] == "alice"
        created = user_db.get_user(username="alice")
        assert created is not None
        assert created["role"] == "admin"


# ---------------------------------------------------------------------------
# GET /api/admin/users/<id>
# ---------------------------------------------------------------------------


class TestAdminUserGetEndpoint:
    """Tests for GET /api/admin/users/<id>."""

    def test_get_user(self, admin_client, user_db):
        user = user_db.create_user(username="alice", email="alice@example.com")

        resp = admin_client.get(f"/api/admin/users/{user['id']}")
        assert resp.status_code == 200
        assert resp.json["username"] == "alice"
        assert resp.json["email"] == "alice@example.com"

    def test_get_user_includes_settings(self, admin_client, user_db):
        user = user_db.create_user(username="alice")
        user_db.set_user_settings(user["id"], {"BOOKLORE_LIBRARY_ID": 5})

        resp = admin_client.get(f"/api/admin/users/{user['id']}")
        assert resp.json["settings"]["BOOKLORE_LIBRARY_ID"] == 5

    def test_get_user_empty_settings(self, admin_client, user_db):
        user = user_db.create_user(username="alice")

        resp = admin_client.get(f"/api/admin/users/{user['id']}")
        assert resp.json["settings"] == {}

    def test_get_user_excludes_password_hash(self, admin_client, user_db):
        user = user_db.create_user(username="alice", password_hash="secret_hash")

        resp = admin_client.get(f"/api/admin/users/{user['id']}")
        assert "password_hash" not in resp.json

    def test_get_nonexistent_user(self, admin_client):
        resp = admin_client.get("/api/admin/users/9999")
        assert resp.status_code == 404

    def test_get_user_requires_admin(self, regular_client, user_db):
        user = user_db.create_user(username="alice")
        resp = regular_client.get(f"/api/admin/users/{user['id']}")
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# PUT /api/admin/users/<id>
# ---------------------------------------------------------------------------


class TestAdminUserUpdateEndpoint:
    """Tests for PUT /api/admin/users/<id>."""

    def test_update_user_role(self, admin_client, user_db):
        user = user_db.create_user(username="alice", role="user")

        resp = admin_client.put(
            f"/api/admin/users/{user['id']}",
            json={"role": "admin"},
        )
        assert resp.status_code == 200
        updated = user_db.get_user(user_id=user["id"])
        assert updated["role"] == "admin"

    def test_demote_last_admin_allowed_without_auth(self, admin_client, user_db):
        user = user_db.create_user(
            username="onlyadmin",
            role="admin",
            password_hash="hashed_pw",
        )

        with patch("shelfmark.core.admin_routes.load_active_auth_mode", return_value="none"):
            resp = admin_client.put(
                f"/api/admin/users/{user['id']}",
                json={"role": "user"},
            )

        assert resp.status_code == 200
        updated = user_db.get_user(user_id=user["id"])
        assert updated is not None
        assert updated["role"] == "user"

    @pytest.mark.parametrize("auth_mode", ["builtin", "oidc"])
    def test_demote_last_local_admin_rejected(self, admin_client, user_db, auth_mode):
        """Regression for #1387: demoting the last local admin used to disable auth."""
        user = user_db.create_user(
            username="onlyadmin",
            role="admin",
            password_hash="hashed_pw",
        )

        with patch("shelfmark.core.admin_routes.load_active_auth_mode", return_value=auth_mode):
            resp = admin_client.put(
                f"/api/admin/users/{user['id']}",
                json={"role": "user"},
            )

        assert resp.status_code == 400
        assert resp.json["error"] == "Cannot remove admin role from the last local admin account"
        assert user_db.get_user(user_id=user["id"])["role"] == "admin"

    def test_update_user_email(self, admin_client, user_db):
        user = user_db.create_user(username="alice")

        resp = admin_client.put(
            f"/api/admin/users/{user['id']}",
            json={"email": "alice@new.com"},
        )
        assert resp.status_code == 200
        assert resp.json["email"] == "alice@new.com"

    def test_update_user_display_name(self, admin_client, user_db):
        user = user_db.create_user(username="alice")

        resp = admin_client.put(
            f"/api/admin/users/{user['id']}",
            json={"display_name": "Alice Wonderland"},
        )
        assert resp.status_code == 200
        assert resp.json["display_name"] == "Alice Wonderland"

    def test_update_multiple_fields(self, admin_client, user_db):
        user = user_db.create_user(username="alice", role="user")

        resp = admin_client.put(
            f"/api/admin/users/{user['id']}",
            json={"role": "admin", "email": "alice@admin.com", "display_name": "Admin Alice"},
        )
        assert resp.status_code == 200
        assert resp.json["role"] == "admin"
        assert resp.json["email"] == "alice@admin.com"
        assert resp.json["display_name"] == "Admin Alice"

    def test_update_user_settings(self, admin_client, user_db):
        user = user_db.create_user(username="alice")

        resp = admin_client.put(
            f"/api/admin/users/{user['id']}",
            json={"settings": {"BOOKLORE_LIBRARY_ID": 3}},
        )
        assert resp.status_code == 200
        settings = user_db.get_user_settings(user["id"])
        assert settings["BOOKLORE_LIBRARY_ID"] == 3

    def test_update_user_settings_accepts_audiobook_destination(self, admin_client, user_db):
        user = user_db.create_user(username="alice")

        resp = admin_client.put(
            f"/api/admin/users/{user['id']}",
            json={"settings": {"DESTINATION_AUDIOBOOK": "/audiobooks/alice"}},
        )
        assert resp.status_code == 200
        settings = user_db.get_user_settings(user["id"])
        assert settings["DESTINATION_AUDIOBOOK"] == "/audiobooks/alice"

    def test_update_user_settings_normalizes_book_languages(self, admin_client, user_db):
        user = user_db.create_user(username="alice")

        resp = admin_client.put(
            f"/api/admin/users/{user['id']}",
            json={"settings": {"BOOK_LANGUAGE": ["German", "de", " en "]}},
        )
        assert resp.status_code == 200
        settings = user_db.get_user_settings(user["id"])
        assert settings["BOOK_LANGUAGE"] == ["de", "en"]

    def test_update_user_settings_rejects_unknown_book_language(self, admin_client, user_db):
        user = user_db.create_user(username="alice")

        resp = admin_client.put(
            f"/api/admin/users/{user['id']}",
            json={"settings": {"BOOK_LANGUAGE": ["de", "klingon"]}},
        )
        assert resp.status_code == 400
        assert resp.json["error"] == "Invalid settings payload"
        assert any(
            "BOOK_LANGUAGE contains an unsupported language: klingon" in msg
            for msg in resp.json["details"]
        )
        assert user_db.get_user_settings(user["id"]) == {}

    def test_update_user_settings_skips_blank_book_languages(self, admin_client, user_db):
        """A trailing comma or a blank slot means "nothing there", not an unknown language."""
        user = user_db.create_user(username="alice")

        resp = admin_client.put(
            f"/api/admin/users/{user['id']}",
            json={"settings": {"BOOK_LANGUAGE": "en,"}},
        )
        assert resp.status_code == 200
        assert user_db.get_user_settings(user["id"])["BOOK_LANGUAGE"] == ["en"]

    def test_update_user_settings_normalizes_every_validated_search_key(
        self, admin_client, user_db
    ):
        """Keys the search validator recognises keep their normalized value.

        These three were validated but then stored raw, because the write-back was
        gated on a hand-maintained subset of the validated keys. A padded provider
        name was accepted and then persisted with its padding, so every later lookup
        of it failed.
        """
        user = user_db.create_user(username="alice")

        resp = admin_client.put(
            f"/api/admin/users/{user['id']}",
            json={
                "settings": {
                    "METADATA_PROVIDER_COMBINED": "  openlibrary  ",
                    "SHOW_COMBINED_SELECTOR": "yes",
                    "FORCE_COMBINED_SEARCH": "",
                }
            },
        )
        assert resp.status_code == 200

        settings = user_db.get_user_settings(user["id"])
        assert settings["METADATA_PROVIDER_COMBINED"] == "openlibrary"
        assert settings["SHOW_COMBINED_SELECTOR"] is True
        assert settings["FORCE_COMBINED_SEARCH"] is False

    def test_update_user_settings_accepts_notification_overrides(self, admin_client, user_db):
        user = user_db.create_user(username="alice")

        resp = admin_client.put(
            f"/api/admin/users/{user['id']}",
            json={
                "settings": {
                    "USER_NOTIFICATION_ROUTES": [
                        {"event": "all", "url": " ntfys://ntfy.sh/alice "},
                        {"event": "download_failed", "url": "ntfys://ntfy.sh/errors"},
                        {"event": "download_failed", "url": "ntfys://ntfy.sh/errors"},
                    ],
                }
            },
        )
        assert resp.status_code == 200
        settings = user_db.get_user_settings(user["id"])
        assert settings["USER_NOTIFICATION_ROUTES"] == [
            {"event": ["all"], "url": "ntfys://ntfy.sh/alice"},
            {"event": ["download_failed"], "url": "ntfys://ntfy.sh/errors"},
        ]

    def test_update_user_settings_rejects_invalid_notification_url(self, admin_client, user_db):
        user = user_db.create_user(username="alice")

        resp = admin_client.put(
            f"/api/admin/users/{user['id']}",
            json={
                "settings": {
                    "USER_NOTIFICATION_ROUTES": [{"event": "all", "url": "not-a-valid-url"}]
                }
            },
        )
        assert resp.status_code == 400
        assert resp.json["error"] == "Invalid settings payload"
        assert any(
            "Invalid value for USER_NOTIFICATION_ROUTES" in msg for msg in resp.json["details"]
        )

    def test_update_user_settings_accepts_valid_request_policy_rule(self, admin_client, user_db):
        user = user_db.create_user(username="alice")

        resp = admin_client.put(
            f"/api/admin/users/{user['id']}",
            json={
                "settings": {
                    "REQUEST_POLICY_RULES": [
                        {
                            "source": "prowlarr",
                            "content_type": "audiobook",
                            "mode": "request_release",
                        }
                    ]
                }
            },
        )

        assert resp.status_code == 200
        settings = user_db.get_user_settings(user["id"])
        assert settings["REQUEST_POLICY_RULES"] == [
            {
                "source": "prowlarr",
                "content_type": "audiobook",
                "mode": "request_release",
            }
        ]

    def test_update_user_settings_rejects_invalid_source_content_type_pair(
        self, admin_client, user_db
    ):
        user = user_db.create_user(username="alice")

        resp = admin_client.put(
            f"/api/admin/users/{user['id']}",
            json={
                "settings": {
                    "REQUEST_POLICY_RULES": [
                        {
                            "source": "direct_download",
                            "content_type": "audiobook",
                            "mode": "request_release",
                        }
                    ]
                }
            },
        )

        assert resp.status_code == 400
        assert resp.json["error"] == "Invalid settings payload"
        assert any(
            "does not support content_type 'audiobook'" in msg for msg in resp.json["details"]
        )

    def test_update_settings_merges(self, admin_client, user_db):
        user = user_db.create_user(username="alice")
        user_db.set_user_settings(user["id"], {"DESTINATION": "/books/alice"})

        resp = admin_client.put(
            f"/api/admin/users/{user['id']}",
            json={"settings": {"BOOKLORE_LIBRARY_ID": "2"}},
        )
        assert resp.status_code == 200
        assert resp.json["settings"]["DESTINATION"] == "/books/alice"
        assert resp.json["settings"]["BOOKLORE_LIBRARY_ID"] == "2"

    def test_update_settings_admin_is_not_limited_to_global_destination(
        self, admin_client, user_db
    ):
        user = user_db.create_user(username="alice")

        resp = admin_client.put(
            f"/api/admin/users/{user['id']}",
            json={"settings": {"DESTINATION": "/other/alice", "BOOKS_OUTPUT_MODE": "custom"}},
        )
        assert resp.status_code == 200
        assert resp.json["settings"]["DESTINATION"] == "/other/alice"
        assert resp.json["settings"]["BOOKS_OUTPUT_MODE"] == "custom"

    def test_update_response_includes_settings(self, admin_client, user_db):
        user = user_db.create_user(username="alice")
        user_db.set_user_settings(user["id"], {"DESTINATION": "/books/alice"})

        resp = admin_client.put(
            f"/api/admin/users/{user['id']}",
            json={"role": "admin"},
        )
        assert resp.status_code == 200
        assert "settings" in resp.json
        assert resp.json["settings"]["DESTINATION"] == "/books/alice"

    def test_update_user_settings_null_clears_override(self, admin_client, user_db):
        user = user_db.create_user(username="alice")
        user_db.set_user_settings(user["id"], {"DESTINATION": "/books/alice"})

        resp = admin_client.put(
            f"/api/admin/users/{user['id']}",
            json={"settings": {"DESTINATION": None}},
        )
        assert resp.status_code == 200
        settings = user_db.get_user_settings(user["id"])
        assert settings.get("DESTINATION") is None

    def test_update_user_settings_null_policy_mode_accepted(self, admin_client, user_db):
        user = user_db.create_user(username="alice")
        user_db.set_user_settings(user["id"], {"REQUEST_POLICY_DEFAULT_EBOOK": "request_book"})

        resp = admin_client.put(
            f"/api/admin/users/{user['id']}",
            json={"settings": {"REQUEST_POLICY_DEFAULT_EBOOK": None}},
        )
        assert resp.status_code == 200
        settings = user_db.get_user_settings(user["id"])
        assert settings.get("REQUEST_POLICY_DEFAULT_EBOOK") is None

    def test_update_user_settings_null_policy_rules_accepted(self, admin_client, user_db):
        user = user_db.create_user(username="alice")
        user_db.set_user_settings(
            user["id"],
            {
                "REQUEST_POLICY_RULES": [
                    {"source": "prowlarr", "content_type": "audiobook", "mode": "request_release"}
                ],
            },
        )

        resp = admin_client.put(
            f"/api/admin/users/{user['id']}",
            json={"settings": {"REQUEST_POLICY_RULES": None}},
        )
        assert resp.status_code == 200
        settings = user_db.get_user_settings(user["id"])
        assert settings.get("REQUEST_POLICY_RULES") is None

    def test_update_user_settings_mixed_null_and_values(self, admin_client, user_db):
        user = user_db.create_user(username="alice")
        user_db.set_user_settings(
            user["id"],
            {
                "DESTINATION": "/books/alice",
                "REQUEST_POLICY_DEFAULT_EBOOK": "request_book",
            },
        )

        resp = admin_client.put(
            f"/api/admin/users/{user['id']}",
            json={
                "settings": {
                    "DESTINATION": None,
                    "BOOKLORE_LIBRARY_ID": "5",
                    "REQUEST_POLICY_DEFAULT_EBOOK": None,
                    "REQUEST_POLICY_DEFAULT_AUDIOBOOK": "download",
                }
            },
        )
        assert resp.status_code == 200
        settings = user_db.get_user_settings(user["id"])
        assert settings.get("DESTINATION") is None
        assert settings["BOOKLORE_LIBRARY_ID"] == "5"
        assert settings.get("REQUEST_POLICY_DEFAULT_EBOOK") is None
        assert settings["REQUEST_POLICY_DEFAULT_AUDIOBOOK"] == "download"

    def test_update_user_settings_rejects_unknown_key(self, admin_client, user_db):
        user = user_db.create_user(username="alice")

        resp = admin_client.put(
            f"/api/admin/users/{user['id']}",
            json={"settings": {"UNKNOWN_SETTING": "value"}},
        )
        assert resp.status_code == 400
        assert resp.json["error"] == "Invalid settings payload"
        assert any("Unknown setting: UNKNOWN_SETTING" in msg for msg in resp.json["details"])

    def test_update_user_settings_rejects_non_overridable_key(self, admin_client, user_db):
        user = user_db.create_user(username="alice")

        resp = admin_client.put(
            f"/api/admin/users/{user['id']}",
            json={"settings": {"FILE_ORGANIZATION": "rename"}},
        )
        assert resp.status_code == 400
        assert resp.json["error"] == "Invalid settings payload"
        assert any(
            "Setting not user-overridable: FILE_ORGANIZATION" in msg for msg in resp.json["details"]
        )

    def test_update_user_settings_rejects_lowercase_key(self, admin_client, user_db):
        user = user_db.create_user(username="alice")

        resp = admin_client.put(
            f"/api/admin/users/{user['id']}",
            json={"settings": {"destination": "/books/alice"}},
        )
        assert resp.status_code == 400
        assert resp.json["error"] == "Invalid settings payload"
        assert any("Unknown setting: destination" in msg for msg in resp.json["details"])

    def test_update_user_settings_warns_when_runtime_refresh_fails(self, admin_client, user_db):
        user = user_db.create_user(username="alice")

        with (
            patch(
                "shelfmark.core.admin_routes.app_config.refresh", side_effect=RuntimeError("boom")
            ),
            patch("shelfmark.core.admin_routes.logger.warning") as mock_warning,
        ):
            resp = admin_client.put(
                f"/api/admin/users/{user['id']}",
                json={"settings": {"DESTINATION": "/books/alice"}},
            )

        assert resp.status_code == 200
        settings = user_db.get_user_settings(user["id"])
        assert settings["DESTINATION"] == "/books/alice"
        mock_warning.assert_called_once()
        assert "failed to refresh runtime config" in mock_warning.call_args[0][0]

    def test_update_response_excludes_password_hash(self, admin_client, user_db):
        user = user_db.create_user(username="alice", password_hash="secret")

        resp = admin_client.put(
            f"/api/admin/users/{user['id']}",
            json={"role": "admin"},
        )
        assert "password_hash" not in resp.json

    def test_update_nonexistent_user(self, admin_client):
        resp = admin_client.put(
            "/api/admin/users/9999",
            json={"role": "admin"},
        )
        assert resp.status_code == 404

    def test_update_requires_admin(self, regular_client, user_db):
        user = user_db.create_user(username="alice", role="user")
        resp = regular_client.put(
            f"/api/admin/users/{user['id']}",
            json={"role": "admin"},
        )
        assert resp.status_code == 403

    def test_update_proxy_role_rejected(self, admin_client, user_db):
        user = user_db.create_user(username="proxyuser", role="user", auth_source="proxy")

        resp = admin_client.put(
            f"/api/admin/users/{user['id']}",
            json={"role": "admin"},
        )

        assert resp.status_code == 400
        assert "Cannot change role for PROXY users" in resp.json["error"]

    def test_update_proxy_role_noop_allowed(self, admin_client, user_db):
        user = user_db.create_user(username="proxyuser", role="user", auth_source="proxy")

        resp = admin_client.put(
            f"/api/admin/users/{user['id']}",
            json={"role": "user", "display_name": "Proxy User"},
        )

        assert resp.status_code == 200
        assert resp.json["display_name"] == "Proxy User"

    def test_update_cwa_email_rejected(self, admin_client, user_db):
        user = user_db.create_user(
            username="cwauser",
            email="old@example.com",
            auth_source="cwa",
        )

        resp = admin_client.put(
            f"/api/admin/users/{user['id']}",
            json={"email": "new@example.com"},
        )

        assert resp.status_code == 400
        assert "Cannot change email for CWA users" in resp.json["error"]

    def test_update_oidc_email_rejected(self, admin_client, user_db):
        user = user_db.create_user(
            username="oidcuser",
            email="old@example.com",
            oidc_subject="sub-oidc-1",
            auth_source="oidc",
        )

        resp = admin_client.put(
            f"/api/admin/users/{user['id']}",
            json={"email": "new@example.com"},
        )

        assert resp.status_code == 400
        assert "Cannot change email for OIDC users" in resp.json["error"]


# ---------------------------------------------------------------------------
# PUT /api/admin/users/<id> — password update
# ---------------------------------------------------------------------------


class TestAdminUserPasswordUpdate:
    """Tests for password update via PUT /api/admin/users/<id>."""

    def test_update_password(self, admin_client, user_db):
        """Setting a new password should hash and store it."""
        user = user_db.create_user(username="alice", password_hash="old_hash")

        resp = admin_client.put(
            f"/api/admin/users/{user['id']}",
            json={"password": "newpass99"},
        )
        assert resp.status_code == 200

        updated = user_db.get_user(user_id=user["id"])
        assert updated["password_hash"] != "old_hash"
        assert updated["password_hash"].startswith("scrypt:") or updated[
            "password_hash"
        ].startswith("pbkdf2:")

    def test_update_password_too_short(self, admin_client, user_db):
        """Password shorter than 4 characters should be rejected."""
        user = user_db.create_user(username="alice", password_hash="old_hash")

        resp = admin_client.put(
            f"/api/admin/users/{user['id']}",
            json={"password": "ab"},
        )
        assert resp.status_code == 400
        assert "4 characters" in resp.json["error"]

    def test_update_password_empty_string_ignored(self, admin_client, user_db):
        """Empty password string should not change existing hash."""
        user = user_db.create_user(username="alice", password_hash="original_hash")

        resp = admin_client.put(
            f"/api/admin/users/{user['id']}",
            json={"password": ""},
        )
        assert resp.status_code == 200

        updated = user_db.get_user(user_id=user["id"])
        assert updated["password_hash"] == "original_hash"

    def test_update_password_with_other_fields(self, admin_client, user_db):
        """Password update should work alongside other field updates."""
        user = user_db.create_user(username="alice", role="user", password_hash="old")

        resp = admin_client.put(
            f"/api/admin/users/{user['id']}",
            json={"password": "newpass99", "role": "admin"},
        )
        assert resp.status_code == 200
        assert resp.json["role"] == "admin"

        updated = user_db.get_user(user_id=user["id"])
        assert updated["password_hash"] != "old"

    def test_update_password_hash_not_in_response(self, admin_client, user_db):
        """Response should never contain password_hash."""
        user = user_db.create_user(username="alice", password_hash="old")

        resp = admin_client.put(
            f"/api/admin/users/{user['id']}",
            json={"password": "newpass99"},
        )
        assert resp.status_code == 200
        assert "password_hash" not in resp.json
        assert "password" not in resp.json

    def test_update_password_rejected_for_proxy_user(self, admin_client, user_db):
        user = user_db.create_user(username="proxyuser", auth_source="proxy")

        resp = admin_client.put(
            f"/api/admin/users/{user['id']}",
            json={"password": "newpass99"},
        )

        assert resp.status_code == 400
        assert "Cannot set password for PROXY users" in resp.json["error"]

    def test_update_password_not_applied_when_role_is_rejected(self, admin_client, user_db):
        """A rejected role leaves the stored password untouched."""
        user = user_db.create_user(username="alice", role="user", password_hash="old_hash")

        resp = admin_client.put(
            f"/api/admin/users/{user['id']}",
            json={"password": "newpass99", "role": "superuser"},
        )
        assert resp.status_code == 400
        assert resp.json["error"] == "Role must be 'admin' or 'user'"

        updated = user_db.get_user(user_id=user["id"])
        assert updated["password_hash"] == "old_hash"
        assert updated["role"] == "user"

    def test_update_password_not_applied_when_settings_are_rejected(self, admin_client, user_db):
        """A rejected settings payload leaves the password and fields untouched."""
        user = user_db.create_user(username="alice", role="user", password_hash="old_hash")

        resp = admin_client.put(
            f"/api/admin/users/{user['id']}",
            json={
                "password": "newpass99",
                "role": "admin",
                "settings": {"BOOK_LANGUAGE": ["klingon"]},
            },
        )
        assert resp.status_code == 400
        assert resp.json["error"] == "Invalid settings payload"

        updated = user_db.get_user(user_id=user["id"])
        assert updated["password_hash"] == "old_hash"
        assert updated["role"] == "user"


# ---------------------------------------------------------------------------
# POST /api/admin/users/sync-cwa
# ---------------------------------------------------------------------------


class TestAdminSyncCwaUsersEndpoint:
    """Tests for POST /api/admin/users/sync-cwa."""

    def test_sync_cwa_users_links_by_email_and_avoids_username_overwrite(
        self,
        admin_client,
        user_db,
        tmp_path,
    ):
        cwa_db_path = tmp_path / "app.db"
        conn = sqlite3.connect(cwa_db_path)
        try:
            conn.execute(
                """
                CREATE TABLE user (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT,
                    role INTEGER,
                    email TEXT
                )
                """
            )
            conn.executemany(
                "INSERT INTO user (name, role, email) VALUES (?, ?, ?)",
                [
                    ("alice", 1, "alice@example.com"),
                    ("bob", 0, "bob@example.com"),
                    (" ", 1, "skip@example.com"),
                ],
            )
            conn.commit()
        finally:
            conn.close()

        local_email_match = user_db.create_user(
            username="alice_local",
            email="alice@example.com",
            role="user",
            auth_source="builtin",
        )
        local_username_collision = user_db.create_user(
            username="bob",
            email="old@example.com",
            role="admin",
            auth_source="builtin",
        )
        stale_cwa = user_db.create_user(
            username="stale__cwa",
            email="stale@example.com",
            role="user",
            auth_source="cwa",
        )

        with patch("shelfmark.core.admin_routes.load_active_auth_mode", return_value="cwa"):
            with patch("shelfmark.core.admin_routes.CWA_DB_PATH", cwa_db_path):
                resp = admin_client.post("/api/admin/users/sync-cwa")

        assert resp.status_code == 200
        assert resp.json["success"] is True
        assert resp.json["created"] == 1
        assert resp.json["updated"] == 1
        assert resp.json["deleted"] == 1
        assert resp.json["total"] == 2

        alice_linked = user_db.get_user(user_id=local_email_match["id"])
        assert alice_linked is not None
        assert alice_linked["username"] == "alice_local"
        assert alice_linked["auth_source"] == "cwa"
        assert alice_linked["role"] == "admin"
        assert alice_linked["email"] == "alice@example.com"

        bob_original = user_db.get_user(user_id=local_username_collision["id"])
        assert bob_original is not None
        assert bob_original["username"] == "bob"
        assert bob_original["auth_source"] == "builtin"
        assert bob_original["role"] == "admin"
        assert bob_original["email"] == "old@example.com"

        bob_cwa = next(
            user
            for user in user_db.list_users()
            if user.get("auth_source") == "cwa" and user.get("email") == "bob@example.com"
        )
        assert bob_cwa["username"].startswith("bob__cwa")
        assert bob_cwa["role"] == "user"
        assert user_db.get_user(user_id=stale_cwa["id"]) is None

    def test_sync_cwa_users_rejected_when_not_in_cwa_mode(self, admin_client):
        with patch("shelfmark.core.admin_routes.load_active_auth_mode", return_value="builtin"):
            resp = admin_client.post("/api/admin/users/sync-cwa")

        assert resp.status_code == 400
        assert "only available" in resp.json["error"]

    def test_sync_cwa_users_returns_503_when_db_unavailable(self, admin_client, tmp_path):
        missing_db_path = tmp_path / "missing.db"
        with patch("shelfmark.core.admin_routes.load_active_auth_mode", return_value="cwa"):
            with patch("shelfmark.core.admin_routes.CWA_DB_PATH", missing_db_path):
                resp = admin_client.post("/api/admin/users/sync-cwa")

        assert resp.status_code == 503
        assert "not available" in resp.json["error"]


# ---------------------------------------------------------------------------
# GET /api/admin/download-defaults
# ---------------------------------------------------------------------------


class TestAdminDownloadDefaults:
    """Tests for GET /api/admin/download-defaults."""

    @pytest.fixture(autouse=True)
    def setup_config(self, tmp_path, monkeypatch):
        """Create a temporary downloads config file."""
        import json
        from pathlib import Path

        from shelfmark.core.config import config as app_config

        config_dir = str(tmp_path)
        monkeypatch.setenv("CONFIG_DIR", config_dir)
        monkeypatch.delenv("INGEST_DIR", raising=False)
        monkeypatch.setattr("shelfmark.config.env.CONFIG_DIR", Path(config_dir))
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        config = {
            "BOOKS_OUTPUT_MODE": "folder",
            "DESTINATION": "/books",
            "DESTINATION_AUDIOBOOK": "/audiobooks",
            "BOOKLORE_LIBRARY_ID": "2",
            "BOOKLORE_PATH_ID": "5",
            "EMAIL_RECIPIENT": "reader@example.com",
        }
        (plugins_dir / "downloads.json").write_text(json.dumps(config))
        app_config.refresh(force=True)
        yield
        app_config.refresh(force=True)

    def test_returns_download_defaults(self, admin_client):
        resp = admin_client.get("/api/admin/download-defaults")
        assert resp.status_code == 200
        data = resp.json
        assert data["BOOKS_OUTPUT_MODE"] == "folder"
        assert data["DESTINATION"] == "/books"
        assert data["DESTINATION_AUDIOBOOK"] == "/audiobooks"
        assert data["BOOKLORE_LIBRARY_ID"] == "2"
        assert data["BOOKLORE_PATH_ID"] == "5"
        assert data["EMAIL_RECIPIENT"] == "reader@example.com"

    def test_returns_defaults_when_no_config(self, admin_client, tmp_path):
        """If no downloads config file exists, return sensible defaults."""

        config_path = tmp_path / "plugins" / "downloads.json"
        if config_path.exists():
            os.remove(config_path)

        resp = admin_client.get("/api/admin/download-defaults")
        assert resp.status_code == 200
        data = resp.json
        assert "BOOKS_OUTPUT_MODE" in data
        assert "DESTINATION" in data
        assert "DESTINATION_AUDIOBOOK" in data

    def test_requires_admin(self, regular_client):
        resp = regular_client.get("/api/admin/download-defaults")
        assert resp.status_code == 403


class TestAdminBookloreOptions:
    """Tests for GET /api/admin/booklore-options."""

    def test_returns_library_and_path_options(self, admin_client, monkeypatch):
        mock_libraries = [{"value": "1", "label": "My Library"}]
        mock_paths = [{"value": "10", "label": "My Library: /books", "childOf": "1"}]
        monkeypatch.setattr(
            "shelfmark.core.admin_routes.get_booklore_library_options",
            lambda: mock_libraries,
        )
        monkeypatch.setattr(
            "shelfmark.core.admin_routes.get_booklore_path_options",
            lambda: mock_paths,
        )
        resp = admin_client.get("/api/admin/booklore-options")
        assert resp.status_code == 200
        data = resp.json
        assert data["libraries"] == mock_libraries
        assert data["paths"] == mock_paths

    def test_returns_empty_when_not_configured(self, admin_client, monkeypatch):
        monkeypatch.setattr(
            "shelfmark.core.admin_routes.get_booklore_library_options",
            lambda: [],
        )
        monkeypatch.setattr(
            "shelfmark.core.admin_routes.get_booklore_path_options",
            lambda: [],
        )
        resp = admin_client.get("/api/admin/booklore-options")
        assert resp.status_code == 200
        data = resp.json
        assert data["libraries"] == []
        assert data["paths"] == []

    def test_requires_admin(self, regular_client):
        resp = regular_client.get("/api/admin/booklore-options")
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# GET /api/admin/users/<id>/delivery-preferences
# ---------------------------------------------------------------------------


class TestAdminDeliveryPreferences:
    """Tests for GET /api/admin/users/<id>/delivery-preferences."""

    @pytest.fixture(autouse=True)
    def setup_config(self, tmp_path, monkeypatch):
        import json
        from pathlib import Path

        config_dir = str(tmp_path)
        monkeypatch.setenv("CONFIG_DIR", config_dir)
        monkeypatch.setattr("shelfmark.config.env.CONFIG_DIR", Path(config_dir))

        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        downloads_config = {
            "BOOKS_OUTPUT_MODE": "folder",
            "DESTINATION": "/books",
            "DESTINATION_AUDIOBOOK": "/audiobooks",
            "BOOKLORE_LIBRARY_ID": "7",
            "BOOKLORE_PATH_ID": "21",
            "EMAIL_RECIPIENT": "global@example.com",
        }
        (plugins_dir / "downloads.json").write_text(json.dumps(downloads_config))

        from shelfmark.core.config import config as app_config

        app_config.refresh(force=True)

    def test_returns_curated_fields_and_effective_values(self, admin_client, user_db):
        user = user_db.create_user(username="alice")
        user_db.set_user_settings(
            user["id"],
            {
                "BOOKS_OUTPUT_MODE": "email",
                "EMAIL_RECIPIENT": "alice@example.com",
                "DESTINATION_AUDIOBOOK": "/audiobooks/alice",
            },
        )

        resp = admin_client.get(f"/api/admin/users/{user['id']}/delivery-preferences")
        assert resp.status_code == 200

        data = resp.json
        assert data["tab"] == "downloads"
        assert data["keys"] == [
            "BOOKS_OUTPUT_MODE",
            "DESTINATION",
            "BOOKLORE_LIBRARY_ID",
            "BOOKLORE_PATH_ID",
            "EMAIL_RECIPIENT",
            "DESTINATION_AUDIOBOOK",
            "DOWNLOAD_TO_BROWSER_CONTENT_TYPES",
        ]

        field_keys = [field["key"] for field in data["fields"]]
        assert set(field_keys) == set(data["keys"])

        assert data["userOverrides"]["BOOKS_OUTPUT_MODE"] == "email"
        assert data["userOverrides"]["EMAIL_RECIPIENT"] == "alice@example.com"
        assert data["userOverrides"]["DESTINATION_AUDIOBOOK"] == "/audiobooks/alice"

        assert data["effective"]["BOOKS_OUTPUT_MODE"]["source"] == "user_override"
        assert data["effective"]["BOOKS_OUTPUT_MODE"]["value"] == "email"
        assert data["effective"]["DESTINATION"]["source"] in {"global_config", "env_var"}
        assert data["effective"]["BOOKLORE_LIBRARY_ID"]["source"] == "global_config"
        assert data["effective"]["BOOKLORE_LIBRARY_ID"]["value"] == "7"
        assert data["effective"]["EMAIL_RECIPIENT"]["source"] == "user_override"
        assert data["effective"]["EMAIL_RECIPIENT"]["value"] == "alice@example.com"
        assert data["effective"]["DESTINATION_AUDIOBOOK"]["source"] == "user_override"
        assert data["effective"]["DESTINATION_AUDIOBOOK"]["value"] == "/audiobooks/alice"

    def test_returns_404_for_unknown_user(self, admin_client):
        resp = admin_client.get("/api/admin/users/9999/delivery-preferences")
        assert resp.status_code == 404

    def test_requires_admin(self, regular_client, user_db):
        user = user_db.create_user(username="alice")
        resp = regular_client.get(f"/api/admin/users/{user['id']}/delivery-preferences")
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# GET /api/admin/users/<id>/search-preferences
# ---------------------------------------------------------------------------


class TestAdminSearchPreferences:
    """Tests for GET /api/admin/users/<id>/search-preferences."""

    @pytest.fixture(autouse=True)
    def setup_config(self, tmp_path, monkeypatch):
        import json
        from pathlib import Path

        config_dir = str(tmp_path)
        monkeypatch.setenv("CONFIG_DIR", config_dir)
        monkeypatch.setattr("shelfmark.config.env.CONFIG_DIR", Path(config_dir))

        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        search_mode_config = {
            "SEARCH_MODE": "direct",
            "METADATA_PROVIDER": "openlibrary",
            "METADATA_PROVIDER_AUDIOBOOK": "",
            "DEFAULT_RELEASE_SOURCE": "direct_download",
            "DEFAULT_RELEASE_SOURCE_AUDIOBOOK": "",
        }
        (plugins_dir / "search_mode.json").write_text(json.dumps(search_mode_config))

        from shelfmark.core.config import config as app_config

        app_config.refresh(force=True)

    def test_returns_curated_fields_and_effective_values(self, admin_client, user_db):
        user = user_db.create_user(username="alice")
        user_db.set_user_settings(
            user["id"],
            {
                "SEARCH_MODE": "universal",
                "METADATA_PROVIDER": "openlibrary",
                "DEFAULT_RELEASE_SOURCE": "prowlarr",
                "DEFAULT_RELEASE_SOURCE_AUDIOBOOK": "audiobookbay",
            },
        )

        resp = admin_client.get(f"/api/admin/users/{user['id']}/search-preferences")
        assert resp.status_code == 200

        data = resp.json
        assert data["tab"] == "search_mode"
        assert data["keys"] == [
            "SEARCH_MODE",
            "BOOK_LANGUAGE",
            "DEFAULT_CONTENT_TYPE",
            "SHOW_COMBINED_SELECTOR",
            "FORCE_COMBINED_SEARCH",
            "METADATA_PROVIDER",
            "METADATA_PROVIDER_AUDIOBOOK",
            "METADATA_PROVIDER_COMBINED",
            "DEFAULT_RELEASE_SOURCE",
            "DEFAULT_RELEASE_SOURCE_AUDIOBOOK",
            "SHOW_SERIES_COLUMN",
            "SHOW_NARRATOR_COLUMN",
            "SHOW_BITRATE_COLUMN",
        ]

        field_keys = [field["key"] for field in data["fields"]]
        assert set(field_keys) == set(data["keys"])

        assert data["userOverrides"]["SEARCH_MODE"] == "universal"
        assert data["userOverrides"]["METADATA_PROVIDER"] == "openlibrary"
        assert data["userOverrides"]["DEFAULT_RELEASE_SOURCE"] == "prowlarr"
        assert data["userOverrides"]["DEFAULT_RELEASE_SOURCE_AUDIOBOOK"] == "audiobookbay"

        assert data["effective"]["SEARCH_MODE"]["source"] == "user_override"
        assert data["effective"]["SEARCH_MODE"]["value"] == "universal"
        assert data["effective"]["METADATA_PROVIDER"]["source"] == "user_override"
        assert data["effective"]["METADATA_PROVIDER_AUDIOBOOK"]["source"] in {
            "global_config",
            "default",
        }
        assert data["effective"]["DEFAULT_RELEASE_SOURCE"]["source"] == "user_override"
        assert data["effective"]["DEFAULT_RELEASE_SOURCE"]["value"] == "prowlarr"
        assert data["effective"]["DEFAULT_RELEASE_SOURCE_AUDIOBOOK"]["source"] == "user_override"
        assert data["effective"]["DEFAULT_RELEASE_SOURCE_AUDIOBOOK"]["value"] == "audiobookbay"

    def test_reports_book_language_override(self, admin_client, user_db):
        user = user_db.create_user(username="alice")
        user_db.set_user_settings(user["id"], {"BOOK_LANGUAGE": ["de", "en"]})

        resp = admin_client.get(f"/api/admin/users/{user['id']}/search-preferences")
        assert resp.status_code == 200

        data = resp.json
        assert data["userOverrides"]["BOOK_LANGUAGE"] == ["de", "en"]
        assert data["effective"]["BOOK_LANGUAGE"] == {
            "value": ["de", "en"],
            "source": "user_override",
        }
        assert data["globalValues"]["BOOK_LANGUAGE"] == ["en"]

    def test_returns_404_for_unknown_user(self, admin_client):
        resp = admin_client.get("/api/admin/users/9999/search-preferences")
        assert resp.status_code == 404

    def test_requires_admin(self, regular_client, user_db):
        user = user_db.create_user(username="alice")
        resp = regular_client.get(f"/api/admin/users/{user['id']}/search-preferences")
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# GET /api/admin/users/<id>/notification-preferences
# ---------------------------------------------------------------------------


class TestAdminNotificationPreferences:
    """Tests for GET /api/admin/users/<id>/notification-preferences."""

    @pytest.fixture(autouse=True)
    def setup_config(self, tmp_path, monkeypatch):
        import json
        from pathlib import Path

        config_dir = str(tmp_path)
        monkeypatch.setenv("CONFIG_DIR", config_dir)
        monkeypatch.setattr("shelfmark.config.env.CONFIG_DIR", Path(config_dir))

        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        notifications_config = {
            "ADMIN_NOTIFICATION_ROUTES": [
                {"event": "all", "url": "ntfys://ntfy.sh/admin"},
                {"event": "download_failed", "url": "ntfys://ntfy.sh/admin-errors"},
            ],
            "USER_NOTIFICATION_ROUTES": [
                {"event": "all", "url": "ntfys://ntfy.sh/default-user"},
            ],
        }
        (plugins_dir / "notifications.json").write_text(json.dumps(notifications_config))

        from shelfmark.core.config import config as app_config

        app_config.refresh(force=True)

    def test_returns_curated_fields_and_effective_values(self, admin_client, user_db):
        user = user_db.create_user(username="alice")
        user_db.set_user_settings(
            user["id"],
            {
                "USER_NOTIFICATION_ROUTES": [
                    {"event": "all", "url": "ntfys://ntfy.sh/alice"},
                    {"event": "download_failed", "url": "ntfys://ntfy.sh/alice-errors"},
                ],
            },
        )

        resp = admin_client.get(f"/api/admin/users/{user['id']}/notification-preferences")
        assert resp.status_code == 200

        data = resp.json
        assert data["tab"] == "notifications"
        assert data["keys"] == [
            "USER_NOTIFICATION_ROUTES",
        ]

        field_keys = [field["key"] for field in data["fields"]]
        assert set(field_keys) == set(data["keys"])

        assert data["userOverrides"]["USER_NOTIFICATION_ROUTES"] == [
            {"event": "all", "url": "ntfys://ntfy.sh/alice"},
            {"event": "download_failed", "url": "ntfys://ntfy.sh/alice-errors"},
        ]

        assert data["effective"]["USER_NOTIFICATION_ROUTES"]["source"] == "user_override"

    def test_returns_404_for_unknown_user(self, admin_client):
        resp = admin_client.get("/api/admin/users/9999/notification-preferences")
        assert resp.status_code == 404

    def test_requires_admin(self, regular_client, user_db):
        user = user_db.create_user(username="alice")
        resp = regular_client.get(f"/api/admin/users/{user['id']}/notification-preferences")
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# POST /api/admin/users/<id>/notification-preferences/test
# ---------------------------------------------------------------------------


class TestAdminNotificationPreferencesTestAction:
    """Tests for POST /api/admin/users/<id>/notification-preferences/test."""

    @pytest.fixture(autouse=True)
    def setup_config(self, tmp_path, monkeypatch):
        import json
        from pathlib import Path

        config_dir = str(tmp_path)
        monkeypatch.setenv("CONFIG_DIR", config_dir)
        monkeypatch.setattr("shelfmark.config.env.CONFIG_DIR", Path(config_dir))

        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        notifications_config = {
            "ADMIN_NOTIFICATION_ROUTES": [
                {"event": "all", "url": "ntfys://ntfy.sh/admin"},
            ],
            "USER_NOTIFICATION_ROUTES": [
                {"event": "all", "url": "ntfys://ntfy.sh/default-user"},
            ],
        }
        (plugins_dir / "notifications.json").write_text(json.dumps(notifications_config))

        from shelfmark.core.config import config as app_config

        app_config.refresh(force=True)

    def test_requires_admin(self, regular_client, user_db):
        user = user_db.create_user(username="alice")
        resp = regular_client.post(
            f"/api/admin/users/{user['id']}/notification-preferences/test",
            json={"USER_NOTIFICATION_ROUTES": [{"event": "all", "url": "ntfys://ntfy.sh/alice"}]},
        )
        assert resp.status_code == 403

    def test_returns_404_for_unknown_user(self, admin_client):
        resp = admin_client.post("/api/admin/users/9999/notification-preferences/test", json={})
        assert resp.status_code == 404

    def test_uses_payload_routes_when_provided(self, admin_client, user_db):
        user = user_db.create_user(username="alice")

        with patch(
            "shelfmark.config.notifications_settings.send_test_notification",
            return_value={"success": True, "message": "ok"},
        ) as mock_send:
            resp = admin_client.post(
                f"/api/admin/users/{user['id']}/notification-preferences/test",
                json={
                    "USER_NOTIFICATION_ROUTES": [
                        {"event": "all", "url": " ntfys://ntfy.sh/alice "},
                        {"event": "download_failed", "url": "ntfys://ntfy.sh/alice-errors"},
                        {"event": "download_failed", "url": "ntfys://ntfy.sh/alice-errors"},
                    ]
                },
            )

        assert resp.status_code == 200
        assert resp.json["success"] is True
        mock_send.assert_called_once_with(["ntfys://ntfy.sh/alice", "ntfys://ntfy.sh/alice-errors"])

    def test_uses_effective_routes_when_payload_missing(self, admin_client, user_db):
        user = user_db.create_user(username="alice")

        with patch(
            "shelfmark.config.notifications_settings.send_test_notification",
            return_value={"success": True, "message": "ok"},
        ) as mock_send:
            resp = admin_client.post(
                f"/api/admin/users/{user['id']}/notification-preferences/test",
            )

        assert resp.status_code == 200
        assert resp.json["success"] is True
        mock_send.assert_called_once_with(["ntfys://ntfy.sh/default-user"])

    def test_rejects_invalid_urls(self, admin_client, user_db):
        user = user_db.create_user(username="alice")

        resp = admin_client.post(
            f"/api/admin/users/{user['id']}/notification-preferences/test",
            json={"USER_NOTIFICATION_ROUTES": [{"event": "all", "url": "not-a-valid-url"}]},
        )

        assert resp.status_code == 400
        assert "invalid personal notification URL" in resp.json["message"]

    def test_requires_at_least_one_url(self, admin_client, user_db):
        user = user_db.create_user(username="alice")

        resp = admin_client.post(
            f"/api/admin/users/{user['id']}/notification-preferences/test",
            json={"USER_NOTIFICATION_ROUTES": [{"event": "all", "url": ""}]},
        )

        assert resp.status_code == 400
        assert "Add at least one personal notification URL route first." in resp.json["message"]


# ---------------------------------------------------------------------------
# GET /api/admin/settings/overrides-summary
# ---------------------------------------------------------------------------


class TestAdminOverridesSummary:
    """Tests for GET /api/admin/settings/overrides-summary."""

    def test_returns_override_counts_for_downloads_tab(self, admin_client, user_db):
        alice = user_db.create_user(username="alice")
        bob = user_db.create_user(username="bob")

        user_db.set_user_settings(
            alice["id"],
            {"BOOKS_OUTPUT_MODE": "folder", "DESTINATION": "/books/alice"},
        )
        user_db.set_user_settings(
            bob["id"],
            {
                "BOOKS_OUTPUT_MODE": "email",
                "DESTINATION": "/books/bob",
                "EMAIL_RECIPIENT": "bob@example.com",
            },
        )

        resp = admin_client.get("/api/admin/settings/overrides-summary?tab=downloads")
        assert resp.status_code == 200

        data = resp.json
        assert data["tab"] == "downloads"
        keys = data["keys"]

        assert keys["BOOKS_OUTPUT_MODE"]["count"] == 2
        assert keys["DESTINATION"]["count"] == 2
        assert keys["EMAIL_RECIPIENT"]["count"] == 1
        assert "BOOKLORE_LIBRARY_ID" not in keys

        destination_users = {u["username"] for u in keys["DESTINATION"]["users"]}
        assert destination_users == {"alice", "bob"}

        email_users = keys["EMAIL_RECIPIENT"]["users"]
        assert len(email_users) == 1
        assert email_users[0]["username"] == "bob"
        assert email_users[0]["value"] == "bob@example.com"

    def test_returns_404_for_unknown_tab(self, admin_client):
        resp = admin_client.get("/api/admin/settings/overrides-summary?tab=does-not-exist")
        assert resp.status_code == 404

    def test_requires_admin(self, regular_client):
        resp = regular_client.get("/api/admin/settings/overrides-summary?tab=downloads")
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# GET /api/admin/users/<id>/effective-settings
# ---------------------------------------------------------------------------


class TestAdminEffectiveSettings:
    """Tests for GET /api/admin/users/<id>/effective-settings."""

    @pytest.fixture(autouse=True)
    def setup_config(self, tmp_path, monkeypatch):
        import json
        from pathlib import Path

        config_dir = str(tmp_path)
        monkeypatch.setenv("CONFIG_DIR", config_dir)
        monkeypatch.setattr("shelfmark.config.env.CONFIG_DIR", Path(config_dir))

        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        downloads_config = {
            "BOOKS_OUTPUT_MODE": "booklore",
            "BOOKLORE_LIBRARY_ID": "7",
        }
        (plugins_dir / "downloads.json").write_text(json.dumps(downloads_config))

        monkeypatch.setenv("INGEST_DIR", "/env/books")

        # Ensure config singleton sees the current test env/config dir.
        from shelfmark.core.config import config as app_config

        app_config.refresh(force=True)

    def test_returns_effective_values_with_sources(self, admin_client, user_db):
        user = user_db.create_user(username="alice")
        user_db.set_user_settings(
            user["id"],
            {"EMAIL_RECIPIENT": "alice@kindle.com"},
        )

        resp = admin_client.get(f"/api/admin/users/{user['id']}/effective-settings")
        assert resp.status_code == 200

        data = resp.json
        assert data["DESTINATION"]["value"] == "/env/books"
        assert data["DESTINATION"]["source"] == "env_var"

        assert data["BOOKLORE_LIBRARY_ID"]["value"] == "7"
        assert data["BOOKLORE_LIBRARY_ID"]["source"] == "global_config"

        assert data["BOOKLORE_PATH_ID"]["value"] in ("", None)
        assert data["BOOKLORE_PATH_ID"]["source"] == "default"

        assert data["EMAIL_RECIPIENT"]["value"] == "alice@kindle.com"
        assert data["EMAIL_RECIPIENT"]["source"] == "user_override"

    def test_returns_404_for_unknown_user(self, admin_client):
        resp = admin_client.get("/api/admin/users/9999/effective-settings")
        assert resp.status_code == 404

    def test_requires_admin(self, regular_client, user_db):
        user = user_db.create_user(username="alice")
        resp = regular_client.get(f"/api/admin/users/{user['id']}/effective-settings")
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# DELETE /api/admin/users/<id>
# ---------------------------------------------------------------------------


class TestAdminUserDeleteEndpoint:
    """Tests for DELETE /api/admin/users/<id>."""

    def test_delete_user(self, admin_client, user_db):
        user = user_db.create_user(username="alice")

        resp = admin_client.delete(f"/api/admin/users/{user['id']}")
        assert resp.status_code == 200
        assert resp.json["success"] is True
        assert user_db.get_user(user_id=user["id"]) is None

    def test_delete_nonexistent_user(self, admin_client):
        resp = admin_client.delete("/api/admin/users/9999")
        assert resp.status_code == 404

    def test_delete_requires_admin(self, regular_client, user_db):
        user = user_db.create_user(username="alice")
        resp = regular_client.delete(f"/api/admin/users/{user['id']}")
        assert resp.status_code == 403

    def test_delete_user_removes_from_list(self, admin_client, user_db):
        user = user_db.create_user(username="alice")
        user_db.create_user(username="bob")

        admin_client.delete(f"/api/admin/users/{user['id']}")

        resp = admin_client.get("/api/admin/users")
        assert len(resp.json) == 1
        assert resp.json[0]["username"] == "bob"

    def test_delete_active_proxy_user_allowed(self, admin_client, user_db):
        user = user_db.create_user(username="proxyuser", auth_source="proxy")

        with patch("shelfmark.core.admin_routes.load_active_auth_mode", return_value="proxy"):
            resp = admin_client.delete(f"/api/admin/users/{user['id']}")

        assert resp.status_code == 200
        assert resp.json["success"] is True

    def test_delete_active_cwa_user_rejected(self, admin_client, user_db):
        user = user_db.create_user(username="cwauser", auth_source="cwa")

        with patch("shelfmark.core.admin_routes.load_active_auth_mode", return_value="cwa"):
            resp = admin_client.delete(f"/api/admin/users/{user['id']}")

        assert resp.status_code == 400
        assert "Cannot delete active CWA users" in resp.json["error"]

    def test_delete_inactive_proxy_user_allowed(self, admin_client, user_db):
        user = user_db.create_user(username="proxyuser", auth_source="proxy")

        with patch("shelfmark.core.admin_routes.load_active_auth_mode", return_value="builtin"):
            resp = admin_client.delete(f"/api/admin/users/{user['id']}")

        assert resp.status_code == 200
        assert resp.json["success"] is True

    def test_delete_active_oidc_user_allowed_when_auto_provision_enabled(
        self, admin_client, user_db
    ):
        user = user_db.create_user(
            username="oidcuser",
            oidc_subject="sub-123",
            auth_source="oidc",
        )

        with patch("shelfmark.core.admin_routes.load_active_auth_mode", return_value="oidc"):
            resp = admin_client.delete(f"/api/admin/users/{user['id']}")

        assert resp.status_code == 200
        assert resp.json["success"] is True

    @pytest.mark.parametrize("auth_mode", ["builtin", "oidc"])
    def test_delete_last_local_admin_rejected(self, admin_client, user_db, auth_mode):
        """Regression for #1387: removing the last local admin used to disable auth."""
        user = user_db.create_user(
            username="onlyadmin",
            password_hash="hashed_pw",
            role="admin",
        )

        with patch("shelfmark.core.admin_routes.load_active_auth_mode", return_value=auth_mode):
            resp = admin_client.delete(f"/api/admin/users/{user['id']}")

        assert resp.status_code == 400
        assert resp.json["error"] == "Cannot delete the last local admin account"
        assert user_db.get_user(user_id=user["id"]) is not None

    def test_delete_local_admin_allowed_when_another_remains(self, admin_client, user_db):
        user = user_db.create_user(username="admin1", password_hash="hashed_pw", role="admin")
        user_db.create_user(username="admin2", password_hash="hashed_pw", role="admin")

        with patch("shelfmark.core.admin_routes.load_active_auth_mode", return_value="builtin"):
            resp = admin_client.delete(f"/api/admin/users/{user['id']}")

        assert resp.status_code == 200
        assert user_db.get_user(user_id=user["id"]) is None

    @pytest.mark.parametrize("auth_mode", ["none", "proxy"])
    def test_delete_last_local_admin_allowed_without_local_auth(
        self, admin_client, user_db, auth_mode
    ):
        user = user_db.create_user(username="onlyadmin", password_hash="hashed_pw", role="admin")

        with patch("shelfmark.core.admin_routes.load_active_auth_mode", return_value=auth_mode):
            resp = admin_client.delete(f"/api/admin/users/{user['id']}")

        assert resp.status_code == 200
        assert user_db.get_user(user_id=user["id"]) is None

    def test_delete_last_local_admin_allowed_when_local_auth_disabled(self, admin_client, user_db):
        user = user_db.create_user(username="onlyadmin", password_hash="hashed_pw", role="admin")

        with (
            patch("shelfmark.core.admin_routes.load_active_auth_mode", return_value="oidc"),
            patch("shelfmark.core.admin_routes.DISABLE_LOCAL_AUTH", True),
        ):
            resp = admin_client.delete(f"/api/admin/users/{user['id']}")

        assert resp.status_code == 200
        assert user_db.get_user(user_id=user["id"]) is None

    def test_delete_own_account_rejected(self, admin_client, user_db):
        user = user_db.create_user(
            username="onlyadmin",
            password_hash="hashed_pw",
            role="admin",
        )

        with admin_client.session_transaction() as sess:
            sess["user_id"] = user["username"]
            sess["db_user_id"] = user["id"]
            sess["is_admin"] = True

        resp = admin_client.delete(f"/api/admin/users/{user['id']}")

        assert resp.status_code == 400
        assert resp.json["error"] == "Cannot delete your own account"
        assert user_db.get_user(user_id=user["id"]) is not None


# ---------------------------------------------------------------------------
# OIDC lockout prevention (security on_save handler)
# ---------------------------------------------------------------------------


class TestOIDCLockoutPrevention:
    """Tests for _on_save_security blocking OIDC without a local admin."""

    @pytest.fixture(autouse=True)
    def setup_config_dir(self, db_path, tmp_path, monkeypatch):
        """Point CONFIG_DIR to a temp dir so _on_save_security can find users.db."""
        config_dir = str(tmp_path)
        monkeypatch.setenv("CONFIG_DIR", config_dir)
        monkeypatch.setattr("shelfmark.config.env.CONFIG_DIR", tmp_path)
        # Create user_db at the path _on_save_security will look for
        self._user_db = UserDB(os.path.join(config_dir, "users.db"))
        self._user_db.initialize()

    def _call_on_save(self, values):
        from shelfmark.config.security import _on_save_security

        return _on_save_security(values)

    def test_oidc_blocked_without_local_admin(self):
        """OIDC should be blocked when no local password admin exists."""
        result = self._call_on_save({"AUTH_METHOD": "oidc"})
        assert result["error"] is True
        assert "local admin" in result["message"].lower()

    def test_oidc_blocked_with_oidc_only_admin(self):
        """OIDC admin without password should not count as local admin."""
        self._user_db.create_user(
            username="sso_admin",
            oidc_subject="sub123",
            role="admin",
        )
        result = self._call_on_save({"AUTH_METHOD": "oidc"})
        assert result["error"] is True

    def test_oidc_blocked_with_local_non_admin(self):
        """A local password user who is not admin should not unblock OIDC."""
        self._user_db.create_user(
            username="regular",
            password_hash="hashed_pw",
            role="user",
        )
        result = self._call_on_save({"AUTH_METHOD": "oidc"})
        assert result["error"] is True

    def test_oidc_allowed_with_local_admin(self):
        """OIDC should be allowed when a local password admin exists."""
        self._user_db.create_user(
            username="admin_user",
            password_hash="hashed_pw",
            role="admin",
        )
        result = self._call_on_save(
            {
                "AUTH_METHOD": "oidc",
                "OIDC_DISCOVERY_URL": "https://auth.example.com/.well-known/openid-configuration",
                "OIDC_CLIENT_ID": "shelfmark",
                "OIDC_CLIENT_SECRET": "secret123",
            }
        )
        assert result["error"] is False

    def test_external_methods_not_blocked(self):
        """Methods that do not rely on a local admin should not trigger the check."""
        for method in ("none", "proxy", "cwa"):
            result = self._call_on_save({"AUTH_METHOD": method})
            assert result["error"] is False, f"AUTH_METHOD={method} should not be blocked"

    def test_builtin_blocked_without_local_admin(self):
        """Local auth without a local admin would lock every admin out."""
        result = self._call_on_save({"AUTH_METHOD": "builtin"})
        assert result["error"] is True
        assert "local admin" in result["message"].lower()

    def test_oidc_check_preserves_values(self):
        """When OIDC is blocked, the original values should be returned."""
        values = {"AUTH_METHOD": "oidc", "OIDC_CLIENT_ID": "myapp"}
        result = self._call_on_save(values)
        assert result["values"]["OIDC_CLIENT_ID"] == "myapp"
