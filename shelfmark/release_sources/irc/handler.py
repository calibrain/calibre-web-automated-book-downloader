"""IRC DCC download handler.

Handles downloading IRC releases via DCC protocol.
"""

from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING

from shelfmark.core.config import config
from shelfmark.core.logger import setup_logger
from shelfmark.release_sources import DownloadHandler, register_handler

from .client import has_control_characters
from .connection_manager import connection_manager
from .dcc import DCCError, download_dcc, safe_dcc_filename

if TYPE_CHECKING:
    from collections.abc import Callable
    from threading import Event

    from shelfmark.core.models import DownloadTask

logger = setup_logger(__name__)


def _server_from_download_request(download_request: str) -> str | None:
    """Extract the expected IRC bot nick from a release request line."""
    stripped = download_request.strip()
    if not stripped.startswith("!"):
        return None
    server = stripped[1:].split(maxsplit=1)[0]
    return server or None


def _config_text(key: str) -> str:
    """Read a string config value with whitespace trimmed."""
    value = config.get(key, "")
    if value is None:
        return ""
    return str(value).strip()


def _config_port(key: str, default: int) -> int:
    """Read an IRC port value from config, accepting ints and numeric strings."""
    value = config.get(key, default)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if stripped:
            with suppress(ValueError):
                return int(stripped)
    return default


def _config_bool(key: str, default: bool) -> bool:
    """Read a boolean config value from config."""
    value = config.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


@register_handler("irc")
class IRCDownloadHandler(DownloadHandler):
    """Handle IRC DCC downloads."""

    def download(
        self,
        task: DownloadTask,
        cancel_flag: Event,
        progress_callback: Callable[[float], None],
        status_callback: Callable[[str, str | None], None],
    ) -> str | None:
        """Download a release via IRC DCC. task.task_id contains the IRC request string."""
        download_request = task.task_id
        logger.info("IRC download: %s...", download_request[:60])
        if has_control_characters(download_request):
            logger.warning("IRC download refused: request line contains control characters")
            status_callback("error", "Invalid IRC request: contains control characters")
            return None
        expected_server = _server_from_download_request(download_request)

        # Get IRC settings
        server = _config_text("IRC_SERVER")
        port = _config_port("IRC_PORT", 6697)
        use_tls = _config_bool("IRC_USE_TLS", True)
        channel = _config_text("IRC_CHANNEL")
        nick = _config_text("IRC_NICK")

        if not server or not channel or not nick:
            logger.warning("IRC not fully configured")
            status_callback("failed", "IRC not configured")
            return None

        client = None

        def check_cancelled() -> bool:
            """Check if cancelled and handle cleanup."""
            if not cancel_flag.is_set():
                return False
            if client:
                connection_manager.close_connection(client)
            status_callback("cancelled", "Cancelled")
            return True

        try:
            # Phase 1: Get or reuse IRC connection
            status_callback("resolving", f"Connecting to {server}")

            if check_cancelled():
                return None

            client = connection_manager.get_connection(
                server=server,
                port=port,
                nick=nick,
                use_tls=use_tls,
                channel=channel,
            )

            # Phase 2: Send download request
            status_callback("resolving", "Requesting file from bot")

            if check_cancelled():
                return None

            # Send the full request line to the channel
            client.send_message(f"#{channel}", download_request)

            # Phase 3: Wait for DCC offer
            status_callback("resolving", "Waiting for bot response")

            wait_kwargs = {"expected_senders": {expected_server}} if expected_server else {}
            offer = client.wait_for_dcc(timeout=120.0, result_type=False, **wait_kwargs)

            if not offer:
                status_callback("error", "No response from bot")
                connection_manager.release_connection(client)
                return None

            if check_cancelled():
                return None

            # Phase 4: Download via DCC
            status_callback("downloading", "")

            # Get file extension from offer filename
            ext = (
                Path(safe_dcc_filename(offer.filename)).suffix.lstrip(".") or task.format or "epub"
            )

            # Stage to temp directory (lazy import to avoid circular import)
            from shelfmark.download.staging import get_staging_path

            staging_path = get_staging_path(task.task_id, ext)

            download_dcc(
                offer=offer,
                dest_path=staging_path,
                progress_callback=progress_callback,
                cancel_flag=cancel_flag,
                timeout=60.0,
            )

            # Release connection for reuse (don't close it)
            connection_manager.release_connection(client)

            if cancel_flag.is_set():
                # Clean up partial download
                staging_path.unlink(missing_ok=True)
                status_callback("cancelled", "Cancelled")
                return None

            logger.info("Download complete: %s", staging_path)
            return str(staging_path)

        except DCCError as e:
            logger.exception("DCC error")
            status_callback("error", str(e))
            if client:
                connection_manager.close_connection(client)
            return None

        except Exception as e:
            logger.exception("Download failed")
            status_callback("error", f"Download failed: {e}")
            if client:
                connection_manager.close_connection(client)
            return None

    def cancel(self, task_id: str) -> bool:
        """Cancel an in-progress download (cleanup if cancel_flag fails)."""
        logger.debug("Cancel requested for IRC task: %s", task_id)
        return True
