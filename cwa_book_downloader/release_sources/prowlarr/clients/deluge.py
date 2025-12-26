"""
Deluge download client for Prowlarr integration.

Uses the deluge-client library to communicate with Deluge's RPC daemon.
Note: Deluge uses a custom binary RPC protocol over TCP (default port 58846,
configurable via DELUGE_PORT), which requires the daemon to have
"Allow Remote Connections" enabled.
"""

import base64
from typing import Optional, Tuple

import requests

from cwa_book_downloader.core.config import config
from cwa_book_downloader.core.logger import setup_logger
from cwa_book_downloader.release_sources.prowlarr.clients import (
    DownloadClient,
    DownloadStatus,
    register_client,
)
from cwa_book_downloader.release_sources.prowlarr.clients.torrent_utils import (
    extract_hash_from_magnet,
    extract_info_hash_from_torrent,
)

logger = setup_logger(__name__)


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

            # Use configured category if not explicitly provided
            category = category or self._category

            # Try to extract hash from magnet URL before adding
            expected_hash = extract_hash_from_magnet(url)
            if expected_hash:
                logger.debug(f"Extracted hash from magnet: {expected_hash}")

            is_magnet = url.startswith("magnet:")
            logger.debug(f"Adding torrent - URL type: {'magnet' if is_magnet else 'torrent file'}")

            torrent_data = None

            # For non-magnet URLs, fetch the .torrent file
            if not is_magnet:
                logger.debug(f"Fetching torrent file from: {url[:80]}...")
                try:
                    resp = requests.get(url, timeout=30)
                    resp.raise_for_status()
                    torrent_data = resp.content
                    expected_hash = extract_info_hash_from_torrent(torrent_data)
                    if expected_hash:
                        logger.debug(f"Extracted hash from torrent file: {expected_hash}")
                    else:
                        logger.warning("Could not extract hash from torrent file")
                except Exception as e:
                    logger.warning(f"Failed to fetch torrent file: {e}")
                    raise

            # Add options
            options = {}

            # Add the torrent
            if is_magnet:
                # Add magnet link
                torrent_id = self._client.call(
                    'core.add_torrent_magnet',
                    url,
                    options,
                )
            else:
                # Add from torrent file content (base64 encoded)
                filedump = base64.b64encode(torrent_data).decode('ascii')
                torrent_id = self._client.call(
                    'core.add_torrent_file',
                    f"{name}.torrent",
                    filedump,
                    options,
                )

            if torrent_id:
                # Deluge returns bytes, decode to string
                if isinstance(torrent_id, bytes):
                    torrent_id = torrent_id.decode('utf-8')
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
                return DownloadStatus(
                    progress=0,
                    state="error",
                    message="Torrent not found",
                    complete=False,
                    file_path=None,
                )

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

            deluge_state = status.get(b'state', b'Unknown')
            if isinstance(deluge_state, bytes):
                deluge_state = deluge_state.decode('utf-8')

            state, message = state_map.get(deluge_state, ('unknown', deluge_state))
            progress = status.get(b'progress', 0)
            complete = progress >= 100

            if complete:
                message = "Download complete"

            # Get ETA if available and reasonable
            eta = status.get(b'eta')
            if eta and eta > 604800:  # More than 1 week
                eta = None

            # Build file path for completed downloads
            file_path = None
            if complete:
                save_path = status.get(b'save_path', b'')
                name = status.get(b'name', b'')
                if isinstance(save_path, bytes):
                    save_path = save_path.decode('utf-8')
                if isinstance(name, bytes):
                    name = name.decode('utf-8')
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
            return DownloadStatus(
                progress=0,
                state="error",
                message=f"{error_type}: {e}",
                complete=False,
                file_path=None,
            )

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
                save_path = status.get(b'save_path', b'')
                name = status.get(b'name', b'')
                if isinstance(save_path, bytes):
                    save_path = save_path.decode('utf-8')
                if isinstance(name, bytes):
                    name = name.decode('utf-8')
                if save_path and name:
                    return f"{save_path}/{name}"
            return None

        except Exception as e:
            self._connected = False
            error_type = type(e).__name__
            logger.debug(f"Deluge get_download_path failed ({error_type}): {e}")
            return None

    def find_existing(self, url: str) -> Optional[Tuple[str, DownloadStatus]]:
        """
        Check if a torrent for this URL already exists in Deluge.

        Args:
            url: Magnet link or .torrent URL

        Returns:
            Tuple of (info_hash, status) if found, None if not found.
        """
        try:
            self._ensure_connected()

            # Try to extract hash from magnet URL
            expected_hash = extract_hash_from_magnet(url)

            # If not a magnet, try to fetch and parse the .torrent file
            if not expected_hash and not url.startswith("magnet:"):
                logger.debug(f"Fetching torrent file to check for existing: {url[:80]}...")
                try:
                    resp = requests.get(url, timeout=30)
                    resp.raise_for_status()
                    expected_hash = extract_info_hash_from_torrent(resp.content)
                except Exception as e:
                    logger.debug(f"Could not fetch torrent file: {e}")
                    return None

            if not expected_hash:
                logger.debug("Could not extract hash from URL")
                return None

            # Check if this torrent exists in Deluge
            status = self._client.call(
                'core.get_torrent_status',
                expected_hash,
                ['state'],
            )

            if status:
                full_status = self.get_status(expected_hash)
                logger.debug(f"Found existing torrent in Deluge: {expected_hash} (state: {full_status.state})")
                return (expected_hash, full_status)

            return None

        except Exception as e:
            self._connected = False
            logger.debug(f"Error checking for existing torrent: {e}")
            return None
