"""Metadata provider plugin system - base classes and registry."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Type, Union


class SearchType(str, Enum):
    """Type of search to perform."""
    GENERAL = "general"  # Search all fields (title, author, ISBN, etc.)
    TITLE = "title"      # Search by title only
    AUTHOR = "author"    # Search by author only
    ISBN = "isbn"        # Search by ISBN


class SortOrder(str, Enum):
    """Sort order for search results."""
    RELEVANCE = "relevance"    # Best match first (default)
    POPULARITY = "popularity"  # Most popular first
    RATING = "rating"          # Highest rated first
    NEWEST = "newest"          # Most recently published first
    OLDEST = "oldest"          # Oldest published first
    SERIES_ORDER = "series_order"  # By series position (requires series field)


# Display labels for sort options
SORT_LABELS: Dict[SortOrder, str] = {
    SortOrder.RELEVANCE: "Most relevant",
    SortOrder.POPULARITY: "Most popular",
    SortOrder.RATING: "Highest rated",
    SortOrder.NEWEST: "Newest",
    SortOrder.OLDEST: "Oldest",
    SortOrder.SERIES_ORDER: "Series order",
}


@dataclass
class TextSearchField:
    """Text input search field."""
    key: str                              # Field identifier (e.g., "author", "publisher")
    label: str                            # Display label in UI
    placeholder: str = ""                 # Placeholder text
    description: str = ""                 # Help text


@dataclass
class NumberSearchField:
    """Numeric input search field."""
    key: str
    label: str
    placeholder: str = ""
    description: str = ""
    min_value: Optional[int] = None
    max_value: Optional[int] = None
    step: int = 1


@dataclass
class SelectSearchField:
    """Single-choice dropdown search field."""
    key: str
    label: str
    options: List[Dict[str, str]] = field(default_factory=list)  # [{value: "", label: ""}]
    placeholder: str = ""
    description: str = ""


@dataclass
class CheckboxSearchField:
    """Boolean checkbox search field."""
    key: str
    label: str
    description: str = ""
    default: bool = False


# Type alias for all search field types
SearchField = Union[TextSearchField, NumberSearchField, SelectSearchField, CheckboxSearchField]


def serialize_search_field(search_field: SearchField) -> Dict[str, Any]:
    """Serialize a search field to dict for API response."""
    result: Dict[str, Any] = {
        "key": search_field.key,
        "label": search_field.label,
        "type": search_field.__class__.__name__,
        "placeholder": getattr(search_field, 'placeholder', ''),
        "description": getattr(search_field, 'description', ''),
    }

    # Add type-specific properties
    if isinstance(search_field, NumberSearchField):
        result["min"] = search_field.min_value
        result["max"] = search_field.max_value
        result["step"] = search_field.step
    elif isinstance(search_field, SelectSearchField):
        result["options"] = search_field.options
    elif isinstance(search_field, CheckboxSearchField):
        result["default"] = search_field.default

    return result


@dataclass
class MetadataSearchOptions:
    """Options for metadata search queries across all providers."""
    query: str
    search_type: SearchType = SearchType.GENERAL
    language: Optional[str] = None  # ISO 639-1 code (e.g., "en", "fr")
    sort: SortOrder = SortOrder.RELEVANCE
    limit: int = 40
    page: int = 1
    fields: Dict[str, Any] = field(default_factory=dict)  # Custom search field values


@dataclass
class DisplayField:
    """A display field for metadata cards (ratings, page counts, etc.)."""
    label: str                       # e.g., "Rating", "Pages", "Readers"
    value: str                       # e.g., "4.5", "496", "8,041"
    icon: Optional[str] = None       # Icon name: "star", "book", "users", "editions"


@dataclass
class BookMetadata:
    """Book from metadata provider (not a specific release)."""
    provider: str                    # Which provider this came from (internal name)
    provider_id: str                 # ID in that provider's system
    title: str

    # Provider display name for UI (e.g., "Open Library" instead of "openlibrary")
    provider_display_name: Optional[str] = None

    # Optional - not all providers have all fields
    authors: List[str] = field(default_factory=list)
    isbn_10: Optional[str] = None
    isbn_13: Optional[str] = None
    cover_url: Optional[str] = None
    description: Optional[str] = None
    publisher: Optional[str] = None
    publish_year: Optional[int] = None
    language: Optional[str] = None
    genres: List[str] = field(default_factory=list)
    source_url: Optional[str] = None  # Link to book on provider's site
    subtitle: Optional[str] = None  # Book subtitle, if any

    # Provider-specific display fields for cards/lists
    display_fields: List[DisplayField] = field(default_factory=list)

    # Series info (if book is part of a series)
    series_name: Optional[str] = None      # Name of the series
    series_position: Optional[float] = None  # This book's position (e.g., 3, 1.5 for novellas)
    series_count: Optional[int] = None     # Total books in the series

    # Alternative titles by language (for localized searches)
    # Maps language code (e.g., "de", "German") to localized title
    titles_by_language: Dict[str, str] = field(default_factory=dict)


@dataclass
class SearchResult:
    """Result from a metadata search with pagination info."""
    books: List[BookMetadata]
    page: int = 1
    total_found: int = 0  # Total matching results (if known)
    has_more: bool = False  # True if more results available


class MetadataProvider(ABC):
    """Interface for metadata providers.

    All metadata providers must implement this interface. The search method
    accepts MetadataSearchOptions for unified search across providers.

    Attributes:
        name: Internal identifier (e.g., "hardcover")
        display_name: Human-readable name (e.g., "Hardcover")
        requires_auth: True if API key/authentication is required
        supported_sorts: List of SortOrder values this provider supports
        search_fields: List of provider-specific search fields
    """
    name: str
    display_name: str
    requires_auth: bool
    supported_sorts: List[SortOrder] = [SortOrder.RELEVANCE]
    search_fields: List[SearchField] = []

    @abstractmethod
    def search(self, options: MetadataSearchOptions) -> List[BookMetadata]:
        """Search for books using the provided options."""
        pass

    @abstractmethod
    def get_book(self, book_id: str) -> Optional[BookMetadata]:
        """Get a specific book by provider ID."""
        pass

    @abstractmethod
    def search_by_isbn(self, isbn: str) -> Optional[BookMetadata]:
        """Search for a book by ISBN."""
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """Check if this provider is configured and available."""
        pass

    def search_paginated(self, options: MetadataSearchOptions) -> SearchResult:
        """Search with pagination info. Override for accurate pagination."""
        books = self.search(options)
        # Heuristic: if we got exactly limit results, there might be more
        has_more = len(books) >= options.limit
        return SearchResult(
            books=books,
            page=options.page,
            total_found=0,  # Unknown without provider-specific implementation
            has_more=has_more
        )


# Provider registry
_PROVIDERS: Dict[str, Type[MetadataProvider]] = {}
_PROVIDER_KWARGS_FACTORIES: Dict[str, Any] = {}  # Callable[[], Dict]


def register_provider(name: str):
    """Decorator to register a metadata provider."""
    def decorator(cls):
        _PROVIDERS[name] = cls
        return cls
    return decorator


def register_provider_kwargs(name: str):
    """Decorator to register a provider's kwargs factory.

    The decorated function should return a Dict of kwargs to pass to the
    provider constructor. This allows each provider to define its own
    configuration requirements without polluting the core module.

    Example:
        @register_provider_kwargs("hardcover")
        def _hardcover_kwargs() -> Dict:
            from cwa_book_downloader.core.config import config
            return {"api_key": config.get("HARDCOVER_API_KEY", "")}
    """
    def decorator(fn):
        _PROVIDER_KWARGS_FACTORIES[name] = fn
        return fn
    return decorator


def get_provider(name: str, **kwargs) -> MetadataProvider:
    """Factory - instantiate any registered provider."""
    if name not in _PROVIDERS:
        raise ValueError(f"Unknown metadata provider: {name}")
    return _PROVIDERS[name](**kwargs)


def list_providers() -> List[dict]:
    """For settings UI - list available providers with their requirements."""
    return [
        {"name": n, "display_name": c.display_name, "requires_auth": c.requires_auth}
        for n, c in _PROVIDERS.items()
    ]


def get_provider_kwargs(provider_name: str) -> Dict:
    """Get provider-specific initialization kwargs from registered factory."""
    factory = _PROVIDER_KWARGS_FACTORIES.get(provider_name)
    if factory:
        return factory()
    return {}


def is_provider_registered(provider_name: str) -> bool:
    """Check if a provider is registered."""
    return provider_name in _PROVIDERS


def is_provider_enabled(provider_name: str) -> bool:
    """Check if a provider is enabled in settings."""
    from cwa_book_downloader.core.config import config as app_config

    # Refresh config to get latest settings
    app_config.refresh()

    # Check the provider-specific enabled flag
    enabled_key = f"{provider_name.upper()}_ENABLED"
    return app_config.get(enabled_key, False) is True


def get_enabled_providers() -> List[str]:
    """Get list of all enabled provider names."""
    return [name for name in _PROVIDERS if is_provider_enabled(name)]


def get_configured_provider(content_type: str = "ebook") -> Optional[MetadataProvider]:
    """Get the currently configured metadata provider for the content type."""
    from cwa_book_downloader.core.config import config as app_config

    # Refresh config to ensure we have the latest saved settings
    app_config.refresh()

    # For audiobooks, try audiobook-specific provider first, then fall back to main provider
    if content_type == "audiobook":
        metadata_provider = app_config.get("METADATA_PROVIDER_AUDIOBOOK", "")
        if not metadata_provider:
            metadata_provider = app_config.get("METADATA_PROVIDER", "")
    else:
        metadata_provider = app_config.get("METADATA_PROVIDER", "")

    if not metadata_provider:
        return None

    if metadata_provider not in _PROVIDERS:
        return None

    # Check if the provider is enabled
    if not is_provider_enabled(metadata_provider):
        return None

    kwargs = get_provider_kwargs(metadata_provider)
    return get_provider(metadata_provider, **kwargs)


def _get_configured_provider_name() -> str:
    """Get the currently configured metadata provider name from config."""
    from cwa_book_downloader.core.config import config as app_config
    app_config.refresh()
    return app_config.get("METADATA_PROVIDER", "")


def get_provider_sort_options(provider_name: Optional[str] = None) -> List[Dict[str, str]]:
    """Get sort options for a metadata provider as {value, label} dicts."""
    if provider_name is None:
        provider_name = _get_configured_provider_name()

    if provider_name and provider_name in _PROVIDERS:
        provider_class = _PROVIDERS[provider_name]
        supported = getattr(provider_class, 'supported_sorts', [SortOrder.RELEVANCE])
    else:
        supported = [SortOrder.RELEVANCE]

    return [
        {"value": sort.value, "label": SORT_LABELS.get(sort, sort.value.title())}
        for sort in supported
    ]


def get_provider_search_fields(provider_name: Optional[str] = None) -> List[Dict[str, Any]]:
    """Get search fields for a metadata provider as serialized dicts."""
    if provider_name is None:
        provider_name = _get_configured_provider_name()

    if provider_name and provider_name in _PROVIDERS:
        provider_class = _PROVIDERS[provider_name]
        fields = getattr(provider_class, 'search_fields', [])
    else:
        fields = []

    return [serialize_search_field(f) for f in fields]


def get_provider_default_sort(provider_name: Optional[str] = None) -> str:
    """Get the default sort order for a metadata provider."""
    from cwa_book_downloader.core.config import config as app_config

    if provider_name is None:
        provider_name = _get_configured_provider_name()

    if not provider_name:
        return "relevance"

    # Look up provider-specific default sort setting
    setting_key = f"{provider_name.upper()}_DEFAULT_SORT"
    return app_config.get(setting_key, "relevance")


def sync_metadata_provider_selection() -> None:
    """Sync the METADATA_PROVIDER setting based on enabled providers.

    If the currently selected provider is not enabled (or nothing is selected),
    auto-select the first enabled provider. This should be called after
    enabling/disabling a provider.
    """
    from cwa_book_downloader.core.config import config as app_config
    from cwa_book_downloader.core.settings_registry import save_config_file, load_config_file

    app_config.refresh()

    current_provider = app_config.get("METADATA_PROVIDER", "")
    enabled = get_enabled_providers()

    # If current provider is valid and enabled, nothing to do
    if current_provider and current_provider in enabled:
        return

    # Auto-select first enabled provider (or clear if none)
    new_provider = enabled[0] if enabled else ""

    if new_provider != current_provider:
        # Update the general settings config
        general_config = load_config_file("general")
        general_config["METADATA_PROVIDER"] = new_provider
        save_config_file("general", general_config)
        app_config.refresh()


# Import provider implementations to trigger registration
# These must be imported AFTER the base classes and registry are defined
try:
    from cwa_book_downloader.metadata_providers import hardcover  # noqa: F401, E402
except ImportError:
    pass  # Hardcover provider is optional

try:
    from cwa_book_downloader.metadata_providers import openlibrary  # noqa: F401, E402
except ImportError:
    pass  # Open Library provider is optional

try:
    from cwa_book_downloader.metadata_providers import googlebooks  # noqa: F401, E402
except ImportError:
    pass  # Google Books provider is optional
