"""Tests for config file persistence, corruption recovery, and permissions."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest


def test_save_config_file_merges_existing_values_and_preserves_unknown_keys(tmp_path):
    from shelfmark.core.settings_registry import load_config_file, save_config_file

    config_dir = tmp_path
    plugins_dir = config_dir / "plugins"
    plugins_dir.mkdir(parents=True)
    (plugins_dir / "downloads.json").write_text(
        '{"existing": "value", "unknown": {"nested": true}}'
    )

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr("shelfmark.config.env.CONFIG_DIR", config_dir)
        assert save_config_file("downloads", {"existing": "updated", "new": "value"}) is True
        result = load_config_file("downloads")

    assert result == {
        "existing": "updated",
        "new": "value",
        "unknown": {"nested": True},
    }


@pytest.mark.parametrize(
    ("filename", "contents"),
    [
        ("missing", None),
        ("empty", ""),
        ("broken", "{ invalid json }"),
        ("partial", '{"key": "value"'),
    ],
)
def test_load_config_file_returns_empty_dict_for_missing_or_corrupted_files(
    tmp_path: Path,
    filename: str,
    contents: str | None,
):
    from shelfmark.core.settings_registry import load_config_file

    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir(parents=True)
    if contents is not None:
        (plugins_dir / f"{filename}.json").write_text(contents)

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr("shelfmark.config.env.CONFIG_DIR", tmp_path)
        result = load_config_file(filename)

    assert result == {}


@pytest.mark.parametrize(
    "tab_name",
    ["../escape", "nested/plugin", "..", "."],
)
def test_get_config_file_path_rejects_path_traversal(tmp_path: Path, tab_name: str):
    from shelfmark.core.settings_registry import _get_config_file_path

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr("shelfmark.config.env.CONFIG_DIR", tmp_path)
        with pytest.raises(ValueError, match="Invalid tab name"):
            _get_config_file_path(tab_name)


def test_is_config_dir_writable_tracks_directory_state(tmp_path: Path):
    from shelfmark.config.env import _is_config_dir_writable

    writable_dir = tmp_path / "writable"
    writable_dir.mkdir()
    missing_dir = tmp_path / "missing"

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr("shelfmark.config.env.CONFIG_DIR", writable_dir)
        assert _is_config_dir_writable() is True

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr("shelfmark.config.env.CONFIG_DIR", missing_dir)
        assert _is_config_dir_writable() is False


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0, reason="Permission tests are unreliable as root"
)
def test_save_config_file_returns_false_when_config_dir_is_not_writable(tmp_path: Path):
    from shelfmark.core.settings_registry import save_config_file

    config_dir = tmp_path / "readonly"
    config_dir.mkdir()
    config_dir.chmod(stat.S_IRUSR | stat.S_IXUSR)

    try:
        with pytest.MonkeyPatch.context() as monkeypatch:
            monkeypatch.setattr("shelfmark.config.env.CONFIG_DIR", config_dir)
            assert save_config_file("downloads", {"key": "value"}) is False
    finally:
        config_dir.chmod(stat.S_IRWXU)


def test_covers_cache_probe_is_remembered_for_a_short_time(monkeypatch):
    from shelfmark.config import env

    probes: list[int] = []
    monkeypatch.setattr(env, "_is_config_dir_writable", lambda: (probes.append(1), True)[1])
    clock = [1000.0]
    monkeypatch.setattr(env.time, "monotonic", lambda: clock[0])

    for _ in range(5):
        assert env._config_dir_writable_cached() is True
    assert probes == [1]

    clock[0] += env._WRITABLE_PROBE_TTL_SECONDS + 1
    assert env._config_dir_writable_cached() is True
    assert probes == [1, 1]
