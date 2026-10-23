"""Shared text-normalization + fuzzy token-matching helpers for book matching.

Used by the library ownership check (``library_index``) and its providers, so every
library matches titles, authors and ISBNs the same way.
"""

from __future__ import annotations

import re
import unicodedata
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Collection

DEFAULT_TITLE_MATCH_THRESHOLD = 0.85

# Short/common words that add noise to title token matching.
STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "of",
        "and",
        "or",
        "to",
        "in",
        "on",
        "for",
        "with",
        "is",
        "by",
    }
)


# Letters NFKD leaves whole, folded by hand so "Nesbø" and "Straße" compare as "nesbo" and
# "strasse", which is how a release name or a shelf usually spells them.
_LETTER_FOLD = str.maketrans(
    {
        "ß": "ss",
        "æ": "ae",
        "œ": "oe",
        "ø": "o",
        "ł": "l",
        "đ": "d",
        "ð": "d",
        "þ": "th",
        "ı": "i",
    }
)


def fold(text: str) -> str:
    """Lowercase text with its diacritics dropped, so "Húrin" and "Hurin" read the same.

    Decomposes (NFKD) and discards the combining marks, which also covers a title that
    arrives already decomposed (a "u" followed by a combining acute). A metadata
    provider and a release name rarely agree on accents; without this, "The Children of
    Húrin" split into "hu" and "rin" and scored half a match against its own audiobook.
    """
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).translate(_LETTER_FOLD)


# Small numbers the way titles write them. "Part II", "Part Two" and "Part 2" name the
# same part, and a shelf, a provider and a release rarely agree on which to use.
_ROMAN = [
    "i",
    "ii",
    "iii",
    "iv",
    "v",
    "vi",
    "vii",
    "viii",
    "ix",
    "x",
    "xi",
    "xii",
    "xiii",
    "xiv",
    "xv",
    "xvi",
    "xvii",
    "xviii",
    "xix",
    "xx",
]
_NUMBER_WORDS = [
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
    "twenty",
]
NUMERALS: dict[str, str] = {
    **{roman: str(n) for n, roman in enumerate(_ROMAN, 1)},
    **{word: str(n) for n, word in enumerate(_NUMBER_WORDS, 1)},
}


def canonical_numeral(tok: str) -> str:
    """The digit form of a small number written as a word, a roman numeral or with a
    leading zero ("two", "ii", "02" all give "2"); any other token unchanged."""
    if tok.isdigit():
        return str(int(tok)) if len(tok) <= 2 else tok
    return NUMERALS.get(tok, tok)


def tokens(text: str | None) -> list[str]:
    """Lowercase alphanumeric tokens from arbitrary text, diacritics folded away."""
    if not text:
        return []
    return [tok for tok in re.split(r"[^a-z0-9]+", fold(text)) if tok]


def numeral_tokens(text: str | None) -> list[str]:
    """``tokens`` with small numbers in their digit form, for comparing titles.

    Kept apart from ``tokens`` because a shelf title's "Book Three" is a word that
    names another work to the library matcher, where "Book 3" is a volume number it
    deliberately lets pass (see ``extra_work_tokens``).
    """
    return [canonical_numeral(tok) for tok in tokens(text)]


def significant_tokens(text: str | None) -> list[str]:
    """Title words for comparison: stopwords and single letters removed, small numbers
    in their digit form.

    Numbers always count, however short: "Part 2" is not "Part 1".
    """
    return [
        tok
        for tok in numeral_tokens(text)
        if tok not in STOPWORDS and (len(tok) >= 2 or tok.isdigit())
    ]


def author_surname(author: str | None) -> str | None:
    """Return the most distinctive author token (the surname), or None.

    A generational suffix is skipped: "Henry Louis Gates III" is Gates.
    """
    value = author or ""
    if "," in value:  # "Last, First" -> keep the "Last" portion
        value = value.split(",")[0]
    toks = [tok for tok in tokens(value) if len(tok) >= 2 and tok not in STOPWORDS]
    words = [tok for tok in toks if not canonical_numeral(tok).isdigit()]
    return (words or toks)[-1] if toks else None


# Number words and roman numerals of two letters or more, as whole words. The single
# letters (I, V, X) are left out here: in a query they are words far more often than
# numbers ("What I Loved", "Malcolm X").
_NUMERAL_WORD = re.compile(
    r"\b(" + "|".join(sorted((k for k in NUMERALS if len(k) > 1), key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)


def normalize_numerals(text: str) -> str:
    """Text with its small numbers written as digits: "Part II" and "Part Two" become
    "Part 2". For queries; the token comparison already treats the forms alike."""
    return _NUMERAL_WORD.sub(lambda match: NUMERALS[match.group(1).lower()], text or "")


# ", Part II", ": Volume 3", " Vol. IV" at the end of a title. "Book" is deliberately not
# a part word: "The Jungle Book 2" is a title, not a title and a part.
_PART_SUFFIX = re.compile(
    r"^(?P<main>.+?)\s*[,:;]?\s+(?:part|pt\.?|volume|vol\.?)\s+(?:\d{1,2}|[ivx]{1,5}|[a-z]+)\.?\s*$",
    re.IGNORECASE,
)


def strip_part_suffix(title: str) -> str:
    """ "The Book of Lost Tales, Part II" without its ", Part II"; anything else unchanged.

    Only when what follows the part word is a small number in some form; "Part" followed
    by a name ("Part of Your World") is left alone.
    """
    match = _PART_SUFFIX.match(title or "")
    if not match:
        return title
    number = numeral_tokens(title[match.end("main") :])
    if not number or not number[-1].isdigit() or not significant_tokens(match.group("main")):
        return title
    return match.group("main")


# "The Hobbit, or There and Back Again", "Moby-Dick; or, The Whale": a main title, a
# comma or semicolon, "or", and the alternate title. "Dead or Alive" has no separator
# before the "or" and is left alone.
_ALTERNATE_TITLE = re.compile(r"^(?P<main>.+?)\s*[,;]\s*or\b[,:]?\s+(?P<alt>\S.*)$", re.IGNORECASE)


def strip_alternate_title(title: str) -> str:
    """The main title of a "Title, or Alternate Title" pair; anything else unchanged.

    Releases and shelves carry the main title alone far more often than the pair, so
    the alternate title only costs recall when searching and matching. The full title
    stays on the book as display data.
    """
    match = _ALTERNATE_TITLE.match(title or "")
    if not match or not significant_tokens(match.group("main")):
        return title
    return match.group("main")


def title_tokens_match(
    title: str | None,
    haystack_tokens: set[str],
    threshold: float = DEFAULT_TITLE_MATCH_THRESHOLD,
) -> bool:
    """True when enough significant title tokens appear in ``haystack_tokens``."""
    return significant_tokens_match(significant_tokens(title), haystack_tokens, threshold)


def significant_tokens_match(
    title_toks: list[str],
    haystack_tokens: Collection[str],
    threshold: float = DEFAULT_TITLE_MATCH_THRESHOLD,
) -> bool:
    """``title_tokens_match`` for a title already run through ``significant_tokens``.

    Matching one title against many entries should tokenise it once, not per entry.
    """
    if not title_toks:
        return False
    # Either side may write a number as a word, a roman numeral or digits.
    haystack = {canonical_numeral(tok) for tok in haystack_tokens}
    present = sum(1 for tok in title_toks if tok in haystack)
    return (present / len(title_toks)) >= threshold


# Words marking a title that bundles several works. A shelf entry carrying one of
# these holds the searched book, but as part of something larger, which is worth
# telling the reader apart from owning it on its own.
COLLECTION_MARKERS = frozenset(
    {
        "omnibus",
        "collection",
        "complete",
        "boxed",
        "boxset",
        "box",
        "set",
        "bundle",
        "megapack",
        "anthology",
        "compendium",
        "trilogy",
        "duology",
        "books",
        "volumes",
        "vols",
    }
)

# Words marking a different printing of the same work, which changes nothing about
# whether the reader owns it.
EDITION_MARKERS = frozenset(
    {
        "edition",
        "editions",
        "series",
        "book",
        "vol",
        "volume",
        "illustrated",
        "annotated",
        "unabridged",
        "abridged",
        "deluxe",
        "revised",
        "reissue",
        "anniversary",
    }
)

# Neither kind is evidence that the shelf holds a different book.
PACKAGING_MARKERS = COLLECTION_MARKERS | EDITION_MARKERS


def bundle_markers(
    title_tokens: set[str] | frozenset[str], search_title: str | None = None
) -> set[str]:
    """Collection words in a title that the searched title does not carry itself.

    "The Complete Maus" searched against a shelf holding "The Complete Maus" is the
    book itself, not a collection containing it.
    """
    return (set(title_tokens) & COLLECTION_MARKERS) - set(tokens(search_title))


# Numbered forms a bundle title takes, which single words cannot express: "Books 1-6",
# "(#1-24)", "1-28 +", "All 5 Books", "+ Short Stories".
_BUNDLE_RANGE = re.compile(
    r"\bbooks?\s*\d{1,3}\s*(?:-|\u2013|to|thru|through)\s*\d{1,3}\b"
    r"|\(#?\d{1,3}\s*[-\u2013]\s*\d{1,3}\)"
    r"|\b\d{1,3}\s*[-\u2013]\s*\d{1,3}\s*\+"
    r"|\ball\s+\d+\s+(?:audio)?books\b"
    r"|\+\s*short\s+stories\b",
    re.IGNORECASE,
)

# "Complete and Unabridged" describes one book's recording, not a set of them.
_UNABRIDGED_PHRASE = re.compile(r"\bcomplete\s*(?:&|and)\s*unabridged\b", re.IGNORECASE)


def is_bundle_title(title: str | None, search_title: str | None = None) -> bool:
    """True when a raw title (a release name, say) bundles several works.

    Checks the collection words and the numbered ranges, ignoring any the searched
    title carries itself. Use ``bundle_markers`` when only tokens are available.
    """
    text = _UNABRIDGED_PHRASE.sub(" ", title or "")
    if bundle_markers(set(tokens(text)), search_title):
        return True
    wanted = (search_title or "").lower()
    return any(match.group(0).lower() not in wanted for match in _BUNDLE_RANGE.finditer(text))


def extra_work_tokens(
    shelf_title_tokens: set[str],
    search_title: str | None,
    context_tokens: set[str] | None = None,
) -> set[str]:
    """Significant words the shelf title adds that suggest a different work.

    Packaging words are ignored, since "Illustrated Edition" and "Books 1-6" describe
    the same work differently wrapped, while "Messiah" or "Chapter Two" name another.
    Only alphabetic words of three or more characters count, so volume numbers and
    structural words do not make a shelf entry look like a different book. A number
    word the searched title carries in any form is its own: "Part Two" on the shelf
    adds nothing to a search for "Part II".

    ``context_tokens`` are the entry's series and author words, which shelf titles
    often repeat ("Alex Cross 25: Cross Kill") without meaning another book.
    """
    search_toks = {
        canonical_numeral(tok) for tok in set(tokens(search_title)) | (context_tokens or set())
    }
    return {
        tok
        for tok in shelf_title_tokens
        # Only a real word counts. Numbers are left alone deliberately: a shelf title
        # carries volume numbers far more often than it names a numbered sequel, and
        # wrongly claiming ownership is the costlier mistake, since the sync then
        # never fetches the book. "Persepolis 2" is the case this concedes.
        if tok.isalpha()
        and len(tok) >= 3
        and tok not in STOPWORDS
        and tok not in PACKAGING_MARKERS
        and canonical_numeral(tok) not in search_toks
    }


def normalize_isbn(value: object) -> str:
    """Normalize an ISBN to comparable form (digits + trailing X, uppercased)."""
    if not value:
        return ""
    return re.sub(r"[^0-9xX]", "", str(value)).upper()


def _isbn10_to_isbn13(isbn10: str) -> str | None:
    """ISBN-13 form of a normalized ISBN-10: 978 prefix plus a recomputed check digit."""
    core = isbn10[:9]
    if len(isbn10) != 10 or not core.isdigit():
        return None
    digits = f"978{core}"
    total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(digits))
    return f"{digits}{(10 - total % 10) % 10}"


def isbn_variants(value: object) -> frozenset[str]:
    """Comparable forms of an ISBN: normalized, plus the ISBN-13 form of an ISBN-10."""
    isbn = normalize_isbn(value)
    if not isbn:
        return frozenset()
    variants = {isbn}
    if len(isbn) == 10 and (isbn13 := _isbn10_to_isbn13(isbn)):
        variants.add(isbn13)
    return frozenset(variants)
