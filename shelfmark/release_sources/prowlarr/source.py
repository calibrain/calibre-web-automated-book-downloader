"""Prowlarr release source - searches indexers for book releases (torrents/usenet)."""

import re
import time
from dataclasses import dataclass
from threading import Lock
from typing import TYPE_CHECKING, ClassVar, NoReturn

import requests

if TYPE_CHECKING:
    from shelfmark.core.search_plan import ReleaseSearchPlan
    from shelfmark.metadata_providers import BookMetadata

from shelfmark.core.config import config
from shelfmark.core.languages import normalize_language
from shelfmark.core.logger import setup_logger
from shelfmark.core.request_helpers import normalize_optional_text
from shelfmark.core.search_plan import ReleaseSearchVariant
from shelfmark.core.utils import AUDIOBOOK_FORMATS as CORE_AUDIOBOOK_FORMATS
from shelfmark.core.utils import normalize_http_url
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
    SourceUnavailableError,
    register_source,
)
from shelfmark.release_sources.prowlarr.api import (
    IndexerSeedSettings,
    ProwlarrClient,
    ProwlarrSearchError,
)
from shelfmark.release_sources.prowlarr.cache import cache_release
from shelfmark.release_sources.prowlarr.utils import (
    AUTHOR_UNKNOWN,
    author_affinity,
    build_source_id,
    coerce_float_like,
    coerce_int_like,
    get_protocol,
)

logger = setup_logger(__name__)

_SIZE_UNIT_BASE = 1024
_TWO_FORMATS = 2
_PROWLARR_SOURCE_ERRORS = (AttributeError, OSError, RuntimeError, TypeError, ValueError)

# Prowlarr indexer priority is 1-50 and lower is preferred; unknown sorts last.
_UNRANKED_INDEXER_RANK = 51

# Errors that can surface from a ProwlarrClient call that talks to Prowlarr. The
# client raises requests exceptions (subclasses of OSError via IOError lineage
# is not guaranteed), so include RequestException explicitly.
_PROWLARR_REQUEST_ERRORS = (*_PROWLARR_SOURCE_ERRORS, requests.exceptions.RequestException)


def _raise_timeout_error(message: str) -> NoReturn:
    raise TimeoutError(message)


def _raise_invalid_indexer_id(item: object) -> NoReturn:
    msg = f"Invalid indexer id: {item!r}"
    raise ValueError(msg)


def _raise_invalid_indexer_selection_type(selected: object) -> NoReturn:
    msg = f"Invalid PROWLARR_INDEXERS type: {type(selected).__name__}"
    raise TypeError(msg)


def _coerce_indexer_id(value: object) -> int | None:
    """Best-effort coercion for indexer identifiers from config/API payloads."""
    return coerce_int_like(value)


def _identity_text(value: object) -> str | None:
    """Trimmed text for an identity field, or None when there is nothing usable."""
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return None


def _release_identity(result: dict) -> str | None:
    """Identify the underlying release, independent of which indexer surfaced it.

    Strong identifiers only. Title is deliberately excluded because matching on
    it here would merge two genuinely different releases that happen to share a
    name, and every caller of this either drops or overwrites a row on a match.
    Returns None when nothing identifies the result.
    """
    for field in ("guid", "downloadUrl", "magnetUrl", "infoUrl"):
        identity = _identity_text(result.get(field))
        if identity is not None:
            return identity
    return None


def _result_dedup_key(result: dict) -> tuple[int | None, str] | None:
    """Dedup key for a raw Prowlarr result, or None if it cannot be identified.

    One tracker is often configured in Prowlarr as several indexer entries that
    differ only by a server-side search filter, say a "freeleech only" entry
    alongside an unfiltered one. Those entries return the same guid for the same
    torrent, so keying on the guid alone throws away the filtered entry's copy
    and with it the only signal that the release matched the filter. Including
    the indexer id keeps the entries distinct.

    Title is an acceptable last resort here, unlike in _release_identity, because
    the indexer id is part of the key: it only ever collapses a literal repeat
    from one indexer, never two rows from different entries.
    """
    identity = _release_identity(result) or _identity_text(result.get("title"))
    if identity is None:
        return None
    return (_coerce_indexer_id(result.get("indexerId")), identity)


def _build_indexer_priority(indexers: list[dict]) -> dict[int, int]:
    """Map indexer id to the priority configured in Prowlarr. Lower is preferred.

    Users already rank their indexers in Prowlarr, and on trackers configured as
    several entries that ranking is usually the meaningful one: a "freeleech
    only" entry is typically given a better priority than the unfiltered entry
    beside it. Reusing it avoids asking for the same ordering a second time.
    """
    priority: dict[int, int] = {}
    for indexer in indexers:
        indexer_id = _coerce_indexer_id(indexer.get("id"))
        if indexer_id is None:
            continue
        rank = coerce_int_like(indexer.get("priority"))
        if rank is not None:
            priority[indexer_id] = rank

    return priority


def _drop_unknown_indexer_ids(
    selected_ids: list[int] | None, indexers: list[dict]
) -> list[int] | None:
    """Keep only selected indexer ids Prowlarr still serves.

    An indexer removed or disabled in Prowlarr stays in the saved selection,
    where settings can no longer show it - so it cannot be unselected, and every
    search keeps querying an indexer that is gone (#1283). Dropping it here
    keeps the saved selection intact for an indexer that comes back.
    """
    if selected_ids is None:
        return None

    live_ids = {
        indexer_id
        for indexer in indexers
        if (indexer_id := _coerce_indexer_id(indexer.get("id"))) is not None
    }
    kept = [indexer_id for indexer_id in selected_ids if indexer_id in live_ids]

    stale = [indexer_id for indexer_id in selected_ids if indexer_id not in live_ids]
    if stale:
        logger.warning(
            "Skipping selected Prowlarr indexers that are no longer enabled in Prowlarr: %s",
            stale,
        )

    return kept


def _rank_for_indexer_id(indexer_id: object, priority: dict[int, int]) -> int:
    """Preference rank for an indexer id. Lower wins, unknown ranks last."""
    coerced = _coerce_indexer_id(indexer_id)
    if coerced is None:
        return _UNRANKED_INDEXER_RANK
    return priority.get(coerced, _UNRANKED_INDEXER_RANK)


def _indexer_rank(result: dict, priority: dict[int, int]) -> int:
    """Preference rank of the indexer that surfaced a raw result."""
    return _rank_for_indexer_id(result.get("indexerId"), priority)


def _release_indexer_rank(release: Release, priority: dict[int, int]) -> int:
    """Preference rank of the indexer that surfaced a converted release."""
    return _rank_for_indexer_id(release.extra.get("indexer_id"), priority)


def _collapse_duplicate_indexer_results(
    results: list[dict], priority: dict[int, int]
) -> list[dict]:
    """Reduce a release to a single row, keeping the preferred indexer entry.

    Opt-in behaviour for users who want one row per torrent. Ties keep the
    result that was queried first, and the winner holds the loser's position so
    the overall result order stays stable.
    """
    position_by_identity: dict[str, int] = {}
    kept: list[dict] = []

    for result in results:
        identity = _release_identity(result)
        if identity is None:
            kept.append(result)
            continue

        existing_position = position_by_identity.get(identity)
        if existing_position is None:
            position_by_identity[identity] = len(kept)
            kept.append(result)
            continue

        if _indexer_rank(result, priority) < _indexer_rank(kept[existing_position], priority):
            kept[existing_position] = result

    return kept


def _parse_size(size_bytes: int | None) -> str | None:
    """Convert bytes to human-readable size string."""
    if size_bytes is None or size_bytes <= 0:
        return None

    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(size_bytes)
    unit_index = 0

    while size >= _SIZE_UNIT_BASE and unit_index < len(units) - 1:
        size /= _SIZE_UNIT_BASE
        unit_index += 1

    if unit_index == 0:
        return f"{int(size)} {units[unit_index]}"

    return f"{size:.1f} {units[unit_index]}"


# Common ebook formats in priority order
EBOOK_FORMATS = [
    "epub",
    "mobi",
    "azw3",
    "azw",
    "pdf",
    "cbz",
    "cbr",
    "fb2",
    "djvu",
    "lit",
    "pdb",
    "txt",
]

# Common audiobook formats
AUDIOBOOK_FORMATS = list(CORE_AUDIOBOOK_FORMATS)

# Combined list for format detection (audiobook formats first for priority)
ALL_BOOK_FORMATS = AUDIOBOOK_FORMATS + EBOOK_FORMATS


# Backend safeguard: cap total Prowlarr search time per request.
PROWLARR_SEARCH_TIMEOUT_SECONDS = 120.0

# The overall budget has to leave room for at least a couple of indexers to spend
# their full per-indexer timeout, otherwise raising PROWLARR_INDEXER_TIMEOUT for a
# Cloudflare-fronted tracker just moves the cutoff here. Capped short of the
# gunicorn worker timeout (300s) so the worker is never the thing that gives up.
_MAX_SEARCH_BUDGET_SECONDS = 240.0


def _search_budget_seconds(indexer_timeout: int) -> float:
    """Total time one Prowlarr search may spend, scaled to the per-indexer timeout."""
    return min(
        _MAX_SEARCH_BUDGET_SECONDS,
        max(PROWLARR_SEARCH_TIMEOUT_SECONDS, indexer_timeout * 2.0),
    )


@dataclass
class _IndexerSearchOutcome:
    """What one pass over the target indexers produced.

    Separates "every indexer answered, none had this book" from "the indexers
    never answered", which the caller has to tell apart before it decides to
    auto-expand or to report the search as failed.
    """

    results: list[dict]
    attempted: int = 0
    failed: int = 0
    skipped: int = 0
    last_error: str | None = None


def _extract_format(title: str) -> str | None:
    """Extract ebook/audiobook format from release title (extension, bracketed, or standalone)."""
    title_lower = title.lower()

    # Pattern priority: file extension > bracketed > standalone word
    # Use %s placeholder since {fmt} conflicts with regex syntax
    pattern_templates = [
        r'\.%s(?:["\'\s\]\)]|$)',  # .format at end or followed by delimiter
        r"[\[\(\{]%s[\]\)\}]",  # [EPUB], (PDF), {mobi}
        r"\b%s\b",  # standalone word
    ]

    for template in pattern_templates:
        for fmt in ALL_BOOK_FORMATS:
            if re.search(template % fmt, title_lower):
                return fmt

    return None


def _extract_mam_language(raw_title: str) -> str | None:
    """Extract the language code from MyAnonamouse titles.

    Prowlarr's MAM parser appends a structured bracket segment like:
      [ENG / EPUB MOBI PDF]

    The language code appears before the "/" - we extract it and map to
    the 2-char ISO code used by the frontend color maps.
    """
    if not raw_title:
        return None

    for bracket in re.findall(r"\[([^\]]+)\]", raw_title):
        if "/" not in bracket:
            continue

        before_slash, _ = bracket.split("/", 1)
        # Extract the language token (should be a 3-char code like ENG, ITA, etc.)
        tokens = re.findall(r"[A-Za-z]+", before_slash.strip())

        for token in tokens:
            lang_code = token.lower()
            resolved = normalize_language(lang_code)
            if resolved is not None:
                return resolved

    return None


def _split_mam_formats(raw_title: str) -> tuple[list[str], list[str]]:
    """Split the format tokens of a MyAnonamouse title into (recognized, unrecognized).

    Prowlarr's MAM parser appends a structured bracket segment like:
      [ENG / EPUB MOBI PDF]

    We only trust this structured segment (and do not attempt generic title
    heuristics for other indexers).

    Tokens after the "/" that Shelfmark does not know as a book or audiobook format
    (e.g. ``[ENG / AVI]``) are returned separately so the UI can warn that the release
    will download but cannot be processed, instead of showing a bare content-type icon
    that looks like an ordinary result.
    """
    if not raw_title:
        return [], []

    format_set = set(ALL_BOOK_FORMATS)
    first_unrecognized: list[str] | None = None
    for bracket in re.findall(r"\[([^\]]+)\]", raw_title):
        if "/" not in bracket:
            continue

        _, after_slash = bracket.split("/", 1)
        tokens = re.findall(r"[A-Za-z0-9]+", after_slash)

        formats: list[str] = []
        unrecognized: list[str] = []
        for token in tokens:
            fmt = token.lower()
            if fmt in format_set:
                if fmt not in formats:
                    formats.append(fmt)
            elif fmt not in unrecognized:
                unrecognized.append(fmt)

        if formats:
            return formats, unrecognized
        if unrecognized and first_unrecognized is None:
            first_unrecognized = unrecognized

    return [], first_unrecognized or []


def _extract_mam_formats(raw_title: str) -> list[str]:
    """Extract the recognized formats from a MyAnonamouse title (see _split_mam_formats)."""
    return _split_mam_formats(raw_title)[0]


def _formats_display(formats: list[str]) -> str | None:
    if not formats:
        return None
    if len(formats) == 1:
        return formats[0]
    if len(formats) == _TWO_FORMATS:
        return f"{formats[0]}, {formats[1]}"
    # Show first two formats + count of others to prevent overflow
    return f"{formats[0]}, {formats[1]} +{len(formats) - 2}"


# Prowlarr category IDs for content type detection
# See: https://wiki.servarr.com/prowlarr/cardigann-yml-definition#categories
AUDIOBOOK_CATEGORY_IDS = {3000, 3030}  # 3000 = Audio, 3030 = Audio/Audiobook
BOOK_CATEGORY_RANGE = range(7000, 8000)  # 7000-7999 = Books (all subcategories)


def _detect_content_type_from_categories(categories: list, fallback: str = "book") -> str:
    """Detect content type from Prowlarr category IDs. Returns 'audiobook', 'book', or 'other'."""
    # Normalize fallback - convert "ebook" to "book" for display consistency
    normalized_fallback = "book" if fallback == "ebook" else fallback

    if not categories:
        return normalized_fallback

    # Extract category IDs from the nested structure
    cat_ids = {
        cat.get("id") if isinstance(cat, dict) else cat
        for cat in categories
        if (isinstance(cat, dict) and cat.get("id") is not None) or isinstance(cat, int)
    }

    if not cat_ids:
        return normalized_fallback

    # Check for audiobook categories first (more specific), then any book range
    if cat_ids & AUDIOBOOK_CATEGORY_IDS:
        return "audiobook"
    if any(cat_id in BOOK_CATEGORY_RANGE for cat_id in cat_ids):
        return "book"

    # Categories are present but not book/audiobook
    return "other"


def _extract_capability_category_ids(categories: list[dict]) -> set[int]:
    """Flatten capability categories and subcategories into a single ID set."""
    category_ids: set[int] = set()

    for category in categories:
        if not isinstance(category, dict):
            continue

        category_id = category.get("id")
        if isinstance(category_id, int):
            category_ids.add(category_id)

        for subcategory in category.get("subCategories", []):
            if not isinstance(subcategory, dict):
                continue
            subcategory_id = subcategory.get("id")
            if isinstance(subcategory_id, int):
                category_ids.add(subcategory_id)

    return category_ids


def _indexer_supports_search_categories(indexer: dict, categories: list[int] | None) -> bool:
    """Return whether an indexer should be queried for the requested categories."""
    if not categories:
        return True

    capability_categories = indexer.get("capabilities", {}).get("categories", [])
    category_ids = _extract_capability_category_ids(capability_categories)
    if not category_ids:
        return True

    for requested_category in categories:
        if requested_category in BOOK_CATEGORY_RANGE:
            if any(cat_id in BOOK_CATEGORY_RANGE for cat_id in category_ids):
                return True
            continue

        if requested_category in category_ids:
            return True

    return False


def _prowlarr_result_to_release(
    result: dict,
    search_content_type: str = "ebook",
    *,
    enable_format_detection: bool = False,
) -> Release:
    """Convert a Prowlarr API result to a Release object."""
    raw_title = result.get("title", "Unknown")
    title = raw_title
    size_bytes = result.get("size")
    indexer = result.get("indexer", "Unknown")
    protocol = get_protocol(result)
    seeders = result.get("seeders")
    leechers = result.get("leechers")
    categories = result.get("categories", [])
    is_torrent = protocol == ReleaseProtocol.TORRENT
    raw_indexer_flags = result.get("indexerFlags") or []
    indexer_flags: list[str] = []
    seen_flags: set[str] = set()

    def add_indexer_flag(flag: object) -> None:
        if flag is None:
            return
        flag_str = str(flag).strip()
        if not flag_str:
            return
        lowered = flag_str.lower()
        if lowered in seen_flags:
            return
        seen_flags.add(lowered)
        indexer_flags.append(flag_str)

    if isinstance(raw_indexer_flags, list):
        for flag in raw_indexer_flags:
            add_indexer_flag(flag)
    elif isinstance(raw_indexer_flags, str):
        add_indexer_flag(raw_indexer_flags)

    # Format peers display string: "seeders / leechers"
    peers_display = (
        f"{seeders} / {leechers}"
        if is_torrent and seeders is not None and leechers is not None
        else None
    )

    format_detected: str | None = None
    formats: list[str] = []
    unrecognized_formats: list[str] = []
    formats_display: str | None = None
    language_detected: str | None = None
    if enable_format_detection:
        book_title = str(result.get("bookTitle") or "").strip()
        if book_title:
            title = book_title

        formats, unrecognized_formats = _split_mam_formats(str(raw_title or ""))
        format_detected = formats[0] if formats else None
        formats_display = _formats_display(formats)
        language_detected = _extract_mam_language(str(raw_title or ""))

    source_id = build_source_id(result)

    # Cache the raw Prowlarr result so handler can look it up by source_id
    cache_release(source_id, result)

    # Derive common indicators from torznab/newznab attrs when present.
    download_volume_factor = coerce_float_like(result.get("downloadVolumeFactor"))
    is_freeleech = download_volume_factor == 0.0

    if any(flag.lower() in {"freeleech", "fl"} for flag in indexer_flags):
        is_freeleech = True

    is_vip = "[vip]" in str(raw_title).lower()
    if is_vip:
        add_indexer_flag("VIP")
    if is_freeleech:
        add_indexer_flag("FreeLeech")

    return Release(
        source="prowlarr",
        source_id=source_id,
        title=title,
        format=format_detected,
        language=language_detected,
        size=_parse_size(size_bytes),
        size_bytes=size_bytes,
        download_url=None,
        info_url=result.get("infoUrl") or result.get("guid"),
        protocol=(
            ReleaseProtocol.TORRENT
            if protocol == "torrent"
            else ReleaseProtocol.NZB
            if protocol == "usenet"
            else None
        ),
        indexer=indexer,
        seeders=seeders if is_torrent else None,
        peers=peers_display,
        content_type=_detect_content_type_from_categories(categories, search_content_type),
        extra={
            "publish_date": result.get("publishDate"),
            "categories": categories,
            "indexer_id": result.get("indexerId"),
            "files": result.get("files"),
            "grabs": result.get("grabs"),
            "author": result.get("author"),
            "book_title": result.get("bookTitle"),
            "indexer_flags": indexer_flags,
            "vip": is_vip,
            "freeleech": is_freeleech,
            "download_volume_factor": result.get("downloadVolumeFactor"),
            "upload_volume_factor": result.get("uploadVolumeFactor"),
            "configured_ratio_limit": result.get("configuredRatioLimit"),
            "configured_seed_time_minutes": result.get("configuredSeedTimeMinutes"),
            "info_hash": result.get("infoHash"),
            "formats": formats or None,
            "formats_display": formats_display,
            # Format tokens the indexer declared but Shelfmark can't process (e.g. a MAM
            # "[ENG / AVI]"). Lets the UI warn instead of showing a bare content icon.
            "unrecognized_formats": unrecognized_formats or None,
            # Raw torznab attributes for rich tooltips (enriched indexers)
            "torznab_attrs": result.get("torznabAttrs"),
        },
    )


# Last successfully fetched per-indexer share limits. Used as a fallback when
# a transient Prowlarr API failure prevents fetching fresh settings during a
# search, so results are never silently cached without seed limits (#795).
_seed_settings_lock = Lock()
_last_known_seed_settings: dict[int, IndexerSeedSettings] = {}


def _fetch_indexer_seed_settings(
    client: ProwlarrClient,
    indexer_ids: list[int] | None,
) -> dict[int, IndexerSeedSettings]:
    """Fetch per-indexer share limits, falling back to last-known-good on failure."""
    try:
        fetched = client.get_indexer_seed_settings(restrict_to=indexer_ids)
    except _PROWLARR_REQUEST_ERRORS:
        with _seed_settings_lock:
            fallback = dict(_last_known_seed_settings)
        logger.warning(
            "Failed to fetch Prowlarr indexer seed settings; "
            "falling back to last known settings for %s indexer(s)",
            len(fallback),
            exc_info=True,
        )
        return fallback

    with _seed_settings_lock:
        _last_known_seed_settings.update(fetched)
    return fetched


def _apply_indexer_seed_settings(
    result: dict,
    indexer_seed_settings: dict[int, IndexerSeedSettings],
) -> dict:
    indexer_id = _coerce_indexer_id(result.get("indexerId"))
    if indexer_id is None:
        return result

    seed_settings = indexer_seed_settings.get(indexer_id)
    if not seed_settings:
        return result

    enriched_result = dict(result)
    if "ratio_limit" in seed_settings:
        enriched_result["configuredRatioLimit"] = seed_settings["ratio_limit"]
    if "seeding_time_limit_minutes" in seed_settings:
        enriched_result["configuredSeedTimeMinutes"] = seed_settings["seeding_time_limit_minutes"]

    return enriched_result


@register_source("prowlarr")
class ProwlarrSource(ReleaseSource):
    """Prowlarr release source for ebooks and audiobooks."""

    name = "prowlarr"
    display_name = "Prowlarr"
    supported_content_types: ClassVar[list[str]] = [
        "ebook",
        "audiobook",
    ]  # Explicitly declare support for both

    def __init__(self) -> None:
        """Initialize per-instance search state for Prowlarr."""
        self.last_search_type: str | None = None

    def get_column_config(self) -> ReleaseColumnConfig:
        """Column configuration for Prowlarr releases."""
        # Fetch available indexers from Prowlarr
        available_indexers: list[str] | None = None
        default_indexers: list[str] | None = None
        client = self._get_client()
        if client:
            try:
                enabled_indexers = client.get_enabled_indexers_detailed()
                # Get user-selected indexer IDs if configured
                selected_ids = self._get_selected_indexer_ids()

                all_indexer_names = []
                selected_indexer_names = []

                for idx in enabled_indexers:
                    idx_id = idx.get("id")
                    idx_name = idx.get("name")
                    if not idx_name:
                        continue

                    # Add to all indexers list
                    all_indexer_names.append(idx_name)

                    # If user has selected specific indexers, track those separately
                    if selected_ids is not None:
                        idx_id_int = _coerce_indexer_id(idx_id)
                        if idx_id_int is not None and idx_id_int in selected_ids:
                            selected_indexer_names.append(idx_name)

                available_indexers = sorted(all_indexer_names) if all_indexer_names else None
                # Only set default_indexers if user has selected specific ones
                default_indexers = (
                    sorted(selected_indexer_names) if selected_indexer_names else None
                )
            except _PROWLARR_SOURCE_ERRORS as e:
                logger.warning("Failed to fetch indexer list for column config: %s", e)

        return ReleaseColumnConfig(
            columns=[
                ColumnSchema(
                    key="indexer",
                    label="Indexer",
                    render_type=ColumnRenderType.INDEXER_PROTOCOL,
                    align=ColumnAlign.LEFT,
                    width="minmax(140px, 1fr)",
                    hide_mobile=False,
                    sortable=True,
                ),
                ColumnSchema(
                    key="extra.indexer_flags",
                    label="Flags",
                    render_type=ColumnRenderType.TAGS,
                    align=ColumnAlign.CENTER,
                    width="50px",
                    hide_mobile=False,
                    color_hint=ColumnColorHint(type="map", value="flags"),
                    fallback="",
                    uppercase=True,
                ),
                ColumnSchema(
                    key="language",
                    label="Lang",
                    render_type=ColumnRenderType.BADGE,
                    align=ColumnAlign.CENTER,
                    width="50px",
                    hide_mobile=True,
                    color_hint=ColumnColorHint(type="map", value="language"),
                    uppercase=True,
                    fallback="",
                ),
                ColumnSchema(
                    key="extra.formats_display",
                    label="Format",
                    render_type=ColumnRenderType.FORMAT_CONTENT_TYPE,
                    align=ColumnAlign.CENTER,
                    width="90px",
                    hide_mobile=False,
                    color_hint=ColumnColorHint(type="map", value="format"),
                    uppercase=True,
                    fallback="",
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
            extra_sort_options=[
                SortOption(label="Peers", sort_key="seeders"),
                SortOption(
                    label="Indexer priority",
                    sort_key="extra.indexer_priority",
                    default_direction="asc",
                ),
            ],
            grid_template="minmax(0,2fr) minmax(140px,1fr) 50px 50px 90px 80px",
            leading_cell=LeadingCellConfig(
                type=LeadingCellType.NONE
            ),  # No leading cell for Prowlarr
            available_indexers=available_indexers,
            default_indexers=default_indexers,
            supported_filters=[
                "language",
                "indexer",
            ],  # Enables multi-language query expansion and indexer filtering
        )

    def _get_client(self) -> ProwlarrClient | None:
        """Get a configured Prowlarr client or None if not configured."""
        raw_url = normalize_optional_text(config.get("PROWLARR_URL", "")) or ""
        api_key = normalize_optional_text(config.get("PROWLARR_API_KEY", "")) or ""

        if not raw_url or not api_key:
            return None

        url = normalize_http_url(raw_url)
        if not url:
            return None

        return ProwlarrClient(url, api_key)

    def _get_selected_indexer_ids(self) -> list[int] | None:
        """Get list of selected indexer IDs from config.

        Returns None if no indexers are selected (search all).
        Returns list of IDs if specific indexers are selected.
        """
        selected = config.get("PROWLARR_INDEXERS", "")
        if not selected:
            return None

        # Handle both list (from JSON config) and string (from env var)
        try:
            if isinstance(selected, list):
                # Already a list from JSON config
                ids = []
                for item in selected:
                    if not item:
                        continue
                    parsed_id = _coerce_indexer_id(item)
                    if parsed_id is None:
                        _raise_invalid_indexer_id(item)
                    ids.append(parsed_id)
            elif isinstance(selected, str):
                # Comma-separated string from env var
                ids = []
                for item in selected.split(","):
                    if not item.strip():
                        continue
                    parsed_id = _coerce_indexer_id(item)
                    if parsed_id is None:
                        _raise_invalid_indexer_id(item)
                    ids.append(parsed_id)
            else:
                _raise_invalid_indexer_selection_type(selected)
        except (ValueError, TypeError) as e:
            logger.warning("Invalid PROWLARR_INDEXERS format: %s (%s)", selected, e)
            return None
        else:
            return ids or None

    def _resolve_indexer_ids_from_names(
        self, client: ProwlarrClient, names: list[str]
    ) -> list[int] | None:
        """Convert indexer names to IDs by looking up enabled indexers.

        Returns None if no names could be resolved.
        """
        if not names:
            return None

        try:
            enabled_indexers = client.get_enabled_indexers_detailed()
            name_to_id = {
                idx.get("name"): idx.get("id")
                for idx in enabled_indexers
                if idx.get("name") and idx.get("id") is not None
            }

            ids = []
            for name in names:
                idx_id = name_to_id.get(name)
                parsed_id = _coerce_indexer_id(idx_id)
                if parsed_id is not None:
                    ids.append(parsed_id)
        except _PROWLARR_SOURCE_ERRORS as e:
            logger.warning("Failed to resolve indexer names to IDs: %s", e)
            return None
        else:
            return ids or None

    def _get_search_indexer_ids(
        self,
        client: ProwlarrClient,
        selected_indexer_ids: list[int] | None,
        categories: list[int] | None,
    ) -> list[int]:
        """Resolve the concrete indexer IDs to query via Torznab."""
        if selected_indexer_ids is not None:
            return selected_indexer_ids

        try:
            enabled_indexers = client.get_enabled_indexers_detailed()
        except _PROWLARR_SOURCE_ERRORS as e:
            logger.warning("Failed to load enabled Prowlarr indexers: %s", e)
            return []

        indexer_ids: list[int] = []
        for indexer in enabled_indexers:
            if not _indexer_supports_search_categories(indexer, categories):
                continue

            indexer_id = indexer.get("id")
            parsed_indexer_id = _coerce_indexer_id(indexer_id)
            if parsed_indexer_id is None:
                continue
            indexer_ids.append(parsed_indexer_id)

        return indexer_ids

    def search(
        self,
        book: BookMetadata,
        plan: ReleaseSearchPlan,
        *,
        expand_search: bool = False,
        content_type: str = "ebook",
    ) -> list[Release]:
        """Search Prowlarr indexers for releases matching the book."""
        client = self._get_client()
        if not client:
            logger.warning("Prowlarr not configured - skipping search")
            return []

        variants = [v for v in plan.title_variants if v.title]

        if not variants and plan.isbn_candidates:
            variants = [
                ReleaseSearchVariant(title=isbn, author="", languages=None)
                for isbn in plan.isbn_candidates
            ]

        if not variants:
            logger.warning("No search query available for book")
            return []

        # Get indexer IDs: prefer plan.indexers (from filter), else use settings
        if plan.indexers:
            indexer_ids = self._resolve_indexer_ids_from_names(client, plan.indexers)
            logger.debug(
                "Using filter-specified indexers: %s -> IDs %s",
                plan.indexers,
                indexer_ids,
            )
        else:
            indexer_ids = self._get_selected_indexer_ids()

        # Get search categories based on content type
        # Audiobooks use 3030 (Audio/Audiobook), ebooks use 7000 (Books)
        search_categories = [3030] if content_type == "audiobook" else [7000]

        # Manual query override should behave like normal Prowlarr searches:
        # - default: search within the content-type categories
        # - expand: rerun without categories
        if plan.manual_query:
            categories = None if expand_search else search_categories
            self.last_search_type = "manual_expanded" if expand_search else "manual_query"
        else:
            categories = None if expand_search else search_categories
            self.last_search_type = "expanded" if expand_search else "categories"

        if plan.manual_query:
            query_type = "manual"
        elif not plan.title_variants and plan.isbn_candidates:
            query_type = "isbn"
        else:
            query_type = "title"

        indexer_desc = f"indexers={indexer_ids}" if indexer_ids else "all enabled indexers"
        if len(variants) == 1:
            logger.debug(
                "Searching Prowlarr: %s='%s', %s, categories=%s",
                query_type,
                variants[0].title,
                indexer_desc,
                categories,
            )
        else:
            logger.debug(
                "Searching Prowlarr: %s (%s variants), %s, categories=%s",
                query_type,
                len(variants),
                indexer_desc,
                categories,
            )

        try:
            auto_expand_enabled = config.get("PROWLARR_AUTO_EXPAND", False)
            search_budget = _search_budget_seconds(client.indexer_timeout)
            deadline = time.monotonic() + search_budget
            try:
                enabled_indexers = client.get_enabled_indexers_detailed(raise_on_error=True)
            except _PROWLARR_REQUEST_ERRORS as e:
                # Prowlarr itself is unreachable. Swallowing this leaves the search
                # with no indexers to query, which the UI renders as "No releases
                # found for this book" - the same lie as a swallowed timeout (#1249).
                msg = f"could not reach Prowlarr: {e}"
                raise SourceUnavailableError(msg) from e
            indexer_ids = _drop_unknown_indexer_ids(indexer_ids, enabled_indexers)
            indexer_priority = _build_indexer_priority(enabled_indexers)
            # Some indexers benefit from title+author queries and extra format detection.
            enriched_indexer_ids = client.get_enriched_indexer_ids(
                restrict_to=indexer_ids, indexers=enabled_indexers
            )
            enriched_indexer_ids_set = set(enriched_indexer_ids)
            indexer_seed_settings = (
                _fetch_indexer_seed_settings(client, indexer_ids)
                if config.get("PROWLARR_USE_SEED_PREFERENCES", False)
                else {}
            )

            def _check_timeout() -> None:
                if time.monotonic() > deadline:
                    _raise_timeout_error(f"Prowlarr search timed out after {int(search_budget)}s")

            # Prowlarr's own search skips an indexer in failure back-off; the
            # per-indexer Torznab endpoint answers 429 instead.
            try:
                disabled_indexers = client.get_disabled_indexers()
            except _PROWLARR_SOURCE_ERRORS as e:
                logger.warning("Failed to load Prowlarr indexer status: %s", e)
                disabled_indexers = {}
            if disabled_indexers:
                logger.info(
                    "Prowlarr: skipping indexer(s) in failure back-off: %s",
                    ", ".join(
                        f"{indexer_id} (till {till})"
                        for indexer_id, till in sorted(disabled_indexers.items())
                    ),
                )

            def search_indexers(query: str, cats: list[int] | None) -> _IndexerSearchOutcome:
                """Search indexers with given categories via Torznab/Newznab.

                Every indexer gets the same title-only query. Enriched indexers used
                to be sent "{title} {author}", but an indexer that ANDs its search
                terms (MyAnonamouse) returns nothing whenever the metadata provider
                spells the author differently to the tracker - "Timothy Ferriss" vs
                "Tim Ferriss" - and the UI reports the book as missing (#1293). The
                author still decides ordering below, where a spelling difference
                costs a release its position rather than its existence.
                """
                outcome = _IndexerSearchOutcome(results=[])
                target_indexer_ids = self._get_search_indexer_ids(client, indexer_ids, cats)
                if not target_indexer_ids:
                    return outcome

                for indexer_id in target_indexer_ids:
                    if indexer_id in disabled_indexers:
                        outcome.skipped += 1
                        continue
                    _check_timeout()
                    outcome.attempted += 1
                    try:
                        raw = client.torznab_search(
                            indexer_id=indexer_id,
                            query=query,
                            categories=cats,
                            search_type="book",
                        )
                    except ProwlarrSearchError as e:
                        # One unreachable indexer must not sink the others, but it
                        # is not "no results" either - record it so the caller can
                        # report a failed search instead of an empty one.
                        outcome.failed += 1
                        outcome.last_error = str(e)
                        continue
                    if raw:
                        outcome.results.extend(raw)

                return outcome

            seen_keys: set[tuple[int | None, str]] = set()
            all_results: list[dict] = []
            attempted_searches = 0
            failed_searches = 0
            skipped_searches = 0
            last_search_error: str | None = None

            for idx, variant in enumerate(variants, start=1):
                _check_timeout()
                query = variant.title

                if len(variants) > 1:
                    logger.debug("Prowlarr query %s/%s: '%s'", idx, len(variants), query)

                outcome = search_indexers(query=query, cats=categories)

                # Auto-expand: if no results with categories and auto-expand enabled, retry without.
                # Only when every indexer actually answered: a failed search says nothing about
                # whether the category filter is what hid the book, and retrying it stacks a second
                # request on an indexer that is still busy solving a Cloudflare challenge (#1249).
                if (
                    not outcome.results
                    and not outcome.failed
                    and outcome.attempted
                    and categories
                    and auto_expand_enabled
                ):
                    _check_timeout()
                    logger.info(
                        "Prowlarr: no results for query '%s' with category filter, auto-expanding search",
                        query,
                    )
                    expanded = search_indexers(query=query, cats=None)
                    outcome.results = expanded.results
                    outcome.attempted += expanded.attempted
                    outcome.failed += expanded.failed
                    outcome.skipped += expanded.skipped
                    outcome.last_error = expanded.last_error or outcome.last_error
                    self.last_search_type = "expanded"

                attempted_searches += outcome.attempted
                failed_searches += outcome.failed
                skipped_searches += outcome.skipped
                last_search_error = outcome.last_error or last_search_error

                for r in outcome.results:
                    key = _result_dedup_key(r)
                    if key is not None:
                        if key in seen_keys:
                            continue
                        seen_keys.add(key)
                    all_results.append(r)

            if failed_searches:
                logger.warning(
                    "Prowlarr: %s of %s indexer searches failed (%s)",
                    failed_searches,
                    attempted_searches,
                    last_search_error,
                )

            if config.get("PROWLARR_COLLAPSE_DUPLICATES", True):
                before_collapse = len(all_results)
                all_results = _collapse_duplicate_indexer_results(all_results, indexer_priority)
                if len(all_results) != before_collapse:
                    logger.debug(
                        "Prowlarr: collapsed %s duplicate result(s) across indexer entries",
                        before_collapse - len(all_results),
                    )

            results: list[Release] = []
            enriched_source_ids: set[str] = set()
            affinity_by_source_id: dict[str, int] = {}
            # A manual query is the user's own words; ranking it against the
            # metadata author would second-guess what they typed.
            wanted_author = "" if plan.manual_query else plan.author

            for raw_result in all_results:
                result_with_seed_settings = _apply_indexer_seed_settings(
                    raw_result, indexer_seed_settings
                )
                idx_id = result_with_seed_settings.get("indexerId")
                idx_id_int = _coerce_indexer_id(idx_id)

                is_enriched = bool(
                    idx_id_int is not None and idx_id_int in enriched_indexer_ids_set
                )
                release = _prowlarr_result_to_release(
                    result_with_seed_settings,
                    content_type,
                    enable_format_detection=is_enriched,
                )
                if idx_id_int is not None and idx_id_int in indexer_priority:
                    release.extra["indexer_priority"] = indexer_priority[idx_id_int]
                results.append(release)
                affinity_by_source_id[release.source_id] = author_affinity(
                    wanted_author, release.extra.get("author")
                )

                if is_enriched:
                    enriched_source_ids.add(release.source_id)

            # Indexer priority first: it is an explicit user preference. Author
            # agreement then orders what one indexer returned, so the editions that
            # match the requested author lead and the rest stay reachable below.
            results.sort(
                key=lambda r: (
                    _release_indexer_rank(r, indexer_priority),
                    affinity_by_source_id.get(r.source_id, AUTHOR_UNKNOWN),
                    0 if r.source_id in enriched_source_ids else 1,
                )
            )

            if results:
                torrent_count = sum(1 for r in results if r.protocol == ReleaseProtocol.TORRENT)
                nzb_count = sum(1 for r in results if r.protocol == ReleaseProtocol.NZB)
                indexers = sorted({r.indexer for r in results if r.indexer})
                indexer_str = ", ".join(indexers) if indexers else "unknown"
                logger.info(
                    "Prowlarr: %s results (%s torrent, %s nzb) from %s",
                    len(results),
                    torrent_count,
                    nzb_count,
                    indexer_str,
                )
            else:
                logger.debug("Prowlarr: no results found")

        except SourceUnavailableError:
            # Already carries its own message for the caller to surface; the blanket
            # handler below would turn it back into a silent empty result.
            raise
        except TimeoutError as e:
            logger.warning("Prowlarr search timed out: %s", e)
            raise
        except Exception:
            logger.exception("Prowlarr search failed")
            return []
        else:
            # An empty list is the UI's "No releases found for this book", so it has
            # to mean the indexers answered and had nothing. When they failed instead,
            # say so rather than blaming the book (#1249).
            if not results and failed_searches:
                msg = (
                    f"{failed_searches} of {attempted_searches} indexer searches failed "
                    f"({last_search_error})"
                )
                raise SourceUnavailableError(msg)
            if not results and not attempted_searches and skipped_searches:
                until = max(disabled_indexers.values(), default="later")
                msg = f"every indexer is disabled by Prowlarr after recent failures (until {until})"
                raise SourceUnavailableError(msg)
            return results

    def is_available(self) -> bool:
        """Check if Prowlarr is enabled and configured."""
        if not config.get("PROWLARR_ENABLED", False):
            return False
        url = normalize_http_url(normalize_optional_text(config.get("PROWLARR_URL", "")))
        api_key = normalize_optional_text(config.get("PROWLARR_API_KEY", "")) or ""
        return bool(url and api_key)
