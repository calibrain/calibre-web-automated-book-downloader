"""A mirror that answers 200 with a non-AA page is quarantined, not reported as empty.

Seized and for-sale domains keep serving 200. Without a look at *what* came back, a
parking page is indistinguishable from a broken search, so the mirror stays in
rotation and every later search pays for it again.
"""

import pytest
from bs4 import Tag

from shelfmark.release_sources.direct_download import annas_archive as aa

PARKED_PAGE = """<!doctype html><html><head><title>annas-archive.li</title></head>
<body><h1>This domain is for sale</h1><p>Inquire now. Buy this domain.</p></body></html>"""

AA_RESULTS_PAGE = """<!doctype html><html><body><main><table><tbody>
<tr><td><a href="/md5/abc123"><img src="/c.jpg"></a></td><td><span>Dune</span></td></tr>
</tbody></table></main></body></html>"""

AA_EMPTY_PAGE = """<!doctype html><html><body><main>
<div>No files found.</div><a href="https://annas-archive.gl/about">about</a>
</main></body></html>"""

DDOS_GUARD_PAGE = """<!doctype html><html><head><title>DDoS-Guard</title></head>
<body><div id="ddos-guard">Checking your browser</div></body></html>"""


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
    """Serve `pages` in order, recording the URL each call was made against."""
    from shelfmark.release_sources.direct_download import source as dd

    calls: list[str] = []

    def fake_get(url, **_kwargs):
        calls.append(url)
        return pages[len(calls) - 1] if len(calls) <= len(pages) else ""

    monkeypatch.setattr(aa.downloader, "html_get_page", fake_get)
    monkeypatch.setattr(aa.network, "get_available_aa_urls", lambda: ["a", "b", "c"])
    return dd, calls


def test_parked_mirror_is_quarantined_and_search_retries_next_mirror(monkeypatch):
    _dd, calls = _patch_pages(monkeypatch, [PARKED_PAGE, AA_RESULTS_PAGE])
    selector = _Selector(["https://parked.test", "https://real.test"])

    html, table = aa._fetch_search_table("https://parked.test/search?q=dune", selector)

    assert selector.quarantined == ["https://parked.test"]
    assert isinstance(table, Tag)
    assert "Dune" in html
    # The retry went to the live mirror, not back to the parked one.
    assert calls[1].startswith("https://real.test")


def test_genuinely_empty_aa_result_does_not_quarantine(monkeypatch):
    """'No files found.' is a real answer from a healthy mirror."""
    _dd, _calls = _patch_pages(monkeypatch, [AA_EMPTY_PAGE])
    selector = _Selector(["https://real.test", "https://other.test"])

    html, table = aa._fetch_search_table("https://real.test/search?q=zzz", selector)

    assert selector.quarantined == []
    assert table is None
    assert "No files found." in html


def test_challenge_page_is_reported_not_passed_off_as_an_empty_result(monkeypatch):
    """An unsolved interstitial means the search never ran.

    The mirror is alive and holds our clearance, so it must not be quarantined - but
    returning it as "no table" made the caller tell the user their query found nothing.
    """
    _dd, _calls = _patch_pages(monkeypatch, [DDOS_GUARD_PAGE])
    selector = _Selector(["https://real.test", "https://other.test"])

    with pytest.raises(aa.SearchUnavailableError, match="protection challenge"):
        aa._fetch_search_table("https://real.test/search?q=dune", selector)

    assert selector.quarantined == []


def test_unreachable_mirror_raises_search_unavailable(monkeypatch):
    _dd, _calls = _patch_pages(monkeypatch, [""])
    selector = _Selector(["https://real.test"])

    try:
        aa._fetch_search_table("https://real.test/search?q=dune", selector)
    except aa.SearchUnavailableError:
        return
    raise AssertionError("expected SearchUnavailableError")


def test_recorded_failure_reason_is_surfaced_to_the_caller(monkeypatch):
    """The concrete give-up reason html_get_page stashed replaces the generic line."""

    reason = "Anna's Archive returned 403 (blocked) and no bypasser is enabled."

    def fake_get(url, *, selector=None, **_kwargs):
        selector.last_failure = reason
        return ""

    monkeypatch.setattr(aa.downloader, "html_get_page", fake_get)
    monkeypatch.setattr(aa.network, "get_available_aa_urls", lambda: ["a"])
    selector = _Selector(["https://real.test"])
    selector.last_failure = None

    with pytest.raises(aa.SearchUnavailableError, match="403 .blocked."):
        aa._fetch_search_table("https://real.test/search?q=dune", selector)
