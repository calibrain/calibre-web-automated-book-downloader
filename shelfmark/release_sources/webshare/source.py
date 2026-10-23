"""Webshare.cz release source - searches Webshare's file index by name.

Webshare is a Czech file host whose search matches file names only, so there is no
book metadata to read: the file name is the release title, its extension the format,
and results are filtered down to the user's supported ebook/audiobook formats.
Downloads resolve a time-limited link per file through the logged-in account (see
handler.py).
"""

import re
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, ClassVar

from shelfmark.core.config import config
from shelfmark.core.logger import setup_logger
from shelfmark.download.postprocess.policy import (
    get_supported_audiobook_formats,
    get_supported_formats,
)
from shelfmark.release_sources import (
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
from shelfmark.release_sources.webshare.api import WebshareClient, WebshareError, WebshareFile

if TYPE_CHECKING:
    from shelfmark.core.search_plan import ReleaseSearchPlan
    from shelfmark.metadata_providers import BookMetadata

logger = setup_logger(__name__)

_DEFAULT_MAX_RESULTS = 50

# Audiobooks on Webshare are usually uploaded as a single archive of mp3s; the
# orchestrator extracts zip/rar before import, so accept them for audiobooks.
_AUDIOBOOK_ARCHIVE_FORMATS = ("zip", "rar")

# Language tags commonly embedded in Webshare file names ("Kniha (CZ).epub").
_LANGUAGE_TAGS = {
    "cz": "cs",
    "cze": "cs",
    "ces": "cs",
    "cesky": "cs",
    "czech": "cs",
    "sk": "sk",
    "slo": "sk",
    "slk": "sk",
    "slovensky": "sk",
    "slovak": "sk",
    "en": "en",
    "eng": "en",
    "english": "en",
    "de": "de",
    "ger": "de",
    "deu": "de",
}
_TOKEN_RE = re.compile(r"[a-z]+")


def get_credentials() -> tuple[str, str]:
    """Return the configured ``(username, password)``, stripped, possibly empty."""
    username = config.get("WEBSHARE_USERNAME", "")
    password = config.get("WEBSHARE_PASSWORD", "")
    return (
        username.strip() if isinstance(username, str) else "",
        password if isinstance(password, str) else "",
    )


def _coerce_positive_int(value: object, default: int) -> int:
    """Return a positive integer config value or the provided default."""
    if isinstance(value, bool):
        return default
    if isinstance(value, int | float) and value > 0:
        return int(value)
    return default


def _build_query_candidates(plan: ReleaseSearchPlan, book: BookMetadata) -> list[str]:
    """Build ordered, de-duplicated search queries from the plan (mirrors Libgen)."""
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


def _file_format(file: WebshareFile) -> str | None:
    """File extension, preferring the name's suffix over Webshare's ``type`` field."""
    suffix = PurePosixPath(file.name).suffix.lstrip(".").lower()
    return suffix or file.type


def _guess_language(name: str) -> str | None:
    """Pick a language code from a tag in the file name, if there is exactly one."""
    stem = PurePosixPath(name).stem.lower()
    found = {_LANGUAGE_TAGS[token] for token in _TOKEN_RE.findall(stem) if token in _LANGUAGE_TAGS}
    return found.pop() if len(found) == 1 else None


def _format_size(size_bytes: int | None) -> str | None:
    if size_bytes is None:
        return None
    size = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return None


def _allowed_formats(content_type: str) -> set[str]:
    if content_type == "audiobook":
        return {*get_supported_audiobook_formats(), *_AUDIOBOOK_ARCHIVE_FORMATS}
    return set(get_supported_formats())


@register_source("webshare")
class WebshareSource(ReleaseSource):
    """Release source that searches Webshare.cz for ebook and audiobook files."""

    name = "webshare"
    display_name = "Webshare.cz"
    supported_content_types: ClassVar[list[str]] = ["ebook", "audiobook"]

    def is_available(self) -> bool:
        """Available only when explicitly enabled and credentials are configured."""
        username, password = get_credentials()
        return config.get("WEBSHARE_ENABLED", False) is True and bool(username and password)

    def search(
        self,
        book: BookMetadata,
        plan: ReleaseSearchPlan,
        *,
        expand_search: bool = False,
        content_type: str = "ebook",
    ) -> list[Release]:
        """Search Webshare for files of a book in the user's supported formats."""
        if content_type not in self.supported_content_types or not self.is_available():
            return []

        queries = _build_query_candidates(plan, book)
        if not queries:
            return []

        max_results = _coerce_positive_int(
            config.get("WEBSHARE_MAX_RESULTS", _DEFAULT_MAX_RESULTS), _DEFAULT_MAX_RESULTS
        )
        allowed = _allowed_formats(content_type)
        client = WebshareClient(*get_credentials())

        for query in queries:
            logger.info("Searching Webshare for: %s", query)
            try:
                files = client.search(query, limit=max_results)
            except WebshareError as exc:
                msg = f"Webshare search failed: {exc}"
                raise SourceUnavailableError(msg) from exc

            releases = [
                self._file_to_release(file, content_type)
                for file in files
                if not file.password_protected and _file_format(file) in allowed
            ]
            if releases:
                return releases
        return []

    def _file_to_release(self, file: WebshareFile, content_type: str) -> Release:
        """Normalize a Webshare search hit into a Release.

        ``source_id`` is namespaced ``webshare:<ident>`` so it can never collide with
        another source's queue key; the handler strips the prefix back to the ident.
        """
        fmt = _file_format(file)
        language = _guess_language(file.name)
        return Release(
            source="webshare",
            source_id=f"webshare:{file.ident}",
            title=PurePosixPath(file.name).stem or file.name,
            format=fmt,
            language=language,
            size=_format_size(file.size_bytes),
            size_bytes=file.size_bytes,
            download_url=None,  # links are time-limited; the handler resolves one per download
            info_url=file.info_url,
            protocol=ReleaseProtocol.HTTP,
            indexer="Webshare.cz",
            content_type=content_type,
            extra={
                "ident": file.ident,
                "file_name": file.name,
                "language": language,
                "votes": file.positive_votes - file.negative_votes,
            },
        )

    def get_column_config(self) -> ReleaseColumnConfig:
        """Language, format, size and the file's Webshare vote balance."""
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
                    sortable=True,
                    sort_key="size_bytes",
                ),
                ColumnSchema(
                    key="extra.votes",
                    label="Votes",
                    render_type=ColumnRenderType.NUMBER,
                    align=ColumnAlign.CENTER,
                    width="60px",
                    hide_mobile=True,
                    sortable=True,
                ),
            ],
            grid_template="minmax(0,2fr) 60px 80px 80px 60px",
            supported_filters=["format", "language"],
        )
