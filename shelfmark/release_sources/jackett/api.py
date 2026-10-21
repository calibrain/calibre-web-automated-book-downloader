"""Jackett API client for connection testing, indexer listing, and Torznab search."""

import re
from contextlib import suppress
from http import HTTPStatus
from typing import Any

import requests
from defusedxml import ElementTree as DefusedElementTree
from defusedxml.common import DefusedXmlException

from shelfmark.core.logger import setup_logger
from shelfmark.core.request_helpers import normalize_optional_text
from shelfmark.core.utils import normalize_http_url
from shelfmark.download.network import get_ssl_verify
from shelfmark.release_sources.prowlarr.api import (
    _CONNECT_TIMEOUT_SECONDS,
    resolve_indexer_timeout,
)
from shelfmark.release_sources.prowlarr.torznab import parse_torznab_xml

logger = setup_logger(__name__)

# Jackett's aggregate pseudo-indexer: one request fans out to every configured indexer.
ALL_INDEXERS_ID = "all"

JACKETT_TIMEOUT_CONFIG_KEY = "JACKETT_INDEXER_TIMEOUT"

# Jackett reports bad credentials and similar failures as a Torznab <error> document,
# sometimes alongside a 200 status.
_TORZNAB_ERROR_RE = re.compile(
    r'<error\s+code="(?P<code>\d+)"\s+description="(?P<description>[^"]*)"', re.IGNORECASE
)
_TORZNAB_AUTH_ERROR_CODES = {"100", "101", "102"}

_JACKETT_CLIENT_ERRORS = (
    requests.exceptions.RequestException,
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
)


class JackettSearchError(RuntimeError):
    """A Jackett search could not be completed.

    Distinct from an empty result list so a timeout or error is not reported to the
    user as "no releases found for this book".
    """


def _torznab_error(text: str) -> tuple[str, str] | None:
    """Return (code, description) when a response body is a Torznab error document."""
    match = _TORZNAB_ERROR_RE.search(text or "")
    if not match:
        return None
    return match.group("code"), match.group("description")


class JackettClient:
    """Client for interacting with a Jackett instance."""

    def __init__(
        self, url: str, api_key: str, timeout: int = 30, indexer_timeout: int | None = None
    ) -> None:
        """Initialize the client with base URL, API key and timeouts.

        Args:
            url: Jackett base URL.
            api_key: Jackett API key.
            timeout: Timeout for Jackett's own endpoints (indexer list, caps).
            indexer_timeout: Timeout for searches, which Jackett proxies out to the
                trackers. Defaults to JACKETT_INDEXER_TIMEOUT.

        """
        self.base_url = normalize_http_url(url)
        self.api_key = api_key
        self.timeout = timeout
        self.indexer_timeout = resolve_indexer_timeout(
            indexer_timeout, config_key=JACKETT_TIMEOUT_CONFIG_KEY
        )
        self._session = requests.Session()

    def _torznab_url(self, indexer_id: str) -> str:
        return f"{self.base_url}/api/v2.0/indexers/{indexer_id}/results/torznab/api"

    def test_connection(self) -> tuple[bool, str]:
        """Test connection and API key via the aggregate caps endpoint."""
        logger.info("Testing Jackett connection to: %s", self.base_url)
        url = self._torznab_url(ALL_INDEXERS_ID)
        try:
            response = self._session.get(
                url,
                params={"t": "caps", "apikey": self.api_key},
                timeout=(_CONNECT_TIMEOUT_SECONDS, self.timeout),
                verify=get_ssl_verify(url),
            )
            error = _torznab_error(response.text)
            if error and error[0] in _TORZNAB_AUTH_ERROR_CODES:
                return False, "Invalid API key"
            response.raise_for_status()
            if error:
                return False, f"Jackett error {error[0]}: {error[1]}"
        except requests.exceptions.ConnectionError:
            return False, "Could not connect to Jackett. Check the URL."
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else "unknown"
            if e.response is not None and e.response.status_code in (
                HTTPStatus.UNAUTHORIZED,
                HTTPStatus.FORBIDDEN,
            ):
                return False, "Invalid API key"
            return False, f"HTTP error {status}"
        except _JACKETT_CLIENT_ERRORS as e:
            return False, f"Connection failed: {e!s}"

        message = "Connected to Jackett"
        with suppress(*_JACKETT_CLIENT_ERRORS):
            message += f" ({len(self.get_indexers(raise_on_error=True))} indexers configured)"
        logger.info("Jackett connection successful")
        return True, message

    def get_indexers(self, *, raise_on_error: bool = False) -> list[dict[str, str]]:
        """Get configured indexers as ``{"id", "name"}`` records.

        Args:
            raise_on_error: Propagate failures instead of returning an empty list.

        """
        # /api/v2.0/indexers is the admin UI's endpoint and wants a login cookie; the
        # Torznab "indexers" function lists the same thing for a plain API key.
        url = self._torznab_url(ALL_INDEXERS_ID)
        try:
            response = self._session.get(
                url,
                params={"t": "indexers", "configured": "true", "apikey": self.api_key},
                timeout=self.timeout,
                verify=get_ssl_verify(url),
            )
            error = _torznab_error(response.text)
            if error:
                msg = f"Jackett error {error[0]}: {error[1]}"
                raise RuntimeError(msg)
            response.raise_for_status()
            root = DefusedElementTree.fromstring(response.text)
        except (DefusedElementTree.ParseError, DefusedXmlException) as e:
            if raise_on_error:
                msg = f"Unexpected Jackett indexer list response: {e}"
                raise ValueError(msg) from e
            logger.exception("Failed to parse Jackett indexers")
            return []
        except _JACKETT_CLIENT_ERRORS:
            if raise_on_error:
                raise
            logger.exception("Failed to get Jackett indexers")
            return []

        indexers: list[dict[str, str]] = []
        for item in root.iter("indexer"):
            if item.get("configured", "true").lower() == "false":
                continue
            indexer_id = normalize_optional_text(item.get("id"))
            if indexer_id is None:
                continue
            name = normalize_optional_text(item.findtext("title")) or indexer_id
            indexers.append({"id": indexer_id, "name": name})
        return indexers

    def torznab_search(
        self,
        *,
        indexer_id: str = ALL_INDEXERS_ID,
        query: str,
        categories: list[int] | None = None,
        search_type: str = "book",
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Search one indexer (or ``all``) through Jackett's Torznab endpoint.

        Raises:
            JackettSearchError: The search could not be completed. An empty list
                strictly means Jackett answered with no matches.

        """
        if not query:
            return []

        url = self._torznab_url(indexer_id)
        params: dict[str, Any] = {
            "apikey": self.api_key,
            "t": search_type,
            "q": query,
            "limit": limit,
            "offset": offset,
        }
        if categories:
            params["cat"] = ",".join(str(c) for c in categories)

        logger.debug("Jackett API: GET %s (torznab, indexer=%s)", url, indexer_id)

        try:
            response = self._session.get(
                url,
                params=params,
                timeout=(_CONNECT_TIMEOUT_SECONDS, self.indexer_timeout),
                headers={"Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8"},
                verify=get_ssl_verify(url),
            )
            if not response.ok:
                with suppress(Exception):
                    logger.error("Jackett Torznab error response: %s", response.text[:500])
            error = _torznab_error(response.text)
            if error:
                msg = f"Jackett error {error[0]}: {error[1]}"
                raise JackettSearchError(msg)  # noqa: TRY301
            response.raise_for_status()
            results = parse_torznab_xml(response.text)
        except JackettSearchError:
            raise
        except requests.exceptions.Timeout as e:
            logger.warning(
                "Jackett search for indexer %s timed out after %ss - raise %s if this "
                "keeps happening.",
                indexer_id,
                self.indexer_timeout,
                JACKETT_TIMEOUT_CONFIG_KEY,
            )
            msg = f"indexer {indexer_id} did not respond within {self.indexer_timeout}s"
            raise JackettSearchError(msg) from e
        except Exception as e:
            logger.exception("Jackett search failed for indexer %s", indexer_id)
            msg = f"indexer {indexer_id} search failed: {e}"
            raise JackettSearchError(msg) from e
        else:
            return results
