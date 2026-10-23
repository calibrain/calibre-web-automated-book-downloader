"""Metadata provider plugin system - base classes and registry."""

from abc import ABC, abstractmethod
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, ClassVar, TypeVar

from shelfmark.core.request_helpers import normalize_optional_text


class SearchType(StrEnum):
    """Type of search to perform."""

    GENERAL = "general"  # Search all fields (title, author, ISBN, etc.)
    TITLE = "title"  # Search by title only
    AUTHOR = "author"  # Search by author only
    ISBN = "isbn"  # Search by ISBN


class SortOrder(StrEnum):
    """Sort order for search results."""

    RELEVANCE = "relevance"  # Best match first (default)
    POPULARITY = "popularity"  # Most popular first
    RATING = "rating"  # Highest rated first
    NEWEST = "newest"  # Most recently published first
    OLDEST = "oldest"  # Oldest published first
    SERIES_ORDER = "series_order"  # By series position (requires series field)


# Display labels for sort options
SORT_LABELS: dict[SortOrder, str] = {
    SortOrder.RELEVANCE: "Most relevant",
    SortOrder.POPULARITY: "Most popular",
    SortOrder.RATING: "Highest rated",
    SortOrder.NEWEST: "Newest",
    SortOrder.OLDEST: "Oldest",
    SortOrder.SERIES_ORDER: "Series order",
}


@dataclass
class MetadataCapability:
    """Declarative provider capability consumed by shared UI code."""

    key: str
    field_key: str | None = None
    sort: SortOrder | None = None


@dataclass
class TextSearchField:
    """Text input search field."""

    key: str  # Field identifier (e.g., "author", "publisher")
    label: str  # Display label in UI
    placeholder: str = ""  # Placeholder text
    description: str = ""  # Help text
    suggestions_endpoint: str | None = None  # Remote suggestions endpoint for typeahead
    suggestions_min_query_length: int = 2  # Minimum chars before requesting suggestions


@dataclass
class NumberSearchField:
    """Numeric input search field."""

    key: str
    label: str
    placeholder: str = ""
    description: str = ""
    min_value: int | None = None
    max_value: int | None = None
    step: int = 1


@dataclass
class SelectSearchField:
    """Single-choice dropdown search field."""

    key: str
    label: str
    options: list[dict[str, str]] = field(default_factory=list)  # [{value: "", label: ""}]
    placeholder: str = ""
    description: str = ""


@dataclass
class CheckboxSearchField:
    """Boolean checkbox search field."""

    key: str
    label: str
    description: str = ""
    default: bool = False


@dataclass
class DynamicSelectSearchField:
    """Single-choice dropdown field with options loaded from an API endpoint."""

    key: str
    label: str
    options_endpoint: str
    placeholder: str = ""
    description: str = ""


# Type alias for all search field types
SearchField = (
    TextSearchField
    | NumberSearchField
    | SelectSearchField
    | CheckboxSearchField
    | DynamicSelectSearchField
)


def serialize_metadata_capability(capability: MetadataCapability) -> dict[str, Any]:
    """Serialize a provider capability for API responses."""
    result: dict[str, Any] = {
        "key": capability.key,
    }

    if capability.field_key:
        result["field_key"] = capability.field_key

    if capability.sort:
        result["sort"] = capability.sort.value

    return result


def serialize_search_field(search_field: SearchField) -> dict[str, Any]:
    """Serialize a search field to dict for API response."""
    result: dict[str, Any] = {
        "key": search_field.key,
        "label": search_field.label,
        "type": search_field.__class__.__name__,
        "placeholder": getattr(search_field, "placeholder", ""),
        "description": getattr(search_field, "description", ""),
    }

    # Add type-specific properties
    if isinstance(search_field, NumberSearchField):
        result["min"] = search_field.min_value
        result["max"] = search_field.max_value
        result["step"] = search_field.step
    elif isinstance(search_field, TextSearchField):
        if search_field.suggestions_endpoint:
            result["suggestions_endpoint"] = search_field.suggestions_endpoint
            result["suggestions_min_query_length"] = search_field.suggestions_min_query_length
    elif isinstance(search_field, SelectSearchField):
        result["options"] = search_field.options
    elif isinstance(search_field, CheckboxSearchField):
        result["default"] = search_field.default
    elif isinstance(search_field, DynamicSelectSearchField):
        result["options_endpoint"] = search_field.options_endpoint

    return result


@dataclass
class MetadataSearchOptions:
    """Options for metadata search queries across all providers."""

    query: str
    search_type: SearchType = SearchType.GENERAL
    language: str | None = None  # ISO 639-1 code (e.g., "en", "fr")
    sort: SortOrder = SortOrder.RELEVANCE
    limit: int = 40
    page: int = 1
    fields: dict[str, Any] = field(default_factory=dict)  # Custom search field values
    # Searching user, for per-user settings; kept out of repr and thus out of cache keys
    user_id: int | None = field(default=None, repr=False)


@dataclass
class DisplayField:
    """A display field for metadata cards (ratings, page counts, etc.)."""

    label: str  # e.g., "Rating", "Pages", "Readers"
    value: str  # e.g., "4.5", "496", "8,041"
    icon: str | None = None  # Icon name: "star", "book", "users", "editions"


@dataclass
class BookMetadata:
    """Book from metadata provider (not a specific release)."""

    provider: str  # Which provider this came from (internal name)
    provider_id: str  # ID in that provider's system
    title: str

    # Provider display name for UI (e.g., "Open Library" instead of "openlibrary")
    provider_display_name: str | None = None

    # Optional - not all providers have all fields
    authors: list[str] = field(default_factory=list)
    isbn_10: str | None = None
    isbn_13: str | None = None
    cover_url: str | None = None
    description: str | None = None
    publisher: str | None = None
    publish_year: int | None = None
    language: str | None = None
    genres: list[str] = field(default_factory=list)
    source_url: str | None = None  # Link to book on provider's site
    subtitle: str | None = None  # Book subtitle, if any
    search_title: str | None = None  # Cleaner title for search queries (provider-specific)
    search_author: str | None = None  # Cleaner author for search queries (provider-specific)

    # Cover aspect ratio hint for the frontend ("portrait" or "square")
    cover_aspect: str | None = None

    # Provider-specific display fields for cards/lists
    display_fields: list[DisplayField] = field(default_factory=list)

    # Series info (if book is part of a series)
    series_id: str | None = None  # Provider-specific series ID
    series_name: str | None = None  # Name of the series
    series_position: float | None = None  # This book's position (e.g., 3, 1.5 for novellas)
    series_count: int | None = None  # Total books in the series

    # Alternative titles by language (for localized searches)
    # Maps language code (e.g., "de", "German") to localized title
    titles_by_language: dict[str, str] = field(default_factory=dict)


def group_languages_by_localized_title(
    base_title: str,
    languages: list[str] | None,
    titles_by_language: dict[str, str] | None = None,
) -> list[tuple[str, list[str] | None]]:
    """Group language codes by localized title.

    Release sources that support language filtering (e.g., Anna's Archive)
    may want to run separate searches per localized title, while still
    passing the correct language filters per query.

    Args:
        base_title: Fallback title when no localized title exists.
        languages: Requested language codes (e.g., ["en", "hu"]).
        titles_by_language: Mapping of language identifiers to localized titles.

    Returns:
        List of (title, languages) tuples. If languages is None/empty, returns
        [(base_title, None)].

    """
    if not base_title:
        return []

    if not languages:
        return [(base_title, None)]

    normalized_langs = [lang.strip() for lang in languages if lang and lang.strip()]
    if not normalized_langs:
        return [(base_title, None)]

    if not titles_by_language:
        return [(base_title, normalized_langs)]

    title_to_langs: dict[str, list[str]] = {}
    for lang in normalized_langs:
        localized_title = titles_by_language.get(lang) or base_title
        title_to_langs.setdefault(localized_title, []).append(lang)

    return list(title_to_langs.items())


def build_localized_search_titles(
    base_title: str,
    languages: list[str] | None,
    titles_by_language: dict[str, str] | None = None,
    excluded_languages: set[str] | None = None,
) -> list[str]:
    """Build a list of titles to search for, including localized editions.

    This is useful for release sources that *can't* pass language filters to
    an upstream search API (e.g., Prowlarr), but still want to broaden matches
    by searching for localized edition titles.

    The list always includes base_title first.

    Args:
        base_title: Primary title to search for.
        languages: User language preferences (order matters).
        titles_by_language: Mapping of language identifiers to localized titles.
        excluded_languages: Optional set of normalized language identifiers to skip.

    Returns:
        List of unique titles to search for, in priority order.

    """
    if not base_title:
        return []

    titles: list[str] = [base_title]
    seen = {base_title}

    if not languages or not titles_by_language:
        return titles

    excluded = {lang.lower() for lang in (excluded_languages or set())}

    for lang in languages:
        if not lang:
            continue
        normalized_lang = lang.strip()
        if not normalized_lang:
            continue
        if normalized_lang.lower() in excluded:
            continue

        localized_title = titles_by_language.get(normalized_lang)
        if not localized_title:
            continue

        if localized_title not in seen:
            seen.add(localized_title)
            titles.append(localized_title)

    return titles


@dataclass
class SearchResult:
    """Result from a metadata search with pagination info."""

    books: list[BookMetadata]
    page: int = 1
    total_found: int = 0  # Total matching results (if known)
    has_more: bool = False  # True if more results available
    source_url: str | None = None  # External URL for the result set (e.g. Hardcover list page)
    source_title: str | None = None  # Display title for the result set (e.g. list name)


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
        account_search_fields: Search fields that read the provider's own account, admin only
        capabilities: Declarative capabilities exposed to shared UI code

    """

    name: str
    display_name: str
    requires_auth: bool
    supported_sorts: ClassVar[tuple[SortOrder, ...]] = (SortOrder.RELEVANCE,)
    search_fields: ClassVar[tuple[SearchField, ...]] = ()
    # Search fields that read the provider's own account (private lists, shelves) rather than public
    # data. Only an admin may use them; see ``_session_may_use_provider_account``.
    account_search_fields: ClassVar[frozenset[str]] = frozenset()
    capabilities: ClassVar[tuple[MetadataCapability, ...]] = ()

    @abstractmethod
    def search(self, options: MetadataSearchOptions) -> list[BookMetadata]:
        """Search for books using the provided options."""

    @abstractmethod
    def get_book(self, book_id: str) -> BookMetadata | None:
        """Get a specific book by provider ID."""

    @abstractmethod
    def search_by_isbn(self, isbn: str) -> BookMetadata | None:
        """Search for a book by ISBN."""

    @abstractmethod
    def is_available(self) -> bool:
        """Check if this provider is configured and available."""

    def search_paginated(self, options: MetadataSearchOptions) -> SearchResult:
        """Search with pagination info. Override for accurate pagination."""
        books = self.search(options)
        # Heuristic: if we got exactly limit results, there might be more
        has_more = len(books) >= options.limit
        return SearchResult(
            books=books,
            page=options.page,
            total_found=0,  # Unknown without provider-specific implementation
            has_more=has_more,
        )

    def get_search_field_options(
        self,
        field_key: str,
        query: str | None = None,
    ) -> list[dict[str, str]]:
        """Get dynamic options for a provider-specific search field."""
        return []

    def get_book_targets(self, book_id: str) -> list[dict[str, Any]]:
        """Get provider-managed list or status targets for a specific book."""
        msg = f"{self.display_name} does not support book targets"
        raise NotImplementedError(msg)

    def get_book_targets_batch(self, book_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
        """Get provider-managed targets for multiple books.

        Returns a dict mapping each book_id to its list of target options.
        Default implementation calls get_book_targets per book.
        """
        return {book_id: self._get_book_targets_for_batch(book_id) for book_id in book_ids}

    def _get_book_targets_for_batch(self, book_id: str) -> list[dict[str, Any]]:
        """Safely fetch targets for one book, falling back to an empty list."""
        try:
            return self.get_book_targets(book_id)
        except NotImplementedError, ValueError:
            return []

    def set_book_target_state(
        self,
        book_id: str,
        target: str,
        *,
        selected: bool,
    ) -> dict[str, Any]:
        """Set whether a book belongs to a provider-managed list or shelf.

        Returns a dict with at least ``{"changed": bool}``.
        """
        msg = f"{self.display_name} does not support book targets"
        raise NotImplementedError(msg)


# Provider registry
_PROVIDERS: dict[str, type[MetadataProvider]] = {}
_PROVIDER_KWARGS_FACTORIES: dict[str, Callable[[], dict[str, Any]]] = {}
ProviderType = TypeVar("ProviderType", bound=MetadataProvider)
ProviderKwargsFactory = TypeVar(
    "ProviderKwargsFactory",
    bound=Callable[[], dict[str, Any]],
)


def register_provider(
    name: str,
) -> Callable[[type[ProviderType]], type[ProviderType]]:
    """Register a metadata provider."""

    def decorator(cls: type[ProviderType]) -> type[ProviderType]:
        _PROVIDERS[name] = cls
        return cls

    return decorator


def register_provider_kwargs(
    name: str,
) -> Callable[[ProviderKwargsFactory], ProviderKwargsFactory]:
    """Register a provider kwargs factory.

    The decorated function should return a Dict of kwargs to pass to the
    provider constructor. This allows each provider to define its own
    configuration requirements without polluting the core module.

    Example:
        @register_provider_kwargs("hardcover")
        def _hardcover_kwargs() -> Dict:
            from shelfmark.core.config import config
            return {"api_key": config.get("HARDCOVER_API_KEY", "")}

    """

    def decorator(fn: ProviderKwargsFactory) -> ProviderKwargsFactory:
        _PROVIDER_KWARGS_FACTORIES[name] = fn
        return fn

    return decorator


def get_provider(name: str, **kwargs: object) -> MetadataProvider:
    """Instantiate a registered metadata provider."""
    if name not in _PROVIDERS:
        msg = f"Unknown metadata provider: {name}"
        raise ValueError(msg)
    return _PROVIDERS[name](**kwargs)


def list_providers() -> list[dict]:
    """For settings UI - list available providers with their requirements."""
    return [
        {"name": n, "display_name": c.display_name, "requires_auth": c.requires_auth}
        for n, c in _PROVIDERS.items()
    ]


def get_provider_kwargs(provider_name: str) -> dict:
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
    from shelfmark.core.config import config as app_config

    # Refresh config to get latest settings
    app_config.refresh()

    # Check the provider-specific enabled flag
    enabled_key = f"{provider_name.upper()}_ENABLED"
    return app_config.get(enabled_key, False) is True


def get_enabled_providers() -> list[str]:
    """Get list of all enabled provider names."""
    return [name for name in _PROVIDERS if is_provider_enabled(name)]


def get_configured_provider(
    content_type: str = "ebook",
    user_id: int | None = None,
) -> MetadataProvider | None:
    """Get the currently configured metadata provider for the content type."""
    from shelfmark.core.config import config as app_config

    # Refresh config to ensure we have the latest saved settings
    app_config.refresh()

    # For audiobooks, try audiobook-specific provider first, then fall back to main provider
    if content_type == "audiobook":
        metadata_provider = normalize_optional_text(
            app_config.get("METADATA_PROVIDER_AUDIOBOOK", "", user_id=user_id)
        )
        if not metadata_provider:
            metadata_provider = normalize_optional_text(
                app_config.get("METADATA_PROVIDER", "", user_id=user_id)
            )
    else:
        metadata_provider = normalize_optional_text(
            app_config.get("METADATA_PROVIDER", "", user_id=user_id)
        )

    if not metadata_provider:
        return None

    if metadata_provider not in _PROVIDERS:
        return None

    # Check if the provider is enabled
    if not is_provider_enabled(metadata_provider):
        return None

    kwargs = get_provider_kwargs(metadata_provider)
    return get_provider(metadata_provider, **kwargs)


def get_configured_provider_name(
    content_type: str = "ebook",
    user_id: int | None = None,
    *,
    fallback_to_main: bool = True,
) -> str:
    """Get the configured metadata provider name for a content type."""
    from shelfmark.core.config import config as app_config

    app_config.refresh()

    if content_type == "combined":
        combined_provider = normalize_optional_text(
            app_config.get(
                "METADATA_PROVIDER_COMBINED",
                "",
                user_id=user_id,
            )
        )
        if combined_provider or not fallback_to_main:
            return combined_provider or ""

    if content_type == "audiobook":
        audiobook_provider = normalize_optional_text(
            app_config.get(
                "METADATA_PROVIDER_AUDIOBOOK",
                "",
                user_id=user_id,
            )
        )
        if audiobook_provider or not fallback_to_main:
            return audiobook_provider or ""

    return normalize_optional_text(app_config.get("METADATA_PROVIDER", "", user_id=user_id)) or ""


def get_provider_sort_options(
    provider_name: str | None = None,
    user_id: int | None = None,
) -> list[dict[str, str]]:
    """Get sort options for a metadata provider as {value, label} dicts."""
    if provider_name is None:
        provider_name = get_configured_provider_name(user_id=user_id)

    if provider_name and provider_name in _PROVIDERS:
        provider_class = _PROVIDERS[provider_name]
        supported = getattr(provider_class, "supported_sorts", [SortOrder.RELEVANCE])
    else:
        supported = [SortOrder.RELEVANCE]

    return [
        {"value": sort.value, "label": SORT_LABELS.get(sort, sort.value.title())}
        for sort in supported
    ]


def get_provider_search_fields(
    provider_name: str | None = None,
    user_id: int | None = None,
) -> list[dict[str, Any]]:
    """Get search fields for a metadata provider as serialized dicts."""
    if provider_name is None:
        provider_name = get_configured_provider_name(user_id=user_id)

    if provider_name and provider_name in _PROVIDERS:
        provider_class = _PROVIDERS[provider_name]
        fields = getattr(provider_class, "search_fields", [])
    else:
        fields = []

    return [serialize_search_field(f) for f in fields]


def get_provider_capabilities(
    provider_name: str | None = None,
    user_id: int | None = None,
) -> list[dict[str, Any]]:
    """Get declarative capabilities for a metadata provider."""
    if provider_name is None:
        provider_name = get_configured_provider_name(user_id=user_id)

    if provider_name and provider_name in _PROVIDERS:
        provider_class = _PROVIDERS[provider_name]
        capabilities = getattr(provider_class, "capabilities", [])
    else:
        capabilities = []

    return [serialize_metadata_capability(capability) for capability in capabilities]


def get_provider_default_sort(
    provider_name: str | None = None,
    user_id: int | None = None,
) -> str:
    """Get the default sort order for a metadata provider."""
    from shelfmark.core.config import config as app_config

    if provider_name is None:
        provider_name = get_configured_provider_name(user_id=user_id)

    if not provider_name:
        return "relevance"

    # Look up provider-specific default sort setting
    setting_key = f"{provider_name.upper()}_DEFAULT_SORT"
    return normalize_optional_text(app_config.get(setting_key, "relevance", user_id=user_id)) or (
        "relevance"
    )


def sync_metadata_provider_selection() -> None:
    """Sync the METADATA_PROVIDER setting based on enabled providers.

    If the currently selected provider is not enabled (or nothing is selected),
    auto-select the first enabled provider. This should be called after
    enabling/disabling a provider.
    """
    from shelfmark.core.config import config as app_config
    from shelfmark.core.settings_registry import load_config_file, save_config_file

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
        app_config.refresh(force=True)


# Import provider implementations to trigger registration
# These must be imported AFTER the base classes and registry are defined
with suppress(ImportError):
    from shelfmark.metadata_providers import hardcover as hardcover

with suppress(ImportError):
    from shelfmark.metadata_providers import openlibrary as openlibrary

with suppress(ImportError):
    from shelfmark.metadata_providers import googlebooks as googlebooks

with suppress(ImportError):
    from shelfmark.metadata_providers import moly as moly

with suppress(ImportError):
    from shelfmark.metadata_providers import databazeknih as databazeknih

with suppress(ImportError):
    from shelfmark.metadata_providers import dnb as dnb
