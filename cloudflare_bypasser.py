import time
import os
import socket
from urllib.parse import urlparse
import threading
import env
from env import LOG_DIR, DEBUG
import signal
from datetime import datetime
import subprocess
import requests
from typing import Optional

# --- SeleniumBase Import ---
from seleniumbase import Driver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

import network
from logger import setup_logger
from env import MAX_RETRY, DEFAULT_SLEEP
import config
from config import PROXIES, VIRTUAL_SCREEN_SIZE, RECORDING_DIR

logger = setup_logger(__name__)

DRIVER = None
DISPLAY = {
    "xvfb": None,
    "ffmpeg": None,
}
LAST_USED = None
LOCKED = threading.Lock()
TENTATIVE_CURRENT_URL = None

def _reset_pyautogui_display_state():
    try:
        import pyautogui
        import Xlib.display
        pyautogui._pyautogui_x11._display = (
                    Xlib.display.Display(os.environ['DISPLAY'])
                )
    except Exception as e:
        logger.warning(f"Error resetting pyautogui display state: {e}")

def _detect_challenge_type(sb) -> str:
    """Detect what type of challenge we're facing.
    
    Returns:
        str: 'cloudflare', 'ddos_guard', or 'none' if no challenge detected
    """
    try:
        try:
            title = sb.get_title().lower()
        except:
            title = ""
        try:
            body = sb.get_text("body").lower()
        except:
            body = ""
        try:
            page_source = sb.page_source.lower()
        except:
            page_source = ""
        try:
            current_url = sb.get_current_url()
        except:
            current_url = ""
        
        # DDOS-Guard indicators
        ddos_guard_indicators = [
            "ddos-guard",
            "ddos guard",
            "checking your browser before accessing",
            "complete the manual check to continue",
            "could not verify your browser automatically"
        ]
        for indicator in ddos_guard_indicators:
            if indicator in title or indicator in body or indicator in page_source:
                logger.debug(f"DDOS-Guard indicator found: '{indicator}'")
                return "ddos_guard"
        
        # Cloudflare indicators
        cloudflare_indicators = [
            "just a moment",
            "verify you are human",
            "verifying you are human",
            "cloudflare.com/products/turnstile"
        ]
        for indicator in cloudflare_indicators:
            if indicator in title or indicator in body:
                logger.debug(f"Cloudflare indicator found: '{indicator}'")
                return "cloudflare"
        
        # Check URL patterns
        if "cf-" in body or "cloudflare" in current_url.lower():
            return "cloudflare"
        if "/cdn-cgi/" in current_url:
            return "cloudflare"
            
        return "none"
        
    except Exception as e:
        logger.warning(f"Error detecting challenge type: {e}")
        return "none"

def _is_bypassed(sb, escape_emojis : bool = True) -> bool:
    """Enhanced bypass detection with more comprehensive checks"""
    try:
        # Get page information with error handling
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
        
        # Check if page is too long, if so we are probably bypassed
        if len(body.strip()) > 100000:
            logger.debug(f"Page content too long, we are probably bypassed len: {len(body.strip())}")
            return True
        
        # Detect if there is an emoji in the page, any utf8 emoji, if so we are probably bypassed
        if escape_emojis:
            import emoji
            emoji_list = emoji.emoji_list(body)
            if len(emoji_list) >= 3:
                logger.debug(f"Detected emoji in page, we are probably bypassed len: {len(emoji_list)}")
                return True

        # Enhanced verification texts for newer Cloudflare versions
        verification_texts = [
            "just a moment",
            "verify you are human",
            "verifying you are human",
            "cloudflare.com/products/turnstile/?utm_source=turnstile"
        ]
        
        # Check for Cloudflare indicators
        for text in verification_texts:
            if text in title or text in body:
                logger.debug(f"Cloudflare indicator found: '{text}' in page")
                return False
        
        # DDOS-Guard indicators - these mean we're NOT bypassed
        ddos_guard_texts = [
            "ddos-guard",
            "ddos guard", 
            "checking your browser before accessing",
            "complete the manual check to continue",
            "could not verify your browser automatically"
        ]
        for text in ddos_guard_texts:
            if text in title or text in body:
                logger.debug(f"DDOS-Guard indicator found: '{text}' in page")
                return False
        
        # Additional checks for specific Cloudflare patterns
        if "cf-" in body or "cloudflare" in current_url.lower():
            logger.debug("Cloudflare patterns detected in page")
            return False
            
        # Check if we're still on a challenge page (common Cloudflare pattern)
        if "/cdn-cgi/" in current_url:
            logger.debug("Still on Cloudflare CDN challenge page")
            return False
            
        # If page is mostly empty, it might still be loading
        if len(body.strip()) < 50:
            logger.debug("Page content too short, might still be loading")
            return False
            
        logger.debug(f"Bypass check passed - Title: '{title[:100]}', Body length: {len(body)}")
        return True
        
    except Exception as e:
        logger.warning(f"Error checking bypass status: {e}")
        # If we can't check, assume we're not bypassed
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
        import random
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
    """DDOS-Guard bypass: Click the checkbox using iframe detection"""
    try:
        import random
        logger.debug("Attempting DDOS-Guard bypass method 1: iframe checkbox click")
        
        # Wait for the page to fully load
        time.sleep(random.uniform(2, 4))
        
        # DDOS-Guard typically uses an iframe for the checkbox
        # Try to find and switch to the iframe
        try:
            # Look for common DDOS-Guard iframe patterns
            iframes = sb.find_elements("iframe")
            logger.debug(f"Found {len(iframes)} iframes on page")
            
            for i, iframe in enumerate(iframes):
                try:
                    # Get iframe attributes for debugging
                    iframe_src = iframe.get_attribute("src") or ""
                    iframe_id = iframe.get_attribute("id") or ""
                    iframe_class = iframe.get_attribute("class") or ""
                    logger.debug(f"Iframe {i}: src={iframe_src[:100]}, id={iframe_id}, class={iframe_class}")
                    
                    # Check if this looks like a DDOS-Guard captcha iframe
                    if "ddos" in iframe_src.lower() or "captcha" in iframe_src.lower() or "check" in iframe_src.lower():
                        sb.switch_to_frame(iframe)
                        logger.debug(f"Switched to iframe {i}")
                        
                        # Try to find and click the checkbox
                        time.sleep(random.uniform(1, 2))
                        
                        # Look for checkbox elements
                        checkbox_selectors = [
                            "input[type='checkbox']",
                            ".checkbox",
                            "#checkbox",
                            "[role='checkbox']",
                            ".cb-i",  # Common DDOS-Guard class
                            "#ddos-guard-checkbox"
                        ]
                        
                        for selector in checkbox_selectors:
                            try:
                                if sb.is_element_visible(selector):
                                    logger.debug(f"Found checkbox with selector: {selector}")
                                    sb.click(selector)
                                    time.sleep(random.uniform(2, 4))
                                    sb.switch_to_default_content()
                                    time.sleep(3)
                                    return _is_bypassed(sb)
                            except:
                                continue
                        
                        sb.switch_to_default_content()
                except Exception as iframe_e:
                    logger.debug(f"Error with iframe {i}: {iframe_e}")
                    try:
                        sb.switch_to_default_content()
                    except:
                        pass
                    continue
        except Exception as e:
            logger.debug(f"Error finding iframes: {e}")
        
        return False
    except Exception as e:
        logger.debug(f"DDOS-Guard method 1 failed: {e}")
        return False

def _bypass_ddos_guard_method_2(sb) -> bool:
    """DDOS-Guard bypass: Use pyautogui to click the checkbox directly"""
    try:
        import random
        logger.debug("Attempting DDOS-Guard bypass method 2: pyautogui coordinate click")
        
        # Wait for the page to fully load
        time.sleep(random.uniform(2, 4))
        
        try:
            import pyautogui
            
            # Get the page dimensions
            window_size = sb.get_window_size()
            width = window_size.get("width", 1920)
            height = window_size.get("height", 1080)
            
            logger.debug(f"Window size: {width}x{height}")
            
            # DDOS-Guard checkbox is typically in the center of the page
            # The checkbox itself is usually slightly left of center
            # Based on the screenshot, the checkbox appears to be around 40% from left, 55% from top
            checkbox_x = int(width * 0.35)  # Slightly left of center where checkbox typically is
            checkbox_y = int(height * 0.55)  # Slightly below center
            
            logger.debug(f"Clicking at coordinates: ({checkbox_x}, {checkbox_y})")
            
            # Move mouse smoothly to simulate human behavior
            current_x, current_y = pyautogui.position()
            
            # Small random offset for human-like behavior
            offset_x = random.randint(-5, 5)
            offset_y = random.randint(-5, 5)
            
            # Move to checkbox location with human-like motion
            pyautogui.moveTo(
                checkbox_x + offset_x, 
                checkbox_y + offset_y, 
                duration=random.uniform(0.3, 0.7)
            )
            time.sleep(random.uniform(0.1, 0.3))
            
            # Click
            pyautogui.click()
            
            # Wait for verification
            time.sleep(random.uniform(3, 5))
            
            return _is_bypassed(sb)
            
        except ImportError:
            logger.debug("pyautogui not available")
            return False
        except Exception as e:
            logger.debug(f"pyautogui click failed: {e}")
            return False
            
    except Exception as e:
        logger.debug(f"DDOS-Guard method 2 failed: {e}")
        return False

def _bypass_ddos_guard_method_3(sb) -> bool:
    """DDOS-Guard bypass: Use SeleniumBase's built-in captcha handling"""
    try:
        import random
        logger.debug("Attempting DDOS-Guard bypass method 3: SeleniumBase uc_gui methods")
        
        # Wait for the page to load
        time.sleep(random.uniform(2, 4))
        
        # Try different SeleniumBase UC methods
        try:
            # uc_gui_click_captcha sometimes works for DDOS-Guard too
            sb.uc_gui_click_captcha()
            time.sleep(random.uniform(3, 5))
            if _is_bypassed(sb):
                return True
        except Exception as e:
            logger.debug(f"uc_gui_click_captcha failed: {e}")
        
        # Try clicking on any visible checkbox-like element
        try:
            checkbox_patterns = [
                "//input[@type='checkbox']",
                "//*[contains(@class, 'checkbox')]",
                "//*[contains(@class, 'cb-')]",
                "//*[contains(text(), \"I'm not a robot\")]/..",
                "//*[contains(text(), 'not a robot')]/.."
            ]
            
            for pattern in checkbox_patterns:
                try:
                    elements = sb.find_elements(f"xpath:{pattern}")
                    for elem in elements:
                        if elem.is_displayed():
                            logger.debug(f"Found clickable element with pattern: {pattern}")
                            elem.click()
                            time.sleep(random.uniform(3, 5))
                            if _is_bypassed(sb):
                                return True
                except:
                    continue
        except Exception as e:
            logger.debug(f"Checkbox pattern search failed: {e}")
        
        return False
    except Exception as e:
        logger.debug(f"DDOS-Guard method 3 failed: {e}")
        return False

def _bypass(sb, max_retries: int = MAX_RETRY) -> None:
    """Enhanced bypass function with multiple strategies for different protection types"""
    try_count = 0
    
    # Cloudflare-specific methods
    cloudflare_methods = [_bypass_method_1, _bypass_method_2, _bypass_method_3]
    
    # DDOS-Guard-specific methods
    ddos_guard_methods = [_bypass_ddos_guard_method_1, _bypass_ddos_guard_method_2, _bypass_ddos_guard_method_3]

    while not _is_bypassed(sb):
        if try_count >= max_retries:
            logger.warning("Exceeded maximum retries. Bypass failed.")
            break
        
        # Detect challenge type on each iteration (it might change after attempts)
        challenge_type = _detect_challenge_type(sb)
        logger.info(f"Detected challenge type: {challenge_type}")
        
        # Select methods based on challenge type
        if challenge_type == "ddos_guard":
            methods = ddos_guard_methods
        elif challenge_type == "cloudflare":
            methods = cloudflare_methods
        else:
            # Unknown challenge, try all methods
            methods = cloudflare_methods + ddos_guard_methods
            
        method_index = try_count % len(methods)
        method = methods[method_index]
        
        logger.info(f"Bypass attempt {try_count + 1} / {max_retries} using {method.__name__} (challenge: {challenge_type})")
        
        try_count += 1

        # Progressive backoff: wait longer between retries
        wait_time = min(DEFAULT_SLEEP * (try_count - 1), 15)
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
                        logger.info(f"Chrome: Pre-resolved {hostname} -> {ip}")
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
        # Enhanced error logging with full stack trace
        import traceback
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
            logger.info("Resetting driver due to WebDriver error...")
            _reset_driver()
            
    return _get(url, retry - 1)

def get(url, retry : int = MAX_RETRY):
    global LOCKED, TENTATIVE_CURRENT_URL, LAST_USED
    with LOCKED:
        TENTATIVE_CURRENT_URL = url
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

def _get_driver():
    global DRIVER, DISPLAY
    global LAST_USED
    logger.info("Getting driver...")
    LAST_USED = time.time()
    if env.DOCKERMODE and env.USE_CF_BYPASS and not DISPLAY["xvfb"]:
        from pyvirtualdisplay import Display
        display = Display(visible=False, size=VIRTUAL_SCREEN_SIZE)
        display.start()
        logger.info("Display started")
        DISPLAY["xvfb"] = display
        time.sleep(DEFAULT_SLEEP)
        _reset_pyautogui_display_state()

        if env.DEBUG:
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
    logger.log_resource_usage()
    logger.info("Resetting driver...")
    global DRIVER, DISPLAY
    if DRIVER:
        try:
            DRIVER.quit()
            DRIVER = None
        except Exception as e:
            logger.warning(f"Error quitting driver: {e}")
        time.sleep(0.5)
    if DISPLAY["xvfb"]:
        try:
            DISPLAY["xvfb"].stop()
            DISPLAY["xvfb"] = None
        except Exception as e:
            logger.warning(f"Error stopping display: {e}")
        time.sleep(0.5)
    try:
        os.system("pkill -f Xvfb")
    except Exception as e:
        logger.debug(f"Error killing Xvfb: {e}")
    time.sleep(0.5)
    if DISPLAY["ffmpeg"]:
        try:
            DISPLAY["ffmpeg"].send_signal(signal.SIGINT)
            DISPLAY["ffmpeg"] = None
        except Exception as e:
            logger.debug(f"Error stopping ffmpeg: {e}")
        time.sleep(0.5)
    try:
        os.system("pkill -f ffmpeg")
    except Exception as e:
        logger.debug(f"Error killing ffmpeg: {e}")
    time.sleep(0.5)
    try:
        os.system("pkill -f chrom")
    except Exception as e:
        logger.debug(f"Error killing chrom: {e}")
    time.sleep(0.5)
    logger.info("Driver reset.")
    logger.log_resource_usage()

def _cleanup_driver():
    global LOCKED
    global LAST_USED
    with LOCKED:
        if LAST_USED:
            if time.time() - LAST_USED >= env.BYPASS_RELEASE_INACTIVE_MIN * 60:
                _reset_driver()
                LAST_USED = None
                logger.info("Driver reset due to inactivity.")

def _cleanup_loop():
    while True:
        _cleanup_driver()
        time.sleep(max(env.BYPASS_RELEASE_INACTIVE_MIN / 2, 1))

def _init_cleanup_thread():
    cleanup_thread = threading.Thread(target=_cleanup_loop)
    cleanup_thread.daemon = True
    cleanup_thread.start()

def wait_for_result(func, timeout : int = 10, condition : any = True):
    start_time = time.time()
    while time.time() - start_time < timeout:
        result = func()
        if condition(result):
            return result
        time.sleep(0.5)
    return None
_init_cleanup_thread()


def get_bypassed_page(url: str, selector: Optional[network.AAMirrorSelector] = None) -> Optional[str]:
    """Fetch HTML content from a URL using the internal Cloudflare Bypasser.

    Args:
        url: Target URL
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
