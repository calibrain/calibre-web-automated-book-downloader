# Release Sources Plugin Development Guide

This guide explains how to create custom release source plugins for the CWA Book Downloader. The plugin system allows you to add new sources for searching and downloading books while integrating seamlessly with the existing queue, progress reporting, and settings infrastructure.

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Core Concepts](#core-concepts)
3. [Quick Start](#quick-start)
4. [ReleaseSource Interface](#releasesource-interface)
5. [DownloadHandler Interface](#downloadhandler-interface)
6. [Data Models](#data-models)
7. [Registration System](#registration-system)
8. [Settings Integration](#settings-integration)
9. [UI Column Configuration](#ui-column-configuration)
10. [Progress & Status Reporting](#progress--status-reporting)
11. [Error Handling](#error-handling)
12. [Complete Example Plugin](#complete-example-plugin)
13. [Best Practices](#best-practices)

---

## Architecture Overview

The release sources system is built around two core interfaces:

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  ReleaseSource  │────▶│     Release      │────▶│  DownloadTask   │
│   (Search)      │     │   (Result)       │     │   (Queue)       │
└─────────────────┘     └──────────────────┘     └─────────────────┘
                                                          │
                                                          ▼
                                                 ┌─────────────────┐
                                                 │ DownloadHandler │
                                                 │   (Execute)     │
                                                 └─────────────────┘
```

- **ReleaseSource**: Searches for releases based on book metadata (title, ISBN, author)
- **Release**: Standardized search result from any source
- **DownloadTask**: Source-agnostic task queued for download
- **DownloadHandler**: Executes the actual download with progress reporting

This separation allows:
- Different search sources (Anna's Archive, Prowlarr, IRC, etc.)
- Different download protocols (HTTP, torrent, usenet, etc.)
- Shared queue and progress infrastructure

---

## Core Concepts

### Plugin Lifecycle

1. **Registration** (import time): Decorators register your classes in the global registry
2. **Discovery**: `list_available_sources()` checks `is_available()` on each source
3. **Search**: User selects source, `search(book_metadata)` returns releases
4. **Queue**: User selects release, it becomes a `DownloadTask` in the queue
5. **Download**: Orchestrator calls `handler.download()` with callbacks
6. **Progress**: Handler reports via `progress_callback` and `status_callback`
7. **Completion**: Handler returns file path or `None` on failure

### Source vs Handler

A source has both a `ReleaseSource` (for searching) and a `DownloadHandler` (for downloading). They share the same `name` identifier:

```python
@register_source("my_source")      # Search registration
class MySource(ReleaseSource): ...

@register_handler("my_source")     # Download registration
class MyHandler(DownloadHandler): ...
```

---

## Quick Start

Create a new file at `cwa_book_downloader/release_sources/my_source.py`:

```python
from typing import Callable, List, Optional
from threading import Event

from cwa_book_downloader.release_sources import (
    Release,
    ReleaseSource,
    DownloadHandler,
    register_source,
    register_handler,
)
from cwa_book_downloader.metadata_providers import BookMetadata
from cwa_book_downloader.core.models import DownloadTask


@register_source("my_source")
class MySource(ReleaseSource):
    name = "my_source"
    display_name = "My Source"

    def search(self, book: BookMetadata) -> List[Release]:
        # Your search logic here
        return []

    def is_available(self) -> bool:
        return True  # Check if configured


@register_handler("my_source")
class MyHandler(DownloadHandler):
    def download(
        self,
        task: DownloadTask,
        cancel_flag: Event,
        progress_callback: Callable[[float], None],
        status_callback: Callable[[str, Optional[str]], None],
    ) -> Optional[str]:
        # Your download logic here
        return None  # Return file path on success

    def cancel(self, task_id: str) -> bool:
        return False  # Cancellation via cancel_flag
```

Register the import in `cwa_book_downloader/release_sources/__init__.py`:

```python
# At the bottom of the file
from cwa_book_downloader.release_sources import my_source  # noqa: F401, E402
```

---

## ReleaseSource Interface

The `ReleaseSource` abstract base class defines the search interface:

```python
from abc import ABC, abstractmethod
from typing import List
from cwa_book_downloader.metadata_providers import BookMetadata
from cwa_book_downloader.release_sources import Release, ReleaseColumnConfig

class ReleaseSource(ABC):
    """Interface for searching a release source."""

    name: str          # Internal identifier: "direct_download", "prowlarr"
    display_name: str  # User-facing name: "Direct Download", "Prowlarr"

    @abstractmethod
    def search(self, book: BookMetadata) -> List[Release]:
        """Search for releases of a book.

        Args:
            book: Book metadata including title, authors, ISBN, language

        Returns:
            List of Release objects found from this source
        """
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """Check if this source is configured and reachable.

        Returns:
            True if source can be used for searching
        """
        pass

    @classmethod
    def get_column_config(cls) -> ReleaseColumnConfig:
        """Get the column configuration for this source's release list UI.

        Override to customize how releases are displayed in the modal.
        Default implementation provides a basic layout.
        """
        return _default_column_config()
```

### Search Method

The `search` method receives complete book metadata and returns standardized releases:

```python
def search(self, book: BookMetadata) -> List[Release]:
    results = []

    # Strategy 1: Search by ISBN (most accurate)
    if book.isbn_13:
        api_results = self.api.search_isbn(book.isbn_13)
        for r in api_results:
            results.append(Release(
                source="my_source",
                source_id=r.id,
                title=r.title,
                format=r.format,
                size=r.size,
                # ... more fields
            ))

    # Strategy 2: Fallback to title + author search
    if not results:
        query = f"{book.title} {book.authors[0] if book.authors else ''}"
        api_results = self.api.search_text(query)
        for r in api_results:
            results.append(Release(...))

    return results
```

### Availability Check

Return `True` only if the source is properly configured and reachable:

```python
def is_available(self) -> bool:
    # Check for required configuration
    if not self.api_key:
        return False

    # Optional: Test connection
    try:
        return self.api.ping()
    except Exception:
        return False
```

---

## DownloadHandler Interface

The `DownloadHandler` abstract base class defines the download interface:

```python
from abc import ABC, abstractmethod
from typing import Callable, Optional
from threading import Event
from cwa_book_downloader.core.models import DownloadTask

class DownloadHandler(ABC):
    """Interface for executing downloads from a source."""

    @abstractmethod
    def download(
        self,
        task: DownloadTask,
        cancel_flag: Event,
        progress_callback: Callable[[float], None],
        status_callback: Callable[[str, Optional[str]], None],
    ) -> Optional[str]:
        """Execute download and return path to downloaded file.

        Args:
            task: The download task with metadata and identifiers
            cancel_flag: Threading Event - check .is_set() for cancellation
            progress_callback: Call with progress 0-100
            status_callback: Call with (status, optional_message)

        Returns:
            Absolute path to downloaded file, or None if failed/cancelled
        """
        pass

    @abstractmethod
    def cancel(self, task_id: str) -> bool:
        """Cancel an in-progress download.

        For most implementations, cancellation is handled via cancel_flag.
        This method is for external cancellation (e.g., torrent client).

        Returns:
            True if cancellation was initiated
        """
        pass
```

### Download Method Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `task` | `DownloadTask` | Contains `task_id`, `source`, `title`, `author`, `format`, `size`, `preview` |
| `cancel_flag` | `Event` | Check `cancel_flag.is_set()` periodically for cancellation |
| `progress_callback` | `Callable[[float], None]` | Call with progress 0-100 |
| `status_callback` | `Callable[[str, Optional[str]], None]` | Call with `(status, message)` |

### Status Values

| Status | Description | When to Use |
|--------|-------------|-------------|
| `"queued"` | Waiting in queue | Set by orchestrator, rarely needed in handler |
| `"resolving"` | Pre-download phase | Fetching metadata, extracting URLs, connecting |
| `"downloading"` | Active download | File transfer in progress |
| `"complete"` | Successfully finished | Set by orchestrator when handler returns a file path |
| `"error"` | Failed | Any unrecoverable error (handler should set this) |
| `"cancelled"` | User cancelled | Set by orchestrator when `cancel_flag` is triggered |

**Note**: The orchestrator automatically sets `"complete"` when your handler returns a valid file path, and `"cancelled"` when cancellation is detected. Your handler should primarily use `"resolving"`, `"downloading"`, and `"error"`.

### Typical Download Flow

```python
def download(self, task, cancel_flag, progress_callback, status_callback):
    try:
        # Phase 1: Resolve download URL
        status_callback("resolving", "Fetching download link...")

        if cancel_flag.is_set():
            return None

        download_url = self.api.get_download_url(task.task_id)
        if not download_url:
            status_callback("error", "No download URL available")
            return None

        # Phase 2: Download file
        status_callback("downloading", None)

        temp_path = Path(tempfile.mktemp(suffix=f".{task.format}"))

        response = requests.get(download_url, stream=True)
        total_size = int(response.headers.get("content-length", 0))
        downloaded = 0

        with open(temp_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                if cancel_flag.is_set():
                    temp_path.unlink(missing_ok=True)
                    return None

                f.write(chunk)
                downloaded += len(chunk)

                if total_size > 0:
                    progress_callback((downloaded / total_size) * 100)

        # Phase 3: Post-process and move to final location
        final_path = INGEST_DIR / f"{task.title}.{task.format}"
        shutil.move(str(temp_path), str(final_path))

        # Return the file path - orchestrator will set status to "complete"
        return str(final_path)

    except Exception as e:
        if not cancel_flag.is_set():
            status_callback("error", str(e))
        return None
```

---

## Data Models

### Release

Standardized search result returned by all sources:

```python
@dataclass
class Release:
    source: str                      # "direct_download", "prowlarr", "irc"
    source_id: str                   # Unique ID within that source
    title: str                       # Display title

    format: Optional[str] = None     # File format: "epub", "mobi", "pdf"
    size: Optional[str] = None       # Human-readable: "5.2 MB"
    size_bytes: Optional[int] = None # For sorting

    download_url: Optional[str] = None  # Direct download URL if available
    info_url: Optional[str] = None      # Link to tracker/info page

    protocol: Optional[str] = None   # "http", "torrent", "usenet"
    indexer: Optional[str] = None    # Display name: "Anna's Archive", "MyAnonamouse"
    seeders: Optional[int] = None    # For torrents

    extra: Dict = field(default_factory=dict)  # Source-specific metadata
```

#### Using the `extra` Field

Store source-specific data in `extra` that doesn't fit standard fields:

```python
Release(
    source="my_source",
    source_id="abc123",
    title="The Great Book",
    format="epub",
    size="2.5 MB",
    extra={
        "author": "Author Name",
        "language": "en",
        "preview": "https://example.com/cover.jpg",
        "quality": "high",
        "publisher": "Publisher Name",
    }
)
```

The frontend can access these via column config (e.g., `key="extra.language"`).

### DownloadTask

Source-agnostic task in the download queue:

```python
@dataclass
class DownloadTask:
    task_id: str                     # Unique ID (e.g., AA MD5, Prowlarr GUID)
    source: str                      # Handler name: "direct_download"
    title: str                       # Display title

    # Display info (from Release.extra or Release fields)
    author: Optional[str] = None
    format: Optional[str] = None
    size: Optional[str] = None
    preview: Optional[str] = None    # Cover image URL

    # Runtime state (managed by orchestrator)
    priority: int = 0
    added_time: float = field(default_factory=time.time)
    progress: float = 0.0
    status: str = "queued"
    status_message: Optional[str] = None
    download_path: Optional[str] = None
```

### BookMetadata

Book information passed to `search()`:

```python
@dataclass
class BookMetadata:
    provider: str                    # Metadata provider: "hardcover", "openlibrary"
    provider_id: str                 # ID in provider's system
    title: str

    provider_display_name: Optional[str] = None
    authors: List[str] = field(default_factory=list)
    isbn_10: Optional[str] = None
    isbn_13: Optional[str] = None
    cover_url: Optional[str] = None
    description: Optional[str] = None
    publisher: Optional[str] = None
    publish_year: Optional[int] = None
    language: Optional[str] = None   # ISO 639-1 code: "en", "de", "fr"
    genres: List[str] = field(default_factory=list)
    source_url: Optional[str] = None
    display_fields: List[DisplayField] = field(default_factory=list)
```

---

## Registration System

### Decorators

```python
from cwa_book_downloader.release_sources import register_source, register_handler

@register_source("my_source")
class MySource(ReleaseSource):
    ...

@register_handler("my_source")
class MyHandler(DownloadHandler):
    ...
```

### Registry Functions

```python
from cwa_book_downloader.release_sources import (
    get_source,
    get_handler,
    list_available_sources,
)

# Get a source instance by name
source = get_source("my_source")
releases = source.search(book_metadata)

# Get a handler instance by name
handler = get_handler("my_source")
file_path = handler.download(task, cancel_flag, progress_cb, status_cb)

# List all sources where is_available() returns True
available = list_available_sources()
# Returns: [{"name": "direct_download", "display_name": "Direct Download"}, ...]
```

### Import Registration

**Critical**: Your plugin must be imported at module load time. Add to `release_sources/__init__.py`:

```python
# At the bottom of __init__.py
from cwa_book_downloader.release_sources import direct_download  # noqa: F401, E402
from cwa_book_downloader.release_sources import my_source  # noqa: F401, E402
```

The `noqa` comments suppress linter warnings about unused imports.

---

## Settings Integration

Register plugin settings using the settings registry decorator:

```python
from cwa_book_downloader.core.settings_registry import (
    register_settings,
    HeadingField,
    TextField,
    PasswordField,
    NumberField,
    CheckboxField,
    SelectField,
    ActionButton,
    get_setting_value,
    is_value_from_env,
)

@register_settings(
    name="my_source",               # Unique identifier
    display_name="My Source",       # Shown in settings UI
    icon="globe",                   # Icon name (optional)
    order=60,                       # Sort order in sidebar
    group="sources",                # Grouping in sidebar
)
def my_source_settings():
    """Register settings for My Source plugin."""
    return [
        HeadingField(
            key="heading",
            title="My Source Configuration",
            description="Configure connection to My Source API",
        ),

        PasswordField(
            key="MY_SOURCE_API_KEY",
            label="API Key",
            description="Your API key from mysource.com/settings",
            required=True,
            env_supported=True,  # Can be set via MY_SOURCE_API_KEY env var
        ),

        TextField(
            key="MY_SOURCE_BASE_URL",
            label="Base URL",
            placeholder="https://api.mysource.com",
            env_supported=True,
        ),

        NumberField(
            key="MY_SOURCE_TIMEOUT",
            label="Request Timeout",
            description="Seconds to wait for API responses",
            default=30,
            min_value=5,
            max_value=120,
        ),

        CheckboxField(
            key="MY_SOURCE_ENABLED",
            label="Enable My Source",
            description="Include in release searches",
            default=True,
        ),

        ActionButton(
            key="test_connection",
            label="Test Connection",
            callback=test_my_source_connection,
            style="primary",
        ),
    ]

def test_my_source_connection():
    """Test connection callback - returns result dict."""
    try:
        # Get current API key
        api_key = get_setting_value(
            PasswordField(key="MY_SOURCE_API_KEY", label=""),
            "my_source"
        )

        if not api_key:
            return {"success": False, "message": "API key not configured"}

        # Test the connection
        response = requests.get(
            "https://api.mysource.com/ping",
            headers={"Authorization": f"Bearer {api_key}"}
        )

        if response.status_code == 200:
            return {"success": True, "message": "Connected successfully!"}
        else:
            return {"success": False, "message": f"API returned {response.status_code}"}

    except Exception as e:
        return {"success": False, "message": str(e)}
```

### Field Types Reference

| Field Type | Purpose | Key Properties |
|------------|---------|----------------|
| `HeadingField` | Section header | `title`, `description`, `link_url`, `link_text` |
| `TextField` | Single-line text | `placeholder`, `max_length` |
| `PasswordField` | Masked input | Not returned in GET unless changed |
| `NumberField` | Numeric input | `min_value`, `max_value`, `step`, `default` |
| `CheckboxField` | Boolean toggle | `default` |
| `SelectField` | Single dropdown | `options` (list or callable) |
| `MultiSelectField` | Multi-choice | `options` (list or callable) |
| `ActionButton` | Trigger callback | `callback`, `style` |

### Reading Settings

```python
from cwa_book_downloader.core.settings_registry import (
    get_setting_value,
    is_value_from_env,
    load_config_file,
)

# In your source/handler class
def __init__(self):
    # Load from config file
    config = load_config_file("my_source")
    self.api_key = config.get("MY_SOURCE_API_KEY")
    self.base_url = config.get("MY_SOURCE_BASE_URL", "https://api.mysource.com")

# Or use get_setting_value for ENV var priority
api_key = get_setting_value(
    PasswordField(key="MY_SOURCE_API_KEY", label=""),
    "my_source"
)
```

### Settings Priority

Settings are resolved in this order (highest priority first):

1. **Environment variable** (if `env_supported=True`)
2. **Config file** (`/config/plugins/{tab_name}.json`)
3. **Default value** (from field definition)

When a value comes from an ENV var, the UI shows a "locked" badge and the field is read-only.

---

## UI Column Configuration

Customize how releases are displayed in the release modal by overriding `get_column_config()`:

```python
from cwa_book_downloader.release_sources import (
    ReleaseColumnConfig,
    ColumnSchema,
    ColumnRenderType,
    ColumnAlign,
    ColumnColorHint,
    LeadingCellConfig,
    LeadingCellType,
)

@classmethod
def get_column_config(cls) -> ReleaseColumnConfig:
    return ReleaseColumnConfig(
        columns=[
            ColumnSchema(
                key="indexer",
                label="Source",
                render_type=ColumnRenderType.TEXT,
                width="minmax(0, 1fr)",
            ),
            ColumnSchema(
                key="extra.language",
                label="Language",
                render_type=ColumnRenderType.BADGE,
                align=ColumnAlign.CENTER,
                width="60px",
                color_hint=ColumnColorHint(type="map", value="language"),
                uppercase=True,
            ),
            ColumnSchema(
                key="format",
                label="Format",
                render_type=ColumnRenderType.BADGE,
                color_hint=ColumnColorHint(type="map", value="format"),
                uppercase=True,
            ),
            ColumnSchema(
                key="size",
                label="Size",
                render_type=ColumnRenderType.SIZE,
                align=ColumnAlign.CENTER,
                width="80px",
            ),
            ColumnSchema(
                key="seeders",
                label="Seeders",
                render_type=ColumnRenderType.SEEDERS,
                align=ColumnAlign.CENTER,
                width="60px",
                hide_mobile=True,
            ),
        ],
        grid_template="minmax(0, 2fr) 60px 80px 80px 60px",
        leading_cell=LeadingCellConfig(
            type=LeadingCellType.THUMBNAIL,
            key="extra.preview",
        ),
    )
```

### Column Properties

| Property | Type | Description |
|----------|------|-------------|
| `key` | `str` | Data path: `"format"`, `"extra.language"`, `"extra.seeders"` |
| `label` | `str` | Accessibility label |
| `render_type` | `ColumnRenderType` | `TEXT`, `BADGE`, `SIZE`, `NUMBER`, `SEEDERS` |
| `align` | `ColumnAlign` | `LEFT`, `CENTER`, `RIGHT` |
| `width` | `str` | CSS width: `"80px"`, `"minmax(0, 2fr)"` |
| `hide_mobile` | `bool` | Hide on small screens |
| `color_hint` | `ColumnColorHint` | For `BADGE` type coloring |
| `fallback` | `str` | Shown when data missing (default: `"-"`) |
| `uppercase` | `bool` | Force uppercase text |

### Color Hints

For `BADGE` render type, specify colors:

```python
# Use frontend color map (defined in colorMaps.ts)
ColumnColorHint(type="map", value="format")    # Maps epub→green, pdf→red, etc.
ColumnColorHint(type="map", value="language")  # Maps en→blue, de→yellow, etc.

# Use static Tailwind class
ColumnColorHint(type="static", value="bg-purple-500/20 text-purple-400")
```

### Leading Cell Types

Configure what appears before the title:

```python
# Show cover thumbnail
LeadingCellConfig(type=LeadingCellType.THUMBNAIL, key="extra.preview")

# Show colored badge
LeadingCellConfig(
    type=LeadingCellType.BADGE,
    key="protocol",
    color_hint=ColumnColorHint(type="map", value="protocol"),
)

# No leading cell
LeadingCellConfig(type=LeadingCellType.NONE)
```

---

## Progress & Status Reporting

### Progress Callback

Report download progress (0-100):

```python
# In your download loop
for chunk in response.iter_content(8192):
    downloaded += len(chunk)
    progress_callback((downloaded / total_size) * 100)
```

Progress updates are throttled by the orchestrator before WebSocket broadcast:
- Start (0%) and completion (≥99%) always broadcast
- Otherwise every `DOWNLOAD_PROGRESS_UPDATE_INTERVAL` seconds
- On progress jumps >10%

### Status Callback

Report status changes with optional messages:

```python
status_callback("resolving", "Connecting to server...")
status_callback("resolving", "Fetching download link...")
status_callback("downloading", None)  # Message optional
status_callback("complete", None)
status_callback("error", "Connection timeout after 30s")
```

### Typical Status Sequence

```
queued          → Initial state (set by orchestrator)
resolving       → "Fetching book details..."
resolving       → "Trying source 1..."
resolving       → "Trying source 2..." (on retry)
downloading     → (progress updates: 0%, 25%, 50%, 75%, 100%)
complete        → Success
```

Or on failure:

```
queued          → Initial state
resolving       → "Connecting..."
error           → "All download sources failed"
```

---

## Error Handling

### In Search Methods

Return empty list on errors, log for debugging:

```python
def search(self, book: BookMetadata) -> List[Release]:
    try:
        results = self.api.search(book.title)
        return [self._to_release(r) for r in results]
    except ConnectionError as e:
        logger.warning(f"Connection error searching {self.name}: {e}")
        return []
    except AuthenticationError:
        logger.error(f"{self.name} API key invalid")
        return []
    except Exception as e:
        logger.error(f"Unexpected error in {self.name}.search: {e}")
        return []
```

### In Download Methods

Use `status_callback` to report errors, return `None`:

```python
def download(self, task, cancel_flag, progress_callback, status_callback):
    try:
        # ... download logic ...
        return file_path

    except Exception as e:
        if not cancel_flag.is_set():
            logger.error(f"Download error for {task.task_id}: {e}")
            status_callback("error", str(e))
        return None
```

### Handling Cancellation

Check `cancel_flag.is_set()` at key points:

```python
def download(self, task, cancel_flag, progress_callback, status_callback):
    # Check before expensive operations
    if cancel_flag.is_set():
        return None

    status_callback("resolving", "Fetching metadata...")
    metadata = self.api.get_metadata(task.task_id)

    # Check before download
    if cancel_flag.is_set():
        return None

    status_callback("downloading", None)

    # Check during download loop
    for chunk in response.iter_content(8192):
        if cancel_flag.is_set():
            # Clean up partial file
            temp_file.unlink(missing_ok=True)
            return None

        # ... process chunk ...

    return file_path
```

---

## Complete Example Plugin

Here's a complete example plugin for a fictional "BookNet" API:

```python
"""
BookNet Release Source Plugin

A complete example plugin demonstrating all features of the release source system.
"""

import requests
import tempfile
import shutil
from pathlib import Path
from typing import Callable, Dict, List, Optional
from threading import Event

from cwa_book_downloader.release_sources import (
    Release,
    ReleaseSource,
    DownloadHandler,
    register_source,
    register_handler,
    ReleaseColumnConfig,
    ColumnSchema,
    ColumnRenderType,
    ColumnAlign,
    ColumnColorHint,
    LeadingCellConfig,
    LeadingCellType,
)
from cwa_book_downloader.metadata_providers import BookMetadata
from cwa_book_downloader.core.models import DownloadTask
from cwa_book_downloader.core.logger import setup_logger
from cwa_book_downloader.core.settings_registry import (
    register_settings,
    HeadingField,
    TextField,
    PasswordField,
    NumberField,
    CheckboxField,
    ActionButton,
    load_config_file,
)
from cwa_book_downloader.config.env import INGEST_DIR

logger = setup_logger(__name__)

# ---------------------------------------------------------------------------
# Settings Registration
# ---------------------------------------------------------------------------

@register_settings(
    name="booknet",
    display_name="BookNet",
    icon="book",
    order=55,
    group="sources",
)
def booknet_settings():
    """Register BookNet settings."""
    return [
        HeadingField(
            key="heading",
            title="BookNet API",
            description="Connect to BookNet for additional book sources",
            link_url="https://booknet.example.com/api-docs",
            link_text="API Documentation",
        ),

        PasswordField(
            key="BOOKNET_API_KEY",
            label="API Key",
            description="Get your key from booknet.example.com/settings",
            required=True,
            env_supported=True,
        ),

        TextField(
            key="BOOKNET_BASE_URL",
            label="Base URL",
            placeholder="https://api.booknet.example.com",
            env_supported=True,
        ),

        NumberField(
            key="BOOKNET_TIMEOUT",
            label="Request Timeout",
            description="Seconds to wait for API responses",
            default=30,
            min_value=5,
            max_value=120,
        ),

        CheckboxField(
            key="BOOKNET_PREFER_EPUB",
            label="Prefer EPUB format",
            description="Sort EPUB results first when available",
            default=True,
        ),

        ActionButton(
            key="test_connection",
            label="Test Connection",
            callback=_test_booknet_connection,
            style="primary",
        ),
    ]


def _test_booknet_connection() -> Dict:
    """Test BookNet API connection."""
    try:
        config = load_config_file("booknet")
        api_key = config.get("BOOKNET_API_KEY")
        base_url = config.get("BOOKNET_BASE_URL", "https://api.booknet.example.com")

        if not api_key:
            return {"success": False, "message": "API key not configured"}

        response = requests.get(
            f"{base_url}/v1/ping",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10,
        )

        if response.status_code == 200:
            data = response.json()
            return {
                "success": True,
                "message": f"Connected! {data.get('books_available', 0):,} books available"
            }
        elif response.status_code == 401:
            return {"success": False, "message": "Invalid API key"}
        else:
            return {"success": False, "message": f"API error: {response.status_code}"}

    except requests.Timeout:
        return {"success": False, "message": "Connection timeout"}
    except Exception as e:
        return {"success": False, "message": str(e)}


# ---------------------------------------------------------------------------
# Release Source Implementation
# ---------------------------------------------------------------------------

@register_source("booknet")
class BookNetSource(ReleaseSource):
    """Search BookNet for book releases."""

    name = "booknet"
    display_name = "BookNet"

    def __init__(self):
        config = load_config_file("booknet")
        self.api_key = config.get("BOOKNET_API_KEY")
        self.base_url = config.get("BOOKNET_BASE_URL", "https://api.booknet.example.com")
        self.timeout = config.get("BOOKNET_TIMEOUT", 30)
        self.prefer_epub = config.get("BOOKNET_PREFER_EPUB", True)

    def search(self, book: BookMetadata) -> List[Release]:
        """Search BookNet for releases."""
        if not self.is_available():
            return []

        releases = []

        try:
            # Strategy 1: Search by ISBN (most accurate)
            if book.isbn_13:
                releases = self._search_isbn(book.isbn_13)

            # Strategy 2: Fallback to title + author
            if not releases:
                releases = self._search_text(book.title, book.authors)

            # Sort by preference
            if self.prefer_epub:
                releases.sort(key=lambda r: (0 if r.format == "epub" else 1))

            return releases

        except requests.Timeout:
            logger.warning("BookNet search timeout")
            return []
        except Exception as e:
            logger.error(f"BookNet search error: {e}")
            return []

    def _search_isbn(self, isbn: str) -> List[Release]:
        """Search by ISBN."""
        response = requests.get(
            f"{self.base_url}/v1/search",
            params={"isbn": isbn},
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        return [self._to_release(r) for r in response.json().get("results", [])]

    def _search_text(self, title: str, authors: List[str]) -> List[Release]:
        """Search by title and author."""
        query = title
        if authors:
            query += f" {authors[0]}"

        response = requests.get(
            f"{self.base_url}/v1/search",
            params={"q": query},
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        return [self._to_release(r) for r in response.json().get("results", [])]

    def _to_release(self, data: dict) -> Release:
        """Convert API response to Release object."""
        return Release(
            source="booknet",
            source_id=data["id"],
            title=data["title"],
            format=data.get("format", "").lower(),
            size=data.get("size_human"),
            size_bytes=data.get("size_bytes"),
            download_url=data.get("download_url"),
            info_url=data.get("info_url"),
            protocol="http",
            indexer="BookNet",
            extra={
                "author": data.get("author"),
                "language": data.get("language", "en"),
                "preview": data.get("cover_url"),
                "quality": data.get("quality"),
                "publisher": data.get("publisher"),
            },
        )

    def is_available(self) -> bool:
        """Check if BookNet is configured."""
        return bool(self.api_key)

    @classmethod
    def get_column_config(cls) -> ReleaseColumnConfig:
        """Custom column layout for BookNet releases."""
        return ReleaseColumnConfig(
            columns=[
                ColumnSchema(
                    key="extra.quality",
                    label="Quality",
                    render_type=ColumnRenderType.BADGE,
                    align=ColumnAlign.CENTER,
                    width="70px",
                    color_hint=ColumnColorHint(type="static", value="bg-purple-500/20 text-purple-400"),
                ),
                ColumnSchema(
                    key="extra.language",
                    label="Language",
                    render_type=ColumnRenderType.BADGE,
                    align=ColumnAlign.CENTER,
                    width="60px",
                    color_hint=ColumnColorHint(type="map", value="language"),
                    uppercase=True,
                ),
                ColumnSchema(
                    key="format",
                    label="Format",
                    render_type=ColumnRenderType.BADGE,
                    color_hint=ColumnColorHint(type="map", value="format"),
                    uppercase=True,
                ),
                ColumnSchema(
                    key="size",
                    label="Size",
                    render_type=ColumnRenderType.SIZE,
                    align=ColumnAlign.CENTER,
                    width="80px",
                ),
            ],
            grid_template="minmax(0, 2fr) 70px 60px 80px 80px",
            leading_cell=LeadingCellConfig(
                type=LeadingCellType.THUMBNAIL,
                key="extra.preview",
            ),
        )


# ---------------------------------------------------------------------------
# Download Handler Implementation
# ---------------------------------------------------------------------------

@register_handler("booknet")
class BookNetHandler(DownloadHandler):
    """Handle downloads from BookNet."""

    def __init__(self):
        config = load_config_file("booknet")
        self.api_key = config.get("BOOKNET_API_KEY")
        self.base_url = config.get("BOOKNET_BASE_URL", "https://api.booknet.example.com")
        self.timeout = config.get("BOOKNET_TIMEOUT", 30)

    def download(
        self,
        task: DownloadTask,
        cancel_flag: Event,
        progress_callback: Callable[[float], None],
        status_callback: Callable[[str, Optional[str]], None],
    ) -> Optional[str]:
        """Execute download from BookNet."""
        try:
            # Phase 1: Get download URL
            status_callback("resolving", "Fetching download link...")

            if cancel_flag.is_set():
                return None

            download_info = self._get_download_info(task.task_id)
            if not download_info:
                status_callback("error", "Could not get download link")
                return None

            download_url = download_info["url"]
            expected_size = download_info.get("size_bytes", 0)

            # Phase 2: Download file
            if cancel_flag.is_set():
                return None

            status_callback("downloading", None)

            temp_path = Path(tempfile.mktemp(suffix=f".{task.format or 'epub'}"))

            try:
                downloaded_size = self._download_file(
                    download_url,
                    temp_path,
                    expected_size,
                    cancel_flag,
                    progress_callback,
                )

                if cancel_flag.is_set():
                    temp_path.unlink(missing_ok=True)
                    return None

                # Validate download
                if downloaded_size < 10 * 1024:  # Less than 10KB
                    temp_path.unlink(missing_ok=True)
                    status_callback("error", f"File too small ({downloaded_size} bytes)")
                    return None

                # Phase 3: Move to final location
                safe_title = "".join(
                    c if c.isalnum() or c in " .-_" else "_"
                    for c in task.title[:100]
                ).strip()

                final_filename = f"{safe_title}.{task.format or 'epub'}"
                final_path = Path(INGEST_DIR) / final_filename

                # Avoid overwriting existing files
                counter = 1
                while final_path.exists():
                    final_path = Path(INGEST_DIR) / f"{safe_title} ({counter}).{task.format or 'epub'}"
                    counter += 1

                shutil.move(str(temp_path), str(final_path))

                # Return file path - orchestrator sets "complete" status
                return str(final_path)

            except Exception as e:
                temp_path.unlink(missing_ok=True)
                raise

        except Exception as e:
            if not cancel_flag.is_set():
                logger.error(f"BookNet download error: {e}")
                status_callback("error", str(e))
            return None

    def _get_download_info(self, item_id: str) -> Optional[dict]:
        """Get download URL from API."""
        response = requests.get(
            f"{self.base_url}/v1/download/{item_id}",
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=self.timeout,
        )

        if response.status_code != 200:
            return None

        return response.json()

    def _download_file(
        self,
        url: str,
        dest_path: Path,
        expected_size: int,
        cancel_flag: Event,
        progress_callback: Callable[[float], None],
    ) -> int:
        """Download file with progress reporting."""
        response = requests.get(
            url,
            headers={"Authorization": f"Bearer {self.api_key}"},
            stream=True,
            timeout=self.timeout,
        )
        response.raise_for_status()

        total_size = int(response.headers.get("content-length", expected_size))
        downloaded = 0

        with open(dest_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                if cancel_flag.is_set():
                    return downloaded

                f.write(chunk)
                downloaded += len(chunk)

                if total_size > 0:
                    progress_callback((downloaded / total_size) * 100)

        return downloaded

    def cancel(self, task_id: str) -> bool:
        """Cancellation is handled via cancel_flag."""
        return False
```

---

## Best Practices

### 1. Separation of Concerns

- **ReleaseSource**: Only search logic, no download code
- **DownloadHandler**: Only download logic, no search code
- Keep settings registration separate from source/handler classes

### 2. Graceful Degradation

```python
def is_available(self) -> bool:
    """Return False gracefully if not configured."""
    try:
        return bool(self.api_key) and self._test_connection()
    except Exception:
        return False

def search(self, book: BookMetadata) -> List[Release]:
    """Return empty list on errors, don't raise."""
    try:
        return self._do_search(book)
    except Exception as e:
        logger.warning(f"Search failed: {e}")
        return []
```

### 3. Cancellation Checks

Check `cancel_flag.is_set()` before and during expensive operations:

```python
def download(self, task, cancel_flag, progress_callback, status_callback):
    # Before network calls
    if cancel_flag.is_set():
        return None

    # During download loop
    for chunk in response.iter_content(8192):
        if cancel_flag.is_set():
            cleanup_partial_file()
            return None
```

### 4. Progress Reporting

Report progress frequently for good UX:

```python
# In download loop
for chunk in response.iter_content(8192):
    downloaded += len(chunk)
    if total_size > 0:
        progress_callback((downloaded / total_size) * 100)
```

### 5. Meaningful Status Messages

```python
# Good: specific and actionable
status_callback("resolving", "Connecting to BookNet API...")
status_callback("resolving", "Fetching download link...")
status_callback("error", "API rate limit exceeded, try again in 60s")

# Bad: vague
status_callback("resolving", "Working...")
status_callback("error", "Failed")
```

### 6. Clean Up on Failure

```python
temp_path = Path(tempfile.mktemp())
try:
    # ... download to temp_path ...
except Exception:
    temp_path.unlink(missing_ok=True)
    raise
```

### 7. Settings with ENV Support

For deployment flexibility, mark key settings as `env_supported=True`:

```python
PasswordField(
    key="MY_API_KEY",
    label="API Key",
    env_supported=True,  # Can set via MY_API_KEY env var
)
```

### 8. Logging

Use the project's logger for consistent output:

```python
from cwa_book_downloader.core.logger import setup_logger

logger = setup_logger(__name__)

logger.debug("Detailed debug info")      # Development only
logger.info("Normal operation info")     # General progress
logger.warning("Recoverable issues")     # Fallback used, retry happening
logger.error("Failures")                 # Unrecoverable errors
```

---

## Appendix: Import Checklist

When creating a new plugin:

1. Create `cwa_book_downloader/release_sources/my_plugin.py`
2. Implement `ReleaseSource` subclass with `@register_source("my_plugin")`
3. Implement `DownloadHandler` subclass with `@register_handler("my_plugin")`
4. Add settings with `@register_settings("my_plugin", ...)`
5. Add import to `cwa_book_downloader/release_sources/__init__.py`:
   ```python
   from cwa_book_downloader.release_sources import my_plugin  # noqa: F401, E402
   ```
6. Test `is_available()` returns `True` when configured
7. Test search returns valid `Release` objects
8. Test download handles cancellation and errors gracefully
