# Test Suite

This directory contains the test suite for CWA Book Downloader. Tests are organized by scope and component.

## Quick Start

```bash
# Run all unit tests (fast, no external dependencies)
docker exec test-cwabd python3 -m pytest tests/ -v -m "not integration and not e2e"

# Run E2E API tests
docker exec test-cwabd python3 -m pytest tests/e2e/ -v -m e2e

# Run everything except integration tests
docker exec test-cwabd python3 -m pytest tests/ -v -m "not integration"
```

## Test Structure

```
tests/
├── e2e/                    # End-to-end API tests
│   ├── conftest.py         # Fixtures (APIClient, DownloadTracker)
│   ├── test_api.py         # Core API endpoint tests
│   ├── test_download_flow.py   # Full download journey tests
│   └── test_prowlarr_flow.py   # Prowlarr-specific tests
│
├── prowlarr/               # Prowlarr plugin tests
│   ├── conftest.py         # Shared fixtures
│   ├── test_clients.py     # DownloadClient base, registry, DownloadStatus
│   ├── test_qbittorrent_client.py  # qBittorrent client unit tests
│   ├── test_transmission_client.py # Transmission client unit tests
│   ├── test_nzbget_client.py       # NZBGet client unit tests
│   ├── test_sabnzbd_client.py      # SABnzbd client unit tests
│   ├── test_handler.py     # ProwlarrHandler unit tests
│   ├── test_torrent_utils.py   # Bencode, hash extraction, URL parsing
│   ├── test_bencode.py     # Bencode encoding/decoding
│   ├── test_source.py      # Release source (size parsing, format detection)
│   ├── test_cache.py       # Release cache
│   ├── test_integration_clients.py  # Integration tests (require Docker stack)
│   └── test_integration_handler.py  # Handler integration tests
│
└── README.md               # This file
```

## Test Types

### Unit Tests
Fast tests that mock external dependencies. Run these frequently during development.

```bash
docker exec test-cwabd python3 -m pytest tests/prowlarr/ -v -m "not integration"
```

**What they test:**
- Download client logic (status mapping, URL handling, error cases)
- Bencode encoding/decoding for torrent files
- Hash extraction from magnet links and .torrent files
- Protocol detection (torrent vs usenet)
- Release cache operations
- Handler download flow logic

### E2E Tests
Test the full application through its HTTP API. Require the app to be running.

```bash
docker exec test-cwabd python3 -m pytest tests/e2e/ -v -m e2e
```

**What they test:**
- Health check endpoint
- Configuration endpoint
- Metadata provider search (Hardcover, etc.)
- Release source listing
- Download queue operations (add, cancel, reorder, clear)
- Settings API
- Prowlarr integration

### Integration Tests
Test against real services (qBittorrent, Transmission, etc.). Require the full Docker test stack.

```bash
# Start the test stack first
docker compose -f docker-compose.test-clients.yml up -d

# Run integration tests
docker exec test-cwabd python3 -m pytest tests/prowlarr/ -v -m integration
```

**What they test:**
- Real connections to download clients
- Adding/removing actual torrents
- Status polling from real clients

## Test Markers

| Marker | Description | When to Skip |
|--------|-------------|--------------|
| `integration` | Requires running services (qBittorrent, etc.) | Default skip with `-m "not integration"` |
| `e2e` | End-to-end API tests | When app isn't running |
| `slow` | Tests that take longer (network calls, polling) | Quick feedback with `-m "not slow"` |

## Common Commands

```bash
# Run specific test file
docker exec test-cwabd python3 -m pytest tests/prowlarr/test_clients.py -v

# Run specific test class
docker exec test-cwabd python3 -m pytest tests/e2e/test_api.py::TestHealthEndpoint -v

# Run specific test
docker exec test-cwabd python3 -m pytest tests/e2e/test_api.py::TestHealthEndpoint::test_health_returns_ok -v

# Run with short traceback (cleaner output)
docker exec test-cwabd python3 -m pytest tests/ -v --tb=short -m "not integration"

# Run and stop on first failure
docker exec test-cwabd python3 -m pytest tests/ -v -x -m "not integration"

# Run with coverage (if pytest-cov installed)
docker exec test-cwabd python3 -m pytest tests/ --cov=cwa_book_downloader -m "not integration"
```

## Writing New Tests

### Unit Test Example

```python
from unittest.mock import MagicMock, patch

class TestMyFeature:
    def test_something(self, monkeypatch):
        # Mock config values
        monkeypatch.setattr(
            "cwa_book_downloader.module.config.get",
            lambda key, default="": {"KEY": "value"}.get(key, default),
        )

        # Test your code
        result = my_function()
        assert result == expected
```

### E2E Test Example

```python
import pytest
from .conftest import APIClient, DownloadTracker

@pytest.mark.e2e
class TestMyEndpoint:
    def test_endpoint_works(self, api_client: APIClient):
        resp = api_client.get("/api/my-endpoint")
        assert resp.status_code == 200

    def test_with_cleanup(self, api_client: APIClient, download_tracker: DownloadTracker):
        # Track IDs for automatic cleanup after test
        download_tracker.track("some-id")
        # ... test code ...
```

## Test Fixtures

### E2E Fixtures (`tests/e2e/conftest.py`)

| Fixture | Scope | Description |
|---------|-------|-------------|
| `api_client` | session | HTTP client for API calls |
| `download_tracker` | function | Tracks downloads for cleanup |
| `server_config` | session | Cached server configuration |

### Prowlarr Fixtures (`tests/prowlarr/conftest.py`)

| Fixture | Scope | Description |
|---------|-------|-------------|
| `transmission_client` | module | Real Transmission client (integration) |
| `qbittorrent_client` | module | Real qBittorrent client (integration) |
| `deluge_client` | module | Real Deluge client (integration) |
| `nzbget_client` | module | Real NZBGet client (integration) |
| `sabnzbd_client` | module | Real SABnzbd client (integration) |

## Expected Skips

Some tests skip when external services aren't available. This is normal:

- **"No metadata providers available"** - Metadata provider not responding
- **"Prowlarr not configured"** - Prowlarr settings not set up
- **"No releases found"** - No indexers configured in Prowlarr
- **"Legacy search source unavailable"** - Direct download source offline
- **"Transmission/qBittorrent not available"** - Docker test stack not running

## Troubleshooting

### Tests can't connect to app
```bash
# Check the app is running
docker ps | grep test-cwabd

# Check app logs
docker logs test-cwabd
```

### Import errors
```bash
# Make sure you're running inside the container
docker exec test-cwabd python3 -m pytest ...

# Not from your local machine
pytest ...  # This won't work
```

### Integration tests failing
```bash
# Make sure test stack is running
docker compose -f docker-compose.test-clients.yml up -d

# Check client containers
docker ps | grep -E "qbittorrent|transmission|deluge|nzbget|sabnzbd"
```

### Stale test data
```bash
# Clear the queue between test runs
docker exec test-cwabd python3 -c "
from cwa_book_downloader.core.queue import book_queue
book_queue.clear()
"
```
