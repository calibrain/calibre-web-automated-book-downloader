"""Tests for the Hardcover Sync settings tab registration and action buttons."""

import shelfmark.config.hardcover_sync_settings  # noqa: F401 - registers the tab
from shelfmark.core import settings_registry


def _field_map():
    tab = settings_registry.get_settings_tab("hardcover_sync")
    assert tab is not None
    return {field.key: field for field in tab.fields if hasattr(field, "key")}


def test_tab_is_registered():
    tab = settings_registry.get_settings_tab("hardcover_sync")

    assert tab is not None
    assert tab.display_name == "Hardcover Sync"


def test_everything_is_off_by_default():
    fields = _field_map()

    assert fields["HARDCOVER_SYNC_ENABLED"].default is False
    assert fields["AUTO_DOWNLOAD_ENABLED"].default is False
    assert fields["LIBRARY_CHECK_ENABLED"].default is False


def test_schedule_and_content_type_defaults():
    fields = _field_map()

    assert fields["HARDCOVER_SYNC_INTERVAL"].default == 6
    assert fields["HARDCOVER_SYNC_INTERVAL"].min_value == 1
    assert fields["HARDCOVER_SYNC_INTERVAL_UNIT"].default == "hours"
    assert fields["HARDCOVER_SYNC_CONTENT_TYPE"].default == "audiobook"
    assert fields["HARDCOVER_SYNC_STATUSES"].default == "1"
    assert fields["AUTO_DOWNLOAD_SOURCE_PRIORITY"].default == []


def test_shelf_options_come_from_hardcover_statuses():
    options = _field_map()["HARDCOVER_SYNC_STATUSES"].options()

    assert {"value": "1", "label": "Want to Read"} in options


def test_action_buttons_are_registered():
    fields = _field_map()

    assert fields["sync_now"].callback is not None
    assert fields["test_library_connection"].callback is not None


def test_sync_now_reports_busy_when_a_run_is_active(monkeypatch):
    monkeypatch.setattr("shelfmark.core.hardcover_scheduler.trigger_async", lambda **_: False)

    result = settings_registry.execute_action("hardcover_sync", "sync_now")

    assert result["success"] is False
    assert "already running" in result["message"]


def test_sync_now_starts_a_forced_run(monkeypatch):
    seen = []

    def _trigger(**kwargs):
        seen.append(kwargs)
        return True

    monkeypatch.setattr("shelfmark.core.hardcover_scheduler.trigger_async", _trigger)

    result = settings_registry.execute_action("hardcover_sync", "sync_now")

    assert result["success"] is True
    assert seen == [{"force": True}]


def test_test_library_connection_delegates_to_library_index(monkeypatch):
    expected = {"success": True, "message": "Connected. Indexed 3 library item(s)."}
    monkeypatch.setattr("shelfmark.core.library_index.test_connection", lambda: expected)

    assert settings_registry.execute_action("hardcover_sync", "test_library_connection") == expected
