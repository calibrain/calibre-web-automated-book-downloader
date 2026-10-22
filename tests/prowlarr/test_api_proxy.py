"""Prowlarr API client must honor Network HTTP proxy settings (#1524)."""

import pytest
import requests

import shelfmark.release_sources.prowlarr.api as prowlarr_api
from shelfmark.release_sources.prowlarr.api import ProwlarrClient

_TORZNAB_EMPTY = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel></channel></rss>"""


class _Response:
    def __init__(self, text="", status_code=200, reason="OK", payload=None):
        self.text = text
        self.status_code = status_code
        self.reason = reason
        self.ok = status_code < 400
        self._payload = payload if payload is not None else {}

    def raise_for_status(self):
        if not self.ok:
            raise requests.exceptions.HTTPError(f"{self.status_code} {self.reason}", response=self)

    def json(self):
        return self._payload


@pytest.fixture
def http_proxy(monkeypatch):
    expected = {
        "http": "http://10.0.80.3:8118",
        "https": "http://10.0.80.3:8118",
    }
    monkeypatch.setattr(prowlarr_api, "get_proxies", lambda _url: expected)
    monkeypatch.setattr(prowlarr_api, "get_ssl_verify", lambda _url: True)
    return expected


def test_prowlarr_json_request_passes_configured_http_proxy(monkeypatch, http_proxy):
    client = ProwlarrClient("http://prowlarr:9696", "apikey")
    captured: dict[str, object] = {}

    def fake_request(**kwargs):
        captured.update(kwargs)
        return _Response(payload={"version": "1.0.0"})

    monkeypatch.setattr(client._session, "request", fake_request)

    ok, _message = client.test_connection()

    assert ok is True
    assert captured.get("proxies") == http_proxy


def test_prowlarr_torznab_search_passes_configured_http_proxy(monkeypatch, http_proxy):
    client = ProwlarrClient("http://prowlarr:9696", "apikey", indexer_timeout=90)
    captured: dict[str, object] = {}

    def fake_get(**kwargs):
        captured.update(kwargs)
        return _Response(_TORZNAB_EMPTY)

    monkeypatch.setattr(client._session, "get", fake_get)

    assert client.torznab_search(indexer_id=1, query="Dune") == []
    assert captured.get("proxies") == http_proxy
