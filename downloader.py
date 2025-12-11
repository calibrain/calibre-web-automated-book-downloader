"""Network operations manager for the book downloader application."""

import network
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
    """
    Exponential backoff with jitter; attempt starts at 1.
    """
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
    """Fetch HTML content from a URL with retry mechanism (iterative, bounded attempts)."""
    remaining = retry
    current_use_bypasser = use_bypasser
    selector = selector or network.AAMirrorSelector()
    original_url = url
    current_url = selector.rewrite(original_url)
    dns_rotations = 0

    attempt = 1
    while remaining > 0:
        try:
            logger.debug(f"html_get_page: {current_url}, retry_left: {remaining}, use_bypasser: {current_use_bypasser}")
            if current_use_bypasser and USE_CF_BYPASS:
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
            remaining -= 1
            attempt += 1

            current_aa_url = network.get_aa_base_url()
            is_connection_error = isinstance(e, (requests.exceptions.ConnectionError,
                                                 requests.exceptions.Timeout,
                                                 requests.exceptions.SSLError))
            is_http_error = isinstance(e, requests.exceptions.HTTPError) and hasattr(e, 'response') and e.response
            status_code = e.response.status_code if is_http_error else None

            if current_url.startswith(current_aa_url):
                # Transport / non-403 errors: try mirror switch, else DNS rotate
                if is_connection_error or (is_http_error and status_code != 403):
                    new_base, action = selector.next_mirror_or_rotate_dns()
                    if action == "mirror" and new_base:
                        current_url = selector.rewrite(original_url)
                        logger.info(f"[aa-retry] action=mirror url={current_url}")
                        continue
                    if action == "dns" and new_base:
                        network._agent_debug_log(
                            "H4",
                            "downloader.py:html_get_page",
                            "aa_switch_exhausted_trigger_dns",
                            {"url": current_url, "retry": remaining, "status_code": status_code, "is_conn_err": is_connection_error}
                        )
                        current_url = selector.rewrite(original_url)
                        logger.warning(f"[aa-retry] action=dns url={current_url}")
                        continue

                # 403 without bypasser: try next mirror
                if status_code == 403 and not USE_CF_BYPASS:
                    new_base, action = selector.next_mirror_or_rotate_dns()
                    if action in ("mirror", "dns") and new_base:
                        current_url = selector.rewrite(original_url)
                        log_fn = logger.warning if action == "dns" else logger.info
                        log_fn(f"[aa-retry] action={action} url={current_url}")
                        continue
                    if USE_CF_BYPASS:
                        current_use_bypasser = True
                        logger.info(f"403 detected; enabling cloudflare bypasser for: {current_url}")
                        continue
            else:
                # Non-AA target: optionally rotate DNS on repeated transport/5xx/429/403 issues
                should_dns_rotate = is_connection_error or (
                    is_http_error and status_code in (403, 429, 500, 502, 503, 504)
                )
                if should_dns_rotate:
                    if network.rotate_dns_provider():
                        dns_rotations += 1
                        current_url = original_url
                        logger.warning(f"[dns-rotate] target={current_url}")
                        continue

            # If we reach here and no special handling took over
            if remaining <= 0:
                # All AA mirrors and DNS options exhausted; log clean summary without traceback noise
                logger.error(f"Giving up fetching {current_url} after {retry} attempts; last error: {type(e).__name__}: {e}")
                return ""

            # Simplified logging to avoid noisy trace spam on repeated failures
            if status_code == 404:
                logger.warning(f"404 error for URL: {current_url}")
                return ""
            if status_code == 403 and not current_use_bypasser and USE_CF_BYPASS:
                logger.warning(f"403 detected for URL: {current_url}. Switching to bypasser.")
                current_use_bypasser = True
                continue

            logger.warning(f"Retrying GET {current_url} (remaining: {remaining}) due to {type(e).__name__}: {e}")
            time.sleep(_backoff_delay(attempt))
            continue

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
    attempt = 1
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
        # Check if this is an AA URL and handle failover
        current_aa_url = network.get_aa_base_url()
        is_connection_error = isinstance(e, (requests.exceptions.ConnectionError,
                                              requests.exceptions.Timeout,
                                              requests.exceptions.SSLError))
        is_http_error = isinstance(e, requests.exceptions.HTTPError) and hasattr(e, 'response') and e.response
        status_code = e.response.status_code if is_http_error else None

        if link_to_use.startswith(current_aa_url):
            should_switch = is_connection_error or (is_http_error and status_code != 403)
            # Rotate on hard blocks or overloads even when not a transport error
            if is_http_error and status_code in (403, 429, 500, 502, 503, 504):
                should_switch = True
            if should_switch:
                new_base, action = selector.next_mirror_or_rotate_dns()
                if action in ("mirror", "dns") and new_base:
                    new_link = selector.rewrite(link)
                    log_fn = logger.warning if action == "dns" else logger.info
                    log_fn(f"[aa-download] action={action} url={new_link}")
                    return download_url(new_link, size, progress_callback, cancel_flag, selector)
        else:
            # Non-AA target: rotate DNS on transport/5xx/429/403 failures
            should_dns_rotate = is_connection_error or (
                is_http_error and status_code in (403, 429, 500, 502, 503, 504)
            )
            if should_dns_rotate and network.rotate_dns_provider():
                new_link = link  # same URL, new DNS
                logger.warning(f"[dns-rotate] target={new_link}")
                return download_url(new_link, size, progress_callback, cancel_flag, selector)
        
        time.sleep(_backoff_delay(attempt))
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
