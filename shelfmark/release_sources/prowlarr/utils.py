"""Shared utilities for Prowlarr release source.

Provides common helper functions used across the Prowlarr plugin.
"""

import re
from typing import TYPE_CHECKING
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from shelfmark.core.request_helpers import normalize_optional_text

if TYPE_CHECKING:
    from pathlib import Path

_INTEGER_LIKE_PATTERN = re.compile(r"^[+-]?\d+$")
_FLOAT_LIKE_PATTERN = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$")
_AUTHOR_TOKEN_PATTERN = re.compile(r"\w+", re.UNICODE)
_AUTHOR_NOISE_TOKENS = frozenset(
    {"jr", "sr", "ii", "iii", "iv", "phd", "md", "dr", "mr", "mrs", "ms", "et", "al", "and", "the"}
)

# Ordering tiers for author agreement between the requested book and what an
# indexer reported. Lower sorts first.
AUTHOR_MATCH = 0
AUTHOR_UNKNOWN = 1
AUTHOR_MISMATCH = 2

# A mononym ("Homer") can only ever agree on one token; a longer name needs a
# given name and a surname to agree before it counts as the same person.
_AUTHOR_TOKENS_REQUIRED = 2


def coerce_int_like(value: object) -> int | None:
    """Return an integer for int-like config/API values, else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None

    normalized = normalize_optional_text(value)
    if normalized is None or not _INTEGER_LIKE_PATTERN.fullmatch(normalized):
        return None

    return int(normalized)


def _author_tokens(value: object) -> list[str]:
    """Split an author string into comparable lowercase name tokens."""
    if not isinstance(value, str):
        return []
    tokens = [token.lower() for token in _AUTHOR_TOKEN_PATTERN.findall(value)]
    return [token for token in tokens if token not in _AUTHOR_NOISE_TOKENS]


def _author_tokens_compatible(wanted: str, offered: str) -> bool:
    """Treat an abbreviated given name as the name it abbreviates."""
    return wanted == offered or wanted.startswith(offered) or offered.startswith(wanted)


def author_affinity(wanted: object, offered: object) -> int:
    """Rank how far an indexer's author field is from the requested author.

    Shelfmark ranks on this rather than filtering on it, so a wrong verdict only
    costs a release its position in the list, never its visibility. That is what
    makes the loose token comparison safe: "Tim"/"Timothy" and "T."/"Timothy"
    agree, while a transliteration ("Dostoevsky"/"Dostoyevsky") is merely sorted
    last instead of being hidden.

    Three-way on purpose: an indexer that reports no author at all must not sort
    below one that reports a wrong author, so "no metadata" ranks between
    agreement and disagreement rather than counting as either.
    """
    wanted_tokens = _author_tokens(wanted)
    offered_tokens = _author_tokens(offered)
    if not wanted_tokens or not offered_tokens:
        return AUTHOR_UNKNOWN

    matched = sum(
        1
        for wanted_token in wanted_tokens
        if any(
            _author_tokens_compatible(wanted_token, offered_token)
            for offered_token in offered_tokens
        )
    )
    required = min(_AUTHOR_TOKENS_REQUIRED, len(wanted_tokens))
    return AUTHOR_MATCH if matched >= required else AUTHOR_MISMATCH


def build_source_id(result: dict) -> str:
    """Build the Release.source_id for a raw Prowlarr result.

    Qualified by the indexer id because one tracker is often configured in
    Prowlarr as several indexer entries that differ only by a server-side search
    filter, and those entries return the same guid for the same torrent. Without
    the qualifier the entries collide in the release cache and a grab routes
    through whichever entry happened to cache last.
    """
    guid = result.get("guid")
    if guid:
        base = str(guid)
    else:
        indexer = result.get("indexer", "Unknown")
        base = f"{indexer}:{hash(result.get('title', 'Unknown'))}"

    indexer_id = coerce_int_like(result.get("indexerId"))
    if indexer_id is None:
        return base
    return f"{indexer_id}:{base}"


def coerce_float_like(value: object) -> float | None:
    """Return a float for float-like config/API values, else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)

    normalized = normalize_optional_text(value)
    if normalized is None or not _FLOAT_LIKE_PATTERN.fullmatch(normalized):
        return None

    return float(normalized)


def get_protocol(result: dict) -> str:
    """Get the download protocol from a Prowlarr result.

    Uses the protocol field directly if available, otherwise infers from URLs.
    """
    protocol = str(result.get("protocol", "")).lower()
    if protocol in ("torrent", "usenet"):
        return protocol

    magnet_url = str(result.get("magnetUrl") or "").lower()
    download_url = str(result.get("downloadUrl") or "").lower()

    # Prefer magnetUrl for inference if present.
    if magnet_url.startswith("magnet:"):
        return "torrent"

    if download_url.startswith("magnet:") or ".torrent" in download_url:
        return "torrent"
    if ".nzb" in download_url:
        return "usenet"

    return "unknown"


def get_preferred_download_url(result: dict, *, prefer_torrent_file: bool = False) -> str:
    """Pick the best URL to hand to a download client.

    For torrent results, prefer magnetUrl when available unless the configured
    client needs the fetched .torrent bytes.
    """
    protocol = str(result.get("protocol", "")).lower()
    magnet_url = str(result.get("magnetUrl") or "").strip()
    download_url = sanitize_download_url(str(result.get("downloadUrl") or "").strip())

    if protocol == "torrent":
        if prefer_torrent_file:
            return download_url or magnet_url
        return magnet_url or download_url
    if protocol == "usenet":
        return download_url or magnet_url

    # Unknown protocol: if it looks like a magnet, still prefer it.
    if magnet_url.lower().startswith("magnet:"):
        return magnet_url

    return download_url or magnet_url


def sanitize_download_url(download_url: str) -> str:
    """Normalize Prowlarr download URLs to avoid malformed query strings."""
    if not download_url:
        return download_url

    normalized = download_url.strip()
    if not normalized:
        return normalized

    lower = normalized.lower()
    if not lower.startswith(("http://", "https://")):
        return normalized

    if " " not in normalized:
        return normalized

    parsed = urlparse(normalized)
    if not parsed.query:
        return normalized

    cleaned_pairs = []
    changed = False
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        cleaned_key = key.strip()
        cleaned_value = value.strip()
        if cleaned_key != key or cleaned_value != value:
            changed = True
        cleaned_pairs.append((cleaned_key, cleaned_value))

    if not changed:
        return normalized

    cleaned_query = urlencode(cleaned_pairs, doseq=True)
    return urlunparse(parsed._replace(query=cleaned_query))


def get_protocol_display(result: dict) -> str:
    """Get a user-friendly display label for the protocol.

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
    """Generate a unique path in staging_dir, appending _N if needed.

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
