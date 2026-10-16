"""Mark audiobook requests for books that no store sells as an audiobook.

A pending audiobook request that no source ever matches sits in Needs Review for good,
and an admin ends up looking it up by hand to learn that there is no recording to find.
This does that lookup: once a request has gone a configured number of consecutive
auto-download passes without a strict match, Audible's US and UK catalogues are asked,
through the Audiobookshelf server Shelfmark already talks to, whether the book exists as
an audiobook at all.

- Not listed on either store: the request stays pending and its failure reason says so,
  for an admin to decline or keep. Audible search misses real books (collection-only
  novellas, retitled releases), so the check never declines on its own.
- Listed: the request stays pending and its failure reason says so, with the ASIN, so
  the admin sees why it waits. Either way the store is not asked again for a week.
- The store or the server failed, or the request's search budget was spent: nothing is
  decided. An error is not "no audiobook". Audiobookshelf answers a failed Audible search
  with an empty list rather than an error, so before a "not listed" counts, a book both
  stores are known to sell (``CANARY``) is looked up too; if that is not found either,
  the answer is "unknown".

The pass counter lives in the request's ``book_data`` beside the retry bookkeeping. A
pass that matched the book clears it, and so does any rejection (``requests_service``
drops it), so a request an admin reopens starts counting afresh. Only audiobook requests are checked: ebooks have no store
that could vouch for their absence.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import requests

from shelfmark.core.config import config as app_config
from shelfmark.core.existence_match import StoreMatch, find_match, search_title, us_spelling
from shelfmark.core.library_providers import audiobookshelf
from shelfmark.core.logger import setup_logger
from shelfmark.core.request_helpers import coerce_int
from shelfmark.core.request_validation import RequestStatus

if TYPE_CHECKING:
    from shelfmark.core.auto_download import AutoDownloadOutcome
    from shelfmark.core.user_db import UserDB

logger = setup_logger(__name__)

ENABLED_SETTING = "AUTO_DOWNLOAD_EXISTENCE_CHECK_ENABLED"
AFTER_PASSES_SETTING = "AUTO_DOWNLOAD_EXISTENCE_CHECK_AFTER_PASSES"
DEFAULT_AFTER_PASSES = 3

# A book the store listed is asked about again after this long, not every pass.
RECHECK_DAYS = 7

# Audiobookshelf's provider ids for the two Audible stores asked, in order.
STORES: tuple[tuple[str, str], ...] = (("audible", "US"), ("audible.uk", "UK"))
US_STORE = "audible"

# Where the bookkeeping sits in a request's book_data, beside ``failed_releases``.
BOOK_DATA_KEY = "existence_check"

# One store lookup may take this long, and one is not started with less than this of the
# request's search budget left: the pass must not stall on a store that stopped answering.
_LOOKUP_TIMEOUT = 15.0
_MIN_LOOKUP_BUDGET = 5.0

# A book both stores sell, asked about before a "not listed" is believed: Audiobookshelf
# answers a failed Audible search with an empty list, which reads the same as "no such book".
CANARY = ("The Hobbit", "J.R.R. Tolkien")

# Outcomes of a pass that mean the request found its book, so the count starts over.
_MATCHED = frozenset({"queued", "in_library"})

NOT_LISTED_REASON = "Not found on Audible (US/UK) after {passes} searches, {date}"
LISTED_REASON = "Exists on Audible ({asin}), not on our sources yet"


@dataclass(frozen=True)
class ExistenceResult:
    """What the stores said: ``found``, ``not_found`` or ``unknown`` (nothing decided)."""

    status: str
    match: StoreMatch | None = None
    store: str | None = None
    detail: str = ""


def enabled() -> bool:
    return bool(app_config.get(ENABLED_SETTING, False))


def passes_before_check() -> int:
    """How many consecutive no-match passes a request goes through before the lookup."""
    raw = coerce_int(app_config.get(AFTER_PASSES_SETTING, DEFAULT_AFTER_PASSES), 0)
    return raw if raw >= 1 else DEFAULT_AFTER_PASSES


def _search(
    title: str, author: str, provider: str, store: str, deadline: float | None
) -> list[dict[str, Any]] | ExistenceResult:
    """One store search, or the ``unknown`` result that stands in for it."""
    timeout = _LOOKUP_TIMEOUT
    if deadline is not None:
        left = deadline - time.monotonic()
        if left < _MIN_LOOKUP_BUDGET:
            return ExistenceResult(
                "unknown", store=store, detail=f"no search time left for the {store} store"
            )
        timeout = min(timeout, left)
    try:
        return audiobookshelf.search_books(title, author, provider, timeout=timeout)
    except (requests.RequestException, ValueError) as exc:
        return ExistenceResult("unknown", store=store, detail=f"{store} lookup failed: {exc}")


def look_up(
    title: str | None, author: str | None, *, budget_seconds: float | None = None
) -> ExistenceResult:
    """Ask Audible US, then UK, whether ``title`` by ``author`` exists as an audiobook.

    The lookups share ``budget_seconds`` when one is given; a store that cannot be asked
    within it is not asked, and the answer is ``unknown``. Any server or store error is
    ``unknown`` too, and so is a "not listed" that the stores also give for ``CANARY``:
    an error never says the book is missing.
    """
    query = search_title(title, author)
    if not query:
        return ExistenceResult("unknown", detail="the request has no title to search")
    deadline = None if budget_seconds is None else time.monotonic() + budget_seconds
    for provider, store in STORES:
        store_query = us_spelling(query) if provider == US_STORE else query
        results = _search(store_query, author or "", provider, store, deadline)
        if isinstance(results, ExistenceResult):
            return results
        match = find_match(title, author, results)
        if match is not None:
            return ExistenceResult("found", match=match, store=store)
    canary_title, canary_author = CANARY
    for provider, store in STORES:
        results = _search(canary_title, canary_author, provider, store, deadline)
        if isinstance(results, ExistenceResult):
            return results
        if find_match(canary_title, canary_author, results) is None:
            return ExistenceResult(
                "unknown",
                store=store,
                detail=f"the {store} store did not list {canary_title} either; taken as a failure",
            )
    return ExistenceResult("not_found")


def without_state(book_data: dict[str, Any]) -> dict[str, Any] | None:
    """``book_data`` without this check's bookkeeping, or None when it has none."""
    if BOOK_DATA_KEY not in book_data:
        return None
    return _with_state(book_data, None)


def _state(book_data: dict[str, Any]) -> dict[str, Any]:
    raw = book_data.get(BOOK_DATA_KEY)
    return dict(raw) if isinstance(raw, dict) else {}


def _with_state(book_data: dict[str, Any], state: dict[str, Any] | None) -> dict[str, Any]:
    cleared = {key: value for key, value in book_data.items() if key != BOOK_DATA_KEY}
    if state:
        cleared[BOOK_DATA_KEY] = state
    return cleared


def _checked_recently(state: dict[str, Any], now: datetime) -> bool:
    """True when the store said "found" less than ``RECHECK_DAYS`` ago."""
    if state.get("result") not in ("found", "not_found"):
        return False
    try:
        checked_at = datetime.fromisoformat(str(state.get("checked_at") or ""))
    except ValueError:
        return False
    if checked_at.tzinfo is None:
        checked_at = checked_at.replace(tzinfo=UTC)
    return now - checked_at < timedelta(days=RECHECK_DAYS)


def after_request(
    user_db: UserDB,
    row: dict[str, Any],
    outcome: AutoDownloadOutcome,
    *,
    admin_user_id: int,
    content_type: str,
    budget_seconds: float | None = None,
    now: datetime | None = None,
) -> str | None:
    """Count the pass for one request and, when it is time, ask the stores.

    Called by the auto-download pass after each request with that request's outcome.
    Returns what happened: ``counted`` (not yet time), ``waiting`` (the store listed it
    recently), ``listed`` (the store lists it; the request stays pending and says so),
    ``not_listed`` (not on either store; stays pending and says so), ``unknown`` (lookup failed, nothing changed) or None when
    the request was not an audiobook no-match, or the check is off.
    """
    if content_type != "audiobook" or not enabled():
        return None
    request_id = int(row["id"])
    # The pass may have written to the request (a failed release, a status change), so
    # work from what is stored now, not from the row the pass started with.
    current = user_db.get_request(request_id)
    if current is None:
        return None
    book_data = current.get("book_data")
    if not isinstance(book_data, dict):
        return None
    state = _state(book_data)

    if outcome.status != "no_match":
        if outcome.status in _MATCHED and state:
            # The book was found; a retry after a failed download starts counting afresh.
            user_db.update_request(request_id, book_data=_with_state(book_data, None))
        return None
    if current.get("status") != RequestStatus.PENDING:
        return None

    moment = now or datetime.now(UTC)
    passes = coerce_int(state.get("no_match_passes"), 0) + 1
    state["no_match_passes"] = passes
    # Written before the lookup, so a pass still counts if the lookup never finishes.
    user_db.update_request(
        request_id,
        expected_current_status=RequestStatus.PENDING,
        book_data=_with_state(book_data, state),
    )
    if passes < passes_before_check():
        return "counted"
    if _checked_recently(state, moment):
        return "waiting"

    title = str(book_data.get("title") or "")
    author = str(book_data.get("author") or "")
    result = look_up(title, author, budget_seconds=budget_seconds)
    if result.status == "unknown":
        logger.warning(
            "existence check: request %s (%s) not decided: %s", request_id, title, result.detail
        )
        return "unknown"

    if result.status == "found" and result.match is not None:
        state.update(
            result="found",
            checked_at=moment.isoformat(timespec="seconds"),
            asin=result.match.asin,
            store=result.store,
        )
        user_db.update_request(
            request_id,
            expected_current_status=RequestStatus.PENDING,
            book_data=_with_state(book_data, state),
            last_failure_reason=LISTED_REASON.format(asin=result.match.asin),
        )
        logger.info(
            "existence check: request %s (%s) is on Audible %s as %s; still pending",
            request_id,
            title,
            result.store,
            result.match.asin,
        )
        return "listed"

    # Not on either store. Audible search misses too much to decline on that alone (books
    # sold only in a collection, retitled releases, fan recordings), so the request stays
    # pending for an admin to decide, with the reason saying what the store answered.
    state.update(result="not_found", checked_at=moment.isoformat(timespec="seconds"))
    user_db.update_request(
        request_id,
        expected_current_status=RequestStatus.PENDING,
        book_data=_with_state(book_data, state),
        last_failure_reason=NOT_LISTED_REASON.format(passes=passes, date=moment.date().isoformat()),
    )
    logger.info(
        "existence check: request %s (%s) not on Audible US or UK after %d searches; "
        "left pending for review",
        request_id,
        title,
        passes,
    )
    return "not_listed"
