"""DNS rotation, mirror selection, and network utilities."""

import os
import requests
import urllib.request
from typing import Sequence, Tuple, Any, Union, cast, List, Optional, Callable
import socket
import dns.resolver
from socket import AddressFamily, SocketKind
import urllib.parse
import ssl
import ipaddress

from cwa_book_downloader.core.logger import setup_logger
from cwa_book_downloader.config.settings import AA_BASE_URL, AA_AVAILABLE_URLS
from cwa_book_downloader.config import settings as config
from cwa_book_downloader.core.config import config as app_config
from datetime import datetime, timedelta


def get_proxies() -> dict:
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

# DNS state - authoritative values managed by this module
# Other modules should use get_dns_config() to read these
CUSTOM_DNS: List[str] = []
DOH_SERVER: str = ""

# Try to use gevent locks if available (for gevent worker compatibility)
# Fall back to threading locks for non-gevent environments
try:
    from gevent.lock import RLock as _RLock
    _using_gevent_locks = True
except ImportError:
    from threading import RLock as _RLock
    _using_gevent_locks = False

logger = setup_logger(__name__)

# In-memory state (no disk persistence)
STATE_TTL_DAYS = 30
_initialized = False
_dns_initialized = False
_aa_initialized = False
state: dict[str, Any] = {}

# Locks for greenlet-safe initialization and DNS switching
# Use RLock (reentrant lock) since init() calls init_dns() and init_aa()
_init_lock = _RLock()
_dns_switch_lock = _RLock()

# DNS rotation callbacks - called when DNS provider switches in auto mode
# Callbacks receive (provider_name: str, servers: List[str], doh_url: str)
_dns_rotation_callbacks: List[Callable[[str, List[str], str], None]] = []
_dns_callback_lock = _RLock()


def register_dns_rotation_callback(callback: Callable[[str, List[str], str], None]) -> None:
    """Register a callback to be called when DNS provider rotates.

    The callback receives (provider_name, servers, doh_url) as arguments.
    Use this to restart components that cache DNS resolution (e.g., Chrome).
    """
    with _dns_callback_lock:
        if callback not in _dns_rotation_callbacks:
            _dns_rotation_callbacks.append(callback)
            logger.debug(f"Registered DNS rotation callback: {callback.__name__}")


def unregister_dns_rotation_callback(callback: Callable[[str, List[str], str], None]) -> None:
    """Unregister a previously registered DNS rotation callback."""
    with _dns_callback_lock:
        if callback in _dns_rotation_callbacks:
            _dns_rotation_callbacks.remove(callback)
            logger.debug(f"Unregistered DNS rotation callback: {callback.__name__}")


def _notify_dns_rotation(provider_name: str, servers: List[str], doh_url: str) -> None:
    """Notify all registered callbacks about DNS rotation."""
    with _dns_callback_lock:
        callbacks = _dns_rotation_callbacks.copy()

    for callback in callbacks:
        try:
            logger.debug(f"Calling DNS rotation callback: {callback.__name__}")
            callback(provider_name, servers, doh_url)
        except Exception as e:
            logger.warning(f"DNS rotation callback {callback.__name__} failed: {e}")

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
    global _initialized
    if _initialized:
        return
    with _init_lock:
        # Double-check after acquiring lock
        if not _initialized:
            init()

# DNS provider definitions: (name, servers, doh_url)
# Note: Google uses /resolve endpoint for JSON API, others use /dns-query
DNS_PROVIDERS = [
    ("cloudflare", ["1.1.1.1", "1.0.0.1"], "https://cloudflare-dns.com/dns-query"),
    ("google", ["8.8.8.8", "8.8.4.4"], "https://dns.google/resolve"),
    ("quad9", ["9.9.9.9", "149.112.112.112"], "https://dns.quad9.net/dns-query"),
    ("opendns", ["208.67.222.222", "208.67.220.220"], "https://doh.opendns.com/dns-query"),
]

# Domain patterns that should trigger DNS rotation on failure
DNS_ROTATION_DOMAINS = [
    "annas-archive",
]


def should_rotate_dns_for_url(url: str) -> bool:
    """Check if a URL matches a known source domain for DNS rotation."""
    url_lower = url.lower()
    return any(domain in url_lower for domain in DNS_ROTATION_DOMAINS)


# DNS state
_current_dns_index = -1  # -1 = system DNS
_dns_exhausted_logged = False


def _is_auto_dns_mode() -> bool:
    """Check if DNS is in auto-rotation mode."""
    custom_dns = app_config.get("CUSTOM_DNS", "auto")
    using_tor = app_config.get("USING_TOR", False)
    return str(custom_dns).lower().strip() == "auto" and not using_tor


def _current_dns_label() -> str:
    """Readable label for the active DNS choice."""
    if _current_dns_index >= 0:
        return DNS_PROVIDERS[_current_dns_index][0]
    if CUSTOM_DNS:
        return f"manual ({len(CUSTOM_DNS)} servers)"
    return "system"


def get_dns_config() -> dict:
    """
    Get the current DNS configuration.

    Returns:
        Dict with keys:
        - provider: str - Current provider name ('auto', 'system', 'google', 'cloudflare', etc.)
        - servers: List[str] - DNS server IPs in use
        - doh_url: str - DoH server URL (empty if disabled)
        - doh_enabled: bool - Whether DoH is active
        - is_auto_mode: bool - Whether auto-rotation is enabled
    """
    _ensure_initialized()

    custom_dns = str(app_config.get("CUSTOM_DNS", "auto")).lower().strip()
    if _current_dns_index >= 0:
        provider = DNS_PROVIDERS[_current_dns_index][0]
    elif custom_dns == "auto":
        provider = "auto"
    elif custom_dns == "system":
        provider = "system"
    elif custom_dns == "manual":
        provider = "manual"
    else:
        provider = custom_dns

    return {
        "provider": provider,
        "servers": list(CUSTOM_DNS),
        "doh_url": DOH_SERVER,
        "doh_enabled": bool(DOH_SERVER),
        "is_auto_mode": _is_auto_dns_mode(),
    }

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
    """DNS over HTTPS resolver implementation with caching."""
    
    # Cache TTL in seconds (5 minutes)
    CACHE_TTL = 300
    
    def __init__(self, provider_url: str, hostname: str, ip: str):
        """Initialize DoH resolver with specified provider."""
        self.base_url = provider_url.lower().strip()
        self.hostname = hostname  # Store the hostname for hostname-based skipping
        self.ip = ip              # Store IP for direct connections
        self.session = requests.Session()
        # DNS cache: {(hostname, record_type): (ip_list, timestamp)}
        self._cache: dict[tuple[str, str], tuple[List[str], datetime]] = {}
        
        # Different headers based on provider
        if 'google' in self.base_url:
            self.session.headers.update({
                'Accept': 'application/json',
            })
        else:
            self.session.headers.update({
                'Accept': 'application/dns-json',
            })
    
    def _get_cached(self, hostname: str, record_type: str) -> Optional[List[str]]:
        """Get cached DNS result if still valid."""
        key = (hostname, record_type)
        if key in self._cache:
            ips, timestamp = self._cache[key]
            if datetime.now() - timestamp < timedelta(seconds=self.CACHE_TTL):
                logger.debug(f"DoH cache hit for {hostname}: {ips}")
                return ips
            else:
                # Cache expired, remove it
                del self._cache[key]
        return None
    
    def _set_cached(self, hostname: str, record_type: str, ips: List[str]) -> None:
        """Cache DNS result."""
        if ips:  # Only cache non-empty results
            self._cache[(hostname, record_type)] = (ips, datetime.now())
    
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
        
        # Check cache first
        cached = self._get_cached(hostname, record_type)
        if cached is not None:
            return cached
            
        try:
            params = {
                'name': hostname,
                'type': 'AAAA' if record_type == 'AAAA' else 'A'
            }
            
            response = self.session.get(
                self.base_url,
                params=params,
                proxies=get_proxies(),
                timeout=10  # Increased from 5s to handle slow network conditions
            )
            response.raise_for_status()
            
            data = response.json()
            if 'Answer' not in data:
                logger.warning(f"DoH resolution failed for {hostname}: {data}")
                return []
            
            # Extract IP addresses from the response    
            answers = [answer['data'] for answer in data['Answer'] 
                    if answer.get('type') == (28 if record_type == 'AAAA' else 1)]
            
            # Cache the result
            self._set_cached(hostname, record_type, answers)
            
            # Don't log here - the caller (custom_getaddrinfo) will log the final result
            return answers
            
        except Exception as e:
            logger.warning(f"DoH resolution failed for {hostname}: {e}")
            return []

def create_custom_resolver(servers: Optional[List[str]] = None):
    """Create a custom DNS resolver using the specified or configured DNS servers."""
    custom_resolver = dns.resolver.Resolver()
    custom_resolver.nameservers = servers if servers is not None else CUSTOM_DNS
    return custom_resolver

def resolve_with_custom_dns(resolver, hostname: str, record_type: str) -> List[str]:
    """Resolve hostname using custom DNS resolver."""
    try:
        answers = resolver.resolve(hostname, record_type)
        return [str(answer) for answer in answers]
    except Exception:
        # Don't log here - let the caller handle it to prevent spam
        # Don't trigger DNS switch here either - caller handles it
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
        
        def _log_results(source: str, provider_label: str, res: Sequence[Tuple[AddressFamily, SocketKind, int, str, Tuple[Any, ...]]], is_bypass: bool = False) -> None:
            """Emit a unified resolver log with the IPs returned.
            
            Args:
                source: Description of resolver source
                provider_label: Label for the DNS provider
                res: Resolution results
                is_bypass: If True, log at DEBUG level (for local/IP addresses)
            """
            # Skip logging entirely for localhost to reduce noise
            if host_str in ('localhost', '127.0.0.1', '::1'):
                return
            try:
                ips = [entry[4][0] for entry in res if len(entry) >= 5 and entry[4]]
                msg = f"Resolved {host_str} via {source} [{provider_label}]: {ips}"
                if is_bypass:
                    logger.debug(msg)
                else:
                    logger.info(msg)
            except Exception:
                pass  # Silently ignore logging failures
        
        # Skip custom resolution for IP addresses, local addresses, or if skip check passes
        if _is_ip_address(host_str) or _is_local_address(host_str) or (skip_check and skip_check(host_str)):
            # Quietly bypass custom resolution for IP/local targets
            res = original_getaddrinfo(host, port, family, type, proto, flags)
            _log_results("system resolver (bypass)", "system", res, is_bypass=True)
            return res
        
        results: list[Tuple[AddressFamily, SocketKind, int, str, Tuple[Any, ...]]] = []
        
        try:
            # Try IPv4 (IPv6 disabled to avoid noisy AAAA failures)
            if family == 0 or family == socket.AF_INET:
                ipv4_answers = resolve_ipv4(host_str)
                for answer in ipv4_answers:
                    results.append((socket.AF_INET, cast(SocketKind, type), proto, '', (answer, port_int)))
            
            if results:
                _log_results("custom resolver", _current_dns_label(), results)
                return results
                
        except Exception as e:
            logger.warning(f"Custom DNS resolution failed for {host_str}: {e}, falling back to system DNS")
            # Trigger DNS switch on failure (if auto mode)
            if _is_auto_dns_mode() and not _is_local_address(host_str) and not _is_ip_address(host_str):
                # Only switch if we haven't exhausted all providers
                if _current_dns_index < len(DNS_PROVIDERS):
                    logger.info(f"Requesting DNS provider switch after custom resolver failure for {host_str}")
                    switch_dns_provider()
        
        # Fall back to system DNS if custom resolution fails
        logger.info(f"Custom DNS returned no addresses for {host_str}; falling back to system resolver")
        try:
            res = original_getaddrinfo(host, port, family, type, proto, flags)
            _log_results("system resolver (fallback)", "system", res)
            return res
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
    _switch_logged: set[str] = set()
    
    def system_failover_getaddrinfo(
        host: Union[str, bytes, None],
        port: Union[str, bytes, int, None],
        family: int = 0,
        type: int = 0,
        proto: int = 0,
        flags: int = 0
    ) -> Sequence[Tuple[AddressFamily, SocketKind, int, str, Tuple[Any, ...]]]:
        host_str = _decode_host(host)
        try:
            return original_getaddrinfo(host, port, family, type, proto, flags)
        except Exception as e:
            if host_str not in _switch_logged:
                logger.warning(f"System DNS resolution failed for {host_str}: {e}")
            
            # Trigger DNS switch only in auto mode for non-local targets
            if _is_auto_dns_mode() and not _is_ip_address(host_str) and not _is_local_address(host_str):
                if _current_dns_index + 1 < len(DNS_PROVIDERS):
                    if host_str not in _switch_logged:
                        logger.info(f"Switching DNS provider after system DNS failure for {host_str}")
                        _switch_logged.add(host_str)
                    if switch_dns_provider():
                        return socket.getaddrinfo(host, port, family, type, proto, flags)
            raise
    
    return system_failover_getaddrinfo

def _init_doh_resolver_internal(doh_server: str) -> DoHResolver:
    """Internal: Initialize DNS over HTTPS resolver with specified server.
    
    Args:
        doh_server: The DoH server URL
        
    Returns:
        Configured DoHResolver instance
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


def _init_custom_resolver_internal(servers: List[str]):
    """Internal: Initialize custom DNS resolver with specified servers.
    
    Args:
        servers: List of DNS server IPs to use
    """
    custom_resolver = create_custom_resolver(servers)
    
    # Create resolver functions
    def resolve_ipv4(hostname: str) -> List[str]:
        return resolve_with_custom_dns(custom_resolver, hostname, 'A')
    
    def resolve_ipv6(hostname: str) -> List[str]:
        return resolve_with_custom_dns(custom_resolver, hostname, 'AAAA')
    
    # Replace socket.getaddrinfo with our custom resolver
    socket.getaddrinfo = cast(Any, create_custom_getaddrinfo(resolve_ipv4, resolve_ipv6))
    
    logger.info("Custom DNS resolver successfully configured and activated")
    return custom_resolver


def init_doh_resolver(doh_server: str = ""):
    """Initialize DNS over HTTPS resolver."""
    server = doh_server or DOH_SERVER
    if not server:
        return None
    return _init_doh_resolver_internal(server)


def init_custom_resolver():
    """Initialize custom DNS resolver using configured DNS servers."""
    if not CUSTOM_DNS:
        return None
    return _init_custom_resolver_internal(CUSTOM_DNS)

def switch_dns_provider() -> bool:
    """Switch to next DNS provider (auto mode only)."""
    global CUSTOM_DNS, DOH_SERVER, _current_dns_index, _dns_exhausted_logged

    if not _is_auto_dns_mode():
        return False

    with _dns_switch_lock:
        if _current_dns_index + 1 >= len(DNS_PROVIDERS):
            if not _dns_exhausted_logged:
                logger.warning("All DNS providers exhausted, staying with current")
                _dns_exhausted_logged = True
            return False

        _current_dns_index += 1
        name, servers, doh = DNS_PROVIDERS[_current_dns_index]
        CUSTOM_DNS = servers
        DOH_SERVER = doh
        config.CUSTOM_DNS = servers
        config.DOH_SERVER = doh

        logger.warning(f"Switched DNS provider to: {name} (using DoH)")
        _save_state(dns_provider=name)
        init_dns_resolvers()

        # Notify listeners (e.g., Chrome bypasser) to restart with new DNS
        _notify_dns_rotation(name, servers, doh)
        return True


def rotate_dns_provider() -> bool:
    """Rotate DNS provider (auto mode only), cycling back if exhausted."""
    global _current_dns_index, _dns_exhausted_logged

    if not _is_auto_dns_mode():
        return False

    if _current_dns_index + 1 >= len(DNS_PROVIDERS):
        logger.warning("DNS rotation: cycling back to first provider")
        _current_dns_index = -1
        _dns_exhausted_logged = False

    return switch_dns_provider()

def rotate_dns_and_reset_aa() -> bool:
    """
    Switch DNS provider (auto mode) and reset AA URL list to the first entry.
    Returns True if DNS switched; False if no providers left or not in auto mode.
    
    Note: This function can be called during initialization, so we must NOT call
    _ensure_initialized() here to avoid recursive init loops.
    """
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

def set_dns_provider(provider: str, manual_servers: list[str] | None = None, use_doh: bool | None = None) -> bool:
    """
    Set DNS to a specific provider or manual servers.

    Args:
        provider: One of 'auto', 'system', 'google', 'cloudflare', 'quad9', 'opendns', 'manual'
        manual_servers: List of DNS server IPs when provider is 'manual'
        use_doh: Whether to use DNS over HTTPS. If None, uses current USE_DOH config setting.
                 Note: Auto mode always uses DoH for reliability during rotation.

    Returns:
        True if DNS was changed successfully.
    """
    global CUSTOM_DNS, DOH_SERVER, _current_dns_index, _dns_exhausted_logged

    provider = provider.lower().strip()

    # Determine DoH preference - use provided value or fall back to config setting
    doh_enabled = use_doh if use_doh is not None else app_config.get("USE_DOH", True)

    with _dns_switch_lock:
        if provider == "system":
            # Use system DNS only - no custom resolver, no failover rotation
            _current_dns_index = -1
            _dns_exhausted_logged = False
            CUSTOM_DNS = []
            DOH_SERVER = ""
            config.CUSTOM_DNS = []
            config.DOH_SERVER = ""
            # Restore original system getaddrinfo
            socket.getaddrinfo = original_getaddrinfo
            logger.info("DNS set to system mode (using OS default resolver)")
            _notify_dns_rotation("system", [], "")
            return True

        if provider == "auto":
            # Reset to auto mode - start with system DNS
            # Note: Auto mode always uses DoH when rotating for reliability
            _current_dns_index = -1
            _dns_exhausted_logged = False
            CUSTOM_DNS = []
            DOH_SERVER = ""
            config.CUSTOM_DNS = []
            config.DOH_SERVER = ""
            logger.info("DNS set to auto mode (system DNS, will rotate on failure with DoH)")
            init_dns_resolvers()
            _notify_dns_rotation("auto", [], "")
            return True

        if provider == "manual":
            if not manual_servers:
                logger.warning("Manual DNS requested but no servers provided")
                return False
            _current_dns_index = -1  # Not using preset providers
            CUSTOM_DNS = manual_servers
            DOH_SERVER = ""  # No DoH for manual servers
            config.CUSTOM_DNS = manual_servers
            config.DOH_SERVER = ""
            logger.info(f"DNS set to manual servers: {manual_servers}")
            init_dns_resolvers()
            _notify_dns_rotation("manual", manual_servers, "")
            return True

        # Find the provider in DNS_PROVIDERS
        for i, (name, servers, doh) in enumerate(DNS_PROVIDERS):
            if name == provider:
                _current_dns_index = i
                _dns_exhausted_logged = False
                CUSTOM_DNS = servers
                # Only set DoH server if DoH is enabled
                DOH_SERVER = doh if doh_enabled else ""
                config.CUSTOM_DNS = servers
                config.DOH_SERVER = DOH_SERVER
                doh_status = "DoH enabled" if doh_enabled else "standard DNS"
                logger.info(f"DNS set to: {name} ({doh_status})")
                _save_state(dns_provider=name)
                init_dns_resolvers()
                _notify_dns_rotation(name, servers, DOH_SERVER)
                return True

        logger.warning(f"Unknown DNS provider: {provider}")
        return False


def init_dns_resolvers():
    """Initialize DNS resolvers based on configuration."""
    global CUSTOM_DNS, DOH_SERVER
    
    if _is_auto_dns_mode():
        if _current_dns_index >= 0:
            name, servers, doh = DNS_PROVIDERS[_current_dns_index]
            CUSTOM_DNS = servers
            DOH_SERVER = doh
            config.CUSTOM_DNS = servers
            config.DOH_SERVER = doh
            logger.info(f"Using DNS provider: {name} (DoH enabled)")
        else:
            CUSTOM_DNS = []
            DOH_SERVER = ""
            config.CUSTOM_DNS = []
            config.DOH_SERVER = ""
            logger.debug("Using system DNS (auto mode - will switch on failure)")
            socket.getaddrinfo = cast(Any, create_system_failover_getaddrinfo())
            return
    
    if CUSTOM_DNS:
        init_custom_resolver()
        if DOH_SERVER:
            init_doh_resolver(DOH_SERVER)


def _get_initial_dns_config() -> tuple[str, List[str] | None, bool]:
    """
    Determine initial DNS configuration from config singleton.

    The config singleton already handles ENV > config file > default priority,
    so we just read from config.

    Returns:
        Tuple of (provider, manual_servers, use_doh)
    """
    provider = str(app_config.get("CUSTOM_DNS", "auto")).lower().strip()
    use_doh = app_config.get("USE_DOH", True)
    manual_servers = None

    # Check for manual DNS servers in config
    if provider == "manual":
        manual_dns = str(app_config.get("CUSTOM_DNS_MANUAL", "")).strip()
        if manual_dns:
            manual_servers = [s.strip() for s in manual_dns.split(",") if s.strip()]

    # Handle legacy format: IPs directly in CUSTOM_DNS setting
    if provider and provider not in ("auto", "system", "google", "cloudflare", "quad9", "opendns", "manual", ""):
        # Check if it looks like IP addresses
        parts = provider.split(",")
        potential_ips = [p.strip() for p in parts if p.strip()]
        if potential_ips and all(_looks_like_ip(p) for p in potential_ips):
            manual_servers = potential_ips
            provider = "manual"
            logger.info(f"Detected legacy DNS format, treating as manual: {manual_servers}")

    return provider or "auto", manual_servers, use_doh


def _looks_like_ip(s: str) -> bool:
    """Check if a string looks like an IP address."""
    # Simple heuristic: contains only digits, dots, and colons
    return s.replace(".", "").replace(":", "").isdigit()

def _initialize_aa_state() -> None:
    """Restore or probe AA URL state."""
    global AA_BASE_URL, _current_aa_url_index
    if AA_BASE_URL == "auto":
        if state.get('aa_base_url') and state['aa_base_url'] in _aa_urls:
            _current_aa_url_index = _aa_urls.index(state['aa_base_url'])
            AA_BASE_URL = state['aa_base_url']
        else:
            logger.debug(f"AA_BASE_URL: auto, checking available urls {_aa_urls}")
            for i, url in enumerate(_aa_urls):
                try:
                    response = requests.get(url, proxies=get_proxies(), timeout=3)
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
    """Initialize DNS state and resolvers using set_dns_provider() for consistency."""
    global state, _dns_initialized, _current_dns_index
    if _dns_initialized and not force:
        return
    with _init_lock:
        # Double-check after acquiring lock
        if _dns_initialized and not force:
            return
        # Do work first, set flag after to prevent race conditions
        try:
            logger.debug(f"Initializing DNS (using {'gevent' if _using_gevent_locks else 'threading'} locks)")
            state = _load_state()

            # Get initial DNS configuration from environment
            provider, manual_servers, use_doh = _get_initial_dns_config()

            if provider == "auto":
                # Auto mode: check for persisted provider from previous rotation
                persisted = state.get('dns_provider') if state else None
                if persisted:
                    for i, (name, _, _) in enumerate(DNS_PROVIDERS):
                        if name == persisted:
                            _current_dns_index = i
                            logger.info(f"Restored DNS provider from state: {name}")
                            break
                # Use init_dns_resolvers() for auto mode to preserve rotation capability
                init_dns_resolvers()
            else:
                # Non-auto mode: use set_dns_provider() for consistent initialization
                set_dns_provider(provider, manual_servers, use_doh=use_doh)

            # Only set flag AFTER work completes successfully
            _dns_initialized = True
        except Exception:
            # Flag stays False so retry is possible
            raise

def init_aa(force: bool = False) -> None:
    """Initialize AA mirror selection."""
    global state, _aa_initialized
    if _aa_initialized and not force:
        return
    with _init_lock:
        # Double-check after acquiring lock
        if _aa_initialized and not force:
            return
        # Do work first, set flag after to prevent race conditions
        try:
            state = _load_state()
            _initialize_aa_state()
            # Only set flag AFTER work completes successfully
            _aa_initialized = True
        except Exception:
            # Flag stays False so retry is possible
            raise

def init(force: bool = False) -> None:
    """
    Initialize network state (DNS resolvers and AA mirror selection).

    Called lazily on first network operation. Safe to call repeatedly;
    later calls no-op unless force=True.
    """
    global _initialized
    if _initialized and not force:
        return
    with _init_lock:
        # Double-check after acquiring lock
        if _initialized and not force:
            return
        # Do the work first, then set flag to prevent race conditions
        # where another thread sees _initialized=True but AA_BASE_URL is still "auto"
        try:
            init_dns(force=force)
            init_aa(force=force)
            # Only set flag AFTER work completes successfully
            _initialized = True
        except Exception:
            # Flag stays False so retry is possible
            raise

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
