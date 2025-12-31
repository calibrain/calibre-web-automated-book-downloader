"""
Shared utilities for torrent clients.

Provides:
- Bencode encoding/decoding for .torrent files
- Info hash extraction from torrent files and magnet links
- URL parsing utilities for torrent clients

Bencode is the encoding used by BitTorrent for .torrent files.
See BEP-3: http://bittorrent.org/beps/bep_0003.html
"""

import base64
import hashlib
import re
from dataclasses import dataclass
from typing import Optional, Tuple
from urllib.parse import parse_qs, urlparse

import requests

from cwa_book_downloader.core.logger import setup_logger

logger = setup_logger(__name__)


@dataclass
class TorrentInfo:
    """Parsed information from a torrent URL."""

    info_hash: Optional[str]
    """40-character lowercase hex info_hash, or None if extraction failed."""

    torrent_data: Optional[bytes]
    """Raw .torrent file content, only populated for .torrent URLs."""

    is_magnet: bool
    """True if the URL was a magnet link."""

    magnet_url: Optional[str] = None
    """The actual magnet URL, if available."""


def extract_torrent_info(url: str, fetch_torrent: bool = True) -> TorrentInfo:
    """
    Extract torrent info_hash and optionally fetch torrent file content.

    Handles both magnet links and .torrent file URLs. For magnet links,
    extracts the hash directly. For .torrent URLs, optionally fetches
    the file and parses it to extract the hash.

    Also handles the case where a download URL redirects to a magnet link
    or returns a magnet link in the response body.

    Args:
        url: Magnet link or .torrent URL
        fetch_torrent: If True, fetch .torrent URLs to extract hash.
                      Set to False if you only need the hash from magnets.

    Returns:
        TorrentInfo with extracted hash and optional torrent data.
    """
    is_magnet = url.startswith("magnet:")

    # Try to extract hash from magnet URL
    if is_magnet:
        info_hash = extract_hash_from_magnet(url)
        return TorrentInfo(info_hash=info_hash, torrent_data=None, is_magnet=True, magnet_url=url)

    # Not a magnet - try to fetch and parse the .torrent file
    if not fetch_torrent:
        return TorrentInfo(info_hash=None, torrent_data=None, is_magnet=False)

    try:
        logger.debug(f"Fetching torrent file from: {url[:80]}...")

        # Use allow_redirects=False to handle magnet link redirects manually
        # Some indexers redirect download URLs to magnet links
        resp = requests.get(url, timeout=30, allow_redirects=False)

        # Check if this is a redirect to a magnet link
        if resp.status_code in (301, 302, 303, 307, 308):
            redirect_url = resp.headers.get("Location", "")
            if redirect_url.startswith("magnet:"):
                logger.debug(f"Download URL redirected to magnet link")
                info_hash = extract_hash_from_magnet(redirect_url)
                return TorrentInfo(
                    info_hash=info_hash, torrent_data=None, is_magnet=True, magnet_url=redirect_url
                )
            # Not a magnet redirect, follow it manually
            logger.debug(f"Following redirect to: {redirect_url[:80]}...")
            resp = requests.get(redirect_url, timeout=30)

        resp.raise_for_status()
        torrent_data = resp.content

        # Check if response is actually a magnet link (text response)
        # Some indexers return magnet links as plain text instead of redirecting
        if len(torrent_data) < 2000:  # Magnet links are typically short
            try:
                text_content = torrent_data.decode("utf-8", errors="ignore").strip()
                if text_content.startswith("magnet:"):
                    logger.debug("Download URL returned magnet link as response body")
                    info_hash = extract_hash_from_magnet(text_content)
                    return TorrentInfo(
                        info_hash=info_hash, torrent_data=None, is_magnet=True, magnet_url=text_content
                    )
            except Exception:
                pass  # Not text, continue with torrent parsing

        info_hash = extract_info_hash_from_torrent(torrent_data)
        if info_hash:
            logger.debug(f"Extracted hash from torrent file: {info_hash}")
        else:
            logger.warning("Could not extract hash from torrent file")
        return TorrentInfo(info_hash=info_hash, torrent_data=torrent_data, is_magnet=False)
    except Exception as e:
        logger.debug(f"Could not fetch torrent file: {e}")
        return TorrentInfo(info_hash=None, torrent_data=None, is_magnet=False)


def parse_transmission_url(url: str) -> Tuple[str, int, str]:
    """
    Parse a Transmission URL into host, port, and RPC path.

    Handles various URL formats and ensures the path ends with /rpc.

    Args:
        url: Transmission URL (e.g., "http://transmission:9091" or
             "http://localhost:9091/transmission/rpc")

    Returns:
        Tuple of (host, port, path) for transmission-rpc Client.
        - host: Hostname (defaults to "localhost" if not specified)
        - port: Port number (defaults to 9091 if not specified)
        - path: RPC path (ensures it ends with "/rpc")
    """
    parsed = urlparse(url)
    host = parsed.hostname or "localhost"
    port = parsed.port or 9091
    path = parsed.path or "/transmission/rpc"

    # Ensure path ends with /rpc
    if not path.endswith("/rpc"):
        path = path.rstrip("/") + "/transmission/rpc"

    return host, port, path


def bencode_decode(data: bytes) -> tuple:
    """
    Decode bencoded data.

    Bencode format:
    - 'i<integer>e' for integers
    - '<length>:<bytes>' for byte strings
    - 'l<elements>e' for lists
    - 'd<key><value>e' for dicts (keys must be byte strings, sorted)

    Args:
        data: Bencoded bytes

    Returns:
        Tuple of (decoded_value, remaining_bytes)

    Raises:
        ValueError: If data is not valid bencode
    """
    if data[0:1] == b'd':
        # Dictionary
        result = {}
        data = data[1:]
        while data[0:1] != b'e':
            key, data = bencode_decode(data)
            value, data = bencode_decode(data)
            result[key] = value
        return result, data[1:]
    elif data[0:1] == b'l':
        # List
        result = []
        data = data[1:]
        while data[0:1] != b'e':
            value, data = bencode_decode(data)
            result.append(value)
        return result, data[1:]
    elif data[0:1] == b'i':
        # Integer
        end = data.index(b'e')
        return int(data[1:end]), data[end + 1:]
    elif data[0:1].isdigit():
        # Byte string
        colon = data.index(b':')
        length = int(data[:colon])
        start = colon + 1
        return data[start:start + length], data[start + length:]
    else:
        first_byte = data[0:1]
        raise ValueError(
            f"Invalid bencode data: expected 'd', 'l', 'i', or digit, "
            f"got {first_byte!r}. First 20 bytes: {data[:20]!r}"
        )


def bencode_encode(data) -> bytes:
    """
    Encode data to bencode format.

    Args:
        data: Python object (dict, list, int, bytes, or str)

    Returns:
        Bencoded bytes

    Raises:
        ValueError: If data type cannot be bencoded
    """
    if isinstance(data, dict):
        # Keys must be sorted (bencode spec requirement)
        result = b'd'
        for key in sorted(data.keys()):
            result += bencode_encode(key)
            result += bencode_encode(data[key])
        result += b'e'
        return result
    elif isinstance(data, list):
        result = b'l'
        for item in data:
            result += bencode_encode(item)
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
        raise ValueError(
            f"Cannot bencode type {type(data).__name__}: "
            f"expected dict, list, int, bytes, or str. Value: {data!r}"
        )


def extract_info_hash_from_torrent(torrent_data: bytes) -> Optional[str]:
    """
    Extract info_hash from raw .torrent file data.

    The info_hash is the SHA1 hash of the bencoded 'info' dictionary,
    which uniquely identifies a torrent in the BitTorrent network.

    Args:
        torrent_data: Raw bytes of a .torrent file

    Returns:
        40-character lowercase hex string of the info_hash, or None if extraction fails
    """
    try:
        decoded, _ = bencode_decode(torrent_data)
        if b'info' not in decoded:
            return None

        # Re-encode info dict to get canonical bytes for hashing
        info_dict = decoded[b'info']
        info_bencoded = bencode_encode(info_dict)

        # SHA1 hash is required by BitTorrent spec (BEP-3)
        return hashlib.sha1(info_bencoded).hexdigest().lower()
    except Exception as e:
        logger.debug(f"Failed to parse torrent file: {e}")
        return None


def extract_hash_from_magnet(magnet_url: str) -> Optional[str]:
    """
    Extract info_hash from a magnet URL.

    Magnet URIs contain the info_hash in the 'xt' (exact topic) parameter
    as either a 40-character hex string or 32-character base32 string.

    Args:
        magnet_url: Magnet URI string

    Returns:
        40-character lowercase hex string of the info_hash, or None if extraction fails
    """
    if not magnet_url.startswith("magnet:"):
        return None

    parsed = urlparse(magnet_url)
    params = parse_qs(parsed.query)

    # Get the xt (exact topic) parameter
    xt_list = params.get("xt", [])
    for xt in xt_list:
        # Format: urn:btih:<hash>
        # Hash can be 40 hex chars or 32 base32 chars
        match = re.match(r"urn:btih:([a-fA-F0-9]{40}|[a-zA-Z2-7]{32})", xt)
        if match:
            hash_value = match.group(1)
            # Convert base32 to hex if needed (32 chars = base32, 40 chars = hex)
            if len(hash_value) == 32:
                try:
                    decoded = base64.b32decode(hash_value.upper())
                    return decoded.hex().lower()
                except Exception as e:
                    logger.debug(
                        f"Base32 decode failed for hash '{hash_value}': {e}, "
                        f"treating as hex value"
                    )
            return hash_value.lower()
    return None
