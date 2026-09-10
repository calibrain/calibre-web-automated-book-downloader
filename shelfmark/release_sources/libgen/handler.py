"""Libgen download handler - resolves an md5 to a file via the ads.php cascade.

Selected by ``get_handler(task.source)`` for ``source == "libgen"``. It mirrors
DirectDownloadHandler's shape (stage into TMP_DIR, let the orchestrator post-process) but
only knows the libgen ``ads.php?md5= -> get.php`` path, keyed on the md5 the search source
put in ``source_id``.
"""

from typing import TYPE_CHECKING

from shelfmark.config.env import TMP_DIR
from shelfmark.core.config import config
from shelfmark.core.logger import setup_logger
from shelfmark.core.models import build_filename
from shelfmark.download import http as downloader
from shelfmark.release_sources import DownloadHandler, register_handler
from shelfmark.release_sources.libgen import scraper

if TYPE_CHECKING:
    from collections.abc import Callable
    from threading import Event

    from shelfmark.core.models import DownloadTask

logger = setup_logger(__name__)

# Files under this size are almost certainly an error/challenge page, not a book. Same
# threshold direct_download uses; duplicated to keep the package self-contained.
_MIN_VALID_FILE_SIZE = 10 * 1024


@register_handler("libgen")
class LibgenHandler(DownloadHandler):
    """Download handler for Libgen search releases."""

    def download(
        self,
        task: DownloadTask,
        cancel_flag: Event,
        progress_callback: Callable[[float], None],
        status_callback: Callable[[str, str | None], None],
    ) -> str | None:
        """Resolve the md5 through each configured mirror and download the file.

        Returns the staged temp path on success (orchestrator handles post-processing) or
        None if every mirror fails.
        """
        from shelfmark.core import mirrors

        try:
            if cancel_flag.is_set():
                status_callback("cancelled", "Cancelled")
                return None

            # source_id was namespaced "libgen:<md5>" to avoid a queue-key collision with
            # direct_download; strip it back to the bare (lowercase) md5 the download page expects.
            md5 = task.task_id.split(":", 1)[-1].lower()

            if config.get("FILE_ORGANIZATION", "rename") == "none":
                book_name = f"{md5}.{task.format or 'bin'}"
            else:
                book_name = build_filename(task.title, task.author, task.year, task.format)
            book_path = TMP_DIR / book_name

            for base in mirrors.get_libgen_mirrors():
                if cancel_flag.is_set():
                    status_callback("cancelled", "Cancelled")
                    return None

                ads_url = f"{base.rstrip('/')}/ads.php?md5={md5}"
                status_callback("resolving", "Resolving Libgen")
                ads_html = scraper.fetch_page(ads_url, (5, 10))
                if not ads_html:
                    continue
                get_url = scraper.resolve_download_url(ads_html, base)
                if not get_url:
                    continue

                # _selector=None: download_url builds its own AAMirrorSelector (a no-op for
                # non-AA URLs), so we avoid initialising dead AA-mirror state here.
                data = downloader.download_url(
                    get_url,
                    task.size or "",
                    progress_callback,
                    cancel_flag,
                    None,
                    status_callback,
                    referer=ads_url,
                )
                if not data:
                    continue
                if data.tell() < _MIN_VALID_FILE_SIZE:
                    logger.warning("Libgen file too small from %s, treating as failure", base)
                    continue

                data.seek(0)
                with book_path.open("wb") as file:
                    file.write(data.getbuffer())
                return str(book_path)
        except Exception as exc:
            if cancel_flag.is_set():
                status_callback("cancelled", "Cancelled")
            else:
                logger.exception("Error downloading from Libgen")
                status_callback("error", str(exc))
            return None
        else:
            # Loop exhausted without returning: every mirror failed to resolve/download.
            status_callback("error", "All Libgen mirrors failed")
            return None

    def cancel(self, task_id: str) -> bool:
        """Cancellation is handled by the orchestrator via the cancel_flag."""
        return False
