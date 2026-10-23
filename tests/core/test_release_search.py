"""Tests for the per-source release search helper extracted from ``/api/releases``."""

from __future__ import annotations

from typing import Any

import pytest

from shelfmark.core import release_search
from shelfmark.metadata_providers import BookMetadata
from shelfmark.release_sources import SourceUnavailableError


def _book(**overrides: Any) -> BookMetadata:
    fields: dict[str, Any] = {
        "provider": "manual",
        "provider_id": "1",
        "title": "Dungeon Crawler Carl",
        "authors": ["Matt Dinniman"],
        "isbn_13": "9780593820247",
    }
    fields.update(overrides)
    return BookMetadata(**fields)


class _Source:
    def __init__(self, result: list[str] | None = None, error: Exception | None = None) -> None:
        self.result = result if result is not None else []
        self.error = error
        self.calls: list[tuple[Any, Any, bool, str]] = []

    def search(
        self, book: BookMetadata, plan: Any, *, expand_search: bool, content_type: str
    ) -> list[str]:
        self.calls.append((book, plan, expand_search, content_type))
        if self.error is not None:
            raise self.error
        return list(self.result)


def test_unknown_source_returns_error_tuple(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(name: str) -> Any:
        msg = f"Unknown source: {name}"
        raise ValueError(msg)

    monkeypatch.setattr("shelfmark.release_sources.get_source", _raise)

    source, releases, error = release_search.search_source_releases("nope", _book())

    assert source is None
    assert releases == []
    assert error == "Unknown source: nope"


@pytest.mark.parametrize(
    "exc",
    [SourceUnavailableError("down"), OSError("boom"), RuntimeError("bad"), ValueError("odd")],
    ids=["unavailable", "oserror", "runtime", "valueerror"],
)
def test_source_failures_become_messages(monkeypatch: pytest.MonkeyPatch, exc: Exception) -> None:
    src = _Source(error=exc)
    monkeypatch.setattr("shelfmark.release_sources.get_source", lambda _name: src)

    source, releases, error = release_search.search_source_releases("s", _book())

    assert source is None
    assert releases == []
    assert error == f"s: {exc!s}"


def test_success_returns_source_and_forwards_search_kwargs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    src = _Source(result=["r1", "r2"])
    monkeypatch.setattr("shelfmark.release_sources.get_source", lambda _name: src)

    source, releases, error = release_search.search_source_releases(
        "s", _book(), expand_search=True, content_type="audiobook"
    )

    assert source is src
    assert releases == ["r1", "r2"]
    assert error is None
    book, plan, expand_search, content_type = src.calls[0]
    assert book.title == "Dungeon Crawler Carl"
    assert "dungeon crawler carl" in plan.primary_query.lower()
    assert plan.isbn_candidates == ["9780593820247"]
    assert expand_search is True
    assert content_type == "audiobook"


def test_plan_receives_filters_and_user_id(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    real_build = release_search.build_release_search_plan

    def _build(book: BookMetadata, **kwargs: Any) -> Any:
        captured.update(kwargs)
        return real_build(book, **kwargs)

    monkeypatch.setattr(release_search, "build_release_search_plan", _build)
    src = _Source()
    monkeypatch.setattr("shelfmark.release_sources.get_source", lambda _name: src)

    release_search.search_source_releases(
        "s",
        _book(),
        languages=["en"],
        manual_query="dcc",
        indexers=["idx"],
        user_id=42,
    )

    assert captured == {
        "languages": ["en"],
        "manual_query": "dcc",
        "indexers": ["idx"],
        "source_filters": None,
        "user_id": 42,
    }


def test_search_book_releases_preserves_source_order_and_aggregates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sources = {"a": _Source(result=["a1"]), "b": _Source(result=["b1", "b2"])}
    monkeypatch.setattr("shelfmark.release_sources.get_source", lambda name: sources[name])

    all_releases, by_source, errors = release_search.search_book_releases(
        _book(), sources=["b", "a"], content_type="audiobook"
    )

    assert list(by_source) == ["b", "a"]
    assert by_source == {"b": ["b1", "b2"], "a": ["a1"]}
    assert all_releases == ["b1", "b2", "a1"]
    assert errors == []
    _book_arg, _plan, expand_search, content_type = sources["a"].calls[0]
    assert expand_search is True
    assert content_type == "audiobook"


def test_search_book_releases_collects_errors_and_keeps_healthy_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sources = {
        "down": _Source(error=SourceUnavailableError("offline")),
        "ok": _Source(result=["r1"]),
    }

    def _get(name: str) -> Any:
        if name not in sources:
            msg = f"Unknown release source: {name}"
            raise ValueError(msg)
        return sources[name]

    monkeypatch.setattr("shelfmark.release_sources.get_source", _get)

    all_releases, by_source, errors = release_search.search_book_releases(
        _book(), sources=["down", "ok", "nope"]
    )

    assert all_releases == ["r1"]
    assert by_source == {"ok": ["r1"]}
    assert errors == ["down: offline", "Unknown source: nope"]


def test_search_book_releases_with_no_sources_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _unexpected(_name: str) -> Any:
        raise AssertionError("no source should be resolved")

    monkeypatch.setattr("shelfmark.release_sources.get_source", _unexpected)

    assert release_search.search_book_releases(_book(), sources=[]) == ([], {}, [])
