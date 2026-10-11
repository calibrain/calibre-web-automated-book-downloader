"""
Tests for AudiobookBay release source.
"""

from unittest.mock import patch

import pytest

from shelfmark.core.search_plan import ReleaseSearchPlan, ReleaseSearchVariant
from shelfmark.metadata_providers import BookMetadata
from shelfmark.release_sources.audiobookbay.source import (
    AudiobookBaySource,
    _generate_source_id,
    _map_language,
    _parse_bitrate_to_kbps,
    _split_title_and_author,
)


class TestMapLanguage:
    """Tests for the _map_language function."""

    def test_map_language_english(self):
        """Test mapping English language."""
        assert _map_language("English") == "en"
        assert _map_language("english") == "en"
        assert _map_language("ENGLISH") == "en"

    def test_map_language_spanish(self):
        """Test mapping Spanish language."""
        assert _map_language("Spanish") == "es"
        assert _map_language("spanish") == "es"

    def test_map_language_french(self):
        """Test mapping French language."""
        assert _map_language("French") == "fr"
        assert _map_language("french") == "fr"

    def test_map_language_german(self):
        """Test mapping German language."""
        assert _map_language("German") == "de"
        assert _map_language("german") == "de"

    def test_map_language_unknown(self):
        """Test mapping unknown language returns lowercase."""
        assert _map_language("UnknownLanguage") == "unknownlanguage"
        assert _map_language("Klingon") == "klingon"

    def test_map_language_empty(self):
        """Test empty language returns None."""
        assert _map_language("") is None
        assert _map_language(None) is None

    def test_map_language_with_whitespace(self):
        """Test language with whitespace is trimmed."""
        assert _map_language("  English  ") == "en"
        assert _map_language("\tFrench\n") == "fr"


class TestParseBitrate:
    """Tests for the _parse_bitrate_to_kbps function."""

    def test_parse_bitrate_to_kbps(self):
        """Test normal bitrate parsing from ABB string values."""
        assert _parse_bitrate_to_kbps("128 Kbps") == 128
        assert _parse_bitrate_to_kbps("192kbps") == 192

    def test_parse_bitrate_to_kbps_invalid(self):
        """Test invalid bitrate values return None."""
        assert _parse_bitrate_to_kbps(None) is None
        assert _parse_bitrate_to_kbps("") is None
        assert _parse_bitrate_to_kbps("Unknown") is None


class TestGenerateSourceId:
    """Tests for the _generate_source_id function."""

    def test_generate_source_id_consistent(self):
        """Test that same URL generates same ID."""
        url = "https://audiobookbay.lu/abss/test-book/"
        id1 = _generate_source_id(url)
        id2 = _generate_source_id(url)
        assert id1 == id2
        assert len(id1) == 32  # MD5 hex digest length

    def test_generate_source_id_different_urls(self):
        """Test that different URLs generate different IDs."""
        url1 = "https://audiobookbay.lu/abss/book1/"
        url2 = "https://audiobookbay.lu/abss/book2/"
        id1 = _generate_source_id(url1)
        id2 = _generate_source_id(url2)
        assert id1 != id2


class TestAudiobookBaySource:
    """Tests for the AudiobookBaySource class."""

    @pytest.fixture(autouse=True)
    def configure_hostname(self, monkeypatch):
        """Provide a default ABB hostname for source.search tests."""
        import shelfmark.release_sources.audiobookbay.source as source_module

        original_get = source_module.config.get

        def mock_get(key, default=None):
            if key == "ABB_HOSTNAME":
                return "audiobookbay.lu"
            return original_get(key, default)

        monkeypatch.setattr(source_module.config, "get", mock_get)

    def test_is_available_enabled(self, monkeypatch):
        """Test is_available when enabled."""

        def mock_get(key, default=False):
            if key == "ABB_ENABLED":
                return True
            if key == "ABB_HOSTNAME":
                return "audiobookbay.lu"
            return default

        import shelfmark.release_sources.audiobookbay.source as source_module

        monkeypatch.setattr(source_module.config, "get", mock_get)

        source = AudiobookBaySource()
        assert source.is_available() is True

    def test_is_available_disabled(self, monkeypatch):
        """Test is_available when disabled."""

        def mock_get(key, default=False):
            if key == "ABB_ENABLED":
                return False
            return default

        import shelfmark.release_sources.audiobookbay.source as source_module

        monkeypatch.setattr(source_module.config, "get", mock_get)

        source = AudiobookBaySource()
        assert source.is_available() is False

    def test_search_non_audiobook_content_type(self):
        """Test that non-audiobook content types return empty."""
        source = AudiobookBaySource()
        book = BookMetadata(
            provider="test",
            provider_id="123",
            title="Test Book",
            authors=["Test Author"],
        )
        plan = ReleaseSearchPlan(
            languages=["en"],
            isbn_candidates=[],
            author="Test Author",
            title_variants=[ReleaseSearchVariant(title="Test Book", author="Test Author")],
            grouped_title_variants=[],
        )

        results = source.search(book, plan, content_type="ebook")
        assert results == []

    @patch("shelfmark.release_sources.audiobookbay.source.scraper.search_audiobookbay")
    def test_search_query_generation_manual(self, mock_search):
        """Test search with manual query."""
        mock_search.return_value = []

        source = AudiobookBaySource()
        book = BookMetadata(
            provider="test",
            provider_id="123",
            title="Test Book",
            authors=["Test Author"],
        )
        plan = ReleaseSearchPlan(
            languages=["en"],
            isbn_candidates=[],
            author="Test Author",
            title_variants=[ReleaseSearchVariant(title="Test Book", author="Test Author")],
            grouped_title_variants=[],
            manual_query="custom search query",
        )

        source.search(book, plan, content_type="audiobook")

        mock_search.assert_called_once()
        call_args = mock_search.call_args
        assert call_args.kwargs["query"] == "custom search query"

    @patch("shelfmark.release_sources.audiobookbay.source.scraper.search_audiobookbay")
    def test_search_query_generation_from_variants(self, mock_search):
        """Test search query generation from title variants with title-only retry."""
        mock_search.return_value = []

        source = AudiobookBaySource()
        book = BookMetadata(
            provider="test",
            provider_id="123",
            title="Test Book",
            authors=["Test Author"],
        )
        plan = ReleaseSearchPlan(
            languages=["en"],
            isbn_candidates=[],
            author="Test Author",
            title_variants=[ReleaseSearchVariant(title="Test Book", author="Test Author")],
            grouped_title_variants=[],
        )

        source.search(book, plan, content_type="audiobook")

        assert mock_search.call_count == 2
        first_call = mock_search.call_args_list[0]
        second_call = mock_search.call_args_list[1]
        assert first_call.kwargs["query"] == "test book test author"
        assert second_call.kwargs["query"] == "test book"

    @patch("shelfmark.release_sources.audiobookbay.source.scraper.search_audiobookbay")
    def test_search_query_generation_from_title_only(self, mock_search):
        """Test search query generation when only title available."""
        mock_search.return_value = []

        source = AudiobookBaySource()
        book = BookMetadata(
            provider="test",
            provider_id="123",
            title="Test Book",
            authors=[],
        )
        plan = ReleaseSearchPlan(
            languages=["en"],
            isbn_candidates=[],
            author="",
            title_variants=[],
            grouped_title_variants=[],
        )

        source.search(book, plan, content_type="audiobook")

        mock_search.assert_called_once()
        call_args = mock_search.call_args
        assert call_args.kwargs["query"] == "test book"

    @patch("shelfmark.release_sources.audiobookbay.source.scraper.search_audiobookbay")
    def test_search_empty_query(self, mock_search):
        """Test search with empty query returns empty."""
        source = AudiobookBaySource()
        book = BookMetadata(
            provider="test",
            provider_id="123",
            title="",
            authors=[],
        )
        plan = ReleaseSearchPlan(
            languages=["en"],
            isbn_candidates=[],
            author="",
            title_variants=[],
            grouped_title_variants=[],
        )

        results = source.search(book, plan, content_type="audiobook")

        assert results == []
        mock_search.assert_not_called()

    @patch("shelfmark.release_sources.audiobookbay.source.scraper.search_audiobookbay")
    def test_search_relevance_filtering(self, mock_search):
        """Test that irrelevant results are filtered out."""
        mock_search.return_value = [
            {
                "title": "Test Book by Test Author",
                "link": "https://audiobookbay.lu/abss/test-book/",
                "format": "M4B",
                "size": "500 MB",
                "language": "English",
            },
            {
                "title": "Something Completely Different",
                "link": "https://audiobookbay.lu/abss/unrelated/",
                "format": "MP3",
                "size": "1 GB",
                "language": "English",
            },
        ]

        source = AudiobookBaySource()
        book = BookMetadata(
            provider="test",
            provider_id="123",
            title="Test Book",
            authors=["Test Author"],
        )
        plan = ReleaseSearchPlan(
            languages=["en"],
            isbn_candidates=[],
            author="Test Author",
            title_variants=[ReleaseSearchVariant(title="Test Book", author="Test Author")],
            grouped_title_variants=[],
        )

        results = source.search(book, plan, content_type="audiobook")

        # Should filter out "Unrelated Book Title" as it doesn't contain query words
        assert len(results) == 1
        assert results[0].title == "Test Book by Test Author"

    @patch("shelfmark.release_sources.audiobookbay.source.scraper.search_audiobookbay")
    def test_search_relevance_filtering_spans_typographic_punctuation(self, mock_search):
        """Test an ASCII query still matches the typographic title ABB renders."""
        mock_search.return_value = [
            {
                "title": "The Stranger’s Wife — Anna‑Lou Weatherley",
                "link": "https://audiobookbay.lu/abss/the-strangers-wife/",
                "format": "M4B",
                "size": "259 MB",
                "language": "English",
            },
        ]

        source = AudiobookBaySource()
        book = BookMetadata(
            provider="test",
            provider_id="123",
            title="Stranger's",
            authors=["Anna-Lou Weatherley"],
        )
        # Every query word carries punctuation, so the result survives only when
        # both sides of the comparison are normalized.
        plan = ReleaseSearchPlan(
            languages=["en"],
            isbn_candidates=[],
            author="",
            title_variants=[ReleaseSearchVariant(title="Stranger's", author="")],
            grouped_title_variants=[],
        )

        results = source.search(book, plan, content_type="audiobook")

        assert len(results) == 1
        # The release keeps the title as ABB rendered it; only matching normalizes.
        assert results[0].title == "The Stranger’s Wife — Anna‑Lou Weatherley"

    @patch("shelfmark.release_sources.audiobookbay.source.scraper.search_audiobookbay")
    def test_search_result_mapping(self, mock_search):
        """Test conversion of scraper results to Release objects."""
        mock_search.return_value = [
            {
                "title": "Test Book - Test Author",
                "link": "https://audiobookbay.lu/abss/test-book/",
                "format": "M4B",
                "size": "500.00 MBs",
                "language": "English",
                "bitrate": "128 Kbps",
                "posted_date": "01 Jan 2024",
                "cover": "https://example.com/cover.jpg",
            },
        ]

        source = AudiobookBaySource()
        book = BookMetadata(
            provider="test",
            provider_id="123",
            title="Test Book",
            authors=["Test Author"],
        )
        plan = ReleaseSearchPlan(
            languages=["en"],
            isbn_candidates=[],
            author="Test Author",
            title_variants=[ReleaseSearchVariant(title="Test Book", author="Test Author")],
            grouped_title_variants=[],
        )

        results = source.search(book, plan, content_type="audiobook")

        assert len(results) == 1
        release = results[0]
        assert release.source == "audiobookbay"
        assert release.title == "Test Book"
        assert release.format == "m4b"
        assert release.language == "en"
        assert release.size == "500.00 MBs"
        assert release.download_url == "https://audiobookbay.lu/abss/test-book/"
        assert release.info_url == "https://audiobookbay.lu/abss/test-book/"
        assert release.protocol.value == "torrent"
        assert release.indexer == "AudiobookBay"
        assert release.content_type == "audiobook"
        assert release.extra["preview"] == "https://example.com/cover.jpg"
        assert release.extra["detail_url"] == "https://audiobookbay.lu/abss/test-book/"
        assert release.extra["bitrate"] == "128 Kbps"
        assert release.extra["bitrate_value"] == 128
        assert release.extra["posted_date"] == "01 Jan 2024"
        assert release.extra["title_raw"] == "Test Book - Test Author"
        assert release.extra["author"] == "Test Author"
        assert release.extra["language_raw"] == "English"

    def test_split_title_and_author(self):
        """Test title/author parsing from ABB title patterns."""
        assert _split_title_and_author("Book Title - Author Name") == ("Book Title", "Author Name")
        assert _split_title_and_author("Book Title - Author Name - Narrator") == (
            "Book Title - Author Name",
            "Narrator",
        )
        assert _split_title_and_author("Book Title") == ("Book Title", None)
        assert _split_title_and_author("  Book Title - Author Name  ") == (
            "Book Title",
            "Author Name",
        )

    @patch("shelfmark.release_sources.audiobookbay.source.scraper.search_audiobookbay")
    def test_search_handles_scraper_exception(self, mock_search):
        """Test that scraper exceptions are handled gracefully."""
        mock_search.side_effect = Exception("Scraper error")

        source = AudiobookBaySource()
        book = BookMetadata(
            provider="test",
            provider_id="123",
            title="Test Book",
            authors=["Test Author"],
        )
        plan = ReleaseSearchPlan(
            languages=["en"],
            isbn_candidates=[],
            author="Test Author",
            title_variants=[ReleaseSearchVariant(title="Test Book", author="Test Author")],
            grouped_title_variants=[],
        )

        results = source.search(book, plan, content_type="audiobook")

        assert results == []

    @patch("shelfmark.release_sources.audiobookbay.source.scraper.search_audiobookbay")
    def test_search_handles_invalid_result(self, mock_search):
        """Test that invalid results are skipped."""
        mock_search.return_value = [
            {
                "title": "Relevant Book",
                "link": "https://audiobookbay.lu/abss/valid/",
                "format": "M4B",
            },
            {
                # Missing required fields
                "title": "Relevant But Invalid",
            },
        ]

        source = AudiobookBaySource()
        book = BookMetadata(
            provider="test",
            provider_id="123",
            title="Relevant",
            authors=["Author"],
        )
        plan = ReleaseSearchPlan(
            languages=["en"],
            isbn_candidates=[],
            author="Author",
            title_variants=[ReleaseSearchVariant(title="Relevant", author="Author")],
            grouped_title_variants=[],
        )

        results = source.search(book, plan, content_type="audiobook")

        # Should only include valid result
        assert len(results) == 1
        assert results[0].title == "Relevant Book"

    @patch("shelfmark.release_sources.audiobookbay.source.scraper.search_audiobookbay")
    def test_search_config_hostname(self, mock_search, monkeypatch):
        """Test that custom hostname is used from config."""
        mock_search.return_value = []

        def mock_get(key, default=None):
            if key == "ABB_HOSTNAME":
                return "audiobookbay.is"
            if key == "ABB_PAGE_LIMIT":
                return 3
            return default

        import shelfmark.release_sources.audiobookbay.source as source_module

        monkeypatch.setattr(source_module.config, "get", mock_get)

        source = AudiobookBaySource()
        book = BookMetadata(
            provider="test",
            provider_id="123",
            title="Test Book",
            authors=["Test Author"],
        )
        plan = ReleaseSearchPlan(
            languages=["en"],
            isbn_candidates=[],
            author="Test Author",
            title_variants=[ReleaseSearchVariant(title="Test Book", author="Test Author")],
            grouped_title_variants=[],
        )

        source.search(book, plan, content_type="audiobook")

        call_args = mock_search.call_args
        assert call_args.kwargs["hostname"] == "audiobookbay.is"
        assert call_args.kwargs["max_pages"] == 3

    @patch("shelfmark.release_sources.audiobookbay.source.scraper.search_audiobookbay")
    def test_search_exact_phrase_setting_forwarded(self, mock_search, monkeypatch):
        """Test that exact phrase setting is forwarded to scraper search."""
        mock_search.return_value = [
            {
                "title": "Test Book - Test Author",
                "link": "https://audiobookbay.lu/abss/test-book/",
                "cover": None,
                "language": "English",
                "format": "M4B",
                "bitrate": "128 Kbps",
                "size": "500 MB",
                "posted_date": "01 Jan 2024",
            }
        ]

        def mock_get(key, default=None):
            if key == "ABB_HOSTNAME":
                return "audiobookbay.lu"
            if key == "ABB_EXACT_PHRASE":
                return True
            return default

        import shelfmark.release_sources.audiobookbay.source as source_module

        monkeypatch.setattr(source_module.config, "get", mock_get)

        source = AudiobookBaySource()
        book = BookMetadata(
            provider="test",
            provider_id="123",
            title="Test Book",
            authors=["Test Author"],
        )
        plan = ReleaseSearchPlan(
            languages=["en"],
            isbn_candidates=[],
            author="Test Author",
            title_variants=[ReleaseSearchVariant(title="Test Book", author="Test Author")],
            grouped_title_variants=[],
        )

        source.search(book, plan, content_type="audiobook")

        call_args = mock_search.call_args_list[0]
        assert call_args.kwargs["exact_phrase"] is True

    @patch("shelfmark.release_sources.audiobookbay.source.scraper.search_audiobookbay")
    def test_search_falls_back_to_broad_when_exact_finds_no_results(self, mock_search, monkeypatch):
        """Test fallback to broad search when exact phrase search has no results."""
        mock_search.side_effect = [
            [],
            [
                {
                    "title": "Test Book - Test Author",
                    "link": "https://audiobookbay.lu/abss/test-book/",
                    "cover": None,
                    "language": "English",
                    "format": "M4B",
                    "bitrate": "128 Kbps",
                    "size": "500 MB",
                    "posted_date": "01 Jan 2024",
                }
            ],
        ]

        def mock_get(key, default=None):
            if key == "ABB_HOSTNAME":
                return "audiobookbay.lu"
            if key == "ABB_EXACT_PHRASE":
                return True
            return default

        import shelfmark.release_sources.audiobookbay.source as source_module

        monkeypatch.setattr(source_module.config, "get", mock_get)

        source = AudiobookBaySource()
        book = BookMetadata(
            provider="test",
            provider_id="123",
            title="Test Book",
            authors=["Test Author"],
        )
        plan = ReleaseSearchPlan(
            languages=["en"],
            isbn_candidates=[],
            author="Test Author",
            title_variants=[ReleaseSearchVariant(title="Test Book", author="Test Author")],
            grouped_title_variants=[],
        )

        results = source.search(book, plan, content_type="audiobook")

        assert len(results) == 1
        assert mock_search.call_count == 2
        first_call = mock_search.call_args_list[0].kwargs
        second_call = mock_search.call_args_list[1].kwargs
        assert first_call["exact_phrase"] is True
        assert second_call["exact_phrase"] is False

    @staticmethod
    def _row(title: str, slug: str) -> dict[str, str]:
        return {"title": title, "link": f"https://audiobookbay.lu/abss/{slug}/", "format": "MP3"}

    @staticmethod
    def _lost_tales_search():
        book = BookMetadata(
            provider="test",
            provider_id="341",
            title="The Book of Lost Tales, Part II",
            authors=["J.R.R. Tolkien"],
        )
        plan = ReleaseSearchPlan(
            languages=["en"],
            isbn_candidates=[],
            author="Tolkien",
            title_variants=[
                ReleaseSearchVariant(title="The Book of Lost Tales, Part II", author="Tolkien")
            ],
            grouped_title_variants=[],
        )
        return book, plan

    @patch("shelfmark.release_sources.audiobookbay.source.scraper.search_audiobookbay")
    def test_search_drops_a_trailing_part_when_only_packs_come_back(self, mock_search):
        """ "Part II" brings back only packs whose descriptions list the book; the volume's
        own post ("Part Two") comes back once the part is dropped from the query."""
        packs = [
            self._row("Lord of the Rings (Complete Tolkien works) - J.R.R Tolkien", "lotr"),
            self._row("Entire Audiobooks Collection - Various", "all"),
        ]
        volumes = [
            self._row("The Book of Lost Tales, Part Two - J.R.R. Tolkien", "lt2"),
            self._row("The Book of Lost Tales, Part One - J.R.R. Tolkien", "lt1"),
            packs[0],
        ]
        mock_search.side_effect = [packs, packs, volumes]

        book, plan = self._lost_tales_search()
        results = AudiobookBaySource().search(book, plan, content_type="audiobook")

        queries = [call.kwargs["query"] for call in mock_search.call_args_list]
        assert queries == [
            "the book of lost tales, part ii tolkien",
            "the book of lost tales, part ii",
            "the book of lost tales tolkien",
        ]
        titles = [release.title for release in results]
        # Part One comes back too; the strict matcher is what tells the parts apart.
        assert "The Book of Lost Tales, Part Two" in titles
        assert "The Book of Lost Tales, Part One" in titles
        # A row returned by more than one query is kept once.
        assert len({release.source_id for release in results}) == len(results)

    @patch("shelfmark.release_sources.audiobookbay.source.scraper.search_audiobookbay")
    def test_search_stops_at_the_first_query_whose_rows_hold_the_title(self, mock_search):
        mock_search.return_value = [
            self._row("The Book of Lost Tales, Part 2 - J.R.R. Tolkien", "lt2"),
        ]

        book, plan = self._lost_tales_search()
        results = AudiobookBaySource().search(book, plan, content_type="audiobook")

        assert mock_search.call_count == 1
        assert [release.title for release in results] == ["The Book of Lost Tales, Part 2"]

    @patch("shelfmark.release_sources.audiobookbay.source.scraper.search_audiobookbay")
    def test_search_adds_no_part_query_for_a_title_without_one(self, mock_search):
        mock_search.return_value = []

        book = BookMetadata(
            provider="test", provider_id="1", title="The Jungle Book 2", authors=["Someone"]
        )
        plan = ReleaseSearchPlan(
            languages=["en"],
            isbn_candidates=[],
            author="Someone",
            title_variants=[ReleaseSearchVariant(title="The Jungle Book 2", author="Someone")],
            grouped_title_variants=[],
        )
        AudiobookBaySource().search(book, plan, content_type="audiobook")

        queries = [call.kwargs["query"] for call in mock_search.call_args_list]
        assert queries == ["the jungle book 2 someone", "the jungle book 2"]

    def test_get_column_config(self):
        """Test column configuration."""
        source = AudiobookBaySource()
        config = source.get_column_config()

        assert config is not None
        assert len(config.columns) == 4
        column_keys = [col.key for col in config.columns]
        assert "language" in column_keys
        assert "format" in column_keys
        assert "extra.bitrate" in column_keys
        assert "size" in column_keys
        assert "seeders" not in column_keys  # ABB doesn't show seeders
        assert config.supported_filters == ["format", "language"]

        bitrate_col = next(col for col in config.columns if col.key == "extra.bitrate")
        assert bitrate_col.sortable is True
        assert bitrate_col.sort_key == "extra.bitrate_value"

        size_col = next(col for col in config.columns if col.key == "size")
        assert size_col.sortable is True
        assert size_col.sort_key == "size_bytes"
