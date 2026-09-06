"""Shared contracts and result normalization for Direct Download websites."""

import hashlib
import re
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from bs4 import BeautifulSoup, Tag

from shelfmark.core.config import config
from shelfmark.core.languages import language_alias_map
from shelfmark.release_sources import BrowseRecord, SourceUnavailableError

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from pathlib import Path
    from threading import Event

    from shelfmark.core.models import SearchFilters
    from shelfmark.core.search_plan import ReleaseSearchPlan
    from shelfmark.metadata_providers import BookMetadata

_LANGUAGE_ALIAS_TO_CODE: dict[str, str] | None = None
_LANGUAGE_ALIAS_LOCK = threading.Lock()
_SIZE_UNIT_PATTERN = re.compile(r"(kb|mb|gb|tb)", re.IGNORECASE)
MIN_VALID_FILE_SIZE = 10 * 1024


class DirectDownloadUnavailableError(SourceUnavailableError):
    """Raised when the composite Direct Download source cannot be reached."""


def coerce_str_list(value: object) -> list[str]:
    """Return only string items from a config value."""
    if not isinstance(value, list | tuple):
        return []
    return [item for item in value if isinstance(item, str)]


def get_supported_formats() -> list[str]:
    """Return configured supported formats as a clean string list."""
    return coerce_str_list(config.SUPPORTED_FORMATS)


def html_response_text(response: str | tuple[str, str]) -> str:
    """Extract the HTML body from downloader responses."""
    if isinstance(response, tuple):
        return response[0]
    return response


def attr_to_str(value: object) -> str | None:
    """Convert a BeautifulSoup attribute value to a plain string."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        for item in value:
            if isinstance(item, str):
                return item
    return None


def get_attr(tag: Tag, attr: str) -> str | None:
    """Safely fetch a tag attribute as a string."""
    return attr_to_str(tag.get(attr))


@dataclass(frozen=True)
class ParsedSearchResult:
    """Provider-neutral fields extracted from one search-result element."""

    key: str
    title: str
    formats: tuple[str, ...]
    record_id: str | None = None
    author: str | None = None
    publisher: str | None = None
    year: str | None = None
    language: str | None = None
    content: str | None = None
    size: str | None = None
    preview: str | None = None
    source_url: str | None = None
    download_path: str | None = None


class DirectDownloadProvider(Protocol):
    """Provider lifecycle used by the composite Direct Download source."""

    id: str
    display_name: str

    def is_enabled(self) -> bool: ...

    def handles(self, url: str) -> bool: ...

    def search(
        self,
        book: BookMetadata,
        plan: ReleaseSearchPlan,
        *,
        expand_search: bool = False,
        content_type: str = "ebook",
    ) -> list[BrowseRecord]: ...

    def download(
        self,
        book_info: BrowseRecord,
        book_path: Path,
        progress_callback: Callable[[float], None] | None,
        cancel_flag: Event | None,
        status_callback: Callable[[str, str | None], None] | None,
    ) -> str | None: ...


@runtime_checkable
class RecordLookupProvider(Protocol):
    """Optional capability for providers that can reopen source-native records."""

    def get_record(
        self, record_id: str, *, fetch_download_count: bool = True
    ) -> BrowseRecord | None: ...


def normalize_language_token(value: str) -> str:
    normalized = value.strip().lower()
    for dash in ("‑", "–", "—", "−"):
        normalized = normalized.replace(dash, "-")
    return normalized


def language_alias_to_code() -> dict[str, str]:
    """Alias to code map, delegating to the shared language data."""
    global _LANGUAGE_ALIAS_TO_CODE
    cached = _LANGUAGE_ALIAS_TO_CODE
    if cached is not None:
        return cached

    with _LANGUAGE_ALIAS_LOCK:
        cached = _LANGUAGE_ALIAS_TO_CODE
        if cached is not None:
            return cached

        _LANGUAGE_ALIAS_TO_CODE = language_alias_map()
        return _LANGUAGE_ALIAS_TO_CODE


def normalize_requested_languages(languages: list[str] | None) -> set[str]:
    if not languages:
        return set()
    aliases = language_alias_to_code()
    normalized: set[str] = set()
    for value in languages:
        token = normalize_language_token(str(value))
        if not token or token == "all":  # noqa: S105 - "all" is a language sentinel
            continue
        normalized.add(aliases.get(token, token))
    return normalized


def book_matches_requested_languages(book_language: str | None, requested: set[str]) -> bool:
    """Return True when a book's language matches the requested filter.

    Books with unknown/missing language always pass — the server-side &lang= filter
    already narrowed the result set, so dropping unlabelled rows hides valid results.
    """
    if not requested:
        return True
    if not book_language:
        return True
    aliases = language_alias_to_code()
    normalized_book = aliases.get(
        normalize_language_token(book_language),
        normalize_language_token(book_language),
    )
    return normalized_book in requested


def normalize_size(size_str: str) -> str:
    """Normalize size string by uppercasing units (e.g., '5.2 mb' -> '5.2 MB')."""
    return _SIZE_UNIT_PATTERN.sub(lambda m: m.group(1).upper(), size_str.strip())


def parse_search_items(
    items: Iterable[Tag],
    filters: SearchFilters | None,
    *,
    provider_id: str,
    extract_item: Callable[[Tag], ParsedSearchResult | None],
) -> list[BrowseRecord]:
    """Normalize provider-specific HTML elements into Direct Download records.

    Providers only describe how fields are extracted from their DOM. Language and
    format filtering, stable IDs, and BrowseRecord construction stay shared.
    """
    requested_languages = normalize_requested_languages(filters.lang) if filters else set()
    requested_formats = (
        {value.casefold() for value in (filters.format or get_supported_formats())}
        if filters
        else set()
    )
    records: list[BrowseRecord] = []

    for item in items:
        parsed = extract_item(item)
        if parsed is None:
            continue

        normalized_language = normalize_language_token(parsed.language) if parsed.language else ""
        language = language_alias_to_code().get(normalized_language, normalized_language) or None
        if not book_matches_requested_languages(language, requested_languages):
            continue

        formats = parsed.formats or ("",)
        for book_format in formats:
            normalized_format = book_format.casefold()
            if (
                normalized_format
                and requested_formats
                and normalized_format not in requested_formats
            ):
                continue
            record_id = parsed.record_id
            if not record_id or len(formats) > 1:
                source_key = f"{parsed.key}#{normalized_format}"
                digest = hashlib.blake2b(source_key.encode(), digest_size=16).hexdigest()
                record_id = f"{provider_id}:{digest}"
            records.append(
                BrowseRecord(
                    id=record_id,
                    title=parsed.title,
                    source="direct_download",
                    author=parsed.author,
                    publisher=parsed.publisher,
                    year=parsed.year,
                    language=language,
                    format=normalized_format or None,
                    size=parsed.size,
                    preview=parsed.preview,
                    content=parsed.content,
                    source_url=parsed.source_url,
                    download_path=parsed.download_path,
                )
            )
    return records


def parse_search_page(
    page: str | BeautifulSoup | Tag,
    filters: SearchFilters | None,
    *,
    provider_id: str,
    item_selector: str,
    extract_item: Callable[[Tag], ParsedSearchResult | None],
) -> list[BrowseRecord]:
    """Parse a result page using provider-specific selectors and extraction."""
    root = BeautifulSoup(page, "html.parser") if isinstance(page, str) else page
    return parse_search_items(
        (item for item in root.select(item_selector) if isinstance(item, Tag)),
        filters,
        provider_id=provider_id,
        extract_item=extract_item,
    )
