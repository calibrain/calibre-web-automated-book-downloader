"""Anna's Archive search, metadata parsing, and MD5 mirror download cascade."""

import itertools
import json
import re
import time
import unicodedata
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn, TypedDict
from urllib.parse import quote, urlparse

import requests
from bs4 import BeautifulSoup, Tag
from bs4.element import NavigableString

from shelfmark.bypass.challenge import MAX_CHALLENGE_HTML_CHARS, challenge_marker
from shelfmark.config.env import DEBUG_SKIP_SOURCES
from shelfmark.core import search_deadline
from shelfmark.core.config import config
from shelfmark.core.logger import setup_logger
from shelfmark.core.models import SearchFilters
from shelfmark.core.utils import CONTENT_TYPES
from shelfmark.download import http as downloader
from shelfmark.download import network
from shelfmark.release_sources import BrowseRecord
from shelfmark.release_sources.direct_download.common import (
    MIN_VALID_FILE_SIZE as _MIN_VALID_FILE_SIZE,
)
from shelfmark.release_sources.direct_download.common import (
    DirectDownloadUnavailableError,
    ParsedSearchResult,
    get_attr,
    get_supported_formats,
    html_response_text,
    language_alias_to_code,
    normalize_language_token,
    normalize_requested_languages,
    normalize_size,
    parse_search_items,
    parse_search_page,
)
from shelfmark.release_sources.direct_download.common import (
    book_matches_requested_languages as _book_matches_requested_languages,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator
    from pathlib import Path
    from threading import Event

    from shelfmark.core.search_plan import ReleaseSearchPlan
    from shelfmark.metadata_providers import BookMetadata

logger = setup_logger(__name__)


class SourcePriorityEntry(TypedDict):
    """Normalized source priority entry from config."""

    id: str
    enabled: bool


def _raise_runtime_error(message: str) -> NoReturn:
    raise RuntimeError(message)


def _parse_source_priority_entries(
    value: object,
    *,
    allowed_ids: set[str] | None = None,
    excluded_ids: set[str] | None = None,
) -> list[SourcePriorityEntry]:
    """Normalize orderable-list config values into typed source entries."""
    if not isinstance(value, list):
        return []

    entries: list[SourcePriorityEntry] = []
    for item in value:
        if not isinstance(item, dict):
            continue

        source_id = item.get("id")
        if not isinstance(source_id, str):
            continue

        if allowed_ids is not None and source_id not in allowed_ids:
            continue
        if excluded_ids is not None and source_id in excluded_ids:
            continue

        entries.append({"id": source_id, "enabled": bool(item.get("enabled", True))})

    return entries


def _html_response_url(response: str | tuple[str, str]) -> str | None:
    """The URL that actually answered, when the downloader was asked to report it.

    None for the plain-string shape, so a caller can fall back to what it requested.
    """
    if isinstance(response, tuple):
        return response[1] or None
    return None


def _first_stripped_text(tag: Tag | None) -> str | None:
    """Return the first non-empty stripped string from a tag."""
    if tag is None:
        return None

    for text in tag.stripped_strings:
        return text
    return None


def _iter_child_tags(tag: Tag) -> Iterable[Tag]:
    """Iterate only over child tags, skipping text nodes."""
    for child in tag.children:
        if isinstance(child, Tag):
            yield child


def _find_first_anchor_with_text(
    container: BeautifulSoup | Tag,
    text: str,
    *,
    contains: bool = False,
) -> Tag | None:
    """Find the first anchor whose text matches the requested value."""
    expected = text.lower()
    for anchor in container.find_all("a", href=True):
        anchor_text = anchor.get_text(strip=True)
        if not anchor_text:
            continue
        candidate = anchor_text.lower()
        if candidate == expected or (contains and expected in candidate):
            return anchor
    return None


def _find_text_node(container: BeautifulSoup | Tag, needle: str) -> NavigableString | None:
    """Find a text node containing a case-insensitive substring."""
    expected = needle.lower()
    for text_node in container.find_all(string=True):
        if isinstance(text_node, NavigableString) and expected in text_node.strip().lower():
            return text_node
    return None


def _tag_has_class_containing(tag: Tag, needle: str) -> bool:
    """Check whether a tag has a CSS class containing a substring."""
    class_values = tag.get("class")
    if isinstance(class_values, str):
        return needle in class_values
    if isinstance(class_values, list):
        return any(isinstance(value, str) and needle in value for value in class_values)
    return False


_aa_slow_rotation = itertools.count()
_url_source_types: dict[str, str] = {}

if DEBUG_SKIP_SOURCES:
    logger.warning("DEBUG_SKIP_SOURCES active: skipping sources %s", DEBUG_SKIP_SOURCES)

_DOWNLOAD_SOURCES = [
    ("welib", "Welib", ["welib.org"]),
    ("aa-fast", "Anna's Archive (Fast)", ["/dyn/api/fast_download"]),
    ("aa-slow-wait", "Anna's Archive (Waitlist)", []),  # Matched via _url_source_types
    ("aa-slow-nowait", "Anna's Archive", []),  # Matched via _url_source_types
    ("aa-slow", "Anna's Archive", ["/slow_download/", "annas-"]),  # Fallback for untagged AA URLs
    ("libgen", "Libgen", ["libgen"]),
    ("zlib", "Z-Library", ["z-lib", "zlibrary"]),
]

_SOURCE_FAILURE_THRESHOLD = 4
_AA_COUNTDOWN_MAX_SECONDS = 300


# --- Distant-path language detection ---

_DISTANT_PATH_EXTENSIONS = (
    "epub",
    "mobi",
    "azw3",
    "fb2",
    "djvu",
    "cbz",
    "cbr",
    "pdf",
    "zip",
    "rar",
    "m4b",
    "mp3",
)
_DISTANT_PATH_EXTENSION_PATTERN = "|".join(re.escape(e) for e in _DISTANT_PATH_EXTENSIONS)
_DISTANT_PATH_PATTERN = re.compile(
    rf"(?:[A-Za-z0-9._-]+/)?[A-Za-z]:(?:\\|/)[^\n\r<>\"]+?\.(?:{_DISTANT_PATH_EXTENSION_PATTERN})\b",
    re.IGNORECASE,
)
_DISTANT_PATH_FALLBACK_PATTERN = re.compile(
    r"(?:[A-Za-z0-9._-]+/)?[A-Za-z]:(?:\\|/)[^\n\r<>\"]+",
    re.IGNORECASE,
)
_BRACKETED_LANGUAGE_CODE_PATTERN = re.compile(
    r"\[(?:bd[\s._-]*)?([A-Za-z]{2,3})\]",
    re.IGNORECASE,
)
_KEYED_LANGUAGE_CODE_PATTERN = re.compile(
    r"\b(?:bd|lang(?:uage)?)\s*[:._-]?\s*([A-Za-z]{2,3})\b",
    re.IGNORECASE,
)
_LANGUAGE_CODE_TOKEN_PATTERN = re.compile(
    r"(?:^|[\s_./\\\-\[(])([A-Za-z]{2,3})(?=$|[\s_./\\\-)\]])"
)
_LANGUAGE_NAME_TOKEN_PATTERN = re.compile(r"[a-z]{4,}(?:-[a-z0-9]+)?")
_LANGUAGE_PLACEHOLDERS = frozenset({"", "-", "--", "unknown", "unk", "n/a", "na"})
# Short codes that appear in common words — require bracket/key context to accept
_AMBIGUOUS_SHORT_LANGUAGE_CODES = frozenset({"de", "en", "it", "la", "no", "or", "is", "in"})

# Sources that require Cloudflare bypass
_CF_BYPASS_REQUIRED = frozenset({"aa-slow-nowait", "aa-slow-wait", "zlib", "welib"})

# Sources whose URLs come from AA page (multiple mirrors)
_AA_PAGE_SOURCES = frozenset({"aa-slow-nowait", "aa-slow-wait"})


def _is_language_from_path_enabled() -> bool:
    return bool(config.get("DIRECT_DOWNLOAD_LANGUAGE_FROM_PATH", False))


def _fold_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(c for c in normalized if not unicodedata.combining(c)).lower()


def _extract_distant_path(row: Tag, *, enabled: bool) -> str | None:
    """Extract the Windows-style file path from an AA search result row."""
    if not enabled:
        return None

    def _normalize_candidate(text: str) -> str:
        normalized = re.sub(r"\s*([\\/])\s*", r"\1", text)
        normalized = re.sub(r":\s*([\\/])", r":\1", normalized)
        return re.sub(
            r"\s+\.(epub|mobi|azw3|fb2|djvu|cbz|cbr|pdf|zip|rar|m4b|mp3)\b",
            r".\1",
            normalized,
            flags=re.IGNORECASE,
        )

    candidates = [row.get_text(" ", strip=True)]
    for cell in row.find_all("td"):
        cell_text = cell.get_text(" ", strip=True)
        if cell_text:
            candidates.append(cell_text)

    best: str | None = None
    for text in candidates:
        for match in _DISTANT_PATH_PATTERN.findall(_normalize_candidate(text)):
            candidate = match.strip().rstrip(".,;")
            if best is None or len(candidate) > len(best):
                best = candidate

    if best is not None:
        return best

    for text in candidates:
        for match in _DISTANT_PATH_FALLBACK_PATTERN.findall(_normalize_candidate(text)):
            candidate = match.strip().rstrip(".,;")
            if best is None or len(candidate) > len(best):
                best = candidate

    return best


def _detect_language_from_distant_path(path: str | None) -> str | None:
    """Infer a language code from distant-path tags such as [BD FR] or [Fr]."""
    if not path:
        return None

    aliases = language_alias_to_code()
    if not aliases:
        return None

    folded_path = _fold_text(path)
    strong_candidates: list[str] = []

    for code in _BRACKETED_LANGUAGE_CODE_PATTERN.findall(path):
        normalized = normalize_language_token(code)
        if normalized in aliases:
            strong_candidates.append(aliases[normalized])

    for code in _KEYED_LANGUAGE_CODE_PATTERN.findall(path):
        normalized = normalize_language_token(code)
        if normalized in aliases:
            strong_candidates.append(aliases[normalized])

    non_ambiguous = [c for c in strong_candidates if c not in _AMBIGUOUS_SHORT_LANGUAGE_CODES]
    if non_ambiguous:
        return non_ambiguous[0]

    for token in _LANGUAGE_NAME_TOKEN_PATTERN.findall(folded_path):
        normalized = normalize_language_token(token)
        if normalized in aliases:
            candidate = aliases[normalized]
            if candidate not in _AMBIGUOUS_SHORT_LANGUAGE_CODES:
                return candidate

    if strong_candidates:
        return strong_candidates[0]

    for code in _LANGUAGE_CODE_TOKEN_PATTERN.findall(path):
        normalized = normalize_language_token(code)
        if normalized in _AMBIGUOUS_SHORT_LANGUAGE_CODES:
            continue
        if normalized in aliases:
            return aliases[normalized]

    return None


def _is_missing_or_placeholder_language(language: str | None) -> bool:
    if language is None:
        return True
    return normalize_language_token(language) in _LANGUAGE_PLACEHOLDERS


def _is_configured_zlib_link(url: str) -> bool:
    """Return True when a URL belongs to a configured Z-Library mirror."""
    from shelfmark.core.mirrors import get_zlib_cookie_domains

    hostname = (urlparse(url).hostname or "").lower()
    if not hostname:
        return False

    base_domain = ".".join(hostname.split(".")[-2:]) if "." in hostname else hostname

    for domain in get_zlib_cookie_domains():
        candidate = str(domain).lower()
        if hostname == candidate or hostname.endswith(f".{candidate}") or base_domain == candidate:
            return True

    return False


def _get_md5_url_template(source_id: str) -> str | None:
    """Get URL template for MD5-based sources from centralized config."""
    from shelfmark.core import mirrors

    if source_id == "zlib":
        return mirrors.get_zlib_url_template()
    if source_id == "welib":
        return mirrors.get_welib_url_template()
    return None


def _get_libgen_domains() -> list[str]:
    """Get LibGen domains from centralized config."""
    from shelfmark.core import mirrors

    return mirrors.get_libgen_mirrors()


_LIBGEN_GET_PATTERNS = [
    re.compile(
        r'<a\s+href=["\']([^"\']*get\.php\?md5=[^"\']+&key=[^"\']+)["\'][^>]*>\s*<h2[^>]*>GET</h2>\s*</a>',
        re.IGNORECASE,
    ),
    re.compile(
        r'<a[^>]+href=["\']([^"\']*get\.php\?md5=[^"\']+&(?:amp;)?key=[^"\']+)["\']', re.IGNORECASE
    ),
    re.compile(
        r'<a\s+href=["\']([^"\']*get\.php[^"\']*)["\'][^>]*>[\s\S]*?<h2[^>]*>GET</h2>',
        re.IGNORECASE,
    ),
    re.compile(
        r'href=["\']([^"\']*get\.php\?[^"\']*md5=[^"\']*&[^"\']*key=[^"\']+)["\']', re.IGNORECASE
    ),
]


def _get_source_priority() -> list[SourcePriorityEntry]:
    """Get the full source priority list.

    Fast sources come from user config (FAST_SOURCES_DISPLAY).
    Slow sources come from user config.
    """
    from shelfmark.core import mirrors

    fast_sources = _parse_source_priority_entries(
        config.get("FAST_SOURCES_DISPLAY"),
        allowed_ids={"aa-fast", "libgen"},
    )
    has_donator_key = bool(config.get("AA_DONATOR_KEY"))

    for source in fast_sources:
        if (not mirrors.has_download_source_mirror_configuration(source["id"])) or (
            source["id"] == "aa-fast" and not has_donator_key
        ):
            source["enabled"] = False

    slow_sources = _parse_source_priority_entries(
        config.get("SOURCE_PRIORITY"),
        excluded_ids={"aa-fast", "libgen", "oceanofpdf"},
    )
    for source in slow_sources:
        if not mirrors.has_download_source_mirror_configuration(source["id"]):
            source["enabled"] = False

    return fast_sources + slow_sources


def _is_source_enabled(source_id: str) -> bool:
    """Check if a source is enabled in the priority config.

    Returns False for unknown sources.
    """
    for item in _get_source_priority():
        if item["id"] == source_id:
            return item.get("enabled", True)
    return False


def get_unavailable_reason() -> str | None:
    """Return a user-facing reason when Direct Download cannot be used."""
    from shelfmark.core import mirrors

    if not config.get("DIRECT_DOWNLOAD_ENABLED", False):
        return (
            "Direct Download is disabled. Enable the source in Settings and add your mirror URLs."
        )

    if not mirrors.has_aa_mirror_configuration():
        return (
            "Direct Download is not configured. Add at least one Anna's Archive mirror URL in "
            "Settings."
        )

    return None


def ensure_available() -> None:
    """Raise a source-unavailable error when Direct Download is disabled or unconfigured."""
    reason = get_unavailable_reason()
    if reason:
        raise SearchUnavailableError(reason)


SearchUnavailableError = DirectDownloadUnavailableError


# Markers that prove a 200 really came from Anna's Archive, and markers that mean we
# are looking at a protection interstitial rather than the site. A page with neither
# is a domain that answers but is not AA - seized, parked or for sale.
#
# Deliberately structural rather than the domain name: a parking page's whole job is
# to display the domain it is squatting on, so "annas-archive" matches the very pages
# this is meant to catch. These paths only exist on the real site.
_AA_PAGE_MARKERS = (
    "/md5/",
    "aarecord",
    "anna's archive",
    "/dyn/",
    "/datasets",
    "/fast_download",
    "/slow_download",
)


def _looks_like_aa_page(html: str) -> bool:
    """Whether ``html`` is recognisably Anna's Archive itself."""
    lowered = html.lower()
    return any(marker in lowered for marker in _AA_PAGE_MARKERS)


def _looks_like_challenge_page(html: str) -> bool:
    """Whether ``html`` is a protection interstitial rather than the site behind it.

    Delegates to the shared detector rather than substring-matching here. A bare
    "ddos-guard"/"cloudflare" scan flags the protected site's *own* pages: DDoS-Guard
    links its endpoints on everything it fronts, and AA ships a `DDOS-GUARD` comment in
    the inline JS on every page it serves. That misread every real AA response that was
    not a results table as an unsolved challenge, and sent users off to fix a bypasser
    that had just succeeded - see #1289/#1292. `challenge_marker` caps its scan at
    64 KB, which is what separates a few-KB interstitial from the page behind it.
    """
    return challenge_marker(html) is not None


# Pages already fetched during the search in flight, keyed by URL. Scoped to one
# DirectDownload.search() so nothing is carried between requests.
_search_page_cache: ContextVar[dict[str, tuple[str, Tag | None]] | None] = ContextVar(
    "aa_search_page_cache", default=None
)


@contextmanager
def search_page_reuse() -> Iterator[None]:
    """Fetch each distinct AA search URL at most once per search.

    One search asks AA for the same URL more than once. The language-filter retry in
    `search()` re-runs every title variant, and when DIRECT_DOWNLOAD_LANGUAGE_FROM_PATH
    is on the requested language is applied locally instead of as `&lang=`, so both
    passes build a byte-identical URL - the retry differs only in the filtering it does
    to the response it already had. A repeat is not a cheap round trip either: AA is
    behind DDoS-Guard, so each one is a fresh browser solve, tens of seconds that buy
    nothing. See issue #1285.
    """
    token = _search_page_cache.set({})
    try:
        yield
    finally:
        _search_page_cache.reset(token)


def _is_reusable_answer(result: tuple[str, Tag | None]) -> bool:
    """Whether a fetched page is an answer, rather than a giving-up worth retrying.

    `_fetch_search_table_uncached` exists to rotate past mirrors that are not actually AA,
    and when it runs out of them it *returns* instead of raising: a page with no results
    table and no marker. Storing that would hand the language-filter retry - the pass this
    cache exists for - a mirror set that may have recovered in between (DNS rotation, a
    mirror coming back), turning a transient outage into "this book has no releases". A
    real "No files found." is an answer and is worth keeping.
    """
    html, tbody = result
    return tbody is not None or "No files found." in html or _looks_like_aa_page(html)


# How much of an unreadable search page to quote in the debug log. Enough to carry the
# <head> - title, injected challenge scripts - without pasting a 180 KB page into a log
# file that ships inside the debug bundle.
_PAGE_FINGERPRINT_CHARS = 700
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def _log_untabled_search_page(url: str, html: str) -> None:
    """Record why a search page with no results table is about to be classified.

    #1289 cost a full investigation because the log said only "unsolved protection
    challenge" while FlareSolverr said "Challenge solved!", and the debug bundle carries
    no response bodies - there was no way to tell a real AA page from an interstitial
    after the fact. These are the facts that would have settled it in one line: the size
    (the 64 KB cap is what separates the two), which markers matched, and the head of
    the document.

    Diagnostics must never be the reason a search fails, so this swallows its own errors.
    """
    try:
        title_match = _TITLE_RE.search(html[: _PAGE_FINGERPRINT_CHARS * 4])
        title = " ".join(title_match.group(1).split())[:120] if title_match else "<none>"
        lowered = html.lower()
        aa_markers = [marker for marker in _AA_PAGE_MARKERS if marker in lowered]
        logger.info(
            "Search page has no results table: %s (bytes=%d, title=%r, aa_markers=%s, "
            "challenge_marker=%r, over_challenge_size_cap=%s)",
            url,
            len(html),
            title,
            aa_markers or "none",
            challenge_marker(html),
            len(html) > MAX_CHALLENGE_HTML_CHARS,
        )
        logger.debug(
            "Untabled search page head (%d of %d bytes): %s",
            min(len(html), _PAGE_FINGERPRINT_CHARS),
            len(html),
            html[:_PAGE_FINGERPRINT_CHARS],
        )
    except Exception:
        logger.debug("Could not fingerprint the untabled search page", exc_info=True)


def _fetch_search_table(url: str, selector: network.AAMirrorSelector) -> tuple[str, Tag | None]:
    """Fetch the AA search page, reusing one already fetched during this search."""
    cache = _search_page_cache.get()
    if cache is not None and url in cache:
        logger.debug("Reusing search page already fetched for this search: %s", url)
        return cache[url]

    result = _fetch_search_table_uncached(url, selector)

    if cache is not None and _is_reusable_answer(result):
        cache[url] = result
    return result


def _fetch_search_table_uncached(
    url: str, selector: network.AAMirrorSelector
) -> tuple[str, Tag | None]:
    """Fetch the AA search page, retrying past mirrors that are not actually AA.

    A parked or seized domain answers 200 with a page that has no results table and no
    "No files found." - indistinguishable from a broken search unless we check whether
    the response looks like AA at all. Those mirrors are quarantined for the session so
    later searches skip them instead of paying the timeout again.
    """
    attempt_url = url
    for _ in range(len(network.get_available_aa_urls()) or 1):
        # Every mirror shares the protection, so once the search budget is gone another
        # mirror is another full solve nobody is still waiting for.
        if search_deadline.expired():
            raise SearchUnavailableError(search_deadline.deadline_message())

        # include_response_url is what makes the diagnostics below name the mirror that
        # actually answered. html_get_page rotates mirrors and follows redirects on its
        # own, so `attempt_url` is only where this iteration started: #1298's bundle
        # reported the untabled page against annas-archive.gl when the body had come
        # from .pk, which is precisely the triage cost #1289 added the line to remove.
        response = downloader.html_get_page(
            attempt_url,
            selector=selector,
            allow_bypasser_fallback=True,
            include_response_url=True,
        )
        html = html_response_text(response)
        # Checked on the body, not on `response`: with include_response_url the give-up
        # shape is the tuple ("", url), and a tuple is truthy.
        if not html:
            # Network/mirror exhaustion path bubbles up so API can notify clients.
            # html_get_page records the concrete give-up reason on the selector; fall
            # back to the generic line only if nothing was recorded.
            detail = getattr(selector, "last_failure", None) or (
                "Network restricted or mirrors are blocked."
            )
            raise SearchUnavailableError(f"Unable to reach download source. {detail}")

        answered_url = _html_response_url(response) or attempt_url
        soup = BeautifulSoup(html, "html.parser")
        table = soup.find("table")
        if isinstance(table, Tag):
            return html, table
        if table is not None:
            msg = f"Expected results table tag, got {type(table).__name__}"
            raise TypeError(msg)
        if "No files found." in html:
            # A real, genuinely empty answer from a healthy mirror.
            return html, None

        # A search page with no table is the one shape we cannot read off the response
        # alone, and the response body is not in the debug bundle. Fingerprint it here
        # so the next report says which branch fired and why, rather than costing
        # another round of guesswork - see #1289.
        _log_untabled_search_page(answered_url, html)

        if _looks_like_aa_page(html):
            # A real AA response in a shape the caller should report as drift. Checked
            # ahead of the challenge branch: AA's own pages carry the protection's
            # markers, so an interstitial is only the better explanation once the page
            # has nothing of AA's about it. A genuine interstitial has no AA markers.
            return html, None
        if _looks_like_challenge_page(html):
            # The bypass did not actually clear the protection - the interstitial is
            # what came back. Rotating is pointless (every mirror shares the same
            # protection) and reporting it as an empty result is worse: the user is
            # told their query found nothing when the search never ran.
            #
            # The wording no longer blames the bypasser outright. In #1292 it was
            # reachable and working, and the page it was handed was DDoS-Guard's manual
            # CAPTCHA - so "check that the bypasser is working" was the one piece of
            # advice guaranteed to waste the reporter's time. Name the marker instead
            # and let the two causes be told apart.
            msg = (
                "Anna's Archive answered with a protection challenge that was not "
                f"cleared (marker={challenge_marker(html)!r}). If the bypasser reports "
                "solving it, the host is serving a manual CAPTCHA that no bypasser can "
                "answer - try again shortly. Otherwise check that the bypasser is "
                "reachable and working."
            )
            raise SearchUnavailableError(msg)

        new_base, action = selector.next_mirror_or_rotate_dns(
            fatal=True, reason="responded without an Anna's Archive page"
        )
        if action not in ("mirror", "dns") or not new_base:
            return html, None
        attempt_url = selector.rewrite(url)
        logger.info("Retrying search on %s", new_base)

    return "", None


def search_books(query: str, filters: SearchFilters) -> list[BrowseRecord]:
    """Search for books matching the query.

    Args:
        query: Search term (ISBN, title, author, etc.)
        filters: Search filters (language, format, content type, etc.)

    Returns:
        List[BrowseRecord]: List of matching books

    Raises:
        SearchUnavailableError: If Anna's Archive cannot be reached
        Exception: If parsing fails

    """
    query_html = quote(query)

    if filters.isbn:
        isbns = " || ".join([f"('isbn13:{isbn}' || 'isbn10:{isbn}')" for isbn in filters.isbn])
        query_html = quote(f"({isbns}) {query}")

    filters_query = ""

    path_language_enabled = _is_language_from_path_enabled()
    requested_langs = normalize_requested_languages(filters.lang)

    # When path-language inference is on and a language is requested, skip the
    # server-side &lang= filter: lgli files often have no AA language metadata
    # and would be excluded before we can infer language from the distant path.
    # Local filtering below handles the narrowing instead.
    if not (path_language_enabled and requested_langs):
        for value in filters.lang or []:
            if value and value != "all":
                filters_query += f"&lang={quote(value)}"

    if filters.sort and filters.sort != "relevance":
        filters_query += f"&sort={quote(filters.sort)}"

    if filters.content:
        for value in filters.content:
            filters_query += f"&content={quote(value)}"

    formats_to_use = filters.format or get_supported_formats()

    index = 1
    for filter_type, filter_values in vars(filters).items():
        if filter_type in ("author", "title") and filter_values:
            for value in filter_values:
                filters_query += f"&termtype_{index}={filter_type}&termval_{index}={quote(value)}"
                index += 1

    selector = network.AAMirrorSelector()

    url = (
        f"{network.get_aa_base_url()}"
        f"/search?index=&page=1&display=table"
        f"&acc=aa_download&acc=external_download"
        f"&ext={'&ext='.join(formats_to_use)}"
        f"&q={query_html}"
        f"{filters_query}"
    )

    # AA gates /search behind a DDoS-Guard JS challenge, which every mirror shares. Rotating
    # to another mirror only collects another 403, so let the bypasser solve it.
    html, tbody = _fetch_search_table(url, selector)
    if tbody is None:
        if "No files found." in html:
            logger.info("No books found for query: %s", query)
            return []
        logger.warning("No results table found for query: %s", query)
        msg = "No books found. Please try another query."
        raise RuntimeError(msg)
    if not isinstance(tbody, Tag):
        msg = f"Expected results table tag, got {type(tbody).__name__}"
        raise TypeError(msg)

    books = parse_search_page(
        tbody,
        filters,
        provider_id="annas_archive",
        item_selector="tr",
        extract_item=_extract_aa_search_result,
    )

    if path_language_enabled and requested_langs:
        books = [b for b in books if _book_matches_requested_languages(b.language, requested_langs)]

    supported_formats = get_supported_formats()

    books.sort(
        key=lambda x: (
            supported_formats.index(x.format)
            if x.format in supported_formats
            else len(supported_formats)
        )
    )

    return books


def get_book_info(book_id: str, *, fetch_download_count: bool = True) -> BrowseRecord:
    """Get detailed information for a specific book.

    Args:
        book_id: Book identifier (MD5 hash)
        fetch_download_count: Whether to fetch download count from summary API.
            Only needed for display in DetailsModal, not for downloads.

    Returns:
        BrowseRecord: Detailed book information including download URLs

    """
    url = f"{network.get_aa_base_url()}/md5/{book_id}"
    selector = network.AAMirrorSelector()
    # Same challenge as search: the detail page is gated on every mirror, so bypass it.
    html = downloader.html_get_page(url, selector=selector, allow_bypasser_fallback=True)

    if not html:
        detail = getattr(selector, "last_failure", None) or (
            "Network restricted or mirrors are blocked."
        )
        raise SearchUnavailableError(f"Unable to reach download source. {detail}")

    soup = BeautifulSoup(html_response_text(html), "html.parser")

    return _parse_book_info_page(soup, book_id, fetch_download_count=fetch_download_count)


def _extract_aa_search_result(row: Tag) -> ParsedSearchResult | None:
    """Extract Anna's Archive table fields for the shared parser."""
    try:
        if row.text.strip().lower().startswith("your ad here"):
            return None

        cells = row.find_all("td")
        anchors = row.find_all("a", href=True)
        if len(cells) < 11 or not anchors:
            return None

        record_id = (get_attr(anchors[0], "href") or "").split("/")[-1]
        if not record_id:
            return None

        path_language_enabled = _is_language_from_path_enabled()
        distant_path = _extract_distant_path(row, enabled=path_language_enabled)

        preview_img = cells[0].find("img")
        preview = get_attr(preview_img, "src") if isinstance(preview_img, Tag) else None

        title_span = cells[1].find("span")
        if isinstance(title_span, Tag):
            # AA nests related-edition spans inside the main title span — take only direct text.
            direct = " ".join(
                str(c).strip()
                for c in title_span.children
                if isinstance(c, NavigableString) and str(c).strip()
            ).strip()
            title = direct or _first_stripped_text(title_span)
        else:
            title = None
        author = _first_stripped_text(cells[2].find("span"))
        publisher = _first_stripped_text(cells[3].find("span"))
        year = _first_stripped_text(cells[4].find("span"))
        language = _first_stripped_text(cells[7].find("span"))
        content = _first_stripped_text(cells[8].find("span"))
        file_format = _first_stripped_text(cells[9].find("span"))
        size = _first_stripped_text(cells[10].find("span"))

        # Only title and format are truly required — lgli rows often have sparse metadata
        if title is None or file_format is None:
            return None

        # Skip entries where the title is a catalog format descriptor, not a real title
        # e.g. "Book/Online Audio", "Print book" — lgli metadata pollution
        if title and "/" in title and len(title) < 40 and not any(c.isdigit() for c in title):
            return None

        if path_language_enabled and _is_missing_or_placeholder_language(language):
            detected = _detect_language_from_distant_path(distant_path)
            language = detected or "unknown"

        return ParsedSearchResult(
            key=record_id,
            record_id=record_id,
            title=title,
            formats=(file_format.lower(),),
            preview=preview,
            author=author,
            publisher=publisher,
            year=year,
            language=language,
            content=content.lower() if content else None,
            size=size,
            download_path=distant_path,
            source_url=f"{network.get_aa_base_url()}/md5/{record_id}",
        )
    except (AttributeError, IndexError, KeyError, TypeError) as e:
        logger.error_trace(f"Error parsing search result row: {e}")
        return None


def _parse_search_result_row(row: Tag) -> BrowseRecord | None:
    """Compatibility wrapper for parsing one Anna's Archive result row."""
    records = parse_search_items(
        [row],
        None,
        provider_id="annas_archive",
        extract_item=_extract_aa_search_result,
    )
    return records[0] if records else None


def _parse_book_info_page(
    soup: BeautifulSoup,
    book_id: str,
    *,
    fetch_download_count: bool = True,
) -> BrowseRecord:
    """Parse the book info page HTML into a browse record."""
    data = soup.select_one("body > main > div:nth-of-type(1)")

    if not data:
        msg = f"Failed to parse book info for ID: {book_id}"
        raise RuntimeError(msg)

    preview: str = ""

    node = data.select_one("div:nth-of-type(1) > img")
    if isinstance(node, Tag):
        preview = get_attr(node, "src") or ""

    main_inner = next(
        (tag for tag in soup.find_all("div", {"class": "main-inner"}) if isinstance(tag, Tag)),
        None,
    )
    if main_inner is None:
        msg = f"Failed to parse book details for ID: {book_id}"
        raise RuntimeError(msg)

    details_container = main_inner.find_next("div")
    if not isinstance(details_container, Tag):
        msg = f"Expected details container tag for book ID {book_id}, got {type(details_container).__name__}"
        raise TypeError(msg)

    original_nodes = list(details_container.children)
    divs = [node for node in original_nodes if isinstance(node, Tag)]

    slow_urls_no_waitlist: set[str] = set()
    slow_urls_with_waitlist: set[str] = set()

    for anchor in soup.find_all("a"):
        try:
            text = anchor.text.strip().lower()
            href = get_attr(anchor, "href")
            if not href:
                continue

            next_text = ""
            next_elements = anchor.next_elements
            next(next_elements, None)
            second_next = next(next_elements, None)
            if second_next is not None:
                next_text = (
                    second_next.get_text(strip=True).lower()
                    if isinstance(second_next, Tag)
                    else str(second_next).strip().lower()
                )

            if text.startswith("slow partner server") and "waitlist" in next_text:
                if "no waitlist" in next_text:
                    slow_urls_no_waitlist.add(href)
                else:
                    slow_urls_with_waitlist.add(href)
        except AttributeError, TypeError:
            pass

    logger.debug(
        "Source inventory for %s -> aa_no_wait=%d, aa_wait=%d",
        book_id,
        len(slow_urls_no_waitlist),
        len(slow_urls_with_waitlist),
    )

    # Convert to absolute URLs and tag by source type
    base_url = network.get_aa_base_url()
    urls = []

    for rel_url in slow_urls_no_waitlist:
        abs_url = downloader.get_absolute_url(base_url, rel_url)
        if abs_url:
            urls.append(abs_url)
            _url_source_types[abs_url] = "aa-slow-nowait"

    for rel_url in slow_urls_with_waitlist:
        abs_url = downloader.get_absolute_url(base_url, rel_url)
        if abs_url:
            urls.append(abs_url)
            _url_source_types[abs_url] = "aa-slow-wait"

    divs = [div for div in divs if div.get_text(strip=True)]

    all_details = _find_in_divs(divs, " · ")
    file_format = ""
    size = ""
    content = ""
    supported_formats = get_supported_formats()

    for _details in all_details:
        _details = _details.split(" · ")
        for f in _details:
            stripped_lower = f.strip().lower()
            if file_format == "" and stripped_lower in supported_formats:
                file_format = f.strip().lower()
            if size == "" and any(u in f.strip().lower() for u in ("mb", "kb", "gb")):
                size = normalize_size(f)
            if content == "":
                for ct in CONTENT_TYPES:
                    if ct in f.strip().lower():
                        content = ct
                        break
        if file_format == "" or size == "":
            for f in _details:
                stripped = f.strip().lower()
                if file_format == "" and stripped and " " not in stripped:
                    file_format = stripped
                if size == "" and "." in stripped:
                    size = normalize_size(f)

    book_title = (_find_in_divs(divs, "🔍") or [""])[0].strip("🔍").strip()

    # Extract basic information
    description = _extract_book_description(soup)

    book_info = BrowseRecord(
        id=book_id,
        title=book_title,
        source="direct_download",
        preview=preview,
        content=content,
        publisher=(_find_in_divs(divs, "icon-[mdi--company]", is_class=True) or [""])[0],
        author=(_find_in_divs(divs, "icon-[mdi--user-edit]", is_class=True) or [""])[0],
        format=file_format,
        size=size,
        description=description,
        download_urls=urls,
    )

    # Extract additional metadata
    metadata_node = original_nodes[-6]
    if not isinstance(metadata_node, Tag):
        msg = f"Expected metadata container tag for book ID {book_id}, got {type(metadata_node).__name__}"
        raise TypeError(msg)
    info = _extract_book_metadata(metadata_node)

    if fetch_download_count:
        try:
            summary_url = f"{network.get_aa_base_url()}/dyn/md5/summary/{book_id}"
            # Unlike search and the detail page above, this one stays off the bypasser: a
            # download count is decoration on the details modal, not worth holding the
            # modal open for a browser solve. If it is gated, drop it and move on.
            summary_response = downloader.html_get_page(
                summary_url, selector=network.AAMirrorSelector(), allow_bypasser_fallback=False
            )
            if summary_response:
                summary_data = json.loads(html_response_text(summary_response))
                if "downloads_total" in summary_data:
                    info["Downloads"] = [str(summary_data["downloads_total"])]
        except (
            SearchUnavailableError,
            RuntimeError,
            json.JSONDecodeError,
            TypeError,
            KeyError,
            AttributeError,
        ) as e:
            logger.debug("Failed to fetch download count for %s: %s", book_id, e)

    book_info.info = info

    # Set language and year from metadata if available
    if info.get("Language"):
        book_info.language = info["Language"][0]
    if info.get("Year"):
        book_info.year = info["Year"][0]

    # Set source URL for linking back to Anna's Archive
    book_info.source_url = f"{network.get_aa_base_url()}/md5/{book_id}"

    return book_info


def _find_in_divs(divs: list[Tag], text: str, *, is_class: bool = False) -> list[str]:
    """Find divs containing text or having a specific class."""
    results: list[str] = []
    for div in divs:
        if is_class:
            if div.find(class_=text):
                results.append(div.text.strip())
        elif text in div.text.strip():
            results.append(div.text.strip())
    return results


def _get_next_value_div(label_div: Tag) -> Tag | None:
    """Find the next sibling div that holds the value for a metadata label."""
    sibling = label_div.next_sibling
    while sibling:
        if isinstance(sibling, Tag) and sibling.name == "div":
            return sibling
        sibling = sibling.next_sibling
    return None


def _extract_book_description(soup: BeautifulSoup) -> str | None:
    """Extract the primary or alternative description from the book page."""
    container = soup.select_one(".js-md5-top-box-description")
    if not container:
        return None

    alternative: str | None = None

    label_divs = container.select("div.text-xs.text-gray-500.uppercase")
    for label_div in label_divs:
        label_text = label_div.get_text(strip=True).lower()
        value_div = _get_next_value_div(label_div)
        if not value_div:
            continue

        value_text = value_div.get_text(separator=" ", strip=True)
        if not value_text:
            continue

        if label_text == "description":
            return value_text
        if label_text == "alternative description" and not alternative:
            alternative = value_text

    if alternative:
        return alternative

    # Fallback to the first text block inside the description container
    fallback_div = container.find("div", class_="mb-1")
    if fallback_div:
        fallback_text = fallback_div.get_text(separator=" ", strip=True)
        if fallback_text:
            return fallback_text

    return None


def _extract_book_metadata(metadata_divs: Tag) -> dict[str, list[str]]:
    """Extract metadata from book info divs."""
    info: dict[str, set[str]] = {}

    sub_datas = metadata_divs.find_all("div")[0]
    for sub_data in _iter_child_tags(sub_datas):
        if sub_data.get_text(strip=True) == "":
            continue
        children = list(_iter_child_tags(sub_data))
        key = children[0].get_text(strip=True)
        value = children[1].get_text(strip=True)
        if key not in info:
            info[key] = set()
        info[key].add(value)

    relevant_prefixes = ("isbn-", "alternative", "asin", "goodreads", "language", "year")
    return {
        k.strip(): list(v)
        for k, v in info.items()
        if k.lower().startswith(relevant_prefixes) and "filename" not in k.lower()
    }


def _get_source_info(link: str) -> tuple[str, str]:
    """Get source label and friendly name for a download link.

    Args:
        link: Download URL

    Returns:
        Tuple of (log_label, friendly_name)

    """
    # Check detailed source type mapping first (for AA slow distinction)
    if link in _url_source_types:
        detailed_label = _url_source_types[link]
        for log_label, friendly_name, _ in _DOWNLOAD_SOURCES:
            if log_label == detailed_label:
                return log_label, friendly_name

    for log_label, friendly_name, patterns in _DOWNLOAD_SOURCES:
        if patterns and any(pattern in link for pattern in patterns):
            return log_label, friendly_name
    return "unknown", "Mirror"


def _friendly_source_name(link: str) -> str:
    """Get user-friendly name for a download source."""
    return _get_source_info(link)[1]


def _group_urls_by_source(urls: list[str], urls_by_source: dict[str, list[str]]) -> None:
    """Group URLs into urls_by_source dict by their source type."""
    for url in urls:
        source_type = _url_source_types.get(url)
        if source_type:
            urls_by_source.setdefault(source_type, []).append(url)


def _fetch_aa_page_urls(book_info: BrowseRecord, urls_by_source: dict[str, list[str]]) -> None:
    """Fetch and parse AA page, populating urls_by_source dict.

    Groups existing book_info.download_urls by source type. If book_info
    has no URLs, fetches the AA page fresh.
    """
    if book_info.download_urls:
        _group_urls_by_source(book_info.download_urls, urls_by_source)
        return

    try:
        fresh_book_info = get_book_info(book_info.id, fetch_download_count=False)
        _group_urls_by_source(fresh_book_info.download_urls, urls_by_source)
    except (SearchUnavailableError, RuntimeError, TypeError, AttributeError) as e:
        logger.warning("Failed to fetch AA page: %s", e)


def _get_urls_for_source(
    source_id: str,
    book_info: BrowseRecord,
    selector: network.AAMirrorSelector,
    cancel_flag: Event | None,
    status_callback: Callable[[str, str | None], None] | None,
    urls_by_source: dict[str, list[str]],
) -> list[str]:
    """Get URLs for a specific source, fetching lazily if needed."""
    # AA Fast - generate URL dynamically
    if source_id == "aa-fast":
        if not config.AA_DONATOR_KEY:
            return []
        url = f"{network.get_aa_base_url()}/dyn/api/fast_download.json?md5={book_info.id}&key={config.AA_DONATOR_KEY}"
        _url_source_types[url] = "aa-fast"
        return [url]

    # MD5-based sources - generate URL from template
    template = _get_md5_url_template(source_id)
    if template:
        url = template.format(md5=book_info.id)
        _url_source_types[url] = source_id
        return [url]

    if source_id == "libgen":
        urls = []
        for base_url in _get_libgen_domains():
            url = f"{base_url}/ads.php?md5={book_info.id}"
            _url_source_types[url] = "libgen"
            urls.append(url)
        return urls

    # Welib - fetch page and parse for slow_download links
    if source_id == "welib":
        if status_callback:
            status_callback("resolving", "Fetching welib sources")
        return _get_download_urls_from_welib(
            book_info.id,
            selector=selector,
            cancel_flag=cancel_flag,
            status_callback=status_callback,
        )

    # AA page sources - fetch AA page if not already done
    if source_id in _AA_PAGE_SOURCES:
        if not urls_by_source:
            if status_callback:
                status_callback("resolving", "Fetching download sources")
            _fetch_aa_page_urls(book_info, urls_by_source)

        return urls_by_source.get(source_id, [])

    return []


def _try_download_url(
    url: str,
    source_id: str,
    book_info: BrowseRecord,
    book_path: Path,
    progress_callback: Callable[[float], None] | None,
    cancel_flag: Event | None,
    status_callback: Callable[[str, str | None], None] | None,
    selector: network.AAMirrorSelector,
    source_context: str,
) -> str | None:
    """Attempt to download from a single URL.

    Returns: download URL on success, None on failure.
    """
    try:
        logger.info("Trying download source [%s]: %s", source_id, url)

        if status_callback:
            status_callback("resolving", f"Trying {source_context}")

        download_url = _get_download_url(
            url, book_info.title, cancel_flag, status_callback, selector, source_context
        )
        if not download_url:
            _raise_runtime_error("No download URL resolved")

        logger.info("Resolved download URL [%s]: %s", source_id, download_url)

        data = downloader.download_url(
            download_url,
            book_info.size or "",
            progress_callback,
            cancel_flag,
            selector,
            status_callback,
            referer=url,
        )

        if not data:
            _raise_runtime_error("No data received from download")

        file_size = data.tell()
        if file_size < _MIN_VALID_FILE_SIZE:
            logger.warning("Downloaded file too small (%s bytes), likely an error page", file_size)
            _raise_runtime_error(f"File too small ({file_size} bytes)")

        logger.debug("Download finished (%s bytes). Writing to %s", file_size, book_path)
        data.seek(0)
        with book_path.open("wb") as f:
            f.write(data.getbuffer())

    except (
        RuntimeError,
        requests.exceptions.RequestException,
        OSError,
        KeyError,
        ValueError,
        TypeError,
        AttributeError,
    ) as e:
        logger.warning("Failed to download from %s (source=%s): %s", url, source_id, e)
        return None
    else:
        return download_url


def _get_download_urls_from_welib(
    book_id: str,
    selector: network.AAMirrorSelector | None = None,
    cancel_flag: Event | None = None,
    status_callback: Callable[[str, str | None], None] | None = None,
) -> list[str]:
    """Get download URLs from welib.org (bypasser required)."""
    from shelfmark.core import mirrors

    if not _is_source_enabled("welib"):
        return []
    template = mirrors.get_welib_url_template()
    if not template:
        return []
    url = template.format(md5=book_id)
    logger.info("Fetching welib download URLs for %s", book_id)
    try:
        html = downloader.html_get_page(
            url,
            use_bypasser=True,
            selector=selector or network.AAMirrorSelector(),
            cancel_flag=cancel_flag,
            status_callback=status_callback,
        )
    except (
        SearchUnavailableError,
        requests.exceptions.RequestException,
        RuntimeError,
        ValueError,
        TypeError,
        AttributeError,
    ) as exc:
        logger.error_trace(f"Welib fetch failed for {book_id}: {exc}")
        return []
    if not html:
        logger.warning("Welib page empty for %s", book_id)
        return []

    soup = BeautifulSoup(html_response_text(html), "html.parser")
    links = [
        downloader.get_absolute_url(url, href)
        for a in soup.find_all("a", href=True)
        if (href := get_attr(a, "href")) and "/slow_download/" in href
    ]
    return list(dict.fromkeys(links))  # Dedupe while preserving order


def _extract_libgen_download_url(link: str, cancel_flag: Event | None = None) -> str:
    """Extract download URL from Libgen ads.php page using direct HTTP."""
    if cancel_flag and cancel_flag.is_set():
        return ""

    base_url = "/".join(link.split("/")[:3])
    logger.debug("Libgen fast: trying %s", link)

    try:
        response = requests.get(
            link,
            headers=downloader.DOWNLOAD_HEADERS,
            timeout=(5, 10),
            allow_redirects=True,
            proxies=network.get_proxies(link),
            verify=network.get_ssl_verify(link),
        )

        if response.status_code != HTTPStatus.OK:
            logger.debug("Libgen fast: %s returned %s", link, response.status_code)
            return ""

        html = response.text
        final_url = response.url

        if "libgen" not in final_url.lower() and "ads.php" not in final_url.lower():
            logger.debug("Libgen fast: redirected away to %s", final_url)
            return ""

        if "get.php" not in html:
            logger.debug("Libgen fast: page doesn't contain get.php")
            return ""

        download_url = None
        for pattern in _LIBGEN_GET_PATTERNS:
            match = pattern.search(html)
            if match:
                download_url = (
                    match.group(1).replace("&amp;", "&").replace("&gt;", ">").replace("&lt;", "<")
                )
                break

        if not download_url:
            logger.debug("Libgen fast: couldn't extract GET link")
            return ""
        if not download_url.startswith("http"):
            download_url = f"{base_url}/{download_url.lstrip('/')}"

        logger.debug("Libgen fast: extracted %s", download_url)
    except requests.exceptions.RequestException as e:
        logger.debug("Libgen fast: request failed: %s", e)
        return ""
    except (AttributeError, TypeError, ValueError) as e:
        logger.warning("Libgen fast: unexpected error: %s", e)
        return ""
    else:
        return download_url


def download_book(
    book_info: BrowseRecord,
    book_path: Path,
    progress_callback: Callable[[float], None] | None = None,
    cancel_flag: Event | None = None,
    status_callback: Callable[[str, str | None], None] | None = None,
) -> str | None:
    """Download a book using sources in configured priority order.

    Returns: Download URL if successful, None otherwise.
    """
    selector = network.AAMirrorSelector()
    source_failures: dict[str, int] = {}
    urls_by_source: dict[str, list[str]] = {}
    url_attempt_counter = 0

    # Get enabled sources in priority order
    priority = [s for s in _get_source_priority() if s.get("enabled", True)]

    for source_config in priority:
        source_id = source_config["id"]

        if cancel_flag and cancel_flag.is_set():
            return None

        # Debug: skip sources for testing fallback chains
        if source_id in DEBUG_SKIP_SOURCES:
            logger.info("DEBUG_SKIP_SOURCES: skipping %s", source_id)
            continue

        # Skip if source requires CF bypass and it's not enabled
        if source_id in _CF_BYPASS_REQUIRED and not config.USE_CF_BYPASS:
            logger.debug("Skipping %s - requires CF bypass", source_id)
            continue

        # Skip if source has failed too many times
        if source_failures.get(source_id, 0) >= _SOURCE_FAILURE_THRESHOLD:
            logger.debug("Skipping %s - too many failures", source_id)
            continue

        # Get URLs for this source (lazy-loads as needed)
        urls_to_try = _get_urls_for_source(
            source_id,
            book_info,
            selector,
            cancel_flag,
            status_callback,
            urls_by_source,
        )

        if not urls_to_try:
            continue

        # Apply round-robin rotation if multiple URLs
        if len(urls_to_try) > 1:
            rotation_value = next(_aa_slow_rotation)
            rotation = rotation_value % len(urls_to_try)
            urls_to_try = urls_to_try[rotation:] + urls_to_try[:rotation]
            if rotation:
                logger.debug("Rotated %s URLs by %s", source_id, rotation)

        # Try each URL for this source
        for url in urls_to_try:
            if cancel_flag and cancel_flag.is_set():
                return None

            if source_id == "libgen":
                source_context = "Libgen (Fast)"
            else:
                url_attempt_counter += 1
                friendly_name = _friendly_source_name(url)
                source_context = f"{friendly_name} (Server #{url_attempt_counter})"

            result = _try_download_url(
                url,
                source_id,
                book_info,
                book_path,
                progress_callback,
                cancel_flag,
                status_callback,
                selector,
                source_context,
            )

            if result:
                return result

            source_failures[source_id] = source_failures.get(source_id, 0) + 1

            # Check if we've hit the failure threshold
            if source_failures[source_id] >= _SOURCE_FAILURE_THRESHOLD:
                logger.info("Source %s hit failure threshold, moving to next source", source_id)
                break

    if status_callback:
        status_callback("error", "All sources failed")
    return None


def _get_download_url(
    link: str,
    title: str,
    cancel_flag: Event | None = None,
    status_callback: Callable[[str, str | None], None] | None = None,
    selector: network.AAMirrorSelector | None = None,
    source_context: str | None = None,
) -> str:
    """Extract actual download URL from various source pages.

    Args:
        link: URL to extract download link from
        title: Book title for logging
        cancel_flag: Optional cancellation flag
        status_callback: Optional callback for status updates
        selector: Optional AA mirror selector
        source_context: Optional context string like "Welib (1/12)" for status messages

    """
    sel = selector or network.AAMirrorSelector()

    # AA fast download API (JSON response)
    if link.startswith(f"{network.get_aa_base_url()}/dyn/api/fast_download.json"):
        page = downloader.html_get_page(
            link, selector=sel, cancel_flag=cancel_flag, status_callback=status_callback
        )
        page_data = json.loads(html_response_text(page))
        download_url = page_data.get("download_url", "")
        return (
            downloader.get_absolute_url(link, download_url) if isinstance(download_url, str) else ""
        )

    if "/ads.php?md5=" in link and any(domain in link for domain in _get_libgen_domains()):
        return _extract_libgen_download_url(link, cancel_flag)

    html = downloader.html_get_page(
        link, selector=sel, cancel_flag=cancel_flag, status_callback=status_callback
    )
    if not html:
        return ""

    soup = BeautifulSoup(html_response_text(html), "html.parser")
    url = ""

    # Z-Library
    if _is_configured_zlib_link(link):
        dl = soup.find("a", href=True, class_="addDownloadedBook")
        if not dl:
            # Retry after delay if page not fully loaded
            time.sleep(2)
            html = downloader.html_get_page(
                link, selector=sel, cancel_flag=cancel_flag, status_callback=status_callback
            )
            if html:
                soup = BeautifulSoup(html_response_text(html), "html.parser")
                dl = soup.find("a", href=True, class_="addDownloadedBook")
        url = (get_attr(dl, "href") or "") if isinstance(dl, Tag) else ""

    # AA slow download / partner servers
    elif "/slow_download/" in link:
        url = _extract_slow_download_url(
            soup, link, title, cancel_flag, status_callback, sel, source_context
        )

    else:
        get_btn = _find_first_anchor_with_text(soup, "GET") or _find_first_anchor_with_text(
            soup, "Download"
        )
        if get_btn:
            url = get_attr(get_btn, "href") or ""
        else:
            logger.warning("Unknown source type, couldn't find download link: %s", link)
            url = ""

    return downloader.get_absolute_url(link, url)


_AA_COUNTDOWN_MAX_RETRIES = 3


def _extract_slow_download_url(
    soup: BeautifulSoup,
    link: str,
    title: str,
    cancel_flag: Event | None,
    status_callback: Callable[[str, str | None], None] | None,
    selector: network.AAMirrorSelector,
    source_context: str | None = None,
    _countdown_attempts: int = 0,
) -> str:
    """Extract download URL from AA slow download pages."""
    html_str = str(soup)

    clipboard_match = re.search(r"navigator\.clipboard\.writeText\(['\"]([^'\"]+)['\"]\)", html_str)
    if clipboard_match:
        url = clipboard_match.group(1)
        if url.startswith("http") and "/slow_download/" not in url:
            return url

    dl_link = _find_first_anchor_with_text(soup, "📚 Download now") or _find_first_anchor_with_text(
        soup, "Download now", contains=True
    )
    if dl_link:
        return get_attr(dl_link, "href") or ""

    for a_tag in soup.find_all("a", href=True):
        if a_tag.has_attr("download"):
            href = get_attr(a_tag, "href")
            if not href:
                continue
            if href.startswith("http") and "/slow_download/" not in href:
                return href

    for span in soup.find_all("span"):
        if not _tag_has_class_containing(span, "whitespace-normal"):
            continue
        text = span.get_text(strip=True)
        if text.startswith(("http://", "https://")) and "/slow_download/" not in text:
            return text

    for span in soup.find_all("span"):
        if not _tag_has_class_containing(span, "bg-gray-200"):
            continue
        text = span.get_text(strip=True)
        if text.startswith(("http://", "https://")):
            return text

    location_match = re.search(r"window\.location\.href\s*=\s*['\"]([^'\"]+)['\"]", html_str)
    if location_match:
        url = location_match.group(1)
        if url.startswith("http") and "/slow_download/" not in url:
            return url

    copy_text = _find_text_node(soup, "copy this url")
    if copy_text and copy_text.parent:
        parent = copy_text.parent
        next_link = parent.find_next("a", href=True)
        if isinstance(next_link, Tag):
            next_href = get_attr(next_link, "href")
            if next_href:
                return next_href
        code_elem = parent.find_next("code")
        if isinstance(code_elem, Tag):
            return code_elem.get_text(strip=True)
        for sibling in parent.find_next_siblings():
            text = (
                sibling.get_text(strip=True) if isinstance(sibling, Tag) else str(sibling).strip()
            )
            if text.startswith("http"):
                return text

    countdown_seconds = _extract_countdown_seconds(soup, html_str)
    if countdown_seconds > 0:
        if _countdown_attempts >= _AA_COUNTDOWN_MAX_RETRIES:
            logger.warning(
                "Countdown retry limit (%s) reached for %s, giving up",
                _AA_COUNTDOWN_MAX_RETRIES,
                title,
            )
            return ""

        max_countdown_seconds = 600
        sleep_time = min(countdown_seconds, max_countdown_seconds)
        if countdown_seconds > max_countdown_seconds:
            logger.warning(
                "Countdown %ss exceeds max, capping at %ss",
                countdown_seconds,
                max_countdown_seconds,
            )
        logger.info(
            "AA waitlist: %ss for %s (attempt %s/%s)",
            sleep_time,
            title,
            _countdown_attempts + 1,
            _AA_COUNTDOWN_MAX_RETRIES,
        )

        # Live countdown with status updates
        for remaining in range(sleep_time, 0, -1):
            wait_msg = (
                f"{source_context} - Waiting {remaining}s"
                if source_context
                else f"Waiting {remaining}s"
            )
            if status_callback:
                status_callback("resolving", wait_msg)

            # Wait 1 second (or until cancelled)
            if cancel_flag and cancel_flag.wait(timeout=1):
                logger.info("Cancelled wait for %s", title)
                return ""

        # After countdown, update status and re-fetch
        if status_callback and source_context:
            status_callback("resolving", f"{source_context} - Fetching")

        html = downloader.html_get_page(
            link, selector=selector, cancel_flag=cancel_flag, status_callback=status_callback
        )
        if not html:
            return ""
        new_soup = BeautifulSoup(html_response_text(html), "html.parser")
        return _extract_slow_download_url(
            new_soup,
            link,
            title,
            cancel_flag,
            status_callback,
            selector,
            source_context,
            _countdown_attempts + 1,
        )

    link_texts = [a.get_text(strip=True)[:50] for a in soup.find_all("a", href=True)[:10]]
    logger.warning("No download URL found. First 10 links: %s", link_texts)
    # A bypassed page with no AA download links often means the network served a wrong
    # page (e.g. an ISP block page) instead of Anna's Archive. Probe for DNS interference
    # so we can give the user an actionable hint instead of a generic failure.
    host = urlparse(link).hostname or ""
    if host:
        network.note_possible_dns_interference(host)
    return ""


def _extract_countdown_seconds(soup: BeautifulSoup, html_str: str) -> int:
    """Extract countdown timer seconds from AA slow download page."""
    countdown_elem = soup.find("span", class_="js-partner-countdown")
    if isinstance(countdown_elem, Tag):
        seconds = _parse_countdown_seconds_from_element(countdown_elem)
        if seconds is not None:
            return seconds

    for elem in soup.find_all(["span", "div"]):
        if not (
            _tag_has_class_containing(elem, "timer") or _tag_has_class_containing(elem, "countdown")
        ):
            continue
        seconds = _parse_countdown_seconds_from_element(elem)
        if seconds is not None:
            return seconds

    countdown_attr = re.search(r'data-countdown=["\'](\d+)["\']', html_str)
    if countdown_attr:
        seconds = int(countdown_attr.group(1))
        if 0 < seconds < _AA_COUNTDOWN_MAX_SECONDS:
            return seconds

    js_countdown = re.search(r"countdown:\s*(\d+)", html_str)
    if js_countdown:
        seconds = int(js_countdown.group(1))
        if 0 < seconds < _AA_COUNTDOWN_MAX_SECONDS:
            return seconds
    js_var = re.search(r"(?:var|let|const)\s+countdown\s*=\s*(\d+)", html_str)
    if js_var:
        seconds = int(js_var.group(1))
        if 0 < seconds < _AA_COUNTDOWN_MAX_SECONDS:
            return seconds

    countdown_secs = re.search(r"countdownSeconds\s*=\s*(\d+)", html_str)
    if countdown_secs:
        seconds = int(countdown_secs.group(1))
        if 0 < seconds < _AA_COUNTDOWN_MAX_SECONDS:
            return seconds

    json_countdown = re.search(r'["\']countdown[_-]?seconds["\']\s*:\s*(\d+)', html_str)
    if json_countdown:
        seconds = int(json_countdown.group(1))
        if 0 < seconds < _AA_COUNTDOWN_MAX_SECONDS:
            return seconds

    wait_text = re.search(r"wait\s+(\d+)\s+seconds", html_str, re.IGNORECASE)
    if wait_text:
        seconds = int(wait_text.group(1))
        if 0 < seconds < _AA_COUNTDOWN_MAX_SECONDS:
            return seconds

    return 0


def _parse_countdown_seconds_from_element(element: Tag) -> int | None:
    """Parse an integer countdown from a tag, returning None when invalid."""
    try:
        seconds = int(element.get_text(strip=True))
    except ValueError, TypeError:
        return None

    if 0 < seconds < _AA_COUNTDOWN_MAX_SECONDS:
        return seconds
    return None


class AnnasArchiveProvider:
    """Anna's Archive provider, including its specialized MD5 mirror cascade."""

    id = "annas_archive"
    display_name = "Anna's Archive"

    def __init__(self) -> None:
        self._last_search_type = "title_author"

    @property
    def last_search_type(self) -> str:
        return self._last_search_type

    def is_enabled(self) -> bool:
        from shelfmark.core import mirrors

        return mirrors.has_aa_mirror_configuration()

    def handles(self, url: str) -> bool:
        from shelfmark.core import mirrors

        hostname = (urlparse(url).hostname or "").lower().rstrip(".")
        if not hostname:
            return False
        return any(
            hostname == (urlparse(base_url).hostname or "").lower().rstrip(".")
            for base_url in mirrors.get_aa_mirrors()
        )

    def get_record(self, record_id: str, *, fetch_download_count: bool = True) -> BrowseRecord:
        ensure_available()
        return get_book_info(record_id, fetch_download_count=fetch_download_count)

    def download(
        self,
        book_info: BrowseRecord,
        book_path: Path,
        progress_callback: Callable[[float], None] | None,
        cancel_flag: Event | None,
        status_callback: Callable[[str, str | None], None] | None,
    ) -> str | None:
        return download_book(book_info, book_path, progress_callback, cancel_flag, status_callback)

    def _search_books_with_language_fallback(
        self,
        query: str,
        filters: SearchFilters,
        *,
        search_label: str,
    ) -> list[BrowseRecord]:
        """Retry AA queries without a language filter when filtered search returns nothing."""
        results = search_books(query, filters)
        if results or not filters.lang:
            return results

        logger.debug(
            "No %s results with langs=%s, retrying without language filter",
            search_label,
            filters.lang,
        )
        return search_books(query, replace(filters, lang=None))

    def search(
        self,
        book: BookMetadata,
        plan: ReleaseSearchPlan,
        *,
        expand_search: bool = False,
        content_type: str = "ebook",
    ) -> list[BrowseRecord]:
        """Search for releases using the book's metadata with request-local page reuse."""
        with search_page_reuse():
            return self._search(
                book,
                plan,
                expand_search=expand_search,
                content_type=content_type,
            )

    def _search(
        self,
        book: BookMetadata,
        plan: ReleaseSearchPlan,
        *,
        expand_search: bool = False,
        content_type: str = "ebook",
    ) -> list[BrowseRecord]:
        """Run Anna's Archive's ISBN-first and localized-title search strategy.

        Priority: ISBN search first (most precise), then title+author fallback.
        For non-English languages, uses localized titles from book.titles_by_language.

        Args:
            book: Book metadata from provider
            plan: Precomputed search plan with normalized queries and filters.
            expand_search: If True, skip ISBN and use title+author directly
            languages: Language codes to filter by (overrides book.language/config)
            content_type: Ignored - Direct download uses format filtering instead

        """
        ensure_available()
        lang_filter = plan.languages

        # Reset search type tracking
        self._last_search_type = "title_author"

        if plan.source_filters is not None:
            query = plan.manual_query or ""
            logger.debug(
                "Searching direct_download: source_query='%s', langs=%s", query, lang_filter
            )
            filters = plan.source_filters or SearchFilters()
            filters.lang = lang_filter if lang_filter is not None else (filters.lang or [])
            results = self._search_books_with_language_fallback(
                query, filters, search_label="manual"
            )
            self._last_search_type = "manual" if query else "title_author"
            return results

        # ISBN search first (unless expand_search requested)
        if plan.manual_query:
            expand_search = True

        if not expand_search:
            isbn = plan.isbn_candidates[0] if plan.isbn_candidates else None
            if isbn:
                logger.debug("Searching direct_download: isbn='%s', langs=%s", isbn, lang_filter)
                filters = SearchFilters(isbn=[isbn])
                filters.lang = lang_filter if lang_filter is not None else []
                try:
                    results = search_books(isbn, filters)
                    if results:
                        logger.info("Found %s releases via ISBN", len(results))
                        self._last_search_type = "isbn"
                        return results
                    logger.debug("No ISBN results, falling back to title+author")
                except SearchUnavailableError:
                    raise
                except (ValueError, TypeError, AttributeError, RuntimeError) as e:
                    logger.warning("ISBN search failed: %s", e)

        # Title + author fallback
        author = plan.author
        searches = [(v.title, v.languages) for v in plan.grouped_title_variants]

        # Execute searches with deduplication
        seen_ids: set = set()
        all_results: list[BrowseRecord] = []

        for title, langs in searches:
            query = f"{title} {author}".strip()
            if not query:
                continue
            # `except Exception` below keeps this loop going past a failed variant, which
            # is right for a parse error and wrong for a spent budget: without this the
            # variants queue up behind each other and the request outlives the caller.
            if search_deadline.expired():
                logger.info("Release search budget spent; skipping remaining title variants")
                break

            logger.debug("Searching direct_download: title_author='%s', langs=%s", query, langs)
            filters = SearchFilters(lang=langs if langs is not None else [])
            try:
                for bi in search_books(query, filters):
                    if bi.id not in seen_ids:
                        seen_ids.add(bi.id)
                        all_results.append(bi)
            except SearchUnavailableError:
                raise
            except Exception:
                logger.exception("Search error")

        if (
            not all_results
            and any(langs for _, langs in searches)
            and not search_deadline.expired()
        ):
            logger.debug(
                "No title+author results with language filter, retrying without language filter"
            )
            for title, _langs in searches:
                query = f"{title} {author}".strip()
                if not query:
                    continue
                if search_deadline.expired():
                    logger.info("Release search budget spent; skipping remaining retries")
                    break

                logger.debug("Searching direct_download: title_author='%s', langs=[]", query)
                try:
                    for bi in search_books(query, SearchFilters()):
                        if bi.id not in seen_ids:
                            seen_ids.add(bi.id)
                            all_results.append(bi)
                except SearchUnavailableError:
                    raise
                except Exception:
                    logger.exception("Search error")

        return all_results
