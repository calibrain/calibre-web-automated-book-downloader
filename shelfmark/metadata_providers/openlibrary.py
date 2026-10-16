"""Open Library metadata provider. No API key required, rate limited."""

import re
import threading
import time
from collections import deque
from dataclasses import replace
from http import HTTPStatus
from typing import Any, ClassVar, NamedTuple

import requests

from shelfmark.core.cache import cacheable
from shelfmark.core.config import config as app_config
from shelfmark.core.logger import setup_logger
from shelfmark.core.settings_registry import (
    ActionButton,
    CheckboxField,
    HeadingField,
    SelectField,
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
    wikidata,
)

logger = setup_logger(__name__)

OPENLIBRARY_BASE_URL = "https://openlibrary.org"
COVERS_BASE_URL = "https://covers.openlibrary.org"

# Rate limiting: Open Library allows ~100 requests per minute
# We use a sliding window with 90 requests per 60 seconds for safety margin
RATE_LIMIT_REQUESTS = 90
RATE_LIMIT_WINDOW_SECONDS = 60
ISBN_10_LENGTH = 10
ISBN_13_LENGTH = 13
# Merged duplicate works leave redirects behind; old links and Wikidata still point at them.
MAX_WORK_REDIRECTS = 3
# Translated works found through Wikidata for a query Open Library itself cannot answer.
WIKIDATA_SEARCH_LIMIT = 3
# Wikidata labels languages as "uk", "pt-br", "zh-hans"; release searches match ISO 639-1 codes.
_PLAIN_LANGUAGE_CODE = re.compile(r"[a-z]{2}")


class _LoadedWork(NamedTuple):
    """A parsed work and the ids Wikidata may know it by."""

    book: BookMetadata
    work_ids: tuple[str, ...]  # current id first, then the merged ids it was reached through
    author_ids: tuple[str, ...]


def _wikidata_enabled() -> bool:
    return bool(app_config.get("OPENLIBRARY_WIKIDATA_TITLES", False))


def _attach_titles(book: BookMetadata, titles: dict[str, str]) -> BookMetadata:
    """Keep ISO 639-1 keys and titles that differ from the book's own title."""
    plain = {
        lang: title
        for lang, title in titles.items()
        if _PLAIN_LANGUAGE_CODE.fullmatch(lang) and title != book.title
    }
    if not plain:
        return book
    return replace(book, titles_by_language={**plain, **book.titles_by_language})


def _author_ids(work: dict) -> tuple[str, ...]:
    """Open Library author ids of a work record ("/authors/OL118077A" -> "OL118077A")."""
    ids: list[str] = []
    for ref in work.get("authors") or []:
        key = ((ref.get("author") or {}).get("key") or "") if isinstance(ref, dict) else ""
        if key:
            ids.append(str(key).rsplit("/", 1)[-1])
    return tuple(ids)


def _redirect_target(work: dict) -> str | None:
    """The work id a redirect record points to, or None for a real work."""
    if (work.get("type") or {}).get("key") != "/type/redirect":
        return None
    location = str(work.get("location") or "")
    return location.rsplit("/", 1)[-1] or None


class RateLimiter:
    """Simple sliding window rate limiter."""

    def __init__(self, max_requests: int, window_seconds: int) -> None:
        """Initialize rate limiter with max requests per time window."""
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.timestamps: deque[float] = deque()
        self.lock = threading.Lock()

    def wait_if_needed(self) -> None:
        """Block until a request is allowed (thread-safe)."""
        wait_time = 0

        # Calculate wait time with lock held
        with self.lock:
            now = time.time()
            cutoff = now - self.window_seconds

            # Remove timestamps outside the window
            while self.timestamps and self.timestamps[0] < cutoff:
                self.timestamps.popleft()

            if len(self.timestamps) >= self.max_requests:
                # Calculate wait time until oldest request falls outside window
                wait_time = self.timestamps[0] + self.window_seconds - now

        # Sleep outside the lock to avoid blocking other threads
        if wait_time > 0:
            logger.debug("Rate limited, waiting %0.2fs", wait_time)
            time.sleep(wait_time)

        # Re-acquire lock and record request
        with self.lock:
            # Re-clean timestamps after sleeping
            now = time.time()
            cutoff = now - self.window_seconds
            while self.timestamps and self.timestamps[0] < cutoff:
                self.timestamps.popleft()

            # Record this request
            self.timestamps.append(time.time())


# Global rate limiter for Open Library
_rate_limiter = RateLimiter(RATE_LIMIT_REQUESTS, RATE_LIMIT_WINDOW_SECONDS)


# Mapping from abstract sort order to Open Library sort parameter
# Note: Open Library only supports relevance (default), new, old, random
SORT_MAPPING: dict[str, str | None] = {
    SortOrder.RELEVANCE: None,  # Default (no sort param)
    SortOrder.NEWEST: "new",
    SortOrder.OLDEST: "old",
    # POPULARITY and RATING not supported - will fall back to relevance
}


@register_provider("openlibrary")
class OpenLibraryProvider(MetadataProvider):
    """Open Library metadata provider using REST API."""

    name = "openlibrary"
    display_name = "Open Library"
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

    def is_available(self) -> bool:
        """Open Library is always available (no auth required)."""
        return True

    def search(self, options: MetadataSearchOptions) -> list[BookMetadata]:
        """Search for books using Open Library's search API."""
        # Handle ISBN search separately
        if options.search_type == SearchType.ISBN:
            result = self.search_by_isbn(options.query)
            return [result] if result else []

        # Build cache key from options (include fields for cache differentiation)
        fields_key = ":".join(f"{k}={v}" for k, v in sorted(options.fields.items()))
        cache_key = f"{options.query}:{options.search_type.value}:{options.sort.value}:{options.language}:{options.limit}:{options.page}:{fields_key}:wikidata={_wikidata_enabled()}"
        return self._search_cached(cache_key, options)

    @cacheable(
        ttl_key="METADATA_CACHE_SEARCH_TTL", ttl_default=300, key_prefix="openlibrary:search"
    )
    def _search_cached(
        self, cache_key: str, options: MetadataSearchOptions, *, translated: bool = True
    ) -> list[BookMetadata]:
        """Return cached Open Library search results.

        ``translated=False`` is for lookups made on behalf of the Wikidata fallback itself,
        which must never fall back to Wikidata again.
        """
        _rate_limiter.wait_if_needed()

        # Build query params
        params: dict[str, Any] = {
            "limit": options.limit,
            "page": options.page,
            "fields": "key,title,author_name,first_publish_year,cover_i,isbn,publisher,language,subject,ratings_average,ratings_count",
        }

        # Field-first search: use custom field values when provided
        author_value = options.fields.get("author", "").strip()
        title_value = options.fields.get("title", "").strip()

        if author_value or title_value:
            # Use field-specific search params (Open Library supports both simultaneously)
            if author_value:
                params["author"] = author_value
            if title_value:
                params["title"] = title_value
            # Also add general query if provided (for additional filtering)
            if options.query.strip():
                params["q"] = options.query
        elif options.search_type == SearchType.TITLE:
            params["title"] = options.query
        elif options.search_type == SearchType.AUTHOR:
            params["author"] = options.query
        else:
            # General search
            params["q"] = options.query

        # Add sort if supported (fallback to relevance/default if not)
        sort = SORT_MAPPING.get(options.sort)
        if sort:
            params["sort"] = sort

        # Add language preference if specified
        if options.language:
            params["lang"] = options.language

        books: list[BookMetadata] = []
        try:
            response = self.session.get(
                f"{OPENLIBRARY_BASE_URL}/search.json",
                params=params,
                timeout=15,
                verify=get_ssl_verify(OPENLIBRARY_BASE_URL),
            )
            response.raise_for_status()
            data = response.json()

            for doc in data.get("docs", []):
                book = self._parse_search_doc(doc)
                if book:
                    books.append(book)

            logger.info("Open Library search '%s' returned %s results", options.query, len(books))

            if (
                translated
                and not books
                and options.page == 1
                and options.search_type != SearchType.AUTHOR
            ):
                books = self._search_translated_title(title_value or options.query, author_value)

        except requests.Timeout:
            logger.warning("Open Library search timed out")
            return []
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == HTTPStatus.SERVICE_UNAVAILABLE:
                logger.warning("Open Library service unavailable (503)")
            else:
                logger.exception("Open Library HTTP error")
            return []
        except requests.RequestException:
            logger.exception("Open Library search request failed")
            return []
        except TypeError, ValueError:
            logger.exception("Open Library search parsing error")
            return []
        return books

    def _fetch_work(self, work_id: str) -> dict:
        """Fetch a work record; it may be a redirect (see ``_redirect_target``)."""
        _rate_limiter.wait_if_needed()
        response = self.session.get(
            f"{OPENLIBRARY_BASE_URL}/works/{work_id}.json",
            timeout=15,
            verify=get_ssl_verify(OPENLIBRARY_BASE_URL),
        )
        response.raise_for_status()
        return response.json()

    def get_book(self, book_id: str) -> BookMetadata | None:
        """Get book details by Open Library work ID (e.g., 'OL12345W').

        A work that was merged into another is followed to its current id, which is the
        ``provider_id`` of the result. With ``OPENLIBRARY_WIKIDATA_TITLES`` on, the book
        also carries its Wikidata titles in ``titles_by_language``.
        """
        loaded = self._load_work(book_id)
        if loaded is None:
            return None
        if not _wikidata_enabled():
            return loaded.book
        return self._with_translated_titles(loaded)

    @cacheable(ttl_key="METADATA_CACHE_BOOK_TTL", ttl_default=600, key_prefix="openlibrary:book")
    def _load_work(self, book_id: str) -> _LoadedWork | None:
        """Fetch and parse a work, following merged-work redirects.

        Not the place for Wikidata: ``get_book`` adds titles after this cached step, so
        neither a setting change nor a Wikidata outage is held in the cache.
        """
        # Normalize the book_id format
        if not book_id.startswith("OL"):
            book_id = f"OL{book_id}"
        if not book_id.endswith("W"):
            book_id = f"{book_id}W"

        try:
            work = self._fetch_work(book_id)
            reached_through: list[str] = []
            for _ in range(MAX_WORK_REDIRECTS):
                target = _redirect_target(work)
                if not target:
                    break
                logger.debug("Open Library work %s redirects to %s", book_id, target)
                reached_through.append(book_id)
                book_id = target
                work = self._fetch_work(book_id)

            book = self._parse_work(work, book_id)
            if book is None:
                return None
            return _LoadedWork(book, (book_id, *reached_through), _author_ids(work))

        except requests.Timeout:
            logger.warning("Open Library get_book timed out")
            return None
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == HTTPStatus.NOT_FOUND:
                logger.debug("Open Library work not found: %s", book_id)
            else:
                logger.exception("Open Library HTTP error")
            return None
        except requests.RequestException:
            logger.exception("Open Library get_book request failed")
            return None
        except TypeError, ValueError:
            logger.exception("Open Library get_book parsing error")
            return None

    @cacheable(ttl_key="METADATA_CACHE_BOOK_TTL", ttl_default=600, key_prefix="openlibrary:isbn")
    def search_by_isbn(self, isbn: str) -> BookMetadata | None:
        """Search for a book by ISBN-10 or ISBN-13."""
        # Clean ISBN
        clean_isbn = isbn.replace("-", "").strip()

        _rate_limiter.wait_if_needed()

        try:
            # First try the ISBN API which returns edition data
            response = self.session.get(
                f"{OPENLIBRARY_BASE_URL}/isbn/{clean_isbn}.json",
                timeout=15,
                verify=get_ssl_verify(OPENLIBRARY_BASE_URL),
            )
            response.raise_for_status()
            edition = response.json()

            # Get the work key for full book info
            works = edition.get("works", [])
            if works:
                work_key = works[0].get("key", "")
                work_id = work_key.split("/")[-1] if work_key else None

                if work_id:
                    # Fetch full work data
                    book = self.get_book(work_id)
                    if book:
                        # Update with ISBN from edition if not present
                        # Use dataclasses.replace() to avoid mutating cached object
                        updates = {}
                        if not book.isbn_10:
                            isbn_10_list = edition.get("isbn_10", [])
                            if isbn_10_list:
                                updates["isbn_10"] = isbn_10_list[0]
                        if not book.isbn_13:
                            isbn_13_list = edition.get("isbn_13", [])
                            if isbn_13_list:
                                updates["isbn_13"] = isbn_13_list[0]
                        if updates:
                            return replace(book, **updates)
                        return book

            # Fallback: parse edition data directly
            return self._parse_edition(edition, clean_isbn)

        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == HTTPStatus.NOT_FOUND:
                logger.debug("Open Library ISBN not found: %s", isbn)
            else:
                logger.exception("Open Library ISBN search HTTP error")
            return None
        except requests.RequestException:
            logger.exception("Open Library ISBN search request failed")
            return None
        except TypeError, ValueError:
            logger.exception("Open Library ISBN search parsing error")
            return None

    def _with_translated_titles(self, loaded: _LoadedWork) -> BookMetadata:
        """Attach the work's Wikidata titles so release searches also try translated titles.

        Open Library files a translation under the original work ("The Secret History"),
        while release sources list the edition the reader wants ("Таємна історія"). The titles
        feed ``titles_by_language``, which the release search plan already turns into
        one query per configured language.
        """
        book = loaded.book
        author = book.authors[0] if book.authors else ""
        try:
            titles = wikidata.localized_titles(
                loaded.work_ids, book.title, author, loaded.author_ids
            )
        except wikidata.WikidataUnavailableError as e:
            logger.debug("Wikidata titles skipped for %s: %s", book.provider_id, e)
            return book
        except KeyError, TypeError, ValueError, AttributeError:
            logger.exception("Unexpected Wikidata answer for %s", book.provider_id)
            return book
        return _attach_titles(book, titles)

    def _search_translated_title(self, text: str, author: str) -> list[BookMetadata]:
        """Find works by a title typed in a non-Latin script, through their Wikidata labels.

        Open Library has almost no translated titles, so «Таємна історія» or «Тартт Таємна
        історія» returns nothing there. Runs only when the setting is on and the query has
        non-Latin letters; author-only searches never get here.
        """
        if not _wikidata_enabled():
            return []
        query = " ".join(part for part in (text.strip(), author.strip()) if part)
        if not text.strip() or not wikidata.has_non_latin_letters(query):
            return []
        try:
            works = wikidata.find_works(query, WIKIDATA_SEARCH_LIMIT)
        except wikidata.WikidataUnavailableError as e:
            logger.debug("Wikidata search skipped for '%s': %s", query, e)
            return []
        except KeyError, TypeError, ValueError, AttributeError:
            logger.exception("Unexpected Wikidata answer for '%s'", query)
            return []
        books: list[BookMetadata] = []
        for work in works:
            book = self._book_for_wikidata_work(work)
            if book and all(b.provider_id != book.provider_id for b in books):
                books.append(book)
        if books:
            logger.info(
                "Open Library search '%s': %s works found through Wikidata", query, len(books)
            )
        return books

    def _book_for_wikidata_work(self, work: wikidata.WikidataWork) -> BookMetadata | None:
        """The Open Library work for a Wikidata work, with its translated titles attached.

        Uses the work's P648 link, else an Open Library search by its Latin-script title
        and the author's surname (many works, Ukrainian originals among them, have no Open
        Library link on Wikidata although Open Library has them). Only the surname is sent:
        Open Library spells "I͡Uriĭ Andrukhovych" where Wikidata has "Yurii Andrukhovych".
        A hit must be by the same author in any script ("Іван Франко" is "Ivan Franko").
        """
        work_ids = work.ol_work_ids
        if not work_ids and (found := self._find_work_id(work)):
            work_ids = (found,)
        for work_id in work_ids:
            loaded = self._load_work(work_id)
            if loaded:
                return _attach_titles(loaded.book, work.titles)
        return None

    def _find_work_id(self, work: wikidata.WikidataWork) -> str | None:
        """Search Open Library for a Wikidata work that has no P648 link."""
        title = work.titles.get("en", "")
        if not title or wikidata.has_non_latin_letters(title):
            title = next(
                (t for t in work.titles.values() if not wikidata.has_non_latin_letters(t)), ""
            )
        family = wikidata.family_name(work.author)
        if not title or not family:
            return None
        for fields in ({"title": title, "author": family}, {"title": title}):
            options = MetadataSearchOptions(query="", fields=fields, limit=5)
            key = f"wikidata-work:{title}:{fields.get('author', '')}"
            for candidate in self._search_cached(key, options, translated=False):
                if work.is_by(candidate.authors):
                    return candidate.provider_id
        return None

    def _parse_search_doc(self, doc: dict) -> BookMetadata | None:
        """Parse a search document into BookMetadata."""
        try:
            # Extract work ID from key
            key = doc.get("key", "")
            work_id = key.split("/")[-1] if key else None

            if not work_id or not doc.get("title"):
                return None

            # Get authors
            authors = doc.get("author_name", [])
            if not isinstance(authors, list):
                authors = [authors] if authors else []

            # Get ISBNs - find first ISBN-10 and ISBN-13
            isbns = doc.get("isbn", [])
            isbn_10 = next((i for i in isbns if len(i) == ISBN_10_LENGTH), None)
            isbn_13 = next((i for i in isbns if len(i) == ISBN_13_LENGTH), None)

            # Get cover URL
            cover_id = doc.get("cover_i")
            cover_url = f"{COVERS_BASE_URL}/b/id/{cover_id}-L.jpg" if cover_id else None

            # Get publishers (take first one)
            publishers = doc.get("publisher", [])
            publisher = publishers[0] if publishers else None

            # Get languages (take first one)
            languages = doc.get("language", [])
            language = languages[0] if languages else None

            # Get subjects as genres (take first 5)
            subjects = doc.get("subject", [])
            genres = subjects[:5] if subjects else []

            # Build display fields from Open Library-specific data
            display_fields = []

            # Rating (if available - not always present)
            ratings_avg = doc.get("ratings_average")
            ratings_count = doc.get("ratings_count")
            if ratings_avg is not None and ratings_avg > 0:
                rating_str = f"{ratings_avg:.1f}"
                if ratings_count:
                    rating_str += f" ({ratings_count:,})"
                display_fields.append(DisplayField(label="Rating", value=rating_str, icon="star"))

            return BookMetadata(
                provider="openlibrary",
                provider_id=work_id,
                title=doc["title"],
                provider_display_name="Open Library",
                authors=authors,
                isbn_10=isbn_10,
                isbn_13=isbn_13,
                cover_url=cover_url,
                publisher=publisher,
                publish_year=doc.get("first_publish_year"),
                language=language,
                genres=genres,
                source_url=f"{OPENLIBRARY_BASE_URL}/works/{work_id}",
                display_fields=display_fields,
            )

        except (TypeError, ValueError, AttributeError, KeyError) as e:
            logger.debug("Failed to parse Open Library search doc: %s", e)
            return None

    def _parse_work(self, work: dict, work_id: str) -> BookMetadata | None:
        """Parse a work object into BookMetadata."""
        try:
            title = work.get("title")
            if not title:
                return None

            # Get description
            description = work.get("description")
            if isinstance(description, dict):
                description = description.get("value")

            # Get authors (requires additional API calls)
            authors = []
            for author_ref in work.get("authors", []):
                author_key = None
                if isinstance(author_ref, dict):
                    author_key = author_ref.get("author", {}).get("key")
                if author_key:
                    author_name = self._get_author_name(author_key)
                    if author_name:
                        authors.append(author_name)

            # Get cover URL from covers array
            cover_url = None
            covers = work.get("covers", [])
            if covers:
                cover_id = covers[0]
                cover_url = f"{COVERS_BASE_URL}/b/id/{cover_id}-L.jpg"

            # Get subjects as genres
            subjects = work.get("subjects", [])
            genres = subjects[:5] if subjects else []

            return BookMetadata(
                provider="openlibrary",
                provider_id=work_id,
                title=title,
                provider_display_name="Open Library",
                authors=authors,
                cover_url=cover_url,
                description=description,
                genres=genres,
                source_url=f"{OPENLIBRARY_BASE_URL}/works/{work_id}",
            )

        except (TypeError, ValueError, AttributeError, KeyError) as e:
            logger.debug("Failed to parse Open Library work: %s", e)
            return None

    def _parse_edition(self, edition: dict, isbn: str) -> BookMetadata | None:
        """Parse an edition object into BookMetadata (fallback for ISBN lookup)."""
        try:
            title = edition.get("title")
            if not title:
                return None

            # Get the edition key as ID
            key = edition.get("key", "")
            edition_id = key.split("/")[-1] if key else isbn

            # Get ISBNs
            isbn_10_list = edition.get("isbn_10", [])
            isbn_13_list = edition.get("isbn_13", [])
            isbn_10 = isbn_10_list[0] if isbn_10_list else None
            isbn_13 = isbn_13_list[0] if isbn_13_list else None

            # Get publishers
            publishers = edition.get("publishers", [])
            publisher = publishers[0] if publishers else None

            # Get cover URL
            cover_url = None
            covers = edition.get("covers", [])
            if covers:
                cover_id = covers[0]
                cover_url = f"{COVERS_BASE_URL}/b/id/{cover_id}-L.jpg"

            # Get publish date and try to extract year
            publish_year = None
            publish_date = edition.get("publish_date", "")
            if publish_date:
                # Try to extract year from various formats
                year_match = re.search(r"\b(19|20)\d{2}\b", publish_date)
                if year_match:
                    publish_year = int(year_match.group())

            return BookMetadata(
                provider="openlibrary",
                provider_id=edition_id,
                title=title,
                provider_display_name="Open Library",
                isbn_10=isbn_10,
                isbn_13=isbn_13,
                cover_url=cover_url,
                publisher=publisher,
                publish_year=publish_year,
                source_url=f"{OPENLIBRARY_BASE_URL}{key}" if key else None,
            )

        except (TypeError, ValueError, AttributeError, KeyError) as e:
            logger.debug("Failed to parse Open Library edition: %s", e)
            return None

    def _get_author_name(self, author_key: str) -> str | None:
        """Get author name from author key (e.g., '/authors/OL123A')."""
        _rate_limiter.wait_if_needed()

        try:
            response = self.session.get(
                f"{OPENLIBRARY_BASE_URL}{author_key}.json",
                timeout=10,
                verify=get_ssl_verify(OPENLIBRARY_BASE_URL),
            )
            response.raise_for_status()
            author = response.json()
            return author.get("name")

        except requests.RequestException, ValueError:
            # Don't log errors for author lookups - they're supplementary
            return None


def _test_openlibrary_connection() -> dict[str, Any]:
    """Test the Open Library API connection."""
    connection_result = {"success": False, "message": "Unexpected response from API"}
    try:
        provider = OpenLibraryProvider()
        # Simple API call to test connectivity
        response = provider.session.get(
            f"{OPENLIBRARY_BASE_URL}/search.json",
            params={"q": "test", "limit": 1},
            timeout=10,
            verify=get_ssl_verify(OPENLIBRARY_BASE_URL),
        )
        response.raise_for_status()
        data = response.json()
        if "docs" in data:
            connection_result = {
                "success": True,
                "message": "Successfully connected to Open Library API",
            }
    except requests.Timeout:
        return {"success": False, "message": "Connection timed out"}
    except requests.RequestException as e:
        return {"success": False, "message": f"Connection failed: {e!s}"}
    except (TypeError, ValueError, AttributeError) as e:
        return {"success": False, "message": f"Error: {e!s}"}
    return connection_result


# Open Library sort options for settings UI
_OPENLIBRARY_SORT_OPTIONS = [
    {"value": "relevance", "label": "Most relevant"},
    {"value": "newest", "label": "Newest"},
    {"value": "oldest", "label": "Oldest"},
]


@register_settings(
    "openlibrary", "Open Library", icon="library", order=52, group="metadata_providers"
)
def openlibrary_settings() -> list[SettingsField]:
    """Open Library metadata provider settings."""
    return [
        HeadingField(
            key="openlibrary_heading",
            title="Open Library",
            description="An initiative of the Internet Archive. A free, open-source library catalog with millions of books. No API key required.",
            link_url="https://openlibrary.org",
            link_text="openlibrary.org",
        ),
        CheckboxField(
            key="OPENLIBRARY_ENABLED",
            label="Enable Open Library",
            description="Enable Open Library as a metadata provider for book searches",
            default=False,
        ),
        ActionButton(
            key="test_connection",
            label="Test Connection",
            description="Verify Open Library API is accessible",
            style="primary",
            callback=_test_openlibrary_connection,
        ),
        SelectField(
            key="OPENLIBRARY_DEFAULT_SORT",
            label="Default Sort Order",
            description="Default sort order for Open Library search results.",
            options=_OPENLIBRARY_SORT_OPTIONS,
            default="relevance",
        ),
        CheckboxField(
            key="OPENLIBRARY_WIKIDATA_TITLES",
            label="Translated titles from Wikidata",
            description=(
                "Open Library lists translations under the original title. When enabled, "
                "release searches also try the title in each of your book languages, taken "
                "from Wikidata, and a search typed in a non-Latin script (Cyrillic, Greek, "
                "...) that Open Library cannot answer is looked up there. Sends requests "
                "to wikidata.org."
            ),
            default=False,
        ),
    ]
