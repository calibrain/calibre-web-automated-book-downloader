"""Network operations manager for the book downloader application."""

import time
from io import BytesIO
from typing import Dict, Optional
from urllib.parse import urlparse

import network
import requests
from tqdm import tqdm

from config import PROXIES
from env import DEFAULT_SLEEP, MAX_RETRY, USE_CF_BYPASS
from logger import setup_logger

if USE_CF_BYPASS:
    import cloudflare_bypasser

network.init()
logger = setup_logger(__name__)


def html_get_page(url: str, retry: int = MAX_RETRY, use_bypasser: bool = False) -> str:
    response = None
    try:
        logger.debug(f"html_get_page: {url}, retry: {retry}, use_bypasser: {use_bypasser}")
        if use_bypasser and USE_CF_BYPASS:
            logger.info(f"GET Using Cloudflare Bypasser for: {url}")
            response_html = cloudflare_bypasser.get(url)
            logger.debug(f"Cloudflare Bypasser response length: {len(response_html)}")
            if response_html.strip() != "":
                return response_html
            else:
                raise requests.exceptions.RequestException("Failed to bypass Cloudflare")
        else:
            logger.info(f"GET: {url}")
            response = requests.get(url, proxies=PROXIES)
            response.raise_for_status()
            logger.debug(f"Success getting: {url}")
            time.sleep(1)
        return str(response.text)
    except Exception as e:
        if retry == 0:
            logger.error_trace(f"Failed to fetch page: {url}, error: {e}")
            return ""

        if use_bypasser and USE_CF_BYPASS:
            logger.warning(f"Exception while using cloudflare bypass for URL: {url}")
            logger.warning(f"Exception: {e}")
            logger.warning(f"Response: {response}")
        elif response is not None and response.status_code == 404:
            logger.warning(f"404 error for URL: {url}")
            return ""
        elif response is not None and response.status_code == 403:
            logger.warning(f"403 detected for URL: {url}. Should retry using cloudflare bypass.")
            return html_get_page(url, retry - 1, True)

        sleep_time = DEFAULT_SLEEP * (MAX_RETRY - retry + 1)
        logger.warning(f"Retrying GET {url} in {sleep_time} seconds due to error: {e}")
        time.sleep(sleep_time)
        return html_get_page(url, retry - 1, use_bypasser)

def download_url(link: str, size: str = "", cookies: Optional[Dict] = None) -> Optional[BytesIO]:
    """Download content from a given URL, using session cookies if provided."""
    try:
        logger.info(f"Starting download from: {link}")
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Referer': 'https://z-lib.fm/'
        }
        # Use the cookies from the Selenium session
        response = requests.get(link, stream=True, proxies=PROXIES, headers=headers, cookies=cookies)
        response.raise_for_status()

        content_type = response.headers.get('content-type', '')
        if 'text/html' in content_type:
            logger.warning(f"Failed to download from {link}. The response was an HTML page, not a file.")
            return None

        total_size = float(response.headers.get('content-length', 0))
        buffer = BytesIO()
        pbar = tqdm(total=total_size, unit='B', unit_scale=True, desc='Downloading')
        for chunk in response.iter_content(chunk_size=8192):
            if chunk:
                buffer.write(chunk)
                pbar.update(len(chunk))
        pbar.close()
        
        return buffer
    except requests.exceptions.RequestException as e:
        logger.error_trace(f"Failed to download file from {link}: {e}")
        return None

def get_absolute_url(base_url: str, url: str) -> str:
    if not url or url.strip() == "":
        return ""
    if url.startswith("http"):
        return url
    parsed_url = urlparse(url)
    parsed_base = urlparse(base_url)
    if not parsed_url.netloc or not parsed_url.scheme:
        parsed_url = parsed_url._replace(netloc=parsed_base.netloc, scheme=parsed_base.scheme)
    return parsed_url.geturl()
