"""Network operations manager for the book downloader application."""

import requests
import urllib.request
from typing import Sequence, Tuple, Any, Union, cast, List, Optional, Callable
import socket
import dns.resolver
from socket import AddressFamily, SocketKind
import urllib.parse
import ssl
import ipaddress

from logger import setup_logger
from config import PROXIES, AA_BASE_URL, CUSTOM_DNS, AA_AVAILABLE_URLS, DOH_SERVER
import config

logger = setup_logger(__name__)

# Common helper functions for DNS resolution
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

def _is_local_address(host_str: str) -> bool:
    """Check if an address is local and should bypass custom DNS."""
    """Check if an address is local or private and should bypass custom DNS."""
    # Localhost checks
    if (host_str == 'localhost' or 
        host_str.startswith('127.') or 
        host_str == '::1' or 
        host_str == '0.0.0.0'):
        return True
        
    # IPv4 private ranges (RFC 1918)
    if (host_str.startswith('10.') or 
        (host_str.startswith('172.') and 
         len(host_str.split('.')) > 1 and 
         16 <= int(host_str.split('.')[1]) <= 31) or
        host_str.startswith('192.168.')):
        return True
        
    # IPv6 private ranges
    if (host_str.startswith('fc') or 
        host_str.startswith('fd') or  # Unique local addresses (fc00::/7)
        host_str.startswith('fe80:')):  # Link-local addresses (fe80::/10)
        return True
    
    return False

def _is_ip_address(host_str: str) -> bool:
    """Check if a string is a valid IP address (IPv4 or IPv6)."""
    try:
        ipaddress.ip_address(host_str)
        return True
    except ValueError:
        return False

# Store the original getaddrinfo function
original_getaddrinfo = socket.getaddrinfo

class DoHResolver:
    """DNS over HTTPS resolver implementation."""
    def __init__(self, provider_url: str, hostname: str, ip: str):
        """Initialize DoH resolver with specified provider."""
        self.base_url = provider_url.lower().strip()
        self.hostname = hostname  # Store the hostname for hostname-based skipping
        self.ip = ip              # Store IP for direct connections
        self.session = requests.Session()
        
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
        # Check if hostname is already an IP address, no need to resolve
        if _is_ip_address(hostname):
            logger.debug(f"Skipping DoH resolution for IP address: {hostname}")
            return [hostname]
            
        # Check if hostname is a private IP address, and skip DoH if it is
        if _is_local_address(hostname):
            logger.debug(f"Skipping DoH resolution for private IP: {hostname}")
            return [hostname]
            
        # Skip resolution for the DoH server itself to prevent recursion
        if hostname == self.hostname:
            logger.debug(f"Skipping DoH resolution for DoH server itself: {hostname}")
            return [self.ip]
            
        try:
            params = {
                'name': hostname,
                'type': 'AAAA' if record_type == 'AAAA' else 'A'
            }
            
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

def create_custom_resolver():
    """Create a custom DNS resolver using the configured DNS servers."""
    custom_resolver = dns.resolver.Resolver()
    custom_resolver.nameservers = CUSTOM_DNS
    return custom_resolver

def resolve_with_custom_dns(resolver, hostname: str, record_type: str) -> List[str]:
    """Resolve hostname using custom DNS resolver.
    
    Args:
        resolver: The DNS resolver to use
        hostname: The hostname to resolve
        record_type: The DNS record type (A or AAAA)
        
    Returns:
        List of resolved IP addresses
    """
    try:
        answers = resolver.resolve(hostname, record_type)
        return [str(answer) for answer in answers]
    except Exception as e:
        logger.debug(f"{record_type} resolution failed for {hostname}: {e}")
        return []

def create_custom_getaddrinfo(
    resolve_ipv4: Callable[[str], List[str]],
    resolve_ipv6: Callable[[str], List[str]],
    skip_check: Optional[Callable[[str], bool]] = None
):
    """Create a custom getaddrinfo function that uses the provided resolvers.
    
    Args:
        resolve_ipv4: Function to resolve IPv4 addresses
        resolve_ipv6: Function to resolve IPv6 addresses
        skip_check: Optional function to check if custom resolution should be skipped
        
    Returns:
        A custom getaddrinfo function
    """
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
        
        # Skip custom resolution for IP addresses, local addresses, or if skip check passes
        if _is_ip_address(host_str) or _is_local_address(host_str) or (skip_check and skip_check(host_str)):
            logger.debug(f"Using system DNS for IP address or local/private address: {host_str}")
            return original_getaddrinfo(host, port, family, type, proto, flags)
        
        results: list[Tuple[AddressFamily, SocketKind, int, str, Tuple[Any, ...]]] = []
        
        try:
            # Try IPv6 first if family allows it
            if family == 0 or family == socket.AF_INET6:
                logger.debug(f"Resolving IPv6 address for {host_str}")
                ipv6_answers = resolve_ipv6(host_str)
                for answer in ipv6_answers:
                    results.append((socket.AF_INET6, cast(SocketKind, type), proto, '', (answer, port_int, 0, 0)))
                if ipv6_answers:
                    logger.debug(f"Found {len(ipv6_answers)} IPv6 addresses for {host_str}")
            
            # Then try IPv4
            if family == 0 or family == socket.AF_INET:
                logger.debug(f"Resolving IPv4 address for {host_str}")
                ipv4_answers = resolve_ipv4(host_str)
                for answer in ipv4_answers:
                    results.append((socket.AF_INET, cast(SocketKind, type), proto, '', (answer, port_int)))
                if ipv4_answers:
                    logger.debug(f"Found {len(ipv4_answers)} IPv4 addresses for {host_str}")
            
            if results:
                logger.debug(f"Resolved {host_str} to {len(results)} addresses")
                return results
                
        except Exception as e:
            logger.warning(f"Custom DNS resolution failed for {host_str}: {e}, falling back to system DNS")
        
        # Fall back to system DNS if custom resolution fails
        try:
            return original_getaddrinfo(host, port, family, type, proto, flags)
        except Exception as e:
            logger.error(f"System DNS resolution also failed for {host_str}: {e}")
            # Last resort: Try to connect to the hostname directly
            if family == 0 or family == socket.AF_INET:
                logger.warning(f"Using direct hostname as last resort for {host_str}")
                return [(socket.AF_INET, cast(SocketKind, type), proto, '', (host_str, port_int))]
            else:
                raise  # Re-raise the exception if we can't provide a last resort
    
    return custom_getaddrinfo

def init_doh_resolver(doh_server: str = DOH_SERVER):
    """Initialize DNS over HTTPS resolver.
    
    Args:
        doh_server: The DoH server URL
    """
    # Pre-resolve the DoH server hostname to prevent recursion
    url = urllib.parse.urlparse(doh_server)
    server_hostname = url.hostname if url.hostname else ''
    
    # Use system DNS for DoH server to prevent circular dependencies
    try:
        # Temporarily restore original getaddrinfo to resolve DoH server
        temp_getaddrinfo = socket.getaddrinfo
        socket.getaddrinfo = original_getaddrinfo
        
        server_ip = socket.gethostbyname(server_hostname)
        logger.info(f"DoH server {server_hostname} resolved to IP: {server_ip}")
        
        # Restore custom getaddrinfo if it was previously set
        socket.getaddrinfo = temp_getaddrinfo
    except Exception as e:
        logger.error(f"Failed to resolve DoH server {server_hostname}: {e}")
        # Fall back to a known public DNS if resolution fails
        server_ip = "1.1.1.1"
        logger.info(f"Using fallback IP for DoH server: {server_ip}")
    
    # Create DoH resolver
    doh_resolver = DoHResolver(doh_server, server_hostname, server_ip)
    
    # Create resolver functions
    def resolve_ipv4(hostname: str) -> List[str]:
        return doh_resolver.resolve(hostname, 'A')
    
    def resolve_ipv6(hostname: str) -> List[str]:
        return doh_resolver.resolve(hostname, 'AAAA')
    
    # Skip DoH resolution for the DoH server itself, IP addresses, and private addresses
    def skip_doh(hostname: str) -> bool:
        return (hostname == server_hostname or 
                hostname == server_ip or 
                _is_ip_address(hostname) or 
                _is_local_address(hostname))
    
    # Replace socket.getaddrinfo with our DoH-enabled version
    socket.getaddrinfo = cast(Any, create_custom_getaddrinfo(
        resolve_ipv4, resolve_ipv6, skip_doh
    ))
    
    logger.info("DoH resolver successfully configured and activated")
    return doh_resolver

def init_custom_resolver():
    """Initialize custom DNS resolver using configured DNS servers."""
    custom_resolver = create_custom_resolver()
    
    # Create resolver functions
    def resolve_ipv4(hostname: str) -> List[str]:
        return resolve_with_custom_dns(custom_resolver, hostname, 'A')
    
    def resolve_ipv6(hostname: str) -> List[str]:
        return resolve_with_custom_dns(custom_resolver, hostname, 'AAAA')
    
    # Replace socket.getaddrinfo with our custom resolver
    socket.getaddrinfo = cast(Any, create_custom_getaddrinfo(resolve_ipv4, resolve_ipv6))
    
    logger.info("Custom DNS resolver successfully configured and activated")
    return custom_resolver

# Initialize DNS resolvers based on configuration
def init_dns_resolvers():
    """Initialize DNS resolvers based on configuration."""
    if len(CUSTOM_DNS) > 0:
        init_custom_resolver()
        if DOH_SERVER:
            init_doh_resolver()

# Initialize DNS resolvers
init_dns_resolvers()

# Check available AA_BASE_URLs if set to auto
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
config.AA_BASE_URL = AA_BASE_URL
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
