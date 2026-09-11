"""What the IRC source posts to the channel, and how it orders the answer.

A search bot ANDs every term against a filename, so a given name the channel
spells differently returns nothing at all. Measured against irchighway's
#ebooks: "Revelations David Petrie" was answered "no results", while
"Revelations Petrie" returned 9 matches.
"""

import pytest

from shelfmark.core.author_match import search_surname
from shelfmark.core.search_plan import build_release_search_plan
from shelfmark.metadata_providers import BookMetadata
from shelfmark.release_sources import Release
from shelfmark.release_sources.irc.source import IRCReleaseSource


def _release(author, server="OnlineBot"):
    return Release(
        source="irc",
        source_id=f"line-{author}-{server}",
        title="Revelations",
        extra={"author": author, "server": server},
    )


class TestSearchSurname:
    @pytest.mark.parametrize(
        ("author", "expected"),
        [
            # The bug: the channel abbreviates the given name, so only the surname
            # is common to both spellings.
            ("David Petrie", "Petrie"),
            ("D. Petrie", "Petrie"),
            ("Kurt Vonnegut Jr.", "Vonnegut"),
            ("Homer", "Homer"),
            ("", ""),
        ],
    )
    def test_trims_to_the_token_both_spellings_share(self, author, expected):
        assert search_surname(author) == expected

    def test_a_particle_is_dropped_with_the_given_names(self):
        # "Le Guin" loses its particle, which costs precision, not matches: the
        # remaining token is still a substring of the filename that the full name
        # would have matched, and _rank_by_author restores the precision.
        assert search_surname("Ursula K. Le Guin") == "Guin"


class TestQueryPostedToChannel:
    def test_query_carries_the_surname_not_the_given_name(self):
        # Through the real plan builder, so the query cannot drift from what
        # build_release_search_plan actually produces.
        book = BookMetadata(
            provider="hardcover", provider_id="1", title="Revelations", authors=["David Petrie"]
        )

        query = IRCReleaseSource()._build_query(book, build_release_search_plan(book))

        assert query == "Revelations Petrie"

    def test_manual_query_is_posted_verbatim(self):
        book = BookMetadata(
            provider="hardcover", provider_id="1", title="Revelations", authors=["David Petrie"]
        )
        plan = build_release_search_plan(book, manual_query="revelations petrie epub")

        query = IRCReleaseSource()._build_query(book, plan)

        assert query == "revelations petrie epub"

    def test_an_isbn_fallback_query_stays_authorless(self):
        # The ISBN variant carries author="" on purpose; appending a surname would
        # make an already-narrow query require the author in the filename too.
        book = BookMetadata(
            provider="hardcover",
            provider_id="1",
            title="",
            isbn_13="9780306406157",
            authors=["David Petrie"],
        )

        query = IRCReleaseSource()._build_query(book, build_release_search_plan(book))

        assert query == "9780306406157"

    def test_title_alone_when_no_author_is_known(self):
        book = BookMetadata(provider="hardcover", provider_id="1", title="Beowulf")

        assert IRCReleaseSource()._build_query(book, build_release_search_plan(book)) == "Beowulf"


class TestAuthorOrdersTheAnswer:
    def test_requested_author_leads_and_the_rest_stay_visible(self):
        source = IRCReleaseSource()
        source._online_servers = {"OnlineBot"}
        releases = [
            _release("Gordon Petrie"),
            # What the parser writes when a filename has no " - " separator; it
            # must not sort below a result that named a different author.
            _release("Unknown"),
            _release("D Petrie"),
        ]

        ranked = source._rank_by_author(releases, "David Petrie")

        assert [release.extra["author"] for release in ranked] == [
            "D Petrie",
            "Unknown",
            "Gordon Petrie",
        ]

    def test_an_offline_server_does_not_outrank_one_that_can_answer(self):
        # Downloading addresses one named bot and waits 120s for it, so a matching
        # author on a bot that left the channel is worse than a mismatch that is there.
        source = IRCReleaseSource()
        source._online_servers = {"OnlineBot"}
        releases = [
            _release("Gordon Petrie", server="OnlineBot"),
            _release("D Petrie", server="DeadBot"),
        ]

        ranked = source._rank_by_author(releases, "David Petrie")

        assert [release.extra["server"] for release in ranked] == ["OnlineBot", "DeadBot"]

    def test_no_requested_author_leaves_the_order_the_bot_sent(self):
        source = IRCReleaseSource()
        releases = [_release("Gordon Petrie"), _release("D Petrie")]

        assert source._rank_by_author(releases, "") == releases


def test_search_posts_the_surname_and_ranks_the_cached_answer(monkeypatch):
    """End to end: the line reaching the channel, and ordering on the cached path.

    The cached path matters because one cache entry is keyed by query alone, so it
    is shared by every book that produced that query - the order has to follow the
    author being asked for now, not the one that filled the cache.
    """
    import shelfmark.release_sources.irc.source as irc_source

    source = IRCReleaseSource()
    sent: list[str] = []

    class FakeClient:
        online_servers: set[str] = {"OnlineBot"}

        def send_message(self, _channel: str, message: str) -> None:
            sent.append(message)

        def wait_for_dcc(self, **_kwargs: object) -> None:
            return None

    monkeypatch.setattr(
        irc_source,
        "_config_text",
        lambda key: {
            "IRC_SERVER": "irc.example.net",
            "IRC_CHANNEL": "ebooks",
            "IRC_NICK": "tester",
            "IRC_SEARCH_BOT": "search",
        }.get(key, ""),
    )
    irc_source._recent_message_sends.clear()
    monkeypatch.setattr(source, "is_available", lambda: True)
    monkeypatch.setattr(irc_source, "_enforce_rate_limit", lambda: None)
    monkeypatch.setattr(irc_source, "_emit_status", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "shelfmark.release_sources.irc.cache.cache_results",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "shelfmark.release_sources.irc.connection_manager.connection_manager.get_connection",
        lambda **_kwargs: FakeClient(),
    )
    monkeypatch.setattr(
        "shelfmark.release_sources.irc.connection_manager.connection_manager.release_connection",
        lambda _client: None,
    )
    monkeypatch.setattr(
        "shelfmark.release_sources.irc.cache.get_cached_results",
        lambda *_args, **_kwargs: None,
    )

    book = BookMetadata(
        provider="hardcover", provider_id="1", title="Revelations", authors=["David Petrie"]
    )
    source.search(book, build_release_search_plan(book), expand_search=True)

    assert sent == ["@search Revelations Petrie"]

    # Same query, now served from cache for the same author: the match leads.
    monkeypatch.setattr(
        "shelfmark.release_sources.irc.cache.get_cached_results",
        lambda *_args, **_kwargs: {
            "releases": [_release("Gordon Petrie"), _release("D Petrie")],
            "online_servers": ["OnlineBot"],
        },
    )

    cached = source.search(book, build_release_search_plan(book))

    assert [release.extra["author"] for release in cached] == ["D Petrie", "Gordon Petrie"]
