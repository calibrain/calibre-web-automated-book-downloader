"""A spent search budget stops the search rather than starting the next attempt.

One release search fans out: a mirror loop inside `_fetch_search_table`, then a title
variant per grouped variant, then the whole set again without the language filter. Each
of those can reach the bypasser, and `except Exception` around the variant loops was
built to keep going past a parse failure. Applied to a spent budget that meant the
variants queued up behind each other long after anyone was still waiting - which is how
a challenge failure became a gateway timeout (issue #1276).
"""

import pytest

from shelfmark.core import search_deadline
from shelfmark.release_sources.direct_download import annas_archive as aa
from shelfmark.release_sources.direct_download import source as dd


@pytest.fixture(autouse=True)
def _no_ambient_deadline(monkeypatch):
    token = search_deadline._current.set(None)
    monkeypatch.setattr(dd.registry, "get_unavailable_reason", lambda _providers: None)
    monkeypatch.setattr(dd.registry, "enabled_providers", lambda providers: (providers[0],))
    yield
    search_deadline._current.reset(token)


class _Selector:
    current_base = "https://annas-archive.gl"
    last_failure = None

    def rewrite(self, url: str) -> str:
        return url

    def next_mirror_or_rotate_dns(self, *, fatal: bool = False, reason: str = ""):
        return None, "exhausted"


def test_fetch_search_table_gives_up_when_the_budget_is_spent(monkeypatch):
    """Every mirror shares the protection, so another mirror is another wasted solve."""
    fetches: list[str] = []
    monkeypatch.setattr(aa.downloader, "html_get_page", lambda url, **_k: fetches.append(url) or "")
    monkeypatch.setattr(aa.network, "get_available_aa_urls", lambda: ["https://annas-archive.gl"])

    with search_deadline.search_deadline(60) as deadline:
        deadline.event.set()
        with pytest.raises(aa.SearchUnavailableError) as excinfo:
            aa._fetch_search_table("https://annas-archive.gl/search?q=dune", _Selector())

    assert fetches == [], "no fetch should have been attempted"
    assert "ran out of time" in str(excinfo.value)


def test_fetch_search_table_runs_normally_within_budget(monkeypatch):
    page = "<html><body><main><table><tbody></tbody></table></main></body></html>"
    monkeypatch.setattr(aa.downloader, "html_get_page", lambda _url, **_k: page)
    monkeypatch.setattr(aa.network, "get_available_aa_urls", lambda: ["https://annas-archive.gl"])

    with search_deadline.search_deadline(60):
        html, table = aa._fetch_search_table("https://annas-archive.gl/search?q=dune", _Selector())

    assert table is not None
    assert html == page


def _plan(titles):
    """A search plan with one grouped title variant per title."""
    from shelfmark.core.search_plan import ReleaseSearchPlan, ReleaseSearchVariant

    variants = [ReleaseSearchVariant(t, "Frank Herbert", ["en"]) for t in titles]
    return ReleaseSearchPlan(
        languages=["en"],
        isbn_candidates=[],
        author="Frank Herbert",
        title_variants=variants,
        grouped_title_variants=variants,
    )


def _book():
    from shelfmark.metadata_providers import BookMetadata

    return BookMetadata(
        provider="manual",
        provider_id="x",
        provider_display_name="Manual",
        title="Dune",
        search_title="Dune",
        authors=["Frank Herbert"],
    )


def test_title_variants_stop_once_the_budget_is_spent(monkeypatch):
    """`except Exception` keeps this loop going past a failure; a spent budget must not."""
    queries: list[str] = []

    def fake_search_books(query, _filters):
        queries.append(query)
        # The first variant is what spends the budget.
        deadline = search_deadline.current()
        if deadline is not None:
            deadline.event.set()
        return []

    monkeypatch.setattr(aa, "search_books", fake_search_books)
    monkeypatch.setattr(aa, "ensure_available", lambda: None)

    source = dd.DirectDownloadSource()
    with search_deadline.search_deadline(60):
        releases = source.search(_book(), _plan(["Dune", "Duna", "Dünen"]))

    assert releases == []
    assert len(queries) == 1, f"only the first variant should have run, got {queries}"


def test_all_title_variants_run_within_budget(monkeypatch):
    queries: list[str] = []
    monkeypatch.setattr(aa, "search_books", lambda q, _f: queries.append(q) or [])
    monkeypatch.setattr(aa, "ensure_available", lambda: None)

    source = dd.DirectDownloadSource()
    with search_deadline.search_deadline(60):
        source.search(_book(), _plan(["Dune", "Duna"]))

    # Two variants with a language filter, then both again without one.
    assert len(queries) == 4


def test_language_filter_retry_is_skipped_on_a_spent_budget(monkeypatch):
    """The no-language sweep doubles the work; it must not start after the deadline."""
    queries: list[str] = []

    def fake_search_books(query, _filters):
        queries.append(query)
        if len(queries) == 2:
            deadline = search_deadline.current()
            if deadline is not None:
                deadline.event.set()
        return []

    monkeypatch.setattr(aa, "search_books", fake_search_books)
    monkeypatch.setattr(aa, "ensure_available", lambda: None)

    source = dd.DirectDownloadSource()
    with search_deadline.search_deadline(60):
        source.search(_book(), _plan(["Dune", "Duna"]))

    assert len(queries) == 2, f"the retry sweep should not have started, got {queries}"
