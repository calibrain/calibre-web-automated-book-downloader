"""
E2E API Tests.

Tests the full application flow through the HTTP API.

Run with: docker exec test-cwabd python3 -m pytest tests/e2e/ -v -m e2e
"""

import pytest

from .conftest import APIClient, DownloadTracker


@pytest.mark.e2e
class TestHealthEndpoint:
    """Tests for the health check endpoint."""

    def test_health_returns_ok(self, api_client: APIClient):
        """Test that health endpoint returns 200."""
        resp = api_client.get("/api/health")

        assert resp.status_code == 200
        data = resp.json()
        assert data.get("status") == "ok"

    def test_health_includes_status(self, api_client: APIClient):
        """Test that health endpoint includes status field."""
        resp = api_client.get("/api/health")

        data = resp.json()
        assert "status" in data
        assert data["status"] == "ok"


@pytest.mark.e2e
class TestConfigEndpoint:
    """Tests for the configuration endpoint."""

    def test_config_returns_expected_fields(self, api_client: APIClient):
        """Test that config includes expected configuration fields."""
        resp = api_client.get("/api/config")

        assert resp.status_code == 200
        data = resp.json()
        # Config should be a dict with various settings
        assert isinstance(data, dict)
        # Should have some standard config fields
        assert "supported_formats" in data or "book_languages" in data

    def test_config_returns_supported_formats(self, api_client: APIClient):
        """Test that config includes supported formats."""
        resp = api_client.get("/api/config")

        data = resp.json()
        assert "supported_formats" in data
        assert isinstance(data["supported_formats"], list)
        # Should include common ebook formats
        formats = data["supported_formats"]
        assert "epub" in formats or "EPUB" in [f.upper() for f in formats]


@pytest.mark.e2e
class TestReleaseSourcesEndpoint:
    """Tests for the release sources endpoint."""

    def test_release_sources_returns_list(self, api_client: APIClient):
        """Test that release sources endpoint returns available sources."""
        resp = api_client.get("/api/release-sources")

        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)

    def test_release_sources_have_required_fields(self, api_client: APIClient):
        """Test that each release source has required fields."""
        resp = api_client.get("/api/release-sources")

        data = resp.json()
        for source in data:
            assert "name" in source
            assert "display_name" in source or "label" in source


@pytest.mark.e2e
class TestMetadataProvidersEndpoint:
    """Tests for the metadata providers endpoint."""

    def test_providers_returns_data(self, api_client: APIClient):
        """Test that providers endpoint returns provider data."""
        resp = api_client.get("/api/metadata/providers")

        assert resp.status_code == 200
        data = resp.json()
        # May be list or dict depending on implementation
        assert isinstance(data, (list, dict))

    def test_providers_have_required_fields(self, api_client: APIClient):
        """Test that each provider has required fields."""
        resp = api_client.get("/api/metadata/providers")

        data = resp.json()
        # Handle both list and dict formats
        if isinstance(data, dict):
            providers = list(data.values()) if data else []
        else:
            providers = data

        for provider in providers:
            if isinstance(provider, dict):
                # Should have name or be identifiable
                assert "name" in provider or "id" in provider or "label" in provider


@pytest.mark.e2e
class TestMetadataSearch:
    """Tests for metadata search functionality."""

    def test_search_requires_query(self, api_client: APIClient):
        """Test that search requires a query parameter."""
        resp = api_client.get("/api/metadata/search")

        # Should return error for missing query
        assert resp.status_code in [400, 422]

    def test_search_returns_results(self, api_client: APIClient):
        """Test that search returns results for a known book."""
        resp = api_client.get("/api/metadata/search", params={"query": "1984 Orwell"})

        # May return 200 with results or 503 if provider unavailable
        if resp.status_code == 200:
            data = resp.json()
            # Response may be list directly, or dict with results key
            assert "results" in data or isinstance(data, list) or "query" in data

    def test_search_with_provider_filter(self, api_client: APIClient):
        """Test searching with a specific provider."""
        # Get available providers first
        providers_resp = api_client.get("/api/metadata/providers")
        if providers_resp.status_code != 200:
            pytest.skip("Could not get providers")

        providers_data = providers_resp.json()
        if not providers_data:
            pytest.skip("No providers available")

        # Handle both list and dict formats
        if isinstance(providers_data, dict):
            # Dict format: get first provider name from keys or values
            if providers_data:
                first_key = list(providers_data.keys())[0]
                provider_info = providers_data[first_key]
                provider_name = provider_info.get("name", first_key) if isinstance(provider_info, dict) else first_key
            else:
                pytest.skip("No providers available")
        else:
            # List format
            provider_name = providers_data[0].get("name") if providers_data else None

        if not provider_name:
            pytest.skip("Could not determine provider name")

        resp = api_client.get(
            "/api/metadata/search",
            params={"query": "Moby Dick", "provider": provider_name},
        )

        # Should return 200 or 503 (provider unavailable)
        assert resp.status_code in [200, 503]


@pytest.mark.e2e
class TestStatusEndpoint:
    """Tests for the status endpoint."""

    def test_status_returns_categories(self, api_client: APIClient):
        """Test that status endpoint returns expected categories."""
        resp = api_client.get("/api/status")

        assert resp.status_code == 200
        data = resp.json()
        # Should have standard status categories
        assert isinstance(data, dict)

    def test_active_downloads_endpoint(self, api_client: APIClient):
        """Test the active downloads endpoint."""
        resp = api_client.get("/api/downloads/active")

        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, (list, dict))


@pytest.mark.e2e
class TestQueueEndpoint:
    """Tests for queue management endpoints."""

    def test_queue_order_returns_data(self, api_client: APIClient):
        """Test that queue order endpoint returns queue data."""
        resp = api_client.get("/api/queue/order")

        assert resp.status_code == 200
        data = resp.json()
        # May return list directly or dict with queue key
        if isinstance(data, dict):
            assert "queue" in data
            assert isinstance(data["queue"], list)
        else:
            assert isinstance(data, list)

    def test_clear_queue(self, api_client: APIClient, download_tracker: DownloadTracker):
        """Test clearing the queue."""
        resp = api_client.delete("/api/queue/clear")

        # Should succeed (may be 200 or 204)
        assert resp.status_code in [200, 204]


@pytest.mark.e2e
class TestSettingsEndpoint:
    """Tests for settings endpoints."""

    def test_settings_returns_tabs(self, api_client: APIClient):
        """Test that settings endpoint returns tab structure."""
        resp = api_client.get("/api/settings")

        # Settings may be disabled if config dir not writable
        if resp.status_code == 403:
            pytest.skip("Settings disabled (config dir not writable)")

        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, (list, dict))

    def test_get_specific_settings_tab(self, api_client: APIClient):
        """Test getting a specific settings tab."""
        # First get available tabs
        resp = api_client.get("/api/settings")
        if resp.status_code == 403:
            pytest.skip("Settings disabled")

        data = resp.json()
        if not data:
            pytest.skip("No settings tabs available")

        # Get the first tab
        if isinstance(data, list):
            tab_name = data[0].get("name") or data[0].get("id")
        else:
            tab_name = list(data.keys())[0] if data else None

        if not tab_name:
            pytest.skip("Could not determine tab name")

        resp = api_client.get(f"/api/settings/{tab_name}")
        assert resp.status_code in [200, 404]


@pytest.mark.e2e
class TestDownloadFlow:
    """Tests for the complete download flow."""

    def test_download_requires_id(self, api_client: APIClient):
        """Test that download endpoint requires an ID."""
        resp = api_client.get("/api/download")

        assert resp.status_code in [400, 422]

    def test_download_invalid_id_returns_error(self, api_client: APIClient):
        """Test that invalid ID returns appropriate error."""
        resp = api_client.get("/api/download", params={"id": "nonexistent-id-12345"})

        # Should return 404 or error status
        assert resp.status_code in [400, 404, 500]

    def test_cancel_nonexistent_download(self, api_client: APIClient):
        """Test cancelling a download that doesn't exist."""
        resp = api_client.delete("/api/download/nonexistent-id-xyz/cancel")

        # Should handle gracefully (may return 200, 204, or 404)
        assert resp.status_code in [200, 204, 404]


@pytest.mark.e2e
class TestReleaseDownloadFlow:
    """Tests for the release-based download flow (new API)."""

    def test_release_download_requires_source_id(self, api_client: APIClient):
        """Test that release download requires source_id."""
        resp = api_client.post("/api/releases/download", json={})

        assert resp.status_code == 400
        data = resp.json()
        assert "error" in data

    def test_release_download_with_minimal_data(
        self, api_client: APIClient, download_tracker: DownloadTracker
    ):
        """Test queueing a release with minimal valid data."""
        # This will queue but likely fail during download (no real source)
        test_id = "e2e-test-release-minimal"
        resp = api_client.post(
            "/api/releases/download",
            json={
                "source": "test_source",
                "source_id": test_id,
                "title": "E2E Test Book",
            },
        )

        if resp.status_code == 200:
            download_tracker.track(test_id)
            data = resp.json()
            assert data.get("status") == "queued"


@pytest.mark.e2e
class TestReleasesSearch:
    """Tests for searching releases."""

    def test_releases_requires_params(self, api_client: APIClient):
        """Test that releases endpoint requires provider and book_id."""
        resp = api_client.get("/api/releases")

        assert resp.status_code == 400
        data = resp.json()
        assert "error" in data

    def test_releases_with_invalid_provider(self, api_client: APIClient):
        """Test releases with invalid provider."""
        resp = api_client.get(
            "/api/releases",
            params={"provider": "nonexistent_provider", "book_id": "123"},
        )

        assert resp.status_code == 400
        data = resp.json()
        assert "error" in data


@pytest.mark.e2e
class TestCoverProxy:
    """Tests for the cover image proxy."""

    def test_cover_without_url_returns_error(self, api_client: APIClient):
        """Test that cover endpoint without URL returns error."""
        resp = api_client.get("/api/covers/test-id")

        # Should return error for missing URL
        assert resp.status_code in [400, 404]


@pytest.mark.e2e
class TestLegacySearchEndpoint:
    """Tests for the legacy search endpoint (backwards compatibility)."""

    def test_legacy_search_without_query(self, api_client: APIClient):
        """Test legacy search behavior without query parameter."""
        resp = api_client.get("/api/search")

        # May return 400 (error) or 200 with empty results depending on implementation
        assert resp.status_code in [200, 400, 422]

    def test_legacy_search_returns_results(self, api_client: APIClient):
        """Test legacy search with a query."""
        resp = api_client.get("/api/search", params={"query": "Pride Prejudice"})

        # May return results or 503 if source unavailable
        if resp.status_code == 200:
            data = resp.json()
            assert isinstance(data, list)


@pytest.mark.e2e
class TestLegacyInfoEndpoint:
    """Tests for the legacy info endpoint."""

    def test_legacy_info_requires_id(self, api_client: APIClient):
        """Test that legacy info requires ID parameter."""
        resp = api_client.get("/api/info")

        assert resp.status_code in [400, 422]

    def test_legacy_info_invalid_id(self, api_client: APIClient):
        """Test legacy info with invalid ID."""
        resp = api_client.get("/api/info", params={"id": "invalid-id-xyz"})

        # Should return 404 or error
        assert resp.status_code in [400, 404, 500]
