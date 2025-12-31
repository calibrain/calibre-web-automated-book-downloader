"""Data structures and models used across the application."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional
from enum import Enum
import re
import time


def build_filename(
    title: str,
    author: Optional[str] = None,
    year: Optional[str] = None,
    fmt: Optional[str] = None,
) -> str:
    """Build sanitized filename: 'Author - Title (Year).format'

    Args:
        title: Book title (required)
        author: Book author
        year: Publication year
        fmt: File format/extension

    Returns:
        Sanitized filename safe for filesystem use
    """
    parts = []
    if author:
        parts.append(author)
        parts.append(" - ")
    parts.append(title)
    if year:
        parts.append(f" ({year})")

    filename = "".join(parts)
    filename = re.sub(r'[\\/:*?"<>|]', '_', filename.strip())[:245]

    if fmt:
        filename = f"{filename}.{fmt}"

    return filename


class QueueStatus(str, Enum):
    """Enum for possible book queue statuses."""
    QUEUED = "queued"
    RESOLVING = "resolving"
    DOWNLOADING = "downloading"
    COMPLETE = "complete"
    AVAILABLE = "available"
    ERROR = "error"
    DONE = "done"
    CANCELLED = "cancelled"


@dataclass
class QueueItem:
    """Queue item with priority and metadata."""
    book_id: str
    priority: int
    added_time: float

    def __lt__(self, other):
        """Compare items for priority queue (lower priority number = higher precedence)."""
        if self.priority != other.priority:
            return self.priority < other.priority
        return self.added_time < other.added_time


@dataclass
class DownloadTask:
    """Source-agnostic download task for the queue.

    This replaces BookInfo in the queue, providing a unified interface
    for both Direct Download and Universal modes. The handler uses task_id
    to fetch whatever source-specific data it needs internally.
    """
    task_id: str                                # Unique ID (e.g., AA MD5 hash, Prowlarr GUID)
    source: str                                 # Handler name ("direct_download", "prowlarr")
    title: str                                  # Display title for queue sidebar

    # Display info for queue sidebar
    author: Optional[str] = None
    year: Optional[str] = None
    format: Optional[str] = None
    size: Optional[str] = None
    preview: Optional[str] = None
    content_type: Optional[str] = None  # "book (fiction)", "audiobook", "magazine", etc.

    # Series info (for library naming templates)
    series_name: Optional[str] = None
    series_position: Optional[float] = None  # Float for novellas (e.g., 1.5)

    # Hardlinking support
    original_download_path: Optional[str] = None  # Path in download client (for hardlinking)

    # Runtime state
    priority: int = 0
    added_time: float = field(default_factory=time.time)
    progress: float = 0.0
    status: QueueStatus = QueueStatus.QUEUED
    status_message: Optional[str] = None
    download_path: Optional[str] = None

    def __lt__(self, other):
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
class BookInfo:
    """Data class representing book information."""
    id: str
    title: str
    preview: Optional[str] = None
    author: Optional[str] = None
    publisher: Optional[str] = None
    year: Optional[str] = None
    language: Optional[str] = None
    content: Optional[str] = None
    format: Optional[str] = None
    size: Optional[str] = None
    info: Optional[Dict[str, List[str]]] = None
    description: Optional[str] = None
    download_urls: List[str] = field(default_factory=list)
    download_path: Optional[str] = None
    priority: int = 0
    progress: Optional[float] = None
    status_message: Optional[str] = None  # Detailed status message for UI display
    added_time: Optional[float] = None  # Timestamp when added to queue
    source: str = "direct_download"  # Release source handler to use for downloads
    source_url: Optional[str] = None  # Link to source page (e.g., Anna's Archive)

    def get_filename(self, fallback_url: Optional[str] = None) -> str:
        """Build sanitized filename: 'Author - Title (Year).format'

        Resolves format from self.format, download_urls, or fallback_url.

        Args:
            fallback_url: URL to extract format from if not already known

        Returns:
            Sanitized filename safe for filesystem use
        """
        # Resolve format if needed
        if not self.format:
            for url in (self.download_urls[0] if self.download_urls else None, fallback_url):
                if url:
                    ext = url.split(".")[-1].lower()
                    if ext and len(ext) <= 5 and ext.isalnum():
                        self.format = ext
                        break

        return build_filename(self.title, self.author, self.year, self.format)


@dataclass
class SearchFilters:
    """Filters for book search queries."""
    isbn: Optional[List[str]] = None
    author: Optional[List[str]] = None
    title: Optional[List[str]] = None
    lang: Optional[List[str]] = None
    sort: Optional[str] = None
    content: Optional[List[str]] = None
    format: Optional[List[str]] = None
