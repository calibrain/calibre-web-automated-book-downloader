"""
Transmission download client for Prowlarr integration.

Uses the transmission-rpc library to communicate with Transmission's RPC API.
"""

from typing import Optional, Tuple

from cwa_book_downloader.core.config import config
from cwa_book_downloader.core.logger import setup_logger
from cwa_book_downloader.release_sources.prowlarr.clients import (
    DownloadClient,
    DownloadStatus,
    register_client,
)
from cwa_book_downloader.release_sources.prowlarr.clients.torrent_utils import (
    extract_torrent_info,
    parse_transmission_url,
)

logger = setup_logger(__name__)


@register_client("torrent")
class TransmissionClient(DownloadClient):
    """Transmission download client using transmission-rpc library."""

    protocol = "torrent"
    name = "transmission"

    def __init__(self):
        """Initialize Transmission client with settings from config."""
        from transmission_rpc import Client

        url = config.get("TRANSMISSION_URL", "")
        if not url:
            raise ValueError("TRANSMISSION_URL is required")

        username = config.get("TRANSMISSION_USERNAME", "")
        password = config.get("TRANSMISSION_PASSWORD", "")

        # Parse URL to extract host, port, and path
        host, port, path = parse_transmission_url(url)

        self._client = Client(
            host=host,
            port=port,
            path=path,
            username=username if username else None,
            password=password if password else None,
        )
        self._category = config.get("TRANSMISSION_CATEGORY", "cwabd")

    @staticmethod
    def is_configured() -> bool:
        """Check if Transmission is configured and selected as the torrent client."""
        client = config.get("PROWLARR_TORRENT_CLIENT", "")
        url = config.get("TRANSMISSION_URL", "")
        return client == "transmission" and bool(url)

    def test_connection(self) -> Tuple[bool, str]:
        """Test connection to Transmission."""
        try:
            session = self._client.get_session()
            version = session.version
            return True, f"Connected to Transmission {version}"
        except Exception as e:
            return False, f"Connection failed: {str(e)}"

    def add_download(self, url: str, name: str, category: str = None) -> str:
        """
        Add torrent by URL (magnet or .torrent).

        Args:
            url: Magnet link or .torrent URL
            name: Display name for the torrent
            category: Category for organization (uses configured default if not specified)

        Returns:
            Torrent hash (info_hash).

        Raises:
            Exception: If adding fails.
        """
        try:
            category = category or self._category

            torrent_info = extract_torrent_info(url)

            if torrent_info.torrent_data:
                torrent = self._client.add_torrent(
                    torrent=torrent_info.torrent_data,
                    labels=[category],
                )
            else:
                # Use magnet URL if available, otherwise original URL
                add_url = torrent_info.magnet_url or url
                torrent = self._client.add_torrent(
                    torrent=add_url,
                    labels=[category],
                )

            torrent_hash = torrent.hashString.lower()
            logger.info(f"Added torrent to Transmission: {torrent_hash}")

            return torrent_hash

        except Exception as e:
            logger.error(f"Transmission add failed: {e}")
            raise

    def get_status(self, download_id: str) -> DownloadStatus:
        """
        Get torrent status by hash.

        Args:
            download_id: Torrent info_hash

        Returns:
            Current download status.
        """
        try:
            torrent = self._client.get_torrent(download_id)

            # Transmission status values:
            # 0: stopped
            # 1: check pending
            # 2: checking
            # 3: download pending
            # 4: downloading
            # 5: seed pending
            # 6: seeding
            # torrent.status is an enum with .value as string
            status_value = torrent.status.value if hasattr(torrent.status, 'value') else str(torrent.status)
            status_map = {
                "stopped": ("paused", "Paused"),
                "check pending": ("checking", "Waiting to check"),
                "checking": ("checking", "Checking files"),
                "download pending": ("queued", "Waiting to download"),
                "downloading": ("downloading", "Downloading"),
                "seed pending": ("processing", "Moving files"),
                "seeding": ("seeding", "Seeding"),
            }

            state, message = status_map.get(status_value, ("downloading", "Downloading"))
            progress = torrent.percent_done * 100
            # Only mark complete when seeding - seed pending means files still being moved
            complete = progress >= 100 and status_value == "seeding"

            if complete:
                message = "Complete"

            # Get ETA if available and reasonable (less than 1 week)
            eta = None
            if hasattr(torrent, 'eta') and torrent.eta:
                eta_seconds = torrent.eta.total_seconds()
                if 0 < eta_seconds < 604800:
                    eta = int(eta_seconds)

            # Get download speed
            download_speed = torrent.rate_download if hasattr(torrent, 'rate_download') else None

            # Get file path for completed downloads
            file_path = None
            if complete:
                download_dir = torrent.download_dir
                name = torrent.name
                file_path = f"{download_dir}/{name}"

            return DownloadStatus(
                progress=progress,
                state="complete" if complete else state,
                message=message,
                complete=complete,
                file_path=file_path,
                download_speed=download_speed,
                eta=eta,
            )

        except KeyError:
            return DownloadStatus.error("Torrent not found")
        except Exception as e:
            error_type = type(e).__name__
            logger.error(f"Transmission get_status failed ({error_type}): {e}")
            return DownloadStatus.error(f"{error_type}: {e}")

    def remove(self, download_id: str, delete_files: bool = False) -> bool:
        """
        Remove a torrent from Transmission.

        Args:
            download_id: Torrent info_hash
            delete_files: Whether to also delete files

        Returns:
            True if successful.
        """
        try:
            self._client.remove_torrent(
                download_id,
                delete_data=delete_files,
            )
            logger.info(
                f"Removed torrent from Transmission: {download_id}"
                + (" (with files)" if delete_files else "")
            )
            return True
        except Exception as e:
            error_type = type(e).__name__
            logger.error(f"Transmission remove failed ({error_type}): {e}")
            return False

    def get_download_path(self, download_id: str) -> Optional[str]:
        """
        Get the path where torrent files are located.

        Args:
            download_id: Torrent info_hash

        Returns:
            Content path (file or directory), or None.
        """
        try:
            torrent = self._client.get_torrent(download_id)
            download_dir = torrent.download_dir
            name = torrent.name
            return f"{download_dir}/{name}"
        except Exception as e:
            error_type = type(e).__name__
            logger.debug(f"Transmission get_download_path failed ({error_type}): {e}")
            return None

    def find_existing(self, url: str) -> Optional[Tuple[str, DownloadStatus]]:
        """Check if a torrent for this URL already exists in Transmission."""
        try:
            torrent_info = extract_torrent_info(url)
            if not torrent_info.info_hash:
                return None

            try:
                self._client.get_torrent(torrent_info.info_hash)
                status = self.get_status(torrent_info.info_hash)
                return (torrent_info.info_hash, status)
            except KeyError:
                return None
        except Exception as e:
            logger.debug(f"Error checking for existing torrent: {e}")
            return None
