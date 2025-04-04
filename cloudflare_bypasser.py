import time, os, socket
import network
from urllib.parse import urlparse
from DrissionPage import ChromiumPage # type: ignore
from DrissionPage import ChromiumOptions
from DrissionPage._functions.elements import ChromiumElementsList # type: ignore
from DrissionPage._pages.chromium_tab import ChromiumTab # type: ignore
from logger import setup_logger
from env import MAX_RETRY, DOCKERMODE, DEFAULT_SLEEP
from config import PROXIES, CUSTOM_DNS, DOH_SERVER, AA_BASE_URL

logger = setup_logger(__name__)

_defaultTab : ChromiumTab | None = None

network.init()

def _search_recursively_shadow_root_with_iframe(ele : ChromiumElementsList) -> ChromiumElementsList | None:
    if ele.shadow_root:
        if ele.shadow_root.child().tag == "iframe":
            return ele.shadow_root.child()
    else:
        for child in ele.children():
            result = _search_recursively_shadow_root_with_iframe(child)
            if result:
                return result
    return None

def _search_recursively_shadow_root_with_cf_input(ele : ChromiumElementsList) -> ChromiumElementsList | None:
    if ele.shadow_root:
        if ele.shadow_root.ele("tag:input"):
            return ele.shadow_root.ele("tag:input")
    else:
        for child in ele.children():
            result = _search_recursively_shadow_root_with_cf_input(child) 
            if result:
                return result
    return None

def _locate_cf_button(driver : ChromiumTab) -> ChromiumElementsList | None:
    button : ChromiumElementsList = None
    eles = driver.eles("tag:input")
    for ele in eles:
        if "name" in ele.attrs.keys() and "type" in ele.attrs.keys():
            if "turnstile" in ele.attrs["name"] and ele.attrs["type"] == "hidden":
                button = ele.parent().shadow_root.child()("tag:body").shadow_root("tag:input")
                break
        
    if button:
        return button
    else:
        # If the button is not found, search it recursively
        logger.debug("Basic search failed. Searching for button recursively.")
        ele = driver.ele("tag:body")
        iframe = _search_recursively_shadow_root_with_iframe(ele)
        if iframe:
            button = _search_recursively_shadow_root_with_cf_input(iframe("tag:body"))
        else:
            logger.debug("Iframe not found. Button search failed.")
        return button

def _click_verification_button(driver: ChromiumTab) -> None:
    try:
        button = _locate_cf_button(driver)
        if button:
            logger.debug("Verification button found. Attempting to click.")
            button.wait.displayed(timeout=DEFAULT_SLEEP) 
            button.click()
        else:
            logger.debug("Verification button not found.")

    except Exception as e:
        logger.debug(f"Error clicking verification button: {e}")

def _is_bypassed(driver: ChromiumTab) -> bool:
    try:
        title = driver.title.lower()
        body = driver.ele("tag:body").text.lower()
        
        # Check both title and body for verification messages
        verification_texts = [
            "just a moment",
            "verify you are human",
            "verifying you are human",
            "needs to review the security of your connection before proceeding"
        ]
        
        for text in verification_texts:
            if text in title.lower() or text in body.lower():
                return False
                
        return True
    except Exception as e:
        logger.debug(f"Error checking page title: {e}")
        return False

def _bypass(driver: ChromiumTab, max_retries: int = MAX_RETRY) -> None:
    try_count = 0

    while not _is_bypassed(driver):
        logger.info(f"Starting Cloudflare bypass... Retry: {try_count + 1} / {max_retries}")
        if try_count >= max_retries:
            logger.warning("Exceeded maximum retries. Bypass failed.")
            break

        logger.info(f"Attempt {try_count + 1}: Verification page detected. Trying to bypass...")

        try_count += 1
        time.sleep(DEFAULT_SLEEP)

        _click_verification_button(driver)

        time.sleep(DEFAULT_SLEEP)

    if _is_bypassed(driver):
        logger.info("Bypass successful.")
    else:
        logger.info("Bypass failed.")

def _get_chromium_options(arguments: list[str]) -> ChromiumOptions:
    options = ChromiumOptions()
    for argument in arguments:
        options.set_argument(argument)
    
    # Add proxy settings if configured
    if PROXIES:
        if 'http' in PROXIES:
            options.set_argument(f'--proxy-server={PROXIES["http"]}')
            logger.debug(f"Setting HTTP proxy: {PROXIES['http']}")
        elif 'https' in PROXIES:
            options.set_argument(f'--proxy-server={PROXIES["https"]}')
            logger.debug(f"Setting HTTPS proxy: {PROXIES['https']}")

    # --- Add Custom DNS settings ---
    try:
        if  len(CUSTOM_DNS) > 0:
            if DOH_SERVER:
                logger.info(f"Configuring DNS over HTTPS (DoH) with server: {DOH_SERVER}")

                # Enable the DoH feature
                options.set_argument(f'--enable-features=DnsOverHttps')

                # TODO: This is probably broken and a halucination,
                # but it should still default to google DOH so its fine...
                options.set_argument(f'--dns-over-https-mode="secure"')
                options.set_argument(f'--dns-over-https-servers="{DOH_SERVER}"')

                doh_hostname = urlparse(DOH_SERVER).hostname
                if doh_hostname:
                    doh_ip = socket.gethostbyname(doh_hostname)
                    options.set_argument(f'--host-resolver-rules=MAP {doh_hostname} {doh_ip}')
                    logger.debug(f"Setting Chromium --host-resolver-rules='MAP {doh_hostname} {doh_ip}'")
            else:
                logger.info(f"Applying custom DNS servers: {CUSTOM_DNS}")
                # Format: "MAP * <dns1>, MAP * <dns2>, ..."
                # We create a separate MAP rule for each DNS server.
                # Chromium should try them based on its internal logic (likely order/availability).
                resolver_rules = []
                for dns_server in CUSTOM_DNS:
                    resolver_rules.append(f"MAP * {dns_server}")

                if resolver_rules:
                    # Join the rules with " , " (comma and space is a common separator)
                    host_resolver_rules_value = " , ".join(resolver_rules)
                    options.set_argument(f'--host-resolver-rules={host_resolver_rules_value}')
                    logger.debug(f"Setting Chromium --host-resolver-rules='{host_resolver_rules_value}'")   
    except Exception as e:
        logger.error_trace(f"Error configuring DNS settings: {e}")
    return options

def _genScraper() -> ChromiumPage:
    arguments = [
        "-no-first-run",
        "-force-color-profile=srgb",
        "-metrics-recording-only",
        "-password-store=basic",
        "-use-mock-keychain",
        "-export-tagged-pdf",
        "-no-default-browser-check",
        "-disable-background-mode",
        "-enable-features=NetworkService,NetworkServiceInProcess,LoadCryptoTokenExtension,PermuteTLSExtensions",
        "-disable-features=FlashDeprecationWarning,EnablePasswordsAccountStorage",
        "-deny-permission-prompts",
        "-disable-gpu",
        "-no-sandbox",
        "-accept-lang=en-US",
        "-remote-debugging-port=9222"
    ]

    options = _get_chromium_options(arguments)
    # Initialize the browser
    driver = ChromiumPage(addr_or_opts=options)
    return driver


def _reset_browser() -> None:
    logger.info("Resetting chromiumbrowser")
    if not DOCKERMODE:
        return
    global _defaultTab
    # Kill the browser
    if _defaultTab:
        _defaultTab.close()
    _defaultTab = None
    # Force kill the browser
    os.system("pkill -f -i 'chromium'")
    os.system("pkill -f -i 'chrom'")
    os.system("pkill -f -i 'xvfb'")
    time.sleep(1)

def _init_browser(retry : int = MAX_RETRY) -> ChromiumTab:
    global _defaultTab
    if _defaultTab:
        return _defaultTab
    else:
        try:
            driver = _genScraper()
            _defaultTab = driver.get_tabs()[0]
            return _defaultTab
        except Exception as e:
            if retry > 0:
                _reset_browser()
            else:
                logger.error_trace(f"Failed to initialize browser: {e}")
                raise e
    return _init_browser(retry - 1)

def get(url : str, retry : int = MAX_RETRY) -> ChromiumTab:
    defaultTab = _init_browser()
    defaultTab.get(url)
    try:
        _bypass(defaultTab)
    except Exception as e:
        if retry > 0:
            return get(url, retry - 1)
        logger.error_trace(f"Failed to bypass Cloudflare for {url}: {e}")
        raise e
    return defaultTab

get(AA_BASE_URL)