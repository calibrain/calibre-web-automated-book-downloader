"""Book download manager handling search and retrieval operations."""

import time, json, os, re
from pathlib import Path
from urllib.parse import quote
from typing import List, Optional, Dict, Union, Callable
from threading import Event
from bs4 import BeautifulSoup, Tag, NavigableString, ResultSet


class SearchUnavailable(Exception):
    """Raised when Anna's Archive cannot be reached via any mirror/DNS."""
    pass

import downloader
from logger import setup_logger
from config import SUPPORTED_FORMATS, BOOK_LANGUAGE
from env import AA_DONATOR_KEY, USE_CF_BYPASS, PRIORITIZE_WELIB, ALLOW_USE_WELIB, DOWNLOAD_PATHS
from models import BookInfo, SearchFilters
import network
logger = setup_logger(__name__)



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

    every_url = soup.find_all("a")
    slow_urls_no_waitlist = []  # Use lists to preserve page order
    slow_urls_with_waitlist = []
    external_urls_libgen = []
    external_urls_z_lib = []
    external_urls_welib: list[str] = []

    for url in every_url:
        try:
            if url.text.strip().lower().startswith("slow partner server"):
                if (
                    url.next is not None
                    and url.next.next is not None
                    and "waitlist" in url.next.next.strip().lower()
                ):
                    internal_text = url.next.next.strip().lower()
                    href = url["href"]
                    if "no waitlist" in internal_text:
                        if href not in slow_urls_no_waitlist:
                            slow_urls_no_waitlist.append(href)
                    else:
                        if href not in slow_urls_with_waitlist:
                            slow_urls_with_waitlist.append(href)
            elif (
                url.next is not None
                and url.next.next is not None
                and "click “GET” at the top" in url.next.next.text.strip()
            ):
                libgen_url = url["href"]
                # TODO : Temporary fix ? Maybe get URLs from https://open-slum.org/ ?
                libgen_url = re.sub(r'libgen\.(lc|is|bz|st)', 'libgen.gl', url["href"])

                if libgen_url not in external_urls_libgen:
                    external_urls_libgen.append(libgen_url)
            elif url.text.strip().lower().startswith("z-lib"):
                if ".onion/" not in url["href"]:
                    href = url["href"]
                    if href not in external_urls_z_lib:
                        external_urls_z_lib.append(href)
        except:
            pass

    # Only prefetch welib when explicitly prioritized; otherwise defer to fallback in download_book
    welib_selector = network.AAMirrorSelector() if USE_CF_BYPASS and PRIORITIZE_WELIB else None
    if USE_CF_BYPASS and PRIORITIZE_WELIB:
        external_urls_welib = _get_download_urls_from_welib(book_id, selector=welib_selector)

    logger.debug(
        "Source inventory for %s -> welib_prefetched=%d, aa_no_wait=%d, aa_wait=%d, libgen=%d, zlib=%d",
        book_id,
        len(external_urls_welib),
        len(slow_urls_no_waitlist),
        len(slow_urls_with_waitlist),
        len(external_urls_libgen),
        len(external_urls_z_lib),
    )

    urls = []
    # Optional: push welib to the front only when explicitly requested
    if PRIORITIZE_WELIB:
        urls += external_urls_welib

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
        publisher=_find_in_divs(divs, "icon-[mdi--company]", isClass=True)[0],
        author=_find_in_divs(divs, "icon-[mdi--user-edit]", isClass=True)[0],
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

def _find_in_divs(divs: List[str], text: str, isClass: bool = False) -> List[str]:
    divs_found = []
    for div in divs:
        if isClass:
            if div.find(class_ = text):
                divs_found.append(div.text.strip())
        else:
            if text in div.text.strip():
                divs_found.append(div.text.strip())
    return divs_found

def _label_source(link: str) -> str:
    """Lightweight source tag for logging/metrics."""
    if "welib.org" in link:
        return "welib"
    if "/dyn/api/fast_download" in link:
        return "aa-fast"
    if "/slow_download/" in link or "annas-" in link:
        return "aa-slow"
    if "libgen" in link:
        return "libgen"
    if "z-lib" in link or "zlibrary" in link:
        return "z-lib"
    return "unknown"

def _friendly_source_name(link: str) -> str:
    """Get a user-friendly name for a download source."""
    if "welib.org" in link:
        return "Welib"
    if "/dyn/api/fast_download" in link or "/slow_download/" in link or "annas-" in link:
        return "AA"
    if "libgen" in link:
        return "Libgen"
    if "z-lib" in link or "zlibrary" in link:
        return "Z-Library"
    return "Mirror"

def _get_download_urls_from_welib(book_id: str, selector: Optional[network.AAMirrorSelector] = None) -> list[str]:
    """Get download urls from welib.org."""
    if ALLOW_USE_WELIB == False:
        return []
    url = f"https://welib.org/md5/{book_id}"
    logger.info(f"Getting download urls from welib.org for {book_id}. While this uses the bypasser, it will not start downloading them yet.")
    sel = selector or network.AAMirrorSelector()
    try:
        html = downloader.html_get_page(url, use_bypasser=True, selector=sel)
    except Exception as exc:
        logger.error_trace(f"Welib fetch failed for {book_id}: {exc}")
        return []
    if not html:
        logger.warning("Welib page empty for %s; skipping fallback URLs", book_id)
        return []
    soup = BeautifulSoup(html, "html.parser")
    download_links = soup.find_all("a", href=True)
    download_links = [link["href"] for link in download_links]
    download_links = [link for link in download_links if "/slow_download/" in link]
    download_links = [downloader.get_absolute_url(url, link) for link in download_links]
    # Deduplicate while preserving order
    seen = set()
    unique_links = []
    for link in download_links:
        if link not in seen:
            seen.add(link)
            unique_links.append(link)
    return unique_links

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
    total_sources = len(links_queue)
    # Track whether we've already fetched welib as a fallback
    welib_fallback_loaded = PRIORITIZE_WELIB
    # Iterate with index so we can append welib links later
    idx = 0
    while idx < len(links_queue):
        link = links_queue[idx]
        try:
            source_label = _label_source(link)
            friendly_name = _friendly_source_name(link)
            current_pos = idx + 1
            # Update total if we added more sources
            total_sources = len(links_queue)
            
            logger.info("Trying download source [%s]: %s (%d/%d)", source_label, link, current_pos, total_sources)
            
            # Update status with simple message showing which source we're trying
            if status_callback:
                status_callback("resolving", f"Trying {friendly_name} ({current_pos}/{total_sources})")

            download_url = _get_download_url(link, book_info.title, cancel_flag, status_callback, selector)
            if download_url == "":
                raise Exception("No download URL resolved")

            logger.info("Resolved download URL [%s]: %s", source_label, download_url)
            # Update status to downloading
            if status_callback:
                status_callback("downloading", None)

            logger.info(f"Downloading `{book_info.title}` from `{download_url}`")

            data = downloader.download_url(download_url, book_info.size or "", progress_callback, cancel_flag, selector)
            if not data:
                raise Exception("No data received")

            logger.info(f"Download finished. Writing to {book_path}")
            with open(book_path, "wb") as f:
                f.write(data.getbuffer())
            logger.info(f"Writing `{book_info.title}` successfully")
            return download_url

        except Exception as e:
            logger.error_trace(f"Failed to download from {link} (source={source_label}): {e}")
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

    return None


def _get_download_url(link: str, title: str, cancel_flag: Optional[Event] = None, status_callback: Optional[Callable[[str, Optional[str]], None]] = None, selector: Optional[network.AAMirrorSelector] = None) -> str:
    """Extract actual download URL from various source pages."""

    url = ""
    sel = selector or network.AAMirrorSelector()
    friendly_name = _friendly_source_name(link)

    if link.startswith(f"{network.get_aa_base_url()}/dyn/api/fast_download.json"):
        page = downloader.html_get_page(link, selector=sel)
        url = json.loads(page).get("download_url")
    else:
        html = downloader.html_get_page(link, selector=sel)

        if html == "":
            return ""

        soup = BeautifulSoup(html, "html.parser")

        if link.startswith("https://z-lib."):
            download_link = soup.find_all("a", href=True, class_="addDownloadedBook")
            if download_link:
                url = download_link[0]["href"]
        elif "/slow_download/" in link:
            # Strategy 1: Look for "📚 Download now" button (exact match)
            download_links = soup.find_all("a", href=True, string="📚 Download now")
            
            # Strategy 2: Flexible match for download button (contains "Download now")
            if not download_links:
                download_links = soup.find_all("a", href=True, string=lambda s: s and "Download now" in s)
            
            # Strategy 3: Look for "copy this URL" text pattern with URL below
            # The page may say: "To download, copy this URL and then paste it in your browser's URL bar:"
            # followed by a code/text element containing the actual download URL
            if not download_links:
                copy_url_text = soup.find(string=lambda s: s and "copy this URL" in s.lower())
                if copy_url_text:
                    # The download URL is typically in the next sibling or nearby element
                    parent = copy_url_text.parent
                    if parent:
                        # Look for a link or code element after the instruction text
                        next_link = parent.find_next("a", href=True)
                        if next_link and next_link.get("href"):
                            url = next_link["href"]
                        else:
                            # Sometimes the URL is in a <code> or text element
                            code_elem = parent.find_next("code")
                            if code_elem:
                                url = code_elem.get_text(strip=True)
                            else:
                                # Try finding URL in nearby text that looks like a URL
                                for sibling in parent.find_next_siblings():
                                    text = sibling.get_text(strip=True) if hasattr(sibling, 'get_text') else str(sibling).strip()
                                    if text.startswith("http"):
                                        url = text
                                        break
            
            # If we found a download link from strategies 1 or 2
            if download_links and not url:
                url = download_links[0]["href"]
            
            # Strategy 4: Check for countdown timer (waitlist servers)
            if not url:
                countdown = soup.find_all("span", class_="js-partner-countdown")
                if countdown:
                    sleep_time = int(countdown[0].text)
                    logger.info(f"Waiting {sleep_time}s for {title}")
                    if cancel_flag is not None and cancel_flag.wait(timeout=sleep_time):
                        logger.info(f"Cancelled wait for {title}")
                        return ""
                    url = _get_download_url(link, title, cancel_flag, status_callback, sel)
                else:
                    # Debug: log what we found to help diagnose future issues
                    all_links = soup.find_all("a", href=True)
                    link_texts = [a.get_text(strip=True)[:50] for a in all_links[:10]]
                    logger.warning(f"No download URL found on page. First 10 link texts: {link_texts}")
        else:
            url = soup.find_all("a", string="GET")[0]["href"]

    return downloader.get_absolute_url(link, url)
