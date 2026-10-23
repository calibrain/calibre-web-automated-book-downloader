"""Tests for WebshareSource: availability, format filtering, release mapping."""

from unittest.mock import patch

import pytest

from shelfmark.core.search_plan import ReleaseSearchPlan, ReleaseSearchVariant
from shelfmark.metadata_providers import BookMetadata
from shelfmark.release_sources import SourceUnavailableError, get_source
from shelfmark.release_sources.webshare import source as webshare_source
from shelfmark.release_sources.webshare.api import WebshareError, WebshareFile

_CONFIG = {
    "WEBSHARE_ENABLED": True,
    "WEBSHARE_USERNAME": " user ",
    "WEBSHARE_PASSWORD": "pw",
    "WEBSHARE_MAX_RESULTS": 20,
    "SUPPORTED_FORMATS": ["epub", "mobi"],
    "SUPPORTED_AUDIOBOOK_FORMATS": ["m4b", "mp3"],
}


def _file(ident, name, *, size=200_000, password=False, up=0, down=0):
    return WebshareFile(
        ident=ident,
        name=name,
        type=None,
        size_bytes=size,
        positive_votes=up,
        negative_votes=down,
        password_protected=password,
    )


def _book():
    return BookMetadata(provider="test", provider_id="1", title="Krysař", authors=["Viktor Dyk"])


def _plan(**kwargs):
    return ReleaseSearchPlan(
        languages=None,
        isbn_candidates=[],
        author="Viktor Dyk",
        title_variants=[ReleaseSearchVariant(title="Krysař", author="Viktor Dyk")],
        grouped_title_variants=[],
        **kwargs,
    )


@pytest.fixture
def config_values():
    values = dict(_CONFIG)
    with (
        patch.object(webshare_source.config, "get", side_effect=lambda k, d=None: values.get(k, d)),
        patch(
            "shelfmark.download.postprocess.policy.core_config.config.get",
            side_effect=lambda k, d=None: values.get(k, d),
        ),
    ):
        yield values


def test_registered_under_webshare():
    assert get_source("webshare").display_name == "Webshare.cz"


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({}, True),
        ({"WEBSHARE_ENABLED": False}, False),
        ({"WEBSHARE_ENABLED": "true"}, False),
        ({"WEBSHARE_USERNAME": ""}, False),
        ({"WEBSHARE_PASSWORD": ""}, False),
    ],
)
def test_is_available(config_values, overrides, expected):
    config_values.update(overrides)
    assert webshare_source.WebshareSource().is_available() is expected


def test_ebook_search_filters_formats_and_maps_release(config_values):
    files = [
        _file("a1", "Dyk Viktor - Krysar (CZ).epub", up=5, down=1),
        _file("a2", "Krysar.mobi"),
        _file("a3", "Krysar.mp3"),
        _file("a4", "Krysar.zip"),
        _file("a5", "Krysar locked.epub", password=True),
    ]
    with patch.object(webshare_source.WebshareClient, "search", return_value=files) as search:
        releases = webshare_source.WebshareSource().search(_book(), _plan())

    assert [r.source_id for r in releases] == ["webshare:a1", "webshare:a2"]
    search.assert_called_once_with("Krysař Viktor Dyk", limit=20)
    first = releases[0]
    assert first.title == "Dyk Viktor - Krysar (CZ)"
    assert first.format == "epub"
    assert first.language == "cs"
    assert first.size == "195.3 KB"
    assert first.size_bytes == 200_000
    assert first.info_url == "https://webshare.cz/#/file/a1"
    assert first.extra["votes"] == 4
    assert first.content_type == "ebook"


def test_audiobook_search_accepts_archives(config_values):
    files = [_file("b1", "Krysar audiokniha.zip"), _file("b2", "Krysar.epub")]
    with patch.object(webshare_source.WebshareClient, "search", return_value=files):
        releases = webshare_source.WebshareSource().search(
            _book(), _plan(), content_type="audiobook"
        )
    assert [r.source_id for r in releases] == ["webshare:b1"]
    assert releases[0].content_type == "audiobook"


def test_falls_back_to_title_only_query(config_values):
    calls = []

    def search(query, limit):
        calls.append(query)
        return [] if len(calls) == 1 else [_file("c1", "Krysar.epub")]

    with patch.object(webshare_source.WebshareClient, "search", side_effect=search):
        releases = webshare_source.WebshareSource().search(_book(), _plan())

    assert calls == ["Krysař Viktor Dyk", "Krysař"]
    assert len(releases) == 1


def test_manual_query_is_used_verbatim(config_values):
    with patch.object(webshare_source.WebshareClient, "search", return_value=[]) as search:
        webshare_source.WebshareSource().search(_book(), _plan(manual_query="  dyk krysar "))
    search.assert_called_once_with("dyk krysar", limit=20)


def test_search_error_marks_source_unavailable(config_values):
    with (
        patch.object(
            webshare_source.WebshareClient, "search", side_effect=WebshareError("Login failed")
        ),
        pytest.raises(SourceUnavailableError),
    ):
        webshare_source.WebshareSource().search(_book(), _plan())


def test_disabled_source_does_not_search(config_values):
    config_values["WEBSHARE_ENABLED"] = False
    with patch.object(webshare_source.WebshareClient, "search") as search:
        assert webshare_source.WebshareSource().search(_book(), _plan()) == []
    search.assert_not_called()


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Kniha (CZ).epub", "cs"),
        ("kniha.sk.epub", "sk"),
        ("Book [ENG].epub", "en"),
        ("Kniha CZ + EN.epub", None),
        ("Kniha.epub", None),
    ],
)
def test_guess_language(name, expected):
    assert webshare_source._guess_language(name) == expected
