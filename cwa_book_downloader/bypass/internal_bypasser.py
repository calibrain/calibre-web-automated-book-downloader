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


class BypassCancelledException(Exception):
    """Raised when a bypass operation is cancelled."""
    pass

import requests
from seleniumbase import Driver

from cwa_book_downloader.config import env
from cwa_book_downloader.download import network
from cwa_book_downloader.config.settings import RECORDING_DIR, VIRTUAL_SCREEN_SIZE
from cwa_book_downloader.config.env import LOG_DIR
from cwa_book_downloader.core.config import config as app_config
from cwa_book_downloader.core.logger import setup_logger


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


def _extract_cookies_from_driver(driver, url: str) -> None:
    """Extract cookies from Chrome after successful bypass."""
    try:
        parsed = urlparse(url)
        domain = parsed.hostname or ""
        if not domain:
            return

        # Get base domain for storage and full-cookie check
        base_domain = '.'.join(domain.split('.')[-2:]) if '.' in domain else domain
        extract_all = base_domain in FULL_COOKIE_DOMAINS

        cookies = driver.get_cookies()
        cookies_found = {}

        for cookie in cookies:
            name = cookie.get('name', '')

            if extract_all:
                should_extract = True
            else:
                is_cf = name in CF_COOKIE_NAMES or name.startswith('cf_')
                is_ddg = name in DDG_COOKIE_NAMES or name.startswith('__ddg')
                should_extract = is_cf or is_ddg

            if should_extract:
                cookies_found[name] = {
                    'value': cookie.get('value', ''),
                    'domain': cookie.get('domain', domain),
                    'path': cookie.get('path', '/'),
                    'expiry': cookie.get('expiry'),
                    'secure': cookie.get('secure', True),
                    'httpOnly': cookie.get('httpOnly', True),
                }

        if cookies_found:
            # Extract User-Agent - Cloudflare ties cf_clearance to the UA
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

    # Get base domain
    base_domain = '.'.join(domain.split('.')[-2:]) if '.' in domain else domain

    with _cf_cookies_lock:
        cookies = _cf_cookies.get(base_domain, {})
        if not cookies:
            return {}

        # Check if cf_clearance exists and hasn't expired
        cf_clearance = cookies.get('cf_clearance', {})
        if cf_clearance:
            expiry = cf_clearance.get('expiry')
            if expiry and time.time() > expiry:
                logger.debug(f"CF cookies expired for {base_domain}")
                _cf_cookies.pop(base_domain, None)
                return {}

        # Return simple name->value dict for requests
        return {name: c['value'] for name, c in cookies.items()}


def has_valid_cf_cookies(domain: str) -> bool:
    """Check if we have valid Cloudflare cookies for a domain."""
    return bool(get_cf_cookies_for_domain(domain))


def get_cf_user_agent_for_domain(domain: str) -> Optional[str]:
    """Get the User-Agent that was used during bypass for a domain."""
    if not domain:
        return None
    base_domain = '.'.join(domain.split('.')[-2:]) if '.' in domain else domain
    with _cf_cookies_lock:
        return _cf_user_agents.get(base_domain)


def clear_cf_cookies(domain: str = None) -> None:
    """Clear stored Cloudflare cookies and User-Agent. If domain is None, clear all."""
    with _cf_cookies_lock:
        if domain:
            base_domain = '.'.join(domain.split('.')[-2:]) if '.' in domain else domain
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
    """Kill any orphan Chrome, ChromeDriver, Xvfb, and ffmpeg processes.

    This should be called at startup before initializing the bypasser to ensure
    no zombie processes from previous crashes are consuming memory or interfering.

    Safety: Only runs in Docker mode to avoid killing user's browser processes
    on development machines.

    Returns:
        Number of processes killed.
    """
    # Safety: only cleanup in Docker mode to avoid killing user's browser
    if not env.DOCKERMODE:
        return 0

    processes_to_kill = ["chrome", "chromedriver", "Xvfb", "ffmpeg"]
    total_killed = 0

    logger.debug("Checking for orphan processes...")
    logger.log_resource_usage()

    for proc_name in processes_to_kill:
        try:
            # Use pgrep to find processes, then kill them
            result = subprocess.run(
                ["pgrep", "-f", proc_name],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0 and result.stdout.strip():
                pids = result.stdout.strip().split('\n')
                count = len(pids)
                if count > 0:
                    logger.info(f"Found {count} orphan {proc_name} process(es), killing...")
                    kill_result = subprocess.run(
                        ["pkill", "-9", "-f", proc_name],
                        capture_output=True,
                        timeout=5
                    )
                    if kill_result.returncode == 0:
                        total_killed += count
                    else:
                        logger.warning(f"pkill for {proc_name} returned {kill_result.returncode}, processes may not have been killed")
        except subprocess.TimeoutExpired:
            logger.warning(f"Timeout while checking for {proc_name} processes")
        except Exception as e:
            logger.debug(f"Error checking for {proc_name} processes: {e}")

    if total_killed > 0:
        # Give processes time to fully terminate
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
    """Detect what type of challenge we're facing.
    
    Returns:
        str: 'cloudflare', 'ddos_guard', or 'none' if no challenge detected
    """
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
        if found := _check_indicators(title, body, CLOUDFLARE_INDICATORS + DDOS_GUARD_INDICATORS):
            logger.debug(f"Protection indicator found: '{found}'")
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
        # Random short wait (human reaction time)
        time.sleep(random.uniform(0.5, 1.5))

        # Maybe scroll a bit (30% chance)
        if random.random() < 0.3:
            sb.scroll_down(random.randint(20, 50))
            time.sleep(random.uniform(0.2, 0.5))
            sb.scroll_up(random.randint(10, 30))
            time.sleep(random.uniform(0.2, 0.4))

        # Brief mouse jiggle via PyAutoGUI
        try:
            import pyautogui
            x, y = pyautogui.position()
            pyautogui.moveTo(
                x + random.randint(-10, 10),
                y + random.randint(-10, 10),
                duration=random.uniform(0.05, 0.15)
            )
        except Exception as e:
            logger.debug(f"Mouse jiggle failed (non-critical): {e}")
    except Exception as e:
        logger.debug(f"Human simulation failed (non-critical): {e}")


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
    """Method 4: Human-like behavior with scroll, wait, and reload."""
    try:
        logger.debug("Attempting bypass: human-like interaction")

        # Extended human-like wait
        time.sleep(random.uniform(6, 10))

        # Scroll behavior
        try:
            sb.scroll_to_bottom()
            time.sleep(random.uniform(1, 2))
            sb.scroll_to_top()
            time.sleep(random.uniform(2, 3))
        except Exception as e:
            logger.debug(f"Scroll behavior failed (non-critical): {e}")

        if _is_bypassed(sb):
            return True

        # Try refresh
        logger.debug("Trying page refresh...")
        sb.refresh()
        time.sleep(random.uniform(5, 8))

        if _is_bypassed(sb):
            return True

        # Final captcha click attempt
        try:
            sb.uc_gui_click_captcha()
            time.sleep(random.uniform(3, 5))
        except Exception as e:
            logger.debug(f"Final captcha click failed (non-critical): {e}")

        return _is_bypassed(sb)
    except Exception as e:
        logger.debug(f"Human-like method failed: {e}")
        return False


def _bypass_method_cdp_solve(sb) -> bool:
    """Method 5: CDP Mode with solve_captcha() - WebDriver disconnected, no PyAutoGUI.

    CDP Mode completely disconnects WebDriver during interaction, making detection
    much harder. The solve_captcha() method auto-detects challenge type.
    """
    try:
        logger.debug("Attempting bypass: CDP Mode solve_captcha")
        current_url = sb.get_current_url()

        # Activate CDP mode - this disconnects WebDriver
        sb.activate_cdp_mode(current_url)
        time.sleep(random.uniform(1, 2))

        # Try CDP solve_captcha (auto-detects challenge type)
        try:
            sb.cdp.solve_captcha()
            time.sleep(random.uniform(3, 5))

            # Reconnect WebDriver to check result
            sb.reconnect()
            time.sleep(random.uniform(1, 2))

            if _is_bypassed(sb):
                return True
        except Exception as e:
            logger.debug(f"CDP solve_captcha failed: {e}")
            # Make sure we reconnect on failure
            try:
                sb.reconnect()
            except Exception as reconnect_e:
                logger.debug(f"Reconnect after CDP solve failure failed: {reconnect_e}")

        return False
    except Exception as e:
        logger.debug(f"CDP Mode solve failed: {e}")
        # Ensure WebDriver is reconnected
        try:
            sb.reconnect()
        except Exception as reconnect_e:
            logger.debug(f"Reconnect after CDP Mode solve failed: {reconnect_e}")
        return False


def _bypass_method_cdp_click(sb) -> bool:
    """CDP Mode with native clicking - no PyAutoGUI dependency.

    Uses sb.cdp.click() which is native CDP clicking added in SeleniumBase 4.45.6.
    This doesn't require PyAutoGUI at all.
    """
    try:
        logger.debug("Attempting bypass: CDP Mode native click")
        current_url = sb.get_current_url()

        # Activate CDP mode
        sb.activate_cdp_mode(current_url)
        time.sleep(random.uniform(1, 2))

        # Common captcha/challenge selectors to try
        selectors = [
            "#turnstile-widget div",      # Cloudflare Turnstile (parent above shadow-root)
            "#cf-turnstile div",          # Alternative CF Turnstile
            "iframe[src*='challenges']",  # CF challenge iframe
            "input[type='checkbox']",     # Generic checkbox (DDOS-Guard)
            "[class*='checkbox']",        # Class-based checkbox
            "#challenge-running",         # CF challenge indicator
        ]

        for selector in selectors:
            try:
                # Check if element exists and is visible
                if sb.cdp.is_element_visible(selector):
                    logger.debug(f"CDP clicking: {selector}")
                    sb.cdp.click(selector)
                    time.sleep(random.uniform(2, 4))

                    # Reconnect and check
                    sb.reconnect()
                    time.sleep(random.uniform(1, 2))

                    if _is_bypassed(sb):
                        return True

                    # Re-enter CDP mode for next attempt
                    sb.activate_cdp_mode(sb.get_current_url())
                    time.sleep(random.uniform(0.5, 1))
            except Exception as e:
                logger.debug(f"CDP click on '{selector}' failed: {e}")
                continue

        # Final reconnect
        try:
            sb.reconnect()
        except Exception as e:
            logger.debug(f"Final reconnect in CDP click failed: {e}")

        return _is_bypassed(sb)
    except Exception as e:
        logger.debug(f"CDP Mode click failed: {e}")
        try:
            sb.reconnect()
        except Exception as reconnect_e:
            logger.debug(f"Reconnect after CDP Mode click failed: {reconnect_e}")
        return False


def _bypass_method_cdp_gui_click(sb) -> bool:
    """CDP Mode with PyAutoGUI-based clicking - uses actual mouse movement.

    For advanced protections (Kasada, DataDome, Akamai), the docs recommend
    using gui_* methods with actual mouse movements instead of CDP clicks.
    This is the most human-like approach in CDP mode.
    """
    try:
        logger.debug("Attempting bypass: CDP Mode gui_click (mouse-based)")
        current_url = sb.get_current_url()

        # Activate CDP mode
        sb.activate_cdp_mode(current_url)
        time.sleep(random.uniform(1, 2))

        # Try the dedicated CDP captcha method first
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

        # Turnstile selectors - use parent above shadow-root as per docs
        selectors = [
            "#turnstile-widget div",      # Cloudflare Turnstile
            "#cf-turnstile div",          # Alternative CF Turnstile
            "#challenge-stage div",       # CF challenge stage
            "input[type='checkbox']",     # Generic checkbox
            "[class*='cb-i']",            # DDOS-Guard checkbox
        ]

        for selector in selectors:
            try:
                if sb.cdp.is_element_visible(selector):
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
                continue

        # Final reconnect
        try:
            sb.reconnect()
        except Exception as e:
            logger.debug(f"Final reconnect in CDP gui_click failed: {e}")

        return _is_bypassed(sb)
    except Exception as e:
        logger.debug(f"CDP Mode gui_click failed: {e}")
        try:
            sb.reconnect()
        except Exception as reconnect_e:
            logger.debug(f"Reconnect after CDP Mode gui_click failed: {reconnect_e}")
        return False


def _bypass(sb, max_retries: Optional[int] = None, cancel_flag: Optional[Event] = None) -> bool:
    """Bypass function with unified strategies for Cloudflare and DDOS-Guard protection.

    Uses a unified method order that works for both protection types.
    Prioritizes CDP Mode (stealthier) with UC Mode PyAutoGUI fallbacks:

    1. CDP Mode solve - WebDriver disconnected, uses cdp.solve_captcha()
    2. CDP Mode click - Native CDP clicking, no PyAutoGUI (4.45.6+)
    3. CDP Mode gui_click - CDP with PyAutoGUI mouse movement (most human-like)
    4. uc_gui_handle_captcha() - TAB+SPACEBAR via PyAutoGUI (UC Mode fallback)
    5. uc_gui_click_captcha() - Direct click via PyAutoGUI (UC Mode fallback)
    6. Human-like interaction - Scroll, wait, reload, retry

    Returns True if bypass succeeded, False otherwise.
    """
    max_retries = max_retries if max_retries is not None else app_config.MAX_RETRY

    # Unified method order - works for both Cloudflare and DDOS-Guard
    # Prioritizes CDP Mode (stealthier), falls back to UC Mode PyAutoGUI methods
    methods = [
        _bypass_method_cdp_solve,        # CDP Mode, WebDriver disconnected
        _bypass_method_cdp_click,        # CDP native click, no PyAutoGUI
        _bypass_method_cdp_gui_click,    # CDP with PyAutoGUI (most human-like)
        _bypass_method_handle_captcha,   # TAB+SPACEBAR via PyAutoGUI (UC Mode)
        _bypass_method_click_captcha,    # Direct click via PyAutoGUI (UC Mode)
        _bypass_method_humanlike,        # Last resort with scroll/refresh
    ]

    for try_count in range(max_retries):
        # Check for cancellation before each attempt
        if cancel_flag and cancel_flag.is_set():
            logger.info("Bypass cancelled by user")
            raise BypassCancelledException("Bypass cancelled")

        if _is_bypassed(sb):
            if try_count == 0:
                logger.info("Page already bypassed (no challenge or auto-solved by uc_open_with_reconnect)")
            return True

        # Log challenge type for debugging (but don't branch on it)
        challenge_type = _detect_challenge_type(sb)
        logger.info(f"Challenge detected: {challenge_type}")

        method = methods[try_count % len(methods)]
        logger.info(f"Bypass attempt {try_count + 1}/{max_retries} using {method.__name__}")

        # Progressive backoff with cancellation checks (randomized)
        if try_count > 0:
            wait_time = min(random.uniform(2, 4) * try_count, 12)
            logger.info(f"Waiting {wait_time:.1f}s before trying...")
            # Check cancellation during wait (check every second)
            for _ in range(int(wait_time)):
                if cancel_flag and cancel_flag.is_set():
                    logger.info("Bypass cancelled during wait")
                    raise BypassCancelledException("Bypass cancelled")
                time.sleep(1)
            # Sleep remaining fractional second
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

def _get_chromium_args():
    """Build Chrome arguments dynamically, pre-resolving hostnames via Python's DNS.

    Instead of trying to configure Chrome's DNS (which is unreliable), we pre-resolve
    AA hostnames using Python's patched socket (which uses DoH/custom DNS) and pass
    the resolved IPs directly to Chrome via --host-resolver-rules. This bypasses
    Chrome's DNS entirely for those hosts.
    """
    arguments = [
        # Ignore certificate and SSL errors (similar to curl's --insecure)
        "--ignore-certificate-errors",
        "--ignore-ssl-errors",
        "--allow-running-insecure-content",
        "--ignore-certificate-errors-spki-list",
        "--ignore-certificate-errors-skip-list"
    ]
    
    # Conditionally add verbose logging arguments
    if app_config.get("DEBUG", False):
        arguments.extend([
            "--enable-logging", # Enable Chrome browser logging
            "--v=1",            # Set verbosity level for Chrome logs
            "--log-file=" + str(LOG_DIR / "chrome_browser.log")
        ])

    # Add proxy settings if configured
    proxies = _get_proxies()
    if proxies:
        proxy_url = proxies.get('https') or proxies.get('http')
        if proxy_url:
            arguments.append(f'--proxy-server={proxy_url}')

    # --- Pre-resolve AA hostnames and map them directly in Chrome ---
    # This bypasses Chrome's DNS entirely - we resolve via Python's patched socket.getaddrinfo
    # (which uses DoH/Cloudflare when system DNS fails) and tell Chrome to use those IPs
    host_rules = []
    
    try:
        aa_urls = network.get_available_aa_urls()
        for url in aa_urls:
            hostname = urlparse(url).hostname
            if hostname:
                try:
                    # Use socket.getaddrinfo which IS patched by our network module
                    # (DoH/Cloudflare if system DNS failed)
                    # getaddrinfo returns: [(family, type, proto, canonname, sockaddr), ...]
                    # sockaddr for IPv4 is (ip, port)
                    results = socket.getaddrinfo(hostname, 443, socket.AF_INET)
                    if results:
                        ip = results[0][4][0]  # First result, sockaddr tuple, IP address
                        host_rules.append(f"MAP {hostname} {ip}")
                        logger.debug(f"Chrome: Pre-resolved {hostname} -> {ip}")
                    else:
                        logger.warning(f"Chrome: No addresses returned for {hostname}")
                except socket.gaierror as e:
                    logger.warning(f"Chrome: Could not pre-resolve {hostname}: {e}")
        
        if host_rules:
            # Join all rules with comma, e.g. "MAP host1 ip1, MAP host2 ip2"
            rules_str = ", ".join(host_rules)
            arguments.append(f'--host-resolver-rules={rules_str}')
            logger.info(f"Chrome: Using host resolver rules for {len(host_rules)} hosts")
        else:
            logger.warning("Chrome: No hosts could be pre-resolved, Chrome will use its own DNS")
            
    except Exception as e:
        logger.error_trace(f"Error pre-resolving hostnames for Chrome: {e}")
    
    return arguments

def _get(url, retry: Optional[int] = None, cancel_flag: Optional[Event] = None):
    retry = retry if retry is not None else app_config.MAX_RETRY
    # Check for cancellation before starting
    if cancel_flag and cancel_flag.is_set():
        logger.info("Bypass cancelled before starting")
        raise BypassCancelledException("Bypass cancelled")

    try:
        logger.info(f"SB_GET: {url}")
        sb = _get_driver()

        # Enhanced page loading with better error handling
        logger.debug("Opening URL with SeleniumBase...")
        sb.uc_open_with_reconnect(url, app_config.DEFAULT_SLEEP)
        time.sleep(app_config.DEFAULT_SLEEP)

        # Check for cancellation after page load
        if cancel_flag and cancel_flag.is_set():
            logger.info("Bypass cancelled after page load")
            raise BypassCancelledException("Bypass cancelled")

        # Log current page title and URL for debugging
        try:
            current_url = sb.get_current_url()
            current_title = sb.get_title()
            logger.debug(f"Page loaded - URL: {current_url}, Title: {current_title}")
        except Exception as debug_e:
            logger.debug(f"Could not get page info: {debug_e}")

        # Attempt bypass with cancellation support
        logger.debug("Starting bypass process...")
        if _bypass(sb, cancel_flag=cancel_flag):
            logger.info("Bypass successful.")
            # Extract cookies for sharing with requests library
            _extract_cookies_from_driver(sb, url)
            return sb.page_source
        else:
            logger.warning("Bypass completed but page still shows Cloudflare protection")
            # Log page content for debugging (truncated)
            try:
                page_text = sb.get_text("body")[:500] + "..." if len(sb.get_text("body")) > 500 else sb.get_text("body")
                logger.debug(f"Page content: {page_text}")
            except Exception:
                pass

    except BypassCancelledException:
        raise
    except Exception as e:
        error_details = f"Exception type: {type(e).__name__}, Message: {str(e)}"
        stack_trace = traceback.format_exc()

        if retry == 0:
            logger.error(f"Failed to initialize browser after all retries: {error_details}")
            logger.debug(f"Full stack trace: {stack_trace}")
            _reset_driver()
            raise e

        logger.warning(f"Failed to bypass Cloudflare (retry {app_config.MAX_RETRY - retry + 1}/{app_config.MAX_RETRY}): {error_details}")
        logger.debug(f"Stack trace: {stack_trace}")

        # Reset driver on certain errors
        error_type = type(e).__name__
        if error_type in ("WebDriverException", "SessionNotCreatedException", "TimeoutException", "MaxRetryError"):
            logger.info("Restarting bypasser due to browser error...")
            _reset_driver()

    # Check for cancellation before retry
    if cancel_flag and cancel_flag.is_set():
        logger.info("Bypass cancelled before retry")
        raise BypassCancelledException("Bypass cancelled")

    return _get(url, retry - 1, cancel_flag)

def get(url, retry: Optional[int] = None, cancel_flag: Optional[Event] = None):
    """Fetch a URL with protection bypass."""
    retry = retry if retry is not None else app_config.MAX_RETRY
    global LAST_USED
    with LOCKED:
        # Check for cookies AFTER acquiring lock - another request may have
        # completed bypass while we were waiting, making Chrome unnecessary
        parsed = urlparse(url)
        cookies = get_cf_cookies_for_domain(parsed.hostname or "")
        if cookies:
            try:
                response = requests.get(url, cookies=cookies, proxies=_get_proxies(), timeout=(5, 10))
                if response.status_code == 200:
                    logger.debug(f"Cookies available after lock wait - skipped Chrome")
                    LAST_USED = time.time()
                    return response.text
            except Exception:
                pass  # Fall through to Chrome bypass

        ret = _get(url, retry, cancel_flag)
        LAST_USED = time.time()
        return ret

def _init_driver():
    global DRIVER
    if DRIVER:
        _reset_driver()
    # Build Chrome args dynamically to pick up current DNS settings from network module
    chromium_args = _get_chromium_args()
    logger.debug(f"Initializing Chrome driver with args: {chromium_args}")
    driver = Driver(uc=True, headless=False, incognito=True, size=f"{VIRTUAL_SCREEN_SIZE[0]},{VIRTUAL_SCREEN_SIZE[1]}", chromium_arg=chromium_args)
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
    display = Display(visible=False, size=VIRTUAL_SCREEN_SIZE)
    display.start()
    DISPLAY["xvfb"] = display
    logger.info("Virtual display started")
    time.sleep(app_config.DEFAULT_SLEEP)
    _reset_pyautogui_display_state()


def _get_driver():
    global DRIVER, DISPLAY, LAST_USED
    logger.info("Getting driver...")
    LAST_USED = time.time()
    
    _ensure_display_initialized()
    
    # Start FFmpeg recording on first actual bypass request (not during warmup)
    # This ensures we only record active bypass sessions, not idle time
    if app_config.get("DEBUG", False) and DISPLAY["xvfb"] and not DISPLAY["ffmpeg"]:
        RECORDING_DIR.mkdir(parents=True, exist_ok=True)
        display = DISPLAY["xvfb"]
        timestamp = datetime.now().strftime("%y%m%d-%H%M%S")
        output_file = RECORDING_DIR / f"screen_recording_{timestamp}.mp4"

        ffmpeg_cmd = [
            "ffmpeg",
            "-y",
            "-f", "x11grab",
            "-video_size", f"{VIRTUAL_SCREEN_SIZE[0]}x{VIRTUAL_SCREEN_SIZE[1]}",
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
        logger.info("Starting FFmpeg recording to %s", output_file)
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

def _reset_driver():
    """Reset the browser driver and cleanup all associated processes."""
    logger.log_resource_usage()
    logger.info("Shutting down Cloudflare bypasser...")
    global DRIVER, DISPLAY
    
    # Quit driver
    if DRIVER:
        try:
            DRIVER.quit()
        except Exception as e:
            logger.warning(f"Error quitting driver: {e}")
        DRIVER = None
    
    # Stop virtual display
    if DISPLAY["xvfb"]:
        try:
            DISPLAY["xvfb"].stop()
        except Exception as e:
            logger.warning(f"Error stopping display: {e}")
        DISPLAY["xvfb"] = None
    
    # Stop ffmpeg recording
    if DISPLAY["ffmpeg"]:
        try:
            DISPLAY["ffmpeg"].send_signal(signal.SIGINT)
        except Exception as e:
            logger.debug(f"Error stopping ffmpeg: {e}")
        DISPLAY["ffmpeg"] = None
    
    # Kill any lingering processes
    time.sleep(0.5)
    for process in ["Xvfb", "ffmpeg", "chrom"]:
        try:
            os.system(f"pkill -f {process}")
        except Exception as e:
            logger.debug(f"Error killing {process}: {e}")
    
    time.sleep(0.5)
    logger.info("Cloudflare bypasser shut down (browser and display stopped)")
    logger.log_resource_usage()

def _restart_chrome_only():
    """Restart just Chrome (not the display) to pick up new DNS settings.

    Called when DNS provider rotates in auto mode. The display is kept running
    to avoid the slower full restart. Chrome will be re-initialized with fresh
    pre-resolved IPs from the new DNS provider.
    """
    global DRIVER, LAST_USED

    logger.debug("Restarting Chrome to apply new DNS settings...")

    # Quit existing driver
    if DRIVER:
        try:
            DRIVER.quit()
        except Exception as e:
            logger.debug(f"Error quitting driver during DNS rotation restart: {e}")
        DRIVER = None

    # Kill any lingering Chrome processes (same pattern as _reset_driver)
    try:
        os.system("pkill -f chrom")
    except Exception as e:
        logger.debug(f"Error killing chrome processes: {e}")

    time.sleep(0.5)

    # Re-initialize driver with new DNS settings
    # _get_chromium_args() will re-resolve hostnames using the new DNS
    try:
        _init_driver()
        LAST_USED = time.time()
        logger.debug("Chrome restarted with updated DNS settings")
    except Exception as e:
        logger.warning(f"Failed to restart Chrome after DNS rotation: {e}")
        # Don't raise - the bypasser can try again on next request


def _on_dns_rotation(provider_name: str, servers: list, doh_url: str) -> None:
    """Callback invoked when network.py rotates DNS provider.

    If Chrome is currently running, schedule a restart in the background.
    This is async to avoid blocking the request that triggered DNS rotation.
    """
    global DRIVER, _dns_rotation_pending

    if DRIVER is None:
        return

    # Always set pending flag - the restart will happen asynchronously
    # This avoids blocking the current request (which triggered DNS rotation)
    with _dns_rotation_lock:
        if _dns_rotation_pending:
            return  # Already scheduled
        _dns_rotation_pending = True

    def _async_restart():
        global _dns_rotation_pending
        logger.debug(f"DNS rotated to {provider_name} - restarting Chrome in background")
        with LOCKED:
            # Clear flag before restart (under lock to be safe)
            with _dns_rotation_lock:
                _dns_rotation_pending = False
            _restart_chrome_only()

    restart_thread = threading.Thread(target=_async_restart, daemon=True)
    restart_thread.start()


def _cleanup_driver():
    """Reset driver after inactivity timeout.

    Uses a longer timeout (4x) when UI clients are connected to avoid
    resetting while users are actively browsing. After all clients disconnect,
    the standard timeout applies as a grace period before shutdown.
    """
    global LAST_USED

    # Check for active UI connections
    try:
        from cwa_book_downloader.api.websocket import ws_manager
        has_active_clients = ws_manager.has_active_connections()
    except ImportError:
        ws_manager = None
        has_active_clients = False

    # Use longer timeout when UI is connected (user might be browsing)
    timeout_minutes = app_config.BYPASS_RELEASE_INACTIVE_MIN
    if has_active_clients:
        timeout_minutes *= 4  # 20 min default when UI open vs 5 min after disconnect

    with LOCKED:
        if LAST_USED and time.time() - LAST_USED >= timeout_minutes * 60:
            logger.info(f"Cloudflare bypasser idle for {timeout_minutes} min - shutting down to free resources")
            _reset_driver()
            LAST_USED = None

            # If clients are still connected, request warmup on next connect so the
            # bypasser restarts when the user becomes active again
            if has_active_clients and ws_manager:
                ws_manager.request_warmup_on_next_connect()
                logger.debug("Requested warmup on next client connect (clients still connected)")

def _cleanup_loop():
    while True:
        _cleanup_driver()
        time.sleep(max(app_config.BYPASS_RELEASE_INACTIVE_MIN / 2, 1))

def _init_cleanup_thread():
    cleanup_thread = threading.Thread(target=_cleanup_loop)
    cleanup_thread.daemon = True
    cleanup_thread.start()

def warmup():
    """Pre-initialize the virtual display and Chrome browser to eliminate cold start time.
    
    This function can be called when a user connects to the web UI to
    warm up the bypasser environment before it's actually needed.
    Both the display and Chrome driver are initialized so the first
    bypass request is nearly instant.
    
    Warmup is skipped in the following scenarios:
    - BYPASS_WARMUP_ON_CONNECT is false (explicit disable)
    - Not running in Docker mode
    - USE_CF_BYPASS is disabled
    - AA_DONATOR_KEY is set (user has fast downloads, bypass rarely needed)
    
    Note: Even when warmup is skipped, the bypasser can still start on-demand
    when actually needed for a download.
    """
    global DRIVER, LAST_USED
    
    if not app_config.get("BYPASS_WARMUP_ON_CONNECT", True):
        logger.debug("Bypasser warmup disabled via BYPASS_WARMUP_ON_CONNECT")
        return

    if not env.DOCKERMODE:
        logger.debug("Bypasser warmup skipped - not in Docker mode")
        return

    if not app_config.get("USE_CF_BYPASS", True):
        logger.debug("Bypasser warmup skipped - CF bypass disabled")
        return

    if app_config.get("AA_DONATOR_KEY", ""):
        logger.debug("Bypasser warmup skipped - AA donator key set (fast downloads available)")
        return

    with LOCKED:
        if is_warmed_up():
            logger.debug("Bypasser already fully warmed up")
            return

        # Clean up any orphan processes from previous crashes before starting fresh.
        # This must be AFTER the is_warmed_up() check to avoid killing a healthy
        # Chrome browser that was started by a previous warmup.
        _cleanup_orphan_processes()

        # If we get here, either nothing is initialized OR the driver is unhealthy.
        # Reset any stale state before reinitializing to avoid the warmup thinking
        # things are already set up when the underlying processes are dead.
        if DRIVER is not None or DISPLAY["xvfb"] is not None:
            logger.info("Resetting stale bypasser state before warmup...")
            _reset_driver()

        logger.info("Warming up Cloudflare bypasser (pre-initializing display and browser)...")
        
        try:
            # Initialize virtual display (FFmpeg recording starts later on first actual request)
            _ensure_display_initialized()
            
            # Initialize Chrome driver
            if DRIVER is None:
                logger.info("Pre-initializing Chrome browser...")
                _init_driver()
                LAST_USED = time.time()
                logger.info("Chrome browser ready")
            
            logger.info("Bypasser warmup complete - ready for instant bypass")
            logger.log_resource_usage()
                
        except Exception as e:
            logger.warning(f"Failed to warm up bypasser: {e}")

def _is_driver_healthy() -> bool:
    """Check if the Chrome driver is actually responsive (not just non-None).

    The DRIVER variable can be non-None but the underlying Chrome process may have
    crashed silently. This function pings the driver to verify it's actually alive.
    """
    global DRIVER
    if DRIVER is None:
        return False

    try:
        # Try a simple operation that requires the driver to be responsive
        # get_current_url() is lightweight and doesn't change state
        DRIVER.get_current_url()
        return True
    except Exception as e:
        logger.warning(f"Driver health check failed: {type(e).__name__}: {e}")
        return False


def is_warmed_up() -> bool:
    """Check if the bypasser is fully warmed up (display and browser initialized and healthy)."""
    if DISPLAY["xvfb"] is None or DRIVER is None:
        return False
    return _is_driver_healthy()

def shutdown_if_idle():
    """Start the inactivity countdown when all WebSocket clients disconnect.
    
    Instead of immediately shutting down, this sets LAST_USED to start the
    inactivity timer. The cleanup loop will then shut down the bypasser after
    BYPASS_RELEASE_INACTIVE_MIN minutes, giving users a grace period to return
    (e.g., if they refresh the page or briefly navigate away).
    
    If there are active downloads, the timer naturally won't trigger until
    they complete since LAST_USED gets updated on each bypass operation.
    """
    global LAST_USED
    
    with LOCKED:
        if not is_warmed_up():
            logger.debug("Bypasser already shut down")
            return
        
        # Start the inactivity countdown
        LAST_USED = time.time()
        logger.info(f"All clients disconnected - bypasser will shut down after {app_config.BYPASS_RELEASE_INACTIVE_MIN} min of inactivity")

_init_cleanup_thread()

# Register for DNS rotation notifications so Chrome can restart with new DNS settings
# Only register if using the internal Chrome bypasser (not external FlareSolverr)
# Note: This module is only imported when internal bypasser is selected, so this check
# is redundant but kept for safety. Use app_config for consistency with other modules.
if app_config.get("USE_CF_BYPASS", True) and not app_config.get("USING_EXTERNAL_BYPASSER", False):
    network.register_dns_rotation_callback(_on_dns_rotation)


def get_bypassed_page(url: str, selector: Optional[network.AAMirrorSelector] = None, cancel_flag: Optional[Event] = None) -> Optional[str]:
    """Fetch HTML content from a URL using the internal Cloudflare Bypasser.

    Args:
        url: Target URL
        selector: Optional mirror selector for AA URL rewriting
        cancel_flag: Optional threading Event to signal cancellation

    Returns:
        str: HTML content if successful, None otherwise

    Raises:
        BypassCancelledException: If cancel_flag is set during operation
    """
    sel = selector or network.AAMirrorSelector()
    attempt_url = sel.rewrite(url)

    # Before using Chrome, check if cookies are available (from a previous bypass)
    # This helps concurrent downloads avoid unnecessary Chrome usage
    parsed = urlparse(attempt_url)
    hostname = parsed.hostname or ""
    cookies = get_cf_cookies_for_domain(hostname)
    if cookies:
        try:
            # Use stored UA - Cloudflare ties cf_clearance to the UA that solved the challenge
            headers = {}
            stored_ua = get_cf_user_agent_for_domain(hostname)
            if stored_ua:
                headers['User-Agent'] = stored_ua
            logger.debug(f"Trying request with cached cookies before Chrome: {attempt_url}")
            response = requests.get(attempt_url, cookies=cookies, headers=headers, proxies=_get_proxies(), timeout=(5, 10))
            if response.status_code == 200:
                logger.debug(f"Cached cookies worked, skipped Chrome bypass")
                return response.text
        except Exception:
            pass  # Fall through to Chrome bypass

    try:
        response_html = get(attempt_url, cancel_flag=cancel_flag)
    except BypassCancelledException:
        raise
    except Exception:
        # Check for cancellation before retry
        if cancel_flag and cancel_flag.is_set():
            raise BypassCancelledException("Bypass cancelled")
        # On failure, try mirror/DNS rotation for AA-like URLs
        new_base, action = sel.next_mirror_or_rotate_dns()
        if action in ("mirror", "dns") and new_base:
            attempt_url = sel.rewrite(url)
            response_html = get(attempt_url, cancel_flag=cancel_flag)
        else:
            raise

    logger.debug(f"Cloudflare Bypasser response length: {len(response_html)}")
    if response_html.strip() == "":
        raise requests.exceptions.RequestException("Failed to bypass Cloudflare")

    return response_html
