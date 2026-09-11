import time
from types import SimpleNamespace

from shelfmark.metadata_providers import BookMetadata
from shelfmark.release_sources import Release
from shelfmark.release_sources.irc.parser import SearchResult
from shelfmark.release_sources.irc.source import IRCReleaseSource


def _plan(title, author="", manual_query=None):
    """A plan stub for tests that only need one to reach search().

    Tests that care how a plan is built use build_release_search_plan instead.
    """
    return SimpleNamespace(
        title_variants=[SimpleNamespace(title=title, author=author)],
        author=author,
        manual_query=manual_query,
    )


def test_convert_to_releases_marks_audiobook_results_and_sorts_audio_before_archives():
    source = IRCReleaseSource()
    source._online_servers = set()

    results = [
        SearchResult(
            server="AudioBot",
            author="Author Name",
            title="Archive Release",
            format="zip",
            size="1.2GB",
            full_line="!AudioBot Author Name - Archive Release.zip ::INFO:: 1.2GB",
        ),
        SearchResult(
            server="AudioBot",
            author="Author Name",
            title="Direct Release",
            format="m4b",
            size="900MB",
            full_line="!AudioBot Author Name - Direct Release.m4b ::INFO:: 900MB",
        ),
    ]

    releases = source._convert_to_releases(results, content_type="audiobook")

    assert [release.format for release in releases] == ["m4b", "zip"]
    assert all(release.content_type == "audiobook" for release in releases)


def test_search_uses_cached_results_without_opening_a_connection(monkeypatch):
    import shelfmark.release_sources.irc.source as irc_source

    source = IRCReleaseSource()
    cached_release = Release(
        source="irc",
        source_id="cached-line",
        title="Cached Result",
    )

    monkeypatch.setattr(source, "is_available", lambda: True)
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
    monkeypatch.setattr(
        "shelfmark.release_sources.irc.cache.get_cached_results",
        lambda cache_key, *_args, **_kwargs: {
            "releases": [cached_release],
            "online_servers": ["AudioBot"],
        },
    )
    monkeypatch.setattr(
        "shelfmark.release_sources.irc.connection_manager.connection_manager.get_connection",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("cache hit should skip IRC connection")
        ),
    )
    monkeypatch.setattr(irc_source, "_emit_status", lambda *_args, **_kwargs: None)

    book = BookMetadata(provider="hardcover", provider_id="123", title="Cached Book")
    plan = _plan("Cached Book")

    releases = source.search(book, plan)

    assert releases == [cached_release]
    assert source._online_servers == {"AudioBot"}


def test_search_no_dcc_offer_releases_connection_and_caches_empty_result(monkeypatch):
    import shelfmark.release_sources.irc.source as irc_source

    source = IRCReleaseSource()
    cache_calls: list[dict[str, object]] = []
    released_clients: list[object] = []

    class FakeClient:
        online_servers = {"AudioBot"}

        def send_message(self, channel: str, message: str) -> None:
            self.channel = channel
            self.message = message

        def wait_for_dcc(
            self, *, timeout: float, result_type: bool, expected_senders: object = None
        ) -> None:
            return None

    client = FakeClient()

    # A search bot is required; make config report one so the channel send path runs.
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
    # Ensure no leftover send budget from a previous test blocks the send.
    irc_source._recent_message_sends.clear()

    monkeypatch.setattr(source, "is_available", lambda: True)
    monkeypatch.setattr(irc_source, "_enforce_rate_limit", lambda: None)
    monkeypatch.setattr(irc_source, "_emit_status", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "shelfmark.release_sources.irc.cache.get_cached_results",
        lambda cache_key, *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "shelfmark.release_sources.irc.cache.cache_results",
        lambda cache_key, title, releases, *, online_servers=None: cache_calls.append(
            {
                "cache_key": cache_key,
                "title": title,
                "releases": releases,
                "online_servers": online_servers,
            }
        ),
    )
    monkeypatch.setattr(
        "shelfmark.release_sources.irc.connection_manager.connection_manager.get_connection",
        lambda **_kwargs: client,
    )
    monkeypatch.setattr(
        "shelfmark.release_sources.irc.connection_manager.connection_manager.release_connection",
        lambda released_client: released_clients.append(released_client),
    )

    book = BookMetadata(provider="hardcover", provider_id="abc", title="Missing Result")
    plan = _plan("Missing Result")

    releases = source.search(book, plan, content_type="audiobook")

    assert releases == []
    assert released_clients == [client]
    # One query maps to one cache entry (the whole, empty answer), keyed by server:channel:query.
    assert cache_calls == [
        {
            "cache_key": "irc.example.net:ebooks:missing result",
            "title": "Missing Result",
            "releases": [],
            "online_servers": ["AudioBot"],
        }
    ]


def test_audiobook_search_routes_to_configured_audiobook_channel_and_bot(monkeypatch):
    """An audiobook request uses the audiobook channel/bot when configured."""
    import shelfmark.release_sources.irc.source as irc_source

    source = IRCReleaseSource()
    connection_kwargs: dict[str, object] = {}
    cache_calls: list[dict[str, object]] = []

    class FakeClient:
        online_servers: set[str] = set()

        def send_message(self, channel: str, message: str) -> None:
            self.channel = channel
            self.message = message

        def wait_for_dcc(
            self, *, timeout: float, result_type: bool, expected_senders: object = None
        ) -> None:
            return None

    client = FakeClient()

    monkeypatch.setattr(
        irc_source,
        "_config_text",
        lambda key: {
            "IRC_SERVER": "irc.example.net",
            "IRC_CHANNEL": "ebooks",
            "IRC_NICK": "tester",
            "IRC_SEARCH_BOT": "search",
            "IRC_AUDIOBOOK_CHANNEL": "bookz",
            "IRC_AUDIOBOOK_SEARCH_BOT": "audiosearch",
        }.get(key, ""),
    )
    irc_source._recent_message_sends.clear()

    monkeypatch.setattr(source, "is_available", lambda: True)
    monkeypatch.setattr(irc_source, "_enforce_rate_limit", lambda: None)
    monkeypatch.setattr(irc_source, "_emit_status", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "shelfmark.release_sources.irc.cache.get_cached_results",
        lambda cache_key, *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "shelfmark.release_sources.irc.cache.cache_results",
        lambda cache_key, title, releases, *, online_servers=None: cache_calls.append(
            {"cache_key": cache_key}
        ),
    )

    def fake_get_connection(**kwargs: object) -> FakeClient:
        connection_kwargs.update(kwargs)
        return client

    monkeypatch.setattr(
        "shelfmark.release_sources.irc.connection_manager.connection_manager.get_connection",
        fake_get_connection,
    )
    monkeypatch.setattr(
        "shelfmark.release_sources.irc.connection_manager.connection_manager.release_connection",
        lambda _client: None,
    )

    book = BookMetadata(provider="hardcover", provider_id="ab", title="Audio Book")
    plan = _plan("Audio Book")

    source.search(book, plan, content_type="audiobook")

    # Joined the audiobook channel, addressed the audiobook search bot, and the cache
    # key is namespaced to the audiobook channel (so it won't collide with the ebook one).
    assert connection_kwargs["channel"] == "bookz"
    assert client.channel == "#bookz"
    assert client.message == "@audiosearch Audio Book"
    assert cache_calls == [{"cache_key": "irc.example.net:bookz:audio book"}]


def test_audiobook_search_reuses_main_bot_when_only_channel_configured(monkeypatch):
    """With an audiobook channel but no audiobook bot, reuse the main search bot."""
    import shelfmark.release_sources.irc.source as irc_source

    source = IRCReleaseSource()

    class FakeClient:
        online_servers: set[str] = set()

        def send_message(self, channel: str, message: str) -> None:
            self.channel = channel
            self.message = message

        def wait_for_dcc(
            self, *, timeout: float, result_type: bool, expected_senders: object = None
        ) -> None:
            return None

    client = FakeClient()

    monkeypatch.setattr(
        irc_source,
        "_config_text",
        lambda key: {
            "IRC_SERVER": "irc.example.net",
            "IRC_CHANNEL": "ebooks",
            "IRC_NICK": "tester",
            "IRC_SEARCH_BOT": "search",
            "IRC_AUDIOBOOK_CHANNEL": "bookz",
            "IRC_AUDIOBOOK_SEARCH_BOT": "",
        }.get(key, ""),
    )
    irc_source._recent_message_sends.clear()

    monkeypatch.setattr(source, "is_available", lambda: True)
    monkeypatch.setattr(irc_source, "_enforce_rate_limit", lambda: None)
    monkeypatch.setattr(irc_source, "_emit_status", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "shelfmark.release_sources.irc.cache.get_cached_results",
        lambda cache_key, *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "shelfmark.release_sources.irc.cache.cache_results",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "shelfmark.release_sources.irc.connection_manager.connection_manager.get_connection",
        lambda **_kwargs: client,
    )
    monkeypatch.setattr(
        "shelfmark.release_sources.irc.connection_manager.connection_manager.release_connection",
        lambda _client: None,
    )

    book = BookMetadata(provider="hardcover", provider_id="ab2", title="Audio Book")
    plan = _plan("Audio Book")

    source.search(book, plan, content_type="audiobook")

    assert client.channel == "#bookz"
    assert client.message == "@search Audio Book"


def test_search_without_search_bot_never_posts_to_channel(monkeypatch):
    """A bare (unaddressed) query must never reach the channel; refuse to connect."""
    import shelfmark.release_sources.irc.source as irc_source

    source = IRCReleaseSource()

    # Force is_available True so we exercise the in-search guard (defense in depth).
    monkeypatch.setattr(source, "is_available", lambda: True)
    monkeypatch.setattr(irc_source, "_emit_status", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(irc_source, "_enforce_rate_limit", lambda: None)
    monkeypatch.setattr(
        "shelfmark.release_sources.irc.cache.get_cached_results",
        lambda cache_key, *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        irc_source,
        "_config_text",
        lambda key: {
            "IRC_SERVER": "irc.example.net",
            "IRC_CHANNEL": "ebooks",
            "IRC_NICK": "tester",
            "IRC_SEARCH_BOT": "",  # not configured
        }.get(key, ""),
    )
    monkeypatch.setattr(
        "shelfmark.release_sources.irc.connection_manager.connection_manager.get_connection",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("must not connect/post without a search bot")
        ),
    )

    book = BookMetadata(provider="hardcover", provider_id="nobot", title="No Bot")
    plan = _plan("No Bot")

    assert source.search(book, plan) == []


def test_recent_send_count_caps_and_windows():
    """The send budget counts identical queries and prunes entries outside the window."""
    import shelfmark.release_sources.irc.source as irc_source

    irc_source._recent_message_sends.clear()
    key = irc_source._query_identity("irc.example.net", "ebooks", "Dubliners")
    other = irc_source._query_identity("irc.example.net", "ebooks", "Ulysses")

    assert irc_source._recent_send_count(key) == 0
    for expected in range(1, irc_source.MAX_IDENTICAL_SENDS + 1):
        irc_source._record_message_sent(key)
        assert irc_source._recent_send_count(key) == expected

    # A different query has its own independent budget.
    assert irc_source._recent_send_count(other) == 0

    # Timestamps older than the window are pruned and don't count.
    stale = time.time() - irc_source.IDENTICAL_SEND_WINDOW_SECONDS - 10
    irc_source._recent_message_sends[key] = [stale, stale]
    assert irc_source._recent_send_count(key) == 0


def test_search_send_budget_blocks_repost_and_returns_cache(monkeypatch):
    """Once the exact query hit its per-window send limit, don't re-post; serve cache."""
    import shelfmark.release_sources.irc.source as irc_source

    source = IRCReleaseSource()
    cached_release = Release(source="irc", source_id="cached-line", title="Cached Result")

    monkeypatch.setattr(source, "is_available", lambda: True)
    monkeypatch.setattr(irc_source, "_emit_status", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(irc_source, "_enforce_rate_limit", lambda: None)
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
    monkeypatch.setattr(
        "shelfmark.release_sources.irc.cache.get_cached_results",
        lambda cache_key, *_args, **_kwargs: {
            "releases": [cached_release],
            "online_servers": ["AudioBot"],
        },
    )
    monkeypatch.setattr(
        "shelfmark.release_sources.irc.connection_manager.connection_manager.get_connection",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("send budget should skip IRC connection")
        ),
    )

    # Exhaust the budget for this exact query on this server-channel.
    irc_source._recent_message_sends.clear()
    send_key = irc_source._query_identity("irc.example.net", "ebooks", "Budget Book")
    for _ in range(irc_source.MAX_IDENTICAL_SENDS):
        irc_source._record_message_sent(send_key)

    book = BookMetadata(provider="hardcover", provider_id="cd", title="Budget Book")
    plan = _plan("Budget Book")

    # expand_search=True bypasses the top-level cache, forcing the budget path.
    releases = source.search(book, plan, expand_search=True)

    assert releases == [cached_release]
    assert source._online_servers == {"AudioBot"}
