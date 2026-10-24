"""/api/health must say so when the download coordinator is not running."""

from __future__ import annotations

import importlib
import threading
from unittest.mock import patch

import pytest

from shelfmark.download import orchestrator


@pytest.fixture(scope="module")
def main_module():
    """Import `shelfmark.main` with background startup disabled."""
    with patch("shelfmark.download.orchestrator.start"):
        import shelfmark.main as main

        importlib.reload(main)
        return main


@pytest.fixture
def client(main_module):
    return main_module.app.test_client()


def test_health_is_ok_while_the_coordinator_runs(main_module, client):
    with patch.object(main_module.backend, "coordinator_running", return_value=True):
        resp = client.get("/api/health")

    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ok"
    assert resp.get_json()["coordinator"] == "running"


def test_health_fails_when_the_coordinator_is_gone(main_module, client):
    with patch.object(main_module.backend, "coordinator_running", return_value=False):
        resp = client.get("/api/health")

    assert resp.status_code == 503
    assert resp.get_json()["status"] == "error"
    assert resp.get_json()["coordinator"] == "stopped"


def test_health_needs_no_login(main_module, client):
    with (
        patch.object(main_module, "get_auth_mode", return_value="builtin"),
        patch.object(main_module.backend, "coordinator_running", return_value=True),
    ):
        resp = client.get("/api/health")

    assert resp.status_code == 200


class TestCoordinatorRunning:
    def test_false_before_it_was_ever_started(self, monkeypatch):
        monkeypatch.setattr(orchestrator, "_coordinator_thread", None)
        assert orchestrator.coordinator_running() is False

    def test_true_while_the_thread_is_alive(self, monkeypatch):
        release = threading.Event()
        thread = threading.Thread(target=release.wait, daemon=True)
        thread.start()
        monkeypatch.setattr(orchestrator, "_coordinator_thread", thread)
        try:
            assert orchestrator.coordinator_running() is True
        finally:
            release.set()
            thread.join(timeout=2)

    def test_false_once_the_thread_has_died(self, monkeypatch):
        thread = threading.Thread(target=lambda: None, daemon=True)
        thread.start()
        thread.join(timeout=2)
        monkeypatch.setattr(orchestrator, "_coordinator_thread", thread)
        assert orchestrator.coordinator_running() is False
