"""Wikidata as a bridge between Open Library works and their translated titles.

Open Library catalogues a translated book under its original title, so a reader whose
library is in, say, Ukrainian or Polish gets release searches for "The Secret History"
while release sources list "Таємна історія" or "Tajemna historia". Wikidata links many
works to Open Library (property P648) and labels them in many languages.

Two questions are answered here, both with the MediaWiki Action API (CirrusSearch +
wbgetentities; the SPARQL endpoint is slower and times out under load):

- ``localized_titles(...)``: what is this Open Library work called in other languages?
- ``find_works(text)``: which written works could a non-Latin query such as
  "Таємна історія" mean? Open Library itself returns nothing for most of them.

This module only talks to Wikidata; everything Open Library is the provider's job.

Politeness follows the Wikimedia API etiquette: an identifying User-Agent, sequential
calls, one retry that honours Retry-After, and a pause after a failure. Each lookup has
one deadline that bounds every request it makes, retries and waits included. Network
trouble raises ``WikidataUnavailableError``, which is never cached, so the caller falls
back to plain Open Library behaviour.
"""

from __future__ import annotations

import re
import threading
import time
import unicodedata
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any

import requests

from shelfmark.core.cache import cacheable
from shelfmark.core.logger import setup_logger
from shelfmark.download.network import get_ssl_verify

logger = setup_logger(__name__)

WIKIDATA_API_URL = "https://www.wikidata.org/w/api.php"
USER_AGENT = "Shelfmark (+https://github.com/calibrain/shelfmark)"

CONNECT_TIMEOUT_SECONDS = 3.0
READ_TIMEOUT_SECONDS = 8.0
BREAKER_SECONDS = 120
LOOKUP_DEADLINE_SECONDS = 10.0
MAX_ENTITIES_PER_LOOKUP = 30
# A label part shorter than this is not a title on its own («I, Robot» is not «I»).
MIN_LABEL_PART_LENGTH = 3

_RETRY_STATUSES = frozenset({429, 502, 503, 504})
_QID_RE = re.compile(r"Q\d+")
_OL_WORK_RE = re.compile(r"OL\d+W")
_DISAMBIGUATOR_RE = re.compile(r"\s*\([^)]*\)\s*$")
_LABEL_SPLIT_RE = re.compile(r"\s*(?:[:;,(]|\s[—–-]\s|\.\s)\s*")

# Instance-of (P31) values that copy a book's author (P50) but are not the book.
_NOT_BOOKS = frozenset(
    {
        "Q11424",  # film
        "Q24869",  # feature film
        "Q202866",  # animated film
        "Q24862",  # short film
        "Q506240",  # television film
        "Q5398426",  # television series
        "Q1259759",  # miniseries
        "Q581714",  # animated series
        "Q21191270",  # television series episode
        "Q7889",  # video game
        "Q1344",  # opera
        "Q2188189",  # musical work
        "Q7725310",  # series of creative works
    }
)


class WikidataUnavailableError(Exception):
    """Wikidata could not be asked; the caller keeps its non-Wikidata behaviour."""


_session_lock = threading.Lock()
_session: requests.Session | None = None
_breaker_until = 0.0


def _http() -> requests.Session:
    global _session
    with _session_lock:
        if _session is None:
            _session = requests.Session()
            _session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
        return _session


def _trip(reason: str) -> WikidataUnavailableError:
    global _breaker_until
    _breaker_until = time.monotonic() + BREAKER_SECONDS
    logger.warning("Wikidata lookups paused for %ss: %s", BREAKER_SECONDS, reason)
    return WikidataUnavailableError(reason)


class Deadline:
    """One wall-clock limit for a whole lookup, shared by all of its requests."""

    def __init__(self, seconds: float = LOOKUP_DEADLINE_SECONDS) -> None:
        self.until = time.monotonic() + seconds

    def remaining(self) -> float:
        left = self.until - time.monotonic()
        if left <= 0:
            raise WikidataUnavailableError("lookup deadline reached")
        return left


def _send(params: dict[str, Any], deadline: Deadline) -> requests.Response:
    left = deadline.remaining()
    try:
        return _http().get(
            WIKIDATA_API_URL,
            params=params,
            timeout=(min(CONNECT_TIMEOUT_SECONDS, left), min(READ_TIMEOUT_SECONDS, left)),
            verify=get_ssl_verify(WIKIDATA_API_URL),
        )
    except requests.RequestException as e:
        raise _trip(repr(e)) from e


def _api(params: dict[str, Any], deadline: Deadline) -> dict[str, Any]:
    """One MediaWiki API call with at most one retry, all within ``deadline``."""
    if time.monotonic() < _breaker_until:
        raise WikidataUnavailableError("paused after a recent failure")
    response = _send(params, deadline)
    if response.status_code in _RETRY_STATUSES:
        try:
            wait = float(response.headers.get("Retry-After", "2"))
        except ValueError:
            wait = 2.0
        wait = max(wait, 0.0)
        if wait >= deadline.remaining():
            raise _trip(f"HTTP {response.status_code}, Retry-After {wait}s")
        time.sleep(wait)
        response = _send(params, deadline)
    if response.status_code >= HTTPStatus.BAD_REQUEST:
        raise _trip(f"HTTP {response.status_code}")
    try:
        data = response.json()
    except ValueError as e:
        raise _trip("invalid JSON") from e
    if not isinstance(data, dict):
        raise _trip("unexpected JSON")
    # MediaWiki reports backend failures with HTTP 200 and an "error" object.
    if data.get("error"):
        error = data["error"]
        code = error.get("code") if isinstance(error, dict) else error
        raise WikidataUnavailableError(f"API error: {code}")
    return data


# ---------------------------------------------------------------- text helpers


def _strip_marks(text: str) -> str:
    """Drop combining marks: "Zabuz͡hko" -> "Zabuzhko", "Café" -> "Cafe". Marks that are
    letters elsewhere («й», «ї») go too; both sides of a comparison are folded alike."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    return unicodedata.normalize(
        "NFC", "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    )


def normalize(text: str) -> str:
    """Fold case, drop accents and punctuation: «Таємна історія!» -> "таємна історія"."""
    folded = _strip_marks(text).casefold()
    # Ukrainian «ґ» came back into the alphabet in 1990; titles are written both ways
    # («Танґо смерті», «Танго смерті»).
    folded = folded.replace("ґ", "г")
    return " ".join(re.findall(r"\w+", folded))


def search_words(text: str, max_length: int = 120) -> str:
    """Case-folded words for a search query, cut at a word boundary. Unlike ``normalize``
    it keeps diacritics: CirrusSearch treats «України» and «Украiни» as different words."""
    words = re.findall(r"\w+", unicodedata.normalize("NFC", text or "").casefold())
    if words and len(words[0]) > max_length:
        return words[0][:max_length]
    out = ""
    for word in words:
        candidate = f"{out} {word}" if out else word
        if len(candidate) > max_length:
            break
        out = candidate
    return out


def contains_phrase(phrase: str, text: str) -> bool:
    """Whole-word containment of a normalized phrase in a normalized text."""
    p, t = normalize(phrase), normalize(text)
    return bool(p) and f" {p} " in f" {t} "


def leftover_words(phrase: str, text: str) -> list[str]:
    """Words of ``text`` outside the first whole-word occurrence of ``phrase``."""
    p, t = normalize(phrase), f" {normalize(text)} "
    return t.replace(f" {p} ", " ", 1).split() if p else t.split()


def close_spelling(a: str, b: str) -> bool:
    """Same word up to one letter for longer words: transliterations of a surname drift
    («Коельо» / «Коельйо», "Andrukhovych" / "Andrukhovich")."""
    if a == b:
        return True
    if min(len(a), len(b)) < 5 or abs(len(a) - len(b)) > 1:
        return False
    if len(a) > len(b):
        a, b = b, a
    i = j = edits = 0
    while i < len(a) and j < len(b):
        if a[i] == b[j]:
            i += 1
            j += 1
            continue
        edits += 1
        if edits > 1:
            return False
        if len(a) == len(b):
            i += 1
        j += 1
    return edits + (len(b) - j) + (len(a) - i) <= 1


_NAME_SUFFIXES = frozenset({"jr", "sr", "ii", "iii", "iv"})


def family_name(name: str) -> str:
    """The family name as written: "Ivan Franko", "Franko, Ivan", "Франко І. Я." and
    "Kurt Vonnegut Jr." give "Franko", "Franko", "Франко", "Vonnegut"; "" if none is left."""
    family = name.split(",", 1)[0] if "," in name else name
    # Library romanizations put marks inside words ("Zabuz͡hko"); \w does not match them.
    words = re.findall(r"\w[\w'’-]*", _strip_marks(family))
    for word in reversed(words):
        folded = normalize(word)
        if len(folded) > 1 and folded not in _NAME_SUFFIXES:
            return word
    return ""


def surname(name: str) -> str:
    """``family_name`` normalized for comparison."""
    return normalize(family_name(name))


def has_non_latin_letters(text: str) -> bool:
    """True when the text has letters outside the Latin script (Cyrillic, Greek, CJK, ...)."""
    for ch in text or "":
        if not ch.isalpha():
            continue
        name = unicodedata.name(ch, "")
        if "LATIN" not in name and "ORDINAL INDICATOR" not in name:
            return True
    return False


def strip_disambiguator(label: str) -> str:
    """«Танґо смерті (роман)» -> «Танґо смерті»: Wikidata's disambiguation is not a title."""
    return _DISAMBIGUATOR_RE.sub("", label).strip() or label


def label_forms(label: str) -> list[str]:
    """The label and its parts: «Гобіт, або Туди і звідти» -> also «Гобіт»;
    «Sapiens: Людина розумна» -> «Sapiens», «Людина розумна». Parts too short to be a
    title on their own are dropped; the whole label is always kept (CJK titles are short)."""
    whole = [label, strip_disambiguator(label)]
    parts = [
        p
        for p in _LABEL_SPLIT_RE.split(strip_disambiguator(label))
        if len(normalize(p).replace(" ", "")) >= MIN_LABEL_PART_LENGTH
    ]
    return [f.strip(" .") for f in dict.fromkeys(whole + parts) if f.strip(" .")]


# ---------------------------------------------------------------- Wikidata primitives


def _search(query: str, deadline: Deadline, limit: int = 5) -> list[str]:
    data = _api(
        {
            "action": "query",
            "list": "search",
            "srsearch": query,
            "srlimit": limit,
            "srnamespace": 0,
            "format": "json",
        },
        deadline,
    )
    hits = (data.get("query") or {}).get("search") or []
    return [str(h["title"]) for h in hits if _QID_RE.fullmatch(str(h.get("title", "")))]


def _entities(
    qids: list[str], deadline: Deadline, *, claims: bool = True
) -> dict[str, dict[str, Any]]:
    """Wikidata items by id, one call; ids beyond MAX_ENTITIES_PER_LOOKUP are dropped (the API allows 50)."""
    data = _api(
        {
            "action": "wbgetentities",
            "ids": "|".join(qids[:MAX_ENTITIES_PER_LOOKUP]),
            "props": "labels|aliases|claims|sitelinks" if claims else "labels|aliases",
            "format": "json",
        },
        deadline,
    )
    entities = data.get("entities") or {}
    return {qid: entity for qid, entity in entities.items() if isinstance(entity, dict)}


def _claim_values(entity: dict[str, Any], prop: str) -> list[str]:
    values: list[str] = []
    for claim in (entity.get("claims") or {}).get(prop) or []:
        value = ((claim.get("mainsnak") or {}).get("datavalue") or {}).get("value")
        if isinstance(value, dict) and "id" in value:
            values.append(str(value["id"]))
        elif isinstance(value, str):
            values.append(value)
    return values


def _all_labels(entity: dict[str, Any]) -> list[str]:
    labels = [v.get("value", "") for v in (entity.get("labels") or {}).values()]
    aliases = [a.get("value", "") for v in (entity.get("aliases") or {}).values() for a in v]
    return list(dict.fromkeys(str(x) for x in labels + aliases if x))


def _titles_by_language(entity: dict[str, Any]) -> dict[str, str]:
    return {
        str(lang): strip_disambiguator(str(v["value"]))
        for lang, v in (entity.get("labels") or {}).items()
        if isinstance(v, dict) and v.get("value")
    }


def _is_written_work(entity: dict[str, Any]) -> bool:
    """Has an author (P50), is not an edition (P629), is not an adaptation that copied P50."""
    return (
        bool(_claim_values(entity, "P50"))
        and not _claim_values(entity, "P629")
        and not set(_claim_values(entity, "P31")) & _NOT_BOOKS
    )


def _sitelink_count(entity: dict[str, Any]) -> int:
    return len(entity.get("sitelinks") or {})


def _best_work(entities: list[dict[str, Any]]) -> dict[str, Any] | None:
    works = [e for e in entities if _is_written_work(e)]
    return max(works, key=_sitelink_count) if works else None


def _latin_name(entity: dict[str, Any]) -> str:
    labels = entity.get("labels") or {}
    for lang in ("en", "mul"):
        value = str((labels.get(lang) or {}).get("value") or "")
        if value and not has_non_latin_letters(value):
            return value
    return next((n for n in _all_labels(entity) if not has_non_latin_letters(n)), "")


# ---------------------------------------------------------------- the two questions


@cacheable(ttl_key="METADATA_CACHE_BOOK_TTL", ttl_default=600, key_prefix="wikidata:titles")
def localized_titles(
    work_ids: tuple[str, ...],
    title: str = "",
    author: str = "",
    author_ids: tuple[str, ...] = (),
) -> dict[str, str]:
    """Titles of an Open Library work in every language Wikidata has, keyed by language.

    ``work_ids`` are the work's Open Library ids, current first, then any it was reached
    through (Wikidata may still name a merged id); ``author_ids`` are its Open Library
    author ids. Tried in order:

    1. the Wikidata item that names one of ``work_ids`` (P648);
    2. the author's items (by Open Library author id, else by name) and their works with
       a label equal to ``title`` in any language.

    Returns {} when nothing matches. Raises WikidataUnavailableError on network trouble
    or when the deadline passes; ``cacheable`` stores neither.
    """
    deadline = Deadline()

    def from_qids(qids: list[str], *, must_match_title: bool) -> dict[str, str]:
        if not qids:
            return {}
        entities = list(_entities(qids, deadline).values())
        if must_match_title:
            wanted = normalize(title)
            entities = [e for e in entities if any(normalize(x) == wanted for x in _all_labels(e))]
        work = _best_work(entities)
        return _titles_by_language(work) if work else {}

    for work_id in dict.fromkeys(work_ids):
        titles = from_qids(
            _search(f"haswbstatement:P648={work_id}", deadline), must_match_title=False
        )
        if titles:
            return titles

    if not normalize(title):
        return {}
    author_qids: list[str] = []
    for ol_author in author_ids[:2]:
        author_qids += _search(f"haswbstatement:P648={ol_author}", deadline, limit=2)
    if not author_qids and author:
        data = _api(
            {
                "action": "wbsearchentities",
                "search": author,
                "language": "en",
                "type": "item",
                "limit": 3,
                "format": "json",
            },
            deadline,
        )
        author_qids = [str(x["id"]) for x in data.get("search") or [] if x.get("id")]
    words = search_words(title)
    for author_qid in list(dict.fromkeys(author_qids))[:3]:
        titles = from_qids(
            _search(f"haswbstatement:P50={author_qid} {words}", deadline), must_match_title=True
        )
        if titles:
            return titles
    return {}


def _match_query(labels: list[str], query: str, name_words: set[str]) -> tuple[bool, bool]:
    """Whether a label (or part of one) is in the query, and whether words left over name
    the author. Left-over words must all be author-name words, up to a letter."""
    matched = names_author = False
    for label in labels:
        for form in label_forms(label):
            if not contains_phrase(form, query):
                continue
            leftover = leftover_words(form, query)
            if all(any(close_spelling(w, n) for n in name_words) for w in leftover):
                matched = True
                names_author = names_author or bool(leftover)
    return matched, names_author


@dataclass(frozen=True)
class WikidataWork:
    """A written work found on Wikidata, with what a provider needs to find it at home."""

    qid: str
    ol_work_ids: tuple[str, ...]
    titles: dict[str, str]
    author: str  # the first author's name in Latin script, for an Open Library search
    author_names: tuple[str, ...]  # every spelling of every author, any script

    def is_by(self, names: list[str]) -> bool:
        """Whether one of ``names`` (a provider's author list) is one of this work's
        authors, by surname in any script ("Іван Франко" is "Ivan Franko")."""
        ours = {s for s in map(surname, self.author_names) if s}
        theirs = {s for s in map(surname, names) if s}
        return any(close_spelling(a, b) for a in ours for b in theirs)


@cacheable(ttl_key="METADATA_CACHE_SEARCH_TTL", ttl_default=300, key_prefix="wikidata:works")
def find_works(text: str, limit: int = 3) -> list[WikidataWork]:
    """Written works a typed query names, best first.

    A candidate must have a label (or a part of one, see ``label_forms``) that appears
    whole in the query; any words left over must be one of its authors' names, up to a
    letter ("Тартт Таємна історія" names Donna Tartt's novel, "Таємна історія України"
    does not). Works the query names together with their author rank first, then works
    linked to Open Library, then by how many Wikipedias describe them. Works without an
    Open Library link are kept: the caller can look them up by title and author. Raises
    WikidataUnavailableError on network trouble or when the deadline passes.
    """
    query = search_words(text)
    if not query:
        return []
    deadline = Deadline()
    words = query.split()
    probes = [query]
    if len(words) >= 2:
        probes += [" ".join(words[1:]), " ".join(words[:-1])]

    qids: list[str] = []
    for probe in dict.fromkeys(probes):
        # haswbstatement:P50 keeps the search to items with an author: written works, not
        # the films, songs and places that share their names.
        for qid in _search(f"{probe} haswbstatement:P50", deadline, limit=10):
            if qid not in qids:
                qids.append(qid)
    if not qids:
        return []

    works = [e for e in _entities(qids, deadline).values() if _is_written_work(e)]
    author_ids = list(dict.fromkeys(a for w in works for a in _claim_values(w, "P50")))
    authors = _entities(author_ids, deadline, claims=False) if author_ids else {}

    ranked: list[tuple[bool, bool, int, WikidataWork]] = []
    for work in works:
        work_authors = [authors.get(a) or {} for a in _claim_values(work, "P50")]
        spellings = [name for a in work_authors for name in _all_labels(a)]
        name_words = {w for name in spellings for w in normalize(name).split() if len(w) > 1}
        matched, names_author = _match_query(_all_labels(work), query, name_words)
        if not matched:
            continue
        ol_ids = tuple(v for v in _claim_values(work, "P648") if _OL_WORK_RE.fullmatch(v))
        found = WikidataWork(
            qid=str(work.get("id", "")),
            ol_work_ids=ol_ids,
            titles=_titles_by_language(work),
            author=next((n for n in map(_latin_name, work_authors) if n), ""),
            author_names=tuple(dict.fromkeys(spellings)),
        )
        ranked.append((names_author, bool(ol_ids), _sitelink_count(work), found))

    ranked.sort(key=lambda r: r[:3], reverse=True)
    return [r[3] for r in ranked[:limit]]
