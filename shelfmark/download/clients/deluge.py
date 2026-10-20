"""Deluge download client for Prowlarr integration.

This implementation talks to Deluge via the Web UI JSON-RPC API (``/json``).

Why Web UI API instead of daemon RPC (port 58846)?
- Matches the approach used by common automation apps
- Avoids requiring Deluge daemon ``auth`` file credentials (username/password)

Requirements:
- ``deluge-web`` must be enabled and reachable from Shelfmark
- Deluge Web UI must be connected (or connectable) to a Deluge daemon
"""

import base64
from contextlib import suppress
from typing import Any, NoReturn
from urllib.parse import urlparse

import requests

from shelfmark.core.config import config
from shelfmark.core.logger import setup_logger
from shelfmark.core.utils import normalize_http_url
from shelfmark.download.clients import (
    DownloadClient,
    DownloadStatus,
    register_client,
)
from shelfmark.download.clients._coercion import (
    coerce_optional_float,
    coerce_optional_int,
    config_text,
    resolve_download_dir,
)
from shelfmark.download.clients.torrent_utils import (
    extract_torrent_info,
)
from shelfmark.download.network import get_ssl_verify

logger = setup_logger(__name__)

MIN_DAEMON_HOST_ENTRY_LENGTH = 2
MIN_DAEMON_HOST_STATUS_ENTRY_LENGTH = 4
DOWNLOAD_COMPLETE_PROGRESS = 100
ONE_WEEK_IN_SECONDS = 604800


class DelugeRpcError(RuntimeError):
    """Raised when Deluge returns a JSON-RPC error response."""

    def __init__(self, message: str, code: int | None = None) -> None:
        """Initialize the RPC error with an optional Deluge error code."""
        super().__init__(message)
        self.code = code


_DELUGE_CLIENT_ERRORS = (
    AttributeError,
    DelugeRpcError,
    OSError,
    requests.exceptions.RequestException,
    RuntimeError,
    TypeError,
    ValueError,
)


def _get_error_message(error: object) -> tuple[str, int | None]:
    if isinstance(error, dict):
        return str(error.get("message") or error), error.get("code")
    return str(error), None


def _raise_runtime_error(message: str) -> NoReturn:
    raise RuntimeError(message)


@register_client("torrent")
class DelugeClient(DownloadClient):
    """Deluge download client using Deluge Web UI JSON-RPC."""

    protocol = "torrent"
    name = "deluge"

    def __init__(self) -> None:
        """Initialize the client from the configured Deluge connection settings."""
        raw_host = config_text(config.get("DELUGE_HOST", "localhost"))
        raw_port = config_text(config.get("DELUGE_PORT", "8112"), "8112")
        password = config_text(config.get("DELUGE_PASSWORD", ""))

        if not raw_host:
            msg = "DELUGE_HOST is required"
            raise ValueError(msg)
        if not password:
            msg = "DELUGE_PASSWORD is required"
            raise ValueError(msg)

        scheme = "http"
        base_path = ""

        # Allow DELUGE_HOST to be either a hostname OR a full URL
        # (useful when Deluge is behind a reverse proxy path).
        raw_host = normalize_http_url(raw_host, strip_trailing_slash=False) if raw_host else ""
        if not raw_host:
            msg = "DELUGE_HOST is invalid"
            raise ValueError(msg)

        host = raw_host
        port = int(raw_port)

        if raw_host.startswith(("http://", "https://")):
            parsed = urlparse(raw_host)
            scheme = parsed.scheme or "http"
            host = parsed.hostname or "localhost"
            if parsed.port is not None:
                port = parsed.port
            base_path = (parsed.path or "").rstrip("/")
        # Allow "host:port" in DELUGE_HOST for convenience.
        elif ":" in raw_host and raw_host.count(":") == 1:
            host_part, port_part = raw_host.split(":", 1)
            if host_part and port_part.isdigit():
                host = host_part
                port = int(port_part)

        self._rpc_url = f"{scheme}://{host}:{port}{base_path}/json"
        self._password = password
        self._session = requests.Session()

        self._authenticated = False
        self._connected = False
        self._rpc_id = 0

        self._category = config_text(config.get("DELUGE_CATEGORY", "books"), "books")
        self._download_dir = config_text(config.get("DELUGE_DOWNLOAD_DIR", ""))
        self._separate_download_dirs = bool(config.get("DELUGE_SEPARATE_DOWNLOAD_DIRS", False))
        self._download_dir_book = config_text(config.get("DELUGE_DOWNLOAD_DIR_BOOK", ""))
        self._download_dir_audiobook = config_text(config.get("DELUGE_DOWNLOAD_DIR_AUDIOBOOK", ""))

    def _next_rpc_id(self) -> int:
        self._rpc_id += 1
        return self._rpc_id

    def _rpc_call(self, method: str, *params: object, timeout: int = 15) -> Any:
        payload = {
            "id": self._next_rpc_id(),
            "method": method,
            "params": list(params),
        }

        response = self._session.post(
            self._rpc_url,
            json=payload,
            timeout=timeout,
            verify=get_ssl_verify(self._rpc_url),
        )
        response.raise_for_status()

        data = response.json()
        if data.get("error"):
            message, code = _get_error_message(data["error"])
            raise DelugeRpcError(message, code)

        return data.get("result")

    def _login(self) -> None:
        result = self._rpc_call("auth.login", self._password)
        if result is not True:
            msg = "Deluge Web UI authentication failed"
            raise DelugeRpcError(msg)
        self._authenticated = True

    def _select_daemon_host_id(self, hosts: list) -> str:
        # Deluge returns entries containing host id, host, port, and status.
        preferred_hosts = {"127.0.0.1", "localhost"}

        for entry in hosts:
            if (
                isinstance(entry, list)
                and len(entry) >= MIN_DAEMON_HOST_ENTRY_LENGTH
                and entry[1] in preferred_hosts
            ):
                return str(entry[0])

        for entry in hosts:
            if (
                isinstance(entry, list)
                and len(entry) >= MIN_DAEMON_HOST_STATUS_ENTRY_LENGTH
                and str(entry[3]).lower() == "online"
            ):
                return str(entry[0])

        return str(hosts[0][0])

    def _ensure_connected(self) -> None:
        if not self._authenticated:
            self._login()

        if self._connected:
            return

        if self._rpc_call("web.connected") is True:
            self._connected = True
            return

        hosts = self._rpc_call("web.get_hosts") or []
        if not hosts:
            msg = (
                "Deluge Web UI isn't connected to Deluge core (no hosts configured). "
                "Add/connect a daemon in Deluge Web UI → Connection Manager."
            )
            raise DelugeRpcError(msg)

        host_id = self._select_daemon_host_id(hosts)
        self._rpc_call("web.connect", host_id)

        if self._rpc_call("web.connected") is not True:
            msg = (
                "Deluge Web UI couldn't connect to Deluge core. "
                "Check daemon status in Deluge Web UI → Connection Manager."
            )
            raise DelugeRpcError(msg)

        self._connected = True

    def _get_daemon_version(self) -> object:
        """Fetch daemon version, preferring daemon.get_version when available."""
        with suppress(*_DELUGE_CLIENT_ERRORS):
            methods = self._rpc_call("system.listMethods")
            if isinstance(methods, list) and "daemon.get_version" in methods:
                return self._rpc_call("daemon.get_version")

        return self._rpc_call("daemon.info")

    def _try_set_label(self, torrent_id: str, label: str) -> bool:
        """Best-effort label assignment (requires Deluge Label plugin)."""
        if not label:
            return False

        try:
            # label.add will error if the plugin is unavailable or the label exists.
            with suppress(*_DELUGE_CLIENT_ERRORS):
                self._rpc_call("label.add", label)

            self._rpc_call("label.set_torrent", torrent_id, label)
        except _DELUGE_CLIENT_ERRORS as e:
            logger.debug("Could not set Deluge label '%s' for %s: %s", label, torrent_id, e)
            return False
        else:
            return True

    @staticmethod
    def is_configured() -> bool:
        """Return whether Deluge is the active configured torrent client."""
        client = config_text(config.get("PROWLARR_TORRENT_CLIENT", ""))
        host = config_text(config.get("DELUGE_HOST", ""))
        password = config_text(config.get("DELUGE_PASSWORD", ""))
        return client == "deluge" and bool(host) and bool(password)

    def test_connection(self) -> tuple[bool, str]:
        """Test connectivity and authentication against the Deluge server."""
        try:
            self._ensure_connected()
            version = self._get_daemon_version()
        except _DELUGE_CLIENT_ERRORS as e:
            self._authenticated = False
            self._connected = False
            return False, f"Connection failed: {e!s}"
        else:
            return True, f"Connected to Deluge {version}"

    def add_download(
        self,
        url: str,
        name: str,
        category: str | None = None,
        expected_hash: str | None = None,
        **kwargs: object,
    ) -> str:
        """Add a torrent to Deluge and return the torrent id."""
        try:
            self._ensure_connected()

            category_value = str(category or self._category)

            torrent_info = extract_torrent_info(url, expected_hash=expected_hash)
            if not torrent_info.is_magnet and not torrent_info.torrent_data:
                message = "Failed to fetch torrent file"
                if torrent_info.fetch_error:
                    message = f"{message}: {torrent_info.fetch_error}"
                _raise_runtime_error(message)

            options: dict[str, Any] = {}
            download_dir = resolve_download_dir(
                separate=self._separate_download_dirs,
                shared_dir=self._download_dir,
                book_dir=self._download_dir_book,
                audiobook_dir=self._download_dir_audiobook,
                content_type=kwargs.get("content_type"),
            )
            if download_dir:
                options["download_location"] = download_dir

            # Per-torrent seeding limits from indexer
            seeding_time_limit = coerce_optional_int(kwargs.get("seeding_time_limit"))
            if seeding_time_limit is not None:
                logger.debug(
                    "Deluge has no per-torrent seeding time limit, ignoring %s minutes",
                    seeding_time_limit,
                )
            ratio_limit = coerce_optional_float(kwargs.get("ratio_limit"))
            if ratio_limit is not None:
                options["stop_ratio"] = ratio_limit
                options["stop_at_ratio"] = True

            if torrent_info.is_magnet:
                magnet_url = torrent_info.magnet_url or url
                torrent_id = self._rpc_call("core.add_torrent_magnet", magnet_url, options)
            else:
                torrent_data = torrent_info.torrent_data
                if torrent_data is None:
                    _raise_runtime_error("Failed to fetch torrent file")

                torrent_data_bytes: bytes = torrent_data
                filedump = base64.b64encode(torrent_data_bytes).decode("ascii")
                torrent_id = self._rpc_call(
                    "core.add_torrent_file",
                    f"{name}.torrent",
                    filedump,
                    options,
                )

            if not torrent_id:
                _raise_runtime_error("Deluge returned no torrent ID")

            torrent_id = str(torrent_id).lower()
            self._try_set_label(torrent_id, category_value)

            logger.info("Added torrent to Deluge: %s", torrent_id)

        except _DELUGE_CLIENT_ERRORS:
            self._authenticated = False
            self._connected = False
            logger.exception("Deluge add failed")
            raise
        else:
            return torrent_id

    def get_status(self, download_id: str) -> DownloadStatus:
        """Return the current Deluge status for a torrent."""
        try:
            self._ensure_connected()

            status = self._rpc_call(
                "core.get_torrent_status",
                download_id,
                [
                    "state",
                    "progress",
                    "download_payload_rate",
                    "eta",
                    "save_path",
                    "name",
                ],
            )

            if not status:
                return DownloadStatus.error("Torrent not found")

            # Deluge states: Downloading, Seeding, Paused, Checking, Queued, Error, Moving
            state_map = {
                "Downloading": ("downloading", None),
                "Seeding": ("seeding", "Seeding"),
                "Paused": ("paused", "Paused"),
                "Checking": ("checking", "Checking files"),
                "Queued": ("queued", "Queued"),
                "Error": ("error", "Error"),
                "Moving": ("processing", "Moving files"),
                "Allocating": ("downloading", "Allocating space"),
            }

            deluge_state = status.get("state", "Unknown")
            state, message = state_map.get(str(deluge_state), ("unknown", str(deluge_state)))

            progress = float(status.get("progress", 0))
            # Don't mark complete while files are being moved
            complete = progress >= DOWNLOAD_COMPLETE_PROGRESS and deluge_state != "Moving"

            if complete:
                message = "Complete"

            eta = status.get("eta")
            if eta is not None:
                try:
                    eta = int(eta)
                except TypeError, ValueError:
                    eta = None

            if eta is not None and (eta < 0 or eta > ONE_WEEK_IN_SECONDS):
                eta = None

            file_path = None
            if complete:
                # Output path is save_path + torrent name
                file_path = self._build_path(
                    str(status.get("save_path", "")),
                    str(status.get("name", "")),
                )

            return DownloadStatus(
                progress=progress,
                state="complete" if complete else state,
                message=message,
                complete=complete,
                file_path=file_path,
                download_speed=status.get("download_payload_rate"),
                eta=eta,
            )

        except _DELUGE_CLIENT_ERRORS as e:
            return DownloadStatus.error(self._log_error("get_status", e))

    def remove(self, download_id: str, *, delete_files: bool = False) -> bool:
        """Remove a torrent from Deluge, optionally deleting its files."""
        try:
            self._ensure_connected()

            result = self._rpc_call("core.remove_torrent", download_id, delete_files)
            if result:
                logger.info(
                    "Removed torrent from Deluge: %s%s",
                    download_id,
                    " (with files)" if delete_files else "",
                )
                return True

        except _DELUGE_CLIENT_ERRORS as e:
            self._log_error("remove", e)
            return False
        else:
            return False

    def set_category(self, download_id: str, category: str) -> bool:
        """Assign a label to a torrent using Deluge's Label plugin."""
        try:
            self._ensure_connected()
            return self._try_set_label(download_id, category)
        except _DELUGE_CLIENT_ERRORS as e:
            self._log_error("set_category", e)
            return False

    def get_download_path(self, download_id: str) -> str | None:
        """Return the resolved download path for a Deluge torrent."""
        try:
            self._ensure_connected()

            status = self._rpc_call(
                "core.get_torrent_status",
                download_id,
                ["save_path", "name"],
            )

            if status:
                return self._build_path(
                    str(status.get("save_path", "")),
                    str(status.get("name", "")),
                )

        except _DELUGE_CLIENT_ERRORS as e:
            self._log_error("get_download_path", e, level="debug")
            return None
        else:
            return None

    def find_existing(
        self, url: str, category: str | None = None
    ) -> tuple[str, DownloadStatus] | None:
        """Find an existing Deluge torrent matching a release URL."""
        try:
            self._ensure_connected()

            torrent_info = extract_torrent_info(url)
            if not torrent_info.info_hash:
                return None

            status = self._rpc_call(
                "core.get_torrent_status",
                torrent_info.info_hash,
                ["state"],
            )

            if status:
                full_status = self.get_status(torrent_info.info_hash)
                return (torrent_info.info_hash, full_status)

        except _DELUGE_CLIENT_ERRORS as e:
            self._authenticated = False
            self._connected = False
            logger.debug("Error checking for existing torrent: %s", e)
            return None
        else:
            return None
