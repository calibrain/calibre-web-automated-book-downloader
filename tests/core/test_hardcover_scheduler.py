"""Tests for the Hardcover sync scheduler's interval parsing and run gating."""

import threading

import pytest

from shelfmark.core import hardcover_scheduler as scheduler


class _Config:
    def __init__(self, **values):
        self.values = values

    def get(self, key, default=None, user_id=None):
        return self.values.get(key, default)


@pytest.fixture(autouse=True)
def _isolated_scheduler(monkeypatch):
    monkeypatch.setattr(scheduler, "_ctx", {})
    monkeypatch.setattr(scheduler, "_run_lock", threading.Lock())
    monkeypatch.setattr(scheduler, "app_config", _Config())


def _configure(monkeypatch, **values):
    monkeypatch.setattr(scheduler, "app_config", _Config(**values))


@pytest.mark.parametrize(
    ("interval", "unit", "expected"),
    [
        (30, "minutes", 1800),
        (2, "hours", 7200),
        ("1.5", "hours", 5400),
        (0.5, "minutes", 60),
        (6, "fortnights", 6 * 3600),
        ("garbage", "hours", 6 * 3600),
        ([], "hours", 6 * 3600),
    ],
)
def test_interval_seconds(monkeypatch, interval, unit, expected):
    _configure(monkeypatch, HARDCOVER_SYNC_INTERVAL=interval, HARDCOVER_SYNC_INTERVAL_UNIT=unit)

    assert scheduler._interval_seconds() == expected


def test_interval_seconds_defaults_to_six_hours(monkeypatch):
    _configure(monkeypatch)

    assert scheduler._interval_seconds() == 6 * 3600


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ({}, False),
        ({"HARDCOVER_SYNC_ENABLED": True}, True),
        ({"AUTO_DOWNLOAD_ENABLED": True}, True),
        ({"HARDCOVER_SYNC_ENABLED": False, "AUTO_DOWNLOAD_ENABLED": False}, False),
    ],
)
def test_enabled(monkeypatch, values, expected):
    _configure(monkeypatch, **values)

    assert scheduler._enabled() is expected


def test_run_once_before_configure_is_unconfigured():
    assert scheduler.run_once() == {"status": "unconfigured"}
    assert scheduler.run_once(force=True) == {"status": "unconfigured"}


def test_run_once_reports_busy_while_another_run_holds_the_lock():
    with scheduler._run_lock:
        assert scheduler.run_once(force=True) == {"status": "busy"}

    assert scheduler.run_once() == {"status": "unconfigured"}


class TestRunOnce:
    @pytest.fixture
    def calls(self, monkeypatch):
        recorded = {"sync": [], "auto": []}

        def _sync(user_db, **kwargs):
            recorded["sync"].append((user_db, kwargs))
            return {"added": 1}

        def _auto(user_db, **kwargs):
            recorded["auto"].append((user_db, kwargs))
            return {"queued": 1}

        monkeypatch.setattr("shelfmark.core.hardcover_sync.sync_wishlist", _sync)
        monkeypatch.setattr("shelfmark.core.auto_download.auto_download_pending", _auto)
        return recorded

    def test_scheduled_run_skips_sync_when_disabled(self, calls):
        user_db, queue_release = object(), object()
        scheduler.configure(user_db, queue_release, "/data/users.db")

        result = scheduler.run_once()

        assert result == {"status": "ok", "sync": None, "auto_download": {"queued": 1}}
        assert calls["sync"] == []
        assert calls["auto"] == [(user_db, {"queue_release": queue_release})]

    def test_forced_run_syncs_regardless_of_toggle(self, calls):
        user_db = object()
        scheduler.configure(user_db, object(), "/data/users.db")

        result = scheduler.run_once(force=True)

        assert result == {"status": "ok", "sync": {"added": 1}, "auto_download": {"queued": 1}}
        assert calls["sync"] == [(user_db, {"db_path": "/data/users.db"})]

    def test_scheduled_run_syncs_when_enabled(self, monkeypatch, calls):
        _configure(monkeypatch, HARDCOVER_SYNC_ENABLED=True)
        user_db = object()
        scheduler.configure(user_db, object(), None)

        result = scheduler.run_once()

        assert result["sync"] == {"added": 1}
        assert calls["sync"] == [(user_db, {"db_path": None})]

    def test_lock_is_released_after_a_run(self, calls):
        scheduler.configure(object(), object(), None)

        scheduler.run_once()

        assert scheduler._run_lock.locked() is False


class TestTriggerAsync:
    def test_returns_false_while_a_run_is_active(self, monkeypatch):
        def _unexpected(**_kwargs):
            raise AssertionError("run_once must not be scheduled")

        monkeypatch.setattr(scheduler, "run_once", _unexpected)

        with scheduler._run_lock:
            assert scheduler.trigger_async() is False

    @pytest.mark.parametrize("force", [True, False])
    def test_runs_once_in_the_background_with_force_flag(self, monkeypatch, force):
        finished = threading.Event()
        seen = []

        def _run_once(**kwargs):
            seen.append(kwargs)
            finished.set()
            return {"status": "ok"}

        monkeypatch.setattr(scheduler, "run_once", _run_once)

        assert scheduler.trigger_async(force=force) is True
        assert finished.wait(timeout=5)
        assert seen == [{"force": force}]
