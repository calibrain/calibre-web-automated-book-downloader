"""Prowlarr API client for connection testing, indexer listing, and search."""

from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin

import requests

from cwa_book_downloader.core.logger import setup_logger

logger = setup_logger(__name__)


class ProwlarrClient:
    """Client for interacting with the Prowlarr API."""

    def __init__(self, url: str, api_key: str, timeout: int = 30):
        self.base_url = url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({
            "X-Api-Key": api_key,
            "Accept": "application/json",
        })

    def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Make an API request to Prowlarr. Returns parsed JSON response."""
        url = urljoin(self.base_url, endpoint)
        logger.debug(f"Prowlarr API: {method} {url}")

        try:
            response = self._session.request(
                method=method,
                url=url,
                params=params,
                json=json_data,
                timeout=self.timeout,
            )

            if not response.ok:
                try:
                    error_body = response.text[:500]
                    logger.error(f"Prowlarr API error response: {error_body}")
                except Exception:
                    pass

            response.raise_for_status()
            return response.json()

        except requests.exceptions.JSONDecodeError as e:
            logger.error(f"Invalid JSON response from Prowlarr: {e}")
            raise ValueError(f"Invalid JSON response: {e}")
        except requests.exceptions.HTTPError as e:
            logger.error(f"Prowlarr API HTTP error: {e.response.status_code} {e.response.reason}")
            raise
        except requests.exceptions.RequestException as e:
            logger.error(f"Prowlarr API request failed: {e}")
            raise

    def test_connection(self) -> Tuple[bool, str]:
        """Test connection to Prowlarr. Returns (success, message)."""
        logger.info(f"Testing Prowlarr connection to: {self.base_url}")
        try:
            data = self._request("GET", "/api/v1/system/status")
            version = data.get("version", "unknown")
            logger.info(f"Prowlarr connection successful: version {version}")
            return True, f"Connected to Prowlarr {version}"
        except requests.exceptions.ConnectionError:
            return False, "Could not connect to Prowlarr. Check the URL."
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else "unknown"
            if e.response is not None and e.response.status_code == 401:
                return False, "Invalid API key"
            return False, f"HTTP error {status}"
        except Exception as e:
            return False, f"Connection failed: {str(e)}"

    def get_indexers(self) -> List[Dict[str, Any]]:
        """Get all configured indexers."""
        try:
            indexers = self._request("GET", "/api/v1/indexer")
            return indexers
        except Exception as e:
            logger.error(f"Failed to get indexers: {e}")
            return []

    def get_enabled_indexers(self) -> List[Dict[str, Any]]:
        """Get enabled indexers with book capability info."""
        indexers = self.get_indexers()
        result = []

        for idx in indexers:
            if not idx.get("enable", False):
                continue

            # Check for book categories (7000-7999 range)
            categories = idx.get("capabilities", {}).get("categories", [])
            has_books = self._has_book_categories(categories)

            result.append({
                "id": idx.get("id"),
                "name": idx.get("name"),
                "protocol": idx.get("protocol"),
                "has_books": has_books,
            })

        return result

    def _has_book_categories(self, categories: List[Dict[str, Any]]) -> bool:
        """Check if any category or subcategory is in the book range (7000-7999)."""
        for cat in categories:
            cat_id = cat.get("id", 0)
            if 7000 <= cat_id <= 7999:
                return True
            for subcat in cat.get("subCategories", []):
                if 7000 <= subcat.get("id", 0) <= 7999:
                    return True
        return False

    def search(
        self,
        query: str,
        indexer_ids: Optional[List[int]] = None,
        categories: Optional[List[int]] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Search for releases via Prowlarr."""
        if not query:
            return []

        params: Dict[str, Any] = {"query": query, "limit": limit}
        if indexer_ids:
            params["indexerIds"] = indexer_ids
        if categories:
            params["categories"] = categories

        try:
            results = self._request("GET", "/api/v1/search", params=params)
            return results if isinstance(results, list) else []
        except Exception as e:
            logger.error(f"Prowlarr search failed: {e}")
            return []
