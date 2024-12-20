"""Network operations manager for the book downloader application."""

import requests
import time
from io import BytesIO
import urllib.request
from typing import Optional

from logger import setup_logger
from config import MAX_RETRY, DEFAULT_SLEEP, CLOUDFLARE_PROXY

logger = setup_logger(__name__)

def setup_urllib_opener():
    """Configure urllib opener with appropriate headers."""
    opener = urllib.request.build_opener()
    opener.addheaders = [
        ('User-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
         'AppleWebKit/537.36 (KHTML, like Gecko) '
         'Chrome/129.0.0.0 Safari/537.3')
    ]
    urllib.request.install_opener(opener)

setup_urllib_opener()

def html_get_page(url: str, retry: int = MAX_RETRY, skip_404: bool = False) -> Optional[str]:
    """Fetch HTML content from a URL with retry mechanism.
    
    Args:
        url: Target URL
        retry: Number of retry attempts
        skip_404: Whether to skip 404 errors
        
    Returns:
        str: HTML content if successful, None otherwise
    """
    try:
        logger.info(f"GET: {url}")
        response = requests.get(url)
        
        if skip_404 and response.status_code == 404:
            logger.warning(f"404 error for URL: {url}")
            return None
            
        response.raise_for_status()
        time.sleep(1)
        return response.text
        
    except requests.exceptions.RequestException as e:
        if retry == 0:
            logger.error(f"Failed to fetch page: {url}, error: {e}")
            return None
            
        sleep_time = DEFAULT_SLEEP * (MAX_RETRY - retry + 1)
        logger.warning(
            f"Retrying GET {url} in {sleep_time} seconds due to error: {e}"
        )
        time.sleep(sleep_time)
        return html_get_page(url, retry - 1)

def html_get_page_cf(url: str, retry: int = MAX_RETRY) -> Optional[str]:
    """Fetch HTML content through Cloudflare proxy.
    
    Args:
        url: Target URL
        retry: Number of retry attempts
        
    Returns:
        str: HTML content if successful, None otherwise
    """
    if CLOUDFLARE_PROXY is None:
        return html_get_page(url, retry)
    
    try:
        logger.info(f"GET_CF: {url}")
        response = requests.get(
            f"{CLOUDFLARE_PROXY}/html?url={url}&retries=3"
        )
        time.sleep(1)
        return response.text
        
    except Exception as e:
        if retry == 0:
            logger.error(f"Failed to fetch page through CF: {url}, error: {e}")
            return None
            
        sleep_time = DEFAULT_SLEEP * (MAX_RETRY - retry + 1)
        logger.warning(
            f"Retrying GET_CF {url} in {sleep_time} seconds due to error: {e}"
        )
        time.sleep(sleep_time)
        return html_get_page_cf(url, retry - 1)

def download_url(link: str) -> Optional[BytesIO]:
    """Download content from URL into a BytesIO buffer.
    
    Args:
        link: URL to download from
        
    Returns:
        BytesIO: Buffer containing downloaded content if successful
    """
    try:
        logger.info(f"Downloading from: {link}")
        response = requests.get(link, stream=True)
        response.raise_for_status()
        
        buffer = BytesIO()
        buffer.write(response.content)
        return buffer
        
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to download from {link}: {e}")
        return None