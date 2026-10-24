import pytest

from shelfmark.core.config import config
from shelfmark.core.utils import ARCHIVE_FORMATS, AUDIOBOOK_FORMATS
from shelfmark.release_sources.irc import parser

# What a stock install actually filters with, so these tests fail if the defaults regress.
_DEFAULT_CONFIG = {
    "SUPPORTED_FORMATS": ["epub", "mobi", "azw3", "fb2", "djvu", "cbz", "cbr"],
    "SUPPORTED_AUDIOBOOK_FORMATS": [*AUDIOBOOK_FORMATS, *ARCHIVE_FORMATS],
}


@pytest.fixture
def default_formats(monkeypatch):
    """Filter with the shipped default format lists."""
    monkeypatch.setattr(config, "get", lambda key, default=None: _DEFAULT_CONFIG.get(key, default))


def test_audiobook_archives_are_found_with_default_settings(default_formats):
    """Regression for #1129: audiobooks ship as .rar/.zip and were dropped by both buckets.

    A single "@search" answers with one file holding every format. These are the lines an
    audiobook actually occupies in it - the extension is a container, and the release name
    is the only thing saying what is inside.
    """
    content = "\n".join(
        [
            "!Oatmeal Andy Weir - Project Hail Mary (Audiobook) [MP3 64kbps].rar ::INFO:: 620.5MB",
            "!DV8 Andy Weir - Project Hail Mary - Audiobook.zip ::INFO:: 700MB",
            "!Horla Andy Weir - Project Hail Mary [Unabridged].m4b ::INFO:: 850.1MB",
        ]
    )

    results = parser.parse_results_file(content, content_type="audiobook")

    assert [result.format for result in results] == ["rar", "zip", "m4b"]


def test_ebook_archive_does_not_leak_into_audiobook_results(default_formats):
    """An ebook .rar must not be offered as an audiobook just because it is an archive."""
    content = "!bald Andy Weir - Project Hail Mary (retail).rar ::INFO:: 2.1MB"

    assert parser.parse_results_file(content, content_type="audiobook") == []


def test_flac_audiobook_is_reachable_with_default_settings(default_formats):
    """FLAC was recognized by the parser and ranked by the sorter, but never selectable."""
    content = "!Ook Andy Weir - Project Hail Mary.flac ::INFO:: 1.1GB"

    results = parser.parse_results_file(content, content_type="audiobook")

    assert [result.format for result in results] == ["flac"]


def test_decimal_size_is_not_mistaken_for_a_file_extension():
    """A line with no extension used to parse as format="5mb" out of "::INFO:: 620.5MB"."""
    line = "!Ook Andy Weir - Project Hail Mary (2021) Audiobook ::INFO:: 620.5MB"

    result = parser.parse_result_line(line)

    assert result.format == "unknown"
    assert result.title == "Project Hail Mary (2021) Audiobook"
    assert result.size == "620.5MB"


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("!s A - T.epub ::INFO:: 1MB", "ebook"),
        ("!s A - T.mp3 ::INFO:: 1MB", "audiobook"),
        ("!s A - T.flac ::INFO:: 1MB", "audiobook"),
        # Archives carry no format information, so the name has to decide.
        ("!s A - T (Audiobook).rar ::INFO:: 1MB", "audiobook"),
        ("!s A - T [Unabridged].zip ::INFO:: 1MB", "audiobook"),
        ("!s A - T (Narrated by Someone).rar ::INFO:: 1MB", "audiobook"),
        ("!s A - T [64kbps].zip ::INFO:: 1MB", "audiobook"),
        ("!s A - T (retail).rar ::INFO:: 1MB", "ebook"),
        ("!s A - T.zip ::INFO:: 1MB", "ebook"),
    ],
)
def test_detect_content_type(line, expected):
    assert parser.detect_content_type(parser.parse_result_line(line)) == expected


def test_recognized_formats_order_is_deterministic():
    """This was a set, so which format won for a multi-extension line varied per restart."""
    assert parser.ALL_RECOGNIZED_FORMATS == tuple(parser.ALL_RECOGNIZED_FORMATS)
    # Longest-first, so ".azw3" cannot be truncated to "azw" (nor ".docx" to "doc").
    lengths = [len(fmt) for fmt in parser.ALL_RECOGNIZED_FORMATS]
    assert lengths == sorted(lengths, reverse=True)


def test_parse_results_file_uses_audiobook_format_settings(monkeypatch):
    values = {
        "SUPPORTED_FORMATS": ["epub"],
        "SUPPORTED_AUDIOBOOK_FORMATS": ["zip", "mp3"],
    }

    monkeypatch.setattr(config, "get", lambda key, default=None: values.get(key, default))

    content = "\n".join(
        [
            "!AudioBot Author Name - Great Audio Book.zip ::INFO:: 1.2GB",
            "!AudioBot Author Name - Great Audio Book.mp3 ::INFO:: 1.1GB",
            "!AudioBot Author Name - Great Audio Book.epub ::INFO:: 5MB",
        ]
    )

    results = parser.parse_results_file(content, content_type="audiobook")

    assert [result.format for result in results] == ["zip", "mp3"]


def test_parse_results_file_uses_book_format_settings_for_ebooks(monkeypatch):
    values = {
        "SUPPORTED_FORMATS": ["epub"],
        "SUPPORTED_AUDIOBOOK_FORMATS": ["zip", "mp3"],
    }

    monkeypatch.setattr(config, "get", lambda key, default=None: values.get(key, default))

    content = "\n".join(
        [
            "!BookBot Author Name - Great Book.zip ::INFO:: 50MB",
            "!BookBot Author Name - Great Book.epub ::INFO:: 5MB",
        ]
    )

    results = parser.parse_results_file(content, content_type="ebook")

    assert [result.format for result in results] == ["epub"]
