"""
Tests for the Prowlarr source module.

Tests the utility functions for parsing release metadata.
"""

# Import the functions to test
import pytest
import requests

from shelfmark.metadata_providers import BookMetadata
from shelfmark.release_sources import SourceUnavailableError
from shelfmark.release_sources.prowlarr.api import ProwlarrClient, ProwlarrSearchError
from shelfmark.release_sources.prowlarr.source import (
    PROWLARR_SEARCH_TIMEOUT_SECONDS,
    ProwlarrSource,
    _build_indexer_priority,
    _collapse_duplicate_indexer_results,
    _detect_content_type_from_categories,
    _drop_unknown_indexer_ids,
    _extract_format,
    _extract_mam_language,
    _fetch_indexer_seed_settings,
    _last_known_seed_settings,
    _release_identity,
    _result_dedup_key,
    _search_budget_seconds,
    _unsupported_title_extension,
    parse_size,
)
from shelfmark.release_sources.prowlarr.utils import (
    build_source_id,
    get_protocol_display,
    sanitize_download_url,
)


class _AvailableSource:
    display_name = "Prowlarr"

    def is_available(self):
        return True


@pytest.fixture(autouse=True)
def source_available_by_default(monkeypatch):
    import shelfmark.download.orchestrator as orchestrator

    monkeypatch.setattr(orchestrator, "get_source", lambda _source: _AvailableSource())


class TestParseSize:
    """Tests for the parse_size function."""

    def test_parse_size_bytes(self):
        """Test parsing small byte sizes."""
        assert parse_size(100) == "100 B"
        assert parse_size(512) == "512 B"

    def test_parse_size_kilobytes(self):
        """Test parsing kilobyte sizes."""
        assert parse_size(1024) == "1.0 KB"
        assert parse_size(2048) == "2.0 KB"
        assert parse_size(1536) == "1.5 KB"

    def test_parse_size_megabytes(self):
        """Test parsing megabyte sizes."""
        assert parse_size(1048576) == "1.0 MB"
        assert parse_size(5242880) == "5.0 MB"
        assert parse_size(1572864) == "1.5 MB"

    def test_parse_size_gigabytes(self):
        """Test parsing gigabyte sizes."""
        assert parse_size(1073741824) == "1.0 GB"
        assert parse_size(2147483648) == "2.0 GB"

    def test_parse_size_terabytes(self):
        """Test parsing terabyte sizes."""
        assert parse_size(1099511627776) == "1.0 TB"

    def test_parse_size_none(self):
        """Test that None returns None."""
        assert parse_size(None) is None

    def test_parse_size_zero(self):
        """Test that zero returns None."""
        assert parse_size(0) is None

    def test_parse_size_negative(self):
        """Test that negative values return None."""
        assert parse_size(-100) is None


class TestExtractFormat:
    """Tests for the _extract_format function."""

    def test_extract_format_from_extension(self):
        """Test extracting format from file extension."""
        assert _extract_format("The Book.epub") == "epub"
        assert _extract_format("The Book.mobi") == "mobi"
        assert _extract_format("The Book.pdf") == "pdf"
        assert _extract_format("The Book.azw3") == "azw3"

    def test_extract_format_from_brackets(self):
        """Test extracting format from brackets."""
        assert _extract_format("The Book [EPUB]") == "epub"
        assert _extract_format("The Book (PDF)") == "pdf"
        assert _extract_format("The Book {MOBI}") == "mobi"

    def test_extract_format_from_word(self):
        """Test extracting format as standalone word."""
        assert _extract_format("The Book epub version") == "epub"
        assert _extract_format("mobi edition of the book") == "mobi"

    def test_extract_format_priority_extension_over_bracket(self):
        """Test that file extension takes priority over brackets."""
        # Extension is more reliable
        assert _extract_format("The Book [PDF].epub") == "epub"

    def test_extract_format_case_insensitive(self):
        """Test that format extraction is case insensitive."""
        assert _extract_format("The Book.EPUB") == "epub"
        assert _extract_format("The Book [PDF]") == "pdf"
        assert _extract_format("The Book.Mobi") == "mobi"

    def test_extract_format_none_when_no_format(self):
        """Test that None is returned when no format found."""
        assert _extract_format("The Book by Author") is None
        assert _extract_format("") is None

    def test_extract_format_cbz_cbr(self):
        """Test comic book formats."""
        assert _extract_format("Comic Issue 1.cbz") == "cbz"
        assert _extract_format("Comic Issue 2.cbr") == "cbr"

    def test_extract_format_fb2(self):
        """Test FB2 format (common in Russian ebooks)."""
        assert _extract_format("Russian Book.fb2") == "fb2"
        assert _extract_format("Book [FB2]") == "fb2"

    def test_extract_format_djvu(self):
        """Test DjVu format."""
        assert _extract_format("Scanned Book.djvu") == "djvu"

    def test_extract_format_avoids_false_positives(self):
        """Test that format extraction doesn't match partial words."""
        # "republic" should not match "pdf" or other formats
        assert _extract_format("The Republic by Plato") is None
        # "literal" should not match "lit"
        assert _extract_format("Literal Translation") is None


class TestGetProtocolDisplay:
    """Tests for the get_protocol_display function."""

    def test_get_protocol_from_protocol_field_torrent(self):
        """Test extracting torrent protocol from protocol field."""
        result = {"protocol": "torrent", "downloadUrl": "https://example.com"}
        assert get_protocol_display(result) == "torrent"

    def test_get_protocol_from_protocol_field_usenet(self):
        """Test extracting usenet protocol from protocol field."""
        result = {"protocol": "usenet", "downloadUrl": "https://example.com"}
        assert get_protocol_display(result) == "nzb"

    def test_get_protocol_from_magnet_url(self):
        """Test inferring torrent from magnet URL."""
        result = {"downloadUrl": "magnet:?xt=urn:btih:abc123"}
        assert get_protocol_display(result) == "torrent"

    def test_get_protocol_from_torrent_url(self):
        """Test inferring torrent from .torrent URL."""
        result = {"downloadUrl": "https://example.com/file.torrent"}
        assert get_protocol_display(result) == "torrent"

    def test_get_protocol_from_nzb_url(self):
        """Test inferring NZB from .nzb URL."""
        result = {"downloadUrl": "https://example.com/file.nzb"}
        assert get_protocol_display(result) == "nzb"

    def test_get_protocol_fallback_to_magnet_url(self):
        """Test fallback to magnetUrl field."""
        result = {"magnetUrl": "magnet:?xt=urn:btih:abc123"}
        assert get_protocol_display(result) == "torrent"

    def test_get_protocol_unknown(self):
        """Test unknown protocol for unclear URLs."""
        result = {"downloadUrl": "https://example.com/download"}
        assert get_protocol_display(result) == "unknown"

    def test_get_protocol_case_insensitive(self):
        """Test protocol detection is case insensitive."""
        result = {"protocol": "TORRENT"}
        assert get_protocol_display(result) == "torrent"

        result = {"protocol": "Usenet"}
        assert get_protocol_display(result) == "nzb"


class TestSanitizeDownloadUrl:
    """Tests for the sanitize_download_url helper."""

    def test_sanitizes_apikey_whitespace(self):
        """Strip whitespace around apikey separators."""
        url = "http://prowlarr:9696/5/download?apikey = 12345"
        assert sanitize_download_url(url) == "http://prowlarr:9696/5/download?apikey=12345"

    def test_sanitizes_multiple_query_params(self):
        """Sanitize all query pairs while keeping params."""
        url = "http://prowlarr:9696/5/download?apikey = 12345&indexer = 7"
        assert (
            sanitize_download_url(url) == "http://prowlarr:9696/5/download?apikey=12345&indexer=7"
        )

    def test_leaves_non_http_urls_untouched(self):
        """Do not modify magnet or other non-http URLs."""
        url = "magnet:?xt=urn:btih:abc123"
        assert sanitize_download_url(url) == url

    def test_leaves_clean_urls_untouched(self):
        """Return clean URLs as-is."""
        url = "https://prowlarr:9696/5/download?apikey=12345"
        assert sanitize_download_url(url) == url


class TestDetectContentType:
    """Tests for the _detect_content_type_from_categories function."""

    def test_fallback_without_categories(self):
        assert _detect_content_type_from_categories([], "ebook") == "book"
        assert _detect_content_type_from_categories([], "audiobook") == "audiobook"

    def test_audiobook_categories(self):
        assert _detect_content_type_from_categories([{"id": 3030}], "ebook") == "audiobook"
        assert _detect_content_type_from_categories([3000], "ebook") == "audiobook"

    def test_book_category_range(self):
        assert _detect_content_type_from_categories([{"id": 7000}], "ebook") == "book"
        assert _detect_content_type_from_categories([7020], "audiobook") == "book"
        assert _detect_content_type_from_categories([7030], "ebook") == "book"

    def test_non_book_categories_return_other(self):
        assert _detect_content_type_from_categories([{"id": 2000}], "ebook") == "other"


class FakeTorznabClient:
    def __init__(self, search_results=None, seed_settings=None):
        self.calls: list[tuple[str, object]] = []
        self.queries: list[str] = []
        self.seed_settings_calls: list[object] = []
        self.search_results = search_results or []
        self.seed_settings = seed_settings or {}
        self.indexer_timeout = 90

    def get_enabled_indexers_detailed(self, *, raise_on_error=False):
        return [
            {
                "id": 1,
                "enable": True,
                "capabilities": {
                    "categories": [
                        {"id": 7000, "subCategories": []},
                        {"id": 3030, "subCategories": []},
                    ]
                },
            }
        ]

    def torznab_search(
        self,
        *,
        indexer_id: int,
        query: str,
        categories=None,
        search_type="book",
        limit=100,
        offset=0,
    ):
        del indexer_id, search_type, limit, offset
        self.calls.append((query, categories))
        self.queries.append(query)
        return self.search_results

    def get_enriched_indexer_ids(self, restrict_to=None, indexers=None):
        del restrict_to, indexers
        return []

    def get_indexer_seed_settings(self, restrict_to=None):
        self.seed_settings_calls.append(restrict_to)
        return self.seed_settings


class TestProwlarrIndexerSeedSettings:
    def test_get_indexer_seed_settings_reads_prowlarr_minutes_field(self, monkeypatch):
        client = ProwlarrClient("http://prowlarr:9696", "apikey")
        monkeypatch.setattr(
            client,
            "get_enabled_indexers_detailed",
            lambda *, raise_on_error=False: [
                {
                    "id": 13,
                    "protocol": "torrent",
                    "fields": [
                        {"name": "torrentBaseSettings.seedRatio", "value": "2.5"},
                        {"name": "torrentBaseSettings.seedTime", "value": "7200"},
                    ],
                },
                {
                    "id": 14,
                    "protocol": "usenet",
                    "fields": [
                        {"name": "torrentBaseSettings.seedRatio", "value": "3"},
                        {"name": "torrentBaseSettings.seedTime", "value": "9999"},
                    ],
                },
            ],
        )

        assert client.get_indexer_seed_settings() == {
            13: {"ratio_limit": 2.5, "seeding_time_limit_minutes": 7200}
        }


class TestProwlarrLocalizedQueries:
    def test_manual_query_still_applies_content_type_categories(self, monkeypatch):
        import shelfmark.release_sources.prowlarr.source as prowlarr_source

        def fake_get(key: str, default=None):
            values = {
                "PROWLARR_INDEXERS": "",
                "PROWLARR_AUTO_EXPAND": False,
            }
            return values.get(key, default)

        monkeypatch.setattr(prowlarr_source.config, "get", fake_get)

        fake_client = FakeTorznabClient()
        source = ProwlarrSource()
        monkeypatch.setattr(source, "_get_client", lambda: fake_client)

        book = BookMetadata(
            provider="hardcover",
            provider_id="123",
            title="Anything",
            authors=["Someone"],
        )

        from shelfmark.core.search_plan import build_release_search_plan

        plan = build_release_search_plan(book, languages=["en"], manual_query="my custom")
        source.search(book, plan, content_type="audiobook")

        assert fake_client.calls == [("my custom", [3030])]

    def test_manual_query_expand_removes_categories(self, monkeypatch):
        import shelfmark.release_sources.prowlarr.source as prowlarr_source

        def fake_get(key: str, default=None):
            values = {
                "PROWLARR_INDEXERS": "",
                "PROWLARR_AUTO_EXPAND": False,
            }
            return values.get(key, default)

        monkeypatch.setattr(prowlarr_source.config, "get", fake_get)

        fake_client = FakeTorznabClient()
        source = ProwlarrSource()
        monkeypatch.setattr(source, "_get_client", lambda: fake_client)

        book = BookMetadata(
            provider="hardcover",
            provider_id="123",
            title="Anything",
            authors=["Someone"],
        )

        from shelfmark.core.search_plan import build_release_search_plan

        plan = build_release_search_plan(book, languages=["en"], manual_query="my custom")
        source.search(book, plan, expand_search=True, content_type="audiobook")

        assert fake_client.calls == [("my custom", None)]

    def test_search_attaches_configured_seed_time_minutes_to_release(self, monkeypatch):
        import shelfmark.release_sources.prowlarr.source as prowlarr_source

        def fake_get(key: str, default=None):
            values = {
                "PROWLARR_INDEXERS": "",
                "PROWLARR_AUTO_EXPAND": False,
                "PROWLARR_USE_SEED_PREFERENCES": True,
            }
            return values.get(key, default)

        monkeypatch.setattr(prowlarr_source.config, "get", fake_get)

        fake_client = FakeTorznabClient(
            search_results=[
                {
                    "guid": "mam-result-1",
                    "protocol": "torrent",
                    "title": "Test Release",
                    "magnetUrl": "magnet:?xt=urn:btih:abc123",
                    "indexerId": 1,
                    "indexer": "MyAnonamouse",
                    "minimumSeedTime": 259200,
                    "minimumRatio": 1,
                }
            ],
            seed_settings={1: {"ratio_limit": 2.0, "seeding_time_limit_minutes": 7200}},
        )
        source = ProwlarrSource()
        monkeypatch.setattr(source, "_get_client", lambda: fake_client)

        book = BookMetadata(
            provider="hardcover",
            provider_id="123",
            title="Anything",
            authors=["Someone"],
        )

        from shelfmark.core.search_plan import build_release_search_plan

        plan = build_release_search_plan(book, languages=["en"])
        releases = source.search(book, plan, content_type="ebook")

        assert len(releases) == 1
        assert fake_client.seed_settings_calls == [None]
        assert releases[0].extra["configured_ratio_limit"] == 2.0
        assert releases[0].extra["configured_seed_time_minutes"] == 7200
        assert "minimum_seed_time" not in releases[0].extra
        assert "minimum_ratio" not in releases[0].extra

    def test_search_ignores_configured_seed_time_when_disabled(self, monkeypatch):
        import shelfmark.release_sources.prowlarr.source as prowlarr_source

        def fake_get(key: str, default=None):
            values = {
                "PROWLARR_INDEXERS": "",
                "PROWLARR_AUTO_EXPAND": False,
                "PROWLARR_USE_SEED_PREFERENCES": False,
            }
            return values.get(key, default)

        monkeypatch.setattr(prowlarr_source.config, "get", fake_get)

        fake_client = FakeTorznabClient(
            search_results=[
                {
                    "guid": "mam-result-1",
                    "protocol": "torrent",
                    "title": "Test Release",
                    "magnetUrl": "magnet:?xt=urn:btih:abc123",
                    "indexerId": 1,
                    "indexer": "MyAnonamouse",
                }
            ],
            seed_settings={1: {"ratio_limit": 2.0, "seeding_time_limit_minutes": 7200}},
        )
        source = ProwlarrSource()
        monkeypatch.setattr(source, "_get_client", lambda: fake_client)

        book = BookMetadata(
            provider="hardcover",
            provider_id="123",
            title="Anything",
            authors=["Someone"],
        )

        from shelfmark.core.search_plan import build_release_search_plan

        plan = build_release_search_plan(book, languages=["en"])
        releases = source.search(book, plan, content_type="ebook")

        assert len(releases) == 1
        assert fake_client.seed_settings_calls == []
        assert releases[0].extra["configured_ratio_limit"] is None
        assert releases[0].extra["configured_seed_time_minutes"] is None

    def test_redacted_search_result_still_builds_private_retry_payload(self, monkeypatch):
        import shelfmark.download.orchestrator as orchestrator
        import shelfmark.release_sources.prowlarr.source as prowlarr_source

        secret_download_url = "https://prowlarr.example.com/1/download?apikey=secret"

        def fake_get(key: str, default=None, user_id=None):
            values = {
                "PROWLARR_INDEXERS": "",
                "PROWLARR_AUTO_EXPAND": False,
                "PROWLARR_USE_SEED_PREFERENCES": False,
            }
            return values.get(key, default)

        monkeypatch.setattr(prowlarr_source.config, "get", fake_get)
        monkeypatch.setattr(orchestrator.config, "get", fake_get)

        fake_client = FakeTorznabClient(
            search_results=[
                {
                    "guid": "secret-prowlarr-release",
                    "protocol": "usenet",
                    "title": "Secret Bearing Release",
                    "downloadUrl": secret_download_url,
                }
            ]
        )
        source = ProwlarrSource()
        monkeypatch.setattr(source, "_get_client", lambda: fake_client)

        book = BookMetadata(
            provider="hardcover",
            provider_id="123",
            title="Anything",
            authors=["Someone"],
        )

        from shelfmark.core.search_plan import build_release_search_plan

        plan = build_release_search_plan(book, languages=["en"])
        releases = source.search(book, plan, content_type="ebook")

        assert len(releases) == 1
        assert releases[0].download_url is None

        captured_tasks = []

        def fake_add(task):
            captured_tasks.append(task)
            return True

        monkeypatch.setattr(orchestrator.book_queue, "add", fake_add)

        success, error = orchestrator.queue_release(releases[0].__dict__)

        assert success is True
        assert error is None
        assert captured_tasks[0].retry_download_url is None
        assert captured_tasks[0].retry_download_protocol is None
        assert captured_tasks[0].retry_source_context == {
            "source_id": "secret-prowlarr-release",
            "info_url": "secret-prowlarr-release",
        }
        assert "retry_download_url" not in orchestrator._task_to_dict(captured_tasks[0])

    def test_search_uses_localized_titles_when_available(self, monkeypatch):
        import shelfmark.release_sources.prowlarr.source as prowlarr_source

        def fake_get(key: str, default=None):
            values = {
                "PROWLARR_INDEXERS": "",
                "PROWLARR_AUTO_EXPAND": False,
            }
            return values.get(key, default)

        monkeypatch.setattr(prowlarr_source.config, "get", fake_get)

        fake_client = FakeTorznabClient()
        source = ProwlarrSource()
        monkeypatch.setattr(source, "_get_client", lambda: fake_client)

        book = BookMetadata(
            provider="hardcover",
            provider_id="219252",
            title="The Lightning Thief",
            authors=["Rick Riordan"],
            titles_by_language={"hu": "A villámtolvaj"},
        )

        from shelfmark.core.search_plan import build_release_search_plan

        plan = build_release_search_plan(book, languages=["en", "hu"])
        source.search(book, plan, content_type="ebook")

        assert "The Lightning Thief" in fake_client.queries
        assert "A villámtolvaj" in fake_client.queries
        assert len(fake_client.queries) == 2

    def test_search_does_not_override_search_title_for_english(self, monkeypatch):
        import shelfmark.release_sources.prowlarr.source as prowlarr_source

        def fake_get(key: str, default=None):
            values = {
                "PROWLARR_INDEXERS": "",
                "PROWLARR_AUTO_EXPAND": False,
            }
            return values.get(key, default)

        monkeypatch.setattr(prowlarr_source.config, "get", fake_get)

        fake_client = FakeTorznabClient()
        source = ProwlarrSource()
        monkeypatch.setattr(source, "_get_client", lambda: fake_client)

        book = BookMetadata(
            provider="hardcover",
            provider_id="123",
            title="Mistborn: The Final Empire",
            search_title="The Final Empire",
            search_author="Brandon Sanderson",
            authors=["Brandon Sanderson"],
            titles_by_language={
                "en": "Mistborn: The Final Empire",
                "hu": "A végső birodalom",
            },
        )

        from shelfmark.core.search_plan import build_release_search_plan

        plan = build_release_search_plan(book, languages=["en", "hu"])
        source.search(book, plan, content_type="ebook")

        assert "The Final Empire" in fake_client.queries
        assert "A végső birodalom" in fake_client.queries
        assert "Mistborn: The Final Empire" not in fake_client.queries

    def test_auto_expand_logs_query_argument(self, monkeypatch):
        import shelfmark.release_sources.prowlarr.source as prowlarr_source

        def fake_get(key: str, default=None):
            values = {
                "PROWLARR_INDEXERS": "",
                "PROWLARR_AUTO_EXPAND": True,
            }
            return values.get(key, default)

        info_calls: list[tuple[str, tuple[object, ...]]] = []

        monkeypatch.setattr(prowlarr_source.config, "get", fake_get)
        monkeypatch.setattr(
            prowlarr_source.logger,
            "info",
            lambda message, *args: info_calls.append((str(message), args)),
        )

        fake_client = FakeTorznabClient()
        source = ProwlarrSource()
        monkeypatch.setattr(source, "_get_client", lambda: fake_client)

        book = BookMetadata(
            provider="hardcover",
            provider_id="123",
            title="Anything",
            authors=["Someone"],
        )

        from shelfmark.core.search_plan import build_release_search_plan

        plan = build_release_search_plan(book, languages=["en"])
        source.search(book, plan, content_type="ebook")

        query = fake_client.calls[0][0]
        assert fake_client.calls == [(query, [7000]), (query, None)]
        assert info_calls == [
            (
                "Prowlarr: no results for query '%s' with category filter, auto-expanding search",
                (query,),
            )
        ]

    def test_get_column_config_ignores_indexer_lookup_failure(self, monkeypatch):
        source = ProwlarrSource()

        class FailingClient:
            def get_enabled_indexers_detailed(self, *, raise_on_error=False):
                raise RuntimeError("indexers unavailable")

        monkeypatch.setattr(source, "_get_client", lambda: FailingClient())
        monkeypatch.setattr(source, "_get_selected_indexer_ids", lambda: None)

        config = source.get_column_config()

        assert config.available_indexers is None
        assert config.default_indexers is None

    def test_resolve_indexer_ids_from_names_returns_none_on_lookup_failure(self):
        source = ProwlarrSource()

        class FailingClient:
            def get_enabled_indexers_detailed(self, *, raise_on_error=False):
                raise RuntimeError("indexers unavailable")

        assert source._resolve_indexer_ids_from_names(FailingClient(), ["Alpha"]) is None

    def test_get_search_indexer_ids_returns_empty_on_lookup_failure(self):
        source = ProwlarrSource()

        class FailingClient:
            def get_enabled_indexers_detailed(self, *, raise_on_error=False):
                raise RuntimeError("indexers unavailable")

        assert source._get_search_indexer_ids(FailingClient(), None, [7000]) == []


class TestFetchIndexerSeedSettingsFallback:
    """Regression tests for #795: transient Prowlarr API failures must not
    silently strip per-indexer share limits from search results."""

    @pytest.fixture(autouse=True)
    def _clear_last_known_seed_settings(self):
        _last_known_seed_settings.clear()
        yield
        _last_known_seed_settings.clear()

    def test_success_updates_last_known_good(self):
        class Client:
            def get_indexer_seed_settings(self, restrict_to=None):
                del restrict_to
                return {13: {"seeding_time_limit_minutes": 4320, "ratio_limit": 1.0}}

        fetched = _fetch_indexer_seed_settings(Client(), None)

        assert fetched == {13: {"seeding_time_limit_minutes": 4320, "ratio_limit": 1.0}}
        assert _last_known_seed_settings == fetched

    def test_failure_falls_back_to_last_known_good(self):
        _last_known_seed_settings[13] = {"seeding_time_limit_minutes": 4320}

        class FailingClient:
            def get_indexer_seed_settings(self, restrict_to=None):
                del restrict_to
                raise RuntimeError("indexers unavailable")

        fetched = _fetch_indexer_seed_settings(FailingClient(), None)

        assert fetched == {13: {"seeding_time_limit_minutes": 4320}}
        # The fallback must be a copy so callers cannot mutate the cache.
        fetched[99] = {"ratio_limit": 2.0}
        assert 99 not in _last_known_seed_settings

    def test_failure_with_no_history_returns_empty(self):
        class FailingClient:
            def get_indexer_seed_settings(self, restrict_to=None):
                del restrict_to
                raise RuntimeError("indexers unavailable")

        assert _fetch_indexer_seed_settings(FailingClient(), None) == {}


class TestMamLanguageCoverage:
    """MyAnonamouse offers 62 languages; an unmapped code is dropped entirely,
    which would leave {Language} empty and different-language editions colliding."""

    def test_unmapped_language_is_dropped_not_passed_through(self):
        # Documents why coverage matters: there is no raw fallback.
        assert _extract_mam_language("Some Book [XYZ / EPUB]") is None

    def test_maps_the_common_three_letter_codes(self):
        cases = {
            "ENG": "en",
            "SWE": "sv",
            "GER": "de",
            "DEU": "de",
            "FRE": "fr",
            "FRA": "fr",
            "CZE": "cs",
            "CES": "cs",
        }
        for tag, expected in cases.items():
            assert _extract_mam_language(f"Book [{tag} / EPUB]") == expected, tag

    def test_maps_languages_added_for_mam_parity(self):
        cases = {
            "LAT": "la",
            "PER": "fa",
            "FAS": "fa",
            "TAM": "ta",
            "URD": "ur",
            "EST": "et",
            "ICE": "is",
            "ISL": "is",
            "GLE": "ga",
            "TGL": "fil",
            "BEN": "bn",
            "BOS": "bs",
            "SAN": "sa",
            "GLA": "gd",
            "GLV": "gv",
            "MAY": "ms",
            "MSA": "ms",
            "BUR": "my",
            "MYA": "my",
        }
        for tag, expected in cases.items():
            assert _extract_mam_language(f"Book [{tag} / M4B]") == expected, tag


class _MultiIndexerClient:
    """Torznab client where each indexer entry returns its own result set.

    Models one tracker configured in Prowlarr as several entries differing by a
    server-side search filter, so the same guid comes back from more than one.
    """

    def __init__(self, results_by_indexer: dict[int, list[dict]], priorities=None):
        self.results_by_indexer = results_by_indexer
        self.priorities = priorities or {}
        self.indexer_timeout = 90

    def get_enabled_indexers_detailed(self, *, raise_on_error=False):
        del raise_on_error
        return [
            {
                "id": indexer_id,
                "enable": True,
                "priority": self.priorities.get(indexer_id, 25),
                "capabilities": {
                    "categories": [
                        {"id": 7000, "subCategories": []},
                        {"id": 3030, "subCategories": []},
                    ]
                },
            }
            for indexer_id in sorted(self.results_by_indexer)
        ]

    def torznab_search(
        self,
        *,
        indexer_id: int,
        query: str,
        categories=None,
        search_type="book",
        limit=100,
        offset=0,
    ):
        del query, categories, search_type, limit, offset
        return self.results_by_indexer.get(indexer_id, [])

    def get_enriched_indexer_ids(self, restrict_to=None, indexers=None):
        del restrict_to, indexers
        return []

    def get_indexer_seed_settings(self, restrict_to=None):
        del restrict_to
        return {}


def _mam_result(indexer_id: int, indexer: str, guid: str, *, freeleech: bool = False) -> dict:
    return {
        "guid": guid,
        "title": "Dune",
        "indexerId": indexer_id,
        "indexer": indexer,
        "protocol": "torrent",
        "size": 1048576,
        "seeders": 10,
        "leechers": 1,
        "categories": [{"id": 7020}],
        "downloadVolumeFactor": 0.0 if freeleech else 1.0,
        "infoUrl": f"https://tracker.example/{guid}",
    }


class TestIndexerAwareDeduplication:
    """One tracker as several Prowlarr entries must not collapse to one row (#1137).

    These assert the split itself, so they turn PROWLARR_COLLAPSE_DUPLICATES off:
    it ships on, which keeps the release list as it was before #1137 for everyone
    who has not asked for the per-entry rows.
    """

    SPLIT = {"PROWLARR_COLLAPSE_DUPLICATES": False}

    ONLY_ACTIVE = 10
    FREELEECH = 25

    def _search(self, monkeypatch, results_by_indexer, config_values=None, priorities=None):
        import shelfmark.release_sources.prowlarr.source as prowlarr_source
        from shelfmark.core.search_plan import build_release_search_plan

        values = {"PROWLARR_INDEXERS": "", "PROWLARR_AUTO_EXPAND": False}
        values.update(config_values or {})
        monkeypatch.setattr(
            prowlarr_source.config, "get", lambda key, default=None: values.get(key, default)
        )

        source = ProwlarrSource()
        monkeypatch.setattr(
            source, "_get_client", lambda: _MultiIndexerClient(results_by_indexer, priorities)
        )

        book = BookMetadata(
            provider="hardcover", provider_id="123", title="Dune", authors=["Frank Herbert"]
        )
        plan = build_release_search_plan(book, languages=["en"])
        return source.search(book, plan)

    def test_same_guid_from_two_indexer_entries_both_survive(self, monkeypatch):
        shared_guid = "https://tracker.example/torrent/555"
        releases = self._search(
            monkeypatch,
            {
                self.ONLY_ACTIVE: [_mam_result(self.ONLY_ACTIVE, "MyAnonamouse", shared_guid)],
                self.FREELEECH: [
                    _mam_result(
                        self.FREELEECH, "MyAnonamouse - Freeleech", shared_guid, freeleech=True
                    )
                ],
            },
            config_values=self.SPLIT,
        )

        assert len(releases) == 2
        assert {r.indexer for r in releases} == {"MyAnonamouse", "MyAnonamouse - Freeleech"}
        assert [r.extra["freeleech"] for r in releases].count(True) == 1

    def test_surviving_rows_have_distinct_source_ids(self, monkeypatch):
        shared_guid = "https://tracker.example/torrent/555"
        releases = self._search(
            monkeypatch,
            {
                self.ONLY_ACTIVE: [_mam_result(self.ONLY_ACTIVE, "MyAnonamouse", shared_guid)],
                self.FREELEECH: [
                    _mam_result(self.FREELEECH, "MyAnonamouse - Freeleech", shared_guid)
                ],
            },
            config_values=self.SPLIT,
        )

        source_ids = [r.source_id for r in releases]
        assert len(source_ids) == 2
        assert len(set(source_ids)) == 2

    def test_source_ids_resolve_to_their_own_indexer_entry(self, monkeypatch):
        from shelfmark.release_sources.prowlarr.cache import get_release

        shared_guid = "https://tracker.example/torrent/555"
        releases = self._search(
            monkeypatch,
            {
                self.ONLY_ACTIVE: [_mam_result(self.ONLY_ACTIVE, "MyAnonamouse", shared_guid)],
                self.FREELEECH: [
                    _mam_result(self.FREELEECH, "MyAnonamouse - Freeleech", shared_guid)
                ],
            },
            config_values=self.SPLIT,
        )

        assert len(releases) == 2
        cached_indexer_ids = []
        for release in releases:
            cached = get_release(release.source_id)
            assert cached is not None
            assert cached["indexerId"] == release.extra["indexer_id"]
            cached_indexer_ids.append(cached["indexerId"])

        assert sorted(cached_indexer_ids) == [self.ONLY_ACTIVE, self.FREELEECH]

    def test_repeat_of_same_result_from_one_indexer_still_collapses(self, monkeypatch):
        duplicate = _mam_result(self.ONLY_ACTIVE, "MyAnonamouse", "https://tracker.example/t/1")
        releases = self._search(monkeypatch, {self.ONLY_ACTIVE: [duplicate, dict(duplicate)]})

        assert len(releases) == 1


class TestBuildSourceId:
    """source_id keys the release cache, so it must survive shared guids."""

    def test_same_guid_from_two_indexer_entries_gets_two_ids(self):
        guid = "https://tracker.example/torrent/555"

        assert build_source_id({"guid": guid, "indexerId": 10}) != build_source_id(
            {"guid": guid, "indexerId": 25}
        )

    def test_same_indexer_and_guid_is_stable(self):
        result = {"guid": "https://tracker.example/torrent/555", "indexerId": 10}

        assert build_source_id(result) == build_source_id(dict(result))

    def test_falls_back_to_the_bare_guid_without_an_indexer_id(self):
        guid = "https://tracker.example/torrent/555"

        assert build_source_id({"guid": guid}) == guid

    def test_falls_back_to_indexer_and_title_without_a_guid(self):
        source_id = build_source_id({"indexerId": 10, "indexer": "MAM", "title": "Dune"})

        assert source_id.startswith("10:MAM:")


class TestCollapseDuplicatesSetting:
    """One-row-per-release collapse, on by default, resolved by Prowlarr's priority."""

    ONLY_ACTIVE = 10
    FREELEECH = 25

    def _search_collapsed(self, monkeypatch, priorities):
        shared_guid = "https://tracker.example/torrent/555"
        return TestIndexerAwareDeduplication()._search(
            monkeypatch,
            {
                self.ONLY_ACTIVE: [_mam_result(self.ONLY_ACTIVE, "MyAnonamouse", shared_guid)],
                self.FREELEECH: [
                    _mam_result(self.FREELEECH, "MyAnonamouse - Freeleech", shared_guid)
                ],
            },
            config_values={"PROWLARR_COLLAPSE_DUPLICATES": True},
            priorities=priorities,
        )

    def test_collapse_keeps_the_better_prowlarr_priority(self, monkeypatch):
        releases = self._search_collapsed(monkeypatch, {self.FREELEECH: 20, self.ONLY_ACTIVE: 24})

        assert len(releases) == 1
        assert releases[0].indexer == "MyAnonamouse - Freeleech"

    def test_collapse_honours_the_reverse_priority(self, monkeypatch):
        releases = self._search_collapsed(monkeypatch, {self.FREELEECH: 30, self.ONLY_ACTIVE: 24})

        assert len(releases) == 1
        assert releases[0].indexer == "MyAnonamouse"

    def test_equal_priority_keeps_the_first_queried(self, monkeypatch):
        releases = self._search_collapsed(monkeypatch, {self.FREELEECH: 25, self.ONLY_ACTIVE: 25})

        assert len(releases) == 1
        assert releases[0].indexer == "MyAnonamouse"

    def _search_shared_guid(self, monkeypatch, config_values):
        return TestIndexerAwareDeduplication()._search(
            monkeypatch,
            {
                self.ONLY_ACTIVE: [
                    _mam_result(self.ONLY_ACTIVE, "MyAnonamouse", "https://tracker.example/t/9")
                ],
                self.FREELEECH: [
                    _mam_result(self.FREELEECH, "MAM - Freeleech", "https://tracker.example/t/9")
                ],
            },
            config_values=config_values,
            priorities={self.FREELEECH: 20, self.ONLY_ACTIVE: 24},
        )

    def test_collapse_is_on_when_the_setting_is_untouched(self, monkeypatch):
        """An upgrading user who sets nothing keeps the single row they had before #1137."""
        releases = self._search_shared_guid(monkeypatch, None)

        assert len(releases) == 1
        assert releases[0].indexer == "MAM - Freeleech"

    def test_opting_out_keeps_every_indexer_entry(self, monkeypatch):
        releases = self._search_shared_guid(monkeypatch, {"PROWLARR_COLLAPSE_DUPLICATES": False})

        assert len(releases) == 2

    def test_the_settings_field_and_the_search_fallback_agree(self):
        """The field default is what governs in production; the search fallback only
        applies to an unregistered key. They have to say the same thing.
        """
        from shelfmark.release_sources.prowlarr.settings import prowlarr_config_settings

        field = next(
            f for f in prowlarr_config_settings() if f.key == "PROWLARR_COLLAPSE_DUPLICATES"
        )

        assert field.default is True


class TestBuildIndexerPriority:
    """The priority NUMBER from Prowlarr is the rank; the id is only the key."""

    def test_reads_the_priority_number_per_indexer(self):
        priority = _build_indexer_priority([{"id": 25, "priority": 20}, {"id": 10, "priority": 24}])

        assert priority == {25: 20, 10: 24}

    def test_a_lower_number_is_preferred(self):
        priority = _build_indexer_priority([{"id": 25, "priority": 20}, {"id": 10, "priority": 24}])

        assert priority[25] < priority[10]

    def test_coerces_string_ids_and_priorities(self):
        assert _build_indexer_priority([{"id": "25", "priority": "20"}]) == {25: 20}

    def test_skips_records_without_a_usable_id_or_priority(self):
        assert (
            _build_indexer_priority([{"priority": 20}, {"id": "abc", "priority": 20}, {"id": 7}])
            == {}
        )

    def test_empty_input_yields_no_preferences(self):
        assert _build_indexer_priority([]) == {}


class TestDedupEdgeCases:
    """Malformed and partial results must never silently lose a row."""

    def test_unidentifiable_results_are_all_kept(self, monkeypatch):
        blank = {"indexerId": 10, "indexer": "MAM", "protocol": "torrent"}
        releases = TestIndexerAwareDeduplication()._search(
            monkeypatch, {10: [dict(blank), dict(blank)]}
        )

        assert len(releases) == 2

    def test_dedup_key_is_none_when_nothing_identifies_the_result(self):
        assert _result_dedup_key({"indexerId": 10}) is None
        assert _result_dedup_key({"guid": "   ", "title": "  "}) is None

    def test_dedup_falls_back_to_title_within_one_indexer(self):
        first = {"indexerId": 10, "title": "Dune"}
        second = {"indexerId": 10, "title": "Dune"}
        assert _result_dedup_key(first) == _result_dedup_key(second)

    def test_same_title_from_two_indexers_is_not_deduped(self):
        assert _result_dedup_key({"indexerId": 10, "title": "Dune"}) != _result_dedup_key(
            {"indexerId": 25, "title": "Dune"}
        )

    def test_identity_ignores_whitespace_only_fields(self):
        assert _release_identity({"guid": "  ", "downloadUrl": "https://x/1"}) == "https://x/1"

    def test_identity_accepts_a_numeric_guid(self):
        assert _release_identity({"guid": 12345}) == "12345"

    def test_identity_is_none_without_a_strong_identifier(self):
        assert _release_identity({"title": "Dune", "indexerId": 10}) is None


class TestCollapseEdgeCases:
    """Collapse discards rows, so it must only ever merge on a strong identifier."""

    def test_unidentifiable_results_are_never_collapsed(self):
        blank = {"indexerId": 10, "title": "Dune"}
        kept = _collapse_duplicate_indexer_results([dict(blank), dict(blank)], {})

        assert len(kept) == 2

    def test_same_title_different_torrents_are_not_collapsed(self):
        results = [
            {"indexerId": 10, "guid": "guid-a", "title": "Dune"},
            {"indexerId": 25, "guid": "guid-b", "title": "Dune"},
        ]

        assert len(_collapse_duplicate_indexer_results(results, {})) == 2

    def test_collapse_preserves_the_position_of_the_row_it_replaces(self):
        results = [
            {"indexerId": 10, "guid": "shared"},
            {"indexerId": 99, "guid": "other"},
            {"indexerId": 25, "guid": "shared"},
        ]
        kept = _collapse_duplicate_indexer_results(results, {25: 0, 10: 1})

        assert [r["indexerId"] for r in kept] == [25, 99]

    def test_results_without_an_indexer_id_still_collapse_on_guid(self):
        results = [{"guid": "shared"}, {"guid": "shared"}]

        assert len(_collapse_duplicate_indexer_results(results, {})) == 1


class TestIndexerPrioritySorting:
    """Results are listed by the indexer priority configured in Prowlarr."""

    ONLY_ACTIVE = 10
    FREELEECH = 25
    OTHER = 99

    def _search(self, monkeypatch, priorities):
        return TestIndexerAwareDeduplication()._search(
            monkeypatch,
            {
                self.ONLY_ACTIVE: [_mam_result(self.ONLY_ACTIVE, "MyAnonamouse", "https://t/a")],
                self.FREELEECH: [
                    _mam_result(self.FREELEECH, "MyAnonamouse - Freeleech", "https://t/b")
                ],
                self.OTHER: [_mam_result(self.OTHER, "Some Other Tracker", "https://t/c")],
            },
            priorities=priorities,
        )

    def test_results_follow_the_priority_numbers(self, monkeypatch):
        releases = self._search(
            monkeypatch, {self.FREELEECH: 20, self.ONLY_ACTIVE: 24, self.OTHER: 50}
        )

        assert [r.extra["indexer_id"] for r in releases] == [
            self.FREELEECH,
            self.ONLY_ACTIVE,
            self.OTHER,
        ]

    def test_reversing_the_numbers_reverses_the_list(self, monkeypatch):
        releases = self._search(
            monkeypatch, {self.FREELEECH: 50, self.ONLY_ACTIVE: 24, self.OTHER: 20}
        )

        assert [r.extra["indexer_id"] for r in releases] == [
            self.OTHER,
            self.ONLY_ACTIVE,
            self.FREELEECH,
        ]

    def test_equal_priorities_keep_query_order(self, monkeypatch):
        releases = self._search(
            monkeypatch, {self.FREELEECH: 25, self.ONLY_ACTIVE: 25, self.OTHER: 25}
        )

        assert [r.extra["indexer_id"] for r in releases] == [
            self.ONLY_ACTIVE,
            self.FREELEECH,
            self.OTHER,
        ]


class TestIndexerPrioritySortOption:
    """The UI needs the priority on the row to offer an explicit sort on it."""

    def test_releases_carry_their_prowlarr_priority(self, monkeypatch):
        releases = TestIndexerPrioritySorting()._search(monkeypatch, {10: 24, 25: 20, 99: 50})

        by_indexer = {r.extra["indexer_id"]: r.extra.get("indexer_priority") for r in releases}
        assert by_indexer == {10: 24, 25: 20, 99: 50}

    def test_priority_is_absent_when_prowlarr_reports_none(self, monkeypatch):
        releases = TestIndexerPrioritySorting()._search(monkeypatch, {})

        assert all(r.extra.get("indexer_priority") == 25 for r in releases)

    def test_sort_option_is_offered_lowest_first(self):
        from shelfmark.release_sources import serialize_column_config

        source = ProwlarrSource()
        serialized = serialize_column_config(source.get_column_config())
        options = {o["label"]: o for o in serialized["extra_sort_options"]}

        assert options["Indexer priority"]["sort_key"] == "extra.indexer_priority"
        assert options["Indexer priority"]["default_direction"] == "asc"
        assert options["Peers"]["default_direction"] == "desc"


class _FailingIndexerClient:
    """Torznab client where chosen indexers raise instead of answering.

    Mirrors #1249: Prowlarr proxies the search to a Cloudflare-fronted tracker,
    FlareSolverr is still solving the challenge when the HTTP read times out.
    """

    def __init__(self, failing_indexers: set[int], results_by_indexer=None):
        self.failing_indexers = failing_indexers
        self.results_by_indexer = results_by_indexer or {}
        self.indexer_timeout = 90
        self.calls: list[tuple[int, object]] = []

    def get_enabled_indexers_detailed(self, *, raise_on_error=False):
        del raise_on_error
        indexer_ids = sorted(self.failing_indexers | set(self.results_by_indexer))
        return [
            {
                "id": indexer_id,
                "enable": True,
                "capabilities": {"categories": [{"id": 7000, "subCategories": []}]},
            }
            for indexer_id in indexer_ids
        ]

    def torznab_search(
        self,
        *,
        indexer_id: int,
        query: str,
        categories=None,
        search_type="book",
        limit=100,
        offset=0,
    ):
        del query, search_type, limit, offset
        self.calls.append((indexer_id, categories))
        if indexer_id in self.failing_indexers:
            msg = f"indexer {indexer_id} did not respond within 90s"
            raise ProwlarrSearchError(msg)
        return self.results_by_indexer.get(indexer_id, [])

    def get_enriched_indexer_ids(self, restrict_to=None, indexers=None):
        del restrict_to, indexers
        return []

    def get_indexer_seed_settings(self, restrict_to=None):
        del restrict_to
        return {}


class TestFailedIndexerSearchIsNotNoResults:
    """A Torznab timeout must not read as "this book has no releases" (#1249)."""

    def _search(self, monkeypatch, client, config_values=None):
        import shelfmark.release_sources.prowlarr.source as prowlarr_source
        from shelfmark.core.search_plan import build_release_search_plan

        values = {"PROWLARR_INDEXERS": "", "PROWLARR_AUTO_EXPAND": False}
        values.update(config_values or {})
        monkeypatch.setattr(
            prowlarr_source.config, "get", lambda key, default=None: values.get(key, default)
        )

        source = ProwlarrSource()
        monkeypatch.setattr(source, "_get_client", lambda: client)

        book = BookMetadata(
            provider="hardcover", provider_id="123", title="Dune", authors=["Frank Herbert"]
        )
        plan = build_release_search_plan(book, languages=["en"])
        return source.search(book, plan)

    def test_sole_indexer_timing_out_raises_instead_of_returning_empty(self, monkeypatch):
        client = _FailingIndexerClient({1})

        with pytest.raises(SourceUnavailableError) as excinfo:
            self._search(monkeypatch, client)

        assert "1 of 1 indexer searches failed" in str(excinfo.value)
        assert "did not respond within 90s" in str(excinfo.value)

    def test_one_failure_does_not_sink_an_indexer_that_answered(self, monkeypatch):
        client = _FailingIndexerClient(
            {1},
            {
                2: [
                    {
                        "guid": "g2",
                        "title": "Dune",
                        "indexerId": 2,
                        "indexer": "working",
                        "protocol": "torrent",
                        "size": 1048576,
                        "seeders": 5,
                    }
                ]
            },
        )

        releases = self._search(monkeypatch, client)

        assert [r.indexer for r in releases] == ["working"]

    def test_partial_failure_with_no_results_still_reports_the_failure(self, monkeypatch):
        client = _FailingIndexerClient({1}, {2: []})

        with pytest.raises(SourceUnavailableError) as excinfo:
            self._search(monkeypatch, client)

        assert "1 of 2 indexer searches failed" in str(excinfo.value)

    def test_auto_expand_does_not_retry_on_top_of_a_failed_search(self, monkeypatch):
        """The second search is what crashes FlareSolverr's Chrome on a small host."""
        client = _FailingIndexerClient({1})

        with pytest.raises(SourceUnavailableError):
            self._search(monkeypatch, client, {"PROWLARR_AUTO_EXPAND": True})

        assert client.calls == [(1, [7000])]

    def test_auto_expand_still_retries_when_the_indexer_answered_empty(self, monkeypatch):
        client = _FailingIndexerClient(set(), {1: []})

        assert self._search(monkeypatch, client, {"PROWLARR_AUTO_EXPAND": True}) == []
        assert client.calls == [(1, [7000]), (1, None)]


class TestUnreachableProwlarrIsNotNoResults:
    """Prowlarr itself being down must not read as "this book has no releases" (#1249)."""

    class _UnreachableClient:
        indexer_timeout = 90

        def get_enabled_indexers_detailed(self, *, raise_on_error=False):
            del raise_on_error
            raise requests.exceptions.ConnectionError("connection refused")

    def test_search_reports_the_connection_failure(self, monkeypatch):
        import shelfmark.release_sources.prowlarr.source as prowlarr_source
        from shelfmark.core.search_plan import build_release_search_plan

        monkeypatch.setattr(
            prowlarr_source.config,
            "get",
            lambda key, default=None: {"PROWLARR_INDEXERS": ""}.get(key, default),
        )
        source = ProwlarrSource()
        monkeypatch.setattr(source, "_get_client", lambda: self._UnreachableClient())

        book = BookMetadata(
            provider="hardcover", provider_id="123", title="Dune", authors=["Frank Herbert"]
        )
        plan = build_release_search_plan(book, languages=["en"])

        with pytest.raises(SourceUnavailableError, match="could not reach Prowlarr"):
            source.search(book, plan)


class TestSearchBudgetScalesWithIndexerTimeout:
    def test_default_budget_is_unchanged(self):
        assert _search_budget_seconds(30) == PROWLARR_SEARCH_TIMEOUT_SECONDS

    def test_a_long_indexer_timeout_widens_the_budget(self):
        assert _search_budget_seconds(120) == 240.0

    def test_budget_stays_under_the_gunicorn_worker_timeout(self):
        assert _search_budget_seconds(300) == 240.0


class TestSplitMamFormats:
    """MAM's structured "[LANG / FORMATS]" bracket, split into known vs unknown tokens."""

    def test_recognized_only(self):
        from shelfmark.release_sources.prowlarr.source import _split_mam_formats

        assert _split_mam_formats("Title by Author [ENG / EPUB MOBI]") == (["epub", "mobi"], [])

    def test_unrecognized_only_is_surfaced(self):
        from shelfmark.release_sources.prowlarr.source import _split_mam_formats

        assert _split_mam_formats("The Martian by Andy Weir [ENG / AVI]") == ([], ["avi"])

    def test_mixed_keeps_both_sides(self):
        from shelfmark.release_sources.prowlarr.source import _split_mam_formats

        assert _split_mam_formats("Title [ENG / M4B AVI]") == (["m4b"], ["avi"])

    def test_no_structured_bracket(self):
        from shelfmark.release_sources.prowlarr.source import _split_mam_formats

        assert _split_mam_formats("Title [VIP]") == ([], [])
        assert _split_mam_formats("") == ([], [])

    def test_extract_mam_formats_still_returns_recognized(self):
        from shelfmark.release_sources.prowlarr.source import _extract_mam_formats

        assert _extract_mam_formats("Title [ENG / MP3]") == ["mp3"]
        assert _extract_mam_formats("Title [ENG / AVI]") == []


class TestUnrecognizedFormatOnRelease:
    def _result(self, title: str) -> dict:
        return {
            "title": title,
            "guid": "https://www.myanonamouse.net/t/627978",
            "indexer": "MyAnonamouse",
            "indexerId": 1,
            "protocol": "torrent",
            "size": 320000000,
            "seeders": 800,
            "leechers": 0,
            "categories": [{"id": 3030}],
        }

    def test_unrecognized_format_lands_in_extra(self):
        from shelfmark.release_sources.prowlarr.source import _prowlarr_result_to_release

        release = _prowlarr_result_to_release(
            self._result("The Martian by Andy Weir [ENG / AVI]"),
            "audiobook",
            enable_format_detection=True,
        )
        assert release.format is None
        assert release.extra["formats"] is None
        assert release.extra["unrecognized_formats"] == ["avi"]

    def test_recognized_format_leaves_unrecognized_empty(self):
        from shelfmark.release_sources.prowlarr.source import _prowlarr_result_to_release

        release = _prowlarr_result_to_release(
            self._result("The Martian by Andy Weir [ENG / M4B]"),
            "audiobook",
            enable_format_detection=True,
        )
        assert release.format == "m4b"
        assert release.extra["unrecognized_formats"] is None

    def test_not_populated_without_format_detection(self):
        from shelfmark.release_sources.prowlarr.source import _prowlarr_result_to_release

        release = _prowlarr_result_to_release(
            self._result("The Martian by Andy Weir [ENG / AVI]"), "audiobook"
        )
        assert release.extra["unrecognized_formats"] is None


class TestProwlarrStaleIndexerSelection:
    """Indexers removed or disabled in Prowlarr must not be searched (#1283)."""

    def test_drop_unknown_indexer_ids_keeps_only_live_indexers(self):
        assert _drop_unknown_indexer_ids([1, 99], [{"id": 1}, {"id": 2}]) == [1]

    def test_drop_unknown_indexer_ids_leaves_search_all_alone(self):
        assert _drop_unknown_indexer_ids(None, [{"id": 1}]) is None

    def _search_with_selection(self, monkeypatch, selection):
        import shelfmark.release_sources.prowlarr.source as prowlarr_source

        def fake_get(key: str, default=None):
            values = {
                "PROWLARR_INDEXERS": selection,
                "PROWLARR_AUTO_EXPAND": False,
            }
            return values.get(key, default)

        monkeypatch.setattr(prowlarr_source.config, "get", fake_get)

        class RecordingClient(FakeTorznabClient):
            def __init__(self):
                super().__init__()
                self.searched_indexer_ids: list[int] = []

            def torznab_search(self, *, indexer_id: int, **kwargs):
                self.searched_indexer_ids.append(indexer_id)
                return super().torznab_search(indexer_id=indexer_id, **kwargs)

        fake_client = RecordingClient()
        source = ProwlarrSource()
        monkeypatch.setattr(source, "_get_client", lambda: fake_client)

        book = BookMetadata(
            provider="hardcover",
            provider_id="123",
            title="Anything",
            authors=["Someone"],
        )

        from shelfmark.core.search_plan import build_release_search_plan

        plan = build_release_search_plan(book, languages=["en"], manual_query="my custom")
        source.search(book, plan, content_type="ebook")
        return fake_client

    def test_search_skips_indexer_missing_from_prowlarr(self, monkeypatch):
        # The fake Prowlarr only serves indexer 1; 99 was removed behind our back.
        fake_client = self._search_with_selection(monkeypatch, [1, 99])

        assert fake_client.searched_indexer_ids == [1]

    def test_search_queries_nothing_when_every_selected_indexer_is_gone(self, monkeypatch):
        fake_client = self._search_with_selection(monkeypatch, [98, 99])

        assert fake_client.searched_indexer_ids == []


class TestHideUnsupportedFormats:
    """Plain indexer titles that end in a format the user did not enable are hidden."""

    SUPPORTED = ["epub", "mobi", "azw3"]

    def _patch_config(self, monkeypatch, overrides=None):
        import shelfmark.release_sources.prowlarr.source as prowlarr_source

        values = {
            "PROWLARR_INDEXERS": "",
            "PROWLARR_AUTO_EXPAND": False,
            "SUPPORTED_FORMATS": self.SUPPORTED,
            **(overrides or {}),
        }
        monkeypatch.setattr(
            prowlarr_source.config, "get", lambda key, default=None: values.get(key, default)
        )

    @pytest.mark.parametrize(
        ("title", "expected"),
        [
            ("Иван Вазов - Под игото.doc", "doc"),
            ("Author - Title.RTF", "rtf"),
            ("Author - Title.pdf ", "pdf"),
            ("Author - Title.epub", None),
            ("Author - Title [EPUB]", None),
            ("Jane.Eyre.by.Bronte", None),
            ("Author - Title.rar", None),
            ("Author - Title.zip", None),
            ("Author - Title", None),
            ("", None),
        ],
    )
    def test_unsupported_title_extension(self, monkeypatch, title, expected):
        self._patch_config(monkeypatch)
        assert _unsupported_title_extension(title, "ebook") == expected

    def test_audiobook_searches_are_not_filtered(self, monkeypatch):
        self._patch_config(monkeypatch)
        assert _unsupported_title_extension("Author - Title.doc", "audiobook") is None

    def test_comma_separated_supported_formats(self, monkeypatch):
        self._patch_config(monkeypatch, {"SUPPORTED_FORMATS": "epub, doc"})
        assert _unsupported_title_extension("Author - Title.doc", "ebook") is None
        assert _unsupported_title_extension("Author - Title.rtf", "ebook") == "rtf"

    def _search(self, monkeypatch, overrides=None):
        self._patch_config(monkeypatch, overrides)
        fake_client = FakeTorznabClient(
            search_results=[
                {
                    "guid": f"result-{index}",
                    "protocol": "torrent",
                    "title": title,
                    "magnetUrl": f"magnet:?xt=urn:btih:abc{index}",
                    "indexerId": 1,
                    "indexer": "Zamunda",
                }
                for index, title in enumerate(
                    ["Иван Вазов - Под игото.epub", "Иван Вазов - Под игото.doc"]
                )
            ]
        )
        source = ProwlarrSource()
        monkeypatch.setattr(source, "_get_client", lambda: fake_client)
        book = BookMetadata(
            provider="hardcover", provider_id="123", title="Под игото", authors=["Иван Вазов"]
        )

        from shelfmark.core.search_plan import build_release_search_plan

        plan = build_release_search_plan(book, languages=["bg"])
        return source.search(book, plan, content_type="ebook")

    def test_search_hides_unsupported_format(self, monkeypatch):
        releases = self._search(monkeypatch)
        assert [release.title for release in releases] == ["Иван Вазов - Под игото.epub"]

    def test_search_keeps_unsupported_format_when_disabled(self, monkeypatch):
        releases = self._search(monkeypatch, {"PROWLARR_HIDE_UNSUPPORTED_FORMATS": False})
        assert len(releases) == 2


class TestEbookMaxSize:
    """PROWLARR_EBOOK_MAX_SIZE_MB hides oversized ebook results and nothing else (#844)."""

    INDEXER = 10
    MB = 1024 * 1024

    def _results(self, *sizes):
        return {
            self.INDEXER: [
                {**_mam_result(self.INDEXER, "Indexer", f"guid-{n}"), "size": size}
                for n, size in enumerate(sizes)
            ]
        }

    def _search(self, monkeypatch, sizes, *, limit=None, content_type="ebook"):
        import shelfmark.release_sources.prowlarr.source as prowlarr_source
        from shelfmark.core.search_plan import build_release_search_plan

        values = {"PROWLARR_INDEXERS": "", "PROWLARR_AUTO_EXPAND": False}
        if limit is not None:
            values["PROWLARR_EBOOK_MAX_SIZE_MB"] = limit
        monkeypatch.setattr(
            prowlarr_source.config, "get", lambda key, default=None: values.get(key, default)
        )
        source = ProwlarrSource()
        monkeypatch.setattr(
            source, "_get_client", lambda: _MultiIndexerClient(self._results(*sizes), None)
        )
        book = BookMetadata(
            provider="hardcover", provider_id="123", title="Dune", authors=["Frank Herbert"]
        )
        plan = build_release_search_plan(book, languages=["en"])
        return source.search(book, plan, content_type=content_type)

    def test_no_limit_shows_everything(self, monkeypatch):
        releases = self._search(monkeypatch, [5 * self.MB, 4000 * self.MB])

        assert len(releases) == 2

    def test_zero_is_no_limit(self, monkeypatch):
        releases = self._search(monkeypatch, [5 * self.MB, 4000 * self.MB], limit=0)

        assert len(releases) == 2

    def test_a_result_over_the_limit_is_hidden(self, monkeypatch):
        releases = self._search(monkeypatch, [5 * self.MB, 4000 * self.MB], limit=100)

        assert [r.size_bytes for r in releases] == [5 * self.MB]

    def test_a_result_exactly_at_the_limit_stays(self, monkeypatch):
        releases = self._search(monkeypatch, [100 * self.MB, 100 * self.MB + 1], limit=100)

        assert [r.size_bytes for r in releases] == [100 * self.MB]

    def test_a_result_with_no_size_stays(self, monkeypatch):
        releases = self._search(monkeypatch, [None, 0, 4000 * self.MB], limit=100)

        assert len(releases) == 2

    def test_audiobook_searches_are_not_limited(self, monkeypatch):
        releases = self._search(
            monkeypatch, [5 * self.MB, 4000 * self.MB], limit=100, content_type="audiobook"
        )

        assert len(releases) == 2

    def test_a_bad_setting_is_no_limit(self, monkeypatch):
        releases = self._search(monkeypatch, [5 * self.MB, 4000 * self.MB], limit="lots")

        assert len(releases) == 2
