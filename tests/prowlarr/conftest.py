import pytest


@pytest.fixture(autouse=True)
def _public_dns_for_torrent_fetch(monkeypatch):
    """Resolve every torrent-fetch hostname to a public address unless a test overrides it."""
    monkeypatch.setattr(
        "shelfmark.download.clients.torrent_utils._resolve_host_addresses",
        lambda host: ["93.184.216.34"],
    )
