"""Book download manager handling search and retrieval operations."""

import json
import re
import time
from pathlib import Path
from threading import Event
from typing import Callable, Dict, List, Optional
from urllib.parse import quote

from bs4 import BeautifulSoup, NavigableString, Tag

import downloader
import network
from config import BOOK_LANGUAGE, SUPPORTED_FORMATS
from env import AA_DONATOR_KEY, ALLOW_USE_WELIB, DOWNLOAD_PATHS, PRIORITIZE_WELIB, USE_CF_BYPASS
from logger import setup_logger
from models import BookInfo, SearchFilters

logger = setup_logger(__name__)


class SearchUnavailable(Exception):
    """Raised when Anna's Archive cannot be reached via any mirror/DNS."""
    pass



def search_books(query: str, filters: SearchFilters) -> List[BookInfo]:
    """Search for books matching the query.

    Args:
        query: Search term (ISBN, title, author, etc.)

    Returns:
        List[BookInfo]: List of matching books

    Raises:
        Exception: If no books found or parsing fails
    """
    query_html = quote(query)

    if filters.isbn:
        # ISBNs are included in query string
        isbns = " || ".join(
            [f"('isbn13:{isbn}' || 'isbn10:{isbn}')" for isbn in filters.isbn]
        )
        query_html = quote(f"({isbns}) {query}")

    filters_query = ""

    for value in filters.lang or BOOK_LANGUAGE:
        if value != "all":
            filters_query += f"&lang={quote(value)}"

    if filters.sort:
        filters_query += f"&sort={quote(filters.sort)}"

    if filters.content:
        for value in filters.content:
            filters_query += f"&content={quote(value)}"

    # Handle format filter
    formats_to_use = filters.format if filters.format else SUPPORTED_FORMATS

    index = 1
    for filter_type, filter_values in vars(filters).items():
        if filter_type == "author" or filter_type == "title" and filter_values:
            for value in filter_values:
                filters_query += (
                    f"&termtype_{index}={filter_type}&termval_{index}={quote(value)}"
                )
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

    html = downloader.html_get_page(url, selector=selector)
    if not html:
        # Network/mirror exhaustion path bubbles up so API can notify clients
        raise SearchUnavailable("Unable to reach Anna's Archive. Network restricted or mirrors are blocked.")

    if "No files found." in html:
        logger.info(f"No books found for query: {query}")
        return []

    soup = BeautifulSoup(html, "html.parser")
    tbody: Tag | NavigableString | None = soup.find("table")

    if not tbody:
        logger.warning(f"No results table found for query: {query}")
        raise Exception("No books found. Please try another query.")

    books = []
    if isinstance(tbody, Tag):
        for line_tr in tbody.find_all("tr"):
            try:
                book = _parse_search_result_row(line_tr)
                if book:
                    books.append(book)
            except Exception as e:
                logger.error_trace(f"Failed to parse search result row: {e}")

    books.sort(
        key=lambda x: (
            SUPPORTED_FORMATS.index(x.format)
            if x.format in SUPPORTED_FORMATS
            else len(SUPPORTED_FORMATS)
        )
    )

    return books


def _parse_search_result_row(row: Tag) -> Optional[BookInfo]:
    """Parse a single search result row into a BookInfo object."""
    try:
        # Skip ad rows
        if row.text.strip().lower().startswith("your ad here"):
            return None
        cells = row.find_all("td")
        preview_img = cells[0].find("img")
        preview = preview_img["src"] if preview_img else None

        return BookInfo(
            id=row.find_all("a")[0]["href"].split("/")[-1],
            preview=preview,
            title=cells[1].find("span").next,
            author=cells[2].find("span").next,
            publisher=cells[3].find("span").next,
            year=cells[4].find("span").next,
            language=cells[7].find("span").next,
            content=cells[8].find("span").next.lower(),
            format=cells[9].find("span").next.lower(),
            size=cells[10].find("span").next,
        )
    except Exception as e:
        logger.error_trace(f"Error parsing search result row: {e}")
        return None


def get_book_info(book_id: str) -> BookInfo:
    """Get detailed information for a specific book.

    Args:
        book_id: Book identifier (MD5 hash)

    Returns:
        BookInfo: Detailed book information
    """
    url = f"{network.get_aa_base_url()}/md5/{book_id}"
    selector = network.AAMirrorSelector()
    html = downloader.html_get_page(url, selector=selector)

    if not html:
        raise Exception(f"Failed to fetch book info for ID: {book_id}")

    soup = BeautifulSoup(html, "html.parser")

    return _parse_book_info_page(soup, book_id)


def _parse_book_info_page(soup: BeautifulSoup, book_id: str) -> BookInfo:
    """Parse the book info page HTML into a BookInfo object."""
    data = soup.select_one("body > main > div:nth-of-type(1)")

    if not data:
        raise Exception(f"Failed to parse book info for ID: {book_id}")

    preview: str = ""

    node = data.select_one("div:nth-of-type(1) > img")
    if node:
        preview_value = node.get("src", "")
        if isinstance(preview_value, list):
            preview = preview_value[0]
        else:
            preview = preview_value

    data = soup.find_all("div", {"class": "main-inner"})[0].find_next("div")
    divs = list(data.children)

    # Collect download URLs by source type (lists preserve page order, dedup inline)
    slow_urls_no_waitlist: list[str] = []
    slow_urls_with_waitlist: list[str] = []
    external_urls_libgen: list[str] = []
    external_urls_z_lib: list[str] = []

    def _append_unique(lst: list[str], href: str) -> None:
        if href and href not in lst:
            lst.append(href)

    for anchor in soup.find_all("a"):
        try:
            text = anchor.text.strip().lower()
            href = anchor.get("href", "")
            next_text = ""
            if anchor.next and anchor.next.next:
                next_text = getattr(anchor.next.next, 'text', str(anchor.next.next)).strip().lower()

            if text.startswith("slow partner server") and "waitlist" in next_text:
                if "no waitlist" in next_text:
                    _append_unique(slow_urls_no_waitlist, href)
                else:
                    _append_unique(slow_urls_with_waitlist, href)
            elif 'click "get" at the top' in next_text:
                # Normalize libgen domains
                libgen_url = re.sub(r'libgen\.(lc|is|bz|st)', 'libgen.gl', href)
                _append_unique(external_urls_libgen, libgen_url)
            elif text.startswith("z-lib") and ".onion/" not in href:
                _append_unique(external_urls_z_lib, href)
        except:
            pass

    logger.debug(
        "Source inventory for %s -> aa_no_wait=%d, aa_wait=%d, libgen=%d, zlib=%d",
        book_id,
        len(slow_urls_no_waitlist),
        len(slow_urls_with_waitlist),
        len(external_urls_libgen),
        len(external_urls_z_lib),
    )

    urls = []

    # Prefer AA / partner and other mirrors first
    urls += slow_urls_no_waitlist if USE_CF_BYPASS else []
    urls += external_urls_libgen
    urls += slow_urls_with_waitlist if USE_CF_BYPASS else []
    urls += external_urls_z_lib

    for i in range(len(urls)):
        urls[i] = downloader.get_absolute_url(network.get_aa_base_url(), urls[i])

    # Remove empty urls
    urls = [url for url in urls if url != ""]

    # Filter out divs that are not text
    original_divs = divs
    divs = [div for div in divs if div.text.strip() != ""]

    all_details = _find_in_divs(divs, " · ")
    format = ""
    size = ""
    content = ""
    
    for _details in all_details:
        _details = _details.split(" · ")
        for f in _details:
            if format == "" and f.strip().lower() in SUPPORTED_FORMATS:
                format = f.strip().lower()
            if size == "" and any(u in f.strip().lower() for u in ["mb", "kb", "gb"]):
                size = f.strip().lower()
            if content == "":
                for ct in DOWNLOAD_PATHS.keys():
                    if ct in f.strip().lower():
                        content = ct
                        break
        if format == "" or size == "":
            for f in _details:
                stripped = f.strip().lower()
                if format == "" and stripped and " " not in stripped:
                    format = stripped
                if size == "" and "." in stripped:
                    size = stripped
    
    book_title = _find_in_divs(divs, "🔍")[0].strip("🔍").strip()

    # Extract basic information
    description = _extract_book_description(soup)

    book_info = BookInfo(
        id=book_id,
        preview=preview,
        title=book_title,
        content=content,
        publisher=_find_in_divs(divs, "icon-[mdi--company]", is_class=True)[0],
        author=_find_in_divs(divs, "icon-[mdi--user-edit]", is_class=True)[0],
        format=format,
        size=size,
        description=description,
        download_urls=urls,
    )

    # Extract additional metadata
    info = _extract_book_metadata(original_divs[-6])
    book_info.info = info

    # Set language and year from metadata if available
    if info.get("Language"):
        book_info.language = info["Language"][0]
    if info.get("Year"):
        book_info.year = info["Year"][0]

    # TODO :
    # Backfill missing metadata from original book
    # To do this, we need to cache the results of search_books() in some kind of LRU

    return book_info

def _find_in_divs(divs: List, text: str, is_class: bool = False) -> List[str]:
    """Find divs containing text or having a specific class."""
    results = []
    for div in divs:
        if is_class:
            if div.find(class_=text):
                results.append(div.text.strip())
        elif text in div.text.strip():
            results.append(div.text.strip())
    return results

# Download source definitions: (log_label, friendly_name, url_patterns)
_DOWNLOAD_SOURCES = [
    ("welib", "Welib", ["welib.org"]),
    ("aa-fast", "AA", ["/dyn/api/fast_download"]),
    ("aa-slow", "AA", ["/slow_download/", "annas-"]),
    ("libgen", "Libgen", ["libgen"]),
    ("z-lib", "Z-Library", ["z-lib", "zlibrary"]),
]


def _get_source_info(link: str) -> tuple[str, str]:
    """Get source label and friendly name for a download link.
    
    Args:
        link: Download URL
        
    Returns:
        Tuple of (log_label, friendly_name)
    """
    for log_label, friendly_name, patterns in _DOWNLOAD_SOURCES:
        if any(pattern in link for pattern in patterns):
            return log_label, friendly_name
    return "unknown", "Mirror"


def _label_source(link: str) -> str:
    """Get lightweight source tag for logging/metrics."""
    return _get_source_info(link)[0]


def _friendly_source_name(link: str) -> str:
    """Get user-friendly name for a download source."""
    return _get_source_info(link)[1]

def _get_download_urls_from_welib(book_id: str, selector: Optional[network.AAMirrorSelector] = None) -> list[str]:
    """Get download URLs from welib.org (bypasser required)."""
    if not ALLOW_USE_WELIB:
        return []
    url = f"https://welib.org/md5/{book_id}"
    logger.info(f"Fetching welib.org download URLs for {book_id}")
    try:
        html = downloader.html_get_page(url, use_bypasser=True, selector=selector or network.AAMirrorSelector())
    except Exception as exc:
        logger.error_trace(f"Welib fetch failed for {book_id}: {exc}")
        return []
    if not html:
        logger.warning(f"Welib page empty for {book_id}")
        return []
    
    soup = BeautifulSoup(html, "html.parser")
    links = [
        downloader.get_absolute_url(url, a["href"])
        for a in soup.find_all("a", href=True)
        if "/slow_download/" in a["href"]
    ]
    return list(dict.fromkeys(links))  # Dedupe while preserving order

def _get_next_value_div(label_div: Tag) -> Optional[Tag]:
    """Find the next sibling div that holds the value for a metadata label."""
    sibling = label_div.next_sibling
    while sibling:
        if isinstance(sibling, Tag) and sibling.name == "div":
            return sibling
        sibling = sibling.next_sibling
    return None

def _extract_book_description(soup: BeautifulSoup) -> Optional[str]:
    """Extract the primary or alternative description from the book page."""
    container = soup.select_one(".js-md5-top-box-description")
    if not container:
        return None

    description: Optional[str] = None
    alternative: Optional[str] = None

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

def _extract_book_metadata(metadata_divs) -> Dict[str, List[str]]:
    """Extract metadata from book info divs."""
    info: Dict[str, List[str]] = {}

    # Process the first set of metadata
    sub_datas = metadata_divs.find_all("div")[0]
    sub_datas = list(sub_datas.children)
    for sub_data in sub_datas:
        if sub_data.text.strip() == "":
            continue
        sub_data = list(sub_data.children)
        key = sub_data[0].text.strip()
        value = sub_data[1].text.strip()
        if key not in info:
            info[key] = set()
        info[key].add(value)
    
    # make set into list
    for key, value in info.items():
        info[key] = list(value)

    # Filter relevant metadata
    relevant_prefixes = [
        "ISBN-",
        "ALTERNATIVE",
        "ASIN",
        "Goodreads",
        "Language",
        "Year",
    ]
    return {
        k.strip(): v
        for k, v in info.items()
        if any(k.lower().startswith(prefix.lower()) for prefix in relevant_prefixes)
        and "filename" not in k.lower()
    }


# After N consecutive failures of the same source type, skip remaining sources of that type
SOURCE_FAILURE_THRESHOLD = 4

# Minimum valid file size in bytes (10KB) - anything smaller is likely an error page
MIN_VALID_FILE_SIZE = 10 * 1024


def download_book(book_info: BookInfo, book_path: Path, progress_callback: Optional[Callable[[float], None]] = None, cancel_flag: Optional[Event] = None, status_callback: Optional[Callable[[str, Optional[str]], None]] = None) -> Optional[str]:
    """Download a book from available sources.

    Args:
        book_id: Book identifier (MD5 hash)
        title: Book title for logging
        progress_callback: Optional callback for download progress updates
        cancel_flag: Optional cancellation flag
        status_callback: Optional callback for status updates (status, message)

    Returns:
        str: Download URL if successful, None otherwise
    """

    selector = network.AAMirrorSelector()

    if len(book_info.download_urls) == 0:
        book_info = get_book_info(book_info.id)
    download_links = list(book_info.download_urls)

    # If AA_DONATOR_KEY is set, use the fast download URL. Else try other sources.
    if AA_DONATOR_KEY != "":
        download_links.insert(
            0,
            f"{network.get_aa_base_url()}/dyn/api/fast_download.json?md5={book_info.id}&key={AA_DONATOR_KEY}",
        )

    # Preserve order but drop duplicates to avoid retrying the same host
    download_links = list(dict.fromkeys(download_links))

    links_queue = download_links
    
    # Fetch welib URLs upfront when prioritized
    welib_fallback_loaded = False
    if USE_CF_BYPASS and PRIORITIZE_WELIB and ALLOW_USE_WELIB:
        logger.info("Fetching welib.org download URLs (PRIORITIZE_WELIB enabled)")
        if status_callback:
            status_callback("resolving", "Fetching welib sources...")
        welib_links = _get_download_urls_from_welib(book_info.id, selector=selector)
        if welib_links:
            links_queue = welib_links + [l for l in links_queue if l not in welib_links]
        welib_fallback_loaded = True
    
    total_sources = len(links_queue)
    
    # Handle case where no download sources are available
    if total_sources == 0:
        logger.warning(f"No download sources available for: {book_info.title}")
        if status_callback:
            status_callback("error", "No download sources found")
        return None
    
    # Track consecutive failures per source type to skip after threshold
    source_failures: dict[str, int] = {}
    # Iterate with index so we can append welib links later
    idx = 0
    while idx < len(links_queue):
        link = links_queue[idx]
        source_label = _label_source(link)
        friendly_name = _friendly_source_name(link)
        
        # Skip source types that have failed too many times
        if source_failures.get(source_label, 0) >= SOURCE_FAILURE_THRESHOLD:
            logger.info("Skipping %s - source type '%s' failed %d times", link, source_label, SOURCE_FAILURE_THRESHOLD)
            idx += 1
            continue
        
        try:
            current_pos = idx + 1
            # Update total if we added more sources
            total_sources = len(links_queue)
            
            logger.info("Trying download source [%s]: %s (%d/%d)", source_label, link, current_pos, total_sources)
            
            # Build source context for status messages (e.g., "Welib (1/12)")
            source_context = f"{friendly_name} ({current_pos}/{total_sources})"
            
            # Update status with simple message showing which source we're trying
            if status_callback:
                status_callback("resolving", f"Trying {source_context}")

            download_url = _get_download_url(link, book_info.title, cancel_flag, status_callback, selector, source_context)
            if download_url == "":
                raise Exception("No download URL resolved")

            logger.info("Resolved download URL [%s]: %s", source_label, download_url)
            # Update status to downloading
            if status_callback:
                status_callback("downloading", None)

            data = downloader.download_url(download_url, book_info.size or "", progress_callback, cancel_flag, selector)
            if not data:
                raise Exception("No data received from download")
            
            # Validate file size - reject suspiciously small files
            file_size = data.tell()
            if file_size < MIN_VALID_FILE_SIZE:
                logger.warning(f"Downloaded file too small ({file_size} bytes), likely an error page")
                raise Exception(f"File too small ({file_size} bytes)")

            logger.debug(f"Download finished ({file_size} bytes). Writing to {book_path}")
            data.seek(0)  # Reset buffer position before writing
            with open(book_path, "wb") as f:
                f.write(data.getbuffer())
            return download_url

        except Exception as e:
            logger.error_trace(f"Failed to download from {link} (source={source_label}): {e}")
            source_failures[source_label] = source_failures.get(source_label, 0) + 1
            idx += 1
            # If we exhausted primary links and haven't loaded welib yet, fetch them lazily
            if (
                idx >= len(links_queue)
                and not welib_fallback_loaded
                and USE_CF_BYPASS
                and ALLOW_USE_WELIB
            ):
                welib_selector = selector  # reuse AA mirror selector for consistency
                welib_links = _get_download_urls_from_welib(book_info.id, selector=welib_selector)
                welib_fallback_loaded = True
                if welib_links:
                    new_links = [wl for wl in welib_links if wl not in links_queue]
                    if new_links:
                        logger.info("Adding welib fallback links (%d)", len(new_links))
                        links_queue.extend(new_links)
                        # continue loop to try newly added links
            continue

    # All sources exhausted - report final error to UI
    if status_callback:
        status_callback("error", f"All {len(links_queue)} sources failed")
    
    return None


def _get_download_url(link: str, title: str, cancel_flag: Optional[Event] = None, status_callback: Optional[Callable[[str, Optional[str]], None]] = None, selector: Optional[network.AAMirrorSelector] = None, source_context: Optional[str] = None) -> str:
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
        page = downloader.html_get_page(link, selector=sel)
        return downloader.get_absolute_url(link, json.loads(page).get("download_url", ""))

    html = downloader.html_get_page(link, selector=sel)
    if not html:
        return ""

    soup = BeautifulSoup(html, "html.parser")
    url = ""

    # Z-Library
    if link.startswith("https://z-lib."):
        dl = soup.find("a", href=True, class_="addDownloadedBook")
        url = dl["href"] if dl else ""

    # AA slow download / partner servers
    elif "/slow_download/" in link:
        url = _extract_slow_download_url(soup, link, title, cancel_flag, status_callback, sel, source_context)

    # Libgen (GET button)
    else:
        get_btn = soup.find("a", string="GET")
        url = get_btn["href"] if get_btn else ""

    return downloader.get_absolute_url(link, url)


def _extract_slow_download_url(soup: BeautifulSoup, link: str, title: str, cancel_flag: Optional[Event], status_callback, selector, source_context: Optional[str] = None) -> str:
    """Extract download URL from AA slow download pages."""
    # Try "Download now" button variations
    dl_link = soup.find("a", href=True, string="📚 Download now")
    if not dl_link:
        dl_link = soup.find("a", href=True, string=lambda s: s and "Download now" in s)
    if dl_link:
        return dl_link["href"]

    # Try "copy this URL" pattern
    copy_text = soup.find(string=lambda s: s and "copy this url" in s.lower())
    if copy_text and copy_text.parent:
        parent = copy_text.parent
        next_link = parent.find_next("a", href=True)
        if next_link and next_link.get("href"):
            return next_link["href"]
        code_elem = parent.find_next("code")
        if code_elem:
            return code_elem.get_text(strip=True)
        for sibling in parent.find_next_siblings():
            text = sibling.get_text(strip=True) if hasattr(sibling, 'get_text') else str(sibling).strip()
            if text.startswith("http"):
                return text

    # Check for countdown timer (waitlist)
    countdown = soup.find("span", class_="js-partner-countdown")
    if countdown:
        sleep_time = int(countdown.text)
        logger.info(f"Waiting {sleep_time}s for {title}")
        
        # Live countdown with status updates
        remaining = sleep_time
        while remaining > 0:
            # Format countdown message with source context
            if source_context:
                wait_msg = f"{source_context} - Waiting {remaining}s"
            else:
                wait_msg = f"Waiting {remaining}s"
            
            if status_callback:
                status_callback("resolving", wait_msg)
            
            # Wait 1 second (or until cancelled)
            if cancel_flag and cancel_flag.wait(timeout=1):
                logger.info(f"Cancelled wait for {title}")
                return ""
            
            remaining -= 1
        
        # After countdown, update status and re-fetch
        if status_callback and source_context:
            status_callback("resolving", f"{source_context} - Fetching...")
        
        return _get_download_url(link, title, cancel_flag, status_callback, selector, source_context)

    # Debug fallback
    link_texts = [a.get_text(strip=True)[:50] for a in soup.find_all("a", href=True)[:10]]
    logger.warning(f"No download URL found. First 10 links: {link_texts}")
    return ""
