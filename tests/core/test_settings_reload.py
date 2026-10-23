"""Rebuilding the settings cache parses each tab's config file once."""

from __future__ import annotations

import json
from pathlib import Path

from shelfmark.core import settings_registry
from shelfmark.core.config import config as app_config


def test_refresh_reads_each_config_file_once(tmp_path, monkeypatch):
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    (tmp_path / "settings.json").write_text(json.dumps({"SEARCH_MODE": "universal"}))
    (plugins / "downloads.json").write_text(json.dumps({"MAX_CONCURRENT_DOWNLOADS": 3}))
    (plugins / "hardcover.json").write_text(json.dumps({"HARDCOVER_ENABLED": False}))

    opened: dict[str, int] = {}
    real_open = Path.open

    def counting_open(self, *args, **kwargs):
        if self.suffix == ".json" and self.parent in (tmp_path, plugins):
            opened[self.name] = opened.get(self.name, 0) + 1
        return real_open(self, *args, **kwargs)

    with monkeypatch.context() as patched:
        patched.setattr(settings_registry, "_get_config_dir", lambda: tmp_path)
        patched.setattr(Path, "open", counting_open)
        app_config.refresh(force=True)

    app_config.refresh(force=True)
    assert opened, "no config file was read"
    assert max(opened.values()) == 1, opened


def test_config_files_are_read_fresh_outside_the_block(tmp_path, monkeypatch):
    monkeypatch.setattr(settings_registry, "_get_config_dir", lambda: tmp_path)
    (tmp_path / "settings.json").write_text(json.dumps({"A": 1}))

    with settings_registry.config_files_read_once():
        assert settings_registry.load_config_file("general") == {"A": 1}
        (tmp_path / "settings.json").write_text(json.dumps({"A": 2}))
        assert settings_registry.load_config_file("general") == {"A": 1}

    assert settings_registry.load_config_file("general") == {"A": 2}
