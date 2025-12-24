"""
Download client infrastructure for Prowlarr integration.

This module provides:
- DownloadStatus: Status dataclass for external download progress
- DownloadClient: Abstract base class for download clients
- Client registry and factory functions

Clients register themselves via the @register_client decorator.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Type


@dataclass
class DownloadStatus:
    """Status of an external download."""

    progress: float  # 0-100
    state: str  # "downloading", "complete", "error", "seeding", "paused", "queued"
    message: Optional[str]  # Status message
    complete: bool  # True when download finished
    file_path: Optional[str]  # Path in client's download dir (when complete)
    download_speed: Optional[int] = None  # Bytes per second
    eta: Optional[int] = None  # Seconds remaining


class DownloadClient(ABC):
    """
    Base class for external download clients.

    Subclasses implement protocol-specific download management
    (e.g., qBittorrent for torrents, NZBGet for usenet).
    """

    protocol: str  # "torrent" or "usenet"
    name: str  # "qbittorrent" or "nzbget"

    @staticmethod
    @abstractmethod
    def is_configured() -> bool:
        """
        Check if this client is configured.

        Returns:
            True if required settings (URL, etc.) are present.
        """
        pass

    @abstractmethod
    def test_connection(self) -> Tuple[bool, str]:
        """
        Test connectivity to the client.

        Returns:
            Tuple of (success, message).
        """
        pass

    @abstractmethod
    def add_download(self, url: str, name: str, category: str = "cwabd") -> str:
        """
        Add a download to the client.

        Args:
            url: Download URL (magnet link, .torrent URL, or NZB URL)
            name: Display name for the download
            category: Category/label for organization

        Returns:
            Client-specific download ID (hash for torrents, ID for NZBGet).

        Raises:
            Exception: If adding fails.
        """
        pass

    @abstractmethod
    def get_status(self, download_id: str) -> DownloadStatus:
        """
        Get status of a download.

        Args:
            download_id: The ID returned by add_download()

        Returns:
            Current download status.
        """
        pass

    @abstractmethod
    def remove(self, download_id: str, delete_files: bool = False) -> bool:
        """
        Remove a download from the client.

        Args:
            download_id: The ID returned by add_download()
            delete_files: Whether to also delete downloaded files

        Returns:
            True if removal succeeded.
        """
        pass

    @abstractmethod
    def get_download_path(self, download_id: str) -> Optional[str]:
        """
        Get the path where files were downloaded.

        Args:
            download_id: The ID returned by add_download()

        Returns:
            File or directory path, or None if not available.
        """
        pass

    def find_existing(self, url: str) -> Optional[Tuple[str, DownloadStatus]]:
        """
        Check if a download for this URL already exists in the client.

        This is useful for detecting already-completed downloads so we can
        skip re-downloading and just copy the existing file.

        Args:
            url: Download URL (magnet link, .torrent URL, or NZB URL)

        Returns:
            Tuple of (download_id, status) if found, None if not found.
            Default implementation returns None.
        """
        return None


# Client registry: protocol -> list of client classes
_CLIENTS: Dict[str, List[Type[DownloadClient]]] = {}


def register_client(protocol: str):
    """
    Decorator to register a download client for a protocol.

    Multiple clients can be registered for the same protocol.
    The `is_configured()` method determines which one is active.

    Args:
        protocol: The protocol this client handles ("torrent" or "usenet")

    Example:
        @register_client("torrent")
        class QBittorrentClient(DownloadClient):
            ...
    """

    def decorator(cls: Type[DownloadClient]) -> Type[DownloadClient]:
        if protocol not in _CLIENTS:
            _CLIENTS[protocol] = []
        _CLIENTS[protocol].append(cls)
        return cls

    return decorator


def get_client(protocol: str) -> Optional[DownloadClient]:
    """
    Get a configured client instance for the given protocol.

    Iterates through all registered clients for the protocol and
    returns the first one that is configured.

    Args:
        protocol: "torrent" or "usenet"

    Returns:
        Configured client instance, or None if not available/configured.
    """
    if protocol not in _CLIENTS:
        return None

    for client_cls in _CLIENTS[protocol]:
        if client_cls.is_configured():
            return client_cls()

    return None


def list_configured_clients() -> List[str]:
    """
    List protocols that have configured clients.

    Returns:
        List of protocol names (e.g., ["torrent", "usenet"]).
    """
    result = []
    for protocol, client_classes in _CLIENTS.items():
        for cls in client_classes:
            if cls.is_configured():
                result.append(protocol)
                break
    return result


def get_all_clients() -> Dict[str, List[Type[DownloadClient]]]:
    """
    Get all registered client classes.

    Returns:
        Dict of protocol -> list of client classes.
    """
    return dict(_CLIENTS)


# Import client implementations to trigger registration
# These imports are at the bottom to avoid circular imports
from cwa_book_downloader.release_sources.prowlarr.clients import qbittorrent  # noqa: F401, E402
from cwa_book_downloader.release_sources.prowlarr.clients import nzbget  # noqa: F401, E402
from cwa_book_downloader.release_sources.prowlarr.clients import sabnzbd  # noqa: F401, E402
from cwa_book_downloader.release_sources.prowlarr.clients import transmission  # noqa: F401, E402
from cwa_book_downloader.release_sources.prowlarr.clients import deluge  # noqa: F401, E402
