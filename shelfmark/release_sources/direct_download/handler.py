"""Direct Download routing, staging, and cancellation."""

from pathlib import Path
from typing import TYPE_CHECKING

from shelfmark.config.env import TMP_DIR
from shelfmark.core.config import config
from shelfmark.core.logger import setup_logger
from shelfmark.core.models import DownloadTask, build_filename
from shelfmark.download import network
from shelfmark.release_sources import (
    BrowseRecord,
    DownloadHandler,
    register_handler,
)
from shelfmark.release_sources.direct_download import registry

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path
    from threading import Event


logger = setup_logger(__name__)


def _download_book(
    book_info: BrowseRecord,
    book_path: Path,
    progress_callback: Callable[[float], None] | None = None,
    cancel_flag: Event | None = None,
    status_callback: Callable[[str, str | None], None] | None = None,
) -> str | None:
    """Route a website record or an Anna's Archive MD5 to its download flow."""
    provider = registry.provider_for_record(book_info)
    if provider is None:
        msg = f"No Direct Download provider owns record {book_info.id!r}"
        raise RuntimeError(msg)
    return provider.download(book_info, book_path, progress_callback, cancel_flag, status_callback)


@register_handler("direct_download")
class DirectDownloadHandler(DownloadHandler):
    """Route and stage downloads from registered Direct Download providers."""

    def download(
        self,
        task: DownloadTask,
        cancel_flag: Event,
        progress_callback: Callable[[float], None],
        status_callback: Callable[[str, str | None], None],
    ) -> str | None:
        """Execute a provider-owned direct HTTP download.

        Args:
            task: Download task with a provider-owned source ID
            cancel_flag: Event to check for cancellation
            progress_callback: Called with progress percentage (0-100)
            status_callback: Called with (status, message) for status updates

        Returns:
            Path to downloaded file if successful, None otherwise

        """
        try:
            # Check for cancellation before starting
            if cancel_flag.is_set():
                logger.info("Download cancelled before starting: %s", task.task_id)
                status_callback("cancelled", "Cancelled")
                return None

            # Reconstruct the provider-owned record without resolving it again.
            book_info = BrowseRecord(
                id=task.task_id,
                title=task.title,
                source="direct_download",
                author=task.author,
                year=task.year,
                format=task.format,
                size=task.size,
                preview=task.preview,
                source_url=task.source_url,
            )

            return self._execute_download(
                book_info, cancel_flag, progress_callback, status_callback
            )

        except Exception as e:
            if cancel_flag.is_set():
                logger.info("Download cancelled during error handling: %s", task.task_id)
                status_callback("cancelled", "Cancelled")
            else:
                logger.exception("Error downloading book")
                status_callback("error", str(e))
            return None

    def _execute_download(
        self,
        book_info: BrowseRecord,
        cancel_flag: Event,
        progress_callback: Callable[[float], None],
        status_callback: Callable[[str, str | None], None],
    ) -> str | None:
        """Execute the direct-download flow with a fetched browse record.

        This contains the core download logic: cascade through sources,
        handle bypass, move to final location.
        """
        try:
            logger.debug("Starting download: %s", book_info.title)

            # Prepare paths - use descriptive staging filename, orchestrator will rename
            # based on FILE_ORGANIZATION setting
            file_org = config.get("FILE_ORGANIZATION", "rename")
            if file_org == "none":
                book_name = f"{book_info.id}.{book_info.format or 'bin'}"
            else:
                book_name = build_filename(
                    book_info.title,
                    book_info.author,
                    book_info.year,
                    book_info.format,
                )
            book_path = TMP_DIR / book_name

            # Check cancellation before download
            if cancel_flag.is_set():
                logger.info("Download cancelled before download call: %s", book_info.id)
                status_callback("cancelled", "Cancelled")
                return None

            # Execute download via _download_book (handles cascade and bypass)
            status_callback("resolving", "Finding download source")
            success_url = _download_book(
                book_info, book_path, progress_callback, cancel_flag, status_callback
            )

            # Check for cancellation after download
            if cancel_flag.is_set():
                logger.info("Download cancelled during download: %s", book_info.id)
                if book_path.exists():
                    book_path.unlink()
                status_callback("cancelled", "Cancelled")
                return None

            if not success_url:
                if network.dns_interference_detected():
                    status_callback(
                        "error",
                        "All sources failed - your network/ISP appears to be blocking "
                        "Anna's Archive. Enable DNS-over-HTTPS in settings.",
                    )
                else:
                    status_callback("error", "All download sources failed")
                return None

            # Return temp path - orchestrator handles post-processing (archive extraction, ingest)
            return str(book_path)

        except Exception:
            if cancel_flag.is_set():
                logger.info("Download cancelled during error handling: %s", book_info.id)
                status_callback("cancelled", "Cancelled")
            else:
                logger.exception("Error downloading book")
            return None

    def cancel(self, task_id: str) -> bool:
        """Cancel an in-progress download.

        Cancellation is handled via the cancel_flag passed to download().
        This method exists for the interface but actual cancellation
        happens through the Event flag mechanism.
        """
        # Cancellation is handled by the orchestrator via cancel_flag
        return False
