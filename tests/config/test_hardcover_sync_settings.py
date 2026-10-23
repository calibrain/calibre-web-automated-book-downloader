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


def test_schedule_and_content_type_defaults():
    fields = _field_map()

    assert fields["HARDCOVER_SYNC_INTERVAL"].default == 6
    assert fields["HARDCOVER_SYNC_INTERVAL"].min_value == 1
    assert fields["HARDCOVER_SYNC_INTERVAL_UNIT"].default == "hours"
    assert fields["HARDCOVER_SYNC_CONTENT_TYPE"].default == "audiobook"
    assert fields["HARDCOVER_SYNC_STATUSES"].default == "1"
    assert fields["AUTO_DOWNLOAD_SOURCE_PRIORITY"].default == []


def test_failed_downloads_are_retried_after_a_week_and_it_can_be_turned_off():
    field = _field_map()["AUTO_DOWNLOAD_RETRY_DAYS"]

    assert field.default == 7
    assert field.min_value == 0  # 0 is the off switch


def test_shelf_options_come_from_hardcover_statuses():
    options = _field_map()["HARDCOVER_SYNC_STATUSES"].options()

    assert {"value": "1", "label": "Want to Read"} in options


def test_action_buttons_are_registered():
    fields = _field_map()

    assert fields["sync_now"].callback is not None


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


def test_request_as_offers_both_and_ebook_sources_have_their_own_priority_list():
    fields = _field_map()
    options = fields["HARDCOVER_SYNC_CONTENT_TYPE"].options
    values = {o["value"] for o in (options() if callable(options) else options)}

    assert values == {"audiobook", "ebook", "both"}
    assert "AUTO_DOWNLOAD_EBOOK_SOURCE_PRIORITY" in fields
    assert fields["AUTO_DOWNLOAD_EBOOK_SOURCE_PRIORITY"].default == []
