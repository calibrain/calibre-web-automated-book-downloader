"""Web scraping functions for AudiobookBay."""

import re
import time
from threading import Lock
from urllib.parse import quote, quote_plus

import requests
from bs4 import BeautifulSoup

from shelfmark.core.config import config
from shelfmark.core.logger import setup_logger
from shelfmark.download import http as downloader
from shelfmark.download.postprocess.packs import PackFile
from shelfmark.release_sources.audiobookbay.utils import normalize_search_punctuation

logger = setup_logger(__name__)

# Default trackers if none found on page
DEFAULT_TRACKERS = [
    "udp://tracker.openbittorrent.com:80",
    "udp://opentor.org:2710",
    "udp://tracker.ccc.de:80",
    "udp://tracker.blackunicorn.xyz:6969",
    "udp://tracker.coppersurfer.tk:6969",
    "udp://tracker.leechers-paradise.org:6969",
]

# ABB request behavior tuning
SEARCH_PAGE_RETRY_ATTEMPTS = 2
DETAIL_PAGE_RETRY_ATTEMPTS = 2
FIRST_PAGE_SESSION_REFRESH_ATTEMPTS = 2

# Legacy search parameter used by older ABB flows
LEGACY_CATEGORY_QUERY = "undefined%2Cundefined"

# Detail pages are fetched once and shared by inspection (file list) and download
# (magnet link) so a "review then download" round trip costs ABB a single request.
DETAIL_PAGE_CACHE_TTL_SECONDS = 120.0
DETAIL_PAGE_CACHE_MAX_ENTRIES = 8
_detail_page_cache: dict[str, tuple[float, str]] = {}
_detail_page_cache_lock = Lock()

# Precompiled patterns used while parsing result cards
LANGUAGE_PATTERN = re.compile(r"Language:\s*([A-Za-z]+)")
POSTED_PATTERN = re.compile(r"Posted:\s*(\d+\s+[A-Za-z]+\s+\d{4})")
FORMAT_PATTERN = re.compile(r"Format:\s*([A-Za-z0-9]+)")
BITRATE_PATTERN = re.compile(r"Bitrate:\s*([\d]+\s*[A-Za-z/]+)")
SIZE_PATTERN = re.compile(r"File Size:\s*([\d.]+)\s*([A-Za-z]+)")
INFO_HASH_LABEL_PATTERN = re.compile(r"Info Hash", re.IGNORECASE)
FILE_ROW_SIZE_PATTERN = re.compile(
    r"^(?P<name>.+?)\s+(?P<size>\d+(?:\.\d+)?)\s*(?P<unit>Bytes?|KBs?|MBs?|GBs?|TBs?)$",
    re.IGNORECASE,
)
_FILE_SIZE_MULTIPLIERS = {"b": 1, "k": 1024, "m": 1024**2, "g": 1024**3, "t": 1024**4}


def _coerce_non_negative_float(value: object, default: float) -> float:
    """Return a non-negative float config value or the provided default."""
    if isinstance(value, bool):
        return default
    if isinstance(value, int | float) and value >= 0:
        return float(value)
    return default


def _coerce_markup_to_html(value: str | tuple[str, str]) -> str:
    """Normalize downloader output to the HTML markup string."""
    if isinstance(value, str):
        return value
    html, _response_url = value
    return html


def _coerce_attribute_to_str(value: object) -> str:
    """Return a plain string HTML attribute value, or an empty string."""
    if isinstance(value, str):
        return value
    return ""


def _build_search_url(
    hostname: str,
    page: int,
    query_encoded: str,
    *,
    include_legacy_category: bool = False,
) -> str:
    """Build an ABB search URL, optionally including legacy category params."""
    # Page 1 uses ABB's root search endpoint; pagination continues via /page/{n}/.
    if page <= 1:
        url = f"https://{hostname}/?s={query_encoded}"
    else:
        url = f"https://{hostname}/page/{page}/?s={query_encoded}"
    if include_legacy_category:
        return f"{url}&cat={LEGACY_CATEGORY_QUERY}"
    return url


def _is_homepage_redirect(final_url: str, hostname: str) -> bool:
    """Detect whether ABB redirected a search request to its homepage."""
    normalized_final = (final_url or "").rstrip("/")
    normalized_home = f"https://{hostname}".rstrip("/")
    return normalized_final in {normalized_home, f"{normalized_home}/"}


def _encode_search_query(query: str, *, exact_phrase: bool) -> str:
    """Encode search query using ABB's space-plus style and optional exact phrase wrapping."""
    search_query = query.strip()
    if (
        exact_phrase
        and search_query
        and not (search_query.startswith('"') and search_query.endswith('"'))
    ):
        search_query = f'"{search_query}"'
    # Keep ABB's space-as-'+' style, but percent-encode everything else: a bare
    # '&' would otherwise start a new query parameter, '%' would open an invalid
    # escape, and a literal '+' would arrive as a space.
    return quote_plus(search_query)


def _normalize_result_url(url: str, hostname: str) -> str:
    """Normalize ABB result URLs to absolute HTTPS URLs."""
    normalized_url = (url or "").strip()
    if not normalized_url:
        return ""
    if normalized_url.startswith(("http://", "https://")):
        return normalized_url
    if normalized_url.startswith("//"):
        return f"https:{normalized_url}"
    if normalized_url.startswith("/"):
        return f"https://{hostname}{normalized_url}"
    return f"https://{hostname}/{normalized_url.lstrip('/')}"


def _bootstrap_abb_session(
    hostname: str,
    session: requests.Session,
    retry_attempts: int,
) -> None:
    """Warm up ABB session cookies (best effort)."""
    downloader.html_get_page(
        f"https://{hostname}/",
        retry=retry_attempts,
        use_bypasser=False,
        allow_bypasser_fallback=False,
        include_response_url=True,
        success_delay=0,
        session=session,
    )


def search_audiobookbay(
    query: str,
    max_pages: int = 1,
    hostname: str = "audiobookbay.lu",
    *,
    exact_phrase: bool = False,
) -> list[dict[str, str]]:
    """Search AudiobookBay for audiobooks matching the query.

    Args:
        query: Search query string
        max_pages: Maximum number of pages to fetch
        hostname: AudiobookBay hostname (e.g., "audiobookbay.lu")
        exact_phrase: Wrap query in quotes for exact phrase matching

    Returns:
        List of dicts with keys: title, link, cover, language, format, bitrate, size, posted_date

    """
    results = []
    # ABB matches the stored, untexturized title, so a curly apostrophe reaching
    # the search returns nothing at all rather than merely ranking worse.
    query = normalize_search_punctuation(query)
    rate_limit_delay = _coerce_non_negative_float(config.get("ABB_RATE_LIMIT_DELAY", 1.0), 1.0)
    session = requests.Session()

    # Bootstrap ABB session cookie (PHPSESSID). ABB increasingly serves reliable
    # search/detail pages only after session initialization, similar to browsers.
    _bootstrap_abb_session(hostname, session, SEARCH_PAGE_RETRY_ATTEMPTS)

    # Iterate through pages
    for page in range(1, max_pages + 1):
        # Construct URL - use + for spaces (matching audiobookbay-automated implementation)
        # This avoids aggressive encoding that PHP-based sites may reject.
        query_encoded = _encode_search_query(query, exact_phrase=exact_phrase)
        # ABB search expects the legacy category query parameter.
        primary_url = _build_search_url(
            hostname,
            page,
            query_encoded,
            include_legacy_category=True,
        )

        try:
            # Reuse shared HTTP fetch logic (without bypasser)
            page_html, final_url = downloader.html_get_page(
                primary_url,
                retry=SEARCH_PAGE_RETRY_ATTEMPTS,
                use_bypasser=False,
                allow_bypasser_fallback=False,
                include_response_url=True,
                success_delay=0,
                session=session,
            )

            was_home_redirect = _is_homepage_redirect(final_url, hostname)

            # ABB can intermittently fail even with a valid URL.
            # If page 1 fails, refresh the session and retry the exact same URL.
            if page == 1 and (not page_html or was_home_redirect):
                for refresh_attempt in range(1, FIRST_PAGE_SESSION_REFRESH_ATTEMPTS + 1):
                    session = requests.Session()
                    _bootstrap_abb_session(hostname, session, SEARCH_PAGE_RETRY_ATTEMPTS)
                    page_html, final_url = downloader.html_get_page(
                        primary_url,
                        retry=SEARCH_PAGE_RETRY_ATTEMPTS,
                        use_bypasser=False,
                        allow_bypasser_fallback=False,
                        include_response_url=True,
                        success_delay=0,
                        session=session,
                    )
                    was_home_redirect = _is_homepage_redirect(final_url, hostname)
                    if page_html and not was_home_redirect:
                        break
                    logger.debug(
                        "ABB page 1 session refresh %d/%d failed",
                        refresh_attempt,
                        FIRST_PAGE_SESSION_REFRESH_ATTEMPTS,
                    )

            if not page_html:
                logger.warning("Failed to fetch page %s", page)
                break

            # Check if we were redirected to the homepage (search was rejected/blocked)
            if was_home_redirect:
                # Search was redirected to homepage - this means the search failed
                # This can happen due to geo-blocking, rate limiting, or invalid query format
                if page == 1:
                    logger.warning(
                        "Search query '%s' was redirected to homepage - search may be blocked or invalid",
                        query,
                    )
                break

            # Parse HTML
            soup = BeautifulSoup(page_html, "html.parser")

            # Extract book entries
            posts = soup.select(".post")
            if not posts:
                # No more results
                break

            for post in posts:
                try:
                    # Extract title
                    title_elem = post.select_one(".postTitle > h2 > a")
                    if not title_elem:
                        continue

                    title = title_elem.text.strip()

                    # Extract link (relative, needs hostname prefix)
                    href = _coerce_attribute_to_str(title_elem.get("href", ""))
                    if not href:
                        continue

                    link = _normalize_result_url(href, hostname)
                    if not link:
                        continue

                    # Extract cover image (try .postContent .center img first, then fallback to any img)
                    cover = None
                    cover_elem = post.select_one(".postContent .center img") or post.select_one(
                        "img"
                    )
                    if cover_elem:
                        cover = (
                            _normalize_result_url(
                                _coerce_attribute_to_str(cover_elem.get("src", "")),
                                hostname,
                            )
                            or None
                        )

                    # Extract language from .postInfo
                    language = None
                    post_info = post.select_one(".postInfo")
                    if post_info:
                        info_text = post_info.get_text(separator=" ", strip=True).replace(
                            "\xa0", " "
                        )
                        lang_match = LANGUAGE_PATTERN.search(info_text)
                        if lang_match:
                            language = lang_match.group(1).strip()

                    # Extract format, bitrate, size, and posted date from .postContent
                    posted_date = None
                    format_type = None
                    bitrate = None
                    size_str = None

                    post_content = post.select_one(".postContent")
                    if post_content:
                        content_text = post_content.get_text(separator=" ", strip=True).replace(
                            "\xa0", " "
                        )

                        # Extract posted date
                        posted_match = POSTED_PATTERN.search(content_text)
                        if posted_match:
                            posted_date = posted_match.group(1).strip()

                        # Extract format (e.g., "M4B", "MP3")
                        format_match = FORMAT_PATTERN.search(content_text)
                        if format_match:
                            format_type = format_match.group(1).strip()

                        # Extract bitrate (e.g., "256 Kbps")
                        bitrate_match = BITRATE_PATTERN.search(content_text)
                        if bitrate_match:
                            bitrate = bitrate_match.group(1).strip()

                        # Extract file size (e.g., "11.68 GBs" -> normalized to "11.68 GB")
                        size_match = SIZE_PATTERN.search(content_text)
                        if size_match:
                            size_value = size_match.group(1)
                            size_unit = size_match.group(2).strip()
                            if size_unit.lower().endswith("s"):
                                size_unit = size_unit[:-1]
                            size_unit = size_unit.upper()
                            size_str = f"{size_value} {size_unit}"

                    results.append(
                        {
                            "title": title,
                            "link": link,
                            "cover": cover or None,
                            "language": language,
                            "format": format_type,
                            "bitrate": bitrate,
                            "size": size_str,
                            "posted_date": posted_date,
                        }
                    )
                except (TypeError, ValueError, AttributeError, IndexError, KeyError) as e:
                    logger.debug("Skipping post due to error: %s", e)
                    continue

            # Rate limiting delay between pages
            if page < max_pages and rate_limit_delay > 0:
                time.sleep(rate_limit_delay)
        except Exception:
            logger.exception("Unexpected error on page %s", page)
            break

    logger.info("Found %s results for query '%s'", len(results), query)
    return results


def _get_cached_detail_page(details_url: str) -> str | None:
    with _detail_page_cache_lock:
        entry = _detail_page_cache.get(details_url)
        if entry is None:
            return None
        fetched_at, html = entry
        if time.monotonic() - fetched_at > DETAIL_PAGE_CACHE_TTL_SECONDS:
            del _detail_page_cache[details_url]
            return None
    return html


def _store_cached_detail_page(details_url: str, html: str) -> None:
    with _detail_page_cache_lock:
        _detail_page_cache[details_url] = (time.monotonic(), html)
        while len(_detail_page_cache) > DETAIL_PAGE_CACHE_MAX_ENTRIES:
            oldest = min(_detail_page_cache, key=lambda key: _detail_page_cache[key][0])
            del _detail_page_cache[oldest]


def clear_detail_page_cache() -> None:
    """Drop cached detail pages (used by tests)."""
    with _detail_page_cache_lock:
        _detail_page_cache.clear()


def _fetch_detail_page_once(details_url: str, hostname: str) -> str:
    session = requests.Session()
    _bootstrap_abb_session(hostname, session, DETAIL_PAGE_RETRY_ATTEMPTS)
    return _coerce_markup_to_html(
        downloader.html_get_page(
            details_url,
            retry=DETAIL_PAGE_RETRY_ATTEMPTS,
            use_bypasser=False,
            allow_bypasser_fallback=False,
            success_delay=0,
            session=session,
        )
    )


def fetch_detail_html(details_url: str, hostname: str = "audiobookbay.lu") -> str:
    """Fetch a detail page (one retry with a fresh session), cached briefly per URL."""
    cached = _get_cached_detail_page(details_url)
    if cached is not None:
        logger.debug("Reusing recently fetched detail page: %s", details_url)
        return cached
    detail_html = _fetch_detail_page_once(details_url, hostname)
    if not detail_html:
        detail_html = _fetch_detail_page_once(details_url, hostname)
    if detail_html:
        _store_cached_detail_page(details_url, detail_html)
    return detail_html


def _parse_file_row(text: str) -> PackFile | None:
    match = FILE_ROW_SIZE_PATTERN.match(text.strip())
    if not match:
        return None
    multiplier = _FILE_SIZE_MULTIPLIERS[match.group("unit")[0].lower()]
    return PackFile(match.group("name"), int(float(match.group("size")) * multiplier))


def extract_file_list(detail_html: str) -> list[PackFile] | None:
    """Read the torrent file rows off a detail page.

    ABB renders the torrent's file table as single-cell rows between the
    "This is a Multifile Torrent" marker (absent for single-file torrents) and the
    "Combined File Size" row. Returns None when the page has no such table.
    """
    soup = BeautifulSoup(detail_html, "html.parser")
    rows: list[PackFile] = []
    for row in soup.find_all("tr"):
        cells = row.find_all("td")
        if not cells:
            continue
        label = cells[0].get_text(" ", strip=True)
        if label.lower().startswith("combined file size"):
            return rows or None
        if len(cells) != 1:
            rows = []  # a two-column metadata row means we're not in the file table yet
            continue
        text = cells[0].get_text(" ", strip=True)
        if "multifile torrent" in text.lower():
            rows = []
            continue
        parsed = _parse_file_row(text)
        if parsed is not None:
            rows.append(parsed)
    return None


# One credit in a line of them: "Anne Rice (Author)", "Dylan Baker (Reader)".
_CREDIT = re.compile(r"^(?P<name>[^()]+?)\s*\((?P<role>[A-Za-z ]+)\)$")
_LEADING_BY = re.compile(r"^\s*by\s+", re.IGNORECASE)
_AUTHOR_ROLES = frozenset({"author", "writer"})


def _credited_authors(text: str) -> list[str]:
    """Authors from a paragraph that is nothing but credits, else an empty list.

    "by Anne Rice (Author)" and "James Patterson (Author), Richard DiLallo (Author),
    Dylan Baker (Reader)" qualify. A paragraph with anything else in it is description,
    and a name it mentions says nothing about who wrote the book.
    """
    parts = [part.strip() for part in _LEADING_BY.sub("", text).split(",")]
    matches = [_CREDIT.match(part) for part in parts]
    if not parts or not all(matches):
        return []
    return [
        credit.group("name").strip()
        for credit in matches
        if credit and credit.group("role").strip().lower() in _AUTHOR_ROLES
    ]


def extract_authors(detail_html: str) -> list[str]:
    """The book's authors as a detail page credits them; empty when it does not say.

    Newer posts mark the authors up ("Written by" and ``span.author``); older ones, whose
    listing title carries no " - Author", have a line of credits instead. Only names
    credited as the author count: a narrator is not one, and a name in the description
    is not read at all.
    """
    soup = BeautifulSoup(detail_html, "html.parser")
    content = soup.select_one(".postContent") or soup
    marked = [span.get_text(" ", strip=True) for span in content.select("span.author")]
    if any(marked):
        return [name for name in marked if name]
    for paragraph in content.find_all("p"):
        authors = _credited_authors(paragraph.get_text(" ", strip=True))
        if authors:
            return authors
    return []


# What a visitor writes under a post nobody is seeding: "0 seeders", "no seeds", "please seed",
# "unseeded", "dead". Anything shorter or vaguer than these is ignored.
_SEED_COMPLAINT_PATTERN = re.compile(
    r"\b(?:0|no|zero)\s+seed(?:s|ers?)?\b"
    r"|\bunseeded\b|\bnot\s+seeding\b|\bno\s+one\s+(?:is\s+)?seeding\b"
    r"|\bplease\s+(?:re-?)?seed\b|\bseeders?\s+needed\b|\bneeds?\s+seed|\bdead\b",
    re.IGNORECASE,
)


def extract_seed_complaints(detail_html: str) -> int:
    """How many comments on a post say nobody is seeding it. A hint, not a seed count."""
    soup = BeautifulSoup(detail_html, "html.parser")
    return sum(
        1
        for body in soup.select('[itemprop="reviewBody"]')
        if _SEED_COMPLAINT_PATTERN.search(body.get_text(" ", strip=True))
    )


def extract_magnet_link(details_url: str, hostname: str = "audiobookbay.lu") -> str | None:
    """Extract info hash and trackers from book detail page, then construct magnet link.

    Args:
        details_url: URL of the book's detail page
        hostname: AudiobookBay hostname (for logging)

    Returns:
        Magnet link, or None if extraction fails

    """
    try:
        detail_html = fetch_detail_html(details_url, hostname)
        if not detail_html:
            logger.warning("Failed to fetch details page")
            return None

        soup = BeautifulSoup(detail_html, "html.parser")

        # 1. Extract Info Hash
        # Look for <td>Info Hash</td> and get next sibling value
        info_hash = None
        info_hash_rows = soup.find_all("td")
        for td in info_hash_rows:
            if td.text.strip().lower() == "info hash":
                next_td = td.find_next_sibling("td")
                if next_td:
                    info_hash = next_td.text.strip()
                    break

        # Alternative: search for text containing "Info Hash" and get next element
        if not info_hash:
            for elem in soup.find_all(string=INFO_HASH_LABEL_PATTERN):
                parent = elem.parent
                if parent and parent.name == "td":
                    next_td = parent.find_next_sibling("td")
                    if next_td:
                        info_hash = next_td.text.strip()
                        break

        if not info_hash:
            logger.warning("Info Hash not found on the page.")
            return None

        # Clean up info hash (remove whitespace, ensure uppercase)
        info_hash = re.sub(r"\s+", "", info_hash).upper()

        # Validate: SHA1 = 40 hex chars, SHA256 = 64 hex chars
        if not re.match(r"^[0-9A-F]{40}$|^[0-9A-F]{64}$", info_hash):
            logger.warning("Info Hash invalid (got %r), trying magnet fallback.", info_hash)
            # Fallback: search entire page for a complete magnet link (e.g. posted in comments)
            magnet_match = re.search(r"magnet:\?xt=urn:btih:([0-9a-fA-F]{40,64})", detail_html)
            if magnet_match:
                info_hash = magnet_match.group(1).upper()
                logger.info("Found hash via magnet fallback: %s", info_hash)
            else:
                logger.warning("No valid magnet link found on page, giving up.")
                return None

        # 2. Extract Trackers
        # Find all <td> containing udp:// or http://
        trackers = []
        for td in soup.find_all("td"):
            text = td.text.strip()
            if text.startswith(("udp://", "http://", "https://")):
                trackers.append(text)

        # 3. Use default trackers if none found
        if not trackers:
            logger.debug("No trackers found on the page. Using default trackers.")
            trackers = DEFAULT_TRACKERS

        # 4. Construct Magnet Link
        # Format: magnet:?xt=urn:btih:{INFO_HASH}&tr={TRACKER1}&tr={TRACKER2}...
        tracker_params = "&".join(f"tr={quote(tracker)}" for tracker in trackers)
        magnet_link = f"magnet:?xt=urn:btih:{info_hash}&{tracker_params}"

        logger.debug("Generated Magnet Link: %s...", magnet_link[:100])

    except Exception:
        logger.exception("Failed to extract magnet link")
        return None

    else:
        return magnet_link
