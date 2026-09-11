"""Tests for the Libgen scraper: results parsing and download-link resolution."""

from unittest.mock import patch

from shelfmark.release_sources.libgen import scraper
from tests.libgen import sample_html as html


def test_parse_results_extracts_all_md5_rows():
    records = scraper._parse_results(html.SEARCH_HTML, "https://libgen.li")
    assert records is not None
    assert [r.id for r in records] == [html.MD5_A, html.MD5_B, html.MD5_C, html.MD5_D]


def test_parse_results_full_row_fields():
    records = scraper._parse_results(html.SEARCH_HTML, "https://libgen.li")
    a = records[0]
    assert a.title == "One Piece, Vol. 1"
    assert a.author == "Eiichiro Oda"
    assert a.format == "epub"
    assert a.size == "180 MB"  # &nbsp; normalized to a plain space
    assert a.language == "en"
    assert a.source == "libgen"
    assert a.source_url == f"https://libgen.li/ads.php?md5={html.MD5_A}"


def test_parse_results_md5_scoped_to_mirrors_cell():
    # Row A's Title cell carries a decoy md5-shaped cover URL that appears BEFORE the real
    # md5 in document order; the parser must resolve to the Mirrors-cell md5, not the decoy.
    records = scraper._parse_results(html.SEARCH_HTML, "https://libgen.li")
    assert records[0].id == html.MD5_A
    assert html.DECOY_MD5 not in {r.id for r in records}


def test_parse_results_offtopic_row_survives():
    # No relevance filter: a row that merely name-drops the query is kept, same as AA.
    records = scraper._parse_results(html.SEARCH_HTML, "https://libgen.li")
    assert any(r.id == html.MD5_B and r.format == "cbr" for r in records)


def test_parse_results_compact_rows_have_no_author_or_language():
    records = scraper._parse_results(html.SEARCH_HTML, "https://libgen.li")
    c = next(r for r in records if r.id == html.MD5_C)
    assert c.title == "One Piece 515"
    assert c.format == "cbr"
    assert c.size == "6 MB"
    assert c.author is None
    assert c.language is None


def test_parse_results_comics_not_dropped_by_format():
    records = scraper._parse_results(html.SEARCH_HTML, "https://libgen.li")
    assert {"cbr", "cbz"} <= {r.format for r in records}


def test_parse_results_no_table_returns_none():
    assert scraper._parse_results(html.NO_TABLE_HTML, "https://libgen.li") is None


def test_parse_results_empty_table_returns_empty_list():
    result = scraper._parse_results(html.EMPTY_TABLE_HTML, "https://libgen.li")
    assert result == []
    assert result is not None  # distinct from the no-table case


def test_resolve_download_url_extracts_keyed_get():
    url = scraper.resolve_download_url(html.ADS_HTML, "https://libgen.li")
    assert url == f"https://libgen.li/get.php?md5={html.MD5_A}&key={html.GET_KEY}"


def test_resolve_download_url_missing_get_returns_none():
    assert scraper.resolve_download_url(html.ADS_HTML_NO_GET, "https://libgen.li") is None


def test_search_libgen_falls_through_dead_mirror():
    calls = []

    def fake_fetch(url, timeout=(5, 15)):
        calls.append(url)
        return None if "dead" in url else html.SEARCH_HTML

    with patch.object(scraper, "fetch_page", side_effect=fake_fetch):
        records = scraper.search_libgen(
            "one piece", ["https://dead.example", "https://libgen.li"], max_results=25
        )
    assert len(records) == 4
    assert len(calls) == 2  # dead mirror tried first, then the live one


def test_search_libgen_all_mirrors_dead_returns_empty():
    with patch.object(scraper, "fetch_page", return_value=None):
        assert scraper.search_libgen("q", ["https://a", "https://b"], max_results=25) == []
