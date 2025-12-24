"""
Transmission download client for Prowlarr integration.

Uses the transmission-rpc library to communicate with Transmission's RPC API.
"""

import hashlib
import re
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


# Reuse the bencode functions from qbittorrent module for hash extraction
def _bencode_decode(data: bytes) -> tuple:
    """Simple bencode decoder. Returns (decoded_value, remaining_bytes)."""
    if data[0:1] == b'd':
        result = {}
        data = data[1:]
        while data[0:1] != b'e':
            key, data = _bencode_decode(data)
            value, data = _bencode_decode(data)
            result[key] = value
        return result, data[1:]
    elif data[0:1] == b'l':
        result = []
        data = data[1:]
        while data[0:1] != b'e':
            value, data = _bencode_decode(data)
            result.append(value)
        return result, data[1:]
    elif data[0:1] == b'i':
        end = data.index(b'e')
        return int(data[1:end]), data[end + 1:]
    elif data[0:1].isdigit():
        colon = data.index(b':')
        length = int(data[:colon])
        start = colon + 1
        return data[start:start + length], data[start + length:]
    else:
        raise ValueError(f"Invalid bencode data: {data[:20]}")


def _bencode_encode(data) -> bytes:
    """Simple bencode encoder."""
    if isinstance(data, dict):
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


def _extract_info_hash_from_torrent(torrent_data: bytes) -> Optional[str]:
    """Extract info_hash from raw .torrent file data."""
    try:
        decoded, _ = _bencode_decode(torrent_data)
        if b'info' not in decoded:
            return None

        info_dict = decoded[b'info']
        info_bencoded = _bencode_encode(info_dict)

        return hashlib.sha1(info_bencoded).hexdigest().lower()
    except Exception as e:
        logger.debug(f"Failed to parse torrent file: {e}")
        return None


@register_client("torrent")
class TransmissionClient(DownloadClient):
    """Transmission download client using transmission-rpc library."""

    protocol = "torrent"
    name = "transmission"

    def __init__(self):
        """Initialize Transmission client with settings from config."""
        from transmission_rpc import Client

        url = config.get("TRANSMISSION_URL", "")
        username = config.get("TRANSMISSION_USERNAME", "")
        password = config.get("TRANSMISSION_PASSWORD", "")

        # Parse URL to extract host, port, and path
        parsed = urlparse(url)
        host = parsed.hostname or "localhost"
        port = parsed.port or 9091
        path = parsed.path or "/transmission/rpc"

        # Ensure path ends with /rpc
        if not path.endswith("/rpc"):
            path = path.rstrip("/") + "/transmission/rpc"

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

    def _extract_hash_from_magnet(self, magnet_url: str) -> Optional[str]:
        """Extract info_hash from a magnet URL if possible."""
        if not magnet_url.startswith("magnet:"):
            return None

        parsed = urlparse(magnet_url)
        params = parse_qs(parsed.query)

        xt_list = params.get("xt", [])
        for xt in xt_list:
            match = re.match(r"urn:btih:([a-fA-F0-9]{40}|[a-zA-Z2-7]{32})", xt)
            if match:
                hash_value = match.group(1)
                if len(hash_value) == 32:
                    import base64
                    try:
                        decoded = base64.b32decode(hash_value.upper())
                        return decoded.hex().lower()
                    except Exception:
                        pass
                return hash_value.lower()
        return None

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

            # Add the torrent
            if torrent_data:
                # Add from torrent file content (pass raw bytes, library handles encoding)
                torrent = self._client.add_torrent(
                    torrent=torrent_data,
                    labels=[category],
                )
            else:
                # Add from URL or magnet
                torrent = self._client.add_torrent(
                    torrent=url,
                    labels=[category],
                )

            # Get the hash from the returned torrent
            torrent_hash = torrent.hashString.lower()
            logger.info(f"Added torrent to Transmission: {torrent_hash}")

            # Verify hash matches if we extracted one
            if expected_hash and torrent_hash != expected_hash:
                logger.warning(
                    f"Hash mismatch: expected {expected_hash}, got {torrent_hash}"
                )

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
                message = "Download complete"

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
            # Torrent not found
            return DownloadStatus(
                progress=0,
                state="error",
                message="Torrent not found",
                complete=False,
                file_path=None,
            )
        except Exception as e:
            logger.error(f"Transmission get_status failed: {e}")
            return DownloadStatus(
                progress=0,
                state="error",
                message=str(e),
                complete=False,
                file_path=None,
            )

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
            logger.error(f"Transmission remove failed: {e}")
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
        except Exception:
            return None

    def find_existing(self, url: str) -> Optional[Tuple[str, DownloadStatus]]:
        """
        Check if a torrent for this URL already exists in Transmission.

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

            # Check if this torrent exists in Transmission
            try:
                torrent = self._client.get_torrent(expected_hash)
                status = self.get_status(expected_hash)
                logger.info(f"Found existing torrent in Transmission: {expected_hash} (state: {status.state})")
                return (expected_hash, status)
            except KeyError:
                # Torrent not found
                return None

        except Exception as e:
            logger.debug(f"Error checking for existing torrent: {e}")
            return None
