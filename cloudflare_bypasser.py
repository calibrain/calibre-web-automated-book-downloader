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

# --- SeleniumBase Import ---
from seleniumbase import Driver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

import network
from logger import setup_logger
from env import MAX_RETRY, DEFAULT_SLEEP
from config import PROXIES, CUSTOM_DNS, DOH_SERVER, VIRTUAL_SCREEN_SIZE, RECORDING_DIR

logger = setup_logger(__name__)
network.init()

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

def _is_bypassed(sb) -> bool:
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
        
        # Enhanced verification texts for newer Cloudflare versions
        verification_texts = [
            "just a moment",
            "verify you are human",
            "verifying you are human",
            "needs to review the security of your connection before proceeding",
            "checking your browser",
            "checking connection",
            "attention required",
            "access denied",
            "needs to review the security of your connection",
            "checking the site connection security",
            "enable javascript and cookies to continue",
            "ray id",
            "cloudflare",
            "please wait",
            "ddos protection",
            "security check",
            "browser check",
            "moment please",
            "hold on",
            "loading",
            "one more step",
            "challenge"
        ]
        
        # Check for Cloudflare indicators
        for text in verification_texts:
            if text in title or text in body:
                logger.debug(f"Cloudflare indicator found: '{text}' in page")
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

def _bypass(sb, max_retries: int = MAX_RETRY) -> None:
    """Enhanced bypass function with multiple strategies"""
    try_count = 0
    methods = [_bypass_method_1, _bypass_method_2, _bypass_method_3]

    while not _is_bypassed(sb):
        if try_count >= max_retries:
            logger.warning("Exceeded maximum retries. Bypass failed.")
            break
            
        method_index = try_count % len(methods)
        method = methods[method_index]
        
        logger.info(f"Bypass attempt {try_count + 1} / {max_retries} using {method.__name__}")
        
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

    # --- Add Custom DNS settings ---
    try:
        if len(CUSTOM_DNS) > 0:
            if DOH_SERVER:
                logger.info(f"Configuring DNS over HTTPS (DoH) with server: {DOH_SERVER}")

                # TODO: This is probably broken and a halucination,
                # but it should still default to google DOH so its fine...
                arguments.extend(['--enable-features=DnsOverHttps', '--dns-over-https-mode=secure', f'--dns-over-https-servers="{DOH_SERVER}"'])
                doh_hostname = urlparse(DOH_SERVER).hostname
                if doh_hostname:
                    try:
                        arguments.append(f'--host-resolver-rules=MAP {doh_hostname} {socket.gethostbyname(doh_hostname)}')
                    except socket.gaierror:
                        logger.warning(f"Could not resolve DoH hostname: {doh_hostname}")
            elif CUSTOM_DNS:
                resolver_rules = [f"MAP * {dns_server}" for dns_server in CUSTOM_DNS]
                if resolver_rules:
                    arguments.append(f'--host-resolver-rules={",".join(resolver_rules)}') 
    except Exception as e:
        logger.error_trace(f"Error configuring DNS settings: {e}")
    return arguments

CHROMIUM_ARGS = _get_chromium_args()

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
    driver = Driver(uc=True, headless=False, size=f"{VIRTUAL_SCREEN_SIZE[0]},{VIRTUAL_SCREEN_SIZE[1]}", chromium_arg=CHROMIUM_ARGS)
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
