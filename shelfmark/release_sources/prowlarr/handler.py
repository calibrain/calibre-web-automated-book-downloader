"""Prowlarr download handler - resolves releases and delegates lifecycle to shared clients."""

from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import requests

from shelfmark.core.config import config
from shelfmark.core.logger import setup_logger
from shelfmark.core.request_helpers import normalize_optional_text
from shelfmark.core.search_plan import build_release_search_plan
from shelfmark.core.utils import normalize_http_url
from shelfmark.download.clients import (
    DownloadClient,
    client_prefers_torrent_file,
    get_client,
    list_configured_clients,
)
from shelfmark.download.clients.base_handler import (
    COMPLETED_PATH_MAX_ATTEMPTS as _DEFAULT_COMPLETED_PATH_MAX_ATTEMPTS,
)
from shelfmark.download.clients.base_handler import (
    COMPLETED_PATH_RETRY_INTERVAL as _DEFAULT_COMPLETED_PATH_RETRY_INTERVAL,
)
from shelfmark.download.clients.base_handler import (
    POLL_INTERVAL as _DEFAULT_POLL_INTERVAL,
)
from shelfmark.download.clients.base_handler import (
    DownloadRequest,
    ExternalClientHandler,
)
from shelfmark.download.clients.torrent_utils import (
    extract_file_list_from_torrent,
    extract_torrent_info,
)
from shelfmark.metadata_providers import BookMetadata
from shelfmark.release_sources import register_handler
from shelfmark.release_sources.prowlarr.api import IndexerSeedSettings, ProwlarrClient
from shelfmark.release_sources.prowlarr.cache import cache_release, get_release, remove_release
from shelfmark.release_sources.prowlarr.source import ProwlarrSource
from shelfmark.release_sources.prowlarr.utils import (
    build_source_id,
    coerce_int_like,
    get_preferred_download_url,
    get_protocol,
    sanitize_download_url,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from shelfmark.core.models import DownloadTask
    from shelfmark.download.postprocess.packs import PackFile

logger = setup_logger(__name__)

# Errors that ProwlarrClient can raise when fetching indexer settings.
_SEED_SETTINGS_FALLBACK_ERRORS = (
    requests.exceptions.RequestException,
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
)

__all__ = [
    "ProwlarrHandler",
    "POLL_INTERVAL",
    "COMPLETED_PATH_RETRY_INTERVAL",
    "COMPLETED_PATH_MAX_ATTEMPTS",
    "config",
]

# Backwards-compat constants for tests patching this module.
POLL_INTERVAL = _DEFAULT_POLL_INTERVAL
COMPLETED_PATH_RETRY_INTERVAL = _DEFAULT_COMPLETED_PATH_RETRY_INTERVAL
COMPLETED_PATH_MAX_ATTEMPTS = _DEFAULT_COMPLETED_PATH_MAX_ATTEMPTS
EXPIRED_LINK_REFRESH_ERROR = (
    "The indexer download link expired and the release could not be refreshed. "
    "Search again for a fresh result."
)
HASH_DETECTION_ERROR = "Could not determine torrent hash from URL"


def _coerce_positive_minutes(raw_minutes: object) -> int | None:
    minutes = coerce_int_like(raw_minutes)
    if minutes is None:
        return None
    return minutes if minutes > 0 else None


@register_handler("prowlarr")
class ProwlarrHandler(ExternalClientHandler):
    """Handler for Prowlarr downloads via configured torrent or usenet client."""

    @staticmethod
    def _build_prowlarr_client() -> ProwlarrClient | None:
        """Build a ProwlarrClient from config, or None if not configured."""
        raw_url = config.get("PROWLARR_URL", "")
        raw_api_key = config.get("PROWLARR_API_KEY", "")
        url = normalize_optional_text(raw_url) if isinstance(raw_url, str) else None
        api_key = normalize_optional_text(raw_api_key) if isinstance(raw_api_key, str) else None
        if not url or not api_key:
            return None
        normalized_url = normalize_http_url(url)
        if not normalized_url:
            return None
        return ProwlarrClient(normalized_url, api_key)

    def _fetch_seed_settings_fallback(self, raw_indexer_id: object) -> IndexerSeedSettings | None:
        """Fetch share limits for one indexer directly from Prowlarr.

        Used when the cached release is missing its search-time seed-limit
        enrichment so that transient failures during search cannot cause a
        torrent to be added without its configured share limits.
        """
        indexer_id = coerce_int_like(raw_indexer_id)
        if indexer_id is None:
            return None

        client = self._build_prowlarr_client()
        if client is None:
            return None

        try:
            settings = client.get_indexer_seed_settings(restrict_to=[indexer_id])
        except _SEED_SETTINGS_FALLBACK_ERRORS:
            logger.warning(
                "Grab-time seed settings fallback failed for indexerId=%s",
                indexer_id,
                exc_info=True,
            )
            return None

        return settings.get(indexer_id)

    def list_files(self, release_data: dict[str, Any]) -> list[PackFile] | None:
        """List a cached torrent release's files from its .torrent, without downloading.

        Magnet-only and usenet releases cannot be listed ahead of time.
        """
        source_id = str(release_data.get("source_id") or "")
        prowlarr_result = get_release(source_id) if source_id else None
        if not prowlarr_result or get_protocol(prowlarr_result) != "torrent":
            return None
        download_url = sanitize_download_url(str(prowlarr_result.get("downloadUrl") or "").strip())
        if not download_url or download_url.startswith("magnet:"):
            return None
        expected_hash = str(prowlarr_result.get("infoHash") or "").strip() or None
        info = extract_torrent_info(download_url, expected_hash=expected_hash)
        if not info.torrent_data:
            return None
        return extract_file_list_from_torrent(info.torrent_data)

    def _get_client(self, protocol: str) -> DownloadClient | None:
        """Compatibility shim so module-level patching still works in tests."""
        return get_client(protocol)

    def _list_configured_clients(self) -> list[str]:
        """Compatibility shim so module-level patching still works in tests."""
        return list_configured_clients()

    def _poll_interval(self) -> float:
        return POLL_INTERVAL

    def _completed_path_retry_interval(self) -> float:
        return COMPLETED_PATH_RETRY_INTERVAL

    def _completed_path_max_attempts(self) -> int:
        return COMPLETED_PATH_MAX_ATTEMPTS

    def build_retry_resolution_fields(self, release_data: dict[str, Any]) -> dict[str, Any]:
        source_id = normalize_optional_text(release_data.get("source_id"))
        extra = release_data.get("extra")
        if not isinstance(extra, dict):
            extra = {}

        retry_source_context: dict[str, Any] = {}
        indexer_id = release_data.get("indexer_id") or extra.get("indexer_id")
        if indexer_id is not None:
            retry_source_context["indexer_id"] = indexer_id

        indexer = normalize_optional_text(release_data.get("indexer") or extra.get("indexer"))
        if indexer is not None and indexer.lower() != "unknown":
            retry_source_context["indexer"] = indexer

        info_url = normalize_optional_text(release_data.get("info_url") or extra.get("info_url"))
        if info_url is not None:
            retry_source_context["info_url"] = info_url

        if source_id is not None:
            retry_source_context["source_id"] = source_id

        return {
            "retry_download_url": None,
            "retry_download_protocol": None,
            "retry_source_context": retry_source_context,
        }

    @classmethod
    def _restore_download_request_from_task(cls, task: DownloadTask) -> DownloadRequest | None:
        """Rebuild a DownloadRequest when the in-memory Prowlarr cache is gone."""
        retry_download_url = normalize_optional_text(getattr(task, "retry_download_url", None))
        retry_download_protocol = normalize_optional_text(
            getattr(task, "retry_download_protocol", None)
        )
        if retry_download_url is None or retry_download_protocol is None:
            return None

        protocol = retry_download_protocol.lower()
        if protocol not in {"torrent", "usenet"}:
            return None

        ratio_limit = getattr(task, "retry_ratio_limit", None)
        if not isinstance(ratio_limit, (int, float)) or isinstance(ratio_limit, bool):
            ratio_limit = None

        seeding_time_limit = getattr(task, "retry_seeding_time_limit_minutes", None)
        if not isinstance(seeding_time_limit, int) or isinstance(seeding_time_limit, bool):
            seeding_time_limit = None

        return DownloadRequest(
            url=retry_download_url,
            protocol=protocol,
            release_name=(
                normalize_optional_text(getattr(task, "retry_release_name", None))
                or task.title
                or "Unknown"
            ),
            expected_hash=normalize_optional_text(getattr(task, "retry_expected_hash", None)),
            seeding_time_limit=seeding_time_limit,
            ratio_limit=float(ratio_limit) if ratio_limit is not None else None,
        )

    def _resolve_download(
        self,
        task: DownloadTask,
        status_callback: Callable[[str, str | None], None],
    ) -> DownloadRequest | None:
        """Resolve Prowlarr cache entry into download request parameters."""
        # Look up the cached release
        prowlarr_result = get_release(task.task_id)
        if not prowlarr_result:
            logger.info("Prowlarr release cache miss, refreshing: %s", task.task_id)
            prowlarr_result = self._refresh_release(task)
            if prowlarr_result is None:
                logger.warning("Prowlarr release refresh failed: %s", task.task_id)
                status_callback("error", EXPIRED_LINK_REFRESH_ERROR)
                return None

        # Determine protocol
        protocol = get_protocol(prowlarr_result)
        if protocol == "unknown":
            status_callback("error", "Could not determine download protocol")
            return None

        download_url = get_preferred_download_url(
            prowlarr_result,
            prefer_torrent_file=client_prefers_torrent_file(protocol),
        )
        if not download_url:
            status_callback("error", "No download URL available")
            return None

        release_name = prowlarr_result.get("title") or task.title or "Unknown"
        expected_hash = str(prowlarr_result.get("infoHash") or "").strip() or None

        seeding_time_limit = None
        ratio_limit = None
        if config.get("PROWLARR_USE_SEED_PREFERENCES", False):
            raw_configured_seed_time = prowlarr_result.get("configuredSeedTimeMinutes")
            raw_configured_ratio = prowlarr_result.get("configuredRatioLimit")

            seeding_time_limit = _coerce_positive_minutes(raw_configured_seed_time)
            ratio_limit = float(raw_configured_ratio) if raw_configured_ratio is not None else None

            # Fallback: search-time enrichment can be missing when the indexer
            # settings fetch transiently failed during the search (#795).
            # Re-resolve the limits from Prowlarr at grab time so torrents are
            # never sent to the client without their configured share limits.
            if seeding_time_limit is None and ratio_limit is None and protocol == "torrent":
                fallback = self._fetch_seed_settings_fallback(prowlarr_result.get("indexerId"))
                if fallback:
                    seeding_time_limit = _coerce_positive_minutes(
                        fallback.get("seeding_time_limit_minutes")
                    )
                    raw_ratio = fallback.get("ratio_limit")
                    ratio_limit = float(raw_ratio) if raw_ratio is not None else None

            if seeding_time_limit is None and ratio_limit is None and protocol == "torrent":
                logger.warning(
                    "Prowlarr seed preferences are enabled but no share limits "
                    "could be resolved for release '%s' (indexerId=%s); the "
                    "torrent will use the client's global limits",
                    release_name,
                    prowlarr_result.get("indexerId"),
                )

        return DownloadRequest(
            url=download_url,
            protocol=protocol,
            release_name=release_name,
            expected_hash=expected_hash,
            seeding_time_limit=seeding_time_limit,
            ratio_limit=ratio_limit,
        )

    def _refresh_release(self, task: DownloadTask) -> dict[str, Any] | None:
        """Re-query Prowlarr and cache the exact original release if it still exists."""
        title = normalize_optional_text(task.title)
        if title is None:
            return None

        context = getattr(task, "retry_source_context", None)
        if not isinstance(context, dict):
            context = {}

        indexer = normalize_optional_text(context.get("indexer"))
        book = BookMetadata(
            provider="shelfmark",
            provider_id=task.task_id,
            title=title,
            authors=[task.author] if task.author else [],
            search_title=title,
            search_author=task.author,
        )
        # No language default here on purpose: this re-finds one exact release by its
        # guid, and Prowlarr does not filter on plan.languages anyway.
        plan = build_release_search_plan(
            book,
            indexers=[indexer] if indexer is not None else None,
        )

        source = ProwlarrSource()
        results = source.search(book, plan, content_type=task.content_type or "ebook")
        for release in results:
            raw_release = get_release(release.source_id)
            if raw_release is None:
                continue
            if not self._raw_release_matches_task(raw_release, task.task_id):
                continue

            cache_release(task.task_id, raw_release)
            logger.info("Refreshed Prowlarr release: %s", task.task_id)
            return raw_release

        return None

    @staticmethod
    def _raw_release_matches_task(raw_release: dict[str, Any], task_id: str) -> bool:
        wanted = normalize_optional_text(task_id)
        if wanted is None:
            return False

        bare = [
            identity
            for identity in (
                normalize_optional_text(raw_release.get("guid")),
                normalize_optional_text(raw_release.get("infoUrl")),
            )
            if identity is not None
        ]

        identities = [*bare, build_source_id(raw_release)]
        indexer_id = coerce_int_like(raw_release.get("indexerId"))
        if indexer_id is not None:
            identities.extend(f"{indexer_id}:{identity}" for identity in bare)

        return wanted in identities

    def _refresh_download_request_after_add_failure(
        self,
        *,
        task: DownloadTask,
        request: DownloadRequest,
        error: Exception,
        status_callback: Callable[[str, str | None], None],
    ) -> DownloadRequest | None:
        """Refresh once when a cached Prowlarr torrent proxy URL has expired."""
        if request.protocol != "torrent":
            return None
        if HASH_DETECTION_ERROR not in str(error):
            return None

        parsed = urlparse(request.url)
        if parsed.scheme.lower() not in {"http", "https"}:
            return None

        logger.info("Refreshing stale Prowlarr torrent URL for %s", task.task_id)
        remove_release(task.task_id)
        refreshed_request = self._resolve_download(task, status_callback)
        if refreshed_request is None:
            raise RuntimeError(EXPIRED_LINK_REFRESH_ERROR) from error
        return refreshed_request

    def _on_download_complete(self, task: DownloadTask) -> None:
        """Remove completed release from the Prowlarr cache."""
        remove_release(task.task_id)

    def cancel(self, task_id: str) -> bool:
        """Cancel download and clean up cache. Primary cancellation is via cancel_flag."""
        logger.debug("Cancel requested for Prowlarr task: %s", task_id)
        remove_release(task_id)
        return super().cancel(task_id)
