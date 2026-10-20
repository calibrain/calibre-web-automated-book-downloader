"""Tests for the slskd settings tab and its Test Connection action."""

from shelfmark.core.settings_registry import (
    ActionButton,
    CheckboxField,
    NumberField,
    PasswordField,
    TextField,
)
from shelfmark.release_sources.slskd.settings import (
    _test_slskd_connection,
    slskd_config_settings,
)


def _field(key: str):
    return next(f for f in slskd_config_settings() if getattr(f, "key", None) == key)


def test_settings_expose_expected_fields():
    assert isinstance(_field("SLSKD_ENABLED"), CheckboxField)
    assert isinstance(_field("SLSKD_URL"), TextField)
    assert isinstance(_field("SLSKD_API_KEY"), PasswordField)
    assert isinstance(_field("test_slskd"), ActionButton)
    assert isinstance(_field("SLSKD_DOWNLOAD_PATH"), TextField)
    assert isinstance(_field("SLSKD_SEARCH_TIMEOUT"), NumberField)
    assert isinstance(_field("SLSKD_RESPONSE_LIMIT"), NumberField)
    assert isinstance(_field("SLSKD_QUEUE_TIMEOUT_MINUTES"), NumberField)
    assert isinstance(_field("SLSKD_REMOVE_COMPLETED"), CheckboxField)


def test_dependent_fields_hide_until_enabled():
    for key in ("SLSKD_URL", "SLSKD_API_KEY", "test_slskd", "SLSKD_DOWNLOAD_PATH"):
        assert _field(key).show_when == {"field": "SLSKD_ENABLED", "value": True}


def test_defaults():
    assert _field("SLSKD_ENABLED").default is False
    assert _field("SLSKD_SEARCH_TIMEOUT").default == 15
    assert _field("SLSKD_RESPONSE_LIMIT").default == 100
    assert _field("SLSKD_QUEUE_TIMEOUT_MINUTES").default == 60
    assert _field("SLSKD_REMOVE_COMPLETED").default is True


def test_connection_action_uses_form_values(monkeypatch):
    import shelfmark.release_sources.slskd.api as api_module

    created: list[tuple[str, str]] = []

    class FakeClient:
        def __init__(self, url, api_key):
            created.append((url, api_key))

        def test_connection(self):
            return True, "Connected to slskd 0.26.0 (logged in to Soulseek)"

    monkeypatch.setattr(api_module, "SlskdClient", FakeClient)

    result = _test_slskd_connection({"SLSKD_URL": "slskd:5030/", "SLSKD_API_KEY": " key "})

    assert result == {
        "success": True,
        "message": "Connected to slskd 0.26.0 (logged in to Soulseek)",
    }
    assert created == [("http://slskd:5030", "key")]


def test_connection_action_requires_url(monkeypatch):
    import shelfmark.release_sources.slskd.settings as mod

    monkeypatch.setattr(mod, "normalize_http_url", lambda url: url)
    from shelfmark.core.config import config

    monkeypatch.setattr(config, "get", lambda k, d=None: d)
    result = _test_slskd_connection({"SLSKD_URL": "", "SLSKD_API_KEY": "k"})
    assert result == {"success": False, "message": "slskd URL is required"}


def test_connection_action_requires_api_key(monkeypatch):
    from shelfmark.core.config import config

    monkeypatch.setattr(config, "get", lambda k, d=None: d)
    result = _test_slskd_connection({"SLSKD_URL": "http://slskd:5030", "SLSKD_API_KEY": ""})
    assert result == {"success": False, "message": "slskd API key is required"}


def test_connection_action_reports_failure(monkeypatch):
    import shelfmark.release_sources.slskd.api as api_module

    class FakeClient:
        def __init__(self, url, api_key):
            pass

        def test_connection(self):
            return False, "Invalid API key"

    monkeypatch.setattr(api_module, "SlskdClient", FakeClient)
    result = _test_slskd_connection({"SLSKD_URL": "http://slskd:5030", "SLSKD_API_KEY": "bad"})
    assert result == {"success": False, "message": "Invalid API key"}


def test_connection_action_wraps_unexpected_errors(monkeypatch):
    import shelfmark.release_sources.slskd.api as api_module

    class FakeClient:
        def __init__(self, url, api_key):
            raise RuntimeError("kaboom")

    monkeypatch.setattr(api_module, "SlskdClient", FakeClient)
    result = _test_slskd_connection({"SLSKD_URL": "http://slskd:5030", "SLSKD_API_KEY": "k"})
    assert result["success"] is False
    assert "kaboom" in result["message"]


def test_remote_path_mapping_offers_slskd_host():
    from shelfmark.config.settings import advanced_settings

    table = next(
        f for f in advanced_settings() if getattr(f, "key", None) == "PROWLARR_REMOTE_PATH_MAPPINGS"
    )
    host_column = next(c for c in table.columns if c["key"] == "host")
    assert {"value": "slskd", "label": "slskd"} in host_column["options"]


class TestIsolatedDownloadSettings:
    """The options for isolated downloads (fork additions)."""

    def _fields(self):
        from shelfmark.core.settings_registry import get_settings_field_map
        from shelfmark.release_sources.slskd import settings as _settings  # noqa: F401

        return get_settings_field_map("slskd_config")

    def test_isolation_is_on_by_default_with_its_own_folder_prefix(self):
        fields = self._fields()

        isolate, _ = fields["SLSKD_ISOLATE_DOWNLOADS"]
        prefix, _ = fields["SLSKD_DESTINATION_PREFIX"]
        keep, _ = fields["SLSKD_KEEP_COMPLETED"]
        assert isolate.default is True
        assert prefix.default == "shelfmark"
        assert keep.default is True

    def test_the_options_that_only_matter_when_isolated_are_hidden_otherwise(self):
        fields = self._fields()

        for key in ("SLSKD_DESTINATION_PREFIX", "SLSKD_KEEP_COMPLETED"):
            assert fields[key][0].show_when == {"field": "SLSKD_ISOLATE_DOWNLOADS", "value": True}
        # the original "remove completed" option is the one for the non-isolated path
        assert fields["SLSKD_REMOVE_COMPLETED"][0].show_when == {
            "field": "SLSKD_ISOLATE_DOWNLOADS",
            "value": False,
        }

    def test_a_cleared_url_in_the_form_is_not_replaced_by_the_saved_one(self, monkeypatch):
        from shelfmark.core import config as core_config
        from shelfmark.release_sources.slskd.settings import _test_slskd_connection

        monkeypatch.setattr(
            core_config.config,
            "get",
            lambda key, default=None: {
                "SLSKD_URL": "http://saved:5030",
                "SLSKD_API_KEY": "saved",
            }.get(key, default),
        )

        result = _test_slskd_connection({"SLSKD_URL": "", "SLSKD_API_KEY": "typed"})

        assert result["success"] is False
        assert "URL is required" in result["message"]

    def test_a_cleared_key_in_the_form_is_not_replaced_by_the_saved_one(self, monkeypatch):
        from shelfmark.core import config as core_config
        from shelfmark.release_sources.slskd.settings import _test_slskd_connection

        monkeypatch.setattr(
            core_config.config,
            "get",
            lambda key, default=None: {
                "SLSKD_URL": "http://saved:5030",
                "SLSKD_API_KEY": "saved",
            }.get(key, default),
        )

        result = _test_slskd_connection({"SLSKD_URL": "http://typed:5030", "SLSKD_API_KEY": ""})

        assert result["success"] is False
        assert "API key is required" in result["message"]
