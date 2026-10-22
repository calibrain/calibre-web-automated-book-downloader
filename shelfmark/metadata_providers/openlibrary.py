"""Open Library metadata provider. No API key required, rate limited."""

import re
import threading
import time
import unicodedata
from collections import deque
from http import HTTPStatus
from typing import Any, ClassVar

import requests

from shelfmark.core.cache import cacheable
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
from shelfmark.core.text_match import STOPWORDS
from shelfmark.download.network import get_proxies, get_ssl_verify
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

logger = setup_logger(__name__)

OPENLIBRARY_BASE_URL = "https://openlibrary.org"
COVERS_BASE_URL = "https://covers.openlibrary.org"

# Rate limiting: Open Library allows ~100 requests per minute
# We use a sliding window with 90 requests per 60 seconds for safety margin
RATE_LIMIT_REQUESTS = 90
RATE_LIMIT_WINDOW_SECONDS = 60
ISBN_10_LENGTH = 10
ISBN_13_LENGTH = 13


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


def _title_words(text: str) -> set[str]:
    """Return the significant words of a title, folded for comparison."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    folded = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return {word for word in re.findall(r"\w+", folded) if len(word) >= 2} - STOPWORDS


def _is_translation_match(work_title: str, edition: dict, query: str) -> bool:
    """Return whether a search's matched edition is a translation of the work.

    Open Library shows the English edition of a work whenever one satisfies the
    query, so an English edition says nothing about which edition the user meant:
    searching "Mistborn" picks the 2023 "Mistborn" reissue of "The Final Empire",
    and "Children of Dune" may pick "Children of Dune (Dune Chronicles, Book 3)".
    An edition in another language comes back when only that language's edition
    matched, which is what searching a translated title looks like. A `lang`
    preference also brings one back for a search that names the work itself
    ("Dune-Zyklus" for "Children of Dune"), so such a search keeps the work.
    """
    edition_title = edition.get("title")
    if not edition_title or _title_words(edition_title) == _title_words(work_title):
        return False

    edition_languages = {normalize_language(code) for code in edition.get("language") or []}
    if not edition_languages - {None} or "en" in edition_languages:
        return False

    query_words = _title_words(query)
    work_words = _title_words(work_title)
    names_work = bool(query_words and work_words) and (
        work_words <= query_words or query_words <= work_words
    )
    return not names_work


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
        cache_key = f"{options.query}:{options.search_type.value}:{options.sort.value}:{options.language}:{options.limit}:{options.page}:{fields_key}"
        return self._search_cached(cache_key, options)

    @cacheable(
        ttl_key="METADATA_CACHE_SEARCH_TTL", ttl_default=300, key_prefix="openlibrary:search"
    )
    def _search_cached(self, cache_key: str, options: MetadataSearchOptions) -> list[BookMetadata]:
        """Return cached Open Library search results."""
        _rate_limiter.wait_if_needed()

        # Build query params
        params: dict[str, Any] = {
            "limit": options.limit,
            "page": options.page,
            "fields": (
                "key,title,author_name,first_publish_year,cover_i,isbn,publisher,"
                "language,subject,ratings_average,ratings_count,"
                "editions,editions.key,editions.title,editions.language,"
                "editions.isbn,editions.cover_i,editions.publish_year"
            ),
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
                proxies=get_proxies(OPENLIBRARY_BASE_URL),
                verify=get_ssl_verify(OPENLIBRARY_BASE_URL),
            )
            response.raise_for_status()
            data = response.json()

            for doc in data.get("docs", []):
                book = self._parse_search_doc(doc, title_value or options.query)
                if book:
                    books.append(book)

            logger.info("Open Library search '%s' returned %s results", options.query, len(books))

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

    @cacheable(ttl_key="METADATA_CACHE_BOOK_TTL", ttl_default=600, key_prefix="openlibrary:book")
    def get_book(self, book_id: str) -> BookMetadata | None:
        """Get book details by Open Library work ID (e.g., 'OL12345W')."""
        _rate_limiter.wait_if_needed()

        # Normalize the book_id format
        if not book_id.startswith("OL"):
            book_id = f"OL{book_id}"
        if not book_id.endswith("W"):
            book_id = f"{book_id}W"

        try:
            response = self.session.get(
                f"{OPENLIBRARY_BASE_URL}/works/{book_id}.json",
                timeout=15,
                proxies=get_proxies(OPENLIBRARY_BASE_URL),
                verify=get_ssl_verify(OPENLIBRARY_BASE_URL),
            )
            response.raise_for_status()
            work = response.json()

            return self._parse_work(work, book_id)

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
                proxies=get_proxies(OPENLIBRARY_BASE_URL),
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
                        from dataclasses import replace

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

    def _parse_search_doc(self, doc: dict, query: str = "") -> BookMetadata | None:
        """Parse a search document into BookMetadata.

        ``query`` is what the user searched for (the title field when given), used
        to tell a search for a translated title apart from one for the work itself.
        """
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

            # Get publishers (take first one)
            publishers = doc.get("publisher", [])
            publisher = publishers[0] if publishers else None

            # Get languages (take first one)
            languages = doc.get("language", [])
            language = languages[0] if languages else None

            # Get subjects as genres (take first 5)
            subjects = doc.get("subject", [])
            genres = subjects[:5] if subjects else []

            # `title`/`isbn`/`cover_i`/`language` above describe the *work*, which
            # Open Library reports with one canonical title - usually the original-
            # language edition - regardless of which translation's text actually
            # matched the search query (e.g. querying "Der Weg der Könige" matches
            # the German edition of "The Way of Kings", but the work's title field
            # still says "The Way of Kings"). The nested `editions` block, requested
            # above, holds the edition Open Library picked to show for the query.
            # When that edition is a translation, prefer its title/isbn/cover/
            # language - otherwise both the result shown to the user and the
            # release search query (which uses `search_title` or else falls back to
            # `title`, see search_plan._pick_search_title) end up in the wrong
            # language even though the user searched for a specific translation by
            # name. Any other edition keeps the work's own title and first year.
            title = doc["title"]
            search_title = None
            publish_year = doc.get("first_publish_year")
            matched_edition = next(iter(doc.get("editions", {}).get("docs", [])), None)
            if matched_edition and _is_translation_match(title, matched_edition, query):
                search_title = matched_edition["title"]
                title = search_title

                edition_isbns = matched_edition.get("isbn", [])
                edition_isbn_10 = next((i for i in edition_isbns if len(i) == ISBN_10_LENGTH), None)
                edition_isbn_13 = next((i for i in edition_isbns if len(i) == ISBN_13_LENGTH), None)
                isbn_10 = edition_isbn_10 or isbn_10
                isbn_13 = edition_isbn_13 or isbn_13

                cover_id = matched_edition.get("cover_i") or cover_id

                edition_languages = matched_edition.get("language") or []
                if edition_languages:
                    language = edition_languages[0]

                # A translation is published later than the work it translates,
                # e.g. 2015 for "Der Weg der Könige" against 2010 for the work.
                edition_years = matched_edition.get("publish_year") or []
                if edition_years:
                    publish_year = min(edition_years)

            cover_url = f"{COVERS_BASE_URL}/b/id/{cover_id}-L.jpg" if cover_id else None

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
                title=title,
                search_title=search_title,
                provider_display_name="Open Library",
                authors=authors,
                isbn_10=isbn_10,
                isbn_13=isbn_13,
                cover_url=cover_url,
                publisher=publisher,
                publish_year=publish_year,
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
                proxies=get_proxies(OPENLIBRARY_BASE_URL),
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
            proxies=get_proxies(OPENLIBRARY_BASE_URL),
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
    ]
