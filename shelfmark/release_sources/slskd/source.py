"""slskd release source - searches the Soulseek network through a slskd instance.

Soulseek results are files shared by peers, not curated releases. Ebooks map one
file to one release. Audiobooks are usually a folder of per-chapter audio files, so
audiobook results are grouped by (peer, directory) into a single multi-file release.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from shelfmark.core.search_plan import ReleaseSearchPlan
    from shelfmark.metadata_providers import BookMetadata

from shelfmark.core.config import config
from shelfmark.core.formats import get_supported_formats_for
from shelfmark.core.logger import setup_logger
from shelfmark.core.utils import (
    ARCHIVE_FORMATS,
    normalize_http_url,
)
from shelfmark.core.utils import is_audiobook as check_audiobook
from shelfmark.release_sources import (
    ColumnAlign,
    ColumnColorHint,
    ColumnRenderType,
    ColumnSchema,
    LeadingCellConfig,
    LeadingCellType,
    Release,
    ReleaseColumnConfig,
    ReleaseProtocol,
    ReleaseSource,
    SortOption,
    SourceActionButton,
    SourceUnavailableError,
    register_source,
)
from shelfmark.release_sources.prowlarr.source import parse_size
from shelfmark.release_sources.slskd.api import SlskdClient

logger = setup_logger(__name__)

SOURCE_NAME = "slskd"
DISPLAY_NAME = "Soulseek (slskd)"

# Soulseek searches complete on their own after slskd's per-search timeout; this caps
# the whole Shelfmark-side search across every query variant.
SLSKD_SEARCH_TIMEOUT_SECONDS = 90.0
DEFAULT_SEARCH_WAIT_SECONDS = 15
DEFAULT_RESPONSE_LIMIT = 100
MAX_QUERY_VARIANTS = 3

# Whole-book audio files: one file holds the entire book, unlike chapter files.
CONTAINER_FORMATS = frozenset({"m4b", "m4a"})


@dataclass(frozen=True)
class PeerFile:
    """One shared file from a search response, with its remote path split apart."""

    username: str
    filename: str  # Full remote path exactly as slskd reports it
    directory: str  # Remote directory (Soulseek separators preserved)
    basename: str
    extension: str  # Lowercase, no dot
    size: int


def split_remote_path(filename: str) -> tuple[str, str]:
    """Split a Soulseek path into (directory, basename).

    Soulseek paths use backslashes, but some clients share with forward slashes.
    """
    text = str(filename or "")
    cut = max(text.rfind("\\"), text.rfind("/"))
    if cut < 0:
        return "", text
    return text[:cut], text[cut + 1 :]


def remote_directory_name(directory: str) -> str:
    """Return the last non-empty segment of a remote directory path."""
    return next((part for part in reversed(re.split(r"[\\/]", directory)) if part), "")


_DRIVE = re.compile(r"^[A-Za-z]:")


def safe_remote_segments(filename: str) -> list[str] | None:
    """The segments of a peer's file path, or None when the path is unsafe to lay out locally.

    slskd turns the last folder and the file name into a path under its downloads folder, and
    a peer controls both. A path with an empty, ``.`` or ``..`` segment is refused, which
    covers one starting at ``/`` or ``\\`` and an empty last folder (``a//b.epub``). A drive
    (``C:``) is allowed only as the first of three or more segments: Windows peers share from
    there, and slskd keeps just the last folder, so it never reaches a local path.
    """
    text = str(filename or "")
    if "\0" in text:
        return None
    segments = re.split(r"[\\/]", text)
    if any(not part or part in {".", ".."} for part in segments):
        return None
    if any(_DRIVE.match(part) for part in segments[1:]) or (
        len(segments) < 3 and _DRIVE.match(segments[0])  # the drive would be the local folder
    ):
        return None
    return segments


def file_extension(basename: str) -> str:
    """Return the lowercase extension of a file name, without the dot."""
    name = str(basename or "")
    if "." not in name:
        return ""
    return name.rsplit(".", 1)[1].strip().lower()


def make_source_id(username: str, remote_path: str) -> str:
    """Stable, cache-safe id for a (peer, remote path) pair."""
    digest = sha256(f"{username}\0{remote_path}".encode()).hexdigest()[:24]
    return f"{SOURCE_NAME}:{digest}"


def get_supported_formats(content_type: str | None) -> set[str]:
    """Return the configured formats for the requested content type."""
    return set(get_supported_formats_for(content_type))


def _coerce_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value.strip()))
        except ValueError:
            return 0
    return 0


def iter_peer_files(response: dict[str, Any]) -> list[PeerFile]:
    """Extract the downloadable files from one slskd search response.

    Locked files (the peer requires a friend/user list) are skipped: slskd would
    just queue them forever.
    """
    username = str(response.get("username") or "").strip()
    raw_files = response.get("files")
    if not username or not isinstance(raw_files, list):
        return []

    peer_files: list[PeerFile] = []
    for raw in raw_files:
        if not isinstance(raw, dict) or raw.get("isLocked"):
            continue
        filename = str(raw.get("filename") or "").strip()
        if not filename:
            continue
        if safe_remote_segments(filename) is None:
            logger.debug("slskd: skipping unsafe file name from %s: %r", username, filename)
            continue
        directory, basename = split_remote_path(filename)
        peer_files.append(
            PeerFile(
                username=username,
                filename=filename,
                directory=directory,
                basename=basename,
                extension=file_extension(basename),
                size=max(_coerce_int(raw.get("size")), 0),
            )
        )
    return peer_files


def _peer_extra(response: dict[str, Any]) -> dict[str, Any]:
    upload_speed = _coerce_int(response.get("uploadSpeed"))
    queue_length = _coerce_int(response.get("queueLength"))
    has_free_slot = bool(response.get("hasFreeUploadSlot"))
    if has_free_slot:
        availability = "Free slot"
    elif queue_length > 0:
        availability = f"Queue {queue_length}"
    else:
        availability = "Busy"
    return {
        "username": str(response.get("username") or ""),
        "upload_speed": upload_speed,
        "queue_length": queue_length,
        "has_free_upload_slot": has_free_slot,
        "availability": availability,
    }


def _strip_extension(basename: str) -> str:
    if "." not in basename:
        return basename
    stem = basename.rsplit(".", 1)[0]
    return stem or basename


def _path_text(directory: str) -> str:
    """A remote folder path as plain words. A Soulseek result has no author field, but the
    folders nearly always name one ("Books\\Frank Herbert\\Dune"), so the path is what
    auto-download's author check reads (it looks at ``extra["author"]``)."""
    return " ".join(part for part in re.split(r"[\\/]", directory) if part and part != "@@")


def file_release(peer_file: PeerFile, response: dict[str, Any], content_type: str) -> Release:
    """Build a single-file release."""
    is_audio = check_audiobook(content_type)
    return Release(
        source=SOURCE_NAME,
        source_id=make_source_id(peer_file.username, peer_file.filename),
        title=_strip_extension(peer_file.basename),
        format=peer_file.extension or None,
        language=None,
        size=parse_size(peer_file.size),
        size_bytes=peer_file.size or None,
        download_url=None,
        info_url=None,
        protocol=ReleaseProtocol.SOULSEEK,
        indexer=peer_file.username,
        content_type="audiobook" if is_audio else "ebook",
        extra={
            **_peer_extra(response),
            "directory": peer_file.directory,
            "directory_name": remote_directory_name(peer_file.directory),
            "author": _path_text(peer_file.directory),
            "title_raw": f"{remote_directory_name(peer_file.directory)} {_strip_extension(peer_file.basename)}".strip(),
            "file_count": 1,
            "files": [{"filename": peer_file.filename, "size": peer_file.size}],
            "formats": [peer_file.extension] if peer_file.extension else [],
        },
    )


def directory_release(
    files: list[PeerFile], response: dict[str, Any], content_type: str
) -> Release:
    """Build a multi-file release from every matching file in one peer directory."""
    first = files[0]
    total_size = sum(f.size for f in files)
    formats: list[str] = []
    for f in files:
        if f.extension and f.extension not in formats:
            formats.append(f.extension)
    formats.sort(key=lambda ext: -sum(1 for f in files if f.extension == ext))
    directory_name = remote_directory_name(first.directory) or first.username
    is_audio = check_audiobook(content_type)

    return Release(
        source=SOURCE_NAME,
        source_id=make_source_id(first.username, first.directory + "\\"),
        title=f"{directory_name} ({len(files)} files)",
        format=formats[0] if formats else None,
        language=None,
        size=parse_size(total_size),
        size_bytes=total_size or None,
        download_url=None,
        info_url=None,
        protocol=ReleaseProtocol.SOULSEEK,
        indexer=first.username,
        content_type="audiobook" if is_audio else "ebook",
        extra={
            **_peer_extra(response),
            "directory": first.directory,
            "directory_name": directory_name,
            "author": _path_text(first.directory),
            "title_raw": directory_name,
            "file_count": len(files),
            "files": [{"filename": f.filename, "size": f.size} for f in files],
            "formats": formats,
        },
    )


def build_releases(
    responses: list[dict[str, Any]],
    *,
    content_type: str,
    formats: set[str],
) -> list[Release]:
    """Turn slskd search responses into releases, filtered to ``formats``."""
    releases: list[Release] = []
    seen_ids: set[str] = set()
    group_audio = check_audiobook(content_type)

    for response in responses:
        matching = [f for f in iter_peer_files(response) if f.extension in formats]
        if not matching:
            continue

        if not group_audio:
            candidates = [file_release(f, response, content_type) for f in matching]
        else:
            by_directory: dict[str, list[PeerFile]] = {}
            for peer_file in matching:
                # A bare filename has no folder, so grouping on "" would bundle unrelated
                # files from the same peer into one audiobook; give each its own group.
                key = peer_file.directory or f"\0{peer_file.filename}"
                by_directory.setdefault(key, []).append(peer_file)
            candidates = []
            for grouped in by_directory.values():
                # An archive is a whole audiobook on its own; keep those separate from
                # the loose chapter files next to them.
                archives = [f for f in grouped if f.extension in ARCHIVE_FORMATS]
                # An m4b/m4a is a whole book in one file too. Next to a folder of chapters it
                # is a different copy of the book, not another chapter.
                containers = [f for f in grouped if f.extension in CONTAINER_FORMATS]
                loose = [
                    f
                    for f in grouped
                    if f.extension not in ARCHIVE_FORMATS and f.extension not in CONTAINER_FORMATS
                ]
                candidates.extend(
                    file_release(f, response, content_type) for f in [*archives, *containers]
                )
                if len(loose) == 1:
                    candidates.append(file_release(loose[0], response, content_type))
                elif loose:
                    candidates.append(directory_release(loose, response, content_type))

        for release in candidates:
            if release.source_id in seen_ids:
                continue
            seen_ids.add(release.source_id)
            releases.append(release)

    return releases


def merge_responses(batches: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Combine the responses of several searches, one per peer.

    Different queries can return different parts of the same peer's folder (the title+author
    query some chapters, the bare title others). Building releases per query would keep the
    first partial folder and drop the rest, so files are merged by peer first. The peer's
    slot and queue details are those of its first answer.
    """
    merged: dict[str, dict[str, Any]] = {}
    for batch in batches:
        for response in batch:
            username = str(response.get("username") or "").strip()
            if not username:
                continue
            known = merged.get(username)
            if known is None:
                merged[username] = {**response, "files": list(response.get("files") or [])}
                continue
            have = {str(f.get("filename") or "") for f in known["files"] if isinstance(f, dict)}
            for raw in response.get("files") or []:
                name = str(raw.get("filename") or "") if isinstance(raw, dict) else ""
                if name and name not in have:
                    known["files"].append(raw)
                    have.add(name)
    return list(merged.values())


def rank_key(release: Release) -> tuple:
    """Sort releases so the peers most likely to actually serve the file come first."""
    extra = release.extra
    return (
        0 if extra.get("has_free_upload_slot") else 1,
        _coerce_int(extra.get("queue_length")),
        -_coerce_int(extra.get("upload_speed")),
        release.title.lower(),
    )


def _config_int(key: str, default: int, *, minimum: int, maximum: int) -> int:
    value = _coerce_int(config.get(key, default))
    if value <= 0:
        value = default
    return max(minimum, min(maximum, value))


def build_client_from_config() -> SlskdClient | None:
    """Build a client from settings, or None when the URL is missing/invalid."""
    raw_url = str(config.get("SLSKD_URL", "") or "").strip()
    if not raw_url:
        return None
    url = normalize_http_url(raw_url)
    if not url:
        return None
    api_key = str(config.get("SLSKD_API_KEY", "") or "").strip()
    return SlskdClient(url, api_key)


def _plan_queries(plan: ReleaseSearchPlan) -> list[str]:
    """Search strings to try, most specific first, with duplicates removed."""
    queries: list[str] = []
    if plan.manual_query:
        queries.append(plan.manual_query)
    for variant in plan.title_variants:
        queries.append(variant.query)
        # Peers rarely name files with the author; the bare title is the fallback.
        if variant.author:
            queries.append(variant.title)

    unique: list[str] = []
    seen: set[str] = set()
    for query in queries:
        cleaned = " ".join(str(query or "").split())
        key = cleaned.lower()
        if not cleaned or key in seen:
            continue
        seen.add(key)
        unique.append(cleaned)
    return unique[:MAX_QUERY_VARIANTS]


@register_source(SOURCE_NAME)
class SlskdSource(ReleaseSource):
    """Release source for the Soulseek network via slskd."""

    name = SOURCE_NAME
    display_name = DISPLAY_NAME
    supported_content_types: ClassVar[list[str]] = ["ebook", "audiobook"]

    def get_column_config(self) -> ReleaseColumnConfig:
        return ReleaseColumnConfig(
            columns=[
                ColumnSchema(
                    key="extra.username",
                    label="Peer",
                    render_type=ColumnRenderType.TEXT,
                    align=ColumnAlign.LEFT,
                    width="minmax(100px, 1fr)",
                    hide_mobile=False,
                    sortable=True,
                    sort_key="indexer",
                ),
                ColumnSchema(
                    key="extra.availability",
                    label="Slot",
                    render_type=ColumnRenderType.TEXT,
                    align=ColumnAlign.CENTER,
                    width="80px",
                    hide_mobile=True,
                ),
                ColumnSchema(
                    key="format",
                    label="Format",
                    render_type=ColumnRenderType.BADGE,
                    align=ColumnAlign.CENTER,
                    width="70px",
                    hide_mobile=False,
                    color_hint=ColumnColorHint(type="map", value="format"),
                    uppercase=True,
                    sortable=True,
                ),
                ColumnSchema(
                    key="size",
                    label="Size",
                    render_type=ColumnRenderType.SIZE,
                    align=ColumnAlign.CENTER,
                    width="80px",
                    hide_mobile=False,
                    sortable=True,
                    sort_key="size_bytes",
                ),
            ],
            grid_template="minmax(0,2fr) minmax(100px,1fr) 80px 70px 80px",
            leading_cell=LeadingCellConfig(type=LeadingCellType.NONE),
            cache_ttl_seconds=600,
            supported_filters=["format"],
            extra_sort_options=[
                SortOption(label="Upload speed", sort_key="extra.upload_speed"),
                SortOption(
                    label="Queue length", sort_key="extra.queue_length", default_direction="asc"
                ),
            ],
            action_button=SourceActionButton(label="Search again"),
        )

    def _get_client(self) -> SlskdClient | None:
        return build_client_from_config()

    def search(
        self,
        book: BookMetadata,
        plan: ReleaseSearchPlan,
        *,
        expand_search: bool = False,
        content_type: str = "ebook",
    ) -> list[Release]:
        """Search Soulseek for files matching the book."""
        client = self._get_client()
        if client is None:
            logger.warning("slskd not configured - skipping search")
            return []

        queries = _plan_queries(plan)
        if not queries:
            logger.warning("slskd: no search query available")
            return []

        formats = get_supported_formats(content_type)
        wait_seconds = _config_int(
            "SLSKD_SEARCH_TIMEOUT", DEFAULT_SEARCH_WAIT_SECONDS, minimum=3, maximum=120
        )
        response_limit = _config_int(
            "SLSKD_RESPONSE_LIMIT", DEFAULT_RESPONSE_LIMIT, minimum=10, maximum=1000
        )
        # Searches now run to slskd's own end rather than being cut at the wait, so the
        # budget must let every planned query start even after slow ones.
        budget = max(SLSKD_SEARCH_TIMEOUT_SECONDS, len(queries) * (wait_seconds + 30.0))
        deadline = time.monotonic() + budget

        releases: list[Release] = []
        answered: list[list[dict[str, Any]]] = []
        last_error: Exception | None = None
        # Expanding keeps going through every variant even after one produced results.
        for index, query in enumerate(queries, start=1):
            if releases and not expand_search:
                break
            if time.monotonic() >= deadline:
                logger.warning("slskd search timed out before query %d/%d", index, len(queries))
                break
            logger.debug("slskd query %d/%d: '%s'", index, len(queries), query)
            try:
                responses = client.search(
                    query,
                    wait_seconds=wait_seconds,
                    response_limit=response_limit,
                )
            except Exception as e:
                logger.exception("slskd search failed for '%s'", query)
                last_error = e
                continue

            answered.append(responses)
            releases = build_releases(
                merge_responses(answered), content_type=content_type, formats=formats
            )

        if not answered and last_error is not None:
            # slskd being down or rejecting the API key is not "no releases found".
            msg = f"search failed: {last_error}"
            raise SourceUnavailableError(msg) from last_error

        releases.sort(key=rank_key)
        if releases:
            peers = len({r.indexer for r in releases})
            logger.info("slskd: %d results from %d peers", len(releases), peers)
        else:
            logger.debug("slskd: no results found")
        return releases

    def is_available(self) -> bool:
        if not config.get("SLSKD_ENABLED", False):
            return False
        if not str(config.get("SLSKD_API_KEY", "") or "").strip():
            return False
        return bool(normalize_http_url(str(config.get("SLSKD_URL", "") or "")))
