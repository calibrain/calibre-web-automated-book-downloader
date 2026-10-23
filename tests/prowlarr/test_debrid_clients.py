"""Debrid clients must resolve a real torrent before uploading (#1250).

Prowlarr hands out a proxy download URL - no magnetUrl, no infoHash - for any
indexer that only publishes torrent files. Posting that URL as if it were a
magnet is what made Real-Debrid answer /torrents/addMagnet with a bare 404.
"""

import hashlib
from unittest.mock import MagicMock, patch

import pytest

from shelfmark.download.clients import DownloadState
from shelfmark.download.clients.alldebrid import AllDebridClient
from shelfmark.download.clients.realdebrid import RealDebridClient, _DownloadState
from shelfmark.download.clients.torrent_utils import (
    DebridMagnet,
    DebridTorrentFile,
    bencode_encode,
    resolve_debrid_upload,
)

_PROWLARR_PROXY_URL = "https://prowlarr.example/api/v1/indexer/1/download?apikey=k&link=abc"
_MAGNET = "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567&dn=Dune"


def _valid_torrent() -> tuple[bytes, str]:
    info_dict = {
        b"name": b"book.epub",
        b"length": 100,
        b"piece length": 16384,
        b"pieces": b"\x00" * 20,
    }
    return (
        bencode_encode({b"info": info_dict}),
        hashlib.sha1(bencode_encode(info_dict)).hexdigest().lower(),
    )


def _mock_fetch(monkeypatch, *, content=b"", status_code=200, error=None):
    """Stand in for the .torrent prefetch inside extract_torrent_info."""
    if error is not None:
        mock_get = MagicMock(side_effect=error)
    else:
        response = MagicMock(status_code=status_code, content=content)
        response.raise_for_status = MagicMock()
        mock_get = MagicMock(return_value=response)
    monkeypatch.setattr("shelfmark.download.clients.torrent_utils.requests.get", mock_get)
    return mock_get


class TestResolveDebridUpload:
    def test_magnet_url_passes_through_untouched(self):
        assert resolve_debrid_upload(_MAGNET) == DebridMagnet(magnet_url=_MAGNET)

    def test_proxy_url_becomes_the_torrent_file(self, monkeypatch):
        torrent_data, _ = _valid_torrent()
        _mock_fetch(monkeypatch, content=torrent_data)

        assert resolve_debrid_upload(_PROWLARR_PROXY_URL) == DebridTorrentFile(
            torrent_data=torrent_data
        )

    def test_proxy_url_returning_a_magnet_body_becomes_that_magnet(self, monkeypatch):
        _mock_fetch(monkeypatch, content=_MAGNET.encode())

        assert resolve_debrid_upload(_PROWLARR_PROXY_URL) == DebridMagnet(magnet_url=_MAGNET)

    def test_falls_back_to_a_magnet_built_from_the_prowlarr_info_hash(self, monkeypatch):
        _mock_fetch(monkeypatch, error=OSError("tracker unreachable"))
        info_hash = "0123456789abcdef0123456789abcdef01234567"

        upload = resolve_debrid_upload(_PROWLARR_PROXY_URL, expected_hash=info_hash)

        assert upload == DebridMagnet(magnet_url=f"magnet:?xt=urn:btih:{info_hash}")

    def test_prefers_the_torrent_file_over_the_info_hash(self, monkeypatch):
        """The file carries the tracker list; a bare btih magnet does not."""
        torrent_data, info_hash = _valid_torrent()
        _mock_fetch(monkeypatch, content=torrent_data)

        upload = resolve_debrid_upload(_PROWLARR_PROXY_URL, expected_hash=info_hash)

        assert upload == DebridTorrentFile(torrent_data=torrent_data)

    def test_unresolvable_url_raises_rather_than_handing_back_the_url(self, monkeypatch):
        _mock_fetch(monkeypatch, error=OSError("tracker unreachable"))

        with pytest.raises(ValueError, match="Could not resolve a torrent to send") as excinfo:
            resolve_debrid_upload(_PROWLARR_PROXY_URL)

        assert "tracker unreachable" in str(excinfo.value)

    def test_resolver_does_not_log_the_raw_url_or_exception_text(self, monkeypatch):
        import shelfmark.download.clients.torrent_utils as torrent_utils

        logger = MagicMock()
        monkeypatch.setattr(torrent_utils, "logger", logger)
        _mock_fetch(monkeypatch, error=OSError(f"boom {_PROWLARR_PROXY_URL}"))

        with pytest.raises(ValueError):
            resolve_debrid_upload(_PROWLARR_PROXY_URL)

        logged = str(logger.mock_calls)
        assert _PROWLARR_PROXY_URL not in logged
        assert torrent_utils._safe_url(_PROWLARR_PROXY_URL) in logged

    def test_resolver_does_not_log_a_credential_bearing_redirect(self, monkeypatch):
        import shelfmark.download.clients.torrent_utils as torrent_utils

        logger = MagicMock()
        monkeypatch.setattr(torrent_utils, "logger", logger)
        redirect_url = "https://tracker.example/get/Dune?t=secret&apikey=k"
        response = MagicMock(
            status_code=302,
            headers={"Location": redirect_url},
            content=b"",
        )
        response.raise_for_status = MagicMock()
        mock_get = MagicMock(return_value=response)
        monkeypatch.setattr("shelfmark.download.clients.torrent_utils.requests.get", mock_get)

        resolve_debrid_upload(_PROWLARR_PROXY_URL, expected_hash="a" * 40)

        logged = str(logger.mock_calls)
        assert redirect_url not in logged
        assert torrent_utils._safe_url(redirect_url) in logged

    def test_resolver_failure_message_keeps_out_the_source_url(self, monkeypatch):
        _mock_fetch(monkeypatch, error=OSError("tracker unreachable"))

        with pytest.raises(ValueError, match="Could not resolve a torrent") as excinfo:
            resolve_debrid_upload(_PROWLARR_PROXY_URL)

        assert "apikey=k" not in str(excinfo.value)


class TestRealDebridAdd:
    @staticmethod
    def _client(monkeypatch):
        monkeypatch.setattr(
            "shelfmark.download.clients.realdebrid.config.get",
            lambda key, default="": {"REALDEBRID_API_KEY": "rd-key"}.get(key, default),
        )
        return RealDebridClient()

    @staticmethod
    def _mock_api(monkeypatch):
        ok = MagicMock(status_code=201)
        ok.raise_for_status = MagicMock()
        ok.json = MagicMock(return_value={"id": "RD1"})
        post = MagicMock(return_value=ok)
        put = MagicMock(return_value=ok)
        monkeypatch.setattr("shelfmark.download.clients.realdebrid._session.post", post)
        monkeypatch.setattr("shelfmark.download.clients.realdebrid._session.put", put)
        return post, put

    def test_proxy_url_is_uploaded_as_a_torrent_file_not_posted_as_a_magnet(self, monkeypatch):
        torrent_data, _ = _valid_torrent()
        _mock_fetch(monkeypatch, content=torrent_data)
        post, put = self._mock_api(monkeypatch)
        client = self._client(monkeypatch)

        assert client.add_download(_PROWLARR_PROXY_URL, "Dune") == "RD1"

        put.assert_called_once()
        assert put.call_args.args[0].endswith("/torrents/addTorrent")
        assert put.call_args.kwargs["data"] == torrent_data
        # The only POST is selectFiles; addMagnet must never see the proxy URL.
        assert [c.args[0] for c in post.call_args_list] == [
            "https://api.real-debrid.com/rest/1.0/torrents/selectFiles/RD1"
        ]

    def test_magnet_still_goes_to_add_magnet(self, monkeypatch):
        post, put = self._mock_api(monkeypatch)
        client = self._client(monkeypatch)

        assert client.add_download(_MAGNET, "Dune") == "RD1"

        put.assert_not_called()
        assert post.call_args_list[0].args[0].endswith("/torrents/addMagnet")
        assert post.call_args_list[0].kwargs["data"] == {"magnet": _MAGNET}

    def test_unresolvable_url_reports_the_reason_instead_of_a_service_error(self, monkeypatch):
        _mock_fetch(monkeypatch, error=OSError("tracker unreachable"))
        post, put = self._mock_api(monkeypatch)
        client = self._client(monkeypatch)

        with pytest.raises(ValueError, match="Could not resolve a torrent to send"):
            client.add_download(_PROWLARR_PROXY_URL, "Dune")

        post.assert_not_called()
        put.assert_not_called()


class TestRealDebridStatus:
    @staticmethod
    def _client():
        return RealDebridClient.__new__(RealDebridClient)

    @staticmethod
    def _state(tmp_path):
        return _DownloadState(
            torrent_id="RD1",
            name="Dune",
            target_dir=tmp_path,
            phase="waiting_rd",
        )

    def test_queued_remains_downloading_and_allows_later_status(self, tmp_path):
        client = self._client()
        state = self._state(tmp_path)

        queued = client._handle_torrent_info(
            {"status": "queued", "progress": 0, "filename": "Dune.epub"},
            state,
        )

        assert queued.state == DownloadState.DOWNLOADING
        assert queued.progress == 0
        assert queued.complete is False
        assert state.phase == "waiting_rd"
        assert state.error_message is None

        downloading = client._handle_torrent_info(
            {"status": "downloading", "progress": 10, "filename": "Dune.epub"},
            state,
        )

        assert downloading.state == DownloadState.DOWNLOADING
        assert state.phase == "waiting_rd"

    def test_dead_is_terminal_and_caches_error(self, tmp_path):
        client = self._client()
        state = self._state(tmp_path)

        status = client._handle_torrent_info({"status": "dead"}, state)

        assert status.state == DownloadState.ERROR
        assert state.phase == "error"
        assert state.error_message == "Real-Debrid status error: dead"


class TestAllDebridAdd:
    @staticmethod
    def _client(monkeypatch):
        monkeypatch.setattr(
            "shelfmark.download.clients.alldebrid.config.get",
            lambda key, default="": {"ALLDEBRID_API_KEY": "ad-key"}.get(key, default),
        )
        return AllDebridClient()

    @staticmethod
    def _mock_api(monkeypatch, entries_key="files"):
        ok = MagicMock(status_code=200)
        ok.raise_for_status = MagicMock()
        ok.json = MagicMock(
            return_value={"status": "success", "data": {entries_key: [{"id": 4242}]}}
        )
        post = MagicMock(return_value=ok)
        monkeypatch.setattr("shelfmark.download.clients.alldebrid._session.post", post)
        return post

    def test_proxy_url_is_uploaded_to_the_file_endpoint(self, monkeypatch):
        torrent_data, _ = _valid_torrent()
        _mock_fetch(monkeypatch, content=torrent_data)
        post = self._mock_api(monkeypatch)
        client = self._client(monkeypatch)

        assert client.add_download(_PROWLARR_PROXY_URL, "Dune") == "4242"

        assert post.call_args.args[0].endswith("/magnet/upload/file")
        assert post.call_args.kwargs["files"]["files[]"][1] == torrent_data

    def test_magnet_still_goes_to_the_magnet_endpoint(self, monkeypatch):
        post = self._mock_api(monkeypatch, entries_key="magnets")
        client = self._client(monkeypatch)

        assert client.add_download(_MAGNET, "Dune") == "4242"

        assert post.call_args.args[0].endswith("/magnet/upload")
        assert post.call_args.kwargs["data"] == {"magnets[]": _MAGNET}


class TestAllDebridLinkLogging:
    @staticmethod
    def _client(monkeypatch):
        monkeypatch.setattr(
            "shelfmark.download.clients.alldebrid.config.get",
            lambda key, default="": {"ALLDEBRID_API_KEY": "ad-secret"}.get(key, default),
        )
        failed = MagicMock(status_code=500)
        monkeypatch.setattr(
            "shelfmark.download.clients.alldebrid._session.post", MagicMock(return_value=failed)
        )
        monkeypatch.setattr(
            "shelfmark.download.clients.alldebrid._session.get", MagicMock(return_value=failed)
        )
        return AllDebridClient()

    @staticmethod
    def _logged(log):
        lines = []
        for call in log.mock_calls:
            if call.args:
                fmt, *args = call.args
                lines.append(fmt % tuple(args))
        return "\n".join(lines)

    def test_apikey_fallback_logs_no_query_and_still_returns_the_key(self, monkeypatch):
        client = self._client(monkeypatch)

        with patch("shelfmark.download.clients.alldebrid.logger") as log:
            result = client._unlock_file_link("https://alldebrid.com/f/abc?token=link-secret")

        assert result.endswith("&apikey=ad-secret")
        logged = self._logged(log)
        assert "https://alldebrid.com/f/abc" in logged
        assert "link-secret" not in logged
        assert "ad-secret" not in logged

    def test_unlock_failure_logs_no_query(self, monkeypatch):
        client = self._client(monkeypatch)

        with patch("shelfmark.download.clients.alldebrid.logger") as log:
            with pytest.raises(RuntimeError):
                client._unlock_file_link("https://example.com/x?token=link-secret")

        logged = self._logged(log)
        assert "https://example.com/x" in logged
        assert "link-secret" not in logged
