"""Does an Audible catalogue result name the audiobook we want?

Text work only, for the results Audiobookshelf's ``/api/search/books`` returns from its
Audible providers (``audible`` is the US store, ``audible.uk`` the UK one). A result names
the wanted book when its title, normalised the same way as the wanted title, is the same
sequence of words (or one begins the other, see ``titles_match``), and one of the wanted
author's surnames appears in its author field.

Normalising a title: diacritics are folded; bracketed notes such as "(Unabridged)" go; a
leading series label ("Robert Langdon, Book 4 - ") goes; the subtitle after ":", ", or",
" aka " or a spaced dash is dropped; "&" reads as "and"; a possessive "'s" is dropped; and
a roman, spelled-out or digit numeral all become the digit ("Part II", "Part Two" and
"Part 2" are one title). Stopwords are left out of the comparison.

The matching leans towards "found" on purpose. A wrong "found" leaves a request pending,
where it already was; a wrong "not found" declines a book that exists.

Standard library only, on purpose: the stress test against a live library runs this file
as-is next to the Audiobookshelf server, so what is tested is what ships.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

_STOPWORDS = frozenset({"a", "an", "the", "of", "and", "or", "to", "in", "on", "for", "with", "by"})

_NUMERALS = {
    "zero": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "ten": "10",
    "eleven": "11",
    "twelve": "12",
    "thirteen": "13",
    "fourteen": "14",
    "fifteen": "15",
    "sixteen": "16",
    "seventeen": "17",
    "eighteen": "18",
    "nineteen": "19",
    "twenty": "20",
    "i": "1",
    "ii": "2",
    "iii": "3",
    "iv": "4",
    "v": "5",
    "vi": "6",
    "vii": "7",
    "viii": "8",
    "ix": "9",
    "x": "10",
    "xi": "11",
    "xii": "12",
    "xiii": "13",
    "xiv": "14",
    "xv": "15",
    "xvi": "16",
    "xvii": "17",
    "xviii": "18",
    "xix": "19",
    "xx": "20",
}

# British spellings the US store does not find ("The Armour of Light" is "The Armor of
# Light" there). Titles compare in the US spelling, and the US store is asked in it.
_US_SPELLING = {
    "armour": "armor",
    "behaviour": "behavior",
    "colour": "color",
    "colours": "colors",
    "endeavour": "endeavor",
    "favour": "favor",
    "harbour": "harbor",
    "honour": "honor",
    "humour": "humor",
    "labour": "labor",
    "neighbour": "neighbor",
    "neighbours": "neighbors",
    "rumour": "rumor",
    "rumours": "rumors",
    "saviour": "savior",
    "splendour": "splendor",
    "valour": "valor",
}
_US_SPELLING_WORD = re.compile(r"\b(" + "|".join(_US_SPELLING) + r")\b", re.IGNORECASE)

_BRACKETED = re.compile(r"\([^)]*\)|\[[^\]]*\]|\{[^}]*\}")
# A dash with space on at least one side splits off a part: "Born a Crime- Stories from
# ...", "Robert Langdon, Book 4 - Inferno". Hyphenated words ("Sword-Sworn") survive.
_DASH = re.compile(r"\s+[-–—]+\s*|\s*[-–—]+\s+")
# ": subtitle", ", or Alternative Title", " aka Other Title": the first ends the title.
_SUBTITLE = re.compile(r"\s*(?::|,\s+or\b|\s+aka\b).*$", re.IGNORECASE)
# A leading part that ends in a series position: "Mayfair Witches 01", "Robert Langdon,
# Book 4", "Discworld #3". A plain number does not count ("Fahrenheit 451 - A Novel").
_NUMBERED_PART = re.compile(
    r"(?:\b(?:book|vol(?:ume)?|part|no)\.?\s*\d+(?:\.\d+)?|\b0\d+|#\d+(?:\.\d+)?)$", re.IGNORECASE
)
# A series label marked by a bracketed number in front of the title: "HGTTG [01] The
# Hitchhiker's Guide to the Galaxy".
_BRACKETED_LABEL = re.compile(r"^[^\[\]()]{0,40}?[\[(]\s*#?\d+(?:\.\d+)?\s*[\])]\s*(?=\S)")
# A zero-padded track number in front of a title ("04 The Visitor").
_TRACK_NUMBER = re.compile(r"^0\d\s+(?=\S)")
_POSSESSIVE = re.compile(r"[’']s\b")
_SPLIT = re.compile(r"[^a-z0-9]+")
# Separators between the names in an author field: "Anne Rice, Christopher Rice",
# "Kristin Hannah/Polly Stone", "Lee Child & Andrew Child", "Douglas Adams and ...".
_AUTHOR_SPLIT = re.compile(r"\s*(?:[,;/&]|\band\b)\s*", re.IGNORECASE)

# A prefix match ("Start with Why" for "Start with Why 15th Anniversary Edition") needs
# this many words in the shorter title, so a one-word title never matches a longer one.
_MIN_PREFIX_WORDS = 2


def _fold(text: str) -> str:
    """ASCII-folded, lower-cased text: diacritics and typographic quotes removed."""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return stripped.replace("’", "'").replace("‘", "'").lower()


def _words(text: str) -> list[str]:
    folded = _fold(text).replace("&", " and ")
    folded = _POSSESSIVE.sub("", folded).replace("'", "")
    return [_US_SPELLING.get(word, word) for word in _SPLIT.split(folded) if word]


def us_spelling(text: str) -> str:
    """``text`` with the British spellings in ``_US_SPELLING`` turned American."""

    def _swap(match: re.Match[str]) -> str:
        word = match.group(0)
        us = _US_SPELLING[word.lower()]
        return us.capitalize() if word[0].isupper() else us

    return _US_SPELLING_WORD.sub(_swap, text)


def search_title(
    title: str | None, author: str | None = None, *, keep_subtitle: bool = False
) -> str:
    """The title alone, as it is sent to the store.

    Bracketed notes ("(Unabridged)", "[Series 02]") go. Of the parts a spaced dash
    separates, leading ones that are a numbered series label or the author's name go too,
    and the first that is left is the title; the rest is subtitle. A subtitle after ":",
    ", or" and " aka " goes (unless ``keep_subtitle``), and so does a trailing ellipsis.
    """
    original = (title or "").strip()
    text = _BRACKETED_LABEL.sub("", original)
    text = _BRACKETED.sub(" - ", text)
    parts = [part.strip(" ,;") for part in _DASH.split(text)]
    parts = [part for part in parts if part]
    author_words = _words(author or "")
    while len(parts) > 1 and (
        _NUMBERED_PART.search(parts[0]) or (author_words and _words(parts[0]) == author_words)
    ):
        parts.pop(0)
    if keep_subtitle:
        text = " ".join(parts)
    else:
        text = parts[0] if parts else ""
        text = _SUBTITLE.sub("", text)
    text = _TRACK_NUMBER.sub("", text)
    text = re.sub(r"\s+", " ", text).strip(" ,;")
    text = re.sub(r"\s*(?:\.{3}|…)$", "", text)
    return text or original


def title_words(
    title: str | None, author: str | None = None, *, keep_subtitle: bool = False
) -> tuple[str, ...]:
    """The comparable words of a title, in order."""
    words = _words(search_title(title, author, keep_subtitle=keep_subtitle))
    return tuple(_NUMERALS.get(word, word) for word in words if word not in _STOPWORDS)


def _same_title(wanted: tuple[str, ...], candidate: tuple[str, ...]) -> bool:
    if not wanted or not candidate:
        return False
    if wanted == candidate:
        return True
    shorter, longer = sorted((wanted, candidate), key=len)
    return len(shorter) >= _MIN_PREFIX_WORDS and longer[: len(shorter)] == shorter


def titles_match(wanted: str | None, candidate: str | None, author: str | None = None) -> bool:
    """True when the two titles name the same book once normalised.

    Same words, or one title's words begin the other's when the shorter has at least two
    ("Merry Christmas" and "Merry Christmas, Alex Cross"). Each title is compared both
    without and with its subtitle, so "The Mummy, or Ramses the Damned" matches "The Mummy
    or Ramses the Damned". A store title with a series in front of a colon ("Ramses the
    Damned: The Reign of Osiris") is also tried without it.
    """
    wanted_forms = {
        title_words(wanted, author),
        title_words(wanted, author, keep_subtitle=True),
    }
    candidate_title = candidate or ""
    candidate_forms = {
        title_words(candidate_title),
        title_words(candidate_title, keep_subtitle=True),
    }
    if ":" in candidate_title:
        candidate_forms.add(title_words(candidate_title.split(":", 1)[1]))
    return any(_same_title(w, c) for w in wanted_forms for c in candidate_forms)


def author_surnames(author: str | None) -> set[str]:
    """The surnames in an author field, one per name.

    "Lee Child" gives {"child"}; "R.C. Bray, Andy Weir" gives {"bray", "weir"}; "Child,
    Lee" gives {"child", "lee"}, which is harmless as the match is on any of them.
    """
    surnames = set()
    for name in _AUTHOR_SPLIT.split(author or ""):
        words = [w for w in _SPLIT.split(_fold(name)) if len(w) >= 2 and w not in _STOPWORDS]
        if words:
            surnames.add(words[-1])
    return surnames


def author_matches(wanted_author: str | None, candidate_author: str | None) -> bool:
    """True when one of the wanted author's surnames is in the result's author field.

    A wanted author with no surname (empty, or initials only) matches on title alone.
    """
    surnames = author_surnames(wanted_author)
    if not surnames:
        return True
    return bool(surnames & set(_SPLIT.split(_fold(candidate_author or ""))))


@dataclass(frozen=True)
class StoreMatch:
    """The catalogue entry that names the wanted book."""

    asin: str
    title: str
    author: str


def find_match(
    wanted_title: str | None, wanted_author: str | None, results: list[dict]
) -> StoreMatch | None:
    """The first store result that is the wanted book, or None.

    ``results`` is what ``/api/search/books`` returns: dicts with ``title``, ``author`` and
    ``asin``. Results without an ASIN are not catalogue entries and are skipped.
    """
    for result in results:
        if not isinstance(result, dict):
            continue
        asin = str(result.get("asin") or "").strip().upper()
        title = str(result.get("title") or "")
        author = str(result.get("author") or "")
        if not asin:
            continue
        if titles_match(wanted_title, title, wanted_author) and author_matches(
            wanted_author, author
        ):
            return StoreMatch(asin=asin, title=title, author=author)
    return None
