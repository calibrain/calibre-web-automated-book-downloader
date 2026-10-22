"""Data structures and models used across the application."""

import re
import time
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

_FORMAT_RE = re.compile(r"[a-z0-9]{1,10}")


def normalize_format(fmt: object) -> str | None:
    """Return ``fmt`` as a lower-case plain file extension, or None if it is not one.

    Accepts only 1 to 10 ASCII letters or digits (epub, mobi, m4b, azw3, cbz...). Anything
    else, including separators, dots and control characters, is rejected rather than
    cleaned, because the value ends up in a staging file name.
    """
    if not isinstance(fmt, str):
        return None
    candidate = fmt.strip().lstrip(".").lower()
    return candidate if _FORMAT_RE.fullmatch(candidate) else None


def resolve_staged_path(base_dir: Path, name: str) -> Path:
    """Return ``base_dir / name`` after checking it is a direct child of ``base_dir``.

    Raises ValueError when ``name`` would land anywhere else (separators, ``..``, a
    symlink pointing out, or the directory itself).
    """
    resolved_base = base_dir.resolve()
    path = base_dir / name
    if not name or path.resolve().parent != resolved_base:
        raise ValueError(f"Refusing staging path outside {base_dir}")
    return path


def build_filename(
    title: str,
    author: str | None = None,
    year: str | None = None,
    fmt: str | None = None,
) -> str:
    """Build a filesystem-safe filename from book metadata."""
    parts = []
    if author:
        parts.append(author)
        parts.append(" - ")
    parts.append(title)
    if year:
        parts.append(f" ({year})")

    filename = "".join(parts)
    filename = re.sub(r'[\\/:*?"<>|]', "_", filename.strip())[:245]

    safe_fmt = normalize_format(fmt)
    if safe_fmt:
        filename = f"{filename}.{safe_fmt}"

    return filename


class QueueStatus(StrEnum):
    """Enum for possible book queue statuses."""

    QUEUED = "queued"
    RESOLVING = "resolving"
    LOCATING = "locating"
    DOWNLOADING = "downloading"
    COMPLETE = "complete"
    ERROR = "error"
    CANCELLED = "cancelled"


TERMINAL_QUEUE_STATUSES: frozenset[QueueStatus] = frozenset(
    {
        QueueStatus.COMPLETE,
        QueueStatus.ERROR,
        QueueStatus.CANCELLED,
    }
)

ACTIVE_QUEUE_STATUSES: frozenset[QueueStatus] = frozenset(
    {
        QueueStatus.QUEUED,
        QueueStatus.RESOLVING,
        QueueStatus.LOCATING,
        QueueStatus.DOWNLOADING,
    }
)


class SearchMode(StrEnum):
    """Search modes supported by the Shelfmark UI and API."""

    DIRECT = "direct"
    UNIVERSAL = "universal"


@dataclass
class QueueItem:
    """Queue item with priority and metadata."""

    book_id: str
    priority: int
    added_time: float

    def __lt__(self, other: QueueItem) -> bool:
        """Compare items for priority queue (lower priority number = higher precedence)."""
        if self.priority != other.priority:
            return self.priority < other.priority
        return self.added_time < other.added_time


@dataclass
class DownloadTask:
    """Mutable download task state tracked throughout the pipeline."""

    task_id: str  # Unique ID (e.g., AA MD5 hash, Prowlarr GUID)
    source: str  # Handler name ("direct_download", "prowlarr")
    title: str  # Display title for queue sidebar

    # Display info for queue sidebar
    author: str | None = None
    year: str | None = None
    format: str | None = None
    size: str | None = None
    downloads: int | None = None  # Download count from source
    preview: str | None = None
    content_type: str | None = None  # "book (fiction)", "audiobook", "magazine", etc.
    source_url: str | None = None  # Original release URL used by source-specific handlers
    retry_download_url: str | None = None  # Resolved download URL for restart-safe retries
    retry_download_protocol: str | None = (
        None  # Protocol for retry_download_url (e.g. torrent, usenet)
    )
    retry_release_name: str | None = None  # Display name to send back to external download clients
    retry_expected_hash: str | None = None  # Optional torrent hash used to match client downloads
    retry_ratio_limit: float | None = None  # Optional post-download seeding ratio
    retry_seeding_time_limit_minutes: int | None = None  # Optional post-download seeding time limit
    retry_source_context: dict[str, Any] = field(
        default_factory=dict
    )  # Source-private context for retry/re-resolution
    can_retry_without_staged_source: bool = (
        True  # Whether the source can restart without a preserved staged file
    )

    # Series info (for library naming templates)
    series_name: str | None = None
    series_position: float | None = None  # Float for novellas (e.g., 1.5)
    subtitle: str | None = None  # Book subtitle for naming templates
    narrator: str | None = None  # Release narrator for the {Narrator} template variable
    language: str | None = None  # Release language code for the {Language} template variable

    # Hardlinking support
    original_download_path: str | None = None  # Path in download client (for hardlinking)

    # Search mode - determines post-download processing behavior
    # See SearchMode enum for behavioral differences
    search_mode: SearchMode | None = None

    # Output selection for post-processing.
    # This is captured at queue time so in-flight tasks are not affected if the user changes settings later.
    output_mode: str | None = None

    output_args: dict[str, Any] = field(
        default_factory=dict
    )  # Per-output parameters (e.g. email recipient)

    # Multi-book packs: one release holding several books. `book_plan` is the split the
    # user approved before download (list of {title, series_position, year, files});
    # `multi_book` asks post-processing to split heuristically when no plan exists.
    multi_book: bool = False
    book_plan: list[dict[str, Any]] | None = None
    # Release-relative paths the user chose to skip in the pack review.
    ignored_files: list[str] | None = None

    # User association (multi-user support)
    user_id: int | None = None  # DB user ID who queued this download
    username: str | None = None  # Username for {User} template variable
    request_id: int | None = None  # Origin request ID when queued from request fulfilment

    # Runtime state
    priority: int = 0
    added_time: float = field(default_factory=time.time)
    progress: float = 0.0
    status: QueueStatus = QueueStatus.QUEUED
    status_message: str | None = None
    download_path: str | None = None
    # Every file the output step produced, download_path among them. Kept in memory
    # only: history persists download_path alone.
    download_paths: list[str] = field(default_factory=list)
    last_error_message: str | None = None
    last_error_type: str | None = None

    # True while the download client has this task's torrent in its own queue, waiting for a
    # free slot. Such a task does no work yet, so it does not count against its source's limit.
    client_queued: bool = False
    staged_path: str | None = None

    def __lt__(self, other: DownloadTask) -> bool:
        """Compare tasks for priority queue (lower priority number = higher precedence)."""
        if self.priority != other.priority:
            return self.priority < other.priority
        return self.added_time < other.added_time

    def get_filename(self) -> str:
        """Build sanitized filename from task metadata."""
        if self.download_path:
            return Path(self.download_path).name
        return build_filename(self.title, self.author, self.year, self.format)


@dataclass
class SearchFilters:
    """Filters for book search queries."""

    isbn: list[str] | None = None
    author: list[str] | None = None
    title: list[str] | None = None
    lang: list[str] | None = None
    sort: str | None = None
    content: list[str] | None = None
    format: list[str] | None = None
