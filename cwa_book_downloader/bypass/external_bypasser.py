"""External Cloudflare bypasser using FlareSolverr."""

from threading import Event
from typing import Optional, TYPE_CHECKING
import requests
import time
import random

from cwa_book_downloader.core.config import config
from cwa_book_downloader.core.logger import setup_logger

if TYPE_CHECKING:
    from cwa_book_downloader.download import network


class BypassCancelledException(Exception):
    """Raised when a bypass operation is cancelled."""
    pass

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
    bypasser_url = config.get("EXT_BYPASSER_URL", "http://flaresolverr:8191")
    bypasser_path = config.get("EXT_BYPASSER_PATH", "/v1")
    bypasser_timeout = config.get("EXT_BYPASSER_TIMEOUT", 60000)

    if not bypasser_url or not bypasser_path:
        logger.error("External bypasser not configured. Check EXT_BYPASSER_URL and EXT_BYPASSER_PATH.")
        return None

    bypasser_endpoint = f"{bypasser_url}{bypasser_path}"
    headers = {"Content-Type": "application/json"}
    payload = {
        "cmd": "request.get",
        "url": target_url,
        "maxTimeout": bypasser_timeout
    }

    # Calculate read timeout: bypasser timeout (ms -> s) + buffer, capped at max
    read_timeout = min((bypasser_timeout / 1000) + READ_TIMEOUT_BUFFER, MAX_READ_TIMEOUT)

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


def get_bypassed_page(url: str, selector: Optional["network.AAMirrorSelector"] = None, cancel_flag: Optional[Event] = None) -> Optional[str]:
    """Fetch HTML content from a URL using an external Cloudflare bypasser service.

    Retries with exponential backoff and mirror/DNS rotation on failure.

    Args:
        url: Target URL to fetch
        selector: Mirror selector for AA URL rewriting and rotation
        cancel_flag: Optional threading Event to signal cancellation

    Returns:
        HTML content if successful, None otherwise

    Raises:
        BypassCancelledException: If cancel_flag is set during operation
    """
    from cwa_book_downloader.download import network as network_module
    sel = selector or network_module.AAMirrorSelector()

    for attempt in range(1, MAX_RETRY + 1):
        # Check for cancellation before each attempt
        if cancel_flag and cancel_flag.is_set():
            logger.info("External bypasser cancelled by user")
            raise BypassCancelledException("Bypass cancelled")

        attempt_url = sel.rewrite(url)
        result = _fetch_via_bypasser(attempt_url)
        if result:
            return result

        if attempt == MAX_RETRY:
            break

        # Check for cancellation before backoff wait
        if cancel_flag and cancel_flag.is_set():
            logger.info("External bypasser cancelled during retry")
            raise BypassCancelledException("Bypass cancelled")

        # Backoff with jitter before retry, checking cancellation during wait
        delay = min(BACKOFF_CAP, BACKOFF_BASE * (2 ** (attempt - 1))) + random.random()
        logger.info(f"External bypasser attempt {attempt}/{MAX_RETRY} failed, retrying in {delay:.1f}s")

        # Check cancellation during delay (check every second)
        for _ in range(int(delay)):
            if cancel_flag and cancel_flag.is_set():
                logger.info("External bypasser cancelled during backoff")
                raise BypassCancelledException("Bypass cancelled")
            time.sleep(1)
        # Sleep remaining fraction
        remaining = delay - int(delay)
        if remaining > 0:
            time.sleep(remaining)

        # Rotate mirror/DNS for next attempt
        new_base, action = sel.next_mirror_or_rotate_dns()
        if action in ("mirror", "dns") and new_base:
            logger.info(f"Rotated {action} for retry")

    return None
