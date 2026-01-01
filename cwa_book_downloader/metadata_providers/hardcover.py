"""Hardcover.app metadata provider. Requires API key."""

import requests
from datetime import datetime
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
    SearchResult,
    SearchType,
    SortOrder,
    register_provider,
    register_provider_kwargs,
    TextSearchField,
)

logger = setup_logger(__name__)

HARDCOVER_API_URL = "https://api.hardcover.app/v1/graphql"
HARDCOVER_PAGE_SIZE = 25  # Hardcover API returns max 25 results per page


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
    """Combine headline (tagline) and description into a single description."""
    if headline and description:
        return f"{headline}\n\n{description}"
    return headline or description


def _extract_cover_url(data: Dict, *keys: str) -> Optional[str]:
    """Extract cover URL from data dict, trying multiple keys.

    Handles both string URLs and dict with 'url' key.
    """
    for key in keys:
        value = data.get(key)
        if value:
            if isinstance(value, str):
                return value
            if isinstance(value, dict):
                return value.get("url")
    return None


def _extract_publish_year(data: Dict) -> Optional[int]:
    """Extract publish year from release_year or release_date fields."""
    if data.get("release_year"):
        try:
            return int(data["release_year"])
        except (ValueError, TypeError):
            pass
    if data.get("release_date"):
        try:
            return int(str(data["release_date"])[:4])
        except (ValueError, TypeError):
            pass
    return None


def _build_source_url(slug: str) -> Optional[str]:
    """Build Hardcover source URL from book slug."""
    return f"https://hardcover.app/books/{slug}" if slug else None


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
        raw_key = api_key or app_config.get("HARDCOVER_API_KEY", "")
        # Strip "Bearer " prefix if user pasted the full auth header from Hardcover
        self.api_key = raw_key.removeprefix("Bearer ").strip() if raw_key else ""
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
        return self.search_paginated(options).books

    def search_paginated(self, options: MetadataSearchOptions) -> SearchResult:
        """Search for books with pagination info.

        Args:
            options: Search options (query, type, sort, pagination, fields).

        Returns:
            SearchResult with books and pagination info.
        """
        if not self.api_key:
            logger.warning("Hardcover API key not configured")
            return SearchResult(books=[], page=options.page, total_found=0, has_more=False)

        # Handle ISBN search separately
        if options.search_type == SearchType.ISBN:
            result = self.search_by_isbn(options.query)
            books = [result] if result else []
            return SearchResult(books=books, page=1, total_found=len(books), has_more=False)

        # Build cache key from options (include fields and settings for cache differentiation)
        fields_key = ":".join(f"{k}={v}" for k, v in sorted(options.fields.items()))
        exclude_compilations = app_config.get("HARDCOVER_EXCLUDE_COMPILATIONS", False)
        exclude_unreleased = app_config.get("HARDCOVER_EXCLUDE_UNRELEASED", False)
        cache_key = f"{options.query}:{options.search_type.value}:{options.sort.value}:{options.limit}:{options.page}:{fields_key}:excl_comp={exclude_compilations}:excl_unrel={exclude_unreleased}"
        return self._search_cached(cache_key, options)

    @cacheable(ttl_key="METADATA_CACHE_SEARCH_TTL", ttl_default=300, key_prefix="hardcover:search")
    def _search_cached(self, cache_key: str, options: MetadataSearchOptions) -> SearchResult:
        """Cached search implementation.

        Args:
            cache_key: Cache key (used by decorator).
            options: Search options.

        Returns:
            SearchResult with books and pagination info.
        """
        # Determine query and fields based on custom search fields
        # Note: Hardcover API requires 'weights' when using 'fields' parameter
        author_value = options.fields.get("author", "").strip()
        title_value = options.fields.get("title", "").strip()
        series_value = options.fields.get("series", "").strip()

        if series_value and not author_value and not title_value:
            query, search_fields, search_weights = series_value, "series_names", "1"
        elif author_value and not title_value and not series_value:
            query, search_fields, search_weights = author_value, "author_names", "1"
        elif title_value and not author_value and not series_value:
            query, search_fields, search_weights = title_value, "title,alternative_titles", "5,1"
        elif author_value and title_value and not series_value:
            query = f"{title_value} {author_value}"
            search_fields, search_weights = "title,alternative_titles,author_names", "5,1,3"
        elif series_value:
            query = " ".join(p for p in [series_value, title_value, author_value] if p)
            search_fields, search_weights = "series_names,title,alternative_titles,author_names", "5,3,1,2"
        else:
            query, search_fields, search_weights = options.query, None, None


        # Build GraphQL query - include fields/weights parameters only when needed
        if search_fields:
            graphql_query = """
            query SearchBooks($query: String!, $limit: Int!, $page: Int!, $sort: String, $fields: String, $weights: String) {
                search(query: $query, query_type: "Book", per_page: $limit, page: $page, sort: $sort, fields: $fields, weights: $weights) {
                    results
                }
            }
            """
        else:
            graphql_query = """
            query SearchBooks($query: String!, $limit: Int!, $page: Int!, $sort: String) {
                search(query: $query, query_type: "Book", per_page: $limit, page: $page, sort: $sort) {
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


        try:
            result = self._execute_query(graphql_query, variables)
            if not result:
                logger.debug("Hardcover search: No result from API")
                return SearchResult(books=[], page=options.page, total_found=0, has_more=False)

            # Extract hits from Typesense response
            results_obj = result.get("search", {}).get("results", {})
            if isinstance(results_obj, dict):
                hits = results_obj.get("hits", [])
                found_count = results_obj.get("found", 0)
            else:
                hits = results_obj if isinstance(results_obj, list) else []
                found_count = 0

            # Parse hits, filtering compilations and unreleased books if enabled
            exclude_compilations = app_config.get("HARDCOVER_EXCLUDE_COMPILATIONS", False)
            exclude_unreleased = app_config.get("HARDCOVER_EXCLUDE_UNRELEASED", False)
            current_year = datetime.now().year
            books = []
            for hit in hits:
                item = hit.get("document", hit) if isinstance(hit, dict) else hit
                if not isinstance(item, dict):
                    continue
                if exclude_compilations and item.get("compilation"):
                    continue
                if exclude_unreleased:
                    release_year = item.get("release_year")
                    if release_year is not None and release_year > current_year:
                        continue
                book = self._parse_search_result(item)
                if book:
                    books.append(book)

            # If series order sort is selected and series field is provided,
            # filter to exact matches and sort by position
            if options.sort == SortOrder.SERIES_ORDER and series_value and books:
                books = self._apply_series_ordering(books, series_value)

            logger.info(f"Hardcover search '{query}' (fields={search_fields}) returned {len(books)} results")

            # Calculate if there are more results
            results_so_far = (options.page - 1) * HARDCOVER_PAGE_SIZE + len(hits)
            has_more = results_so_far < found_count

            return SearchResult(
                books=books,
                page=options.page,
                total_found=found_count,
                has_more=has_more
            )

        except Exception as e:
            logger.error(f"Hardcover search error: {e}")
            return SearchResult(books=[], page=options.page, total_found=0, has_more=False)

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
        # Include editions with titles and languages for localized search support
        graphql_query = """
        query GetBook($id: Int!) {
            books(where: {id: {_eq: $id}}, limit: 1) {
                id
                title
                subtitle
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
                editions(limit: 20, order_by: {users_count: desc}) {
                    title
                    language {
                        language
                        code2
                        code3
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
                    subtitle
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

            # Extract authors - use contribution_types to filter author_names if available
            authors = []

            author_names = item.get("author_names", [])
            if isinstance(author_names, str):
                author_names = [author_names]

            contribution_types = item.get("contribution_types", [])

            # If we have parallel arrays, filter to only "Author" contributions
            if contribution_types and len(contribution_types) == len(author_names):
                for name, contrib_type in zip(author_names, contribution_types):
                    if contrib_type == "Author":
                        authors.append(name)
            elif author_names:
                # No contribution_types or length mismatch - use all names as fallback
                authors = author_names

            cover_url = _extract_cover_url(item, "image")
            publish_year = _extract_publish_year(item)
            source_url = _build_source_url(item.get("slug", ""))

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

            # Extract subtitle if available in search results
            subtitle = item.get("subtitle")

            return BookMetadata(
                provider="hardcover",
                provider_id=str(book_id),
                title=title,
                subtitle=subtitle,
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

        cover_url = _extract_cover_url(book, "cached_image", "image")
        publish_year = _extract_publish_year(book)

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

        source_url = _build_source_url(book.get("slug", ""))

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

        # Extract titles by language from editions
        # This allows searching with localized titles when language filter is active
        titles_by_language: Dict[str, str] = {}
        editions = book.get("editions", [])
        for edition in editions:
            edition_title = edition.get("title")
            lang_data = edition.get("language")
            if edition_title and lang_data:
                # Store by various language identifiers for flexible matching
                # Language name (e.g., "German", "English")
                lang_name = lang_data.get("language")
                # 2-letter code (e.g., "de", "en")
                code2 = lang_data.get("code2")
                # 3-letter code (e.g., "deu", "eng")
                code3 = lang_data.get("code3")

                # Store with all available keys (first title wins for each language)
                if lang_name and lang_name not in titles_by_language:
                    titles_by_language[lang_name] = edition_title
                if code2 and code2 not in titles_by_language:
                    titles_by_language[code2] = edition_title
                if code3 and code3 not in titles_by_language:
                    titles_by_language[code3] = edition_title

        return BookMetadata(
            provider="hardcover",
            provider_id=str(book["id"]),
            title=book["title"],
            subtitle=book.get("subtitle"),
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
            titles_by_language=titles_by_language,
        )


def _test_hardcover_connection(current_values: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Test the Hardcover API connection using current form values."""
    from cwa_book_downloader.core.config import config as app_config

    current_values = current_values or {}

    # Use current form values first, fall back to saved config
    raw_key = current_values.get("HARDCOVER_API_KEY") or app_config.get("HARDCOVER_API_KEY", "")
    # Strip "Bearer " prefix if user pasted the full auth header from Hardcover
    api_key = raw_key.removeprefix("Bearer ").strip() if raw_key else ""

    key_len = len(api_key) if api_key else 0
    logger.debug(f"Hardcover test: key length={key_len}")

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
        CheckboxField(
            key="HARDCOVER_EXCLUDE_COMPILATIONS",
            label="Exclude Compilations",
            description="Filter out compilations, anthologies, and omnibus editions from search results",
            default=False,
        ),
        CheckboxField(
            key="HARDCOVER_EXCLUDE_UNRELEASED",
            label="Exclude Unreleased Books",
            description="Filter out books with a release year in the future",
            default=False,
        ),
    ]
