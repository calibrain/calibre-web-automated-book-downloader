"""Keyless Deutsche Nationalbibliothek SRU provider (MARC21-xml, SRU 1.1)."""

import re
import unicodedata
from typing import TYPE_CHECKING, ClassVar
from xml.etree.ElementTree import ParseError

import requests
from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

from shelfmark.core.cache import cacheable
from shelfmark.core.languages import normalize_language
from shelfmark.core.logger import setup_logger
from shelfmark.core.settings_registry import (
    CheckboxField,
    HeadingField,
    SettingsField,
    register_settings,
)
from shelfmark.download.network import get_ssl_verify
from shelfmark.metadata_providers import (
    BookMetadata,
    DisplayField,
    MetadataProvider,
    MetadataSearchOptions,
    SearchField,
    SearchResult,
    SearchType,
    TextSearchField,
    register_provider,
)

if TYPE_CHECKING:
    from xml.etree.ElementTree import Element

logger = setup_logger(__name__)
DNB_URL = "https://services.dnb.de/sru/dnb"
_SRU = "{http://www.loc.gov/zing/srw/}"
_MARC = "{http://www.loc.gov/MARC21/slim}"
_SORT_CONTROLS = dict.fromkeys(map(ord, "\u0088\u0089\u008c\u0098\u0099\u009c"))


class _DnbRequestError(Exception):
    """Unusable SRU response; raised inside the cache to avoid caching failures."""


def _text(value: str | None) -> str:
    """XML has already decoded entities; strip MARC sort markers and compose accents."""
    return unicodedata.normalize("NFC", " ".join((value or "").translate(_SORT_CONTROLS).split()))


def _values(node: Element, tag: str, code: str) -> list[str]:
    return [
        value
        for sub in node.findall(f"{_MARC}datafield[@tag='{tag}']/{_MARC}subfield[@code='{code}']")
        if (value := _text(sub.text))
    ]


def _first(node: Element, tag: str, code: str) -> str | None:
    return next(iter(_values(node, tag, code)), None)


def _isbn(value: str) -> str:
    return re.sub(r"[-\s]", "", value).upper()


def _record_isbns(node: Element) -> list[str]:
    # $9 is a display form, not a replacement for the repeatable $a identifiers.
    return [
        match.group(0)
        for value in _values(node, "020", "a")
        if (match := re.match(r"(?:97[89]\d{10}|\d{9}[\dX])(?![\dX])", _isbn(value)))
    ]


def _author_key(value: str) -> set[str]:
    # Compare both "Last, First" and "First Last" without changing catalogue display names.
    return set(re.findall(r"\w+", _text(value).casefold()))


def _map_record(node: Element) -> BookMetadata | None:
    identifier = _text(node.findtext(f"{_MARC}controlfield[@tag='001']"))
    title = _first(node, "245", "a")
    if not identifier or not title:
        return None

    rest = " : ".join(_values(node, "245", "b"))
    subtitle = None
    if rest:
        if re.search(r"[a-zäöüß][.!?]$", title):
            title, subtitle = title[:-1], rest
        elif rest[0].islower():
            title = f"{title} {rest}"
        else:
            subtitle = rest

    authors = []
    for person in node.findall(f"{_MARC}datafield"):
        tag = person.get("tag")
        if tag not in {"100", "700"}:
            continue
        relators = {_text(sub.text) for sub in person.findall(f"{_MARC}subfield[@code='4']")}
        if "trl" in relators or not ("aut" in relators or (tag == "100" and not relators)):
            continue
        name = _text(person.findtext(f"{_MARC}subfield[@code='a']"))
        if name and name not in authors:
            authors.append(name)

    isbns = _record_isbns(node)
    date = _first(node, "264", "c") or _first(node, "260", "c") or ""
    year = re.search(r"\b(\d{4})\b", date)
    publish_year = int(year[1]) if year and 1400 <= int(year[1]) <= 2200 else None
    language = _first(node, "041", "a")
    if not language:
        # Fixed-position fields must not be whitespace-normalized before slicing.
        fixed = node.findtext(f"{_MARC}controlfield[@tag='008']", "")
        language = fixed[35:38]

    display_fields = []
    extent = " ".join(_values(node, "300", "a"))
    pages = re.search(r"(\d[\d.\s]*)\s*(?:Seiten|S\.|pages?)", extent, re.IGNORECASE)
    if pages and 0 < (count := int(re.sub(r"\D", "", pages[1]))) <= 100_000:
        display_fields.append(DisplayField(label="Pages", value=str(count), icon="book"))

    series_name = _first(node, "490", "a") or _first(node, "830", "a")
    volume = _first(node, "490", "v") or _first(node, "830", "v") or ""
    position = re.search(r"\d+(?:[.,]\d+)?", volume)
    return BookMetadata(
        provider="dnb",
        provider_display_name="Deutsche Nationalbibliothek",
        provider_id=identifier,
        title=title,
        subtitle=subtitle,
        authors=authors,
        isbn_10=next((value for value in isbns if len(value) == 10), None),
        isbn_13=next((value for value in isbns if len(value) == 13), None),
        publisher=_first(node, "264", "b") or _first(node, "260", "b"),
        publish_year=publish_year,
        language=normalize_language(language),
        description=" ".join(_values(node, "520", "a")) or None,
        genres=list(dict.fromkeys(_values(node, "655", "a"))),
        series_name=series_name,
        series_position=float(position[0].replace(",", ".")) if position else None,
        source_url=f"https://d-nb.info/{identifier}",
        display_fields=display_fields,
    )


def _quote(value: str) -> str:
    return '"' + _text(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


@register_provider("dnb")
class DnbProvider(MetadataProvider):
    """Search the German national bibliography without an API key."""

    name = "dnb"
    display_name = "Deutsche Nationalbibliothek"
    requires_auth = False
    search_fields: ClassVar[tuple[SearchField, ...]] = (
        TextSearchField(key="title", label="Title", description="Search by book title"),
        TextSearchField(key="author", label="Author", description="Search by author name"),
    )

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "Shelfmark (DNB metadata provider)"

    def is_available(self) -> bool:
        return True

    def search(self, options: MetadataSearchOptions) -> list[BookMetadata]:
        return self.search_paginated(options).books

    def search_paginated(self, options: MetadataSearchOptions) -> SearchResult:
        page = max(1, options.page)
        empty = SearchResult(books=[], page=page)
        if options.search_type == SearchType.ISBN:
            book = self.search_by_isbn(options.query) if page == 1 else None
            return SearchResult(
                books=[book] if book else [], page=page, total_found=int(bool(book))
            )

        title = _text(str(options.fields.get("title") or ""))
        author = _text(str(options.fields.get("author") or ""))
        query = _text(options.query)
        if options.search_type == SearchType.TITLE and not title:
            title = query
        if options.search_type == SearchType.AUTHOR and not author:
            author = query
        if title:
            queries = [f"tit={_quote(title)}"]
            if author:
                queries.insert(0, f"tit={_quote(title)} and per={_quote(author)}")
        elif author:
            queries = [f"per={_quote(author)}"]
        elif query:
            queries = [f"woe={_quote(query)}"]
        else:
            return empty

        limit = max(1, min(options.limit, 100))
        start = (page - 1) * limit + 1
        try:
            for cql in queries:
                records, total = self._request(cql, limit, start)
                books = [book for node in records if (book := _map_record(node))]
                if author:
                    books = [
                        book
                        for book in books
                        if any(_author_key(author) <= _author_key(name) for name in book.authors)
                    ]
                if options.language:
                    language = normalize_language(options.language)
                    books = [book for book in books if book.language == language]
                if books or cql == queries[-1]:
                    return SearchResult(
                        books=books,
                        page=page,
                        total_found=total,
                        has_more=start + limit <= total,
                    )
        except _DnbRequestError:
            return empty
        return empty

    def search_by_isbn(self, isbn: str) -> BookMetadata | None:
        digits = _isbn(isbn)
        if not re.fullmatch(r"(?:97[89]\d{10}|\d{9}[\dX])", digits):
            return None
        try:
            records, _ = self._request(f"num={digits}", 1, 1)
        except _DnbRequestError:
            return None
        return next((_map_record(node) for node in records if digits in _record_isbns(node)), None)

    def get_book(self, book_id: str) -> BookMetadata | None:
        if not re.fullmatch(r"\d+[\dXx]", book_id):
            return None
        try:
            records, _ = self._request(f"idn={_quote(book_id)}", 1, 1)
        except _DnbRequestError:
            return None
        return next(
            (
                book
                for node in records
                if (book := _map_record(node)) and book.provider_id == book_id
            ),
            None,
        )

    @cacheable(ttl_key="METADATA_CACHE_SEARCH_TTL", ttl_default=300, key_prefix="dnb:sru")
    def _request(self, query: str, limit: int, start: int) -> tuple[list[Element], int]:
        try:
            reply = self.session.get(
                DNB_URL,
                params={
                    "version": "1.1",
                    "operation": "searchRetrieve",
                    "recordSchema": "MARC21-xml",
                    "query": query,
                    "maximumRecords": limit,
                    "startRecord": start,
                },
                timeout=15,
                verify=get_ssl_verify(DNB_URL),
            )
            reply.raise_for_status()
            root = ElementTree.fromstring(reply.content)
            total = root.findtext(f"{_SRU}numberOfRecords")
            if (
                root.tag != f"{_SRU}searchRetrieveResponse"
                or total is None
                or any(node.tag.rsplit("}", 1)[-1] == "diagnostics" for node in root.iter())
            ):
                raise _DnbRequestError("Invalid SRU response or diagnostic")
            records = root.findall(f"{_SRU}records/{_SRU}record/{_SRU}recordData/{_MARC}record")
            return records, int(total)
        except (requests.RequestException, ParseError, DefusedXmlException, ValueError) as exc:
            logger.warning("DNB SRU request failed (%s)", type(exc).__name__)
            raise _DnbRequestError from exc


@register_settings(
    "dnb", "Deutsche Nationalbibliothek", icon="book", order=50, group="metadata_providers"
)
def dnb_settings() -> list[SettingsField]:
    """DNB is opt-in like the other keyless metadata providers."""
    return [
        HeadingField(
            key="dnb_heading",
            title="Deutsche Nationalbibliothek",
            description="German national bibliography. No API key required. No cover images.",
            link_url="https://www.dnb.de/EN/Professionell/Metadatendienste/Datenbezug/SRU/sru_node.html",
            link_text="DNB SRU documentation",
        ),
        CheckboxField(
            key="DNB_ENABLED",
            label="Enable Deutsche Nationalbibliothek",
            description="Use DNB for German-language book searches.",
            default=False,
        ),
    ]
