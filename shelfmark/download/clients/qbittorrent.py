"""qBittorrent download client for Prowlarr integration."""

from __future__ import annotations

import os
import time
from http import HTTPStatus
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import SimpleNamespace
from typing import NoReturn, TypedDict

import requests

from shelfmark.core.config import config
from shelfmark.core.logger import setup_logger
from shelfmark.download.clients import (
    DownloadClient,
    DownloadStatus,
    register_client,
)
from shelfmark.download.clients._coercion import (
    coerce_optional_float,
    coerce_optional_int,
    config_text,
    normalize_http_config_url,
)
from shelfmark.download.clients.torrent_utils import (
    extract_torrent_info,
)
from shelfmark.download.network import get_ssl_verify

try:
    import qbittorrentapi as _qbittorrentapi
except ImportError:
    _ImportedQBittorrentApiError = RuntimeError
    _ImportedQBittorrentLoginFailed = RuntimeError
else:
    _ImportedQBittorrentApiError = getattr(_qbittorrentapi, "APIError", RuntimeError)
    _ImportedQBittorrentLoginFailed = getattr(_qbittorrentapi, "LoginFailed", RuntimeError)

logger = setup_logger(__name__)

_HASH_LENGTH_40 = 40
_HASH_LENGTH_ED2K = 32
_HTTP_STATUS_FORBIDDEN = HTTPStatus.FORBIDDEN
_HTTP_STATUS_NOT_FOUND = HTTPStatus.NOT_FOUND
_METADATA_DOWNLOAD_STATES = {"forcedMetaDL", "metaDL"}
# How long add_download waits for magnet metadata before falling back to the info
# hash it already knows, rather than holding the download queue on a thin swarm.
_METADATA_WAIT_POLLS = 20
_METADATA_WAIT_INTERVAL_SECONDS = 0.5
_ONE_WEEK_IN_SECONDS = 604800


class _UnsafeQBittorrentPath:
    pass


_UNSAFE_QBITTORRENT_PATH = _UnsafeQBittorrentPath()


class _QBittorrentAddKwargs(TypedDict, total=False):
    rename: str
    category: str
    save_path: str
    tags: str
    seeding_time_limit: int
    ratio_limit: float


def _resolve_qbittorrent_exception_type(candidate: object) -> type[Exception]:
    if isinstance(candidate, type) and issubclass(candidate, Exception):
        return candidate
    return RuntimeError


_QBittorrentApiError = _resolve_qbittorrent_exception_type(_ImportedQBittorrentApiError)
_QBittorrentLoginFailed = _resolve_qbittorrent_exception_type(_ImportedQBittorrentLoginFailed)
_QBITTORRENT_CLIENT_ERRORS = (
    _QBittorrentLoginFailed,
    _QBittorrentApiError,
    AttributeError,
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
)


def _hashes_match(hash1: str, hash2: str) -> bool:
    """Compare hashes, handling Amarr's 40-char zero-padded hashes vs 32-char ed2k hashes."""
    h1, h2 = hash1.lower(), hash2.lower()
    if h1 == h2:
        return True
    if len(h1) == _HASH_LENGTH_40 and len(h2) == _HASH_LENGTH_ED2K and h1.endswith("00000000"):
        return h1[:_HASH_LENGTH_ED2K] == h2
    if len(h2) == _HASH_LENGTH_40 and len(h1) == _HASH_LENGTH_ED2K and h2.endswith("00000000"):
        return h2[:_HASH_LENGTH_ED2K] == h1
    return False


def _torrent_matches_download_id(torrent: object, download_id: str) -> bool:
    """Match an ID against every identity qBittorrent exposes.

    For hybrid torrents, qBittorrent's primary `hash` can change from the v1
    hash to the truncated v2 hash after metadata resolution. The full
    `infohash_v1` and `infohash_v2` fields preserve the torrent's identities.
    """
    identifiers = (
        getattr(torrent, "hash", None),
        getattr(torrent, "infohash_v1", None),
        getattr(torrent, "infohash_v2", None),
    )

    return any(
        isinstance(identifier, str) and identifier and _hashes_match(identifier, download_id)
        for identifier in identifiers
    )


def _raise_runtime_error(message: str) -> NoReturn:
    raise RuntimeError(message)


def _normalize_tags(raw_tags: object) -> list[str]:
    """Normalize tag input to a clean, de-duplicated list of strings."""
    if raw_tags is None:
        return []

    if isinstance(raw_tags, str):
        parts = [part.strip() for part in raw_tags.split(",")]
    elif isinstance(raw_tags, (list, tuple, set)):
        parts = []
        for item in raw_tags:
            if item is None:
                continue
            parts.append(str(item).strip())
    else:
        parts = [str(raw_tags).strip()] if raw_tags else []

    tags: list[str] = []
    seen = set()
    for part in parts:
        if not part:
            continue
        if part in seen:
            continue
        seen.add(part)
        tags.append(part)

    return tags


def _normalize_add_result(raw_result: object) -> str:
    """Normalize qBittorrent add responses to a comparable string."""
    if raw_result is None:
        return ""

    if isinstance(raw_result, bytes):
        return raw_result.decode("utf-8", errors="replace").strip()

    return str(raw_result).strip()


def _is_explicit_add_failure(raw_result: object) -> bool:
    """Detect add responses that clearly indicate failure."""
    normalized = _normalize_add_result(raw_result).rstrip(".").lower()
    return normalized in {"fail", "fails", "error", "errors"}


def _build_qbittorrent_child_path(base_path: object, child_path: object) -> str | None:
    """Build a qBittorrent-reported child path without allowing escape from base."""
    if not isinstance(base_path, str) or not base_path:
        return None
    if not isinstance(child_path, str) or not child_path:
        return None

    child = child_path.replace("\\", "/")
    posix_child = PurePosixPath(child)
    windows_child = PureWindowsPath(child_path)
    if posix_child.is_absolute() or windows_child.is_absolute() or windows_child.drive:
        return None
    if any(part == ".." for part in posix_child.parts):
        return None

    return os.path.normpath(str(Path(base_path) / child))


@register_client("torrent")
class QBittorrentClient(DownloadClient):
    """qBittorrent download client."""

    protocol = "torrent"
    name = "qbittorrent"

    def __init__(self) -> None:
        """Initialize qBittorrent client with settings from config."""
        # Lazy import to avoid dependency issues if not using torrents
        from qbittorrentapi import Client

        raw_url = config.get("QBITTORRENT_URL", "")
        if not raw_url:
            msg = "QBITTORRENT_URL is required"
            raise ValueError(msg)

        # We use `_base_url` for direct HTTP calls, so it must be a fully-qualified URL.
        self._base_url = normalize_http_config_url(raw_url, require_string=True)
        if not self._base_url:
            msg = "QBITTORRENT_URL is invalid"
            raise ValueError(msg)

        username = config_text(config.get("QBITTORRENT_USERNAME", ""))
        password = config_text(config.get("QBITTORRENT_PASSWORD", ""))
        self._api_key = config_text(config.get("QBITTORRENT_API_KEY", ""))

        # qbittorrent-api accepts either a full URL or host:port; prefer the normalized URL
        # for consistency. It ignores the URL's scheme and probes HTTP and HTTPS instead,
        # which can settle on plain HTTP against a TLS port behind a reverse proxy (#1417),
        # so an explicit https:// is kept. http:// is still probed: normalization adds it to
        # a bare host:port, and the probe follows a proxy's redirect to HTTPS.
        self._client = Client(
            host=self._base_url,
            username=username,
            password=password,
            api_key=self._api_key or None,
            VERIFY_WEBUI_CERTIFICATE=get_ssl_verify(self._base_url),
            FORCE_SCHEME_FROM_HOST=self._base_url.lower().startswith("https://"),
        )
        self._category = config_text(config.get("QBITTORRENT_CATEGORY", "books"))
        self._download_dir = config_text(config.get("QBITTORRENT_DOWNLOAD_DIR", ""))
        self._tags = _normalize_tags(config.get("QBITTORRENT_TAG", []))
        # download_id -> qBittorrent's current primary hash, for identities that no
        # longer match it directly. See _resolve_torrent().
        self._primary_hashes: dict[str, str] = {}

    @property
    def _can_reauthenticate(self) -> bool:
        """Whether a 403 is worth retrying; a bearer token cannot be refreshed like a session."""
        return not self._api_key

    def _ensure_authenticated(self) -> None:
        """Authenticate the underlying HTTP session before it is used directly.

        API keys (qBittorrent 5.2.0+) are sent as a bearer header on every request and
        have no login endpoint, so there is no session to establish up front.
        """
        if self._api_key:
            return
        self._client.auth_log_in()

    def _request_torrent_info_records(
        self, params: dict[str, str]
    ) -> tuple[list[SimpleNamespace], str | None]:
        """Request torrent info records from qBittorrent."""
        url = f"{self._base_url}/api/v2/torrents/info"
        try:
            self._ensure_authenticated()
            response = self._client._session.get(url, params=params, timeout=10)
            if response.status_code == _HTTP_STATUS_FORBIDDEN and self._can_reauthenticate:
                logger.debug("qBittorrent returned 403; re-authenticating and retrying")
                self._ensure_authenticated()
                response = self._client._session.get(url, params=params, timeout=10)

            if response.status_code == _HTTP_STATUS_FORBIDDEN:
                logger.warning("qBittorrent authentication failed (HTTP 403)")
                return [], "qBittorrent authentication failed (HTTP 403)"

            response.raise_for_status()
            torrents = response.json()
            return [SimpleNamespace(**t) for t in torrents], None
        except requests.exceptions.HTTPError as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status:
                logger.warning("qBittorrent API error (HTTP %s): %s", status, e)
                return [], f"qBittorrent API request failed (HTTP {status})"

            logger.warning("qBittorrent API error: %s", e)
            return [], "qBittorrent API request failed"
        except requests.exceptions.ConnectionError:
            logger.warning("Cannot connect to qBittorrent at %s", self._base_url)
            return [], f"Cannot connect to qBittorrent at {self._base_url}"
        except requests.exceptions.Timeout:
            logger.warning("qBittorrent request timed out at %s", self._base_url)
            return [], f"qBittorrent request timed out at {self._base_url}"
        except requests.exceptions.InvalidSchema:
            logger.debug("Failed to get torrents info: invalid qBittorrent URL: %s", self._base_url)
            return (
                [],
                "qBittorrent URL is invalid (missing http:// or https://). "
                f"Configured: {self._base_url}",
            )
        except _QBITTORRENT_CLIENT_ERRORS as e:
            logger.debug("Failed to get torrents info: %s", e)
            return [], f"qBittorrent API error: {type(e).__name__}: {e}"

    def _get_torrent_info(self, download_id: str) -> tuple[SimpleNamespace | None, str | None]:
        """Get one torrent by its current qBittorrent hash."""
        torrents, error = self._request_torrent_info_records({"hashes": download_id})
        if error or not torrents:
            return None, error
        return (
            next(
                (
                    torrent
                    for torrent in torrents
                    if isinstance(getattr(torrent, "hash", None), str)
                    and _hashes_match(torrent.hash, download_id)
                ),
                None,
            ),
            None,
        )

    def _list_torrents_by_category(
        self, category: str | None
    ) -> tuple[list[SimpleNamespace], str | None]:
        """List torrent records in a category, or all records when unset."""
        params = {"category": category} if category else {}
        return self._request_torrent_info_records(params)

    def _remember_primary_hash(self, download_id: str, torrent: SimpleNamespace) -> None:
        """Note the primary hash a listing scan found, so later lookups skip the scan."""
        torrent_hash = getattr(torrent, "hash", None)
        if isinstance(torrent_hash, str) and torrent_hash:
            self._primary_hashes[download_id.lower()] = torrent_hash.lower()

    def _resolve_torrent(
        self, download_id: str, category: str | None = None
    ) -> tuple[SimpleNamespace | None, str | None]:
        """Resolve any known torrent identity to its current qBittorrent record.

        A hybrid torrent's primary hash switches from the v1 hash to the truncated v2
        hash once metadata resolves, so a download tracked by its v1 hash misses the
        `hashes=` lookup and falls through to a full listing. Since `get_status()`
        polls every couple of seconds for the life of the download, remember the
        primary hash a scan finds and try it first.
        """
        cached = self._primary_hashes.get(download_id.lower())
        for candidate in (item for item in dict.fromkeys((cached, download_id)) if item):
            torrent, error = self._get_torrent_info(candidate)
            if error:
                return None, error
            if torrent:
                self._remember_primary_hash(download_id, torrent)
                return torrent, None

        categories = [candidate for candidate in (category, self._category) if candidate]
        for candidate in dict.fromkeys(categories):
            torrents, error = self._list_torrents_by_category(candidate)
            if error:
                return None, error
            torrent = next(
                (item for item in torrents if _torrent_matches_download_id(item, download_id)),
                None,
            )
            if torrent:
                self._remember_primary_hash(download_id, torrent)
                return torrent, None

        torrents, error = self._list_torrents_by_category(None)
        if error:
            return None, error
        torrent = next(
            (item for item in torrents if _torrent_matches_download_id(item, download_id)),
            None,
        )
        if torrent:
            self._remember_primary_hash(download_id, torrent)
        else:
            # The torrent is gone; drop the note so a re-add is not looked up by a
            # hash that no longer exists.
            self._primary_hashes.pop(download_id.lower(), None)
        return torrent, None

    def _current_hash(self, download_id: str) -> str:
        """qBittorrent's current primary hash for any identity we know the torrent by.

        Falls back to the given ID when the torrent cannot be found, so callers
        still address the hash they were handed and surface the client's error.
        """
        try:
            torrent, error = self._resolve_torrent(download_id)
        except _QBITTORRENT_CLIENT_ERRORS as e:
            logger.debug("Could not resolve current hash for %s: %s", download_id, e)
            return download_id
        if error or not torrent:
            return download_id
        torrent_hash = getattr(torrent, "hash", None)
        if isinstance(torrent_hash, str) and torrent_hash:
            return torrent_hash
        return download_id

    def _list_category_hashes(self, category: str | None) -> set[str] | None:
        """Snapshot the hashes qBittorrent currently reports for a category."""
        torrents, error = self._list_torrents_by_category(category)
        if error:
            logger.debug("Could not snapshot qBittorrent torrents: %s", error)
            return None
        return {str(torrent.hash).lower() for torrent in torrents if getattr(torrent, "hash", None)}

    def _discover_added_torrent_hash(
        self,
        name: str,
        category: str | None,
        known_hashes: set[str] | None,
    ) -> str | None:
        """Recover the hash of a torrent that was added without a known info_hash.

        A `known_hashes` of None means the pre-add snapshot failed, so only a
        torrent matching the requested rename can identify the new arrival.
        """
        for _ in range(20):
            torrents, error = self._list_torrents_by_category(category)
            if error:
                logger.debug("qBittorrent hash discovery: %s", error)
            else:
                new_torrents = [
                    torrent
                    for torrent in torrents
                    if getattr(torrent, "hash", None)
                    and (known_hashes is None or str(torrent.hash).lower() not in known_hashes)
                ]
                for torrent in new_torrents:
                    if getattr(torrent, "name", None) == name:
                        return str(torrent.hash).lower()
                if known_hashes is not None and len(new_torrents) == 1:
                    return str(new_torrents[0].hash).lower()
            time.sleep(0.5)
        return None

    @staticmethod
    def is_configured() -> bool:
        """Check if qBittorrent is configured and selected as the torrent client."""
        client = config_text(config.get("PROWLARR_TORRENT_CLIENT", ""))
        url = normalize_http_config_url(config.get("QBITTORRENT_URL", ""), require_string=True)
        return client == "qbittorrent" and bool(url)

    def test_connection(self) -> tuple[bool, str]:
        """Test connection to qBittorrent."""
        try:
            self._ensure_authenticated()
            api_version = self._client.app.web_api_version
        except _QBITTORRENT_CLIENT_ERRORS as e:
            return False, f"Connection failed: {e!s}"
        else:
            return True, f"Connected to qBittorrent (API v{api_version})"

    def add_download(
        self,
        url: str,
        name: str,
        category: str | None = None,
        expected_hash: str | None = None,
        **kwargs: object,
    ) -> str:
        """Add torrent by URL (magnet or .torrent).

        Args:
            url: Magnet link or .torrent URL
            name: Display name for the torrent
            category: Category for organization (uses configured default if not specified)
            expected_hash: Optional info_hash hint (from Prowlarr)
            **kwargs: Client-specific options passed through to the implementation.

        Returns:
            Torrent hash (info_hash).

        Raises:
            Exception: If adding fails.

        """
        try:
            # Use configured category if not explicitly provided
            category = category or self._category
            tags = self._tags
            seeding_time_limit: int | None = None
            ratio_limit: float | None = None

            # Ensure category exists (may already exist, which is fine)
            if category:
                try:
                    self._client.torrents_create_category(name=category)
                except _QBITTORRENT_CLIENT_ERRORS as e:
                    # Conflict409Error means category exists - that's expected
                    # Log other errors but continue since download may still work
                    if "Conflict" not in type(e).__name__ and "409" not in str(e):
                        logger.debug(
                            "Could not create category '%s': %s: %s",
                            category,
                            type(e).__name__,
                            e,
                        )

            torrent_info = extract_torrent_info(url, expected_hash=expected_hash)
            expected_hash = torrent_info.info_hash
            torrent_data = torrent_info.torrent_data

            known_hashes: set[str] | None = None
            if not expected_hash:
                known_hashes = self._list_category_hashes(category)

            # Per-torrent seeding limits from indexer
            seeding_time_limit_value = kwargs.get("seeding_time_limit")
            seeding_time_limit = coerce_optional_int(seeding_time_limit_value)
            ratio_limit_value = kwargs.get("ratio_limit")
            ratio_limit = coerce_optional_float(ratio_limit_value)

            add_kwargs: _QBittorrentAddKwargs = {"rename": name}
            if category:
                add_kwargs["category"] = category
            if self._download_dir:
                add_kwargs["save_path"] = self._download_dir
            if tags:
                add_kwargs["tags"] = ",".join(tags)
            if seeding_time_limit is not None:
                add_kwargs["seeding_time_limit"] = seeding_time_limit
            if ratio_limit is not None:
                add_kwargs["ratio_limit"] = ratio_limit

            if torrent_data:
                result = self._client.torrents_add(
                    torrent_files=torrent_data,
                    **add_kwargs,
                )
            else:
                # Use magnet URL if available, otherwise original URL
                add_url = torrent_info.magnet_url or url
                result = self._client.torrents_add(
                    urls=add_url,
                    **add_kwargs,
                )

            result_text = _normalize_add_result(result)
            logger.debug("qBittorrent add result: %s", result_text)

            if _is_explicit_add_failure(result):
                _raise_runtime_error(f"Failed to add torrent: {result_text}")

            if not expected_hash:
                # qBittorrent fetches .torrent URLs itself, so the add can succeed
                # even when no hash could be extracted up front. Recover it by
                # watching for the new torrent to appear.
                expected_hash = self._discover_added_torrent_hash(name, category, known_hashes)
            if not expected_hash:
                message = "Could not determine torrent hash from URL"
                if torrent_info.fetch_error:
                    message = f"{message} (torrent file fetch failed: {torrent_info.fetch_error})"
                _raise_runtime_error(message)

            # Prefer qBittorrent's primary torrent ID, which for hybrid torrents
            # switches from the v1 hash to the truncated v2 hash once metadata
            # resolves. A magnet with few peers can take minutes to fetch metadata,
            # and the torrent is worth keeping in the meantime: every lookup goes
            # through `_resolve_torrent`, which still matches the v1 hash against
            # `infohash_v1` after the primary ID has changed.
            for _ in range(_METADATA_WAIT_POLLS):
                torrent, error = self._resolve_torrent(expected_hash, category)
                if error:
                    logger.debug("qBittorrent add_download: %s", error)
                elif torrent and getattr(torrent, "state", None) not in _METADATA_DOWNLOAD_STATES:
                    torrent_hash = getattr(torrent, "hash", None)
                    if isinstance(torrent_hash, str) and torrent_hash:
                        logger.info("Added torrent: %s", torrent_hash)
                        return torrent_hash.lower()
                time.sleep(_METADATA_WAIT_INTERVAL_SECONDS)

            logger.info(
                "Added torrent %s; metadata still pending after %.0fs, tracking it by info hash",
                expected_hash,
                _METADATA_WAIT_POLLS * _METADATA_WAIT_INTERVAL_SECONDS,
            )
        except _QBITTORRENT_CLIENT_ERRORS:
            logger.exception("qBittorrent add failed")
            raise
        else:
            return expected_hash.lower()

    def get_status(self, download_id: str) -> DownloadStatus:
        """Get torrent status by hash.

        Args:
            download_id: Torrent info_hash

        Returns:
            Current download status.

        """
        try:
            torrent, error = self._resolve_torrent(download_id)
            if error:
                return DownloadStatus.error(error)
            if not torrent:
                return DownloadStatus.error("Torrent not found in qBittorrent")

            # Map qBittorrent states to our states and user-friendly messages
            state_info = {
                "downloading": (
                    "downloading",
                    None,
                ),  # None = use default progress message
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

            torrent_state = getattr(torrent, "state", "unknown")
            state, message = state_info.get(torrent_state, ("unknown", str(torrent_state)))

            torrent_progress = getattr(torrent, "progress", 0.0)
            # Don't mark complete while files are being moved to final location
            # (qBittorrent moves files from incomplete → complete folder)
            complete = torrent_progress >= 1.0 and torrent_state != "moving"

            # For active downloads without a special message, leave message as None
            # so the handler can build the progress message
            if complete:
                message = "Complete"

            torrent_eta = getattr(torrent, "eta", 0)
            eta = (
                torrent_eta
                if isinstance(torrent_eta, int) and 0 < torrent_eta < _ONE_WEEK_IN_SECONDS
                else None
            )

            # Get file path for completed downloads
            file_path = None
            if complete:
                file_path = self._resolve_completed_download_path(torrent)

            torrent_speed = getattr(torrent, "dlspeed", None)
            torrent_speed = torrent_speed if isinstance(torrent_speed, int) else None

            return DownloadStatus(
                progress=float(torrent_progress) * 100,
                state="complete" if complete else state,
                message=message,
                complete=complete,
                file_path=file_path,
                download_speed=torrent_speed,
                eta=eta,
            )
        except _QBITTORRENT_CLIENT_ERRORS as e:
            return DownloadStatus.error(self._log_error("get_status", e))

    def remove(self, download_id: str, *, delete_files: bool = False) -> bool:
        """Remove a torrent from qBittorrent.

        Args:
            download_id: Torrent info_hash
            delete_files: Whether to also delete files

        Returns:
            True if successful.

        """
        try:
            torrent_hash = self._current_hash(download_id)
            self._client.torrents_delete(torrent_hashes=torrent_hash, delete_files=delete_files)
            self._primary_hashes.pop(download_id.lower(), None)
            logger.info(
                "Removed torrent from qBittorrent: %s%s",
                download_id,
                " (with files)" if delete_files else "",
            )
        except _QBITTORRENT_CLIENT_ERRORS as e:
            self._log_error("remove", e)
            return False
        else:
            return True

    def set_category(self, download_id: str, category: str) -> bool:
        """Assign a category to a torrent in qBittorrent."""
        try:
            try:
                self._client.torrents_create_category(name=category)
            except _QBITTORRENT_CLIENT_ERRORS as e:
                if "Conflict" not in type(e).__name__ and "409" not in str(e):
                    logger.debug("Could not create category '%s': %s", category, e)

            self._client.torrents_set_category(
                torrent_hashes=self._current_hash(download_id),
                category=category,
            )
            logger.info("Set qBittorrent category for %s to '%s'", download_id, category)
        except _QBITTORRENT_CLIENT_ERRORS as e:
            self._log_error("set_category", e)
            return False
        else:
            return True

    def get_download_path(self, download_id: str) -> str | None:
        """Get the path where torrent files are located.

        Prefer `content_path` when available.

        When `content_path` is missing (commonly with qBittorrent-like emulators such
        as Amarr), derive the path using:
        - `/api/v2/torrents/properties?hash=<hash>` for `save_path`
        - `/api/v2/torrents/files?hash=<hash>` for the first file name
        - join `save_path` with the torrent's top-level directory
        """
        try:
            torrent, error = self._resolve_torrent(download_id)
            if error:
                logger.debug("qBittorrent get_download_path: %s", error)
                return None
            if not torrent:
                return None

            return self._resolve_completed_download_path(torrent)
        except _QBITTORRENT_CLIENT_ERRORS as e:
            self._log_error("get_download_path", e, level="debug")
            return None

    def _resolve_completed_download_path(self, torrent: SimpleNamespace) -> str | None:
        """Resolve the completed path for a torrent.

        Centralizes the logic shared by `get_status()` and `get_download_path()`:
        - accept `content_path` only when it's not equal to `save_path`
        - otherwise derive via properties+files
        - finally fall back to `save_path + name`
        """
        # Prefer content_path, but treat content_path == save_path as invalid.
        content_path = getattr(torrent, "content_path", "")
        save_path = getattr(torrent, "save_path", "")
        if content_path and (not save_path or str(content_path) != str(save_path)):
            return str(content_path)

        download_id = getattr(torrent, "hash", "")
        if isinstance(download_id, str) and download_id:
            derived = self._derive_download_path_from_files(download_id)
            if derived and not isinstance(derived, _UnsafeQBittorrentPath):
                return derived

        # Legacy fallback: save_path + name (for older clients/emulators)
        return _build_qbittorrent_child_path(
            getattr(torrent, "save_path", ""),
            getattr(torrent, "name", ""),
        )

    def _derive_download_path_from_files(
        self, download_id: str
    ) -> str | _UnsafeQBittorrentPath | None:
        """Derive completed download path using `/torrents/properties` + `/torrents/files`.

        This mirrors how common automation apps derive the path when
        `content_path` isn't provided.
        """
        import os

        def get_with_auth(url: str, params: dict[str, str]) -> requests.Response:
            self._ensure_authenticated()
            resp = self._client._session.get(url, params=params, timeout=10)
            if resp.status_code == _HTTP_STATUS_FORBIDDEN and self._can_reauthenticate:
                logger.debug("qBittorrent returned 403; re-authenticating and retrying")
                self._ensure_authenticated()
                resp = self._client._session.get(url, params=params, timeout=10)
            return resp

        try:
            properties_url = f"{self._base_url}/api/v2/torrents/properties"
            files_url = f"{self._base_url}/api/v2/torrents/files"

            props_resp = get_with_auth(properties_url, {"hash": download_id})
            if props_resp.status_code == _HTTP_STATUS_NOT_FOUND:
                return None
            props_resp.raise_for_status()
            props = props_resp.json() if isinstance(props_resp.json(), dict) else {}

            save_path = props.get("save_path") or props.get("savePath") or ""
            if not isinstance(save_path, str) or not save_path:
                return None

            files_resp = get_with_auth(files_url, {"hash": download_id})
            if files_resp.status_code == _HTTP_STATUS_NOT_FOUND:
                return None
            files_resp.raise_for_status()
            files = files_resp.json() if isinstance(files_resp.json(), list) else []
            if not files:
                return None

            first_name = files[0].get("name") if isinstance(files[0], dict) else None
            if not isinstance(first_name, str) or not first_name:
                return None

            # Get the first path segment (qBittorrent returns '/' even on Windows).
            first_name_norm = first_name.replace("\\", "/")
            top_level = first_name_norm.split("/", 1)[0]
            if not top_level:
                return _UNSAFE_QBITTORRENT_PATH

            derived = _build_qbittorrent_child_path(save_path, top_level)
            if derived is None:
                return _UNSAFE_QBITTORRENT_PATH
            return os.path.normpath(derived)
        except _QBITTORRENT_CLIENT_ERRORS as e:
            logger.debug(
                "qBittorrent could not derive path from files: %s: %s",
                type(e).__name__,
                e,
            )
            return None

    def _await_existing_torrent(
        self, info_hash: str, category: str | None
    ) -> tuple[str, DownloadStatus] | None:
        """Report a torrent already in qBittorrent, waiting out magnet metadata first."""
        for _ in range(_METADATA_WAIT_POLLS):
            torrent, error = self._resolve_torrent(info_hash, category)
            if error:
                logger.debug("qBittorrent find_existing: %s", error)
                return None
            if not torrent:
                return None
            if getattr(torrent, "state", None) not in _METADATA_DOWNLOAD_STATES:
                torrent_hash = getattr(torrent, "hash", None)
                if isinstance(torrent_hash, str) and torrent_hash:
                    torrent_hash = torrent_hash.lower()
                    return (torrent_hash, self.get_status(torrent_hash))
            time.sleep(_METADATA_WAIT_INTERVAL_SECONDS)

        # Metadata is still pending, but the torrent is here and `add_download` keeps
        # one in this state rather than giving up. Report it by info hash so the
        # caller joins the download in progress instead of adding a duplicate.
        logger.info(
            "Existing torrent %s is still fetching metadata; joining it by info hash",
            info_hash,
        )
        return (info_hash.lower(), self.get_status(info_hash))

    def find_existing(
        self, url: str, category: str | None = None
    ) -> tuple[str, DownloadStatus] | None:
        """Check if a torrent for this URL already exists in qBittorrent."""
        try:
            torrent_info = extract_torrent_info(url)
            if not torrent_info.info_hash:
                return None

            existing = self._await_existing_torrent(torrent_info.info_hash, category)
        except _QBITTORRENT_CLIENT_ERRORS as e:
            logger.debug("Error checking for existing torrent: %s", e)
            return None
        else:
            return existing
