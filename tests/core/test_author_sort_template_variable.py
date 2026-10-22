"""Tests for the {AuthorSort} template variable support (issue #1178)."""

import pytest

from shelfmark.core.naming import KNOWN_TOKENS, author_sort, parse_naming_template


class TestAuthorSortInKnownTokens:
    def test_author_sort_in_known_tokens(self):
        assert "authorsort" in KNOWN_TOKENS

    def test_author_sort_is_matched_before_author(self):
        # "authorsort" contains "author"; the longest-first ordering must keep
        # {AuthorSort} from being parsed as {Author} + literal "Sort".
        assert KNOWN_TOKENS.index("authorsort") < KNOWN_TOKENS.index("author")


class TestAuthorSortHelper:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("Arthur Conan Doyle", "Doyle, Arthur Conan"),
            ("Terry Pratchett", "Pratchett, Terry"),
            ("  Terry   Pratchett  ", "Pratchett, Terry"),
            ("Terry Pratchett, Neil Gaiman", "Pratchett, Terry"),
            ("Terry Pratchett; Neil Gaiman", "Pratchett, Terry"),
            ("Martin Luther King Jr.", "King, Martin Luther, Jr."),
            ("Martin Luther King, Jr.", "King, Martin Luther, Jr."),
            ("Plato", "Plato"),
            ("", ""),
            (None, ""),
        ],
    )
    def test_author_sort(self, value, expected):
        assert author_sort(value) == expected

    @pytest.mark.parametrize(
        "value",
        ["Doyle, Arthur Conan", "Pratchett, Terry"],
    )
    def test_name_already_in_last_first_form_is_kept(self, value):
        assert author_sort(value) == value

    def test_surname_particles_are_not_detected(self):
        # Documented limitation: the last word is treated as the surname.
        assert author_sort("Ursula K. Le Guin") == "Guin, Ursula K. Le"


class TestAuthorSortTemplateRendering:
    def test_renders_last_first(self):
        metadata = {"Author": "Arthur Conan Doyle", "Title": "A Study in Scarlet"}
        assert parse_naming_template("{AuthorSort}/{Title}", metadata) == (
            "Doyle, Arthur Conan/A Study in Scarlet"
        )

    def test_case_insensitive(self):
        assert parse_naming_template("{authorsort}", {"Author": "Terry Pratchett"}) == (
            "Pratchett, Terry"
        )

    def test_explicit_author_sort_key_wins_over_derivation(self):
        metadata = {"Author": "Terry Pratchett", "AuthorSort": "Custom"}
        assert parse_naming_template("{AuthorSort}", metadata) == "Custom"

    def test_empty_author_renders_nothing(self):
        assert parse_naming_template("{AuthorSort}", {"Author": ""}) == ""

    def test_author_token_is_unaffected(self):
        metadata = {"Author": "Terry Pratchett"}
        assert parse_naming_template("{Author}", metadata) == "Terry Pratchett"
