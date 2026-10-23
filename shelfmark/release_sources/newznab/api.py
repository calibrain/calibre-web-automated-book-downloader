"""Newznab API client - connects to any Newznab-compatible indexer or aggregator."""

from __future__ import annotations

import re
from typing import Any

import requests

from shelfmark.core.logger import setup_logger
from shelfmark.core.utils import normalize_http_url
from shelfmark.download.network import get_ssl_verify
from shelfmark.release_sources.prowlarr.torznab import parse_torznab_xml

logger = setup_logger(__name__)


class NewznabClient:
    """Client for any Newznab-compatible indexer API."""

    def __init__(self, url: str, api_key: str, timeout: int = 30) -> None:
        self.base_url = normalize_http_url(url)
        self.api_key = api_key
        self.timeout = timeout
        self._session = requests.Session()

    def _api_url(self) -> str:
        """Return the Newznab API endpoint URL."""
        base = self.base_url.rstrip("/")
        # Many indexers expose the API at /api; others at the root with ?page=rss.
        # Prefer /api if the base URL doesn't already end with it.
        if not base.endswith("/api"):
            return base + "/api"
        return base

    def _get(
        self,
        params: dict[str, Any],
        *,
        accept_xml: bool = False,
    ) -> requests.Response:
        """Make a GET request to the Newznab API endpoint."""
        params = {k: v for k, v in params.items() if v is not None}
        if self.api_key:
            params["apikey"] = self.api_key

        url = self._api_url()
        logger.debug("Newznab API: GET %s params=%s", url, sorted(params))

        headers = {}
        if accept_xml:
            headers["Accept"] = "application/rss+xml, application/xml;q=0.9, */*;q=0.8"

        response = self._session.get(
            url=url,
            params=params,
            headers=headers,
            timeout=self.timeout,
            verify=get_ssl_verify(url),
        )
        response.raise_for_status()
        return response

    def test_connection(self) -> tuple[bool, str]:
        """Test connection via the capabilities endpoint. Returns (success, message)."""
        logger.info("Testing Newznab connection to: %s", self.base_url)
        try:
            response = self._get({"t": "caps"})
            # Caps endpoint returns XML; a 200 is sufficient to confirm connectivity.
            # Try to extract the server title from the XML for a friendly message.
            text = response.text or ""
            title = "Newznab indexer"
            # Try <server title="..."/> attribute (NZBHydra2 style), then <title> element
            m = re.search(r'<server[^>]+title="([^"]+)"', text, re.IGNORECASE)
            if not m:
                m = re.search(r"<title>([^<]+)</title>", text, re.IGNORECASE)
            if m:
                title = m.group(1).strip()
            logger.info("Newznab connection successful: %s", title)
        except requests.exceptions.ConnectionError:
            return False, "Could not connect. Check the URL."
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else "unknown"
            if e.response is not None and e.response.status_code == 401:
                return False, "Invalid API key"
            return False, f"HTTP error {status}"
        except requests.exceptions.RequestException as e:
            return False, f"Connection failed: {e!s}"
        else:
            return True, f"Connected to {title}"

    def search(
        self,
        query: str,
        categories: list[int] | None = None,
        search_type: str = "search",
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Search the Newznab indexer and return parsed results.

        Args:
            query: Search string.
            categories: Optional list of Newznab category IDs (e.g. [7000, 3030]).
            search_type: Newznab search type ("search", "book", "audio").
            limit: Max results to return.
            offset: Result page offset.

        Returns:
            List of result dicts shaped like Prowlarr JSON search results so that
            the shared ``_prowlarr_result_to_release`` converter can process them.
        """
        if not query:
            return []

        params: dict[str, Any] = {
            "t": search_type,
            "q": query,
            "limit": limit,
            "offset": offset,
        }
        if categories:
            params["cat"] = ",".join(str(c) for c in categories)

        try:
            response = self._get(params, accept_xml=True)
            results = parse_torznab_xml(response.text)
            logger.debug("Newznab search '%s': %d results", query, len(results))
            if not results:
                preview = response.text[:300].strip() if response.text else "<empty>"
                logger.debug("Newznab empty response body: %s", preview)
        except requests.exceptions.RequestException:
            logger.exception("Newznab search request failed")
            return []
        except Exception:
            logger.exception("Newznab search failed")
            return []
        else:
            return results
