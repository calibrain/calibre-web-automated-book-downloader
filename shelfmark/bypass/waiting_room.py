"""Recognize Anna's Archive pages that require a live JavaScript timer."""

from urllib.parse import urlparse

from bs4 import BeautifulSoup


class WaitingRoomTimeoutError(TimeoutError):
    """The source waiting room did not finish within the browser session budget."""


def is_aa_waiting_room(url: str, html: str) -> bool:
    """Match the download route and actual timer element, not a script reference."""
    return urlparse(url).path.startswith("/slow_download/") and bool(
        BeautifulSoup(html, "html.parser").select_one(".js-partner-countdown")
    )
