from types import SimpleNamespace

import pytest

from shelfmark.core import mirrors
from shelfmark.core.config import config
from shelfmark.release_sources.direct_download import DirectDownloadSource, SearchUnavailableError
from shelfmark.release_sources.direct_download import annas_archive as aa


def _fake_config_get(values: dict[str, object]):
    def _get(key: str, default=None, user_id=None):
        del user_id
        return values.get(key, default)

    return _get


def test_direct_download_source_is_unavailable_when_disabled(monkeypatch):

    monkeypatch.setattr(config, "get", _fake_config_get({"DIRECT_DOWNLOAD_ENABLED": False}))
    monkeypatch.setattr("shelfmark.core.mirrors.has_aa_mirror_configuration", lambda: True)

    source = DirectDownloadSource()

    assert source.is_available() is False

    with pytest.raises(SearchUnavailableError, match="Direct Download is disabled"):
        source.search(SimpleNamespace(), SimpleNamespace())


def test_direct_download_source_is_unavailable_without_aa_mirrors(monkeypatch):

    monkeypatch.setattr(config, "get", _fake_config_get({"DIRECT_DOWNLOAD_ENABLED": True}))
    monkeypatch.setattr("shelfmark.core.mirrors.has_aa_mirror_configuration", lambda: False)

    source = DirectDownloadSource()

    assert source.is_available() is False

    with pytest.raises(SearchUnavailableError, match="not configured"):
        source.get_record("md5-abc")


def test_direct_download_explains_aa_requirement_when_only_zlib_is_configured(monkeypatch):
    monkeypatch.setattr(config, "get", _fake_config_get({"DIRECT_DOWNLOAD_ENABLED": True}))
    monkeypatch.setattr(
        mirrors,
        "_get_config",
        lambda: {"AA_BASE_URL": "auto", "ZLIB_MIRROR_URLS": ["https://z-library.sk"]},
    )

    assert mirrors.has_zlib_mirror_configuration() is True
    assert mirrors.has_aa_mirror_configuration() is False

    source = DirectDownloadSource()

    with pytest.raises(
        SearchUnavailableError,
        match="Anna's Archive or OceanofPDF mirror URL.*only download results found through",
    ):
        source.search(SimpleNamespace(), SimpleNamespace())


def test_direct_download_source_is_available_when_enabled_and_configured(monkeypatch):

    monkeypatch.setattr(config, "get", _fake_config_get({"DIRECT_DOWNLOAD_ENABLED": True}))
    monkeypatch.setattr("shelfmark.core.mirrors.has_aa_mirror_configuration", lambda: True)

    source = DirectDownloadSource()

    assert source.is_available() is True


def test_get_source_priority_disables_entries_without_required_mirrors(monkeypatch):

    monkeypatch.setattr(
        config,
        "get",
        _fake_config_get(
            {
                "AA_DONATOR_KEY": "donator-key",
                "FAST_SOURCES_DISPLAY": [
                    {"id": "aa-fast", "enabled": True},
                    {"id": "libgen", "enabled": True},
                ],
                "SOURCE_PRIORITY": [
                    {"id": "welib", "enabled": True},
                    {"id": "zlib", "enabled": True},
                ],
            }
        ),
    )
    monkeypatch.setattr("shelfmark.core.mirrors.has_aa_mirror_configuration", lambda: False)
    monkeypatch.setattr("shelfmark.core.mirrors.has_libgen_mirror_configuration", lambda: True)
    monkeypatch.setattr("shelfmark.core.mirrors.has_welib_mirror_configuration", lambda: False)
    monkeypatch.setattr("shelfmark.core.mirrors.has_zlib_mirror_configuration", lambda: True)

    priority = {item["id"]: item["enabled"] for item in aa._get_source_priority()}

    assert priority["aa-fast"] is False
    assert priority["libgen"] is True
    assert priority["welib"] is False
    assert priority["zlib"] is True


def test_is_configured_zlib_link_uses_configured_mirror_domains(monkeypatch):

    monkeypatch.setattr(
        "shelfmark.core.mirrors.get_zlib_cookie_domains",
        lambda: {"custom-zlib.example"},
    )

    assert aa._is_configured_zlib_link("https://custom-zlib.example/books/example") is True
    assert aa._is_configured_zlib_link("https://other-zlib.example/books/example") is False
