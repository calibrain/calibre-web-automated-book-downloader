"""Newznab release source - searches a Newznab-compatible indexer for book releases."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING, ClassVar
from urllib.parse import urlparse

if TYPE_CHECKING:
    from shelfmark.core.search_plan import ReleaseSearchPlan
    from shelfmark.metadata_providers import BookMetadata

from shelfmark.core.config import config
from shelfmark.core.logger import setup_logger
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
    register_source,
)
from shelfmark.release_sources.newznab.api import NewznabClient
from shelfmark.release_sources.newznab.cache import cache_release
from shelfmark.release_sources.prowlarr.source import (
    PROWLARR_SEARCH_TIMEOUT_SECONDS as _SEARCH_TIMEOUT,
)

# Re-use the Prowlarr source helpers — they operate on generic result dicts.
from shelfmark.release_sources.prowlarr.source import (
    _detect_content_type_from_categories,
    parse_size,
)

logger = setup_logger(__name__)

# Standard Newznab category IDs, used when the indexer's categories aren't configured.
_DEFAULT_AUDIOBOOK_CATS = [3030]
_DEFAULT_BOOK_CATS = [7000]

# Reuse the same timeout constant as Prowlarr.
NEWZNAB_SEARCH_TIMEOUT_SECONDS = _SEARCH_TIMEOUT


@dataclass(frozen=True)
class _NamedClient:
    """A configured Newznab connection and its stable cache namespace."""

    name: str
    connection_id: str
    client: NewznabClient


def _parse_indexer_rows(raw: object) -> list[tuple[str, str, str]]:
    """Normalize structured Newznab indexer settings.

    Invalid/incomplete rows are ignored so one partially edited row cannot disable
    the other configured indexers.
    """
    if not isinstance(raw, list):
        return []

    indexers: list[tuple[str, str, str]] = []
    seen_connections: set[tuple[str, str]] = set()
    for row in raw:
        if not isinstance(row, dict):
            continue
        raw_url = str(row.get("url") or "").strip()
        url = normalize_http_url(raw_url)
        if not url:
            if raw_url:
                logger.warning("Newznab: ignoring indexer row with invalid URL '%s'", raw_url)
            continue

        api_key = str(row.get("api_key") or "").strip()
        connection_key = (url, api_key)
        if connection_key in seen_connections:
            continue
        seen_connections.add(connection_key)

        configured_name = str(row.get("name") or "").strip()
        hostname = urlparse(url).hostname or ""
        name = configured_name or hostname or "Newznab"
        indexers.append((name, url, api_key))

    return indexers


def _parse_category_ids(raw: object) -> list[int]:
    """Parse a configured category setting into Newznab category IDs.

    Accepts a list of values or a comma/whitespace separated string. Entries that
    aren't positive integers are skipped, and duplicates are dropped.
    """
    if raw is None:
        return []

    values = list(raw) if isinstance(raw, (list, tuple)) else [raw]

    category_ids: list[int] = []
    for value in values:
        for token in re.split(r"[,\s]+", str(value).strip()):
            if not token:
                continue
            try:
                category_id = int(token)
            except ValueError:
                logger.warning("Newznab: ignoring invalid category ID '%s'", token)
                continue
            if category_id > 0 and category_id not in category_ids:
                category_ids.append(category_id)

    return category_ids


def _configured_categories(content_type: str) -> list[int]:
    """Return the categories to search for a content type, falling back to defaults."""
    if content_type == "audiobook":
        key, defaults = "NEWZNAB_AUDIOBOOK_CATEGORIES", _DEFAULT_AUDIOBOOK_CATS
    else:
        key, defaults = "NEWZNAB_EBOOK_CATEGORIES", _DEFAULT_BOOK_CATS

    return _parse_category_ids(config.get(key, None)) or list(defaults)


def _result_category_ids(categories: object) -> set[int]:
    """Extract numeric category IDs from a result's categories field."""
    if not isinstance(categories, (list, tuple)):
        return set()

    category_ids: set[int] = set()
    for cat in categories:
        raw = cat.get("id") if isinstance(cat, dict) else cat
        try:
            category_ids.add(int(raw))  # type: ignore[arg-type]
        except TypeError, ValueError:
            continue
    return category_ids


def _resolve_content_type(
    categories: object,
    content_type: str,
    searched_categories: list[int] | None,
) -> str:
    """Resolve a result's content type, honouring custom indexer categories.

    Indexers using non-standard IDs (e.g. 7100 for ebooks) fall outside the standard
    ranges, so trust the searched content type when the result carries a category we
    explicitly asked for.
    """
    category_list = list(categories) if isinstance(categories, (list, tuple)) else []
    detected = _detect_content_type_from_categories(category_list, content_type)
    if (
        detected == "other"
        and searched_categories
        and _result_category_ids(category_list) & set(searched_categories)
    ):
        return "audiobook" if content_type == "audiobook" else "book"
    return detected


def _newznab_result_to_release(
    result: dict,
    content_type: str = "ebook",
    searched_categories: list[int] | None = None,
) -> Release:
    """Convert a parsed Newznab XML result dict to a Release object."""
    raw_title = result.get("title", "Unknown")
    size_bytes = result.get("size")
    indexer = result.get("indexer") or "Newznab"
    categories = result.get("categories", [])

    protocol_str = str(result.get("protocol", "usenet")).lower()
    protocol = ReleaseProtocol.TORRENT if protocol_str == "torrent" else ReleaseProtocol.NZB

    seeders = result.get("seeders")
    leechers = result.get("leechers")
    is_torrent = protocol == ReleaseProtocol.TORRENT

    peers_display = (
        f"{seeders} / {leechers}"
        if is_torrent and seeders is not None and leechers is not None
        else None
    )

    # Namespace IDs from named connections so identical GUIDs returned by two
    # indexers cannot overwrite one another in the private release cache.
    raw_source_id = result.get("guid") or f"newznab:{hash(raw_title)}"
    connection_id = str(result.get("_newznab_connection_id") or "").strip()
    source_id = f"newznab:{connection_id}:{raw_source_id}" if connection_id else raw_source_id

    # Cache the raw result for the handler
    cache_release(source_id, result)

    # Freeleech / VIP detection
    raw_indexer_flags = result.get("indexerFlags") or []
    indexer_flags: list[str] = []
    seen: set = set()

    def add_flag(flag: object) -> None:
        if flag is None:
            return
        s = str(flag).strip()
        if s and s.lower() not in seen:
            seen.add(s.lower())
            indexer_flags.append(s)

    if isinstance(raw_indexer_flags, list):
        for f in raw_indexer_flags:
            add_flag(f)
    elif raw_indexer_flags:
        add_flag(raw_indexer_flags)

    download_volume_factor = result.get("downloadVolumeFactor")
    is_freeleech = False
    try:
        if download_volume_factor is not None and float(download_volume_factor) == 0.0:
            is_freeleech = True
    except TypeError, ValueError:
        pass

    if any(f.lower() in {"freeleech", "fl"} for f in indexer_flags):
        is_freeleech = True

    is_vip = "[vip]" in str(raw_title).lower()
    if is_vip:
        add_flag("VIP")
    if is_freeleech:
        add_flag("FreeLeech")

    info_url = result.get("infoUrl") or result.get("guid")

    return Release(
        source="newznab",
        source_id=source_id,
        title=raw_title,
        format=None,
        language=None,
        size=parse_size(size_bytes),
        size_bytes=size_bytes,
        download_url=None,
        info_url=info_url,
        protocol=protocol,
        indexer=indexer,
        seeders=seeders if is_torrent else None,
        peers=peers_display,
        content_type=_resolve_content_type(categories, content_type, searched_categories),
        extra={
            "publish_date": result.get("publishDate"),
            "categories": categories,
            "indexer_flags": indexer_flags,
            "vip": is_vip,
            "freeleech": is_freeleech,
            "download_volume_factor": download_volume_factor,
            "upload_volume_factor": result.get("uploadVolumeFactor"),
            "minimum_ratio": result.get("minimumRatio"),
            "minimum_seed_time": result.get("minimumSeedTime"),
            "info_hash": result.get("infoHash"),
            "files": result.get("files"),
            "grabs": result.get("grabs"),
            "author": result.get("author"),
            "book_title": result.get("bookTitle"),
        },
    )


@register_source("newznab")
class NewznabSource(ReleaseSource):
    """Release source for any Newznab-compatible indexer or aggregator."""

    name = "newznab"
    display_name = "Newznab"
    supported_content_types: ClassVar[list[str]] = ["ebook", "audiobook"]

    def get_column_config(self) -> ReleaseColumnConfig:
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
            grid_template="minmax(0,2fr) minmax(140px,1fr) 50px 80px",
            leading_cell=LeadingCellConfig(type=LeadingCellType.NONE),
            supported_filters=["indexer"],
        )

    def _get_client(self) -> NewznabClient | None:
        """Build the legacy single-indexer client."""
        raw_url = str(config.get("NEWZNAB_URL", "") or "")
        api_key = str(config.get("NEWZNAB_API_KEY", "") or "")

        if not raw_url:
            return None

        url = normalize_http_url(raw_url)
        if not url:
            return None

        return NewznabClient(url, api_key or "")

    def _get_clients(self) -> list[_NamedClient]:
        """Build named clients, falling back to the legacy single connection."""
        configured = _parse_indexer_rows(config.get("NEWZNAB_INDEXERS", []))
        if configured:
            clients: list[_NamedClient] = []
            for name, url, api_key in configured:
                digest = sha256(f"{name}\0{url}\0{api_key}".encode()).hexdigest()[:16]
                clients.append(
                    _NamedClient(
                        name=name,
                        connection_id=digest,
                        client=NewznabClient(url, api_key),
                    )
                )
            return clients

        legacy_client = self._get_client()
        if legacy_client is None:
            return []
        legacy_name = str(config.get("NEWZNAB_NAME", "") or "").strip() or "Newznab"
        return [_NamedClient(name=legacy_name, connection_id="legacy", client=legacy_client)]

    def search(
        self,
        book: BookMetadata,
        plan: ReleaseSearchPlan,
        *,
        expand_search: bool = False,
        content_type: str = "ebook",
    ) -> list[Release]:
        """Search the Newznab indexer for releases matching the book."""
        clients = self._get_clients()
        if not clients:
            logger.warning("Newznab not configured - skipping search")
            return []

        queries = [v.title for v in plan.title_variants if v.title]
        queries = [q for q in queries if q]

        if not queries and plan.isbn_candidates:
            queries = list(plan.isbn_candidates)

        if not queries:
            logger.warning("Newznab: no search query available")
            return []

        # Category selection — omit categories when expanding search
        categories = None if expand_search else _configured_categories(content_type)

        auto_expand = config.get("NEWZNAB_AUTO_EXPAND", False)
        deadline = time.monotonic() + NEWZNAB_SEARCH_TIMEOUT_SECONDS

        def _check_timeout() -> None:
            if time.monotonic() > deadline:
                raise TimeoutError(
                    f"Newznab search timed out after {int(NEWZNAB_SEARCH_TIMEOUT_SECONDS)}s"
                )

        seen_keys: set = set()
        all_results: list[dict] = []

        try:
            for connection in clients:
                try:
                    for idx, query in enumerate(queries, start=1):
                        _check_timeout()
                        if len(queries) > 1:
                            logger.debug(
                                "Newznab [%s] query %d/%d: '%s'",
                                connection.name,
                                idx,
                                len(queries),
                                query,
                            )

                        raw = connection.client.search(query=query, categories=categories)

                        # Auto-expand: retry without category filter if no results
                        if not raw and categories and auto_expand:
                            _check_timeout()
                            logger.info(
                                "Newznab [%s]: no results for '%s' with category filter, "
                                "auto-expanding",
                                connection.name,
                                query,
                            )
                            raw = connection.client.search(query=query, categories=None)

                        for raw_result in raw:
                            r = dict(raw_result)
                            # Aggregators can identify the underlying indexer. Plain feeds
                            # generally cannot, so use the user-configured connection name.
                            r["indexer"] = r.get("indexer") or connection.name
                            r["_newznab_connection_id"] = connection.connection_id
                            key = (
                                connection.connection_id,
                                r.get("guid")
                                or r.get("downloadUrl")
                                or f"{r.get('indexer')}:{r.get('title')}",
                            )
                            if key in seen_keys:
                                continue
                            seen_keys.add(key)
                            all_results.append(r)
                except TimeoutError:
                    raise
                except Exception:
                    logger.exception("Newznab search failed for %s", connection.name)

        except TimeoutError as e:
            logger.warning("Newznab search timed out: %s", e)

        results = [_newznab_result_to_release(r, content_type, categories) for r in all_results]
        if plan.indexers:
            selected_indexers = set(plan.indexers)
            results = [r for r in results if r.indexer in selected_indexers]

        if results:
            nzb_count = sum(1 for r in results if r.protocol == ReleaseProtocol.NZB)
            torrent_count = sum(1 for r in results if r.protocol == ReleaseProtocol.TORRENT)
            indexers = sorted({r.indexer for r in results if r.indexer})
            indexer_str = ", ".join(indexers) if indexers else "unknown"
            logger.info(
                "Newznab: %d results (%d nzb, %d torrent) from %s",
                len(results),
                nzb_count,
                torrent_count,
                indexer_str,
            )
        else:
            logger.debug("Newznab: no results found")

        return results

    def is_available(self) -> bool:
        if not config.get("NEWZNAB_ENABLED", False):
            return False
        if _parse_indexer_rows(config.get("NEWZNAB_INDEXERS", [])):
            return True
        url = normalize_http_url(str(config.get("NEWZNAB_URL", "") or ""))
        return bool(url)
