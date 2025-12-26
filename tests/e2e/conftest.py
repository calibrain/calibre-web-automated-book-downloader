"""
E2E Test Configuration and Fixtures.

These tests require the full application stack to be running.
Run with: docker exec test-cwabd python3 -m pytest tests/e2e/ -v -m e2e
"""

import os
import time
from typing import Generator, List, Optional
from dataclasses import dataclass, field

import pytest
import requests


# Default test configuration
DEFAULT_BASE_URL = "http://localhost:8084"
DEFAULT_TIMEOUT = 10
POLL_INTERVAL = 2
DOWNLOAD_TIMEOUT = 300  # 5 minutes max for downloads


@dataclass
class APIClient:
    """HTTP client for E2E API testing."""

    base_url: str
    timeout: int = DEFAULT_TIMEOUT
    session: requests.Session = field(default_factory=requests.Session)

    def get(self, path: str, **kwargs) -> requests.Response:
        """Make a GET request."""
        kwargs.setdefault("timeout", self.timeout)
        return self.session.get(f"{self.base_url}{path}", **kwargs)

    def post(self, path: str, **kwargs) -> requests.Response:
        """Make a POST request."""
        kwargs.setdefault("timeout", self.timeout)
        return self.session.post(f"{self.base_url}{path}", **kwargs)

    def put(self, path: str, **kwargs) -> requests.Response:
        """Make a PUT request."""
        kwargs.setdefault("timeout", self.timeout)
        return self.session.put(f"{self.base_url}{path}", **kwargs)

    def delete(self, path: str, **kwargs) -> requests.Response:
        """Make a DELETE request."""
        kwargs.setdefault("timeout", self.timeout)
        return self.session.delete(f"{self.base_url}{path}", **kwargs)

    def wait_for_health(self, max_wait: int = 30) -> bool:
        """Wait for the server to be healthy."""
        start = time.time()
        while time.time() - start < max_wait:
            try:
                resp = self.get("/api/health")
                if resp.status_code == 200:
                    return True
            except requests.exceptions.ConnectionError:
                pass
            time.sleep(1)
        return False


@dataclass
class DownloadTracker:
    """Tracks downloads for cleanup after tests."""

    client: APIClient
    queued_ids: List[str] = field(default_factory=list)

    def track(self, book_id: str) -> str:
        """Track a book ID for cleanup."""
        self.queued_ids.append(book_id)
        return book_id

    def cleanup(self) -> None:
        """Cancel all tracked downloads."""
        for book_id in self.queued_ids:
            try:
                self.client.delete(f"/api/download/{book_id}/cancel")
            except Exception:
                pass  # Best effort cleanup
        self.queued_ids.clear()

    def wait_for_status(
        self,
        book_id: str,
        target_states: List[str],
        timeout: int = DOWNLOAD_TIMEOUT,
    ) -> Optional[dict]:
        """
        Poll status until book reaches one of the target states.

        Args:
            book_id: The book/task ID to check
            target_states: List of states to wait for (e.g., ["complete", "error"])
            timeout: Maximum seconds to wait

        Returns:
            Status dict if target state reached, None if timeout
        """
        start = time.time()
        while time.time() - start < timeout:
            try:
                resp = self.client.get("/api/status")
                if resp.status_code != 200:
                    time.sleep(POLL_INTERVAL)
                    continue

                status_data = resp.json()

                # Check each status category
                for state in target_states:
                    if state in status_data and book_id in status_data[state]:
                        return {
                            "state": state,
                            "data": status_data[state][book_id],
                        }

                # Check for error state
                if "error" in status_data and book_id in status_data["error"]:
                    return {
                        "state": "error",
                        "data": status_data["error"][book_id],
                    }

            except Exception:
                pass

            time.sleep(POLL_INTERVAL)

        return None


@pytest.fixture(scope="session")
def base_url() -> str:
    """Get the base URL for the API server."""
    return os.environ.get("E2E_BASE_URL", DEFAULT_BASE_URL)


@pytest.fixture(scope="session")
def api_client(base_url: str) -> Generator[APIClient, None, None]:
    """Create an API client for the test session."""
    client = APIClient(base_url=base_url)

    # Wait for server to be healthy
    if not client.wait_for_health():
        pytest.skip("Server not available - ensure the app is running")

    yield client

    # Cleanup session
    client.session.close()


@pytest.fixture
def download_tracker(api_client: APIClient) -> Generator[DownloadTracker, None, None]:
    """Create a download tracker that cleans up after each test."""
    tracker = DownloadTracker(client=api_client)
    yield tracker
    tracker.cleanup()


@pytest.fixture(scope="session")
def server_config(api_client: APIClient) -> dict:
    """Get server configuration."""
    resp = api_client.get("/api/config")
    if resp.status_code != 200:
        return {}
    return resp.json()
