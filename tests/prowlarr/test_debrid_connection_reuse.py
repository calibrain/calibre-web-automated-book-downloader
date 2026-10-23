"""Debrid status polls reuse one HTTP session instead of opening a connection each time.

The download handler polls `get_status` every couple of seconds for each active job.
A bare `requests.get` builds a throwaway Session per call, so every poll paid for a
fresh TCP and TLS handshake with the debrid API.
"""

import pytest
import requests

from shelfmark.download.clients.alldebrid import AllDebridClient
from shelfmark.download.clients.debridlink import DebridLinkClient
from shelfmark.download.clients.realdebrid import RealDebridClient
from shelfmark.download.clients.torbox import TorBoxClient

_CLIENTS = [
    pytest.param(RealDebridClient, "realdebrid", "REALDEBRID_API_KEY", "ABC", id="realdebrid"),
    pytest.param(AllDebridClient, "alldebrid", "ALLDEBRID_API_KEY", "123", id="alldebrid"),
    pytest.param(DebridLinkClient, "debridlink", "DEBRIDLINK_API_KEY", "abc", id="debridlink"),
    pytest.param(TorBoxClient, "torbox", "TORBOX_API_KEY", "42", id="torbox"),
]


@pytest.mark.parametrize(("client_cls", "module", "key_name", "download_id"), _CLIENTS)
def test_status_polls_share_one_session(
    monkeypatch, tmp_path, client_cls, module, key_name, download_id
):
    monkeypatch.setattr(
        f"shelfmark.download.clients.{module}.config.get",
        lambda key, default="": {key_name: "api-key"}.get(key, default),
    )
    monkeypatch.setattr(f"shelfmark.download.clients.{module}.TMP_DIR", tmp_path)
    monkeypatch.setattr(client_cls, "_downloads", {})

    sent: list[str] = []

    def fake_request(_self, method, url, *_args, **_kwargs):
        sent.append(url)
        raise requests.exceptions.ConnectionError("offline")

    sessions_built: list[object] = []
    real_init = requests.Session.__init__

    def counting_init(self, *args, **kwargs):
        sessions_built.append(self)
        real_init(self, *args, **kwargs)

    client = client_cls()
    monkeypatch.setattr(requests.Session, "request", fake_request)
    monkeypatch.setattr(requests.Session, "__init__", counting_init)

    for _ in range(3):
        status = client.get_status(download_id)
        assert status.state is not None

    assert len(sent) == 3, "each poll should still ask the API once"
    assert sessions_built == [], "polls should reuse a session built up front"
