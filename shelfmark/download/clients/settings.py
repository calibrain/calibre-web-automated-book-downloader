"""Shared download client settings registration."""

from __future__ import annotations

import importlib
from contextlib import contextmanager, suppress
from typing import TYPE_CHECKING, Any, NoReturn, Protocol, TypeGuard

from shelfmark.core.settings_registry import (
    ActionButton,
    HeadingField,
    PasswordField,
    SelectField,
    SettingsField,
    TagListField,
    TextField,
    register_settings,
)
from shelfmark.core.utils import get_hardened_xmlrpc_client, normalize_http_url
from shelfmark.download.network import get_ssl_verify

try:
    import qbittorrentapi as _qbittorrentapi
except ImportError:
    _ImportedQBittorrentApiError = RuntimeError
    _ImportedQBittorrentLoginFailed = RuntimeError
else:
    _ImportedQBittorrentApiError = getattr(_qbittorrentapi, "APIError", RuntimeError)
    _ImportedQBittorrentLoginFailed = getattr(_qbittorrentapi, "LoginFailed", RuntimeError)

try:
    from transmission_rpc import TransmissionError as _ImportedTransmissionError
except ImportError:
    _ImportedTransmissionError = RuntimeError

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

# ==================== Test Connection Callbacks ====================
_DELUGE_HOST_ENTRY_MIN_LENGTH = 2


class _SessionWithVerify(Protocol):
    verify: bool


class _RequestsModuleWithSession(Protocol):
    Session: Callable[..., _SessionWithVerify]


class _TransmissionClientWithProtocol(Protocol):
    protocol: str


def _resolve_exception_type(candidate: object) -> type[Exception]:
    if isinstance(candidate, type) and issubclass(candidate, Exception):
        return candidate
    return RuntimeError


_QBittorrentApiError = _resolve_exception_type(_ImportedQBittorrentApiError)
_QBittorrentLoginFailed = _resolve_exception_type(_ImportedQBittorrentLoginFailed)
_TransmissionError = _resolve_exception_type(_ImportedTransmissionError)
_QBITTORRENT_SETTINGS_ERRORS = (
    _QBittorrentLoginFailed,
    _QBittorrentApiError,
    AttributeError,
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
)
_TRANSMISSION_SETTINGS_ERRORS = (
    _TransmissionError,
    AttributeError,
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
)


def _raise_runtime_error(message: str) -> NoReturn:
    raise RuntimeError(message)


def _is_requests_module_with_session(candidate: object) -> TypeGuard[_RequestsModuleWithSession]:
    return callable(getattr(candidate, "Session", None))


def _has_protocol_attr(candidate: object) -> TypeGuard[_TransmissionClientWithProtocol]:
    return hasattr(candidate, "protocol")


def _set_transmission_protocol_if_supported(client: object, protocol: str) -> None:
    if protocol != "https" or not _has_protocol_attr(client):
        return
    with suppress(AttributeError, OSError, RuntimeError, TypeError, ValueError):
        client.protocol = protocol


def _resolve_string_setting(
    current_values: dict[str, Any],
    config_get: Callable[[str, str], object],
    key: str,
    *,
    default: str = "",
) -> str:
    current_value = current_values.get(key)
    if isinstance(current_value, str) and current_value:
        return current_value

    config_value = config_get(key, default)
    if isinstance(config_value, str) and config_value:
        return config_value

    return default


@contextmanager
def _transmission_session_verify_override(url: str) -> Iterator[None]:
    """Ensure transmission-rpc constructor uses the configured TLS verify mode."""
    verify = get_ssl_verify(url)
    if verify:
        yield
        return

    try:
        transmission_rpc_client = importlib.import_module("transmission_rpc.client")
    except ImportError:
        yield
        return

    requests_module = getattr(transmission_rpc_client, "requests", None)
    if not _is_requests_module_with_session(requests_module):
        yield
        return

    original_session_factory = requests_module.Session

    def _session_factory(*args: Any, **kwargs: Any) -> Any:
        session = original_session_factory(*args, **kwargs)
        session.verify = False
        return session

    requests_module.Session = _session_factory
    try:
        yield
    finally:
        requests_module.Session = original_session_factory


def _test_qbittorrent_connection(current_values: dict[str, Any] | None = None) -> dict[str, Any]:
    """Test the qBittorrent connection using current form values."""
    from shelfmark.core.config import config

    current_values = current_values or {}

    raw_url = _resolve_string_setting(current_values, config.get, "QBITTORRENT_URL")
    username = _resolve_string_setting(current_values, config.get, "QBITTORRENT_USERNAME")
    password = _resolve_string_setting(current_values, config.get, "QBITTORRENT_PASSWORD")
    api_key = _resolve_string_setting(current_values, config.get, "QBITTORRENT_API_KEY")

    if not raw_url:
        return {"success": False, "message": "qBittorrent URL is required"}

    try:
        from qbittorrentapi import Client

        url = normalize_http_url(raw_url)
        if not url:
            return {"success": False, "message": "qBittorrent URL is invalid"}

        client = Client(
            host=url,
            username=username,
            password=password,
            api_key=api_key or None,
            VERIFY_WEBUI_CERTIFICATE=get_ssl_verify(url),
        )
        client.auth_log_in()
        api_version = client.app.web_api_version
    except ImportError:
        return {"success": False, "message": "qbittorrent-api package not installed"}
    except _QBITTORRENT_SETTINGS_ERRORS as e:
        if isinstance(e, _QBittorrentLoginFailed):
            # LoginFailed carries no message of its own, so name the rejected credential.
            rejected = "API key" if api_key else "username or password"
            return {"success": False, "message": f"qBittorrent rejected the {rejected}"}
        return {"success": False, "message": f"Connection failed: {e!s}"}
    else:
        # Both credentials can be set at once, so name the one that actually authenticated.
        used = " using the API key" if api_key else ""
        return {
            "success": True,
            "message": f"Connected to qBittorrent (API v{api_version}){used}",
        }


def _test_transmission_connection(current_values: dict[str, Any] | None = None) -> dict[str, Any]:
    """Test the Transmission connection using current form values."""
    from shelfmark.core.config import config
    from shelfmark.download.clients.torrent_utils import (
        parse_transmission_url,
    )

    current_values = current_values or {}

    raw_url = _resolve_string_setting(current_values, config.get, "TRANSMISSION_URL")
    username = _resolve_string_setting(current_values, config.get, "TRANSMISSION_USERNAME")
    password = _resolve_string_setting(current_values, config.get, "TRANSMISSION_PASSWORD")

    if not raw_url:
        return {"success": False, "message": "Transmission URL is required"}

    url = normalize_http_url(raw_url)
    if not url:
        return {"success": False, "message": "Transmission URL is invalid"}

    try:
        from transmission_rpc import Client

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
                client = Client(**client_kwargs)
        except TypeError as e:
            if "protocol" not in str(e):
                raise
            client_kwargs.pop("protocol", None)
            with _transmission_session_verify_override(url):
                client = Client(**client_kwargs)
        _set_transmission_protocol_if_supported(client, protocol)

        # Keep session verify aligned for subsequent calls beyond constructor bootstrap.
        http_session = getattr(client, "_http_session", None)
        if http_session is not None:
            http_session.verify = get_ssl_verify(url)

        session = client.get_session()
        version = session.version
    except ImportError:
        return {"success": False, "message": "transmission-rpc package not installed"}
    except _TRANSMISSION_SETTINGS_ERRORS as e:
        return {"success": False, "message": f"Connection failed: {e!s}"}
    else:
        return {"success": True, "message": f"Connected to Transmission {version}"}


def _test_deluge_connection(current_values: dict[str, Any] | None = None) -> dict[str, Any]:
    """Test Deluge Web UI JSON-RPC connection using current form values."""
    from urllib.parse import urlparse

    import requests

    from shelfmark.core.config import config

    current_values = current_values or {}

    raw_host = _resolve_string_setting(
        current_values, config.get, "DELUGE_HOST", default="localhost"
    )
    raw_port = _resolve_string_setting(current_values, config.get, "DELUGE_PORT", default="8112")
    password = _resolve_string_setting(current_values, config.get, "DELUGE_PASSWORD")

    if not raw_host:
        return {"success": False, "message": "Deluge host is required"}
    if not password:
        return {"success": False, "message": "Deluge password is required"}

    raw_host = str(raw_host)
    raw_host = normalize_http_url(raw_host, strip_trailing_slash=False) if raw_host else ""
    if not raw_host:
        return {"success": False, "message": "Deluge host is invalid"}

    raw_port = str(raw_port or "8112")

    scheme = "http"
    base_path = ""
    host = raw_host
    port = int(raw_port) if raw_port.isdigit() else 8112

    # Allow DELUGE_HOST to be a full URL (e.g. http://deluge:8112)
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

    rpc_url = f"{scheme}://{host}:{port}{base_path}/json"

    def rpc_call(session: requests.Session, rpc_id: int, method: str, *params: Any) -> Any:
        payload = {"id": rpc_id, "method": method, "params": list(params)}
        resp = session.post(rpc_url, json=payload, timeout=15, verify=get_ssl_verify(rpc_url))
        resp.raise_for_status()
        data = resp.json()
        if data.get("error"):
            error = data["error"]
            if isinstance(error, dict):
                raise RuntimeError(error.get("message") or str(error))
            raise RuntimeError(str(error))
        return data.get("result")

    def get_daemon_version(session: requests.Session, rpc_id: int) -> Any:
        try:
            methods = rpc_call(session, rpc_id, "system.listMethods")
            if isinstance(methods, list) and "daemon.get_version" in methods:
                return rpc_call(session, rpc_id + 1, "daemon.get_version")
        except requests.exceptions.RequestException, RuntimeError, ValueError, TypeError:
            # Fall back to daemon.info to preserve existing behavior.
            pass

        return rpc_call(session, rpc_id + 1, "daemon.info")

    try:
        session = requests.Session()

        if rpc_call(session, 1, "auth.login", password) is not True:
            return {"success": False, "message": "Deluge Web UI authentication failed"}

        if rpc_call(session, 2, "web.connected") is not True:
            hosts = rpc_call(session, 3, "web.get_hosts") or []
            if not hosts:
                return {
                    "success": False,
                    "message": "Deluge Web UI isn't connected to Deluge core (no hosts configured). Add/connect a daemon in Deluge Web UI → Connection Manager.",
                }

            host_id = hosts[0][0]
            for entry in hosts:
                if (
                    isinstance(entry, list)
                    and len(entry) >= _DELUGE_HOST_ENTRY_MIN_LENGTH
                    and entry[1] in {"127.0.0.1", "localhost"}
                ):
                    host_id = entry[0]
                    break

            rpc_call(session, 4, "web.connect", host_id)

            if rpc_call(session, 5, "web.connected") is not True:
                return {
                    "success": False,
                    "message": "Deluge Web UI couldn't connect to Deluge core. Check Deluge Web UI → Connection Manager.",
                }

        version = get_daemon_version(session, 6)
    except requests.exceptions.ConnectionError:
        return {"success": False, "message": "Could not connect to Deluge Web UI"}
    except requests.exceptions.Timeout:
        return {"success": False, "message": "Connection timed out"}
    except (
        requests.exceptions.RequestException,
        RuntimeError,
        ValueError,
        TypeError,
        KeyError,
        IndexError,
        AttributeError,
    ) as e:
        return {"success": False, "message": f"Connection failed: {e!s}"}
    else:
        return {"success": True, "message": f"Connected to Deluge {version}"}


def _test_rtorrent_connection(current_values: dict[str, Any] | None = None) -> dict[str, Any]:
    """Test the rTorrent connection using current form values."""
    import ssl
    from urllib.parse import urlparse

    from shelfmark.core.config import config

    current_values = current_values or {}

    raw_url = _resolve_string_setting(current_values, config.get, "RTORRENT_URL")
    username = _resolve_string_setting(current_values, config.get, "RTORRENT_USERNAME")
    password = _resolve_string_setting(current_values, config.get, "RTORRENT_PASSWORD")

    if not raw_url:
        return {"success": False, "message": "rTorrent URL is required"}

    url = normalize_http_url(raw_url)
    if not url:
        return {"success": False, "message": "rTorrent URL is invalid"}

    try:
        xmlrpc_client = get_hardened_xmlrpc_client()
    except (RuntimeError, OSError, ValueError, TypeError) as e:
        return {"success": False, "message": f"Connection failed: {e!s}"}

    try:
        # Add HTTP auth to URL if credentials provided
        if username and password:
            parsed = urlparse(url)
            url = f"{parsed.scheme}://{username}:{password}@{parsed.netloc}{parsed.path}"

        rpc_url = url.rstrip("/")
        verify = get_ssl_verify(rpc_url)
        if rpc_url.startswith("https://") and not verify:
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            rpc = xmlrpc_client.ServerProxy(
                rpc_url,
                transport=xmlrpc_client.SafeTransport(context=ssl_context),
            )
        else:
            rpc = xmlrpc_client.ServerProxy(rpc_url)

        version = rpc.system.client_version()
    except (xmlrpc_client.Error, RuntimeError, OSError, ValueError, TypeError) as e:
        return {"success": False, "message": f"Connection failed: {e!s}"}

    else:
        return {"success": True, "message": f"Connected to rTorrent {version}"}


def _test_nzbget_connection(current_values: dict[str, Any] | None = None) -> dict[str, Any]:
    """Test the NZBGet connection using current form values."""
    import requests

    from shelfmark.core.config import config

    current_values = current_values or {}

    raw_url = _resolve_string_setting(current_values, config.get, "NZBGET_URL")
    username = _resolve_string_setting(
        current_values,
        config.get,
        "NZBGET_USERNAME",
        default="nzbget",
    )
    password = _resolve_string_setting(current_values, config.get, "NZBGET_PASSWORD")

    if not raw_url:
        return {"success": False, "message": "NZBGet URL is required"}

    url = normalize_http_url(raw_url)
    if not url:
        return {"success": False, "message": "NZBGet URL is invalid"}

    try:
        rpc_url = f"{url.rstrip('/')}/jsonrpc"
        payload = {"jsonrpc": "2.0", "method": "status", "params": [], "id": 1}
        response = requests.post(
            rpc_url,
            json=payload,
            auth=(username, password),
            timeout=30,
            verify=get_ssl_verify(rpc_url),
        )
        response.raise_for_status()
        result = response.json()
        if result.get("error"):
            _raise_runtime_error(result["error"].get("message", "RPC error"))
        version = result.get("result", {}).get("Version", "unknown")
    except requests.exceptions.ConnectionError:
        return {"success": False, "message": "Could not connect to NZBGet"}
    except requests.exceptions.Timeout:
        return {"success": False, "message": "Connection timed out"}
    except (
        requests.exceptions.RequestException,
        RuntimeError,
        ValueError,
        AttributeError,
        TypeError,
    ) as e:
        return {"success": False, "message": f"Connection failed: {e!s}"}
    else:
        return {"success": True, "message": f"Connected to NZBGet {version}"}


def _test_sabnzbd_connection(current_values: dict[str, Any] | None = None) -> dict[str, Any]:
    """Test the SABnzbd connection using current form values."""
    import requests

    from shelfmark.core.config import config

    current_values = current_values or {}

    raw_url = _resolve_string_setting(current_values, config.get, "SABNZBD_URL")
    api_key = _resolve_string_setting(current_values, config.get, "SABNZBD_API_KEY")

    if not raw_url:
        return {"success": False, "message": "SABnzbd URL is required"}

    url = normalize_http_url(raw_url)
    if not url:
        return {"success": False, "message": "SABnzbd URL is invalid"}
    if not api_key:
        return {"success": False, "message": "API key is required"}

    try:
        api_url = f"{url.rstrip('/')}/api"
        params = {"apikey": api_key, "mode": "version", "output": "json"}
        response = requests.get(api_url, params=params, timeout=30, verify=get_ssl_verify(api_url))
        response.raise_for_status()
        result = response.json()
        version = result.get("version", "unknown")
    except requests.exceptions.ConnectionError:
        return {"success": False, "message": "Could not connect to SABnzbd"}
    except requests.exceptions.Timeout:
        return {"success": False, "message": "Connection timed out"}
    except (
        requests.exceptions.RequestException,
        RuntimeError,
        ValueError,
        AttributeError,
        TypeError,
    ) as e:
        return {"success": False, "message": f"Connection failed: {e!s}"}
    else:
        return {"success": True, "message": f"Connected to SABnzbd {version}"}


def _test_alldebrid_connection(current_values: dict[str, Any] | None = None) -> dict[str, Any]:
    """Test the AllDebrid API connection using current form values."""
    from shelfmark.core.config import config
    from shelfmark.download.clients.alldebrid import AllDebridClient

    current_values = current_values or {}
    api_key = _resolve_string_setting(current_values, config.get, "ALLDEBRID_API_KEY")

    if not api_key:
        return {"success": False, "message": "AllDebrid API Key is required"}

    client = AllDebridClient()
    client._api_key = api_key
    success, message = client.test_connection()
    return {"success": success, "message": message}


def _test_realdebrid_connection(current_values: dict[str, Any] | None = None) -> dict[str, Any]:
    """Test the Real-Debrid API connection using current form values."""
    from shelfmark.core.config import config
    from shelfmark.download.clients.realdebrid import RealDebridClient

    current_values = current_values or {}
    api_key = _resolve_string_setting(current_values, config.get, "REALDEBRID_API_KEY")

    if not api_key:
        return {"success": False, "message": "Real-Debrid API Key is required"}

    client = RealDebridClient()
    client._api_key = api_key
    success, message = client.test_connection()
    return {"success": success, "message": message}


# ==================== Download Clients Tab ====================


@register_settings(
    name="prowlarr_clients",
    display_name="Download Clients",
    icon="cog",
    order=110,
)
def prowlarr_clients_settings() -> list[SettingsField]:
    """Download client settings shared by external release sources."""
    return [
        # --- Torrent Client Selection ---
        HeadingField(
            key="torrent_heading",
            title="Torrent Client",
            description="Select and configure a torrent client for downloading torrent releases.",
        ),
        SelectField(
            key="PROWLARR_TORRENT_CLIENT",
            label="Torrent Client",
            description="Choose which torrent client to use",
            options=[
                {"value": "", "label": "None"},
                {"value": "alldebrid", "label": "AllDebrid"},
                {"value": "blackhole", "label": "Blackhole"},
                {"value": "qbittorrent", "label": "qBittorrent"},
                {"value": "realdebrid", "label": "Real-Debrid"},
                {"value": "transmission", "label": "Transmission"},
                {"value": "deluge", "label": "Deluge"},
                {"value": "rtorrent", "label": "rTorrent"},
            ],
            default="",
        ),
        TextField(
            key="BLACKHOLE_DIRECTORY",
            label="Blackhole Directory",
            description="Directory where Shelfmark saves .torrent files for another downloader",
            placeholder="/blackhole",
            show_when={"field": "PROWLARR_TORRENT_CLIENT", "value": "blackhole"},
        ),
        # --- AllDebrid Settings ---
        PasswordField(
            key="ALLDEBRID_API_KEY",
            label="API Key",
            description="AllDebrid API Key (apiv4) from your AllDebrid account settings",
            show_when={"field": "PROWLARR_TORRENT_CLIENT", "value": "alldebrid"},
        ),
        ActionButton(
            key="test_alldebrid",
            label="Test Connection",
            description="Verify your AllDebrid configuration",
            style="primary",
            callback=_test_alldebrid_connection,
            show_when={"field": "PROWLARR_TORRENT_CLIENT", "value": "alldebrid"},
        ),
        # --- Real-Debrid Settings ---
        PasswordField(
            key="REALDEBRID_API_KEY",
            label="API Key",
            description="Real-Debrid API Key (Secret Token) from your Real-Debrid account settings",
            show_when={"field": "PROWLARR_TORRENT_CLIENT", "value": "realdebrid"},
        ),
        ActionButton(
            key="test_realdebrid",
            label="Test Connection",
            description="Verify your Real-Debrid configuration",
            style="primary",
            callback=_test_realdebrid_connection,
            show_when={"field": "PROWLARR_TORRENT_CLIENT", "value": "realdebrid"},
        ),
        # --- qBittorrent Settings ---
        TextField(
            key="QBITTORRENT_URL",
            label="qBittorrent URL",
            description="Web UI URL of your qBittorrent instance",
            placeholder="http://qbittorrent:8080",
            show_when={"field": "PROWLARR_TORRENT_CLIENT", "value": "qbittorrent"},
        ),
        TextField(
            key="QBITTORRENT_USERNAME",
            label="Username",
            description="qBittorrent Web UI username",
            placeholder="admin",
            show_when={"field": "PROWLARR_TORRENT_CLIENT", "value": "qbittorrent"},
        ),
        PasswordField(
            key="QBITTORRENT_PASSWORD",
            label="Password",
            description="qBittorrent Web UI password",
            show_when={"field": "PROWLARR_TORRENT_CLIENT", "value": "qbittorrent"},
        ),
        PasswordField(
            key="QBITTORRENT_API_KEY",
            label="API Key",
            description="Found in qBittorrent: Options > Web UI > API Key (qBittorrent 5.2.0+). Used instead of the username and password when set.",
            show_when={"field": "PROWLARR_TORRENT_CLIENT", "value": "qbittorrent"},
        ),
        ActionButton(
            key="test_qbittorrent",
            label="Test Connection",
            description="Verify your qBittorrent configuration",
            style="primary",
            callback=_test_qbittorrent_connection,
            show_when={"field": "PROWLARR_TORRENT_CLIENT", "value": "qbittorrent"},
        ),
        TextField(
            key="QBITTORRENT_CATEGORY",
            label="Book Category",
            description="Category to assign to book downloads in qBittorrent",
            placeholder="books",
            default="books",
            show_when={"field": "PROWLARR_TORRENT_CLIENT", "value": "qbittorrent"},
        ),
        TextField(
            key="QBITTORRENT_CATEGORY_AUDIOBOOK",
            label="Audiobook Category",
            description="Category for audiobook downloads. Leave empty to use the book category.",
            placeholder="",
            default="",
            show_when={"field": "PROWLARR_TORRENT_CLIENT", "value": "qbittorrent"},
        ),
        TextField(
            key="QBITTORRENT_DOWNLOAD_DIR",
            label="Download Directory",
            description="Server-side directory where torrents are downloaded (optional, uses qBittorrent default if not specified)",
            placeholder="/downloads",
            show_when={"field": "PROWLARR_TORRENT_CLIENT", "value": "qbittorrent"},
        ),
        TagListField(
            key="QBITTORRENT_TAG",
            label="Tags",
            description="Tag(s) to assign to qBittorrent downloads. Leave empty for no tags.",
            placeholder="",
            default=[],
            normalize_urls=False,
            show_when={"field": "PROWLARR_TORRENT_CLIENT", "value": "qbittorrent"},
        ),
        # --- Transmission Settings ---
        TextField(
            key="TRANSMISSION_URL",
            label="Transmission URL",
            description="URL of your Transmission instance (use https:// for TLS)",
            placeholder="http://transmission:9091",
            show_when={"field": "PROWLARR_TORRENT_CLIENT", "value": "transmission"},
        ),
        TextField(
            key="TRANSMISSION_USERNAME",
            label="Username",
            description="Transmission RPC username (if authentication enabled)",
            show_when={"field": "PROWLARR_TORRENT_CLIENT", "value": "transmission"},
        ),
        PasswordField(
            key="TRANSMISSION_PASSWORD",
            label="Password",
            description="Transmission RPC password",
            show_when={"field": "PROWLARR_TORRENT_CLIENT", "value": "transmission"},
        ),
        ActionButton(
            key="test_transmission",
            label="Test Connection",
            description="Verify your Transmission configuration",
            style="primary",
            callback=_test_transmission_connection,
            show_when={"field": "PROWLARR_TORRENT_CLIENT", "value": "transmission"},
        ),
        TextField(
            key="TRANSMISSION_CATEGORY",
            label="Book Label",
            description="Label to assign to book downloads in Transmission",
            placeholder="books",
            default="books",
            show_when={"field": "PROWLARR_TORRENT_CLIENT", "value": "transmission"},
        ),
        TextField(
            key="TRANSMISSION_CATEGORY_AUDIOBOOK",
            label="Audiobook Label",
            description="Label for audiobook downloads. Leave empty to use the book label.",
            placeholder="",
            default="",
            show_when={"field": "PROWLARR_TORRENT_CLIENT", "value": "transmission"},
        ),
        TextField(
            key="TRANSMISSION_DOWNLOAD_DIR",
            label="Download Directory",
            description="Server-side directory where torrents are downloaded (optional, uses Transmission default if not specified)",
            placeholder="/downloads",
            show_when={"field": "PROWLARR_TORRENT_CLIENT", "value": "transmission"},
        ),
        # --- Deluge Settings ---
        TextField(
            key="DELUGE_HOST",
            label="Deluge Web UI Host/URL",
            description="Hostname/IP or full URL of your Deluge Web UI (deluge-web)",
            placeholder="http://deluge:8112",
            default="localhost",
            show_when={"field": "PROWLARR_TORRENT_CLIENT", "value": "deluge"},
        ),
        TextField(
            key="DELUGE_PORT",
            label="Deluge Web UI Port",
            description="Deluge Web UI port (default: 8112)",
            placeholder="8112",
            default="8112",
            show_when={"field": "PROWLARR_TORRENT_CLIENT", "value": "deluge"},
        ),
        PasswordField(
            key="DELUGE_PASSWORD",
            label="Password",
            description="Deluge Web UI password (default: deluge)",
            show_when={"field": "PROWLARR_TORRENT_CLIENT", "value": "deluge"},
        ),
        ActionButton(
            key="test_deluge",
            label="Test Connection",
            description="Verify your Deluge configuration",
            style="primary",
            callback=_test_deluge_connection,
            show_when={"field": "PROWLARR_TORRENT_CLIENT", "value": "deluge"},
        ),
        TextField(
            key="DELUGE_CATEGORY",
            label="Book Label",
            description="Label to assign to book downloads in Deluge",
            placeholder="books",
            default="books",
            show_when={"field": "PROWLARR_TORRENT_CLIENT", "value": "deluge"},
        ),
        TextField(
            key="DELUGE_CATEGORY_AUDIOBOOK",
            label="Audiobook Label",
            description="Label for audiobook downloads. Leave empty to use the book label.",
            placeholder="",
            default="",
            show_when={"field": "PROWLARR_TORRENT_CLIENT", "value": "deluge"},
        ),
        TextField(
            key="DELUGE_DOWNLOAD_DIR",
            label="Download Directory",
            description="Server-side directory where torrents are downloaded (optional, uses Deluge default if not specified)",
            placeholder="/downloads",
            show_when={"field": "PROWLARR_TORRENT_CLIENT", "value": "deluge"},
        ),
        # --- rTorrent Settings ---
        TextField(
            key="RTORRENT_URL",
            label="rTorrent URL",
            description="XML-RPC URL of your rTorrent instance",
            placeholder="http://rtorrent:6881/RPC2",
            show_when={"field": "PROWLARR_TORRENT_CLIENT", "value": "rtorrent"},
        ),
        TextField(
            key="RTORRENT_USERNAME",
            label="Username",
            description="HTTP Basic auth username (if authentication enabled)",
            show_when={"field": "PROWLARR_TORRENT_CLIENT", "value": "rtorrent"},
        ),
        PasswordField(
            key="RTORRENT_PASSWORD",
            label="Password",
            description="HTTP Basic auth password",
            show_when={"field": "PROWLARR_TORRENT_CLIENT", "value": "rtorrent"},
        ),
        ActionButton(
            key="test_rtorrent",
            label="Test Connection",
            description="Verify your rTorrent configuration",
            style="primary",
            callback=_test_rtorrent_connection,
            show_when={"field": "PROWLARR_TORRENT_CLIENT", "value": "rtorrent"},
        ),
        TextField(
            key="RTORRENT_LABEL",
            label="Book Label",
            description="Label to assign to ebook downloads in rTorrent",
            placeholder="cwabd",
            default="cwabd",
            show_when={"field": "PROWLARR_TORRENT_CLIENT", "value": "rtorrent"},
        ),
        TextField(
            key="RTORRENT_AUDIOBOOK_LABEL",
            label="Audiobook Label",
            description="Label to assign to audiobook downloads in rTorrent (falls back to Book Label if not set)",
            placeholder="audiobooks",
            show_when={"field": "PROWLARR_TORRENT_CLIENT", "value": "rtorrent"},
        ),
        TextField(
            key="RTORRENT_DOWNLOAD_DIR",
            label="Download Directory",
            description="Server-side directory where torrents are downloaded (optional, uses rTorrent default if not specified)",
            placeholder="/downloads",
            show_when={"field": "PROWLARR_TORRENT_CLIENT", "value": "rtorrent"},
        ),
        # Note: Torrent client download path must be mounted identically in both containers.
        SelectField(
            key="PROWLARR_TORRENT_ACTION",
            label="Torrent Completion Action",
            description="Choose whether to keep, remove, or move the torrent to another category or label after import",
            options=[
                {"value": "keep", "label": "Keep"},
                {"value": "remove", "label": "Remove"},
                {"value": "change_category", "label": "Change Category"},
            ],
            default="keep",
            show_when={"field": "PROWLARR_TORRENT_CLIENT", "notEmpty": True},
        ),
        TextField(
            key="PROWLARR_TORRENT_POST_IMPORT_CATEGORY",
            label="Post-Import Category",
            description="Category or label to assign after a successful import",
            placeholder="imported",
            default="",
            show_when={"field": "PROWLARR_TORRENT_ACTION", "value": "change_category"},
        ),
        # --- Usenet Client Selection ---
        HeadingField(
            key="usenet_heading",
            title="Usenet Client",
            description="Select and configure a usenet client for downloading NZB releases.",
        ),
        SelectField(
            key="PROWLARR_USENET_CLIENT",
            label="Usenet Client",
            description="Choose which usenet client to use",
            options=[
                {"value": "", "label": "None"},
                {"value": "nzbget", "label": "NZBGet"},
                {"value": "sabnzbd", "label": "SABnzbd"},
            ],
            default="",
        ),
        # --- NZBGet Settings ---
        TextField(
            key="NZBGET_URL",
            label="NZBGet URL",
            description="URL of your NZBGet instance",
            placeholder="http://nzbget:6789",
            show_when={"field": "PROWLARR_USENET_CLIENT", "value": "nzbget"},
        ),
        TextField(
            key="NZBGET_USERNAME",
            label="Username",
            description="NZBGet control username",
            placeholder="nzbget",
            default="nzbget",
            show_when={"field": "PROWLARR_USENET_CLIENT", "value": "nzbget"},
        ),
        PasswordField(
            key="NZBGET_PASSWORD",
            label="Password",
            description="NZBGet control password",
            show_when={"field": "PROWLARR_USENET_CLIENT", "value": "nzbget"},
        ),
        ActionButton(
            key="test_nzbget",
            label="Test Connection",
            description="Verify your NZBGet configuration",
            style="primary",
            callback=_test_nzbget_connection,
            show_when={"field": "PROWLARR_USENET_CLIENT", "value": "nzbget"},
        ),
        TextField(
            key="NZBGET_CATEGORY",
            label="Book Category",
            description="Category to assign to book downloads in NZBGet",
            placeholder="Books",
            default="Books",
            show_when={"field": "PROWLARR_USENET_CLIENT", "value": "nzbget"},
        ),
        TextField(
            key="NZBGET_CATEGORY_AUDIOBOOK",
            label="Audiobook Category",
            description="Category for audiobook downloads. Leave empty to use the book category.",
            placeholder="",
            default="",
            show_when={"field": "PROWLARR_USENET_CLIENT", "value": "nzbget"},
        ),
        # --- SABnzbd Settings ---
        TextField(
            key="SABNZBD_URL",
            label="SABnzbd URL",
            description="URL of your SABnzbd instance",
            placeholder="http://sabnzbd:8080",
            show_when={"field": "PROWLARR_USENET_CLIENT", "value": "sabnzbd"},
        ),
        PasswordField(
            key="SABNZBD_API_KEY",
            label="API Key",
            description="Found in SABnzbd: Config > General > API Key",
            show_when={"field": "PROWLARR_USENET_CLIENT", "value": "sabnzbd"},
        ),
        ActionButton(
            key="test_sabnzbd",
            label="Test Connection",
            description="Verify your SABnzbd configuration",
            style="primary",
            callback=_test_sabnzbd_connection,
            show_when={"field": "PROWLARR_USENET_CLIENT", "value": "sabnzbd"},
        ),
        TextField(
            key="SABNZBD_CATEGORY",
            label="Book Category",
            description="Category to assign to book downloads in SABnzbd",
            placeholder="books",
            default="books",
            show_when={"field": "PROWLARR_USENET_CLIENT", "value": "sabnzbd"},
        ),
        TextField(
            key="SABNZBD_CATEGORY_AUDIOBOOK",
            label="Audiobook Category",
            description="Category for audiobook downloads. Leave empty to use the book category.",
            placeholder="",
            default="",
            show_when={"field": "PROWLARR_USENET_CLIENT", "value": "sabnzbd"},
        ),
        # Note: Usenet client download path must be mounted identically in both containers.
        SelectField(
            key="PROWLARR_USENET_ACTION",
            label="NZB Completion Action",
            description="Move deletes the job from your usenet client after import; Copy keeps it in the client",
            options=[
                {"value": "move", "label": "Move"},
                {"value": "copy", "label": "Copy"},
            ],
            default="move",
            show_when={"field": "PROWLARR_USENET_CLIENT", "notEmpty": True},
        ),
    ]
