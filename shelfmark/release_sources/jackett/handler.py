"""Jackett download handler - resolves releases and delegates lifecycle to shared clients."""

from typing import TYPE_CHECKING, Any

from shelfmark.core.logger import setup_logger
from shelfmark.core.request_helpers import normalize_optional_text
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
from shelfmark.release_sources import register_handler
from shelfmark.release_sources.prowlarr.cache import get_release, remove_release
from shelfmark.release_sources.prowlarr.handler import (
    list_cached_release_files,
    restore_download_request_from_task,
)
from shelfmark.release_sources.prowlarr.utils import (
    get_preferred_download_url,
    get_protocol,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from shelfmark.core.models import DownloadTask
    from shelfmark.download.postprocess.packs import PackFile

logger = setup_logger(__name__)

# Backwards-compat constants for tests patching this module.
POLL_INTERVAL = _DEFAULT_POLL_INTERVAL
COMPLETED_PATH_RETRY_INTERVAL = _DEFAULT_COMPLETED_PATH_RETRY_INTERVAL
COMPLETED_PATH_MAX_ATTEMPTS = _DEFAULT_COMPLETED_PATH_MAX_ATTEMPTS


@register_handler("jackett")
class JackettHandler(ExternalClientHandler):
    """Handler for Jackett downloads via the configured torrent client.

    Jackett results share Prowlarr's raw result shape and release cache (source ids
    are namespaced per source), so protocol and URL selection are reused as-is.
    """

    def list_files(self, release_data: dict[str, Any]) -> list[PackFile] | None:
        return list_cached_release_files(release_data)

    @classmethod
    def _restore_download_request_from_task(cls, task: DownloadTask) -> DownloadRequest | None:
        return restore_download_request_from_task(task)

    def _get_client(self, protocol: str) -> DownloadClient | None:
        return get_client(protocol)

    def _list_configured_clients(self) -> list[str]:
        return list_configured_clients()

    def _poll_interval(self) -> float:
        return POLL_INTERVAL

    def _completed_path_retry_interval(self) -> float:
        return COMPLETED_PATH_RETRY_INTERVAL

    def _completed_path_max_attempts(self) -> int:
        return COMPLETED_PATH_MAX_ATTEMPTS

    def build_retry_resolution_fields(self, release_data: dict) -> dict:
        """Snapshot the download URL so a retry survives the release cache expiring.

        Jackett download links carry their own API key and do not expire.
        """
        source_id = normalize_optional_text(release_data.get("source_id"))
        result = get_release(source_id) if source_id else None
        if result is None:
            return {}

        protocol = get_protocol(result)
        return {
            "retry_download_url": normalize_optional_text(
                get_preferred_download_url(
                    result,
                    prefer_torrent_file=client_prefers_torrent_file(protocol),
                )
            ),
            "retry_download_protocol": normalize_optional_text(protocol),
        }

    def _resolve_download(
        self,
        task: DownloadTask,
        status_callback: Callable[[str, str | None], None],
    ) -> DownloadRequest | None:
        result = get_release(task.task_id)
        if not result:
            restored_request = self._restore_download_request_from_task(task)
            if restored_request is not None:
                logger.info("Restored Jackett download request for retry: %s", task.task_id)
                return restored_request
            logger.warning("Jackett release cache miss: %s", task.task_id)
            status_callback("error", "Release not found in cache (may have expired)")
            return None

        protocol = get_protocol(result)
        if protocol == "unknown":
            status_callback("error", "Could not determine download protocol")
            return None

        download_url = get_preferred_download_url(
            result,
            prefer_torrent_file=client_prefers_torrent_file(protocol),
        )
        if not download_url:
            status_callback("error", "No download URL available")
            return None

        return DownloadRequest(
            url=download_url,
            protocol=protocol,
            release_name=result.get("title") or task.title or "Unknown",
            expected_hash=str(result.get("infoHash") or "").strip() or None,
        )

    def _on_download_complete(self, task: DownloadTask) -> None:
        remove_release(task.task_id)

    def cancel(self, task_id: str) -> bool:
        logger.debug("Cancel requested for Jackett task: %s", task_id)
        remove_release(task_id)
        return super().cancel(task_id)
