"""Libgen release source - searches the libgen catalogue directly.

Anna's Archive is shelfmark's only other web search source, and libgen appears there
purely as a download mirror keyed by an AA md5. This source searches libgen's own
catalogue, which surfaces content AA does not index -- most visibly CBZ/CBR comics and
manga volumes. Downloads reuse the existing ``ads.php?md5=`` resolution (see handler.py).
"""

from typing import TYPE_CHECKING, ClassVar

from shelfmark.core import mirrors
from shelfmark.core.config import config
from shelfmark.core.logger import setup_logger
from shelfmark.release_sources import (
    BrowseRecord,
    ColumnAlign,
    ColumnColorHint,
    ColumnRenderType,
    ColumnSchema,
    Release,
    ReleaseColumnConfig,
    ReleaseProtocol,
    ReleaseSource,
    register_source,
)
from shelfmark.release_sources.libgen import scraper

if TYPE_CHECKING:
    from shelfmark.core.models import DownloadTask  # noqa: F401
    from shelfmark.core.search_plan import ReleaseSearchPlan
    from shelfmark.metadata_providers import BookMetadata

logger = setup_logger(__name__)

_DEFAULT_MAX_RESULTS = 25


def _coerce_positive_int(value: object, default: int) -> int:
    """Return a positive integer config value or the provided default."""
    if isinstance(value, bool):
        return default
    if isinstance(value, int) and value > 0:
        return value
    return default


def _build_query_candidates(plan: ReleaseSearchPlan, book: BookMetadata) -> list[str]:
    """Build ordered, de-duplicated search queries from the plan (mirrors AudiobookBay)."""
    candidates: list[str] = []
    if plan.manual_query:
        candidates.append(plan.manual_query.strip())
    elif plan.title_variants:
        variant = plan.title_variants[0]
        combined = f"{variant.title} {variant.author}".strip()
        title_only = (variant.title or "").strip()
        if combined:
            candidates.append(combined)
        if title_only and title_only.lower() != combined.lower():
            candidates.append(title_only)
    elif book.title:
        candidates.append(book.title.strip())

    deduped: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        normalized = candidate.strip()
        if not normalized or normalized.lower() in seen:
            continue
        seen.add(normalized.lower())
        deduped.append(normalized)
    return deduped


@register_source("libgen")
class LibgenSource(ReleaseSource):
    """Release source that searches the libgen catalogue for downloadable files."""

    name = "libgen"
    display_name = "Libgen"
    supported_content_types: ClassVar[list[str]] = ["ebook"]  # incl. comics/manga (cbz/cbr)

    def is_available(self) -> bool:
        """Available only when explicitly enabled and libgen mirrors are configured.

        ``is True`` rather than ``bool(...)`` matches the AudiobookBay idiom and avoids a
        truthy string ever enabling network egress to an unmoderated site.
        """
        return (
            config.get("LIBGEN_SEARCH_ENABLED", False) is True
            and mirrors.has_libgen_mirror_configuration()
        )

    def search(
        self,
        book: BookMetadata,
        plan: ReleaseSearchPlan,
        *,
        expand_search: bool = False,
        content_type: str = "ebook",
    ) -> list[Release]:
        """Search libgen for releases of a book."""
        if content_type != "ebook":
            return []
        if not self.is_available():
            return []

        queries = _build_query_candidates(plan, book)
        if not queries:
            return []
        max_results = _coerce_positive_int(
            config.get("LIBGEN_SEARCH_MAX_RESULTS", _DEFAULT_MAX_RESULTS), _DEFAULT_MAX_RESULTS
        )
        mirror_list = mirrors.get_libgen_mirrors()

        # One search_libgen call per candidate; it already retries every mirror internally.
        # Worst case (all mirrors dead) stays within the shared search deadline.
        for query in queries:
            logger.info("Searching Libgen for: %s", query)
            records = scraper.search_libgen(query, mirror_list, max_results=max_results)
            if records:
                return [self._record_to_release(record) for record in records]
        return []

    def _record_to_release(self, record: BrowseRecord) -> Release:
        """Normalize a libgen catalogue record into a Release.

        ``source_id`` is namespaced ``libgen:<md5>`` so the download queue key never
        collides with a direct_download release for the same md5 (Anna's Archive heavily
        indexes libgen, so the same md5 routinely appears from both sources). The handler
        strips the prefix back to the bare md5.
        """
        return Release(
            source="libgen",
            source_id=f"libgen:{record.id}",
            title=record.title,
            format=record.format,
            language=record.language,
            size=record.size,
            download_url=None,  # handler builds ads.php?md5= from the md5
            info_url=record.source_url,
            protocol=ReleaseProtocol.HTTP,
            indexer="Libgen",
            content_type="ebook",
            extra={
                "author": record.author,
                "year": record.year,
                "md5": record.id,
                "language": record.language,
            },
        )

    def search_results_are_releases(self) -> bool:
        """Libgen search rows are concrete, directly downloadable releases."""
        return True

    def get_record(
        self,
        record_id: str,
        *,
        fetch_download_count: bool = True,
    ) -> BrowseRecord | None:
        """Resolve a libgen record by (possibly prefixed) md5, or None if not found."""
        md5 = record_id.split(":", 1)[-1].lower()
        return scraper.fetch_record_by_md5(md5, mirrors.get_libgen_mirrors())

    def get_column_config(self) -> ReleaseColumnConfig:
        """Language, format and size badges -- same layout as Direct Download."""
        return ReleaseColumnConfig(
            columns=[
                ColumnSchema(
                    key="extra.language",
                    label="Language",
                    render_type=ColumnRenderType.BADGE,
                    align=ColumnAlign.CENTER,
                    width="60px",
                    color_hint=ColumnColorHint(type="map", value="language"),
                    uppercase=True,
                ),
                ColumnSchema(
                    key="format",
                    label="Format",
                    render_type=ColumnRenderType.BADGE,
                    align=ColumnAlign.CENTER,
                    width="80px",
                    color_hint=ColumnColorHint(type="map", value="format"),
                    uppercase=True,
                ),
                ColumnSchema(
                    key="size",
                    label="Size",
                    render_type=ColumnRenderType.SIZE,
                    align=ColumnAlign.CENTER,
                    width="80px",
                ),
            ],
            grid_template="minmax(0,2fr) 60px 80px 80px",
            supported_filters=["format", "language"],
        )
