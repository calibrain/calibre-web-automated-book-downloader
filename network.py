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
import threading

from logger import setup_logger
from config import PROXIES, AA_BASE_URL, CUSTOM_DNS, AA_AVAILABLE_URLS, DOH_SERVER
import config
import env
from datetime import datetime, timedelta

logger = setup_logger(__name__)

# In-memory state (no disk persistence)
STATE_TTL_DAYS = 30
_initialized = False
_dns_initialized = False
_aa_initialized = False
_state_lock = threading.Lock()
state: dict[str, Any] = {}

def _agent_debug_log(code: str, source: str, reason: str, meta: Optional[dict] = None) -> None:
    """Lightweight debug hook for automated runs; safe no-op on failure."""
    try:
        logger.debug(f"[agent] code={code} source={source} reason={reason} meta={meta or {}}")
    except Exception as exc:
        # Avoid raising inside debug logger
        logger.debug(f"[agent] log failed: {exc}")

def _load_state():
    """Return current in-memory network state (no disk persistence)."""
    if state.get('chosen_at'):
        chosen = datetime.fromisoformat(state['chosen_at'])
        if datetime.now() - chosen > timedelta(days=STATE_TTL_DAYS):
            state.clear()
    return state

def _save_state(aa_url=None, dns_provider=None):
    """Update in-memory network state (no disk persistence)."""
    with _state_lock:
        if aa_url:
            state['aa_base_url'] = aa_url
        if dns_provider:
            state['dns_provider'] = dns_provider
        state['chosen_at'] = datetime.now().isoformat()

# AA URL failover state
_current_aa_url_index = 0
_aa_urls = AA_AVAILABLE_URLS.copy()

def _ensure_initialized() -> None:
    """Lazy guard so runtime setup happens once and late calls still work."""
    if not _initialized:
        init()

# DNS provider rotation state
# _current_dns_index = -1 means using system DNS (no custom DNS)
# _current_dns_index >= 0 means using provider at that index
_dns_providers = [
    ("cloudflare", ["1.1.1.1", "1.0.0.1"], "https://cloudflare-dns.com/dns-query"),
    ("google", ["8.8.8.8", "8.8.4.4"], "https://dns.google/dns-query"),
    ("quad9", ["9.9.9.9", "149.112.112.112"], "https://dns.quad9.net/dns-query"),
    ("opendns", ["208.67.222.222", "208.67.220.220"], "https://doh.opendns.com/dns-query"),
]
_current_dns_index = -1  # Start with system DNS

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

def _aa_hostnames() -> List[str]:
    """Return hostname portions for all configured AA URLs."""
    return [
        parsed.hostname for parsed in (urllib.parse.urlparse(url) for url in _aa_urls)
        if parsed.hostname
    ]

def _is_aa_hostname(host_str: str) -> bool:
    """Check if a hostname matches any configured AA mirror host."""
    return any(host_str.endswith(hostname) for hostname in _aa_hostnames())

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
    """Resolve hostname using custom DNS resolver."""
    try:
        answers = resolver.resolve(hostname, record_type)
        return [str(answer) for answer in answers]
    except Exception as e:
        logger.debug(f"{record_type} resolution failed for {hostname}: {e}")
        # Trigger DNS switch on failure (if auto mode)
        # Only switch if we're in auto mode and it's not a local address
        if env._CUSTOM_DNS.lower().strip() == "auto" and not _is_local_address(hostname) and not _is_ip_address(hostname):
            # Only switch if we're using system DNS or a custom provider (not if already exhausted)
            if _current_dns_index < len(_dns_providers):
                logger.info(f"Requesting DNS provider switch after {record_type} resolution failure for {hostname}")
                switch_dns_provider()
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
        
        # Anna's Archive often works only on IPv4 for some ISPs; prefer IPv4 based on configured mirrors
        prefer_ipv4_only = _is_aa_hostname(host_str)
        
        results: list[Tuple[AddressFamily, SocketKind, int, str, Tuple[Any, ...]]] = []
        
        try:
            # Try IPv6 first if family allows it and not forced to IPv4
            if not prefer_ipv4_only and (family == 0 or family == socket.AF_INET6):
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
            # Trigger DNS switch on failure (if auto mode)
            if env._CUSTOM_DNS.lower().strip() == "auto" and not _is_local_address(host_str) and not _is_ip_address(host_str):
                # Only switch if we're using system DNS or a custom provider (not if already exhausted)
                if _current_dns_index < len(_dns_providers):
                    logger.info(f"Requesting DNS provider switch after custom resolver failure for {host_str}")
                    switch_dns_provider()
        
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

def create_system_failover_getaddrinfo():
    """Wrap system getaddrinfo to trigger DNS provider switch on failure."""
    def system_failover_getaddrinfo(
        host: Union[str, bytes, None],
        port: Union[str, bytes, int, None],
        family: int = 0,
        type: int = 0,
        proto: int = 0,
        flags: int = 0
    ) -> Sequence[Tuple[AddressFamily, SocketKind, int, str, Tuple[Any, ...]]]:
        host_str = _decode_host(host)
        port_int = _decode_port(port)
        try:
            return original_getaddrinfo(host, port, family, type, proto, flags)
        except Exception as e:
            logger.warning(f"System DNS resolution failed for {host_str}: {e}")
            # Trigger DNS switch only in auto mode for non-local targets
            if env._CUSTOM_DNS.lower().strip() == "auto" and not env.USING_TOR:
                if not _is_ip_address(host_str) and not _is_local_address(host_str):
                    if _current_dns_index + 1 < len(_dns_providers):
                        logger.info(f"Switching DNS provider after system DNS failure for {host_str}")
                        switch_dns_provider()
                        # After switching, socket.getaddrinfo points to the new resolver
                        return socket.getaddrinfo(host, port, family, type, proto, flags)
            # Re-raise if we cannot switch or still fail
            raise
    
    return system_failover_getaddrinfo

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

def switch_dns_provider() -> bool:
    """Switch to next DNS provider (auto mode only). Starts with system DNS, switches on first failure."""
    _ensure_initialized()
    global CUSTOM_DNS, DOH_SERVER, _current_dns_index
    with _state_lock:
        if _current_dns_index + 1 >= len(_dns_providers):
            logger.warning("All DNS providers exhausted, staying with current")
            return False
        _current_dns_index += 1
        name, servers, doh = _dns_providers[_current_dns_index]
        CUSTOM_DNS = servers
        DOH_SERVER = doh if env.USE_DOH else ""
        config.CUSTOM_DNS = CUSTOM_DNS
        config.DOH_SERVER = DOH_SERVER
        logger.warning(f"Switched DNS provider to: {name}")
        _save_state(dns_provider=name)
    init_dns_resolvers()
    return True

def rotate_dns_provider() -> bool:
    """
    Switch DNS provider (auto mode only). Does not alter AA selection.
    Returns True if DNS switched.
    """
    _ensure_initialized()
    global _current_dns_index
    if env._CUSTOM_DNS.lower().strip() != "auto" or env.USING_TOR:
        return False
    with _state_lock:
        if _current_dns_index + 1 >= len(_dns_providers):
            logger.warning("DNS rotation requested but all providers exhausted; cycling back to first provider")
            _current_dns_index = -1
    return switch_dns_provider()

def rotate_dns_and_reset_aa() -> bool:
    """
    Switch DNS provider (auto mode) and reset AA URL list to the first entry.
    Returns True if DNS switched; False if no providers left or not in auto mode.
    """
    _ensure_initialized()
    if not rotate_dns_provider():
        return False
    # Reset AA URL to first available auto option if using auto AA
    global AA_BASE_URL, _current_aa_url_index
    if AA_BASE_URL == "auto" or AA_BASE_URL in _aa_urls:
        _current_aa_url_index = 0
        AA_BASE_URL = _aa_urls[0]
        config.AA_BASE_URL = AA_BASE_URL
        logger.info(f"After DNS switch, resetting AA URL to: {AA_BASE_URL}")
        _save_state(aa_url=AA_BASE_URL)
    return True

def switch_aa_url():
    """Switch to next AA URL (only if current URL is in available list)."""
    _ensure_initialized()
    global AA_BASE_URL, _current_aa_url_index
    # Don't switch if current URL is not in available list (user-specified custom URL)
    if AA_BASE_URL not in _aa_urls:
        return False
    if _current_aa_url_index + 1 >= len(_aa_urls):
        return False
    _current_aa_url_index += 1
    AA_BASE_URL = _aa_urls[_current_aa_url_index]
    config.AA_BASE_URL = AA_BASE_URL
    logger.warning(f"Switched AA URL to: {AA_BASE_URL}")
    _save_state(aa_url=AA_BASE_URL)
    return True

# Initialize DNS resolvers based on configuration
def init_dns_resolvers():
    """Initialize DNS resolvers based on configuration."""
    global CUSTOM_DNS, DOH_SERVER
    
    # If auto mode, use current DNS provider (or system DNS if _current_dns_index == -1)
    if env._CUSTOM_DNS.lower().strip() == "auto" and not env.USING_TOR:
        if _current_dns_index >= 0:
            # Using a custom DNS provider
            name, servers, doh = _dns_providers[_current_dns_index]
            CUSTOM_DNS = servers
            DOH_SERVER = doh if env.USE_DOH else ""
            config.CUSTOM_DNS = CUSTOM_DNS
            config.DOH_SERVER = DOH_SERVER
            logger.info(f"Using DNS provider: {name}")
        else:
            # Using system DNS (no custom DNS)
            CUSTOM_DNS = []
            DOH_SERVER = ""
            config.CUSTOM_DNS = CUSTOM_DNS
            config.DOH_SERVER = DOH_SERVER
            logger.info("Using system DNS (auto mode - will switch on failure)")
            # Install failover wrapper so we can detect failures and rotate DNS
            socket.getaddrinfo = cast(Any, create_system_failover_getaddrinfo())
    
    if len(CUSTOM_DNS) > 0:
        init_custom_resolver()
        if DOH_SERVER:
            init_doh_resolver()

def _initialize_dns_state() -> None:
    """Restore persisted DNS choice or start fresh."""
    global _current_dns_index, state
    if env._CUSTOM_DNS.lower().strip() == "auto":
        if state.get('dns_provider'):
            for i, (name, _, _) in enumerate(_dns_providers):
                if name == state['dns_provider']:
                    _current_dns_index = i
                    logger.info(f"Restored DNS provider from state: {name}")
                    break
        else:
            _current_dns_index = -1
            logger.info("Starting with system DNS (auto mode)")
    else:
        _current_dns_index = -1

def _initialize_aa_state() -> None:
    """Restore or probe AA URL state."""
    global AA_BASE_URL, _current_aa_url_index
    if AA_BASE_URL == "auto":
        if state.get('aa_base_url') and state['aa_base_url'] in _aa_urls:
            _current_aa_url_index = _aa_urls.index(state['aa_base_url'])
            AA_BASE_URL = state['aa_base_url']
        else:
            logger.info(f"AA_BASE_URL: auto, checking available urls {_aa_urls}")
            for i, url in enumerate(_aa_urls):
                try:
                    response = requests.get(url, proxies=PROXIES, timeout=3)
                    if response.status_code == 200:
                        _current_aa_url_index = i
                        AA_BASE_URL = url
                        _save_state(aa_url=AA_BASE_URL)
                        break
                except Exception:
                    pass
            if AA_BASE_URL == "auto":
                AA_BASE_URL = _aa_urls[0]
                _current_aa_url_index = 0
    elif AA_BASE_URL not in _aa_urls:
        logger.info(f"AA_BASE_URL set to custom value {AA_BASE_URL}; skipping auto-switch")
    else:
        _current_aa_url_index = _aa_urls.index(AA_BASE_URL)

    config.AA_BASE_URL = AA_BASE_URL
    logger.info(f"AA_BASE_URL: {AA_BASE_URL}")

def init_dns(force: bool = False) -> None:
    """Initialize DNS state and resolvers."""
    global state, _dns_initialized
    if _dns_initialized and not force:
        return
    state = _load_state()
    _initialize_dns_state()
    init_dns_resolvers()
    _dns_initialized = True

def init_aa(force: bool = False) -> None:
    """Initialize AA mirror selection."""
    global state, _aa_initialized
    if _aa_initialized and not force:
        return
    state = _load_state()
    _initialize_aa_state()
    _aa_initialized = True

def init(force: bool = False) -> None:
    """
    Perform network bootstrap once at startup.

    Safe to call repeatedly; later calls no-op unless force=True.
    """
    global _initialized
    if _initialized and not force:
        return

    init_dns(force=force)
    init_aa(force=force)
    _initialized = True

def get_aa_base_url():
    """Get current AA base URL."""
    _ensure_initialized()
    return AA_BASE_URL

def get_available_aa_urls():
    """Get list of configured AA URLs (copy)."""
    _ensure_initialized()
    return _aa_urls.copy()

def set_aa_url_index(new_index: int) -> bool:
    """Set AA base URL by index in available list; returns True if applied."""
    _ensure_initialized()
    global AA_BASE_URL, _current_aa_url_index
    if new_index < 0 or new_index >= len(_aa_urls):
        return False
    with _state_lock:
        _current_aa_url_index = new_index
        AA_BASE_URL = _aa_urls[_current_aa_url_index]
        config.AA_BASE_URL = AA_BASE_URL
        logger.info(f"Set AA URL to: {AA_BASE_URL}")
        _save_state(aa_url=AA_BASE_URL)
    return True

class AAMirrorSelector:
    """
    Small helper to keep AA mirror switching consistent across call sites.
    Tracks attempts per DNS cycle and rewrites URLs safely.
    """
    def __init__(self) -> None:
        self._ensure_fresh_state(reset_attempts=True)

    def _ensure_fresh_state(self, reset_attempts: bool = False) -> None:
        _ensure_initialized()
        self.aa_urls = get_available_aa_urls()
        self._index = self._safe_index(get_aa_base_url())
        self.current_base = self.aa_urls[self._index] if self.aa_urls else ""
        if reset_attempts:
            self.attempts_this_dns = 0

    def _safe_index(self, base: str) -> int:
        if base in self.aa_urls:
            return self.aa_urls.index(base)
        return 0

    def rewrite(self, url: str) -> str:
        """Replace any known AA base in url with current_base."""
        for base in self.aa_urls:
            if url.startswith(base):
                return url.replace(base, self.current_base, 1)
        return url

    def next_mirror_or_rotate_dns(self, allow_dns: bool = True) -> tuple[Optional[str], str]:
        """
        Advance to next mirror; if exhausted and allowed, rotate DNS and reset to first.
        Returns (new_base, action) where action is 'mirror', 'dns', or 'exhausted'.
        """
        self.attempts_this_dns += 1
        if self.attempts_this_dns >= len(self.aa_urls):
            if allow_dns and rotate_dns_and_reset_aa():
                self._ensure_fresh_state(reset_attempts=True)
                return self.current_base, "dns"
            return None, "exhausted"

        next_index = (self._index + 1) % len(self.aa_urls)
        set_aa_url_index(next_index)
        self._ensure_fresh_state(reset_attempts=False)
        return self.current_base, "mirror"

# Configure urllib opener with appropriate headers
opener = urllib.request.build_opener()
opener.addheaders = [
    ('User-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/129.0.0.0 Safari/537.3')
]
urllib.request.install_opener(opener)
