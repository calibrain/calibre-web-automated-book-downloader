"""GET /api/releases answers within a budget instead of outliving the caller.

Issue #1276: the endpoint is synchronous and had no deadline, while the bypass path it
reaches was allowed ~840s per URL. A protection challenge nobody could solve therefore
ran until the reverse proxy in front of Shelfmark gave up, and the user was shown
"Server unavailable (504). If using a reverse proxy, check its configuration." - which
names the wrong thing entirely.
"""

from __future__ import annotations

import importlib
from unittest.mock import patch

import pytest

from shelfmark.core import search_deadline


@pytest.fixture(scope="module")
def main_module():
    with patch("shelfmark.download.orchestrator.start"):
        import shelfmark.main as main

        importlib.reload(main)
        return main


@pytest.fixture
def client(main_module):
    return main_module.app.test_client()


def _authenticate(client) -> None:
    with client.session_transaction() as sess:
        sess["user_id"] = "alice"
        sess["is_admin"] = False
        sess["db_user_id"] = 7


def _request(client, main_module, sources, search_impl):
    """Drive /api/releases with a stubbed source list and search implementation."""

    class _Source:
        def search(self, book, plan, *, expand_search=False, content_type="ebook"):
            return search_impl(book, plan)

        def get_column_config(self):
            from shelfmark.release_sources import _default_column_config

            return _default_column_config()

    with (
        patch.object(main_module, "get_auth_mode", return_value="none"),
        patch("shelfmark.release_sources.list_available_sources", return_value=sources),
        patch("shelfmark.release_sources.get_source", return_value=_Source()),
        patch("shelfmark.release_sources.source_results_are_releases", return_value=False),
    ):
        return client.get(
            "/api/releases",
            query_string={"provider": "manual", "book_id": "abc", "title": "Dune"},
        )


def test_a_search_runs_under_a_budget(client, main_module):
    """The handler must put a deadline in force for whatever the sources do."""
    _authenticate(client)
    observed: list[float | None] = []

    def _search(_book, _plan):
        deadline = search_deadline.current()
        observed.append(deadline.budget_seconds if deadline else None)
        return []

    resp = _request(client, main_module, [{"name": "direct_download", "enabled": True}], _search)

    assert resp.status_code == 200
    assert observed and observed[0] is not None, "no budget was in force during the search"


def _run_the_clock_out() -> None:
    """Stand in for a source that waits on the cancel flag until the whole budget is gone."""
    event = search_deadline.cancel_event()
    assert event is not None
    event.wait(timeout=5)


@pytest.fixture
def short_budget(monkeypatch):
    monkeypatch.setattr(search_deadline, "budget_seconds", lambda: 0.3)


def test_the_budget_is_shared_across_sources(client, main_module, short_budget):
    """A stuck first source must not spend the whole request on its own."""
    _authenticate(client)
    searched: list[str] = []

    def _search(book, _plan):
        searched.append(book.title)
        # Whatever the first source did, it ran the clock out.
        _run_the_clock_out()
        return []

    sources = [
        {"name": "direct_download", "enabled": True},
        {"name": "prowlarr", "enabled": True},
    ]
    resp = _request(client, main_module, sources, _search)

    assert len(searched) == 1, "the second source should not have been started"
    assert "ran out of time" in resp.get_json()["error"]


def test_the_failure_carries_a_message_the_frontend_will_show(client, main_module, short_budget):
    """The whole point of the budget.

    Shelfmark answers 503 when a search comes back empty with errors, and the frontend
    only substitutes its "Server unavailable ... check your reverse proxy" text when the
    body carries no message of its own. So the budget has to produce a body that names
    the protection challenge - and has to trip before the proxy's own timeout, where
    there would be no body at all.
    """
    _authenticate(client)

    def _search(_book, _plan):
        _run_the_clock_out()
        return []

    sources = [
        {"name": "direct_download", "enabled": True},
        {"name": "prowlarr", "enabled": True},
    ]
    resp = _request(client, main_module, sources, _search)

    message = resp.get_json()["error"]
    assert "protection challenge" in message
    assert "reverse proxy" not in message
    # The source prefix is stripped by the handler; the sentence must survive intact.
    assert message.startswith("The release search ran out of time")


def test_no_budget_leaks_out_of_the_request(client, main_module):
    """A queued download later on must not inherit a search's deadline."""
    _authenticate(client)
    _request(client, main_module, [{"name": "direct_download", "enabled": True}], lambda *_: [])

    assert search_deadline.current() is None


def test_the_client_is_told_what_the_budget_is(client, main_module):
    """The browser has to outlast the server, or the message above never arrives.

    The frontend puts its own AbortController on the direct_download search. That abort
    was a fixed 180s while this budget defaults to 300s, so the client always gave up
    first and replaced the sentence tested above with a generic network/proxy error -
    and raising RELEASE_SEARCH_TIMEOUT changed nothing a user could see, because the
    hard-coded 180s was in the hashed bundle inside the image. Reporting the budget lets
    the client set its backstop behind it. See issue #1285.
    """
    _authenticate(client)

    with patch.object(main_module, "get_auth_mode", return_value="none"):
        resp = client.get("/api/config")

    assert resp.status_code == 200
    reported = resp.get_json()["release_search_timeout"]
    assert reported == search_deadline.budget_seconds()
    assert reported > 0


def test_the_reported_budget_is_the_one_actually_enforced(client, main_module):
    """An out-of-range setting is clamped, so the raw config value would mislead."""
    _authenticate(client)

    with (
        patch.object(main_module, "get_auth_mode", return_value="none"),
        patch.object(
            main_module.app_config,
            "get",
            side_effect=lambda key, default=None, **_kw: (
                99999 if key == "RELEASE_SEARCH_TIMEOUT" else default
            ),
        ),
    ):
        resp = client.get("/api/config")
        reported = resp.get_json()["release_search_timeout"]

    assert reported == search_deadline._MAX_SEARCH_BUDGET_SECONDS
