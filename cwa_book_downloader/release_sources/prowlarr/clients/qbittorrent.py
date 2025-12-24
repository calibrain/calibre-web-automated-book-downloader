"""
qBittorrent download client for Prowlarr integration.

Uses the qbittorrent-api library to communicate with qBittorrent's Web API.
"""

import hashlib
import re
import time
from typing import Optional, Tuple
from urllib.parse import parse_qs, urlparse

import requests

from cwa_book_downloader.core.config import config
from cwa_book_downloader.core.logger import setup_logger
from cwa_book_downloader.release_sources.prowlarr.clients import (
    DownloadClient,
    DownloadStatus,
    register_client,
)

logger = setup_logger(__name__)


def _bencode_decode(data: bytes) -> tuple:
    """Simple bencode decoder. Returns (decoded_value, remaining_bytes)."""
    if data[0:1] == b'd':
        # Dictionary
        result = {}
        data = data[1:]
        while data[0:1] != b'e':
            key, data = _bencode_decode(data)
            value, data = _bencode_decode(data)
            result[key] = value
        return result, data[1:]
    elif data[0:1] == b'l':
        # List
        result = []
        data = data[1:]
        while data[0:1] != b'e':
            value, data = _bencode_decode(data)
            result.append(value)
        return result, data[1:]
    elif data[0:1] == b'i':
        # Integer
        end = data.index(b'e')
        return int(data[1:end]), data[end + 1:]
    elif data[0:1].isdigit():
        # String (byte string)
        colon = data.index(b':')
        length = int(data[:colon])
        start = colon + 1
        return data[start:start + length], data[start + length:]
    else:
        raise ValueError(f"Invalid bencode data: {data[:20]}")


def _extract_info_hash_from_torrent(torrent_data: bytes) -> Optional[str]:
    """Extract info_hash from raw .torrent file data."""
    try:
        decoded, _ = _bencode_decode(torrent_data)
        if b'info' not in decoded:
            return None

        # Find the raw info dict bytes to hash
        # We need to find where 'info' dict starts and ends in the original data
        info_start = torrent_data.find(b'4:info') + 6
        if info_start < 6:
            return None

        # Re-encode the info dict to get consistent bytes for hashing
        info_dict = decoded[b'info']
        info_bencoded = _bencode_encode(info_dict)

        # SHA1 hash of the info dict is the info_hash
        return hashlib.sha1(info_bencoded).hexdigest().lower()
    except Exception as e:
        logger.debug(f"Failed to parse torrent file: {e}")
        return None


def _bencode_encode(data) -> bytes:
    """Simple bencode encoder."""
    if isinstance(data, dict):
        # Keys must be sorted
        result = b'd'
        for key in sorted(data.keys()):
            result += _bencode_encode(key)
            result += _bencode_encode(data[key])
        result += b'e'
        return result
    elif isinstance(data, list):
        result = b'l'
        for item in data:
            result += _bencode_encode(item)
        result += b'e'
        return result
    elif isinstance(data, int):
        return f'i{data}e'.encode()
    elif isinstance(data, bytes):
        return f'{len(data)}:'.encode() + data
    elif isinstance(data, str):
        encoded = data.encode('utf-8')
        return f'{len(encoded)}:'.encode() + encoded
    else:
        raise ValueError(f"Cannot bencode type: {type(data)}")


@register_client("torrent")
class QBittorrentClient(DownloadClient):
    """qBittorrent download client."""

    protocol = "torrent"
    name = "qbittorrent"

    def __init__(self):
        """Initialize qBittorrent client with settings from config."""
        # Lazy import to avoid dependency issues if not using torrents
        from qbittorrentapi import Client

        self._client = Client(
            host=config.get("QBITTORRENT_URL", ""),
            username=config.get("QBITTORRENT_USERNAME", ""),
            password=config.get("QBITTORRENT_PASSWORD", ""),
        )

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

    def _extract_hash_from_magnet(self, magnet_url: str) -> Optional[str]:
        """Extract info_hash from a magnet URL if possible."""
        if not magnet_url.startswith("magnet:"):
            return None

        # Parse the magnet URL
        parsed = urlparse(magnet_url)
        params = parse_qs(parsed.query)

        # Get the xt (exact topic) parameter
        xt_list = params.get("xt", [])
        for xt in xt_list:
            # Format: urn:btih:<hash>
            match = re.match(r"urn:btih:([a-fA-F0-9]{40}|[a-zA-Z2-7]{32})", xt)
            if match:
                hash_value = match.group(1)
                # Convert base32 to hex if needed (32 chars = base32, 40 chars = hex)
                if len(hash_value) == 32:
                    import base64

                    try:
                        decoded = base64.b32decode(hash_value.upper())
                        return decoded.hex().lower()
                    except Exception:
                        pass
                return hash_value.lower()
        return None

    def add_download(self, url: str, name: str, category: str = "cwabd") -> str:
        """
        Add torrent by URL (magnet or .torrent).

        Args:
            url: Magnet link or .torrent URL
            name: Display name for the torrent
            category: Category for organization

        Returns:
            Torrent hash (info_hash).

        Raises:
            Exception: If adding fails.
        """
        try:
            # Ensure category exists
            try:
                self._client.torrents_create_category(name=category)
            except Exception:
                pass  # Category may already exist

            # Try to extract hash from magnet URL before adding
            expected_hash = self._extract_hash_from_magnet(url)
            if expected_hash:
                logger.debug(f"Extracted hash from magnet: {expected_hash}")

            is_magnet = url.startswith("magnet:")
            logger.debug(f"Adding torrent - URL type: {'magnet' if is_magnet else 'torrent file'}")

            torrent_data = None

            # For non-magnet URLs, fetch the .torrent file to extract the hash
            if not is_magnet and not expected_hash:
                logger.debug(f"Fetching torrent file from: {url[:80]}...")
                try:
                    resp = requests.get(url, timeout=30)
                    resp.raise_for_status()
                    torrent_data = resp.content
                    expected_hash = _extract_info_hash_from_torrent(torrent_data)
                    if expected_hash:
                        logger.debug(f"Extracted hash from torrent file: {expected_hash}")
                    else:
                        logger.warning("Could not extract hash from torrent file")
                except Exception as e:
                    logger.warning(f"Failed to fetch torrent file: {e}")

            logger.debug(f"Expected hash: {expected_hash}")

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
                return DownloadStatus(
                    progress=0,
                    state="error",
                    message="Torrent not found",
                    complete=False,
                    file_path=None,
                )

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
                message = "Download complete"

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
            logger.error(f"qBittorrent get_status failed: {e}")
            return DownloadStatus(
                progress=0,
                state="error",
                message=str(e),
                complete=False,
                file_path=None,
            )

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
            logger.error(f"qBittorrent remove failed: {e}")
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
        except Exception:
            return None

    def find_existing(self, url: str) -> Optional[Tuple[str, DownloadStatus]]:
        """
        Check if a torrent for this URL already exists in qBittorrent.

        Extracts the info_hash from the magnet link or .torrent file and
        checks if qBittorrent already has this torrent.

        Args:
            url: Magnet link or .torrent URL

        Returns:
            Tuple of (info_hash, status) if found, None if not found.
        """
        try:
            # Try to extract hash from magnet URL
            expected_hash = self._extract_hash_from_magnet(url)

            # If not a magnet, try to fetch and parse the .torrent file
            if not expected_hash and not url.startswith("magnet:"):
                logger.debug(f"Fetching torrent file to check for existing: {url[:80]}...")
                try:
                    resp = requests.get(url, timeout=30)
                    resp.raise_for_status()
                    expected_hash = _extract_info_hash_from_torrent(resp.content)
                except Exception as e:
                    logger.debug(f"Could not fetch torrent file: {e}")
                    return None

            if not expected_hash:
                logger.debug("Could not extract hash from URL")
                return None

            # Check if this torrent exists in qBittorrent
            torrents = self._client.torrents_info(torrent_hashes=expected_hash)
            if torrents:
                status = self.get_status(expected_hash)
                logger.info(f"Found existing torrent in qBittorrent: {expected_hash} (state: {status.state})")
                return (expected_hash, status)

            return None

        except Exception as e:
            logger.debug(f"Error checking for existing torrent: {e}")
            return None
