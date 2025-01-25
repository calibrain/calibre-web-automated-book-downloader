"""Book download manager handling search and retrieval operations."""

import time, json
from pathlib import Path
from urllib.parse import quote
from typing import List, Optional, Dict, Union
from bs4 import BeautifulSoup, Tag, NavigableString, ResultSet

from logger import setup_logger
from config import SUPPORTED_FORMATS, BOOK_LANGUAGE, AA_DONATOR_KEY, AA_BASE_URL, USE_CF_BYPASS
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
        #ISBNs are included in query string
        isbns = " || ".join([f"('isbn13:{isbn}' || 'isbn10:{isbn}')" for isbn in filters.isbn])
        query_html = quote(f"({isbns}) {query}")
    
    filters_query = ""
    
    for value in filters.lang or BOOK_LANGUAGE:
        if value != "all":
            filters_query += f"&lang={quote(value)}"
    
    if filters.sort:
        filters_query += f"&sort={quote(filters.sort)}"
    
    for value in filters.content:
        filters_query += f"&content={quote(value)}"

    index = 1
    for filter_type, filter_values in vars(filters).items():
        if filter_type == 'author' or filter_type == 'title' and filter_values:
            for value in filter_values:
                filters_query += f"&termtype_{index}={filter_type}&termval_{index}={quote(value)}"
                index += 1

    url = (
        f"{AA_BASE_URL}"
        f"/search?index=&page=1&display=table"
        f"&acc=aa_download&acc=external_download&sort="
        f"&ext={'&ext='.join(SUPPORTED_FORMATS)}&q={query_html}"
        f"{filters_query}" 
    )
    
    html = network.html_get_page(url)
    if not html:
        raise Exception("Failed to fetch search results")
        
    if "No files found." in html:
        logger.info(f"No books found for query: {query}")
        raise Exception("No books found. Please try another query.")

    soup = BeautifulSoup(html, 'html.parser')
    tbody: Tag | NavigableString | None = soup.find('table')
    
    if not tbody:
        logger.warning(f"No results table found for query: {query}")
        raise Exception("No books found. Please try another query.")

    books = []
    if  isinstance(tbody, Tag):
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

def _parse_search_result_row(row: Tag) -> Optional[BookInfo]:
    """Parse a single search result row into a BookInfo object."""
    try:
        cells = row.find_all('td')
        preview_img = cells[0].find('img')
        preview = preview_img['src'] if preview_img else None
             
        return BookInfo(
            id=row.find_all('a')[0]['href'].split('/')[-1],
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
    url = f"{AA_BASE_URL}/md5/{book_id}"
    html = network.html_get_page(url)
    
    if not html:
        raise Exception(f"Failed to fetch book info for ID: {book_id}")

    soup = BeautifulSoup(html, 'html.parser')

    return _parse_book_info_page(soup, book_id)

def _parse_book_info_page(soup: BeautifulSoup, book_id: str) -> BookInfo:
    """Parse the book info page HTML into a BookInfo object."""
    data = soup.select_one('body > main > div:nth-of-type(1)')
    
    if not data:
        raise Exception(f"Failed to parse book info for ID: {book_id}")
    
    preview: str = ""

    node = data.select_one(
        'div:nth-of-type(1) > img'
    )
    if node:
        preview_value = node.get('src', "")
        if isinstance(preview_value, list):
            preview = preview_value[0]
        else:
            preview  = preview_value

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

    every_url = soup.find_all('a')
    slow_urls_no_waitlist = set()
    slow_urls_with_waitlist = set()
    external_urls_libgen = set()
    external_urls_z_lib = set()


    for url in every_url:
        try:
            if url.parent.text.strip().lower().startswith("option #"):
                if url.text.strip().lower().startswith("slow partner server"):
                    if url.next is not None and url.next.next is not None and "waitlist" in url.next.next.strip().lower():
                        internal_text = url.next.next.strip().lower()
                        if "no waitlist" in internal_text:
                            slow_urls_no_waitlist.add(url['href'])
                        else:
                            slow_urls_with_waitlist.add(url['href'])
                elif url.next is not None and url.next.next is not None and "click “GET” at the top" in url.next.next.text.strip():
                    external_urls_libgen.add(url['href'])
                elif url.text.strip().lower().startswith("z-lib"):
                    if ".onion/" not in url['href']:
                        external_urls_z_lib.add(url['href'])
        except:
            pass

    if USE_CF_BYPASS:
        urls = list(slow_urls_no_waitlist) + list(external_urls_libgen) + list(slow_urls_with_waitlist) + list(external_urls_z_lib)
    else:
        urls = list(external_urls_libgen) + list(external_urls_z_lib) + list(slow_urls_no_waitlist) + list(slow_urls_with_waitlist)

    for i in range(len(urls)):
        urls[i] = network.get_absolute_url(AA_BASE_URL, urls[i])

    # Extract basic information
    book_info = BookInfo(
        id=book_id,
        preview=preview,
        title=divs[start_div_id].next,
        publisher=divs[start_div_id + 1].next,
        author=divs[start_div_id + 2].next,
        format=format,
        size=size,
        download_urls=urls
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

def _extract_book_metadata(metadata_divs: Union[ResultSet[Tag], List[Tag]]) -> Dict[str, List[str]]:
    """Extract metadata from book info divs."""
    info : Dict[str, List[str]] = {}
    
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
    meta_spans: List[Tag] = []
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

def download_book(book_info: BookInfo, book_path: Path) -> bool:
    """Download a book from available sources.
    
    Args:
        book_id: Book identifier (MD5 hash)
        title: Book title for logging
        
    Returns:
        Optional[BytesIO]: Book content buffer if successful
    """

    if len(book_info.download_urls) == 0:
        book_info = get_book_info(book_info.id)
    download_links = book_info.download_urls

    # If AA_DONATOR_KEY is set, use the fast download URL. Else try other sources.
    if AA_DONATOR_KEY != "":
        download_links.insert(0, 
            f"{AA_BASE_URL}/dyn/api/fast_download.json?md5={book_info.id}&key={AA_DONATOR_KEY}"
        )
    
    for link in download_links:
        try:
            download_url = _get_download_url(link, book_info.title)
            if download_url != "":
                logger.info(f"Downloading `{book_info.title}` from `{download_url}`")
                data = network.download_url(download_url, book_info.size or "")
                if not data:
                    raise Exception("No data received")

                logger.info(f"Download finished. Writing to {book_path}")
                with open(book_path, "wb") as f:
                    f.write(data.getbuffer())
                logger.info(f"Writing `{book_info.title}` successfully")
                return True
            
        except Exception as e:
            logger.error(f"Failed to download from {link}: {e}")
            continue
    
    return False

def _get_download_url(link: str, title: str) -> str:
    """Extract actual download URL from various source pages."""

    url = ""
    
    if link.startswith(f"{AA_BASE_URL}/dyn/api/fast_download.json"):
        page = network.html_get_page(link)
        url = json.loads(page).get("download_url")
    else:
        html = network.html_get_page(link, retry=0)
        
        if html == "":
            return ""
        
        soup = BeautifulSoup(html, 'html.parser')
        
        if link.startswith("https://z-lib.gs"):
            download_link = soup.find_all('a', href=True, class_="addDownloadedBook")
            if download_link:
                url = download_link[0]['href']            
        elif link.startswith(f"{AA_BASE_URL}/slow_download/"):
            download_links = soup.find_all('a', href=True, string="📚 Download now")
            if not download_links:
                countdown = soup.find_all('span', class_="js-partner-countdown")
                if countdown:
                    sleep_time = int(countdown[0].text)
                    logger.info(f"Waiting {sleep_time}s for {title}")
                    time.sleep(sleep_time + 5)
                    url = _get_download_url(link, title)
            else:
                url = download_links[0]['href']
        else:
            url = soup.find_all('a', string="GET")[0]['href']

    return network.get_absolute_url(link, url)
