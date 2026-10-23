"""AudiobookBay download handler - resolves magnet links and uses shared client lifecycle."""

import math
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from shelfmark.core.config import config
from shelfmark.core.logger import setup_logger
from shelfmark.core.request_helpers import coerce_int
from shelfmark.download.clients import (
    DownloadClient,
    get_client,
    list_configured_clients,
)
from shelfmark.download.clients.base_handler import (
    DownloadRequest,
    ExternalClientHandler,
)
from shelfmark.release_sources import register_handler
from shelfmark.release_sources.audiobookbay import scraper
from shelfmark.release_sources.audiobookbay.utils import normalize_hostname

if TYPE_CHECKING:
    from collections.abc import Callable

    from shelfmark.core.models import DownloadTask
    from shelfmark.download.postprocess.packs import PackFile

logger = setup_logger(__name__)
DEFAULT_ABB_HOSTNAME = "audiobookbay.lu"
ALLOWED_DETAIL_URL_SCHEMES = {"https"}
# retry_source_context key for the magnet resolved on an earlier attempt. It lives in the
# handler-owned context rather than retry_download_url, which queue time fills from the
# client-supplied release data.
MAGNET_LINK_CONTEXT_KEY = "magnet_link"


def _resolve_configured_hostname() -> str:
    """Return a normalized ABB hostname from config when available."""
    configured_hostname = config.get("ABB_HOSTNAME", "")
    return normalize_hostname(configured_hostname if isinstance(configured_hostname, str) else "")


def _resolve_allowed_detail_hostname() -> str:
    """Return the ABB hostname allowed for queued detail URLs."""
    return _resolve_configured_hostname() or DEFAULT_ABB_HOSTNAME


def _detail_url_matches_host(detail_url: str, hostname: str) -> bool:
    """Return True when a detail URL uses the allowed ABB scheme and host."""
    parsed = urlparse(detail_url)
    detail_hostname = normalize_hostname(parsed.hostname)
    allowed_hostname = normalize_hostname(hostname).lower().rstrip(".")
    return (
        parsed.scheme.lower() in ALLOWED_DETAIL_URL_SCHEMES
        and bool(detail_hostname)
        and detail_hostname.lower().rstrip(".") == allowed_hostname
    )


def _seed_limits() -> tuple[int | None, float | None]:
    """The configured seed time (minutes) and share ratio; None leaves the client's own limit."""
    minutes = coerce_int(config.get("ABB_SEED_TIME_MINUTES", 0), 0)
    raw_ratio = config.get("ABB_SEED_RATIO", 0)
    ratio = 0.0
    if isinstance(raw_ratio, (int, float, str)):
        try:
            ratio = float(raw_ratio)
        except ValueError:
            ratio = 0.0
    return (
        minutes if minutes > 0 else None,
        ratio if math.isfinite(ratio) and ratio > 0 else None,
    )


@register_handler("audiobookbay")
class AudiobookBayHandler(ExternalClientHandler):
    """Handler for AudiobookBay downloads via configured torrent client."""

    @staticmethod
    def _resolve_detail_url(task: DownloadTask) -> str | None:
        """Resolve ABB detail URL from queued task metadata."""
        source_url = (task.source_url or "").strip()
        if source_url:
            return source_url

        # Backward-compat: older tests and some legacy flows used task_id as URL.
        task_id = (task.task_id or "").strip()
        if task_id.startswith(("http://", "https://")):
            return task_id
        return None

    def list_files(self, release_data: dict[str, Any]) -> list[PackFile] | None:
        """Read the torrent's file list off the detail page, without downloading."""
        raw_url = release_data.get("download_url") or release_data.get("source_url")
        detail_url = raw_url.strip() if isinstance(raw_url, str) else ""
        hostname = _resolve_allowed_detail_hostname()
        if not detail_url or not _detail_url_matches_host(detail_url, hostname):
            logger.debug("Cannot list files for AudiobookBay release without a valid detail URL")
            return None
        detail_html = scraper.fetch_detail_html(detail_url, hostname)
        if not detail_html:
            return None
        return scraper.extract_file_list(detail_html)

    def list_authors(self, release_data: dict[str, Any]) -> list[str] | None:
        """Read the authors off the detail page, for a listing title that names none."""
        raw_url = release_data.get("download_url") or release_data.get("source_url")
        detail_url = raw_url.strip() if isinstance(raw_url, str) else ""
        hostname = _resolve_allowed_detail_hostname()
        if not detail_url or not _detail_url_matches_host(detail_url, hostname):
            return None
        # Cached: the pack inspection that follows a match reads the same page.
        detail_html = scraper.fetch_detail_html(detail_url, hostname)
        if not detail_html:
            return None
        return scraper.extract_authors(detail_html) or None

    def seed_complaints(self, release_data: dict[str, Any]) -> int | None:
        """Count the comments on the post that say nobody is seeding it (None: page unreadable)."""
        raw_url = release_data.get("download_url") or release_data.get("source_url")
        detail_url = raw_url.strip() if isinstance(raw_url, str) else ""
        hostname = _resolve_allowed_detail_hostname()
        if not detail_url or not _detail_url_matches_host(detail_url, hostname):
            return None
        detail_html = scraper.fetch_detail_html(detail_url, hostname)
        if not detail_html:
            return None
        return scraper.extract_seed_complaints(detail_html)

    def _get_client(self, protocol: str) -> DownloadClient | None:
        """Compatibility shim so module-level patching still works in tests."""
        return get_client(protocol)

    def _list_configured_clients(self) -> list[str]:
        """Compatibility shim so module-level patching still works in tests."""
        return list_configured_clients()

    def _resolve_download(
        self,
        task: DownloadTask,
        status_callback: Callable[[str, str | None], None],
    ) -> DownloadRequest | None:
        """Resolve ABB detail page into a magnet-link download request."""
        # A retry must not depend on AudiobookBay being reachable: the torrent may already
        # be finished in the client, and the magnet cannot go stale (#1388).
        cached_magnet = task.retry_source_context.get(MAGNET_LINK_CONTEXT_KEY)
        if isinstance(cached_magnet, str) and cached_magnet:
            logger.info("Reusing magnet link from an earlier attempt for task %s", task.task_id)
            seeding_time_limit, ratio_limit = _seed_limits()
            return DownloadRequest(
                url=cached_magnet,
                protocol="torrent",
                release_name=task.title or "Unknown",
                expected_hash=None,
                seeding_time_limit=seeding_time_limit,
                ratio_limit=ratio_limit,
            )

        detail_url = self._resolve_detail_url(task)
        if not detail_url:
            status_callback("error", "Missing AudiobookBay details URL")
            logger.warning("Missing details URL for AudiobookBay task: %s", task.task_id)
            return None

        hostname = _resolve_allowed_detail_hostname()
        if not _detail_url_matches_host(detail_url, hostname):
            status_callback("error", "Invalid AudiobookBay details URL")
            logger.warning(
                "Rejected AudiobookBay details URL with invalid scheme or host: %s",
                detail_url,
            )
            return None

        status_callback("resolving", "Extracting magnet link")
        magnet_link = scraper.extract_magnet_link(detail_url, hostname)

        if not magnet_link:
            status_callback("error", "Failed to extract magnet link from detail page")
            return None

        logger.info("Extracted magnet link for task %s", task.task_id)
        task.retry_source_context[MAGNET_LINK_CONTEXT_KEY] = magnet_link

        seeding_time_limit, ratio_limit = _seed_limits()
        return DownloadRequest(
            url=magnet_link,
            protocol="torrent",
            release_name=task.title or "Unknown",
            expected_hash=None,
            seeding_time_limit=seeding_time_limit,
            ratio_limit=ratio_limit,
        )

    def cancel(self, task_id: str) -> bool:
        """Cancel an in-progress download.

        Shelfmark can stop waiting via the queue cancel flag, but once a magnet has
        been sent to the torrent client we do not remove it client-side. Users must
        cancel/remove it in their torrent client UI.
        """
        logger.debug("Cancel requested for AudiobookBay task: %s", task_id)
        return False
