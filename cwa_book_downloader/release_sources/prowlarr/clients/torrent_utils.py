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
from typing import Optional, Tuple
from urllib.parse import parse_qs, urlparse

from cwa_book_downloader.core.logger import setup_logger

logger = setup_logger(__name__)


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
