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
    try:
        title = sb.get_title().lower()
        body = sb.get_text("body").lower()
        
        # Check both title and body for verification messages
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
        ]
        for text in verification_texts:
            if text in title.lower() or text in body.lower():
                return False
                
        return True
    except Exception as e:
        logger.debug(f"Error checking page title: {e}")
        return False

def _bypass_method_1(sb) -> bool:
    try:
        sb.uc_gui_click_captcha()
    except Exception as e:
        logger.debug_trace(f"Error clicking captcha: {e}")
        time.sleep(5)
        sb.wait_for_element_visible('body')
        try:
            sb.uc_gui_click_captcha()
        except Exception as e:
            logger.debug_trace(f"Error clicking captcha again: {e}")
            time.sleep(DEFAULT_SLEEP)
            sb.uc_gui_click_captcha()
    return _is_bypassed(sb)

def _bypass(sb, max_retries: int = MAX_RETRY) -> None:
    try_count = 0

    while not _is_bypassed(sb):
        if try_count >= max_retries:
            logger.warning("Exceeded maximum retries. Bypass failed.")
            break
        logger.info(f"Bypass attempt {try_count + 1} / {max_retries}")

        try_count += 1

        wait_time = DEFAULT_SLEEP * (try_count - 1)
        logger.info(f"Waiting {wait_time}s before trying...")
        time.sleep(wait_time)

        if _bypass_method_1(sb):
            return

        logger.info("Bypass failed.")

def _get_chromium_args():
    
    arguments = [
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
        sb.uc_open_with_reconnect(url, DEFAULT_SLEEP)
        time.sleep(DEFAULT_SLEEP)
        _bypass(sb)
        if _is_bypassed(sb):
            logger.info("Bypass successful.")
            return sb.page_source
    except Exception as e:
        if retry == 0:
            logger.error_trace(f"Failed to initialize browser: {e}")
            _reset_driver()
            raise e
        logger.error_trace(f"Failed to bypass Cloudflare: {e}. Will retry...")
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
