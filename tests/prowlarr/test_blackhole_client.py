"""Unit tests for the Blackhole torrent-file handoff client."""

from __future__ import annotations

import pytest

from shelfmark.download.clients.torrent_utils import TorrentInfo


def make_config_getter(values: dict[str, str]):
    def getter(key: str, default: str = "") -> str:
        return values.get(key, default)

    return getter


def test_blackhole_saves_torrent_file_and_reports_completed_handoff(monkeypatch, tmp_path):
    from shelfmark.download.clients.blackhole import BlackholeClient

    monkeypatch.setattr(
        "shelfmark.download.clients.blackhole.config.get",
        make_config_getter(
            {
                "PROWLARR_TORRENT_CLIENT": "blackhole",
                "BLACKHOLE_DIRECTORY": str(tmp_path),
            }
        ),
    )
    monkeypatch.setattr(
        "shelfmark.download.clients.blackhole.extract_torrent_info",
        lambda *_args, **_kwargs: TorrentInfo(
            info_hash="abc123",
            torrent_data=b"torrent-bytes",
            is_magnet=False,
        ),
    )

    client = BlackholeClient()
    download_id = client.add_download(
        "https://indexer.example/release.torrent",
        "A Test Book",
    )

    saved_file = tmp_path / "A Test Book.torrent"
    assert download_id == str(saved_file)
    assert saved_file.read_bytes() == b"torrent-bytes"
    assert client.get_status(download_id).complete is True
    assert client.get_download_path(download_id) == str(saved_file)


def test_blackhole_rejects_magnet_only_release(monkeypatch, tmp_path):
    from shelfmark.download.clients.blackhole import BlackholeClient

    monkeypatch.setattr(
        "shelfmark.download.clients.blackhole.config.get",
        make_config_getter({"BLACKHOLE_DIRECTORY": str(tmp_path)}),
    )
    monkeypatch.setattr(
        "shelfmark.download.clients.blackhole.extract_torrent_info",
        lambda *_args, **_kwargs: TorrentInfo(
            info_hash="abc123",
            torrent_data=None,
            is_magnet=True,
            magnet_url="magnet:?xt=urn:btih:abc123",
        ),
    )

    client = BlackholeClient()

    with pytest.raises(ValueError, match="requires a .torrent file"):
        client.add_download("magnet:?xt=urn:btih:abc123", "A Test Book")

    assert list(tmp_path.iterdir()) == []
