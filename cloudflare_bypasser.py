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
from selenium.common.exceptions import TimeoutException, NoSuchElementException

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

def _is_cloudflare_challenge_present(sb) -> bool:
    """Checks if the Cloudflare challenge is present on the page."""
    try:
        return sb.is_element_present('iframe[src*="challenges.cloudflare.com"]')
    except Exception as e:
        logger.debug(f"Error checking for Cloudflare challenge: {e}")
        return False

def _bypass_cloudflare(sb, max_retries: int = MAX_RETRY) -> bool:
    """Attempts to bypass the Cloudflare challenge."""
    for attempt in range(max_retries):
        if not _is_cloudflare_challenge_present(sb):
            logger.info("Cloudflare challenge not found or already bypassed.")
            return True

        logger.info(f"Bypass attempt {attempt + 1}/{max_retries}")
        try:
            # Switch to the Cloudflare iframe
            sb.switch_to_frame('iframe[src*="challenges.cloudflare.com"]')

            # Click the checkbox
            checkbox = sb.find_element(By.CSS_SELECTOR, 'input[type="checkbox"]')
            checkbox.click()
            
            # Wait for the page to load after the click
            time.sleep(5) 
            
            # Switch back to the main content
            sb.switch_to_default_content()
            
            # Check if bypass was successful
            if not _is_cloudflare_challenge_present(sb):
                logger.info("Successfully bypassed Cloudflare.")
                return True

        except NoSuchElementException:
            logger.warning("Cloudflare checkbox not found.")
            # If the checkbox isn't there, maybe it's a different kind of challenge.
            # We will just wait and see if it resolves itself.
            time.sleep(10)
            if not _is_cloudflare_challenge_present(sb):
                logger.info("Cloudflare challenge resolved after waiting.")
                return True
                
        except Exception as e:
            logger.error(f"An error occurred during bypass attempt: {e}")
            time.sleep(DEFAULT_SLEEP)
            
    logger.error("Failed to bypass Cloudflare after multiple attempts.")
    return False

def _get_chromium_args():
    arguments = []
    if DEBUG:
        arguments.extend([
            "--enable-logging",
            "--v=1",
            "--log-file=" + str(LOG_DIR / "chrome_browser.log")
        ])
    if PROXIES:
        proxy_url = PROXIES.get('https') or PROXIES.get('http')
        if proxy_url:
            arguments.append(f'--proxy-server={proxy_url}')
    try:
        if CUSTOM_DNS and DOH_SERVER:
            logger.info(f"Configuring DNS over HTTPS (DoH) with server: {DOH_SERVER}")
            arguments.extend([
                '--enable-features=DnsOverHttps',
                '--dns-over-https-mode=secure',
                f'--dns-over-https-servers="{DOH_SERVER}"'
            ])
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
        logger.error(f"Error configuring DNS settings: {e}")
    return arguments

CHROMIUM_ARGS = _get_chromium_args()

def _get(url, retry: int = MAX_RETRY):
    try:
        logger.info(f"SB_GET: {url}")
        sb = _get_driver()
        sb.uc_open_with_reconnect(url, DEFAULT_SLEEP)
        time.sleep(DEFAULT_SLEEP)
        
        if _bypass_cloudflare(sb):
            logger.info("Cloudflare bypass was successful.")
            return sb.page_source
        else:
            raise Exception("Failed to bypass Cloudflare.")

    except Exception as e:
        if retry <= 0:
            logger.error(f"Failed to get URL after multiple retries: {e}")
            _reset_driver()
            raise e
        logger.warning(f"Failed to get URL: {e}. Retrying...")
        _reset_driver() # Reset driver on failure before retrying
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
    driver = Driver(
        uc=True,
        headless=False,
        size=f"{VIRTUAL_SCREEN_SIZE[0]},{VIRTUAL_SCREEN_SIZE[1]}",
        chromium_arg=" ".join(CHROMIUM_ARGS) # Pass args as a single string
    )
    DRIVER = driver
    time.sleep(DEFAULT_SLEEP)
    return driver

def _get_driver():
    global DRIVER, DISPLAY, LAST_USED
    logger.info("Getting driver...")
    LAST_USED = time.time()

    if env.DOCKERMODE and env.USE_CF_BYPASS and not DISPLAY["xvfb"]:
        from pyvirtualdisplay import Display
        display = Display(visible=False, size=VIRTUAL_SCREEN_SIZE)
        display.start()
        logger.info("Virtual display started.")
        DISPLAY["xvfb"] = display
        time.sleep(DEFAULT_SLEEP)
        _reset_pyautogui_display_state()

        if env.DEBUG:
            timestamp = datetime.now().strftime("%y%m%d-%H%M%S")
            output_file = RECORDING_DIR / f"screen_recording_{timestamp}.mp4"
            ffmpeg_cmd = [
                "ffmpeg", "-y", "-f", "x11grab",
                "-video_size", f"{VIRTUAL_SCREEN_SIZE[0]}x{VIRTUAL_SCREEN_SIZE[1]}",
                "-i", f":{display.display}", "-c:v", "libx264", "-preset", "ultrafast",
                "-maxrate", "700k", "-bufsize", "1400k", "-crf", "36",
                "-pix_fmt", "yuv420p", "-tune", "animation",
                "-x264-params", "bframes=0:deblock=-1,-1", "-r", "15", "-an",
                output_file.as_posix(), "-nostats", "-loglevel", "error"
            ]
            logger.info(f"Starting FFmpeg recording to {output_file}")
            DISPLAY["ffmpeg"] = subprocess.Popen(ffmpeg_cmd)

    if not DRIVER:
        return _init_driver()
    return DRIVER


def _reset_driver():
    logger.info("Resetting driver...")
    global DRIVER, DISPLAY
    if DRIVER:
        try:
            DRIVER.quit()
        except Exception as e:
            logger.warning(f"Error quitting driver: {e}")
        finally:
            DRIVER = None

    if DISPLAY["ffmpeg"]:
        try:
            DISPLAY["ffmpeg"].send_signal(signal.SIGINT)
            DISPLAY["ffmpeg"].wait(timeout=5)
        except Exception as e:
            logger.debug(f"Error stopping ffmpeg: {e}")
        finally:
            DISPLAY["ffmpeg"] = None
            os.system("pkill -f ffmpeg")

    if DISPLAY["xvfb"]:
        try:
            DISPLAY["xvfb"].stop()
        except Exception as e:
            logger.warning(f"Error stopping display: {e}")
        finally:
            DISPLAY["xvfb"] = None
            os.system("pkill -f Xvfb")

    time.sleep(0.5)
    os.system("pkill -f chrom")
    time.sleep(0.5)
    logger.info("Driver reset.")

def _cleanup_driver():
    global LOCKED, LAST_USED
    with LOCKED:
        if LAST_USED and (time.time() - LAST_USED) >= env.BYPASS_RELEASE_INACTIVE_MIN * 60:
            logger.info("Driver reset due to inactivity.")
            _reset_driver()
            LAST_USED = None

def _cleanup_loop():
    while True:
        _cleanup_driver()
        # Sleep for 1 minute before checking again
        time.sleep(60)

def _init_cleanup_thread():
    cleanup_thread = threading.Thread(target=_cleanup_loop)
    cleanup_thread.daemon = True
    cleanup_thread.start()

_init_cleanup_thread()
