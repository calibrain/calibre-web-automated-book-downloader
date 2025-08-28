from logger import setup_logger
from typing import Optional
import requests

try:
    from env import EXT_BYPASSER_PATH, EXT_BYPASSER_TIMEOUT, EXT_BYPASSER_URL
except ImportError:
    raise RuntimeError("Failed to import environment variables. Are you using an `extbp` image?")

logger = setup_logger(__name__)


def get_bypassed_page(url: str) -> Optional[str]:
    """Fetch HTML content from a URL using an External Cloudflare Resolver.

    Args:
        url: Target URL
    Returns:
        str: HTML content if successful, None otherwise
    """
    if not EXT_BYPASSER_URL or not EXT_BYPASSER_PATH:
        logger.error("Wrong External Bypass configuration. Please check your environment configuration.")
        return None
    ext_url = f"{EXT_BYPASSER_URL}{EXT_BYPASSER_PATH}"
    headers = {"Content-Type": "application/json"}
    data = {
        "cmd": "request.get",
        "url": url,
        "maxTimeout": EXT_BYPASSER_TIMEOUT
    }
    response = requests.post(ext_url, headers=headers, json=data)
    response.raise_for_status()
    logger.debug(f"External Bypass response for '{url}': {response.json()['status']} - {response.json()['message']}")
    return response.json()['solution']['response']
