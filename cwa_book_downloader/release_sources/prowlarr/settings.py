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
        version = client.app.version
        return {"success": True, "message": f"Connected to qBittorrent {version}"}
    except ImportError:
        return {"success": False, "message": "qbittorrent-api package not installed"}
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
    icon="settings",
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
        TextField(
            key="PROWLARR_URL",
            label="Prowlarr URL",
            description="Base URL of your Prowlarr instance",
            placeholder="http://prowlarr:9696",
            required=True,
        ),
        PasswordField(
            key="PROWLARR_API_KEY",
            label="API Key",
            description="Found in Prowlarr: Settings > General > API Key",
            required=True,
        ),
        ActionButton(
            key="test_prowlarr",
            label="Test Connection",
            description="Verify your Prowlarr configuration",
            style="primary",
            callback=_test_prowlarr_connection,
        ),
        MultiSelectField(
            key="PROWLARR_INDEXERS",
            label="Indexers to Search",
            description="Select which indexers to search. 📚 = has book categories. Leave empty to search all.",
            options=_get_indexer_options,
            default=[],
            show_when={"field": "PROWLARR_URL", "notEmpty": True},
        ),
    ]


# ==================== Download Clients Tab ====================

@register_settings(
    name="prowlarr_clients",
    display_name="Download Clients",
    icon="download",
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
        # Note: qBittorrent's download path must be mounted identically in both containers.
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
        # Note: NZBGet's download path must be mounted identically in both containers.
        SelectField(
            key="PROWLARR_USENET_ACTION",
            label="Completion Action",
            description="What to do with usenet files after download completes",
            options=[
                {"value": "move", "label": "Move to ingest"},
                {"value": "copy", "label": "Copy to ingest"},
            ],
            default="move",
            show_when={"field": "PROWLARR_USENET_CLIENT", "value": "nzbget"},
        ),
    ]
