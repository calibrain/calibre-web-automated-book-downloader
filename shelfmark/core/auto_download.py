"""Automatic release selection + download for pending requests.

Given a pending request (typically synced from a Hardcover wishlist), this module
searches release sources in a user-configured priority order, applies a strict
title/author/format match guard, picks the best candidate from the first source
that yields a match, and queues it via the normal request-fulfilment path.

Design goals:
- **Strict by default.** Bias toward leaving a request pending (manual review) over
  grabbing the wrong file. Title and author must both match; the format must be a
  real audiobook format (no ebook-only fallbacks when targeting audiobooks).
- **Source priority is primary.** Walk the configured source order top-down and take
  the first source that produces at least one strict match.
"""

from __future__ import annotations

import math
import re
import sqlite3
import statistics
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from shelfmark.core import search_deadline
from shelfmark.core.config import config as app_config
from shelfmark.core.logger import setup_logger
from shelfmark.core.models import QueueStatus
from shelfmark.core.release_search import search_source_releases
from shelfmark.core.request_helpers import coerce_int
from shelfmark.core.request_policy import normalize_content_type
from shelfmark.core.request_validation import RequestStatus
from shelfmark.core.text_match import (
    DEFAULT_TITLE_MATCH_THRESHOLD,
    PACKAGING_MARKERS,
    STOPWORDS,
    author_surname,
    extra_work_tokens,
    is_bundle_title,
    title_tokens_match,
)
from shelfmark.core.text_match import (
    numeral_tokens as _tokens,
)
from shelfmark.core.utils import AUDIOBOOK_FORMATS
from shelfmark.download.postprocess.policy import (
    get_supported_audiobook_formats,
    get_supported_formats,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from shelfmark.core.user_db import UserDB
    from shelfmark.metadata_providers import BookMetadata
    from shelfmark.release_sources import Release

logger = setup_logger(__name__)

# Fraction of significant book-title tokens that must appear in the release title.
TITLE_MATCH_THRESHOLD = DEFAULT_TITLE_MATCH_THRESHOLD

# An archive says nothing about what is inside it. Nothing unattended should unpack one for an
# audiobook request: a Soulseek peer's "(html).rar" (a Calibre ebook export) was queued for an
# audiobook because its release carried the requested content type. Pick it by hand if you want it.
ARCHIVE_FORMATS = frozenset({"rar", "zip", "7z", "tar", "gz", "tgz", "bz2", "xz"})

EBOOK_FORMAT_MARKERS = (
    "epub",
    "mobi",
    "azw3",
    "azw",
    "pdf",
    "fb2",
    "djvu",
    "cbz",
    "cbr",
)
# Ebook format ranking for tie-breaking within a single source.
_EBOOK_FORMAT_RANK = {"epub": 4, "kepub": 3, "azw3": 3, "mobi": 2, "azw": 2, "fb2": 1, "pdf": 1}
# Audiobook signals we recognise in free-text release titles in addition to formats.
AUDIOBOOK_TITLE_MARKERS = ("audiobook", "unabridged", "m4b", "audio book")

# Audiobook format ranking for tie-breaking within a single source. Every format
# Shelfmark can process outranks an unrecognised one; m4b, m4a and mp3 are preferred.
_FORMAT_RANK = {**dict.fromkeys(AUDIOBOOK_FORMATS, 1), "mp3": 2, "m4a": 3, "m4b": 4}

# Separators a release name puts between the title and everything else it carries:
# author, narrator, series, format tags ("Dune - Frank Herbert (Narrated by ...) [m4b]").
_RELEASE_SEGMENT_SPLIT = re.compile(
    r"\s+[-\u2013\u2014|/]\s+|\s+by\s+|:\s+|[\[\](){}]", re.IGNORECASE
)


@dataclass(frozen=True)
class AutoDownloadOutcome:
    """Result of attempting to auto-download a single request."""

    request_id: int
    status: str  # "queued" | "no_match" | "skipped" | "error"
    detail: str = ""
    source: str | None = None


def _audiobook_formats() -> set[str]:
    return set(get_supported_audiobook_formats())


def _ebook_formats() -> set[str]:
    return set(get_supported_formats())


def _author_surname_tokens(book: BookMetadata) -> list[str]:
    """Return distinctive author tokens (prefer the surname of the primary author)."""
    author = book.search_author or (book.authors[0] if book.authors else "")
    surname = author_surname(author)
    return [surname] if surname else []


def _title_match(book: BookMetadata, release_title: str) -> bool:
    return title_tokens_match(
        book.search_title or book.title,
        set(_tokens(release_title)),
        TITLE_MATCH_THRESHOLD,
    )


def _author_match(book: BookMetadata, release: Release) -> bool:
    surname = _author_surname_tokens(book)
    if not surname:
        return True  # No author metadata to verify against; don't block on it.
    haystack = set(_tokens(release.title)) | set(_tokens(release.indexer))
    extra_author = release.extra.get("author") if isinstance(release.extra, dict) else None
    haystack |= set(_tokens(extra_author if isinstance(extra_author, str) else None))
    return all(tok in haystack for tok in surname)


def _audiobook_signal(release: Release, audiobook_formats: set[str]) -> bool:
    fmt = (release.format or "").strip().lower()
    title_l = (release.title or "").lower()
    return (
        fmt in audiobook_formats
        or (release.content_type or "").lower() == "audiobook"
        or any(marker in title_l for marker in AUDIOBOOK_TITLE_MARKERS)
        or any(f".{af}" in title_l or f" {af}" in title_l for af in audiobook_formats)
    )


def _format_match(
    release: Release,
    content_type: str,
    audiobook_formats: set[str],
    ebook_formats: set[str],
) -> bool:
    """Require a format signal for the requested content type and reject the other kind."""
    fmt = (release.format or "").strip().lower()
    if content_type == "audiobook":
        if fmt in ARCHIVE_FORMATS:
            return False
        # Reject things that are clearly an ebook and nothing else.
        return _audiobook_signal(release, audiobook_formats) and fmt not in EBOOK_FORMAT_MARKERS

    title_l = (release.title or "").lower()
    has_ebook_signal = (
        fmt in ebook_formats
        or (release.content_type or "").lower() == "ebook"
        or any(f".{ef}" in title_l for ef in ebook_formats)
    )
    return has_ebook_signal and not _audiobook_signal(release, audiobook_formats)


# Words that mark something made from the work rather than the work itself: a study
# aid, a cut-down recording, a dramatisation. "Dune: Summary & Analysis" keeps them out
# of the segment that holds the title, so they disqualify wherever they sit in the name.
# The library's edition words answer a different question: an abridged recording on the
# shelf still counts as the book owned, but it is not the book to download.
_DERIVATIVE_MARKERS = frozenset(
    {
        "summary",
        "summaries",
        "analysis",
        "workbook",
        "sparknotes",
        "cliffsnotes",
        "abridged",
        "dramatized",
        "dramatised",
        "dramatization",
        "dramatisation",
        "adaptation",
        # A radio play or a broadcast's soundtrack is a dramatisation that does not say so:
        # "The Hobbit: 1979 Mind's Eye Radio Drama", "The Hobbit: The BBC TV Soundtrack".
        "drama",
        "radio",
        "soundtrack",
    }
)
_DERIVATIVE_PHRASES = re.compile(r"\bstudy\s+guide\b|\bgraphic\s+novel\b", re.IGNORECASE)
# "Unabridged" and "Un-Abridged" describe a full recording of the work. Dropped before the
# markers are read so the hyphenated form does not split into "abridged".
_UNABRIDGED = re.compile(r"\bun-?\s?abridged\b", re.IGNORECASE)
# A slash between two names that each hold the title is a 2-in-1: "Dune / Dune Messiah".
_SLASH_SPLIT = re.compile(r"\s*/\s*")


def _names_derivative(book: BookMetadata, release_title: str | None) -> bool:
    """True when the release name marks a derivative of the work, in any segment.

    Words the requested title carries itself are not held against the release.
    """
    title = book.search_title or book.title or ""
    text = _UNABRIDGED.sub(" ", release_title or "")
    if (set(_tokens(text)) & _DERIVATIVE_MARKERS) - set(_tokens(title)):
        return True
    return bool(_DERIVATIVE_PHRASES.search(text)) and not _DERIVATIVE_PHRASES.search(title)


def _is_series_label(seg_tokens: list[str], title_tokens: set[str], series: set[str]) -> bool:
    """True when a segment is the series name rather than the title: "Alex Cross 25".

    Such a segment holds a one-word title ("Cross") only because the series repeats it,
    so it must not vouch for the release. A segment that is the title itself ("Dune" in
    the Dune series) is not a label.
    """
    words = {
        tok
        for tok in seg_tokens
        if tok.isalpha() and tok not in STOPWORDS and tok not in PACKAGING_MARKERS
    }
    return bool(words) and words <= series and not words <= title_tokens


def _carries_sequel_number(seg_tokens: list[str], title: str, context: set[str]) -> bool:
    """True when a bare number follows the title: "Mistborn 2", "Dune Chronicles Book 2".

    Series, author and packaging words between the title and the number are skipped.
    1 is the first book under its series name, and anything of three digits or more is
    a year or a bitrate, so neither names a sequel. A title that carries a number of its
    own ("Part II", "Ready Player Two") is another matter: any other number in that
    place, 1 included, names another volume. Numbers written as words or roman numerals
    arrive here already in digit form.
    """
    title_seq = _tokens(title)
    title_toks = set(title_seq)
    title_numbered = any(tok.isdigit() and len(tok) <= 2 for tok in title_toks)
    # The title ends where its own last word does: in "The Two Towers 2" the trailing 2
    # follows "towers" and is not the title's "two" over again.
    anchored = [i for i, tok in enumerate(seg_tokens) if title_seq and tok == title_seq[-1]]
    last = max(
        anchored or (i for i, tok in enumerate(seg_tokens) if tok in title_toks), default=None
    )
    if last is None:
        return False
    for tok in seg_tokens[last + 1 :]:
        # The book's own title is part of the context, so its number would be skipped
        # as context; past the title's end that number is another volume's.
        repeated = bool(anchored) and tok in title_toks
        if (tok in context and not repeated) or tok in PACKAGING_MARKERS or tok in STOPWORDS:
            continue
        return (
            tok.isdigit()
            and len(tok) <= 2
            and (int(tok) >= 2 or title_numbered)
            and (bool(anchored) or tok not in title_toks)
        )
    return False


def _segments_name_other_work(text: str, title: str, context: set[str], series: set[str]) -> bool:
    """Apply the other-work rule to the parts of a release name that hold the title."""
    title_tokens = set(_tokens(title))
    segments = [seg for seg in _RELEASE_SEGMENT_SPLIT.split(text) if seg.strip()]
    holding = [
        seg
        for seg in segments
        if title_tokens_match(title, set(_tokens(seg)), TITLE_MATCH_THRESHOLD)
        and not _is_series_label(_tokens(seg), title_tokens, series)
    ]
    # A title the separators split apart ("Star Wars: Thrawn") is judged whole.
    return all(
        bool(extra_work_tokens(set(_tokens(seg)), title, context))
        or _carries_sequel_number(_tokens(seg), title, context)
        for seg in holding or [text]
    )


def _names_other_work(book: BookMetadata, release_title: str | None) -> bool:
    """True when the release names a different work that contains the requested title.

    "Dune" is a subset of "Dune Messiah", so the token match alone accepts a sequel.
    The library check rejects a shelf title that adds a word the search did not ask
    for; release names also carry narrators and uploader tags, so the same rule is
    applied only to the parts of the name that hold the title. A segment that is just
    the series name does not count as holding it, and a bare number after the title
    names a sequel. Two slash-separated names that both hold the title are two books
    when either of them names another work.
    """
    title = book.search_title or book.title
    # The book's own full title is context too: a search title shortened from it (the
    # subtitle dropped, or ", or There and Back Again" cut off) must not read a release
    # that spells the whole thing out as another work.
    context = set(
        _tokens(
            " ".join(
                [
                    *(book.authors or []),
                    book.search_author or "",
                    book.series_name or "",
                    book.subtitle or "",
                    book.title or "",
                ]
            )
        )
    )
    series = set(_tokens(book.series_name))
    text = release_title or ""
    parts = [part for part in _SLASH_SPLIT.split(text) if part.strip()]
    holding_parts = [
        part
        for part in parts
        if title_tokens_match(title, set(_tokens(part)), TITLE_MATCH_THRESHOLD)
    ]
    if len(holding_parts) > 1 and any(
        _segments_name_other_work(part, title, context, series) for part in holding_parts
    ):
        return True
    return _segments_name_other_work(text, title, context, series)


def _seeders_ok(release: Release, min_seeders: int) -> bool:
    from shelfmark.release_sources import ReleaseProtocol

    if release.protocol != ReleaseProtocol.TORRENT:
        return True  # Non-torrent protocols have no seeder concept.
    if release.seeders is None:
        return True  # The source reports no count (e.g. AudiobookBay); nothing to judge.
    return release.seeders >= min_seeders


def _listing_is_bundle(release: Release, book: BookMetadata) -> bool:
    """True when the source's own listing title names a multi-book pack.

    Some sources show a pack's contents as one row per book, titled after the book, and keep
    the pack's name in ``extra["title_raw"]``. ``release.title`` alone then looks like a single
    book.
    """
    raw = release.extra.get("title_raw") if isinstance(release.extra, dict) else None
    return isinstance(raw, str) and is_bundle_title(raw, book.search_title or book.title)


# Languages whose titles are not written in Latin letters. When one of these is wanted, a title
# in that script is not a mismatch.
_NON_LATIN_LANGUAGES = frozenset(
    {
        "ru",
        "uk",
        "bg",
        "sr",
        "mk",
        "be",
        "el",
        "he",
        "ar",
        "fa",
        "ur",
        "hi",
        "bn",
        "ta",
        "te",
        "th",
        "zh",
        "ja",
        "ko",
    }
)
_TITLE_LANGUAGE = re.compile(
    r"[(\[]\s*(?:in\s+)?([A-Za-z]{3,12})\s+(?:edition|version|translation|audiobook|audio|narration|language)\s*[)\]]",
    re.IGNORECASE,
)


def _wanted_languages(user_id: int | None) -> list[str] | None:
    """The requester's default book languages as ISO codes; None means every language."""
    try:
        from shelfmark.core.search_plan import _normalize_languages

        return _normalize_languages(None, user_id)
    except Exception:  # noqa: BLE001 - a settings problem must not turn the filter off
        logger.warning("auto-download: could not read the default languages, assuming English")
        return ["en"]


def _mostly_non_latin(text: str) -> bool:
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return False
    other = sum(1 for ch in letters if not unicodedata.name(ch, "").startswith("LATIN"))
    return other / len(letters) > 0.5


def _language_ok(release: Release, languages: list[str] | None) -> bool:
    """True unless the release is known, or visibly, in a language nobody asked for."""
    if not languages:
        return True
    from shelfmark.core.languages import normalize_language

    if release.language:
        code = normalize_language(release.language)
        if code:
            return code in languages
    title = release.title or ""
    named = _TITLE_LANGUAGE.search(title)
    if named:
        code = normalize_language(named.group(1))
        if code and code not in languages:
            return False
    return not (_mostly_non_latin(title) and not _NON_LATIN_LANGUAGES.intersection(languages))


def strict_match(
    release: Release,
    book: BookMetadata,
    *,
    content_type: str = "audiobook",
    min_seeders: int = 1,
    audiobook_formats: set[str] | None = None,
    ebook_formats: set[str] | None = None,
    languages: list[str] | None = None,
) -> bool:
    """Return True only if the release confidently matches the requested book and format."""
    return _author_match(book, release) and _matches_besides_author(
        release,
        book,
        content_type=content_type,
        min_seeders=min_seeders,
        audiobook_formats=audiobook_formats,
        ebook_formats=ebook_formats,
        languages=languages,
    )


def _matches_besides_author(
    release: Release,
    book: BookMetadata,
    *,
    content_type: str = "audiobook",
    min_seeders: int = 1,
    audiobook_formats: set[str] | None = None,
    ebook_formats: set[str] | None = None,
    languages: list[str] | None = None,
) -> bool:
    """Every strict-match rule but the author one."""
    audio = audiobook_formats if audiobook_formats is not None else _audiobook_formats()
    ebook = ebook_formats if ebook_formats is not None else _ebook_formats()
    return (
        _title_match(book, release.title or "")
        and not _names_other_work(book, release.title)
        and not _names_derivative(book, release.title)
        and not is_bundle_title(release.title, book.search_title or book.title)
        and not _listing_is_bundle(release, book)
        and _format_match(release, content_type, audio, ebook)
        and _seeders_ok(release, min_seeders)
        and _language_ok(release, languages)
    )


# Detail pages read per request to find the author of releases whose listing names none.
MAX_AUTHOR_LOOKUPS = 3


def _release_author(release: Release) -> str | None:
    author = release.extra.get("author") if isinstance(release.extra, dict) else None
    return author if isinstance(author, str) and author.strip() else None


def _fill_missing_authors(
    releases: list[Release], book: BookMetadata, match_kwargs: dict[str, Any]
) -> None:
    """Look up the author of the few releases that fail the strict match only for want of one.

    An AudiobookBay listing title without " - Author" ("The Mummy or Ramses the Damned")
    names nobody, so the author rule could never pass it. Its detail page usually credits
    the author; the source's handler reads it for at most ``MAX_AUTHOR_LOOKUPS`` such
    releases, inside the request's search budget. What it finds goes on the release's
    author field and the ordinary strict match then judges it. Nothing is relaxed: a page
    that credits nobody, or someone else, leaves the release rejected.
    """
    from shelfmark.release_sources import get_handler

    if not _author_surname_tokens(book):
        return
    looked = 0
    for release in releases:
        if looked >= MAX_AUTHOR_LOOKUPS or search_deadline.expired():
            return
        if _release_author(release) is not None or _author_match(book, release):
            continue
        if not _matches_besides_author(release, book, **match_kwargs):
            continue
        looked += 1
        try:
            authors = get_handler(release.source).list_authors(
                {"source": release.source, "download_url": release.download_url}
            )
        except Exception as exc:  # noqa: BLE001 - a lookup failure only leaves it unmatched
            logger.warning("auto-download: author lookup failed for %s: %s", release.title, exc)
            continue
        if authors:
            release.extra["author"] = ", ".join(authors)
            logger.info("auto-download: %s credits %s", release.title, release.extra["author"])


def _download_count(release: Release) -> int:
    """Download count reported by the source (direct-download puts it in ``extra``), else 0."""
    raw = getattr(release, "downloads", None)
    if raw is None and isinstance(release.extra, dict):
        raw = release.extra.get("downloads")
    return coerce_int(raw, 0)


def _audiobook_format(release: Release) -> str:
    """The release's audiobook format; an unrecognised one is read from an "m4b" title tag."""
    fmt = (release.format or "").strip().lower()
    if fmt not in _FORMAT_RANK and "m4b" in (release.title or "").lower():
        return "m4b"
    return fmt


def _peer_rank(release: Release) -> tuple[int, int, int]:
    """How well the peer holding a Soulseek release can serve it: a free upload slot, then a
    short queue, then upload speed. Releases without those details (every other source) all
    rank the same here, so their order is unchanged."""
    extra = release.extra if isinstance(release.extra, dict) else {}
    if "has_free_upload_slot" not in extra:
        return (0, 0, 0)
    return (
        1 if extra.get("has_free_upload_slot") else 0,
        -coerce_int(extra.get("queue_length"), 0),
        coerce_int(extra.get("upload_speed"), 0),
    )


def _release_sort_key(release: Release, content_type: str) -> tuple[int, ...]:
    """Best format first, then the copy others chose (download count, then seeders), then how
    well the peer can serve it (Soulseek), then size."""
    if content_type == "audiobook":
        rank = _FORMAT_RANK.get(_audiobook_format(release), 0)
    else:
        rank = _EBOOK_FORMAT_RANK.get((release.format or "").strip().lower(), 0)
    return (
        rank,
        _download_count(release),
        release.seeders or 0,
        *_peer_rank(release),
        release.size_bytes or 0,
    )


# The UI inspects a release and asks "single book or split?" before queueing. Auto-download
# cannot ask, so it inspects the best few candidates itself and passes over packs.
MAX_PACK_CHECKS = 3


_SINGLE_FILE_AUDIO = frozenset({"m4b", "m4a"})


def _is_single_audio_file(files: object) -> bool:
    """True when an inspected file list holds exactly one audio file and it is an m4b/m4a."""
    if not isinstance(files, list):
        return False
    exts = [
        str(f.get("path", "")).rsplit(".", 1)[-1].lower()
        for f in files
        if isinstance(f, dict) and "." in str(f.get("path", ""))
    ]
    audio = [ext for ext in exts if ext in AUDIOBOOK_FORMATS]
    return len(audio) == 1 and audio[0] in _SINGLE_FILE_AUDIO


def _inspect_candidate(release_data: dict[str, Any]) -> tuple[bool, bool]:
    """Inspect a release: (holds several books, is one m4b/m4a file). Any failure is (False, False)."""
    try:
        from shelfmark.core.release_inspect_routes import inspect_release

        result = inspect_release(release_data)
        if not result.get("inspected"):
            return False, False
        plan = result.get("plan")
        return bool(plan and plan.get("is_pack")), _is_single_audio_file(result.get("files"))
    except Exception as exc:  # noqa: BLE001 — inspection is advisory; never block a download
        logger.warning("auto-download: pack inspection failed, queueing anyway: %s", exc)
        return False, False


# A copy far smaller than the others for the same book is a lower-bitrate copy. Format rank must
# not lift it over a fuller one: a thin single m4b should lose to a good chapter set that can be
# merged afterwards. Size is the only quality signal every source reports (no duration or bitrate).
# The cut-off is a setting: the smallest acceptable copy, as a percentage of the largest.
MIN_SIZE_SETTING = "AUTO_DOWNLOAD_AUDIOBOOK_MIN_SIZE_PERCENT"
DEFAULT_MIN_SIZE_PERCENT = 67
# Sizes this far above the median (a pack, say) are ignored when picking the reference size.
_SIZE_OUTLIER_FACTOR = 2.5


def _min_size_percent() -> int:
    """The configured cut-off, 0 (guard off) to 100; anything unusable falls back to the default."""
    raw = app_config.get(MIN_SIZE_SETTING, DEFAULT_MIN_SIZE_PERCENT)
    try:
        percent = float(raw)  # pyright: ignore[reportArgumentType]
    except TypeError, ValueError:
        return DEFAULT_MIN_SIZE_PERCENT
    if not math.isfinite(percent):
        return DEFAULT_MIN_SIZE_PERCENT
    return round(min(max(percent, 0), 100))


def _undersized_copies(releases: list[Release], content_type: str) -> list[Release]:
    """Audiobooks: copies under the configured share of the largest comparable copy's size."""
    percent = _min_size_percent()
    if content_type != "audiobook":
        return []
    sizes = [r.size_bytes for r in releases if r.size_bytes and r.size_bytes > 0]
    if len(sizes) < 2:
        return []
    median = statistics.median(sizes)
    reference = max(size for size in sizes if size <= _SIZE_OUTLIER_FACTOR * median)
    floor = percent / 100 * reference
    return [r for r in releases if r.size_bytes and 0 < r.size_bytes < floor]


def _demote_undersized(ranked: list[Release], content_type: str) -> list[Release]:
    """Move undersized copies behind the rest, keeping the order within each group."""
    thin = _undersized_copies(ranked, content_type)
    return [r for r in ranked if r not in thin] + thin


def pick_best_release(releases: list[Release], content_type: str = "audiobook") -> Release | None:
    if not releases:
        return None
    return max(releases, key=lambda release: _release_sort_key(release, content_type))


def build_release_data(release: Release, book: BookMetadata, content_type: str) -> dict[str, Any]:
    """Build a queue_release-compatible payload from a chosen Release."""
    author = book.search_author or (book.authors[0] if book.authors else None)
    extra = release.extra if isinstance(release.extra, dict) else {}
    # Carry the book's cover so the download shows artwork immediately; the orchestrator
    # reads release_data["preview"] and proxies it (orchestrator.queue_release / task_to_dict).
    preview = book.cover_url or extra.get("preview")
    payload: dict[str, Any] = {
        "source": release.source,
        "source_id": release.source_id,
        "title": release.title,
        "author": author,
        "year": book.publish_year,
        "preview": preview,
        "format": release.format,
        "size": release.size,
        "size_bytes": release.size_bytes,
        "download_url": release.download_url,
        "info_url": release.info_url,
        "protocol": release.protocol.value if release.protocol else None,
        "indexer": release.indexer,
        "seeders": release.seeders,
        "language": release.language,
        "content_type": release.content_type or content_type,
        "series_name": book.series_name,
        "series_position": book.series_position,
        "subtitle": book.subtitle,
        "extra": dict(extra),
    }
    return {key: value for key, value in payload.items() if value is not None}


RETRY_DAYS_SETTING = "AUTO_DOWNLOAD_RETRY_DAYS"
DEFAULT_RETRY_DAYS = 7

# A backlog that is already past the cooldown (a bad week, or the first pass after this rule
# was introduced) would otherwise all retry at once, so each pass reopens only this many,
# oldest first.
MAX_RETRIES_PER_PASS = 5

# A download that itself failed is retried with a different release at most this many times.
MAX_FAILURE_RETRIES = 3

# Delivery states that can mean a download never arrived. "cancelled" is deliberately absent:
# someone or something chose to stop it, and it stays stopped until an admin says otherwise.
_RETRYABLE_DELIVERY_STATES = frozenset({QueueStatus.ERROR.value, QueueStatus.QUEUED.value})

_INTERRUPTED = "interrupted"
_FAILED = "failed"

# The message the startup sweep gives a download it finds orphaned (it also closes the row
# out as "error"). It is the word the activity API already uses for such a row, and it has to
# match: an interrupted download is not a failed release.
_INTERRUPTED_MESSAGE = "interrupted"


def _parse_timestamp(value: object) -> datetime | None:
    """A stored request timestamp (SQLite's or ISO with an offset) as an aware datetime."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip())
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _last_delivery_change(row: dict[str, Any]) -> datetime | None:
    """When the request last changed delivery state, falling back to older stamps."""
    for key in ("delivery_updated_at", "reviewed_at", "created_at"):
        stamp = _parse_timestamp(row.get(key))
        if stamp is not None:
            return stamp
    return None


def _latest_history(db_path: str, request_ids: list[int]) -> dict[int, tuple[str, str | None]]:
    """The newest (status, message) in download_history per request id; none means absent."""
    latest: dict[int, tuple[str, str | None]] = {}
    if not request_ids:
        return latest
    conn = sqlite3.connect(db_path)
    try:
        for start in range(0, len(request_ids), 500):
            chunk = request_ids[start : start + 500]
            marks = ",".join("?" * len(chunk))
            rows = conn.execute(
                "SELECT request_id, final_status, status_message FROM download_history "  # noqa: S608 - placeholders only
                f"WHERE request_id IN ({marks}) ORDER BY id",
                chunk,
            )
            for request_id, final_status, status_message in rows:
                latest[int(request_id)] = (str(final_status), status_message)
    finally:
        conn.close()
    return latest


def _failure_kind(latest: tuple[str, str | None] | None) -> str | None:
    """Why a request whose delivery never completed never arrived, from its latest history row.

    No row, a row still "active", or an error the startup sweep labelled "Interrupted" all mean
    the process stopped under the download, so nothing was wrong with the release. Any other
    error means the download itself failed. Anything else disagrees with the request, so it is
    left alone rather than guessed at.
    """
    if latest is None:
        return _INTERRUPTED
    final_status, message = latest
    if final_status == "active":
        return _INTERRUPTED
    if final_status == "error":
        if (message or "").strip().lower() == _INTERRUPTED_MESSAGE:
            return _INTERRUPTED
        return _FAILED
    return None


def _release_key(release_data: object) -> dict[str, str] | None:
    """Identify the release a request was fulfilled with, or None when it cannot be told."""
    if not isinstance(release_data, dict):
        return None
    source = str(release_data.get("source") or "").strip()
    source_id = str(release_data.get("source_id") or "").strip()
    return {"source": source, "source_id": source_id} if source and source_id else None


def _failed_release_keys(book_data: object) -> set[tuple[str, str]]:
    """Releases already tried and failed for this request, as (source, source_id) pairs."""
    entries = book_data.get("failed_releases") if isinstance(book_data, dict) else None
    keys: set[tuple[str, str]] = set()
    for entry in entries if isinstance(entries, list) else []:
        if isinstance(entry, dict) and entry.get("source") and entry.get("source_id"):
            keys.add((str(entry["source"]), str(entry["source_id"])))
    return keys


def reopen_stale_failures(
    user_db: UserDB,
    *,
    cooldown_days: int,
    db_path: str | None,
    provider_filter: str | None = "hardcover",
    max_reopen: int = MAX_RETRIES_PER_PASS,
    now: datetime | None = None,
) -> int:
    """Return synced requests whose download never arrived to pending once the cooldown passes.

    Nothing else retries them: the sync treats a request in any status as handled, and the
    auto-download pass only acts on pending ones. Reopening goes through
    ``UserDB.reopen_failed_request`` (upstream's own path for a failed request), which clears
    the chosen release and records why, so the normal pass searches again.

    Interrupted downloads are reopened as they were, and the same release may be picked again:
    a torrent already in the client is joined rather than added twice. A download that itself
    failed has its release remembered on the request and skipped next time, and is given up on
    after ``MAX_FAILURE_RETRIES``. Cancelled and rejected requests are decisions and are never
    touched. Returns how many were reopened; ``cooldown_days`` of 0 or less turns retries off,
    and without readable history nothing is retried, since guessing would be worse.
    """
    if cooldown_days <= 0:
        return 0
    cutoff = (now or datetime.now(UTC)) - timedelta(days=cooldown_days)

    stale: list[tuple[datetime, dict[str, Any], str]] = []
    for row in user_db.list_requests(status=RequestStatus.FULFILLED):
        state = str(row.get("delivery_state") or "none").lower()
        if state not in _RETRYABLE_DELIVERY_STATES:
            continue
        book_data = row.get("book_data")
        if provider_filter and (
            not isinstance(book_data, dict) or book_data.get("provider") != provider_filter
        ):
            continue
        changed = _last_delivery_change(row)
        if changed is None or changed > cutoff:
            continue
        stale.append((changed, row, state))
    if not stale or not db_path:
        return 0

    try:
        history = _latest_history(db_path, [int(row["id"]) for _, row, _ in stale])
    except sqlite3.Error as exc:
        logger.warning("auto-download: no retries this pass, could not read history: %s", exc)
        return 0

    stale.sort(key=lambda item: item[0])
    reopened = 0
    for _changed, row, state in stale:
        if reopened >= max_reopen:
            break
        request_id = int(row["id"])
        kind = _failure_kind(history.get(request_id))
        if kind is None:
            continue
        try:
            if kind == _FAILED:
                book_data = dict(row["book_data"])
                tried = list(book_data.get("failed_releases") or [])
                if len(tried) >= MAX_FAILURE_RETRIES:
                    continue
                key = _release_key(row.get("release_data"))
                if key is not None and (
                    key["source"],
                    key["source_id"],
                ) not in _failed_release_keys(book_data):
                    book_data["failed_releases"] = [*tried, key]
                    user_db.update_request(request_id, book_data=book_data)
            reason = f"Retrying after a {cooldown_days}-day cooldown ({kind}; delivery was {state})"
            result = user_db.reopen_failed_request(request_id, failure_reason=reason)
        except sqlite3.Error as exc:
            logger.warning("auto-download: could not reopen request %s: %s", request_id, exc)
            continue
        if result is not None:
            reopened += 1
    return reopened


_SOURCE_PRIORITY_KEYS = {
    "audiobook": "AUTO_DOWNLOAD_SOURCE_PRIORITY",
    "ebook": "AUTO_DOWNLOAD_EBOOK_SOURCE_PRIORITY",
}


def _configured_source_priority(content_type: str) -> list[str]:
    """Return enabled release-source names for ``content_type`` in configured priority order."""
    from shelfmark.release_sources import list_available_sources

    def _supports(src: dict[str, Any]) -> bool:
        supported = src.get("supported_content_types") or ["ebook", "audiobook"]
        return content_type in supported

    available = [src for src in list_available_sources() if _supports(src)]
    available_by_name = {src["name"]: src for src in available}

    raw = app_config.get(
        _SOURCE_PRIORITY_KEYS.get(content_type, "AUTO_DOWNLOAD_SOURCE_PRIORITY"), []
    )
    ordered: list[str] = []
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = item.get("id")
            if not isinstance(name, str) or name not in available_by_name:
                continue
            if not bool(item.get("enabled", True)):
                continue
            if not available_by_name[name].get("enabled", False):
                continue  # Source itself not usable (unconfigured/unavailable).
            ordered.append(name)

    if ordered:
        return ordered
    # Fallback: every usable source for this content type, registry order.
    return [src["name"] for src in available if src.get("enabled")]


def auto_download_request(
    user_db: UserDB,
    request_row: dict[str, Any],
    *,
    sources: list[str],
    content_type: str,
    min_seeders: int,
    queue_release: Callable[..., tuple[bool, str | None]],
    admin_user_id: int,
) -> AutoDownloadOutcome:
    """Search, strict-match, and queue a single pending request."""
    from shelfmark.core.requests_service import RequestServiceError, fulfil_request
    from shelfmark.metadata_providers import (
        get_provider,
        get_provider_kwargs,
        is_provider_registered,
    )

    request_id = int(request_row["id"])
    book_data = request_row.get("book_data") or {}
    if not isinstance(book_data, dict):
        return AutoDownloadOutcome(request_id, "skipped", "no book_data")

    provider_name = book_data.get("provider")
    provider_id = book_data.get("provider_id") or book_data.get("id")
    if not provider_name or not provider_id or not is_provider_registered(provider_name):
        return AutoDownloadOutcome(request_id, "skipped", "no usable provider/id")

    try:
        prov = get_provider(provider_name, **get_provider_kwargs(provider_name))
        book = prov.get_book(str(provider_id))
    except Exception as exc:  # noqa: BLE001 - provider/network errors shouldn't kill the loop
        logger.warning("auto-download: metadata lookup failed for request %s: %s", request_id, exc)
        return AutoDownloadOutcome(request_id, "error", f"metadata lookup failed: {exc}")

    if book is None:
        return AutoDownloadOutcome(request_id, "skipped", "book not found in provider")

    # Final guard: skip if the book is already in a library that is checked for this content type.
    from shelfmark.core import library_index

    try:
        in_library = library_index.is_in_library(book, content_type, strict=True)
    except library_index.LibraryUnavailableError as exc:
        # Unattended: do not download blind. The request stays pending for the next cycle.
        logger.warning(
            "auto-download: request %s held, library check unavailable: %s", request_id, exc
        )
        return AutoDownloadOutcome(request_id, "error", f"library check unavailable: {exc}")
    if in_library:
        logger.info(
            "auto-download: request %s (%s) already in library; skipping",
            request_id,
            book.title,
        )
        return AutoDownloadOutcome(request_id, "in_library", "already in library")

    audiobook_formats = _audiobook_formats()
    ebook_formats = _ebook_formats()
    languages = _wanted_languages(coerce_int(request_row.get("user_id"), 0) or None)
    skipped_pack = False
    # Releases that already failed for this request, so a retry does not pick the same one.
    failed_releases = _failed_release_keys(book_data)

    # Walk sources in priority order; take the first source with a strict match.
    for source_name in sources:
        # The requester's id lets the search plan apply their default languages.
        _source, releases, _error = search_source_releases(
            source_name,
            book,
            expand_search=True,
            content_type=content_type,
            user_id=coerce_int(request_row.get("user_id"), 0) or None,
        )
        match_kwargs: dict[str, Any] = {
            "content_type": content_type,
            "min_seeders": min_seeders,
            "audiobook_formats": audiobook_formats,
            "ebook_formats": ebook_formats,
            "languages": languages,
        }
        releases = [r for r in releases if (r.source, r.source_id) not in failed_releases]
        if not any(strict_match(release, book, **match_kwargs) for release in releases):
            _fill_missing_authors(releases, book, match_kwargs)
        candidates = [
            release for release in releases if strict_match(release, book, **match_kwargs)
        ]
        chosen = None
        release_data: dict[str, Any] = {}
        ranked = sorted(candidates, key=lambda r: _release_sort_key(r, content_type), reverse=True)
        thin = _undersized_copies(ranked, content_type)
        ranked = _demote_undersized(ranked, content_type)
        for index, candidate in enumerate(ranked):
            if index >= MAX_PACK_CHECKS:
                break  # the best few were all packs; don't queue an uninspected tail
            data = build_release_data(candidate, book, content_type)
            is_pack, single_file = _inspect_candidate(data)
            if is_pack:
                logger.info("auto-download: skipping multi-book pack %s", candidate.title)
                skipped_pack = True
                continue
            if single_file and candidate not in thin:
                # One m4b/m4a beats a set of chapter files: no merge step afterwards.
                chosen, release_data = candidate, data
                break
            if chosen is None:
                chosen, release_data = candidate, data  # best-ranked fallback
            if content_type != "audiobook" or _audiobook_format(candidate) == "m4b":
                break  # already as good as it gets; don't inspect the rest
        if chosen is None:
            continue

        try:
            fulfil_request(
                user_db,
                request_id=request_id,
                admin_user_id=admin_user_id,
                queue_release=queue_release,
                release_data=release_data,
            )
        except RequestServiceError as exc:
            logger.warning("auto-download: fulfil failed for request %s: %s", request_id, exc)
            return AutoDownloadOutcome(request_id, "error", str(exc), source=source_name)
        logger.info(
            "auto-download: queued request %s from %s (%s)",
            request_id,
            source_name,
            chosen.title,
        )
        return AutoDownloadOutcome(request_id, "queued", chosen.title, source=source_name)

    if skipped_pack:
        logger.info("auto-download: request %s (%s): only multi-book packs", request_id, book.title)
        return AutoDownloadOutcome(request_id, "no_match", "only multi-book packs found")
    logger.info("auto-download: no strict match for request %s (%s)", request_id, book.title)
    return AutoDownloadOutcome(request_id, "no_match", "no strict match across sources")


def auto_download_pending(
    user_db: UserDB,
    *,
    queue_release: Callable[..., tuple[bool, str | None]],
    provider_filter: str | None = "hardcover",
    admin_user_id: int | None = None,
    db_path: str | None = None,
) -> dict[str, int]:
    """Run the auto-download pass over all eligible pending requests.

    Each request is handled for its own content type (ebook or audiobook): its source
    priority list, format guard and library check all follow the request. Returns a summary
    count dict. No-ops (returns zeros) unless AUTO_DOWNLOAD_ENABLED. ``admin_user_id``
    defaults to the lowest-id admin, who fulfils the requests.
    """
    zeros = {"queued": 0, "no_match": 0, "in_library": 0, "skipped": 0, "error": 0}
    if not bool(app_config.get("AUTO_DOWNLOAD_ENABLED", False)):
        return zeros

    if admin_user_id is None:
        from shelfmark.core.hardcover_sync import resolve_request_owner

        admin_user_id = resolve_request_owner(user_db)
    if admin_user_id is None:
        logger.warning("auto-download: no admin user exists to fulfil requests")
        return zeros

    retry_days = coerce_int(
        app_config.get(RETRY_DAYS_SETTING, DEFAULT_RETRY_DAYS), DEFAULT_RETRY_DAYS
    )
    reopened = reopen_stale_failures(
        user_db, cooldown_days=retry_days, db_path=db_path, provider_filter=provider_filter
    )
    if reopened:
        logger.info(
            "auto-download: reopened %d failed request(s) after the %d-day cooldown",
            reopened,
            retry_days,
        )

    min_seeders = coerce_int(app_config.get("AUTO_DOWNLOAD_MIN_SEEDERS", 1), 1)
    sources_by_type: dict[str, list[str]] = {}

    pending = user_db.list_requests(status="pending")
    summary = dict(zeros)

    for row in pending:
        book_data = row.get("book_data") or {}
        if provider_filter and (
            not isinstance(book_data, dict) or book_data.get("provider") != provider_filter
        ):
            continue
        # Only act on requests that haven't already been dispatched.
        if str(row.get("delivery_state") or "none").lower() not in {"none", ""}:
            continue

        content_type = normalize_content_type(
            row.get("content_type")
            or (book_data.get("content_type") if isinstance(book_data, dict) else None)
        )
        if content_type not in sources_by_type:
            sources_by_type[content_type] = _configured_source_priority(content_type)
            if not sources_by_type[content_type]:
                logger.warning(
                    "auto-download: no usable %s release sources configured", content_type
                )
        sources = sources_by_type[content_type]
        if not sources:
            summary["skipped"] += 1
            continue

        outcome = auto_download_request(
            user_db,
            row,
            sources=sources,
            content_type=content_type,
            min_seeders=min_seeders,
            queue_release=queue_release,
            admin_user_id=admin_user_id,
        )
        summary[outcome.status] = summary.get(outcome.status, 0) + 1

    logger.info("auto-download pass complete: %s", summary)
    return summary
