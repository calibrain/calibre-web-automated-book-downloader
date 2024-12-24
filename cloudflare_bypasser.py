import time, os
from DrissionPage import ChromiumPage # type: ignore
from DrissionPage import ChromiumOptions
from DrissionPage._functions.elements import ChromiumElementsList # type: ignore
from DrissionPage._pages.chromium_tab import ChromiumTab # type: ignore
from logger import setup_logger
from config import MAX_RETRY, DOCKERMODE, DEFAULT_SLEEP

logger = setup_logger(__name__)

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
            button.click()
        else:
            logger.debug("Verification button not found.")

    except Exception as e:
        logger.debug(f"Error clicking verification button: {e}")

def _is_bypassed(driver: ChromiumTab) -> bool:
    try:
        title = driver.title.lower()
        body = driver.ele("tag:body").text.lower()
        # TODO check body
        return "just a moment" not in title
    except Exception as e:
        logger.debug(f"Error checking page title: {e}")
        return False

def _bypass(driver: ChromiumTab, max_retries: int = MAX_RETRY) -> None:
    try_count = 0

    while not _is_bypassed(driver):
        logger.info(f"Starting Cloudflare bypass... Rey : {max_retries + 1} / {try_count}")
        if 0 < max_retries + 1 <= try_count:
            logger.warning("Exceeded maximum retries. Bypass failed.")
            break

        logger.info(f"Attempt {try_count + 1}: Verification page detected. Trying to bypass...")
        _click_verification_button(driver)

        try_count += 1
        time.sleep(DEFAULT_SLEEP)

    if _is_bypassed(driver):
        logger.info("Bypass successful.")
    else:
        logger.info("Bypass failed.")

def _get_chromium_options(arguments: list[str]) -> ChromiumOptions:
    options = ChromiumOptions()
    for argument in arguments:
        options.set_argument(argument)
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
        "-accept-lang=en-US",
    ]

    options = _get_chromium_options(arguments)
    # Initialize the browser
    driver = ChromiumPage(addr_or_opts=options)
    return driver

_defaultTab = None

def _reset_browser() -> None:
    if not DOCKERMODE:
        return
    global _defaultTab
    # Kill the browser
    if _defaultTab:
        _defaultTab.close()
    _defaultTab = None
    # Force kill the browser
    os.system("pkill -f *chrom*")
    time.sleep(1)

def _init_browser(retry : int = MAX_RETRY) -> ChromiumTab:
    global _defaultTab
    if _defaultTab:
        return _defaultTab
    else:
        try:
            driver = _genScraper()
            _defaultTab = driver.get_tabs()[0]
        except Exception as e:
            if retry > 0:
                _reset_browser()
            else:
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
        raise e
    return defaultTab
