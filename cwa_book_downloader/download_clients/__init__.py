"""External download client integrations (qBittorrent, SABnzbd, etc.)."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional, Tuple
from enum import Enum


class DownloadStatus(Enum):
    """Status of a download in an external client."""
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    SEEDING = "seeding"  # Torrents only


@dataclass
class ClientDownloadProgress:
    """Progress info from external download client."""
    status: DownloadStatus
    progress: float                  # 0-100
    download_speed: Optional[int]    # bytes/sec
    eta: Optional[int]               # seconds remaining
    save_path: Optional[str]         # Where the file will be/is


class DownloadClient(ABC):
    """Abstract base class for download clients."""

    @abstractmethod
    def add_download(self, url: str, title: str) -> str:
        """Add a download (torrent/magnet or NZB URL). Returns download ID for tracking."""
        pass

    @abstractmethod
    def get_download(self, download_id: str) -> Optional[ClientDownloadProgress]:
        """Get progress of a specific download."""
        pass

    @abstractmethod
    def list_downloads(self) -> List[Tuple[str, ClientDownloadProgress]]:
        """List all downloads with their progress."""
        pass

    @abstractmethod
    def get_completed_path(self, download_id: str) -> Optional[str]:
        """Get the path to completed download."""
        pass

    @abstractmethod
    def test_connection(self) -> bool:
        """Test if the client is reachable and credentials are valid."""
        pass
