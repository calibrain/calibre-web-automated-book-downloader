# Metadata Providers

This module provides a plugin architecture for fetching book metadata from various sources with a unified interface.

## Overview

Metadata providers allow searching for books and retrieving detailed metadata (title, authors, cover images, descriptions, etc.) from external services. The system uses a decorator-based registration pattern, making it easy to add new providers.

## Available Providers

| Provider | Auth Required | Description |
|----------|---------------|-------------|
| **Hardcover** | Yes (API key) | Modern book tracking platform with GraphQL API. Get your key at [hardcover.app/account/api](https://hardcover.app/account/api) |
| **Open Library** | No | Free, open-source library catalog from the Internet Archive. Rate limited to ~100 requests/minute |

## Core Components

### BookMetadata

Dataclass representing a book from a metadata provider:

```python
@dataclass
class BookMetadata:
    provider: str                    # Internal provider name (e.g., "hardcover")
    provider_id: str                 # ID in that provider's system
    title: str

    # Optional fields
    provider_display_name: str       # Human-readable name (e.g., "Hardcover")
    authors: List[str]
    isbn_10: str
    isbn_13: str
    cover_url: str
    description: str
    publisher: str
    publish_year: int
    language: str
    genres: List[str]
    source_url: str                  # Link to book on provider's site
    display_fields: List[DisplayField]  # Provider-specific display data
```

### DisplayField

Provider-specific metadata for UI cards (ratings, page counts, reader counts, etc.):

```python
@dataclass
class DisplayField:
    label: str       # e.g., "Rating", "Pages", "Readers"
    value: str       # e.g., "4.5", "496", "8,041"
    icon: str        # Icon name: "star", "book", "users", "editions"
```

### MetadataSearchOptions

Unified search options that work across all providers:

```python
@dataclass
class MetadataSearchOptions:
    query: str
    search_type: SearchType = SearchType.GENERAL  # GENERAL, TITLE, AUTHOR, ISBN
    language: str = None                          # ISO 639-1 code (e.g., "en")
    sort: SortOrder = SortOrder.RELEVANCE
    limit: int = 40
    page: int = 1
```

### SortOrder

Available sort options (provider support varies):

| Sort Order | Description | Hardcover | Open Library |
|------------|-------------|-----------|--------------|
| `RELEVANCE` | Best match first (default) | ✓ | ✓ |
| `POPULARITY` | Most popular first | ✓ | ✗ |
| `RATING` | Highest rated first | ✓ | ✗ |
| `NEWEST` | Most recently published | ✓ | ✓ |
| `OLDEST` | Oldest published first | ✓ | ✓ |

### MetadataProvider (Abstract Base Class)

All providers must implement this interface:

```python
class MetadataProvider(ABC):
    name: str                        # Internal identifier
    display_name: str                # Human-readable name
    requires_auth: bool              # True if API key required
    supported_sorts: List[SortOrder] # Supported sort options

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
```

## Registry Functions

### Provider Registration

```python
from cwa_book_downloader.metadata_providers import register_provider

@register_provider("my_provider")
class MyProvider(MetadataProvider):
    ...
```

### Getting Providers

```python
from cwa_book_downloader.metadata_providers import (
    get_provider,
    get_configured_provider,
    get_provider_kwargs,
    list_providers,
    is_provider_registered,
)

# Get specific provider with kwargs
provider = get_provider("hardcover", api_key="...")

# Get currently configured provider (from settings)
provider = get_configured_provider()

# Get provider-specific kwargs from config
kwargs = get_provider_kwargs("hardcover")  # {"api_key": "..."}

# List all registered providers
providers = list_providers()
# [{"name": "hardcover", "display_name": "Hardcover", "requires_auth": True}, ...]

# Check if provider exists
exists = is_provider_registered("hardcover")  # True
```

### Sort Options

```python
from cwa_book_downloader.metadata_providers import get_provider_sort_options

# Get sort options for a specific provider
options = get_provider_sort_options("hardcover")
# [{"value": "relevance", "label": "Most relevant"}, ...]

# Get sort options for configured provider
options = get_provider_sort_options()  # Uses METADATA_PROVIDER from config
```

## Creating a New Provider

1. Create a new file in `cwa_book_downloader/metadata_providers/` (e.g., `my_provider.py`)

2. Implement the provider:

```python
from cwa_book_downloader.metadata_providers import (
    BookMetadata,
    DisplayField,
    MetadataProvider,
    MetadataSearchOptions,
    SearchType,
    SortOrder,
    register_provider,
)
from cwa_book_downloader.core.settings_registry import (
    register_settings,
    HeadingField,
    PasswordField,
    ActionButton,
)
from cwa_book_downloader.core.config import config


@register_provider("my_provider")
class MyProvider(MetadataProvider):
    name = "my_provider"
    display_name = "My Provider"
    requires_auth = True
    supported_sorts = [SortOrder.RELEVANCE, SortOrder.NEWEST]

    def __init__(self, api_key: str = None):
        self.api_key = api_key or config.get("MY_PROVIDER_API_KEY", "")

    def is_available(self) -> bool:
        return bool(self.api_key)

    def search(self, options: MetadataSearchOptions) -> List[BookMetadata]:
        # Handle ISBN search separately
        if options.search_type == SearchType.ISBN:
            result = self.search_by_isbn(options.query)
            return [result] if result else []

        # Implement search logic...
        return []

    def get_book(self, book_id: str) -> Optional[BookMetadata]:
        # Implement get book logic...
        return None

    def search_by_isbn(self, isbn: str) -> Optional[BookMetadata]:
        # Implement ISBN search logic...
        return None


# Settings for the UI
@register_settings("my_provider", "My Provider", icon="book", order=53, group="metadata_providers")
def my_provider_settings():
    return [
        HeadingField(
            key="my_provider_heading",
            title="My Provider",
            description="Description of your provider",
            link_url="https://myprovider.com",
            link_text="myprovider.com",
        ),
        PasswordField(
            key="MY_PROVIDER_API_KEY",
            label="API Key",
            description="Your API key",
            required=True,
        ),
        ActionButton(
            key="test_connection",
            label="Test Connection",
            style="primary",
            callback=_test_connection,
        ),
    ]
```

3. Import your provider in `__init__.py`:

```python
try:
    from cwa_book_downloader.metadata_providers import my_provider  # noqa: F401
except ImportError:
    pass  # Provider is optional
```

4. Add provider kwargs to `get_provider_kwargs()` in `__init__.py`:

```python
def get_provider_kwargs(provider_name: str) -> Dict:
    kwargs: Dict = {}
    if provider_name == "hardcover":
        kwargs["api_key"] = app_config.get("HARDCOVER_API_KEY", "")
    elif provider_name == "my_provider":
        kwargs["api_key"] = app_config.get("MY_PROVIDER_API_KEY", "")
    return kwargs
```

## Caching

Providers should use the `@cacheable` decorator for API calls:

```python
from cwa_book_downloader.core.cache import cacheable
from cwa_book_downloader.config.env import (
    METADATA_CACHE_SEARCH_TTL,
    METADATA_CACHE_BOOK_TTL,
)

@cacheable(ttl=METADATA_CACHE_SEARCH_TTL, key_prefix="myprovider:search")
def _search_cached(self, cache_key: str, options: MetadataSearchOptions):
    # Cached search implementation
    pass

@cacheable(ttl=METADATA_CACHE_BOOK_TTL, key_prefix="myprovider:book")
def get_book(self, book_id: str):
    # Cached book lookup
    pass
```

## Rate Limiting

For providers with rate limits (like Open Library), implement a rate limiter:

```python
from cwa_book_downloader.metadata_providers.openlibrary import RateLimiter

# 90 requests per 60 seconds
rate_limiter = RateLimiter(max_requests=90, window_seconds=60)

def make_request(self):
    rate_limiter.wait_if_needed()  # Blocks if rate limited
    # ... make request
```

## Configuration

Provider settings are stored in `CONFIG_DIR/plugins/<provider_name>.json` and managed via the Settings UI. See [Plugin Settings Guide](../../docs/plugin-settings.md) for detailed documentation on adding settings to your provider.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `METADATA_PROVIDER` | `""` | Active metadata provider name |
| `METADATA_CACHE_SEARCH_TTL` | `3600` | Search cache TTL in seconds |
| `METADATA_CACHE_BOOK_TTL` | `86400` | Book lookup cache TTL in seconds |
