"""IRC Highway release source plugin.

Searches IRC Highway #ebooks channel for book releases.
"""

import tempfile
import time
from pathlib import Path
from typing import List, Optional

from cwa_book_downloader.api.websocket import ws_manager
from cwa_book_downloader.core.config import config
from cwa_book_downloader.core.logger import setup_logger
from cwa_book_downloader.metadata_providers import BookMetadata
from cwa_book_downloader.release_sources import (
    ColumnColorHint,
    ColumnRenderType,
    ColumnSchema,
    LeadingCellConfig,
    LeadingCellType,
    Release,
    ReleaseColumnConfig,
    ReleaseProtocol,
    ReleaseSource,
    register_source,
)

from .client import DEFAULT_CHANNEL, IRCClient
from .dcc import DCCError, download_dcc
from .parser import SearchResult, extract_results_from_zip, parse_results_file

logger = setup_logger(__name__)


def _emit_status(message: str, phase: str = 'searching') -> None:
    """Emit search status to frontend via WebSocket."""
    ws_manager.broadcast_search_status(
        source='irc',
        provider='',
        book_id='',
        message=message,
        phase=phase,
    )

# Rate limiting to avoid server throttling
MIN_SEARCH_INTERVAL = 15.0
_last_search_time: float = 0


def _enforce_rate_limit() -> None:
    """Ensure minimum time between searches."""
    global _last_search_time

    elapsed = time.time() - _last_search_time
    if elapsed < MIN_SEARCH_INTERVAL:
        wait_time = MIN_SEARCH_INTERVAL - elapsed
        logger.info(f"Rate limiting: waiting {wait_time:.1f}s")
        time.sleep(wait_time)

    _last_search_time = time.time()


@register_source("irc")
class IRCReleaseSource(ReleaseSource):
    """Search IRC Highway #ebooks for book releases."""

    name = "irc"
    display_name = "IRC Highway"

    def __init__(self):
        # Track online servers from most recent search
        self._online_servers: Optional[list[str]] = None

    @classmethod
    def is_available(cls) -> bool:
        """Check if IRC is enabled in settings."""
        return config.get("IRC_ENABLED", False)

    def get_column_config(self) -> ReleaseColumnConfig:
        """Configure UI columns for IRC results.

        Includes online_servers from the most recent search, allowing
        the frontend to show status indicators for each server.
        """
        return ReleaseColumnConfig(
            columns=[
                ColumnSchema(
                    key="extra.server",
                    label="Server",
                    render_type=ColumnRenderType.TEXT,
                    width="100px",
                ),
                ColumnSchema(
                    key="format",
                    label="Format",
                    render_type=ColumnRenderType.BADGE,
                    color_hint=ColumnColorHint(type="map", value="format"),
                    width="70px",
                    uppercase=True,
                ),
                ColumnSchema(
                    key="size",
                    label="Size",
                    render_type=ColumnRenderType.TEXT,
                    width="70px",
                ),
            ],
            grid_template="minmax(0,2fr) 100px 70px 70px",
            leading_cell=LeadingCellConfig(type=LeadingCellType.NONE),
            online_servers=list(self._online_servers) if self._online_servers else None,
            cache_ttl_seconds=1800,  # 30 minutes - IRC searches are slow, cache longer
            supported_filters=["format"],  # IRC has no language metadata
        )

    def search(
        self,
        book: BookMetadata,
        expand_search: bool = False,
        languages: Optional[List[str]] = None
    ) -> List[Release]:
        """Search IRC Highway for books matching metadata.

        Args:
            book: Book metadata (title, authors, etc.)
            expand_search: Ignored - IRC always uses title+author search
            languages: Ignored - IRC doesn't support language filtering

        Returns:
            List of matching releases
        """
        # Build search query
        query = self._build_query(book)
        if not query:
            logger.warning("No search query could be built")
            return []

        logger.info(f"IRC search: {query}")

        # Enforce rate limit
        _enforce_rate_limit()

        search_bot = config.get("IRC_SEARCH_BOT", "search")
        nick = config.get("IRC_NICK") or None

        client = None
        try:
            # Connect to IRC
            _emit_status("Connecting to IRC Highway...", phase='connecting')
            client = IRCClient(nick=nick)
            client.connect()

            _emit_status("Joining #ebooks...", phase='connecting')
            client.join_channel(DEFAULT_CHANNEL)

            # Capture online servers (elevated users in channel)
            self._online_servers = client.online_servers

            # Send search request
            client.send_message(f"#{DEFAULT_CHANNEL}", f"@{search_bot} {query}")

            # Wait for results DCC - this is the long wait
            _emit_status("Connected to #ebooks - Waiting for results...", phase='searching')
            offer = client.wait_for_dcc(timeout=60.0, result_type=True)
            if not offer:
                logger.info("No search results received")
                _emit_status("No results found", phase='complete')
                client.disconnect()
                return []

            # Download results file
            _emit_status("Connected to #ebooks - Downloading results...", phase='downloading')
            with tempfile.TemporaryDirectory() as tmpdir:
                result_path = Path(tmpdir) / offer.filename
                download_dcc(offer, result_path, timeout=30.0)

                # Parse results
                if result_path.suffix.lower() == '.zip':
                    content = extract_results_from_zip(result_path)
                else:
                    content = result_path.read_text(errors='replace')

            client.disconnect()

            # Convert to Release objects
            results = parse_results_file(content)
            return self._convert_to_releases(results)

        except DCCError as e:
            logger.error(f"DCC error during search: {e}")
            _emit_status(f"DCC error: {e}", phase='error')
            if client:
                client.disconnect()
            return []
        except Exception as e:
            logger.error(f"IRC search failed: {e}")
            _emit_status(f"Search failed: {e}", phase='error')
            if client:
                client.disconnect()
            return []

    def _build_query(self, book: BookMetadata) -> str:
        """Build search query from book metadata."""
        parts = []

        if book.title:
            parts.append(book.title)

        if book.authors:
            # Use first author
            author = book.authors[0] if isinstance(book.authors, list) else book.authors
            parts.append(author)

        return ' '.join(parts)

    # Format priority for sorting (lower = higher priority)
    FORMAT_PRIORITY = {
        'epub': 0,
        'mobi': 1,
        'azw3': 2,
        'azw': 3,
        'fb2': 4,
        'djvu': 5,
        'pdf': 6,
        'cbr': 7,
        'cbz': 8,
        'doc': 9,
        'docx': 10,
        'rtf': 11,
        'txt': 12,
        'html': 13,
        'htm': 14,
        'rar': 15,
        'zip': 16,
    }

    def _convert_to_releases(self, results: List[SearchResult]) -> List[Release]:
        """Convert parsed results to Release objects.

        Results are sorted by:
        1. Online status (online servers first)
        2. Format priority (epub > mobi > azw3 > ...)
        3. Server name (alphabetically)
        """
        releases = []
        online_servers = self._online_servers or set()

        for result in results:
            release = Release(
                source="irc",
                source_id=result.download_request,  # Full line for download
                title=result.title,
                format=result.format,
                size=result.size,
                size_bytes=self._parse_size(result.size) if result.size else None,
                protocol=ReleaseProtocol.DCC,
                indexer=f"IRC:{result.server}",
                extra={
                    "server": result.server,
                    "author": result.author,
                    "full_line": result.full_line,
                },
            )
            releases.append(release)

        # Tiered sort: online first, then by format priority, then by server name
        def sort_key(release: Release) -> tuple:
            server = release.extra.get("server", "")
            is_online = server in online_servers
            fmt = release.format.lower() if release.format else ""
            format_priority = self.FORMAT_PRIORITY.get(fmt, 99)
            return (
                0 if is_online else 1,  # Online first
                format_priority,         # Then by format
                server.lower(),          # Then alphabetically by server
            )

        releases.sort(key=sort_key)

        return releases

    @staticmethod
    def _parse_size(size_str: str) -> Optional[int]:
        """Parse human-readable size to bytes."""
        if not size_str:
            return None

        size_str = size_str.strip().upper()

        multipliers = {
            'B': 1,
            'KB': 1024,
            'MB': 1024 * 1024,
            'GB': 1024 * 1024 * 1024,
        }

        for suffix, mult in multipliers.items():
            if size_str.endswith(suffix):
                try:
                    num = float(size_str[:-len(suffix)].strip())
                    return int(num * mult)
                except ValueError:
                    return None

        return None
