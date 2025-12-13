from logger import setup_logger
from typing import Optional, TYPE_CHECKING
import requests
import time
import random

if TYPE_CHECKING:
    import network

try:
    from env import EXT_BYPASSER_PATH, EXT_BYPASSER_TIMEOUT, EXT_BYPASSER_URL
except ImportError:
    raise RuntimeError("Failed to import environment variables. Are you using an `extbp` image?")

logger = setup_logger(__name__)

# Connection timeout (seconds) - how long to wait for external bypasser to accept connection
CONNECT_TIMEOUT = 10
# Maximum read timeout cap (seconds) - hard limit regardless of EXT_BYPASSER_TIMEOUT
MAX_READ_TIMEOUT = 120
# Buffer added to bypasser's configured timeout (seconds) - accounts for processing overhead
READ_TIMEOUT_BUFFER = 15
# Retry settings for bypasser failures
MAX_RETRY = 5
BACKOFF_BASE = 1.0
BACKOFF_CAP = 10.0


def _fetch_via_bypasser(target_url: str) -> Optional[str]:
    """Make a single request to the external bypasser service.
    
    Args:
        target_url: The URL to fetch through the bypasser
        
    Returns:
        HTML content if successful, None otherwise
    """
    if not EXT_BYPASSER_URL or not EXT_BYPASSER_PATH:
        logger.error("External bypasser not configured. Check EXT_BYPASSER_URL and EXT_BYPASSER_PATH.")
        return None
    
    bypasser_endpoint = f"{EXT_BYPASSER_URL}{EXT_BYPASSER_PATH}"
    headers = {"Content-Type": "application/json"}
    payload = {
        "cmd": "request.get",
        "url": target_url,
        "maxTimeout": EXT_BYPASSER_TIMEOUT
    }
    
    # Calculate read timeout: bypasser timeout (ms → s) + buffer, capped at max
    read_timeout = min((EXT_BYPASSER_TIMEOUT / 1000) + READ_TIMEOUT_BUFFER, MAX_READ_TIMEOUT)
    
    try:
        response = requests.post(
            bypasser_endpoint, 
            headers=headers, 
            json=payload, 
            timeout=(CONNECT_TIMEOUT, read_timeout)
        )
        response.raise_for_status()
        result = response.json()
        
        status = result.get('status', 'unknown')
        message = result.get('message', '')
        logger.debug(f"External bypasser response for '{target_url}': {status} - {message}")
        
        # Check for error status (bypasser returns status="error" with solution=null on failure)
        if status != 'ok':
            logger.warning(f"External bypasser failed for '{target_url}': {status} - {message}")
            return None
        
        solution = result.get('solution')
        if not solution:
            logger.warning(f"External bypasser returned empty solution for '{target_url}'")
            return None
        
        html = solution.get('response', '')
        if not html:
            logger.warning(f"External bypasser returned empty response for '{target_url}'")
            return None
        
        return html
        
    except requests.exceptions.Timeout:
        logger.warning(f"External bypasser timed out for '{target_url}' (connect: {CONNECT_TIMEOUT}s, read: {read_timeout:.0f}s)")
        return None
    except requests.exceptions.RequestException as e:
        logger.warning(f"External bypasser request failed for '{target_url}': {e}")
        return None
    except (KeyError, TypeError, ValueError) as e:
        logger.warning(f"External bypasser returned malformed response for '{target_url}': {e}")
        return None


def get_bypassed_page(url: str, selector: Optional["network.AAMirrorSelector"] = None) -> Optional[str]:
    """Fetch HTML content from a URL using an external Cloudflare bypasser service.

    Retries with exponential backoff and mirror/DNS rotation on failure.

    Args:
        url: Target URL to fetch
        selector: Mirror selector for AA URL rewriting and rotation

    Returns:
        HTML content if successful, None otherwise
    """
    import network
    sel = selector or network.AAMirrorSelector()

    for attempt in range(1, MAX_RETRY + 1):
        attempt_url = sel.rewrite(url)
        result = _fetch_via_bypasser(attempt_url)
        if result:
            return result

        if attempt == MAX_RETRY:
            break

        # Backoff with jitter before retry
        delay = min(BACKOFF_CAP, BACKOFF_BASE * (2 ** (attempt - 1))) + random.random()
        logger.info(f"External bypasser attempt {attempt}/{MAX_RETRY} failed, retrying in {delay:.1f}s")
        time.sleep(delay)

        # Rotate mirror/DNS for next attempt
        new_base, action = sel.next_mirror_or_rotate_dns()
        if action in ("mirror", "dns") and new_base:
            logger.info(f"Rotated {action} for retry")

    return None
