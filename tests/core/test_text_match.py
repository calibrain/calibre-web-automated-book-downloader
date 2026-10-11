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


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("The Children of Húrin", ["the", "children", "of", "hurin"]),
        # The same title already decomposed (u + combining acute), as a scraped page has it.
        ("The Children of Húrin", ["the", "children", "of", "hurin"]),
        ("The Fall of Númenor", ["the", "fall", "of", "numenor"]),
        ("Beren and Lúthien", ["beren", "and", "luthien"]),
        ("Jo Nesbø", ["jo", "nesbo"]),
        ("Straße", ["strasse"]),
        ("Æsop's Fables", ["aesop", "s", "fables"]),
        ("Gabriel García Márquez", ["gabriel", "garcia", "marquez"]),
    ],
)
def test_tokens_fold_diacritics(text, expected):
    assert text_match.tokens(text) == expected


def test_fold_leaves_plain_ascii_alone():
    assert text_match.fold("Dungeon Crawler Carl: Book 1!") == "dungeon crawler carl: book 1!"


@pytest.mark.parametrize(
    ("title", "haystack"),
    [
        ("The Fall of Númenor", "The Fall of Numenor - J.R.R. Tolkien, Brian Sibley (editor)"),
        ("The Children of Hurin", "The Children of Húrin - J. R. R. Tolkien"),
        ("The Children of Húrin", "The Children of Hurin - J.R.R. Tolkien"),
    ],
)
def test_title_tokens_match_does_not_care_about_accents(title, haystack):
    assert text_match.title_tokens_match(title, set(text_match.tokens(haystack))) is True


def test_author_surname_folds_accents():
    assert text_match.author_surname("Jo Nesbø") == "nesbo"
    assert text_match.author_surname("Márquez, Gabriel García") == "marquez"


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("The Hobbit, or There and Back Again", "The Hobbit"),
        ("The Mummy, or Ramses the Damned", "The Mummy"),
        ("Moby-Dick; or, The Whale", "Moby-Dick"),
        ("Frankenstein; or, The Modern Prometheus", "Frankenstein"),
        ("Twelfth Night, Or What You Will", "Twelfth Night"),
        # No alternate title: "or" inside the title, or nothing before the comma.
        ("Dead or Alive", "Dead or Alive"),
        ("Deal or No Deal", "Deal or No Deal"),
        # Nothing worth searching before the separator.
        ("A, or The Whale", "A, or The Whale"),
        (", or The Whale", ", or The Whale"),
        ("The Hobbit", "The Hobbit"),
        ("", ""),
    ],
)
def test_strip_alternate_title(title, expected):
    assert text_match.strip_alternate_title(title) == expected


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


@pytest.mark.parametrize(
    ("title", "search", "expected"),
    [
        ("Jack Reacher 1-28 + Short Stories - Complete to date", "Killing Floor", True),
        ("Dungeon Crawler Carl Books 1-6", "Dungeon Crawler Carl", True),
        ("The Expanse (#1-9)", "Leviathan Wakes", True),
        ("All 7 Audiobooks - J.K. Rowling", "Harry Potter", True),
        ("Foundation Trilogy - Isaac Asimov", "Foundation", True),
        ("Dune Megapack", "Dune", True),
        ("Dune - Frank Herbert (Complete and Unabridged) [m4b]", "Dune", False),
        ("Dune - Frank Herbert [m4b]", "Dune", False),
        ("The Complete Maus - Art Spiegelman", "The Complete Maus", False),
        ("Dune (Books 1-6)", "Dune Books 1-6", False),
    ],
)
def test_is_bundle_title(title, search, expected) -> None:
    assert text_match.is_bundle_title(title, search) is expected
