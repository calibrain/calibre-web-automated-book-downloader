"""Book download manager handling search and retrieval operations."""

import time, json, re
from pathlib import Path
from urllib.parse import quote, urljoin
from typing import List, Optional, Dict, Union
from bs4 import BeautifulSoup, Tag, NavigableString, ResultSet

import downloader
from logger import setup_logger
from config import SUPPORTED_FORMATS, BOOK_LANGUAGE, AA_BASE_URL
from env import AA_DONATOR_KEY, USE_CF_BYPASS
from models import BookInfo, SearchFilters

logger = setup_logger(__name__)


def search_books(query: str, filters: SearchFilters) -> List[BookInfo]:
    """Search for books matching the query."""
    query_html = quote(query)

    if filters.isbn:
        isbns = " || ".join(
            [f"('isbn13:{isbn}' || 'isbn10:{isbn}')" for isbn in filters.isbn]
        )
        query_html = quote(f"({isbns}) {query}")

    filters_query = ""

    for value in (filters.lang or BOOK_LANGUAGE):
        if value != "all":
            filters_query += f"&lang={quote(value)}"

    if filters.sort:
        filters_query += f"&sort={quote(filters.sort)}"

    if filters.content:
        for value in filters.content:
            filters_query += f"&content={quote(value)}"

    formats_to_use = filters.format if filters.format else SUPPORTED_FORMATS

    index = 1
    for filter_type, filter_values in vars(filters).items():
        if filter_type in ("author", "title") and filter_values:
            for value in filter_values:
                filters_query += (
                    f"&termtype_{index}={filter_type}&termval_{index}={quote(value)}"
                )
                index += 1

    url = (
        f"{AA_BASE_URL}"
        f"/search?index=&page=1&display=table"
        f"&acc=aa_download&acc=external_download"
        f"&ext={'&ext='.join(formats_to_use)}"
        f"&q={query_html}"
        f"{filters_query}"
    )

    html = downloader.html_get_page(url)
    if not html:
        raise Exception("Failed to fetch search results")

    if "No files found." in html:
        logger.info(f"No books found for query: {query}")
        raise Exception("No books found. Please try another query.")

    soup = BeautifulSoup(html, "html.parser")
    tbody: Tag | NavigableString | None = soup.find("table")

    if not tbody:
        logger.warning(f"No results table found for query: {query}")
        raise Exception("No books found. Please try another query.")

    books: List[BookInfo] = []
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
            format=cells[9].find("span").next.lower(),
            size=cells[10].find("span").next,
        )
    except Exception as e:
        logger.error_trace(f"Error parsing search result row: {e}")
        return None


def get_book_info(book_id: str) -> BookInfo:
    """Get detailed information for a specific book."""
    url = f"{AA_BASE_URL}/md5/{book_id}"
    html = downloader.html_get_page(url)

    if not html:
        raise Exception(f"Failed to fetch book info for ID: {book_id}")

    soup = BeautifulSoup(html, "html.parser")
    return _parse_book_info_page(soup, book_id)


def _parse_book_info_page(soup: BeautifulSoup, book_id: str) -> BookInfo:
    data = soup.select_one(".main-inner > div.mb-4.p-6")
    if not data:
        raise Exception(f"Failed to parse info for book ID: {book_id}")

    preview: str = ""
    node = data.select_one("img")
    if node:
        preview = node.get("src", "")

    divs = data.find_all("div")

    start_div_id = next(
        (i for i, div in enumerate(divs) if div.get("class") and "text-3xl" in div.get("class")), -1
    )
    if start_div_id == -1:
        raise Exception("Cannot find title div.")

    # Parse format/size line (the div just before the title)
    format_div_text = divs[start_div_id - 1].get_text(" ", strip=True)
    format_parts = [p.strip() for p in format_div_text.split(",") if p.strip()]

    book_format = None
    size = None
    if format_parts:
        # common pattern: "<lang>, <format>. <size>"
        ext_matcher = re.compile(r'\.(epub|pdf|mobi|azw3|fb2|djvu|cbz|cbr)\b', re.I)
        for p in format_parts:
            m = ext_matcher.search(p)
            if m:
                # store extension without leading dot, e.g. "pdf"
                book_format = m.group(1).lower()
            if "mb" in p.lower() or "kb" in p.lower():
                size = p

    # Collect download links (normalize to absolute)
    urls: List[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/fast_download/" in href or "/slow_download/" in href:
            urls.append(urljoin(AA_BASE_URL, href))

    # Deduplicate while keeping order
    seen = set()
    urls = [u for u in urls if not (u in seen or seen.add(u))]

    book_info = BookInfo(
        id=book_id,
        preview=preview,
        title=divs[start_div_id].get_text(strip=True).replace("🔍", ""),
        publisher=divs[start_div_id + 1].get_text(strip=True),
        author=divs[start_div_id + 2].get_text(strip=True).replace("🔍", ""),
        format=book_format,
        size=size,
        download_urls=urls,
    )

    info = _extract_book_metadata(divs[start_div_id + 3 :])
    book_info.info = info
    if info.get("Language"):
        book_info.language = info["Language"][0]
    if info.get("Year"):
        book_info.year = info["Year"][0]

    return book_info


def _extract_book_metadata(
    metadata_divs: Union[ResultSet[Tag], List[Tag]],
) -> Dict[str, List[str]]:
    """Extract metadata from book info divs."""
    info: Dict[str, List[str]] = {}

    # First block (label/value divs)
    if metadata_divs:
        sub_data = metadata_divs[0].find_all("div")
        for i in range(0, len(sub_data) - 1, 2):
            key = sub_data[i].get_text(strip=True)
            value = sub_data[i + 1].get_text(strip=True)
            info.setdefault(key, []).append(value)

    # Second block (aria-label="code tabs")
    meta_spans: List[Tag] = []
    for div in metadata_divs:
        if div.find_all("div", {"aria-label": "code tabs"}):
            meta_spans = div.find_all("span")
            break
    for i in range(0, len(meta_spans) - 1, 2):
        key = meta_spans[i].get_text(strip=True)
        value = meta_spans[i + 1].get_text(strip=True)
        info.setdefault(key, []).append(value)

    relevant_prefixes = ["ISBN-", "ALTERNATIVE", "ASIN", "Goodreads", "Language", "Year"]
    return {
        k.strip(): v
        for k, v in info.items()
        if any(k.lower().startswith(prefix.lower()) for prefix in relevant_prefixes)
        and "filename" not in k.lower()
    }


def download_book(book_info: BookInfo, book_path: Path) -> bool:
    """Download a book from available sources into book_path."""
    if len(book_info.download_urls) == 0:
        book_info = get_book_info(book_info.id)

    download_links = list(book_info.download_urls)  # copy

    # Prefer donor API if present
    if AA_DONATOR_KEY:
        download_links.insert(
            0, f"{AA_BASE_URL}/dyn/api/fast_download.json?md5={book_info.id}&key={AA_DONATOR_KEY}"
        )

    for link in download_links:
        try:
            download_url = _get_download_url(link, book_info.title)
            if not download_url:
                raise Exception("No download URL extracted")

            logger.info(f"Downloading `{book_info.title}` from `{download_url}`")
            data = downloader.download_url(download_url, book_info.size or "")
            if not data:
                raise Exception("No data received")

            logger.info(f"Download finished. Writing to {book_path}")
            with open(book_path, "wb") as f:
                f.write(data.getbuffer())
            logger.info(f"Writing `{book_info.title}` successfully")
            return True

        except Exception as e:
            logger.error_trace(f"Failed to download from {link}: {e}")
            continue

    return False


def _get_download_url(link: str, title: str) -> str:
    """Extract actual download URL from various source pages."""
    # Ensure the page URL itself is absolute
    page_url = urljoin(AA_BASE_URL, link)

    url = ""
    if page_url.startswith(f"{AA_BASE_URL}/dyn/api/fast_download.json"):
        page = downloader.html_get_page(page_url)
        try:
            url = json.loads(page).get("download_url") if page else ""
        except Exception:
            url = ""
    else:
        html = downloader.html_get_page(page_url)
        if not html:
            return ""

        soup = BeautifulSoup(html, "html.parser")

        # z-lib flow
        if page_url.startswith("https://z-lib."):
            download_link = soup.find_all("a", href=True, class_="addDownloadedBook")
            if download_link:
                url = download_link[0]["href"]
        # slow download flow (wait + "Download now")
        elif page_url.startswith(f"{AA_BASE_URL}/slow_download/"):
            # Look for the explicit Download now button
            download_links = soup.find_all("a", href=True, string=lambda s: s and "Download now" in s)
            if not download_links:
                countdown = soup.find_all("span", class_="js-partner-countdown")
                if countdown:
                    # Some pages show a numeric countdown
                    try:
                        sleep_time = int(countdown[0].get_text(strip=True))
                        logger.info(f"Waiting {sleep_time}s for {title}")
                        time.sleep(sleep_time)
                        return _get_download_url(page_url, title)
                    except Exception:
                        pass
            else:
                url = download_links[0]["href"]
        # fast download or other partner page
        else:
            # Many partners put a single "GET" link
            get_links = soup.find_all("a", href=True, string=lambda s: s and s.strip().upper() == "GET")
            if get_links:
                url = get_links[0]["href"]
            else:
                # Fallback: first anchor that looks like a direct file
                a = soup.find("a", href=True)
                url = a["href"] if a else ""

    # Normalize the extracted URL to absolute
    return urljoin(page_url, url) if url else ""
