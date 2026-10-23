"""A wall-clock budget for one release search, enforced through the existing cancel flag.

`/api/releases` is synchronous: the browser waits on it while the search runs. Nothing
bounded that wait, and the bypasser's own worst case is minutes long
(`internal_bypasser.max_duration_seconds()`), so a search that ran into an unsolvable
protection challenge outlived every reverse proxy in front of it. The user then saw
"Server unavailable (504)" - a gateway timeout that says nothing about what went wrong
and points the blame at their proxy config. See issue #1276.

The budget is expressed as the cancel flag the download path already understands: an
Event armed by a timer. `html_get_page`, the bypassers and the helper subprocess all poll
it, so an expired budget stops a solve already in flight rather than only refusing the
next one. When it trips, the search fails with a message that names the real cause.

Scoped to a context variable so it applies to the request that set it and to nothing else
- a queued download must keep its own, much longer, budget.
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING

from shelfmark.core.logger import setup_logger

if TYPE_CHECKING:
    from collections.abc import Iterator

logger = setup_logger(__name__)

# What one search may spend. A first search on a cold start legitimately pays for a
# browser solve - jfmlima measured 60-120s for a successful one on Anna's Archive - so
# this cannot be as tight as a proxy's default read timeout without breaking working
# setups. It is instead well below the ~840s the bypass path could previously reach,
# which is what turned a failing challenge into a gateway timeout.
DEFAULT_SEARCH_BUDGET_SECONDS = 300.0

_MIN_SEARCH_BUDGET_SECONDS = 30.0

# What each unit still waiting its turn keeps back from the one running now, and the
# least a unit is given while the parent has that much left. Thirty seconds covers a
# plain search; it is not enough for a bypass solve, which is why the unit running
# keeps everything else.
DEFAULT_UNIT_RESERVE_SECONDS = 30.0
_MAX_SEARCH_BUDGET_SECONDS = 1800.0

# Raised to the caller when the budget runs out, so the API can say so plainly.
SEARCH_DEADLINE_MESSAGE = (
    "The release search ran out of time (%.0fs). Anna's Archive is behind a protection "
    "challenge the bypasser could not solve in that window. Raise the release search "
    "timeout if your setup is simply slow."
)


class SearchDeadline:
    """A budget with an Event that trips when it expires."""

    def __init__(self, budget_seconds: float) -> None:
        self.budget_seconds = budget_seconds
        self.expires_at = time.monotonic() + budget_seconds
        # A plain threading.Event on purpose: this is handed on as a cancel flag, and
        # that is the type the download path, the CDP worker thread and the bypass helper
        # already poll.
        self.event = threading.Event()
        self._timer = threading.Timer(budget_seconds, self.event.set)
        self._timer.daemon = True

    def start(self) -> None:
        self._timer.start()

    def cancel(self) -> None:
        self._timer.cancel()

    @property
    def remaining(self) -> float:
        return max(0.0, self.expires_at - time.monotonic())

    @property
    def expired(self) -> bool:
        return self.event.is_set() or self.remaining <= 0


_current: ContextVar[SearchDeadline | None] = ContextVar("search_deadline", default=None)


def budget_seconds() -> float:
    """The configured budget for one release search."""
    from shelfmark.core.config import config as app_config

    raw = app_config.get("RELEASE_SEARCH_TIMEOUT", DEFAULT_SEARCH_BUDGET_SECONDS)
    if isinstance(raw, bool) or not isinstance(raw, int | float | str):
        return DEFAULT_SEARCH_BUDGET_SECONDS
    try:
        value = float(raw)
    except TypeError, ValueError:
        return DEFAULT_SEARCH_BUDGET_SECONDS
    if value <= 0:
        return DEFAULT_SEARCH_BUDGET_SECONDS
    return min(max(value, _MIN_SEARCH_BUDGET_SECONDS), _MAX_SEARCH_BUDGET_SECONDS)


@contextmanager
def search_deadline(budget: float | None = None) -> Iterator[SearchDeadline]:
    """Apply a budget to everything the calling context does."""
    deadline = SearchDeadline(budget if budget is not None else budget_seconds())
    token = _current.set(deadline)
    deadline.start()
    logger.debug("Release search budget: %.0fs", deadline.budget_seconds)
    try:
        yield deadline
    finally:
        deadline.cancel()
        _current.reset(token)


@contextmanager
def unit_deadline(
    waiting: int = 0, *, reserve: float = DEFAULT_UNIT_RESERVE_SECONDS
) -> Iterator[SearchDeadline | None]:
    """Budget for one unit of work inside a search, keeping time back for those after it.

    The unit gets the parent's remaining time minus `reserve` for each of the `waiting`
    units still to run, but never less than `reserve` and never more than the parent has
    left. A lone unit gets everything, and time a fast unit does not use rolls on to the
    next. A stuck unit stops at its own limit, so it cannot spend the seconds the next
    one needs. Outside a search there is no budget to divide, so nothing is applied.
    """
    parent = _current.get()
    if parent is None:
        yield None
        return
    remaining = parent.remaining
    budget = min(remaining, max(reserve, remaining - reserve * max(waiting, 0)))
    with search_deadline(budget) as child:
        yield child


def current() -> SearchDeadline | None:
    """The budget in force, or None outside a search."""
    return _current.get()


def expired() -> bool:
    """Whether the budget in force has run out. False when there is no budget."""
    deadline = _current.get()
    return deadline is not None and deadline.expired


def cancel_event() -> threading.Event | None:
    """The Event that trips when the budget runs out, for use as a cancel flag."""
    deadline = _current.get()
    return deadline.event if deadline is not None else None


def deadline_message() -> str:
    """The failure to report when the budget has run out."""
    deadline = _current.get()
    budget = deadline.budget_seconds if deadline else DEFAULT_SEARCH_BUDGET_SECONDS
    return SEARCH_DEADLINE_MESSAGE % budget
