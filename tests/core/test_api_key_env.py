"""Tests for the SHELFMARK_API_KEY environment-variable authentication."""

from __future__ import annotations

import importlib
import logging
import os
import sqlite3
import tempfile
from unittest.mock import patch

import pytest

from shelfmark.core import api_key


@pytest.fixture
def user_db():
    from shelfmark.core.user_db import UserDB

    with tempfile.TemporaryDirectory() as tmpdir:
        db = UserDB(os.path.join(tmpdir, "users.db"))
        db.initialize()
        yield db


class TestExtractCandidates:
    def test_bearer_only(self):
        assert api_key.extract_api_key_candidates("Bearer a", None) == ["a"]

    def test_bearer_case_insensitive_and_trimmed(self):
        assert api_key.extract_api_key_candidates("bearer  a ", None) == ["a"]

    def test_x_api_key_only(self):
        assert api_key.extract_api_key_candidates(None, "b") == ["b"]

    def test_both_present(self):
        assert api_key.extract_api_key_candidates("Bearer a", "b") == ["a", "b"]

    def test_non_bearer_scheme_plus_x_api_key(self):
        assert api_key.extract_api_key_candidates("Basic dXNlcjpwYXNz", "b") == ["b"]

    def test_empty(self):
        assert api_key.extract_api_key_candidates("Bearer ", "") == []
        assert api_key.extract_api_key_candidates(None, None) == []


class TestMatches:
    def test_unset_never_matches(self, monkeypatch):
        monkeypatch.setattr(api_key, "SHELFMARK_API_KEY", "")
        assert api_key.matches_api_key("anything") is False
        assert api_key.matches_api_key("") is False

    def test_match(self, monkeypatch):
        monkeypatch.setattr(api_key, "SHELFMARK_API_KEY", "s3cret")
        assert api_key.matches_api_key("s3cret") is True

    def test_mismatch_and_prefix(self, monkeypatch):
        monkeypatch.setattr(api_key, "SHELFMARK_API_KEY", "s3cret")
        assert api_key.matches_api_key("s3cre") is False
        assert api_key.matches_api_key("s3cret ") is False
        assert api_key.matches_api_key("") is False


class TestFirstAdmin:
    def test_none_when_no_admin(self, user_db):
        user_db.create_user(username="alice")
        assert user_db.get_first_admin() is None

    def test_first_admin_by_id(self, user_db):
        user_db.create_user(username="alice")
        root = user_db.create_user(username="root", role="admin")
        user_db.create_user(username="root2", role="admin")
        assert user_db.get_first_admin()["id"] == root["id"]


@pytest.fixture(scope="module")
def main_module():
    """Import `shelfmark.main` with background startup disabled."""
    with patch("shelfmark.download.orchestrator.start"):
        import shelfmark.main as main

        importlib.reload(main)
        return main


@pytest.fixture
def wired(main_module, user_db, monkeypatch):
    monkeypatch.setattr(main_module, "user_db", user_db)
    monkeypatch.setattr(api_key, "SHELFMARK_API_KEY", "s3cret")
    with patch.object(main_module, "get_auth_mode", return_value="builtin"):
        yield main_module


def _bearer(value):
    return {"Authorization": f"Bearer {value}"}


def _x_api_key(value):
    return {"X-Api-Key": value}


def _identity_for_key(wired):
    """Run the middleware directly against the isolated `user_db` and return the resulting session.

    Going through a real request/response round trip would need a route
    that reveals identity, and the only such route (`/api/users/me/edit-context`)
    is closed over the real per-worker `users.db` at registration time, not
    the isolated temp DB the `wired` fixture monkeypatches onto
    `main_module.user_db` -- so it can't see admins created here. Calling
    the middleware in a request context sidesteps that entirely.
    """
    with wired.app.test_request_context("/api/downloads/active", headers=_bearer("s3cret")):
        assert wired.api_key_auth_middleware() is None
        from flask import session

        return dict(session)


def _cookie_client(app, user, *, is_admin=False, permanent=False):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = user["username"]
        sess["is_admin"] = is_admin
        sess["db_user_id"] = user["id"]
        sess.permanent = permanent
    return client


class TestKeyedRequests:
    def test_match_reaches_login_required_route(self, wired, user_db):
        user_db.create_user(username="root", role="admin")
        assert (
            wired.app.test_client()
            .get("/api/downloads/active", headers=_bearer("s3cret"))
            .status_code
            == 200
        )

    def test_match_is_admin(self, wired, user_db):
        user_db.create_user(username="root", role="admin")
        assert (
            wired.app.test_client().get("/api/settings", headers=_bearer("s3cret")).status_code
            == 200
        )

    def test_match_without_any_admin_user_is_still_admin(self, wired):
        assert (
            wired.app.test_client().get("/api/settings", headers=_bearer("s3cret")).status_code
            == 200
        )

    def test_match_no_user_db(self, main_module, monkeypatch):
        monkeypatch.setattr(main_module, "user_db", None)
        monkeypatch.setattr(api_key, "SHELFMARK_API_KEY", "s3cret")
        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            assert (
                main_module.app.test_client()
                .get("/api/downloads/active", headers=_bearer("s3cret"))
                .status_code
                == 200
            )

    def test_x_api_key(self, wired, user_db):
        user_db.create_user(username="root", role="admin")
        assert (
            wired.app.test_client()
            .get("/api/downloads/active", headers={"X-Api-Key": "s3cret"})
            .status_code
            == 200
        )

    def test_wrong_bearer_plus_correct_x_api_key_authenticates(self, wired, user_db):
        """A proxy's own Authorization header must not shadow a correct X-Api-Key."""
        user_db.create_user(username="root", role="admin")
        headers = {**_bearer("wrong"), **_x_api_key("s3cret")}
        assert wired.app.test_client().get("/api/settings", headers=headers).status_code == 200

    def test_correct_bearer_plus_wrong_x_api_key_authenticates(self, wired, user_db):
        user_db.create_user(username="root", role="admin")
        headers = {**_bearer("s3cret"), **_x_api_key("wrong")}
        assert wired.app.test_client().get("/api/settings", headers=headers).status_code == 200

    def test_no_set_cookie_even_when_handler_dirties_session(self, wired, user_db, monkeypatch):
        user_db.create_user(username="root", role="admin")
        original = wired.app.view_functions["api_active_downloads"]

        def dirty(*args, **kwargs):
            from flask import session

            session["dirty"] = True
            return original(*args, **kwargs)

        monkeypatch.setitem(wired.app.view_functions, "api_active_downloads", dirty)
        response = wired.app.test_client().get("/api/downloads/active", headers=_bearer("s3cret"))
        assert response.status_code == 200
        assert "Set-Cookie" not in response.headers

    def test_incoming_non_admin_cookie_is_ignored(self, wired, user_db):
        user_db.create_user(username="root", role="admin")
        alice = user_db.create_user(username="alice")
        client = _cookie_client(wired.app, alice)
        assert client.get("/api/settings", headers=_bearer("s3cret")).status_code == 200

    def test_matched_key_leaves_browser_cookie_usable(self, wired, user_db):
        user_db.create_user(username="root", role="admin")
        alice = user_db.create_user(username="alice")
        client = _cookie_client(wired.app, alice)
        assert client.get("/api/settings", headers=_bearer("s3cret")).status_code == 200

        assert client.get("/api/downloads/active").status_code == 200
        assert client.get("/api/settings").status_code == 403

    def test_security_headers_present(self, wired, user_db):
        user_db.create_user(username="root", role="admin")
        response = wired.app.test_client().get("/api/downloads/active", headers=_bearer("s3cret"))
        assert response.headers.get("X-Content-Type-Options") == "nosniff"

    def test_store_error_is_500_not_anonymous(self, wired, user_db, monkeypatch):
        monkeypatch.setattr(
            user_db,
            "get_first_admin",
            lambda: (_ for _ in ()).throw(sqlite3.OperationalError("boom")),
        )
        response = wired.app.test_client().get("/api/downloads/active", headers=_bearer("s3cret"))
        assert response.status_code == 500
        assert response.get_json() == {"error": "Authentication error"}

    def test_store_error_does_not_refresh_browser_cookie(self, wired, user_db, monkeypatch):
        alice = user_db.create_user(username="alice")
        monkeypatch.setattr(
            user_db,
            "get_first_admin",
            lambda: (_ for _ in ()).throw(sqlite3.OperationalError("boom")),
        )
        client = _cookie_client(wired.app, alice, permanent=True)
        response = client.get("/api/downloads/active", headers=_bearer("s3cret"))
        assert response.status_code == 500
        assert "Set-Cookie" not in response.headers

        # The browser's own session is untouched and still usable afterwards.
        assert client.get("/api/downloads/active").status_code == 200


class TestMismatchFallsThrough:
    def test_mismatch_no_cookie_is_plain_unauthorized(self, wired):
        response = wired.app.test_client().get("/api/downloads/active", headers=_bearer("wrong"))
        assert response.status_code == 401
        assert response.get_json() == {"error": "Unauthorized"}

    def test_mismatch_is_indistinguishable_from_no_credential(self, wired):
        client = wired.app.test_client()
        no_credential = client.get("/api/downloads/active")
        with_wrong_bearer = client.get("/api/downloads/active", headers=_bearer("wrong"))

        assert no_credential.status_code == with_wrong_bearer.status_code
        assert no_credential.get_json() == with_wrong_bearer.get_json()

        ignored_headers = {"date", "content-length", "server"}

        def header_names(response):
            return {name.lower() for name in response.headers.keys()} - ignored_headers

        assert header_names(no_credential) == header_names(with_wrong_bearer)
        assert "WWW-Authenticate" not in no_credential.headers
        assert "WWW-Authenticate" not in with_wrong_bearer.headers

    def test_mismatch_writes_no_log(self, wired, caplog):
        with caplog.at_level(logging.INFO, logger="shelfmark"):
            wired.app.test_client().get("/api/downloads/active", headers=_bearer("wrong"))

        for record in caplog.records:
            message = record.getMessage()
            assert "API key" not in message
            assert "wrong" not in message

    def test_mismatch_with_cookie_uses_cookie(self, wired, user_db):
        alice = user_db.create_user(username="alice")
        client = _cookie_client(wired.app, alice)
        assert client.get("/api/downloads/active", headers=_bearer("wrong")).status_code == 200
        assert client.get("/api/settings", headers=_bearer("wrong")).status_code == 403

    def test_mismatch_leaves_browser_session_untouched(self, wired, user_db):
        alice = user_db.create_user(username="alice")
        client = _cookie_client(wired.app, alice, permanent=True)
        response = client.get("/api/downloads/active", headers=_bearer("wrong"))
        assert response.status_code == 200
        assert client.get("/api/downloads/active").status_code == 200

    def test_unset_key_is_noop(self, main_module, user_db, monkeypatch):
        monkeypatch.setattr(main_module, "user_db", user_db)
        monkeypatch.setattr(api_key, "SHELFMARK_API_KEY", "")
        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            response = main_module.app.test_client().get(
                "/api/downloads/active", headers=_bearer("s3cret")
            )
            assert response.status_code == 401
            assert response.get_json() == {"error": "Unauthorized"}

            alice = user_db.create_user(username="alice")
            client = _cookie_client(main_module.app, alice, permanent=True)
            cookie_response = client.get("/api/downloads/active", headers=_bearer("s3cret"))
        assert cookie_response.status_code == 200
        assert "Set-Cookie" in cookie_response.headers


class TestScopeAndModes:
    def test_health_and_auth_paths_and_root_ignore_key(self, wired):
        client = wired.app.test_client()
        assert client.get("/api/health", headers=_bearer("s3cret")).status_code == 200
        assert (
            client.get("/api/auth/check", headers=_bearer("s3cret")).get_json()["authenticated"]
            is False
        )
        assert client.get("/", headers=_bearer("s3cret")).status_code != 401

    def test_none_mode_noop(self, main_module, user_db, monkeypatch):
        monkeypatch.setattr(main_module, "user_db", user_db)
        monkeypatch.setattr(api_key, "SHELFMARK_API_KEY", "s3cret")
        with patch.object(main_module, "get_auth_mode", return_value="none"):
            assert (
                main_module.app.test_client()
                .get("/api/downloads/active", headers=_bearer("s3cret"))
                .status_code
                == 200
            )

    def test_proxy_mode_keyed_request_needs_no_proxy_header(
        self, main_module, user_db, monkeypatch
    ):
        monkeypatch.setattr(main_module, "user_db", user_db)
        monkeypatch.setattr(api_key, "SHELFMARK_API_KEY", "s3cret")
        user_db.create_user(username="root", role="admin", auth_source="proxy")
        with patch.object(main_module, "get_auth_mode", return_value="proxy"):
            assert (
                main_module.app.test_client()
                .get("/api/downloads/active", headers=_bearer("s3cret"))
                .status_code
                == 200
            )


class TestIdentityAndRouting:
    def test_deleted_first_admin_changes_identity(self, wired, user_db):
        root = user_db.create_user(username="root", role="admin")
        root2 = user_db.create_user(username="root2", role="admin")

        identity = _identity_for_key(wired)
        assert identity["user_id"] == "root"
        assert identity["db_user_id"] == root["id"]
        assert identity["is_admin"] is True

        user_db.delete_user(root["id"])

        identity = _identity_for_key(wired)
        assert identity["user_id"] == "root2"
        assert identity["db_user_id"] == root2["id"]

        user_db.delete_user(root2["id"])

        # No admin row left at all; the key still authenticates a bare
        # admin identity for routes that don't need a local user row.
        identity = _identity_for_key(wired)
        assert identity["user_id"] == "api"
        assert identity["is_admin"] is True
        assert "db_user_id" not in identity

    def test_demoted_first_admin_changes_identity(self, wired, user_db):
        root = user_db.create_user(username="root", role="admin")

        identity = _identity_for_key(wired)
        assert identity["user_id"] == "root"

        user_db.update_user(root["id"], role="user")

        # No admin row left to match; the key falls back to a bare identity.
        identity = _identity_for_key(wired)
        assert identity["user_id"] == "api"
        assert identity["is_admin"] is True
        assert "db_user_id" not in identity

        # Bare admin identity still reaches a route that doesn't need a
        # local user row.
        response = wired.app.test_client().get("/api/settings", headers=_bearer("s3cret"))
        assert response.status_code == 200

    def test_path_probes(self, wired, user_db):
        user_db.create_user(username="root", role="admin")
        client = wired.app.test_client()

        # Flask routing is case-sensitive: /API/... doesn't match the /api/
        # prefix the middleware checks, so it falls through to the SPA
        # catch-all route instead of the JSON handler with elevated state.
        response = client.get("/API/downloads/active", headers=_bearer("s3cret"))
        assert response.status_code != 200 or response.content_type != "application/json"

        # A trailing slash must not silently reach the handler either.
        response = client.get("/api/downloads/active/", headers=_bearer("s3cret"))
        assert response.status_code != 200 or not response.data

        # A dot-segment path trick must not resolve to a live route.
        response = client.get("/api/auth/../downloads/active", headers=_bearer("s3cret"))
        assert response.status_code == 404

    def test_keyed_post_reaches_guarded_route(self, wired, user_db):
        """A keyed POST passes the login_required guard and reaches the handler's own validation.

        Uses /api/releases/inspect rather than a settings-save route: that
        handler does no persistence at all, so this proves the request got
        past the auth guard into real handler logic without touching the
        process-wide Config singleton or the on-disk settings files other
        tests (and other workers, under xdist) share.
        """
        user_db.create_user(username="root", role="admin")

        response = wired.app.test_client().post(
            "/api/releases/inspect",
            json={},
            headers=_bearer("s3cret"),
        )
        assert response.status_code == 400
        assert response.get_json() == {"error": "source_id is required"}
        assert "Set-Cookie" not in response.headers


class TestKeyScope:
    def test_admin_key(self, monkeypatch):
        monkeypatch.setattr(api_key, "SHELFMARK_API_KEY", "adm")
        monkeypatch.setattr(api_key, "SHELFMARK_API_KEY_READONLY", "ro")
        assert api_key.key_scope("adm") == "admin"

    def test_read_only_key(self, monkeypatch):
        monkeypatch.setattr(api_key, "SHELFMARK_API_KEY", "adm")
        monkeypatch.setattr(api_key, "SHELFMARK_API_KEY_READONLY", "ro")
        assert api_key.key_scope("ro") == "readonly"

    def test_unknown_and_empty(self, monkeypatch):
        monkeypatch.setattr(api_key, "SHELFMARK_API_KEY", "adm")
        monkeypatch.setattr(api_key, "SHELFMARK_API_KEY_READONLY", "ro")
        assert api_key.key_scope("nope") is None
        assert api_key.key_scope("") is None

    def test_unset_keys_never_match_an_empty_candidate(self, monkeypatch):
        monkeypatch.setattr(api_key, "SHELFMARK_API_KEY", "")
        monkeypatch.setattr(api_key, "SHELFMARK_API_KEY_READONLY", "")
        assert api_key.key_scope("") is None
        assert api_key.key_scope("x") is None

    def test_same_value_in_both_is_admin(self, monkeypatch):
        monkeypatch.setattr(api_key, "SHELFMARK_API_KEY", "same")
        monkeypatch.setattr(api_key, "SHELFMARK_API_KEY_READONLY", "same")
        assert api_key.key_scope("same") == "admin"

    def test_read_only_key_alone(self, monkeypatch):
        monkeypatch.setattr(api_key, "SHELFMARK_API_KEY", "")
        monkeypatch.setattr(api_key, "SHELFMARK_API_KEY_READONLY", "ro")
        assert api_key.key_scope("ro") == "readonly"


class TestReadOnlyKeyRequests:
    @pytest.fixture
    def ro(self, wired, monkeypatch):
        monkeypatch.setattr(api_key, "SHELFMARK_API_KEY_READONLY", "ro-key")
        return wired

    def test_reads_stats(self, ro, user_db):
        user_db.create_user(username="root", role="admin")
        response = ro.app.test_client().get("/api/stats", headers=_bearer("ro-key"))
        assert response.status_code == 200

    def test_x_api_key_works_too(self, ro, user_db):
        user_db.create_user(username="root", role="admin")
        response = ro.app.test_client().get("/api/stats", headers=_x_api_key("ro-key"))
        assert response.status_code == 200

    @pytest.mark.parametrize(
        "path", ["/api/settings", "/api/downloads/active", "/api/users", "/api/requests"]
    )
    def test_cannot_read_anything_else(self, ro, user_db, path):
        user_db.create_user(username="root", role="admin")
        response = ro.app.test_client().get(path, headers=_bearer("ro-key"))
        assert response.status_code == 403

    @pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
    def test_cannot_write_even_to_stats(self, ro, user_db, method):
        user_db.create_user(username="root", role="admin")
        response = getattr(ro.app.test_client(), method)("/api/stats", headers=_bearer("ro-key"))
        assert response.status_code == 403

    def test_never_gets_an_admin_session(self, ro, user_db):
        user_db.create_user(username="root", role="admin")
        with ro.app.test_request_context("/api/stats", headers=_bearer("ro-key")):
            assert ro.api_key_auth_middleware() is None
            from flask import session

            assert "is_admin" not in session
            assert "user_id" not in session

    def test_sets_no_cookie(self, ro, user_db):
        user_db.create_user(username="root", role="admin")
        response = ro.app.test_client().get("/api/stats", headers=_bearer("ro-key"))
        assert "Set-Cookie" not in response.headers

    def test_admin_key_still_reads_stats(self, ro, user_db):
        user_db.create_user(username="root", role="admin")
        response = ro.app.test_client().get("/api/stats", headers=_bearer("s3cret"))
        assert response.status_code == 200

    def test_admin_key_beside_a_read_only_key_keeps_admin_access(self, ro, user_db):
        """A proxy's own Authorization value must not downgrade a correct admin X-Api-Key."""
        user_db.create_user(username="root", role="admin")
        headers = {**_bearer("ro-key"), **_x_api_key("s3cret")}
        assert ro.app.test_client().get("/api/settings", headers=headers).status_code == 200

    def test_no_key_is_refused(self, ro, user_db):
        user_db.create_user(username="root", role="admin")
        assert ro.app.test_client().get("/api/stats").status_code == 401

    def test_wrong_key_is_refused(self, ro, user_db):
        user_db.create_user(username="root", role="admin")
        response = ro.app.test_client().get("/api/stats", headers=_bearer("guess"))
        assert response.status_code == 401

    def test_read_only_key_alone_enables_the_middleware(self, main_module, user_db, monkeypatch):
        monkeypatch.setattr(main_module, "user_db", user_db)
        monkeypatch.setattr(api_key, "SHELFMARK_API_KEY", "")
        monkeypatch.setattr(api_key, "SHELFMARK_API_KEY_READONLY", "ro-key")
        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            client = main_module.app.test_client()
            assert client.get("/api/stats", headers=_bearer("ro-key")).status_code == 200
            assert client.get("/api/settings", headers=_bearer("ro-key")).status_code == 403


class TestStatsEndpoint:
    def test_stats_payload_carries_counters(self, wired, user_db):
        user_db.create_user(username="root", role="admin")
        body = wired.app.test_client().get("/api/stats", headers=_bearer("s3cret")).get_json()

        assert set(body) == {"generated_at", "added", "queue", "requests", "errors"}
        assert body["added"]["total_7d"] == 0

    def test_stats_is_closed_to_a_plain_non_admin_session(self, wired, user_db):
        user = user_db.create_user(username="reader", role="user")
        client = _cookie_client(wired.app, user, is_admin=False)

        assert client.get("/api/stats").status_code == 403

    def test_stats_is_open_to_an_admin_session(self, wired, user_db):
        admin = user_db.create_user(username="root", role="admin")
        client = _cookie_client(wired.app, admin, is_admin=True)

        assert client.get("/api/stats").status_code == 200
