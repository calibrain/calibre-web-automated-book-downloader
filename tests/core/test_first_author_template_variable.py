"""Tests for the {FirstAuthor} template variable support (issue #930)."""

import pytest

from shelfmark.core.naming import KNOWN_TOKENS, first_author, parse_naming_template


class TestFirstAuthorInKnownTokens:
    def test_first_author_in_known_tokens(self):
        assert "firstauthor" in KNOWN_TOKENS

    def test_first_author_is_matched_before_author(self):
        # "firstauthor" contains "author"; the longest-first ordering must keep
        # {FirstAuthor} from being parsed as literal "First" + {Author}.
        assert KNOWN_TOKENS.index("firstauthor") < KNOWN_TOKENS.index("author")


class TestFirstAuthorHelper:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("Arthur Conan Doyle", "Arthur Conan Doyle"),
            ("Terry Pratchett, Neil Gaiman", "Terry Pratchett"),
            ("Terry Pratchett; Neil Gaiman", "Terry Pratchett"),
            ("  Ursula K. Le Guin ,  Someone Else ", "Ursula K. Le Guin"),
            ("", ""),
            (None, ""),
        ],
    )
    def test_first_author(self, value, expected):
        assert first_author(value) == expected

    def test_last_comma_first_is_split_too(self):
        # Documented limitation: a lone "Last, First" name is indistinguishable
        # from a two-author list, so it collapses to "Last" (see #930).
        assert first_author("Doyle, Arthur Conan") == "Doyle"


class TestFirstAuthorTemplateRendering:
    def test_single_author_matches_author(self):
        metadata = {"Author": "Arthur Conan Doyle", "Title": "A Study in Scarlet"}
        assert parse_naming_template("{FirstAuthor} - {Title}", metadata) == (
            "Arthur Conan Doyle - A Study in Scarlet"
        )

    def test_multiple_authors_keep_only_the_first(self):
        metadata = {"Author": "Terry Pratchett, Neil Gaiman", "Title": "Good Omens"}
        assert parse_naming_template("{FirstAuthor}/{Title}", metadata) == (
            "Terry Pratchett/Good Omens"
        )

    def test_case_insensitive(self):
        assert parse_naming_template("{firstauthor}", {"Author": "A, B"}) == "A"

    def test_explicit_first_author_key_wins_over_derivation(self):
        metadata = {"Author": "A, B", "FirstAuthor": "Custom"}
        assert parse_naming_template("{FirstAuthor}", metadata) == "Custom"

    def test_empty_author_renders_nothing(self):
        assert parse_naming_template("{FirstAuthor}", {"Author": ""}) == ""

    def test_author_token_is_unaffected(self):
        metadata = {"Author": "Terry Pratchett, Neil Gaiman"}
        assert parse_naming_template("{Author}", metadata) == "Terry Pratchett, Neil Gaiman"
