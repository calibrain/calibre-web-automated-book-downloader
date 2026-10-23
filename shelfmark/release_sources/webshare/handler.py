"""Webshare.cz download handler - resolves a file ident to a link and streams it.

Selected by ``get_handler(task.source)`` for ``source == "webshare"``. Download links
are time-limited and tied to the session, so one is resolved per attempt rather than at
search time. Audiobook archives can run to gigabytes, so the body is streamed straight
into TMP_DIR instead of buffered in memory like the Libgen handler does.
"""

from pathlib import PurePosixPath
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlsplit

import requests

from shelfmark.config.env import TMP_DIR
from shelfmark.core.config import config
from shelfmark.core.logger import setup_logger
from shelfmark.core.models import build_filename
from shelfmark.download import http as downloader
from shelfmark.download import network
from shelfmark.release_sources import DownloadHandler, register_handler
from shelfmark.release_sources.webshare.api import WebshareClient, WebshareError
from shelfmark.release_sources.webshare.source import get_credentials

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path
    from threading import Event

    from shelfmark.core.models import DownloadTask

logger = setup_logger(__name__)

# Files under this size are almost certainly an error page, not a book.
_MIN_VALID_FILE_SIZE = 10 * 1024
_CHUNK_SIZE = 256 * 1024
_DOWNLOAD_TIMEOUT = (10, 60)


def _target_path(task: DownloadTask, ident: str, link: str) -> Path:
    """Staging path: the naming template, or Webshare's own file name for "none"."""
    if config.get("FILE_ORGANIZATION", "rename") != "none":
        return TMP_DIR / build_filename(task.title, task.author, task.year, task.format)
    link_name = PurePosixPath(unquote(urlsplit(link).path)).name
    return TMP_DIR / (link_name or f"{ident}.{task.format or 'bin'}")


@register_handler("webshare")
class WebshareHandler(DownloadHandler):
    """Download handler for Webshare.cz releases."""

    def download(
        self,
        task: DownloadTask,
        cancel_flag: Event,
        progress_callback: Callable[[float], None],
        status_callback: Callable[[str, str | None], None],
    ) -> str | None:
        """Resolve a download link for the file and stream it into TMP_DIR."""
        ident = task.task_id.split(":", 1)[-1]
        book_path: Path | None = None
        try:
            if cancel_flag.is_set():
                status_callback("cancelled", "Cancelled")
                return None

            status_callback("resolving", "Resolving Webshare link")
            link = WebshareClient(*get_credentials()).file_link(ident)

            book_path = _target_path(task, ident, link)
            status_callback("downloading", None)
            if not self._stream(link, book_path, cancel_flag, progress_callback):
                book_path.unlink(missing_ok=True)
                status_callback("cancelled", "Cancelled")
                return None

            if book_path.stat().st_size < _MIN_VALID_FILE_SIZE:
                book_path.unlink(missing_ok=True)
                status_callback("error", "Webshare returned an invalid file")
                return None
            return str(book_path)
        except (WebshareError, requests.exceptions.RequestException, OSError) as exc:
            if book_path is not None:
                book_path.unlink(missing_ok=True)
            if cancel_flag.is_set():
                status_callback("cancelled", "Cancelled")
            else:
                logger.warning("Webshare download failed for %s: %s", ident, exc)
                status_callback("error", str(exc))
            return None

    @staticmethod
    def _stream(
        link: str,
        book_path: Path,
        cancel_flag: Event,
        progress_callback: Callable[[float], None],
    ) -> bool:
        """Stream ``link`` into ``book_path``; return False if cancelled midway."""
        with requests.get(
            link,
            headers=downloader.DOWNLOAD_HEADERS,
            stream=True,
            timeout=_DOWNLOAD_TIMEOUT,
            proxies=network.get_proxies(link),
            verify=network.get_ssl_verify(link),
        ) as response:
            response.raise_for_status()
            total = int(response.headers.get("Content-Length") or 0)
            written = 0
            with book_path.open("wb") as file:
                for chunk in response.iter_content(chunk_size=_CHUNK_SIZE):
                    if cancel_flag.is_set():
                        return False
                    file.write(chunk)
                    written += len(chunk)
                    if total:
                        progress_callback(min(100.0, written * 100 / total))
        progress_callback(100.0)
        return True

    def cancel(self, task_id: str) -> bool:
        """Cancellation is handled by the orchestrator via the cancel_flag."""
        return False
