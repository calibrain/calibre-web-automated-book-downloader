"""
Pytest configuration and shared fixtures.
"""

import os
import sys
import tempfile

# Set environment variables BEFORE importing the application
# These override the defaults that try to use system paths like /var/log
_temp_base = tempfile.mkdtemp(prefix="cwabd_test_")

# LOG_ROOT is the base - LOG_DIR is computed as LOG_ROOT / "cwa-book-downloader"
# So we set LOG_ROOT to our temp directory to get LOG_DIR = _temp_base/cwa-book-downloader
os.environ["LOG_ROOT"] = _temp_base
os.environ["CONFIG_DIR"] = os.path.join(_temp_base, "config")
os.environ["INGEST_DIR"] = os.path.join(_temp_base, "ingest")
os.environ["TMP_DIR"] = os.path.join(_temp_base, "tmp")

# Create the directories that will be used
os.makedirs(os.path.join(_temp_base, "cwa-book-downloader"), exist_ok=True)  # LOG_DIR
os.makedirs(os.path.join(_temp_base, "config"), exist_ok=True)
os.makedirs(os.path.join(_temp_base, "ingest"), exist_ok=True)
os.makedirs(os.path.join(_temp_base, "tmp"), exist_ok=True)

# Add the project root to Python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


@pytest.fixture
def sample_prowlarr_result():
    """Sample Prowlarr API search result."""
    return {
        "guid": "abc123-guid",
        "title": "The Great Book by Author Name.epub",
        "indexer": "MyIndexer",
        "protocol": "torrent",
        "size": 5242880,  # 5 MB
        "downloadUrl": "magnet:?xt=urn:btih:abc123",
        "infoUrl": "https://example.com/book/123",
        "seeders": 10,
        "leechers": 2,
        "publishDate": "2024-01-15T12:00:00Z",
        "categories": [{"id": 7020, "name": "Books/EBook"}],
        "indexerId": 1,
    }


@pytest.fixture
def sample_nzb_result():
    """Sample Prowlarr API NZB result."""
    return {
        "guid": "nzb456-guid",
        "title": "Another Book [PDF] by Writer",
        "indexer": "NZBIndexer",
        "protocol": "usenet",
        "size": 10485760,  # 10 MB
        "downloadUrl": "https://example.com/download.nzb",
        "infoUrl": "https://example.com/nzb/456",
        "grabs": 50,
        "publishDate": "2024-02-20T10:30:00Z",
        "categories": [{"id": 7020, "name": "Books/EBook"}],
        "indexerId": 2,
    }


@pytest.fixture
def mock_config(monkeypatch):
    """Fixture to mock config values."""
    config_values = {}

    def mock_get(key, default=""):
        return config_values.get(key, default)

    def set_config(key, value):
        config_values[key] = value

    # Create a mock config module
    class MockConfig:
        get = staticmethod(mock_get)
        set = staticmethod(set_config)
        _values = config_values

    return MockConfig
