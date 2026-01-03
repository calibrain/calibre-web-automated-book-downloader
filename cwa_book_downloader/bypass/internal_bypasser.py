import os
import random
import signal
import socket
import subprocess
import threading
import time
import traceback
from datetime import datetime
from threading import Event
from typing import Optional
from urllib.parse import urlparse

import requests
from seleniumbase import Driver

from cwa_book_downloader.bypass import BypassCancelledException
from cwa_book_downloader.bypass.fingerprint import clear_screen_size, get_screen_size
from cwa_book_downloader.config import env
from cwa_book_downloader.config.env import LOG_DIR
from cwa_book_downloader.config.settings import RECORDING_DIR
from cwa_book_downloader.core.config import config as app_config
from cwa_book_downloader.core.logger import setup_logger
from cwa_book_downloader.download import network
from cwa_book_downloader.download.network import get_proxies

logger = setup_logger(__name__)

# Challenge detection indicators
CLOUDFLARE_INDICATORS = [
    "just a moment",
    "verify you are human",
    "verifying you are human",
    "cloudflare.com/products/turnstile",
]

DDOS_GUARD_INDICATORS = [
    "ddos-guard",
    "ddos guard",
    "checking your browser before accessing",
    "complete the manual check to continue",
    "could not verify your browser automatically",
]

DRIVER = None
DISPLAY = {
    "xvfb": None,
    "ffmpeg": None,
}
LAST_USED = None
LOCKED = threading.Lock()

# Flag to track if DNS rotated while a bypass was in progress
# Chrome will be restarted after the current operation completes
_dns_rotation_pending = False
_dns_rotation_lock = threading.Lock()

# Cookie storage - shared with requests library for Cloudflare bypass
# Structure: {domain: {cookie_name: {value, expiry, ...}}}
_cf_cookies: dict[str, dict] = {}
_cf_cookies_lock = threading.Lock()

# User-Agent storage - Cloudflare ties cf_clearance to the UA that solved the challenge
_cf_user_agents: dict[str, str] = {}

# Protection cookie names we care about (Cloudflare and DDoS-Guard)
CF_COOKIE_NAMES = {'cf_clearance', '__cf_bm', 'cf_chl_2', 'cf_chl_prog'}
DDG_COOKIE_NAMES = {'__ddg1_', '__ddg2_', '__ddg5_', '__ddg8_', '__ddg9_', '__ddg10_', '__ddgid_', '__ddgmark_', 'ddg_last_challenge'}

# Domains requiring full session cookies (not just protection cookies)
FULL_COOKIE_DOMAINS = {'z-lib.fm', 'z-lib.gs', 'z-lib.id', 'z-library.sk', 'zlibrary-global.se'}


def _get_base_domain(domain: str) -> str:
    """Extract base domain from hostname (e.g., 'www.example.com' -> 'example.com')."""
    return '.'.join(domain.split('.')[-2:]) if '.' in domain else domain


def _should_extract_cookie(name: str, extract_all: bool) -> bool:
    """Determine if a cookie should be extracted based on its name."""
    if extract_all:
        return True
    is_cf = name in CF_COOKIE_NAMES or name.startswith('cf_')
    is_ddg = name in DDG_COOKIE_NAMES or name.startswith('__ddg')
    return is_cf or is_ddg


def _extract_cookies_from_driver(driver, url: str) -> None:
    """Extract cookies from Chrome after successful bypass."""
    try:
        parsed = urlparse(url)
        domain = parsed.hostname or ""
        if not domain:
            return

        base_domain = _get_base_domain(domain)
        extract_all = base_domain in FULL_COOKIE_DOMAINS

        cookies_found = {}
        for cookie in driver.get_cookies():
            name = cookie.get('name', '')
            if _should_extract_cookie(name, extract_all):
                cookies_found[name] = {
                    'value': cookie.get('value', ''),
                    'domain': cookie.get('domain', domain),
                    'path': cookie.get('path', '/'),
                    'expiry': cookie.get('expiry'),
                    'secure': cookie.get('secure', True),
                    'httpOnly': cookie.get('httpOnly', True),
                }

        if not cookies_found:
            return

        try:
            user_agent = driver.execute_script("return navigator.userAgent")
        except Exception:
            user_agent = None

        with _cf_cookies_lock:
            _cf_cookies[base_domain] = cookies_found
            if user_agent:
                _cf_user_agents[base_domain] = user_agent
                logger.debug(f"Stored UA for {base_domain}: {user_agent[:60]}...")
            else:
                logger.debug(f"No UA captured for {base_domain}")

        cookie_type = "all" if extract_all else "protection"
        logger.debug(f"Extracted {len(cookies_found)} {cookie_type} cookies for {base_domain}")

    except Exception as e:
        logger.debug(f"Failed to extract cookies: {e}")


def get_cf_cookies_for_domain(domain: str) -> dict[str, str]:
    """Get stored cookies for a domain. Returns empty dict if none available."""
    if not domain:
        return {}

    base_domain = _get_base_domain(domain)

    with _cf_cookies_lock:
        cookies = _cf_cookies.get(base_domain, {})
        if not cookies:
            return {}

        cf_clearance = cookies.get('cf_clearance', {})
        if cf_clearance:
            expiry = cf_clearance.get('expiry')
            if expiry and time.time() > expiry:
                logger.debug(f"CF cookies expired for {base_domain}")
                _cf_cookies.pop(base_domain, None)
                return {}

        return {name: c['value'] for name, c in cookies.items()}


def has_valid_cf_cookies(domain: str) -> bool:
    """Check if we have valid Cloudflare cookies for a domain."""
    return bool(get_cf_cookies_for_domain(domain))


def get_cf_user_agent_for_domain(domain: str) -> Optional[str]:
    """Get the User-Agent that was used during bypass for a domain."""
    if not domain:
        return None
    with _cf_cookies_lock:
        return _cf_user_agents.get(_get_base_domain(domain))


def clear_cf_cookies(domain: str = None) -> None:
    """Clear stored Cloudflare cookies and User-Agent. If domain is None, clear all."""
    with _cf_cookies_lock:
        if domain:
            base_domain = _get_base_domain(domain)
            _cf_cookies.pop(base_domain, None)
            _cf_user_agents.pop(base_domain, None)
        else:
            _cf_cookies.clear()
            _cf_user_agents.clear()


def _reset_pyautogui_display_state():
    try:
        import pyautogui
        import Xlib.display
        pyautogui._pyautogui_x11._display = Xlib.display.Display(os.environ['DISPLAY'])
    except Exception as e:
        logger.warning(f"Error resetting pyautogui display state: {e}")


def _cleanup_orphan_processes() -> int:
    """Kill orphan Chrome/Xvfb/ffmpeg processes. Only runs in Docker mode."""
    if not env.DOCKERMODE:
        return 0

    processes_to_kill = ["chrome", "chromedriver", "Xvfb", "ffmpeg"]
    total_killed = 0

    logger.debug("Checking for orphan processes...")
    logger.log_resource_usage()

    for proc_name in processes_to_kill:
        try:
            result = subprocess.run(
                ["pgrep", "-f", proc_name],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode != 0 or not result.stdout.strip():
                continue

            pids = result.stdout.strip().split('\n')
            count = len(pids)
            logger.info(f"Found {count} orphan {proc_name} process(es), killing...")

            kill_result = subprocess.run(
                ["pkill", "-9", "-f", proc_name],
                capture_output=True,
                timeout=5
            )
            if kill_result.returncode == 0:
                total_killed += count
            else:
                logger.warning(f"pkill for {proc_name} returned {kill_result.returncode}")

        except subprocess.TimeoutExpired:
            logger.warning(f"Timeout while checking for {proc_name} processes")
        except Exception as e:
            logger.debug(f"Error checking for {proc_name} processes: {e}")

    if total_killed > 0:
        time.sleep(1)
        logger.info(f"Cleaned up {total_killed} orphan process(es)")
        logger.log_resource_usage()
    else:
        logger.debug("No orphan processes found")

    return total_killed

def _get_page_info(sb) -> tuple[str, str, str]:
    """Extract page title, body text, and current URL safely."""
    try:
        title = sb.get_title().lower()
    except Exception:
        title = ""
    try:
        body = sb.get_text("body").lower()
    except Exception:
        body = ""
    try:
        current_url = sb.get_current_url()
    except Exception:
        current_url = ""
    return title, body, current_url


def _check_indicators(title: str, body: str, indicators: list[str]) -> Optional[str]:
    """Check if any indicator is present in title or body. Returns the found indicator or None."""
    for indicator in indicators:
        if indicator in title or indicator in body:
            return indicator
    return None

def _has_cloudflare_patterns(body: str, url: str) -> bool:
    """Check for Cloudflare-specific patterns in body or URL."""
    return "cf-" in body or "cloudflare" in url.lower() or "/cdn-cgi/" in url

def _detect_challenge_type(sb) -> str:
    """Detect challenge type: 'cloudflare', 'ddos_guard', or 'none'."""
    try:
        title, body, current_url = _get_page_info(sb)
        
        # DDOS-Guard indicators
        if found := _check_indicators(title, body, DDOS_GUARD_INDICATORS):
            logger.debug(f"DDOS-Guard indicator found: '{found}'")
            return "ddos_guard"
        
        # Cloudflare indicators
        if found := _check_indicators(title, body, CLOUDFLARE_INDICATORS):
            logger.debug(f"Cloudflare indicator found: '{found}'")
            return "cloudflare"
        
        # Check URL patterns
        if _has_cloudflare_patterns(body, current_url):
            return "cloudflare"
            
        return "none"
    except Exception as e:
        logger.warning(f"Error detecting challenge type: {e}")
        return "none"

def _is_bypassed(sb, escape_emojis: bool = True) -> bool:
    """Check if the protection has been bypassed."""
    try:
        title, body, current_url = _get_page_info(sb)
        body_len = len(body.strip())
        
        # Long page content = probably bypassed
        if body_len > 100000:
            logger.debug(f"Page content too long, probably bypassed (len: {body_len})")
            return True
        
        # Multiple emojis = probably real content
        if escape_emojis:
            import emoji
            if len(emoji.emoji_list(body)) >= 3:
                logger.debug("Detected emojis in page, probably bypassed")
                return True

        # Check for protection indicators (means NOT bypassed)
        if _check_indicators(title, body, CLOUDFLARE_INDICATORS + DDOS_GUARD_INDICATORS):
            return False
        
        # Cloudflare URL patterns
        if _has_cloudflare_patterns(body, current_url):
            logger.debug("Cloudflare patterns detected in page")
            return False
            
        # Page too short = still loading
        if body_len < 50:
            logger.debug("Page content too short, might still be loading")
            return False
            
        logger.debug(f"Bypass check passed - Title: '{title[:100]}', Body length: {body_len}")
        return True
        
    except Exception as e:
        logger.warning(f"Error checking bypass status: {e}")
        return False

def _simulate_human_behavior(sb) -> None:
    """Simulate human-like behavior before bypass attempt."""
    try:
        time.sleep(random.uniform(0.5, 1.5))

        if random.random() < 0.3:
            sb.scroll_down(random.randint(20, 50))
            time.sleep(random.uniform(0.2, 0.5))
            sb.scroll_up(random.randint(10, 30))
            time.sleep(random.uniform(0.2, 0.4))

        try:
            import pyautogui
            x, y = pyautogui.position()
            pyautogui.moveTo(
                x + random.randint(-10, 10),
                y + random.randint(-10, 10),
                duration=random.uniform(0.05, 0.15)
            )
        except Exception as e:
            logger.debug(f"Mouse jiggle failed: {e}")
    except Exception as e:
        logger.debug(f"Human simulation failed: {e}")


def _bypass_method_handle_captcha(sb) -> bool:
    """Method 2: Use uc_gui_handle_captcha() - TAB+SPACEBAR approach, stealthier than click."""
    try:
        logger.debug("Attempting bypass: uc_gui_handle_captcha (TAB+SPACEBAR)")
        _simulate_human_behavior(sb)
        sb.uc_gui_handle_captcha()
        time.sleep(random.uniform(3, 5))
        return _is_bypassed(sb)
    except Exception as e:
        logger.debug(f"uc_gui_handle_captcha failed: {e}")
        return False


def _bypass_method_click_captcha(sb) -> bool:
    """Method 3: Use uc_gui_click_captcha() - direct click via PyAutoGUI."""
    try:
        logger.debug("Attempting bypass: uc_gui_click_captcha (direct click)")
        _simulate_human_behavior(sb)
        sb.uc_gui_click_captcha()
        time.sleep(random.uniform(3, 5))

        if _is_bypassed(sb):
            return True

        # Retry once with longer wait
        logger.debug("First click attempt failed, retrying...")
        time.sleep(random.uniform(4, 6))
        sb.uc_gui_click_captcha()
        time.sleep(random.uniform(3, 5))
        return _is_bypassed(sb)
    except Exception as e:
        logger.debug(f"uc_gui_click_captcha failed: {e}")
        return False


def _bypass_method_humanlike(sb) -> bool:
    """Human-like behavior with scroll, wait, and reload."""
    try:
        logger.debug("Attempting bypass: human-like interaction")
        time.sleep(random.uniform(6, 10))

        try:
            sb.scroll_to_bottom()
            time.sleep(random.uniform(1, 2))
            sb.scroll_to_top()
            time.sleep(random.uniform(2, 3))
        except Exception as e:
            logger.debug(f"Scroll behavior failed: {e}")

        if _is_bypassed(sb):
            return True

        logger.debug("Trying page refresh...")
        sb.refresh()
        time.sleep(random.uniform(5, 8))

        if _is_bypassed(sb):
            return True

        try:
            sb.uc_gui_click_captcha()
            time.sleep(random.uniform(3, 5))
        except Exception as e:
            logger.debug(f"Final captcha click failed: {e}")

        return _is_bypassed(sb)
    except Exception as e:
        logger.debug(f"Human-like method failed: {e}")
        return False


def _safe_reconnect(sb) -> None:
    """Safely attempt to reconnect WebDriver after CDP mode."""
    try:
        sb.reconnect()
    except Exception as e:
        logger.debug(f"Reconnect failed: {e}")


def _bypass_method_cdp_solve(sb) -> bool:
    """CDP Mode with solve_captcha() - WebDriver disconnected, no PyAutoGUI.

    CDP Mode disconnects WebDriver during interaction, making detection harder.
    The solve_captcha() method auto-detects challenge type.
    """
    try:
        logger.debug("Attempting bypass: CDP Mode solve_captcha")
        sb.activate_cdp_mode(sb.get_current_url())
        time.sleep(random.uniform(1, 2))

        try:
            sb.cdp.solve_captcha()
            time.sleep(random.uniform(3, 5))
            sb.reconnect()
            time.sleep(random.uniform(1, 2))

            if _is_bypassed(sb):
                return True
        except Exception as e:
            logger.debug(f"CDP solve_captcha failed: {e}")
            _safe_reconnect(sb)

        return False
    except Exception as e:
        logger.debug(f"CDP Mode solve failed: {e}")
        _safe_reconnect(sb)
        return False


CDP_CLICK_SELECTORS = [
    "#turnstile-widget div",      # Cloudflare Turnstile
    "#cf-turnstile div",          # Alternative CF Turnstile
    "iframe[src*='challenges']",  # CF challenge iframe
    "input[type='checkbox']",     # Generic checkbox (DDOS-Guard)
    "[class*='checkbox']",        # Class-based checkbox
    "#challenge-running",         # CF challenge indicator
]


def _bypass_method_cdp_click(sb) -> bool:
    """CDP Mode with native clicking - no PyAutoGUI dependency.

    Uses sb.cdp.click() which is native CDP clicking (SeleniumBase 4.45.6+).
    """
    try:
        logger.debug("Attempting bypass: CDP Mode native click")
        sb.activate_cdp_mode(sb.get_current_url())
        time.sleep(random.uniform(1, 2))

        for selector in CDP_CLICK_SELECTORS:
            try:
                if not sb.cdp.is_element_visible(selector):
                    continue

                logger.debug(f"CDP clicking: {selector}")
                sb.cdp.click(selector)
                time.sleep(random.uniform(2, 4))

                sb.reconnect()
                time.sleep(random.uniform(1, 2))

                if _is_bypassed(sb):
                    return True

                sb.activate_cdp_mode(sb.get_current_url())
                time.sleep(random.uniform(0.5, 1))
            except Exception as e:
                logger.debug(f"CDP click on '{selector}' failed: {e}")

        _safe_reconnect(sb)
        return _is_bypassed(sb)
    except Exception as e:
        logger.debug(f"CDP Mode click failed: {e}")
        _safe_reconnect(sb)
        return False


CDP_GUI_CLICK_SELECTORS = [
    "#turnstile-widget div",      # Cloudflare Turnstile
    "#cf-turnstile div",          # Alternative CF Turnstile
    "#challenge-stage div",       # CF challenge stage
    "input[type='checkbox']",     # Generic checkbox
    "[class*='cb-i']",            # DDOS-Guard checkbox
]


def _bypass_method_cdp_gui_click(sb) -> bool:
    """CDP Mode with PyAutoGUI-based clicking - uses actual mouse movement.

    Most human-like approach for advanced protections (Kasada, DataDome, Akamai).
    """
    try:
        logger.debug("Attempting bypass: CDP Mode gui_click (mouse-based)")
        sb.activate_cdp_mode(sb.get_current_url())
        time.sleep(random.uniform(1, 2))

        try:
            logger.debug("Trying cdp.gui_click_captcha()")
            sb.cdp.gui_click_captcha()
            time.sleep(random.uniform(3, 5))

            sb.reconnect()
            time.sleep(random.uniform(1, 2))

            if _is_bypassed(sb):
                return True

            sb.activate_cdp_mode(sb.get_current_url())
            time.sleep(random.uniform(0.5, 1))
        except Exception as e:
            logger.debug(f"cdp.gui_click_captcha() failed: {e}")

        for selector in CDP_GUI_CLICK_SELECTORS:
            try:
                if not sb.cdp.is_element_visible(selector):
                    continue

                logger.debug(f"CDP gui_click_element: {selector}")
                sb.cdp.gui_click_element(selector)
                time.sleep(random.uniform(3, 5))

                sb.reconnect()
                time.sleep(random.uniform(1, 2))

                if _is_bypassed(sb):
                    return True

                sb.activate_cdp_mode(sb.get_current_url())
                time.sleep(random.uniform(0.5, 1))
            except Exception as e:
                logger.debug(f"CDP gui_click on '{selector}' failed: {e}")

        _safe_reconnect(sb)
        return _is_bypassed(sb)
    except Exception as e:
        logger.debug(f"CDP Mode gui_click failed: {e}")
        _safe_reconnect(sb)
        return False


BYPASS_METHODS = [
    _bypass_method_cdp_solve,
    _bypass_method_cdp_click,
    _bypass_method_cdp_gui_click,
    _bypass_method_handle_captcha,
    _bypass_method_click_captcha,
    _bypass_method_humanlike,
]

MAX_CONSECUTIVE_SAME_CHALLENGE = 3


def _check_cancellation(cancel_flag: Optional[Event], message: str) -> None:
    """Check if cancellation was requested and raise if so."""
    if cancel_flag and cancel_flag.is_set():
        logger.info(message)
        raise BypassCancelledException("Bypass cancelled")


def _bypass(sb, max_retries: Optional[int] = None, cancel_flag: Optional[Event] = None) -> bool:
    """Attempt to bypass Cloudflare/DDOS-Guard protection using multiple methods."""
    max_retries = max_retries if max_retries is not None else app_config.MAX_RETRY

    last_challenge_type = None
    consecutive_same_challenge = 0

    for try_count in range(max_retries):
        _check_cancellation(cancel_flag, "Bypass cancelled by user")

        if _is_bypassed(sb):
            if try_count == 0:
                logger.info("Page already bypassed")
            return True

        challenge_type = _detect_challenge_type(sb)
        logger.debug(f"Challenge detected: {challenge_type}")

        # No challenge detected but page doesn't look bypassed - wait and retry
        if challenge_type == "none":
            logger.info("No challenge detected, waiting for page to settle...")
            time.sleep(random.uniform(2, 3))
            if _is_bypassed(sb):
                return True
            # Try a simple reconnect instead of captcha methods
            try:
                sb.reconnect()
                time.sleep(random.uniform(1, 2))
                if _is_bypassed(sb):
                    logger.info("Bypass successful after reconnect")
                    return True
            except Exception as e:
                logger.debug(f"Reconnect during no-challenge wait failed: {e}")
            continue

        if challenge_type == last_challenge_type:
            consecutive_same_challenge += 1
            if consecutive_same_challenge >= MAX_CONSECUTIVE_SAME_CHALLENGE:
                logger.warning(
                    f"Same challenge ({challenge_type}) detected {consecutive_same_challenge} times - aborting"
                )
                return False
        else:
            consecutive_same_challenge = 1
        last_challenge_type = challenge_type

        method = BYPASS_METHODS[try_count % len(BYPASS_METHODS)]
        logger.info(f"Bypass attempt {try_count + 1}/{max_retries} using {method.__name__}")

        if try_count > 0:
            wait_time = min(random.uniform(2, 4) * try_count, 12)
            logger.info(f"Waiting {wait_time:.1f}s before trying...")
            for _ in range(int(wait_time)):
                _check_cancellation(cancel_flag, "Bypass cancelled during wait")
                time.sleep(1)
            time.sleep(wait_time - int(wait_time))

        try:
            if method(sb):
                logger.info(f"Bypass successful using {method.__name__}")
                return True
        except BypassCancelledException:
            raise
        except Exception as e:
            logger.warning(f"Exception in {method.__name__}: {e}")

        logger.info(f"Bypass method {method.__name__} failed.")

    logger.warning("Exceeded maximum retries. Bypass failed.")
    return False

def _get_chromium_args() -> list[str]:
    """Build Chrome arguments, pre-resolving hostnames via Python's patched DNS.

    Pre-resolves AA hostnames and passes IPs to Chrome via --host-resolver-rules,
    bypassing Chrome's DNS entirely for those hosts.
    """
    arguments = [
        "--ignore-certificate-errors",
        "--ignore-ssl-errors",
        "--allow-running-insecure-content",
        "--ignore-certificate-errors-spki-list",
        "--ignore-certificate-errors-skip-list"
    ]

    if app_config.get("DEBUG", False):
        arguments.extend([
            "--enable-logging",
            "--v=1",
            "--log-file=" + str(LOG_DIR / "chrome_browser.log")
        ])

    proxies = get_proxies()
    if proxies:
        proxy_url = proxies.get('https') or proxies.get('http')
        if proxy_url:
            arguments.append(f'--proxy-server={proxy_url}')

    host_rules = _build_host_resolver_rules()
    if host_rules:
        arguments.append(f'--host-resolver-rules={", ".join(host_rules)}')
        logger.debug(f"Chrome: Using host resolver rules for {len(host_rules)} hosts")
    else:
        logger.warning("Chrome: No hosts could be pre-resolved")

    return arguments


def _build_host_resolver_rules() -> list[str]:
    """Pre-resolve AA hostnames and build Chrome host resolver rules."""
    host_rules = []

    try:
        for url in network.get_available_aa_urls():
            hostname = urlparse(url).hostname
            if not hostname:
                continue

            try:
                results = socket.getaddrinfo(hostname, 443, socket.AF_INET)
                if results:
                    ip = results[0][4][0]
                    host_rules.append(f"MAP {hostname} {ip}")
                    logger.debug(f"Chrome: Pre-resolved {hostname} -> {ip}")
                else:
                    logger.warning(f"Chrome: No addresses returned for {hostname}")
            except socket.gaierror as e:
                logger.warning(f"Chrome: Could not pre-resolve {hostname}: {e}")
    except Exception as e:
        logger.error_trace(f"Error pre-resolving hostnames for Chrome: {e}")

    return host_rules

DRIVER_RESET_ERRORS = {"WebDriverException", "SessionNotCreatedException", "TimeoutException", "MaxRetryError"}


def _get(url: str, retry: Optional[int] = None, cancel_flag: Optional[Event] = None) -> str:
    """Fetch URL with Cloudflare bypass. Retries on failure."""
    retry = retry if retry is not None else app_config.MAX_RETRY
    _check_cancellation(cancel_flag, "Bypass cancelled before starting")

    try:
        logger.debug(f"SB_GET: {url}")
        sb = _get_driver()

        hostname = urlparse(url).hostname or ""
        if has_valid_cf_cookies(hostname):
            reconnect_time = 1.0
            logger.debug(f"Using fast reconnect ({reconnect_time}s) - valid cookies exist")
        else:
            reconnect_time = app_config.DEFAULT_SLEEP
            logger.debug(f"Using standard reconnect ({reconnect_time}s) - no cached cookies")

        logger.debug("Opening URL with SeleniumBase...")
        sb.uc_open_with_reconnect(url, reconnect_time)

        _check_cancellation(cancel_flag, "Bypass cancelled after page load")

        try:
            logger.debug(f"Page loaded - URL: {sb.get_current_url()}, Title: {sb.get_title()}")
        except Exception as e:
            logger.debug(f"Could not get page info: {e}")

        logger.debug("Starting bypass process...")
        if _bypass(sb, cancel_flag=cancel_flag):
            _extract_cookies_from_driver(sb, url)
            return sb.page_source

        logger.warning("Bypass completed but page still shows protection")
        try:
            body = sb.get_text("body")
            logger.debug(f"Page content: {body[:500]}..." if len(body) > 500 else body)
        except Exception:
            pass

    except BypassCancelledException:
        raise
    except Exception as e:
        error_details = f"{type(e).__name__}: {e}"

        if retry == 0:
            logger.error(f"Failed after all retries: {error_details}")
            logger.debug(f"Stack trace: {traceback.format_exc()}")
            _reset_driver()
            raise

        logger.warning(f"Bypass failed (retry {app_config.MAX_RETRY - retry + 1}/{app_config.MAX_RETRY}): {error_details}")
        logger.debug(f"Stack trace: {traceback.format_exc()}")

        if type(e).__name__ in DRIVER_RESET_ERRORS:
            logger.info("Restarting bypasser due to browser error...")
            _reset_driver()

    _check_cancellation(cancel_flag, "Bypass cancelled before retry")
    return _get(url, retry - 1, cancel_flag)

def get(url: str, retry: Optional[int] = None, cancel_flag: Optional[Event] = None) -> str:
    """Fetch a URL with protection bypass."""
    retry = retry if retry is not None else app_config.MAX_RETRY
    global LAST_USED

    with LOCKED:
        # Try cookies first - another request may have completed bypass while waiting
        cookies = get_cf_cookies_for_domain(urlparse(url).hostname or "")
        if cookies:
            try:
                response = requests.get(url, cookies=cookies, proxies=get_proxies(), timeout=(5, 10))
                if response.status_code == 200:
                    logger.debug("Cookies available after lock wait - skipped Chrome")
                    LAST_USED = time.time()
                    return response.text
            except Exception:
                pass

        result = _get(url, retry, cancel_flag)
        LAST_USED = time.time()
        return result

def _init_driver() -> Driver:
    """Initialize the Chrome driver with undetected-chromedriver settings."""
    global DRIVER
    if DRIVER:
        _reset_driver()

    chromium_args = _get_chromium_args()
    screen_width, screen_height = get_screen_size()

    logger.debug(f"Initializing Chrome driver with args: {chromium_args}")
    logger.debug(f"Browser screen size: {screen_width}x{screen_height}")

    driver = Driver(
        uc=True,
        headless=False,
        incognito=True,
        locale="en",
        ad_block=True,
        size=f"{screen_width},{screen_height}",
        chromium_arg=chromium_args,
    )
    driver.set_page_load_timeout(60)
    DRIVER = driver
    time.sleep(app_config.DEFAULT_SLEEP)
    return driver

def _ensure_display_initialized():
    """Initialize virtual display if needed. Must be called with LOCKED held."""
    global DISPLAY
    if DISPLAY["xvfb"] is not None:
        return
    if not (env.DOCKERMODE and app_config.get("USE_CF_BYPASS", True)):
        return

    from pyvirtualdisplay import Display
    # Get the screen size (generates a random one if not already set)
    screen_width, screen_height = get_screen_size()
    # Add padding for browser chrome (title bar, borders, taskbar space)
    display_width = screen_width + 100
    display_height = screen_height + 150
    display = Display(visible=False, size=(display_width, display_height))
    display.start()
    DISPLAY["xvfb"] = display
    logger.info(f"Virtual display started: {display_width}x{display_height}")
    time.sleep(app_config.DEFAULT_SLEEP)
    _reset_pyautogui_display_state()


def _get_driver():
    global DRIVER, DISPLAY, LAST_USED
    logger.debug("Getting driver...")
    LAST_USED = time.time()
    
    _ensure_display_initialized()
    
    # Start FFmpeg recording on first actual bypass request (not during warmup)
    # This ensures we only record active bypass sessions, not idle time
    if app_config.get("DEBUG", False) and DISPLAY["xvfb"] and not DISPLAY["ffmpeg"]:
        RECORDING_DIR.mkdir(parents=True, exist_ok=True)
        display = DISPLAY["xvfb"]
        timestamp = datetime.now().strftime("%y%m%d-%H%M%S")
        output_file = RECORDING_DIR / f"screen_recording_{timestamp}.mp4"

        # Get the display size (screen size + padding)
        screen_width, screen_height = get_screen_size()
        display_width = screen_width + 100
        display_height = screen_height + 150

        ffmpeg_cmd = [
            "ffmpeg",
            "-y",
            "-f", "x11grab",
            "-video_size", f"{display_width}x{display_height}",
            "-i", f":{display.display}",
            "-c:v", "libx264",
            "-preset", "ultrafast",  # or "veryfast" (trade speed for slightly better compression)
            "-maxrate", "700k",      # Slightly higher bitrate for text clarity
            "-bufsize", "1400k",    # Buffer size (2x maxrate)
            "-crf", "36",  # Adjust as needed:  higher = smaller, lower = better quality (23 is visually lossless)
            "-pix_fmt", "yuv420p",  # Crucial for compatibility with most players
            "-tune", "animation",   # Optimize encoding for screen content
            "-x264-params", "bframes=0:deblock=-1,-1", # Optimize for text, disable b-frames and deblocking
            "-r", "15",         # Reduce frame rate (if content allows)
            "-an",                # Disable audio recording (if not needed)
            output_file.as_posix(),
            "-nostats", "-loglevel", "0"
        ]
        logger.debug("Starting FFmpeg recording to %s", output_file)
        logger.debug_trace(f"FFmpeg command: {' '.join(ffmpeg_cmd)}")
        DISPLAY["ffmpeg"] = subprocess.Popen(ffmpeg_cmd)
    
    if not DRIVER:
        return _init_driver()

    # Verify the existing driver is actually healthy (browser process still alive)
    if not _is_driver_healthy():
        logger.warning("Existing driver is unhealthy (browser may have crashed), reinitializing...")
        _reset_driver()
        _ensure_display_initialized()  # Display was shut down by _reset_driver, reinitialize it
        return _init_driver()

    logger.log_resource_usage()
    return DRIVER

def _reset_driver() -> None:
    """Reset the browser driver and cleanup all associated processes."""
    logger.log_resource_usage()
    logger.info("Shutting down Cloudflare bypasser...")
    global DRIVER, DISPLAY

    clear_screen_size()

    if DRIVER:
        try:
            DRIVER.quit()
        except Exception as e:
            logger.warning(f"Error quitting driver: {e}")
        DRIVER = None

    if DISPLAY["xvfb"]:
        try:
            DISPLAY["xvfb"].stop()
        except Exception as e:
            logger.warning(f"Error stopping display: {e}")
        DISPLAY["xvfb"] = None

    if DISPLAY["ffmpeg"]:
        try:
            DISPLAY["ffmpeg"].send_signal(signal.SIGINT)
        except Exception as e:
            logger.debug(f"Error stopping ffmpeg: {e}")
        DISPLAY["ffmpeg"] = None

    time.sleep(0.5)
    for process in ["Xvfb", "ffmpeg", "chrom"]:
        try:
            os.system(f"pkill -f {process}")
        except Exception as e:
            logger.debug(f"Error killing {process}: {e}")

    time.sleep(0.5)
    logger.info("Cloudflare bypasser shut down")
    logger.log_resource_usage()

def _restart_chrome_only() -> None:
    """Restart Chrome (not the display) to pick up new DNS settings.

    Called when DNS provider rotates. Display is kept running to avoid slower full restart.
    """
    global DRIVER, LAST_USED

    logger.debug("Restarting Chrome to apply new DNS settings...")

    if DRIVER:
        try:
            DRIVER.quit()
        except Exception as e:
            logger.debug(f"Error quitting driver during DNS rotation: {e}")
        DRIVER = None

    try:
        os.system("pkill -f chrom")
    except Exception as e:
        logger.debug(f"Error killing chrome processes: {e}")

    time.sleep(0.5)

    try:
        _init_driver()
        LAST_USED = time.time()
        logger.debug("Chrome restarted with updated DNS settings")
    except Exception as e:
        logger.warning(f"Failed to restart Chrome after DNS rotation: {e}")


def _on_dns_rotation(provider_name: str, servers: list, doh_url: str) -> None:
    """Callback invoked when network.py rotates DNS provider.

    Schedules an async Chrome restart to avoid blocking the current request.
    """
    global _dns_rotation_pending

    if DRIVER is None:
        return

    with _dns_rotation_lock:
        if _dns_rotation_pending:
            return
        _dns_rotation_pending = True

    def _async_restart():
        global _dns_rotation_pending
        logger.debug(f"DNS rotated to {provider_name} - restarting Chrome in background")
        with LOCKED:
            with _dns_rotation_lock:
                _dns_rotation_pending = False
            _restart_chrome_only()

    threading.Thread(target=_async_restart, daemon=True).start()


def _cleanup_driver() -> None:
    """Reset driver after inactivity timeout.

    Uses 4x longer timeout when UI clients are connected.
    """
    global LAST_USED

    try:
        from cwa_book_downloader.api.websocket import ws_manager
        has_active_clients = ws_manager.has_active_connections()
    except ImportError:
        ws_manager = None
        has_active_clients = False

    timeout_minutes = app_config.BYPASS_RELEASE_INACTIVE_MIN
    if has_active_clients:
        timeout_minutes *= 4

    with LOCKED:
        if not LAST_USED or time.time() - LAST_USED < timeout_minutes * 60:
            return

        logger.info(f"Bypasser idle for {timeout_minutes} min - shutting down")
        _reset_driver()
        LAST_USED = None
        logger.log_resource_usage()

        if has_active_clients and ws_manager:
            ws_manager.request_warmup_on_next_connect()
            logger.debug("Requested warmup on next client connect")

def _cleanup_loop() -> None:
    """Background loop that periodically checks for idle timeout."""
    while True:
        _cleanup_driver()
        time.sleep(max(app_config.BYPASS_RELEASE_INACTIVE_MIN / 2, 1))


def _init_cleanup_thread() -> None:
    """Start the background cleanup thread."""
    threading.Thread(target=_cleanup_loop, daemon=True).start()

def _should_warmup() -> bool:
    """Check if warmup should proceed based on configuration."""
    if not app_config.get("BYPASS_WARMUP_ON_CONNECT", True):
        logger.debug("Bypasser warmup disabled via config")
        return False
    if not env.DOCKERMODE:
        logger.debug("Bypasser warmup skipped - not in Docker mode")
        return False
    if not app_config.get("USE_CF_BYPASS", True):
        logger.debug("Bypasser warmup skipped - CF bypass disabled")
        return False
    if app_config.get("AA_DONATOR_KEY", ""):
        logger.debug("Bypasser warmup skipped - AA donator key set")
        return False
    return True


def warmup() -> None:
    """Pre-initialize the virtual display and Chrome browser.

    Called when a user connects to the web UI. Skipped if warmup is disabled,
    not in Docker mode, CF bypass is disabled, or AA donator key is set.
    """
    global LAST_USED

    if not _should_warmup():
        return

    with LOCKED:
        if is_warmed_up():
            logger.debug("Bypasser already warmed up")
            return

        _cleanup_orphan_processes()

        if DRIVER is not None or DISPLAY["xvfb"] is not None:
            logger.info("Resetting stale bypasser state before warmup...")
            _reset_driver()

        logger.info("Warming up Cloudflare bypasser...")

        try:
            _ensure_display_initialized()

            if DRIVER is None:
                logger.info("Pre-initializing Chrome browser...")
                _init_driver()
                LAST_USED = time.time()
                logger.info("Chrome browser ready")

            logger.info("Bypasser warmup complete")
            logger.log_resource_usage()

        except Exception as e:
            logger.warning(f"Failed to warm up bypasser: {e}")

def _is_driver_healthy() -> bool:
    """Check if the Chrome driver is responsive (not just non-None)."""
    if DRIVER is None:
        return False

    try:
        DRIVER.get_current_url()
        return True
    except Exception as e:
        logger.warning(f"Driver health check failed: {type(e).__name__}: {e}")
        return False


def is_warmed_up() -> bool:
    """Check if the bypasser is fully warmed up (display and browser initialized)."""
    if DISPLAY["xvfb"] is None or DRIVER is None:
        return False
    return _is_driver_healthy()


def shutdown_if_idle() -> None:
    """Start the inactivity countdown when all WebSocket clients disconnect.

    Sets LAST_USED to start the timer. The cleanup loop shuts down after
    BYPASS_RELEASE_INACTIVE_MIN minutes of inactivity.
    """
    global LAST_USED

    with LOCKED:
        if not is_warmed_up():
            logger.debug("Bypasser already shut down")
            return

        LAST_USED = time.time()
        logger.info(f"All clients disconnected - shutdown after {app_config.BYPASS_RELEASE_INACTIVE_MIN} min of inactivity")

_init_cleanup_thread()

# Register for DNS rotation notifications (Chrome restarts with new DNS settings)
if app_config.get("USE_CF_BYPASS", True) and not app_config.get("USING_EXTERNAL_BYPASSER", False):
    network.register_dns_rotation_callback(_on_dns_rotation)


def _try_with_cached_cookies(url: str, hostname: str) -> Optional[str]:
    """Attempt request with cached cookies before using Chrome."""
    cookies = get_cf_cookies_for_domain(hostname)
    if not cookies:
        return None

    try:
        headers = {}
        stored_ua = get_cf_user_agent_for_domain(hostname)
        if stored_ua:
            headers['User-Agent'] = stored_ua

        logger.debug(f"Trying request with cached cookies: {url}")
        response = requests.get(url, cookies=cookies, headers=headers, proxies=get_proxies(), timeout=(5, 10))
        if response.status_code == 200:
            logger.debug("Cached cookies worked, skipped Chrome bypass")
            return response.text
    except Exception:
        pass

    return None


def get_bypassed_page(
    url: str,
    selector: Optional[network.AAMirrorSelector] = None,
    cancel_flag: Optional[Event] = None
) -> Optional[str]:
    """Fetch HTML content from a URL using the internal Cloudflare Bypasser."""
    sel = selector or network.AAMirrorSelector()
    attempt_url = sel.rewrite(url)
    hostname = urlparse(attempt_url).hostname or ""

    cached_result = _try_with_cached_cookies(attempt_url, hostname)
    if cached_result:
        return cached_result

    try:
        response_html = get(attempt_url, cancel_flag=cancel_flag)
    except BypassCancelledException:
        raise
    except Exception:
        _check_cancellation(cancel_flag, "Bypass cancelled")
        new_base, action = sel.next_mirror_or_rotate_dns()
        if action in ("mirror", "dns") and new_base:
            attempt_url = sel.rewrite(url)
            response_html = get(attempt_url, cancel_flag=cancel_flag)
        else:
            raise

    if not response_html.strip():
        raise requests.exceptions.RequestException("Failed to bypass Cloudflare")

    return response_html
