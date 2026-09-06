"""Direct Download search and release-source integration."""

from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

import requests

from shelfmark.core.logger import setup_logger
from shelfmark.core.utils import get_aa_content_type_dir
from shelfmark.core.utils import is_audiobook as check_audiobook
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
    SourceUnavailableError,
    register_source,
)
from shelfmark.release_sources.direct_download import registry
from shelfmark.release_sources.direct_download.common import (
    DirectDownloadUnavailableError,
    RecordLookupProvider,
)

if TYPE_CHECKING:
    from pathlib import Path

    from shelfmark.core.models import DownloadTask
    from shelfmark.core.search_plan import ReleaseSearchPlan
    from shelfmark.metadata_providers import BookMetadata

logger = setup_logger(__name__)


def _browse_record_to_release(record: BrowseRecord) -> Release:
    """Convert a browse record to a Release object.

    This bridges the direct source's browse data to the generic release model.
    """
    provider = registry.provider_for_record(record)
    provider_id = provider.id if provider is not None else None
    return Release(
        source=record.source,
        source_id=record.id,
        title=record.title,
        format=record.format,
        language=record.language,  # Top-level language for filtering
        size=record.size,
        download_url=record.source_url
        or (record.download_urls[0] if record.download_urls else None),
        info_url=record.source_url,
        protocol=ReleaseProtocol.HTTP,
        indexer="Direct Download",
        content_type=record.content,  # Preserve content type from source
        extra={
            "author": record.author,
            "publisher": record.publisher,
            "year": record.year,
            "language": record.language,
            "preview": record.preview,
            "description": record.description,
            "download_urls": record.download_urls,
            "info": record.info,
            "direct_download_provider": provider_id,
            # Kept for older frontends and persisted request payloads.
            "web_provider": provider_id if provider_id != "annas_archive" else None,
        },
    )


@register_source("direct_download")
class DirectDownloadSource(ReleaseSource):
    """Direct download source - searches web sources for books.

    This wraps the search_books() functionality to provide releases
    via the plugin interface.
    """

    name = "direct_download"
    display_name = "Direct Download"
    supported_content_types: ClassVar[list[str]] = ["ebook"]  # Direct downloads only support ebooks

    def __init__(self) -> None:
        """Initialize per-instance search state for direct downloads."""
        self._providers = registry.create_providers()

    @property
    def last_search_type(self) -> str:
        """Returns the search type used in the last search() call."""
        provider = registry.provider_by_id("annas_archive", self._providers)
        return str(getattr(provider, "last_search_type", "title_author"))

    def get_column_config(self) -> ReleaseColumnConfig:
        """Column configuration for Direct Download source.

        Shows language, format, and size badges for each release.
        Language is hidden on mobile; format and size are shown.
        """
        return ReleaseColumnConfig(
            columns=[
                ColumnSchema(
                    key="extra.language",
                    label="Language",
                    render_type=ColumnRenderType.BADGE,
                    align=ColumnAlign.CENTER,
                    width="60px",
                    hide_mobile=False,  # Language shown on mobile
                    color_hint=ColumnColorHint(type="map", value="language"),
                    uppercase=True,
                ),
                ColumnSchema(
                    key="format",
                    label="Format",
                    render_type=ColumnRenderType.BADGE,
                    align=ColumnAlign.CENTER,
                    width="80px",
                    hide_mobile=False,  # Format shown on mobile
                    color_hint=ColumnColorHint(type="map", value="format"),
                    uppercase=True,
                ),
                ColumnSchema(
                    key="size",
                    label="Size",
                    render_type=ColumnRenderType.SIZE,
                    align=ColumnAlign.CENTER,
                    width="80px",
                    hide_mobile=False,  # Size shown on mobile
                ),
            ],
            grid_template="minmax(0,2fr) 60px 80px 80px",
            supported_filters=["format", "language"],  # AA has reliable language metadata
        )

    def get_record(
        self,
        record_id: str,
        *,
        fetch_download_count: bool = True,
    ) -> BrowseRecord | None:
        """Resolve a direct-download record for direct-mode info/download flows."""
        provider = registry.provider_for_record_id(record_id, self._providers)
        if provider is None or not isinstance(provider, RecordLookupProvider):
            return None
        native_id = record_id.partition(":")[2] or record_id
        return provider.get_record(native_id, fetch_download_count=fetch_download_count)

    def search_results_are_releases(self) -> bool:
        """Direct search results already represent concrete downloadable releases."""
        return True

    def get_destination_override(self, task: DownloadTask) -> Path | None:
        """Apply Anna's Archive content-type routing when configured."""
        if check_audiobook(task.content_type):
            return None
        return get_aa_content_type_dir(task.content_type)

    def search(
        self,
        book: BookMetadata,
        plan: ReleaseSearchPlan,
        *,
        expand_search: bool = False,
        content_type: str = "ebook",
    ) -> list[Release]:
        """Search every enabled provider through the shared provider lifecycle."""
        unavailable_reason = registry.get_unavailable_reason(self._providers)
        if unavailable_reason:
            raise DirectDownloadUnavailableError(unavailable_reason)

        releases: list[Release] = []
        unavailable_errors: list[SourceUnavailableError] = []
        for provider in registry.enabled_providers(self._providers):
            try:
                records = provider.search(
                    book,
                    plan,
                    expand_search=expand_search,
                    content_type=content_type,
                )
            except SourceUnavailableError as exc:
                unavailable_errors.append(exc)
                continue
            except (
                RuntimeError,
                TypeError,
                ValueError,
                requests.exceptions.RequestException,
            ) as exc:
                logger.warning("%s search failed: %s", provider.display_name, exc)
                continue
            releases.extend(_browse_record_to_release(record) for record in records)

        if unavailable_errors and not releases:
            raise unavailable_errors[0]
        return releases

    def is_available(self) -> bool:
        """Check if Direct Download has been explicitly enabled and configured."""
        return registry.get_unavailable_reason(self._providers) is None
