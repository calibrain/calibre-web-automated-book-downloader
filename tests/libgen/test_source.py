"""Tests for LibgenSource: availability gating, query building, record mapping."""

import types
from unittest.mock import patch

from shelfmark.release_sources import BrowseRecord, ReleaseProtocol
from shelfmark.release_sources.libgen import source as libgen_source
from shelfmark.release_sources.libgen.source import LibgenSource, _build_query_candidates
from tests.libgen import sample_html as html


def _plan(manual_query=None, variants=None):
    return types.SimpleNamespace(
        manual_query=manual_query, title_variants=variants or [], author=""
    )


def _variant(title, author):
    return types.SimpleNamespace(title=title, author=author)


def _book(title):
    return types.SimpleNamespace(title=title)


class TestIsAvailable:
    def _patches(self, *, enabled, has_mirrors):
        return (
            patch.object(
                libgen_source.config,
                "get",
                side_effect=lambda k, d=None: enabled if k == "LIBGEN_SEARCH_ENABLED" else d,
            ),
            patch.object(
                libgen_source.mirrors, "has_libgen_mirror_configuration", return_value=has_mirrors
            ),
        )

    def test_enabled_with_mirrors(self):
        cfg, mir = self._patches(enabled=True, has_mirrors=True)
        with cfg, mir:
            assert LibgenSource().is_available() is True

    def test_disabled(self):
        cfg, mir = self._patches(enabled=False, has_mirrors=True)
        with cfg, mir:
            assert LibgenSource().is_available() is False

    def test_enabled_without_mirrors(self):
        cfg, mir = self._patches(enabled=True, has_mirrors=False)
        with cfg, mir:
            assert LibgenSource().is_available() is False

    def test_truthy_string_does_not_enable(self):
        cfg = patch.object(
            libgen_source.config,
            "get",
            side_effect=lambda k, d=None: "true" if k == "LIBGEN_SEARCH_ENABLED" else d,
        )
        mir = patch.object(
            libgen_source.mirrors, "has_libgen_mirror_configuration", return_value=True
        )
        with cfg, mir:
            assert LibgenSource().is_available() is False


def test_build_query_candidates_manual_query_wins():
    plan = _plan(manual_query="  attack on titan  ")
    assert _build_query_candidates(plan, _book("ignored")) == ["attack on titan"]


def test_build_query_candidates_combined_then_title_only():
    plan = _plan(variants=[_variant("One Piece", "Oda")])
    assert _build_query_candidates(plan, _book("x")) == ["One Piece Oda", "One Piece"]


def test_build_query_candidates_dedups_when_no_author():
    plan = _plan(variants=[_variant("Dune", "")])
    assert _build_query_candidates(plan, _book("x")) == ["Dune"]


def test_build_query_candidates_book_title_fallback():
    assert _build_query_candidates(_plan(), _book("Fallback Title")) == ["Fallback Title"]


def test_search_non_ebook_returns_empty():
    with patch.object(LibgenSource, "is_available", return_value=True):
        result = LibgenSource().search(
            _book("x"), _plan(manual_query="x"), content_type="audiobook"
        )
    assert result == []


def test_search_unavailable_returns_empty():
    with patch.object(LibgenSource, "is_available", return_value=False):
        assert LibgenSource().search(_book("x"), _plan(manual_query="x")) == []


def test_search_maps_records_to_releases():
    record = BrowseRecord(
        id=html.MD5_A,
        title="One Piece, Vol. 1",
        source="libgen",
        format="epub",
        size="180 MB",
        language="en",
        author="Oda",
    )
    with (
        patch.object(LibgenSource, "is_available", return_value=True),
        patch.object(
            libgen_source.mirrors, "get_libgen_mirrors", return_value=["https://libgen.li"]
        ),
        patch.object(libgen_source.config, "get", side_effect=lambda k, d=None: d),
        patch.object(libgen_source.scraper, "search_libgen", return_value=[record]) as mock_search,
    ):
        releases = LibgenSource().search(_book("One Piece"), _plan(manual_query="One Piece"))
    assert len(releases) == 1
    assert releases[0].source_id == f"libgen:{html.MD5_A}"
    mock_search.assert_called_once()


def test_record_to_release_namespaces_source_id_and_fields():
    record = BrowseRecord(
        id=html.MD5_C, title="One Piece 515", source="libgen", format="cbr", size="6 MB"
    )
    release = LibgenSource()._record_to_release(record)
    assert release.source == "libgen"
    assert release.source_id == f"libgen:{html.MD5_C}"
    assert release.protocol == ReleaseProtocol.HTTP
    assert release.indexer == "Libgen"
    assert release.content_type == "ebook"
    assert release.extra["md5"] == html.MD5_C
    assert release.download_url is None


def test_search_results_are_releases():
    assert LibgenSource().search_results_are_releases() is True
