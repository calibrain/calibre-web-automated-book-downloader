"""Release source plugin system - base classes and registry."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from importlib import import_module
from typing import TYPE_CHECKING, Any, ClassVar, Literal

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path
    from threading import Event

    from shelfmark.core.models import DownloadTask
    from shelfmark.core.search_plan import ReleaseSearchPlan
    from shelfmark.download.postprocess.packs import PackFile

from shelfmark.metadata_providers import BookMetadata


class ReleaseProtocol(StrEnum):
    """Protocol for downloading a release."""

    HTTP = "http"  # Direct HTTP download
    TORRENT = "torrent"  # BitTorrent
    NZB = "nzb"  # Usenet NZB
    DCC = "dcc"  # IRC DCC


class SourceUnavailableError(Exception):
    """Raised when a source is configured but currently unreachable."""


@dataclass
class BrowseRecord:
    """Source-native browse/search record used before normalization to Release."""

    id: str
    title: str
    source: str
    preview: str | None = None
    author: str | None = None
    publisher: str | None = None
    year: str | None = None
    language: str | None = None
    content: str | None = None
    format: str | None = None
    size: str | None = None
    info: dict[str, list[str]] | None = None
    description: str | None = None
    download_urls: list[str] = field(default_factory=list)
    download_path: str | None = None
    priority: int = 0
    progress: float | None = None
    status_message: str | None = None
    added_time: float | None = None
    source_url: str | None = None


@dataclass
class Release:
    """A downloadable release - all sources return this same structure."""

    source: str  # "direct", "prowlarr", "irc", etc.
    source_id: str  # ID within that source
    title: str
    format: str | None = None
    language: str | None = None  # ISO 639-1 code (e.g., "en", "de", "fr")
    size: str | None = None
    size_bytes: int | None = None
    download_url: str | None = None
    info_url: str | None = None  # Link to release info page (e.g., tracker) - makes title clickable
    protocol: ReleaseProtocol | None = None
    indexer: str | None = None  # Source name for display
    seeders: int | None = None  # For torrents
    peers: str | None = None  # For torrents: "seeders/leechers" display string
    content_type: str | None = None  # "ebook" or "audiobook" - preserved from search
    extra: dict = field(default_factory=dict)  # Source-specific metadata


@dataclass
class DownloadProgress:
    """DEPRECATED: Use progress_callback and status_callback instead."""

    status: str  # "queued", "resolving", "downloading", "complete", "failed"
    progress: float  # 0-100
    status_message: str | None = None
    download_speed: int | None = None
    eta: int | None = None
    save_path: str | None = None


# --- Column Schema for Plugin-Driven UI ---


class ColumnRenderType(StrEnum):
    """How the frontend should render the column value."""

    TEXT = "text"  # Plain text
    BADGE = "badge"  # Colored badge (format, language)
    TAGS = "tags"  # List of colored badges
    SIZE = "size"  # File size formatting
    NUMBER = "number"  # Numeric value
    PEERS = "peers"  # Peers display: "S/L" with color based on seeder count
    INDEXER_PROTOCOL = "indexer_protocol"  # Text + colored dot for torrent/usenet
    FLAG_ICON = "flag_icon"  # Icon with tooltip (VIP, freeleech, etc.)
    FORMAT_CONTENT_TYPE = "format_content_type"  # Content type icon + format badge


class ColumnAlign(StrEnum):
    """Column alignment options."""

    LEFT = "left"
    CENTER = "center"
    RIGHT = "right"


@dataclass
class ColumnColorHint:
    """Color hint for badge-type columns."""

    type: Literal["map", "static"]  # "map" uses frontend colorMaps, "static" is fixed class
    value: str  # Map name ("format", "language") or Tailwind class


@dataclass
class ColumnSchema:
    """Definition for a single column in the release list."""

    key: str  # Data path (e.g., "format", "extra.language")
    label: str  # Accessibility label
    render_type: ColumnRenderType = ColumnRenderType.TEXT
    align: ColumnAlign = ColumnAlign.LEFT
    width: str = "auto"  # CSS width (e.g., "80px", "minmax(0,2fr)")
    hide_mobile: bool = False  # Hide on small screens
    color_hint: ColumnColorHint | None = None  # For BADGE render type
    fallback: str = "-"  # Value to show when data is missing
    uppercase: bool = False  # Force uppercase display
    sortable: bool = False  # Show in sort dropdown (opt-in)
    sort_key: str | None = None  # Field to sort by (defaults to `key` if None)


class LeadingCellType(StrEnum):
    """Type of leading cell to display in release rows."""

    THUMBNAIL = "thumbnail"  # Show book cover image
    BADGE = "badge"  # Show colored badge (e.g., "Torrent", "Usenet")
    NONE = "none"  # No leading cell


@dataclass
class LeadingCellConfig:
    """Configuration for the leading cell in release rows."""

    type: LeadingCellType = LeadingCellType.THUMBNAIL
    key: str | None = None  # Field path for data (e.g., "extra.preview" or "extra.download_type")
    color_hint: ColumnColorHint | None = None  # For badge type - maps values to colors
    uppercase: bool = False  # Force uppercase for badge text


@dataclass
class SortOption:
    """A sort option that appears in the sort dropdown without being tied to a column."""

    label: str  # Display label in the sort dropdown
    sort_key: str  # Field to sort by on the Release object
    default_direction: Literal["asc", "desc"] = "desc"  # Which way "best first" runs


@dataclass
class SourceActionButton:
    """Action button configuration for a release source."""

    label: str  # Button text (e.g., "Refresh search")
    action: str = "expand"  # Action type: "expand" triggers expand_search


@dataclass
class ReleaseColumnConfig:
    """Complete column configuration for a release source."""

    columns: list[ColumnSchema]
    grid_template: str = "minmax(0,2fr) 60px 80px 80px"  # CSS grid-template-columns
    leading_cell: LeadingCellConfig | None = None  # Defaults to thumbnail mode if None
    online_servers: list[str] | None = None  # For IRC: list of currently online server nicks
    available_indexers: list[str] | None = None  # For Prowlarr: list of all enabled indexer names
    default_indexers: list[str] | None = (
        None  # For Prowlarr: indexers selected in settings (pre-selected in filter)
    )
    cache_ttl_seconds: int | None = None  # How long to cache results (default: 5 min)
    supported_filters: list[str] | None = (
        None  # Which filters this source supports: ["format", "language", "indexer"]
    )
    extra_sort_options: list[SortOption] | None = (
        None  # Additional sort options not tied to a column
    )
    action_button: SourceActionButton | None = (
        None  # Custom action button (replaces default expand search)
    )


def serialize_column_config(config: ReleaseColumnConfig) -> dict[str, Any]:
    """Serialize column configuration for API response."""
    result: dict[str, Any] = {
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
                    "value": col.color_hint.value,
                }
                if col.color_hint
                else None,
                "fallback": col.fallback,
                "uppercase": col.uppercase,
                "sortable": col.sortable,
                "sort_key": col.sort_key,
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
                "value": config.leading_cell.color_hint.value,
            }
            if config.leading_cell.color_hint
            else None,
            "uppercase": config.leading_cell.uppercase,
        }

    # Include online_servers if provided (e.g., for IRC source)
    if config.online_servers is not None:
        result["online_servers"] = config.online_servers

    # Include available_indexers if provided (e.g., for Prowlarr source)
    if config.available_indexers is not None:
        result["available_indexers"] = config.available_indexers

    # Include default_indexers if provided (indexers selected in settings, for pre-selection)
    if config.default_indexers is not None:
        result["default_indexers"] = config.default_indexers

    # Include cache TTL if specified (sources can request longer caching)
    if config.cache_ttl_seconds is not None:
        result["cache_ttl_seconds"] = config.cache_ttl_seconds

    # Include supported filters (sources declare which filters they support)
    if config.supported_filters is not None:
        result["supported_filters"] = config.supported_filters

    # Include extra sort options (sort entries not tied to a column)
    if config.extra_sort_options:
        result["extra_sort_options"] = [
            {
                "label": opt.label,
                "sort_key": opt.sort_key,
                "default_direction": opt.default_direction,
            }
            for opt in config.extra_sort_options
        ]

    # Include action button if specified (replaces default expand search)
    if config.action_button is not None:
        result["action_button"] = {
            "label": config.action_button.label,
            "action": config.action_button.action,
        }

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

    name: str  # "direct", "prowlarr"
    display_name: str  # "Direct Download", "Prowlarr"
    supported_content_types: ClassVar[list[str]] = [
        "ebook",
        "audiobook",
    ]  # Content types this source supports
    can_be_default: bool = True  # Whether this source can be selected as default in settings

    @abstractmethod
    def search(
        self,
        book: BookMetadata,
        plan: ReleaseSearchPlan,
        *,
        expand_search: bool = False,
        content_type: str = "ebook",
    ) -> list[Release]:
        """Search for releases of a book."""

    @abstractmethod
    def is_available(self) -> bool:
        """Check if this source is configured and reachable."""

    def get_column_config(self) -> ReleaseColumnConfig:
        """Get column configuration for release list UI. Override for custom columns."""
        return _default_column_config()

    def get_record(
        self,
        record_id: str,
        *,
        fetch_download_count: bool = True,
    ) -> BrowseRecord | None:
        """Resolve a source-native record for browse flows."""
        msg = f"{self.display_name} does not support record lookup"
        raise NotImplementedError(msg)

    def search_results_are_releases(self) -> bool:
        """Whether source-native browse results already represent concrete releases."""
        return False

    def get_destination_override(self, task: DownloadTask) -> Path | None:
        """Return a source-specific destination override for a queued download."""
        return None


@dataclass(frozen=True)
class HandoffResult:
    """An external handoff that completed without a Shelfmark book payload."""

    path: str
    message: str


class DownloadHandler(ABC):
    """Interface for executing downloads.

    A handler may either:
    - download directly into ``TMP_DIR`` (managed by Shelfmark), or
    - return a path owned by an external client (e.g. torrent/usenet).
    - finish an external handoff without producing a book payload.

    The orchestrator is responsible for post-processing (archive extraction, output mode
    handling) and transferring files into their final destination.
    """

    @abstractmethod
    def download(
        self,
        task: DownloadTask,
        cancel_flag: Event,
        progress_callback: Callable[[float], None],
        status_callback: Callable[[str, str | None], None],
    ) -> str | HandoffResult | None:
        """Execute download and return a payload path or completed external handoff."""

    def post_process_cleanup(self, task: DownloadTask, *, success: bool) -> None:
        """Run optional cleanup after orchestrator post-processing.

        This is primarily used for external download clients, where the handler may need
        to trigger client-side cleanup only after Shelfmark has safely imported the files.
        """
        return

    def build_retry_resolution_fields(self, release_data: dict[str, Any]) -> dict[str, Any]:
        """Return private queue-time fields needed for restart-safe retry."""
        return {}

    def list_files(self, release_data: dict[str, Any]) -> list[PackFile] | None:
        """Return the release's file list without downloading it.

        Lets the UI review a multi-book pack before queueing. Return None when the
        source cannot know the files ahead of time (magnet links, usenet, ...).
        """
        return None

    @abstractmethod
    def cancel(self, task_id: str) -> bool:
        """Cancel an in-progress download."""


# --- Registry ---

_SOURCES: dict[str, type[ReleaseSource]] = {}
_HANDLERS: dict[str, type[DownloadHandler]] = {}
_BUILTIN_SOURCE_MODULES = (
    "shelfmark.release_sources.audiobookbay",
    "shelfmark.release_sources.direct_download",
    "shelfmark.release_sources.irc",
    "shelfmark.release_sources.newznab",
    "shelfmark.release_sources.prowlarr",
)
_builtin_source_state = {"loaded": False}


def _ensure_builtin_sources_registered() -> None:
    """Import built-in source modules once to populate source registries."""
    if _builtin_source_state["loaded"]:
        return

    for module_name in _BUILTIN_SOURCE_MODULES:
        import_module(module_name)

    _builtin_source_state["loaded"] = True


def register_source(
    name: str,
) -> Callable[[type[ReleaseSource]], type[ReleaseSource]]:
    """Register a release source."""

    def decorator(cls: type[ReleaseSource]) -> type[ReleaseSource]:
        _SOURCES[name] = cls
        return cls

    return decorator


def register_handler(
    name: str,
) -> Callable[[type[DownloadHandler]], type[DownloadHandler]]:
    """Register a download handler."""

    def decorator(cls: type[DownloadHandler]) -> type[DownloadHandler]:
        _HANDLERS[name] = cls
        return cls

    return decorator


def get_source(name: str) -> ReleaseSource:
    """Get a release source instance by name."""
    _ensure_builtin_sources_registered()
    if name not in _SOURCES:
        msg = f"Unknown release source: {name}"
        raise ValueError(msg)
    return _SOURCES[name]()


def get_handler(name: str) -> DownloadHandler:
    """Get a download handler instance by name."""
    _ensure_builtin_sources_registered()
    if name not in _HANDLERS:
        msg = f"Unknown download handler: {name}"
        raise ValueError(msg)
    return _HANDLERS[name]()


def list_available_sources() -> list[dict]:
    """List all registered sources with their availability status."""
    _ensure_builtin_sources_registered()
    result = []
    for name, src_class in _SOURCES.items():
        instance = src_class()
        result.append(
            {
                "name": name,
                "display_name": instance.display_name,
                "enabled": instance.is_available(),
                "supported_content_types": getattr(
                    instance, "supported_content_types", ["ebook", "audiobook"]
                ),
                "browse_results_are_releases": instance.search_results_are_releases(),
                "can_be_default": getattr(instance, "can_be_default", True),
            }
        )
    return result


def get_source_display_name(name: str) -> str:
    """Get display name for a source by its identifier."""
    _ensure_builtin_sources_registered()
    if name in _SOURCES:
        return _SOURCES[name]().display_name
    return name.replace("_", " ").title()


def browse_record_to_book_metadata(
    record: BrowseRecord,
    *,
    title_override: str | None = None,
    author_override: str | None = None,
) -> BookMetadata:
    """Convert a source-native browse record into generic book metadata."""
    resolved_title = title_override or str(record.title or "").strip() or "Unknown title"
    resolved_author = author_override or str(record.author or "").strip()
    # `author_override` is the frontend's display string, `authors.join(', ')` - every
    # contributor, translators included. The split below is the only place that knows the
    # commas were joins rather than part of a name, so `search_author` is taken from it
    # rather than from the joined text. See issue #1252.
    authors = [part.strip() for part in resolved_author.split(",") if part.strip()]
    publish_year = None

    if isinstance(record.year, int):
        publish_year = record.year
    elif isinstance(record.year, str):
        normalized_year = record.year.strip()
        if normalized_year.isdigit():
            publish_year = int(normalized_year)

    return BookMetadata(
        provider=record.source,
        provider_id=record.id,
        provider_display_name=get_source_display_name(record.source),
        title=resolved_title,
        search_title=resolved_title,
        search_author=authors[0] if authors else None,
        authors=authors,
        cover_url=record.preview,
        description=record.description,
        publisher=record.publisher,
        publish_year=publish_year,
        language=record.language,
        source_url=record.source_url,
    )


def source_results_are_releases(name: str) -> bool:
    """Whether a source's browse/search results already map to concrete releases."""
    _ensure_builtin_sources_registered()
    if name not in _SOURCES:
        return False
    return _SOURCES[name]().search_results_are_releases()


_ensure_builtin_sources_registered()
