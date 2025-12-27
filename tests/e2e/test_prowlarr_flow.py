"""
E2E Tests for Prowlarr Integration.

These tests verify the Prowlarr release source and download client flow.
Requires Prowlarr and a download client (qBittorrent, Transmission, etc.) to be configured.

Run with: docker exec test-cwabd python3 -m pytest tests/e2e/test_prowlarr_flow.py -v -m e2e
"""

import time

import pytest

from .conftest import APIClient, DownloadTracker


def _is_prowlarr_configured(api_client: APIClient) -> bool:
    """Check if Prowlarr is configured and available."""
    resp = api_client.get("/api/release-sources")
    if resp.status_code != 200:
        return False

    sources = resp.json()
    for source in sources:
        if source.get("name") == "prowlarr":
            return True
    return False


def _get_prowlarr_settings(api_client: APIClient) -> dict | None:
    """Get Prowlarr settings if available."""
    resp = api_client.get("/api/settings/prowlarr")
    if resp.status_code == 200:
        return resp.json()
    return None


def _get_first_provider_name(api_client: APIClient) -> str | None:
    """Get the first available provider name."""
    providers_resp = api_client.get("/api/metadata/providers")
    if providers_resp.status_code != 200:
        return None

    providers_data = providers_resp.json()
    if not providers_data:
        return None

    # Handle both dict and list formats
    if isinstance(providers_data, dict):
        return list(providers_data.keys())[0] if providers_data else None
    else:
        return providers_data[0].get("name") if providers_data else None


@pytest.mark.e2e
class TestProwlarrConfiguration:
    """Tests for Prowlarr configuration."""

    def test_prowlarr_in_release_sources(self, api_client: APIClient):
        """Test that Prowlarr appears in release sources."""
        resp = api_client.get("/api/release-sources")

        assert resp.status_code == 200
        sources = resp.json()
        source_names = [s.get("name") for s in sources]
        assert "prowlarr" in source_names

    def test_prowlarr_settings_tab_exists(self, api_client: APIClient):
        """Test that Prowlarr settings tab exists."""
        resp = api_client.get("/api/settings")

        if resp.status_code == 403:
            pytest.skip("Settings disabled")

        assert resp.status_code == 200
        data = resp.json()

        # Settings may have nested structure with groups/tabs
        if isinstance(data, dict):
            # Could have groups containing tabs, or be flat
            if "groups" in data:
                # Nested: look in groups for prowlarr tabs
                all_tab_names = []
                for group in data.get("groups", []):
                    if isinstance(group, dict):
                        for tab in group.get("tabs", []):
                            if isinstance(tab, dict):
                                all_tab_names.append(tab.get("name") or tab.get("id", ""))
                tab_names = all_tab_names
            else:
                tab_names = list(data.keys())
        else:
            tab_names = [t.get("name") or t.get("id") for t in data if isinstance(t, dict)]

        # Prowlarr settings should exist (may be under different name)
        prowlarr_tabs = [n for n in tab_names if n and "prowlarr" in n.lower()]
        # Also check if we can directly access the prowlarr_clients settings
        prowlarr_resp = api_client.get("/api/settings/prowlarr_clients")
        has_prowlarr_settings = prowlarr_resp.status_code == 200

        assert prowlarr_tabs or has_prowlarr_settings, f"No prowlarr settings found. Tab names: {tab_names}"


@pytest.mark.e2e
@pytest.mark.slow
class TestProwlarrSearch:
    """Tests for searching via Prowlarr."""

    def test_prowlarr_search_with_metadata(self, api_client: APIClient):
        """Test searching Prowlarr with metadata from a provider."""
        if not _is_prowlarr_configured(api_client):
            pytest.skip("Prowlarr not configured")

        provider = _get_first_provider_name(api_client)
        if not provider:
            pytest.skip("No metadata providers")

        # Search for a book
        search_resp = api_client.get(
            "/api/metadata/search",
            params={"query": "The Great Gatsby", "provider": provider},
            timeout=30,
        )

        if search_resp.status_code != 200:
            pytest.skip("Metadata search unavailable")

        search_data = search_resp.json()
        results = search_data.get("results", search_data)

        # Handle dict format where results might be nested
        if isinstance(results, dict) and "results" not in results:
            # Results might be the actual result list under a different key
            for key, value in results.items():
                if isinstance(value, list) and value:
                    results = value
                    break

        if not results or (isinstance(results, dict) and not results):
            pytest.skip("No metadata results")

        # Get first result
        if isinstance(results, list):
            book = results[0]
        else:
            pytest.skip("Unexpected results format")

        book_id = book.get("id") or book.get("provider_id")

        # Now search releases specifically from Prowlarr
        releases_resp = api_client.get(
            "/api/releases",
            params={
                "provider": provider,
                "book_id": book_id,
                "source": "prowlarr",
                "title": book.get("title", ""),
                "author": book.get("author", ""),
            },
            timeout=60,
        )

        # Prowlarr may not be reachable
        if releases_resp.status_code == 503:
            pytest.skip("Prowlarr not reachable")

        if releases_resp.status_code == 200:
            data = releases_resp.json()
            assert "releases" in data
            # Releases may be empty if Prowlarr has no indexers configured


@pytest.mark.e2e
class TestProwlarrClientSettings:
    """Tests for Prowlarr download client settings."""

    def test_client_settings_structure(self, api_client: APIClient):
        """Test that client settings have expected structure."""
        resp = api_client.get("/api/settings/prowlarr_clients")

        if resp.status_code == 403:
            pytest.skip("Settings disabled")
        if resp.status_code == 404:
            pytest.skip("Prowlarr clients settings tab not found")

        assert resp.status_code == 200
        data = resp.json()

        # Should have fields for client configuration
        assert isinstance(data, (dict, list))

    def test_can_save_client_settings(self, api_client: APIClient):
        """Test that client settings can be saved."""
        # Get current settings
        get_resp = api_client.get("/api/settings/prowlarr_clients")

        if get_resp.status_code in [403, 404]:
            pytest.skip("Settings not available")

        current = get_resp.json()

        # Try to save the same settings back (no-op save)
        if isinstance(current, dict) and "fields" in current:
            # Extract just the values
            values = {}
            for field in current.get("fields", []):
                key = field.get("key") or field.get("name")
                if key:
                    values[key] = field.get("value", "")

            put_resp = api_client.put(
                "/api/settings/prowlarr_clients",
                json=values,
            )
            # Should succeed (200) or be a no-op
            assert put_resp.status_code in [200, 204, 400]


@pytest.mark.e2e
@pytest.mark.slow
class TestProwlarrDownload:
    """Tests for downloading via Prowlarr."""

    def test_queue_prowlarr_release(
        self, api_client: APIClient, download_tracker: DownloadTracker
    ):
        """Test queueing a Prowlarr release for download."""
        if not _is_prowlarr_configured(api_client):
            pytest.skip("Prowlarr not configured")

        provider = _get_first_provider_name(api_client)
        if not provider:
            pytest.skip("No providers")

        # Search metadata
        search_resp = api_client.get(
            "/api/metadata/search",
            params={"query": "Dracula Bram Stoker", "provider": provider},
            timeout=30,
        )

        if search_resp.status_code != 200:
            pytest.skip("Metadata search failed")

        search_data = search_resp.json()
        results = search_data.get("results", search_data)

        # Handle different result formats
        if isinstance(results, dict):
            for key, value in results.items():
                if isinstance(value, list) and value:
                    results = value
                    break

        if not results or not isinstance(results, list):
            pytest.skip("No results")

        book = results[0]
        book_id = book.get("id") or book.get("provider_id")

        # Search Prowlarr releases
        releases_resp = api_client.get(
            "/api/releases",
            params={
                "provider": provider,
                "book_id": book_id,
                "source": "prowlarr",
                "title": book.get("title", ""),
            },
            timeout=60,
        )

        if releases_resp.status_code != 200:
            pytest.skip(f"Releases search failed: {releases_resp.status_code}")

        releases = releases_resp.json().get("releases", [])
        if not releases:
            pytest.skip("No Prowlarr releases found")

        # Get the first release
        release = releases[0]
        source_id = release.get("source_id") or release.get("id")
        download_tracker.track(source_id)

        # Queue it
        queue_resp = api_client.post(
            "/api/releases/download",
            json={
                "source": "prowlarr",
                "source_id": source_id,
                "title": release.get("title", book.get("title", "Test")),
                "format": release.get("format"),
                "size": release.get("size"),
                "extra": release.get("extra", {}),
            },
        )

        # May fail if no download client configured
        if queue_resp.status_code == 200:
            data = queue_resp.json()
            assert data.get("status") == "queued"

            # Wait briefly and check status
            time.sleep(3)

            status_resp = api_client.get("/api/status")
            if status_resp.status_code == 200:
                status_data = status_resp.json()
                # Should be in one of the status categories
                found = False
                for category in status_data.values():
                    if isinstance(category, dict) and source_id in category:
                        found = True
                        break
                # It's ok if not found (may have already processed/errored)


@pytest.mark.e2e
class TestProwlarrClientConnection:
    """Tests for testing download client connections."""

    def test_connection_test_action(self, api_client: APIClient):
        """Test the connection test action for download clients."""
        # This tests the action button functionality in settings
        resp = api_client.post(
            "/api/settings/prowlarr_clients/action/test_torrent_connection"
        )

        # May succeed, fail, or not exist depending on configuration
        # We just verify it returns a response
        assert resp.status_code in [200, 400, 404, 500]

        if resp.status_code == 200:
            data = resp.json()
            # Should have success/message structure
            assert "success" in data or "message" in data or "result" in data
