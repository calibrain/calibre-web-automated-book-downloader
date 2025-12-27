"""DCC (Direct Client-to-Client) protocol implementation.

Handles DCC SEND file transfers used by IRC bots to send files.
"""

import re
import socket
import struct
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Callable, Optional

from cwa_book_downloader.core.logger import setup_logger

logger = setup_logger(__name__)

# Regex to parse DCC SEND messages - handles quoted filenames
# Format: DCC SEND "filename.epub" 2760158537 2050 2321788
#                  |                |          |    |
#                  filename         IP(int)    port size
DCC_REGEX = re.compile(r'DCC SEND "?(.+[^"])"?\s(\d+)\s+(\d+)\s+(\d+)\s*')

# Buffer size for DCC transfers - 4096 bytes provides good performance
BUFFER_SIZE = 4096


@dataclass
class DCCOffer:
    """Parsed DCC SEND offer."""
    filename: str
    ip: str
    port: int
    size: int

    @property
    def address(self) -> tuple[str, int]:
        """Return (ip, port) tuple for socket.connect()."""
        return (self.ip, self.port)


class DCCError(Exception):
    """Base exception for DCC operations."""
    pass


class DCCParseError(DCCError):
    """Failed to parse DCC SEND string."""
    pass


class DCCSizeError(DCCError):
    """Downloaded size doesn't match expected size."""
    pass


class DCCConnectionError(DCCError):
    """Failed to connect to DCC sender."""
    pass


def int_to_ip(ip_int: int) -> str:
    """Convert 32-bit integer to dotted IP notation.

    DCC protocol sends IP addresses as 32-bit unsigned integers
    in network byte order (big-endian).

    Example: 2760158537 -> "164.132.173.73"
    """
    packed = struct.pack('>I', ip_int)
    return '.'.join(str(b) for b in packed)


def parse_dcc_send(text: str) -> DCCOffer:
    """Parse a DCC SEND message into a DCCOffer.

    Args:
        text: Full IRC message containing DCC SEND

    Returns:
        DCCOffer with filename, ip, port, size

    Raises:
        DCCParseError: If message doesn't match expected format
    """
    match = DCC_REGEX.search(text)
    if not match:
        raise DCCParseError(f"Invalid DCC SEND format: {text[:100]}")

    filename = match.group(1).strip('"')
    ip_int = int(match.group(2))
    port = int(match.group(3))
    size = int(match.group(4))

    return DCCOffer(
        filename=filename,
        ip=int_to_ip(ip_int),
        port=port,
        size=size,
    )


def download_dcc(
    offer: DCCOffer,
    dest_path: Path,
    progress_callback: Optional[Callable[[float], None]] = None,
    cancel_flag: Optional[Event] = None,
    timeout: float = 30.0,
) -> None:
    """Download file via DCC protocol.

    Uses a custom read loop with 4096-byte buffer which is faster than
    Python's shutil.copyfileobj since DCC servers don't properly signal EOF.

    Args:
        offer: Parsed DCC offer with connection details
        dest_path: Where to save the file
        progress_callback: Called with percentage (0-100) during download
        cancel_flag: If set, abort the download
        timeout: Socket timeout in seconds

    Raises:
        DCCConnectionError: Failed to connect to sender
        DCCSizeError: Downloaded bytes != expected size
        DCCError: Other socket/IO errors
    """
    logger.info(f"DCC connecting to {offer.ip}:{offer.port} for {offer.filename}")

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect(offer.address)
    except socket.error as e:
        raise DCCConnectionError(f"Failed to connect to {offer.ip}:{offer.port}: {e}")

    try:
        received = 0
        last_progress = -1

        with open(dest_path, 'wb') as f:
            while received < offer.size:
                # Check for cancellation
                if cancel_flag and cancel_flag.is_set():
                    logger.info("DCC download cancelled")
                    return

                # Read chunk
                try:
                    chunk = sock.recv(BUFFER_SIZE)
                except socket.timeout:
                    raise DCCError(f"Timeout reading from {offer.ip}:{offer.port}")

                if not chunk:
                    # Connection closed prematurely
                    break

                f.write(chunk)
                received += len(chunk)

                # Report progress (every 1%)
                if progress_callback:
                    progress = int((received / offer.size) * 100)
                    if progress != last_progress:
                        progress_callback(progress)
                        last_progress = progress

        # Verify downloaded size matches expected
        if received != offer.size:
            raise DCCSizeError(
                f"Size mismatch: expected {offer.size} bytes, got {received}"
            )

        logger.info(f"DCC download complete: {received} bytes")

    finally:
        sock.close()
