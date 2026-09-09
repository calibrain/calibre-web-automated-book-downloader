"""HTTP download with retry, resume, and Cloudflare bypass support."""

import random
import time
from http import HTTPStatus
from io import BytesIO
from typing import TYPE_CHECKING, NoReturn
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import requests
from tqdm import tqdm

from shelfmark.bypass import BypassCancelledError, ChallengeNotSolvedError, cookie_store
from shelfmark.bypass.challenge import challenge_marker
from shelfmark.bypass.waiting_room import WaitingRoomTimeoutError, is_aa_waiting_room
from shelfmark.core import search_deadline
from shelfmark.core.config import config as app_config
from shelfmark.core.logger import setup_logger
from shelfmark.core.request_helpers import coerce_bool, normalize_positive_int
from shelfmark.download import network
from shelfmark.download.activity import release_activity_grace, request_activity_grace
from shelfmark.download.network import get_proxies, get_ssl_verify

if TYPE_CHECKING:
    from collections.abc import Callable
    from threading import Event
    from types import ModuleType

logger = setup_logger(__name__)
_RNG = random.SystemRandom()

_MAX_REDIRECTS = 5
# DDoS-Guard's re-check probe. Its 302 to `?check=1` is one hop of a handshake rather
# than a page: the parameter asserts the caller already holds the cookies that hop
# issued.
_DDG_CHECK_PARAM = "check"
# Z-Library answers the first hit with a 503 whose only real payload is a Set-Cookie; echoing
# that cookie back returns the 302 to the real page. Two attempts cover the handshake without
# letting a server that keeps re-issuing cookies hold us in the loop.
_MAX_COOKIE_HANDSHAKE_RETRIES = 2
_HTTP_STATUS_FORBIDDEN = HTTPStatus.FORBIDDEN
_HTTP_STATUS_NOT_FOUND = HTTPStatus.NOT_FOUND
_HTTP_STATUS_RATE_LIMITED = HTTPStatus.TOO_MANY_REQUESTS
_HTTP_STATUS_SERVICE_UNAVAILABLE = HTTPStatus.SERVICE_UNAVAILABLE
_HTTP_STATUS_OK = HTTPStatus.OK
_HTTP_STATUS_RANGE_NOT_SATISFIABLE = HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE
_HTTP_STATUS_PARTIAL_CONTENT = HTTPStatus.PARTIAL_CONTENT
_HTTP_STATUS_NON_RETRYABLE = (_HTTP_STATUS_FORBIDDEN, _HTTP_STATUS_NOT_FOUND)
_STATUS_CALLBACK_ERRORS = (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError)
# Added on top of the active bypasser's own budget so it reports its real failure before
# stall detection cancels the download.
_BYPASS_GRACE_SLACK_SECONDS = 30.0
_BYPASSER_ERRORS = (
    AttributeError,
    BypassCancelledError,
    ChallengeNotSolvedError,
    KeyError,
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
    network.RateLimitedError,
    requests.exceptions.RequestException,
)

# Bypasser modules are imported lazily to support dynamic selection based on config
_internal_bypasser = None
_external_bypasser = None


def _raise_too_many_redirects(message: str) -> NoReturn:
    raise requests.exceptions.TooManyRedirects(message)


def _new_cookies(response: requests.Response, already_sent: dict[str, str]) -> dict[str, str]:
    """Cookies a response set that we were not already echoing back.

    Returning only the *new* ones is what makes the retry terminate: a server that keeps
    re-issuing the same cookie yields nothing here, so we stop instead of spinning.
    """
    jar = getattr(response, "cookies", None)
    if not jar:
        return {}
    return {name: value for name, value in jar.items() if already_sent.get(name) != value}


def _get_internal_bypasser() -> ModuleType:
    """Lazy import of internal bypasser module."""
    global _internal_bypasser
    if _internal_bypasser is None:
        try:
            from shelfmark.bypass import internal_bypasser

            _internal_bypasser = internal_bypasser
        except ImportError as e:
            msg = (
                f"Failed to import internal bypasser: {e}. "
                "Check that all dependencies are installed. "
                "You may need to disable CF bypass or use the external bypasser."
            )
            raise RuntimeError(msg) from e
    return _internal_bypasser


def _get_external_bypasser() -> ModuleType:
    """Lazy import of external bypasser module."""
    global _external_bypasser
    if _external_bypasser is None:
        try:
            from shelfmark.bypass import external_bypasser

            _external_bypasser = external_bypasser
        except ImportError as e:
            msg = (
                f"Failed to import external bypasser: {e}. "
                "Check that the external bypasser is properly configured."
            )
            raise RuntimeError(msg) from e
    return _external_bypasser


def _is_using_external_bypasser() -> bool:
    """Check if external bypasser is configured (reads from config, not just env)."""
    return coerce_bool(app_config.get("USING_EXTERNAL_BYPASSER", False))


def _is_cf_bypass_enabled() -> bool:
    """Check if Cloudflare bypass is enabled."""
    return coerce_bool(app_config.get("USE_CF_BYPASS", True))


def _bypass_grace_seconds() -> float:
    """How long a bypass may block before stall detection should give up on it.

    Each bypasser knows its own retry/timeout budget, so ask the active one rather than
    duplicating the arithmetic here. The slack keeps the bypasser's own deadline expiring
    first, so the user sees its real error instead of a generic "Download stalled".
    """
    bypasser = (
        _get_external_bypasser() if _is_using_external_bypasser() else _get_internal_bypasser()
    )
    return bypasser.max_duration_seconds() + _BYPASS_GRACE_SLACK_SECONDS


def get_bypassed_page(
    url: str,
    selector: network.AAMirrorSelector | None = None,
    cancel_flag: Event | None = None,
) -> str | None:
    """Fetch a bypassed page using the active bypasser implementation."""
    if _is_using_external_bypasser():
        return _get_external_bypasser().get_bypassed_page(url, selector, cancel_flag)
    return _get_internal_bypasser().get_bypassed_page(url, selector, cancel_flag)


def get_cf_cookies_for_domain(domain: str) -> dict[str, str]:
    """Get the clearance cookies won by whichever bypasser solved this domain."""
    return cookie_store.get_cf_cookies_for_domain(domain)


def get_cf_user_agent_for_domain(domain: str) -> str | None:
    """Get the User-Agent that solved this domain's challenge, if one is stored."""
    return cookie_store.get_cf_user_agent_for_domain(domain)


def _apply_cf_bypass(url: str, headers: dict) -> dict:
    """Apply CF bypass cookies and user agent if available.

    Modifies headers in-place with the stored user agent (if available).
    Returns cookies dict to use with the request.
    """
    if not _is_cf_bypass_enabled():
        return {}

    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    cookies = get_cf_cookies_for_domain(hostname)
    stored_ua = get_cf_user_agent_for_domain(hostname)
    if stored_ua:
        headers["User-Agent"] = stored_ua
    return cookies


# Network settings
REQUEST_TIMEOUT = (5, 10)  # (connect, read)
MAX_DOWNLOAD_RETRIES = 2
MAX_RESUME_ATTEMPTS = 3

RETRYABLE_CODES = (429, 500, 502, 503, 504)
CONNECTION_ERRORS = (
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    requests.exceptions.SSLError,
    requests.exceptions.ChunkedEncodingError,
)
DOWNLOAD_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}


def parse_size_string(size: str) -> float | None:
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
    except ValueError, IndexError:
        return None


def _backoff_delay(attempt: int, base: float = 0.25, cap: float = 3.0) -> float:
    """Exponential backoff with jitter."""
    return min(cap, base * (2 ** (attempt - 1))) + _RNG.random() * base


def _get_status_code(e: Exception) -> int | None:
    """Extract HTTP status code from an exception, or None if not applicable."""
    if isinstance(e, requests.exceptions.HTTPError) and e.response is not None:
        return e.response.status_code
    return None


def _is_retryable_error(e: Exception) -> bool:
    """Check if error is retryable (connection error or retryable HTTP status)."""
    if isinstance(e, CONNECTION_ERRORS):
        return True
    status = _get_status_code(e)
    return status is not None and status in RETRYABLE_CODES


# Statuses that mean the host is gone rather than busy: 410 Gone and 451 Unavailable
# For Legal Reasons are what a seized domain answers with.
_DEAD_MIRROR_CODES = (410, 451)


def _response_challenge_marker(response: requests.Response) -> str | None:
    """The challenge marker in a response body, or None if it carries no challenge.

    Content type is checked first so a JSON or octet-stream error body is never
    decoded just to be scanned; a missing header is scanned anyway, since an
    interstitial served without one is still an interstitial.
    """
    content_type = response.headers.get("Content-Type", "")
    if content_type and "html" not in content_type.lower():
        return None
    try:
        return challenge_marker(response.text)
    except UnicodeDecodeError, ValueError:
        return None


def _solvable_url(url: str) -> str:
    """The URL a solver should open, given one we may be mid-handshake on.

    The manual AA redirect follower in `html_get_page` walks DDoS-Guard's handshake by
    reassigning `current_url`, so by the time a 403, a 503 challenge or a redirect loop
    hands that URL to a bypasser it is often the `?check=1` probe rather than the page
    we actually wanted. A solver opens it in a fresh browser holding none of the cookies
    the probe exists to collect, so DDoS-Guard cannot verify it automatically and answers
    with the manual CAPTCHA page that nothing can solve - the failure in #1292, where
    FlareSolverr reported "Challenge solved!" over a 4.7 KB DDOS-GUARD interstitial.

    Handing over the pre-probe URL instead lets the solver's browser run the whole
    handshake itself, which is what a real browser does and what the solver is for.

    Scoped to the hosts whose redirects we follow manually: everywhere else `check` is
    an ordinary query parameter and none of our business.
    """
    if not network.should_rotate_dns_for_url(url):
        return url
    parsed = urlparse(url)
    params = parse_qsl(parsed.query, keep_blank_values=True)
    kept = [(key, value) for key, value in params if key != _DDG_CHECK_PARAM]
    if len(kept) == len(params):
        return url
    return urlunparse(parsed._replace(query=urlencode(kept)))


def _fatal_mirror_reason(e: Exception) -> str | None:
    """Return why ``e`` proves the mirror is unusable, or None if it may recover.

    Hard evidence only - the name does not resolve, nothing is listening, or the host
    says it is gone for good. A timeout, a 5xx or a challenge all mean the mirror is
    alive, and rotating off it discards the bypass clearance held for that domain.
    """
    status = _get_status_code(e)
    if status is not None and status in _DEAD_MIRROR_CODES:
        return f"HTTP {status}"

    # requests wraps the real cause; a read timeout subclasses ConnectionError for
    # some adapters, so exclude timeouts explicitly before inspecting the message.
    if isinstance(e, requests.exceptions.Timeout):
        return None
    if not isinstance(e, requests.exceptions.ConnectionError):
        return None

    text = str(e).lower()
    if "nameresolutionerror" in text or "failed to resolve" in text or "name or service" in text:
        return "DNS does not resolve"
    if "connection refused" in text or "no route to host" in text:
        return "connection refused"
    return None


def _try_rotation(
    original_url: str,
    current_url: str,
    selector: network.AAMirrorSelector,
    *,
    fatal_reason: str | None = None,
) -> str | None:
    """Try mirror/DNS rotation. Returns new URL or None."""
    aa_base_url = network.get_aa_base_url()
    if aa_base_url and current_url.startswith(aa_base_url):
        new_base, action = selector.next_mirror_or_rotate_dns(
            fatal=fatal_reason is not None, reason=fatal_reason or ""
        )
        if action in ("mirror", "dns") and new_base:
            new_url = selector.rewrite(original_url)
            logger.info("[%s] switching to: %s", action, new_url)
            return new_url
    elif network.should_rotate_dns_for_url(current_url) and network.rotate_dns_provider():
        logger.info("[dns-rotate] retrying: %s", original_url)
        return original_url
    return None


def html_get_page(
    url: str,
    retry: int | None = None,
    selector: network.AAMirrorSelector | None = None,
    cancel_flag: Event | None = None,
    status_callback: Callable[[str, str | None], None] | None = None,
    *,
    use_bypasser: bool = False,
    allow_bypasser_fallback: bool = True,
    include_response_url: bool = False,
    success_delay: float = 1.0,
    session: requests.Session | None = None,
) -> str | tuple[str, str]:
    """Fetch HTML content from a URL with retry mechanism.

    Args:
        url: URL to fetch.
        retry: Maximum number of attempts before giving up.
        selector: Mirror selector used for AA mirror and DNS rotation.
        cancel_flag: Optional event used to abort retries early.
        status_callback: Optional callback for UI status updates.
        allow_bypasser_fallback: Whether a challenge may be handed to the bypasser.
            If False, a 403 triggers mirror rotation instead, and an AA redirect loop
            gives up immediately rather than waiting on a browser solve. Use False for
            best-effort fetches whose result is optional (e.g. the download count on
            the details modal); search and detail pages pass True.
        use_bypasser: Whether to start with the bypasser instead of direct HTTP.
        include_response_url: If True, return `(html, final_url)` to expose the
            resolved response URL after redirects.
        success_delay: Optional delay (seconds) after successful fetch.
        session: Optional requests session to reuse across attempts.

    """

    # Normalise before the closures below capture it: they touch selector.last_failure,
    # so it must be a concrete selector, not the Optional parameter.
    selector = selector or network.AAMirrorSelector()

    # A release search runs under a wall-clock budget (see shelfmark.core.search_deadline).
    # Adopting it as the cancel flag is what makes the budget bite on a solve already in
    # flight: the bypassers and the helper subprocess poll this flag but know nothing about
    # deadlines. Only when the caller has no flag of its own - a queued download brings one
    # and must keep it, and runs outside any search context anyway.
    if cancel_flag is None:
        cancel_flag = search_deadline.cancel_event()

    def _result(html: str, response_url: str) -> str | tuple[str, str]:
        if include_response_url:
            return html, response_url
        return html

    def _fail(reason: str, response_url: str) -> str | tuple[str, str]:
        """Record why the fetch is giving up, then return the empty result.

        Every give-up path returns an empty page, which is all the caller used to
        see. Stashing the concrete reason on the shared selector lets the caller
        surface it (see release_sources.direct_download) rather than reporting the
        same generic "network restricted or mirrors blocked" for every cause.
        """
        selector.last_failure = reason
        return _result("", response_url)

    def _run_bypasser(bypass_url: str) -> str | tuple[str, str]:
        """Run the active bypasser for one URL and return its result.

        Factored out so the redirect-loop handoff below can invoke it directly. That
        call site sits inside the inner redirect `while`, so it cannot reach the
        retry-loop branch above with `continue`, and with MAX_RETRY=1 there is no
        later attempt for that branch to run on either.
        """
        # Every handoff reaches the solver through here, so this is the one place the
        # mid-handshake `?check=1` URL has to be unwound. See _solvable_url.
        bypass_url = _solvable_url(bypass_url)
        # Never start a minutes-long browser solve on a budget that has already run out:
        # nothing downstream would get to report the real reason before the caller's
        # deadline (or its reverse proxy) cut the request off.
        if search_deadline.expired():
            logger.info("Release search budget spent; not starting a bypass for %s", bypass_url)
            return _fail(search_deadline.deadline_message(), bypass_url)

        if status_callback:
            status_callback("resolving", "Bypassing protection...")
        try:
            # A bypass is one long blocking call with no incremental progress, so
            # tell the orchestrator up front how long it may legitimately take
            # instead of trying to fake activity while it runs. Inside the try so a
            # bypasser that fails to load is still reported as a bypasser error.
            request_activity_grace(status_callback, _bypass_grace_seconds())
            result = get_bypassed_page(bypass_url, selector, cancel_flag)
            if result:
                return _result(result, bypass_url)
            return _fail(
                "The protection bypasser returned an empty page — the challenge was "
                "not solved. Check that FlareSolverr/the CF bypasser is reachable.",
                bypass_url,
            )
        except network.RateLimitedError as e:
            # Not a bypasser malfunction: the host is throttling this IP and a solve
            # cannot help. Surface the wait as a plain failure so the search ends cleanly
            # instead of looping another minutes-long solve against a 429.
            logger.info("Skipping bypass (rate-limited): %s", e)
            if status_callback:
                try:
                    status_callback("resolving", "Rate limited, try again shortly")
                except _STATUS_CALLBACK_ERRORS:
                    logger.debug("Rate-limit status callback failed", exc_info=True)
            return _fail(str(e), bypass_url)
        except WaitingRoomTimeoutError as e:
            logger.info("Waiting room timed out: %s", e)
            return _fail(str(e), bypass_url)
        except ChallengeNotSolvedError as e:
            # Not a bypasser malfunction: it ran, and the host answered with something it
            # cannot clear - DDoS-Guard's manual CAPTCHA, typically. Must precede the
            # generic handler below, whose "the protection bypasser failed" is what sent
            # #1292 off to fix a FlareSolverr that was working perfectly.
            logger.info("Bypass ran but did not clear the protection: %s", e)
            if status_callback:
                try:
                    status_callback("error", str(e))
                except _STATUS_CALLBACK_ERRORS:
                    logger.debug("Unsolved-challenge status callback failed", exc_info=True)
            return _fail(str(e), bypass_url)
        except _BYPASSER_ERRORS as e:
            logger.warning("Bypasser error: %s: %s", type(e).__name__, e)
            # Surface the real reason. Without this the caller only sees an empty
            # page and the download dies with a generic failure, hiding e.g. a
            # FlareSolverr 500 behind a silent wait.
            if status_callback and not isinstance(e, BypassCancelledError):
                try:
                    status_callback("error", f"Bypass failed: {type(e).__name__}: {e}")
                except _STATUS_CALLBACK_ERRORS:
                    logger.debug("Bypass error status callback failed", exc_info=True)
            if isinstance(e, BypassCancelledError):
                # The budget trips the same cancel flag a user's cancel does, so tell them
                # apart here - "cancelled" is a confusing thing to read when nobody did.
                if search_deadline.expired():
                    return _fail(search_deadline.deadline_message(), bypass_url)
                return _fail("The protection bypass was cancelled.", bypass_url)
            return _fail(f"The protection bypasser failed: {type(e).__name__}: {e}", bypass_url)
        finally:
            release_activity_grace(status_callback)

    def _bypass_handoff_allowed() -> bool:
        """Whether a challenge on the current URL may be handed to the bypasser.

        allow_bypasser_fallback is honoured for the same reason the 403 path honours it:
        callers such as the /dyn/md5/summary fetch behind the details modal pass False
        precisely so a best-effort request fails fast instead of holding the UI open for
        a minutes-long browser solve.
        """
        return allow_bypasser_fallback and _is_cf_bypass_enabled() and not use_bypasser_now

    def _purge_clearance(target_url: str) -> None:
        """Drop the host's stored clearance cookies.

        Called whenever the protection answered a request that *carried* cookies:
        being challenged while presenting them proves they no longer work, so keeping
        them only guarantees the same rejection on every later request. Applies to
        either bypasser, since both fill the same store.
        """
        hostname = urlparse(target_url).hostname or ""
        # An empty domain means "clear every host" to the store, so skip the purge
        # rather than wipe clearance for sites that are working fine.
        if hostname:
            cookie_store.clear_cf_cookies(hostname)

    def _redirect_loop_handoff(bypass_url: str) -> str | tuple[str, str]:
        """Drop the host's stale clearance cookies, then bypass `bypass_url`.

        A `?check=1` loop is how DDoS-Guard answers a clearance cookie that has gone
        stale, so the dead cookie has to go before the solve — otherwise it is merged
        back over the fresh one on the next request and the loop simply resumes.
        """
        _purge_clearance(bypass_url)
        return _run_bypasser(bypass_url)

    configured_retry = normalize_positive_int(app_config.MAX_RETRY)
    retry_limit = (
        retry if retry is not None else (configured_retry if configured_retry is not None else 1)
    )
    original_url = url
    current_url = selector.rewrite(original_url)
    use_bypasser_now = use_bypasser
    # Survives across attempts so a cookie won once is still presented on later retries.
    handshake_cookies: dict[str, str] = {}
    handshake_retries = 0
    # Last transport error seen, so the exhausted-retries path can name the real
    # cause (timeout, connection refused, DNS, ...) instead of a generic message.
    last_error: Exception | None = None

    for attempt in range(1, retry_limit + 1):
        # Check for cancellation before each attempt
        if cancel_flag and cancel_flag.is_set():
            if search_deadline.expired():
                logger.info("Release search budget spent before attempt %s", attempt)
                return _fail(search_deadline.deadline_message(), current_url)
            logger.info("html_get_page cancelled before attempt %s", attempt)
            return _fail("The request was cancelled.", current_url)

        cookies: dict[str, str] = {}
        try:
            if use_bypasser_now and _is_cf_bypass_enabled():
                return _run_bypasser(current_url)

            logger.debug("GET: %s", current_url)

            # Use a browser-like UA by default (AA can behave differently for python-requests UA).
            headers = {"User-Agent": DOWNLOAD_HEADERS["User-Agent"]}

            # AA mirrors sometimes redirect to other (seized/dead) mirror domains. If we let
            # requests follow those redirects, the request fails on DNS and we rotate away
            # from an otherwise working mirror. Handle AA redirects manually instead.
            is_aa_url = network.should_rotate_dns_for_url(current_url)
            allow_redirects = not is_aa_url

            redirects_followed = 0
            while True:
                # Try with CF cookies/UA if available (from previous bypass)
                cookies = _apply_cf_bypass(current_url, headers)
                request_client = session or requests
                response = request_client.get(
                    current_url,
                    proxies=get_proxies(current_url),
                    timeout=REQUEST_TIMEOUT,
                    # Handshake cookies win. They were issued by *this* exchange, so by
                    # definition they are fresher than anything the store holds, and the
                    # server is waiting to see them echoed back on the very next hop.
                    # Letting the store overwrite them meant a stored cookie of the same
                    # name (DDoS-Guard reuses __ddg1_/__ddg2_ for both) was replayed on
                    # every hop and the freshly issued value never left this process - the
                    # ?check=1 probe could then never terminate, so every request ended in
                    # the redirect-loop handoff and paid for a full browser solve.
                    cookies={**cookies, **handshake_cookies},
                    headers=headers,
                    allow_redirects=allow_redirects,
                    verify=get_ssl_verify(current_url),
                )

                # Z-Library gates the first hit with a 503 that carries nothing but a
                # Set-Cookie; echoing it back yields the 302 to the real page. Without this
                # the cookie is dropped and every retry re-runs the same rejected request.
                if (
                    response.status_code == _HTTP_STATUS_SERVICE_UNAVAILABLE
                    and handshake_retries < _MAX_COOKIE_HANDSHAKE_RETRIES
                ):
                    issued = _new_cookies(response, handshake_cookies)
                    if issued:
                        handshake_cookies.update(issued)
                        handshake_retries += 1
                        logger.debug(
                            "503 set %s cookie(s); retrying with them: %s",
                            len(issued),
                            current_url,
                        )
                        continue

                # A 503 still serving a challenge is protection, not a busy origin. The
                # handshake above has nothing left to echo back, and 503 is in
                # RETRYABLE_CODES, so without this the request spends every attempt on
                # the same wall: the bypasser is only ever reached from the 403 branch
                # and the AA redirect rescues. Gate on the body, not the status, so a
                # genuine overloaded-origin 503 keeps its retry path.
                if response.status_code == _HTTP_STATUS_SERVICE_UNAVAILABLE:
                    marker = _response_challenge_marker(response)
                    if marker and _bypass_handoff_allowed():
                        if cookies:
                            # Challenged while presenting clearance means those cookies
                            # are dead; same reasoning as the 403 branch below.
                            logger.debug(
                                "503 challenge with cookies presented; purging: %s", current_url
                            )
                            _purge_clearance(current_url)
                        logger.info(
                            "503 challenge detected (%s); switching to bypasser: %s",
                            marker,
                            current_url,
                        )
                        return _run_bypasser(current_url)
                    if marker:
                        logger.debug(
                            "503 challenge (%s) but no bypasser handoff available: %s",
                            marker,
                            current_url,
                        )

                if is_aa_url and response.is_redirect:
                    location = response.headers.get("Location", "")
                    if not location:
                        _raise_too_many_redirects(
                            f"Redirect with no Location header: {current_url}"
                        )

                    redirect_url = urljoin(current_url, location)
                    current_host = urlparse(current_url).hostname or ""
                    redirect_host = urlparse(redirect_url).hostname or ""

                    # If an AA mirror redirects to a different hostname, treat that as a mirror
                    # failure and rotate rather than following the redirect (auto mode only).
                    if current_host and redirect_host and current_host != redirect_host:
                        if not network.is_aa_auto_mode():
                            logger.warning(
                                "AA mirror locked to %s but redirected to %s: %s",
                                current_host,
                                redirect_host,
                                current_url,
                            )
                            return _fail(
                                f"The configured mirror {current_host} redirected to "
                                f"{redirect_host}; it may be down or seized. Point MIRROR at "
                                "a working host or switch to auto mode.",
                                current_url,
                            )

                        new_url = _try_rotation(original_url, current_url, selector)
                        if new_url:
                            current_url = new_url
                            # Reset per-request state for the new host.
                            headers = {"User-Agent": DOWNLOAD_HEADERS["User-Agent"]}
                            handshake_cookies.clear()
                            is_aa_url = network.should_rotate_dns_for_url(current_url)
                            allow_redirects = not is_aa_url
                            redirects_followed = 0
                            continue

                        logger.warning(
                            "AA redirect from %s to %s but mirrors exhausted: %s",
                            current_host,
                            redirect_host,
                            current_url,
                        )
                        return _fail(
                            "Every Anna's Archive mirror redirected away to a dead host — "
                            "all configured mirrors are unreachable.",
                            current_url,
                        )

                    # Same-host redirect (relative or absolute) - follow manually.
                    # DDoS-Guard gates AA /search behind a cookie probe: the 302 to
                    # ?check=1 carries Set-Cookie (__ddg*) which must be echoed back on
                    # the next hop, or the server just re-issues the redirect forever.
                    issued = _new_cookies(response, handshake_cookies)
                    if issued:
                        handshake_cookies.update(issued)
                    redirects_followed += 1
                    if redirects_followed > _MAX_REDIRECTS:
                        # A same-host redirect loop on AA is not a network fault — it is
                        # how DDoS-Guard presents a handshake that is unsolved, or whose
                        # clearance cookie has gone stale: /search redirects to
                        # /search&check=1, which redirects back, indefinitely. Hand it
                        # straight to the bypasser rather than raising, which would send it
                        # down the retry path to re-run the whole loop on every attempt
                        # (10 x 6 = ~60 requests to AA) without ever offering the URL to the
                        # bypasser. `continue` is no use here either — it would target this
                        # inner redirect loop rather than the retry branch below.
                        if _bypass_handoff_allowed():
                            logger.info(
                                "Redirect loop detected; switching to bypasser: %s", current_url
                            )
                            return _redirect_loop_handoff(current_url)
                        # No bypasser to hand it to. Every AA mirror shares the challenge,
                        # so rotating only collects another loop — give up now instead of
                        # raising and burning the same ~60 requests over the retry budget.
                        logger.warning(
                            "Redirect loop and no bypasser available, giving up: %s", current_url
                        )
                        return _fail(
                            "Anna's Archive is behind a protection challenge (endless "
                            "redirect loop) and no bypasser is enabled to solve it. Enable "
                            "FlareSolverr/the CF bypasser.",
                            current_url,
                        )
                    current_url = redirect_url
                    continue

                response.raise_for_status()
                if (
                    _bypass_handoff_allowed()
                    and not _is_using_external_bypasser()
                    and is_aa_waiting_room(current_url, response.text)
                ):
                    # A successful HTTP response can still need a live browser: the
                    # queue's JavaScript must finish in the session that entered it.
                    return _run_bypasser(current_url)
                if success_delay > 0:
                    time.sleep(success_delay)
                return _result(response.text, response.url)

        except Exception as e:
            last_error = e
            status = _get_status_code(e)

            # The same DDoS-Guard rescue, for the loops the manual AA follower above hands
            # back rather than resolving inline — an AA redirect missing its Location
            # header. TooManyRedirects carries no status, so the 403 rescue below never
            # fires and every retry would re-send the dead cookies. Scoped to the hosts
            # whose redirects we follow manually: elsewhere `requests` follows them itself,
            # and a loop there is an ordinary misconfiguration that a cookie purge and a
            # minutes-long browser solve would be the wrong answer to.
            if (
                isinstance(e, requests.exceptions.TooManyRedirects)
                and network.should_rotate_dns_for_url(current_url)
                and _bypass_handoff_allowed()
            ):
                logger.info("Redirect loop detected; switching to bypasser: %s", current_url)
                return _redirect_loop_handoff(current_url)

            # 403 = Cloudflare/DDoS-Guard protection
            if status == _HTTP_STATUS_FORBIDDEN:
                # If bypasser fallback is disabled, try mirrors instead
                if not allow_bypasser_fallback:
                    new_url = _try_rotation(original_url, current_url, selector)
                    if new_url:
                        current_url = new_url
                        continue
                    logger.warning("403 error, mirrors exhausted: %s", current_url)
                    return _fail(
                        "Anna's Archive returned 403 (blocked) and all mirrors are exhausted.",
                        current_url,
                    )

                if _is_cf_bypass_enabled() and not use_bypasser_now:
                    # Before switching to bypasser, check if cookies have become available
                    # (another concurrent download may have completed bypass and extracted cookies)
                    parsed = urlparse(current_url)
                    fresh_cookies = get_cf_cookies_for_domain(parsed.hostname or "")
                    if fresh_cookies and not cookies and attempt < retry_limit:
                        # Cookies are now available - retry with cookies before using bypasser.
                        # Guarded on there being a next attempt: `continue` on the last one
                        # ends the retry loop and abandons the request without ever offering
                        # the URL to the bypasser, and MAX_RETRY=1 is the supported setting.
                        # Same reasoning as the bypasser invocation below.
                        logger.debug(
                            "403 but cookies now available - retrying with cookies: %s",
                            current_url,
                        )
                        continue
                    if cookies:
                        # Challenged *while presenting* clearance: those cookies are
                        # dead. Without this they survive the solve and get merged back
                        # over the fresh ones, so every later request re-presents a
                        # known-rejected cookie and is challenged again - the stale
                        # retry that never ends.
                        logger.debug("403 with cookies presented; purging: %s", current_url)
                        _purge_clearance(current_url)
                    logger.info("403 detected; switching to bypasser: %s", current_url)
                    # Invoke it here rather than setting use_bypasser_now and continuing.
                    # The branch that acts on that flag runs at the top of the *next* retry
                    # attempt, so under the supported MAX_RETRY=1 there is no next attempt
                    # and the bypasser was never reached — a 403 simply ended the search.
                    # Same reasoning as the redirect-loop handoffs.
                    return _run_bypasser(current_url)
                logger.warning("403 error, giving up: %s", current_url)
                return _fail(
                    "Anna's Archive returned 403 (blocked) and no bypasser is enabled "
                    "to solve the protection challenge.",
                    current_url,
                )

            # 404 = Not found
            if status == _HTTP_STATUS_NOT_FOUND:
                logger.warning("404 error: %s", current_url)
                return _fail(
                    f"Anna's Archive returned 404 Not Found for {current_url}.", current_url
                )

            # 429 = origin throttling this IP. Arm the per-host backoff so selection and
            # the bypasser stop hammering it, then fall through to normal rotation onto a
            # mirror that is not (yet) rate-limited.
            if status == _HTTP_STATUS_RATE_LIMITED:
                network.note_rate_limited(current_url)

            # Try mirror/DNS rotation on retryable errors. A failure that proves the
            # mirror is unusable also drops it from this process's rotation, so the
            # next search does not pay for it again.
            fatal_reason = _fatal_mirror_reason(e)
            if fatal_reason or _is_retryable_error(e):
                new_url = _try_rotation(
                    original_url, current_url, selector, fatal_reason=fatal_reason
                )
                if new_url:
                    current_url = new_url
                    handshake_cookies.clear()
                    continue

            # Retry with backoff
            if attempt < retry_limit:
                logger.warning(
                    "Retry %s/%s for %s: %s: %s",
                    attempt,
                    retry_limit,
                    current_url,
                    type(e).__name__,
                    e,
                )
                time.sleep(_backoff_delay(attempt))
            else:
                logger.exception("Giving up after %s attempts: %s", retry_limit, current_url)

    if last_error is not None:
        return _fail(
            f"Could not reach Anna's Archive after {retry_limit} attempt(s): "
            f"{type(last_error).__name__}: {last_error}",
            current_url,
        )
    return _fail(
        "Could not reach Anna's Archive — all mirrors were exhausted without a usable response.",
        current_url,
    )


def download_url(
    link: str,
    size: str = "",
    progress_callback: Callable[[float], None] | None = None,
    cancel_flag: Event | None = None,
    _selector: network.AAMirrorSelector | None = None,
    status_callback: Callable[[str, str | None], None] | None = None,
    referer: str | None = None,
) -> BytesIO | None:
    """Download content from URL with automatic retry and resume support."""
    selector = _selector or network.AAMirrorSelector()
    current_url = selector.rewrite(link)

    # Build headers with optional referer
    headers = DOWNLOAD_HEADERS.copy()
    if referer:
        headers["Referer"] = referer
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
                status_callback(
                    "resolving",
                    f"Connecting (Attempt {attempt + 1}/{MAX_DOWNLOAD_RETRIES})",
                )

            logger.info(
                "Downloading: %s (attempt %s/%s)",
                current_url,
                attempt + 1,
                MAX_DOWNLOAD_RETRIES,
            )
            # Try with CF cookies/UA if available
            cookies = _apply_cf_bypass(current_url, headers)
            response = requests.get(
                current_url,
                stream=True,
                proxies=get_proxies(current_url),
                timeout=REQUEST_TIMEOUT,
                cookies=cookies,
                headers=headers,
                verify=get_ssl_verify(current_url),
            )
            response.raise_for_status()

            if status_callback:
                status_callback("downloading", "")

            total_size = total_size or float(response.headers.get("content-length", 0))
            pbar = tqdm(total=total_size, unit="B", unit_scale=True, desc="Downloading")

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
            if (
                total_size > 0
                and bytes_downloaded < total_size * 0.9
                and response.headers.get("content-type", "").startswith("text/html")
            ):
                logger.warning("Received HTML instead of file: %s", current_url)
                return None

            logger.debug("Download completed: %s bytes", bytes_downloaded)

        except requests.exceptions.RequestException as e:
            status = _get_status_code(e)
            retryable = _is_retryable_error(e)

            # Z-Library 403 - try refreshing cookies via bypasser once before giving up
            if (
                status == _HTTP_STATUS_FORBIDDEN
                and _is_cf_bypass_enabled()
                and not zlib_cookie_refresh_attempted
            ):
                parsed = urlparse(current_url)
                if _is_configured_zlib_host(parsed.hostname) and referer:
                    zlib_cookie_refresh_attempted = True
                    logger.info("Z-Library 403 - refreshing cookies via referer: %s", referer)
                    try:
                        get_bypassed_page(referer, selector, cancel_flag)
                        time.sleep(0.5)
                        # Retry with fresh cookies (don't increment attempt)
                        continue
                    except _BYPASSER_ERRORS as cookie_err:
                        logger.warning("Z-Library cookie refresh failed: %s", cookie_err)

            # Non-retryable errors
            if status in _HTTP_STATUS_NON_RETRYABLE:
                logger.warning("Download failed (%s): %s", status, current_url)
                return None

            # Rate limited - skip to next source immediately
            # (waiting doesn't help with concurrent downloads hitting the same server)
            if status == _HTTP_STATUS_RATE_LIMITED:
                network.note_rate_limited(current_url)
                logger.info("Rate limited (429) - trying next source")
                if status_callback:
                    status_callback("resolving", "Server busy, trying next")
                return None

            # Timeout - don't retry, server likely overloaded
            if isinstance(e, requests.exceptions.Timeout):
                logger.warning("Timeout: %s - skipping to next source", current_url)
                if status_callback:
                    status_callback("resolving", "Server timed out, trying next")
                return None

            # Try to resume if we got some data
            if bytes_downloaded > 0 and retryable:
                resumed = _try_resume(
                    current_url,
                    buffer,
                    bytes_downloaded,
                    total_size,
                    progress_callback,
                    cancel_flag,
                    headers,
                )
                if resumed:
                    return resumed

            # Try mirror/DNS rotation if nothing downloaded yet
            if bytes_downloaded == 0 and retryable:
                new_url = _try_rotation(link, current_url, selector)
                if new_url:
                    current_url = new_url
                    attempt += 1
                    continue

            logger.warning("Download error: %s: %s", type(e).__name__, e)
            if attempt < MAX_DOWNLOAD_RETRIES - 1:
                time.sleep(_backoff_delay(attempt + 1))
            attempt += 1
        else:
            return buffer

    logger.error("Download failed after %s attempts: %s", MAX_DOWNLOAD_RETRIES, link)
    return None


def _is_configured_zlib_host(hostname: str | None) -> bool:
    """Return True when a hostname matches a configured Z-Library mirror."""
    if not hostname:
        return False

    from shelfmark.core.mirrors import get_zlib_cookie_domains

    hostname = hostname.lower()
    base_domain = ".".join(hostname.split(".")[-2:]) if "." in hostname else hostname

    for domain in get_zlib_cookie_domains():
        candidate = str(domain).lower()
        if hostname == candidate or hostname.endswith(f".{candidate}") or base_domain == candidate:
            return True

    return False


def _try_resume(
    url: str,
    buffer: BytesIO,
    start_byte: int,
    total_size: float,
    progress_callback: Callable[[float], None] | None,
    cancel_flag: Event | None,
    base_headers: dict | None = None,
) -> BytesIO | None:
    """Try to resume an interrupted download."""
    for attempt in range(MAX_RESUME_ATTEMPTS):
        logger.info(
            "Resuming from %s bytes (attempt %s/%s)",
            start_byte,
            attempt + 1,
            MAX_RESUME_ATTEMPTS,
        )
        time.sleep(_backoff_delay(attempt + 1, base=0.5, cap=5.0))

        try:
            # Try with CF cookies/UA if available
            resume_headers = {
                **(base_headers or DOWNLOAD_HEADERS),
                "Range": f"bytes={start_byte}-",
            }
            cookies = _apply_cf_bypass(url, resume_headers)
            response = requests.get(
                url,
                stream=True,
                proxies=get_proxies(url),
                timeout=REQUEST_TIMEOUT,
                headers=resume_headers,
                cookies=cookies,
                verify=get_ssl_verify(url),
            )

            # Check resume support
            if response.status_code == _HTTP_STATUS_OK:  # Server doesn't support resume
                logger.info("Server doesn't support resume")
                return None
            if response.status_code == _HTTP_STATUS_RANGE_NOT_SATISFIABLE:  # Range not satisfiable
                logger.warning("Range not satisfiable")
                return None
            if response.status_code != _HTTP_STATUS_PARTIAL_CONTENT:
                response.raise_for_status()

            pbar = tqdm(
                total=total_size,
                initial=start_byte,
                unit="B",
                unit_scale=True,
                desc="Resuming",
            )
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

            logger.info("Resume completed: %s bytes", start_byte)

        except requests.exceptions.RequestException as e:
            logger.debug("Resume attempt %s failed: %s", attempt + 1, e)
        else:
            return buffer

    logger.warning("Resume failed after %s attempts", MAX_RESUME_ATTEMPTS)
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
