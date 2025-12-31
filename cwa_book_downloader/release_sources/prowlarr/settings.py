"""
Prowlarr settings registration.

Registers Prowlarr settings as a group with multiple tabs:
- Configuration: Prowlarr connection settings + indexer selection
- Download Clients: Torrent and usenet client settings
"""

from typing import Any, Dict, List

from cwa_book_downloader.core.settings_registry import (
    register_group,
    register_settings,
    CheckboxField,
    HeadingField,
    TextField,
    PasswordField,
    ActionButton,
    SelectField,
    MultiSelectField,
)


# ==================== Dynamic Options Loaders ====================

def _get_indexer_options() -> List[Dict[str, str]]:
    """
    Fetch available indexers from Prowlarr for the multi-select field.

    Returns list of {value: "id", label: "name (protocol)"} options.
    """
    from cwa_book_downloader.core.config import config
    from cwa_book_downloader.core.logger import setup_logger

    logger = setup_logger(__name__)

    url = config.get("PROWLARR_URL", "")
    api_key = config.get("PROWLARR_API_KEY", "")

    if not url or not api_key:
        return []

    try:
        from cwa_book_downloader.release_sources.prowlarr.api import ProwlarrClient

        client = ProwlarrClient(url, api_key)
        indexers = client.get_enabled_indexers()

        options = []
        for idx in indexers:
            idx_id = idx.get("id")
            name = idx.get("name", "Unknown")
            protocol = idx.get("protocol", "")
            has_books = idx.get("has_books", False)

            # Add indicator for book support
            label = f"{name} ({protocol})"
            if has_books:
                label += " 📚"

            options.append({
                "value": str(idx_id),
                "label": label,
            })

        return options

    except Exception as e:
        logger.error(f"Failed to fetch Prowlarr indexers: {e}")
        return []


# ==================== Test Connection Callbacks ====================

def _test_prowlarr_connection(current_values: Dict[str, Any] = None) -> Dict[str, Any]:
    """Test the Prowlarr connection using current form values."""
    from cwa_book_downloader.core.config import config
    from cwa_book_downloader.core.logger import setup_logger
    from cwa_book_downloader.release_sources.prowlarr.api import ProwlarrClient

    logger = setup_logger(__name__)
    current_values = current_values or {}

    url = current_values.get("PROWLARR_URL") or config.get("PROWLARR_URL", "")
    api_key = current_values.get("PROWLARR_API_KEY") or config.get("PROWLARR_API_KEY", "")

    if not url:
        return {"success": False, "message": "Prowlarr URL is required"}
    if not api_key:
        return {"success": False, "message": "API key is required"}

    try:
        client = ProwlarrClient(url, api_key)
        success, message = client.test_connection()
        return {"success": success, "message": message}
    except Exception as e:
        return {"success": False, "message": f"Connection failed: {str(e)}"}


def _test_qbittorrent_connection(current_values: Dict[str, Any] = None) -> Dict[str, Any]:
    """Test the qBittorrent connection using current form values."""
    from cwa_book_downloader.core.config import config

    current_values = current_values or {}

    url = current_values.get("QBITTORRENT_URL") or config.get("QBITTORRENT_URL", "")
    username = current_values.get("QBITTORRENT_USERNAME") or config.get("QBITTORRENT_USERNAME", "")
    password = current_values.get("QBITTORRENT_PASSWORD") or config.get("QBITTORRENT_PASSWORD", "")

    if not url:
        return {"success": False, "message": "qBittorrent URL is required"}

    try:
        from qbittorrentapi import Client

        client = Client(host=url, username=username, password=password)
        client.auth_log_in()
        api_version = client.app.web_api_version
        return {"success": True, "message": f"Connected to qBittorrent (API v{api_version})"}
    except ImportError:
        return {"success": False, "message": "qbittorrent-api package not installed"}
    except Exception as e:
        return {"success": False, "message": f"Connection failed: {str(e)}"}


def _test_transmission_connection(current_values: Dict[str, Any] = None) -> Dict[str, Any]:
    """Test the Transmission connection using current form values."""
    from cwa_book_downloader.core.config import config
    from cwa_book_downloader.release_sources.prowlarr.clients.torrent_utils import (
        parse_transmission_url,
    )

    current_values = current_values or {}

    url = current_values.get("TRANSMISSION_URL") or config.get("TRANSMISSION_URL", "")
    username = current_values.get("TRANSMISSION_USERNAME") or config.get("TRANSMISSION_USERNAME", "")
    password = current_values.get("TRANSMISSION_PASSWORD") or config.get("TRANSMISSION_PASSWORD", "")

    if not url:
        return {"success": False, "message": "Transmission URL is required"}

    try:
        from transmission_rpc import Client

        # Parse URL to extract host, port, and path
        host, port, path = parse_transmission_url(url)

        client = Client(
            host=host,
            port=port,
            path=path,
            username=username if username else None,
            password=password if password else None,
        )
        session = client.get_session()
        version = session.version
        return {"success": True, "message": f"Connected to Transmission {version}"}
    except ImportError:
        return {"success": False, "message": "transmission-rpc package not installed"}
    except Exception as e:
        return {"success": False, "message": f"Connection failed: {str(e)}"}


def _test_deluge_connection(current_values: Dict[str, Any] = None) -> Dict[str, Any]:
    """Test the Deluge connection using current form values."""
    from cwa_book_downloader.core.config import config

    current_values = current_values or {}

    host = current_values.get("DELUGE_HOST") or config.get("DELUGE_HOST", "localhost")
    port = current_values.get("DELUGE_PORT") or config.get("DELUGE_PORT", "58846")
    username = current_values.get("DELUGE_USERNAME") or config.get("DELUGE_USERNAME", "")
    password = current_values.get("DELUGE_PASSWORD") or config.get("DELUGE_PASSWORD", "")

    if not host:
        return {"success": False, "message": "Deluge host is required"}
    if not password:
        return {"success": False, "message": "Deluge password is required"}

    try:
        from deluge_client import DelugeRPCClient

        client = DelugeRPCClient(
            host=host,
            port=int(port),
            username=username,
            password=password,
        )
        client.connect()
        version = client.call('daemon.info')
        return {"success": True, "message": f"Connected to Deluge {version}"}
    except ImportError:
        return {"success": False, "message": "deluge-client package not installed"}
    except Exception as e:
        return {"success": False, "message": f"Connection failed: {str(e)}"}


def _test_nzbget_connection(current_values: Dict[str, Any] = None) -> Dict[str, Any]:
    """Test the NZBGet connection using current form values."""
    import requests
    from cwa_book_downloader.core.config import config

    current_values = current_values or {}

    url = current_values.get("NZBGET_URL") or config.get("NZBGET_URL", "")
    username = current_values.get("NZBGET_USERNAME") or config.get("NZBGET_USERNAME", "nzbget")
    password = current_values.get("NZBGET_PASSWORD") or config.get("NZBGET_PASSWORD", "")

    if not url:
        return {"success": False, "message": "NZBGet URL is required"}

    try:
        rpc_url = f"{url.rstrip('/')}/jsonrpc"
        payload = {"jsonrpc": "2.0", "method": "status", "params": [], "id": 1}
        response = requests.post(rpc_url, json=payload, auth=(username, password), timeout=30)
        response.raise_for_status()
        result = response.json()
        if "error" in result and result["error"]:
            raise Exception(result["error"].get("message", "RPC error"))
        version = result.get("result", {}).get("Version", "unknown")
        return {"success": True, "message": f"Connected to NZBGet {version}"}
    except requests.exceptions.ConnectionError:
        return {"success": False, "message": "Could not connect to NZBGet"}
    except requests.exceptions.Timeout:
        return {"success": False, "message": "Connection timed out"}
    except Exception as e:
        return {"success": False, "message": f"Connection failed: {str(e)}"}


def _test_sabnzbd_connection(current_values: Dict[str, Any] = None) -> Dict[str, Any]:
    """Test the SABnzbd connection using current form values."""
    import requests
    from cwa_book_downloader.core.config import config

    current_values = current_values or {}

    url = current_values.get("SABNZBD_URL") or config.get("SABNZBD_URL", "")
    api_key = current_values.get("SABNZBD_API_KEY") or config.get("SABNZBD_API_KEY", "")

    if not url:
        return {"success": False, "message": "SABnzbd URL is required"}
    if not api_key:
        return {"success": False, "message": "API key is required"}

    try:
        api_url = f"{url.rstrip('/')}/api"
        params = {"apikey": api_key, "mode": "version", "output": "json"}
        response = requests.get(api_url, params=params, timeout=30)
        response.raise_for_status()
        result = response.json()
        version = result.get("version", "unknown")
        return {"success": True, "message": f"Connected to SABnzbd {version}"}
    except requests.exceptions.ConnectionError:
        return {"success": False, "message": "Could not connect to SABnzbd"}
    except requests.exceptions.Timeout:
        return {"success": False, "message": "Connection timed out"}
    except Exception as e:
        return {"success": False, "message": f"Connection failed: {str(e)}"}


# ==================== Register Group ====================

register_group(
    name="prowlarr",
    display_name="Prowlarr",
    icon="download",
    order=40,
)


# ==================== Configuration Tab ====================

@register_settings(
    name="prowlarr_config",
    display_name="Configuration",
    order=41,
    group="prowlarr",
)
def prowlarr_config_settings():
    """Prowlarr connection and indexer settings."""
    return [
        HeadingField(
            key="prowlarr_heading",
            title="Prowlarr Integration",
            description="Search for books across your indexers via Prowlarr.",
            link_url="https://prowlarr.com",
            link_text="prowlarr.com",
        ),
        CheckboxField(
            key="PROWLARR_ENABLED",
            label="Enable Prowlarr source",
            default=False,
            description="Enable searching for books via Prowlarr indexers",
        ),
        TextField(
            key="PROWLARR_URL",
            label="Prowlarr URL",
            description="Base URL of your Prowlarr instance",
            placeholder="http://prowlarr:9696",
            required=True,
            show_when={"field": "PROWLARR_ENABLED", "value": True},
        ),
        PasswordField(
            key="PROWLARR_API_KEY",
            label="API Key",
            description="Found in Prowlarr: Settings > General > API Key",
            required=True,
            show_when={"field": "PROWLARR_ENABLED", "value": True},
        ),
        ActionButton(
            key="test_prowlarr",
            label="Test Connection",
            description="Verify your Prowlarr configuration",
            style="primary",
            callback=_test_prowlarr_connection,
            show_when={"field": "PROWLARR_ENABLED", "value": True},
        ),
        MultiSelectField(
            key="PROWLARR_INDEXERS",
            label="Indexers to Search",
            description="Select which indexers to search. 📚 = has book categories. Leave empty to search all.",
            options=_get_indexer_options,
            default=[],
            show_when={"field": "PROWLARR_ENABLED", "value": True},
        ),
        MultiSelectField(
            key="PROWLARR_SEARCH_CATEGORIES",
            label="Search Categories",
            description="Categories to include in search. Select multiple to broaden results. Leave empty to search all categories.",
            options=[
                {"value": "7000", "label": "Books (All)"},
                {"value": "7010", "label": "Mags", "childOf": "7000"},
                {"value": "7020", "label": "Ebook", "childOf": "7000"},
                {"value": "7030", "label": "Comics", "childOf": "7000"},
                {"value": "7040", "label": "Technical", "childOf": "7000"},
                {"value": "7050", "label": "Other", "childOf": "7000"},
                {"value": "7060", "label": "Foreign", "childOf": "7000"},
                {"value": "3030", "label": "Audio/Audiobook"},
            ],
            default=["7000"],
            variant="dropdown",
            show_when={"field": "PROWLARR_ENABLED", "value": True},
        ),
    ]


# ==================== Download Clients Tab ====================

@register_settings(
    name="prowlarr_clients",
    display_name="Download Clients",
    order=42,
    group="prowlarr",
)
def prowlarr_clients_settings():
    """Download client settings for Prowlarr."""
    return [
        # --- Torrent Client Selection ---
        HeadingField(
            key="torrent_heading",
            title="Torrent Client",
            description="Select and configure a torrent client for downloading torrents from Prowlarr.",
        ),
        SelectField(
            key="PROWLARR_TORRENT_CLIENT",
            label="Torrent Client",
            description="Choose which torrent client to use",
            options=[
                {"value": "", "label": "None"},
                {"value": "qbittorrent", "label": "qBittorrent"},
                {"value": "transmission", "label": "Transmission"},
                {"value": "deluge", "label": "Deluge"},
            ],
            default="",
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
            label="Category",
            description="Category to assign to downloads in qBittorrent",
            placeholder="cwabd",
            default="cwabd",
            show_when={"field": "PROWLARR_TORRENT_CLIENT", "value": "qbittorrent"},
        ),

        # --- Transmission Settings ---
        TextField(
            key="TRANSMISSION_URL",
            label="Transmission URL",
            description="URL of your Transmission instance",
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
            label="Label",
            description="Label to assign to downloads in Transmission",
            placeholder="cwabd",
            default="cwabd",
            show_when={"field": "PROWLARR_TORRENT_CLIENT", "value": "transmission"},
        ),

        # --- Deluge Settings ---
        TextField(
            key="DELUGE_HOST",
            label="Deluge Host",
            description="Hostname or IP of your Deluge daemon",
            placeholder="localhost",
            default="localhost",
            show_when={"field": "PROWLARR_TORRENT_CLIENT", "value": "deluge"},
        ),
        TextField(
            key="DELUGE_PORT",
            label="Deluge Port",
            description="Deluge daemon RPC port (default: 58846). IMPORTANT: Ensure \"Allow Remote Connections\" is enabled in Deluge settings.",
            placeholder="58846",
            default="58846",
            show_when={"field": "PROWLARR_TORRENT_CLIENT", "value": "deluge"},
        ),
        TextField(
            key="DELUGE_USERNAME",
            label="Username",
            description="Deluge daemon username (from auth file)",
            show_when={"field": "PROWLARR_TORRENT_CLIENT", "value": "deluge"},
        ),
        PasswordField(
            key="DELUGE_PASSWORD",
            label="Password",
            description="Deluge daemon password (from auth file)",
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
            label="Label",
            description="Label to assign to downloads in Deluge",
            placeholder="cwabd",
            default="cwabd",
            show_when={"field": "PROWLARR_TORRENT_CLIENT", "value": "deluge"},
        ),
        # Note: Torrent client download path must be mounted identically in both containers.
        # Torrents are always copied (not moved) to preserve seeding capability.

        # --- Usenet Client Selection ---
        HeadingField(
            key="usenet_heading",
            title="Usenet Client",
            description="Select and configure a usenet client for downloading NZBs from Prowlarr.",
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
            label="Category",
            description="Category to assign to downloads in NZBGet",
            placeholder="Books",
            default="Books",
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
            label="Category",
            description="Category to assign to downloads in SABnzbd",
            placeholder="cwabd",
            default="cwabd",
            show_when={"field": "PROWLARR_USENET_CLIENT", "value": "sabnzbd"},
        ),

        # Note: Usenet client download path must be mounted identically in both containers.
        SelectField(
            key="PROWLARR_USENET_ACTION",
            label="Completion Action",
            description="What to do with usenet files after download completes",
            options=[
                {"value": "move", "label": "Move to ingest"},
                {"value": "copy", "label": "Copy to ingest"},
            ],
            default="move",
            show_when={"field": "PROWLARR_USENET_CLIENT", "notEmpty": True},
        ),
    ]
