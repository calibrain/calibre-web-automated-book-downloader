"""Network operations manager for the book downloader application."""

import random
import time
from io import BytesIO
from threading import Event
from typing import Callable, Optional
from urllib.parse import urlparse

import requests
from tqdm import tqdm

import network
from config import PROXIES
from env import DEFAULT_SLEEP, MAX_RETRY, USE_CF_BYPASS, USING_EXTERNAL_BYPASSER
from logger import setup_logger

# Import bypasser if enabled
if USE_CF_BYPASS:
    if USING_EXTERNAL_BYPASSER:
        from cloudflare_bypasser_external import get_bypassed_page
    else:
        from cloudflare_bypasser import get_bypassed_page

logger = setup_logger(__name__)

# Network settings
REQUEST_TIMEOUT = (5, 10)  # (connect, read)
MAX_DOWNLOAD_RETRIES = 2
MAX_RESUME_ATTEMPTS = 3
RETRYABLE_CODES = (429, 500, 502, 503, 504)
CONNECTION_ERRORS = (requests.exceptions.ConnectionError, requests.exceptions.Timeout,
                     requests.exceptions.SSLError, requests.exceptions.ChunkedEncodingError)


def parse_size_string(size: str) -> Optional[float]:
    """Parse a human-readable size string (e.g., '10.5 MB') into bytes."""
    if not size:
        return None
    try:
        normalized = size.strip().replace(" ", "").replace(",", ".").upper()
        multipliers = {"GB": 1024**3, "MB": 1024**2, "KB": 1024}
        for suffix, mult in multipliers.items():
            if normalized.endswith(suffix):
                return float(normalized[:-2]) * mult
        return float(normalized)
    except (ValueError, IndexError):
        return None

def _backoff_delay(attempt: int, base: float = 0.25, cap: float = 3.0) -> float:
    """Exponential backoff with jitter."""
    return min(cap, base * (2 ** (attempt - 1))) + random.random() * base


def _get_status_code(e: Exception) -> Optional[int]:
    """Extract HTTP status code from an exception, or None if not applicable."""
    if isinstance(e, requests.exceptions.HTTPError) and e.response is not None:
        return e.response.status_code
    return None

def _is_retryable_error(e: Exception) -> bool:
    """Check if error is retryable (connection error or retryable HTTP status)."""
    if isinstance(e, CONNECTION_ERRORS):
        return True
    status = _get_status_code(e)
    return status in RETRYABLE_CODES if status else False


def _try_rotation(original_url: str, current_url: str, selector: network.AAMirrorSelector) -> Optional[str]:
    """Try mirror/DNS rotation. Returns new URL or None."""
    if current_url.startswith(network.get_aa_base_url()):
        new_base, action = selector.next_mirror_or_rotate_dns()
        if action in ("mirror", "dns") and new_base:
            new_url = selector.rewrite(original_url)
            logger.info(f"[{action}] switching to: {new_url}")
            return new_url
    elif network.should_rotate_dns_for_url(current_url) and network.rotate_dns_provider():
        logger.info(f"[dns-rotate] retrying: {original_url}")
        return original_url
    return None


def html_get_page(
    url: str,
    retry: int = MAX_RETRY,
    use_bypasser: bool = False,
    selector: Optional[network.AAMirrorSelector] = None,
    cancel_flag: Optional[Event] = None,
) -> str:
    """Fetch HTML content from a URL with retry mechanism."""
    selector = selector or network.AAMirrorSelector()
    original_url = url
    current_url = selector.rewrite(original_url)
    use_bypasser_now = use_bypasser

    for attempt in range(1, retry + 1):
        # Check for cancellation before each attempt
        if cancel_flag and cancel_flag.is_set():
            logger.info(f"html_get_page cancelled before attempt {attempt}")
            return ""

        try:
            logger.debug(f"html_get_page: {current_url}, attempt: {attempt}/{retry}, bypasser: {use_bypasser_now}")

            if use_bypasser_now and USE_CF_BYPASS:
                logger.info(f"GET (bypasser): {current_url}")
                try:
                    result = get_bypassed_page(current_url, selector, cancel_flag)
                    return result or ""
                except Exception as e:
                    logger.warning(f"Bypasser error: {type(e).__name__}: {e}")
                    return ""

            logger.info(f"GET: {current_url}")
            response = requests.get(current_url, proxies=PROXIES, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            time.sleep(1)
            return response.text

        except Exception as e:
            status = _get_status_code(e)

            # 403 = Cloudflare - switch to bypasser once
            if status == 403:
                if USE_CF_BYPASS and not use_bypasser_now:
                    logger.info(f"403 detected; switching to bypasser: {current_url}")
                    use_bypasser_now = True
                    continue
                logger.warning(f"403 error, giving up: {current_url}")
                return ""

            # 404 = Not found
            if status == 404:
                logger.warning(f"404 error: {current_url}")
                return ""

            # Try mirror/DNS rotation on retryable errors
            if _is_retryable_error(e):
                new_url = _try_rotation(original_url, current_url, selector)
                if new_url:
                    current_url = new_url
                    continue

            # Retry with backoff
            if attempt < retry:
                logger.warning(f"Retry {attempt}/{retry} for {current_url}: {type(e).__name__}: {e}")
                time.sleep(_backoff_delay(attempt))
            else:
                logger.error(f"Giving up after {retry} attempts: {current_url}")

    return ""


def download_url(
    link: str,
    size: str = "",
    progress_callback: Optional[Callable[[float], None]] = None,
    cancel_flag: Optional[Event] = None,
    _selector: Optional[network.AAMirrorSelector] = None,
    status_callback: Optional[Callable[[str, Optional[str]], None]] = None,
) -> Optional[BytesIO]:
    """Download content from URL with automatic retry and resume support."""
    selector = _selector or network.AAMirrorSelector()
    current_url = selector.rewrite(link)
    total_size = parse_size_string(size) or 0
    
    for attempt in range(MAX_DOWNLOAD_RETRIES):
        if cancel_flag and cancel_flag.is_set():
            return None
        
        buffer = BytesIO()
        bytes_downloaded = 0
        
        try:
            if attempt > 0 and status_callback:
                status_callback("resolving", f"Connecting (Attempt {attempt + 1}/{MAX_DOWNLOAD_RETRIES})")
            
            logger.info(f"Downloading: {current_url} (attempt {attempt + 1}/{MAX_DOWNLOAD_RETRIES})")
            response = requests.get(current_url, stream=True, proxies=PROXIES, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            
            if status_callback:
                status_callback("downloading", "")
            
            total_size = total_size or float(response.headers.get('content-length', 0))
            pbar = tqdm(total=total_size, unit='B', unit_scale=True, desc='Downloading')
            
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    buffer.write(chunk)
                    bytes_downloaded += len(chunk)
                    pbar.update(len(chunk))
                    if progress_callback and total_size > 0:
                        progress_callback(bytes_downloaded * 100.0 / total_size)
                    if cancel_flag and cancel_flag.is_set():
                        pbar.close()
                        return None
            pbar.close()
            
            # Validate - check we didn't get HTML instead of file
            if total_size > 0 and bytes_downloaded < total_size * 0.9:
                if response.headers.get('content-type', '').startswith('text/html'):
                    logger.warning(f"Received HTML instead of file: {current_url}")
                    return None
            
            logger.debug(f"Download completed: {bytes_downloaded} bytes")
            return buffer
            
        except requests.exceptions.RequestException as e:
            status = _get_status_code(e)
            retryable = _is_retryable_error(e)
            
            # Non-retryable errors
            if status in (403, 404):
                logger.warning(f"Download failed ({status}): {current_url}")
                return None
            
            # Timeout - don't retry, server likely overloaded
            if isinstance(e, requests.exceptions.Timeout):
                logger.warning(f"Timeout: {current_url} - skipping to next source")
                return None
            
            # Try to resume if we got some data
            if bytes_downloaded > 0 and retryable:
                resumed = _try_resume(current_url, buffer, bytes_downloaded, total_size, progress_callback, cancel_flag)
                if resumed:
                    return resumed
            
            # Try mirror/DNS rotation if nothing downloaded yet
            if bytes_downloaded == 0 and retryable:
                new_url = _try_rotation(link, current_url, selector)
                if new_url:
                    current_url = new_url
                    continue
            
            logger.warning(f"Download error: {type(e).__name__}: {e}")
            if attempt < MAX_DOWNLOAD_RETRIES - 1:
                time.sleep(_backoff_delay(attempt + 1))
    
    logger.error(f"Download failed after {MAX_DOWNLOAD_RETRIES} attempts: {link}")
    return None


def _try_resume(
    url: str,
    buffer: BytesIO,
    start_byte: int,
    total_size: float,
    progress_callback: Optional[Callable[[float], None]],
    cancel_flag: Optional[Event],
) -> Optional[BytesIO]:
    """Try to resume an interrupted download."""
    for attempt in range(MAX_RESUME_ATTEMPTS):
        logger.info(f"Resuming from {start_byte} bytes (attempt {attempt + 1}/{MAX_RESUME_ATTEMPTS})")
        time.sleep(_backoff_delay(attempt + 1, base=0.5, cap=5.0))
        
        try:
            response = requests.get(
                url, stream=True, proxies=PROXIES, timeout=REQUEST_TIMEOUT,
                headers={'Range': f'bytes={start_byte}-'}
            )
            
            # Check resume support
            if response.status_code == 200:  # Server doesn't support resume
                logger.info("Server doesn't support resume")
                return None
            if response.status_code == 416:  # Range not satisfiable
                logger.warning("Range not satisfiable")
                return None
            if response.status_code != 206:
                response.raise_for_status()
            
            pbar = tqdm(total=total_size, initial=start_byte, unit='B', unit_scale=True, desc='Resuming')
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    buffer.write(chunk)
                    start_byte += len(chunk)
                    pbar.update(len(chunk))
                    if progress_callback and total_size > 0:
                        progress_callback(start_byte * 100.0 / total_size)
                    if cancel_flag and cancel_flag.is_set():
                        pbar.close()
                        return None
            pbar.close()
            
            logger.info(f"Resume completed: {start_byte} bytes")
            return buffer
            
        except requests.exceptions.RequestException as e:
            logger.debug(f"Resume attempt {attempt + 1} failed: {e}")
    
    logger.warning(f"Resume failed after {MAX_RESUME_ATTEMPTS} attempts")
    return None


def get_absolute_url(base_url: str, url: str) -> str:
    """Convert a relative URL to absolute using the base URL."""
    url = url.strip()
    if not url or url == "#" or url.startswith("http"):
        return url if url.startswith("http") else ""
    parsed = urlparse(url)
    base = urlparse(base_url)
    if not parsed.netloc or not parsed.scheme:
        parsed = parsed._replace(netloc=base.netloc, scheme=base.scheme)
    return parsed.geturl()
