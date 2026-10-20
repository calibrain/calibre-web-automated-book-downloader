"""Google Books metadata provider.

Uses the Google Books API v1 to search and retrieve book metadata.
Requires a free API key from Google Cloud Console (~1000 requests/day quota).

API Documentation: https://developers.google.com/books/docs/v1/using
"""

from contextlib import suppress
from dataclasses import replace
from http import HTTPStatus
from typing import Any, ClassVar

import requests

from shelfmark.core.cache import cacheable
from shelfmark.core.config import config as app_config
from shelfmark.core.logger import setup_logger
from shelfmark.core.request_helpers import normalize_optional_text
from shelfmark.core.settings_registry import (
    ActionButton,
    CheckboxField,
    HeadingField,
    PasswordField,
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
    SearchResult,
    SearchType,
    SortOrder,
    TextSearchField,
    register_provider,
    register_provider_kwargs,
)

logger = setup_logger(__name__)

_HTTP_STATUS_FORBIDDEN = HTTPStatus.FORBIDDEN
_HTTP_STATUS_BAD_REQUEST = HTTPStatus.BAD_REQUEST
_HTTP_STATUS_NOT_FOUND = HTTPStatus.NOT_FOUND

GOOGLE_BOOKS_BASE_URL = "https://www.googleapis.com/books/v1"


class _GoogleBooksRequestError(Exception):
    """Raised when Google Books does not return a usable API response."""


# Sort mapping - Google only supports "relevance" and "newest"
SORT_MAPPING: dict[SortOrder, str | None] = {
    SortOrder.RELEVANCE: None,  # Default, no param needed
    SortOrder.NEWEST: "newest",
    # POPULARITY, RATING, OLDEST not supported - fall back to relevance
}


def _normalize_googlebooks_api_key(value: object) -> str:
    """Normalize Google Books API keys loaded from config or form values."""
    return normalize_optional_text(value) or ""


@register_provider_kwargs("googlebooks")
def _googlebooks_kwargs() -> dict[str, Any]:
    """Provide Google Books-specific constructor kwargs."""
    return {"api_key": _normalize_googlebooks_api_key(app_config.get("GOOGLEBOOKS_API_KEY", ""))}


@register_provider("googlebooks")
class GoogleBooksProvider(MetadataProvider):
    """Google Books metadata provider using REST API."""

    name = "googlebooks"
    display_name = "Google Books"
    requires_auth = True
    supported_sorts: ClassVar[tuple[SortOrder, ...]] = (
        SortOrder.RELEVANCE,
        SortOrder.NEWEST,
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

    def __init__(self, api_key: str | None = None) -> None:
        """Initialize provider with optional API key (falls back to config)."""
        raw_key = api_key or app_config.get("GOOGLEBOOKS_API_KEY", "")
        self.api_key = _normalize_googlebooks_api_key(raw_key)
        self.session = requests.Session()

    def is_available(self) -> bool:
        """Check if provider is configured with an API key."""
        return bool(self.api_key)

    def search(self, options: MetadataSearchOptions) -> list[BookMetadata]:
        """Search for books using Google Books API."""
        if not self.api_key:
            logger.warning("Google Books API key not configured")
            return []

        # Handle ISBN search separately
        if options.search_type == SearchType.ISBN:
            result = self.search_by_isbn(options.query)
            return [result] if result else []

        # Build cache key from all options
        fields_key = ":".join(f"{k}={v}" for k, v in sorted(options.fields.items()))
        cache_key = (
            f"{options.query}:{options.search_type.value}:{options.sort.value}:"
            f"{options.language}:{options.limit}:{options.page}:{fields_key}"
        )
        try:
            return self._search_cached(cache_key, options)
        except _GoogleBooksRequestError:
            return []

    @cacheable(
        ttl_key="METADATA_CACHE_SEARCH_TTL",
        ttl_default=300,
        key_prefix="googlebooks:search",
    )
    def _search_cached(self, cache_key: str, options: MetadataSearchOptions) -> list[BookMetadata]:
        """Return cached search results for Google Books."""
        # Build query string with Google Books operators
        author_value = options.fields.get("author", "").strip()
        title_value = options.fields.get("title", "").strip()

        query_parts = []

        # Add field-specific operators
        if title_value:
            query_parts.append(f"intitle:{title_value}")
        elif options.search_type == SearchType.TITLE:
            query_parts.append(f"intitle:{options.query}")

        if author_value:
            query_parts.append(f"inauthor:{author_value}")
        elif options.search_type == SearchType.AUTHOR:
            query_parts.append(f"inauthor:{options.query}")

        # Fall back to general search if no specific fields
        if not query_parts:
            query_parts.append(options.query)

        query = "+".join(query_parts)

        # Build request params
        page_size = min(options.limit, 40)  # Google max is 40
        params: dict[str, Any] = {
            "q": query,
            "maxResults": page_size,
            "startIndex": (options.page - 1) * page_size,
            "printType": "books",  # Exclude magazines
        }

        # Map sort order (Google only supports relevance and newest)
        sort = SORT_MAPPING.get(options.sort)
        if sort:  # Only add if not default (relevance)
            params["orderBy"] = sort

        # Add language filter if specified
        if options.language:
            params["langRestrict"] = options.language

        result = self._make_request("/volumes", params)
        if result is None:
            raise _GoogleBooksRequestError

        books: list[BookMetadata] = []
        try:
            items = result.get("items", [])
            for item in items:
                book = self._parse_volume(item)
                if book:
                    books.append(book)

            logger.info("Google Books search '%s' returned %s results", query, len(books))

        except Exception:
            logger.exception("Google Books search error")
            return []
        return books

    @cacheable(
        ttl_key="METADATA_CACHE_BOOK_TTL",
        ttl_default=600,
        key_prefix="googlebooks:book",
    )
    def get_book(self, book_id: str) -> BookMetadata | None:
        """Get book details by Google Books volume ID."""
        try:
            result = self._make_request(f"/volumes/{book_id}", {})
            if not result:
                return None

            return self._parse_volume(result)

        except Exception:
            logger.exception("Google Books get_book error")
            return None

    @cacheable(
        ttl_key="METADATA_CACHE_BOOK_TTL",
        ttl_default=600,
        key_prefix="googlebooks:isbn",
    )
    def search_paginated(self, options: MetadataSearchOptions) -> SearchResult:
        """Google returns at most 40 per page, so a full page is 40 whatever limit asked for."""
        return super().search_paginated(replace(options, limit=min(options.limit, 40)))

    def search_by_isbn(self, isbn: str) -> BookMetadata | None:
        """Search for a book by ISBN-10 or ISBN-13."""
        # Clean ISBN (remove hyphens and spaces)
        clean_isbn = isbn.replace("-", "").replace(" ", "").strip()

        # Use ISBN operator for precise lookup
        params: dict[str, Any] = {
            "q": f"isbn:{clean_isbn}",
            "maxResults": 1,
        }

        try:
            result = self._make_request("/volumes", params)
            if not result:
                return None

            items = result.get("items", [])
            if not items:
                logger.debug("No Google Books result for ISBN: %s", isbn)
                return None

            return self._parse_volume(items[0])

        except Exception:
            logger.exception("Google Books ISBN search error")
            return None

    def _make_request(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any] | None:
        """Make authenticated API request to endpoint."""
        if not self.api_key:
            logger.warning("Google Books API key not configured")
            return None

        # Add API key to params
        params["key"] = self.api_key

        url = f"{GOOGLE_BOOKS_BASE_URL}{endpoint}"

        try:
            response = self.session.get(url, params=params, timeout=15, verify=get_ssl_verify(url))
            response.raise_for_status()
            return response.json()

        except requests.Timeout:
            logger.warning("Google Books API request timed out")
            return None
        except requests.HTTPError as e:
            if e.response is not None:
                if e.response.status_code == _HTTP_STATUS_FORBIDDEN:
                    # Quota exceeded or invalid API key
                    logger.exception(
                        "Google Books API: quota exceeded or invalid API key (HTTP 403)"
                    )
                elif e.response.status_code == _HTTP_STATUS_BAD_REQUEST:
                    logger.warning("Google Books API: bad request - %s", e)
                elif e.response.status_code == _HTTP_STATUS_NOT_FOUND:
                    logger.debug("Google Books: volume not found")
                else:
                    logger.exception("Google Books API HTTP error")
            else:
                logger.exception("Google Books API HTTP error")
            return None
        except Exception:
            logger.exception("Google Books API request failed")
            return None

    def _parse_volume(self, volume: dict[str, Any]) -> BookMetadata | None:
        """Parse a volume object into BookMetadata."""
        try:
            volume_id = volume.get("id")
            volume_info = volume.get("volumeInfo", {})

            title = volume_info.get("title")
            if not volume_id or not title:
                return None

            authors = volume_info.get("authors", [])

            # ISBNs - extract from industryIdentifiers
            isbn_10 = None
            isbn_13 = None
            for identifier in volume_info.get("industryIdentifiers", []):
                id_type = identifier.get("type", "")
                id_value = identifier.get("identifier", "")
                if id_type == "ISBN_10" and not isbn_10:
                    isbn_10 = id_value
                elif id_type == "ISBN_13" and not isbn_13:
                    isbn_13 = id_value

            # Cover URL - prefer larger images
            image_links = volume_info.get("imageLinks", {})
            cover_url = (
                image_links.get("large")
                or image_links.get("medium")
                or image_links.get("small")
                or image_links.get("thumbnail")
                or image_links.get("smallThumbnail")
            )
            # Remove edge=curl parameter and upgrade to https
            if cover_url:
                cover_url = cover_url.replace("&edge=curl", "").replace("http://", "https://")

            # Publisher
            publisher = volume_info.get("publisher")

            # Publish year - extract from publishedDate (YYYY-MM-DD or YYYY)
            publish_year = None
            published_date = volume_info.get("publishedDate", "")
            if published_date:
                with suppress(ValueError, TypeError):
                    publish_year = int(published_date[:4])

            # Language
            language = volume_info.get("language")

            # Genres/categories (limit to 5)
            genres = volume_info.get("categories", [])[:5]

            # Description (may contain HTML - leave as-is for UI to sanitize)
            description = volume_info.get("description")

            # Source URL
            source_url = volume_info.get("infoLink")

            # Build display fields - rating only
            display_fields: list[DisplayField] = []

            average_rating = volume_info.get("averageRating")
            ratings_count = volume_info.get("ratingsCount")
            if average_rating is not None:
                rating_str = f"{average_rating:.1f}"
                if ratings_count:
                    rating_str += f" ({ratings_count:,})"
                display_fields.append(DisplayField(label="Rating", value=rating_str, icon="star"))

            return BookMetadata(
                provider="googlebooks",
                provider_id=volume_id,
                title=title,
                provider_display_name="Google Books",
                authors=authors,
                isbn_10=isbn_10,
                isbn_13=isbn_13,
                cover_url=cover_url,
                description=description,
                publisher=publisher,
                publish_year=publish_year,
                language=language,
                genres=genres,
                source_url=source_url,
                display_fields=display_fields,
            )

        except (AttributeError, TypeError, ValueError) as e:
            logger.debug("Failed to parse Google Books volume: %s", e)
            return None


def _test_googlebooks_connection(
    current_values: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Test the Google Books API connection using current form values."""
    current_values = current_values or {}

    # Use current form values first, fall back to saved config
    raw_key = current_values.get("GOOGLEBOOKS_API_KEY") or app_config.get("GOOGLEBOOKS_API_KEY", "")
    api_key = _normalize_googlebooks_api_key(raw_key)

    if not api_key:
        return {
            "success": False,
            "message": "API key is required",
        }

    try:
        provider = GoogleBooksProvider(api_key=api_key)
        # Simple test search
        result = provider._make_request("/volumes", {"q": "test", "maxResults": 1})
        test_result = {
            "success": False,
            "message": "API request failed - check your API key",
        }

        if result is not None and "items" in result:
            test_result = {
                "success": True,
                "message": "Successfully connected to Google Books API",
            }
        elif result is not None:
            test_result = {
                "success": True,
                "message": "API connected but returned no results for test query",
            }
    except Exception as e:
        logger.exception("Google Books connection test failed")
        return {"success": False, "message": f"Connection failed: {e!s}"}
    return test_result


# Sort options for settings UI
_GOOGLEBOOKS_SORT_OPTIONS = [
    {"value": "relevance", "label": "Most relevant"},
    {"value": "newest", "label": "Newest"},
]


@register_settings("googlebooks", "Google Books", icon="book", order=53, group="metadata_providers")
def googlebooks_settings() -> list[SettingsField]:
    """Google Books metadata provider settings."""
    return [
        HeadingField(
            key="googlebooks_heading",
            title="Google Books",
            description=(
                "Access Google's comprehensive book database. "
                "Requires a free API key with ~1000 requests/day quota."
            ),
            link_url="https://console.cloud.google.com/apis/library/books.googleapis.com",
            link_text="Get API Key",
        ),
        CheckboxField(
            key="GOOGLEBOOKS_ENABLED",
            label="Enable Google Books",
            description="Enable Google Books as a metadata provider for book searches",
            default=False,
        ),
        PasswordField(
            key="GOOGLEBOOKS_API_KEY",
            label="API Key",
            description=(
                "Get your API key from Google Cloud Console (APIs & Services > Credentials)"
            ),
            required=True,
        ),
        ActionButton(
            key="test_connection",
            label="Test Connection",
            description="Verify your API key works",
            style="primary",
            callback=_test_googlebooks_connection,
        ),
        SelectField(
            key="GOOGLEBOOKS_DEFAULT_SORT",
            label="Default Sort Order",
            description="Default sort order for Google Books search results.",
            options=_GOOGLEBOOKS_SORT_OPTIONS,
            default="relevance",
        ),
    ]
