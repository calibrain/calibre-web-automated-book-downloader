"""Jackett release source - searches Jackett's Torznab indexers for book releases."""

import time
from typing import TYPE_CHECKING, ClassVar

import requests

if TYPE_CHECKING:
    from shelfmark.core.search_plan import ReleaseSearchPlan
    from shelfmark.metadata_providers import BookMetadata

from shelfmark.core.author_match import AUTHOR_UNKNOWN, author_affinity
from shelfmark.core.config import config
from shelfmark.core.logger import setup_logger
from shelfmark.core.request_helpers import normalize_optional_text
from shelfmark.core.search_plan import ReleaseSearchVariant
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
    ReleaseSource,
    SortOption,
    SourceUnavailableError,
    register_source,
)
from shelfmark.release_sources.jackett.api import (
    ALL_INDEXERS_ID,
    JackettClient,
    JackettSearchError,
)
from shelfmark.release_sources.prowlarr.source import (
    _IndexerSearchOutcome,
    _is_over_size,
    _max_release_bytes,
    _prowlarr_result_to_release,
    _result_dedup_key,
    _search_budget_seconds,
    _unsupported_title_extension,
)

logger = setup_logger(__name__)

_JACKETT_SOURCE_ERRORS = (
    AttributeError,
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
    requests.exceptions.RequestException,
)


def _jackett_source_id(result: dict) -> str:
    """Build a Release.source_id that is unique across Jackett's indexers."""
    base = (
        result.get("guid")
        or result.get("downloadUrl")
        or f"{result.get('indexer', 'unknown')}:{result.get('title', 'Unknown')}"
    )
    indexer_key = normalize_optional_text(result.get("indexerKey"))
    prefix = f"jackett:{indexer_key}" if indexer_key else "jackett"
    return f"{prefix}:{base}"


def _with_magnet_url(result: dict) -> dict:
    """Surface Jackett's ``magneturl`` Torznab attribute where the handler looks for it."""
    attrs = result.get("torznabAttrs")
    magnet = normalize_optional_text(attrs.get("magneturl")) if isinstance(attrs, dict) else None
    if not magnet or result.get("magnetUrl"):
        return result
    return {**result, "magnetUrl": magnet}


@register_source("jackett")
class JackettSource(ReleaseSource):
    """Jackett release source for ebooks and audiobooks."""

    name = "jackett"
    display_name = "Jackett"
    supported_content_types: ClassVar[list[str]] = ["ebook", "audiobook"]

    def __init__(self) -> None:
        """Initialize per-instance search state."""
        self.last_search_type: str | None = None

    def _get_client(self) -> JackettClient | None:
        """Get a configured Jackett client or None if not configured."""
        raw_url = normalize_optional_text(config.get("JACKETT_URL", "")) or ""
        api_key = normalize_optional_text(config.get("JACKETT_API_KEY", "")) or ""
        if not raw_url or not api_key:
            return None

        url = normalize_http_url(raw_url)
        if not url:
            return None

        return JackettClient(url, api_key)

    @staticmethod
    def _get_selected_indexer_ids() -> list[str] | None:
        """Indexer ids chosen in settings, or None to search every configured indexer."""
        selected = config.get("JACKETT_INDEXERS", "")
        if isinstance(selected, str):
            selected = selected.split(",")
        if not isinstance(selected, (list, tuple)):
            return None
        ids = [text for item in selected if (text := normalize_optional_text(item))]
        return ids or None

    def get_column_config(self) -> ReleaseColumnConfig:
        """Column configuration for Jackett releases."""
        available_indexers: list[str] | None = None
        default_indexers: list[str] | None = None
        client = self._get_client()
        if client:
            try:
                indexers = client.get_indexers(raise_on_error=True)
                selected_ids = self._get_selected_indexer_ids()
                available_indexers = sorted(idx["name"] for idx in indexers) or None
                if selected_ids:
                    default_indexers = (
                        sorted(idx["name"] for idx in indexers if idx["id"] in selected_ids) or None
                    )
            except _JACKETT_SOURCE_ERRORS as e:
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
            extra_sort_options=[SortOption(label="Peers", sort_key="seeders")],
            grid_template="minmax(0,2fr) minmax(140px,1fr) 50px 80px",
            leading_cell=LeadingCellConfig(type=LeadingCellType.NONE),
            available_indexers=available_indexers,
            default_indexers=default_indexers,
            supported_filters=["indexer"],
        )

    def _resolve_indexer_ids(
        self, client: JackettClient, plan: ReleaseSearchPlan
    ) -> list[str] | None:
        """Indexer ids to query: the filter's names, else the settings selection.

        None means "ask Jackett's ``all`` aggregate", which is one request instead of one
        per indexer. A name that no longer matches a configured indexer is dropped; if an
        explicit filter matches nothing, an empty list is returned (search nothing).
        """
        if not plan.indexers:
            return self._get_selected_indexer_ids()

        try:
            indexers = client.get_indexers(raise_on_error=True)
        except _JACKETT_SOURCE_ERRORS as e:
            msg = f"could not reach Jackett: {e}"
            raise SourceUnavailableError(msg) from e
        by_name = {idx["name"]: idx["id"] for idx in indexers}
        return [by_name[name] for name in plan.indexers if name in by_name]

    def search(
        self,
        book: BookMetadata,
        plan: ReleaseSearchPlan,
        *,
        expand_search: bool = False,
        content_type: str = "ebook",
    ) -> list[Release]:
        """Search Jackett for releases matching the book."""
        client = self._get_client()
        if not client:
            logger.warning("Jackett not configured - skipping search")
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

        indexer_ids = self._resolve_indexer_ids(client, plan)
        if indexer_ids is not None and not indexer_ids:
            logger.warning("Jackett indexer filter matched no configured indexers")
            return []
        targets = indexer_ids or [ALL_INDEXERS_ID]

        # Audiobooks use 3030 (Audio/Audiobook), ebooks use 7000 (Books)
        search_categories = [3030] if content_type == "audiobook" else [7000]
        categories = None if expand_search else search_categories
        if plan.manual_query:
            self.last_search_type = "manual_expanded" if expand_search else "manual_query"
        else:
            self.last_search_type = "expanded" if expand_search else "categories"

        logger.debug(
            "Searching Jackett: %s variant(s), indexers=%s, categories=%s",
            len(variants),
            targets,
            categories,
        )

        auto_expand_enabled = config.get("JACKETT_AUTO_EXPAND", False)
        search_budget = _search_budget_seconds(client.indexer_timeout)
        deadline = time.monotonic() + search_budget

        def _check_timeout() -> None:
            if time.monotonic() > deadline:
                msg = f"Jackett search timed out after {int(search_budget)}s"
                raise TimeoutError(msg)

        def search_indexers(query: str, cats: list[int] | None) -> _IndexerSearchOutcome:
            outcome = _IndexerSearchOutcome(results=[])
            for indexer_id in targets:
                _check_timeout()
                outcome.attempted += 1
                try:
                    outcome.results.extend(
                        client.torznab_search(
                            indexer_id=indexer_id,
                            query=query,
                            categories=cats,
                            search_type="book",
                        )
                    )
                except JackettSearchError as e:
                    # One failing indexer must not sink the others, but it is not
                    # "no results" either.
                    outcome.failed += 1
                    outcome.last_error = str(e)
            return outcome

        seen_keys: set[tuple[int | None, str]] = set()
        all_results: list[dict] = []
        attempted = failed = 0
        last_error: str | None = None

        try:
            for idx, variant in enumerate(variants, start=1):
                _check_timeout()
                query = variant.title
                if len(variants) > 1:
                    logger.debug("Jackett query %s/%s: '%s'", idx, len(variants), query)

                outcome = search_indexers(query, categories)

                # Only auto-expand when every request answered: a failure says nothing
                # about whether the category filter hid the book.
                if (
                    not outcome.results
                    and not outcome.failed
                    and categories
                    and auto_expand_enabled
                ):
                    _check_timeout()
                    logger.info(
                        "Jackett: no results for query '%s' with category filter, auto-expanding",
                        query,
                    )
                    expanded = search_indexers(query, None)
                    outcome.results = expanded.results
                    outcome.attempted += expanded.attempted
                    outcome.failed += expanded.failed
                    outcome.last_error = expanded.last_error or outcome.last_error
                    self.last_search_type = "expanded"

                attempted += outcome.attempted
                failed += outcome.failed
                last_error = outcome.last_error or last_error

                for raw in outcome.results:
                    key = _result_dedup_key(raw)
                    if key is not None:
                        if key in seen_keys:
                            continue
                        seen_keys.add(key)
                    all_results.append(raw)
        except TimeoutError as e:
            logger.warning("Jackett search timed out: %s", e)
            raise

        if failed:
            logger.warning("Jackett: %s of %s searches failed (%s)", failed, attempted, last_error)

        wanted_author = "" if plan.manual_query else plan.author
        hide_unsupported = config.get("JACKETT_HIDE_UNSUPPORTED_FORMATS", True)
        max_bytes = _max_release_bytes(content_type, "JACKETT_EBOOK_MAX_SIZE_MB")

        results: list[Release] = []
        affinity_by_source_id: dict[str, int] = {}
        for raw in all_results:
            if _is_over_size(raw, max_bytes):
                continue
            result = _with_magnet_url(raw)
            release = _prowlarr_result_to_release(
                result,
                content_type,
                source="jackett",
                source_id=_jackett_source_id(result),
            )
            if hide_unsupported and _unsupported_title_extension(release.title, content_type):
                continue
            results.append(release)
            affinity_by_source_id[release.source_id] = author_affinity(
                wanted_author, release.extra.get("author")
            )

        results.sort(key=lambda r: affinity_by_source_id.get(r.source_id, AUTHOR_UNKNOWN))

        if results:
            indexers = ", ".join(sorted({r.indexer for r in results if r.indexer})) or "unknown"
            logger.info("Jackett: %s results from %s", len(results), indexers)
            return results

        logger.debug("Jackett: no results found")
        # An empty list is the UI's "No releases found for this book", so it must
        # mean Jackett answered and had nothing, not that the searches failed.
        if failed:
            msg = f"{failed} of {attempted} Jackett searches failed ({last_error})"
            raise SourceUnavailableError(msg)
        return results

    def is_available(self) -> bool:
        """Check if Jackett is enabled and configured."""
        if not config.get("JACKETT_ENABLED", False):
            return False
        url = normalize_http_url(normalize_optional_text(config.get("JACKETT_URL", "")))
        api_key = normalize_optional_text(config.get("JACKETT_API_KEY", "")) or ""
        return bool(url and api_key)
