import os
import random
import signal
import socket
import subprocess
import threading
import time
import traceback
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse

import requests
from seleniumbase import Driver

import env
import network
from config import PROXIES, RECORDING_DIR, VIRTUAL_SCREEN_SIZE
from env import AA_DONATOR_KEY, BYPASS_WARMUP_ON_CONNECT, DEBUG, DEFAULT_SLEEP, LOG_DIR, MAX_RETRY
from logger import setup_logger

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

def _reset_pyautogui_display_state():
    try:
        import pyautogui
        import Xlib.display
        pyautogui._pyautogui_x11._display = Xlib.display.Display(os.environ['DISPLAY'])
    except Exception as e:
        logger.warning(f"Error resetting pyautogui display state: {e}")

def _get_page_info(sb) -> tuple[str, str, str]:
    """Extract page title, body text, and current URL safely."""
    try:
        title = sb.get_title().lower()
    except:
        title = ""
    try:
        body = sb.get_text("body").lower()
    except:
        body = ""
    try:
        current_url = sb.get_current_url()
    except:
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

def _bypass_method_1(sb) -> bool:
    """Original bypass method using uc_gui_click_captcha"""
    try:
        logger.debug("Attempting bypass method 1: uc_gui_click_captcha")
        sb.uc_gui_click_captcha()
        time.sleep(3)
        return _is_bypassed(sb)
    except Exception as e:
        logger.debug(f"Method 1 failed on first try: {e}")
        try:
            time.sleep(5)
            sb.wait_for_element_visible('body', timeout=10)
            sb.uc_gui_click_captcha()
            time.sleep(3)
            return _is_bypassed(sb)
        except Exception as e2:
            logger.debug(f"Method 1 failed on second try: {e2}")
            try:
                time.sleep(DEFAULT_SLEEP)
                sb.uc_gui_click_captcha()
                time.sleep(5)
                return _is_bypassed(sb)
            except Exception as e3:
                logger.debug(f"Method 1 completely failed: {e3}")
                return False

def _bypass_method_2(sb) -> bool:
    """Alternative bypass method using longer waits and manual interaction"""
    try:
        logger.debug("Attempting bypass method 2: wait and reload")
        # Wait longer for page to load completely
        time.sleep(10)
        
        # Try refreshing the page
        sb.refresh()
        time.sleep(8)
        
        # Check if bypass worked after refresh
        if _is_bypassed(sb):
            return True
            
        # Try clicking on the page center (sometimes helps trigger bypass)
        try:
            sb.click_if_visible("body", timeout=5)
            time.sleep(5)
        except:
            pass
            
        return _is_bypassed(sb)
    except Exception as e:
        logger.debug(f"Method 2 failed: {e}")
        return False

def _bypass_method_3(sb) -> bool:
    """Third bypass method using user-agent rotation and stealth mode"""
    try:
        logger.debug("Attempting bypass method 3: stealth approach")
        # Wait a random amount to appear more human
        wait_time = random.uniform(8, 15)
        time.sleep(wait_time)
        
        # Try to scroll the page (human-like behavior)
        try:
            sb.scroll_to_bottom()
            time.sleep(2)
            sb.scroll_to_top()
            time.sleep(3)
        except:
            pass
            
        # Check if this helped
        if _is_bypassed(sb):
            return True
            
        # Try the original captcha click as last resort
        try:
            sb.uc_gui_click_captcha()
            time.sleep(5)
        except:
            pass
            
        return _is_bypassed(sb)
    except Exception as e:
        logger.debug(f"Method 3 failed: {e}")
        return False

def _bypass_ddos_guard_method_1(sb) -> bool:
    """DDOS-Guard bypass: Use SeleniumBase's captcha handling (most reliable)"""
    try:
        logger.debug("Attempting DDOS-Guard bypass: SeleniumBase uc_gui methods")
        time.sleep(random.uniform(2, 4))
        
        # SeleniumBase's uc_gui_click_captcha often works for DDOS-Guard too
        try:
            sb.uc_gui_click_captcha()
            time.sleep(random.uniform(3, 5))
            if _is_bypassed(sb):
                return True
        except Exception as e:
            logger.debug(f"uc_gui_click_captcha failed: {e}")
        
        # Fallback: Try clicking visible checkbox-like elements
        checkbox_patterns = [
            "//input[@type='checkbox']",
            "//*[contains(@class, 'checkbox')]",
            "//*[contains(@class, 'cb-')]",
        ]
        for pattern in checkbox_patterns:
            try:
                elements = sb.find_elements(f"xpath:{pattern}")
                for elem in elements:
                    if elem.is_displayed():
                        logger.debug(f"Clicking element with pattern: {pattern}")
                        elem.click()
                        time.sleep(random.uniform(3, 5))
                        if _is_bypassed(sb):
                            return True
            except:
                continue
        
        return False
    except Exception as e:
        logger.debug(f"DDOS-Guard method 1 failed: {e}")
        return False

def _bypass_ddos_guard_method_2(sb) -> bool:
    """DDOS-Guard bypass: Use pyautogui to click estimated checkbox location"""
    try:
        logger.debug("Attempting DDOS-Guard bypass: pyautogui coordinate click")
        time.sleep(random.uniform(2, 4))
        
        import pyautogui
        window_size = sb.get_window_size()
        width = window_size.get("width", 1920)
        height = window_size.get("height", 1080)
        
        # DDOS-Guard checkbox is typically around 35% from left, 55% from top
        checkbox_x = int(width * 0.35) + random.randint(-5, 5)
        checkbox_y = int(height * 0.55) + random.randint(-5, 5)
        
        logger.debug(f"Clicking at coordinates: ({checkbox_x}, {checkbox_y})")
        pyautogui.moveTo(checkbox_x, checkbox_y, duration=random.uniform(0.3, 0.7))
        time.sleep(random.uniform(0.1, 0.3))
        pyautogui.click()
        time.sleep(random.uniform(3, 5))
        
        return _is_bypassed(sb)
    except ImportError:
        logger.debug("pyautogui not available")
        return False
    except Exception as e:
        logger.debug(f"DDOS-Guard method 2 failed: {e}")
        return False

def _bypass(sb, max_retries: int = MAX_RETRY) -> None:
    """Bypass function with strategies for Cloudflare and DDOS-Guard protection."""
    cloudflare_methods = [_bypass_method_1, _bypass_method_2, _bypass_method_3]
    ddos_guard_methods = [_bypass_ddos_guard_method_1, _bypass_ddos_guard_method_2]

    for try_count in range(max_retries):
        if _is_bypassed(sb):
            return
        
        challenge_type = _detect_challenge_type(sb)
        logger.info(f"Detected challenge type: {challenge_type}")
        
        if challenge_type == "ddos_guard":
            methods = ddos_guard_methods
        elif challenge_type == "cloudflare":
            methods = cloudflare_methods
        else:
            methods = cloudflare_methods + ddos_guard_methods
            
        method = methods[try_count % len(methods)]
        logger.info(f"Bypass attempt {try_count + 1}/{max_retries} using {method.__name__}")

        # Progressive backoff
        wait_time = min(DEFAULT_SLEEP * try_count, 15)
        if wait_time > 0:
            logger.info(f"Waiting {wait_time}s before trying...")
            time.sleep(wait_time)

        try:
            if method(sb):
                logger.info(f"Bypass successful using {method.__name__}")
                return
        except Exception as e:
            logger.warning(f"Exception in {method.__name__}: {e}")

        logger.info(f"Bypass method {method.__name__} failed.")
    
    logger.warning("Exceeded maximum retries. Bypass failed.")

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
    if DEBUG:
        arguments.extend([
            "--enable-logging", # Enable Chrome browser logging
            "--v=1",            # Set verbosity level for Chrome logs
            "--log-file=" + str(LOG_DIR / "chrome_browser.log")
        ])

    # Add proxy settings if configured
    if PROXIES:
        proxy_url = PROXIES.get('https') or PROXIES.get('http')
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

def _get(url, retry : int = MAX_RETRY):
    try:
        logger.info(f"SB_GET: {url}")
        sb = _get_driver()
        
        # Enhanced page loading with better error handling
        logger.debug("Opening URL with SeleniumBase...")
        sb.uc_open_with_reconnect(url, DEFAULT_SLEEP)
        time.sleep(DEFAULT_SLEEP)
        
        # Log current page title and URL for debugging
        try:
            current_url = sb.get_current_url()
            current_title = sb.get_title()
            logger.debug(f"Page loaded - URL: {current_url}, Title: {current_title}")
        except Exception as debug_e:
            logger.debug(f"Could not get page info: {debug_e}")
        
        # Attempt bypass
        logger.debug("Starting bypass process...")
        _bypass(sb)
        
        if _is_bypassed(sb):
            logger.info("Bypass successful.")
            return sb.page_source
        else:
            logger.warning("Bypass completed but page still shows Cloudflare protection")
            # Log page content for debugging (truncated)
            try:
                page_text = sb.get_text("body")[:500] + "..." if len(sb.get_text("body")) > 500 else sb.get_text("body")
                logger.debug(f"Page content: {page_text}")
            except:
                pass
            
    except Exception as e:
        error_details = f"Exception type: {type(e).__name__}, Message: {str(e)}"
        stack_trace = traceback.format_exc()
        
        if retry == 0:
            logger.error(f"Failed to initialize browser after all retries: {error_details}")
            logger.debug(f"Full stack trace: {stack_trace}")
            _reset_driver()
            raise e
        
        logger.warning(f"Failed to bypass Cloudflare (retry {MAX_RETRY - retry + 1}/{MAX_RETRY}): {error_details}")
        logger.debug(f"Stack trace: {stack_trace}")
        
        # Reset driver on certain errors
        if "WebDriverException" in str(type(e)) or "SessionNotCreatedException" in str(type(e)):
            logger.info("Restarting bypasser due to browser error...")
            _reset_driver()
            
    return _get(url, retry - 1)

def get(url, retry : int = MAX_RETRY):
    """Fetch a URL with protection bypass."""
    global LAST_USED
    with LOCKED:
        ret = _get(url, retry)
        LAST_USED = time.time()
        return ret

def _init_driver():
    global DRIVER
    if DRIVER:
        _reset_driver()
    # Build Chrome args dynamically to pick up current DNS settings from network module
    chromium_args = _get_chromium_args()
    logger.debug(f"Initializing Chrome driver with args: {chromium_args}")
    driver = Driver(uc=True, headless=False, size=f"{VIRTUAL_SCREEN_SIZE[0]},{VIRTUAL_SCREEN_SIZE[1]}", chromium_arg=chromium_args)
    DRIVER = driver
    time.sleep(DEFAULT_SLEEP)
    return driver

def _ensure_display_initialized():
    """Initialize virtual display if needed. Must be called with LOCKED held."""
    global DISPLAY
    if DISPLAY["xvfb"] is not None:
        return
    if not (env.DOCKERMODE and env.USE_CF_BYPASS):
        return
    
    from pyvirtualdisplay import Display
    display = Display(visible=False, size=VIRTUAL_SCREEN_SIZE)
    display.start()
    DISPLAY["xvfb"] = display
    logger.info("Virtual display started")
    time.sleep(DEFAULT_SLEEP)
    _reset_pyautogui_display_state()


def _get_driver():
    global DRIVER, DISPLAY, LAST_USED
    logger.info("Getting driver...")
    LAST_USED = time.time()
    
    _ensure_display_initialized()
    
    # Start FFmpeg recording on first actual bypass request (not during warmup)
    # This ensures we only record active bypass sessions, not idle time
    if env.DEBUG and DISPLAY["xvfb"] and not DISPLAY["ffmpeg"]:
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

def _cleanup_driver():
    """Reset driver after inactivity timeout.
    
    Uses a longer timeout (4x) when UI clients are connected to avoid
    resetting while users are actively browsing. After all clients disconnect,
    the standard timeout applies as a grace period before shutdown.
    """
    global LAST_USED
    
    # Check for active UI connections
    try:
        from websocket_manager import ws_manager
        has_active_clients = ws_manager.has_active_connections()
    except ImportError:
        has_active_clients = False
    
    # Use longer timeout when UI is connected (user might be browsing)
    timeout_minutes = env.BYPASS_RELEASE_INACTIVE_MIN
    if has_active_clients:
        timeout_minutes *= 4  # 20 min default when UI open vs 5 min after disconnect
    
    with LOCKED:
        if LAST_USED and time.time() - LAST_USED >= timeout_minutes * 60:
            logger.info(f"Cloudflare bypasser idle for {timeout_minutes} min - shutting down to free resources")
            _reset_driver()
            LAST_USED = None

def _cleanup_loop():
    while True:
        _cleanup_driver()
        time.sleep(max(env.BYPASS_RELEASE_INACTIVE_MIN / 2, 1))

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
    
    if not BYPASS_WARMUP_ON_CONNECT:
        logger.debug("Bypasser warmup disabled via BYPASS_WARMUP_ON_CONNECT")
        return
    
    if not env.DOCKERMODE:
        logger.debug("Bypasser warmup skipped - not in Docker mode")
        return
    
    if not env.USE_CF_BYPASS:
        logger.debug("Bypasser warmup skipped - CF bypass disabled")
        return
    
    if AA_DONATOR_KEY:
        logger.debug("Bypasser warmup skipped - AA donator key set (fast downloads available)")
        return
    
    with LOCKED:
        if is_warmed_up():
            logger.debug("Bypasser already fully warmed up")
            return
        
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

def is_warmed_up() -> bool:
    """Check if the bypasser is fully warmed up (display and browser initialized)."""
    return DISPLAY["xvfb"] is not None and DRIVER is not None

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
        logger.info(f"All clients disconnected - bypasser will shut down after {env.BYPASS_RELEASE_INACTIVE_MIN} min of inactivity")

_init_cleanup_thread()


def get_bypassed_page(url: str, selector: Optional[network.AAMirrorSelector] = None) -> Optional[str]:
    """Fetch HTML content from a URL using the internal Cloudflare Bypasser.

    Args:
        url: Target URL
        selector: Optional mirror selector for AA URL rewriting
        
    Returns:
        str: HTML content if successful, None otherwise
    """
    sel = selector or network.AAMirrorSelector()
    attempt_url = sel.rewrite(url)
    try:
        response_html = get(attempt_url)
    except Exception:
        # On failure, try mirror/DNS rotation for AA-like URLs
        new_base, action = sel.next_mirror_or_rotate_dns()
        if action in ("mirror", "dns") and new_base:
            attempt_url = sel.rewrite(url)
            response_html = get(attempt_url)
        else:
            raise

    logger.debug(f"Cloudflare Bypasser response length: {len(response_html)}")
    if response_html.strip() != "":
        return response_html
    else:
        raise requests.exceptions.RequestException("Failed to bypass Cloudflare")
