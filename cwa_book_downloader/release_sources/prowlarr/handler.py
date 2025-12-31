"""
Prowlarr download handler.

Handles downloads from Prowlarr via external download clients.
Supported torrent clients: qBittorrent, Transmission, Deluge.
Supported usenet clients: NZBGet, SABnzbd.
"""

import shutil
from pathlib import Path
from threading import Event
from typing import Callable, Optional

from cwa_book_downloader.core.config import config
from cwa_book_downloader.core.logger import setup_logger
from cwa_book_downloader.core.models import DownloadTask
from cwa_book_downloader.release_sources import DownloadHandler, register_handler
from cwa_book_downloader.release_sources.prowlarr.cache import get_release, remove_release
from cwa_book_downloader.release_sources.prowlarr.clients import (
    DownloadState,
    get_client,
    list_configured_clients,
)
from cwa_book_downloader.release_sources.prowlarr.utils import get_protocol, get_unique_path

logger = setup_logger(__name__)

# How often to poll the download client for status (seconds)
POLL_INTERVAL = 2


@register_handler("prowlarr")
class ProwlarrHandler(DownloadHandler):
    """Handler for Prowlarr downloads via configured torrent or usenet client."""

    def download(
        self,
        task: DownloadTask,
        cancel_flag: Event,
        progress_callback: Callable[[float], None],
        status_callback: Callable[[str, Optional[str]], None],
    ) -> Optional[str]:
        """
        Execute a Prowlarr download.

        Args:
            task: Download task with task_id (Prowlarr source_id/GUID)
            cancel_flag: Event to check for cancellation
            progress_callback: Called with progress percentage (0-100)
            status_callback: Called with (status, message) for status updates

        Returns:
            Path to downloaded file if successful, None otherwise
        """
        try:
            # Look up the cached release
            prowlarr_result = get_release(task.task_id)
            if not prowlarr_result:
                status_callback("error", "Release not found in cache (may have expired)")
                return None

            # Extract download URL
            download_url = prowlarr_result.get("downloadUrl") or prowlarr_result.get("magnetUrl")
            if not download_url:
                status_callback("error", "No download URL available")
                return None

            # Determine protocol
            protocol = get_protocol(prowlarr_result)
            if protocol == "unknown":
                status_callback("error", "Could not determine download protocol")
                return None

            # Get the appropriate download client
            client = get_client(protocol)
            if not client:
                configured = list_configured_clients()
                if not configured:
                    status_callback("error", "No download clients configured. Configure qBittorrent or NZBGet in settings.")
                else:
                    status_callback("error", f"No {protocol} client configured")
                return None

            # Check if this download already exists in the client
            status_callback("resolving", f"Checking {client.name}")
            existing = client.find_existing(download_url)

            if existing:
                download_id, existing_status = existing
                logger.info(f"Found existing download in {client.name}: {download_id}")

                # If already complete, skip straight to file handling
                if existing_status.complete:
                    logger.info(f"Existing download is complete, copying file directly")
                    status_callback("resolving", "Found existing download, copying to library")

                    source_path = client.get_download_path(download_id)
                    if not source_path:
                        status_callback("error", "Could not locate existing download file")
                        return None

                    result = self._handle_completed_file(
                        source_path=Path(source_path),
                        protocol=protocol,
                        task=task,
                        status_callback=status_callback,
                    )

                    if result:
                        remove_release(task.task_id)
                    return result

                # Existing but still downloading - join the progress polling
                logger.info(f"Existing download in progress, joining poll loop")
                status_callback("downloading", "Resuming existing download")
            else:
                # No existing download - add new
                status_callback("resolving", f"Sending to {client.name}")
                try:
                    release_name = prowlarr_result.get("title") or task.title or "Unknown"
                    download_id = client.add_download(
                        url=download_url,
                        name=release_name,
                    )
                except Exception as e:
                    logger.error(f"Failed to add to {client.name}: {e}")
                    status_callback("error", f"Failed to add to {client.name}: {e}")
                    return None

                logger.info(f"Added to {client.name}: {download_id} for '{release_name}'")

            # Poll for progress
            return self._poll_and_complete(
                client=client,
                download_id=download_id,
                protocol=protocol,
                task=task,
                cancel_flag=cancel_flag,
                progress_callback=progress_callback,
                status_callback=status_callback,
            )

        except Exception as e:
            logger.error(f"Prowlarr download error: {e}")
            status_callback("error", str(e))
            return None

    def _poll_and_complete(
        self,
        client,
        download_id: str,
        protocol: str,
        task: DownloadTask,
        cancel_flag: Event,
        progress_callback: Callable[[float], None],
        status_callback: Callable[[str, Optional[str]], None],
    ) -> Optional[str]:
        """Poll the download client for progress and handle completion."""
        try:
            while not cancel_flag.is_set():
                status = client.get_status(download_id)
                progress_callback(status.progress)

                # Check for completion
                if status.complete:
                    if status.state == DownloadState.ERROR:
                        status_callback("error", status.message or "Download failed")
                        return None
                    # Download complete - break to handle file
                    break

                # Check for error state
                if status.state == DownloadState.ERROR:
                    status_callback("error", status.message or "Download failed")
                    client.remove(download_id, delete_files=True)
                    return None

                # Build status message
                # If client provided a specific message (e.g., "Stalled", "Fetching metadata"),
                # use that. Otherwise, build a progress message.
                if status.message:
                    msg = status.message
                else:
                    msg = f"{status.progress:.0f}%"
                    if status.download_speed and status.download_speed > 0:
                        speed_mb = status.download_speed / 1024 / 1024
                        msg += f" ({speed_mb:.1f} MB/s)"
                    if status.eta and status.eta > 0:
                        if status.eta < 60:
                            msg += f" - {status.eta}s left"
                        elif status.eta < 3600:
                            msg += f" - {status.eta // 60}m left"
                        else:
                            msg += f" - {status.eta // 3600}h {(status.eta % 3600) // 60}m left"

                status_callback("downloading", msg)

                # Wait for next poll (interruptible by cancel)
                if cancel_flag.wait(timeout=POLL_INTERVAL):
                    break

            # Handle cancellation
            if cancel_flag.is_set():
                logger.info(f"Download cancelled, removing from {client.name}: {download_id}")
                client.remove(download_id, delete_files=True)
                status_callback("cancelled", "Cancelled")
                return None

            # Handle completed file
            source_path = client.get_download_path(download_id)
            if not source_path:
                status_callback("error", "Could not locate downloaded file")
                return None

            result = self._handle_completed_file(
                source_path=Path(source_path),
                protocol=protocol,
                task=task,
                status_callback=status_callback,
            )

            # Clean up cache on success
            if result:
                remove_release(task.task_id)

            return result

        except Exception as e:
            logger.error(f"Error during download polling: {e}")
            status_callback("error", str(e))
            try:
                client.remove(download_id, delete_files=True)
            except Exception as cleanup_error:
                logger.error(f"Failed to cleanup download {download_id} after error: {cleanup_error}")
            return None

    def _handle_completed_file(
        self,
        source_path: Path,
        protocol: str,
        task: DownloadTask,
        status_callback: Callable[[str, Optional[str]], None],
    ) -> Optional[str]:
        """Stage completed download for orchestrator post-processing.

        For directories (multi-file torrents), copies the entire directory.
        The orchestrator will find and filter book files.
        """
        try:
            status_callback("resolving", "Staging file")

            # For torrents, store original download client path for library mode hardlinking
            # This is where the file lives for seeding - hardlinks must point to this location
            if protocol == "torrent":
                task.original_download_path = str(source_path)

            # Torrents: copy to preserve seeding. Usenet: configurable.
            if protocol == "torrent":
                use_copy = True
            else:
                use_copy = config.get("PROWLARR_USENET_ACTION", "move") == "copy"

            from cwa_book_downloader.download.orchestrator import get_staging_dir
            staging_dir = get_staging_dir()

            if source_path.is_dir():
                # Multi-file download: stage entire directory
                # Orchestrator will extract book files
                staged_path = get_unique_path(staging_dir, source_path.name)

                if use_copy:
                    shutil.copytree(str(source_path), str(staged_path))
                else:
                    shutil.move(str(source_path), str(staged_path))
                logger.debug(f"Staged directory: {staged_path.name}")
            else:
                # Single file download
                staged_path = get_unique_path(staging_dir, source_path.stem, source_path.suffix)

                if use_copy:
                    shutil.copy2(str(source_path), str(staged_path))
                else:
                    shutil.move(str(source_path), str(staged_path))
                logger.debug(f"Staged: {staged_path.name}")

            return str(staged_path)

        except PermissionError as e:
            logger.error(f"Permission denied staging file: {e}")
            status_callback("error", f"Permission denied: {e}")
            return None
        except Exception as e:
            logger.error(f"Staging failed: {e}")
            status_callback("error", f"Failed to stage file: {e}")
            return None

    def cancel(self, task_id: str) -> bool:
        """
        Cancel an in-progress download.

        Note: Actual cancellation is handled via the cancel_flag in download().
        This method is for cleanup if the cancel_flag mechanism fails.
        """
        logger.debug(f"Cancel requested for Prowlarr task: {task_id}")
        # Remove from cache if present
        remove_release(task_id)
        return True
