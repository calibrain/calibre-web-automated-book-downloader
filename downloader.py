"""Network operations manager for the book downloader application."""

import requests
import time
from io import BytesIO
from typing import Optional
from urllib.parse import urlparse
from tqdm import tqdm
from typing import Callable
from threading import Event
from logger import setup_logger
from config import PROXIES
from env import MAX_RETRY, DEFAULT_SLEEP, USE_CF_BYPASS, USING_EXTERNAL_BYPASSER
import network
if USE_CF_BYPASS:
    if USING_EXTERNAL_BYPASSER:
        from cloudflare_bypasser_external import get_bypassed_page
    else:
        from cloudflare_bypasser import get_bypassed_page

logger = setup_logger(__name__)

# Keep AA fetches snappy; connect/read timeout
REQUEST_TIMEOUT = (5, 15)

def _backoff_delay(attempt: int, base: float = 0.25, cap: float = 3.0) -> float:
    """Exponential backoff with jitter; attempt starts at 1."""
    import random
    delay = min(cap, base * (2 ** (attempt - 1)))
    return delay + random.random() * base


def html_get_page(
    url: str,
    retry: int = MAX_RETRY,
    use_bypasser: bool = False,
    status_callback: Optional[Callable[[str], None]] = None,
    selector: Optional[network.AAMirrorSelector] = None,
) -> str:
    """Fetch HTML content from a URL with retry mechanism.
    
    Retry logic:
    - 403 (Cloudflare): Switch to bypasser immediately. If still fails, give up. No retries.
    - 404: Give up immediately.
    - Connection/timeout/5xx: Retry with backoff, try mirror/DNS rotation.
    """
    selector = selector or network.AAMirrorSelector()
    original_url = url
    current_url = selector.rewrite(original_url)
    
    attempt = 0
    max_attempts = retry
    use_bypasser_now = use_bypasser

    while attempt < max_attempts:
        attempt += 1
        
        try:
            logger.debug(f"html_get_page: {current_url}, attempt: {attempt}/{max_attempts}, use_bypasser: {use_bypasser_now}")
            
            if use_bypasser_now and USE_CF_BYPASS:
                if status_callback:
                    status_callback("bypassing")
                logger.info(f"GET Using Cloudflare Bypasser for: {current_url}")
                return get_bypassed_page(current_url, selector)

            logger.info(f"GET: {current_url}")
            response = requests.get(current_url, proxies=PROXIES, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            logger.debug(f"Success getting: {current_url}")
            time.sleep(1)
            return str(response.text)

        except Exception as e:
            is_connection_error = isinstance(e, (requests.exceptions.ConnectionError,
                                                 requests.exceptions.Timeout,
                                                 requests.exceptions.SSLError))
            is_http_error = isinstance(e, requests.exceptions.HTTPError) and hasattr(e, 'response') and e.response
            status_code = e.response.status_code if is_http_error else None

            # 403 = Cloudflare wall. Use bypasser, don't retry.
            if status_code == 403:
                if USE_CF_BYPASS and not use_bypasser_now:
                    logger.info(f"403 detected; switching to bypasser for: {current_url}")
                    use_bypasser_now = True
                    attempt -= 1  # Don't count this as a retry attempt
                    continue
                # Already using bypasser or bypasser not available - give up
                logger.warning(f"403 error for: {current_url}; giving up")
                return ""

            # 404 = Not found, give up immediately
            if status_code == 404:
                logger.warning(f"404 error for: {current_url}")
                return ""

            # Connection/transport errors or 5xx: try mirror/DNS rotation for AA URLs
            current_aa_url = network.get_aa_base_url()
            if current_url.startswith(current_aa_url):
                if is_connection_error or (is_http_error and status_code in (429, 500, 502, 503, 504)):
                    new_base, action = selector.next_mirror_or_rotate_dns()
                    if action in ("mirror", "dns") and new_base:
                        current_url = selector.rewrite(original_url)
                        log_fn = logger.warning if action == "dns" else logger.info
                        log_fn(f"[aa-retry] action={action} url={current_url}")
                        continue
            else:
                # Non-AA target: rotate DNS on transport/5xx/429 failures
                if is_connection_error or (is_http_error and status_code in (429, 500, 502, 503, 504)):
                    if network.rotate_dns_provider():
                        current_url = original_url
                        logger.warning(f"[dns-rotate] target={current_url}")
                        continue

            # Transient error - retry with backoff if attempts remain
            if attempt < max_attempts:
                logger.warning(f"Retrying GET {current_url} (attempt {attempt}/{max_attempts}) due to {type(e).__name__}: {e}")
                time.sleep(_backoff_delay(attempt))
            else:
                logger.error(f"Giving up fetching {current_url} after {max_attempts} attempts; last error: {type(e).__name__}: {e}")

    return ""


def download_url(link: str, size: str = "", progress_callback: Optional[Callable[[float], None]] = None, cancel_flag: Optional[Event] = None, _selector: Optional[network.AAMirrorSelector] = None) -> Optional[BytesIO]:
    """Download content from URL into a BytesIO buffer.
    
    Args:
        link: URL to download from
        
    Returns:
        BytesIO: Buffer containing downloaded content if successful
    """
    selector = _selector or network.AAMirrorSelector()
    link_to_use = selector.rewrite(link)
    try:
        logger.info(f"Downloading from: {link_to_use}")
        response = requests.get(link_to_use, stream=True, proxies=PROXIES, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()

        total_size : float = 0.0
        try:
            # we assume size is in MB
            total_size = float(size.strip().replace(" ", "").replace(",", ".").upper()[:-2].strip()) * 1024 * 1024
        except:
            total_size = float(response.headers.get('content-length', 0))
        
        buffer = BytesIO()

        # Initialize the progress bar with your guess
        pbar = tqdm(total=total_size, unit='B', unit_scale=True, desc='Downloading')
        for chunk in response.iter_content(chunk_size=1000):
            buffer.write(chunk)
            pbar.update(len(chunk))
            if progress_callback is not None:
                progress_callback(pbar.n * 100.0 / total_size)
            if cancel_flag is not None and cancel_flag.is_set():
                logger.info(f"Download cancelled: {link}")
                return None
            
        pbar.close()
        if buffer.tell() * 0.1 < total_size * 0.9:
            # Check the content of the buffer if its HTML or binary
            if response.headers.get('content-type', '').startswith('text/html'):
                logger.warn(f"Failed to download content for {link}. Found HTML content instead.")
                return None
        return buffer
    except requests.exceptions.RequestException as e:
        current_aa_url = network.get_aa_base_url()
        is_connection_error = isinstance(e, (requests.exceptions.ConnectionError,
                                              requests.exceptions.Timeout,
                                              requests.exceptions.SSLError))
        is_http_error = isinstance(e, requests.exceptions.HTTPError) and hasattr(e, 'response') and e.response
        status_code = e.response.status_code if is_http_error else None

        # For actual file downloads, 403 means the URL is bad or blocked - just fail
        # The bypasser is for HTML pages, not binary downloads
        if status_code == 403:
            logger.warning(f"403 error downloading from: {link_to_use}")
            return None

        # Connection/transport or 5xx: try mirror/DNS rotation
        if link_to_use.startswith(current_aa_url):
            if is_connection_error or (is_http_error and status_code in (429, 500, 502, 503, 504)):
                new_base, action = selector.next_mirror_or_rotate_dns()
                if action in ("mirror", "dns") and new_base:
                    new_link = selector.rewrite(link)
                    log_fn = logger.warning if action == "dns" else logger.info
                    log_fn(f"[aa-download] action={action} url={new_link}")
                    return download_url(new_link, size, progress_callback, cancel_flag, selector)
        else:
            # Non-AA target: rotate DNS on transport/5xx/429 failures
            if is_connection_error or (is_http_error and status_code in (429, 500, 502, 503, 504)):
                if network.rotate_dns_provider():
                    logger.warning(f"[dns-rotate] target={link}")
                    return download_url(link, size, progress_callback, cancel_flag, selector)
        
        logger.error_trace(f"Failed to download from {link_to_use}: {e}")
        return None


def get_absolute_url(base_url: str, url: str) -> str:
    """Get absolute URL from relative URL and base URL.
    
    Args:
        base_url: Base URL
        url: Relative URL
    """
    if url.strip() == "":
        return ""
    if url.strip("#") == "":
        return ""
    if url.startswith("http"):
        return url
    parsed_url = urlparse(url)
    parsed_base = urlparse(base_url)
    if parsed_url.netloc == "" or parsed_url.scheme == "":
        parsed_url = parsed_url._replace(netloc=parsed_base.netloc, scheme=parsed_base.scheme)
    return parsed_url.geturl()
