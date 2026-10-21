"""Transmission download client for Prowlarr integration.

Uses the transmission-rpc library to communicate with Transmission's RPC API.
"""

from __future__ import annotations

import importlib
from contextlib import contextmanager, suppress
from typing import TYPE_CHECKING, Protocol, TypeGuard

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
    resolve_download_dir,
)
from shelfmark.download.clients.torrent_utils import (
    extract_torrent_info,
    parse_transmission_url,
)
from shelfmark.download.network import get_ssl_verify

try:
    from transmission_rpc import TransmissionError as _ImportedTransmissionError
except ImportError:
    _ImportedTransmissionError = RuntimeError

if TYPE_CHECKING:
    from collections.abc import Iterator

logger = setup_logger(__name__)

_SEEDING_PROGRESS_PERCENT = 100
_ETA_MAX_SECONDS = 604800
_TransmissionError = (
    _ImportedTransmissionError
    if isinstance(_ImportedTransmissionError, type)
    and issubclass(_ImportedTransmissionError, Exception)
    else RuntimeError
)
_TRANSMISSION_CLIENT_ERRORS = (
    _TransmissionError,
    AttributeError,
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
)


class _TransmissionSessionProtocol(Protocol):
    verify: bool


class _TransmissionSessionFactory(Protocol):
    def __call__(self, *args: object, **kwargs: object) -> _TransmissionSessionProtocol: ...


class _TransmissionRequestsNamespace(Protocol):
    Session: _TransmissionSessionFactory


class _TransmissionProtocolAttribute(Protocol):
    protocol: str


def _is_requests_namespace_with_session(
    candidate: object,
) -> TypeGuard[_TransmissionRequestsNamespace]:
    return hasattr(candidate, "Session") and callable(getattr(candidate, "Session", None))


def _has_protocol_attr(candidate: object) -> TypeGuard[_TransmissionProtocolAttribute]:
    return hasattr(candidate, "protocol")


def _set_transmission_protocol_if_supported(client: object, protocol: str) -> None:
    if protocol != "https" or not _has_protocol_attr(client):
        return
    with suppress(AttributeError, OSError, RuntimeError, TypeError, ValueError):
        client.protocol = protocol


@contextmanager
def _transmission_session_verify_override(url: str) -> Iterator[None]:
    """Temporarily override transmission-rpc's session factory when verify is disabled.

    transmission-rpc performs an RPC call inside Client.__init__, so verify must be
    set before the client is constructed.
    """
    verify = get_ssl_verify(url)
    if verify:
        yield
        return

    try:
        transmission_rpc_client = importlib.import_module("transmission_rpc.client")
        requests_namespace = getattr(transmission_rpc_client, "requests", None)
    except ImportError:
        # If internals differ, gracefully fall back to default behavior.
        yield
        return

    if not _is_requests_namespace_with_session(requests_namespace):
        yield
        return

    original_session_factory = requests_namespace.Session

    def _session_factory(*args: object, **kwargs: object) -> _TransmissionSessionProtocol:
        session = original_session_factory(*args, **kwargs)
        session.verify = False
        return session

    requests_namespace.Session = _session_factory
    try:
        yield
    finally:
        requests_namespace.Session = original_session_factory


def _apply_transmission_ssl_verify(client: object, url: str) -> None:
    """Apply global certificate validation policy to transmission-rpc client."""
    session = getattr(client, "_http_session", None)
    if session is None:
        return
    try:
        session.verify = get_ssl_verify(url)
    except (AttributeError, OSError, TypeError, ValueError) as e:
        logger.debug("Unable to apply Transmission TLS verify setting: %s", e)


@register_client("torrent")
class TransmissionClient(DownloadClient):
    """Transmission download client using transmission-rpc library."""

    protocol = "torrent"
    name = "transmission"

    def __init__(self) -> None:
        """Initialize Transmission client with settings from config."""
        from transmission_rpc import Client

        raw_url = config_text(config.get("TRANSMISSION_URL", ""))
        if not raw_url:
            msg = "TRANSMISSION_URL is required"
            raise ValueError(msg)

        url = normalize_http_config_url(raw_url)
        if not url:
            msg = "TRANSMISSION_URL is invalid"
            raise ValueError(msg)

        username = config_text(config.get("TRANSMISSION_USERNAME", ""))
        password = config_text(config.get("TRANSMISSION_PASSWORD", ""))

        # Parse URL to extract host, port, and path
        protocol, host, port, path = parse_transmission_url(url)

        client_kwargs = {
            "host": host,
            "port": port,
            "path": path,
            "username": username or None,
            "password": password or None,
            "protocol": protocol,
        }
        try:
            with _transmission_session_verify_override(url):
                self._client = Client(**client_kwargs)
        except TypeError as e:
            # Older transmission-rpc versions may not accept protocol as a kwarg.
            if "protocol" not in str(e):
                raise
            client_kwargs.pop("protocol", None)
            with _transmission_session_verify_override(url):
                self._client = Client(**client_kwargs)
            # Some versions expose protocol as an attribute rather than kwarg.
            _set_transmission_protocol_if_supported(self._client, protocol)
        _apply_transmission_ssl_verify(self._client, url)
        self._category = config_text(config.get("TRANSMISSION_CATEGORY", "books"))
        self._download_dir = config_text(config.get("TRANSMISSION_DOWNLOAD_DIR", ""))
        self._separate_download_dirs = bool(
            config.get("TRANSMISSION_SEPARATE_DOWNLOAD_DIRS", False)
        )
        self._download_dir_book = config_text(config.get("TRANSMISSION_DOWNLOAD_DIR_BOOK", ""))
        self._download_dir_audiobook = config_text(
            config.get("TRANSMISSION_DOWNLOAD_DIR_AUDIOBOOK", "")
        )

    @staticmethod
    def is_configured() -> bool:
        """Check if Transmission is configured and selected as the torrent client."""
        client = config_text(config.get("PROWLARR_TORRENT_CLIENT", ""))
        url = normalize_http_config_url(config.get("TRANSMISSION_URL", ""))
        return client == "transmission" and bool(url)

    def test_connection(self) -> tuple[bool, str]:
        """Test connection to Transmission."""
        try:
            session = self._client.get_session()
            version = session.version
        except _TRANSMISSION_CLIENT_ERRORS as e:
            return False, f"Connection failed: {e!s}"
        else:
            return True, f"Connected to Transmission {version}"

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
            resolved_category = category or self._category

            torrent_info = extract_torrent_info(url, expected_hash=expected_hash)
            add_kwargs = {}

            if resolved_category:
                add_kwargs["labels"] = [resolved_category]
            download_dir = resolve_download_dir(
                separate=self._separate_download_dirs,
                shared_dir=self._download_dir,
                book_dir=self._download_dir_book,
                audiobook_dir=self._download_dir_audiobook,
                content_type=kwargs.get("content_type"),
            )
            if download_dir:
                add_kwargs["download_dir"] = download_dir

            if torrent_info.torrent_data:
                torrent = self._client.add_torrent(
                    torrent=torrent_info.torrent_data,
                    **add_kwargs,
                )
            else:
                # Use magnet URL if available, otherwise original URL
                add_url = torrent_info.magnet_url or url
                torrent = self._client.add_torrent(
                    torrent=add_url,
                    **add_kwargs,
                )

            torrent_hash = torrent.hashString.lower()
            logger.info("Added torrent to Transmission: %s", torrent_hash)

            # Apply per-torrent seeding limits from indexer
            seed_kwargs = {}
            seeding_time_limit = coerce_optional_int(kwargs.get("seeding_time_limit"))
            if seeding_time_limit is not None:
                seed_kwargs["seed_idle_limit"] = seeding_time_limit
                seed_kwargs["seed_idle_mode"] = 1  # per-torrent
            ratio_limit = coerce_optional_float(kwargs.get("ratio_limit"))
            if ratio_limit is not None:
                seed_kwargs["seed_ratio_limit"] = ratio_limit
                seed_kwargs["seed_ratio_mode"] = 1  # per-torrent
            if seed_kwargs:
                try:
                    self._client.change_torrent(ids=torrent_hash, **seed_kwargs)
                except _TRANSMISSION_CLIENT_ERRORS as e:
                    logger.warning("Failed to set seeding limits for %s: %s", torrent_hash, e)

        except _TRANSMISSION_CLIENT_ERRORS:
            logger.exception("Transmission add failed")
            raise
        else:
            return torrent_hash

    def get_status(self, download_id: str) -> DownloadStatus:
        """Get torrent status by hash.

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
            status_value = (
                torrent.status.value if hasattr(torrent.status, "value") else str(torrent.status)
            )
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
            # Only mark complete when seeding or stopped (e.g. if seed limit/ratio is 0)
            # and progress is complete. seed pending means files still being moved
            complete = progress >= _SEEDING_PROGRESS_PERCENT and status_value in (
                "seeding",
                "stopped",
            )

            if complete:
                message = "Complete"

            # Get ETA if available and reasonable (less than 1 week)
            eta = None
            if hasattr(torrent, "eta") and torrent.eta:
                eta_seconds = torrent.eta.total_seconds()
                if 0 < eta_seconds < _ETA_MAX_SECONDS:
                    eta = int(eta_seconds)

            # Get download speed
            download_speed = torrent.rate_download if hasattr(torrent, "rate_download") else None

            # Get file path for completed downloads
            file_path = None
            if complete:
                # Output path is downloadDir + torrent name (with ':' replaced)
                torrent_name = getattr(torrent, "name", "")
                if isinstance(torrent_name, str):
                    torrent_name = torrent_name.replace(":", "_")
                file_path = self._build_path(
                    getattr(torrent, "download_dir", ""),
                    torrent_name,
                )

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
            return DownloadStatus.error("Torrent not found")
        except _TRANSMISSION_CLIENT_ERRORS as e:
            return DownloadStatus.error(self._log_error("get_status", e))

    def remove(self, download_id: str, *, delete_files: bool = False) -> bool:
        """Remove a torrent from Transmission.

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
                "Removed torrent from Transmission: %s%s",
                download_id,
                " (with files)" if delete_files else "",
            )
        except _TRANSMISSION_CLIENT_ERRORS as e:
            self._log_error("remove", e)
            return False
        else:
            return True

    def _get_torrent_labels(self, download_id: str) -> list[str]:
        """Return a torrent's current labels, preserving their order."""
        torrent = self._client.get_torrent(download_id)
        raw_labels = getattr(torrent, "labels", None) or []
        return [str(label) for label in raw_labels if str(label)]

    def set_category(self, download_id: str, category: str) -> bool:
        """Add the post-import label to a torrent, keeping labels set elsewhere."""
        try:
            existing_labels = self._get_torrent_labels(download_id)
            if category in existing_labels:
                logger.debug(
                    "Transmission torrent %s already has label '%s'", download_id, category
                )
                return True

            self._client.change_torrent(ids=download_id, labels=[*existing_labels, category])
            logger.info("Added Transmission label '%s' to %s", category, download_id)
        except _TRANSMISSION_CLIENT_ERRORS as e:
            self._log_error("set_category", e)
            return False
        else:
            return True

    def get_download_path(self, download_id: str) -> str | None:
        """Get the path where torrent files are located.

        Args:
            download_id: Torrent info_hash

        Returns:
            Content path (file or directory), or None.

        """
        try:
            torrent = self._client.get_torrent(download_id)
            torrent_name = getattr(torrent, "name", "")
            if isinstance(torrent_name, str):
                torrent_name = torrent_name.replace(":", "_")
            return self._build_path(
                getattr(torrent, "download_dir", ""),
                torrent_name,
            )
        except _TRANSMISSION_CLIENT_ERRORS as e:
            self._log_error("get_download_path", e, level="debug")
            return None

    def find_existing(
        self, url: str, category: str | None = None
    ) -> tuple[str, DownloadStatus] | None:
        """Check if a torrent for this URL already exists in Transmission."""
        try:
            torrent_info = extract_torrent_info(url)
            if not torrent_info.info_hash:
                return None

            try:
                self._client.get_torrent(torrent_info.info_hash)
                status = self.get_status(torrent_info.info_hash)
            except KeyError:
                return None
            else:
                return (torrent_info.info_hash, status)
        except _TRANSMISSION_CLIENT_ERRORS as e:
            logger.debug("Error checking for existing torrent: %s", e)
            return None
