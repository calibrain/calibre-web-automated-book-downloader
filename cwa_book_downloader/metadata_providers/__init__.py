"""Metadata provider plugin system - base classes and registry."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import List, Optional, Dict, Type, Literal, Any, Union


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


# Display labels for sort options
SORT_LABELS: Dict[SortOrder, str] = {
    SortOrder.RELEVANCE: "Most relevant",
    SortOrder.POPULARITY: "Most popular",
    SortOrder.RATING: "Highest rated",
    SortOrder.NEWEST: "Newest",
    SortOrder.OLDEST: "Oldest",
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


def _get_field_type_name(search_field: SearchField) -> str:
    """Get the type name for a search field."""
    return search_field.__class__.__name__


def serialize_search_field(search_field: SearchField) -> Dict[str, Any]:
    """Serialize a search field for API response.

    Args:
        search_field: The search field definition.

    Returns:
        Dict representation for frontend.
    """
    result: Dict[str, Any] = {
        "key": search_field.key,
        "label": search_field.label,
        "type": _get_field_type_name(search_field),
        "placeholder": search_field.placeholder if hasattr(search_field, 'placeholder') else "",
        "description": search_field.description if hasattr(search_field, 'description') else "",
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
    """Options for metadata search queries.

    Provides an abstracted interface that works across all metadata providers.
    Providers map these options to their specific API parameters.
    """
    query: str
    search_type: SearchType = SearchType.GENERAL
    language: Optional[str] = None  # ISO 639-1 code (e.g., "en", "fr")
    sort: SortOrder = SortOrder.RELEVANCE
    limit: int = 20
    page: int = 1
    fields: Dict[str, Any] = field(default_factory=dict)  # Custom search field values


@dataclass
class DisplayField:
    """A display field for metadata cards.

    Providers can populate these to show provider-specific metadata
    like ratings, page counts, reader counts, etc.
    """
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

    # Provider-specific display fields for cards/lists
    display_fields: List[DisplayField] = field(default_factory=list)


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
        """Search for books using the provided options.

        Args:
            options: Search options including query, type, language, sort, pagination.

        Returns:
            List of BookMetadata matching the search criteria.

        Note:
            - If search_type is ISBN, this delegates to search_by_isbn()
            - Unsupported sort orders fall back to RELEVANCE
            - Language filtering is best-effort (not all providers support it)
        """
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
    """Get provider-specific initialization kwargs based on configuration.

    Looks up the provider's registered kwargs factory and calls it to get
    the configuration. Each provider registers its own factory via
    @register_provider_kwargs decorator.

    Args:
        provider_name: Name of the provider.

    Returns:
        Dict of kwargs to pass to provider constructor.
    """
    factory = _PROVIDER_KWARGS_FACTORIES.get(provider_name)
    if factory:
        return factory()
    return {}


def is_provider_registered(provider_name: str) -> bool:
    """Check if a provider is registered.

    Args:
        provider_name: Name of the provider.

    Returns:
        True if provider is registered, False otherwise.
    """
    return provider_name in _PROVIDERS


def is_provider_enabled(provider_name: str) -> bool:
    """Check if a provider is enabled in settings.

    Each provider has an enabled flag (e.g., HARDCOVER_ENABLED, OPENLIBRARY_ENABLED)
    that must be explicitly set to True for the provider to be used.

    Args:
        provider_name: Name of the provider.

    Returns:
        True if provider is enabled, False otherwise.
    """
    from cwa_book_downloader.core.config import config as app_config

    # Refresh config to get latest settings
    app_config.refresh()

    # Check the provider-specific enabled flag
    enabled_key = f"{provider_name.upper()}_ENABLED"
    return app_config.get(enabled_key, False) is True


def get_enabled_providers() -> List[str]:
    """Get list of all enabled provider names.

    Returns:
        List of enabled provider names.
    """
    enabled = []
    for name in _PROVIDERS:
        if is_provider_enabled(name):
            enabled.append(name)
    return enabled


def get_configured_provider() -> Optional[MetadataProvider]:
    """Get the currently configured metadata provider, if any.

    Uses the METADATA_PROVIDER config setting to determine which provider
    to instantiate. Returns None if no provider is configured or not enabled.

    Returns:
        MetadataProvider instance or None.
    """
    from cwa_book_downloader.core.config import config as app_config

    # Refresh config to ensure we have the latest saved settings
    app_config.refresh()

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


def get_provider_sort_options(provider_name: Optional[str] = None) -> List[Dict[str, str]]:
    """Get sort options for a metadata provider.

    Returns a list of {value, label} dicts suitable for frontend dropdowns.

    Args:
        provider_name: Provider name. If None, uses configured provider.

    Returns:
        List of sort option dicts, or default [relevance] if provider not found.
    """
    if provider_name is None:
        from cwa_book_downloader.core.config import config as app_config
        app_config.refresh()
        provider_name = app_config.get("METADATA_PROVIDER", "")

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
    """Get search fields for a metadata provider.

    Returns a list of serialized search field dicts suitable for frontend rendering.

    Args:
        provider_name: Provider name. If None, uses configured provider.

    Returns:
        List of search field dicts, or empty list if provider not found.
    """
    if provider_name is None:
        from cwa_book_downloader.core.config import config as app_config
        app_config.refresh()
        provider_name = app_config.get("METADATA_PROVIDER", "")

    if provider_name and provider_name in _PROVIDERS:
        provider_class = _PROVIDERS[provider_name]
        fields = getattr(provider_class, 'search_fields', [])
    else:
        fields = []

    return [serialize_search_field(f) for f in fields]


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
