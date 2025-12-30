"""HTTP download with retry, resume, and Cloudflare bypass support."""

import random
import time
from io import BytesIO
from threading import Event
from typing import Callable, Optional
from urllib.parse import urlparse

import requests
from tqdm import tqdm

from cwa_book_downloader.download import network
from cwa_book_downloader.core.config import config as app_config
from cwa_book_downloader.core.logger import setup_logger

# Bypasser modules are imported lazily to support dynamic selection based on config
_internal_bypasser = None
_external_bypasser = None


def _get_internal_bypasser():
    """Lazy import of internal bypasser module."""
    global _internal_bypasser
    if _internal_bypasser is None:
        try:
            from cwa_book_downloader.bypass import internal_bypasser
            _internal_bypasser = internal_bypasser
        except ImportError as e:
            raise RuntimeError(
                f"Failed to import internal bypasser: {e}. "
                "Check that all dependencies are installed. "
                "You may need to disable CF bypass or use the external bypasser."
            ) from e
    return _internal_bypasser


def _get_external_bypasser():
    """Lazy import of external bypasser module."""
    global _external_bypasser
    if _external_bypasser is None:
        try:
            from cwa_book_downloader.bypass import external_bypasser
            _external_bypasser = external_bypasser
        except ImportError as e:
            raise RuntimeError(
                f"Failed to import external bypasser: {e}. "
                "Check that the external bypasser is properly configured."
            ) from e
    return _external_bypasser


def _is_using_external_bypasser() -> bool:
    """Check if external bypasser is configured (reads from config, not just env)."""
    return app_config.get("USING_EXTERNAL_BYPASSER", False)


def _is_cf_bypass_enabled() -> bool:
    """Check if Cloudflare bypass is enabled."""
    return app_config.get("USE_CF_BYPASS", True)


def get_bypassed_page(url, selector=None, cancel_flag=None):
    """Wrapper that delegates to the appropriate bypasser based on config."""
    if _is_using_external_bypasser():
        return _get_external_bypasser().get_bypassed_page(url, selector, cancel_flag)
    else:
        return _get_internal_bypasser().get_bypassed_page(url, selector, cancel_flag)


def get_cf_cookies_for_domain(domain):
    """Get CF cookies - only available with internal bypasser."""
    if _is_using_external_bypasser():
        logger.debug(f"External bypasser in use, CF cookies not available for {domain}")
        return {}
    return _get_internal_bypasser().get_cf_cookies_for_domain(domain)


def get_cf_user_agent_for_domain(domain):
    """Get CF user agent - only available with internal bypasser."""
    if _is_using_external_bypasser():
        logger.debug(f"External bypasser in use, CF user agent not available for {domain}")
        return None
    return _get_internal_bypasser().get_cf_user_agent_for_domain(domain)

logger = setup_logger(__name__)

# Network settings
REQUEST_TIMEOUT = (5, 10)  # (connect, read)
MAX_DOWNLOAD_RETRIES = 2
MAX_RESUME_ATTEMPTS = 3


def _get_proxies() -> dict:
    """Get current proxy configuration from config singleton."""
    proxy_mode = app_config.get("PROXY_MODE", "none")

    if proxy_mode == "socks5":
        socks_proxy = app_config.get("SOCKS5_PROXY", "")
        if socks_proxy:
            return {"http": socks_proxy, "https": socks_proxy}
    elif proxy_mode == "http":
        proxies = {}
        http_proxy = app_config.get("HTTP_PROXY", "")
        https_proxy = app_config.get("HTTPS_PROXY", "")
        if http_proxy:
            proxies["http"] = http_proxy
        if https_proxy:
            proxies["https"] = https_proxy
        elif http_proxy:
            # Fallback: use HTTP proxy for HTTPS if HTTPS proxy not specified
            proxies["https"] = http_proxy
        return proxies

    return {}


RETRYABLE_CODES = (429, 500, 502, 503, 504)
CONNECTION_ERRORS = (requests.exceptions.ConnectionError, requests.exceptions.Timeout,
                     requests.exceptions.SSLError, requests.exceptions.ChunkedEncodingError)
DOWNLOAD_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
}


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
    retry: Optional[int] = None,
    use_bypasser: bool = False,
    selector: Optional[network.AAMirrorSelector] = None,
    cancel_flag: Optional[Event] = None,
) -> str:
    """Fetch HTML content from a URL with retry mechanism."""
    retry = retry if retry is not None else app_config.MAX_RETRY
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
            if use_bypasser_now and _is_cf_bypass_enabled():
                logger.debug(f"GET (bypasser): {current_url}")
                try:
                    result = get_bypassed_page(current_url, selector, cancel_flag)
                    return result or ""
                except Exception as e:
                    logger.warning(f"Bypasser error: {type(e).__name__}: {e}")
                    return ""

            logger.debug(f"GET: {current_url}")
            # Try with CF cookies/UA if available (from previous bypass)
            cookies = {}
            headers = {}
            if _is_cf_bypass_enabled():
                parsed = urlparse(current_url)
                hostname = parsed.hostname or ""
                cookies = get_cf_cookies_for_domain(hostname)
                stored_ua = get_cf_user_agent_for_domain(hostname)
                if stored_ua:
                    headers['User-Agent'] = stored_ua
            response = requests.get(current_url, proxies=_get_proxies(), timeout=REQUEST_TIMEOUT, cookies=cookies, headers=headers)
            response.raise_for_status()
            time.sleep(1)
            return response.text

        except Exception as e:
            status = _get_status_code(e)

            # 403 = Cloudflare/DDoS-Guard protection
            if status == 403:
                if _is_cf_bypass_enabled() and not use_bypasser_now:
                    # Before switching to bypasser, check if cookies have become available
                    # (another concurrent download may have completed bypass and extracted cookies)
                    parsed = urlparse(current_url)
                    fresh_cookies = get_cf_cookies_for_domain(parsed.hostname or "")
                    if fresh_cookies and not cookies:
                        # Cookies are now available - retry with cookies before using bypasser
                        logger.debug(f"403 but cookies now available - retrying with cookies: {current_url}")
                        continue
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
    referer: Optional[str] = None,
) -> Optional[BytesIO]:
    """Download content from URL with automatic retry and resume support."""
    selector = _selector or network.AAMirrorSelector()
    current_url = selector.rewrite(link)

    # Build headers with optional referer
    headers = DOWNLOAD_HEADERS.copy()
    if referer:
        headers['Referer'] = referer
    total_size = parse_size_string(size) or 0

    attempt = 0
    zlib_cookie_refresh_attempted = False

    while attempt < MAX_DOWNLOAD_RETRIES:
        if cancel_flag and cancel_flag.is_set():
            return None

        buffer = BytesIO()
        bytes_downloaded = 0

        try:
            if attempt > 0 and status_callback:
                status_callback("resolving", f"Connecting (Attempt {attempt + 1}/{MAX_DOWNLOAD_RETRIES})")

            logger.info(f"Downloading: {current_url} (attempt {attempt + 1}/{MAX_DOWNLOAD_RETRIES})")
            # Try with CF cookies/UA if available
            cookies = {}
            if _is_cf_bypass_enabled():
                parsed = urlparse(current_url)
                hostname = parsed.hostname or ""
                cookies = get_cf_cookies_for_domain(hostname)
                # Use stored UA - Cloudflare ties cf_clearance to the UA that solved the challenge
                stored_ua = get_cf_user_agent_for_domain(hostname)
                if stored_ua:
                    headers['User-Agent'] = stored_ua
                    logger.debug(f"Using stored UA for {hostname}")
                else:
                    logger.debug(f"No stored UA available for {hostname}")
                if cookies:
                    logger.debug(f"Using {len(cookies)} cookies for {hostname}: {list(cookies.keys())}")
            response = requests.get(current_url, stream=True, proxies=_get_proxies(), timeout=REQUEST_TIMEOUT, cookies=cookies, headers=headers)
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

            # Z-Library 403 - try refreshing cookies via bypasser once before giving up
            if status == 403 and _is_cf_bypass_enabled() and not zlib_cookie_refresh_attempted:
                parsed = urlparse(current_url)
                if parsed.hostname and 'z-lib' in parsed.hostname and referer:
                    zlib_cookie_refresh_attempted = True
                    logger.info(f"Z-Library 403 - refreshing cookies via referer: {referer}")
                    try:
                        get_bypassed_page(referer, selector, cancel_flag)
                        time.sleep(0.5)
                        # Retry with fresh cookies (don't increment attempt)
                        continue
                    except Exception as cookie_err:
                        logger.warning(f"Z-Library cookie refresh failed: {cookie_err}")

            # Non-retryable errors
            if status in (403, 404):
                logger.warning(f"Download failed ({status}): {current_url}")
                return None

            # Rate limited - skip to next source immediately
            # (waiting doesn't help with concurrent downloads hitting the same server)
            if status == 429:
                logger.info(f"Rate limited (429) - trying next source")
                if status_callback:
                    status_callback("resolving", "Server busy, trying next")
                return None

            # Timeout - don't retry, server likely overloaded
            if isinstance(e, requests.exceptions.Timeout):
                logger.warning(f"Timeout: {current_url} - skipping to next source")
                if status_callback:
                    status_callback("resolving", "Server timed out, trying next")
                return None

            # Try to resume if we got some data
            if bytes_downloaded > 0 and retryable:
                resumed = _try_resume(current_url, buffer, bytes_downloaded, total_size, progress_callback, cancel_flag, headers)
                if resumed:
                    return resumed

            # Try mirror/DNS rotation if nothing downloaded yet
            if bytes_downloaded == 0 and retryable:
                new_url = _try_rotation(link, current_url, selector)
                if new_url:
                    current_url = new_url
                    attempt += 1
                    continue

            logger.warning(f"Download error: {type(e).__name__}: {e}")
            if attempt < MAX_DOWNLOAD_RETRIES - 1:
                time.sleep(_backoff_delay(attempt + 1))
            attempt += 1

    logger.error(f"Download failed after {MAX_DOWNLOAD_RETRIES} attempts: {link}")
    return None


def _try_resume(
    url: str,
    buffer: BytesIO,
    start_byte: int,
    total_size: float,
    progress_callback: Optional[Callable[[float], None]],
    cancel_flag: Optional[Event],
    base_headers: Optional[dict] = None,
) -> Optional[BytesIO]:
    """Try to resume an interrupted download."""
    for attempt in range(MAX_RESUME_ATTEMPTS):
        logger.info(f"Resuming from {start_byte} bytes (attempt {attempt + 1}/{MAX_RESUME_ATTEMPTS})")
        time.sleep(_backoff_delay(attempt + 1, base=0.5, cap=5.0))

        try:
            # Try with CF cookies/UA if available
            cookies = {}
            resume_headers = {**(base_headers or DOWNLOAD_HEADERS), 'Range': f'bytes={start_byte}-'}
            if _is_cf_bypass_enabled():
                parsed = urlparse(url)
                hostname = parsed.hostname or ""
                cookies = get_cf_cookies_for_domain(hostname)
                stored_ua = get_cf_user_agent_for_domain(hostname)
                if stored_ua:
                    resume_headers['User-Agent'] = stored_ua
            response = requests.get(
                url, stream=True, proxies=_get_proxies(), timeout=REQUEST_TIMEOUT,
                headers=resume_headers, cookies=cookies
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
