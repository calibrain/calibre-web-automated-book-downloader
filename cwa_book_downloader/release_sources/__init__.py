"""Release source plugin system - base classes and registry."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from enum import Enum
from threading import Event
from typing import List, Optional, Dict, Type, Callable, Literal, Any

from cwa_book_downloader.core.models import DownloadTask
from cwa_book_downloader.metadata_providers import BookMetadata


class ReleaseProtocol(str, Enum):
    """Protocol for downloading a release."""
    HTTP = "http"       # Direct HTTP download
    TORRENT = "torrent" # BitTorrent
    NZB = "nzb"         # Usenet NZB
    DCC = "dcc"         # IRC DCC


@dataclass
class Release:
    """A downloadable release - all sources return this same structure."""
    source: str                      # "direct", "prowlarr", "irc", etc.
    source_id: str                   # ID within that source
    title: str
    format: Optional[str] = None
    language: Optional[str] = None   # ISO 639-1 code (e.g., "en", "de", "fr")
    size: Optional[str] = None
    size_bytes: Optional[int] = None
    download_url: Optional[str] = None
    info_url: Optional[str] = None   # Link to release info page (e.g., tracker) - makes title clickable
    protocol: Optional[ReleaseProtocol] = None
    indexer: Optional[str] = None    # Source name for display
    seeders: Optional[int] = None    # For torrents
    peers: Optional[str] = None      # For torrents: "seeders/leechers" display string
    content_type: Optional[str] = None  # "ebook" or "audiobook" - preserved from search
    extra: Dict = field(default_factory=dict)  # Source-specific metadata


@dataclass
class DownloadProgress:
    """Progress update structure.

    DEPRECATED: This class is deprecated and will be removed.
    The new DownloadHandler.download() uses simpler callbacks:
    - progress_callback(float) for progress percentage
    - status_callback(str, Optional[str]) for status and message
    """
    status: str                      # "queued", "resolving", "downloading", "complete", "failed"
    progress: float                  # 0-100
    status_message: Optional[str] = None
    download_speed: Optional[int] = None
    eta: Optional[int] = None
    save_path: Optional[str] = None


# --- Column Schema for Plugin-Driven UI ---

class ColumnRenderType(str, Enum):
    """How the frontend should render the column value."""
    TEXT = "text"           # Plain text
    BADGE = "badge"         # Colored badge (format, language)
    SIZE = "size"           # File size formatting
    NUMBER = "number"       # Numeric value
    PEERS = "peers"         # Peers display: "S/L" with color based on seeder count


class ColumnAlign(str, Enum):
    """Column alignment options."""
    LEFT = "left"
    CENTER = "center"
    RIGHT = "right"


@dataclass
class ColumnColorHint:
    """Color hint for badge-type columns."""
    type: Literal["map", "static"]   # "map" uses frontend colorMaps, "static" is fixed class
    value: str                        # Map name ("format", "language") or Tailwind class


@dataclass
class ColumnSchema:
    """Definition for a single column in the release list."""
    key: str                                      # Data path (e.g., "format", "extra.language")
    label: str                                    # Accessibility label
    render_type: ColumnRenderType = ColumnRenderType.TEXT
    align: ColumnAlign = ColumnAlign.LEFT
    width: str = "auto"                           # CSS width (e.g., "80px", "minmax(0,2fr)")
    hide_mobile: bool = False                     # Hide on small screens
    color_hint: Optional[ColumnColorHint] = None  # For BADGE render type
    fallback: str = "-"                           # Value to show when data is missing
    uppercase: bool = False                       # Force uppercase display


class LeadingCellType(str, Enum):
    """Type of leading cell to display in release rows."""
    THUMBNAIL = "thumbnail"  # Show book cover image
    BADGE = "badge"          # Show colored badge (e.g., "Torrent", "Usenet")
    NONE = "none"            # No leading cell


@dataclass
class LeadingCellConfig:
    """Configuration for the leading cell in release rows."""
    type: LeadingCellType = LeadingCellType.THUMBNAIL
    key: Optional[str] = None                     # Field path for data (e.g., "extra.preview" or "extra.download_type")
    color_hint: Optional[ColumnColorHint] = None  # For badge type - maps values to colors
    uppercase: bool = False                       # Force uppercase for badge text


@dataclass
class ReleaseColumnConfig:
    """Complete column configuration for a release source."""
    columns: List[ColumnSchema]
    grid_template: str = "minmax(0,2fr) 60px 80px 80px"  # CSS grid-template-columns
    leading_cell: Optional[LeadingCellConfig] = None     # Defaults to thumbnail mode if None
    online_servers: Optional[List[str]] = None           # For IRC: list of currently online server nicks
    cache_ttl_seconds: Optional[int] = None              # How long to cache results (default: 5 min)
    supported_filters: Optional[List[str]] = None        # Which filters this source supports: ["format", "language"]


def serialize_column_config(config: ReleaseColumnConfig) -> Dict[str, Any]:
    """Serialize column configuration for API response."""
    result: Dict[str, Any] = {
        "columns": [
            {
                "key": col.key,
                "label": col.label,
                "render_type": col.render_type.value,
                "align": col.align.value,
                "width": col.width,
                "hide_mobile": col.hide_mobile,
                "color_hint": {
                    "type": col.color_hint.type,
                    "value": col.color_hint.value
                } if col.color_hint else None,
                "fallback": col.fallback,
                "uppercase": col.uppercase,
            }
            for col in config.columns
        ],
        "grid_template": config.grid_template,
    }

    # Include leading_cell config if specified
    if config.leading_cell:
        result["leading_cell"] = {
            "type": config.leading_cell.type.value,
            "key": config.leading_cell.key,
            "color_hint": {
                "type": config.leading_cell.color_hint.type,
                "value": config.leading_cell.color_hint.value
            } if config.leading_cell.color_hint else None,
            "uppercase": config.leading_cell.uppercase,
        }

    # Include online_servers if provided (e.g., for IRC source)
    if config.online_servers is not None:
        result["online_servers"] = config.online_servers

    # Include cache TTL if specified (sources can request longer caching)
    if config.cache_ttl_seconds is not None:
        result["cache_ttl_seconds"] = config.cache_ttl_seconds

    # Include supported filters (sources declare which filters they support)
    if config.supported_filters is not None:
        result["supported_filters"] = config.supported_filters

    return result


def _default_column_config() -> ReleaseColumnConfig:
    """Default column configuration used when source doesn't define its own."""
    return ReleaseColumnConfig(
        columns=[
            ColumnSchema(
                key="extra.language",
                label="Language",
                render_type=ColumnRenderType.BADGE,
                align=ColumnAlign.CENTER,
                width="60px",
                hide_mobile=False,  # Language shown on mobile
                color_hint=ColumnColorHint(type="map", value="language"),
                uppercase=True,
            ),
            ColumnSchema(
                key="format",
                label="Format",
                render_type=ColumnRenderType.BADGE,
                align=ColumnAlign.CENTER,
                width="80px",
                hide_mobile=False,  # Format shown on mobile
                color_hint=ColumnColorHint(type="map", value="format"),
                uppercase=True,
            ),
            ColumnSchema(
                key="size",
                label="Size",
                render_type=ColumnRenderType.SIZE,
                align=ColumnAlign.CENTER,
                width="80px",
                hide_mobile=False,  # Size shown on mobile
            ),
        ],
        grid_template="minmax(0,2fr) 60px 80px 80px",
        supported_filters=["format", "language"],  # Default: both filters available
    )


class ReleaseSource(ABC):
    """Interface for searching a release source."""
    name: str                        # "direct", "prowlarr"
    display_name: str                # "Direct Download", "Prowlarr"
    supported_content_types: List[str] = ["ebook", "audiobook"]  # Content types this source supports

    @abstractmethod
    def search(
        self,
        book: BookMetadata,
        expand_search: bool = False,
        languages: Optional[List[str]] = None,
        content_type: str = "ebook"
    ) -> List[Release]:
        """Search for releases of a book.

        Args:
            book: Book metadata from provider
            expand_search: If True, use broader search (e.g., title+author instead of ISBN).
                          Not all sources support this - they may ignore it.
            languages: Optional list of language codes to filter by.
                      If provided, overrides book.language and default settings.
                      Not all sources support this - they may ignore it.
            content_type: Content type - "ebook" or "audiobook" (default: "ebook").
                         Sources may use this to adjust search categories/filters.
        """
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """Check if this source is configured and reachable."""
        pass

    @classmethod
    def get_column_config(cls) -> ReleaseColumnConfig:
        """Get the column configuration for this source's release list UI.

        Override this method in subclasses to provide custom columns.
        Default implementation returns standard columns (language, format, size).
        """
        return _default_column_config()


class DownloadHandler(ABC):
    """Interface for executing downloads from a source.

    ## Staging Architecture

    Handlers are responsible for getting files into the STAGING directory (TMP_DIR).
    The orchestrator handles all post-processing and moving to the INGEST directory.

    This means handlers should:
    1. Download/retrieve the file to the staging directory
    2. Return the path to the staged file
    3. NOT move files to the ingest folder (orchestrator does this)

    Examples by source type:
    - **Direct downloads**: Download directly to staging dir
    - **Torrents**: Copy completed file from torrent client to staging (keep seeding)
    - **Usenet**: Move completed file from NZB client to staging

    Use the staging helpers from orchestrator:
    - `get_staging_dir()` - Get the staging directory path
    - `get_staging_path(task_id, ext)` - Get a staging path for a task
    - `stage_file(source, task_id, copy=False)` - Stage a file (copy or move)

    The orchestrator then handles:
    - Archive extraction (RAR/ZIP)
    - Custom script execution
    - Moving to the final ingest folder
    """

    @abstractmethod
    def download(
        self,
        task: DownloadTask,
        cancel_flag: Event,
        progress_callback: Callable[[float], None],
        status_callback: Callable[[str, Optional[str]], None]
    ) -> Optional[str]:
        """
        Execute download and return path to STAGED file.

        Handlers should download/copy files to the staging directory (TMP_DIR),
        NOT directly to the ingest folder. The orchestrator handles post-processing
        (archive extraction, custom scripts) and final move to ingest.

        Args:
            task: The download task with task_id and display info
            cancel_flag: Event to check for cancellation
            progress_callback: Called with progress percentage (0-100)
            status_callback: Called with (status, message) for status updates

        Returns:
            Path to staged file (in TMP_DIR) if successful, None otherwise
        """
        pass

    @abstractmethod
    def cancel(self, task_id: str) -> bool:
        """Cancel an in-progress download."""
        pass


# --- Registry ---

_SOURCES: Dict[str, Type[ReleaseSource]] = {}
_HANDLERS: Dict[str, Type[DownloadHandler]] = {}


def register_source(name: str):
    """Decorator to register a release source."""
    def decorator(cls):
        _SOURCES[name] = cls
        return cls
    return decorator


def register_handler(name: str):
    """Decorator to register a download handler."""
    def decorator(cls):
        _HANDLERS[name] = cls
        return cls
    return decorator


def get_source(name: str) -> ReleaseSource:
    """Get a release source instance by name."""
    if name not in _SOURCES:
        raise ValueError(f"Unknown release source: {name}")
    return _SOURCES[name]()


def get_handler(name: str) -> DownloadHandler:
    """Get a download handler instance by name."""
    if name not in _HANDLERS:
        raise ValueError(f"Unknown download handler: {name}")
    return _HANDLERS[name]()


def list_available_sources() -> List[dict]:
    """For frontend - list all registered sources with their status.

    Returns all sources (not just available ones) so the frontend can show
    appropriate UI for disabled/unconfigured sources instead of hiding them.
    """
    result = []
    for name, src_class in _SOURCES.items():
        instance = src_class()
        result.append({
            "name": name,
            "display_name": instance.display_name,
            "enabled": instance.is_available(),
            "supported_content_types": getattr(instance, 'supported_content_types', ["ebook", "audiobook"]),
        })
    return result


def get_source_display_name(name: str) -> str:
    """Get display name for a source by its identifier."""
    if name in _SOURCES:
        return _SOURCES[name]().display_name
    return name.replace('_', ' ').title()


# Import source implementations to trigger registration
# These must be imported AFTER the base classes and registry are defined
from cwa_book_downloader.release_sources import direct_download  # noqa: F401, E402
from cwa_book_downloader.release_sources import prowlarr  # noqa: F401, E402
from cwa_book_downloader.release_sources import irc  # noqa: F401, E402
