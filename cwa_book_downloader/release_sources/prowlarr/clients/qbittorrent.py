"""
qBittorrent download client for Prowlarr integration.

Uses the qbittorrent-api library to communicate with qBittorrent's Web API.
"""

import time
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
)

logger = setup_logger(__name__)


@register_client("torrent")
class QBittorrentClient(DownloadClient):
    """qBittorrent download client."""

    protocol = "torrent"
    name = "qbittorrent"

    def __init__(self):
        """Initialize qBittorrent client with settings from config."""
        # Lazy import to avoid dependency issues if not using torrents
        from qbittorrentapi import Client

        url = config.get("QBITTORRENT_URL", "")
        if not url:
            raise ValueError("QBITTORRENT_URL is required")

        self._client = Client(
            host=url,
            username=config.get("QBITTORRENT_USERNAME", ""),
            password=config.get("QBITTORRENT_PASSWORD", ""),
        )
        self._category = config.get("QBITTORRENT_CATEGORY", "cwabd")

    @staticmethod
    def is_configured() -> bool:
        """Check if qBittorrent is configured and selected as the torrent client."""
        client = config.get("PROWLARR_TORRENT_CLIENT", "")
        url = config.get("QBITTORRENT_URL", "")
        return client == "qbittorrent" and bool(url)

    def test_connection(self) -> Tuple[bool, str]:
        """Test connection to qBittorrent."""
        try:
            self._client.auth_log_in()
            version = self._client.app.version
            return True, f"Connected to qBittorrent {version}"
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
            # Use configured category if not explicitly provided
            category = category or self._category

            # Ensure category exists (may already exist, which is fine)
            try:
                self._client.torrents_create_category(name=category)
            except Exception as e:
                # Conflict409Error means category exists - that's expected
                # Log other errors but continue since download may still work
                if "Conflict" not in type(e).__name__ and "409" not in str(e):
                    logger.debug(f"Could not create category '{category}': {type(e).__name__}: {e}")

            torrent_info = extract_torrent_info(url)
            expected_hash = torrent_info.info_hash
            torrent_data = torrent_info.torrent_data

            # Add the torrent - use file content if we have it, otherwise URL
            if torrent_data:
                result = self._client.torrents_add(
                    torrent_files=torrent_data,
                    category=category,
                    rename=name,
                )
            else:
                result = self._client.torrents_add(
                    urls=url,
                    category=category,
                    rename=name,
                )

            logger.debug(f"qBittorrent add result: {result}")

            if result == "Ok.":
                if expected_hash:
                    # We know the hash - verify it was added
                    for attempt in range(10):
                        torrents = self._client.torrents_info(
                            torrent_hashes=expected_hash
                        )
                        if torrents:
                            logger.info(f"Added torrent to qBittorrent: {expected_hash}")
                            return expected_hash
                        time.sleep(0.5)

                    # qBittorrent said Ok, trust it even if we can't find it yet
                    logger.warning(
                        f"qBittorrent returned Ok but torrent not yet visible. "
                        f"Returning expected hash: {expected_hash}"
                    )
                    return expected_hash

                # No hash available - this shouldn't happen often
                raise Exception(
                    "Could not determine torrent hash. "
                    "Try using a magnet link instead."
                )

            raise Exception(f"Failed to add torrent: {result}")
        except Exception as e:
            logger.error(f"qBittorrent add failed: {e}")
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
            torrents = self._client.torrents_info(torrent_hashes=download_id)
            if not torrents:
                return DownloadStatus.error("Torrent not found")

            torrent = torrents[0]

            # Map qBittorrent states to our states and user-friendly messages
            state_info = {
                "downloading": ("downloading", None),  # None = use default progress message
                "stalledDL": ("downloading", "Stalled"),
                "metaDL": ("downloading", "Fetching metadata"),
                "forcedDL": ("downloading", None),
                "allocating": ("downloading", "Allocating space"),
                "uploading": ("seeding", "Seeding"),
                "stalledUP": ("seeding", "Seeding (stalled)"),
                "forcedUP": ("seeding", "Seeding"),
                "pausedDL": ("paused", "Paused"),
                "pausedUP": ("paused", "Paused"),
                "queuedDL": ("queued", "Queued"),
                "queuedUP": ("queued", "Queued"),
                "checkingDL": ("checking", "Checking files"),
                "checkingUP": ("checking", "Checking files"),
                "checkingResumeData": ("checking", "Checking resume data"),
                "moving": ("processing", "Moving files"),
                "error": ("error", "Error"),
                "missingFiles": ("error", "Missing files"),
                "unknown": ("unknown", "Unknown state"),
            }

            state, message = state_info.get(torrent.state, ("unknown", torrent.state))
            complete = torrent.progress >= 1.0

            # For active downloads without a special message, leave message as None
            # so the handler can build the progress message
            if complete:
                message = "Complete"

            # Only include ETA if it's reasonable (less than 1 week)
            eta = torrent.eta if 0 < torrent.eta < 604800 else None

            return DownloadStatus(
                progress=torrent.progress * 100,
                state="complete" if complete else state,
                message=message,
                complete=complete,
                file_path=torrent.content_path if complete else None,
                download_speed=torrent.dlspeed,
                eta=eta,
            )
        except Exception as e:
            error_type = type(e).__name__
            logger.error(f"qBittorrent get_status failed ({error_type}): {e}")
            return DownloadStatus.error(f"{error_type}: {e}")

    def remove(self, download_id: str, delete_files: bool = False) -> bool:
        """
        Remove a torrent from qBittorrent.

        Args:
            download_id: Torrent info_hash
            delete_files: Whether to also delete files

        Returns:
            True if successful.
        """
        try:
            self._client.torrents_delete(
                torrent_hashes=download_id, delete_files=delete_files
            )
            logger.info(
                f"Removed torrent from qBittorrent: {download_id}"
                + (" (with files)" if delete_files else "")
            )
            return True
        except Exception as e:
            error_type = type(e).__name__
            logger.error(f"qBittorrent remove failed ({error_type}): {e}")
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
            torrents = self._client.torrents_info(torrent_hashes=download_id)
            if torrents:
                return torrents[0].content_path
            return None
        except Exception as e:
            error_type = type(e).__name__
            logger.debug(f"qBittorrent get_download_path failed ({error_type}): {e}")
            return None

    def find_existing(self, url: str) -> Optional[Tuple[str, DownloadStatus]]:
        """Check if a torrent for this URL already exists in qBittorrent."""
        try:
            torrent_info = extract_torrent_info(url)
            if not torrent_info.info_hash:
                return None

            torrents = self._client.torrents_info(torrent_hashes=torrent_info.info_hash)
            if torrents:
                status = self.get_status(torrent_info.info_hash)
                return (torrent_info.info_hash, status)

            return None
        except Exception as e:
            logger.debug(f"Error checking for existing torrent: {e}")
            return None
