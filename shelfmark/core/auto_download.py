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

import json
import math
import re
import sqlite3
import statistics
import time
import unicodedata
from dataclasses import dataclass, replace
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
    normalize_numerals,
    significant_tokens,
    strip_alternate_title,
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

# Admin note on a pending request auto-download closed because the library already has it.
# "[auto]" marks an admin action no person took; the note is all a request records about why.
IN_LIBRARY_NOTE = "[auto] Already in the library."

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


# A Soulseek peer whose recent downloads all failed is tried after the others, not before.
DEAD_PEER_DAYS = 3
# What a Soulseek task records when the peer is the problem: a transfer that errored or timed
# out, or a peer that did not answer slskd's queue request (it answers 500, and the download is
# tried twice before giving up).
_PEER_FAILURE_MESSAGES = ("peer transfer failed", "failed to add to slskd")
# AudiobookBay shows no seed counts, so its best few candidates have their comments read for
# "0 seeders" and the like. A page costs a fetch (cached), so only this many are looked at.
MAX_COMPLAINT_CHECKS = 5


def recent_failed_peers(
    db_path: str | None, *, now: datetime | None = None, days: int = DEAD_PEER_DAYS
) -> frozenset[str]:
    """Soulseek peers that failed a transfer in the last ``days`` and delivered nothing in it."""
    if not db_path:
        return frozenset()
    since = ((now or datetime.now(UTC)) - timedelta(days=days)).isoformat(timespec="seconds")
    try:
        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute(
                "SELECT final_status, status_message, retry_payload FROM download_history "
                "WHERE source = 'slskd' AND queued_at >= ?",
                (since,),
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        logger.debug("auto-download: could not read recent Soulseek failures: %s", exc)
        return frozenset()
    failed: set[str] = set()
    delivered: set[str] = set()
    for final_status, message, payload in rows:
        try:
            context = (json.loads(payload) if payload else {}).get("retry_source_context") or {}
        except ValueError, AttributeError:
            continue
        peer = str(context.get("username") or "").strip() if isinstance(context, dict) else ""
        if not peer:
            continue
        if final_status == "complete":
            delivered.add(peer)
        elif final_status == "error" and str(message or "").lower().startswith(
            _PEER_FAILURE_MESSAGES
        ):
            failed.add(peer)
    return frozenset(failed - delivered)


# A release that failed is not offered again for this long, then it is tried once more: a torrent
# that stalled can be revived by new seeds, and a failure may simply have been a bad hour.
FAILED_RELEASE_RETRY_DAYS_SETTING = "AUTO_DOWNLOAD_RETRY_FAILED_RELEASE_DAYS"
DEFAULT_FAILED_RELEASE_RETRY_DAYS = 7
# A torrent release that stalled or had no seeders is not offered to ANY request in that time.
# Soulseek failures are about a peer, not a release, and are handled by the dead-peer rule.
_DEAD_RELEASE_MESSAGES = ("download stalled", "no seeders")
# Rows that say nothing about the release: the process stopped, or a later download replaced it.
_NOT_THE_RELEASES_FAULT = ("interrupted", "superseded")


def failed_release_retry_days() -> int:
    """Days a failed release is kept out before it may be picked again (at least 1)."""
    return max(
        coerce_int(
            app_config.get(FAILED_RELEASE_RETRY_DAYS_SETTING, DEFAULT_FAILED_RELEASE_RETRY_DAYS),
            DEFAULT_FAILED_RELEASE_RETRY_DAYS,
        ),
        1,
    )


def recent_failed_releases(
    db_path: str | None, *, now: datetime | None = None, days: int | None = None
) -> frozenset[tuple[str, str]]:
    """Releases (source, source_id) whose latest download failed within the last ``days``.

    The history row of a release is the one place its failure time is recorded, so a failure
    older than that no longer counts against the release, whatever a request remembers. A release
    that completed in the window is not failed.
    """
    if not db_path:
        return frozenset()
    window = failed_release_retry_days() if days is None else days
    since = ((now or datetime.now(UTC)) - timedelta(days=window)).isoformat(timespec="seconds")
    try:
        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute(
                "SELECT task_id, source, final_status, status_message FROM download_history "
                "WHERE COALESCE(terminal_at, queued_at) >= ?",
                (since,),
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        logger.debug("auto-download: could not read recent failed releases: %s", exc)
        return frozenset()
    failed: set[tuple[str, str]] = set()
    delivered: set[tuple[str, str]] = set()
    for task_id, source, final_status, message in rows:
        key = (str(source), str(task_id))
        if final_status == "complete":
            delivered.add(key)
        elif final_status in ("cancelled", "error") and not str(message or "").lower().startswith(
            _NOT_THE_RELEASES_FAULT
        ):
            failed.add(key)
    return frozenset(failed - delivered)


def recent_dead_releases(
    db_path: str | None, *, now: datetime | None = None, days: int | None = None
) -> frozenset[tuple[str, str]]:
    """Releases (source, source_id) that stalled or had no seeders in the last ``days``.

    Another request's failure counts: a request remembers only the releases that failed for it,
    so a torrent that died for one book was picked again for the next. A release that also
    completed in the window is not dead.
    """
    if not db_path:
        return frozenset()
    window = failed_release_retry_days() if days is None else days
    since = ((now or datetime.now(UTC)) - timedelta(days=window)).isoformat(timespec="seconds")
    try:
        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute(
                "SELECT task_id, source, final_status, status_message FROM download_history "
                "WHERE source != 'slskd' AND COALESCE(terminal_at, queued_at) >= ?",
                (since,),
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        logger.debug("auto-download: could not read recent dead releases: %s", exc)
        return frozenset()
    dead: set[tuple[str, str]] = set()
    delivered: set[tuple[str, str]] = set()
    for task_id, source, final_status, message in rows:
        key = (str(source), str(task_id))
        if final_status == "complete":
            delivered.add(key)
        elif final_status in ("cancelled", "error") and str(message or "").lower().startswith(
            _DEAD_RELEASE_MESSAGES
        ):
            dead.add(key)
    return frozenset(dead - delivered)


def _dead_source_reason(
    release: Release, dead_peers: frozenset[str], complaint_checks: list[int]
) -> str | None:
    extra = release.extra if isinstance(release.extra, dict) else {}
    if release.source == "slskd":
        peer = str(extra.get("username") or "").strip()
        return f"peer {peer} failed recently" if peer and peer in dead_peers else None
    if release.source == "audiobookbay" and complaint_checks[0] < MAX_COMPLAINT_CHECKS:
        if search_deadline.expired():
            return None
        complaint_checks[0] += 1
        from shelfmark.release_sources import get_handler

        try:
            complaints = get_handler(release.source).seed_complaints(
                {"source": release.source, "download_url": release.download_url}
            )
        except Exception as exc:  # noqa: BLE001 - a page that cannot be read just is not judged
            logger.debug("auto-download: no seed check for %s: %s", release.title, exc)
            return None
        if complaints:
            return f"{complaints} comment(s) say it has no seeders"
    return None


def _demote_dead_sources(ranked: list[Release], dead_peers: frozenset[str]) -> list[Release]:
    """Move releases from sources known to be dead behind the rest, keeping order within each."""
    live: list[Release] = []
    dead: list[Release] = []
    complaint_checks = [0]
    for release in ranked:
        reason = _dead_source_reason(release, dead_peers, complaint_checks)
        if reason is None:
            live.append(release)
            continue
        logger.info("auto-download: trying %s last, %s", release.title, reason)
        dead.append(release)
    return live + dead


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


# A request that keeps finding nothing is searched less often, so the same unmatched books do not
# cost every source a search every cycle. The first FULL_RATE_PASSES no-match passes run at full
# rate (a release often turns up within a day, and the existence check counts these passes), then
# one pass a day, then one every two, up to the setting (default 3 days). A match, a manual
# "Sync now" or the setting at 0 searches at full rate again. Anything doubtful searches.
BACKOFF_SETTING = "AUTO_DOWNLOAD_BACKOFF_MAX_DAYS"
DEFAULT_BACKOFF_MAX_DAYS = 3
FULL_RATE_PASSES = 3
BACKOFF_KEY = "search_backoff"  # in book_data, beside ``failed_releases``
# A cycle starts a little earlier or later than the one before, so a request searched late in one
# pass would just miss "a day later" in the next; this much early is on time.
_BACKOFF_SLACK = timedelta(hours=1)


def _backoff_state(book_data: object) -> dict[str, Any]:
    raw = book_data.get(BACKOFF_KEY) if isinstance(book_data, dict) else None
    return dict(raw) if isinstance(raw, dict) else {}


def _search_is_due(book_data: object, now: datetime, max_days: int) -> bool:
    """False only while a repeatedly unmatched request is inside its wait."""
    if max_days <= 0:
        return True
    state = _backoff_state(book_data)
    passes = coerce_int(state.get("no_match_passes"), 0)
    if passes < FULL_RATE_PASSES:
        return True
    try:
        last = datetime.fromisoformat(str(state.get("last_searched_at") or ""))
    except ValueError:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    wait = timedelta(days=min(max_days, passes - FULL_RATE_PASSES + 1))
    return now - last >= wait - _BACKOFF_SLACK


def _record_search_outcome(
    user_db: UserDB, request_id: int, outcome_status: str, now: datetime
) -> None:
    """Count a no-match pass for the backoff, or start over after a match.

    Best effort: the pass must not fail because this bookkeeping did, and a request whose
    count was not written is simply searched again next time.
    """
    try:
        current = user_db.get_request(request_id)
        if current is None:
            return
        book_data = current.get("book_data")
        if not isinstance(book_data, dict):
            return
        state: dict[str, Any] | None
        if outcome_status == "no_match" and current.get("status") == RequestStatus.PENDING:
            passes = coerce_int(_backoff_state(book_data).get("no_match_passes"), 0) + 1
            state = {
                "no_match_passes": passes,
                "last_searched_at": now.isoformat(timespec="seconds"),
            }
        elif outcome_status in {"queued", "in_library"} and _backoff_state(book_data):
            state = None
        else:
            return
        rest = {key: value for key, value in book_data.items() if key != BACKOFF_KEY}
        if state:
            rest[BACKOFF_KEY] = state
        user_db.update_request(request_id, book_data=rest)
    except Exception:  # noqa: BLE001 - bookkeeping must never take the pass with it
        logger.debug("auto-download: could not record the search for request %s", request_id)


RETRY_DAYS_SETTING = "AUTO_DOWNLOAD_RETRY_DAYS"
DEFAULT_RETRY_DAYS = 7

# A backlog that is already past the cooldown (a bad week, or the first pass after this rule
# was introduced) would otherwise all retry at once, so each pass reopens only this many,
# oldest first.
MAX_RETRIES_PER_PASS = 5

# A download that itself failed is retried with a different release at most this many times.
MAX_FAILURE_RETRIES = 3

# Delivery states that can mean a download never arrived. "cancelled" is deliberately absent:
# someone chose to stop it, and it stays stopped until an admin says otherwise. The one
# exception is a cancel the stall timer made (see _STALLED), which no one chose.
_RETRYABLE_DELIVERY_STATES = frozenset({QueueStatus.ERROR.value, QueueStatus.QUEUED.value})
_CANCELLED = QueueStatus.CANCELLED.value

_INTERRUPTED = "interrupted"
_FAILED = "failed"
_STALLED = "stalled"

# The message the stall timer records when it ends a download. A person cancelling leaves the
# last status message instead, so this prefix is what tells a timeout from a decision.
_STALL_MESSAGE_PREFIX = "download stalled"

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
    """The newest (status, message) in download_history per request id; none means absent.

    Newest by when it was queued: a reused release keeps its old row id, so id order is not
    chronological.
    """
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
                f"WHERE request_id IN ({marks}) ORDER BY queued_at, id",
                chunk,
            )
            for request_id, final_status, status_message in rows:
                latest[int(request_id)] = (str(final_status), status_message)
    finally:
        conn.close()
    return latest


# What a download records when its client could not be reached. The release is not to blame.
_CLIENT_UNREACHABLE = (
    "failed to connect",
    "cannot connect",
    "connection refused",
    "connection aborted",
)


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
        lowered = (message or "").strip().lower()
        if lowered == _INTERRUPTED_MESSAGE or any(m in lowered for m in _CLIENT_UNREACHABLE):
            # Nothing was wrong with the release: the process stopped, or its client was down.
            return _INTERRUPTED
        return _FAILED
    if final_status == "cancelled" and (message or "").strip().lower().startswith(
        _STALL_MESSAGE_PREFIX
    ):
        return _STALLED
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


# Delivery states that say a task is alive. Only the status poll moves a request off them, and a
# finished task leaves the queue an hour after it ends, so with nobody watching they can stay
# for good.
_LIVE_DELIVERY_STATES = frozenset({QueueStatus.QUEUED.value, QueueStatus.DOWNLOADING.value})
_SETTLED_STATUSES = frozenset(
    {QueueStatus.COMPLETE.value, QueueStatus.ERROR.value, QueueStatus.CANCELLED.value}
)


def _parse_moment(value: object) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def settle_delivery_states_from_history(user_db: UserDB, *, db_path: str | None) -> int:
    """Record how a download ended for requests that still say queued or downloading.

    The status poll does this while someone watches. Here the history decides: the newest row of
    the request, if it ended after the request last changed, is the outcome, and the request is
    dated from when it ended so the retry cooldown counts from then. A row still
    "active", or one that ended before the request was fulfilled again, says nothing about the
    current attempt and is left alone. Returns how many requests were updated.
    """
    if not db_path:
        return 0
    candidates = [
        row
        for row in user_db.list_requests(status=RequestStatus.FULFILLED)
        if str(row.get("delivery_state") or "none").lower() in _LIVE_DELIVERY_STATES
    ]
    if not candidates:
        return 0
    ids = [int(row["id"]) for row in candidates]
    latest: dict[int, tuple[str, object]] = {}
    try:
        conn = sqlite3.connect(db_path)
        try:
            for start in range(0, len(ids), 500):
                chunk = ids[start : start + 500]
                marks = ",".join("?" * len(chunk))
                for request_id, final_status, terminal_at in conn.execute(
                    "SELECT request_id, final_status, terminal_at FROM download_history "  # noqa: S608 - placeholders only
                    f"WHERE request_id IN ({marks}) ORDER BY queued_at, id",
                    chunk,
                ):
                    latest[int(request_id)] = (str(final_status), terminal_at)
        finally:
            conn.close()
    except sqlite3.Error as exc:
        logger.warning("auto-download: could not settle delivery states from history: %s", exc)
        return 0
    settled = 0
    for row in candidates:
        request_id = int(row["id"])
        entry = latest.get(request_id)
        if entry is None or entry[0] not in _SETTLED_STATUSES:
            continue
        ended = _parse_moment(entry[1])
        changed = _last_delivery_change(row)
        if ended is None or (changed is not None and ended <= changed):
            continue
        try:
            user_db.update_request(
                request_id,
                delivery_state=entry[0],
                delivery_updated_at=ended.isoformat(timespec="seconds"),
            )
        except sqlite3.Error as exc:
            logger.warning("auto-download: could not settle request %s: %s", request_id, exc)
            continue
        logger.info(
            "auto-download: request %s said %s but its download ended (%s); recorded",
            request_id,
            row.get("delivery_state"),
            entry[0],
        )
        settled += 1
    return settled


def reopen_stale_failures(
    user_db: UserDB,
    *,
    cooldown_days: int,
    db_path: str | None,
    provider_filter: str | None = "hardcover",
    max_reopen: int = MAX_RETRIES_PER_PASS,
    now: datetime | None = None,
    failure_cooldown: timedelta | None = None,
    only_kinds: frozenset[str] | None = None,
    reopened_ids: list[int] | None = None,
) -> int:
    """Return synced requests whose download never arrived to pending once the cooldown passes.

    ``failure_cooldown`` shortens the wait for a download that itself failed or that the stall
    timer ended (a dead release says nothing about when the book will turn up, so another
    release can be tried soon); interrupted downloads keep the ``cooldown_days`` wait.
    ``only_kinds`` limits which of those are reopened, and ``reopened_ids`` collects the ids.

    Nothing else retries them: the sync treats a request in any status as handled, and the
    auto-download pass only acts on pending ones. Reopening goes through
    ``UserDB.reopen_failed_request`` (upstream's own path for a failed request), which clears
    the chosen release and records why, so the normal pass searches again.

    Interrupted downloads are reopened as they were, and the same release may be picked again:
    a torrent already in the client is joined rather than added twice. A download that itself
    failed, or that the stall timer cancelled, has its release remembered on the request and
    skipped next time, and is given up on after ``MAX_FAILURE_RETRIES``. Other cancelled requests
    and rejected ones are decisions and are never touched. Returns how many were reopened; ``cooldown_days`` of 0 or less turns retries off,
    and without readable history nothing is retried, since guessing would be worse.
    """
    if cooldown_days <= 0 and failure_cooldown is None:
        return 0
    settle_delivery_states_from_history(user_db, db_path=db_path)
    moment = now or datetime.now(UTC)
    cutoff = moment - timedelta(days=cooldown_days) if cooldown_days > 0 else None
    failure_cutoff = moment - failure_cooldown if failure_cooldown is not None else None
    # A row can only be reopened once it is past the shorter of the two waits.
    earliest = max(c for c in (cutoff, failure_cutoff) if c is not None)

    stale: list[tuple[datetime, dict[str, Any], str]] = []
    for row in user_db.list_requests(status=RequestStatus.FULFILLED):
        state = str(row.get("delivery_state") or "none").lower()
        if state not in _RETRYABLE_DELIVERY_STATES and state != _CANCELLED:
            continue
        book_data = row.get("book_data")
        if provider_filter and (
            not isinstance(book_data, dict) or book_data.get("provider") != provider_filter
        ):
            continue
        changed = _last_delivery_change(row)
        if changed is None or changed > earliest:
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
        if kind is None or (state == _CANCELLED and kind != _STALLED):
            continue
        if only_kinds is not None and kind not in only_kinds:
            continue
        threshold = (
            failure_cutoff if kind in (_FAILED, _STALLED) and failure_cutoff is not None else cutoff
        )
        if threshold is None or _changed > threshold:
            continue
        try:
            if kind in (_FAILED, _STALLED):
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
            wait = (
                f"{int(failure_cooldown.total_seconds() // 60)}-minute"
                if failure_cooldown is not None and threshold is failure_cutoff
                else f"{cooldown_days}-day"
            )
            reason = f"Retrying after a {wait} cooldown ({kind}; delivery was {state})"
            result = user_db.reopen_failed_request(request_id, failure_reason=reason)
        except sqlite3.Error as exc:
            logger.warning("auto-download: could not reopen request %s: %s", request_id, exc)
            continue
        if result is not None:
            reopened += 1
            if reopened_ids is not None:
                reopened_ids.append(request_id)
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
    configured = isinstance(raw, list) and any(isinstance(item, dict) for item in raw)
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
    if configured:
        # A priority list was saved but nothing in it is usable. Falling back to every
        # source would search the ones the user turned off.
        logger.warning(
            "auto-download: every source in the priority list is disabled or unavailable"
        )
        return []
    # Nothing configured yet: every usable source for this content type, registry order.
    return [src["name"] for src in available if src.get("enabled")]


def _acted_on_since_listed(user_db: UserDB, request_id: int) -> bool:
    """True only when the request is read back and is positively no longer waiting.

    A pass lists its pending requests once and can run for minutes, so an admin may have
    fulfilled, rejected or cancelled one since. Anything doubtful (the read fails, the row
    is missing, an unexpected value) answers False, so the request is searched exactly as
    it was before this check existed.
    """
    try:
        current = user_db.get_request(request_id)
    except Exception:  # noqa: BLE001 - a failed read must never stop a download
        return False
    if not isinstance(current, dict):
        return False
    status = str(current.get("status") or "").lower()
    delivery = str(current.get("delivery_state") or "none").lower()
    closed = {RequestStatus.FULFILLED, RequestStatus.REJECTED, RequestStatus.CANCELLED}
    return status in closed or (status == RequestStatus.PENDING and delivery not in {"none", ""})


# Simpler queries tried, in order, when a request's own query finds no strict match.
MAX_QUERY_VARIANTS = 3
# "Find Your Why: A Practical Guide..." and "Dune - Deluxe Edition" both name the book
# before the separator.
_SUBTITLE_SPLIT = re.compile(r"^(?P<main>.+?)(?:\s*:\s+|\s+-\s+)\S")


def _without_subtitle(title: str, book: BookMetadata) -> str:
    """The title before a ':' or ' - ' subtitle; the title itself when that would not do.

    A prefix that is only series or author words ("Star Wars: Thrawn", "Alex Cross:
    Kiss the Girls") names a whole series rather than this book, so it is not taken;
    nor is one followed by only series words (the "Thrawn" series' "Star Wars:
    Thrawn"), where the prefix is a franchise.
    Nor is it when the metadata cannot say so: without a series name, or a subtitle
    the part after the separator holds, "Star Wars: Thrawn" would be searched and
    matched as "Star Wars".
    """
    match = _SUBTITLE_SPLIT.match(title)
    if not match:
        return title
    main = match.group("main").strip()
    words = set(significant_tokens(main))
    known = set(significant_tokens(" ".join([book.series_name or "", *(book.authors or [])])))
    rest = set(significant_tokens(title[len(main) :]))
    series = set(significant_tokens(book.series_name))
    if not words or words <= known or (rest and rest <= series):
        return title
    subtitle = set(significant_tokens(book.subtitle))
    confirmed = bool(subtitle) and subtitle <= rest
    if not (book.series_name or confirmed):
        return title
    return main


def query_variants(book: BookMetadata) -> list[tuple[str, str]]:
    """Simpler titles to search when the book's own search title finds no strict match.

    Returns ``(label, title)`` pairs, at most ``MAX_QUERY_VARIANTS``, each different
    from the title already searched and from the ones before it: the title without a
    subtitle, without an alternate title (", or There and Back Again"), and with its
    numbers written as digits. Each is searched and matched as the book's search title,
    so every strict-match rule still applies to it.
    """
    searched = book.search_title or book.title or ""
    candidates = [
        ("without subtitle", _without_subtitle(searched, book)),
        ("without alternate title", strip_alternate_title(searched)),
        ("numerals as digits", normalize_numerals(searched)),
    ]
    seen = {" ".join(searched.lower().split())}
    variants: list[tuple[str, str]] = []
    for label, title in candidates:
        key = " ".join(title.lower().split())
        if not key or key in seen or not significant_tokens(title):
            continue
        seen.add(key)
        variants.append((label, title.strip()))
    return variants[:MAX_QUERY_VARIANTS]


def auto_download_request(
    user_db: UserDB,
    request_row: dict[str, Any],
    *,
    sources: list[str],
    content_type: str,
    min_seeders: int,
    queue_release: Callable[..., tuple[bool, str | None]],
    admin_user_id: int,
    providers: dict[str, Any] | None = None,
    dead_peers: frozenset[str] = frozenset(),
    dead_releases: frozenset[tuple[str, str]] = frozenset(),
    recent_failures: frozenset[tuple[str, str]] | None = None,
) -> AutoDownloadOutcome:
    """Search, strict-match, and queue a single pending request.

    ``providers`` lets a pass share one metadata provider (and its HTTP connection) across its
    requests instead of building one per request.
    """
    from shelfmark.core.requests_service import RequestServiceError, fulfil_request
    from shelfmark.metadata_providers import (
        get_provider,
        get_provider_kwargs,
        is_provider_registered,
    )

    request_id = int(request_row["id"])
    if _acted_on_since_listed(user_db, request_id):
        logger.info("auto-download: request %s was handled since this pass listed it", request_id)
        return AutoDownloadOutcome(request_id, "skipped", "no longer pending")
    book_data = request_row.get("book_data") or {}
    if not isinstance(book_data, dict):
        return AutoDownloadOutcome(request_id, "skipped", "no book_data")

    provider_name = book_data.get("provider")
    provider_id = book_data.get("provider_id") or book_data.get("id")
    if not provider_name or not provider_id or not is_provider_registered(provider_name):
        return AutoDownloadOutcome(request_id, "skipped", "no usable provider/id")

    try:
        prov = providers.get(provider_name) if providers is not None else None
        if prov is None:
            prov = get_provider(provider_name, **get_provider_kwargs(provider_name))
            if providers is not None:
                providers[provider_name] = prov
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
        # Close it, or it sits in Needs Review and is looked up again every pass.
        # Same end state as an admin approving it with no release.
        try:
            user_db.update_request(
                request_id,
                expected_current_status=RequestStatus.PENDING,
                status=RequestStatus.FULFILLED,
                delivery_state=QueueStatus.COMPLETE,
                delivery_updated_at=datetime.now(UTC).isoformat(timespec="seconds"),
                admin_note=IN_LIBRARY_NOTE,
                reviewed_by=admin_user_id,
                reviewed_at=datetime.now(UTC).isoformat(timespec="seconds"),
            )
        except ValueError as exc:
            # Changed under us (an admin acted on it mid-pass); leave it as it now is.
            logger.info(
                "auto-download: request %s already in library, not closed: %s", request_id, exc
            )
            return AutoDownloadOutcome(request_id, "in_library", "already in library")
        logger.info(
            "auto-download: request %s (%s) already in library; closed",
            request_id,
            book.title,
        )
        return AutoDownloadOutcome(request_id, "in_library", "already in library; closed")

    audiobook_formats = _audiobook_formats()
    ebook_formats = _ebook_formats()
    languages = _wanted_languages(coerce_int(request_row.get("user_id"), 0) or None)
    # Releases that already failed for this request, so a retry does not pick the same one. Only
    # those whose failure is recent: an older one is given another chance (None keeps all).
    remembered = _failed_release_keys(book_data)
    if recent_failures is not None:
        remembered &= recent_failures
    failed_releases = remembered | dead_releases
    source_errors: list[str] = []
    skipped_pack = False

    def _walk(searched_book: BookMetadata) -> AutoDownloadOutcome | None:
        """Search the sources in priority order; the first with a strict match wins."""
        nonlocal skipped_pack
        for source_name in sources:
            # The requester's id lets the search plan apply their default languages.
            _source, releases, search_error = search_source_releases(
                source_name,
                searched_book,
                expand_search=True,
                content_type=content_type,
                user_id=coerce_int(request_row.get("user_id"), 0) or None,
            )
            if search_error:
                source_errors.append(f"{source_name}: {search_error}")
            match_kwargs: dict[str, Any] = {
                "content_type": content_type,
                "min_seeders": min_seeders,
                "audiobook_formats": audiobook_formats,
                "ebook_formats": ebook_formats,
                "languages": languages,
            }
            releases = [r for r in releases if (r.source, r.source_id) not in failed_releases]
            if not any(
                strict_match(release, searched_book, **match_kwargs) for release in releases
            ):
                _fill_missing_authors(releases, searched_book, match_kwargs)
            candidates = [
                release
                for release in releases
                if strict_match(release, searched_book, **match_kwargs)
            ]
            chosen = None
            release_data: dict[str, Any] = {}
            ranked = sorted(
                candidates, key=lambda r: _release_sort_key(r, content_type), reverse=True
            )
            thin = _undersized_copies(ranked, content_type)
            ranked = _demote_undersized(ranked, content_type)
            ranked = _demote_dead_sources(ranked, dead_peers)
            for index, candidate in enumerate(ranked):
                if index >= MAX_PACK_CHECKS:
                    break  # the best few were all packs; don't queue an uninspected tail
                data = build_release_data(candidate, searched_book, content_type)
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
        return None

    outcome = _walk(book)
    if outcome is not None:
        return outcome
    # Nothing matched the request's own query. Try a few simpler ones, stopping at the
    # first that yields a strict match; the match rules are the same for each.
    for label, title in query_variants(book):
        logger.info(
            "auto-download: request %s (%s): no strict match, trying %s: %r",
            request_id,
            book.title,
            label,
            title,
        )
        outcome = _walk(replace(book, search_title=title))
        if outcome is not None:
            if outcome.status == "queued":
                logger.info(
                    "auto-download: request %s matched on the query %s: %r",
                    request_id,
                    label,
                    title,
                )
            return outcome

    if source_errors:
        # A source that failed has not said the book is missing.
        detail = "search failed: " + "; ".join(source_errors)
        logger.warning("auto-download: request %s (%s): %s", request_id, book.title, detail)
        return AutoDownloadOutcome(request_id, "error", detail)

    if skipped_pack:
        logger.info("auto-download: request %s (%s): only multi-book packs", request_id, book.title)
        return AutoDownloadOutcome(request_id, "no_match", "only multi-book packs found")
    logger.info("auto-download: no strict match for request %s (%s)", request_id, book.title)
    return AutoDownloadOutcome(request_id, "no_match", "no strict match across sources")


FAILURE_RETRY_MINUTES_SETTING = "AUTO_DOWNLOAD_FAILURE_RETRY_MINUTES"


def retry_failed_downloads(
    user_db: UserDB,
    *,
    queue_release: Callable[..., tuple[bool, str | None]],
    provider_filter: str | None = "hardcover",
    admin_user_id: int | None = None,
    db_path: str | None = None,
) -> dict[str, int] | None:
    """Pick another release for requests whose download just failed or stalled.

    Runs between full passes. Only the requests it reopens are searched, so it costs nothing
    when no download has failed. The release that failed is noted on the request first, and a
    request is given up on after ``MAX_FAILURE_RETRIES`` attempts. None when switched off.
    """
    minutes = coerce_int(app_config.get(FAILURE_RETRY_MINUTES_SETTING, 0), 0)
    if minutes <= 0 or not bool(app_config.get("AUTO_DOWNLOAD_ENABLED", False)):
        return None
    reopened_ids: list[int] = []
    reopen_stale_failures(
        user_db,
        cooldown_days=0,
        db_path=db_path,
        provider_filter=provider_filter,
        failure_cooldown=timedelta(minutes=minutes),
        only_kinds=frozenset({_FAILED, _STALLED}),
        reopened_ids=reopened_ids,
    )
    # A failed download the status poll already reopened is pending with no release; those
    # are searched here as well, so they are not left for the next full pass.
    pending_after_failure = {
        int(row["id"])
        for row in user_db.list_requests(status="pending")
        if row.get("last_failure_reason")
        and str(row.get("delivery_state") or "none").lower() in {"none", ""}
        and isinstance(row.get("book_data"), dict)
        and row["book_data"].get("failed_releases")
        and len(row["book_data"]["failed_releases"]) < MAX_FAILURE_RETRIES
    }
    ids = frozenset(reopened_ids) | pending_after_failure
    if not ids:
        return None
    logger.info("auto-download: trying another release for %d request(s)", len(ids))
    return auto_download_pending(
        user_db,
        queue_release=queue_release,
        provider_filter=provider_filter,
        admin_user_id=admin_user_id,
        db_path=db_path,
        only_request_ids=ids,
    )


def auto_download_pending(
    user_db: UserDB,
    *,
    queue_release: Callable[..., tuple[bool, str | None]],
    provider_filter: str | None = "hardcover",
    admin_user_id: int | None = None,
    db_path: str | None = None,
    ignore_backoff: bool = False,
    only_request_ids: frozenset[int] | None = None,
) -> dict[str, int]:
    """Run the auto-download pass over all eligible pending requests.

    ``only_request_ids`` limits the pass to those requests (the quick retry after a failed
    download), and skips the 7-day reopen.

    ``ignore_backoff`` (a manual "Sync now") searches every request, however many passes in a
    row found nothing for it.

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
    reopened = (
        0
        if only_request_ids is not None
        else reopen_stale_failures(
            user_db, cooldown_days=retry_days, db_path=db_path, provider_filter=provider_filter
        )
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
    providers: dict[str, Any] = {}
    backoff_days = (
        0
        if ignore_backoff
        else coerce_int(app_config.get(BACKOFF_SETTING, DEFAULT_BACKOFF_MAX_DAYS), 0)
    )

    dead_peers = recent_failed_peers(db_path)
    dead_releases = recent_dead_releases(db_path)
    # Without a history to read there is no way to tell how old a failure is, so all count.
    recent_failures = recent_failed_releases(db_path) if db_path else None
    for row in pending:
        if only_request_ids is not None and int(row["id"]) not in only_request_ids:
            continue
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

        if not _search_is_due(book_data, datetime.now(UTC), backoff_days):
            logger.debug(
                "auto-download: request %s is waiting out its no-match backoff", row.get("id")
            )
            summary["skipped"] += 1
            continue

        started = time.monotonic()
        try:
            outcome = auto_download_request(
                user_db,
                row,
                sources=sources,
                content_type=content_type,
                min_seeders=min_seeders,
                queue_release=queue_release,
                admin_user_id=admin_user_id,
                providers=providers,
                dead_peers=dead_peers,
                dead_releases=dead_releases,
                recent_failures=recent_failures,
            )
        except Exception:
            # One bad request must not take the rest of the pass with it.
            logger.exception("auto-download: request %s failed unexpectedly", row.get("id"))
            summary["error"] += 1
            continue
        summary[outcome.status] = summary.get(outcome.status, 0) + 1
        _existence_check(
            user_db,
            row,
            outcome,
            admin_user_id=admin_user_id,
            content_type=content_type,
            # What is left of this request's search budget; the store lookups spend it.
            budget_seconds=max(
                0.0, search_deadline.budget_seconds() - (time.monotonic() - started)
            ),
            summary=summary,
        )
        _record_search_outcome(user_db, int(row["id"]), outcome.status, datetime.now(UTC))

    logger.info("auto-download pass complete: %s", summary)
    return summary


def _existence_check(
    user_db: UserDB,
    row: dict[str, Any],
    outcome: AutoDownloadOutcome,
    *,
    admin_user_id: int,
    content_type: str,
    budget_seconds: float,
    summary: dict[str, int],
) -> None:
    """Hand a request's outcome to the audiobook existence check (see ``existence_check``).

    Its failure must not take the pass with it, so it is logged and the pass moves on.
    """
    from shelfmark.core import existence_check

    try:
        verdict = existence_check.after_request(
            user_db,
            row,
            outcome,
            admin_user_id=admin_user_id,
            content_type=content_type,
            budget_seconds=budget_seconds,
        )
    except Exception:
        logger.exception("auto-download: existence check for request %s failed", row.get("id"))
        return
    if verdict == "not_listed":
        summary["not_listed"] = summary.get("not_listed", 0) + 1
