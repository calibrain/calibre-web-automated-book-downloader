"""Book download manager handling search and retrieval operations."""

import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union
from urllib.parse import quote

from bs4 import BeautifulSoup, NavigableString, ResultSet, Tag
from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

import downloader
from config import AA_BASE_URL, BOOK_LANGUAGE, SUPPORTED_FORMATS
from env import AA_DONATOR_KEY, USE_CF_BYPASS, ZLIBRARY_PASSWORD, ZLIBRARY_USERNAME
from logger import setup_logger
from models import BookInfo, SearchFilters

logger = setup_logger(__name__)


def get_zlibrary_session_data(page_url: str, username: str, password: str) -> Optional[Tuple[str, Dict]]:
    """
    Uses Selenium to log into Z-Library, get the download URL, and extract the session cookies.
    Returns:
        A tuple containing (download_url, session_cookies) or None on failure.
    """
    options = Options()
    user_data_dir = tempfile.mkdtemp()
    
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument(f"--user-data-dir={user_data_dir}")
    
    driver = None
    try:
        driver = webdriver.Chrome(options=options)
        
        logger.info("Navigating to Z-Library homepage to log in.")
        driver.get("https://z-lib.fm/login")

        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.NAME, "email"))).send_keys(username)
        driver.find_element(By.NAME, "password").send_keys(password)
        driver.find_element(By.CSS_SELECTOR, "button[type='submit']").click()
        logger.info("Login credentials submitted.")

        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, 'section.navigation-user-card-element.logged'))
        )
        logger.info("Login successful.")

        logger.info(f"Navigating to book page: {page_url}")
        driver.get(page_url)

        download_button = WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "a.addDownloadedBook"))
        )
        logger.info("Download button found on book page.")
        
        download_href = download_button.get_attribute('href')
        absolute_download_url = downloader.get_absolute_url("https://z-lib.fm", download_href)

        # **COOKIE EXTRACTION**
        selenium_cookies = driver.get_cookies()
        request_cookies = {cookie['name']: cookie['value'] for cookie in selenium_cookies}
        logger.info("Session cookies extracted successfully.")

        return absolute_download_url, request_cookies

    except Exception as e:
        logger.error_trace(f"An unexpected error occurred in Selenium for {page_url}: {e}")
        if driver:
            driver.save_screenshot('zlibrary_error.png')
            logger.error("A screenshot 'zlibrary_error.png' has been saved for debugging.")
        return None
    finally:
        if driver:
            driver.quit()
        if os.path.exists(user_data_dir):
            shutil.rmtree(user_data_dir)

def search_books(query: str, filters: SearchFilters) -> List[BookInfo]:
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

    if filters.content:
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
        f"&acc=aa_download&acc=external_download"
        f"&ext={'&ext='.join(SUPPORTED_FORMATS)}&q={query_html}"
        f"{filters_query}"
    )

    html = downloader.html_get_page(url)
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
    if isinstance(tbody, Tag):
        for line_tr in tbody.find_all('tr'):
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
        logger.error_trace(f"Error parsing search result row: {e}")
        return None

def get_book_info(book_id: str) -> BookInfo:
    """Get detailed information for a specific book.
    
    Args:
        book_id: Book identifier (MD5 hash)
        
    Returns:
        BookInfo: Detailed book information
    """
    url = f"{AA_BASE_URL}/md5/{book_id}"
    html = downloader.html_get_page(url)

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
    
    node = data.select_one('div:nth-of-type(1) > img')
    if node:
        preview_value = node.get('src', "")
        if isinstance(preview_value, list):
            preview = preview_value[0]
        else:
            preview = preview_value

    # Find the start of book information
    divs = data.find_all('div')
    start_div_id = next((i for i, div in enumerate(divs) if "🔍" in div.text), 3)
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
        urls[i] = downloader.get_absolute_url(AA_BASE_URL, urls[i])

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

    # Process the first set of metadata
    info: Dict[str, List[str]] = {}
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
    relevant_prefixes = ["ISBN-", "ALTERNATIVE", "ASIN", "Goodreads", "Language", "Year"]
    return {
        k.strip(): v for k, v in info.items()
        if any(k.lower().startswith(prefix.lower()) for prefix in relevant_prefixes)
        and "filename" not in k.lower()
    }

def _get_download_url(link: str, title: str) -> str:
    """Extract actual download URL from various source pages."""

    url = ""

    if link.startswith(f"{AA_BASE_URL}/dyn/api/fast_download.json"):
        page = downloader.html_get_page(link)
        url = json.loads(page).get("download_url")
    else:
        html = downloader.html_get_page(link)

        if not html:
            return ""
        
        soup = BeautifulSoup(html, 'html.parser')
        
        if link.startswith(f"{AA_BASE_URL}/slow_download/"):
            download_links = soup.find_all('a', href=True, string="📚 Download now")
            if not download_links:
                countdown = soup.find_all('span', class_="js-partner-countdown")
                if countdown:
                    sleep_time = int(countdown[0].text)
                    logger.info(f"Waiting {sleep_time}s for {title}")
                    time.sleep(sleep_time)
                    url = _get_download_url(link, title)
            else:
                url = download_links[0]['href']
        else:
            get_links = soup.find_all('a', string="GET")
            if get_links:
                url = get_links[0]['href']

    return downloader.get_absolute_url(link, url)


def download_book(book_info: BookInfo, book_path: Path) -> bool:
    """
    Download a book from available sources.
    """
    if not book_info.download_urls:
        book_info = get_book_info(book_info.id)
    download_links = book_info.download_urls

    if AA_DONATOR_KEY:
        download_links.insert(0, f"{AA_BASE_URL}/dyn/api/fast_download.json?md5={book_info.id}&key={AA_DONATOR_KEY}")
    
    for link in download_links:
        try:
            data = None
            if "z-lib.fm" in link:
                if ZLIBRARY_USERNAME is None or ZLIBRARY_PASSWORD is None:
                    raise Exception("Found Z-Library link, but Z-Library is not configured.")
                logger.info(f"Z-Library link found. Using Selenium to get direct URL for: {link}")
                session_data = get_zlibrary_session_data(link, ZLIBRARY_USERNAME, ZLIBRARY_PASSWORD)
                
                if session_data:
                    download_url, cookies = session_data
                    logger.info(f"Downloading `{book_info.title}` from `{download_url}`")
                    data = downloader.download_url(download_url, book_info.size or "", cookies=cookies)
            else:
                download_url = _get_download_url(link, book_info.title)
                if download_url:
                    logger.info(f"Downloading `{book_info.title}` from `{download_url}`")
                    data = downloader.download_url(download_url, book_info.size or "")

            if data:
                logger.info(f"Download finished. Writing to {book_path}")
                with open(book_path, "wb") as f:
                    f.write(data.getbuffer())
                logger.info(f"Writing `{book_info.title}` successfully")
                return True
            else:
                logger.warning(f"Could not get a downloadable URL or data from the link: {link}")
        
        except Exception as e:
            logger.error_trace(f"Failed to download from {link}: {e}")
            continue
    
    return False
