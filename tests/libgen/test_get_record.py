"""Tests for md5 -> record resolution via the ads.php metadata block."""

from unittest.mock import patch

from shelfmark.release_sources.libgen import scraper
from shelfmark.release_sources.libgen import source as libgen_source
from shelfmark.release_sources.libgen.source import LibgenSource
from tests.libgen import sample_html as html


def test_fetch_record_by_md5_parses_ads_metadata():
    with patch.object(scraper, "fetch_page", return_value=html.ADS_HTML):
        record = scraper.fetch_record_by_md5(html.MD5_A, ["https://libgen.li"])
    assert record is not None
    assert record.title == "One Piece, Vol. 1"
    assert record.author == "Eiichiro Oda"
    assert record.publisher == "Viz Media"
    assert record.year == "2003"  # stops at "ISBN:", not swallowed
    assert record.language == "en"
    assert record.source == "libgen"
    assert record.id == html.MD5_A


def test_fetch_record_by_md5_titleless_page_returns_none():
    with patch.object(scraper, "fetch_page", return_value=html.ADS_HTML_NO_GET):
        assert scraper.fetch_record_by_md5(html.MD5_A, ["https://libgen.li"]) is None


def test_fetch_record_by_md5_all_mirrors_miss_returns_none():
    with patch.object(scraper, "fetch_page", return_value=None):
        assert scraper.fetch_record_by_md5(html.MD5_A, ["https://a", "https://b"]) is None


def test_get_record_strips_prefix_before_lookup():
    with (
        patch.object(
            libgen_source.mirrors, "get_libgen_mirrors", return_value=["https://libgen.li"]
        ),
        patch.object(libgen_source.scraper, "fetch_record_by_md5", return_value=None) as mock_fetch,
    ):
        LibgenSource().get_record(f"libgen:{html.MD5_A}")
    mock_fetch.assert_called_once()
    assert mock_fetch.call_args.args[0] == html.MD5_A
