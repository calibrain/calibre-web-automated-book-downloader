"""rTorrent download client for Prowlarr integration.

Uses xmlrpc to communicate with rTorrent's RPC interface.
"""

import ssl
import time
import xmlrpc.client as stdlib_xmlrpc_client
from typing import Any, NoReturn, Protocol, cast
from urllib.parse import urlparse

from shelfmark.core.config import config
from shelfmark.core.logger import setup_logger
from shelfmark.core.utils import (
    get_hardened_xmlrpc_client,
)
from shelfmark.core.utils import (
    is_audiobook as check_audiobook,
)
from shelfmark.download.clients import (
    DownloadClient,
    DownloadStatus,
    register_client,
)
from shelfmark.download.clients._coercion import config_text, normalize_http_config_url
from shelfmark.download.clients.torrent_utils import (
    extract_torrent_info,
)
from shelfmark.download.network import get_ssl_verify

logger = setup_logger(__name__)


_ETA_MAX_SECONDS = 604800
_RTORRENT_CLIENT_ERRORS = (
    AttributeError,
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
    stdlib_xmlrpc_client.Error,
)


class _RTorrentSystemProtocol(Protocol):
    def client_version(self) -> object: ...


class _RTorrentLoadProtocol(Protocol):
    def raw_start(self, target: str, torrent_data: bytes, commands: str) -> object: ...

    def start(self, target: str, url: str, commands: str) -> object: ...


class _RTorrentCustom1Protocol(Protocol):
    def set(self, download_id: str, value: str) -> object: ...


class _RTorrentDownloadProtocol(Protocol):
    custom1: _RTorrentCustom1Protocol

    def multicall2(self, *args: object) -> list[list[Any]]: ...

    def erase(self, download_id: str) -> object: ...

    def stop(self, download_id: str) -> object: ...


class _RTorrentDirectoryProtocol(Protocol):
    def default(self) -> str: ...


class _RTorrentRpcProtocol(Protocol):
    system: _RTorrentSystemProtocol
    load: _RTorrentLoadProtocol
    d: _RTorrentDownloadProtocol
    directory: _RTorrentDirectoryProtocol


def _create_rtorrent_server_proxy(url: str) -> _RTorrentRpcProtocol:
    """Create an XML-RPC ServerProxy honoring certificate validation mode."""
    xmlrpc_client = get_hardened_xmlrpc_client()

    verify = get_ssl_verify(url)
    if url.startswith("https://") and not verify:
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        transport = xmlrpc_client.SafeTransport(context=ssl_context)
        return cast(_RTorrentRpcProtocol, xmlrpc_client.ServerProxy(url, transport=transport))

    return cast(_RTorrentRpcProtocol, xmlrpc_client.ServerProxy(url))


def _raise_runtime_error(message: str) -> NoReturn:
    raise RuntimeError(message)


@register_client("torrent")
class RTorrentClient(DownloadClient):
    """rTorrent download client using xmlrpc."""

    protocol = "torrent"
    name = "rtorrent"

    def __init__(self) -> None:
        """Initialize rTorrent client with settings from config."""
        raw_url = config_text(config.get("RTORRENT_URL", ""))
        if not raw_url:
            msg = "RTORRENT_URL is required"
            raise ValueError(msg)

        self._base_url = normalize_http_config_url(raw_url)
        if not self._base_url:
            msg = "RTORRENT_URL is invalid"
            raise ValueError(msg)

        username = config_text(config.get("RTORRENT_USERNAME", ""))
        password = config_text(config.get("RTORRENT_PASSWORD", ""))

        if username and password:
            parsed = urlparse(self._base_url)
            self._base_url = f"{parsed.scheme}://{username}:{password}@{parsed.netloc}{parsed.path}"

        self._rpc = _create_rtorrent_server_proxy(self._base_url)
        self._download_dir = config_text(config.get("RTORRENT_DOWNLOAD_DIR", ""))
        self._label = config_text(config.get("RTORRENT_LABEL", ""))
        self._audiobook_label = config_text(config.get("RTORRENT_AUDIOBOOK_LABEL", ""))

    @staticmethod
    def is_configured() -> bool:
        """Check if rTorrent is configured and selected as the torrent client."""
        client = config_text(config.get("PROWLARR_TORRENT_CLIENT", ""))
        url = normalize_http_config_url(config.get("RTORRENT_URL", ""))
        return client == "rtorrent" and bool(url)

    def test_connection(self) -> tuple[bool, str]:
        """Test connection to rTorrent."""
        try:
            version = self._rpc.system.client_version()
        except _RTORRENT_CLIENT_ERRORS as e:
            return False, f"Connection failed: {e!s}"
        else:
            return True, f"Connected to rTorrent {version}"

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
            category: Category for organization (uses configured label if not specified)
            expected_hash: Optional info_hash hint (from Prowlarr)
            **kwargs: Client-specific options passed through to the implementation.

        Returns:
            Torrent hash (info_hash).

        Raises:
            Exception: If adding fails.

        """
        try:
            torrent_info = extract_torrent_info(url, expected_hash=expected_hash)

            known_hashes: set[str] | None = None
            if not (torrent_info.info_hash or expected_hash):
                known_hashes = self._list_torrent_hashes()

            commands = []

            content_type = kwargs.get("content_type")
            is_audiobook = check_audiobook(content_type if isinstance(content_type, str) else None)
            default_label = (
                self._audiobook_label if is_audiobook and self._audiobook_label else self._label
            )
            label = category or default_label
            if label:
                logger.debug("Setting rTorrent label: %s", label)
                commands.append(f"d.custom1.set={label}")

            download_dir = self._download_dir or self._get_download_dir()
            if download_dir:
                logger.debug("Setting rTorrent download directory: %s", download_dir)
                commands.append(f"d.directory.set={download_dir}")

            if torrent_info.torrent_data:
                logger.debug(
                    "Adding torrent data directly to rTorrent for: %s with commands: %s with data size: %s",
                    name,
                    commands,
                    len(torrent_info.torrent_data),
                )
                self._rpc.load.raw_start("", torrent_info.torrent_data, ";".join(commands))
            else:
                logger.debug(
                    "Adding torrent URL to rTorrent for: %s with commands: %s with URL: %s",
                    name,
                    commands,
                    url,
                )
                add_url = torrent_info.magnet_url or url
                self._rpc.load.start("", add_url, ";".join(commands))

            torrent_hash = torrent_info.info_hash or expected_hash
            if not torrent_hash:
                # rTorrent fetches .torrent URLs itself, so the add can succeed
                # even when no hash could be extracted up front. Recover it by
                # watching for the new download to appear.
                torrent_hash = self._discover_added_torrent_hash(name, label, known_hashes)
            if not torrent_hash:
                message = "Could not determine torrent hash from URL"
                if torrent_info.fetch_error:
                    message = f"{message} (torrent file fetch failed: {torrent_info.fetch_error})"
                _raise_runtime_error(message)

            logger.debug("Added torrent to rTorrent: %s", torrent_hash)

        except _RTORRENT_CLIENT_ERRORS:
            logger.exception("rTorrent add failed")
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
            # rtorrent is somehow case sensitive and requires uppercase hashes for look
            download_id = download_id.upper()
            all_torrents = self._rpc.d.multicall2(
                "",
                "",
                "d.hash=",
                "d.state=",
                "d.completed_bytes=",
                "d.size_bytes=",
                "d.down.rate=",
                "d.up.rate=",
                "d.custom1=",
                "d.complete=",
            )
            torrent_list = [t for t in all_torrents if t and t[0] == download_id]
            logger.debug(
                "Fetched torrent status from rTorrent for: %s - %s",
                download_id,
                torrent_list,
            )
            if not torrent_list:
                logger.warning("Torrent not found in rTorrent: %s", download_id)
                return DownloadStatus.error("Torrent not found")

            torrent = torrent_list[0]
            if not torrent:
                logger.warning("Torrent data is empty for: %s", download_id)
                return DownloadStatus.error("Torrent not found")

            logger.debug("Torrent data for %s: %s", download_id, torrent)
            (
                _torrent_hash,
                state,
                bytes_downloaded,
                bytes_total,
                down_rate,
                _up_rate,
                _custom_category,
                complete,
            ) = torrent

            try:
                state = int(state)
            except TypeError, ValueError:
                state = 0

            complete = bool(complete)

            progress = (bytes_downloaded / bytes_total) * 100 if bytes_total > 0 else 0

            bytes_left = max(0, bytes_total - bytes_downloaded)

            state_map = {
                0: ("paused", "Paused"),
                1: ("downloading", "Downloading"),
                2: ("downloading", "Downloading"),
                3: ("downloading", "Downloading"),
                4: ("seeding", "Seeding"),
            }

            state_str, message = state_map.get(state, ("unknown", "Unknown state"))

            if complete and not message:
                message = "Complete"

            eta = None
            if down_rate > 0 and bytes_left > 0:
                eta_seconds = bytes_left / down_rate
                if eta_seconds < _ETA_MAX_SECONDS:
                    eta = int(eta_seconds)

            file_path = None
            if complete:
                file_path = self._get_torrent_path(download_id)

            return DownloadStatus(
                progress=min(100, progress),
                state="complete" if complete else state_str,
                message=message,
                complete=complete,
                file_path=file_path,
                download_speed=down_rate if down_rate > 0 else None,
                eta=eta,
            )

        except _RTORRENT_CLIENT_ERRORS as e:
            error_type = type(e).__name__
            logger.exception("rTorrent get_status failed (%s)", error_type)
            return DownloadStatus.error(f"{error_type}: {e}")

    def remove(self, download_id: str, *, delete_files: bool = False) -> bool:
        """Remove a torrent; rTorrent RPC cannot delete downloaded payloads."""
        if delete_files:
            logger.warning(
                "Cannot remove torrent %s with files: rTorrent RPC does not delete downloaded data",
                download_id,
            )
            return False

        try:
            # rTorrent lookups are case sensitive and require uppercase hashes.
            torrent_hash = download_id.upper()
            self._rpc.d.stop(torrent_hash)
            self._rpc.d.erase(torrent_hash)
            logger.info("Removed torrent from rTorrent: %s", download_id)
        except _RTORRENT_CLIENT_ERRORS as e:
            error_type = type(e).__name__
            logger.exception("rTorrent remove failed (%s)", error_type)
            return False
        else:
            return True

    def set_category(self, download_id: str, category: str) -> bool:
        """Assign a label to a torrent using rTorrent's custom1 field."""
        try:
            # rtorrent is somehow case sensitive and requires uppercase hashes for look
            self._rpc.d.custom1.set(download_id.upper(), category)
            logger.info("Set rTorrent label for %s to '%s'", download_id, category)
        except _RTORRENT_CLIENT_ERRORS as e:
            error_type = type(e).__name__
            logger.exception("rTorrent set_category failed (%s)", error_type)
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
            return self._get_torrent_path(download_id)
        except _RTORRENT_CLIENT_ERRORS as e:
            error_type = type(e).__name__
            logger.debug("rTorrent get_download_path failed (%s): %s", error_type, e)
            return None

    def find_existing(
        self, url: str, category: str | None = None
    ) -> tuple[str, DownloadStatus] | None:
        """Check if a torrent for this URL already exists in rTorrent."""
        try:
            torrent_info = extract_torrent_info(url)
            if not torrent_info.info_hash:
                return None

            try:
                status = self.get_status(torrent_info.info_hash)
                if status.state != DownloadStatus.error("").state:
                    return (torrent_info.info_hash, status)
            except _RTORRENT_CLIENT_ERRORS as exc:
                logger.debug(
                    "Could not fetch existing rTorrent status for %s: %s",
                    torrent_info.info_hash,
                    exc,
                )
        except _RTORRENT_CLIENT_ERRORS as e:
            logger.debug("Error checking for existing torrent: %s", e)
            return None
        else:
            return None

    def _get_download_dir(self) -> str:
        """Get the download directory from rTorrent config."""
        try:
            return self._rpc.directory.default()
        except _RTORRENT_CLIENT_ERRORS:
            return "/downloads"

    def _list_torrent_hashes(self) -> set[str] | None:
        """Snapshot the hashes rTorrent currently reports."""
        try:
            all_torrents = self._rpc.d.multicall2("", "", "d.hash=")
        except _RTORRENT_CLIENT_ERRORS as e:
            logger.debug("Could not snapshot rTorrent downloads: %s", e)
            return None
        return {str(row[0]).lower() for row in all_torrents if row and row[0]}

    def _discover_added_torrent_hash(
        self,
        name: str,
        label: str,
        known_hashes: set[str] | None,
    ) -> str | None:
        """Recover the hash of a torrent that was added without a known info_hash.

        rTorrent fetches .torrent URLs itself, so the add can succeed even when
        no hash could be extracted up front. A `known_hashes` of None means the
        pre-add snapshot failed, so only an exact name match can identify the
        new arrival.
        """
        for _ in range(20):
            try:
                all_torrents = self._rpc.d.multicall2("", "", "d.hash=", "d.name=", "d.custom1=")
            except _RTORRENT_CLIENT_ERRORS as e:
                logger.debug("rTorrent hash discovery: %s", e)
            else:
                new_torrents = [
                    row
                    for row in all_torrents
                    if row
                    and row[0]
                    and (known_hashes is None or str(row[0]).lower() not in known_hashes)
                ]
                # The label set at add time distinguishes concurrent arrivals,
                # but rTorrent may not have applied it yet, so it only ever
                # narrows a non-empty candidate list.
                if label:
                    labeled = [row for row in new_torrents if len(row) > 2 and row[2] == label]
                    if labeled:
                        new_torrents = labeled
                for row in new_torrents:
                    if len(row) > 1 and row[1] == name:
                        return str(row[0]).lower()
                if known_hashes is not None and len(new_torrents) == 1:
                    return str(new_torrents[0][0]).lower()
            time.sleep(0.5)
        return None

    def _get_torrent_path(self, download_id: str) -> str | None:
        """Get the file path of a torrent by hash.

        Uses `d.base_path` for the item output path. In the xmlrpc interface
        this corresponds to `d.get_base_path()`.
        """
        try:
            # rTorrent is case sensitive for hashes; use uppercase as in get_status()
            download_hash = download_id.upper()
            all_torrents = self._rpc.d.multicall2(
                "",
                "",
                "d.hash=",
                "d.base_path=",
            )
            details = [t[1:] for t in all_torrents if t and t[0] == download_hash]
            if not details:
                return None
            path = details[0][0]
        except _RTORRENT_CLIENT_ERRORS:
            return None
        else:
            return str(path) if path else None
