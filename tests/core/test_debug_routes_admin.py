"""Access control tests for the DEBUG-only /api/debug and /api/restart routes."""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from types import ModuleType
from unittest.mock import patch

import pytest

from shelfmark.core.config import config as app_config

_DEBUG_OVERRIDES: dict[str, object] = {"DEBUG": True, "USING_EXTERNAL_BYPASSER": True}
_ORIGINAL_CONFIG_GET = app_config.get


def _config_get_with_debug(key: str, default: object = None, user_id: int | None = None) -> object:
    if key in _DEBUG_OVERRIDES:
        return _DEBUG_OVERRIDES[key]
    return _ORIGINAL_CONFIG_GET(key, default, user_id=user_id)


@pytest.fixture(scope="module")
def main_module() -> Iterator[ModuleType]:
    """Import `shelfmark.main` with DEBUG on so the debug routes get registered."""
    with (
        patch("shelfmark.download.orchestrator.start"),
        patch.object(app_config, "get", side_effect=_config_get_with_debug),
    ):
        import shelfmark.main as main

        importlib.reload(main)
    yield main
    # Put the module back the way other test modules expect it (no debug routes).
    with patch("shelfmark.download.orchestrator.start"):
        importlib.reload(main)


@pytest.fixture
def client(main_module):
    return main_module.app.test_client()


def _set_session(client, *, user_id: str, is_admin: bool) -> None:
    with client.session_transaction() as sess:
        sess["user_id"] = user_id
        sess["is_admin"] = is_admin


@pytest.fixture
def no_side_effects(main_module):
    """Never run the real debug script or exit the process from a test."""
    with (
        patch.object(main_module.subprocess, "run") as run_mock,
        patch.object(main_module.os, "_exit") as exit_mock,
    ):
        yield run_mock, exit_mock


def test_debug_routes_are_registered_when_debug_is_on(main_module):
    rules = {rule.rule for rule in main_module.app.url_map.iter_rules()}
    assert "/api/debug" in rules
    assert "/api/restart" in rules


@pytest.mark.parametrize("path", ["/api/debug", "/api/restart"])
def test_debug_routes_reject_anonymous_users(client, main_module, no_side_effects, path):
    run_mock, exit_mock = no_side_effects
    with patch.object(main_module, "get_auth_mode", return_value="builtin"):
        response = client.get(path)

    assert response.status_code == 401
    run_mock.assert_not_called()
    exit_mock.assert_not_called()


@pytest.mark.parametrize("path", ["/api/debug", "/api/restart"])
def test_debug_routes_reject_non_admin_users(client, main_module, no_side_effects, path):
    run_mock, exit_mock = no_side_effects
    _set_session(client, user_id="regular-user", is_admin=False)
    with patch.object(main_module, "get_auth_mode", return_value="builtin"):
        response = client.get(path)

    assert response.status_code == 403
    assert response.get_json() == {"error": "Admin access required"}
    run_mock.assert_not_called()
    exit_mock.assert_not_called()


def test_restart_allows_admin_users(client, main_module, no_side_effects):
    _, exit_mock = no_side_effects
    _set_session(client, user_id="admin-user", is_admin=True)
    with patch.object(main_module, "get_auth_mode", return_value="builtin"):
        client.get("/api/restart")

    exit_mock.assert_called_once_with(0)


def test_restart_allows_access_when_auth_is_disabled(client, main_module, no_side_effects):
    _, exit_mock = no_side_effects
    with patch.object(main_module, "get_auth_mode", return_value="none"):
        client.get("/api/restart")

    exit_mock.assert_called_once_with(0)


def test_debug_allows_admin_users(client, main_module, no_side_effects, tmp_path):
    run_mock, _ = no_side_effects
    bundle = tmp_path / "shelfmark-debug.zip"
    bundle.write_bytes(b"PK\x05\x06" + b"\x00" * 18)
    run_mock.return_value.returncode = 0
    run_mock.return_value.stdout = f"{bundle}\n"
    _set_session(client, user_id="admin-user", is_admin=True)
    with (
        patch.object(main_module, "get_auth_mode", return_value="builtin"),
        patch.object(main_module.time, "sleep"),
    ):
        response = client.get("/api/debug")

    assert response.status_code == 200
    assert response.mimetype == "application/zip"
    run_mock.assert_called_once()
