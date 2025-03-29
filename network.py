"""Network operations manager for the book downloader application."""

import requests
import urllib.request
from typing import Sequence, Tuple, Any, Union, cast
import socket
import dns.resolver
from socket import AddressFamily, SocketKind

from logger import setup_logger
from config import PROXIES, AA_BASE_URL, CUSTOM_DNS, AA_AVAILABLE_URLS

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

# Need an empty function to be called by downloader.py
def init():
    pass
