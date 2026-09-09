"""Prowlarr API client for connection testing, indexer listing, and search."""

from collections.abc import Mapping
from contextlib import suppress
from datetime import UTC, datetime
from http import HTTPStatus
from typing import Any, TypedDict

import requests

from shelfmark.core.config import config
from shelfmark.core.logger import setup_logger
from shelfmark.core.utils import normalize_http_url
from shelfmark.download.network import get_ssl_verify
from shelfmark.release_sources.prowlarr.torznab import parse_torznab_xml
from shelfmark.release_sources.prowlarr.utils import coerce_float_like, coerce_int_like

logger = setup_logger(__name__)

_HTTP_STATUS_UNAUTHORIZED = HTTPStatus.UNAUTHORIZED
_BOOK_CATEGORY_RANGE_START = 7000
_BOOK_CATEGORY_RANGE_END = 8000

# Prowlarr's own JSON endpoints (status, indexer list) read local state and answer
# in milliseconds, so they keep a short timeout. A Torznab search is different: it
# is Prowlarr proxying a live request to the tracker, which for a Cloudflare-fronted
# indexer means waiting on FlareSolverr to solve a challenge. A cold challenge
# routinely runs past a minute, so indexer searches get their own, longer budget.
DEFAULT_INDEXER_TIMEOUT_SECONDS = 90
MIN_INDEXER_TIMEOUT_SECONDS = 5
MAX_INDEXER_TIMEOUT_SECONDS = 300

# Connecting to Prowlarr itself is a LAN hop; only the read is allowed to be slow.
_CONNECT_TIMEOUT_SECONDS = 10.0

_PROWLARR_CLIENT_ERRORS = (
    requests.exceptions.RequestException,
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
)


class ProwlarrSearchError(RuntimeError):
    """A Torznab search could not be completed.

    Deliberately distinct from an empty result list. Reporting a failed search as
    "this indexer has nothing" is what turns a slow FlareSolverr challenge into
    "No releases found for this book" in the UI (#1249), and it also makes the
    auto-expand retry fire a second request on top of the one still running.
    """


def resolve_indexer_timeout(timeout: object = None) -> int:
    """Resolve the per-indexer search timeout, falling back to config.

    Out-of-range and unparsable values are clamped rather than rejected: this
    feeds an HTTP timeout, and a bad setting should not take searching down.
    """
    if timeout is None:
        timeout = config.get("PROWLARR_INDEXER_TIMEOUT", DEFAULT_INDEXER_TIMEOUT_SECONDS)

    resolved = coerce_int_like(timeout)
    if resolved is None:
        logger.warning(
            "Invalid PROWLARR_INDEXER_TIMEOUT %r - using %ss",
            timeout,
            DEFAULT_INDEXER_TIMEOUT_SECONDS,
        )
        return DEFAULT_INDEXER_TIMEOUT_SECONDS

    return max(MIN_INDEXER_TIMEOUT_SECONDS, min(MAX_INDEXER_TIMEOUT_SECONDS, resolved))


class IndexerSeedSettings(TypedDict, total=False):
    ratio_limit: float
    seeding_time_limit_minutes: int


_INDEXER_FIELD_SEED_RATIO = "torrentBaseSettings.seedRatio"
_INDEXER_FIELD_SEED_TIME_MINUTES = "torrentBaseSettings.seedTime"


def _normalize_json_object(payload: object, *, context: str) -> dict[str, Any]:
    """Return a JSON object payload with string keys or raise on unexpected shapes."""
    if not isinstance(payload, Mapping):
        msg = f"Unexpected {context} response payload"
        raise TypeError(msg)

    normalized: dict[str, Any] = {}
    for key, value in payload.items():
        if not isinstance(key, str):
            msg = f"Unexpected {context} response payload"
            raise TypeError(msg)
        normalized[key] = value

    return normalized


def _normalize_json_object_list(payload: object, *, context: str) -> list[dict[str, Any]]:
    """Return a list of JSON objects or raise on unexpected item shapes."""
    if not isinstance(payload, list):
        msg = f"Unexpected {context} response payload"
        raise TypeError(msg)

    return [_normalize_json_object(item, context=context) for item in payload]


def _get_field_value(fields: object, name: str) -> object | None:
    if not isinstance(fields, list):
        return None

    for field in fields:
        if not isinstance(field, Mapping):
            continue
        if field.get("name") == name:
            return field.get("value")

    return None


class ProwlarrClient:
    """Client for interacting with the Prowlarr API."""

    def __init__(
        self, url: str, api_key: str, timeout: int = 30, indexer_timeout: int | None = None
    ) -> None:
        """Initialize the API client with base URL, key, and timeouts.

        Args:
            url: Prowlarr base URL.
            api_key: Prowlarr API key.
            timeout: Timeout for Prowlarr's own JSON endpoints.
            indexer_timeout: Timeout for Torznab searches, which Prowlarr proxies
                out to the tracker. Defaults to PROWLARR_INDEXER_TIMEOUT.

        """
        self.base_url = normalize_http_url(url)
        self.api_key = api_key
        self.timeout = timeout
        self.indexer_timeout = resolve_indexer_timeout(indexer_timeout)
        self._session = requests.Session()
        self._session.headers.update(
            {
                "X-Api-Key": api_key,
                "Accept": "application/json",
            }
        )

    def _request(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
    ) -> object:
        """Make an API request to Prowlarr. Returns parsed JSON response."""
        url = self.base_url + endpoint
        logger.debug("Prowlarr API: %s %s", method, url)

        try:
            response = self._session.request(
                method=method,
                url=url,
                params=params,
                json=json_data,
                timeout=self.timeout,
                verify=get_ssl_verify(url),
            )

            if not response.ok:
                with suppress(Exception):
                    error_body = response.text[:500]
                    logger.error("Prowlarr API error response: %s", error_body)

            response.raise_for_status()
            return response.json()

        except requests.exceptions.JSONDecodeError as e:
            logger.exception("Invalid JSON response from Prowlarr")
            msg = f"Invalid JSON response: {e}"
            raise ValueError(msg) from e
        except requests.exceptions.HTTPError as e:
            status_code = e.response.status_code if e.response is not None else "unknown"
            reason = e.response.reason if e.response is not None else "unknown"
            logger.exception(
                "Prowlarr API HTTP error: %s %s",
                status_code,
                reason,
            )
            raise
        except requests.exceptions.RequestException:
            logger.exception("Prowlarr API request failed")
            raise

    def test_connection(self) -> tuple[bool, str]:
        """Test connection to Prowlarr. Returns (success, message)."""
        logger.info("Testing Prowlarr connection to: %s", self.base_url)
        try:
            data = _normalize_json_object(
                self._request("GET", "/api/v1/system/status"),
                context="Prowlarr status",
            )
            version = data.get("version", "unknown")
        except requests.exceptions.ConnectionError:
            return False, "Could not connect to Prowlarr. Check the URL."
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else "unknown"
            if e.response is not None and e.response.status_code == _HTTP_STATUS_UNAUTHORIZED:
                return False, "Invalid API key"
            return False, f"HTTP error {status}"
        except _PROWLARR_CLIENT_ERRORS as e:
            return False, f"Connection failed: {e!s}"
        else:
            logger.info("Prowlarr connection successful: version %s", version)
            return True, f"Connected to Prowlarr {version}"

    def get_indexers(self, *, raise_on_error: bool = False) -> list[dict[str, Any]]:
        """Get all configured indexers.

        Args:
            raise_on_error: When True, propagate API failures instead of
                returning an empty list. Callers that must distinguish
                "no indexers" from "the request failed" should set this.

        """
        try:
            return _normalize_json_object_list(
                self._request("GET", "/api/v1/indexer"),
                context="Prowlarr indexer list",
            )
        except _PROWLARR_CLIENT_ERRORS:
            if raise_on_error:
                raise
            logger.exception("Failed to get indexers")
            return []

    def get_disabled_indexers(self, *, now: datetime | None = None) -> dict[int, str]:
        """Get indexers in failure back-off, keyed by ID with their disabledTill value."""
        try:
            entries = _normalize_json_object_list(
                self._request("GET", "/api/v1/indexerstatus"),
                context="Prowlarr indexer status",
            )
        except _PROWLARR_CLIENT_ERRORS:
            logger.exception("Failed to get indexer status")
            return {}

        current = now or datetime.now(UTC)
        disabled: dict[int, str] = {}
        for entry in entries:
            indexer_id = coerce_int_like(entry.get("indexerId"))
            disabled_till_raw = entry.get("disabledTill")
            if indexer_id is None or not disabled_till_raw:
                continue
            try:
                disabled_till = datetime.fromisoformat(str(disabled_till_raw))
            except ValueError:
                continue
            if disabled_till.tzinfo is None:
                disabled_till = disabled_till.replace(tzinfo=UTC)
            if disabled_till > current:
                disabled[indexer_id] = str(disabled_till_raw)
        return disabled

    def get_enabled_indexers_detailed(
        self, *, raise_on_error: bool = False
    ) -> list[dict[str, Any]]:
        """Get enabled indexers, including implementation metadata.

        Note: Prowlarr indexer "name" is user-configurable; prefer
        "implementation"/"implementationName" for stable identification.
        """
        indexers = self.get_indexers(raise_on_error=raise_on_error)
        return [idx for idx in indexers if idx.get("enable", False)]

    def get_enriched_indexer_ids(
        self,
        *,
        restrict_to: list[int] | None = None,
        indexers: list[dict[str, Any]] | None = None,
    ) -> list[int]:
        """Return enabled indexer IDs that benefit from extra Torznab handling.

        Args:
            restrict_to: Optional list of candidate indexer IDs to consider.
            indexers: Optional already-fetched enabled indexer list, so callers
                that need the full records for other reasons can avoid a second
                round trip.

        """
        enriched_ids: list[int] = []

        for idx in indexers if indexers is not None else self.get_enabled_indexers_detailed():
            idx_id_int = coerce_int_like(idx.get("id"))
            if idx_id_int is None:
                continue

            if restrict_to is not None and idx_id_int not in restrict_to:
                continue

            impl = str(
                idx.get("implementation")
                or idx.get("implementationName")
                or idx.get("definitionName")
                or ""
            )
            # Currently only MyAnonamouse provides consistently rich Torznab metadata.
            if impl.strip().lower() == "myanonamouse":
                enriched_ids.append(idx_id_int)

        return enriched_ids

    def get_indexer_seed_settings(
        self, *, restrict_to: list[int] | None = None
    ) -> dict[int, IndexerSeedSettings]:
        """Return configured per-indexer torrent share limits.

        Prowlarr exposes seedTime in minutes, which is also the unit expected by
        torrent clients.

        Raises:
            requests.exceptions.RequestException (and other client errors) when
            the indexer list cannot be fetched. An empty dict strictly means
            "no share limits are configured", never "the request failed" -
            callers rely on this to avoid silently dropping seed limits.

        """
        settings_by_indexer: dict[int, IndexerSeedSettings] = {}

        for idx in self.get_enabled_indexers_detailed(raise_on_error=True):
            idx_id_int = coerce_int_like(idx.get("id"))
            if idx_id_int is None:
                continue
            if restrict_to is not None and idx_id_int not in restrict_to:
                continue
            if str(idx.get("protocol") or "").lower() != "torrent":
                continue

            fields = idx.get("fields")
            ratio_limit = coerce_float_like(_get_field_value(fields, _INDEXER_FIELD_SEED_RATIO))
            seeding_time_limit = coerce_int_like(
                _get_field_value(fields, _INDEXER_FIELD_SEED_TIME_MINUTES)
            )

            settings: IndexerSeedSettings = {}
            if ratio_limit is not None and ratio_limit > 0:
                settings["ratio_limit"] = ratio_limit
            if seeding_time_limit is not None and seeding_time_limit > 0:
                settings["seeding_time_limit_minutes"] = seeding_time_limit

            if settings:
                settings_by_indexer[idx_id_int] = settings

        return settings_by_indexer

    def get_enabled_indexers(self) -> list[dict[str, Any]]:
        """Get enabled indexers with book capability info."""
        indexers = self.get_indexers()
        result = []

        for idx in indexers:
            if not idx.get("enable", False):
                continue

            # Check for book categories (7000-7999 range)
            categories = idx.get("capabilities", {}).get("categories", [])
            has_books = self._has_book_categories(categories)

            result.append(
                {
                    "id": idx.get("id"),
                    "name": idx.get("name"),
                    "protocol": idx.get("protocol"),
                    "has_books": has_books,
                }
            )

        return result

    def torznab_search(
        self,
        *,
        indexer_id: int,
        query: str,
        categories: list[int] | None = None,
        search_type: str = "book",
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Search a specific indexer via Prowlarr's Torznab/Newznab endpoint.

        This returns richer fields (e.g., author/booktitle, torznab tags like
        FreeLeech) than the JSON /api/v1/search endpoint.

        Raises:
            ProwlarrSearchError: The search could not be completed. An empty list
                strictly means the indexer answered with no matches, never that
                the request timed out or errored.

        """
        if not query:
            return []

        endpoint = f"/api/v1/indexer/{int(indexer_id)}/newznab"
        url = self.base_url + endpoint

        params: dict[str, Any] = {
            "t": search_type,
            "q": query,
            "limit": limit,
            "offset": offset,
        }
        if categories:
            params["cat"] = ",".join(str(c) for c in categories)

        logger.debug("Prowlarr API: GET %s (torznab)", url)

        try:
            response = self._session.get(
                url=url,
                params=params,
                timeout=(_CONNECT_TIMEOUT_SECONDS, self.indexer_timeout),
                headers={
                    # Override the session default JSON accept header.
                    "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8"
                },
                verify=get_ssl_verify(url),
            )
            if not response.ok:
                with suppress(Exception):
                    error_body = response.text[:500]
                    logger.error("Prowlarr Torznab error response: %s", error_body)
            response.raise_for_status()

            results = parse_torznab_xml(response.text)
            # Ensure indexerId is always set (Prowlarr includes it, but be defensive).
            for r in results:
                if r.get("indexerId") is None:
                    r["indexerId"] = int(indexer_id)
        except requests.exceptions.Timeout as e:
            logger.warning(
                "Prowlarr Torznab search for indexer %s timed out after %ss. An indexer "
                "behind FlareSolverr can need far longer than that on a cold Cloudflare "
                "challenge - raise PROWLARR_INDEXER_TIMEOUT if this keeps happening.",
                indexer_id,
                self.indexer_timeout,
            )
            msg = f"indexer {indexer_id} did not respond within {self.indexer_timeout}s"
            raise ProwlarrSearchError(msg) from e
        except Exception as e:
            logger.exception("Prowlarr Torznab search failed for indexer %s", indexer_id)
            msg = f"indexer {indexer_id} search failed: {e}"
            raise ProwlarrSearchError(msg) from e
        else:
            return results

    def _has_book_categories(self, categories: list[dict[str, Any]]) -> bool:
        """Check if any category or subcategory is in the book range (7000-7999)."""
        for cat in categories:
            cat_id = cat.get("id", 0)
            if _BOOK_CATEGORY_RANGE_START <= cat_id < _BOOK_CATEGORY_RANGE_END:
                return True
            for subcat in cat.get("subCategories", []):
                if _BOOK_CATEGORY_RANGE_START <= subcat.get("id", 0) < _BOOK_CATEGORY_RANGE_END:
                    return True
        return False
