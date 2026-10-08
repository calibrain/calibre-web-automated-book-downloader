"""Shared utilities for torrent clients."""

from __future__ import annotations

import base64
import hashlib
import re
import time
from binascii import Error as BinasciiError
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from threading import Lock
from urllib.parse import ParseResult, parse_qs, urljoin, urlparse, urlunparse

import requests

from shelfmark.core.config import config
from shelfmark.core.logger import setup_logger
from shelfmark.core.utils import normalize_http_url
from shelfmark.download.network import get_ssl_verify
from shelfmark.download.postprocess.packs import PackFile

logger = setup_logger(__name__)

_MAGNET_RESPONSE_MAX_BYTES = 2000
_TORRENT_FETCH_MAX_REDIRECTS = 5
_BASE32_BTMH_TAG_BYTES = 34
_BTIH_INFO_BYTE_HEX = 0x20
_BTIH_PREFIX_BYTE = 0x12
_BTIH_DIGEST_LENGTH = 32
_BTIH_HASH_LENGTH_40 = 40
_BTIH_HASH_LENGTH_32 = 32
_TORRENT_FETCH_ERRORS = (
    requests.exceptions.RequestException,
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
)
_TORRENT_PARSE_ERRORS = (IndexError, KeyError, TypeError, ValueError)
_TRUSTED_TORRENT_FETCH_URL_CONFIG_KEYS = ("PROWLARR_URL", "NEWZNAB_URL")

# Successful torrent fetches are reused for a short window so one add attempt
# hits the download link only once. Tracker download links (e.g. private
# trackers behind Prowlarr's proxy) can be slow, rate-limited, or single-use,
# and both find_existing() and add_download() resolve the same URL (#1111).
_TORRENT_FETCH_CACHE_TTL_SECONDS = 120.0
_TORRENT_FETCH_CACHE_MAX_ENTRIES = 8
_torrent_fetch_cache_lock = Lock()
_torrent_fetch_cache: dict[str, tuple[float, TorrentInfo]] = {}

type BencodeValue = dict[str | bytes, BencodeValue] | list[BencodeValue] | int | bytes | str


def _safe_url(url: str, *, limit: int = 120) -> str:
    """Return a log-safe URL: scheme/host/path kept, query and fragment dropped.

    Torrent download URLs commonly carry credentials in their query string
    (Prowlarr's ``apikey=...`` proxy links among them), so raw URLs must never
    reach logs or exception messages.
    """
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return f"<unparsed url: {type(url).__name__}>"
    safe = urlunparse(parsed._replace(query="", fragment=""))
    return safe[:limit]


_URL_IN_TEXT_PATTERN = re.compile(r"https?://\S+")


def _redact_urls_in_text(text: str) -> str:
    """Scrub credential-bearing URLs out of free-form text such as exception messages."""
    return _URL_IN_TEXT_PATTERN.sub(lambda match: _safe_url(match.group(0)), text)


@dataclass
class TorrentInfo:
    """Parsed information from a torrent URL."""

    info_hash: str | None
    """Lowercase hex info_hash (32 or 40 chars), or None if extraction failed."""

    torrent_data: bytes | None
    """Raw .torrent file content, only populated for .torrent URLs."""

    is_magnet: bool
    """True if the URL was a magnet link."""

    magnet_url: str | None = None
    """The actual magnet URL, if available."""

    fetch_error: str | None = None
    """Why fetching the .torrent URL failed, or None if it succeeded/was skipped."""

    def with_info_hash(self, info_hash: str | None) -> TorrentInfo:
        """Return a copy with the info_hash replaced when provided."""
        if info_hash:
            return TorrentInfo(
                info_hash=info_hash,
                torrent_data=self.torrent_data,
                is_magnet=self.is_magnet,
                magnet_url=self.magnet_url,
                fetch_error=self.fetch_error,
            )
        return self


@dataclass
class DebridMagnet:
    """A magnet link, ready to hand to a debrid service as-is."""

    magnet_url: str


@dataclass
class DebridTorrentFile:
    """Raw .torrent bytes, for a debrid service's file-upload endpoint."""

    torrent_data: bytes


# A debrid service takes one or the other, never an indexer page or a proxy URL.
type DebridUpload = DebridMagnet | DebridTorrentFile


def resolve_debrid_upload(url: str, *, expected_hash: str | None = None) -> DebridUpload:
    """Resolve a release download URL into a magnet link or .torrent bytes.

    Prowlarr hands out a proxy URL, with no magnetUrl and no infoHash, for any
    indexer that only publishes torrent files - 1337x among them. Posting that
    URL to a debrid service as if it were a magnet is what produced a bare 404
    from the service instead of a download (#1250).

    The torrent file is preferred over a synthesized `urn:btih:` magnet because
    it carries the tracker list, which is how the service finds a swarm that is
    not already cached. Fetches are shared with the rest of the add path through
    the torrent fetch cache, so resolving here costs at most one request.

    Raises:
        ValueError: The URL resolved to neither form, so there is nothing to send.

    """
    if url.startswith("magnet:"):
        return DebridMagnet(magnet_url=url)

    info = extract_torrent_info(url, expected_hash=expected_hash)

    if info.is_magnet and info.magnet_url:
        # The download URL redirected to, or returned, a magnet link.
        return DebridMagnet(magnet_url=info.magnet_url)
    if info.torrent_data:
        return DebridTorrentFile(torrent_data=info.torrent_data)
    if info.info_hash:
        # No file to upload, but the hash alone still identifies the torrent.
        return DebridMagnet(magnet_url=f"magnet:?xt=urn:btih:{info.info_hash}")

    reason = info.fetch_error or "no magnet link, info hash, or torrent file was available"
    msg = f"Could not resolve a torrent to send from {_safe_url(url)} ({reason})"
    raise ValueError(msg)


def extract_torrent_info(
    url: str,
    *,
    fetch_torrent: bool = True,
    expected_hash: str | None = None,
) -> TorrentInfo:
    """Extract info_hash from magnet link or .torrent URL.

    Notes:
        When the URL points at Prowlarr's proxied download endpoint, it typically
        requires the `X-Api-Key` header. If `PROWLARR_API_KEY` is configured,
        include it for the torrent fetch request.

        Redirects to magnet links are handled explicitly so we can extract a
        hash from the magnet when available.

    """
    is_magnet = url.startswith("magnet:")

    # Try to extract hash from magnet URL
    if is_magnet:
        info_hash = extract_hash_from_magnet(url)
        if not info_hash and expected_hash:
            info_hash = expected_hash
        return TorrentInfo(info_hash=info_hash, torrent_data=None, is_magnet=True, magnet_url=url)

    # Not a magnet - try to fetch and parse the .torrent file
    if not fetch_torrent:
        return TorrentInfo(info_hash=expected_hash, torrent_data=None, is_magnet=False)

    info = _get_cached_torrent_fetch(url)
    if info is None:
        info = _fetch_torrent_info(url)
        if info.fetch_error is None:
            _store_cached_torrent_fetch(url, info)

    return info.with_info_hash(info.info_hash or expected_hash)


def _get_cached_torrent_fetch(url: str) -> TorrentInfo | None:
    with _torrent_fetch_cache_lock:
        entry = _torrent_fetch_cache.get(url)
        if entry is None:
            return None
        fetched_at, info = entry
        if time.monotonic() - fetched_at > _TORRENT_FETCH_CACHE_TTL_SECONDS:
            del _torrent_fetch_cache[url]
            return None
    logger.debug("Reusing recently fetched torrent data for: %s...", _safe_url(url))
    return info


def _store_cached_torrent_fetch(url: str, info: TorrentInfo) -> None:
    with _torrent_fetch_cache_lock:
        _torrent_fetch_cache[url] = (time.monotonic(), info)
        while len(_torrent_fetch_cache) > _TORRENT_FETCH_CACHE_MAX_ENTRIES:
            oldest_url = min(_torrent_fetch_cache, key=lambda key: _torrent_fetch_cache[key][0])
            del _torrent_fetch_cache[oldest_url]


def clear_torrent_fetch_cache() -> None:
    """Drop all cached torrent fetches (used by tests)."""
    with _torrent_fetch_cache_lock:
        _torrent_fetch_cache.clear()


def _fetch_torrent_info(url: str) -> TorrentInfo:
    """Fetch a .torrent URL and parse out the info_hash and raw torrent data.

    On failure, the returned TorrentInfo carries the reason in `fetch_error`
    so callers can surface it instead of a generic hash error.
    """
    # A release source can legitimately hand us a download URL on a different
    # origin than the configured Prowlarr/Newznab endpoint (e.g. a direct
    # tracker link, or Prowlarr reached through a separate proxy), and a trusted
    # Prowlarr download URL commonly redirects to the indexer's own download
    # link. We still need to fetch the .torrent to recover the info_hash when
    # the source did not provide one, so the prefetch runs regardless of origin
    # and follows cross-origin redirects. The Prowlarr API key, however, is
    # re-evaluated per hop and only ever sent to a trusted origin so it can
    # never leak to an arbitrary indexer/tracker host.
    headers: dict[str, str] = {"Accept": "application/x-bittorrent"}
    # TODO(shelfmark): Move this source-specific Prowlarr auth handling into a source hook.
    api_key = str(config.get("PROWLARR_API_KEY", "") or "").strip()
    if api_key:
        headers["X-Api-Key"] = api_key

    def resolve_url(current: str, location: str) -> str:
        if not location:
            return current
        # Support relative redirect locations
        return urljoin(current, location)

    try:
        logger.debug("Fetching torrent file from: %s...", _safe_url(url))

        # Redirects are followed manually: some indexers redirect download URLs
        # to magnet links, and each hop must decide anew whether it may see the
        # API key.
        current_url = url
        redirects_remaining = _TORRENT_FETCH_MAX_REDIRECTS
        while True:
            request_headers = dict(headers)
            if not _is_trusted_torrent_fetch_url(current_url):
                request_headers.pop("X-Api-Key", None)

            resp = requests.get(
                current_url,
                timeout=30,
                allow_redirects=False,
                headers=request_headers,
                verify=get_ssl_verify(current_url),
            )

            if resp.status_code not in (301, 302, 303, 307, 308):
                break

            redirect_url = resolve_url(current_url, resp.headers.get("Location", ""))
            if redirect_url.startswith("magnet:"):
                logger.debug("Download URL redirected to magnet link")
                return TorrentInfo(
                    info_hash=extract_hash_from_magnet(redirect_url),
                    torrent_data=None,
                    is_magnet=True,
                    magnet_url=redirect_url,
                )
            if redirects_remaining <= 0:
                logger.warning("Too many redirects fetching torrent file: %s...", _safe_url(url))
                return TorrentInfo(
                    info_hash=None,
                    torrent_data=None,
                    is_magnet=False,
                    fetch_error="too many redirects",
                )
            redirects_remaining -= 1
            logger.debug("Following redirect to: %s...", _safe_url(redirect_url))
            current_url = redirect_url

        resp.raise_for_status()
        torrent_data = resp.content

        # Check if response is actually a magnet link (text response)
        # Some indexers return magnet links as plain text instead of redirecting
        if len(torrent_data) < _MAGNET_RESPONSE_MAX_BYTES:  # Magnet links are typically short
            text_content = torrent_data.decode("utf-8", errors="ignore").strip()
            if text_content.startswith("magnet:"):
                logger.debug("Download URL returned magnet link as response body")
                return TorrentInfo(
                    info_hash=extract_hash_from_magnet(text_content),
                    torrent_data=None,
                    is_magnet=True,
                    magnet_url=text_content,
                )

        info_hash = extract_info_hash_from_torrent(torrent_data)
        if info_hash:
            logger.debug("Extracted hash from torrent file: %s", info_hash)
        else:
            logger.warning("Could not extract hash from torrent file")
        return TorrentInfo(info_hash=info_hash, torrent_data=torrent_data, is_magnet=False)
    except _TORRENT_FETCH_ERRORS as e:
        # Exception messages can repeat the source or redirect URL, including its
        # credentials; scrub them before logging or storing the reason.
        message = _redact_urls_in_text(str(e))
        logger.warning("Could not fetch torrent file: %s: %s", type(e).__name__, message)
        return TorrentInfo(info_hash=None, torrent_data=None, is_magnet=False, fetch_error=message)


def _is_trusted_torrent_fetch_url(url: str) -> bool:
    parsed = urlparse(url)
    origin = _url_origin(parsed)
    if origin is None:
        return False

    for key in _TRUSTED_TORRENT_FETCH_URL_CONFIG_KEYS:
        configured_url = str(config.get(key, "") or "").strip()
        if not configured_url:
            continue
        configured_origin = _url_origin(urlparse(normalize_http_url(configured_url)))
        if configured_origin == origin:
            return True

    return False


def _url_origin(parsed_url: ParseResult) -> tuple[str, str, int] | None:
    scheme = parsed_url.scheme.lower()
    if scheme not in {"http", "https"}:
        return None

    hostname = parsed_url.hostname
    if not hostname:
        return None

    default_port = 443 if scheme == "https" else 80
    return (scheme, hostname.lower(), parsed_url.port or default_port)


def parse_transmission_url(url: str) -> tuple[str, str, int, str]:
    """Parse Transmission URL into (protocol, host, port, path)."""
    parsed = urlparse(url)
    protocol = (parsed.scheme or "http").lower()
    if protocol not in ("http", "https"):
        protocol = "http"
    host = parsed.hostname or "localhost"
    port = parsed.port or 9091
    path = parsed.path or "/transmission/rpc"

    # Ensure path ends with /rpc
    if not path.endswith("/rpc"):
        path = path.rstrip("/") + "/transmission/rpc"

    return protocol, host, port, path


def bencode_decode(data: bytes) -> tuple:
    """Decode bencoded data. Returns (value, remaining_bytes)."""
    if data[0:1] == b"d":
        # Dictionary
        result = {}
        data = data[1:]
        while data[0:1] != b"e":
            key, data = bencode_decode(data)
            value, data = bencode_decode(data)
            result[key] = value
        return result, data[1:]
    if data[0:1] == b"l":
        # List
        result = []
        data = data[1:]
        while data[0:1] != b"e":
            value, data = bencode_decode(data)
            result.append(value)
        return result, data[1:]
    if data[0:1] == b"i":
        # Integer
        end = data.index(b"e")
        return int(data[1:end]), data[end + 1 :]
    if data[0:1].isdigit():
        # Byte string
        colon = data.index(b":")
        length = int(data[:colon])
        start = colon + 1
        return data[start : start + length], data[start + length :]
    first_byte = data[0:1]
    msg = (
        f"Invalid bencode data: expected 'd', 'l', 'i', or digit, "
        f"got {first_byte!r}. First 20 bytes: {data[:20]!r}"
    )
    raise ValueError(msg)


def bencode_encode(data: BencodeValue) -> bytes:
    """Encode data to bencode format."""
    if isinstance(data, dict):
        # Keys must be sorted (bencode spec requirement)
        result = b"d"
        for key in sorted(data.keys()):
            result += bencode_encode(key)
            result += bencode_encode(data[key])
        result += b"e"
        return result
    if isinstance(data, list):
        result = b"l"
        for item in data:
            result += bencode_encode(item)
        result += b"e"
        return result
    if isinstance(data, int):
        return f"i{data}e".encode()
    if isinstance(data, bytes):
        return f"{len(data)}:".encode() + data
    if isinstance(data, str):
        encoded = data.encode("utf-8")
        return f"{len(encoded)}:".encode() + encoded
    msg = (
        f"Cannot bencode type {type(data).__name__}: "
        f"expected dict, list, int, bytes, or str. Value: {data!r}"
    )
    raise ValueError(msg)


def extract_info_hash_from_torrent(torrent_data: bytes) -> str | None:
    """Extract info_hash from .torrent file data."""
    try:
        decoded, _ = bencode_decode(torrent_data)
        if b"info" not in decoded:
            return None

        info_bencoded = bencode_encode(decoded[b"info"])
        info_dict = decoded[b"info"]
        if isinstance(info_dict, dict) and b"pieces" in info_dict:
            # BitTorrent v1 info hashes are defined as SHA-1.
            return hashlib.sha1(info_bencoded).hexdigest().lower()  # noqa: S324
        return hashlib.sha256(info_bencoded).hexdigest().lower()
    except _TORRENT_PARSE_ERRORS as e:
        logger.debug("Failed to parse torrent file: %s", e)
        return None


def _decode_torrent_text(value: object) -> str | None:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        return value
    return None


def extract_file_list_from_torrent(torrent_data: bytes) -> list[PackFile] | None:
    """List the files a .torrent describes, release-relative, without downloading it.

    Multi-file torrents nest every path under the torrent name (which becomes the
    client's save folder); single-file torrents are just the named file.
    """
    try:
        decoded, _ = bencode_decode(torrent_data)
    except _TORRENT_PARSE_ERRORS as e:
        logger.debug("Failed to parse torrent file list: %s", e)
        return None
    if not isinstance(decoded, dict):
        return None
    info = decoded.get(b"info")
    if not isinstance(info, dict):
        return None

    name = _decode_torrent_text(info.get(b"name")) or ""
    raw_files = info.get(b"files")
    if not isinstance(raw_files, list):
        length = info.get(b"length")
        if not name:
            return None
        return [PackFile(name, length if isinstance(length, int) else None)]

    files: list[PackFile] = []
    for entry in raw_files:
        if not isinstance(entry, dict):
            continue
        raw_path = entry.get(b"path")
        if not isinstance(raw_path, list):
            continue
        segments = [seg for seg in (_decode_torrent_text(part) for part in raw_path) if seg]
        if not segments:
            continue
        if name:
            segments.insert(0, name)
        length = entry.get(b"length")
        files.append(PackFile("/".join(segments), length if isinstance(length, int) else None))
    return files


def extract_hash_from_magnet(magnet_url: str) -> str | None:
    """Extract info_hash from a magnet URL."""
    if not magnet_url.startswith("magnet:"):
        return None

    parsed = urlparse(magnet_url)
    params = parse_qs(parsed.query)

    def extract_btmh(value: str) -> str | None:
        raw_value = value.strip()
        if not raw_value:
            return None

        data: bytes | None = None
        if re.fullmatch(r"[a-fA-F0-9]+", raw_value):
            if len(raw_value) % 2 != 0:
                return None
            try:
                data = bytes.fromhex(raw_value)
            except ValueError:
                return None
        else:
            padded = raw_value.upper() + "=" * (-len(raw_value) % 8)
            try:
                data = base64.b32decode(padded, casefold=True)
            except BinasciiError, ValueError:
                return None

        if not data:
            return None

        if (
            len(data) >= _BASE32_BTMH_TAG_BYTES
            and data[0] == _BTIH_PREFIX_BYTE
            and data[1] == _BTIH_INFO_BYTE_HEX
        ):
            digest = data[2:_BASE32_BTMH_TAG_BYTES]
            if len(digest) == _BTIH_DIGEST_LENGTH:
                return digest.hex().lower()

        if len(data) == _BTIH_HASH_LENGTH_32:
            return data.hex().lower()

        return None

    xt_values = params.get("xt", [])

    for xt in xt_values:
        # Format: urn:btih:<hash> (32 or 40 chars)
        match = re.match(r"urn:btih:([a-fA-F0-9]{40}|[a-zA-Z0-9]{32})", xt)
        if match:
            hash_value = match.group(1)

            # 40-char hex or 32-char hex (ED2K) - return as-is
            if len(hash_value) == _BTIH_HASH_LENGTH_40 or re.match(
                r"^[a-fA-F0-9]{32}$", hash_value
            ):
                return hash_value.lower()

            # 32-char base32 - decode to hex
            if re.match(r"^[A-Z2-7]{32}$", hash_value.upper()):
                try:
                    return base64.b32decode(hash_value.upper()).hex().lower()
                except BinasciiError, ValueError:
                    logger.debug(
                        "Could not decode base32 BTIH hash from magnet URI: %s", hash_value
                    )

            # Fallback: return as-is
            return hash_value.lower()

    for xt in xt_values:
        if xt.startswith("urn:btmh:"):
            btmh_value = xt[len("urn:btmh:") :]
            btmh_hash = extract_btmh(btmh_value)
            if btmh_hash:
                return btmh_hash

    return None


def safe_relative_path(name: str, target_dir: Path, service: str) -> Path:
    """Validate a service-supplied file name before anything is written below ``target_dir``.

    Debrid services pass torrent file names straight through, and a torrent can name a
    file ``../../etc/cron.d/x``. Absolute paths, drive letters and ``..`` segments are
    refused, and the resolved destination must stay inside ``target_dir``.
    """
    if not name:
        msg = f"{service} returned a file without a name"
        raise RuntimeError(msg)

    relative_path = PurePosixPath(name.replace("\\", "/"))
    windows_path = PureWindowsPath(name)
    if (
        relative_path.is_absolute()
        or windows_path.is_absolute()
        or windows_path.drive
        or ".." in relative_path.parts
    ):
        msg = f"{service} returned an unsafe file path: {name}"
        raise RuntimeError(msg)
    if relative_path == PurePosixPath("."):
        msg = f"{service} returned a file without a usable name"
        raise RuntimeError(msg)

    destination = (target_dir / Path(*relative_path.parts)).resolve()
    try:
        destination.relative_to(target_dir.resolve())
    except ValueError:
        msg = f"{service} returned an unsafe file path: {name}"
        raise RuntimeError(msg) from None
    return Path(*relative_path.parts)
