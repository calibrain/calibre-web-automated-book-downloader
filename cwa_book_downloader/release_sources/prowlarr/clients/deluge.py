"""
Deluge download client for Prowlarr integration.

Uses the deluge-client library to communicate with Deluge's RPC daemon.
Note: Deluge uses a custom binary RPC protocol over TCP (default port 58846,
configurable via DELUGE_PORT), which requires the daemon to have
"Allow Remote Connections" enabled.
"""

import base64
from typing import Any, Optional, Tuple

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


def _decode(value: Any) -> Any:
    """Decode bytes to string if needed (Deluge returns bytes for strings)."""
    return value.decode('utf-8') if isinstance(value, bytes) else value


@register_client("torrent")
class DelugeClient(DownloadClient):
    """Deluge download client using deluge-client RPC library."""

    protocol = "torrent"
    name = "deluge"

    def __init__(self):
        """Initialize Deluge client with settings from config."""
        from deluge_client import DelugeRPCClient

        host = config.get("DELUGE_HOST", "localhost")
        password = config.get("DELUGE_PASSWORD", "")

        if not host:
            raise ValueError("DELUGE_HOST is required")
        if not password:
            raise ValueError("DELUGE_PASSWORD is required")

        port = int(config.get("DELUGE_PORT", "58846"))
        username = config.get("DELUGE_USERNAME", "")

        self._client = DelugeRPCClient(
            host=host,
            port=port,
            username=username,
            password=password,
        )
        self._connected = False
        self._category = config.get("DELUGE_CATEGORY", "cwabd")

    def _ensure_connected(self):
        """Ensure we're connected to the Deluge daemon."""
        if not self._connected:
            logger.debug("Connecting to Deluge daemon...")
            try:
                self._client.connect()
                self._connected = True
                logger.debug("Connected to Deluge daemon")
            except Exception as e:
                logger.error(f"Failed to connect to Deluge daemon: {type(e).__name__}: {e}")
                raise

    @staticmethod
    def is_configured() -> bool:
        """Check if Deluge is configured and selected as the torrent client."""
        client = config.get("PROWLARR_TORRENT_CLIENT", "")
        host = config.get("DELUGE_HOST", "")
        password = config.get("DELUGE_PASSWORD", "")
        return client == "deluge" and bool(host) and bool(password)

    def test_connection(self) -> Tuple[bool, str]:
        """Test connection to Deluge."""
        try:
            self._ensure_connected()
            # Get daemon info
            version = self._client.call('daemon.info')
            return True, f"Connected to Deluge {version}"
        except Exception as e:
            self._connected = False
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
            self._ensure_connected()

            category = category or self._category

            torrent_info = extract_torrent_info(url)
            if not torrent_info.is_magnet and not torrent_info.torrent_data:
                raise Exception("Failed to fetch torrent file")

            options = {}

            if torrent_info.is_magnet:
                # Add magnet link
                torrent_id = self._client.call(
                    'core.add_torrent_magnet',
                    url,
                    options,
                )
            else:
                filedump = base64.b64encode(torrent_info.torrent_data).decode('ascii')
                torrent_id = self._client.call(
                    'core.add_torrent_file',
                    f"{name}.torrent",
                    filedump,
                    options,
                )

            if torrent_id:
                torrent_id = _decode(torrent_id)
                logger.info(f"Added torrent to Deluge: {torrent_id}")
                return torrent_id.lower()

            raise Exception("Deluge returned no torrent ID")

        except Exception as e:
            self._connected = False
            logger.error(f"Deluge add failed: {e}")
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
            self._ensure_connected()

            # Get torrent status
            status = self._client.call(
                'core.get_torrent_status',
                download_id,
                ['state', 'progress', 'download_payload_rate', 'eta', 'save_path', 'name'],
            )

            if not status:
                return DownloadStatus.error("Torrent not found")

            # Deluge states: Downloading, Seeding, Paused, Checking, Queued, Error, Moving
            state_map = {
                'Downloading': ('downloading', None),
                'Seeding': ('seeding', 'Seeding'),
                'Paused': ('paused', 'Paused'),
                'Checking': ('checking', 'Checking files'),
                'Queued': ('queued', 'Queued'),
                'Error': ('error', 'Error'),
                'Moving': ('processing', 'Moving files'),
                'Allocating': ('downloading', 'Allocating space'),
            }

            deluge_state = _decode(status.get(b'state', b'Unknown'))
            state, message = state_map.get(deluge_state, ('unknown', deluge_state))
            progress = status.get(b'progress', 0)
            complete = progress >= 100

            if complete:
                message = "Download complete"

            eta = status.get(b'eta')
            if eta and eta > 604800:
                eta = None

            file_path = None
            if complete:
                save_path = _decode(status.get(b'save_path', b''))
                name = _decode(status.get(b'name', b''))
                if save_path and name:
                    file_path = f"{save_path}/{name}"

            return DownloadStatus(
                progress=progress,
                state="complete" if complete else state,
                message=message,
                complete=complete,
                file_path=file_path,
                download_speed=status.get(b'download_payload_rate'),
                eta=eta,
            )

        except Exception as e:
            self._connected = False
            error_type = type(e).__name__
            logger.error(f"Deluge get_status failed ({error_type}): {e}")
            return DownloadStatus.error(f"{error_type}: {e}")

    def remove(self, download_id: str, delete_files: bool = False) -> bool:
        """
        Remove a torrent from Deluge.

        Args:
            download_id: Torrent info_hash
            delete_files: Whether to also delete files

        Returns:
            True if successful.
        """
        try:
            self._ensure_connected()

            result = self._client.call(
                'core.remove_torrent',
                download_id,
                delete_files,
            )

            if result:
                logger.info(
                    f"Removed torrent from Deluge: {download_id}"
                    + (" (with files)" if delete_files else "")
                )
                return True
            return False

        except Exception as e:
            self._connected = False
            error_type = type(e).__name__
            logger.error(f"Deluge remove failed ({error_type}): {e}")
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
            self._ensure_connected()

            status = self._client.call(
                'core.get_torrent_status',
                download_id,
                ['save_path', 'name'],
            )

            if status:
                save_path = _decode(status.get(b'save_path', b''))
                name = _decode(status.get(b'name', b''))
                if save_path and name:
                    return f"{save_path}/{name}"
            return None

        except Exception as e:
            self._connected = False
            error_type = type(e).__name__
            logger.debug(f"Deluge get_download_path failed ({error_type}): {e}")
            return None

    def find_existing(self, url: str) -> Optional[Tuple[str, DownloadStatus]]:
        """Check if a torrent for this URL already exists in Deluge."""
        try:
            self._ensure_connected()

            torrent_info = extract_torrent_info(url)
            if not torrent_info.info_hash:
                return None

            status = self._client.call(
                'core.get_torrent_status',
                torrent_info.info_hash,
                ['state'],
            )

            if status:
                full_status = self.get_status(torrent_info.info_hash)
                return (torrent_info.info_hash, full_status)

            return None
        except Exception as e:
            self._connected = False
            logger.debug(f"Error checking for existing torrent: {e}")
            return None
