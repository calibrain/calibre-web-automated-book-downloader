"""Hardcover.app metadata provider. Requires API key."""

import requests
from typing import Any, Dict, List, Optional

from cwa_book_downloader.core.cache import cacheable
from cwa_book_downloader.core.logger import setup_logger
from cwa_book_downloader.core.settings_registry import (
    register_settings,
    CheckboxField,
    PasswordField,
    SelectField,
    ActionButton,
    HeadingField,
)
from cwa_book_downloader.core.config import config as app_config
from cwa_book_downloader.metadata_providers import (
    BookMetadata,
    DisplayField,
    MetadataProvider,
    MetadataSearchOptions,
    SearchType,
    SortOrder,
    register_provider,
    register_provider_kwargs,
    TextSearchField,
)

logger = setup_logger(__name__)

HARDCOVER_API_URL = "https://api.hardcover.app/v1/graphql"


# Mapping from abstract sort order to Hardcover sort parameter
# Note: release_year is more consistently populated than release_date_i
SORT_MAPPING: Dict[SortOrder, str] = {
    SortOrder.RELEVANCE: "_text_match:desc,users_count:desc",
    SortOrder.POPULARITY: "users_count:desc",
    SortOrder.RATING: "rating:desc",
    SortOrder.NEWEST: "release_year:desc",
    SortOrder.OLDEST: "release_year:asc",
}

# Mapping from abstract search type to Hardcover fields parameter
SEARCH_TYPE_FIELDS: Dict[SearchType, str] = {
    SearchType.GENERAL: "title,isbns,series_names,author_names,alternative_titles",
    SearchType.TITLE: "title,alternative_titles",
    SearchType.AUTHOR: "author_names",
    # ISBN is handled separately via search_by_isbn()
}


def _combine_headline_description(headline: Optional[str], description: Optional[str]) -> Optional[str]:
    """Combine headline (tagline) and description into a single description.

    Hardcover stores a short 'headline' (tagline/promotional text) separately
    from the main description. This combines them for display.

    Args:
        headline: Short promotional text or tagline.
        description: Full book synopsis/description.

    Returns:
        Combined description with headline as the first line, or just one if only one exists.
    """
    if headline and description:
        # Add headline as first paragraph, followed by description
        return f"{headline}\n\n{description}"
    elif headline:
        return headline
    elif description:
        return description
    return None


@register_provider_kwargs("hardcover")
def _hardcover_kwargs() -> Dict[str, Any]:
    """Provide Hardcover-specific constructor kwargs."""
    return {"api_key": app_config.get("HARDCOVER_API_KEY", "")}


@register_provider("hardcover")
class HardcoverProvider(MetadataProvider):
    """Hardcover.app metadata provider using GraphQL API."""

    name = "hardcover"
    display_name = "Hardcover"
    requires_auth = True
    supported_sorts = [
        SortOrder.RELEVANCE,
        SortOrder.POPULARITY,
        SortOrder.RATING,
        SortOrder.NEWEST,
        SortOrder.OLDEST,
        SortOrder.SERIES_ORDER,
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
        TextSearchField(
            key="series",
            label="Series",
            description="Search by series name",
        ),
    ]

    def __init__(self, api_key: Optional[str] = None):
        """Initialize provider with API key.

        Args:
            api_key: Hardcover API key. If not provided, uses config singleton.
        """
        self.api_key = api_key or app_config.get("HARDCOVER_API_KEY", "")
        self.session = requests.Session()
        if self.api_key:
            self.session.headers.update({
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            })

    def is_available(self) -> bool:
        """Check if provider is configured with an API key."""
        return bool(self.api_key)

    def search(self, options: MetadataSearchOptions) -> List[BookMetadata]:
        """Search for books using Hardcover's search API.

        Args:
            options: Search options (query, type, sort, pagination, fields).

        Returns:
            List of BookMetadata objects.
        """
        if not self.api_key:
            logger.warning("Hardcover API key not configured")
            return []

        # Handle ISBN search separately
        if options.search_type == SearchType.ISBN:
            result = self.search_by_isbn(options.query)
            return [result] if result else []

        # Build cache key from options (include fields for cache differentiation)
        fields_key = ":".join(f"{k}={v}" for k, v in sorted(options.fields.items()))
        cache_key = f"{options.query}:{options.search_type.value}:{options.sort.value}:{options.limit}:{options.page}:{fields_key}"
        return self._search_cached(cache_key, options)

    @cacheable(ttl_key="METADATA_CACHE_SEARCH_TTL", ttl_default=300, key_prefix="hardcover:search")
    def _search_cached(self, cache_key: str, options: MetadataSearchOptions) -> List[BookMetadata]:
        """Cached search implementation.

        Args:
            cache_key: Cache key (used by decorator).
            options: Search options.

        Returns:
            List of BookMetadata objects.
        """
        # Determine query and fields based on custom search fields
        # Field-first search: when a specific field has a value, search that field
        author_value = options.fields.get("author", "").strip()
        title_value = options.fields.get("title", "").strip()
        series_value = options.fields.get("series", "").strip()

        logger.debug(f"Field-first search check: author='{author_value}', title='{title_value}', series='{series_value}'")

        # Determine what to search and which fields to target
        # Note: Hardcover API requires 'weights' when using 'fields' parameter
        if series_value and not author_value and not title_value:
            # Series-only search: search series_names field
            query = series_value
            search_fields = "series_names"
            search_weights = "1"
            logger.debug(f"Series-only search: query='{query}', fields='{search_fields}'")
        elif author_value and not title_value and not series_value:
            # Author-only search: search author_names field with author query
            query = author_value
            search_fields = "author_names"
            search_weights = "1"
            logger.debug(f"Author-only search: query='{query}', fields='{search_fields}'")
        elif title_value and not author_value and not series_value:
            # Title-only search: search title fields with title query
            query = title_value
            search_fields = "title,alternative_titles"
            search_weights = "5,1"
            logger.debug(f"Title-only search: query='{query}', fields='{search_fields}'")
        elif author_value and title_value and not series_value:
            # Author + Title: combine into query, search both fields
            query = f"{title_value} {author_value}"
            search_fields = "title,alternative_titles,author_names"
            search_weights = "5,1,3"
            logger.debug(f"Combined title+author search: query='{query}', fields='{search_fields}'")
        elif series_value:
            # Series with other fields: include series_names in search
            parts = [p for p in [series_value, title_value, author_value] if p]
            query = " ".join(parts)
            search_fields = "series_names,title,alternative_titles,author_names"
            search_weights = "5,3,1,2"
            logger.debug(f"Combined search with series: query='{query}', fields='{search_fields}'")
        else:
            # No custom fields: use general query with all default fields
            query = options.query
            search_fields = None
            search_weights = None
            logger.debug(f"General search: query='{query}', no field restriction")

        # Build GraphQL query with optional fields/weights parameters
        if search_fields:
            graphql_query = """
            query SearchBooks($query: String!, $limit: Int!, $page: Int!, $sort: String, $fields: String, $weights: String) {
                search(
                    query: $query,
                    query_type: "Book",
                    per_page: $limit,
                    page: $page,
                    sort: $sort,
                    fields: $fields,
                    weights: $weights
                ) {
                    results
                }
            }
            """
        else:
            graphql_query = """
            query SearchBooks($query: String!, $limit: Int!, $page: Int!, $sort: String) {
                search(
                    query: $query,
                    query_type: "Book",
                    per_page: $limit,
                    page: $page,
                    sort: $sort
                ) {
                    results
                }
            }
            """

        # Map abstract sort order to Hardcover's sort parameter
        sort_param = SORT_MAPPING.get(options.sort, SORT_MAPPING[SortOrder.RELEVANCE])

        variables = {
            "query": query,
            "limit": options.limit,
            "page": options.page,
            "sort": sort_param,
        }

        if search_fields:
            variables["fields"] = search_fields
            variables["weights"] = search_weights

        logger.debug(f"GraphQL variables: {variables}")

        try:
            result = self._execute_query(graphql_query, variables)
            if not result:
                logger.debug("Hardcover search: No result from API")
                return []

            search_data = result.get("search", {})

            # Results is a Typesense response object with hits array
            results_obj = search_data.get("results", {})
            if isinstance(results_obj, dict):
                hits = results_obj.get("hits", [])
            else:
                hits = results_obj if isinstance(results_obj, list) else []

            # Parse the search results - each hit has a 'document' field
            books = []
            for hit in hits:
                # Get the document from the hit
                item = hit.get("document", hit) if isinstance(hit, dict) else hit
                if isinstance(item, dict):
                    book = self._parse_search_result(item)
                    if book:
                        books.append(book)

            # If series order sort is selected and series field is provided,
            # filter to exact matches and sort by position
            if options.sort == SortOrder.SERIES_ORDER and series_value and books:
                books = self._apply_series_ordering(books, series_value)

            logger.info(f"Hardcover search '{query}' (fields={search_fields}) returned {len(books)} results")
            return books

        except Exception as e:
            logger.error(f"Hardcover search error: {e}")
            return []

    def _apply_series_ordering(self, books: List[BookMetadata], series_name: str) -> List[BookMetadata]:
        """Filter books to exact series match and sort by series position.

        Args:
            books: List of books from search results.
            series_name: The series name to match.

        Returns:
            Filtered and sorted list of books.
        """
        series_name_lower = series_name.lower()
        books_with_position = []

        for book in books:
            # Fetch full book details to get series info
            full_book = self.get_book(book.provider_id)
            if not full_book or not full_book.series_name:
                continue

            # Exact match on series name
            if full_book.series_name.lower() != series_name_lower:
                continue

            # Merge series info into the search result book
            book.series_name = full_book.series_name
            book.series_position = full_book.series_position
            book.series_count = full_book.series_count
            # Also grab description if search didn't have it
            if not book.description and full_book.description:
                book.description = full_book.description
            books_with_position.append(book)

        # Sort by series position (books without position go last)
        books_with_position.sort(key=lambda b: (b.series_position is None, b.series_position or 0))

        logger.debug(f"Series ordering: filtered {len(books)} -> {len(books_with_position)} books for '{series_name}'")
        return books_with_position

    @cacheable(ttl_key="METADATA_CACHE_BOOK_TTL", ttl_default=600, key_prefix="hardcover:book")
    def get_book(self, book_id: str) -> Optional[BookMetadata]:
        """Get book details by Hardcover ID.

        Args:
            book_id: Hardcover book ID.

        Returns:
            BookMetadata or None if not found.
        """
        if not self.api_key:
            logger.warning("Hardcover API key not configured")
            return None

        # Query for specific book by ID
        # Use contributions with filter to get only primary authors (not translators/narrators)
        # Also include cached_contributors as fallback if contributions is empty
        # Include featured_book_series for series info
        graphql_query = """
        query GetBook($id: Int!) {
            books(where: {id: {_eq: $id}}, limit: 1) {
                id
                title
                slug
                release_date
                headline
                description
                pages
                cached_image
                cached_tags
                cached_contributors
                contributions(where: {contribution: {_eq: "Author"}}) {
                    author {
                        name
                    }
                }
                default_physical_edition {
                    isbn_10
                    isbn_13
                }
                featured_book_series {
                    position
                    series {
                        name
                        primary_books_count
                    }
                }
            }
        }
        """

        try:
            book_id_int = int(book_id)
            result = self._execute_query(graphql_query, {"id": book_id_int})
            if not result:
                return None

            books = result.get("books", [])
            if not books:
                return None

            return self._parse_book(books[0])

        except ValueError:
            logger.error(f"Invalid book ID: {book_id}")
            return None
        except Exception as e:
            logger.error(f"Hardcover get_book error: {e}")
            return None

    @cacheable(ttl_key="METADATA_CACHE_BOOK_TTL", ttl_default=600, key_prefix="hardcover:isbn")
    def search_by_isbn(self, isbn: str) -> Optional[BookMetadata]:
        """Search for a book by ISBN.

        Args:
            isbn: ISBN-10 or ISBN-13.

        Returns:
            BookMetadata or None if not found.
        """
        if not self.api_key:
            logger.warning("Hardcover API key not configured")
            return None

        # Clean ISBN (remove hyphens)
        clean_isbn = isbn.replace("-", "").strip()

        # Search for editions with matching ISBN
        # Use contributions with filter to get only primary authors (not translators/narrators)
        graphql_query = """
        query SearchByISBN($isbn: String!) {
            editions(
                where: {
                    _or: [
                        {isbn_10: {_eq: $isbn}},
                        {isbn_13: {_eq: $isbn}}
                    ]
                },
                limit: 1
            ) {
                isbn_10
                isbn_13
                book {
                    id
                    title
                    slug
                    release_date
                    headline
                    description
                    pages
                    cached_image
                    cached_tags
                    contributions(where: {contribution: {_eq: "Author"}}) {
                        author {
                            name
                        }
                    }
                }
            }
        }
        """

        try:
            result = self._execute_query(graphql_query, {"isbn": clean_isbn})
            if not result:
                return None

            editions = result.get("editions", [])
            if not editions:
                logger.debug(f"No Hardcover book found for ISBN: {isbn}")
                return None

            edition = editions[0]
            book_data = edition.get("book", {})
            if not book_data:
                return None

            # Add ISBN data from edition to book data
            book_data["isbn_10"] = edition.get("isbn_10")
            book_data["isbn_13"] = edition.get("isbn_13")

            return self._parse_book(book_data)

        except Exception as e:
            logger.error(f"Hardcover ISBN search error: {e}")
            return None

    def _execute_query(self, query: str, variables: Dict[str, Any]) -> Optional[Dict]:
        """Execute a GraphQL query.

        Args:
            query: GraphQL query string.
            variables: Query variables.

        Returns:
            Response data dict or None on error.
        """
        try:
            response = self.session.post(
                HARDCOVER_API_URL,
                json={"query": query, "variables": variables},
                timeout=15
            )
            response.raise_for_status()

            data = response.json()

            if "errors" in data:
                logger.error(f"GraphQL errors: {data['errors']}")
                return None

            return data.get("data")

        except requests.Timeout:
            logger.warning("Hardcover API request timed out")
            return None
        except requests.HTTPError as e:
            if e.response.status_code == 401:
                logger.error("Hardcover API key is invalid")
            else:
                logger.error(f"Hardcover API HTTP error: {e}")
            return None
        except Exception as e:
            logger.error(f"Hardcover API request failed: {e}")
            return None

    def _parse_search_result(self, item: Dict) -> Optional[BookMetadata]:
        """Parse a search result item into BookMetadata.

        Args:
            item: Search result item dict.

        Returns:
            BookMetadata or None if parsing fails.
        """
        try:
            book_id = item.get("id") or item.get("document", {}).get("id")
            title = item.get("title") or item.get("document", {}).get("title")

            if not book_id or not title:
                return None

            # Extract authors from various possible fields
            authors = []
            if "author_names" in item:
                authors = item["author_names"] if isinstance(item["author_names"], list) else [item["author_names"]]
            elif "cached_contributors" in item:
                for contrib in item.get("cached_contributors", []):
                    if isinstance(contrib, dict) and contrib.get("name"):
                        authors.append(contrib["name"])
                    elif isinstance(contrib, str):
                        authors.append(contrib)

            # Get cover URL
            cover_url = None
            if "image" in item and item["image"]:
                cover_url = item["image"] if isinstance(item["image"], str) else item["image"].get("url")

            # Extract year - prefer release_year if available, fall back to release_date
            publish_year = None
            if "release_year" in item and item["release_year"]:
                try:
                    publish_year = int(item["release_year"])
                except (ValueError, TypeError):
                    pass
            elif "release_date" in item and item["release_date"]:
                try:
                    publish_year = int(str(item["release_date"])[:4])
                except (ValueError, TypeError):
                    pass

            slug = item.get("slug", "")
            source_url = f"https://hardcover.app/books/{slug}" if slug else None

            # Build display fields from Hardcover-specific data
            display_fields = []

            # Rating (e.g., "4.5 (3,764)")
            rating = item.get("rating")
            ratings_count = item.get("ratings_count")
            if rating is not None:
                rating_str = f"{rating:.1f}"
                if ratings_count:
                    rating_str += f" ({ratings_count:,})"
                display_fields.append(DisplayField(label="Rating", value=rating_str, icon="star"))

            # Readers (users who have this book)
            users_count = item.get("users_count")
            if users_count:
                display_fields.append(DisplayField(label="Readers", value=f"{users_count:,}", icon="users"))

            # Combine headline and description if both present
            headline = item.get("headline")
            description = item.get("description")
            full_description = _combine_headline_description(headline, description)

            return BookMetadata(
                provider="hardcover",
                provider_id=str(book_id),
                title=title,
                provider_display_name="Hardcover",
                authors=authors,
                cover_url=cover_url,
                description=full_description,
                publish_year=publish_year,
                source_url=source_url,
                display_fields=display_fields,
            )

        except Exception as e:
            logger.debug(f"Failed to parse Hardcover search result: {e}")
            return None

    def _parse_book(self, book: Dict) -> BookMetadata:
        """Parse a book object into BookMetadata.

        Args:
            book: Book data dict from GraphQL response.

        Returns:
            BookMetadata object.
        """
        # Extract authors - try contributions first (filtered), fall back to cached_contributors
        authors = []
        contributions = book.get("contributions") or []
        cached_contributors = book.get("cached_contributors") or []

        logger.debug(f"_parse_book [{book.get('id')}]: contributions={contributions}, cached_contributors={cached_contributors}")

        # Try contributions first (filtered to "Author" role only - cleaner data)
        for contrib in contributions:
            author = contrib.get("author", {})
            if author and author.get("name"):
                authors.append(author["name"])

        # Fallback to cached_contributors if no authors found
        if not authors:
            for contrib in cached_contributors:
                if isinstance(contrib, dict):
                    # Handle nested structure: {"author": {"name": "..."}, "contribution": ...}
                    if contrib.get("author", {}).get("name"):
                        authors.append(contrib["author"]["name"])
                    # Handle flat structure: {"name": "..."}
                    elif contrib.get("name"):
                        authors.append(contrib["name"])
                elif isinstance(contrib, str):
                    authors.append(contrib)

        logger.debug(f"_parse_book [{book.get('id')}]: final authors={authors}")

        # Get cover URL from cached_image (jsonb) or image relationship
        cover_url = None
        if book.get("cached_image"):
            cached = book["cached_image"]
            if isinstance(cached, dict):
                cover_url = cached.get("url")
            elif isinstance(cached, str):
                cover_url = cached
        elif book.get("image"):
            img = book["image"]
            cover_url = img if isinstance(img, str) else img.get("url")

        # Extract year from release_date
        publish_year = None
        if book.get("release_date"):
            try:
                publish_year = int(str(book["release_date"])[:4])
            except (ValueError, TypeError):
                pass

        # Extract genres from cached_tags
        genres = []
        for tag in book.get("cached_tags", []):
            if isinstance(tag, dict) and tag.get("tag"):
                genres.append(tag["tag"])
            elif isinstance(tag, str):
                genres.append(tag)

        # Get ISBN from direct fields, default_physical_edition, or editions
        isbn_10 = book.get("isbn_10")
        isbn_13 = book.get("isbn_13")

        if not isbn_10 and not isbn_13:
            # Try default_physical_edition first
            edition = book.get("default_physical_edition")
            if edition:
                isbn_10 = edition.get("isbn_10")
                isbn_13 = edition.get("isbn_13")

            # Fallback to editions array
            if not isbn_10 and not isbn_13 and book.get("editions"):
                for ed in book["editions"]:
                    if not isbn_10 and ed.get("isbn_10"):
                        isbn_10 = ed["isbn_10"]
                    if not isbn_13 and ed.get("isbn_13"):
                        isbn_13 = ed["isbn_13"]
                    if isbn_10 and isbn_13:
                        break

        slug = book.get("slug", "")
        source_url = f"https://hardcover.app/books/{slug}" if slug else None

        # Combine headline and description if both present
        headline = book.get("headline")
        description = book.get("description")
        full_description = _combine_headline_description(headline, description)

        # Extract series info from featured_book_series
        series_name = None
        series_position = None
        series_count = None
        featured_series = book.get("featured_book_series")
        if featured_series:
            series_position = featured_series.get("position")
            series_data = featured_series.get("series")
            if series_data:
                series_name = series_data.get("name")
                series_count = series_data.get("primary_books_count")

        return BookMetadata(
            provider="hardcover",
            provider_id=str(book["id"]),
            title=book["title"],
            provider_display_name="Hardcover",
            authors=authors,
            isbn_10=isbn_10,
            isbn_13=isbn_13,
            cover_url=cover_url,
            description=full_description,
            publish_year=publish_year,
            genres=genres,
            source_url=source_url,
            series_name=series_name,
            series_position=series_position,
            series_count=series_count,
        )


def _test_hardcover_connection(current_values: Dict[str, Any] = None) -> Dict[str, Any]:
    """Test the Hardcover API connection using current form values."""
    from cwa_book_downloader.core.config import config as app_config

    current_values = current_values or {}

    # Use current form values first, fall back to saved config
    api_key = current_values.get("HARDCOVER_API_KEY") or app_config.get("HARDCOVER_API_KEY", "")

    # Debug: log key info
    key_len = len(api_key) if api_key else 0
    key_preview = f"{api_key[:10]}...{api_key[-10:]}" if key_len > 20 else "(too short)"
    logger.info(f"Hardcover test: key length={key_len}, preview={key_preview}")

    if not api_key:
        # Clear any stored username since there's no key
        _save_connected_username(None)
        return {"success": False, "message": "API key is required"}

    if key_len < 100:
        return {"success": False, "message": f"API key seems too short ({key_len} chars). Expected 500+ chars."}

    try:
        provider = HardcoverProvider(api_key=api_key)
        # Use the 'me' query to test connection (recommended by API docs)
        result = provider._execute_query(
            "query { me { id, username } }",
            {}
        )
        if result is not None:
            # Handle both single object and array response formats
            me_data = result.get("me", {})
            if isinstance(me_data, list) and me_data:
                me_data = me_data[0]
            username = me_data.get("username", "Unknown") if isinstance(me_data, dict) else "Unknown"

            # Save the username for persistent display
            _save_connected_username(username)

            return {"success": True, "message": f"Connected as: {username}"}
        else:
            _save_connected_username(None)
            return {"success": False, "message": "API request failed - check your API key"}
    except Exception as e:
        logger.exception("Hardcover connection test failed")
        _save_connected_username(None)
        return {"success": False, "message": f"Connection failed: {str(e)}"}


def _save_connected_username(username: Optional[str]) -> None:
    """Save or clear the connected username in config."""
    from cwa_book_downloader.core.settings_registry import save_config_file, load_config_file

    config = load_config_file("hardcover")
    if username:
        config["_connected_username"] = username
    else:
        config.pop("_connected_username", None)
    save_config_file("hardcover", config)


def _get_connected_username() -> Optional[str]:
    """Get the stored connected username."""
    from cwa_book_downloader.core.settings_registry import load_config_file

    config = load_config_file("hardcover")
    return config.get("_connected_username")


# Hardcover sort options for settings UI
_HARDCOVER_SORT_OPTIONS = [
    {"value": "relevance", "label": "Most relevant"},
    {"value": "popularity", "label": "Most popular"},
    {"value": "rating", "label": "Highest rated"},
    {"value": "newest", "label": "Newest"},
    {"value": "oldest", "label": "Oldest"},
]


@register_settings("hardcover", "Hardcover", icon="book", order=51, group="metadata_providers")
def hardcover_settings():
    """Hardcover metadata provider settings."""
    # Check for connected username to show status
    connected_user = _get_connected_username()
    test_button_description = f"Connected as: {connected_user}" if connected_user else "Verify your API key works"

    return [
        HeadingField(
            key="hardcover_heading",
            title="Hardcover",
            description="A modern book tracking and discovery platform with a comprehensive API.",
            link_url="https://hardcover.app",
            link_text="hardcover.app",
        ),
        CheckboxField(
            key="HARDCOVER_ENABLED",
            label="Enable Hardcover",
            description="Enable Hardcover as a metadata provider for book searches",
            default=False,
        ),
        PasswordField(
            key="HARDCOVER_API_KEY",
            label="API Key",
            description="Get your API key from hardcover.app/account/api",
            required=True,
            env_supported=False,  # UI-only setting, no ENV var support
        ),
        ActionButton(
            key="test_connection",
            label="Test Connection",
            description=test_button_description,
            style="primary",
            callback=_test_hardcover_connection,
        ),
        SelectField(
            key="HARDCOVER_DEFAULT_SORT",
            label="Default Sort Order",
            description="Default sort order for Hardcover search results.",
            options=_HARDCOVER_SORT_OPTIONS,
            default="relevance",
            env_supported=False,  # UI-only setting
        ),
    ]
