"""OceanofPDF provider: search parsing, download resolution and registry wiring.

The fixtures mirror the live site's markup (Genesis WordPress theme) as of 2026-10:
search results are <article> posts, the book page carries one POST form per format, and
the form's response is an interstitial with a meta refresh to a signed file URL.
"""

from io import BytesIO
from threading import Event
from types import SimpleNamespace

import pytest
from bs4 import BeautifulSoup

from shelfmark.core import mirrors
from shelfmark.core.models import SearchFilters
from shelfmark.release_sources import BrowseRecord
from shelfmark.release_sources.direct_download import oceanofpdf as op
from shelfmark.release_sources.direct_download import registry
from shelfmark.release_sources.direct_download.common import DirectDownloadUnavailableError

BASE = "https://ocean.example"
DUNE_URL = f"{BASE}/authors/frank-herbert/pdf-epub-dune-download-85663993149/"
SONG_URL = f"{BASE}/authors/anissa-m-bouziane/pdf-epub-dune-song-download/"
ART_URL = f"{BASE}/authors/alejandro-jodorowsky/pdf-dune-art-book-download/"
FETCH_URL = f"{BASE}/Fetching_Resource.php"
FILE_URL = "https://files.example/OceanofPDF.com/Dune_1_-_Frank_Herbert.epub?md5=abc&expires=1"


def _article(url: str, title: str, label: str, summary: str, cover: str = "") -> str:
    return f"""
    <article class="post type-post entry" aria-label="{label}">
      <header class="entry-header">
        <a class="entry-image-link" href="{url}"><img src="data:image/svg+xml,placeholder"
           data-src="{cover}"></a>
        <h2 class="entry-title"><a class="entry-title-link" href="{url}">{title}</a></h2>
        <p class="entry-meta"><time class="entry-time">September 27, 2019</time></p>
      </header>
      <div class="entry-content"><p>{summary} [Read more…]</p></div>
    </article>"""


def _page(body: str) -> str:
    return f"""<html><head><title>OceanofPDF</title></head><body>
    <a class="genesis-skip-link" href="#genesis-content">Skip to main content</a>
    <main id="genesis-content">{body}</main></body></html>"""


SEARCH_PAGE = _page(
    _article(
        DUNE_URL,
        "Dune",
        "[PDF] [EPUB] Dune Download",
        "[PDF] [EPUB] Dune Download by Frank Herbert. Download Dune by Frank Herbert in PDF "
        "EPUB format complete free.",
        cover="https://media.example/dune.jpg",
    )
    + _article(
        SONG_URL,
        "Dune Song",
        "[PDF] [EPUB] Dune Song Download",
        "[PDF] [EPUB] Dune Song Download by Anissa M. Bouziane. Download Dune Song by Anissa "
        "M. Bouziane in PDF EPUB format complete free.",
    )
    + _article(
        ART_URL,
        "Dune Art Book",
        "[PDF] Dune Art Book Download",
        "[PDF] Dune Art Book Download by Alejandro Jodorowsky. Download Dune Art Book in PDF "
        "format complete free.",
    )
)

EMPTY_SEARCH_PAGE = _page("<h1>Nothing Found</h1>")

BOOK_PAGE = _page(
    """
    <div class="entry-content">
      <ul><li>Full Book Name: Dune</li><li>Author Name: Frank Herbert</li>
      <li>PDF File Size: 2.1 MB</li><li>EPUB File Size: 952 KB</li></ul>
      <form action="https://ocean.example/" method="get"><input name="s" type="search"></form>
      <form action="https://ocean.example/Fetching_Resource.php" method="post" target="_blank">
        <input type="hidden" name="id" value="srv3">
        <input type="hidden" name="filename" value="Dune_1_-_Frank_Herbert.pdf">
      </form>
      <form action="https://ocean.example/Fetching_Resource.php" method="post" target="_blank">
        <input type="hidden" name="id" value="srv3">
        <input type="hidden" name="filename" value="Dune_1_-_Frank_Herbert.epub">
      </form>
    </div>"""
)

INTERSTITIAL_PAGE = _page(
    f'<meta http-equiv="refresh" content="0; url={FILE_URL}">'
    "<p>Don't worry! Your download will still start.</p>"
)

CHALLENGE_PAGE = (
    "<html><head><title>Just a moment...</title></head><body>"
    '<div id="challenge-form"></div><script src="/cdn-cgi/challenge-platform/x.js"></script>'
    "</body></html>"
)


@pytest.fixture
def configured(monkeypatch):
    """Configure an OceanofPDF mirror and keep every request off the network.

    Returns the settings the provider reads, so a test can flip the toggle or the bypass.
    """
    settings = {
        "USE_CF_BYPASS": True,
        "SOURCE_PRIORITY": [{"id": "zlib", "enabled": True}, {"id": "oceanofpdf", "enabled": True}],
    }
    monkeypatch.setattr(
        op, "config", SimpleNamespace(get=lambda key, default=None: settings.get(key, default))
    )
    monkeypatch.setattr(mirrors, "_get_config", lambda: {"OCEANOFPDF_MIRROR_URLS": [BASE]})
    monkeypatch.setattr(op.network, "AAMirrorSelector", lambda: SimpleNamespace(last_failure=None))
    monkeypatch.setattr(op.network, "get_proxies", lambda _url: {})
    monkeypatch.setattr(op.network, "get_ssl_verify", lambda _url: True)
    monkeypatch.setattr(op.downloader, "get_cf_cookies_for_domain", lambda _host: {})
    monkeypatch.setattr(op.downloader, "get_cf_user_agent_for_domain", lambda _host: None)
    return settings


def _plan(**overrides):
    values = {
        "manual_query": None,
        "source_filters": None,
        "languages": None,
        "author": "Frank Herbert",
        "grouped_title_variants": [SimpleNamespace(title="Dune")],
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _serve_pages(monkeypatch, pages: dict[str, str]) -> list[str]:
    """Answer html_get_page from ``pages`` by URL, recording each URL requested."""
    requested: list[str] = []

    def fake_html_get_page(url, *_args, **_kwargs):
        requested.append(url)
        return pages.get(url, "")

    monkeypatch.setattr(op.downloader, "html_get_page", fake_html_get_page)
    return requested


# --- Search parsing ---


def test_search_results_split_into_one_record_per_format():
    records = op.parse_search_results(SEARCH_PAGE, SearchFilters(format=["pdf", "epub"]))

    assert [(r.title, r.format) for r in records] == [
        ("Dune", "pdf"),
        ("Dune", "epub"),
        ("Dune Song", "pdf"),
        ("Dune Song", "epub"),
        ("Dune Art Book", "pdf"),
    ]
    dune_pdf, dune_epub = records[0], records[1]
    assert dune_pdf.id.startswith("oceanofpdf:")
    assert dune_pdf.id != dune_epub.id
    assert dune_pdf.source_url == DUNE_URL
    assert dune_pdf.author == "Frank Herbert"
    assert dune_pdf.preview == "https://media.example/dune.jpg"
    assert dune_pdf.source == "direct_download"


def test_record_ids_are_stable_across_searches():
    first = op.parse_search_results(SEARCH_PAGE, SearchFilters())
    second = op.parse_search_results(SEARCH_PAGE, SearchFilters())

    assert [r.id for r in first] == [r.id for r in second]


def test_author_with_initials_is_not_cut_at_the_first_full_stop():
    records = op.parse_search_results(SEARCH_PAGE, SearchFilters())

    assert {r.author for r in records if r.title == "Dune Song"} == {"Anissa M. Bouziane"}


def test_unfiltered_search_keeps_only_the_configured_supported_formats(monkeypatch):
    monkeypatch.setattr(
        "shelfmark.release_sources.direct_download.common.get_supported_formats",
        lambda: ["epub", "mobi"],
    )

    records = op.parse_search_results(SEARCH_PAGE, SearchFilters())

    assert {r.format for r in records} == {"epub"}


def test_format_filter_drops_other_formats():
    records = op.parse_search_results(SEARCH_PAGE, SearchFilters(format=["epub"]))

    assert {r.format for r in records} == {"epub"}
    assert "Dune Art Book" not in {r.title for r in records}


def test_results_without_a_language_survive_a_language_filter():
    records = op.parse_search_results(SEARCH_PAGE, SearchFilters(lang=["en"]))

    assert records


def test_empty_search_page_parses_to_no_records():
    assert op.parse_search_results(EMPTY_SEARCH_PAGE, SearchFilters()) == []


# --- Book page and interstitial parsing ---


def test_download_form_is_picked_by_format():
    soup = BeautifulSoup(BOOK_PAGE, "html.parser")

    action, fields = op.pick_download_form(soup, "epub")

    assert action == FETCH_URL
    assert fields == {"id": "srv3", "filename": "Dune_1_-_Frank_Herbert.epub"}


def test_download_form_ignores_the_search_form_and_defaults_to_the_first():
    soup = BeautifulSoup(BOOK_PAGE, "html.parser")

    _action, fields = op.pick_download_form(soup, None)

    assert fields["filename"].endswith(".pdf")


def test_download_form_is_none_for_a_format_the_page_lacks():
    soup = BeautifulSoup(BOOK_PAGE, "html.parser")

    assert op.pick_download_form(soup, "mobi") is None


def test_file_size_is_read_for_the_requested_format():
    soup = BeautifulSoup(BOOK_PAGE, "html.parser")

    assert op.file_size_from_page(soup, "epub") == "952 KB"
    assert op.file_size_from_page(soup, "pdf") == "2.1 MB"
    assert op.file_size_from_page(soup, "mobi") is None


def test_meta_refresh_gives_the_file_url_and_delay():
    html = f'<meta http-equiv="Refresh" content="5;URL=\'{FILE_URL}\'">'

    assert op.parse_file_redirect(html, FETCH_URL) == (FILE_URL, 5)


def test_relative_refresh_target_is_resolved_against_the_page():
    html = '<meta http-equiv="refresh" content="3; url=/files/book.epub">'

    assert op.parse_file_redirect(html, FETCH_URL) == (f"{BASE}/files/book.epub", 3)


def test_plain_file_link_is_used_when_there_is_no_meta_refresh():
    html = '<a href="https://files.example/x/book.epub?md5=1">Download</a>'

    assert op.parse_file_redirect(html, FETCH_URL) == ("https://files.example/x/book.epub?md5=1", 0)


def test_interstitial_without_a_file_link_resolves_to_none():
    assert op.parse_file_redirect("<p>Please donate</p>", FETCH_URL) is None


# --- Provider search ---


def test_search_queries_title_and_author_first(configured, monkeypatch):
    requested = _serve_pages(monkeypatch, {f"{BASE}/?s=Dune+Frank+Herbert": SEARCH_PAGE})

    records = op.OceanofPDFProvider().search(SimpleNamespace(), _plan())

    assert requested == [f"{BASE}/?s=Dune+Frank+Herbert"]
    assert records[0].title == "Dune"


def test_search_falls_back_to_the_bare_title(configured, monkeypatch):
    requested = _serve_pages(
        monkeypatch,
        {f"{BASE}/?s=Dune+Frank+Herbert": EMPTY_SEARCH_PAGE, f"{BASE}/?s=Dune": SEARCH_PAGE},
    )

    records = op.OceanofPDFProvider().search(SimpleNamespace(), _plan())

    assert requested == [f"{BASE}/?s=Dune+Frank+Herbert", f"{BASE}/?s=Dune"]
    assert records


def test_manual_search_sends_the_query_as_typed(configured, monkeypatch):
    requested = _serve_pages(monkeypatch, {f"{BASE}/?s=dune+song": SEARCH_PAGE})

    op.OceanofPDFProvider().search(
        SimpleNamespace(), _plan(manual_query="dune song", source_filters=SearchFilters())
    )

    assert requested == [f"{BASE}/?s=dune+song"]


def test_unreachable_site_is_reported_not_treated_as_no_results(configured, monkeypatch):
    _serve_pages(monkeypatch, {})

    with pytest.raises(DirectDownloadUnavailableError, match="Unable to reach OceanofPDF"):
        op.OceanofPDFProvider().search(SimpleNamespace(), _plan())


def test_uncleared_challenge_is_reported(configured, monkeypatch):
    _serve_pages(monkeypatch, {f"{BASE}/?s=Dune+Frank+Herbert": CHALLENGE_PAGE})

    with pytest.raises(DirectDownloadUnavailableError, match="Cloudflare challenge"):
        op.OceanofPDFProvider().search(SimpleNamespace(), _plan())


def test_real_page_carrying_challenge_markers_is_not_misread(configured, monkeypatch):
    page = SEARCH_PAGE.replace("</body>", '<script src="/cdn-cgi/challenge-platform/x.js">')
    _serve_pages(monkeypatch, {f"{BASE}/?s=Dune+Frank+Herbert": page})

    assert op.OceanofPDFProvider().search(SimpleNamespace(), _plan())


def test_search_without_a_mirror_returns_nothing(monkeypatch):
    monkeypatch.setattr(mirrors, "_get_config", lambda: {})

    assert op.OceanofPDFProvider().search(SimpleNamespace(), _plan()) == []


# --- Provider download ---


class _FakeResponse:
    def __init__(self, text: str, status_code: int = 200, url: str = FETCH_URL):
        self.text = text
        self.status_code = status_code
        self.url = url
        self.headers: dict[str, str] = {}
        self.is_redirect = False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise op.requests.HTTPError(f"{self.status_code} error")


def _record(book_format: str = "epub") -> BrowseRecord:
    return BrowseRecord(
        id="oceanofpdf:abc",
        title="Dune",
        source="direct_download",
        format=book_format,
        source_url=DUNE_URL,
    )


def _wire_download(monkeypatch, *, post_responses, payload: bytes):
    """Serve the book page, queue POST responses and return the file as ``payload``."""
    _serve_pages(monkeypatch, {DUNE_URL: BOOK_PAGE})
    posts: list[dict] = []
    downloads: list[dict] = []
    queued = list(post_responses)

    def fake_post(url, *, data, headers, **_kwargs):
        posts.append({"url": url, "data": data, "headers": headers})
        return queued.pop(0)

    def fake_download_url(link, size, *_args, referer=None, **_kwargs):
        downloads.append({"link": link, "size": size, "referer": referer})
        return BytesIO(payload)

    monkeypatch.setattr(op.requests, "post", fake_post)
    monkeypatch.setattr(op.downloader, "download_url", fake_download_url)
    return posts, downloads


def test_download_posts_the_form_and_saves_the_file(configured, monkeypatch, tmp_path):
    posts, downloads = _wire_download(
        monkeypatch, post_responses=[_FakeResponse(INTERSTITIAL_PAGE)], payload=b"PK" * 10_000
    )
    book_path = tmp_path / "dune.epub"

    result = op.OceanofPDFProvider().download(_record(), book_path, None, Event(), None)

    assert result == FILE_URL
    assert book_path.read_bytes() == b"PK" * 10_000
    assert posts[0]["url"] == FETCH_URL
    assert posts[0]["data"] == {"id": "srv3", "filename": "Dune_1_-_Frank_Herbert.epub"}
    assert posts[0]["headers"]["Referer"] == DUNE_URL
    assert downloads == [{"link": FILE_URL, "size": "952 KB", "referer": FETCH_URL}]


def test_download_form_post_never_offers_brotli(configured, monkeypatch, tmp_path):
    # requests cannot decode Brotli without the optional package, and Cloudflare answers
    # in Brotli whenever it is offered - the interstitial came back unreadable.
    posts, _downloads = _wire_download(
        monkeypatch, post_responses=[_FakeResponse(INTERSTITIAL_PAGE)], payload=b"PK" * 10_000
    )

    op.OceanofPDFProvider().download(_record(), tmp_path / "d.epub", None, Event(), None)

    assert "br" not in posts[0]["headers"].get("Accept-Encoding", "")


def test_page_without_a_file_link_is_described_in_the_log(configured, monkeypatch, tmp_path):
    page = _FakeResponse("<html><title>Fetching Resource</title><p>Please donate</p></html>")
    page.headers = {"Content-Type": "text/html", "Content-Encoding": "br"}
    _wire_download(monkeypatch, post_responses=[page], payload=b"")
    warnings: list[str] = []
    monkeypatch.setattr(op.logger, "warning", lambda msg, *args: warnings.append(msg % args))

    result = op.OceanofPDFProvider().download(_record(), tmp_path / "d.epub", None, Event(), None)

    assert result is None
    described = [message for message in warnings if "had no file link" in message]
    assert described
    assert "content-encoding='br'" in described[0]
    assert "title='Fetching Resource'" in described[0]


def test_download_rejects_an_html_page_served_as_the_file(configured, monkeypatch, tmp_path):
    _wire_download(
        monkeypatch,
        post_responses=[_FakeResponse(INTERSTITIAL_PAGE)],
        payload=b"<!DOCTYPE html>" + b" " * 20_000,
    )
    book_path = tmp_path / "dune.epub"

    assert op.OceanofPDFProvider().download(_record(), book_path, None, Event(), None) is None
    assert not book_path.exists()


def test_challenged_form_post_refreshes_clearance_and_retries(configured, monkeypatch, tmp_path):
    posts, _downloads = _wire_download(
        monkeypatch,
        post_responses=[_FakeResponse(CHALLENGE_PAGE, 403), _FakeResponse(INTERSTITIAL_PAGE)],
        payload=b"PK" * 10_000,
    )
    bypassed: list[str] = []
    page_fetch = op.downloader.html_get_page

    def fake_html_get_page(url, *args, use_bypasser=False, **kwargs):
        if use_bypasser:
            bypassed.append(url)
        return page_fetch(url, *args, **kwargs)

    monkeypatch.setattr(op.downloader, "html_get_page", fake_html_get_page)

    result = op.OceanofPDFProvider().download(_record(), tmp_path / "d.epub", None, Event(), None)

    assert result == FILE_URL
    assert bypassed == [DUNE_URL]
    assert len(posts) == 2


def test_missing_format_on_the_book_page_fails_cleanly(configured, monkeypatch, tmp_path):
    posts, _downloads = _wire_download(monkeypatch, post_responses=[], payload=b"")

    result = op.OceanofPDFProvider().download(
        _record("mobi"), tmp_path / "d.mobi", None, Event(), None
    )

    assert result is None
    assert posts == []


def test_cancelled_download_never_posts(configured, monkeypatch, tmp_path):
    posts, _downloads = _wire_download(monkeypatch, post_responses=[], payload=b"")
    cancelled = Event()
    cancelled.set()

    result = op.OceanofPDFProvider().download(_record(), tmp_path / "d.epub", None, cancelled, None)

    assert result is None
    assert posts == []


# --- Registry wiring ---


def test_oceanofpdf_records_route_to_the_oceanofpdf_provider(configured):
    provider = registry.provider_for_record(_record())

    assert provider is not None
    assert provider.id == "oceanofpdf"


def test_provider_is_enabled_only_with_a_mirror(configured, monkeypatch):
    provider = op.OceanofPDFProvider()
    assert provider.is_enabled()

    monkeypatch.setattr(mirrors, "_get_config", lambda: {})
    assert not provider.is_enabled()


def test_toggle_off_disables_the_provider(configured):
    configured["SOURCE_PRIORITY"] = [{"id": "oceanofpdf", "enabled": False}]

    assert not op.OceanofPDFProvider().is_enabled()


def test_saved_list_from_before_oceanofpdf_counts_as_off(configured):
    # The settings page shows an option missing from the saved list as off; agree with it.
    configured["SOURCE_PRIORITY"] = [{"id": "zlib", "enabled": True}]

    assert not op.OceanofPDFProvider().is_enabled()


def test_nothing_saved_falls_back_to_on(configured):
    configured["SOURCE_PRIORITY"] = None

    assert op.OceanofPDFProvider().is_enabled()


def test_provider_is_off_without_the_cloudflare_bypass(configured):
    configured["USE_CF_BYPASS"] = False

    assert not op.OceanofPDFProvider().is_enabled()


def test_toggle_is_offered_in_slow_sources_and_on_by_default(configured):
    from shelfmark.config import settings as settings_module

    options = {o["id"]: o for o in settings_module._get_slow_source_options()}
    defaults = {d["id"]: d for d in settings_module._get_slow_source_defaults()}

    assert options["oceanofpdf"]["label"] == "OceanofPDF"
    assert options["oceanofpdf"]["isLocked"] is False
    assert defaults["oceanofpdf"]["enabled"] is True


def test_toggle_is_locked_until_a_mirror_is_added(configured, monkeypatch):
    from shelfmark.config import settings as settings_module

    monkeypatch.setattr(mirrors, "_get_config", lambda: {})
    option = next(o for o in settings_module._get_slow_source_options() if o["id"] == "oceanofpdf")

    assert option["isLocked"] is True
    assert option["disabledReason"] == "Add at least one OceanofPDF mirror in Mirrors"


def test_annas_archive_cascade_ignores_the_oceanofpdf_entry(monkeypatch):
    from shelfmark.release_sources.direct_download import annas_archive as aa

    priority = [{"id": "zlib", "enabled": True}, {"id": "oceanofpdf", "enabled": True}]
    monkeypatch.setattr(
        aa.config,
        "get",
        lambda key, default=None: priority if key == "SOURCE_PRIORITY" else default,
    )

    assert "oceanofpdf" not in [entry["id"] for entry in aa._get_source_priority()]


def test_provider_handles_only_its_configured_host(configured):
    provider = op.OceanofPDFProvider()

    assert provider.handles(DUNE_URL)
    assert not provider.handles("https://annas-archive.example/md5/abc")


def test_direct_download_is_available_with_only_oceanofpdf(configured, monkeypatch):
    monkeypatch.setattr(
        registry.config,
        "get",
        lambda key, default=None: True if key == "DIRECT_DOWNLOAD_ENABLED" else default,
    )

    assert registry.get_unavailable_reason() is None
    assert [p.id for p in registry.enabled_providers()] == ["oceanofpdf"]


def test_mirror_list_is_normalised_and_first_entry_wins(monkeypatch):
    monkeypatch.setattr(
        mirrors,
        "_get_config",
        lambda: {"OCEANOFPDF_MIRROR_URLS": ["ocean.example/", "https://backup.example"]},
    )

    assert mirrors.get_oceanofpdf_mirrors() == ["https://ocean.example", "https://backup.example"]
    assert mirrors.get_oceanofpdf_base_url() == "https://ocean.example"


# --- Hostile records and pages ---


def _hostile_record(url: str) -> BrowseRecord:
    return BrowseRecord(
        id="oceanofpdf:x",
        title="Dune",
        source="direct_download",
        format="epub",
        source_url=url,
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8080/admin",
        "http://169.254.169.254/latest/meta-data/",
        "https://other.example/book/",
        "http://ocean.example/authors/dune/",
        "https://ocean.example:8443/authors/dune/",
        "file:///etc/passwd",
    ],
)
def test_download_never_fetches_a_source_url_off_the_mirror(configured, monkeypatch, tmp_path, url):
    requested = _serve_pages(monkeypatch, {url: BOOK_PAGE})
    posts, downloads = _wire_download(monkeypatch, post_responses=[], payload=b"PK" * 10_000)

    with pytest.raises(RuntimeError):
        op.OceanofPDFProvider().download(
            _hostile_record(url), tmp_path / "x.epub", None, Event(), None
        )

    assert url not in requested
    assert posts == []
    assert downloads == []


def test_download_refuses_when_the_provider_is_switched_off(configured, monkeypatch, tmp_path):
    configured["SOURCE_PRIORITY"] = [{"id": "oceanofpdf", "enabled": False}]
    requested = _serve_pages(monkeypatch, {DUNE_URL: BOOK_PAGE})

    with pytest.raises(RuntimeError):
        op.OceanofPDFProvider().download(_record(), tmp_path / "x.epub", None, Event(), None)

    assert requested == []


@pytest.mark.parametrize(
    "action",
    ["http://127.0.0.1:9000/Fetching_Resource.php", "https://evil.example/Fetching_Resource.php"],
)
def test_download_never_posts_to_a_form_action_off_the_mirror(
    configured, monkeypatch, tmp_path, action
):
    page = BOOK_PAGE.replace("https://ocean.example/Fetching_Resource.php", action)
    posts, downloads = _wire_download(monkeypatch, post_responses=[], payload=b"PK" * 10_000)
    _serve_pages(monkeypatch, {DUNE_URL: page})

    result = op.OceanofPDFProvider().download(_record(), tmp_path / "x.epub", None, Event(), None)

    assert result is None
    assert posts == []
    assert downloads == []


def test_download_never_fetches_a_file_link_that_is_not_http(configured, monkeypatch, tmp_path):
    interstitial = _page('<meta http-equiv="refresh" content="0; url=file:///etc/passwd">')
    _posts, downloads = _wire_download(
        monkeypatch, post_responses=[_FakeResponse(interstitial)], payload=b"PK" * 10_000
    )

    result = op.OceanofPDFProvider().download(_record(), tmp_path / "x.epub", None, Event(), None)

    assert result is None
    assert downloads == []


def test_form_post_redirect_off_the_mirror_is_not_followed(configured, monkeypatch, tmp_path):
    redirect = _FakeResponse("", status_code=302)
    redirect.is_redirect = True
    redirect.headers = {"Location": "http://127.0.0.1:9000/internal"}
    posts, downloads = _wire_download(
        monkeypatch, post_responses=[redirect], payload=b"PK" * 10_000
    )
    gets: list[str] = []
    monkeypatch.setattr(op.requests, "get", lambda url, **_kw: gets.append(url))

    result = op.OceanofPDFProvider().download(_record(), tmp_path / "x.epub", None, Event(), None)

    assert result is None
    assert len(posts) == 1
    assert gets == []
    assert downloads == []
