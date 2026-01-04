"""
NZBGet download client for Prowlarr integration.

Uses NZBGet's JSON-RPC API directly via requests (no external dependency).
"""

import json
from typing import Any, Optional, Tuple

import requests

from cwa_book_downloader.core.config import config
from cwa_book_downloader.core.logger import setup_logger
from cwa_book_downloader.release_sources.prowlarr.clients import (
    DownloadClient,
    DownloadStatus,
    register_client,
)

logger = setup_logger(__name__)


@register_client("usenet")
class NZBGetClient(DownloadClient):
    """NZBGet download client using JSON-RPC API."""

    protocol = "usenet"
    name = "nzbget"

    def __init__(self):
        """Initialize NZBGet client with settings from config."""
        url = config.get("NZBGET_URL", "")
        if not url:
            raise ValueError("NZBGET_URL is required")

        self.url = url.rstrip("/")
        self.username = config.get("NZBGET_USERNAME", "nzbget")
        self.password = config.get("NZBGET_PASSWORD", "")
        self._category = config.get("NZBGET_CATEGORY", "Books")

    @staticmethod
    def is_configured() -> bool:
        """Check if NZBGet is configured and selected as the usenet client."""
        client = config.get("PROWLARR_USENET_CLIENT", "")
        url = config.get("NZBGET_URL", "")
        return client == "nzbget" and bool(url)

    def _rpc_call(self, method: str, params: list = None) -> Any:
        """
        Make a JSON-RPC call to NZBGet.

        Args:
            method: RPC method name
            params: Method parameters

        Returns:
            Result from NZBGet.

        Raises:
            Exception: If RPC call fails.
        """
        rpc_url = f"{self.url}/jsonrpc"

        payload = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params or [],
        }, separators=(',', ':'))

        response = requests.post(
            rpc_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            auth=(self.username, self.password),
            timeout=30,
        )
        response.raise_for_status()

        result = response.json()
        if "error" in result and result["error"]:
            raise Exception(result["error"].get("message", "RPC error"))

        return result.get("result")

    def test_connection(self) -> Tuple[bool, str]:
        """Test connection to NZBGet."""
        try:
            status = self._rpc_call("status")
            version = status.get("Version", "unknown")
            return True, f"Connected to NZBGet {version}"
        except requests.exceptions.ConnectionError:
            return False, "Could not connect to NZBGet"
        except requests.exceptions.Timeout:
            return False, "Connection timed out"
        except Exception as e:
            return False, f"Connection failed: {str(e)}"

    def add_download(self, url: str, name: str, category: str = None) -> str:
        """
        Add NZB by URL.

        Fetches the NZB content from the URL (e.g., Prowlarr proxy) and sends
        it base64-encoded to NZBGet, since NZBGet may not handle redirects well.

        Args:
            url: NZB URL (can be Prowlarr proxy URL)
            name: Display name for the download
            category: Category for organization (uses configured default if not specified)

        Returns:
            NZBGet download ID (NZBID).

        Raises:
            Exception: If adding fails.
        """
        import base64

        # Use configured category if not explicitly provided
        category = category or self._category

        try:
            # Fetch NZB content from the URL (handles Prowlarr proxy redirects)
            logger.debug(f"Fetching NZB from: {url}")
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            nzb_content = base64.b64encode(response.content).decode('ascii')

            # Ensure filename has .nzb extension
            nzb_filename = name if name.endswith('.nzb') else f"{name}.nzb"

            # NZBGet append method parameters (all 10 required):
            # NZBFilename, Content, Category, Priority, AddToTop, AddPaused,
            # DupeKey, DupeScore, DupeMode, PPParameters
            nzb_id = self._rpc_call(
                "append",
                [
                    nzb_filename,  # NZBFilename
                    nzb_content,  # Content (base64-encoded NZB)
                    category,  # Category
                    0,  # Priority (0 = normal)
                    False,  # AddToTop
                    False,  # AddPaused
                    "",  # DupeKey
                    0,  # DupeScore
                    "SCORE",  # DupeMode
                    [],  # PPParameters (empty array)
                ],
            )

            if nzb_id and nzb_id > 0:
                logger.info(f"Added NZB to NZBGet: {nzb_id}")
                return str(nzb_id)

            raise Exception("NZBGet returned invalid ID")
        except requests.RequestException as e:
            logger.error(f"Failed to fetch NZB from URL: {e}")
            raise Exception(f"Failed to fetch NZB: {e}")
        except Exception as e:
            logger.error(f"NZBGet add failed: {e}")
            raise

    def get_status(self, download_id: str) -> DownloadStatus:
        """
        Get NZB status by ID.

        Args:
            download_id: NZBGet NZBID

        Returns:
            Current download status.
        """
        try:
            nzb_id = int(download_id)

            # Check active downloads (queue)
            groups = self._rpc_call("listgroups", [0])

            for group in groups:
                if group.get("NZBID") == nzb_id:
                    # Calculate progress
                    # NZBGet uses Hi/Lo for 64-bit values on 32-bit systems
                    file_size = (group.get("FileSizeHi", 0) << 32) + group.get(
                        "FileSizeLo", 0
                    )
                    remaining = (group.get("RemainingSizeHi", 0) << 32) + group.get(
                        "RemainingSizeLo", 0
                    )

                    progress = (
                        ((file_size - remaining) / file_size * 100)
                        if file_size > 0
                        else 0
                    )
                    status = group.get("Status", "")

                    # Map NZBGet status to our states
                    if "DOWNLOADING" in status:
                        state = "downloading"
                    elif "PAUSED" in status:
                        state = "paused"
                    elif "QUEUED" in status:
                        state = "queued"
                    elif "POST-PROCESSING" in status or "UNPACKING" in status:
                        state = "processing"
                    else:
                        state = "unknown"

                    return DownloadStatus(
                        progress=progress,
                        state=state,
                        message=status.replace("-", " ").title(),
                        complete=False,
                        file_path=None,
                        download_speed=group.get("DownloadRate"),
                        eta=(
                            group.get("RemainingSec")
                            if group.get("RemainingSec", 0) > 0
                            else None
                        ),
                    )

            # Check history for completed downloads
            history = self._rpc_call("history", [False])

            for item in history:
                if item.get("NZBID") == nzb_id:
                    status = item.get("Status", "")
                    dest_dir = item.get("DestDir", "")

                    if "SUCCESS" in status:
                        return DownloadStatus(
                            progress=100,
                            state="complete",
                            message="Complete",
                            complete=True,
                            file_path=dest_dir,
                        )
                    else:
                        return DownloadStatus(
                            progress=100,
                            state="error",
                            message=f"Download failed: {status}",
                            complete=True,
                            file_path=None,
                        )

            # Not found in queue or history
            return DownloadStatus.error("Download not found")
        except Exception as e:
            error_type = type(e).__name__
            logger.error(f"NZBGet get_status failed ({error_type}): {e}")
            return DownloadStatus.error(f"{error_type}: {e}")

    def remove(self, download_id: str, delete_files: bool = False) -> bool:
        """
        Remove a download from NZBGet.

        Args:
            download_id: NZBGet NZBID
            delete_files: Whether to permanently delete (vs move to history)

        Returns:
            True if successful.
        """
        try:
            nzb_id = int(download_id)
            # editqueue params: Command (str), Param (str), IDs (int[])
            # GroupFinalDelete = permanent removal, GroupDelete = move to history
            command = "GroupFinalDelete" if delete_files else "GroupDelete"
            result = self._rpc_call("editqueue", [command, "", [nzb_id]])
            if result:
                logger.info(f"Removed NZB from NZBGet: {download_id}")
            return bool(result)
        except Exception as e:
            error_type = type(e).__name__
            logger.error(f"NZBGet remove failed ({error_type}): {e}")
            return False

    def get_download_path(self, download_id: str) -> Optional[str]:
        """
        Get the path where NZB files are located.

        Args:
            download_id: NZBGet NZBID

        Returns:
            Destination directory, or None.
        """
        status = self.get_status(download_id)
        return status.file_path
