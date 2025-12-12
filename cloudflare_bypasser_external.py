from logger import setup_logger
from typing import Optional, TYPE_CHECKING
import requests

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
    
    Mirrors the behavior of the internal bypasser: on failure, attempts mirror/DNS 
    rotation for AA URLs before giving up.

    Args:
        url: Target URL to fetch
        selector: Mirror selector for AA URL rewriting and rotation
        
    Returns:
        HTML content if successful, None otherwise
    """
    import network
    sel = selector or network.AAMirrorSelector()
    
    # Rewrite URL to use current mirror (only affects AA URLs)
    attempt_url = sel.rewrite(url)
    
    # First attempt
    result = _fetch_via_bypasser(attempt_url)
    if result:
        return result
    
    # On failure, try mirror/DNS rotation (matches internal bypasser behavior)
    new_base, action = sel.next_mirror_or_rotate_dns()
    if action in ("mirror", "dns") and new_base:
        attempt_url = sel.rewrite(url)
        logger.info(f"External bypasser retrying after {action} rotation: {attempt_url}")
        result = _fetch_via_bypasser(attempt_url)
        if result:
            return result
    
    return None
