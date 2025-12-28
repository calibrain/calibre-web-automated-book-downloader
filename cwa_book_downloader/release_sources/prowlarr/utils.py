"""
Shared utilities for Prowlarr release source.

Provides common helper functions used across the Prowlarr plugin.
"""

from pathlib import Path
from typing import Optional


def get_protocol(result: dict) -> str:
    """
    Get the download protocol from a Prowlarr result.

    Uses the protocol field directly if available, otherwise infers from URL.

    Args:
        result: Prowlarr search result dictionary

    Returns:
        Protocol string: "torrent", "usenet", or "unknown"
    """
    # Prowlarr provides protocol directly - use it
    protocol = result.get("protocol", "").lower()
    if protocol in ("torrent", "usenet"):
        return protocol

    # Fallback: infer from download URL
    download_url = result.get("downloadUrl") or result.get("magnetUrl") or ""
    url_lower = download_url.lower()
    if url_lower.startswith("magnet:") or ".torrent" in url_lower:
        return "torrent"
    if ".nzb" in url_lower:
        return "usenet"

    return "unknown"


def get_protocol_display(result: dict) -> str:
    """
    Get a user-friendly display label for the protocol.

    Args:
        result: Prowlarr search result dictionary

    Returns:
        Display label: "torrent", "nzb", or "unknown"
    """
    protocol = get_protocol(result)
    if protocol == "usenet":
        return "nzb"
    return protocol


def get_unique_path(staging_dir: Path, name: str, suffix: str = "") -> Path:
    """
    Generate a unique path in staging_dir, appending _N if needed.

    Args:
        staging_dir: Directory to create the path in
        name: Base name for the file/directory
        suffix: Optional suffix (e.g., ".epub" for files)

    Returns:
        Unique Path that doesn't exist in staging_dir
    """
    staged_path = staging_dir / (name + suffix)
    if not staged_path.exists():
        return staged_path

    counter = 1
    while True:
        staged_path = staging_dir / f"{name}_{counter}{suffix}"
        if not staged_path.exists():
            return staged_path
        counter += 1
