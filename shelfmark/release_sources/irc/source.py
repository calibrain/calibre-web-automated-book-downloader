"""IRC release source plugin.

Searches IRC channels for ebook and audiobook releases.
"""

import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from shelfmark.core.search_plan import ReleaseSearchPlan
    from shelfmark.metadata_providers import BookMetadata

from shelfmark.api.websocket import ws_manager
from shelfmark.core.author_match import author_affinity, search_surname
from shelfmark.core.config import config
from shelfmark.core.logger import setup_logger
from shelfmark.core.utils import is_audiobook
from shelfmark.release_sources import (
    ColumnColorHint,
    ColumnRenderType,
    ColumnSchema,
    LeadingCellConfig,
    LeadingCellType,
    Release,
    ReleaseColumnConfig,
    ReleaseProtocol,
    ReleaseSource,
    SourceActionButton,
    register_source,
)

from .connection_manager import connection_manager
from .dcc import DCCError, download_dcc, safe_dcc_filename
from .parser import SearchResult, extract_results_from_zip, parse_results_file

logger = setup_logger(__name__)


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
            try:
                return int(stripped)
            except ValueError:
                return default
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


def _emit_status(message: str, phase: str = "searching") -> None:
    """Emit search status to frontend via WebSocket."""
    ws_manager.broadcast_search_status(
        source="irc",
        provider="",
        book_id="",
        message=message,
        phase=phase,
    )


def _reported_author(release: Release) -> str:
    """The author a result actually claims, with the parser's sentinel read as none.

    A filename with no " - " separator has no author to report and parser.py:168
    fills in "Unknown". Ranked literally that sorts as a wrong author, below every
    result that named someone else; as absent it sorts between agreement and
    disagreement, which is what the tier was built for.
    """
    author = release.extra.get("author", "")
    return "" if author == "Unknown" else author


# Rate limiting to avoid server throttling
MIN_SEARCH_INTERVAL = 15.0
_last_search_time: float = 0

# Anti-spam budget: the exact same message may only be posted to the channel a limited
# number of times within a rolling window. This stops a retry/refresh loop from flooding
# the channel with the same line over and over, while still allowing a few genuine retries
# (a search that came back empty can be tried again, and Refresh works until the budget runs
# out). Normal use never hits this: successful searches are served from the result cache
# without re-posting at all.
MAX_IDENTICAL_SENDS = 3
IDENTICAL_SEND_WINDOW_SECONDS = 24 * 60 * 60  # 24 hours
# message-send-key -> timestamps of recent posts of that exact message
_recent_message_sends: dict[str, list[float]] = {}


def _enforce_rate_limit() -> None:
    """Ensure minimum time between searches."""
    global _last_search_time

    elapsed = time.time() - _last_search_time
    if elapsed < MIN_SEARCH_INTERVAL:
        wait_time = MIN_SEARCH_INTERVAL - elapsed
        logger.info("Rate limiting: waiting %.1fs", wait_time)
        time.sleep(wait_time)

    _last_search_time = time.time()


def _query_identity(server: str, channel: str, query: str) -> str:
    """Stable identity for a query on a given IRC server-channel.

    Used as BOTH the result-cache key and the per-query send-counter key, so the same
    query shares one cached answer and one send budget regardless of which book or
    content type triggered it.
    """
    return f"{server.casefold()}:{channel.casefold()}:{query.strip().casefold()}"


def _recent_send_count(key: str) -> int:
    """Number of times this exact message was posted within the rolling window."""
    cutoff = time.time() - IDENTICAL_SEND_WINDOW_SECONDS
    timestamps = [ts for ts in _recent_message_sends.get(key, []) if ts > cutoff]
    if timestamps:
        _recent_message_sends[key] = timestamps
    else:
        _recent_message_sends.pop(key, None)
    return len(timestamps)


def _record_message_sent(key: str) -> None:
    """Record that an exact message was just posted to the channel."""
    now = time.time()
    cutoff = now - IDENTICAL_SEND_WINDOW_SECONDS
    timestamps = [ts for ts in _recent_message_sends.get(key, []) if ts > cutoff]
    timestamps.append(now)
    _recent_message_sends[key] = timestamps


@register_source("irc")
class IRCReleaseSource(ReleaseSource):
    """Search IRC channels for ebook and audiobook releases."""

    name = "irc"
    display_name = "IRC"
    supported_content_types: ClassVar[list[str]] = ["ebook", "audiobook"]
    can_be_default = False  # Exclude from default source options (requires deliberate selection)

    def __init__(self) -> None:
        """Initialize per-search IRC source state."""
        # Track online servers from most recent search
        self._online_servers: set[str] | None = None

    def is_available(self) -> bool:
        """Check if IRC is configured (server, channel, nick, and search bot are set).

        The search bot is required: without it we would post bare queries straight
        to the channel, which reads as spam and gets the nick banned.
        """
        server = _config_text("IRC_SERVER")
        channel = _config_text("IRC_CHANNEL")
        nick = _config_text("IRC_NICK")
        search_bot = _config_text("IRC_SEARCH_BOT")
        return bool(server and channel and nick and search_bot)

    def get_column_config(self) -> ReleaseColumnConfig:
        """Configure UI columns for IRC results."""
        return ReleaseColumnConfig(
            columns=[
                ColumnSchema(
                    key="extra.server",
                    label="Server",
                    render_type=ColumnRenderType.TEXT,
                    width="100px",
                    sortable=True,
                ),
                ColumnSchema(
                    key="format",
                    label="Format",
                    render_type=ColumnRenderType.BADGE,
                    color_hint=ColumnColorHint(type="map", value="format"),
                    width="70px",
                    uppercase=True,
                    sortable=True,
                ),
                ColumnSchema(
                    key="size",
                    label="Size",
                    render_type=ColumnRenderType.TEXT,
                    width="70px",
                    sortable=True,
                    sort_key="size_bytes",
                ),
            ],
            grid_template="minmax(0,2fr) 100px 70px 70px",
            leading_cell=LeadingCellConfig(type=LeadingCellType.NONE),
            online_servers=list(self._online_servers) if self._online_servers else None,
            cache_ttl_seconds=1800,  # 30 minutes - IRC searches are slow, cache longer
            supported_filters=["format"],  # IRC has no language metadata
            action_button=SourceActionButton(label="Refresh search"),
        )

    def search(
        self,
        book: BookMetadata,
        plan: ReleaseSearchPlan,
        *,
        expand_search: bool = False,
        content_type: str = "ebook",
    ) -> list[Release]:
        """Search IRC for books matching metadata.

        The expand_search parameter is repurposed for IRC as a "refresh" flag.
        When True, it bypasses the cache and forces a fresh search.
        """
        from .cache import cache_results, get_cached_results

        if not self.is_available():
            logger.debug("IRC source is disabled, skipping search")
            return []

        query = self._build_query(book, plan)
        if not query:
            logger.warning("No search query could be built")
            return []

        # A manual query is the user's own words; ranking it against the metadata
        # author would second-guess what they typed.
        wanted_author = "" if plan.manual_query else plan.author

        # Get IRC settings
        server = _config_text("IRC_SERVER")
        port = _config_port("IRC_PORT", 6697)
        use_tls = _config_bool("IRC_USE_TLS", True)
        channel = _config_text("IRC_CHANNEL")
        nick = _config_text("IRC_NICK")
        search_bot = _config_text("IRC_SEARCH_BOT")

        # A few networks index audiobooks in a separate channel from ebooks (Undernet's
        # #bookz, say). When an audiobook channel is configured and an audiobook was
        # requested, route the search there (with its own search bot if set). Otherwise
        # fall back to the main channel/bot — that is the common case, since most networks
        # (irchighway included) serve both formats from the one channel.
        if is_audiobook(content_type):
            audiobook_channel = _config_text("IRC_AUDIOBOOK_CHANNEL")
            if audiobook_channel:
                channel = audiobook_channel
                audiobook_search_bot = _config_text("IRC_AUDIOBOOK_SEARCH_BOT")
                if audiobook_search_bot:
                    search_bot = audiobook_search_bot

        # Never post an unaddressed query to the channel. A bare book title looks like
        # spam to everyone else in the channel and gets the nick banned. Searches must
        # be addressed to a search bot ("@<bot> <query>").
        if not search_bot:
            logger.warning(
                "IRC search bot not configured; refusing to post unaddressed query to channel"
            )
            _emit_status("IRC search bot not configured", phase="error")
            return []

        # One identity per query on this server-channel. The result cache and the send
        # counter are both keyed on it: the SAME query shares one cached answer and one
        # send budget regardless of which book/content type triggered it, while different
        # queries are independent (searching 100 different books posts 100 messages).
        requested = "audiobook" if is_audiobook(content_type) else "ebook"
        query_key = _query_identity(server, channel, query)

        # Serve the cached whole answer for an identical query (unless this is a refresh).
        if not expand_search:
            cached = get_cached_results(query_key)
            if cached:
                _emit_status("Using cached results", phase="complete")
                self._online_servers = set(cached.get("online_servers", []))
                return self._rank_by_author(
                    self._filter_by_content_type(cached["releases"], requested), wanted_author
                )

        # Anti-spam cap: the exact same query may only be POSTED a limited number of times
        # per window, even via refresh. Beyond that, serve whatever is cached rather than
        # re-posting the identical message to the channel.
        if _recent_send_count(query_key) >= MAX_IDENTICAL_SENDS:
            logger.info(
                "IRC query hit %s-send limit in window, not re-posting: %s",
                MAX_IDENTICAL_SENDS,
                query,
            )
            _emit_status(
                "Search limit reached for this query — showing latest results", phase="complete"
            )
            cached = get_cached_results(query_key)
            if cached:
                self._online_servers = set(cached.get("online_servers", []))
                return self._rank_by_author(
                    self._filter_by_content_type(cached["releases"], requested), wanted_author
                )
            return []

        logger.info("IRC search: %s", query)

        # Enforce rate limit
        _enforce_rate_limit()

        client = None
        try:
            # Get or reuse IRC connection
            _emit_status(f"Connecting to {server}...", phase="connecting")
            client = connection_manager.get_connection(
                server=server,
                port=port,
                nick=nick,
                use_tls=use_tls,
                channel=channel,
            )

            # Capture online servers (elevated users in channel)
            self._online_servers = client.online_servers

            # Send search request (always addressed to the search bot, never bare)
            client.send_message(f"#{channel}", f"@{search_bot} {query}")
            _record_message_sent(query_key)

            # Wait for results DCC - this is the long wait.
            # Don't restrict the sender to the trigger bot's nick: many channels answer an
            # "@search" from a differently-named results bot. The DCC endpoint/filename are
            # still validated, and wait_for_dcc falls back to the channel's server list.
            _emit_status(f"Connected to #{channel} - Waiting for results...", phase="searching")
            offer = client.wait_for_dcc(timeout=60.0, result_type=True)

            online_servers = list(self._online_servers) if self._online_servers else None

            if not offer:
                logger.info("No search results received")
                _emit_status("No results found", phase="complete")
                # Release connection for reuse (don't close it)
                connection_manager.release_connection(client)
                # Cache the (empty) answer under the query identity so an identical query
                # is served from cache instead of re-posting.
                cache_results(query_key, query, [], online_servers=online_servers)
                return []

            # Download results file
            _emit_status(f"Connected to #{channel} - Downloading results...", phase="downloading")
            with tempfile.TemporaryDirectory() as tmpdir:
                result_path = Path(tmpdir) / safe_dcc_filename(offer.filename)
                download_dcc(offer, result_path, timeout=30.0)

                # Parse results
                if result_path.suffix.lower() == ".zip":
                    content = extract_results_from_zip(result_path)
                else:
                    content = result_path.read_text(errors="replace")

            # Release connection for reuse (don't close it)
            connection_manager.release_connection(client)

            # A single "@search" returns one file containing every format. Parse the whole
            # answer (both ebooks and audiobooks) and cache it under the query identity, so
            # requesting the other content type is served from cache without re-posting.
            ebook_releases = self._convert_to_releases(
                parse_results_file(content, content_type="ebook"), content_type="ebook"
            )
            audiobook_releases = self._convert_to_releases(
                parse_results_file(content, content_type="audiobook"), content_type="audiobook"
            )
            cache_results(
                query_key,
                query,
                ebook_releases + audiobook_releases,
                online_servers=online_servers,
            )
            # Ranked on the way out, never before the cache: one query identity is
            # shared by every book that produced the same query, so the order has to
            # follow the author asked for now, not the one that filled the cache.
            releases = self._rank_by_author(
                audiobook_releases if requested == "audiobook" else ebook_releases, wanted_author
            )

        except DCCError as e:
            logger.exception("DCC error during search")
            _emit_status(f"DCC error: {e}", phase="error")
            if client:
                connection_manager.close_connection(client)
            return []
        except Exception as e:
            logger.exception("IRC search failed")
            _emit_status(f"Search failed: {e}", phase="error")
            if client:
                connection_manager.close_connection(client)
            return []

        else:
            return releases

    def _build_query(self, book: BookMetadata, plan: ReleaseSearchPlan) -> str:
        """Build the line posted to the channel: a title, plus a surname.

        Both come off the variant rather than the plan: an ISBN fallback variant
        carries `author=""` on purpose (search_plan.py:246), and so does a manual
        query, so reading `plan.author` here would append a surname to searches
        that deliberately have none.
        """
        variant = plan.title_variants[0] if plan.title_variants else None
        title = variant.title if variant else (book.search_title or book.title)
        author = variant.author if variant else plan.author
        parts = [part for part in (title, search_surname(author)) if part]
        return " ".join(parts)

    def _rank_by_author(self, releases: list[Release], wanted_author: str) -> list[Release]:
        """Order releases by author agreement, under the server's availability.

        A surname is a weak filter - it also matches a different author who shares
        it - so the full name decides the order while the search bot decides the
        set. Availability stays the outer key: downloading asks one named bot and
        waits 120s for it (handler.py:133-139), so a release from a bot that is not
        in the channel must not outrank one that can actually answer. Sorting is
        stable, so format and server order survive inside each tier.
        """
        if not wanted_author:
            return releases
        online = self._online_servers or set()
        return sorted(
            releases,
            key=lambda release: (
                0 if release.extra.get("server", "") in online else 1,
                author_affinity(wanted_author, _reported_author(release)),
            ),
        )

    # Format priority for sorting (lower = higher priority)
    EBOOK_FORMAT_PRIORITY: ClassVar[dict[str, int]] = {
        "epub": 0,
        "mobi": 1,
        "azw3": 2,
        "azw": 3,
        "fb2": 4,
        "djvu": 5,
        "pdf": 6,
        "cbr": 7,
        "cbz": 8,
        "doc": 9,
        "docx": 10,
        "rtf": 11,
        "txt": 12,
        "html": 13,
        "htm": 14,
        "rar": 15,
        "zip": 16,
    }

    AUDIOBOOK_FORMAT_PRIORITY: ClassVar[dict[str, int]] = {
        "m4b": 0,
        "mp3": 1,
        "m4a": 2,
        "mp4": 3,
        "flac": 4,
        "opus": 5,
        "ogg": 6,
        "aac": 7,
        "wav": 8,
        "wma": 9,
        "rar": 10,
        "zip": 11,
    }

    def _convert_to_releases(
        self,
        results: list[SearchResult],
        content_type: str = "ebook",
    ) -> list[Release]:
        """Convert parsed results to Release objects, sorted by online/format/server."""
        releases = []
        online_servers = self._online_servers or set()
        format_priority_map = (
            self.AUDIOBOOK_FORMAT_PRIORITY
            if content_type == "audiobook"
            else self.EBOOK_FORMAT_PRIORITY
        )

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
                content_type=content_type,
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
            format_priority = format_priority_map.get(fmt, 99)
            return (
                0 if is_online else 1,  # Online first
                format_priority,  # Then by format
                server.lower(),  # Then alphabetically by server
            )

        releases.sort(key=sort_key)

        return releases

    @staticmethod
    def _filter_by_content_type(releases: list[Release], requested: str) -> list[Release]:
        """Pick the requested content type out of a cached whole answer.

        The cache stores releases for every content type under one query identity; each
        release is tagged with its content type (defaulting to ebook when missing).
        """
        return [release for release in releases if (release.content_type or "ebook") == requested]

    @staticmethod
    def _parse_size(size_str: str) -> int | None:
        """Parse human-readable size (e.g., '1.2MB', '500K') to bytes."""
        if not size_str:
            return None

        size_str = size_str.strip().upper()

        # Map suffixes to multipliers (check longer suffixes first)
        multipliers = [
            ("GB", 1024 * 1024 * 1024),
            ("MB", 1024 * 1024),
            ("KB", 1024),
            ("G", 1024 * 1024 * 1024),
            ("M", 1024 * 1024),
            ("K", 1024),
            ("B", 1),
        ]

        for suffix, mult in multipliers:
            if size_str.endswith(suffix):
                try:
                    num = float(size_str[: -len(suffix)].strip())
                    return int(num * mult)
                except ValueError:
                    return None

        # Try parsing as plain number (bytes)
        try:
            return int(float(size_str))
        except ValueError:
            return None
