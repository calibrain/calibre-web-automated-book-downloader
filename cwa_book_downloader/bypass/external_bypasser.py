"""External Cloudflare bypasser using FlareSolverr."""

import random
import time
from threading import Event
from typing import TYPE_CHECKING, Optional

import requests

from cwa_book_downloader.bypass import BypassCancelledException
from cwa_book_downloader.core.config import config
from cwa_book_downloader.core.logger import setup_logger

if TYPE_CHECKING:
    from cwa_book_downloader.download import network

logger = setup_logger(__name__)

# Timeout constants (seconds)
CONNECT_TIMEOUT = 10
MAX_READ_TIMEOUT = 120
READ_TIMEOUT_BUFFER = 15

# Retry settings
MAX_RETRY = 5
BACKOFF_BASE = 1.0
BACKOFF_CAP = 10.0


def _fetch_via_bypasser(target_url: str) -> Optional[str]:
    """Make a single request to the external bypasser service. Returns HTML or None."""
    bypasser_url = config.get("EXT_BYPASSER_URL", "http://flaresolverr:8191")
    bypasser_path = config.get("EXT_BYPASSER_PATH", "/v1")
    bypasser_timeout = config.get("EXT_BYPASSER_TIMEOUT", 60000)

    if not bypasser_url or not bypasser_path:
        logger.error("External bypasser not configured. Check EXT_BYPASSER_URL and EXT_BYPASSER_PATH.")
        return None

    read_timeout = min((bypasser_timeout / 1000) + READ_TIMEOUT_BUFFER, MAX_READ_TIMEOUT)

    try:
        response = requests.post(
            f"{bypasser_url}{bypasser_path}",
            headers={"Content-Type": "application/json"},
            json={"cmd": "request.get", "url": target_url, "maxTimeout": bypasser_timeout},
            timeout=(CONNECT_TIMEOUT, read_timeout)
        )
        response.raise_for_status()
        result = response.json()

        status = result.get('status', 'unknown')
        message = result.get('message', '')
        logger.debug(f"External bypasser response for '{target_url}': {status} - {message}")

        if status != 'ok':
            logger.warning(f"External bypasser failed for '{target_url}': {status} - {message}")
            return None

        solution = result.get('solution')
        html = solution.get('response', '') if solution else ''

        if not html:
            logger.warning(f"External bypasser returned empty response for '{target_url}'")
            return None

        return html

    except requests.exceptions.Timeout:
        logger.warning(f"External bypasser timed out for '{target_url}' (connect: {CONNECT_TIMEOUT}s, read: {read_timeout:.0f}s)")
    except requests.exceptions.RequestException as e:
        logger.warning(f"External bypasser request failed for '{target_url}': {e}")
    except (KeyError, TypeError, ValueError) as e:
        logger.warning(f"External bypasser returned malformed response for '{target_url}': {e}")

    return None


def _check_cancelled(cancel_flag: Optional[Event], context: str) -> None:
    """Check if operation was cancelled and raise exception if so."""
    if cancel_flag and cancel_flag.is_set():
        logger.info(f"External bypasser cancelled {context}")
        raise BypassCancelledException("Bypass cancelled")


def _sleep_with_cancellation(seconds: float, cancel_flag: Optional[Event]) -> None:
    """Sleep for the specified duration, checking for cancellation each second."""
    for _ in range(int(seconds)):
        _check_cancelled(cancel_flag, "during backoff")
        time.sleep(1)
    remaining = seconds - int(seconds)
    if remaining > 0:
        time.sleep(remaining)


def get_bypassed_page(
    url: str,
    selector: Optional["network.AAMirrorSelector"] = None,
    cancel_flag: Optional[Event] = None
) -> Optional[str]:
    """Fetch HTML via external bypasser with retries and mirror rotation."""
    from cwa_book_downloader.download import network as network_module

    sel = selector or network_module.AAMirrorSelector()

    for attempt in range(1, MAX_RETRY + 1):
        _check_cancelled(cancel_flag, "by user")

        attempt_url = sel.rewrite(url)
        result = _fetch_via_bypasser(attempt_url)
        if result:
            return result

        if attempt == MAX_RETRY:
            break

        delay = min(BACKOFF_CAP, BACKOFF_BASE * (2 ** (attempt - 1))) + random.random()
        logger.info(f"External bypasser attempt {attempt}/{MAX_RETRY} failed, retrying in {delay:.1f}s")

        _sleep_with_cancellation(delay, cancel_flag)

        new_base, action = sel.next_mirror_or_rotate_dns()
        if action in ("mirror", "dns") and new_base:
            logger.info(f"Rotated {action} for retry")

    return None
