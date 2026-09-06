from io import BytesIO
from types import SimpleNamespace

import pytest

from shelfmark.core import mirrors
from shelfmark.core.config import config
from shelfmark.core.models import SearchFilters
from shelfmark.release_sources import BrowseRecord, get_source
from shelfmark.release_sources.direct_download import (
    DirectDownloadHandler,
    DirectDownloadSource,
    handler,
    oceanofpdf,
)
from shelfmark.release_sources.direct_download import annas_archive as aa

SEARCH_HTML = """
<article class="post entry" aria-label="[PDF EPUB] Dune Download">
  <header><h2 class="entry-title">
    <a href="https://oceanofpdf.com/authors/frank-herbert/pdf-epub-dune-download/">Dune</a>
  </h2></header>
  <a class="entry-image-link"><img class="entry-image" data-src="https://media.oceanofpdf.com/dune.jpg"></a>
  <div class="postmetainfo">
    <strong>Author: </strong>Frank Herbert<br>
    <strong>Language: </strong>English<br>
  </div>
</article>
"""

DETAIL_HTML = """
<main class="entry-content">
  <ul><li><strong>PDF File Size</strong>: <strong>4.2 MB</strong></li></ul>
  <form action="https://oceanofpdf.com/Fetching_Resource.php" method="post">
    <input name="id" type="hidden" value="srv3">
    <input name="filename" type="hidden" value="Dune_-_Frank_Herbert.pdf">
  </form>
</main>
"""

RESOURCE_HTML = """
<html><body>
  <form id="my_form" action="https://ads.example/article" method="post">
    <input name="id" type="hidden" value="srv3">
    <input name="filename" type="hidden" value="Dune_-_Frank_Herbert.pdf">
  </form>
  <script>document.getElementById('my_form').submit();</script>
</body></html>
"""

HANDOFF_HTML = """
<script>
setTimeout("location.href = 'https://fs5.oceanofpdf.com/OceanofPDF.com/Dune_-_Frank_Herbert.pdf?token=abc';", 8000);
</script>
"""


@pytest.fixture(autouse=True)
def configured_mirrors(monkeypatch):
    monkeypatch.setattr(mirrors, "get_oceanofpdf_mirrors", lambda: ["https://oceanofpdf.com"])


def test_parse_search_page_creates_one_result_per_advertised_format():
    results = oceanofpdf.parse_results(SEARCH_HTML, SearchFilters(format=["pdf", "epub"]))

    assert [result.format for result in results] == ["pdf", "epub"]
    assert results[0].source == "direct_download"
    assert results[0].title == "Dune"
    assert results[0].author == "Frank Herbert"
    assert results[0].language == "en"
    assert results[0].preview == "https://media.oceanofpdf.com/dune.jpg"
    assert results[0].source_url == (
        "https://oceanofpdf.com/authors/frank-herbert/pdf-epub-dune-download/"
    )


def test_parse_download_forms_extracts_post_payload_and_size():
    forms = oceanofpdf.parse_download_forms(
        DETAIL_HTML,
        "https://oceanofpdf.com/authors/frank-herbert/pdf-epub-dune-download/",
    )

    assert len(forms) == 1
    assert forms[0]["action"] == "https://oceanofpdf.com/Fetching_Resource.php"
    assert forms[0]["fields"] == {"id": "srv3", "filename": "Dune_-_Frank_Herbert.pdf"}
    assert forms[0]["format"] == "pdf"
    assert forms[0]["size"] == "4.2 MB"


def test_oceanofpdf_results_are_exposed_by_direct_download(monkeypatch):

    records = oceanofpdf.parse_results(SEARCH_HTML, SearchFilters(format=["pdf", "epub"]))
    monkeypatch.setattr(
        config,
        "get",
        lambda key, default=None: {
            "DIRECT_DOWNLOAD_ENABLED": True,
            "SOURCE_PRIORITY": [{"id": "oceanofpdf", "enabled": True}],
        }.get(key, default),
    )
    monkeypatch.setattr(
        oceanofpdf.OceanofPDFProvider,
        "search",
        lambda *_args, **_kwargs: records,
    )
    monkeypatch.setattr(aa.AnnasArchiveProvider, "is_enabled", lambda _self: True)
    monkeypatch.setattr(
        aa.AnnasArchiveProvider,
        "search",
        lambda *_args, **_kwargs: [
            BrowseRecord(
                id="aa-id",
                title="Dune",
                source="direct_download",
            )
        ],
    )
    source = DirectDownloadSource()
    plan = SimpleNamespace(
        manual_query=None,
        primary_query="Dune Frank Herbert",
        title_variants=[SimpleNamespace(title="Dune")],
        source_filters=None,
        languages=None,
    )

    releases = source.search(SimpleNamespace(title="Dune"), plan)

    assert [release.source for release in releases] == ["direct_download"] * 3
    assert [release.extra.get("web_provider") for release in releases] == [
        "oceanofpdf",
        "oceanofpdf",
        None,
    ]
    with pytest.raises(ValueError, match="Unknown release source"):
        get_source("oceanofpdf")


def test_handler_reuses_shared_page_and_download_helpers(monkeypatch, tmp_path):

    monkeypatch.setattr(handler, "TMP_DIR", tmp_path)
    monkeypatch.setattr(aa.downloader, "html_get_page", lambda *_args, **_kwargs: DETAIL_HTML)
    monkeypatch.setattr(oceanofpdf, "_post_download_form", lambda *_args: BytesIO(b"x" * 12_000))
    monkeypatch.setattr(config, "get", lambda *_args, **_kwargs: "rename")
    progress = []
    statuses = []
    task = SimpleNamespace(
        source_url="https://oceanofpdf.com/authors/frank-herbert/pdf-epub-dune-download/",
        task_id="dune-pdf",
        format="pdf",
        title="Dune",
        author="Frank Herbert",
        year="1965",
        size=None,
        preview=None,
    )

    result = DirectDownloadHandler().download(
        task,
        SimpleNamespace(is_set=lambda: False),
        progress.append,
        lambda status, message: statuses.append((status, message)),
    )

    assert result == str(tmp_path / "Frank Herbert - Dune (1965).pdf")
    assert (tmp_path / "Frank Herbert - Dune (1965).pdf").stat().st_size == 12_000
    assert progress == [100.0]
    assert statuses[-1] == ("resolving", "Resolving PDF download")


def test_search_uses_configured_primary_mirror(monkeypatch):
    monkeypatch.setattr(
        mirrors,
        "get_oceanofpdf_mirrors",
        lambda: ["https://books.example", "https://backup.example"],
    )
    monkeypatch.setattr(
        config,
        "get",
        lambda key, default=None: (
            [{"id": "oceanofpdf", "enabled": True}] if key == "SOURCE_PRIORITY" else default
        ),
    )
    fetched = []
    fetch_options = []

    def fetch(url, **kwargs):
        fetched.append(url)
        fetch_options.append(kwargs)
        if "/page/2/" in url:
            return "<main>No more results</main>"
        return SEARCH_HTML.replace("oceanofpdf.com", "books.example")

    monkeypatch.setattr(oceanofpdf.downloader, "html_get_page", fetch)
    results = oceanofpdf.OceanofPDFProvider().search_query(
        "Dune & Herbert", SearchFilters(format=["pdf"])
    )

    assert fetched == [
        "https://books.example/?s=Dune+%26+Herbert",
        "https://books.example/page/2/?s=Dune+%26+Herbert",
    ]
    assert fetch_options == [
        {"retry": 2, "allow_bypasser_fallback": True, "success_delay": 0},
        {"retry": 2, "allow_bypasser_fallback": True, "success_delay": 0},
    ]
    assert len(results) == 1
    assert results[0].source_url.startswith("https://books.example/")


def test_search_aggregates_later_pages_when_first_page_formats_are_filtered(monkeypatch):
    monkeypatch.setattr(
        config,
        "get",
        lambda key, default=None: (
            [{"id": "oceanofpdf", "enabled": True}] if key == "SOURCE_PRIORITY" else default
        ),
    )
    pdf_only = SEARCH_HTML.replace("[PDF EPUB]", "[PDF]")
    third_page = SEARCH_HTML.replace("[PDF EPUB]", "[EPUB]").replace("Dune", "Dune EPUB")
    fourth_page = (
        SEARCH_HTML.replace("[PDF EPUB]", "[EPUB]")
        .replace("Dune", "Shadows")
        .replace("pdf-epub-dune-download", "epub-shadows-download")
    )
    fetched = []

    def fetch(url, **kwargs):
        fetched.append(url)
        if "/page/3/" in url:
            return third_page
        if "/page/4/" in url:
            return fourth_page
        if "/page/5/" in url:
            return "<main>No more results</main>"
        if "/page/2/" in url:
            return pdf_only
        return pdf_only

    monkeypatch.setattr(oceanofpdf.downloader, "html_get_page", fetch)

    results = oceanofpdf.OceanofPDFProvider().search_query("shadow", SearchFilters(format=["epub"]))

    assert fetched == [
        "https://oceanofpdf.com/?s=shadow",
        "https://oceanofpdf.com/page/2/?s=shadow",
        "https://oceanofpdf.com/page/3/?s=shadow",
        "https://oceanofpdf.com/page/4/?s=shadow",
        "https://oceanofpdf.com/page/5/?s=shadow",
    ]
    assert [(result.title, result.format) for result in results] == [
        ("Dune EPUB", "epub"),
        ("Shadows", "epub"),
    ]


def test_search_stops_after_result_limit_and_deduplicates(monkeypatch):
    monkeypatch.setattr(
        config,
        "get",
        lambda key, default=None: (
            [{"id": "oceanofpdf", "enabled": True}] if key == "SOURCE_PRIORITY" else default
        ),
    )
    fetched = []

    def result_page(start, count):
        return "".join(
            SEARCH_HTML.replace("[PDF EPUB]", "[EPUB]")
            .replace("Dune Download", f"Book {number} Download")
            .replace(">Dune<", f">Book {number}<")
            .replace("pdf-epub-dune-download", f"epub-book-{number}-download")
            for number in range(start, start + count)
        )

    def fetch(url, **kwargs):
        fetched.append(url)
        return result_page(1, 13) if "/page/2/" not in url else result_page(13, 13)

    monkeypatch.setattr(oceanofpdf.downloader, "html_get_page", fetch)

    results = oceanofpdf.OceanofPDFProvider().search_query("books", SearchFilters(format=["epub"]))

    assert len(results) == 25
    assert len({result.id for result in results}) == 25
    assert fetched == [
        "https://oceanofpdf.com/?s=books",
        "https://oceanofpdf.com/page/2/?s=books",
    ]


def test_search_stops_pagination_when_site_returns_no_cards(monkeypatch):
    monkeypatch.setattr(
        config,
        "get",
        lambda key, default=None: (
            [{"id": "oceanofpdf", "enabled": True}] if key == "SOURCE_PRIORITY" else default
        ),
    )
    fetched = []

    def fetch(url, **kwargs):
        fetched.append(url)
        return "<main>No more results</main>"

    monkeypatch.setattr(oceanofpdf.downloader, "html_get_page", fetch)

    assert oceanofpdf.OceanofPDFProvider().search_query("missing", SearchFilters()) == []
    assert fetched == ["https://oceanofpdf.com/?s=missing"]


def test_unconfigured_provider_does_not_search(monkeypatch):
    monkeypatch.setattr(mirrors, "get_oceanofpdf_mirrors", lambda: [])
    monkeypatch.setattr(config, "get", lambda *_args: True)

    def unexpected_fetch(*args, **kwargs):
        pytest.fail("Unconfigured OceanofPDF must not make a request")

    monkeypatch.setattr(oceanofpdf.downloader, "html_get_page", unexpected_fetch)
    provider = oceanofpdf.OceanofPDFProvider()
    assert provider.is_enabled() is False
    assert provider.search_query("Dune", SearchFilters()) == []
    assert provider.handles("https://oceanofpdf.com/book") is False


def test_priority_toggle_disables_provider_even_with_a_mirror(monkeypatch):
    monkeypatch.setattr(
        config,
        "get",
        lambda key, default=None: (
            [{"id": "oceanofpdf", "enabled": False}] if key == "SOURCE_PRIORITY" else default
        ),
    )

    assert oceanofpdf.OceanofPDFProvider().is_enabled() is False


def test_configured_domains_control_download_form_routing(monkeypatch):
    monkeypatch.setattr(mirrors, "get_oceanofpdf_mirrors", lambda: ["https://books.example"])
    provider = oceanofpdf.OceanofPDFProvider()
    assert provider.handles("https://files.books.example/book") is True
    assert provider.handles("https://books.example.evil.test/book") is False
    assert provider.handles("https://otherbooks.example/book") is False
    assert provider.handles("ftp://books.example/book") is False
    assert oceanofpdf.parse_download_forms(DETAIL_HTML, "https://books.example/book") == []
    forms = oceanofpdf.parse_download_forms(
        DETAIL_HTML.replace(
            "https://oceanofpdf.com/Fetching_Resource.php", "/Fetching_Resource.php"
        ),
        "https://books.example/book",
    )
    assert forms[0]["action"] == "https://books.example/Fetching_Resource.php"


def test_post_download_form_follows_explicit_handoff(monkeypatch):
    class FakeResponse:
        def __init__(self, url, content, content_type):
            self.url = url
            self.content = content
            self.text = content.decode()
            self.headers = {"content-type": content_type}

        def raise_for_status(self):
            return None

    post_responses = iter(
        [
            FakeResponse(
                "https://oceanofpdf.com/Fetching_Resource.php",
                RESOURCE_HTML.encode(),
                "text/html; charset=UTF-8",
            ),
            FakeResponse(
                "https://ads.example/article",
                HANDOFF_HTML.encode(),
                "text/html; charset=UTF-8",
            ),
        ]
    )
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return next(post_responses)

    downloaded = BytesIO(b"x" * 12_000)
    download_calls = []
    monkeypatch.setattr(oceanofpdf.requests, "post", post)
    monkeypatch.setattr(oceanofpdf.downloader, "get_cf_cookies_for_domain", lambda _host: {})
    monkeypatch.setattr(
        oceanofpdf.downloader,
        "download_url",
        lambda *args, **kwargs: download_calls.append((args, kwargs)) or downloaded,
    )
    form = oceanofpdf.parse_download_forms(DETAIL_HTML, "https://oceanofpdf.com/book")[0]

    result = oceanofpdf._post_download_form(form, "https://oceanofpdf.com/book", None, None, None)

    assert result is not None
    assert result is downloaded
    assert [url for url, _kwargs in calls] == [
        "https://oceanofpdf.com/Fetching_Resource.php",
        "https://ads.example/article",
    ]
    assert calls[1][1]["data"] == {
        "id": "srv3",
        "filename": "Dune_-_Frank_Herbert.pdf",
    }
    assert calls[0][1]["headers"]["Accept-Encoding"] == "gzip, deflate"
    assert download_calls[0][0][0] == (
        "https://fs5.oceanofpdf.com/OceanofPDF.com/Dune_-_Frank_Herbert.pdf?token=abc"
    )


def test_auto_download_url_rejects_an_external_file_host():
    with pytest.raises(RuntimeError, match="no automatic download URL"):
        oceanofpdf._parse_auto_download_url(
            "<script>location.href = 'https://ads.example/file.pdf';</script>",
            "https://ads.example/article",
            "pdf",
        )


def test_auto_download_url_rejects_wrong_format():
    with pytest.raises(RuntimeError, match="wrong format"):
        oceanofpdf._parse_auto_download_url(
            "<script>location.href = 'https://fs5.oceanofpdf.com/file.epub';</script>",
            "https://ads.example/article",
            "pdf",
        )
