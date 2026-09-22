"""Tests for the shared token/ISBN/surname matching helpers."""

import pytest

from shelfmark.core import text_match

_TWENTY_WORDS = " ".join(f"word{i:02d}" for i in range(20))


def test_tokens_lowercases_and_splits_on_non_alphanumerics():
    assert text_match.tokens("Dungeon Crawler Carl: Book 1!") == [
        "dungeon",
        "crawler",
        "carl",
        "book",
        "1",
    ]


@pytest.mark.parametrize("value", [None, "", "  ---  "])
def test_tokens_empty_input(value):
    assert text_match.tokens(value) == []


def test_significant_tokens_drops_stopwords_and_single_characters():
    assert text_match.significant_tokens("The Way of Kings, Part A 2") == ["way", "kings", "part"]


@pytest.mark.parametrize(
    ("author", "expected"),
    [
        ("Sanderson, Brandon", "sanderson"),
        ("Brandon Sanderson", "sanderson"),
        ("Dinniman, Matt J.", "dinniman"),
        ("", None),
        (None, None),
        ("A", None),
    ],
)
def test_author_surname(author, expected):
    assert text_match.author_surname(author) == expected


def test_title_tokens_match_default_threshold_edges():
    words = _TWENTY_WORDS.split()

    assert text_match.title_tokens_match(_TWENTY_WORDS, set(words[:17])) is True  # 17/20 == 0.85
    assert text_match.title_tokens_match(_TWENTY_WORDS, set(words[:16])) is False


def test_title_tokens_match_explicit_threshold_is_inclusive():
    assert text_match.title_tokens_match("alpha beta", {"alpha"}, threshold=0.5) is True
    assert text_match.title_tokens_match("alpha beta", {"alpha"}, threshold=0.51) is False


def test_title_tokens_match_ignores_stopwords_in_title():
    assert text_match.title_tokens_match("The Name of the Wind", {"name", "wind"}) is True


@pytest.mark.parametrize("title", [None, "", "the of a"])
def test_title_tokens_match_without_significant_tokens_is_false(title):
    assert text_match.title_tokens_match(title, {"the", "of"}) is False


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("978-0-59-382024-7", "9780593820247"),
        ("0-306-40615-x", "030640615X"),
        (" 0306406152 ", "0306406152"),
        (9780593820247, "9780593820247"),
        (None, ""),
        ("", ""),
    ],
)
def test_normalize_isbn(value, expected):
    assert text_match.normalize_isbn(value) == expected


@pytest.mark.parametrize(
    ("title", "search", "expected"),
    [
        ("Dune Omnibus", "Dune", {"omnibus"}),
        ("The Complete Maus", "The Complete Maus", set()),
        ("Foundation Trilogy", "Foundation", {"trilogy"}),
        ("Dune", "Dune", set()),
    ],
)
def test_bundle_markers_ignore_words_the_search_title_carries(title, search, expected) -> None:
    assert text_match.bundle_markers(set(text_match.tokens(title)), search) == expected
