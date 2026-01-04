"""Open Library metadata provider. No API key required, rate limited."""

import re
import time
import threading
from collections import deque
from typing import Any, Deque, Dict, List, Optional

import requests

from cwa_book_downloader.core.cache import cacheable
from cwa_book_downloader.core.logger import setup_logger
from cwa_book_downloader.core.settings_registry import (
    register_settings,
    CheckboxField,
    SelectField,
    ActionButton,
    HeadingField,
)
from cwa_book_downloader.metadata_providers import (
    BookMetadata,
    DisplayField,
    MetadataProvider,
    MetadataSearchOptions,
    SearchType,
    SortOrder,
    register_provider,
    TextSearchField,
)

logger = setup_logger(__name__)

OPENLIBRARY_BASE_URL = "https://openlibrary.org"
COVERS_BASE_URL = "https://covers.openlibrary.org"

# Rate limiting: Open Library allows ~100 requests per minute
# We use a sliding window with 90 requests per 60 seconds for safety margin
RATE_LIMIT_REQUESTS = 90
RATE_LIMIT_WINDOW_SECONDS = 60


class RateLimiter:
    """Simple sliding window rate limiter."""

    def __init__(self, max_requests: int, window_seconds: int):
        """Initialize rate limiter with max requests per time window."""
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.timestamps: Deque[float] = deque()
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
            logger.debug(f"Rate limited, waiting {wait_time:.2f}s")
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
SORT_MAPPING: Dict[str, Optional[str]] = {
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
    supported_sorts = [
        SortOrder.RELEVANCE,
        SortOrder.NEWEST,
        SortOrder.OLDEST,
    ]
    search_fields = [
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
    ]

    def __init__(self):
        """Initialize provider."""
        self.session = requests.Session()

    def is_available(self) -> bool:
        """Open Library is always available (no auth required)."""
        return True

    def search(self, options: MetadataSearchOptions) -> List[BookMetadata]:
        """Search for books using Open Library's search API."""
        # Handle ISBN search separately
        if options.search_type == SearchType.ISBN:
            result = self.search_by_isbn(options.query)
            return [result] if result else []

        # Build cache key from options (include fields for cache differentiation)
        fields_key = ":".join(f"{k}={v}" for k, v in sorted(options.fields.items()))
        cache_key = f"{options.query}:{options.search_type.value}:{options.sort.value}:{options.language}:{options.limit}:{options.page}:{fields_key}"
        return self._search_cached(cache_key, options)

    @cacheable(ttl_key="METADATA_CACHE_SEARCH_TTL", ttl_default=300, key_prefix="openlibrary:search")
    def _search_cached(self, cache_key: str, options: MetadataSearchOptions) -> List[BookMetadata]:
        """Cached search implementation."""
        _rate_limiter.wait_if_needed()

        # Build query params
        params: Dict[str, Any] = {
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

        try:
            response = self.session.get(
                f"{OPENLIBRARY_BASE_URL}/search.json",
                params=params,
                timeout=15
            )
            response.raise_for_status()
            data = response.json()

            books = []
            for doc in data.get("docs", []):
                book = self._parse_search_doc(doc)
                if book:
                    books.append(book)

            logger.info(f"Open Library search '{options.query}' returned {len(books)} results")
            return books

        except requests.Timeout:
            logger.warning("Open Library search timed out")
            return []
        except requests.HTTPError as e:
            if e.response.status_code == 503:
                logger.warning("Open Library service unavailable (503)")
            else:
                logger.error(f"Open Library HTTP error: {e}")
            return []
        except Exception as e:
            logger.error(f"Open Library search error: {e}")
            return []

    @cacheable(ttl_key="METADATA_CACHE_BOOK_TTL", ttl_default=600, key_prefix="openlibrary:book")
    def get_book(self, book_id: str) -> Optional[BookMetadata]:
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
                timeout=15
            )
            response.raise_for_status()
            work = response.json()

            return self._parse_work(work, book_id)

        except requests.Timeout:
            logger.warning("Open Library get_book timed out")
            return None
        except requests.HTTPError as e:
            if e.response.status_code == 404:
                logger.debug(f"Open Library work not found: {book_id}")
            else:
                logger.error(f"Open Library HTTP error: {e}")
            return None
        except Exception as e:
            logger.error(f"Open Library get_book error: {e}")
            return None

    @cacheable(ttl_key="METADATA_CACHE_BOOK_TTL", ttl_default=600, key_prefix="openlibrary:isbn")
    def search_by_isbn(self, isbn: str) -> Optional[BookMetadata]:
        """Search for a book by ISBN-10 or ISBN-13."""
        # Clean ISBN
        clean_isbn = isbn.replace("-", "").strip()

        _rate_limiter.wait_if_needed()

        try:
            # First try the ISBN API which returns edition data
            response = self.session.get(
                f"{OPENLIBRARY_BASE_URL}/isbn/{clean_isbn}.json",
                timeout=15
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
            if e.response.status_code == 404:
                logger.debug(f"Open Library ISBN not found: {isbn}")
            else:
                logger.error(f"Open Library ISBN search HTTP error: {e}")
            return None
        except Exception as e:
            logger.error(f"Open Library ISBN search error: {e}")
            return None

    def _parse_search_doc(self, doc: dict) -> Optional[BookMetadata]:
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
            isbn_10 = next((i for i in isbns if len(i) == 10), None)
            isbn_13 = next((i for i in isbns if len(i) == 13), None)

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

        except Exception as e:
            logger.debug(f"Failed to parse Open Library search doc: {e}")
            return None

    def _parse_work(self, work: dict, work_id: str) -> Optional[BookMetadata]:
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

        except Exception as e:
            logger.debug(f"Failed to parse Open Library work: {e}")
            return None

    def _parse_edition(self, edition: dict, isbn: str) -> Optional[BookMetadata]:
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
                year_match = re.search(r'\b(19|20)\d{2}\b', publish_date)
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

        except Exception as e:
            logger.debug(f"Failed to parse Open Library edition: {e}")
            return None

    def _get_author_name(self, author_key: str) -> Optional[str]:
        """Get author name from author key (e.g., '/authors/OL123A')."""
        _rate_limiter.wait_if_needed()

        try:
            response = self.session.get(
                f"{OPENLIBRARY_BASE_URL}{author_key}.json",
                timeout=10
            )
            response.raise_for_status()
            author = response.json()
            return author.get("name")

        except Exception:
            # Don't log errors for author lookups - they're supplementary
            return None


def _test_openlibrary_connection() -> Dict[str, Any]:
    """Test the Open Library API connection."""
    try:
        provider = OpenLibraryProvider()
        # Simple API call to test connectivity
        response = provider.session.get(
            f"{OPENLIBRARY_BASE_URL}/search.json",
            params={"q": "test", "limit": 1},
            timeout=10
        )
        response.raise_for_status()
        data = response.json()
        if "docs" in data:
            return {"success": True, "message": "Successfully connected to Open Library API"}
        else:
            return {"success": False, "message": "Unexpected response from API"}
    except requests.Timeout:
        return {"success": False, "message": "Connection timed out"}
    except requests.RequestException as e:
        return {"success": False, "message": f"Connection failed: {str(e)}"}
    except Exception as e:
        return {"success": False, "message": f"Error: {str(e)}"}


# Open Library sort options for settings UI
_OPENLIBRARY_SORT_OPTIONS = [
    {"value": "relevance", "label": "Most relevant"},
    {"value": "newest", "label": "Newest"},
    {"value": "oldest", "label": "Oldest"},
]


@register_settings("openlibrary", "Open Library", icon="library", order=52, group="metadata_providers")
def openlibrary_settings():
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
            env_supported=False,  # UI-only setting
        ),
    ]
