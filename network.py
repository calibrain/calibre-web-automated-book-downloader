"""Network operations manager for the book downloader application."""

import requests
import urllib.request
from typing import Sequence, Tuple, Any, Union, cast, List
import socket
import dns.resolver
from socket import AddressFamily, SocketKind
import urllib.parse
import ssl

from logger import setup_logger
from config import PROXIES, AA_BASE_URL, CUSTOM_DNS, AA_AVAILABLE_URLS, DOH_SERVER

logger = setup_logger(__name__)

# DoH resolver class
def init_doh_resolver(doh_server: str = DOH_SERVER):
    # Pre-resolve the DoH server hostname to prevent recursion
    url = urllib.parse.urlparse(DOH_SERVER)
    server_hostname = url.hostname
    server_ip = socket.gethostbyname(server_hostname)
    logger.info(f"DoH server {server_hostname} resolved to IP: {server_ip}")
    
    class DoHResolver:
        """DNS over HTTPS resolver implementation."""
        def __init__(self, provider_url: str, hostname: str, ip: str):
            """Initialize DoH resolver with specified provider."""
            self.base_url = provider_url.lower().strip()
            self.hostname = hostname  # Store the hostname for hostname-based skipping
            self.ip = ip              # Store IP for direct connections
            self.session = requests.Session()
            
            # Custom SSL context for SNI
            # Create a custom adapter with SNI support for IP-based connections
            class SNIAdapter(requests.adapters.HTTPAdapter):
                def __init__(self, server_hostname):
                    self.server_hostname = server_hostname
                    super().__init__()
                
                def init_poolmanager(self, *args, **kwargs):
                    # Use a custom SSL context that includes the SNI hostname
                    context = ssl.create_default_context()
                    context.check_hostname = True
                    kwargs["ssl_context"] = context
                    kwargs["server_hostname"] = self.server_hostname
                    return super().init_poolmanager(*args, **kwargs)
            
            # Use the original hostname URL for requests
            # This handles SNI properly while still using the correct hostname for verification
            
            # Different headers based on provider
            if 'google' in self.base_url:
                self.session.headers.update({
                    'Accept': 'application/json',
                })
            else:
                self.session.headers.update({
                    'Accept': 'application/dns-json',
                })
        
        def resolve(self, hostname: str, record_type: str) -> List[str]:
            """Resolve a hostname using DoH.
            
            Args:
                hostname: The hostname to resolve
                record_type: The DNS record type (A or AAAA)
                
            Returns:
                List of resolved IP addresses
            """
            # Skip resolution for the DoH server itself to prevent recursion
            if hostname == self.hostname:
                logger.debug(f"Skipping DoH resolution for DoH server itself: {hostname}")
                return [self.ip]
                
            try:
                params = {
                    'name': hostname,
                    'type': 'AAAA' if record_type == 'AAAA' else 'A'
                }
                
                # Just use the original URL - no need for IP-based URL with Host header
                # This is simpler and more reliable for TLS certificate validation
                response = self.session.get(
                    self.base_url,
                    params=params,
                    proxies=PROXIES,
                    timeout=5
                )
                response.raise_for_status()
                
                data = response.json()
                if 'Answer' not in data:
                    logger.warning(f"DoH resolution failed for {hostname}: {data}")
                    return []
                
                # Extract IP addresses from the response    
                answers = [answer['data'] for answer in data['Answer'] 
                        if answer.get('type') == (28 if record_type == 'AAAA' else 1)]
                logger.debug(f"Resolved {hostname} to {len(answers)} addresses using DoH: {answers}")
                return answers
                
            except Exception as e:
                logger.warning(f"DoH resolution failed for {hostname}: {e}")
                return []
    
    # Check if the DOH_SERVER is a valid preset provider
    logger.info(f"Initializing DoH resolver with provider: {doh_server}")
    doh_resolver = DoHResolver(doh_server, server_hostname, server_ip)
    
    # Helper functions for DoH-enabled socket.getaddrinfo
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
    
    # Store the original getaddrinfo function
    original_getaddrinfo = socket.getaddrinfo
    
    # Create a DoH-enabled getaddrinfo function
    def doh_getaddrinfo(
        host: Union[str, bytes, None],
        port: Union[str, bytes, int, None],
        family: int = 0,
        type: int = 0,
        proto: int = 0,
        flags: int = 0
    ) -> Sequence[Tuple[AddressFamily, SocketKind, int, str, Tuple[Any, ...]]]:
        """Resolve addresses using DoH before falling back to system DNS."""
        host_str = _decode_host(host)
        port_int = _decode_port(port)
        
        # Skip DoH for local addresses and the DoH server itself
        if (host_str == 'localhost' or 
            host_str.startswith('127.') or 
            host_str.startswith('::1') or 
            host_str.startswith('0.0.0.0') or
            host_str == server_hostname or
            host_str == server_ip):
            return original_getaddrinfo(host, port, family, type, proto, flags)
        
        # Handle the DoH server directly with pre-resolved IP
        if host_str == server_hostname:
            if family == 0 or family == socket.AF_INET:
                logger.debug(f"Using pre-resolved IP for DoH server: {server_ip}")
                return [(socket.AF_INET, cast(SocketKind, type), proto, '', (server_ip, port_int))]
            else:
                return original_getaddrinfo(host, port, family, type, proto, flags)
        
        results: list[Tuple[AddressFamily, SocketKind, int, str, Tuple[Any, ...]]] = []
        
        # Try DoH resolution first
        try:
            # Try IPv6 first if family allows it
            if family == 0 or family == socket.AF_INET6:
                logger.debug(f"Resolving IPv6 address for {host_str} using DoH")
                ipv6_answers = doh_resolver.resolve(host_str, 'AAAA')
                for answer in ipv6_answers:
                    results.append((socket.AF_INET6, cast(SocketKind, type), proto, '', (answer, port_int, 0, 0)))
            
            # Then try IPv4
            if family == 0 or family == socket.AF_INET:
                logger.debug(f"Resolving IPv4 address for {host_str} using DoH")
                ipv4_answers = doh_resolver.resolve(host_str, 'A')
                for answer in ipv4_answers:
                    results.append((socket.AF_INET, cast(SocketKind, type), proto, '', (answer, port_int)))
            
            if results:
                logger.debug(f"Successfully resolved {host_str} using DoH")
                return results
            
        except Exception as e:
            logger.warning(f"DoH resolution failed for {host_str}: {e}, falling back to system DNS")
        
        # Fall back to system DNS if DoH resolution fails
        try:
            logger.debug(f"Falling back to system DNS for {host_str}")
            return original_getaddrinfo(host, port, family, type, proto, flags)
        except Exception as e:
            logger.error(f"System DNS resolution also failed for {host_str}: {e}")
            # Last resort: Try to connect to the hostname directly
            if family == 0 or family == socket.AF_INET:
                logger.warning(f"Using direct hostname as last resort for {host_str}")
                return [(socket.AF_INET, cast(SocketKind, type), proto, '', (host_str, port_int))]
            else:
                raise  # Re-raise the exception if we can't provide a last resort
    
    # Replace the system's getaddrinfo with our DoH-enabled version
    socket.getaddrinfo = cast(Any, doh_getaddrinfo)
    logger.info("DoH resolver successfully configured and activated")
    
    return doh_resolver


def init_custom_resolver():
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

# Configure DNS resolver to use Custom DNS
if len(CUSTOM_DNS) > 0:
    init_custom_resolver()
    if DOH_SERVER:
        init_doh_resolver()
        
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


# Configure urllib opener with appropriate headers
opener = urllib.request.build_opener()
opener.addheaders = [
    ('User-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/129.0.0.0 Safari/537.3')
]
urllib.request.install_opener(opener)

# Need an empty function to be called by downloader.py
def init():
    pass
