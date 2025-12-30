"""Browser fingerprint profile management for bypass stealth."""

import random
from typing import Optional

from cwa_book_downloader.core.logger import setup_logger

logger = setup_logger(__name__)

COMMON_RESOLUTIONS = [
    (1920, 1080, 0.35),  
    (1366, 768, 0.18),   
    (1536, 864, 0.10),   
    (1440, 900, 0.08),   
    (1280, 720, 0.07),   
    (1600, 900, 0.06),   
    (1280, 800, 0.05),   
    (2560, 1440, 0.04),  
    (1680, 1050, 0.04),  
    (1920, 1200, 0.03),  
]

# Current screen size (module-level singleton)
_current_screen_size: Optional[tuple[int, int]] = None


def get_screen_size() -> tuple[int, int]:
    global _current_screen_size
    if _current_screen_size is None:
        _current_screen_size = _generate_screen_size()
        logger.debug(f"Generated initial screen size: {_current_screen_size[0]}x{_current_screen_size[1]}")
    return _current_screen_size


def rotate_screen_size() -> tuple[int, int]:
    global _current_screen_size
    old_size = _current_screen_size
    _current_screen_size = _generate_screen_size()

    if old_size:
        logger.info(f"Rotated screen size: {old_size[0]}x{old_size[1]} -> "
                    f"{_current_screen_size[0]}x{_current_screen_size[1]}")
    else:
        logger.info(f"Generated screen size: {_current_screen_size[0]}x{_current_screen_size[1]}")

    return _current_screen_size


def clear_screen_size() -> None:
    global _current_screen_size
    _current_screen_size = None


def _generate_screen_size() -> tuple[int, int]:
    resolutions = [(w, h) for w, h, _ in COMMON_RESOLUTIONS]
    weights = [weight for _, _, weight in COMMON_RESOLUTIONS]
    return random.choices(resolutions, weights=weights, k=1)[0]
