"""Newznab download handler - resolves releases and delegates to shared clients."""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from shelfmark.core.models import DownloadTask

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
from shelfmark.release_sources.newznab.cache import get_release, remove_release

logger = setup_logger(__name__)

# Backwards-compat constants for tests patching this module.
POLL_INTERVAL = _DEFAULT_POLL_INTERVAL
COMPLETED_PATH_RETRY_INTERVAL = _DEFAULT_COMPLETED_PATH_RETRY_INTERVAL
COMPLETED_PATH_MAX_ATTEMPTS = _DEFAULT_COMPLETED_PATH_MAX_ATTEMPTS


def _get_protocol(result: dict) -> str:
    """Infer download protocol from a Newznab result dict."""
    protocol = str(result.get("protocol", "")).lower()
    if protocol in ("torrent", "usenet"):
        return protocol

    download_url = str(result.get("downloadUrl") or "").lower()
    magnet_url = str(result.get("magnetUrl") or "").lower()

    if magnet_url.startswith("magnet:"):
        return "torrent"
    if download_url.startswith("magnet:") or ".torrent" in download_url:
        return "torrent"
    if ".nzb" in download_url:
        return "usenet"

    # Newznab indexers are usenet-native; default to usenet when ambiguous.
    return "usenet"


def _get_download_url(result: dict, *, prefer_torrent_file: bool = False) -> str:
    """Pick the best URL to hand to a download client."""
    protocol = _get_protocol(result)
    magnet_url = str(result.get("magnetUrl") or "").strip()
    download_url = str(result.get("downloadUrl") or "").strip()

    if protocol == "torrent":
        if prefer_torrent_file:
            return download_url or magnet_url
        return magnet_url or download_url
    return download_url or magnet_url


@register_handler("newznab")
class NewznabHandler(ExternalClientHandler):
    """Handler for Newznab downloads via configured usenet/torrent client."""

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
        source_id = normalize_optional_text(release_data.get("source_id"))
        if source_id is None:
            return {}

        result = get_release(source_id)
        if result is None:
            return {}

        protocol = _get_protocol(result)
        return {
            "retry_download_url": normalize_optional_text(
                _get_download_url(
                    result,
                    prefer_torrent_file=client_prefers_torrent_file(protocol),
                )
            ),
            "retry_download_protocol": normalize_optional_text(protocol),
        }

    @classmethod
    def _restore_download_request_from_task(cls, task: DownloadTask) -> DownloadRequest | None:
        retry_download_url = normalize_optional_text(getattr(task, "retry_download_url", None))
        retry_download_protocol = normalize_optional_text(
            getattr(task, "retry_download_protocol", None)
        )
        if retry_download_url is None or retry_download_protocol is None:
            return None

        protocol = retry_download_protocol.lower()
        if protocol not in {"torrent", "usenet"}:
            return None

        return DownloadRequest(
            url=retry_download_url,
            protocol=protocol,
            release_name=(
                normalize_optional_text(getattr(task, "retry_release_name", None))
                or task.title
                or "Unknown"
            ),
            expected_hash=normalize_optional_text(getattr(task, "retry_expected_hash", None)),
        )

    def _resolve_download(
        self,
        task: DownloadTask,
        status_callback: Callable[[str, str | None], None],
    ) -> DownloadRequest | None:
        result = get_release(task.task_id)
        if not result:
            restored_request = self._restore_download_request_from_task(task)
            if restored_request is not None:
                logger.info("Restored Newznab download request for retry: %s", task.task_id)
                return restored_request
            logger.warning("Newznab release cache miss: %s", task.task_id)
            status_callback("error", "Release not found in cache (may have expired)")
            return None

        protocol = _get_protocol(result)
        if protocol not in ("torrent", "usenet"):
            status_callback("error", "Could not determine download protocol")
            return None

        download_url = _get_download_url(
            result,
            prefer_torrent_file=client_prefers_torrent_file(protocol),
        )
        if not download_url:
            status_callback("error", "No download URL available")
            return None

        release_name = result.get("title") or task.title or "Unknown"
        expected_hash = str(result.get("infoHash") or "").strip() or None

        return DownloadRequest(
            url=download_url,
            protocol=protocol,
            release_name=release_name,
            expected_hash=expected_hash,
        )

    def _on_download_complete(self, task: DownloadTask) -> None:
        remove_release(task.task_id)

    def cancel(self, task_id: str) -> bool:
        logger.debug("Cancel requested for Newznab task: %s", task_id)
        remove_release(task_id)
        return super().cancel(task_id)
