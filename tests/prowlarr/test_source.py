"""
Tests for the Prowlarr source module.

Tests the utility functions for parsing release metadata.
"""

import pytest

# Import the functions to test
from cwa_book_downloader.release_sources.prowlarr.source import (
    _parse_size,
    _extract_format,
    _get_protocol,
    _extract_language,
)


class TestParseSize:
    """Tests for the _parse_size function."""

    def test_parse_size_bytes(self):
        """Test parsing small byte sizes."""
        assert _parse_size(100) == "100 B"
        assert _parse_size(512) == "512 B"

    def test_parse_size_kilobytes(self):
        """Test parsing kilobyte sizes."""
        assert _parse_size(1024) == "1.0 KB"
        assert _parse_size(2048) == "2.0 KB"
        assert _parse_size(1536) == "1.5 KB"

    def test_parse_size_megabytes(self):
        """Test parsing megabyte sizes."""
        assert _parse_size(1048576) == "1.0 MB"
        assert _parse_size(5242880) == "5.0 MB"
        assert _parse_size(1572864) == "1.5 MB"

    def test_parse_size_gigabytes(self):
        """Test parsing gigabyte sizes."""
        assert _parse_size(1073741824) == "1.0 GB"
        assert _parse_size(2147483648) == "2.0 GB"

    def test_parse_size_terabytes(self):
        """Test parsing terabyte sizes."""
        assert _parse_size(1099511627776) == "1.0 TB"

    def test_parse_size_none(self):
        """Test that None returns None."""
        assert _parse_size(None) is None

    def test_parse_size_zero(self):
        """Test that zero returns None."""
        assert _parse_size(0) is None

    def test_parse_size_negative(self):
        """Test that negative values return None."""
        assert _parse_size(-100) is None


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


class TestGetProtocol:
    """Tests for the _get_protocol function."""

    def test_get_protocol_from_protocol_field_torrent(self):
        """Test extracting torrent protocol from protocol field."""
        result = {"protocol": "torrent", "downloadUrl": "https://example.com"}
        assert _get_protocol(result) == "torrent"

    def test_get_protocol_from_protocol_field_usenet(self):
        """Test extracting usenet protocol from protocol field."""
        result = {"protocol": "usenet", "downloadUrl": "https://example.com"}
        assert _get_protocol(result) == "nzb"

    def test_get_protocol_from_magnet_url(self):
        """Test inferring torrent from magnet URL."""
        result = {"downloadUrl": "magnet:?xt=urn:btih:abc123"}
        assert _get_protocol(result) == "torrent"

    def test_get_protocol_from_torrent_url(self):
        """Test inferring torrent from .torrent URL."""
        result = {"downloadUrl": "https://example.com/file.torrent"}
        assert _get_protocol(result) == "torrent"

    def test_get_protocol_from_nzb_url(self):
        """Test inferring NZB from .nzb URL."""
        result = {"downloadUrl": "https://example.com/file.nzb"}
        assert _get_protocol(result) == "nzb"

    def test_get_protocol_fallback_to_magnet_url(self):
        """Test fallback to magnetUrl field."""
        result = {"magnetUrl": "magnet:?xt=urn:btih:abc123"}
        assert _get_protocol(result) == "torrent"

    def test_get_protocol_unknown(self):
        """Test unknown protocol for unclear URLs."""
        result = {"downloadUrl": "https://example.com/download"}
        assert _get_protocol(result) == "unknown"

    def test_get_protocol_case_insensitive(self):
        """Test protocol detection is case insensitive."""
        result = {"protocol": "TORRENT"}
        assert _get_protocol(result) == "torrent"

        result = {"protocol": "Usenet"}
        assert _get_protocol(result) == "nzb"


class TestExtractLanguage:
    """Tests for the _extract_language function."""

    def test_extract_language_english(self):
        """Test extracting English language."""
        assert _extract_language("The Book [English]") == "en"
        assert _extract_language("Book (eng)") == "en"
        assert _extract_language("Book [EN]") == "en"

    def test_extract_language_german(self):
        """Test extracting German language."""
        assert _extract_language("Das Buch [German]") == "de"
        assert _extract_language("Buch (Deutsch)") == "de"
        assert _extract_language("Buch [DE]") == "de"

    def test_extract_language_french(self):
        """Test extracting French language."""
        assert _extract_language("Le Livre [French]") == "fr"
        assert _extract_language("Livre (Français)") == "fr"
        assert _extract_language("Livre [FR]") == "fr"

    def test_extract_language_spanish(self):
        """Test extracting Spanish language."""
        assert _extract_language("El Libro [Spanish]") == "es"
        assert _extract_language("Libro (Español)") == "es"
        assert _extract_language("Libro [ES]") == "es"

    def test_extract_language_italian(self):
        """Test extracting Italian language."""
        assert _extract_language("Il Libro [Italian]") == "it"
        assert _extract_language("Libro (Italiano)") == "it"

    def test_extract_language_russian(self):
        """Test extracting Russian language."""
        assert _extract_language("Book [Russian]") == "ru"
        assert _extract_language("Book [RU]") == "ru"

    def test_extract_language_japanese(self):
        """Test extracting Japanese language."""
        assert _extract_language("Book [Japanese]") == "ja"
        assert _extract_language("Book [JA]") == "ja"

    def test_extract_language_chinese(self):
        """Test extracting Chinese language."""
        assert _extract_language("Book [Chinese]") == "zh"
        assert _extract_language("Book [ZH]") == "zh"

    def test_extract_language_none_when_not_found(self):
        """Test that None is returned when no language found."""
        assert _extract_language("The Book by Author") is None
        assert _extract_language("") is None

    def test_extract_language_case_insensitive(self):
        """Test that language extraction is case insensitive."""
        assert _extract_language("Book [GERMAN]") == "de"
        assert _extract_language("Book [german]") == "de"
        assert _extract_language("Book [German]") == "de"
