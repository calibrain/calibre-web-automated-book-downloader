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


def test_significant_tokens_drops_stopwords_and_single_letters_but_keeps_numbers():
    assert text_match.significant_tokens("The Way of Kings, Part A 2") == [
        "way",
        "kings",
        "part",
        "2",
    ]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("The Book of Lost Tales, Part II", ["the", "book", "of", "lost", "tales", "part", "2"]),
        ("The Book of Lost Tales, Part Two", ["the", "book", "of", "lost", "tales", "part", "2"]),
        ("The Book of Lost Tales, Part 02", ["the", "book", "of", "lost", "tales", "part", "2"]),
        ("Ready Player One", ["ready", "player", "1"]),
        ("Henry V", ["henry", "5"]),
        ("Station Eleven", ["station", "11"]),
        # Out of range or not a number: left as written.
        ("Catch-22", ["catch", "22"]),
        ("Fahrenheit 451", ["fahrenheit", "451"]),
        ("Part XXI", ["part", "xxi"]),
        ("Alex Cross 007", ["alex", "cross", "007"]),
    ],
)
def test_numeral_tokens_write_small_numbers_as_digits(text, expected):
    assert text_match.numeral_tokens(text) == expected


def test_tokens_keep_numbers_as_written():
    assert text_match.tokens("Book Three, Part II") == ["book", "three", "part", "ii"]


@pytest.mark.parametrize(
    ("shelf", "search", "expected"),
    [
        # A number word the title does not carry still names another work on a shelf.
        ("Dungeon Crawler Carl Book Three", "Dungeon Crawler Carl", {"three"}),
        ("Dune Two", "Dune", {"two"}),
        # The same number in another form is the title's own.
        ("The Book of Lost Tales, Part Two", "The Book of Lost Tales, Part II", set()),
        # Digits are volume numbers, conceded as before.
        ("Dungeon Crawler Carl 3", "Dungeon Crawler Carl", set()),
    ],
)
def test_extra_work_tokens_reads_number_words_in_any_form(shelf, search, expected):
    shelf_tokens = set(text_match.tokens(shelf))
    assert text_match.extra_work_tokens(shelf_tokens, search) == expected


@pytest.mark.parametrize(
    ("title", "haystack", "expected"),
    [
        (
            "The Book of Lost Tales, Part II",
            "The Book of Lost Tales, Part Two - J.R.R. Tolkien",
            True,
        ),
        (
            "The Book of Lost Tales, Part II",
            "The Book of Lost Tales, Part 2 - J.R.R. Tolkien",
            True,
        ),
        (
            "The Book of Lost Tales, Part II",
            "The Book of Lost Tales, Part One - J.R.R. Tolkien",
            False,
        ),
        ("The Book of Lost Tales, Part II", "The Book of Lost Tales - J.R.R. Tolkien", False),
        ("Ready Player One", "Ready Player Two - Ernest Cline", False),
        ("Ready Player Two", "Ready Player One - Ernest Cline", False),
    ],
)
def test_title_tokens_match_treats_numeral_forms_alike_but_not_different_numbers(
    title, haystack, expected
):
    assert text_match.title_tokens_match(title, set(text_match.tokens(haystack))) is expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("The Book of Lost Tales, Part II", "The Book of Lost Tales, Part 2"),
        ("The Book of Lost Tales, Part Two", "The Book of Lost Tales, Part 2"),
        ("Ready Player One", "Ready Player 1"),
        ("Rocky IV", "Rocky 4"),
        # Single letters stay words.
        ("What I Loved", "What I Loved"),
        ("Henry V", "Henry V"),
        ("Malcolm X", "Malcolm X"),
        ("", ""),
    ],
)
def test_normalize_numerals(text, expected):
    assert text_match.normalize_numerals(text) == expected


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("The Book of Lost Tales, Part II", "The Book of Lost Tales"),
        ("The Book of Lost Tales, Part Two", "The Book of Lost Tales"),
        ("The Book of Lost Tales Part 2", "The Book of Lost Tales"),
        ("Berserk, Vol. 3", "Berserk"),
        ("Don Quixote: Volume I", "Don Quixote"),
        ("The Jungle Book 2", "The Jungle Book 2"),
        ("Part of Your World", "Part of Your World"),
        ("Morgoth's Ring", "Morgoth's Ring"),
        ("Part 2", "Part 2"),
        ("", ""),
    ],
)
def test_strip_part_suffix(title, expected):
    assert text_match.strip_part_suffix(title) == expected


def test_author_surname_skips_a_generational_suffix():
    assert text_match.author_surname("Henry Louis Gates III") == "gates"
    assert text_match.author_surname("50 Cent") == "cent"


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


def test_significant_tokens_match_agrees_with_title_tokens_match():
    haystack = {"alpha", "beta", "gamma"}
    for title in ("Alpha Beta", "alpha delta", "the of", ""):
        assert text_match.significant_tokens_match(
            text_match.significant_tokens(title), haystack
        ) is text_match.title_tokens_match(title, haystack)
