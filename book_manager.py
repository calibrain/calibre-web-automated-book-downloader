"""Book download manager handling search and retrieval operations."""

import time
from urllib.parse import urlparse, quote
from typing import List, Optional, Dict
from bs4 import BeautifulSoup
from io import BytesIO

from logger import setup_logger
from config import SUPPORTED_FORMATS, BOOK_LANGUAGE, ANNAS_ARCHIVE_KEY
from models import BookInfo
import network

logger = setup_logger(__name__)

def search_books(query: str) -> List[BookInfo]:
    """Search for books matching the query.
    
    Args:
        query: Search term (ISBN, title, author, etc.)
        
    Returns:
        List[BookInfo]: List of matching books
        
    Raises:
        Exception: If no books found or parsing fails
    """
    query_html = quote(query)
    url = (
        f"https://annas-archive.org/search?index=&page=1&display=table"
        f"&acc=aa_download&acc=external_download&sort="
        f"&ext={'&ext='.join(SUPPORTED_FORMATS)}&lang={'&lang='.join(BOOK_LANGUAGE)}&q={query_html}"
    )
    
    html = network.html_get_page(url)
    if not html:
        raise Exception("Failed to fetch search results")
        
    if "No files found." in html:
        logger.info(f"No books found for query: {query}")
        raise Exception("No books found. Please try another query.")

    soup = BeautifulSoup(html, 'html.parser')
    tbody = soup.find('table')
    
    if not tbody:
        logger.warning(f"No results table found for query: {query}")
        raise Exception("No books found. Please try another query.")

    books = []
    for line_tr in tbody.find_all('tr'):
        try:
            book = _parse_search_result_row(line_tr)
            if book:
                books.append(book)
        except Exception as e:
            logger.error(f"Failed to parse search result row: {e}")

    books.sort(
        key=lambda x: (
            SUPPORTED_FORMATS.index(x.format)
            if x.format in SUPPORTED_FORMATS
            else len(SUPPORTED_FORMATS)
        )
    )
    
    return books

def _parse_search_result_row(row) -> Optional[BookInfo]:
    """Parse a single search result row into a BookInfo object."""
    try:
        cells = row.find_all('td')
        preview_img = cells[0].find('img')
        preview = preview_img['src'] if preview_img else None
        
        return BookInfo(
            id=row.find('a')['href'].split('/')[-1],
            preview=preview,
            title=cells[1].find('span').next,
            author=cells[2].find('span').next,
            publisher=cells[3].find('span').next,
            year=cells[4].find('span').next,
            language=cells[7].find('span').next,
            format=cells[9].find('span').next.lower(),
            size=cells[10].find('span').next
        )
    except Exception as e:
        logger.error(f"Error parsing search result row: {e}")
        return None

def get_book_info(book_id: str) -> BookInfo:
    """Get detailed information for a specific book.
    
    Args:
        book_id: Book identifier (MD5 hash)
        
    Returns:
        BookInfo: Detailed book information
    """
    url = f"https://annas-archive.org/md5/{book_id}"
    html = network.html_get_page(url)
    
    if not html:
        raise Exception(f"Failed to fetch book info for ID: {book_id}")

    soup = BeautifulSoup(html, 'html.parser')
    data = soup.select_one('body > main > div:nth-of-type(1)')
    
    if not data:
        raise Exception(f"Failed to parse book info for ID: {book_id}")

    return _parse_book_info_page(data, book_id)

def _parse_book_info_page(data, book_id: str) -> BookInfo:
    """Parse the book info page HTML into a BookInfo object."""
    preview = data.select_one(
        'div:nth-of-type(1) > img'
    )['src']

    # Find the start of book information
    divs = data.find_all('div')
    start_div_id = next(
        (i for i, div in enumerate(divs) if "🔍" in div.text),
        3
    )

    format_div = divs[start_div_id - 1].text
    format_parts = format_div.split(".")
    if len(format_parts) > 1:
        format = format_parts[1].split(",")[0].strip().lower()
    else:
        format = None

    size = next(
        (token.strip() for token in format_div.split(",")
         if token.strip() and token.strip()[0].isnumeric()),
        None
    )

    # Extract basic information
    book_info = BookInfo(
        id=book_id,
        preview=preview,
        title=divs[start_div_id].next,
        publisher=divs[start_div_id + 1].next,
        author=divs[start_div_id + 2].next,
        format=format,
        size=size
    )

    # Extract additional metadata
    info = _extract_book_metadata(divs[start_div_id + 3:])
    book_info.info = info

    # Set language and year from metadata if available
    if info.get("Language"):
        book_info.language = info["Language"][0]
    if info.get("Year"):
        book_info.year = info["Year"][0]

    return book_info

def _extract_book_metadata(metadata_divs) -> Dict[str, List[str]]:
    """Extract metadata from book info divs."""
    info = {}
    
    # Process the first set of metadata
    sub_data = metadata_divs[0].find_all('div')
    for i in range(0, len(sub_data) - 1, 2):
        key = sub_data[i].next
        value = sub_data[i + 1].next
        if key not in info:
            info[key] = []
        info[key].append(value)

    # Process the second set of metadata (spans)
    # Find elements where aria-label="code tabs"
    meta_spans = []
    for div in metadata_divs:
        if div.find_all('div', {'aria-label': 'code tabs'}):
            meta_spans = div.find_all('span')
            break
    for i in range(0, len(meta_spans) - 1, 2):
        key = meta_spans[i].next
        value = meta_spans[i + 1].next
        if key not in info:
            info[key] = []
        info[key].append(value)

    # Filter relevant metadata
    relevant_prefixes = [
        "ISBN-", "ALTERNATIVE", "ASIN", "Goodreads", "Language", "Year"
    ]
    return {
        k.strip(): v for k, v in info.items()
        if any(k.lower().startswith(prefix.lower()) for prefix in relevant_prefixes)
        and "filename" not in k.lower()
    }

def download_book(book_id: str, title: str) -> Optional[BytesIO]:
    """Download a book from available sources.
    
    Args:
        book_id: Book identifier (MD5 hash)
        title: Book title for logging
        
    Returns:
        Optional[BytesIO]: Book content buffer if successful
    """

    """If ANNAS_ARCHIVE_KEY is set, use the fast download URL. Else try other sources."""
    if ANNAS_ARCHIVE_KEY:
        try:
            download_url = network.get_donator_download_url(book_id, title)
            if download_url:
                logger.info(f"Downloading {title} from {download_url}")
                return network.download_url(download_url)
        except Exception as e:
            logger.error(f"Failed to download from donator API {link}: {e}. Trying other sources.")

    """Not a donator. Try other sources."""
    download_links = [
        f"https://annas-archive.org/slow_download/{book_id}/0/2",
        f"https://libgen.li/ads.php?md5={book_id}",
        f"https://library.lol/fiction/{book_id}",
        f"https://library.lol/main/{book_id}",
        f"https://annas-archive.org/slow_download/{book_id}/0/0",
        f"https://annas-archive.org/slow_download/{book_id}/0/1"
    ]

    for link in download_links:
        try:
            download_url = _get_download_url(link, title)
            if download_url:
                logger.info(f"Downloading {title} from {download_url}")
                return network.download_url(download_url)
        except Exception as e:
            logger.error(f"Failed to download from {link}: {e}")
            continue
    
    return None

def _get_download_url(link: str, title: str) -> Optional[str]:
    """Extract actual download URL from various source pages."""
    html = network.html_get_page_cf(link)
    if not html:
        return None

    soup = BeautifulSoup(html, 'html.parser')
    
    if link.startswith("https://z-lib.gs"):
        download_link = soup.find_all('a', href=True, class_="addDownloadedBook")
        if download_link:
            parsed = urlparse(download_link[0]['href'])
            return f"{parsed.scheme}://{parsed.netloc}{download_link[0]['href']}"
            
    elif link.startswith("https://libgen.li"):
        get_section = soup.find_all('h2', string="GET")
        if get_section:
            href = get_section[0].parent['href']
            parsed = urlparse(href)
            return f"{parsed.scheme}://{parsed.netloc}/{href}"
            
    elif link.startswith("https://library.lol/fiction/"):
        get_section = soup.find_all('h2', string="GET")
        if get_section:
            return get_section[0].parent['href']
            
    elif link.startswith("https://annas-archive.org/slow_download/"):
        download_links = soup.find_all('a', href=True, string="📚 Download now")
        if not download_links:
            countdown = soup.find_all('span', class_="js-partner-countdown")
            if countdown:
                sleep_time = int(countdown[0].text)
                logger.info(f"Waiting {sleep_time}s for {title}")
                time.sleep(sleep_time + 5)
                return _get_download_url(link, title)
        else:
            return download_links[0]['href']
            
    return None
