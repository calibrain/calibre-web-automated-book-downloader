"""Provider composition and dispatch for the Direct Download release source."""

import re
from typing import TYPE_CHECKING

from shelfmark.core.config import config
from shelfmark.release_sources.direct_download.annas_archive import AnnasArchiveProvider
from shelfmark.release_sources.direct_download.oceanofpdf import OceanofPDFProvider

if TYPE_CHECKING:
    from collections.abc import Sequence

    from shelfmark.release_sources import BrowseRecord
    from shelfmark.release_sources.direct_download.common import DirectDownloadProvider


PROVIDER_TYPES = (AnnasArchiveProvider, OceanofPDFProvider)
_AA_MD5_PATTERN = re.compile(r"^[0-9a-f]{32}$", re.IGNORECASE)


def create_providers() -> tuple[DirectDownloadProvider, ...]:
    """Create request-local providers so mutable search state is not shared."""
    return tuple(provider_type() for provider_type in PROVIDER_TYPES)


def enabled_providers(
    providers: Sequence[DirectDownloadProvider] | None = None,
) -> tuple[DirectDownloadProvider, ...]:
    if not config.get("DIRECT_DOWNLOAD_ENABLED", False):
        return ()
    candidates = providers if providers is not None else create_providers()
    enabled = [provider for provider in candidates if provider.is_enabled()]
    priority = config.get("SOURCE_PRIORITY", [])
    if not isinstance(priority, list):
        return tuple(enabled)

    positions = {
        item["id"]: index
        for index, item in enumerate(priority)
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    fallback_position = len(positions)

    def _position(provider: DirectDownloadProvider) -> int:
        if provider.id == "oceanofpdf":
            return positions.get("oceanofpdf", fallback_position)
        if provider.id == "annas_archive":
            aa_positions = [
                position for source_id, position in positions.items() if source_id != "oceanofpdf"
            ]
            return min(aa_positions, default=fallback_position)
        return fallback_position

    return tuple(sorted(enabled, key=_position))


def get_unavailable_reason(
    providers: Sequence[DirectDownloadProvider] | None = None,
) -> str | None:
    if not config.get("DIRECT_DOWNLOAD_ENABLED", False):
        return "Direct Download is disabled. Enable the source in Settings."
    if not enabled_providers(providers):
        return (
            "Direct Download is not configured. Enable and configure at least one "
            "download provider in Settings."
        )
    return None


def provider_by_id(
    provider_id: str | None,
    providers: Sequence[DirectDownloadProvider] | None = None,
) -> DirectDownloadProvider | None:
    if not provider_id:
        return None
    candidates = providers if providers is not None else create_providers()
    return next((provider for provider in candidates if provider.id == provider_id), None)


def provider_for_record(
    record: BrowseRecord,
    providers: Sequence[DirectDownloadProvider] | None = None,
) -> DirectDownloadProvider | None:
    """Resolve a record explicitly, retaining safe compatibility with legacy tasks."""
    candidates = providers if providers is not None else create_providers()
    prefix, separator, _remainder = record.id.partition(":")
    if separator:
        provider = provider_by_id(prefix, candidates)
        if provider is not None:
            return provider

    if record.source_url:
        provider = next(
            (provider for provider in candidates if provider.handles(record.source_url)),
            None,
        )
        if provider is not None:
            return provider

    # Anna's Archive records historically carried only their raw MD5. Preserve those
    # persisted tasks without treating arbitrary unknown URLs as Anna's Archive.
    if _AA_MD5_PATTERN.fullmatch(record.id):
        return provider_by_id("annas_archive", candidates)
    return None


def provider_for_record_id(
    record_id: str,
    providers: Sequence[DirectDownloadProvider] | None = None,
) -> DirectDownloadProvider | None:
    candidates = providers if providers is not None else create_providers()
    prefix, separator, _remainder = record_id.partition(":")
    if separator:
        return provider_by_id(prefix, candidates)
    # Record lookup predates provider-qualified IDs, so unqualified IDs are AA IDs.
    return provider_by_id("annas_archive", candidates)
