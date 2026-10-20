"""Databáze knih metadata provider. Czech and Slovak book catalog, no API key required.

databazeknih.cz has no public API, so this reads the web pages. Book and author
pages carry schema.org JSON-LD, which is the primary source; the rest is HTML.

Pages used (ids are the number at the end of the slug, the site redirects
``/prehled-knihy/kniha-30482`` to the full slug):
    /vyhledavani/knihy?q=krev+elfu&page=2     title search, 20 per page
    /search?q=80-85951-05-3                    ISBN search, redirects to the book
    /prehled-knihy/zaklinac-saga-krev-elfu-30482
    /book-detail-more-info/30482               pages, original title, ISBN
    /search?in=authors&q=sapkowski             author search
    /vydane-knihy/andrzej-sapkowski-269        an author's books, 20 per page
"""

import json
import re
import unicodedata
from typing import Any, ClassVar
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup, Tag

from shelfmark.core.cache import cacheable
from shelfmark.core.logger import setup_logger
from shelfmark.core.settings_registry import (
    ActionButton,
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
    SearchType,
    SortOrder,
    TextSearchField,
    register_provider,
)
from shelfmark.metadata_providers.openlibrary import RateLimiter

logger = setup_logger(__name__)

DBK_BASE_URL = "https://www.databazeknih.cz"
DBK_BOOK_URL = f"{DBK_BASE_URL}/prehled-knihy/kniha-"
DBK_MORE_INFO_URL = f"{DBK_BASE_URL}/book-detail-more-info/"
DBK_SEARCH_URL = f"{DBK_BASE_URL}/vyhledavani/knihy?"
DBK_SITE_SEARCH_URL = f"{DBK_BASE_URL}/search?"
DBK_AUTHOR_BOOKS_URL = f"{DBK_BASE_URL}/vydane-knihy/autor-"

# The site publishes no limit; stay well below anything a reader would notice
RATE_LIMIT_REQUESTS = 30
RATE_LIMIT_WINDOW_SECONDS = 60

REQUEST_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"),
    "Accept-Language": "cs,sk;q=0.9,en;q=0.7",
}

ISBN_13_LENGTH = 13
AUTHOR_MATCH_THRESHOLD = 0.9

# Search 'lang' filter values, keyed by ISO 639-1 code
_SEARCH_LANGUAGES = {"cs": "cz", "sk": "sk", "en": "en", "de": "de"}

# 'Jazyk vydání' values on the more-info fragment, used when JSON-LD has no inLanguage
_LANGUAGE_NAMES = {
    "český": "cs",
    "slovenský": "sk",
    "anglický": "en",
    "německý": "de",
    "polský": "pl",
    "ruský": "ru",
    "francouzský": "fr",
    "španělský": "es",
    "italský": "it",
    "maďarský": "hu",
    "ukrajinský": "uk",
}

_rate_limiter = RateLimiter(RATE_LIMIT_REQUESTS, RATE_LIMIT_WINDOW_SECONDS)


def _clean_text(value: str | None) -> str:
    """Collapse whitespace."""
    return " ".join((value or "").split())


def _clean_author(name: str | None) -> str:
    """Drop the site's pseudonym marker, e.g. 'Jason Dark (p)'."""
    return _clean_text(re.sub(r"\s*\(p\)\s*$", "", name or ""))


def _normalize_for_match(value: str | None) -> str:
    """Accent-insensitive, punctuation-insensitive comparison form."""
    if not value:
        return ""
    # 'ł' has no combining accent, so NFKD would keep it
    value = unicodedata.normalize("NFKD", value.translate({0x141: "L", 0x142: "l"}))
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = "".join(char if char.isalnum() else " " for char in value)
    return " ".join(value.lower().split())


def _names_match(a: str, b: str) -> bool:
    """Name match ignoring accents and word order ('Sapkowski Andrzej')."""
    return sorted(_normalize_for_match(a).split()) == sorted(_normalize_for_match(b).split())


def _id_from_link(link: str | None) -> str:
    """Return the number at the end of a link like /prehled-knihy/krev-elfu-433?lang=cz."""
    match = re.search(r"-(\d+)/?(?:[?#].*)?$", link or "")
    return match.group(1) if match else ""


def _absolute_url(url: str | None) -> str | None:
    if not url:
        return None
    if url.startswith(("http://", "https://")):
        return url
    return DBK_BASE_URL + "/" + url.lstrip("/")


def _valid_isbn(candidate: str | None) -> str | None:
    """Return a normalized ISBN-10/13 (digits, with optional X check digit), else None."""
    digits = re.sub(r"[\s-]", "", candidate or "").upper()
    if len(digits) == ISBN_13_LENGTH and digits.isdigit():
        return digits
    if re.fullmatch(r"\d{9}[\dX]", digits):
        return digits
    return None


def _json_ld(soup: BeautifulSoup, wanted_type: str) -> dict[str, Any]:
    """Return the schema.org JSON-LD block of the wanted type, e.g. Book or Person."""
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except ValueError:
            continue
        for item in data if isinstance(data, list) else [data]:
            if isinstance(item, dict) and item.get("@type") == wanted_type:
                return item
    return {}


def _ld_names(value: object) -> list[str]:
    """JSON-LD author/publisher can be an object, a list of objects or text."""
    if not value:
        return []
    names: list[str] = []
    for item in value if isinstance(value, list) else [value]:
        name = _clean_text(item.get("name", "") if isinstance(item, dict) else str(item))
        if name and name not in names:
            names.append(name)
    return names


def _cover_or_none(url: str | None) -> str | None:
    """The site serves a placeholder image for books without a cover."""
    if not url or "nobook" in url or "noimage" in url:
        return None
    return _absolute_url(url)


@register_provider("databazeknih")
class DatabazeKnihProvider(MetadataProvider):
    """Databáze knih metadata provider (HTML scraping, Czech and Slovak catalog)."""

    name = "databazeknih"
    display_name = "Databáze knih"
    requires_auth = False
    supported_sorts: ClassVar[tuple[SortOrder, ...]] = (SortOrder.RELEVANCE,)
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
        self.session.headers.update(REQUEST_HEADERS)

    def is_available(self) -> bool:
        """Databáze knih needs no authentication."""
        return True

    def _fetch(self, url: str, timeout: int = 15) -> str | None:
        _rate_limiter.wait_if_needed()
        try:
            response = self.session.get(url, timeout=timeout, verify=get_ssl_verify(DBK_BASE_URL))
            response.raise_for_status()
        except requests.Timeout:
            logger.warning("Databáze knih request timed out: %s", url)
            return None
        except requests.RequestException:
            logger.exception("Databáze knih request failed: %s", url)
            return None
        return response.text

    # ------------------------------------------------------------------ search

    def search(self, options: MetadataSearchOptions) -> list[BookMetadata]:
        """Search databazeknih.cz by title, or list an author's books."""
        if options.search_type == SearchType.ISBN:
            result = self.search_by_isbn(options.query)
            return [result] if result else []

        author_value = _clean_text(options.fields.get("author"))
        title_value = _clean_text(options.fields.get("title"))
        query = _clean_text(options.query)
        if options.search_type == SearchType.AUTHOR and not author_value:
            author_value, query = query, ""
        if not (author_value or title_value or query):
            return []

        fields_key = ":".join(f"{k}={v}" for k, v in sorted(options.fields.items()))
        cache_key = (
            f"{query}:{options.search_type.value}:{options.language}:"
            f"{options.page}:{options.limit}:{fields_key}"
        )
        return (
            self._search_cached(
                cache_key,
                query,
                title_value,
                author_value,
                options.language,
                options.page,
                options.limit,
            )
            or []
        )

    @cacheable(
        ttl_key="METADATA_CACHE_SEARCH_TTL", ttl_default=300, key_prefix="databazeknih:search"
    )
    def _search_cached(
        self,
        cache_key: str,
        query: str,
        title: str,
        author: str,
        language: str | None,
        page: int,
        limit: int,
    ) -> list[BookMetadata] | None:
        # Return None (not []) on fetch failure so the failure is not cached.
        if author and not title:
            # The site search only matches titles, so list the author's books instead
            books = self._search_author_books(author, page)
        else:
            books = self._search_titles(title or query, language, page)
            if books is not None and author:
                books = [b for b in books if any(_names_match(a, author) for a in b.authors)]
        if books is None:
            return None
        logger.info(
            "Databáze knih search '%s' returned %s results",
            " / ".join(t for t in (author, title or query) if t),
            len(books),
        )
        return books[:limit]

    def _search_titles(
        self, query: str, language: str | None, page: int
    ) -> list[BookMetadata] | None:
        params: dict[str, str | int] = {"q": query}
        site_language = _SEARCH_LANGUAGES.get((language or "").lower()[:2])
        if site_language:
            params["lang"] = site_language
        if page > 1:
            params["page"] = page
        html = self._fetch(DBK_SEARCH_URL + urlencode(params))
        if html is None:
            return None
        soup = BeautifulSoup(html, "html.parser")

        # A search with a single match can redirect straight to the book
        book = self._parse_book(soup)
        if book is not None:
            return [book] if page == 1 else []

        return self._parse_search_results(soup)

    def _parse_search_results(self, soup: BeautifulSoup) -> list[BookMetadata]:
        books: list[BookMetadata] = []
        seen: set[str] = set()
        for link in soup.find_all("a", attrs={"type": "book"}, href=re.compile(r"/prehled-knihy/")):
            book_id = _id_from_link(str(link.get("href")))
            title = _clean_text(link.get_text(" "))
            if not book_id or not title or book_id in seen:
                continue
            entry = link.find_parent("p") or link.parent

            year = None
            authors: list[str] = []
            note = entry.find("span", class_="pozn") if entry else None
            if note:
                # e.g. '1998, Andrzej Sapkowski'
                text = _clean_text(note.get_text(" "))
                match = re.match(r"^(\d{4})\s*,\s*(.*)$", text)
                if match:
                    year = int(match.group(1))
                    text = match.group(2)
                authors = [_clean_author(n) for n in text.split(",") if _clean_author(n)]

            display_fields: list[DisplayField] = []
            square = entry.find("img", title=re.compile(r"^\d+%$")) if entry else None
            if square:
                display_fields.append(
                    DisplayField(label="Rating", value=str(square["title"]), icon="star")
                )

            cover = entry.find("img", class_=re.compile("img_39")) if entry else None

            seen.add(book_id)
            books.append(
                BookMetadata(
                    provider=self.name,
                    provider_id=book_id,
                    provider_display_name=self.display_name,
                    title=title,
                    authors=authors,
                    cover_url=_cover_or_none(str(cover.get("src") or "")) if cover else None,
                    publish_year=year,
                    source_url=DBK_BOOK_URL + book_id,
                    search_title=title,
                    search_author=authors[0] if authors else None,
                    display_fields=display_fields,
                )
            )
        return books

    def _search_author_books(self, author: str, page: int) -> list[BookMetadata] | None:
        author_id = self._find_author(author)
        if author_id is None:
            return None
        if not author_id:
            return []
        url = DBK_AUTHOR_BOOKS_URL + author_id
        if page > 1:
            url += f"?{urlencode({'page': page})}"
        html = self._fetch(url)
        if html is None:
            return None
        return self._parse_author_books(BeautifulSoup(html, "html.parser"), author)

    def _find_author(self, author: str) -> str | None:
        """Return the site's id for the author, '' when not found, None on fetch failure."""
        html = self._fetch(DBK_SITE_SEARCH_URL + urlencode({"in": "authors", "q": author}))
        if html is None:
            return None
        soup = BeautifulSoup(html, "html.parser")
        # A search with a single match can redirect straight to the author
        person = _json_ld(soup, "Person")
        if person.get("url"):
            return _id_from_link(str(person["url"]))
        for link in soup.find_all("a", href=re.compile(r"^/?autori/")):
            if _names_match(_clean_text(link.get_text(" ")), author):
                return _id_from_link(str(link.get("href")))
        return ""

    def _parse_author_books(self, soup: BeautifulSoup, author: str) -> list[BookMetadata]:
        books: list[BookMetadata] = []
        for heading in soup.find_all("h3"):
            link = heading.find("a", href=re.compile(r"/prehled-knihy/"))
            if not link:
                continue
            book_id = _id_from_link(str(link.get("href")))
            title = _clean_text(link.get_text(" "))
            if not book_id or not title:
                continue
            year = None
            note = heading.find("span", class_="pozn")
            if note:
                # e.g. '2011 (1. vydání 1994)', or '2002 (1. vydání)' for a first edition
                text = _clean_text(note.get_text(" "))
                match = re.search(r"1\. vydání (\d{4})", text) or re.match(r"(\d{4})", text)
                if match:
                    year = int(match.group(1))
            books.append(
                BookMetadata(
                    provider=self.name,
                    provider_id=book_id,
                    provider_display_name=self.display_name,
                    title=title,
                    authors=[author],
                    publish_year=year,
                    source_url=DBK_BOOK_URL + book_id,
                    search_title=title,
                    search_author=author,
                )
            )
        return books

    # ------------------------------------------------------------------ book

    @cacheable(ttl_key="METADATA_CACHE_BOOK_TTL", ttl_default=600, key_prefix="databazeknih:book")
    def get_book(self, book_id: str) -> BookMetadata | None:
        """Get book details by databazeknih.cz book id (e.g. '30482')."""
        book_id = _id_from_link(book_id) or book_id.strip()
        if not book_id.isdigit():
            return None
        html = self._fetch(DBK_BOOK_URL + book_id)
        if html is None:
            return None
        book = self._parse_book(BeautifulSoup(html, "html.parser"))
        if book is None:
            logger.warning("Databáze knih book page missing title/authors: %s", book_id)
            return None
        more = self._fetch(DBK_MORE_INFO_URL + book_id)
        if more:
            self._apply_more_info(book, BeautifulSoup(more, "html.parser"))
        return book

    @cacheable(ttl_key="METADATA_CACHE_BOOK_TTL", ttl_default=600, key_prefix="databazeknih:isbn")
    def search_by_isbn(self, isbn: str) -> BookMetadata | None:
        """The site search redirects a known ISBN to the book page."""
        isbn = _valid_isbn(isbn) or ""
        if not isbn:
            return None
        html = self._fetch(DBK_SITE_SEARCH_URL + urlencode({"q": isbn}))
        if html is None:
            return None
        book = self._parse_book(BeautifulSoup(html, "html.parser"))
        if book is None:
            return None
        return self.get_book(book.provider_id)

    def _parse_book(self, soup: BeautifulSoup) -> BookMetadata | None:
        """Parse a book page from its JSON-LD, or None if this is not a book page."""
        data = _json_ld(soup, "Book")
        if not data:
            return None
        book_id = _id_from_link(str(data.get("url", "")))
        if not book_id:
            # Pages without a JSON-LD url still carry the id on the rating buttons
            tag = soup.select_one("[data-typeid]")
            book_id = str(tag["data-typeid"]) if tag else ""
        title = _clean_text(str(data.get("name", "")))
        authors = [_clean_author(n) for n in _ld_names(data.get("author"))]
        authors = [a for a in authors if a]
        if not book_id or not title or not authors:
            return None

        # The page has the whole description, the JSON-LD one is cut short
        description = None
        left = soup.find(id="left")
        para = left.find("p", class_="new2") if left else None
        if para:
            lines = (_clean_text(line) for line in para.get_text().splitlines())
            description = "\n".join(line for line in lines if line) or None
        if not description:
            description = _clean_text(str(data.get("description", ""))) or None

        # This edition's year is in the text under the rating, e.g. '1998 , Leonardo'
        publish_year = None
        info = soup.find("div", class_="lineHeightMid")
        if info:
            match = re.search(r"\b(1\d{3}|20\d{2})\b", info.get_text(" "))
            if match:
                publish_year = int(match.group(1))

        genres = data.get("genre") or []
        if isinstance(genres, str):
            genres = [genres]

        isbn = _valid_isbn(str(data.get("isbn", "")))
        publishers = _ld_names(data.get("publisher"))
        language = str(data.get("inLanguage", "")).strip().lower() or None

        display_fields: list[DisplayField] = []
        rating = data.get("aggregateRating") or {}
        try:
            rating_value = float(rating.get("ratingValue", 0))
            rating_count = int(rating.get("ratingCount", 0))
        except TypeError, ValueError:
            rating_value, rating_count = 0.0, 0
        if rating_value:
            display_fields.append(
                DisplayField(label="Rating", value=f"{rating_value:.1f}", icon="star")
            )
        if rating_count:
            display_fields.append(
                DisplayField(label="Ratings", value=f"{rating_count:,}", icon="users")
            )

        series_id, series_name, series_position = self._parse_series(soup)

        return BookMetadata(
            provider=self.name,
            provider_id=book_id,
            provider_display_name=self.display_name,
            title=title,
            authors=authors,
            isbn_13=isbn if isbn and len(isbn) == ISBN_13_LENGTH else None,
            isbn_10=isbn if isbn and len(isbn) != ISBN_13_LENGTH else None,
            cover_url=_cover_or_none(str(data.get("image", ""))),
            description=description,
            publisher=", ".join(publishers) or None,
            publish_year=publish_year,
            language=language,
            genres=[_clean_text(str(g)) for g in genres if _clean_text(str(g))],
            source_url=str(data.get("url") or DBK_BOOK_URL + book_id),
            search_title=title,
            search_author=authors[0],
            display_fields=display_fields,
            series_id=series_id,
            series_name=series_name,
            series_position=series_position,
        )

    def _parse_series(self, soup: BeautifulSoup) -> tuple[str | None, str | None, float | None]:
        """Return (id, name, position) of the first series in the book's series box.

        A book can be in several series, e.g. 'Zaklínač: sága' 1. díl and
        'Zaklínač' 3. díl; the first one listed is the most specific.
        """
        box = soup.find("div", class_="book_detail_serie_info")
        link = box.find("a", href=re.compile(r"^/serie/")) if box else None
        if not link:
            return None, None, None
        series_id = _id_from_link(str(link["href"])) or None
        name = _clean_text(str(link.get("title") or link.get_text())) or None
        # The part number follows the link, up to the next series link
        text = []
        parent = link.parent
        for elem in parent.next_siblings if parent else ():
            if isinstance(elem, Tag):
                if elem.find("a", href=re.compile(r"^/serie/")):
                    break
                text.append(elem.get_text(" "))
            else:
                text.append(str(elem))
        match = re.search(r"(\d+(?:[.,]\d+)?)\.\s*díl", " ".join(text))
        position = float(match.group(1).replace(",", ".")) if match else None
        return series_id, name, position

    def _apply_more_info(self, book: BookMetadata, soup: BeautifulSoup) -> None:
        """Fill pages, original title, ISBN and language from the more-info fragment."""
        for row in soup.find_all("div", class_="book-details__row"):
            label_node = row.find("dt")
            value_node = row.find("dd")
            if not label_node or not value_node:
                continue
            label = _clean_text(label_node.get_text(" "))
            value = _clean_text(value_node.get_text(" "))
            if label == "Počet stran":
                match = re.search(r"\d+", value)
                if match:
                    book.display_fields.append(
                        DisplayField(label="Pages", value=match.group(), icon="book")
                    )
            elif label == "Originální název" and value:
                # e.g. 'Krew elfów, 1994'
                book.display_fields.append(
                    DisplayField(label="Original title", value=value, icon="editions")
                )
            elif label == "ISBN" and not (book.isbn_13 or book.isbn_10):
                isbn = _valid_isbn(value.split(",")[0])
                if isbn and len(isbn) == ISBN_13_LENGTH:
                    book.isbn_13 = isbn
                elif isbn:
                    book.isbn_10 = isbn
            elif label == "Jazyk vydání" and not book.language:
                book.language = _LANGUAGE_NAMES.get(value.split(",")[0].strip().lower())


def _test_databazeknih_connection() -> dict[str, Any]:
    """Test connectivity to databazeknih.cz."""
    try:
        provider = DatabazeKnihProvider()
        response = provider.session.get(
            DBK_SEARCH_URL + urlencode({"q": "test"}),
            timeout=10,
            verify=get_ssl_verify(DBK_BASE_URL),
        )
        response.raise_for_status()
    except requests.Timeout:
        return {"success": False, "message": "Connection timed out"}
    except requests.RequestException as e:
        return {"success": False, "message": f"Connection failed: {e}"}
    if "databazeknih" in response.text.lower():
        return {"success": True, "message": "Successfully connected to databazeknih.cz"}
    return {"success": False, "message": "Unexpected response from databazeknih.cz"}


@register_settings(
    "databazeknih", "Databáze knih", icon="library", order=55, group="metadata_providers"
)
def databazeknih_settings() -> list[SettingsField]:
    """Databáze knih metadata provider settings."""
    return [
        HeadingField(
            key="databazeknih_heading",
            title="Databáze knih",
            description=(
                "Czech and Slovak community book catalog with excellent coverage of "
                "Czech and Slovak editions and translations. No API key required."
            ),
            link_url=DBK_BASE_URL,
            link_text="databazeknih.cz",
        ),
        CheckboxField(
            key="DATABAZEKNIH_ENABLED",
            label="Enable Databáze knih",
            description="Enable Databáze knih as a metadata provider for book searches",
            default=False,
        ),
        ActionButton(
            key="test_connection",
            label="Test Connection",
            description="Verify databazeknih.cz is accessible",
            style="primary",
            callback=_test_databazeknih_connection,
        ),
    ]
