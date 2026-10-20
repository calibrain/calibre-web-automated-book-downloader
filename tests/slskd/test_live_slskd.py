"""Live checks against a real slskd instance.

Skipped unless ``SLSKD_TEST_URL`` and ``SLSKD_TEST_API_KEY`` are set, e.g. after
``docker compose -f docker-compose.test-clients.yml up -d slskd``.
"""

import os

import pytest

from shelfmark.release_sources.slskd.api import SlskdAuthError, SlskdClient

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def live_client() -> SlskdClient:
    url = os.environ.get("SLSKD_TEST_URL", "").strip()
    api_key = os.environ.get("SLSKD_TEST_API_KEY", "").strip()
    if not url or not api_key:
        pytest.skip("SLSKD_TEST_URL / SLSKD_TEST_API_KEY not set")
    client = SlskdClient(url, api_key, timeout=15)
    try:
        client.get_application()
    except Exception as e:  # pragma: no cover - environment dependent
        pytest.skip(f"slskd not reachable: {e}")
    return client


def test_connection_reports_login_state(live_client):
    ok, message = live_client.test_connection()
    assert "slskd" in message
    if ok:
        assert "logged in" in message.lower()
    else:
        assert "not logged in" in message


def test_bad_api_key_is_rejected(live_client):
    bad = SlskdClient(live_client.base_url, "definitely-not-the-key", timeout=15)
    with pytest.raises(SlskdAuthError):
        bad.get_application()


def test_download_directory_is_reported(live_client):
    directory = live_client.get_download_directory()
    assert directory
    assert directory.startswith("/")


def test_search_lifecycle(live_client):
    search_id = live_client.start_search(
        "zzzz-shelfmark-nonexistent-qqqq", timeout_ms=1000, response_limit=5
    )
    state = live_client.get_search(search_id)
    assert state["id"] == search_id
    assert isinstance(live_client.get_search_responses(search_id), list)
    live_client.delete_search(search_id)


def test_missing_transfer_is_none(live_client):
    assert (
        live_client.get_transfer("nobody-shelfmark-test", "00000000-0000-0000-0000-000000000000")
        is None
    )
    assert live_client.list_downloads("nobody-shelfmark-test") == []


def test_network_search_returns_ebooks_when_logged_in(live_client):
    if not live_client.is_logged_in():
        pytest.skip("slskd is not logged in to Soulseek")
    from shelfmark.release_sources.slskd.source import build_releases

    responses = live_client.search("sherlock holmes epub", wait_seconds=10, response_limit=20)
    releases = build_releases(responses, content_type="ebook", formats={"epub"})
    # The network is live, so only assert on shape when something answered.
    for release in releases[:5]:
        assert release.source == "slskd"
        assert release.format == "epub"
        assert release.extra["files"][0]["filename"].lower().endswith(".epub")
