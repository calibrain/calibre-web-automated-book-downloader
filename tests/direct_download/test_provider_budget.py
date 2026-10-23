"""A stuck first provider must not starve the second (issue #1531).

All providers share one search budget. Anna's Archive runs first, and its bypass retries
could spend the whole of it, so OceanofPDF saw an expired budget and never searched.
"""

import pytest

from shelfmark.core import search_deadline
from shelfmark.metadata_providers import BookMetadata
from shelfmark.release_sources.direct_download import registry
from shelfmark.release_sources.direct_download.source import DirectDownloadSource


class _Provider:
    def __init__(self, name, behaviour):
        self.name = name
        self.display_name = name
        self._behaviour = behaviour
        self.expired_on_entry = None

    def is_enabled(self):
        return True

    def search(self, book, plan, *, expand_search=False, content_type="ebook"):
        self.expired_on_entry = search_deadline.expired()
        return self._behaviour(self)


def _stuck(_provider):
    """Wait on the cancel flag the way a bypass does, then fail like Anna's Archive."""
    event = search_deadline.cancel_event()
    assert event is not None
    event.wait(timeout=5)
    raise RuntimeError("bypass failed")


def _search(providers, monkeypatch):
    monkeypatch.setattr(registry, "enabled_providers", lambda p=None: tuple(p))
    monkeypatch.setattr(registry, "get_unavailable_reason", lambda p=None: None)
    monkeypatch.setattr(
        "shelfmark.release_sources.direct_download.source._browse_record_to_release",
        lambda record: record,
    )
    source = DirectDownloadSource.__new__(DirectDownloadSource)
    source._providers = providers
    book = BookMetadata(provider="x", provider_id="1", title="T", authors=["A"])
    return source.search(book, None)  # pyright: ignore[reportArgumentType]


def test_a_stuck_first_provider_leaves_the_second_a_chance(monkeypatch):
    monkeypatch.setattr(search_deadline, "DEFAULT_UNIT_RESERVE_SECONDS", 0.3)
    original = search_deadline.unit_deadline

    def short_reserve(waiting=0, *, reserve=0.5):
        return original(waiting, reserve=reserve)

    monkeypatch.setattr(search_deadline, "unit_deadline", short_reserve)
    first = _Provider("aa", _stuck)
    second = _Provider("oceanofpdf", lambda _p: ["found"])
    with search_deadline.search_deadline(1.5):
        releases = _search([first, second], monkeypatch)
    assert second.expired_on_entry is False
    assert releases == ["found"]


def test_a_single_provider_still_gets_the_whole_budget(monkeypatch):
    seen = {}

    def record(_p):
        seen["budget"] = search_deadline.current().budget_seconds
        return ["found"]

    only = _Provider("aa", record)
    with search_deadline.search_deadline(300) as parent:
        _search([only], monkeypatch)
        assert seen["budget"] == pytest.approx(parent.budget_seconds, abs=1)
