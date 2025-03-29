"""Network operations manager for the book downloader application."""

import requests
import time
from io import BytesIO
import urllib.request
from typing import Optional, Sequence, Tuple, Any, Union, cast
from urllib.parse import urlparse
from tqdm import tqdm
import socket
import dns.resolver
from socket import AddressFamily, SocketKind
from dns.name import Name

from logger import setup_logger
from config import PROXIES, AA_BASE_URL, CUSTOM_DNS, AA_AVAILABLE_URLS
from env import MAX_RETRY, DEFAULT_SLEEP, USE_CF_BYPASS
if USE_CF_BYPASS:
    import cloudflare_bypasser

logger = setup_logger(__name__)



# Configure DNS resolver to use Custom DNS
if len(CUSTOM_DNS) > 0:
    custom_resolver = dns.resolver.Resolver()
    custom_resolver.nameservers = CUSTOM_DNS

    # Custom resolver function using Custom DNS
    original_getaddrinfo = socket.getaddrinfo

    def _decode_host(host: Union[str, bytes, None]) -> str:
        """Convert host to string, handling bytes and None cases."""
        if host is None:
            return ""
        if isinstance(host, bytes):
            return host.decode('utf-8')
        return str(host)

    def _decode_port(port: Union[str, bytes, int, None]) -> int:
        """Convert port to integer, handling various input types."""
        if port is None:
            return 0
        if isinstance(port, (str, bytes)):
            return int(port)
        return int(port)

    def custom_getaddrinfo(
        host: Union[str, bytes, None],
        port: Union[str, bytes, int, None],
        family: int = 0,
        type: int = 0,
        proto: int = 0,
        flags: int = 0
    ) -> Sequence[Tuple[AddressFamily, SocketKind, int, str, Tuple[Any, ...]]]:
        host_str = _decode_host(host)
        port_int = _decode_port(port)
        
        if host_str == 'localhost' or host_str.startswith('127.') or host_str.startswith('::1') or host_str.startswith('0.0.0.0'):
            return original_getaddrinfo(host, port, family, type, proto, flags)
        
        results: list[Tuple[AddressFamily, SocketKind, int, str, Tuple[Any, ...]]] = []
        
        try:
            try:
                logger.debug(f"Resolving IPv6 address for {host_str} using Custom DNS")
                ipv6_answers = custom_resolver.resolve(host_str, 'AAAA')
                for answer in ipv6_answers:
                    results.append((socket.AF_INET6, cast(SocketKind, type), proto, '', (str(answer), port_int, 0, 0)))
                logger.debug(f"Found {len(results)} IPv6 addresses for {host_str} : {results}")
            except Exception as e:
                logger.debug(f"IPv6 resolution skipped or failed for {host_str}: {e}")

            try:
                logger.debug(f"Resolving IPv4 address for {host_str} using Custom DNS")
                ipv4_answers = custom_resolver.resolve(host_str, 'A')
                for answer in ipv4_answers:
                    results.append((socket.AF_INET, cast(SocketKind, type), proto, '', (str(answer), port_int)))
                logger.debug(f"Found {len(results)} IPv4 addresses for {host_str} : {results}")
            except Exception as e:
                logger.warning(f"IPv4 resolution failed for {host_str}: {e}")

            if results:
                logger.debug(f"Resolved {host_str} to {len(results)} addresses: {results}")
                return results
                
        except Exception as e:
            logger.warning(f"DNS resolution failed for {host_str}: {e}, falling back to system DNS")
        
        # Fall back to system DNS if Custom DNS resolution fails
        try:
            return original_getaddrinfo(host, port, family, type, proto, flags)
        except Exception as e:
            logger.error(f"System DNS resolution also failed for {host_str}: {e}")
            # Last resort: Try to connect to the hostname directly
            if family == 0 or family == socket.AF_INET:
                return [(socket.AF_INET, cast(SocketKind, type), proto, '', (host_str, port_int))]
            else:
                raise  # Re-raise the exception if we can't provide a last resort

    # Replace socket.getaddrinfo with our custom resolver
    socket.getaddrinfo = cast(Any, custom_getaddrinfo)

# Configure urllib opener with appropriate headers
opener = urllib.request.build_opener()
opener.addheaders = [
    ('User-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/129.0.0.0 Safari/537.3')
]
urllib.request.install_opener(opener)

def _refresh_aa_base_url():
    global AA_BASE_URL, AA_AVAILABLE_URLS
    if AA_BASE_URL == "auto":
        logger.info(f"AA_BASE_URL: auto, checking available urls {AA_AVAILABLE_URLS}")
        for url in AA_AVAILABLE_URLS:
            try:
                response = requests.get(url, proxies=PROXIES)
                if response.status_code == 200:
                    AA_BASE_URL = url
                    break
            except Exception as e:
                logger.error_trace(f"Error checking {url}: {e}")
        if AA_BASE_URL == "auto":
            AA_BASE_URL = AA_AVAILABLE_URLS[0]
    logger.info(f"AA_BASE_URL: {AA_BASE_URL}")
_refresh_aa_base_url()

def html_get_page(url: str, retry: int = MAX_RETRY, use_bypasser: bool = False) -> str:
    """Fetch HTML content from a URL with retry mechanism.
    
    Args:
        url: Target URL
        retry: Number of retry attempts
        skip_404: Whether to skip 404 errors
        
    Returns:
        str: HTML content if successful, None otherwise
    """
    response = None
    try:
        logger.debug(f"html_get_page: {url}, retry: {retry}, use_bypasser: {use_bypasser}")
        if use_bypasser and USE_CF_BYPASS:
            logger.info(f"GET Using Cloudflare Bypasser for: {url}")
            response = cloudflare_bypasser.get(url)
            logger.debug(f"Cloudflare Bypasser response: {response}")
            if response:
                return str(response.html)
            else:
                raise requests.exceptions.RequestException("Failed to bypass Cloudflare")
            
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
        
        if response is not None and response.status_code == 404:
            logger.warning(f"404 error for URL: {url}")
            return ""

        if response is not None and response.status_code == 403:
            if use_bypasser:
                logger.warning(f"403 error while using cloudflare bypass for URL: {url}")
                return ""
            logger.warning(f"403 detected for URL: {url}. Should retry using cloudflare bypass.")
            return html_get_page(url, retry - 1, True)
            
        sleep_time = DEFAULT_SLEEP * (MAX_RETRY - retry + 1)
        logger.warning(
            f"Retrying GET {url} in {sleep_time} seconds due to error: {e}"
        )
        time.sleep(sleep_time)
        return html_get_page(url, retry - 1, use_bypasser)

def download_url(link: str, size: str = "") -> Optional[BytesIO]:
    """Download content from URL into a BytesIO buffer.
    
    Args:
        link: URL to download from
        
    Returns:
        BytesIO: Buffer containing downloaded content if successful
    """
    try:
        logger.info(f"Downloading from: {link}")
        response = requests.get(link, stream=True, proxies=PROXIES)
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
            
        pbar.close()
        if buffer.tell() * 0.1 < total_size * 0.9:
            # Check the content of the buffer if its HTML or binary
            if response.headers.get('content-type', '').startswith('text/html'):
                logger.warn(f"Failed to download content for {link}. Found HTML content instead.")
                return None
        return buffer
    except requests.exceptions.RequestException as e:
        logger.error_trace(f"Failed to download from {link}: {e}")
        return None

def get_absolute_url(base_url: str, url: str) -> str:
    """Get absolute URL from relative URL and base URL.
    
    Args:
        base_url: Base URL
        url: Relative URL
    """
    if url.strip() == "":
        return ""
    if url.startswith("http"):
        return url
    parsed_url = urlparse(url)
    parsed_base = urlparse(base_url)
    if parsed_url.netloc == "" or parsed_url.scheme == "":
        parsed_url = parsed_url._replace(netloc=parsed_base.netloc, scheme=parsed_base.scheme)
    return parsed_url.geturl()
