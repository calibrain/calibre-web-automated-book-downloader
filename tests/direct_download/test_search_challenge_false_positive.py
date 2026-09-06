"""A real Anna's Archive page must not be mistaken for a protection interstitial.

Regression for #1289/#1292. `_looks_like_challenge_page` substring-matched "ddos-guard"
over the whole document, and DDoS-Guard-fronted sites carry that string on their *own*
pages - Anna's Archive ships a `DDOS-GUARD` comment in the inline JS it serves on every
page. Any real AA response that was not a results table was therefore reported as an
unsolved challenge, telling users to go fix a bypasser that had just succeeded.
"""

import pytest
from bs4 import Tag

from shelfmark.release_sources.direct_download import annas_archive as aa

# Verbatim from a live annas-archive.pk 403, trimmed of nothing that matters: this is
# what an interstitial actually looks like, and it is under a kilobyte.
DDOS_GUARD_INTERSTITIAL = (
    '<!doctype html><html><head><title>DDoS-Guard</title><meta charset="utf-8"/>'
    '<link rel="stylesheet" href="/.well-known/ddos-guard/js-challenge/index.css">'
    '<script defer="defer" src="/.well-known/ddos-guard/js-challenge/index.js"></script>'
    '<script src="https://check.ddos-guard.net/check.js"></script></head>'
    '<body data-ddg-origin="true"><div class="container"><h1 id="ddg-l10n-title">'
    'Checking your browser before accessing <span class="ddg-origin"></span></h1>'
    "<p>Please wait a few seconds.</p></div></body></html>"
)

# The marker that made every real AA page look like a challenge, quoted from the live
# site's inline JS, plus enough real page to clear the 64 KB size guard.
_AA_DDG_COMMENT = '// "text/css" for DDOS-GUARD caching.'
AA_PAGE_WITHOUT_TABLE = (
    "<!doctype html><html><head><title>Anna’s Archive</title></head><body>"
    f"<script>function f(){{ {_AA_DDG_COMMENT} fetch('/dyn/recent_downloads/'); }}</script>"
    '<main><a href="/md5/abc123">a record</a>'
    + ("<p>real page body content</p>" * 3000)
    + "</main></body></html>"
)


class _Selector:
    def __init__(self, bases: list[str]) -> None:
        self._bases = bases
        self._index = 0
        self.current_base = bases[0]
        self.quarantined: list[str] = []

    def rewrite(self, url: str) -> str:
        for base in self._bases:
            if url.startswith(base):
                return url.replace(base, self.current_base, 1)
        return url

    def next_mirror_or_rotate_dns(self, *, fatal: bool = False, reason: str = ""):
        if fatal:
            self.quarantined.append(self.current_base)
        self._index += 1
        if self._index >= len(self._bases):
            return None, "exhausted"
        self.current_base = self._bases[self._index]
        return self.current_base, "mirror"


def _patch_pages(monkeypatch, pages: list[str]):
    from shelfmark.release_sources.direct_download import source as dd

    calls: list[str] = []

    def fake_get(url, **_kwargs):
        calls.append(url)
        return pages[len(calls) - 1] if len(calls) <= len(pages) else ""

    monkeypatch.setattr(aa.downloader, "html_get_page", fake_get)
    monkeypatch.setattr(aa.network, "get_available_aa_urls", lambda: ["a", "b"])
    return dd, calls


@pytest.fixture
def search_logs():
    """Collect this module's log messages.

    setup_logger builds loggers outside the standard hierarchy, so their records never
    reach the root handler caplog installs - see tests/bypass/test_ddg_cookie_reuse.py.
    """
    import logging

    messages: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            messages.append(record.getMessage())

    handler = _Capture()
    aa.logger.addHandler(handler)
    previous = aa.logger.level
    aa.logger.setLevel(logging.DEBUG)
    # Logger.setLevel only invalidates the is-enabled cache through the manager, which
    # these loggers are not registered with; without this the DEBUG line stays filtered.
    aa.logger._cache.clear()
    try:
        yield messages
    finally:
        aa.logger.removeHandler(handler)
        aa.logger.setLevel(previous)


def test_the_size_guard_is_what_separates_a_real_page_from_an_interstitial():
    """The two inputs this bug turned on, checked directly."""
    from shelfmark.bypass.challenge import MAX_CHALLENGE_HTML_CHARS

    assert len(DDOS_GUARD_INTERSTITIAL) < MAX_CHALLENGE_HTML_CHARS
    assert len(AA_PAGE_WITHOUT_TABLE) > MAX_CHALLENGE_HTML_CHARS
    # Both contain "ddos-guard"; only one is a challenge.
    assert "ddos-guard" in AA_PAGE_WITHOUT_TABLE.lower()
    assert aa._looks_like_challenge_page(DDOS_GUARD_INTERSTITIAL)
    assert not aa._looks_like_challenge_page(AA_PAGE_WITHOUT_TABLE)


def test_real_aa_page_without_a_table_is_not_reported_as_a_challenge(monkeypatch):
    """The #1289 failure: a served AA page raised "unsolved protection challenge"."""
    _dd, calls = _patch_pages(monkeypatch, [AA_PAGE_WITHOUT_TABLE])
    selector = _Selector(["https://real.test", "https://other.test"])

    html, table = aa._fetch_search_table("https://real.test/search?q=malice", selector)

    assert table is None
    assert html == AA_PAGE_WITHOUT_TABLE
    # A live mirror holding our clearance: neither quarantined nor rotated away from.
    assert selector.quarantined == []
    assert len(calls) == 1


def test_aa_markers_win_over_challenge_markers_on_the_same_page(monkeypatch):
    """Ordering, not just the size guard, keeps a marker-carrying AA page readable."""
    _dd, _calls = _patch_pages(monkeypatch, [AA_PAGE_WITHOUT_TABLE])
    monkeypatch.setattr(aa, "_looks_like_challenge_page", lambda _html: True)
    selector = _Selector(["https://real.test", "https://other.test"])

    _html, table = aa._fetch_search_table("https://real.test/search?q=malice", selector)

    assert table is None


def test_genuine_interstitial_still_raises(monkeypatch):
    """The behaviour the check exists for is untouched."""
    _dd, _calls = _patch_pages(monkeypatch, [DDOS_GUARD_INTERSTITIAL])
    selector = _Selector(["https://real.test", "https://other.test"])

    with pytest.raises(aa.SearchUnavailableError, match="protection challenge"):
        aa._fetch_search_table("https://real.test/search?q=malice", selector)

    assert selector.quarantined == []


def test_results_table_is_still_returned(monkeypatch):
    """A page carrying the marker and a table is read as results, as before."""
    page = AA_PAGE_WITHOUT_TABLE.replace(
        "<main>", "<main><table><tbody><tr><td>Malice</td></tr></tbody></table>"
    )
    _dd, _calls = _patch_pages(monkeypatch, [page])
    selector = _Selector(["https://real.test"])

    _html, table = aa._fetch_search_table("https://real.test/search?q=malice", selector)

    assert isinstance(table, Tag)


def test_untabled_page_is_fingerprinted_in_the_log(monkeypatch, search_logs):
    """#1289 was unsolvable from the bundle because no line said what came back.

    The log must carry the facts that separate the two cases, so the next report is read
    rather than reverse-engineered: size, whether the size guard applied, the AA markers
    found, and the challenge marker (or its absence).
    """
    _dd, _calls = _patch_pages(monkeypatch, [AA_PAGE_WITHOUT_TABLE])
    selector = _Selector(["https://real.test", "https://other.test"])

    aa._fetch_search_table("https://real.test/search?q=malice", selector)

    verdict = next(m for m in search_logs if "no results table" in m)
    assert f"bytes={len(AA_PAGE_WITHOUT_TABLE)}" in verdict
    assert "over_challenge_size_cap=True" in verdict
    assert "challenge_marker=None" in verdict
    assert "/md5/" in verdict
    # The head of the document is quoted too, bounded so a 180 KB page cannot flood the
    # log file that ships inside the debug bundle.
    head = next(m for m in search_logs if "Untabled search page head" in m)
    assert AA_PAGE_WITHOUT_TABLE[:200] in head
    assert len(head) < 2000


def test_interstitial_fingerprint_names_the_marker_that_proved_it(monkeypatch, search_logs):
    """The same line must also settle the opposite case, without needing the body."""
    _dd, _calls = _patch_pages(monkeypatch, [DDOS_GUARD_INTERSTITIAL])
    selector = _Selector(["https://real.test", "https://other.test"])

    with pytest.raises(aa.SearchUnavailableError):
        aa._fetch_search_table("https://real.test/search?q=malice", selector)

    verdict = next(m for m in search_logs if "no results table" in m)
    assert "over_challenge_size_cap=False" in verdict
    assert "challenge_marker='/.well-known/ddos-guard/'" in verdict
    assert "aa_markers=none" in verdict


def test_fingerprint_failure_never_breaks_a_search(monkeypatch):
    """Diagnostics are best-effort; a bug in them must not cost the user their search."""
    _dd, _calls = _patch_pages(monkeypatch, [AA_PAGE_WITHOUT_TABLE])

    def boom(_html):
        raise RuntimeError("marker scan blew up")

    monkeypatch.setattr(aa, "challenge_marker", boom)
    selector = _Selector(["https://real.test", "https://other.test"])

    _html, table = aa._fetch_search_table("https://real.test/search?q=malice", selector)

    assert table is None
