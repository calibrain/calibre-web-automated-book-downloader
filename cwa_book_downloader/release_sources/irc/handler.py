"""IRC DCC download handler.

Handles downloading books via IRC DCC protocol.
"""

from pathlib import Path
from threading import Event
from typing import Callable, Optional

from cwa_book_downloader.core.config import config
from cwa_book_downloader.core.logger import setup_logger
from cwa_book_downloader.core.models import DownloadTask
from cwa_book_downloader.release_sources import DownloadHandler, register_handler

from .client import DEFAULT_CHANNEL, IRCClient
from .dcc import DCCError, download_dcc

logger = setup_logger(__name__)


@register_handler("irc")
class IRCDownloadHandler(DownloadHandler):
    """Handle IRC DCC downloads."""

    def download(
        self,
        task: DownloadTask,
        cancel_flag: Event,
        progress_callback: Callable[[float], None],
        status_callback: Callable[[str, Optional[str]], None],
    ) -> Optional[str]:
        """Download a book via IRC DCC.

        The task.task_id contains the full IRC request string,
        e.g., "!ServerName Author - Title.epub ::INFO:: 2.5MB"

        Args:
            task: Download task with IRC request info
            cancel_flag: Set to cancel download
            progress_callback: Report progress 0-100
            status_callback: Report status messages

        Returns:
            Path to downloaded file, or None on failure
        """
        download_request = task.task_id
        logger.info(f"IRC download: {download_request[:60]}...")

        nick = config.get("IRC_NICK") or None
        client = None

        def check_cancelled() -> bool:
            """Check if cancelled and handle cleanup."""
            if not cancel_flag.is_set():
                return False
            if client:
                client.disconnect()
            status_callback("cancelled", "Cancelled")
            return True

        try:
            # Phase 1: Connect to IRC
            status_callback("resolving", "Connecting to IRC")

            if check_cancelled():
                return None

            client = IRCClient(nick=nick)
            client.connect()
            client.join_channel(DEFAULT_CHANNEL)

            # Phase 2: Send download request
            status_callback("resolving", "Requesting file from bot")

            if check_cancelled():
                return None

            # Send the full request line to the channel
            client.send_message(f"#{DEFAULT_CHANNEL}", download_request)

            # Phase 3: Wait for DCC offer
            status_callback("resolving", "Waiting for bot response")

            offer = client.wait_for_dcc(timeout=120.0, result_type=False)

            if not offer:
                status_callback("error", "No response from bot")
                client.disconnect()
                return None

            if check_cancelled():
                return None

            # Phase 4: Download via DCC
            status_callback("downloading", "")

            # Get file extension from offer filename
            ext = Path(offer.filename).suffix.lstrip('.') or task.format or "epub"

            # Stage to temp directory (lazy import to avoid circular import)
            from cwa_book_downloader.download.orchestrator import get_staging_path
            staging_path = get_staging_path(task.task_id, ext)

            download_dcc(
                offer=offer,
                dest_path=staging_path,
                progress_callback=progress_callback,
                cancel_flag=cancel_flag,
                timeout=60.0,
            )

            client.disconnect()

            if cancel_flag.is_set():
                # Clean up partial download
                staging_path.unlink(missing_ok=True)
                status_callback("cancelled", "Cancelled")
                return None

            logger.info(f"Download complete: {staging_path}")
            return str(staging_path)

        except DCCError as e:
            logger.error(f"DCC error: {e}")
            status_callback("error", str(e))
            if client:
                client.disconnect()
            return None

        except Exception as e:
            logger.error(f"Download failed: {e}")
            status_callback("error", f"Download failed: {e}")
            if client:
                client.disconnect()
            return None

    def cancel(self, task_id: str) -> bool:
        """Cancel an in-progress download.

        Note: Actual cancellation is handled via the cancel_flag in download().
        This method is for cleanup if the cancel_flag mechanism fails.
        """
        logger.debug(f"Cancel requested for IRC task: {task_id}")
        return True
