"""SABnzbd download client for Prowlarr integration.

Uses SABnzbd's REST API directly via requests (no external dependency).
"""

import ipaddress
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from publicsuffixlist import PublicSuffixList

from shelfmark.core.config import config
from shelfmark.core.logger import setup_logger
from shelfmark.download.clients import (
    DownloadClient,
    DownloadStatus,
    register_client,
    with_retry,
)
from shelfmark.download.clients._coercion import config_text, normalize_http_config_url
from shelfmark.download.network import get_ssl_verify

logger = setup_logger(__name__)

_ETA_PART_COUNT = 3
_SPEED_PARTS_MIN = 2
_SABNZBD_CLIENT_ERRORS = (
    requests.exceptions.RequestException,
    AttributeError,
    RuntimeError,
    TypeError,
    ValueError,
)
_SabnzbdRequestParam = str | int | float | bool
_PUBLIC_SUFFIXES = PublicSuffixList()
_NZB_FETCH_MAX_REDIRECTS = 5


def _url_origin(value: str) -> tuple[str, str, int] | None:
    try:
        parsed = urlparse(value)
        port = parsed.port
    except ValueError:
        return None

    scheme = parsed.scheme.lower()
    hostname = (parsed.hostname or "").lower()
    if scheme not in {"http", "https"} or not hostname:
        return None

    if port is None:
        port = 443 if scheme == "https" else 80

    return scheme, hostname, port


def _origin_is_trusted(
    target: tuple[str, str, int],
    trusted: tuple[str, str, int] | None,
) -> bool:
    """Match the trusted origin exactly, or any host in its registrable domain on the same scheme/port.

    Indexers commonly serve NZB downloads from a different host in the same domain
    as their API (e.g. dl.indexer.example for api.indexer.example). The Public Suffix
    List keeps this from widening to shared suffixes such as co.uk or duckdns.org.
    """
    if trusted is None:
        return False
    if target == trusted:
        return True

    target_scheme, target_host, target_port = target
    trusted_scheme, trusted_host, trusted_port = trusted
    if (target_scheme, target_port) != (trusted_scheme, trusted_port):
        return False

    try:
        ipaddress.ip_address(trusted_host)
    except ValueError:
        trusted_domain = _PUBLIC_SUFFIXES.privatesuffix(trusted_host)
        return trusted_domain is not None and (
            _PUBLIC_SUFFIXES.privatesuffix(target_host) == trusted_domain
        )
    return False


def _parse_eta(eta_str: str) -> int | None:
    """Parse SABnzbd ETA string (format: 'H:MM:SS') to seconds."""
    if not eta_str or eta_str == "0:00:00":
        return None
    try:
        parts = eta_str.split(":")
        if len(parts) == _ETA_PART_COUNT:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    except ValueError, IndexError:
        pass
    return None


def _parse_speed(slot: dict) -> int | None:
    """Parse download speed from SABnzbd slot data, returning bytes/sec."""
    # Prefer kbpersec field (more reliable numeric value)
    kbpersec_str = slot.get("kbpersec", "")
    if kbpersec_str:
        try:
            return int(float(kbpersec_str) * 1024)
        except ValueError, TypeError:
            pass

    # Fall back to human-readable speed field
    speed_str = slot.get("speed", "")
    if not speed_str:
        return None

    try:
        speed_parts = speed_str.split()
        if len(speed_parts) < _SPEED_PARTS_MIN:
            return None
        speed_val = float(speed_parts[0])
        unit = speed_parts[1].upper()
        multipliers = {"K": 1024, "M": 1024**2, "G": 1024**3}
        for prefix, mult in multipliers.items():
            if prefix in unit:
                return int(speed_val * mult)
        return int(speed_val)
    except ValueError, IndexError:
        return None


@register_client("usenet")
class SABnzbdClient(DownloadClient):
    """SABnzbd download client using REST API."""

    @staticmethod
    def _resolve_completed_storage_path(storage: str, title: str) -> str:
        """Normalize SABnzbd's `storage` into a stable "job root" folder.

        Walks up parent directories looking for a directory named exactly like the
        job `title`.

        This helps when SABnzbd reports a nested path (e.g. sorting/post-processing)
        but we want the root folder for the completed job.
        """
        from pathlib import Path

        storage = storage or ""
        title = (title or "").strip()
        if not storage or not title:
            return storage

        # SAB returns absolute paths; don't require existence on disk.
        path = Path(storage)
        best_match: Path | None = None

        for parent in [path, *path.parents]:
            if parent.name == title:
                best_match = parent

        return str(best_match) if best_match is not None else storage

    protocol = "usenet"
    name = "sabnzbd"

    def __init__(self) -> None:
        """Initialize SABnzbd client with settings from config."""
        raw_url = config_text(config.get("SABNZBD_URL", ""))
        if not raw_url:
            msg = "SABNZBD_URL is required"
            raise ValueError(msg)

        api_key = config_text(config.get("SABNZBD_API_KEY", ""))
        if not api_key:
            msg = "SABNZBD_API_KEY is required"
            raise ValueError(msg)

        self.url = normalize_http_config_url(raw_url)
        if not self.url:
            msg = "SABNZBD_URL is invalid"
            raise ValueError(msg)
        self.api_key = api_key
        self._category = config_text(config.get("SABNZBD_CATEGORY", "books"))

    @staticmethod
    def is_configured() -> bool:
        """Check if SABnzbd is configured and selected as the usenet client."""
        client = config_text(config.get("PROWLARR_USENET_CLIENT", ""))
        url = normalize_http_config_url(config.get("SABNZBD_URL", ""))
        api_key = config_text(config.get("SABNZBD_API_KEY", ""))
        return client == "sabnzbd" and bool(url) and bool(api_key)

    @with_retry()
    def _api_call(self, mode: str, params: dict[str, _SabnzbdRequestParam] | None = None) -> Any:
        """Make an API call to SABnzbd.

        Args:
            mode: API mode (e.g., "version", "addurl", "queue", "history")
            params: Additional parameters

        Returns:
            JSON response from SABnzbd.

        Raises:
            Exception: If API call fails after retries.

        """
        api_url = f"{self.url}/api"

        request_params: dict[str, _SabnzbdRequestParam] = {
            "apikey": self.api_key,
            "mode": mode,
            "output": "json",
        }
        if params:
            request_params.update(params)

        response = requests.get(
            api_url, params=request_params, timeout=30, verify=get_ssl_verify(api_url)
        )
        response.raise_for_status()

        result = response.json()

        # Check for error in response
        if isinstance(result, dict) and result.get("status") is False:
            error = result.get("error", "Unknown error")
            msg = f"SABnzbd error: {error}"
            raise RuntimeError(msg)

        return result

    def _api_post_file(
        self, nzb_content: bytes, filename: str, nzb_name: str, category: str
    ) -> Any:
        """Upload an NZB file to SABnzbd using addfile.

        Returns:
            JSON response from SABnzbd.

        """
        api_url = f"{self.url}/api"
        request_params: dict[str, _SabnzbdRequestParam] = {
            "apikey": self.api_key,
            "mode": "addfile",
            "output": "json",
            "nzbname": nzb_name,
            "cat": category,
        }
        files = {"name": (filename, nzb_content, "application/x-nzb")}

        response = requests.post(
            api_url,
            params=request_params,
            files=files,
            timeout=30,
            verify=get_ssl_verify(api_url),
        )
        response.raise_for_status()
        result = response.json()

        if isinstance(result, dict) and result.get("status") is False:
            error = result.get("error", "Unknown error")
            msg = f"SABnzbd error: {error}"
            raise RuntimeError(msg)

        return result

    def _fetch_nzb_content(self, url: str) -> bytes:
        """Fetch NZB content, including Prowlarr auth headers when appropriate."""
        # Follow redirects manually so the Prowlarr API key is re-evaluated
        # per hop. requests forwards custom headers such as X-Api-Key to the
        # redirect target even when it is a different host, so letting it
        # follow a Prowlarr 302 to the indexer's own download link would leak
        # the key to a third party. This mirrors the torrent fetch path.
        current_url = url
        redirects_remaining = _NZB_FETCH_MAX_REDIRECTS
        while True:
            headers = self._get_prowlarr_headers(current_url)
            response = requests.get(
                current_url,
                timeout=30,
                headers=headers,
                verify=get_ssl_verify(current_url),
                allow_redirects=False,
            )
            if response.status_code not in (301, 302, 303, 307, 308):
                response.raise_for_status()
                return response.content
            location = response.headers.get("Location", "")
            if not location:
                response.raise_for_status()
                return response.content
            if redirects_remaining <= 0:
                msg = f"Too many redirects fetching NZB from {urlparse(url).hostname or 'url'}"
                raise requests.exceptions.TooManyRedirects(msg)
            redirects_remaining -= 1
            current_url = urljoin(current_url, location)

    def _can_prefetch_nzb_url(self, url: str) -> bool:
        target_origin = _url_origin(url)
        if target_origin is None:
            return False

        for key in ("PROWLARR_URL", "NEWZNAB_URL"):
            trusted_url = normalize_http_config_url(config.get(key, ""))
            if trusted_url and _origin_is_trusted(target_origin, _url_origin(trusted_url)):
                return True

        named_indexers = config.get("NEWZNAB_INDEXERS", [])
        if isinstance(named_indexers, list):
            for row in named_indexers:
                if not isinstance(row, dict):
                    continue
                trusted_url = normalize_http_config_url(row.get("url"))
                if trusted_url and _origin_is_trusted(target_origin, _url_origin(trusted_url)):
                    return True

        return False

    def _get_prowlarr_headers(self, url: str) -> dict:
        # TODO(shelfmark): Move this source-specific Prowlarr auth handling into a source hook.
        api_key = str(config.get("PROWLARR_API_KEY", "") or "").strip()
        if not api_key:
            return {}

        prowlarr_url = normalize_http_config_url(config.get("PROWLARR_URL", ""))
        if not prowlarr_url:
            return {}

        try:
            target = urlparse(url)
            base = urlparse(prowlarr_url)
        except ValueError:
            return {}

        if target.hostname and base.hostname and target.hostname.lower() == base.hostname.lower():
            return {"X-Api-Key": api_key}

        return {}

    @staticmethod
    def _build_nzb_filename(name: str, url: str) -> str:
        base_name = (name or "download").strip() or "download"
        parsed = urlparse(url)
        path = parsed.path or ""
        lower_path = path.lower()

        if lower_path.endswith(".nzb.gz"):
            suffix = ".nzb.gz"
        elif lower_path.endswith(".nzb"):
            suffix = ".nzb"
        else:
            suffix = ""

        lower_base = base_name.lower()
        if suffix and lower_base.endswith(suffix):
            return base_name
        if suffix == ".nzb.gz" and lower_base.endswith(".nzb"):
            return f"{base_name}.gz"
        if suffix:
            return f"{base_name}{suffix}"
        if lower_base.endswith((".nzb", ".nzb.gz")):
            return base_name
        return f"{base_name}.nzb"

    @staticmethod
    def _extract_nzo_id(result: object) -> str:
        if not isinstance(result, dict):
            msg = "SABnzbd returned invalid response"
            raise TypeError(msg)

        nzo_ids = result.get("nzo_ids") or result.get("nzo_id")
        if isinstance(nzo_ids, list) and nzo_ids:
            return str(nzo_ids[0])
        if isinstance(nzo_ids, str) and nzo_ids:
            return nzo_ids
        if isinstance(nzo_ids, int):
            return str(nzo_ids)

        msg = "SABnzbd returned no nzo_id"
        raise RuntimeError(msg)

    def test_connection(self) -> tuple[bool, str]:
        """Test connection to SABnzbd."""
        try:
            result = self._api_call("version")
            version = result.get("version", "unknown")
        except requests.exceptions.ConnectionError:
            return False, "Could not connect to SABnzbd"
        except requests.exceptions.Timeout:
            return False, "Connection timed out"
        except _SABNZBD_CLIENT_ERRORS as e:
            return False, f"Connection failed: {e!s}"
        else:
            return True, f"Connected to SABnzbd {version}"

    def add_download(
        self,
        url: str,
        name: str,
        category: str | None = None,
        expected_hash: str | None = None,
        **kwargs: object,
    ) -> str:
        """Add NZB by URL.

        Args:
            url: NZB URL (can be Prowlarr proxy URL)
            name: Display name for the download
            category: Category for organization (uses configured default if not specified)
            expected_hash: Optional info_hash hint (unused)
            **kwargs: Client-specific options passed through to the implementation.

        Returns:
            SABnzbd nzo_id.

        Raises:
            Exception: If adding fails.

        """
        # Use configured category if not explicitly provided
        resolved_category = category or self._category

        try:
            logger.debug("Adding NZB to SABnzbd: %s", name)
            if self._can_prefetch_nzb_url(url):
                nzb_filename = self._build_nzb_filename(name, url)
                nzb_content = self._fetch_nzb_content(url)
                result = self._api_post_file(nzb_content, nzb_filename, name, resolved_category)
                nzo_id = self._extract_nzo_id(result)
                logger.info("Added NZB to SABnzbd: %s", nzo_id)
            else:
                logger.info("Skipping SABnzbd addfile prefetch for untrusted NZB URL")
                nzo_id = ""
        except _SABNZBD_CLIENT_ERRORS as e:
            logger.warning("SABnzbd addfile failed, falling back to addurl: %s", e)
        else:
            if nzo_id:
                return nzo_id

        try:
            result = self._api_call(
                "addurl",
                {
                    "name": url,
                    "nzbname": name,
                    "cat": resolved_category,
                },
            )
            nzo_id = self._extract_nzo_id(result)
            logger.info("Added NZB to SABnzbd via addurl: %s", nzo_id)
        except _SABNZBD_CLIENT_ERRORS:
            logger.exception("SABnzbd add failed")
            raise
        else:
            return nzo_id

    def get_status(self, download_id: str) -> DownloadStatus:
        """Get NZB status by nzo_id.

        Args:
            download_id: SABnzbd nzo_id

        Returns:
            Current download status.

        """
        try:
            # Check active queue first
            queue_result = self._api_call("queue")
            queue = queue_result.get("queue", {})
            slots = queue.get("slots", [])

            for slot in slots:
                if slot.get("nzo_id") == download_id:
                    # Found in queue
                    status_text = slot.get("status", "").upper()
                    percentage = float(slot.get("percentage", 0))

                    # Map SABnzbd status to our states
                    status_mapping = {
                        "DOWNLOADING": "downloading",
                        "PAUSED": "paused",
                        "QUEUED": "queued",
                        "IDLE": "queued",
                        "PROPAGATING": "queued",
                        "FETCHING": "queued",
                        "GRABBING": "queued",
                        "VERIFYING": "processing",
                        "REPAIRING": "processing",
                        "EXTRACTING": "processing",
                        "MOVING": "processing",
                        "RUNNING": "processing",
                        "FAILED": "error",
                    }
                    state = status_mapping.get(status_text, "downloading")

                    return DownloadStatus(
                        progress=percentage,
                        state=state,
                        message=status_text.lower().replace("_", " ").title(),
                        complete=False,
                        file_path=None,
                        download_speed=_parse_speed(slot),
                        eta=_parse_eta(slot.get("timeleft", "")),
                    )

            # Not in queue, check history
            history_result = self._api_call("history", {"limit": 100})
            history = history_result.get("history", {})
            history_slots = history.get("slots", [])

            for slot in history_slots:
                if slot.get("nzo_id") == download_id:
                    status_text = slot.get("status", "").upper()
                    storage = slot.get("storage", "")
                    if storage is None:
                        storage = ""
                    logger.debug(
                        "SABnzbd history: %s status=%s storage='%s'",
                        download_id,
                        status_text,
                        storage,
                    )

                    if status_text == "COMPLETED":
                        title = slot.get("name") or slot.get("nzb_name") or ""
                        resolved_storage = self._resolve_completed_storage_path(storage, title)

                        return DownloadStatus(
                            progress=100,
                            state="complete",
                            message="Complete",
                            complete=True,
                            file_path=resolved_storage,
                        )
                    if status_text == "FAILED":
                        fail_message = slot.get("fail_message", "Download failed")
                        title = slot.get("name") or slot.get("nzb_name") or ""
                        resolved_storage = self._resolve_completed_storage_path(storage, title)
                        return DownloadStatus(
                            progress=100,
                            state="error",
                            message=fail_message,
                            complete=True,
                            file_path=resolved_storage,
                        )
                    # Post-processing states: Queued, QuickCheck, Verifying,
                    # Repairing, Fetching, Extracting, Moving, Running
                    # Keep polling - not yet complete
                    return DownloadStatus(
                        progress=100,
                        state="processing",
                        message=status_text.title(),
                        complete=False,
                        file_path=None,
                    )

            # Not found
            logger.warning("SABnzbd: download %s not found in queue or history", download_id)
            return DownloadStatus.error("Download not found")
        except _SABNZBD_CLIENT_ERRORS as e:
            return DownloadStatus.error(self._log_error("get_status", e))

    def remove(self, download_id: str, *, delete_files: bool = False, archive: bool = True) -> bool:
        """Remove a download from SABnzbd.

        Args:
            download_id: SABnzbd nzo_id
            delete_files: Whether to delete the files
            archive: If True, move to archive instead of permanent delete (history only)

        Returns:
            True if successful.

        """
        # First try to remove from queue. If it isn't there (common for completed jobs),
        # fall back to history removal instead of failing fast on a SABnzbd error response.
        try:
            result = self._api_call(
                "queue",
                {
                    "name": "delete",
                    "value": download_id,
                    "del_files": 1 if delete_files else 0,
                },
            )

            if result.get("status"):
                logger.info("Removed NZB from SABnzbd queue: %s", download_id)
                return True
        except _SABNZBD_CLIENT_ERRORS as e:
            logger.debug("SABnzbd queue delete skipped for %s: %s", download_id, e)

        # If not in queue (or queue delete failed), try to remove from history.
        try:
            result = self._api_call(
                "history",
                {
                    "name": "delete",
                    "value": download_id,
                    "del_files": 1 if delete_files else 0,
                    "archive": 1 if archive else 0,
                },
            )

            if result.get("status"):
                action = "archived" if archive else "removed"
                logger.info("NZB %s from SABnzbd history: %s", action, download_id)
                return True
        except _SABNZBD_CLIENT_ERRORS as e:
            self._log_error("remove", e)
            return False

        return False

    def get_download_path(self, download_id: str) -> str | None:
        """Get the path where NZB files are located.

        Args:
            download_id: SABnzbd nzo_id

        Returns:
            Storage directory, or None.

        """
        status = self.get_status(download_id)
        return status.file_path

    def find_existing(
        self, url: str, category: str | None = None
    ) -> tuple[str, DownloadStatus] | None:
        """Check if an NZB for this URL already exists in SABnzbd.

        Note: Unlike torrents which have a unique info_hash, usenet NZBs don't have
        a universal unique identifier. SABnzbd generates an nzo_id when adding,
        but there's no way to derive it from the URL. This method searches by
        NZB name extracted from the URL, which may not always be accurate.

        Args:
            url: NZB URL
            category: Category to filter by (defaults to configured category)

        Returns:
            Tuple of (nzo_id, status) if found, None if not found.

        """
        try:
            # Extract NZB name from URL (last path component without extension)
            from urllib.parse import unquote, urlparse

            parsed = urlparse(url)
            path = unquote(parsed.path)

            # Get filename from path
            filename = path.rsplit("/", 1)[-1] if "/" in path else path

            # Remove common NZB extensions
            for ext in [".nzb", ".nzb.gz"]:
                if filename.lower().endswith(ext):
                    filename = filename[: -len(ext)]
                    break

            if not filename:
                return None

            # Use provided category or fall back to configured default
            search_category = category or self._category

            # Search queue (SABnzbd uses "cat" field for category in queue)
            queue_result = self._api_call("queue")
            queue = queue_result.get("queue", {})
            for slot in queue.get("slots", []):
                if slot.get("cat", "") != search_category:
                    continue
                slot_name = slot.get("filename", "")
                if filename.lower() in slot_name.lower():
                    nzo_id = slot.get("nzo_id")
                    if nzo_id:
                        status = self.get_status(nzo_id)
                        logger.debug("Found existing NZB in SABnzbd queue: %s", nzo_id)
                        return (nzo_id, status)

            # Search history (SABnzbd uses "category" field in history)
            history_result = self._api_call("history", {"limit": 100})
            history = history_result.get("history", {})
            for slot in history.get("slots", []):
                if slot.get("category", "") != search_category:
                    continue
                slot_name = slot.get("name", "")
                if filename.lower() in slot_name.lower():
                    nzo_id = slot.get("nzo_id")
                    if nzo_id:
                        status = self.get_status(nzo_id)
                        logger.debug("Found existing NZB in SABnzbd history: %s", nzo_id)
                        return (nzo_id, status)

        except _SABNZBD_CLIENT_ERRORS as e:
            logger.debug("Error checking for existing NZB: %s", e)
            return None
        else:
            return None
