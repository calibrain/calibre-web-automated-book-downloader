"""The untabled-page diagnostic must name the mirror that actually answered.

Regression for #1298. `html_get_page` rotates mirrors and follows redirects internally,
so the URL the search layer passed in is only where the attempt started. Logging that
one made the debug bundle report the untabled page against annas-archive.gl when the
body had come from .pk - the exact triage cost the #1289 diagnostics were added to
remove, reintroduced by reading the wrong variable.
"""

import logging

import pytest

from shelfmark.release_sources.direct_download import annas_archive as aa

# A protection challenge, so the fingerprint line fires without looking like AA.
CHALLENGE_PAGE = (
    "<html><head><title>DDOS-GUARD</title>"
    '<link rel="stylesheet" href="/.well-known/ddos-guard/ddg-captcha-page/index.css">'
    "</head><body>Complete the manual check to continue</body></html>"
)


@pytest.fixture
def search_logs():
    """Collect this module's log messages.

    setup_logger builds loggers outside the standard hierarchy, so their records never
    reach the root handler caplog installs - see tests/bypass/test_ddg_cookie_reuse.py.
    """

    messages: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            messages.append(record.getMessage())

    handler = _Capture()
    aa.logger.addHandler(handler)
    previous = aa.logger.level
    aa.logger.setLevel(logging.DEBUG)
    aa.logger._cache.clear()
    try:
        yield messages
    finally:
        aa.logger.removeHandler(handler)
        aa.logger.setLevel(previous)


class _Selector:
    current_base = "https://annas-archive.gl"
    last_failure = None

    def rewrite(self, url: str) -> str:
        return url

    def next_mirror_or_rotate_dns(self, *, fatal: bool = False, reason: str = ""):
        return None, "exhausted"


REQUESTED = "https://annas-archive.gl/search?q=Ken+follett"
ANSWERED = "https://annas-archive.pk/search?q=Ken+follett"


def test_the_fingerprint_names_the_mirror_that_answered(monkeypatch, search_logs):

    def fake_get(url, **kwargs):
        # The caller must ask for it, or there is nothing to report.
        assert kwargs["include_response_url"] is True
        assert url == REQUESTED
        # What an internal rotation looks like from the outside: a different host.
        return CHALLENGE_PAGE, ANSWERED

    monkeypatch.setattr(aa.downloader, "html_get_page", fake_get)
    monkeypatch.setattr(aa.network, "get_available_aa_urls", lambda: ["a"])

    with pytest.raises(aa.SearchUnavailableError):
        aa._fetch_search_table_uncached(REQUESTED, _Selector())

    fingerprint = [m for m in search_logs if m.startswith("Search page has no results table")]
    assert len(fingerprint) == 1
    assert ANSWERED in fingerprint[0]
    assert "annas-archive.gl" not in fingerprint[0]


def test_a_downloader_that_reports_no_url_falls_back_to_the_request(monkeypatch, search_logs):
    """The plain-string shape stays supported; the line is still worth having."""

    monkeypatch.setattr(aa.downloader, "html_get_page", lambda _url, **_k: CHALLENGE_PAGE)
    monkeypatch.setattr(aa.network, "get_available_aa_urls", lambda: ["a"])

    with pytest.raises(aa.SearchUnavailableError):
        aa._fetch_search_table_uncached(REQUESTED, _Selector())

    fingerprint = [m for m in search_logs if m.startswith("Search page has no results table")]
    assert len(fingerprint) == 1
    assert REQUESTED in fingerprint[0]


def test_the_empty_body_give_up_survives_the_tuple_shape(monkeypatch):
    """`("", url)` is truthy, so the exhaustion check has to read the body."""

    monkeypatch.setattr(aa.downloader, "html_get_page", lambda _url, **_k: ("", REQUESTED))
    monkeypatch.setattr(aa.network, "get_available_aa_urls", lambda: ["a"])

    selector = _Selector()
    selector.last_failure = "Every mirror refused the connection."

    with pytest.raises(aa.SearchUnavailableError) as excinfo:
        aa._fetch_search_table_uncached(REQUESTED, selector)

    assert "Every mirror refused the connection." in str(excinfo.value)
