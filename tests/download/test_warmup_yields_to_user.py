"""The warm-up must not queue its throwaway solve in front of the user's first search.

Issue #1276: the warm-up fires 15s after boot, and the bundle showed a user clicking a
book 13 seconds in. Three seconds later the warm-up started anyway, and because the
bypasser serializes on one browser, the user's title+author search sat behind a solve for
"The Great Gatsby" from 13:41:26 to 13:42:26 - a full minute of a 2m27s wait, for a query
nobody asked for. Once the user has beaten the warm-up to it there is nothing left to
pre-solve.
"""

import shelfmark.download.warmup as warmup

# The flag is cleared around every test by an autouse fixture in conftest.py - it is
# process-global, and /api/releases sets it, so tests that read it cannot rely on
# whatever else shared their xdist worker.


def test_warmup_runs_when_nobody_has_searched(monkeypatch):
    monkeypatch.setattr(
        "shelfmark.core.mirrors.has_aa_mirror_configuration", lambda: True, raising=False
    )
    searched: list[str] = []
    monkeypatch.setattr(
        "shelfmark.release_sources.direct_download.search_books",
        lambda query, _filters: (searched.append(query), ["a result"], None, 1)[1:],
    )

    assert warmup.run_warmup() is True
    assert searched == [warmup.warmup_query()]


def test_warmup_stands_down_once_a_real_search_has_started(monkeypatch):
    searched: list[str] = []
    monkeypatch.setattr(
        "shelfmark.release_sources.direct_download.search_books",
        lambda query, _filters: searched.append(query) or [],
    )

    warmup.note_user_search()

    assert warmup.run_warmup() is False
    assert searched == [], "the warm-up must not compete for the bypasser"


def test_the_check_happens_at_fire_time_not_schedule_time(monkeypatch):
    """The start-up delay is exactly what this races with, so a search that lands during
    the wait has to count - checking only in start() would miss every real case."""
    monkeypatch.setattr(
        "shelfmark.core.mirrors.has_aa_mirror_configuration", lambda: True, raising=False
    )
    searched: list[str] = []
    monkeypatch.setattr(
        "shelfmark.release_sources.direct_download.search_books",
        lambda query, _filters: searched.append(query) or [],
    )

    # Scheduling succeeds: at this point nothing has searched.
    monkeypatch.setattr(warmup, "_setting", lambda _key, _default: True)
    assert warmup.is_enabled() is True

    # The user clicks while the timer is still pending.
    warmup.note_user_search()

    assert warmup.run_warmup() is False
    assert searched == []
