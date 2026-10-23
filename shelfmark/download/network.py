"""DNS rotation, mirror selection, and network utilities."""

import fnmatch
import ipaddress
import socket
import time
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from socket import AddressFamily, SocketKind
from typing import TYPE_CHECKING, Any, NamedTuple, cast

import dns.resolver
import httpx
import requests
from dns.exception import DNSException

from shelfmark.core.config import config as app_config
from shelfmark.core.logger import setup_logger
from shelfmark.core.request_helpers import coerce_bool, normalize_optional_text
from shelfmark.core.utils import normalize_http_url

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence


def _get_no_proxy_patterns() -> list[str]:
    """Get list of NO_PROXY patterns from config."""
    no_proxy = normalize_optional_text(app_config.get("NO_PROXY", ""))
    if not no_proxy:
        return []
    return [p.strip().lower() for p in no_proxy.split(",") if p.strip()]


def should_bypass_proxy(url: str) -> bool:
    """Check if a URL should bypass the proxy based on NO_PROXY patterns.

    Supports:
    - Exact hostname match: localhost, myhost.local
    - Wildcard prefix: *.local matches foo.local
    - Wildcard suffix: 10.* matches 10.1.2.3
    """
    if not url:
        return False

    patterns = _get_no_proxy_patterns()
    if not patterns:
        return False

    # Extract hostname from URL
    try:
        parsed = urllib.parse.urlparse(url)
        hostname = (parsed.hostname or "").lower()
    except ValueError as e:
        logger.debug("Failed to parse URL for proxy bypass check: %s", e)
        return False

    if not hostname:
        return False

    return any(fnmatch.fnmatch(hostname, pattern) for pattern in patterns)


def get_proxies(url: str = "") -> dict:
    """Get current proxy configuration from config singleton.

    Args:
        url: Optional URL to check against NO_PROXY patterns.
             If provided and matches a pattern, returns empty dict.

    """
    # Check NO_PROXY bypass first
    if url and should_bypass_proxy(url):
        return {}

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


def get_ssl_verify(url: str = "") -> bool:
    """Return the ``verify`` value for outbound requests.

    - ``enabled``        → always ``True``
    - ``disabled_local`` → ``False`` for local/private addresses, ``True`` otherwise
    - ``disabled``       → always ``False``
    """
    mode = app_config.get("CERTIFICATE_VALIDATION", "enabled")

    if mode == "disabled":
        return False

    if mode == "disabled_local" and url:
        try:
            parsed = urllib.parse.urlparse(url)
            hostname = parsed.hostname or ""
            if hostname and _is_local_address(hostname):
                return False
        except ValueError:
            pass

    return True


_ssl_warnings_suppressed = False


def _apply_ssl_warning_suppression() -> None:
    """Suppress or restore urllib3 InsecureRequestWarning.

    Called once at init and again whenever the setting changes via the UI.
    Only modifies warning filters when the mode is not 'enabled', so the
    default case is a complete no-op (zero behavioural change for users who
    never touch the setting).
    """
    global _ssl_warnings_suppressed
    import urllib3

    mode = app_config.get("CERTIFICATE_VALIDATION", "enabled")
    if mode in ("disabled", "disabled_local"):
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        _ssl_warnings_suppressed = True
        logger.debug("SSL warnings suppressed (certificate validation: %s)", mode)
    elif _ssl_warnings_suppressed:
        import warnings

        warnings.simplefilter("default", urllib3.exceptions.InsecureRequestWarning)
        _ssl_warnings_suppressed = False
        logger.debug("SSL warnings restored (certificate validation: enabled)")


# DNS state - authoritative values managed by this module
# Other modules should use get_dns_config() to read these
CUSTOM_DNS: list[str] = []
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
_GETADDRINFO_SOCKADDR_INDEX = 4


def _call_dns_rotation_callback(
    callback: Callable[[str, list[str], str], None],
    provider_name: str,
    servers: list[str],
    doh_url: str,
) -> None:
    """Call one DNS rotation callback and log failures."""
    try:
        logger.debug("Calling DNS rotation callback: %s", callback.__name__)
        callback(provider_name, servers, doh_url)
    except (OSError, RuntimeError, TypeError, ValueError) as e:
        logger.warning("DNS rotation callback %s failed: %s", callback.__name__, e)


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
_dns_rotation_callbacks: list[Callable[[str, list[str], str], None]] = []
_dns_callback_lock = _RLock()


def register_dns_rotation_callback(callback: Callable[[str, list[str], str], None]) -> None:
    """Register a callback to be called when DNS provider rotates.

    The callback receives (provider_name, servers, doh_url) as arguments.
    Use this to restart components that cache DNS resolution (e.g., Chrome).
    """
    with _dns_callback_lock:
        if callback not in _dns_rotation_callbacks:
            _dns_rotation_callbacks.append(callback)
            logger.debug("Registered DNS rotation callback: %s", callback.__name__)


def unregister_dns_rotation_callback(callback: Callable[[str, list[str], str], None]) -> None:
    """Unregister a previously registered DNS rotation callback."""
    with _dns_callback_lock:
        if callback in _dns_rotation_callbacks:
            _dns_rotation_callbacks.remove(callback)
            logger.debug("Unregistered DNS rotation callback: %s", callback.__name__)


def _notify_dns_rotation(provider_name: str, servers: list[str], doh_url: str) -> None:
    """Notify all registered callbacks about DNS rotation."""
    with _dns_callback_lock:
        callbacks = _dns_rotation_callbacks.copy()

    for callback in callbacks:
        _call_dns_rotation_callback(callback, provider_name, servers, doh_url)


def _load_state() -> dict[str, Any]:
    """Return current in-memory network state (no disk persistence)."""
    if state.get("chosen_at"):
        chosen = datetime.fromisoformat(state["chosen_at"])
        if chosen.tzinfo is None:
            chosen = chosen.replace(tzinfo=UTC)
        if datetime.now(UTC) - chosen > timedelta(days=STATE_TTL_DAYS):
            state.clear()
    return state


def _save_state(aa_url: str | None = None, dns_provider: str | None = None) -> None:
    """Update in-memory network state (no disk persistence)."""
    if aa_url:
        state["aa_base_url"] = aa_url
    if dns_provider:
        state["dns_provider"] = dns_provider
    state["chosen_at"] = datetime.now(UTC).isoformat()


def _set_runtime_dns_state(servers: list[str], doh_server: str) -> None:
    """Update the module DNS state and mirrored config attributes.

    The config singleton's `get()` values still represent persisted/configured
    settings. These attribute writes are only for runtime consumers that read
    the live resolver state via attribute access.
    """
    global CUSTOM_DNS, DOH_SERVER

    CUSTOM_DNS = list(servers)
    DOH_SERVER = doh_server
    runtime_config = cast(Any, app_config)
    runtime_config.CUSTOM_DNS = CUSTOM_DNS
    runtime_config.DOH_SERVER = DOH_SERVER


def _get_configured_aa_url() -> str:
    """Return the configured AA base URL normalized for runtime use."""
    configured_url = normalize_http_url(
        normalize_optional_text(app_config.get("AA_BASE_URL", "auto")),
        default_scheme="https",
        allow_special=("auto",),
    )
    return configured_url or "auto"


# AA URL failover state
_current_aa_url_index = 0
_aa_urls: list[str] = []  # Initialized lazily in _initialize_aa_state()
_aa_base_url: str = ""  # Current active AA URL

# Mirrors quarantined for this process: domains that are not a working AA mirror at
# all (NXDOMAIN, refused, or a 200 that isn't AA - seized/parked/for-sale domains all
# land here). Kept separate from ordinary failures: a 403 challenge or a 5xx means the
# mirror is alive and rotating away from it only discards the DDoS-Guard clearance we
# hold for it. Deliberately in-memory only, so a restart re-probes everything.
_dead_aa_urls: set[str] = set()
_dead_aa_urls_lock = _RLock()


# Per-host rate-limit backoff. A 429 is the origin throttling *this IP*, not a challenge:
# a DDoS-Guard/Cloudflare solve still renders, so the bypass "succeeds" yet the cleared
# request is rejected again and the throttle is only renewed. The single answer is to
# wait, so a 429 sidelines the host for a growing window - mirror selection and the
# bypasser both skip a cooling-down host until its deadline passes. The wait escalates
# 2 -> 5 -> 10 -> 15 -> 30 minutes each time the host throttles us again *after* we
# already waited a full window out; a host left clear for longer than the top step
# starts the ladder over. Keyed by host so every mirror and source shares one view;
# in-memory only, so a restart starts clean.
_RATE_LIMIT_COOLDOWN_LADDER_SECONDS: tuple[float, ...] = (120.0, 300.0, 600.0, 900.0, 1800.0)
# A host that has been clear this long is treated as a fresh episode: the next 429
# restarts the ladder at 2 minutes rather than resuming the escalation.
_RATE_LIMIT_RESET_AFTER_SECONDS = 1800.0


class _Cooldown(NamedTuple):
    """One host's active rate-limit window and how far up the ladder it has climbed."""

    deadline: float  # time.monotonic() value at which the wait expires
    level: int  # index into _RATE_LIMIT_COOLDOWN_LADDER_SECONDS


_host_cooldowns: dict[str, _Cooldown] = {}
_host_cooldowns_lock = _RLock()


class RateLimitedError(Exception):
    """Raised to abandon a request whose host is in a 429 cooldown.

    Not a transport failure - nothing is wrong with the network, the origin is
    throttling this IP and only time clears it. Callers surface it as a plain failure
    rather than retrying or handing the URL to the bypasser.
    """


def _cooldown_key(url: str) -> str:
    """Host a cooldown is keyed by; '' when the URL carries none."""
    return (urllib.parse.urlparse(url).hostname or "").lower()


def note_rate_limited(url: str) -> float:
    """Escalate a host's 429 backoff and (re)arm its cooldown; return the wait applied.

    The step advances only when a fresh 429 arrives *after* the previous window already
    elapsed - i.e. we waited it out and the host throttled us again. A 429 that lands
    while the host is still cooling is the same episode: it neither escalates the level
    nor shortens the wait. See the ladder note above.
    """
    host = _cooldown_key(url)
    if not host:
        return 0.0
    now = time.monotonic()
    ladder = _RATE_LIMIT_COOLDOWN_LADDER_SECONDS
    with _host_cooldowns_lock:
        prev = _host_cooldowns.get(host)
        if prev is not None and now < prev.deadline:
            # Still inside the current window - same throttling episode, leave it be.
            return prev.deadline - now
        if prev is None or now - prev.deadline > _RATE_LIMIT_RESET_AFTER_SECONDS:
            level = 0
        else:
            level = min(prev.level + 1, len(ladder) - 1)
        wait = ladder[level]
        _host_cooldowns[host] = _Cooldown(deadline=now + wait, level=level)
    logger.info(
        "Rate limited (429): backing off %s for %.0fs (step %d/%d)",
        host,
        wait,
        level + 1,
        len(ladder),
    )
    return wait


def host_cooldown_remaining(url: str) -> float:
    """Seconds left on a host's 429 cooldown; 0.0 when clear or expired.

    Leaves an expired record in place: the ladder level it carries is what a later 429
    escalates from (or resets, once the clear gap is long enough).
    """
    host = _cooldown_key(url)
    if not host:
        return 0.0
    now = time.monotonic()
    with _host_cooldowns_lock:
        rec = _host_cooldowns.get(host)
        if rec is None or rec.deadline <= now:
            return 0.0
        return rec.deadline - now


def is_host_cooling_down(url: str) -> bool:
    """True while ``url``'s host is inside its 429 cooldown window."""
    return host_cooldown_remaining(url) > 0.0


def clear_host_cooldowns() -> None:
    """Forget all rate-limit cooldowns (manual reset / tests)."""
    with _host_cooldowns_lock:
        _host_cooldowns.clear()


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

# httpx raises its own hierarchy, which shares no base class with requests', so a
# wireformat failure would escape a requests-only except clause.
_DOH_REQUEST_ERRORS = (OSError, ValueError, requests.RequestException, httpx.HTTPError)


def _first_proxy(proxies: dict[str, str] | None) -> str | None:
    """Pick a single proxy URL from a requests-style mapping, for httpx."""
    if not proxies:
        return None
    return proxies.get("https") or proxies.get("http") or None


# DoH providers that speak RFC 8484 wireformat rather than the (non-standard) JSON API
# Cloudflare and Google popularised. Verified against the live services: both reject a
# ?name=&type= query outright - Quad9 with 505 (it also mandates HTTP/2 per RFC 8484
# section 5.2, which requests cannot speak), OpenDNS with 400 "No valid query received".
_DOH_WIREFORMAT_HOSTS = frozenset({"dns.quad9.net", "doh.opendns.com"})

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
    """Get the current DNS configuration.

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
def _decode_host(host: str | bytes | None) -> str:
    """Convert host to string, handling bytes and None cases."""
    if host is None:
        return ""
    if isinstance(host, bytes):
        return host.decode("utf-8")
    return str(host)


def _decode_port(port: str | bytes | int | None) -> int:
    """Convert port to integer, handling various input types."""
    if port is None:
        return 0
    return int(port)


def _is_local_address(host_str: str) -> bool:
    """Check if an address is local/private and should bypass custom DNS.

    Returns True for:
    - 'localhost'
    - Private/loopback/link-local IP addresses
    - Simple hostnames without a dot (e.g., 'booklore', 'prowlarr') - likely Docker service names
    - Hostnames ending in common internal TLDs (.local, .internal, .lan, .home, .docker)
    """
    if not host_str:
        return False

    host_lower = host_str.lower()

    # Check for localhost
    if host_lower == "localhost":
        return True

    # Check for simple hostnames (no dot = likely internal Docker/container name)
    if "." not in host_str:
        return True

    # Check for common internal TLDs
    internal_tlds = (".local", ".internal", ".lan", ".home", ".docker", ".localdomain")
    if any(host_lower.endswith(tld) for tld in internal_tlds):
        return True

    # Check for private/loopback/link-local IP addresses
    try:
        addr = ipaddress.ip_address(host_str)
    except ValueError:
        return False
    else:
        return addr.is_private or addr.is_loopback or addr.is_link_local


class UnsafeDestinationError(requests.exceptions.InvalidURL):
    """A request target resolves to a loopback, private or otherwise non-public address."""


def _address_is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global


def _system_getaddrinfo(hostname: str, port: None) -> list[Any]:
    return original_getaddrinfo(hostname, port)


def hostname_resolves_publicly(
    hostname: str, *, allow_unresolved: bool = False, use_custom_dns: bool = False
) -> bool:
    """Return True when every address ``hostname`` resolves to is globally routable.

    A name that does not resolve counts as public only with ``allow_unresolved``, so the
    caller's own request fails on it the way it always did. The check asks the system
    resolver unless ``use_custom_dns`` is set: a failed lookup through the patched
    resolver switches the DNS provider, and a pre-flight check must not do that.
    """
    resolver = socket.getaddrinfo if use_custom_dns else _system_getaddrinfo
    try:
        return _address_is_public(hostname)
    except ValueError:
        pass  # not an IP literal, resolve it
    try:
        resolved = resolver(hostname, None)
        return all(_address_is_public(str(sockaddr[0])) for *_, sockaddr in resolved)
    except socket.gaierror:
        return allow_unresolved
    except ValueError:
        return False


def _configured_mirror_hosts() -> set[str]:
    """Hostnames of every mirror the operator configured, which are trusted as given."""
    from shelfmark.core import mirrors

    pinned_aa_url = mirrors.get_pinned_aa_base_url()
    urls = [
        *mirrors.get_aa_mirrors(),
        *([pinned_aa_url] if pinned_aa_url else []),
        *mirrors.get_libgen_mirrors(),
        *mirrors.get_zlib_mirrors(),
        *mirrors.get_welib_mirrors(),
        *mirrors.get_oceanofpdf_mirrors(),
    ]
    return {(urllib.parse.urlparse(url).hostname or "").lower() for url in urls}


def ensure_public_destination(url: str) -> None:
    """Refuse a URL whose host resolves to a non-public address.

    Direct-download pages and file links come from remote sites or from API callers, so a
    hostile value could otherwise aim the server at loopback, the LAN or a sibling
    container. A host the operator configured as a mirror is allowed as written, and so is
    a name that does not resolve (the request fails on it normally).
    """
    parsed = urllib.parse.urlparse(url)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme not in ("http", "https") or not hostname:
        msg = f"Refusing to request {url!r}: not an http(s) URL with a host"
        raise UnsafeDestinationError(msg)
    if hostname in _configured_mirror_hosts():
        return
    if not hostname_resolves_publicly(hostname, allow_unresolved=True):
        msg = f"Refusing to request {hostname!r}: it resolves to a non-public address"
        raise UnsafeDestinationError(msg)


def _guard_redirect_hop(response: requests.Response, *_args: object, **_kwargs: object) -> None:
    """Response hook: check where a redirect points before requests follows it."""
    if response.is_redirect:
        location = response.headers.get("Location", "")
        if location:
            ensure_public_destination(urllib.parse.urljoin(response.url, location))


# Pass as ``hooks=`` so every redirect hop of a request is checked, not just its first URL.
REDIRECT_GUARD_HOOKS: dict[str, Any] = {"response": [_guard_redirect_hop]}


def _is_ip_address(host_str: str) -> bool:
    """Check if a string is a valid IP address (IPv4 or IPv6)."""
    try:
        ipaddress.ip_address(host_str)
    except ValueError:
        return False
    else:
        return True


def _aa_hostnames() -> list[str]:
    """Return hostname portions for all configured AA URLs."""
    return [
        parsed.hostname
        for parsed in (urllib.parse.urlparse(url) for url in _aa_urls)
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

    def __init__(self, provider_url: str, hostname: str, ip: str) -> None:
        """Initialize DoH resolver with specified provider."""
        self.base_url = provider_url.lower().strip()
        self.hostname = hostname  # Store the hostname for hostname-based skipping
        self.ip = ip  # Store IP for direct connections
        self.session = requests.Session()
        # DNS cache: {(hostname, record_type): (ip_list, timestamp)}
        self._cache: dict[tuple[str, str], tuple[list[str], datetime]] = {}

        # RFC 8484 providers get a separate transport: they need wireformat, and Quad9
        # additionally refuses HTTP/1.1, which requests has no way to upgrade from.
        self.use_wireformat = urllib.parse.urlparse(self.base_url).hostname in (
            _DOH_WIREFORMAT_HOSTS
        )
        self._http2_client: Any | None = None

        if self.use_wireformat:
            self.session.headers.update({"Accept": "application/dns-message"})
        elif "google" in self.base_url:
            self.session.headers.update(
                {
                    "Accept": "application/json",
                }
            )
        else:
            self.session.headers.update(
                {
                    "Accept": "application/dns-json",
                }
            )

    def _get_http2_client(self) -> Any:
        """Lazily build the HTTP/2 client used for RFC 8484 providers.

        Built on first use so a resolver pointed at a JSON provider never opens an
        HTTP/2 connection pool it will not use.
        """
        if self._http2_client is None:
            self._http2_client = httpx.Client(
                http2=True,
                timeout=10,
                verify=get_ssl_verify(self.base_url),
                proxy=_first_proxy(get_proxies(self.base_url)),
            )
        return self._http2_client

    def _resolve_wireformat(self, hostname: str, record_type: str) -> list[str]:
        """Resolve via RFC 8484: base64url query in, DNS message out."""
        from shelfmark.download import doh_wireformat

        qtype = doh_wireformat.TYPE_AAAA if record_type == "AAAA" else doh_wireformat.TYPE_A
        param = doh_wireformat.encode_query_param(hostname, qtype)
        response = self._get_http2_client().get(
            self.base_url,
            params={"dns": param},
            headers={"Accept": "application/dns-message"},
        )
        response.raise_for_status()
        return doh_wireformat.decode_answer(response.content, qtype)

    def _get_cached(self, hostname: str, record_type: str) -> list[str] | None:
        """Get cached DNS result if still valid."""
        key = (hostname, record_type)
        if key in self._cache:
            ips, timestamp = self._cache[key]
            if datetime.now(UTC) - timestamp < timedelta(seconds=self.CACHE_TTL):
                logger.debug("DoH cache hit for %s: %s", hostname, ips)
                return ips
            # Cache expired, remove it
            del self._cache[key]
        return None

    def _set_cached(self, hostname: str, record_type: str, ips: list[str]) -> None:
        """Cache DNS result."""
        if ips:  # Only cache non-empty results
            self._cache[(hostname, record_type)] = (ips, datetime.now(UTC))

    def resolve(self, hostname: str, record_type: str) -> list[str]:
        """Resolve a hostname using DoH.

        Args:
            hostname: The hostname to resolve
            record_type: The DNS record type (A or AAAA)

        Returns:
            List of resolved IP addresses

        """
        # Check if hostname is already an IP address, no need to resolve
        if _is_ip_address(hostname):
            logger.debug("Skipping DoH resolution for IP address: %s", hostname)
            return [hostname]

        # Check if hostname is a private IP address, and skip DoH if it is
        if _is_local_address(hostname):
            logger.debug("Skipping DoH resolution for private IP: %s", hostname)
            return [hostname]

        # Skip resolution for the DoH server itself to prevent recursion
        if hostname == self.hostname:
            logger.debug("Skipping DoH resolution for DoH server itself: %s", hostname)
            return [self.ip]

        # Check cache first
        cached = self._get_cached(hostname, record_type)
        if cached is not None:
            return cached

        try:
            if self.use_wireformat:
                answers = self._resolve_wireformat(hostname, record_type)
            else:
                params = {"name": hostname, "type": "AAAA" if record_type == "AAAA" else "A"}

                response = self.session.get(
                    self.base_url,
                    params=params,
                    proxies=get_proxies(self.base_url),
                    timeout=10,  # Increased from 5s to handle slow network conditions
                    verify=get_ssl_verify(self.base_url),
                )
                response.raise_for_status()

                data = response.json()
                if "Answer" not in data:
                    logger.warning("DoH resolution failed for %s: %s", hostname, data)
                    return []

                # Extract IP addresses from the response
                answers = [
                    answer["data"]
                    for answer in data["Answer"]
                    if answer.get("type") == (28 if record_type == "AAAA" else 1)
                ]

            # Cache the result
            self._set_cached(hostname, record_type, answers)

            # Don't log here - the caller (custom_getaddrinfo) will log the final result
        except _DOH_REQUEST_ERRORS as e:
            logger.warning("DoH resolution failed for %s: %s", hostname, e)
            return []
        else:
            return answers


def create_custom_resolver(
    servers: list[str] | None = None,
) -> dns.resolver.Resolver:
    """Create a custom DNS resolver using the specified or configured DNS servers."""
    custom_resolver = dns.resolver.Resolver()
    custom_resolver.nameservers = servers if servers is not None else CUSTOM_DNS
    return custom_resolver


def resolve_with_custom_dns(
    resolver: dns.resolver.Resolver, hostname: str, record_type: str
) -> list[str]:
    """Resolve hostname using custom DNS resolver."""
    try:
        answers = resolver.resolve(hostname, record_type)
        return [str(answer) for answer in answers]
    except DNSException:
        # Don't log here - let the caller handle it to prevent spam
        # Don't trigger DNS switch here either - caller handles it
        return []


def create_custom_getaddrinfo(
    resolve_ipv4: Callable[[str], list[str]],
    resolve_ipv6: Callable[[str], list[str]],
    skip_check: Callable[[str], bool] | None = None,
) -> Callable[
    [str | bytes | None, str | bytes | int | None, int, int, int, int],
    Sequence[tuple[AddressFamily, SocketKind, int, str, tuple[Any, ...]]],
]:
    """Create a custom getaddrinfo function that uses the provided resolvers.

    Args:
        resolve_ipv4: Function to resolve IPv4 addresses
        resolve_ipv6: Function to resolve IPv6 addresses
        skip_check: Optional function to check if custom resolution should be skipped

    Returns:
        A custom getaddrinfo function

    """

    def custom_getaddrinfo(
        host: str | bytes | None,
        port: str | bytes | int | None,
        family: int = 0,
        socket_type: int = 0,
        proto: int = 0,
        flags: int = 0,
    ) -> Sequence[tuple[AddressFamily, SocketKind, int, str, tuple[Any, ...]]]:
        host_str = _decode_host(host)
        port_int = _decode_port(port)

        def _log_results(
            source: str,
            provider_label: str,
            res: Sequence[tuple[AddressFamily, SocketKind, int, str, tuple[Any, ...]]],
        ) -> None:
            """Emit a unified resolver log with the IPs returned.

            Args:
                source: Description of resolver source
                provider_label: Label for the DNS provider
                res: Resolution results

            """
            # Skip logging entirely for localhost to reduce noise
            if host_str in ("localhost", "127.0.0.1", "::1"):
                return
            ips = []
            for entry in res:
                if not isinstance(entry, tuple) or len(entry) <= _GETADDRINFO_SOCKADDR_INDEX:
                    continue
                sockaddr = entry[_GETADDRINFO_SOCKADDR_INDEX]
                if not isinstance(sockaddr, tuple) or not sockaddr:
                    continue
                ip = sockaddr[0]
                if isinstance(ip, str):
                    ips.append(ip)
            logger.debug("Resolved %s via %s [%s]: %s", host_str, source, provider_label, ips)

        # Skip custom resolution for IP addresses, local addresses, or if skip check passes
        if (
            _is_ip_address(host_str)
            or _is_local_address(host_str)
            or (skip_check and skip_check(host_str))
        ):
            # Quietly bypass custom resolution for IP/local targets
            res = original_getaddrinfo(host, port, family, socket_type, proto, flags)
            _log_results("system resolver (bypass)", "system", res)
            return res

        results: list[tuple[AddressFamily, SocketKind, int, str, tuple[Any, ...]]] = []

        try:
            # Try IPv4 (IPv6 disabled to avoid noisy AAAA failures)
            if family in {0, socket.AF_INET}:
                ipv4_answers = resolve_ipv4(host_str)
                results.extend(
                    [
                        (
                            socket.AF_INET,
                            cast("SocketKind", socket_type),
                            proto,
                            "",
                            (answer, port_int),
                        )
                        for answer in ipv4_answers
                    ]
                )

            if results:
                _log_results("custom resolver", _current_dns_label(), results)
                return results

        except (
            DNSException,
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
            requests.RequestException,
        ) as e:
            logger.warning(
                "Custom DNS resolution failed for %s: %s, falling back to system DNS", host_str, e
            )
            # Trigger DNS switch on failure (if auto mode)
            if (
                _is_auto_dns_mode()
                and not _is_local_address(host_str)
                and not _is_ip_address(host_str)
                and _current_dns_index < len(DNS_PROVIDERS)
            ):
                logger.info(
                    "Requesting DNS provider switch after custom resolver failure for %s", host_str
                )
                switch_dns_provider()

        # Fall back to system DNS if custom resolution fails
        logger.info(
            "Custom DNS returned no addresses for %s; falling back to system resolver", host_str
        )
        try:
            res = original_getaddrinfo(host, port, family, socket_type, proto, flags)
            _log_results("system resolver (fallback)", "system", res)
        except OSError:
            logger.exception("System DNS resolution also failed for %s", host_str)
            # Last resort: Try to connect to the hostname directly
            if family in {0, socket.AF_INET}:
                logger.warning("Using direct hostname as last resort for %s", host_str)
                return [
                    (
                        socket.AF_INET,
                        cast("SocketKind", socket_type),
                        proto,
                        "",
                        (host_str, port_int),
                    )
                ]
            raise  # Re-raise the exception if we can't provide a last resort
        else:
            return res

    return custom_getaddrinfo


def create_system_failover_getaddrinfo() -> Callable[
    [str | bytes | None, str | bytes | int | None, int, int, int, int],
    Sequence[tuple[AddressFamily, SocketKind, int, str, tuple[Any, ...]]],
]:
    """Wrap system getaddrinfo to trigger DNS provider switch on failure."""
    _switch_logged: set[str] = set()

    def system_failover_getaddrinfo(
        host: str | bytes | None,
        port: str | bytes | int | None,
        family: int = 0,
        socket_type: int = 0,
        proto: int = 0,
        flags: int = 0,
    ) -> Sequence[tuple[AddressFamily, SocketKind, int, str, tuple[Any, ...]]]:
        host_str = _decode_host(host)
        try:
            return original_getaddrinfo(host, port, family, socket_type, proto, flags)
        except OSError as e:
            if host_str not in _switch_logged:
                logger.warning("System DNS resolution failed for %s: %s", host_str, e)

            # Trigger DNS switch only in auto mode for non-local targets
            if (
                _is_auto_dns_mode()
                and not _is_ip_address(host_str)
                and not _is_local_address(host_str)
                and _current_dns_index + 1 < len(DNS_PROVIDERS)
            ):
                if host_str not in _switch_logged:
                    logger.info("Switching DNS provider after system DNS failure for %s", host_str)
                    _switch_logged.add(host_str)
                if switch_dns_provider():
                    return socket.getaddrinfo(host, port, family, socket_type, proto, flags)
            raise

    return system_failover_getaddrinfo


def _init_doh_resolver_internal(doh_server: str) -> DoHResolver:
    """Initialize a DNS-over-HTTPS resolver for the given server.

    Args:
        doh_server: The DoH server URL

    Returns:
        Configured DoHResolver instance

    """
    # Pre-resolve the DoH server hostname to prevent recursion
    url = urllib.parse.urlparse(doh_server)
    server_hostname = url.hostname or ""

    # Use system DNS for DoH server to prevent circular dependencies
    try:
        # Temporarily restore original getaddrinfo to resolve DoH server
        temp_getaddrinfo = socket.getaddrinfo
        socket.getaddrinfo = original_getaddrinfo

        server_ip = socket.gethostbyname(server_hostname)
        logger.info("DoH server %s resolved to IP: %s", server_hostname, server_ip)

        # Restore custom getaddrinfo if it was previously set
        socket.getaddrinfo = temp_getaddrinfo
    except OSError:
        logger.exception("Failed to resolve DoH server %s", server_hostname)
        # Fall back to a known public DNS if resolution fails
        server_ip = "1.1.1.1"
        logger.info("Using fallback IP for DoH server: %s", server_ip)

    # Create DoH resolver
    doh_resolver = DoHResolver(doh_server, server_hostname, server_ip)

    # Create resolver functions
    def resolve_ipv4(hostname: str) -> list[str]:
        return doh_resolver.resolve(hostname, "A")

    def resolve_ipv6(hostname: str) -> list[str]:
        return doh_resolver.resolve(hostname, "AAAA")

    # Skip DoH resolution for the DoH server itself, IP addresses, and private addresses
    def skip_doh(hostname: str) -> bool:
        return (
            hostname in (server_hostname, server_ip)
            or _is_ip_address(hostname)
            or _is_local_address(hostname)
        )

    # Replace socket.getaddrinfo with our DoH-enabled version
    socket.getaddrinfo = cast(
        "Any", create_custom_getaddrinfo(resolve_ipv4, resolve_ipv6, skip_doh)
    )

    logger.info("DoH resolver successfully configured and activated")
    return doh_resolver


def _init_custom_resolver_internal(servers: list[str]) -> dns.resolver.Resolver:
    """Initialize a custom DNS resolver for the given servers.

    Args:
        servers: List of DNS server IPs to use

    """
    custom_resolver = create_custom_resolver(servers)

    # Create resolver functions
    def resolve_ipv4(hostname: str) -> list[str]:
        return resolve_with_custom_dns(custom_resolver, hostname, "A")

    def resolve_ipv6(hostname: str) -> list[str]:
        return resolve_with_custom_dns(custom_resolver, hostname, "AAAA")

    # Replace socket.getaddrinfo with our custom resolver
    socket.getaddrinfo = cast("Any", create_custom_getaddrinfo(resolve_ipv4, resolve_ipv6))

    logger.info("Custom DNS resolver successfully configured and activated")
    return custom_resolver


# --- ISP / network DNS interference detection ---------------------------------
# Compare what the (tamperable) system resolver returns for a host against a
# tamper-resistant DoH lookup. Divergent answers are a strong signal the network is
# hijacking or NXDOMAIN-blocking the domain (a common reason AA downloads "work" but
# land on an ISP block page). Used to surface an actionable hint to the user.
_dns_interference_warned: set[str] = set()
_dns_interference_active = False


def _build_detection_doh_resolver() -> DoHResolver | None:
    """Build a throwaway DoH resolver for interference checks (no socket patching).

    Honours the DoH provider the user selected (``DNS_PROVIDERS[_current_dns_index]``),
    falling back to the first configured provider when none is active. The endpoint is
    pinned to the provider's own nameserver IP so resolving the DoH host can't be
    redirected by the very DNS layer the check is meant to detect.
    """
    if 0 <= _current_dns_index < len(DNS_PROVIDERS):
        _name, servers, doh_url = DNS_PROVIDERS[_current_dns_index]
    elif DNS_PROVIDERS:
        _name, servers, doh_url = DNS_PROVIDERS[0]
    else:
        return None
    server_hostname = urllib.parse.urlparse(doh_url).hostname or ""
    if not server_hostname or not servers:
        return None
    return DoHResolver(doh_url, server_hostname, servers[0])


def detect_dns_interference(hostname: str) -> dict[str, list[str]] | None:
    """Detect network DNS interference by comparing system DNS against DoH.

    Returns ``{"system_ips": [...], "doh_ips": [...]}`` when the two resolvers disagree
    (no overlapping IPs), otherwise None. No-op for IP literals / local hostnames and
    when DoH resolution is unavailable, so it never produces a false positive.
    """
    host = (hostname or "").strip().lower()
    if not host or _is_ip_address(host) or _is_local_address(host):
        return None
    resolver = _build_detection_doh_resolver()
    if resolver is None:
        return None
    try:
        system_ips = {str(info[4][0]) for info in original_getaddrinfo(host, 443, socket.AF_INET)}
    except OSError:
        return None
    if not system_ips:
        return None
    doh_ips = {ip for ip in resolver.resolve(host, "A") if ip}
    if not doh_ips or (system_ips & doh_ips):
        return None
    return {"system_ips": sorted(system_ips), "doh_ips": sorted(doh_ips)}


def note_possible_dns_interference(hostname: str) -> bool:
    """Check ``hostname`` for DNS interference, logging an actionable warning once.

    Returns True when interference has been detected this session. The check runs at
    most once per host to avoid repeated DoH lookups and log spam.
    """
    global _dns_interference_active
    host = (hostname or "").strip().lower()
    if not host or host in _dns_interference_warned:
        return _dns_interference_active
    _dns_interference_warned.add(host)

    result = detect_dns_interference(host)
    if not result:
        return _dns_interference_active

    _dns_interference_active = True
    routing_via_doh = _current_dns_index >= 0 and bool(DOH_SERVER)
    remedy = (
        "Shelfmark is routing this domain through DNS-over-HTTPS to work around it."
        if routing_via_doh
        else "Enable DNS-over-HTTPS (USE_DOH=true) or set a custom DNS provider to bypass it."
    )
    logger.warning(
        "Possible ISP/network DNS interference for %s: system DNS resolves to %s but DoH "
        "resolves to %s. The network appears to be blocking or redirecting this domain. %s",
        host,
        result["system_ips"],
        result["doh_ips"],
        remedy,
    )
    return True


def dns_interference_detected() -> bool:
    """Whether network DNS interference has been detected this session."""
    return _dns_interference_active


def init_doh_resolver(doh_server: str = "") -> DoHResolver | None:
    """Initialize DNS over HTTPS resolver."""
    server = doh_server or DOH_SERVER
    if not server:
        return None
    return _init_doh_resolver_internal(server)


def init_custom_resolver() -> dns.resolver.Resolver | None:
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
        _set_runtime_dns_state(servers, doh)

        logger.warning("Switched DNS provider to: %s (using DoH)", name)
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
    """Switch DNS provider and reset the AA URL list.

    Note: This function can be called during initialization, so we must NOT call
    _ensure_initialized() here to avoid recursive init loops.
    """
    if not rotate_dns_provider():
        return False
    # Reset AA URL to first available auto option if using auto AA
    global _aa_base_url, _current_aa_url_index
    configured_url = _get_configured_aa_url()

    if configured_url == "auto":
        # Auto mode always resets to the first mirror to restart the cascade. Skip any
        # quarantined ones: a new DNS provider cannot revive a parked or seized domain.
        with _dead_aa_urls_lock:
            restart_urls = [url for url in _aa_urls if url not in _dead_aa_urls] or _aa_urls
        if restart_urls:
            _aa_base_url = restart_urls[0]
            _current_aa_url_index = _aa_urls.index(_aa_base_url)
            logger.info("After DNS switch, resetting AA URL to: %s", _aa_base_url)
            _save_state(aa_url=_aa_base_url)
        else:
            _aa_base_url = ""
            _current_aa_url_index = 0
            logger.info("After DNS switch, AA URL remains unconfigured")
    else:
        # Keep the user's configured primary mirror (if it exists in the list),
        # otherwise keep the configured URL as-is (custom/env).
        _current_aa_url_index = _aa_urls.index(configured_url) if configured_url in _aa_urls else 0
        _aa_base_url = configured_url
        logger.info("After DNS switch, keeping configured AA URL: %s", _aa_base_url)
        _save_state(aa_url=_aa_base_url)
    return True


def set_dns_provider(
    provider: str, manual_servers: list[str] | None = None, *, use_doh: bool | None = None
) -> bool:
    """Set DNS to a specific provider or manual servers.

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
    doh_enabled = (
        use_doh
        if use_doh is not None
        else coerce_bool(app_config.get("USE_DOH", True), default=True)
    )

    with _dns_switch_lock:
        if provider == "system":
            # Use system DNS only - no custom resolver, no failover rotation
            _current_dns_index = -1
            _dns_exhausted_logged = False
            _set_runtime_dns_state([], "")
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
            _set_runtime_dns_state([], "")
            logger.info("DNS set to auto mode (system DNS, will rotate on failure with DoH)")
            init_dns_resolvers()
            _notify_dns_rotation("auto", [], "")
            return True

        if provider == "manual":
            if not manual_servers:
                logger.warning("Manual DNS requested but no servers provided")
                return False
            _current_dns_index = -1  # Not using preset providers
            _set_runtime_dns_state(manual_servers, "")
            logger.info("DNS set to manual servers: %s", manual_servers)
            init_dns_resolvers()
            _notify_dns_rotation("manual", manual_servers, "")
            return True

        # Find the provider in DNS_PROVIDERS
        for i, (name, servers, doh) in enumerate(DNS_PROVIDERS):
            if name == provider:
                _current_dns_index = i
                _dns_exhausted_logged = False
                # Only set DoH server if DoH is enabled
                runtime_doh_server = doh if doh_enabled else ""
                _set_runtime_dns_state(servers, runtime_doh_server)
                doh_status = "DoH enabled" if doh_enabled else "standard DNS"
                logger.info("DNS set to: %s (%s)", name, doh_status)
                _save_state(dns_provider=name)
                init_dns_resolvers()
                _notify_dns_rotation(name, servers, DOH_SERVER)
                return True

        logger.warning("Unknown DNS provider: %s", provider)
        return False


def init_dns_resolvers() -> None:
    """Initialize DNS resolvers based on configuration."""
    if _is_auto_dns_mode():
        if _current_dns_index >= 0:
            name, servers, doh = DNS_PROVIDERS[_current_dns_index]
            _set_runtime_dns_state(servers, doh)
            logger.info("Using DNS provider: %s (DoH enabled)", name)
        else:
            _set_runtime_dns_state([], "")
            logger.debug("Using system DNS (auto mode - will switch on failure)")
            socket.getaddrinfo = cast("Any", create_system_failover_getaddrinfo())
            return

    if CUSTOM_DNS:
        init_custom_resolver()
        if DOH_SERVER:
            init_doh_resolver(DOH_SERVER)


def _get_initial_dns_config() -> tuple[str, list[str] | None, bool]:
    """Determine initial DNS configuration from config singleton.

    The config singleton already handles ENV > config file > default priority,
    so we just read from config.

    Returns:
        Tuple of (provider, manual_servers, use_doh)

    """
    provider = str(app_config.get("CUSTOM_DNS", "auto")).lower().strip()
    use_doh = coerce_bool(app_config.get("USE_DOH", True), default=True)
    manual_servers = None

    # Check for manual DNS servers in config
    if provider == "manual":
        manual_dns = str(app_config.get("CUSTOM_DNS_MANUAL", "")).strip()
        if manual_dns:
            manual_servers = [s.strip() for s in manual_dns.split(",") if s.strip()]

    # Handle legacy format: IPs directly in CUSTOM_DNS setting
    if provider and provider not in (
        "auto",
        "system",
        "google",
        "cloudflare",
        "quad9",
        "opendns",
        "manual",
        "",
    ):
        # Check if it looks like IP addresses
        parts = provider.split(",")
        potential_ips = [p.strip() for p in parts if p.strip()]
        if potential_ips and all(_looks_like_ip(p) for p in potential_ips):
            manual_servers = potential_ips
            provider = "manual"
            logger.info("Detected legacy DNS format, treating as manual: %s", manual_servers)

    return provider or "auto", manual_servers, use_doh


def _looks_like_ip(s: str) -> bool:
    """Check if a string looks like an IP address."""
    # Simple heuristic: contains only digits, dots, and colons
    return s.replace(".", "").replace(":", "").isdigit()


def _build_aa_urls() -> list[str]:
    """Build list of available AA URLs from centralized mirror config."""
    from shelfmark.core.mirrors import get_aa_mirrors

    return get_aa_mirrors()


def _initialize_aa_state() -> None:
    """Restore or probe AA URL state."""
    global _aa_base_url, _current_aa_url_index, _aa_urls

    # Build URL list from config
    previous_urls = _aa_urls
    _aa_urls = _build_aa_urls()

    # Drop quarantine decisions only when the mirror list itself changed - they were
    # made about a list that no longer applies. This runs on every re-init (settings
    # sync, DNS rotation, helper subprocess startup), and clearing unconditionally
    # would resurrect a parked mirror mid-session.
    if previous_urls != _aa_urls:
        with _dead_aa_urls_lock:
            _dead_aa_urls.clear()

    # Get configured base URL from config
    configured_url = _get_configured_aa_url()

    # If AA_BASE_URL is pinned to a custom URL that's not in the mirror list, we still
    # want to treat it as the active base (and rewrite known mirror links to it).
    if configured_url != "auto" and configured_url not in _aa_urls:
        _aa_urls = [configured_url, *_aa_urls]

    if not _aa_urls:
        _aa_base_url = ""
        _current_aa_url_index = 0
        logger.info("AA_BASE_URL: unconfigured")
        return

    if configured_url == "auto":
        # Never restore or probe a mirror quarantined this session: re-init happens
        # often, and re-electing a parked domain costs a wasted request every time
        # (its parking page answers 200, so the probe would happily pick it).
        with _dead_aa_urls_lock:
            candidates = [url for url in _aa_urls if url not in _dead_aa_urls]
        restored = state.get("aa_base_url")
        if restored and restored in candidates:
            _current_aa_url_index = _aa_urls.index(restored)
            _aa_base_url = restored
        else:
            logger.debug("AA_BASE_URL: auto, checking available urls %s", candidates)
            for url in candidates:
                try:
                    response = requests.get(
                        url, proxies=get_proxies(url), timeout=3, verify=get_ssl_verify(url)
                    )
                    if response.status_code == HTTPStatus.OK:
                        _current_aa_url_index = _aa_urls.index(url)
                        _aa_base_url = url
                        _save_state(aa_url=_aa_base_url)
                        break
                except (OSError, requests.RequestException) as exc:
                    logger.debug("Could not reach AA mirror candidate %s: %s", url, exc)
            # Also covers the case where every probe failed and the previous base is
            # itself quarantined - keeping it would aim the next search at a dead host.
            if not _aa_base_url or _aa_base_url == "auto" or _aa_base_url not in candidates:
                _aa_base_url = (candidates or _aa_urls)[0]
                _current_aa_url_index = _aa_urls.index(_aa_base_url)
    elif configured_url not in _aa_urls:
        logger.info("AA_BASE_URL set to custom value %s; skipping auto-switch", configured_url)
        _aa_base_url = configured_url
    else:
        _current_aa_url_index = _aa_urls.index(configured_url)
        _aa_base_url = configured_url

    logger.info("AA_BASE_URL: %s", _aa_base_url)


def init_dns(*, force: bool = False) -> None:
    """Initialize DNS state and resolvers using set_dns_provider() for consistency."""
    global state, _dns_initialized, _current_dns_index
    if _dns_initialized and not force:
        return
    with _init_lock:
        # Double-check after acquiring lock
        if _dns_initialized and not force:
            return
        # Do work first, set flag after to prevent race conditions.
        logger.debug(
            "Initializing DNS (using %s locks)", "gevent" if _using_gevent_locks else "threading"
        )
        state = _load_state()

        # Get initial DNS configuration from environment
        provider, manual_servers, use_doh = _get_initial_dns_config()

        if provider == "auto":
            # Auto mode: check for persisted provider from previous rotation
            persisted = state.get("dns_provider") if state else None
            if persisted:
                for i, (name, _, _) in enumerate(DNS_PROVIDERS):
                    if name == persisted:
                        _current_dns_index = i
                        logger.info("Restored DNS provider from state: %s", name)
                        break
            # Use init_dns_resolvers() for auto mode to preserve rotation capability
            init_dns_resolvers()
        else:
            # Non-auto mode: use set_dns_provider() for consistent initialization
            set_dns_provider(provider, manual_servers, use_doh=use_doh)

        # Only set flag AFTER work completes successfully.
        _dns_initialized = True


def init_aa(*, force: bool = False) -> None:
    """Initialize AA mirror selection."""
    global state, _aa_initialized
    if _aa_initialized and not force:
        return
    with _init_lock:
        # Double-check after acquiring lock
        if _aa_initialized and not force:
            return
        # Do work first, set flag after to prevent race conditions.
        state = _load_state()
        _initialize_aa_state()
        # Only set flag AFTER work completes successfully.
        _aa_initialized = True


def init(*, force: bool = False) -> None:
    """Initialize network state (DNS resolvers and AA mirror selection).

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
        # where another thread sees _initialized=True but _aa_base_url is still empty.
        init_dns(force=force)
        init_aa(force=force)
        _apply_ssl_warning_suppression()
        # Only set flag AFTER work completes successfully.
        _initialized = True


def get_aa_base_url() -> str:
    """Get current AA base URL."""
    _ensure_initialized()
    return _aa_base_url


def is_aa_auto_mode() -> bool:
    """Return True when AA_BASE_URL is set to 'auto' (mirror failover enabled)."""
    return _get_configured_aa_url() == "auto"


def get_available_aa_urls() -> list[str]:
    """Get configured AA URLs (copy), minus any quarantined this process.

    Falls back to the full list when every mirror has been quarantined: a wrong
    classification must not leave the app with nowhere to search.
    """
    _ensure_initialized()
    with _dead_aa_urls_lock:
        alive = [url for url in _aa_urls if url not in _dead_aa_urls]
        if not alive and _aa_urls:
            logger.warning("All AA mirrors quarantined; retrying the full list")
            _dead_aa_urls.clear()
            alive = _aa_urls.copy()
    # Prefer mirrors that are not serving a 429 cooldown so rotation stops hammering a
    # throttled host. When every live mirror is cooling, keep the full live list rather
    # than returning nothing: selection must never be left with nowhere to point, and
    # the bypasser's fail-fast reports the "all rate-limited" case with a clear error.
    breathing = [url for url in alive if not is_host_cooling_down(url)]
    return breathing or alive


def _aa_base_for_url(url: str) -> str:
    """Return the configured mirror base that ``url`` belongs to, if any."""
    for base in _aa_urls:
        if base and url.startswith(base):
            return base
    return ""


def mark_aa_url_dead(url: str, reason: str) -> bool:
    """Quarantine an AA mirror for the rest of this process.

    Only for hard evidence that the host is not a working AA mirror. Transient
    failures (403 challenge, 429, 5xx, timeouts) must never come through here -
    quarantining a live mirror throws away its bypass clearance.
    """
    _ensure_initialized()
    base = _aa_base_for_url(url) or url
    with _dead_aa_urls_lock:
        if base not in _aa_urls or base in _dead_aa_urls:
            return False
        # Keep at least one mirror in play, even if it is the failing one.
        if len([u for u in _aa_urls if u not in _dead_aa_urls]) <= 1:
            logger.warning("Not quarantining last remaining AA mirror %s (%s)", base, reason)
            return False
        _dead_aa_urls.add(base)
    logger.warning("Quarantined AA mirror %s for this session: %s", base, reason)
    return True


def get_dead_aa_urls() -> set[str]:
    """Return the mirrors quarantined this process (copy)."""
    with _dead_aa_urls_lock:
        return set(_dead_aa_urls)


def set_aa_url(url: str) -> bool:
    """Set the active AA base URL; returns True if applied."""
    _ensure_initialized()
    global _aa_base_url, _current_aa_url_index
    if url not in _aa_urls:
        return False
    _current_aa_url_index = _aa_urls.index(url)
    _aa_base_url = url
    logger.info("Set AA URL to: %s", _aa_base_url)
    _save_state(aa_url=_aa_base_url)
    return True


def set_aa_url_index(new_index: int) -> bool:
    """Set AA base URL by index in the full configured list; True if applied."""
    _ensure_initialized()
    if new_index < 0 or new_index >= len(_aa_urls):
        return False
    return set_aa_url(_aa_urls[new_index])


class AAMirrorSelector:
    """Keep AA mirror switching consistent across call sites.

    Tracks attempts per DNS cycle and rewrites URLs safely.
    """

    def __init__(self) -> None:
        """Initialize mirror state from the current AA configuration."""
        # Set by html_get_page at each give-up path so a caller that only sees the
        # returned empty page can still report *why* the fetch produced nothing
        # (403, 404, redirect loop, bypasser error, mirrors exhausted, ...) instead
        # of a blanket "network restricted" guess. None means "no failure recorded".
        self.last_failure: str | None = None
        self._ensure_fresh_state(reset_attempts=True)

    def _ensure_fresh_state(self, *, reset_attempts: bool = False) -> None:
        _ensure_initialized()
        self.aa_urls = get_available_aa_urls()
        # Rotation walks the live mirrors, but rewriting has to recognise every
        # configured base: a URL built before a mirror was quarantined still points at
        # it, and failing to rewrite would send the retry back to the dead host.
        self.all_aa_urls = _aa_urls.copy()
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
        for base in self.all_aa_urls:
            if url.startswith(base):
                return url.replace(base, self.current_base, 1)
        return url

    def quarantine_current(self, reason: str) -> bool:
        """Quarantine the mirror this selector is on (hard failures only)."""
        if not self.current_base:
            return False
        dropped = mark_aa_url_dead(self.current_base, reason)
        if dropped:
            # Rebuild from the surviving mirrors so the dead one is out of the cycle.
            self._ensure_fresh_state(reset_attempts=False)
        return dropped

    def next_mirror_or_rotate_dns(
        self, *, allow_dns: bool = True, fatal: bool = False, reason: str = ""
    ) -> tuple[str | None, str]:
        """Advance to the next mirror or rotate DNS if needed.

        ``fatal`` marks the current mirror as not-an-AA-mirror (NXDOMAIN, refused, a
        200 that isn't AA) and drops it from this process's rotation. Leave it False
        for anything the mirror can recover from - a challenge or a 5xx means the host
        is alive, and quarantining it would discard its bypass clearance.

        Returns (new_base, action) where action is 'mirror', 'dns', or 'exhausted'.
        """
        if fatal and self.quarantine_current(reason or "unusable mirror"):
            # Quarantining rebuilt the state onto a surviving mirror, so that mirror is
            # the next one to try - advancing again here would skip straight past it.
            self.attempts_this_dns += 1
            if self.current_base and is_aa_auto_mode():
                set_aa_url(self.current_base)
                return self.current_base, "mirror"

        self.attempts_this_dns += 1
        max_attempts = len(self.aa_urls) if is_aa_auto_mode() else 1
        if self.attempts_this_dns >= max_attempts:
            if allow_dns and rotate_dns_and_reset_aa():
                self._ensure_fresh_state(reset_attempts=True)
                return self.current_base, "dns"
            return None, "exhausted"

        if not is_aa_auto_mode():
            # Mirror is explicitly configured; do not fail over to other mirrors.
            return None, "exhausted"

        if not self.aa_urls:
            return None, "exhausted"

        next_index = (self._index + 1) % len(self.aa_urls)
        set_aa_url(self.aa_urls[next_index])
        self._ensure_fresh_state(reset_attempts=False)
        return self.current_base, "mirror"


# Configure urllib opener with appropriate headers
opener = urllib.request.build_opener()
opener.addheaders = [
    (
        "User-agent",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/129.0.0.0 Safari/537.3",
    )
]
urllib.request.install_opener(opener)
