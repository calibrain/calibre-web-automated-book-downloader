"""MyAnonamouse enrichment for Prowlarr results.

Prowlarr's MyAnonamouse indexer keeps the title, author, language and filetype
of each torrent but drops the narrator and series MAM returns alongside them, and
Torznab has no field to carry them. With the user's MAM session ID (``mam_id``)
Shelfmark reruns the search Prowlarr sent - same text, categories and indexer
options - against MAM's JSON API and matches the torrents back to Prowlarr's
results by their MAM torrent ID.
"""

import json
import re
import time
import unicodedata
from collections import deque
from dataclasses import dataclass
from threading import Lock
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import requests

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

from shelfmark.core.logger import setup_logger
from shelfmark.download.network import get_proxies, get_ssl_verify
from shelfmark.release_sources.prowlarr.api import get_indexer_field
from shelfmark.release_sources.prowlarr.utils import coerce_int_like

logger = setup_logger(__name__)

# The only origin Prowlarr's MyAnonamouse indexer uses. The session cookie is never
# sent anywhere else, whatever URL a search result carries.
MAM_BASE_URL = "https://www.myanonamouse.net"
_MAM_DOMAIN = "myanonamouse.net"
_SEARCH_PATH = "/tor/js/loadSearchJSONbasic.php"
_USER_PATH = "/jsonLoad.php"
_REQUEST_TIMEOUT_SECONDS = 15
_RESULTS_PER_PAGE = 100
# Rerunning Prowlarr's search normally takes one request per title variant; the rest
# page past torrents that moved (a new upload, an option Shelfmark can't mirror).
_MAX_REQUESTS = 4
_CACHE_TTL_SECONDS = 3600
_HTTP_FORBIDDEN = 403

# Consecutive failures back off for 1, 2, 4 ... minutes, at most 30. The tenth in a
# row stops enrichment until the session ID changes or "Test MAM Session" succeeds.
_MAX_CONSECUTIVE_FAILURES = 10
_BACKOFF_BASE_SECONDS = 60
_BACKOFF_MAX_SECONDS = 1800

# Prowlarr sets both infoUrl and guid to "https://www.myanonamouse.net/t/{id}".
_TORRENT_PATH_RE = re.compile(r"/t/(\d+)/?")
# Uploaders put the bitrate in the free-text tags: "64 kbps", "128kbps", "64 kb/s".
_BITRATE_RE = re.compile(r"\b(\d{2,4}(?:\.\d+)?)\s*k(?:bps|b/s|bit/s)\b", re.IGNORECASE)

# Prowlarr's clean-up of the query (SearchCriteriaBase.GetSanitizedTerm keeps these,
# after standardising dashes and quotes; MyAnonamouse's generator then turns every
# non-word run into a space). Anything else is dropped outright.
_PROWLARR_KEPT_PUNCTUATION = frozenset("-._()@/'[]+%`´‘’")
_NON_WORD_RE = re.compile(r"[^\w]+")

# Prowlarr's MyAnonamouse "Search Type" options, by their value in the indexer settings.
_SEARCH_TYPES = {0: "all", 1: "active", 2: "fl", 3: "fl-VIP", 4: "VIP", 5: "nVIP"}
# Optional "Search in ..." indexer settings; title, author and narrator always apply.
_OPTIONAL_SEARCH_FIELDS = {
    "searchInDescription": "description",
    "searchInSeries": "series",
    "searchInFilenames": "filenames",
}
# The MAM main categories Prowlarr maps to the Torznab categories Shelfmark searches:
# AudioBooks, Musicology and Radio are Audio/Audiobook, E-Books is the Books tree.
_MAIN_CATEGORIES_BY_TORZNAB = {3030: ("13", "15", "16"), 7000: ("14",)}


class MamError(Exception):
    """MAM answered, but not with a usable result."""


class MamAuthError(MamError):
    """MAM rejected the session ID."""


_MAM_REQUEST_ERRORS = (MamError, requests.exceptions.RequestException, ValueError)


@dataclass(frozen=True)
class MamTorrentDetails:
    """The fields Prowlarr drops from a MyAnonamouse search result."""

    narrator: str | None = None
    series: str | None = None  # Display text: "Name #1, Other Series #2"
    # First series only, for the {Series}/{SeriesPosition} naming placeholders.
    series_name: str | None = None
    series_position: float | None = None
    bitrate: str | None = None
    bitrate_kbps: int | None = None


@dataclass(frozen=True)
class MamSearchOptions:
    """How Prowlarr searched MAM, so rerunning the search returns the same torrents."""

    search_type: str = "all"
    search_in: tuple[str, ...] = ("title", "author", "narrator")
    languages: tuple[str, ...] = ()
    main_categories: tuple[str, ...] = ()  # empty searches every category


@dataclass
class _Failures:
    """Consecutive failed MAM requests for one session ID."""

    mam_id: str = ""
    count: int = 0
    retry_at: float = 0.0


_cache: dict[int, tuple[MamTorrentDetails, float]] = {}
_cache_lock = Lock()
_failures = _Failures()
_failures_lock = Lock()


def mam_torrent_id(url: object) -> int | None:
    """Return the MAM torrent ID from a Prowlarr infoUrl/guid, or None for other trackers."""
    if not isinstance(url, str):
        return None
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    if (
        parts.scheme not in {"http", "https"}
        or "@" in parts.netloc
        or "\\" in parts.netloc
        or not (host == _MAM_DOMAIN or host.endswith(f".{_MAM_DOMAIN}"))
    ):
        return None
    match = _TORRENT_PATH_RE.fullmatch(parts.path)
    return int(match.group(1)) if match else None


def prowlarr_search_text(query: str) -> str:
    """Clean a query the way Prowlarr does before it sends the query to MAM."""
    kept = "".join(
        char
        for char in query
        if char.isalpha()
        or char.isdecimal()
        or char.isspace()
        or char in _PROWLARR_KEPT_PUNCTUATION
        or unicodedata.category(char) == "Pd"
    )
    return _NON_WORD_RE.sub(" ", kept).strip()


def mam_search_options(
    indexer: Mapping[str, Any] | None, torznab_categories: Iterable[int] | None
) -> MamSearchOptions:
    """Mirror a Prowlarr MyAnonamouse indexer's search settings and the categories searched."""
    fields = indexer.get("fields") if indexer else None
    languages = get_indexer_field(fields, "searchLanguages")
    return MamSearchOptions(
        search_type=_SEARCH_TYPES.get(
            coerce_int_like(get_indexer_field(fields, "searchType")) or 0, "all"
        ),
        search_in=(
            "title",
            "author",
            "narrator",
            *(
                name
                for key, name in _OPTIONAL_SEARCH_FIELDS.items()
                if get_indexer_field(fields, key) is True
            ),
        ),
        languages=tuple(
            str(language_id)
            for language in (languages if isinstance(languages, list) else [])
            if (language_id := coerce_int_like(language)) is not None
        ),
        main_categories=tuple(
            dict.fromkeys(
                main_category
                for category in torznab_categories or ()
                for main_category in _MAIN_CATEGORIES_BY_TORZNAB.get(category, ())
            )
        ),
    )


def _decode_info(raw: object) -> dict[str, Any]:
    """MAM returns author/narrator/series info as a JSON-encoded object string."""
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        decoded = json.loads(raw)
    except ValueError:
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _parse_names(raw: object) -> str | None:
    names = [str(v).strip() for v in _decode_info(raw).values() if isinstance(v, str)]
    names = [n for n in names if n]
    return ", ".join(dict.fromkeys(names)) or None


def _series_entries(raw: object) -> list[tuple[str, str]]:
    """Return (name, number) pairs from series_info ({"id": ["Name", "1"]})."""
    entries: list[tuple[str, str]] = []
    for value in _decode_info(raw).values():
        name: str = ""
        number: str = ""
        if isinstance(value, list | tuple) and value:
            name = str(value[0] or "").strip()
            if len(value) > 1 and value[1] not in (None, ""):
                number = str(value[1]).strip()
        elif isinstance(value, str):
            name = value.strip()
        if name:
            entries.append((name, number))
    return entries


def _parse_series(raw: object) -> str | None:
    """Format series_info as "Name #1", joining several series with commas."""
    entries = [f"{name} #{number}" if number else name for name, number in _series_entries(raw)]
    return ", ".join(dict.fromkeys(entries)) or None


def _parse_series_position(number: str) -> float | None:
    """Read a MAM series number ("1", "2.5") as a float; ranges like "1-3" are None."""
    try:
        return float(number)
    except ValueError:
        return None


def _parse_bitrate(tags: object) -> tuple[str | None, int | None]:
    if not isinstance(tags, str):
        return None, None
    match = _BITRATE_RE.search(tags)
    if not match:
        return None, None
    kbps = round(float(match.group(1)))
    return f"{kbps} Kbps", kbps


def parse_torrent_details(item: dict[str, Any]) -> MamTorrentDetails:
    """Pull narrator, series and bitrate out of one MAM search result."""
    bitrate, bitrate_kbps = _parse_bitrate(item.get("tags"))
    series_entries = _series_entries(item.get("series_info"))
    first_name, first_number = series_entries[0] if series_entries else (None, "")
    return MamTorrentDetails(
        narrator=_parse_names(item.get("narrator_info")),
        series=_parse_series(item.get("series_info")),
        series_name=first_name,
        series_position=_parse_series_position(first_number) if first_number else None,
        bitrate=bitrate,
        bitrate_kbps=bitrate_kbps,
    )


class MamClient:
    """Minimal MyAnonamouse JSON API client authenticated by a ``mam_id`` cookie."""

    def __init__(self, mam_id: str) -> None:
        """Create a client for the given session ID."""
        # A plain header rather than a cookie jar entry: requests drops the header on a
        # redirect, and redirects are not followed anyway.
        self._headers = {"Accept": "application/json", "Cookie": f"mam_id={mam_id.strip()}"}

    def _get(self, path: str, params: Mapping[str, str | list[str]] | None = None) -> object:
        url = MAM_BASE_URL + path
        response = requests.get(
            url,
            params=params,
            headers=self._headers,
            timeout=_REQUEST_TIMEOUT_SECONDS,
            proxies=get_proxies(url),
            verify=get_ssl_verify(url),
            allow_redirects=False,
        )
        if response.status_code == _HTTP_FORBIDDEN:
            # MAM explains itself in the body (bad cookie, IP/ASN mismatch, ...).
            reply = " ".join(response.text.split())[:200]
            msg = (
                "MyAnonamouse rejected the session ID (403). Sessions only work from the "
                "IP/ASN they are locked to, so check Shelfmark reaches MAM from that address"
                + (f". MAM said: {reply}" if reply else "")
            )
            raise MamAuthError(msg)
        if response.status_code != requests.codes.ok:
            msg = f"MyAnonamouse answered HTTP {response.status_code}"
            raise requests.exceptions.HTTPError(msg, response=response)
        return response.json()

    def get_username(self) -> str | None:
        """Return the account name the session belongs to (used by Test Connection)."""
        data = self._get(_USER_PATH)
        if not isinstance(data, dict):
            return None
        username = data.get("username")
        return str(username) if username else None

    def search(
        self, text: str, options: MamSearchOptions, *, start: int = 0
    ) -> list[dict[str, Any]]:
        """Return one page of torrents; fewer than a full page means it was the last."""
        params: dict[str, str | list[str]] = {
            "tor[text]": text,
            "tor[searchType]": options.search_type,
            "tor[searchIn]": "torrents",
            **{f"tor[srchIn][{field}]": "true" for field in options.search_in},
            "tor[cat][]": "0",
            "tor[sortType]": "default",
            "tor[startNumber]": str(start),
            "perpage": str(_RESULTS_PER_PAGE),
        }
        if options.main_categories:
            params["tor[main_cat][]"] = list(options.main_categories)
        if options.languages:
            params["tor[browse_lang][]"] = list(options.languages)

        data = self._get(_SEARCH_PATH, params)
        items = data.get("data") if isinstance(data, dict) else None
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
        error = data.get("error") if isinstance(data, dict) else None
        # An empty search answers {"error": "Nothing returned, out of N"}.
        if isinstance(error, str) and error.startswith("Nothing returned"):
            return []
        msg = f"Unexpected MyAnonamouse search reply: {str(error or data)[:200]}"
        raise MamError(msg)


def _cached(torrent_id: int) -> MamTorrentDetails | None:
    with _cache_lock:
        entry = _cache.get(torrent_id)
        if entry is None:
            return None
        details, cached_at = entry
        if time.time() - cached_at > _CACHE_TTL_SECONDS:
            del _cache[torrent_id]
            return None
        return details


def _store(details_by_id: dict[int, MamTorrentDetails]) -> None:
    now = time.time()
    with _cache_lock:
        # Most IDs are never looked up again, so expiry on read alone would let the
        # cache grow for as long as Shelfmark runs.
        expired = [
            torrent_id
            for torrent_id, (_, cached_at) in _cache.items()
            if now - cached_at > _CACHE_TTL_SECONDS
        ]
        for torrent_id in expired:
            del _cache[torrent_id]
        for torrent_id, details in details_by_id.items():
            _cache[torrent_id] = (details, now)


def _blocked_reason(mam_id: str) -> str | None:
    """Return why MAM must not be called right now, or None if it may be."""
    with _failures_lock:
        if _failures.mam_id != mam_id or not _failures.count:
            return None
        if _failures.count >= _MAX_CONSECUTIVE_FAILURES:
            return f"stopped after {_failures.count} consecutive failures"
        wait = _failures.retry_at - time.monotonic()
        if wait > 0:
            return f"backing off for {wait:.0f}s after {_failures.count} failure(s)"
        return None


def _record_success(mam_id: str) -> None:
    with _failures_lock:
        if _failures.mam_id == mam_id:
            _failures.count = 0
            _failures.retry_at = 0.0


def _record_failure(mam_id: str, error: Exception) -> None:
    with _failures_lock:
        if _failures.mam_id != mam_id:
            _failures.mam_id = mam_id
            _failures.count = 0
        _failures.count += 1
        count = _failures.count
        delay = min(_BACKOFF_BASE_SECONDS * 2 ** (count - 1), _BACKOFF_MAX_SECONDS)
        _failures.retry_at = time.monotonic() + delay

    if count >= _MAX_CONSECUTIVE_FAILURES:
        logger.warning(
            "MAM enrichment stopped after %s consecutive failures (last: %s). "
            "Test the MAM session in the Prowlarr settings to turn it back on.",
            count,
            error,
        )
    else:
        logger.warning(
            "MAM enrichment failed (%s/%s), next try in %ss: %s",
            count,
            _MAX_CONSECUTIVE_FAILURES,
            delay,
            error,
        )


def reset_failures() -> None:
    """Let enrichment call MAM again straight away (the session test succeeded)."""
    with _failures_lock:
        _failures.count = 0
        _failures.retry_at = 0.0


def lookup_torrent_details(
    mam_id: str,
    torrent_ids: set[int],
    queries: list[str],
    *,
    options: MamSearchOptions | None = None,
    deadline: float | None = None,
) -> dict[int, MamTorrentDetails]:
    """Fetch narrator/series/bitrate for the given MAM torrent IDs (best effort).

    Reruns Prowlarr's search for each query, reading further pages only while IDs
    are missing, capped at a few requests. A failure backs MAM off and returns what
    was found so far; enrichment must never break the Prowlarr search it decorates.
    """
    found: dict[int, MamTorrentDetails] = {}
    for torrent_id in torrent_ids:
        details = _cached(torrent_id)
        if details is not None:
            found[torrent_id] = details

    missing = torrent_ids - found.keys()
    mam_id = mam_id.strip()
    if not missing or not mam_id:
        return found

    blocked = _blocked_reason(mam_id)
    if blocked:
        logger.debug("MAM enrichment skipped: %s", blocked)
        return found

    client = MamClient(mam_id)
    options = options or MamSearchOptions()
    # Page 1 of every query before page 2 of any: each query found its own torrents.
    pages = deque(
        (text, 0)
        for text in dict.fromkeys(prowlarr_search_text(query) for query in queries)
        if text
    )
    requests_made = 0
    while pages and missing and requests_made < _MAX_REQUESTS:
        if deadline is not None and time.monotonic() > deadline:
            logger.debug("MAM enrichment: search budget spent, %s torrent(s) left", len(missing))
            break
        text, start = pages.popleft()
        requests_made += 1
        try:
            items = client.search(text, options, start=start)
        except _MAM_REQUEST_ERRORS as e:
            _record_failure(mam_id, e)
            break
        _record_success(mam_id)

        fetched: dict[int, MamTorrentDetails] = {}
        for item in items:
            torrent_id = coerce_int_like(item.get("id"))
            if torrent_id is not None:
                fetched[torrent_id] = parse_torrent_details(item)
        _store(fetched)
        for torrent_id in missing & fetched.keys():
            found[torrent_id] = fetched[torrent_id]
        missing -= fetched.keys()
        if len(items) >= _RESULTS_PER_PAGE:
            pages.append((text, start + len(items)))

    logger.debug(
        "MAM enrichment: %s of %s torrent(s) matched in %s request(s)",
        len(found),
        len(torrent_ids),
        requests_made,
    )
    return found
