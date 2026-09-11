from shelfmark.core.config import config
from shelfmark.core.models import SearchFilters
from shelfmark.core.search_plan import build_release_search_plan
from shelfmark.metadata_providers import BookMetadata
from shelfmark.release_sources import BrowseRecord
from shelfmark.release_sources.direct_download import DirectDownloadSource
from shelfmark.release_sources.direct_download import annas_archive as aa


def _browse_record(record_id: str, title: str) -> BrowseRecord:
    return BrowseRecord(id=record_id, title=title, source="direct_download")


def _enable_direct_download(monkeypatch):
    from shelfmark.release_sources.direct_download import source as dd

    original_get = config.get

    def _fake_get(key: str, default=None, user_id=None):
        del user_id
        if key == "DIRECT_DOWNLOAD_ENABLED":
            return True
        return original_get(key, default)

    monkeypatch.setattr(config, "get", _fake_get)
    monkeypatch.setattr("shelfmark.core.mirrors.has_aa_mirror_configuration", lambda: True)
    return dd


class TestDirectDownloadSearchQueries:
    def test_uses_search_title_for_english_queries(self, monkeypatch):
        captured: list[str] = []

        def fake_search_books(query: str, filters):
            captured.append(query)
            return []

        _enable_direct_download(monkeypatch)

        monkeypatch.setattr(aa, "search_books", fake_search_books)

        source = DirectDownloadSource()
        book = BookMetadata(
            provider="hardcover",
            provider_id="123",
            title="Mistborn: The Final Empire",
            search_title="The Final Empire",
            search_author="Brandon Sanderson",
            authors=["Brandon Sanderson"],
            titles_by_language={
                "en": "Mistborn: The Final Empire",
                "hu": "A végső birodalom",
            },
        )

        plan = build_release_search_plan(book, languages=["en", "hu"])
        source.search(book, plan, expand_search=True)

        assert "The Final Empire Brandon Sanderson" in captured
        assert "A végső birodalom Brandon Sanderson" in captured
        assert "Mistborn: The Final Empire Brandon Sanderson" not in captured

    def test_deduplicates_results_across_localized_queries(self, monkeypatch):
        captured: list[tuple[str, list[str] | None]] = []
        records_by_query = {
            "The Final Empire Brandon Sanderson": [
                _browse_record("shared", "Shared release"),
                _browse_record("en-only", "English only"),
            ],
            "A végső birodalom Brandon Sanderson": [
                _browse_record("shared", "Shared release"),
                _browse_record("hu-only", "Hungarian only"),
            ],
        }

        def fake_search_books(query: str, filters):
            captured.append((query, filters.lang))
            return records_by_query[query]

        _enable_direct_download(monkeypatch)

        monkeypatch.setattr(aa, "search_books", fake_search_books)

        source = DirectDownloadSource()
        book = BookMetadata(
            provider="hardcover",
            provider_id="123",
            title="Mistborn: The Final Empire",
            search_title="The Final Empire",
            search_author="Brandon Sanderson",
            authors=["Brandon Sanderson"],
            titles_by_language={
                "en": "Mistborn: The Final Empire",
                "hu": "A végső birodalom",
            },
        )

        plan = build_release_search_plan(book, languages=["en", "hu"])
        results = source.search(book, plan, expand_search=True)

        assert captured == [
            ("The Final Empire Brandon Sanderson", ["en"]),
            ("A végső birodalom Brandon Sanderson", ["hu"]),
        ]
        assert [release.source_id for release in results] == ["shared", "en-only", "hu-only"]

    def test_retries_without_language_filters_when_localized_queries_miss(self, monkeypatch):
        captured: list[tuple[str, list[str] | None]] = []
        fallback_results = {
            "The Final Empire Brandon Sanderson": [
                _browse_record("fallback-en", "Fallback English")
            ],
            "A végső birodalom Brandon Sanderson": [
                _browse_record("fallback-hu", "Fallback Hungarian")
            ],
        }

        def fake_search_books(query: str, filters):
            captured.append((query, filters.lang))
            if filters.lang:
                return []
            return fallback_results[query]

        _enable_direct_download(monkeypatch)

        monkeypatch.setattr(aa, "search_books", fake_search_books)

        source = DirectDownloadSource()
        book = BookMetadata(
            provider="hardcover",
            provider_id="123",
            title="Mistborn: The Final Empire",
            search_title="The Final Empire",
            search_author="Brandon Sanderson",
            authors=["Brandon Sanderson"],
            titles_by_language={
                "en": "Mistborn: The Final Empire",
                "hu": "A végső birodalom",
            },
        )

        plan = build_release_search_plan(book, languages=["en", "hu"])
        results = source.search(book, plan, expand_search=True)

        assert captured == [
            ("The Final Empire Brandon Sanderson", ["en"]),
            ("A végső birodalom Brandon Sanderson", ["hu"]),
            ("The Final Empire Brandon Sanderson", None),
            ("A végső birodalom Brandon Sanderson", None),
        ]
        assert [release.source_id for release in results] == ["fallback-en", "fallback-hu"]

    def test_manual_query_fallback_preserves_other_filters(self, monkeypatch):
        captured: list[tuple[str, list[str] | None, list[str] | None]] = []

        def fake_search_books(query: str, filters):
            captured.append((query, filters.lang, filters.format))
            if filters.lang:
                return []
            return [_browse_record("manual-1", "Manual result")]

        _enable_direct_download(monkeypatch)

        monkeypatch.setattr(aa, "search_books", fake_search_books)

        source = DirectDownloadSource()
        book = BookMetadata(
            provider="hardcover",
            provider_id="123",
            title="Mistborn: The Final Empire",
            authors=["Brandon Sanderson"],
        )

        plan = build_release_search_plan(
            book,
            languages=["en"],
            manual_query="mistborn custom query",
            source_filters=SearchFilters(format=["epub"], sort="newest"),
        )
        results = source.search(book, plan)

        assert [release.source_id for release in results] == ["manual-1"]
        assert captured == [
            ("mistborn custom query", ["en"], ["epub"]),
            ("mistborn custom query", None, ["epub"]),
        ]


# --- Distant-path language detection tests ---


def _patch_path_language(monkeypatch, enabled: bool = True):
    from shelfmark.release_sources.direct_download import source as dd

    original_get = config.get

    def _fake_get(key: str, default=None, user_id=None):
        del user_id
        if key == "DIRECT_DOWNLOAD_LANGUAGE_FROM_PATH":
            return enabled
        return original_get(key, default)

    monkeypatch.setattr(config, "get", _fake_get)
    return dd


def _row_from_html(html: str):
    from bs4 import BeautifulSoup

    return BeautifulSoup(html, "html.parser").find("tr")


def _make_row(distant_path: str, language: str = "", record_id: str = "rec-1") -> str:
    return rf"""
    <tr>
      <td><a href="/md5/{record_id}"><img src="cover.jpg"></a></td>
      <td><span>A Book Title</span></td>
      <td><span>Author Name</span></td>
      <td><span>Publisher</span></td>
      <td><span>2024</span></td>
      <td><span>-</span></td>
      <td><span>-</span></td>
      <td><span>{language}</span></td>
      <td><span>fiction</span></td>
      <td><span>epub</span></td>
      <td><span>1 mb</span></td>
      <td><span>{distant_path}</span></td>
    </tr>
    """


def test_detects_bracketed_language_from_distant_path(monkeypatch):
    _patch_path_language(monkeypatch)
    row = _row_from_html(_make_row(r"lgli/N:\comics1\emule\2021.08.01\[BD FR] Scrameustache.cbz"))
    record = aa._parse_search_result_row(row)
    assert record is not None
    assert record.language == "fr"
    assert record.download_path is not None


def test_detects_mixed_case_bracketed_language(monkeypatch):
    _patch_path_language(monkeypatch)
    row = _row_from_html(_make_row(r"lgli/V:\comics\_0DAY3\[Fr]\BDs [Fr]\!Pdf\S\Book.pdf"))
    record = aa._parse_search_result_row(row)
    assert record is not None
    assert record.language == "fr"


def test_overrides_unknown_language_with_path_detection(monkeypatch):
    _patch_path_language(monkeypatch)
    row = _row_from_html(_make_row(r"lgli/V:\comics\_0DAY3\[Fr]\Book.pdf", language="unknown"))
    record = aa._parse_search_result_row(row)
    assert record is not None
    assert record.language == "fr"


def test_sets_unknown_when_path_has_no_language(monkeypatch):
    _patch_path_language(monkeypatch)
    row = _row_from_html(_make_row(r"lgli/N:\comics1\emule\NoLanguageHere.epub"))
    record = aa._parse_search_result_row(row)
    assert record is not None
    assert record.language == "unknown"


def test_avoids_en_false_positive_when_french_present(monkeypatch):
    _patch_path_language(monkeypatch)
    row = _row_from_html(
        _make_row(r"lgli/V:\comics\_0DAY2\Stripboeken Frans - BD en Français\[BD Fr] Book.cbr")
    )
    record = aa._parse_search_result_row(row)
    assert record is not None
    assert record.language == "fr"


def test_keeps_row_with_missing_language_when_toggle_disabled(monkeypatch):
    _patch_path_language(monkeypatch, enabled=False)
    row = _row_from_html(_make_row(r"lgli/N:\comics1\[BD FR] Scrameustache.cbz"))
    record = aa._parse_search_result_row(row)
    assert record is not None
    assert record.language is None


def test_keeps_sparse_lgli_row(monkeypatch):
    """lgli rows missing author/publisher/year must not be dropped."""
    _patch_path_language(monkeypatch)
    html = r"""
    <tr>
      <td><a href="/md5/sparse-1"><img src="cover.jpg"></a></td>
      <td><span>Gos - 1978 - Le scrameustache T06.cbz</span></td>
      <td></td><td></td><td></td><td></td><td></td><td></td>
      <td><span>Comic book</span></td>
      <td><span>cbz</span></td>
      <td><span>17.4MB</span></td>
      <td><span>lgli/N:\comics1\ftp\[BD.FR] French Comics\Book.cbz</span></td>
    </tr>
    """
    record = aa._parse_search_result_row(_row_from_html(html))
    assert record is not None
    assert record.id == "sparse-1"
    assert record.language == "fr"
    assert record.author is None


def test_search_books_filters_locally_when_path_language_enabled(monkeypatch):
    _patch_path_language(monkeypatch)
    monkeypatch.setattr(aa.network, "get_aa_base_url", lambda: "https://mirror.example")
    monkeypatch.setattr(aa.network, "AAMirrorSelector", lambda: object())

    captured_url: dict[str, str] = {}

    def _fake_html_get_page(url: str, selector, **_kwargs):
        del selector
        captured_url["url"] = url
        return r"""
        <table>
            <tr>
                <td><a href="/md5/rec-fr"><img src="c.jpg"></a></td>
                <td><span>Livre FR</span></td><td><span>Auteur</span></td>
                <td><span>Editeur</span></td><td><span>2025</span></td>
                <td><span>-</span></td><td><span>-</span></td><td></td>
                <td><span>fiction</span></td><td><span>pdf</span></td>
                <td><span>2 mb</span></td>
                <td><span>lgli/V:\comics\_0DAY3\[Fr]\Book FR.pdf</span></td>
            </tr>
            <tr>
                <td><a href="/md5/rec-en"><img src="c.jpg"></a></td>
                <td><span>Book EN</span></td><td><span>Author</span></td>
                <td><span>Publisher</span></td><td><span>2025</span></td>
                <td><span>-</span></td><td><span>-</span></td><td></td>
                <td><span>fiction</span></td><td><span>pdf</span></td>
                <td><span>2 mb</span></td>
                <td><span>lgli/V:\comics\_0DAY3\[En]\Book EN.pdf</span></td>
            </tr>
        </table>
        """

    monkeypatch.setattr(aa.downloader, "html_get_page", _fake_html_get_page)

    records = aa.search_books("demo", SearchFilters(lang=["fr"], format=["pdf"]))

    assert "&lang=" not in captured_url["url"]
    assert len(records) == 1
    assert records[0].id == "rec-fr"
    assert records[0].language == "fr"


def test_book_matches_requested_languages_logic():

    assert aa._book_matches_requested_languages(None, {"fr"}) is True
    assert aa._book_matches_requested_languages(None, set()) is True
    assert aa._book_matches_requested_languages("en", {"fr"}) is False
    assert aa._book_matches_requested_languages("fr", {"fr"}) is True
