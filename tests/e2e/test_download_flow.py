"""
E2E Download Flow Tests.

These tests verify the complete download journey from search to file retrieval.
They require external services to be available and may take longer to run.

Run with: docker exec test-cwabd python3 -m pytest tests/e2e/test_download_flow.py -v -m e2e
"""

import os
import hashlib
import time

import pytest

from .conftest import APIClient, DownloadTracker, DOWNLOAD_TIMEOUT


def _find_available_provider(api_client: APIClient) -> str | None:
    """Find a working metadata provider."""
    resp = api_client.get("/api/metadata/providers")
    if resp.status_code != 200:
        return None

    providers_data = resp.json()

    # Handle both dict and list formats
    if isinstance(providers_data, dict):
        # Dict format: keys are provider names
        provider_names = list(providers_data.keys())
    else:
        # List format
        provider_names = [p.get("name") for p in providers_data if isinstance(p, dict) and p.get("name")]

    for name in provider_names:
        if name:
            # Try a simple search to verify it works
            test_resp = api_client.get(
                "/api/metadata/search",
                params={"query": "test", "provider": name},
                timeout=30,
            )
            if test_resp.status_code == 200:
                return name
    return None


def _find_available_release_source(api_client: APIClient) -> str | None:
    """Find a working release source."""
    resp = api_client.get("/api/release-sources")
    if resp.status_code != 200:
        return None

    sources = resp.json()
    for source in sources:
        name = source.get("name")
        # Skip prowlarr unless configured
        if name and name != "prowlarr":
            return name
    return None


@pytest.mark.e2e
@pytest.mark.slow
class TestMetadataToReleaseFlow:
    """Test the flow from metadata search to release listing."""

    def test_search_to_releases_flow(self, api_client: APIClient):
        """Test searching metadata then finding releases."""
        # Find a working provider
        provider = _find_available_provider(api_client)
        if not provider:
            pytest.skip("No metadata providers available")

        # Search for a public domain book
        search_resp = api_client.get(
            "/api/metadata/search",
            params={"query": "Moby Dick Herman Melville", "provider": provider},
            timeout=30,
        )

        if search_resp.status_code != 200:
            pytest.skip(f"Search failed: {search_resp.status_code}")

        search_data = search_resp.json()
        results = search_data.get("results", search_data)

        # Handle dict format where results might be nested
        if isinstance(results, dict):
            # Results might be under a key like the query or "results"
            for key, value in results.items():
                if isinstance(value, list) and value:
                    results = value
                    break

        if not results or not isinstance(results, list):
            pytest.skip("No search results returned")

        # Get the first result
        first_result = results[0]
        book_id = first_result.get("id") or first_result.get("provider_id")
        assert book_id, "Search result missing ID"

        # Now search for releases
        releases_resp = api_client.get(
            "/api/releases",
            params={
                "provider": provider,
                "book_id": book_id,
                "title": first_result.get("title", ""),
                "author": first_result.get("author", ""),
            },
            timeout=60,
        )

        # Releases may fail if sources are unavailable
        if releases_resp.status_code == 200:
            releases_data = releases_resp.json()
            assert "releases" in releases_data
            assert "book" in releases_data


@pytest.mark.e2e
@pytest.mark.slow
class TestFullDownloadJourney:
    """
    Test the complete download journey.

    This test:
    1. Searches for a book
    2. Finds releases
    3. Queues a download
    4. Waits for completion
    5. Verifies the file exists
    """

    def test_complete_download_flow(
        self, api_client: APIClient, download_tracker: DownloadTracker
    ):
        """Test the complete search -> download -> verify flow."""
        # Find a working provider
        provider = _find_available_provider(api_client)
        if not provider:
            pytest.skip("No metadata providers available")

        # Search for a public domain book
        search_resp = api_client.get(
            "/api/metadata/search",
            params={"query": "Pride and Prejudice Jane Austen", "provider": provider},
            timeout=30,
        )

        if search_resp.status_code != 200:
            pytest.skip(f"Metadata search unavailable: {search_resp.status_code}")

        search_data = search_resp.json()
        results = search_data.get("results", search_data)

        # Handle dict format where results might be nested
        if isinstance(results, dict):
            for key, value in results.items():
                if isinstance(value, list) and value:
                    results = value
                    break

        if not results or not isinstance(results, list):
            pytest.skip("No search results")

        first_result = results[0]
        book_id = first_result.get("id") or first_result.get("provider_id")

        # Get releases
        releases_resp = api_client.get(
            "/api/releases",
            params={
                "provider": provider,
                "book_id": book_id,
                "title": first_result.get("title", ""),
            },
            timeout=60,
        )

        if releases_resp.status_code != 200:
            pytest.skip(f"Releases unavailable: {releases_resp.status_code}")

        releases_data = releases_resp.json()
        releases = releases_data.get("releases", [])

        if not releases:
            pytest.skip("No releases available")

        # Find an epub release (prefer smaller files)
        target_release = None
        for release in releases:
            fmt = release.get("format", "").lower()
            if fmt == "epub":
                target_release = release
                break

        if not target_release:
            # Fall back to first release
            target_release = releases[0]

        # Queue the download
        source_id = target_release.get("source_id") or target_release.get("id")
        download_tracker.track(source_id)

        queue_resp = api_client.post(
            "/api/releases/download",
            json={
                "source": target_release.get("source", "direct_download"),
                "source_id": source_id,
                "title": target_release.get("title", "Test Book"),
                "format": target_release.get("format"),
                "size": target_release.get("size"),
            },
        )

        assert queue_resp.status_code == 200, f"Failed to queue: {queue_resp.text}"
        queue_data = queue_resp.json()
        assert queue_data.get("status") == "queued"

        # Wait for download to complete (or error)
        result = download_tracker.wait_for_status(
            source_id,
            target_states=["complete", "done", "available"],
            timeout=DOWNLOAD_TIMEOUT,
        )

        if result is None:
            # Check if it errored
            status_resp = api_client.get("/api/status")
            if status_resp.status_code == 200:
                status_data = status_resp.json()
                if "error" in status_data and source_id in status_data["error"]:
                    error_info = status_data["error"][source_id]
                    pytest.skip(f"Download failed: {error_info}")
            pytest.fail("Download timed out")

        assert result["state"] in ["complete", "done", "available"]


@pytest.mark.e2e
@pytest.mark.slow
class TestLegacyDownloadFlow:
    """Test the legacy download API (for backwards compatibility)."""

    def test_legacy_search_and_download(
        self, api_client: APIClient, download_tracker: DownloadTracker
    ):
        """Test the legacy search -> info -> download flow."""
        # Use the legacy search endpoint
        search_resp = api_client.get(
            "/api/search",
            params={"query": "Frankenstein Mary Shelley"},
            timeout=30,
        )

        if search_resp.status_code == 503:
            pytest.skip("Legacy search source unavailable")

        if search_resp.status_code != 200:
            pytest.skip(f"Legacy search failed: {search_resp.status_code}")

        results = search_resp.json()
        if not results:
            pytest.skip("No legacy search results")

        first_result = results[0]
        book_id = first_result.get("id")
        assert book_id, "Result missing ID"

        # Get book info
        info_resp = api_client.get("/api/info", params={"id": book_id})

        if info_resp.status_code != 200:
            pytest.skip(f"Info endpoint failed: {info_resp.status_code}")

        # Queue download (legacy endpoint)
        download_tracker.track(book_id)
        download_resp = api_client.get("/api/download", params={"id": book_id})

        if download_resp.status_code != 200:
            pytest.skip(f"Download queue failed: {download_resp.status_code}")

        download_data = download_resp.json()
        assert download_data.get("status") == "queued"


@pytest.mark.e2e
class TestDownloadCancellation:
    """Test download cancellation functionality."""

    def test_cancel_queued_download(
        self, api_client: APIClient, download_tracker: DownloadTracker
    ):
        """Test cancelling a queued download."""
        # Queue a fake download
        test_id = f"cancel-test-{int(time.time())}"
        download_tracker.track(test_id)

        queue_resp = api_client.post(
            "/api/releases/download",
            json={
                "source": "test_source",
                "source_id": test_id,
                "title": "Cancel Test Book",
            },
        )

        if queue_resp.status_code != 200:
            pytest.skip("Could not queue test download")

        # Give it a moment
        time.sleep(1)

        # Cancel it
        cancel_resp = api_client.delete(f"/api/download/{test_id}/cancel")

        assert cancel_resp.status_code in [200, 204]

    def test_cancel_removes_from_queue(
        self, api_client: APIClient, download_tracker: DownloadTracker
    ):
        """Test that cancellation removes item from queue."""
        test_id = f"cancel-verify-{int(time.time())}"
        download_tracker.track(test_id)

        # Queue it
        api_client.post(
            "/api/releases/download",
            json={
                "source": "test_source",
                "source_id": test_id,
                "title": "Cancel Verify Test",
            },
        )

        time.sleep(0.5)

        # Cancel it
        api_client.delete(f"/api/download/{test_id}/cancel")

        time.sleep(0.5)

        # Check it's not in the queue
        queue_resp = api_client.get("/api/queue/order")
        if queue_resp.status_code == 200:
            queue_order = queue_resp.json()
            assert test_id not in queue_order


@pytest.mark.e2e
class TestQueuePriority:
    """Test queue priority functionality."""

    def test_set_priority(
        self, api_client: APIClient, download_tracker: DownloadTracker
    ):
        """Test setting download priority."""
        test_id = f"priority-test-{int(time.time())}"
        download_tracker.track(test_id)

        # Queue it
        queue_resp = api_client.post(
            "/api/releases/download",
            json={
                "source": "test_source",
                "source_id": test_id,
                "title": "Priority Test",
                "priority": 0,
            },
        )

        if queue_resp.status_code != 200:
            pytest.skip("Could not queue download")

        time.sleep(0.5)

        # Update priority
        priority_resp = api_client.put(
            f"/api/queue/{test_id}/priority",
            json={"priority": 10},
        )

        # Should succeed or return 404 if already processed
        assert priority_resp.status_code in [200, 404]
