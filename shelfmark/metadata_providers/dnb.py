"""Deutsche Nationalbibliothek (DNB) metadata provider via its SRU API. No API key required.

Record heuristics adapted from the Calibre DNB_DE plugin and its Calibre-Web /
LazyLibrarian port (GPL v3). API: https://www.dnb.de/sru
"""

import re
import unicodedata
from dataclasses import replace
from typing import TYPE_CHECKING, Any, ClassVar

import requests
from bs4 import BeautifulSoup
from defusedxml import ElementTree as DefusedElementTree
from defusedxml.common import DefusedXmlException

from shelfmark.core.cache import cacheable
from shelfmark.core.config import config as app_config
from shelfmark.core.languages import normalize_language
from shelfmark.core.logger import setup_logger
from shelfmark.core.settings_registry import (
    ActionButton,
    CheckboxField,
    HeadingField,
    SelectField,
    SettingsField,
    register_settings,
)
from shelfmark.download.network import get_proxies, get_ssl_verify
from shelfmark.metadata_providers import (
    BookMetadata,
    DisplayField,
    MetadataProvider,
    MetadataSearchOptions,
    SearchField,
    SearchResult,
    SearchType,
    SortOrder,
    TextSearchField,
    register_provider,
)
from shelfmark.metadata_providers.openlibrary import RateLimiter

if TYPE_CHECKING:
    from xml.etree.ElementTree import Element

logger = setup_logger(__name__)

DNB_SRU_URL = "https://services.dnb.de/sru/dnb"
DNB_PERMALINK_URL = "https://d-nb.info/"
# No covers: DNB's cover service is behind bot protection (Anubis)

# Allowed hosts for blurb links in field 856 (deposit.dnb.de is legacy)
_BLURB_URL_PREFIXES = (
    "https://services.dnb.de/plus/",
    "http://deposit.dnb.de/",
    "https://deposit.dnb.de/",
)

USER_AGENT = "Shelfmark (DNB metadata provider; +https://github.com/calibrain/shelfmark)"

# DNB publishes no limit; stay polite
RATE_LIMIT_REQUESTS = 60
RATE_LIMIT_WINDOW_SECONDS = 60

# SRU caps maximumRecords at 100
MAX_RECORDS_PER_REQUEST = 100
ISBN_13_LENGTH = 13
# The number index can match more than the book itself
ISBN_LOOKUP_RECORDS = 5

# Plausibility bounds for parsed years and page counts
MIN_YEAR = 1400
MAX_YEAR = 2100
MAX_PAGES = 100_000

# Relator codes ($4) that never mean author (translator, narrator, ...)
_NON_AUTHOR_RELATORS = frozenset(
    {
        "act",
        "aui",
        "cmp",
        "cnd",
        "cov",
        "ill",
        "itr",
        "nrt",
        "pht",
        "prf",
        "sng",
        "spk",
        "trl",
        "wpr",
    }
)

# Page counts such as 336 Seiten, 1.024 S. or 224 pages
_PAGES_RE = re.compile(r"(\d{1,3}(?:\.\d{3})+|\d+)\s*(?:Seiten|S\.|pages?\b)", re.IGNORECASE)

# German transliterations, so "Schaetzing" and "Schätzing" compare equal
_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue"})

# MARC 21 language codes, the only form DNB's spr index accepts (German: ger, not deu)
_MARC_LANGUAGE_CODES = {
    "af": "afr",
    "ar": "ara",
    "az": "aze",
    "ba": "bak",
    "be": "bel",
    "bg": "bul",
    "bn": "ben",
    "bo": "tib",
    "bs": "bos",
    "ca": "cat",
    "cs": "cze",
    "da": "dan",
    "de": "ger",
    "el": "gre",
    "en": "eng",
    "eo": "epo",
    "es": "spa",
    "et": "est",
    "fa": "per",
    "fi": "fin",
    "fil": "fil",
    "fr": "fre",
    "ga": "gle",
    "gd": "gla",
    "gl": "glg",
    "gu": "guj",
    "gv": "glv",
    "he": "heb",
    "hi": "hin",
    "hr": "hrv",
    "hu": "hun",
    "hy": "arm",
    "id": "ind",
    "is": "ice",
    "it": "ita",
    "ja": "jpn",
    "jv": "jav",
    "ka": "geo",
    "kk": "kaz",
    "kn": "kan",
    "ko": "kor",
    "ky": "kir",
    "la": "lat",
    "lt": "lit",
    "lv": "lav",
    "ml": "mal",
    "mn": "mon",
    "mr": "mar",
    "ms": "may",
    "my": "bur",
    "nl": "dut",
    "no": "nor",
    "pa": "pan",
    "pl": "pol",
    "pt": "por",
    "qu": "que",
    "ro": "rum",
    "ru": "rus",
    "rw": "kin",
    "sa": "san",
    "shn": "shn",
    "sk": "slo",
    "sl": "slv",
    "sq": "alb",
    "sr": "srp",
    "sv": "swe",
    "sw": "swa",
    "ta": "tam",
    "te": "tel",
    "th": "tha",
    "tr": "tur",
    "ug": "uig",
    "uk": "ukr",
    "ur": "urd",
    "vi": "vie",
    "zh": "chi",
    "zh-Hant": "chi",  # MARC has one code for both scripts
}

_NS = {
    "srw": "http://www.loc.gov/zing/srw/",
    "diag": "http://www.loc.gov/zing/srw/diagnostic/",
    "marc": "http://www.loc.gov/MARC21/slim",
}

# Films, music and microfiches share the catalog with books
_EXCLUDED_MATERIALS = "NOT (mat=film OR mat=music OR mat=microfiches)"

# DNB has no relevance ranking; "Most relevant" is its default order (newest first)
SORT_MAPPING: dict[SortOrder, str | None] = {
    SortOrder.RELEVANCE: None,
    SortOrder.NEWEST: "jhr/sort.descending",
    SortOrder.OLDEST: "jhr/sort.ascending",
}

# Pseudo-index for search-box words: title or person name ("woe" also matches subjects)
_TITLE_OR_NAME = "tit|per"

# Tried in front of search-box text
_LEADING_ARTICLES = ("Der", "Die", "Das", "Ein", "Eine")

# Dropped in the word-by-word fallback
_GERMAN_JOINERS = frozenset({"ein", "eine", "einer", "der", "die", "das", "und", "oder"})

# Publisher imprints DNB records as series (list from the Calibre DNB_DE plugin)
_UNWANTED_SERIES_RE = re.compile(
    # Names that start an imprint series
    r"^(?:Allgemeine|Ariadne-Krimi|Bastei[ -]L|Beck Paperback|Beck-.*berater|Beck'sche Reihe"
    r"|bt-Kinder|C.-Bertelsmann|C.H. Beck|dtv|Edition C|Ein Goldmann|Knaur|Premium|Ravensburger"
    r"|Rororo|Ullstein|Unionsverlag|Volk-und-Welt)"
    # Complete imprint names
    r"|^(?:Roman|Science-fiction|\[Ariadne\]|Ariadne|atb|BvT|bb|Bibliothek Suhrkamp|BLT"
    r"|DLV-Taschenbuch|Edition Suhrkamp|Edition Lingen Stiftung|Edition Metzgenstein|ETB"
    r"|Oettinger-Taschenbuch|Haymon-Taschenbuch|Mira Taschenbuch|Suhrkamp-Taschenbuch|Hey|btb"
    r"|Sammlung Luchterhand|blanvalet|KiWi|Piper|Goldmann|Moewig|Fischer Klassik|hey! shorties"
    r"|Phantastische Bibliothek|Horror-Bibliothek)$",
    flags=re.IGNORECASE,
)

_TRANSLATOR_SUFFIX_RE = re.compile(r"^(.+) [/:] [Aa]us dem .+? von(\s\w+)+$")
# "Angaben aus der Verlagsmeldung" header and the title heading after it
_BLANK_HTML = r"(?:\s|<br\s*/?>|<p>)*"
_PUBLISHER_NOTE_RE = re.compile(
    rf"{_BLANK_HTML}Angaben aus der Verlagsmeldung{_BLANK_HTML}(?:<h3>.*?</h3>)*{_BLANK_HTML}",
    flags=re.IGNORECASE,
)

_rate_limiter = RateLimiter(RATE_LIMIT_REQUESTS, RATE_LIMIT_WINDOW_SECONDS)


def _clean_text(value: str | None) -> str:
    """Drop non-sorting markers, collapse whitespace, compose Unicode.

    DNB sends decomposed umlauts ('a' + U+0308), which indexers wouldn't match.
    """
    if not value:
        return ""
    value = unicodedata.normalize("NFC", value.replace("\x98", "").replace("\x9c", ""))
    return " ".join(value.split())


def _strip_isbd(value: str) -> str:
    """Strip trailing ISBD punctuation (' /', ' :', ' ;') left on older records."""
    return value.rstrip(" /:;,=")


def _sanitize_term(value: str) -> str:
    """Remove characters that would break out of a quoted CQL term."""
    return " ".join(value.replace('"', " ").replace("\\", " ").split())


def _parse_year(value: str | None) -> int | None:
    """First plausible four-digit year in a date string ('[2022]', '©2022', '202611')."""
    match = re.search(r"\d{4}", value or "")
    if not match:
        return None
    year = int(match.group())
    return year if MIN_YEAR <= year <= MAX_YEAR else None


def _name_tokens(value: str) -> set[str]:
    """Comparable name parts: casefolded, transliterated, accents and initials dropped."""
    folded = unicodedata.normalize("NFC", value).casefold().translate(_UMLAUTS)
    decomposed = unicodedata.normalize("NFKD", folded)
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    return set(re.findall(r"\w{2,}", plain))


def _filter_by_author(books: list[BookMetadata], author: str) -> list[BookMetadata]:
    """Drop books by other people (DNB's person index also matches subjects).

    One shared name part keeps a book; a page without any match stays unfiltered (pseudonyms).
    """
    wanted = _name_tokens(author)
    matching = [book for book in books if any(wanted & _name_tokens(name) for name in book.authors)]
    return matching or books


def _book_languages(user_id: int | None) -> list[str]:
    """The user's Default Book Languages (else the system default), if DNB should use them."""
    if app_config.get("DNB_USE_BOOK_LANGUAGES", True) is not True:
        return []
    value = app_config.get("BOOK_LANGUAGE", [], user_id=user_id)
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, list):
        return []
    return [text for item in value if (text := str(item).strip())]


def _language_clause(languages: list[str]) -> str | None:
    """CQL language filter, or None for all; languages without a MARC code are skipped."""
    codes = dict.fromkeys(
        marc
        for language in languages
        if (marc := _MARC_LANGUAGE_CODES.get(normalize_language(language) or ""))
    )
    if not codes:
        return None
    return "(" + " OR ".join(f"spr={code}" for code in codes) + ")"


def _parse_number(value: str | None) -> float | None:
    if not value:
        return None
    match = re.search(r"\d+(?:[.,]\d+)?", value)
    if not match:
        return None
    return float(match.group().replace(",", "."))


def _valid_isbn(candidate: str) -> str | None:
    """Return a normalized ISBN-10/13 (digits, with optional X check digit), else None."""
    digits = candidate.replace("-", "").strip()
    if len(digits) == ISBN_13_LENGTH and digits.isdigit():
        return digits
    if len(digits) == 10 and re.fullmatch(r"\d{9}[\dXx]", digits):
        return digits.upper()
    return None


def _format_author(name: str) -> str:
    """Turn DNB's 'Rowling, J. K. [Hrsg.]' (or 'Rowling, J. K.,') into 'J. K. Rowling'."""
    name = re.sub(r" \[.*\]$", "", name).rstrip(" ,")
    return re.sub(r"^([^,]+), (.+)$", r"\2 \1", name)


def _clean_title(title: str) -> str:
    """Remove a trailing translator note ('... / aus dem Englischen von ...')."""
    match = _TRANSLATOR_SUFFIX_RE.search(title)
    return match.group(1) if match else title


def _drop_interpolations(value: str) -> str:
    """Remove cataloguer insertions: '20000 [Zwanzigtausend] Meilen' -> '20000 Meilen'."""
    cleaned = re.sub(r"\s*\[[^\]]*\]", "", value).strip(" ;:,/")
    return cleaned or value.strip("[] ")


def _clean_publisher(name: str | None) -> str | None:
    """Publisher without bracketed notes ('[u.a.]', '[Vertrieb]'); '[s. n.]' means none."""
    if not name:
        return None
    name = name.replace("@", "").strip()  # "@" is a sorting marker in older records
    if re.fullmatch(r"\[?s\.\s*n\.\]?", name, flags=re.IGNORECASE):  # sine nomine
        return None
    return _strip_isbd(_drop_interpolations(name)).strip("[] ") or None


def _clean_series(series: str | None, publisher: str | None) -> str | None:
    """Return the series name, or None if it is really a publisher imprint."""
    series = _strip_isbd(_clean_text(series))
    if not series:
        return None

    if publisher:
        if publisher.lower() == series.lower():
            return None
        match = re.search(r"^(\w\w\w\w+)", publisher)
        if match and re.search(r"^\W*" + re.escape(match.group(1)), series, flags=re.IGNORECASE):
            return None

    return None if _UNWANTED_SERIES_RE.search(series) else series


def _strip_german_joiners(words: list[str]) -> list[str]:
    return [word for word in words if word.lower() not in _GERMAN_JOINERS]


def _cql_term(index: str, value: str) -> str:
    if index == _TITLE_OR_NAME:
        return f'(tit="{value}" OR per="{value}")'
    return f'{index}="{value}"'


def _cql_words(index: str, value: str) -> str:
    words = value.split()
    if index != "per":
        words = _strip_german_joiners(words) or words
    return " AND ".join(_cql_term(index, word) for word in words)


def _build_cql_variants(terms: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """(hit-count probe, query) pairs from precise to loose; the first probe with hits wins."""
    if len(terms) == 1 and terms[0][0] == _TITLE_OR_NAME:
        return _search_box_variants(terms[0][1])
    exact = " AND ".join(_cql_term(index, value) for index, value in terms)
    loose = " AND ".join(_cql_words(index, value) for index, value in terms)
    return [(exact, exact)] if loose == exact else [(exact, exact), (loose, loose)]


def _search_box_variants(text: str) -> list[tuple[str, str]]:
    """Variants for search-box text (a title, a name, or both).

    DNB has no relevance ranking, so precision comes from the query. Title
    stages also include records where a word is a person's name:
    1. complete title, also with a leading article ("Das Boot", "Herr der Ringe")
    2. title phrase ("Remarque Im Westen nichts Neues")
    3. every word in a title or name ("Kafka Verwandlung")
    4. the same without German articles
    """
    words = text.split()
    every_word = " AND ".join(_cql_term(_TITLE_OR_NAME, word) for word in words)
    some_name = " OR ".join(_cql_term("per", word) for word in words)

    def with_names(title_match: str) -> tuple[str, str]:
        return title_match, f"({title_match} OR ({every_word} AND ({some_name})))"

    forms = [text]
    if words[0].lower() not in {article.lower() for article in _LEADING_ARTICLES}:
        forms += [f"{article} {text}" for article in _LEADING_ARTICLES]
    variants = [with_names("(" + " OR ".join(_cql_term("tst", form) for form in forms) + ")")]
    if len(words) > 1:
        variants.append(with_names(_cql_term("tit", text)))
    variants.append((every_word, every_word))
    loose = _cql_words(_TITLE_OR_NAME, text)
    if loose != every_word:
        variants.append((loose, loose))
    return variants


def _blurb_to_text(html: str) -> str | None:
    """Convert a DNB blurb page to plain text with paragraph breaks."""
    if "Zugriff derzeit nicht möglich" in html:
        return None
    # Only <br> and block ends separate lines
    html = _PUBLISHER_NOTE_RE.sub("", " ".join(html.split()))
    soup = BeautifulSoup(html, "html.parser")
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for block in soup.find_all(["p", "div", "h3"]):
        block.append("\n")
    lines = [" ".join(line.split()) for line in soup.get_text().splitlines()]
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return text or None


def _datafields(record: Element, tag: str) -> list[Element]:
    return record.findall(f"marc:datafield[@tag='{tag}']", _NS)


def _subfields(field: Element, code: str) -> list[str]:
    subfields = field.findall(f"marc:subfield[@code='{code}']", _NS)
    return [value for sub in subfields if (value := _clean_text(sub.text))]


def _first_subfield(field: Element, code: str) -> str | None:
    values = _subfields(field, code)
    return values[0] if values else None


def _record_idn(record: Element) -> str:
    """The record's IDN from 001, or from 016 on records without one."""
    idn = _clean_text(record.findtext("marc:controlfield[@tag='001']", namespaces=_NS))
    if idn:
        return idn
    return next(
        (value for field in _datafields(record, "016") for value in _subfields(field, "a")), ""
    )


def _record_isbns(record: Element) -> list[str]:
    """Every valid ISBN in 020 $a, in record order."""
    return [
        isbn
        for field in _datafields(record, "020")
        for value in _subfields(field, "a")
        for candidate in re.findall(r"[\dXx-]{10,17}", value)
        if (isbn := _valid_isbn(candidate))
    ]


def _as_isbn13(isbn: str) -> str:
    """ISBN-13 form of a normalized ISBN, so an ISBN-10 and its ISBN-13 compare equal."""
    if len(isbn) == ISBN_13_LENGTH:
        return isbn
    core = "978" + isbn[:9]
    checksum = sum(int(digit) * (3 if position % 2 else 1) for position, digit in enumerate(core))
    return core + str(-checksum % 10)


@register_provider("dnb")
class DNBProvider(MetadataProvider):
    """Deutsche Nationalbibliothek metadata provider (SRU API, MARC21-XML)."""

    name = "dnb"
    display_name = "DNB"
    requires_auth = False
    supported_sorts: ClassVar[tuple[SortOrder, ...]] = (
        SortOrder.RELEVANCE,
        SortOrder.NEWEST,
        SortOrder.OLDEST,
    )
    search_fields: ClassVar[tuple[SearchField, ...]] = (
        TextSearchField(
            key="author",
            label="Author",
            description="Search by author name",
        ),
        TextSearchField(
            key="title",
            label="Title",
            description="Search by book title",
        ),
    )

    def __init__(self) -> None:
        """Initialize provider."""
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT

    def is_available(self) -> bool:
        """DNB needs no authentication."""
        return True

    def _fetch(self, url: str, params: dict[str, Any] | None = None) -> requests.Response | None:
        _rate_limiter.wait_if_needed()
        try:
            response = self.session.get(
                url, params=params, timeout=15, proxies=get_proxies(url), verify=get_ssl_verify(url)
            )
            response.raise_for_status()
        except requests.Timeout:
            logger.warning("DNB request timed out: %s", url)
            return None
        except requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else "error"
            logger.warning("DNB request failed with HTTP %s: %s", status, url)
            return None
        except requests.RequestException as e:
            logger.warning("DNB request failed (%s): %s", e, url)
            return None
        return response

    def _query(
        self,
        cql: str,
        *,
        limit: int,
        start: int = 1,
        sort: SortOrder = SortOrder.RELEVANCE,
    ) -> tuple[list[Element], int] | None:
        """Run an SRU query: (records, total hits), or None on any failure (never cached)."""
        query = f"{cql} {_EXCLUDED_MATERIALS}"
        sort_key = SORT_MAPPING.get(sort)
        if sort_key:
            query += f" sortBy {sort_key}"

        response = self._fetch(
            DNB_SRU_URL,
            {
                "version": "1.1",
                "operation": "searchRetrieve",
                "recordSchema": "MARC21-xml",
                # Safety net; search_paginated clamps already
                "maximumRecords": max(0, min(limit, MAX_RECORDS_PER_REQUEST)),
                "startRecord": max(1, start),
                "query": query,
            },
        )
        if response is None:
            return None

        try:
            root = DefusedElementTree.fromstring(response.content)
        except DefusedElementTree.ParseError, DefusedXmlException:
            logger.warning("DNB returned an unparseable response for query: %s", cql)
            return None

        if root.tag != f"{{{_NS['srw']}}}searchRetrieveResponse":
            logger.warning("DNB returned a non-SRU response for query: %s", cql)
            return None

        total = root.findtext("srw:numberOfRecords", default="", namespaces=_NS).strip()
        if not total.isdigit():
            # SRU reports errors, including paging past the last hit, as diagnostics
            message = root.findtext(".//diag:message", default="", namespaces=_NS)
            logger.warning("DNB diagnostic for query '%s': %s", cql, message)
            return None

        records = root.findall("srw:records/srw:record/srw:recordData/marc:record", _NS)
        return records, int(total)

    def search(self, options: MetadataSearchOptions) -> list[BookMetadata]:
        """Search the DNB catalog."""
        return self.search_paginated(options).books

    def search_paginated(self, options: MetadataSearchOptions) -> SearchResult:
        """Search the DNB catalog with exact pagination from the SRU hit count."""
        # Clamp API input; a limit of 0 would page forever through empty pages
        options = replace(
            options,
            page=max(1, options.page),
            limit=max(1, min(options.limit, MAX_RECORDS_PER_REQUEST)),
        )
        # A pasted ISBN goes straight to the ISBN lookup
        looks_like_isbn = (
            options.search_type == SearchType.GENERAL
            and not options.fields
            and _valid_isbn(options.query.replace(" ", "")) is not None
        )
        if options.search_type == SearchType.ISBN or looks_like_isbn:
            books = [book] if (book := self.search_by_isbn(options.query)) else []
            return SearchResult(books=books, page=options.page, total_found=len(books))

        terms = self._search_terms(options)
        if not terms:
            return SearchResult(books=[], page=options.page)

        fields_key = ":".join(f"{k}={v}" for k, v in sorted(options.fields.items()))
        cache_key = (
            f"{options.query}:{options.search_type.value}:{options.sort.value}:"
            f"{options.limit}:{options.page}:{fields_key}"
        )
        queries = _build_cql_variants(terms)
        # A language passed with the search wins over the book languages
        language_clause = _language_clause(
            [options.language] if options.language else _book_languages(options.user_id)
        )
        if language_clause:
            queries = [
                (f"{probe} AND {language_clause}", f"{cql} AND {language_clause}")
                for probe, cql in queries
            ]
        author = dict(terms).get("per")
        return self._search_cached(cache_key, queries, author, options) or SearchResult(
            books=[], page=options.page
        )

    def _search_terms(self, options: MetadataSearchOptions) -> list[tuple[str, str]]:
        """(SRU index, value) pairs for the search: person, title, or search-box text."""
        author = _sanitize_term(options.fields.get("author") or "")
        title = _sanitize_term(options.fields.get("title") or "")
        query = _sanitize_term(options.query)

        if not author and not title:
            if options.search_type == SearchType.AUTHOR:
                author = query
            elif options.search_type == SearchType.TITLE:
                title = query

        terms: list[tuple[str, str]] = []
        if author:
            terms.append(("per", author))
        if title:
            terms.append(("tit", title))
        if not terms and query:
            terms.append((_TITLE_OR_NAME, query))
        return terms

    @cacheable(ttl_key="METADATA_CACHE_SEARCH_TTL", ttl_default=300, key_prefix="dnb:search")
    def _search_cached(
        self,
        cache_key: str,
        queries: list[tuple[str, str]],
        author: str | None,
        options: MetadataSearchOptions,
    ) -> SearchResult | None:
        # None on failure, so it isn't cached
        start = (options.page - 1) * options.limit + 1
        result = self._run_search(queries, limit=options.limit, start=start, sort=options.sort)
        if result is None:
            return None

        cql, records, total = result
        books = [book for record in records if (book := self._parse_record(record))]
        if author:
            books = _filter_by_author(books, author)
        logger.info("DNB search '%s' returned %s results", cql, len(books))
        # Paging follows the raw records, so filtered pages don't end it early
        return SearchResult(
            books=books,
            page=options.page,
            total_found=total,
            has_more=start - 1 + len(records) < total,
        )

    def _run_search(
        self, queries: list[tuple[str, str]], *, limit: int, start: int, sort: SortOrder
    ) -> tuple[str, list[Element], int] | None:
        """Run the first variant whose probe has hits (the last one unconditionally).

        Chosen by hit count, so every page uses the same variant; a variant that
        is its own probe uses its page 1 as the count.
        """
        for probe, cql in queries[:-1]:
            page_is_probe = start == 1 and probe == cql
            result = self._query(probe, limit=limit if page_is_probe else 0, sort=sort)
            if result is None:
                return None
            records, total = result
            if total and page_is_probe:
                return cql, records, total
            if total:
                break
        else:
            cql = queries[-1][1]

        result = self._query(cql, limit=limit, start=start, sort=sort)
        return None if result is None else (cql, *result)

    @cacheable(ttl_key="METADATA_CACHE_BOOK_TTL", ttl_default=600, key_prefix="dnb:book")
    def get_book(self, book_id: str) -> BookMetadata | None:
        """Get book details by DNB IDN (e.g. '1420086006')."""
        idn = book_id.strip().upper()
        if not re.fullmatch(r"\d+[\dX]", idn):
            return None
        result = self._query(f"idn={idn}", limit=1)
        records = result[0] if result else []
        record = next((record for record in records if _record_idn(record) == idn), None)
        return self._parse_full_record(record) if record is not None else None

    @cacheable(ttl_key="METADATA_CACHE_BOOK_TTL", ttl_default=600, key_prefix="dnb:isbn")
    def search_by_isbn(self, isbn: str) -> BookMetadata | None:
        """Search for a book by ISBN-10 or ISBN-13."""
        clean_isbn = _valid_isbn(isbn.replace(" ", ""))
        if not clean_isbn:
            return None
        # The number index also matches ISSNs etc.; require the ISBN itself (10 or 13)
        wanted = _as_isbn13(clean_isbn)
        result = self._query(f"num={clean_isbn}", limit=ISBN_LOOKUP_RECORDS)
        records = result[0] if result else []
        record = next(
            (
                record
                for record in records
                if wanted in {_as_isbn13(found) for found in _record_isbns(record)}
            ),
            None,
        )
        return self._parse_full_record(record) if record is not None else None

    def _parse_full_record(self, record: Element) -> BookMetadata | None:
        """Parse a record for the details view, including the linked blurb."""
        return self._parse_record(record, description=self._fetch_description(record))

    def _fetch_description(self, record: Element) -> str | None:
        """Fetch the publisher blurb linked from field 856, if any."""
        for field in _datafields(record, "856"):
            for url in _subfields(field, "u"):
                if not url.startswith(_BLURB_URL_PREFIXES):
                    continue
                _rate_limiter.wait_if_needed()
                try:
                    response = self.session.get(
                        url, timeout=15, proxies=get_proxies(url), verify=get_ssl_verify(url)
                    )
                    response.raise_for_status()
                except requests.RequestException as e:
                    logger.debug("DNB blurb fetch failed for %s: %s", url, e)
                    continue
                description = _blurb_to_text(response.text)
                if description:
                    return description
        return None

    def _parse_record(self, record: Element, description: str | None = None) -> BookMetadata | None:
        """Parse a MARC21 record into BookMetadata."""
        # Videos can slip past the material filter
        if any(
            (_first_subfield(field, "a") or "").lower() == "video"
            for field in _datafields(record, "337")
        ):
            return None

        idn = _record_idn(record)
        publisher, publish_year = self._parse_publication(record)
        title, subtitle, series_name, series_position = self._parse_title(record, publisher)
        authors = self._parse_authors(record)
        if not idn or not title or not authors:
            return None

        # Series: multi-part title, then 830/490, then a "(Series 3)" suffix (always stripped)
        if not series_name:
            series_name, series_position = self._parse_series_statement(record, publisher)
        guessed = self._guess_series_from_title(title, publisher)
        if guessed:
            title, guessed_name, guessed_position = guessed
            if not series_name:
                series_name, series_position = guessed_name, guessed_position

        isbns = _record_isbns(record)
        isbn_13 = next((isbn for isbn in isbns if len(isbn) == ISBN_13_LENGTH), None)
        isbn_10 = next((isbn for isbn in isbns if len(isbn) != ISBN_13_LENGTH), None)

        display_fields: list[DisplayField] = []
        pages = self._parse_pages(record)
        if pages:
            display_fields.append(DisplayField(label="Pages", value=pages, icon="book"))

        return BookMetadata(
            provider=self.name,
            provider_id=idn,
            provider_display_name=self.display_name,
            title=title,
            subtitle=subtitle,
            authors=authors,
            isbn_13=isbn_13,
            isbn_10=isbn_10,
            # Blurb first, 520 summary as fallback
            description=description or self._parse_summary(record),
            publisher=publisher,
            publish_year=publish_year,
            language=self._parse_language(record),
            genres=self._parse_subjects(record)[:5],
            source_url=DNB_PERMALINK_URL + idn,
            search_title=title,
            search_author=authors[0],
            series_name=series_name,
            series_position=series_position,
            display_fields=display_fields,
        )

    def _parse_title(
        self, record: Element, publisher: str | None
    ) -> tuple[str | None, str | None, str | None, float | None]:
        """Parse field 245 into (title, subtitle, series, series position).

        Multi-part works are catalogued as '$a Series $n 3 $p Part title'.
        """
        fields = _datafields(record, "245")
        if not fields:
            return None, None, None, None
        field = fields[0]

        code_a = [_strip_isbd(_drop_interpolations(value)) for value in _subfields(field, "a")]
        code_n = [
            match.group()
            for value in _subfields(field, "n")
            if (match := re.search(r"\d+(?:[,.]\d+)?", value))
        ]
        code_p = [_strip_isbd(_drop_interpolations(value)) for value in _subfields(field, "p")]

        title_parts = code_a
        series = None
        position = None
        if code_a and code_n:
            if code_p:
                title_parts = [code_p[-1]]
            series_parts = [code_a[0], *code_p[: min(len(code_p), len(code_n)) - 1]]
            for i in range(min(len(series_parts), len(code_n) - 1)):
                series_parts[i] += f" {code_n[i]}"
            series = _clean_series(" - ".join(series_parts), publisher)
            position = _parse_number(code_n[-1])

        title = _clean_title(" : ".join(part for part in title_parts if part)) or None
        subtitle = _first_subfield(field, "b")
        subtitle = _clean_title(_strip_isbd(_drop_interpolations(subtitle))) if subtitle else None
        return title, subtitle or None, series, position if series else None

    def _parse_series_statement(
        self, record: Element, publisher: str | None
    ) -> tuple[str | None, float | None]:
        """Series from 830 (authorized form) or 490 (as printed), with a volume number."""
        for tag in ("830", "490"):
            for field in _datafields(record, tag):
                position = _parse_number(_first_subfield(field, "v"))
                if position is None:
                    continue
                series = _clean_series(_first_subfield(field, "a"), publisher)
                if series:
                    return series, position
        return None, None

    def _guess_series_from_title(
        self, title: str, publisher: str | None
    ) -> tuple[str, str, float] | None:
        """Split 'Title (Series 3)' into (title, series, position)."""
        parts = title.split(" (")
        if len(parts) != 2:
            return None
        text_part, index_part = parts
        try:
            series, index = index_part.split(")", 1)[0].rsplit(" ", 1)
        except ValueError:
            return None
        series = _clean_series(series, publisher)
        position = _parse_number(index) if index[-1:].isdigit() else None
        if not series or position is None:
            return None
        text_part = text_part.strip(" -–—:")
        return (text_part or title), series, position

    def _parse_authors(self, record: Element) -> list[str]:
        """Authors (100/700 with $4 aut), else everyone except translators, narrators etc."""
        persons = [*_datafields(record, "100"), *_datafields(record, "700")]
        authors = [field for field in persons if "aut" in _subfields(field, "4")]
        if not authors:
            authors = [
                field
                for field in persons
                if not _NON_AUTHOR_RELATORS.intersection(_subfields(field, "4"))
            ]
        names = [name for field in authors for name in _subfields(field, "a")]
        return list(dict.fromkeys(_format_author(name) for name in names))

    def _parse_publication(self, record: Element) -> tuple[str | None, int | None]:
        """Publisher and year.

        - Publisher only from publication statements (264 ind2=1, 260), never printers.
        - Year from those first, then other 264s, 263 (announced) and 008.
        """
        statements = sorted(
            [*_datafields(record, "264"), *_datafields(record, "260")],
            key=lambda field: (field.get("tag"), field.get("ind2")) != ("264", "1"),
        )
        publisher = None
        year = None
        for field in statements:
            is_publication = field.get("tag") == "260" or field.get("ind2") == "1"
            if is_publication and not publisher:
                name = _first_subfield(field, "b")
                publisher = _clean_publisher(name)
            year = year or _parse_year(_first_subfield(field, "c"))
        for field in _datafields(record, "263"):
            year = year or _parse_year(_first_subfield(field, "a"))
        if not year:
            # Positions 7-10 hold the (first) publication date
            fixed = record.findtext("marc:controlfield[@tag='008']", default="", namespaces=_NS)
            year = _parse_year(fixed[7:11])
        return publisher or None, year

    def _parse_language(self, record: Element) -> str | None:
        for field in _datafields(record, "041"):
            for code in _subfields(field, "a"):
                language = normalize_language(code)
                if language:
                    return language
        # 008 positions 35-37
        fixed = record.findtext("marc:controlfield[@tag='008']", default="", namespaces=_NS)
        return normalize_language(fixed[35:38]) if len(fixed) >= 38 else None

    def _parse_subjects(self, record: Element) -> list[str]:
        """GND subject headings first, then other 6XX subject terms."""
        gnd: list[str] = []
        other: list[str] = []
        for field in _datafields(record, "689"):
            gnd.extend(_subfields(field, "a"))
        for field in record.findall("marc:datafield", _NS):
            tag = field.get("tag", "")
            # 653: publisher keywords, not subjects
            if not (tag.isdigit() and 600 <= int(tag) <= 655) or tag == "653":
                continue
            # Skip qualified terms like (Produktform)Paperback
            terms = [term for term in _subfields(field, "a") if not term.startswith("(")]
            if "gnd" in _subfields(field, "2"):
                gnd.extend(terms)
            for term in terms:
                other.extend(part.strip() for part in re.split(r"[,;]", term))
        return list(dict.fromkeys(term for term in [*gnd, *other] if len(term) >= 2))

    def _parse_pages(self, record: Element) -> str | None:
        for field in _datafields(record, "300"):
            for extent in _subfields(field, "a"):
                match = _PAGES_RE.search(extent)
                if match and 0 < (pages := int(match.group(1).replace(".", ""))) <= MAX_PAGES:
                    return str(pages)
        return None

    def _parse_summary(self, record: Element) -> str | None:
        summaries = [
            text for field in _datafields(record, "520") for text in _subfields(field, "a")
        ]
        summary = "\n\n".join(summaries)
        # Some summaries are HTML
        return (_blurb_to_text(summary) if "<" in summary else summary) or None


def _test_dnb_connection() -> dict[str, Any]:
    """Test connectivity to the DNB SRU interface."""
    if DNBProvider()._query('tit="test"', limit=0) is None:
        return {"success": False, "message": "Could not reach the DNB catalog (details in the log)"}
    return {"success": True, "message": "Successfully connected to the DNB catalog"}


# DNB sort options for settings UI
_DNB_SORT_OPTIONS = [
    {"value": "relevance", "label": "Most relevant"},
    {"value": "newest", "label": "Newest"},
    {"value": "oldest", "label": "Oldest"},
]


@register_settings("dnb", "DNB", icon="library", order=55, group="metadata_providers")
def dnb_settings() -> list[SettingsField]:
    """DNB metadata provider settings."""
    return [
        HeadingField(
            key="dnb_heading",
            title="Deutsche Nationalbibliothek",
            description=(
                "The German National Library catalogs every book published in Germany, "
                "plus German-language and translated works from abroad. "
                "No API key required."
            ),
            link_url="https://www.dnb.de",
            link_text="dnb.de",
        ),
        CheckboxField(
            key="DNB_ENABLED",
            label="Enable DNB",
            description="Enable DNB as a metadata provider for book searches",
            default=False,
        ),
        ActionButton(
            key="test_connection",
            label="Test Connection",
            description="Verify the DNB catalog is accessible",
            style="primary",
            callback=_test_dnb_connection,
        ),
        SelectField(
            key="DNB_DEFAULT_SORT",
            label="Default Sort Order",
            description="Default sort order for DNB search results.",
            options=_DNB_SORT_OPTIONS,
            default="relevance",
        ),
        CheckboxField(
            key="DNB_USE_BOOK_LANGUAGES",
            label="Only show editions in my book languages",
            description=(
                "Filter DNB results by Default Book Languages: each user's own setting, else "
                "the system default. DNB also catalogs translations into many other languages."
            ),
            default=True,
        ),
    ]
