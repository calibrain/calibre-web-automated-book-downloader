"""Open Library requests must honor Network HTTP proxy settings (#1524)."""

import shelfmark.metadata_providers.openlibrary as openlibrary_module
from shelfmark.metadata_providers import MetadataSearchOptions, SearchType
from shelfmark.metadata_providers.openlibrary import OpenLibraryProvider


class _FakeResponse:
    def __init__(self, payload=None, status_code=200):
        self._payload = payload if payload is not None else {"docs": []}
        self.status_code = status_code
        self.ok = status_code < 400

    def raise_for_status(self):
        if not self.ok:
            raise openlibrary_module.requests.HTTPError(f"{self.status_code}", response=self)

    def json(self):
        return self._payload


def test_openlibrary_search_passes_configured_http_proxy(monkeypatch):
    """Universal Open Library search must send traffic through get_proxies()."""
    expected = {
        "http": "http://10.0.80.3:8118",
        "https": "http://10.0.80.3:8118",
    }
    monkeypatch.setattr(
        openlibrary_module,
        "get_proxies",
        lambda url: expected if "openlibrary.org" in url else {},
    )
    monkeypatch.setattr(openlibrary_module, "get_ssl_verify", lambda _url: True)
    monkeypatch.setattr(openlibrary_module._rate_limiter, "wait_if_needed", lambda: None)

    from shelfmark.core.config import config as app_config

    monkeypatch.setattr(
        app_config,
        "get",
        lambda key, default=None: False if key == "METADATA_CACHE_ENABLED" else default,
    )

    provider = OpenLibraryProvider()
    captured: dict = {}

    def fake_get(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return _FakeResponse({"docs": []})

    monkeypatch.setattr(provider.session, "get", fake_get)

    provider.search(MetadataSearchOptions(query="Mistborn", search_type=SearchType.GENERAL))

    assert captured.get("proxies") == expected


def test_openlibrary_connection_test_passes_configured_http_proxy(monkeypatch):
    """Settings connection test for Open Library must also use the proxy."""
    expected = {
        "http": "http://proxy.local:8118",
        "https": "http://proxy.local:8118",
    }
    monkeypatch.setattr(openlibrary_module, "get_proxies", lambda _url: expected)
    monkeypatch.setattr(openlibrary_module, "get_ssl_verify", lambda _url: True)

    captured: dict = {}

    def fake_get(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return _FakeResponse({"docs": [{"title": "ok"}]})

    monkeypatch.setattr(
        openlibrary_module.requests.Session,
        "get",
        lambda self, url, **kwargs: fake_get(url, **kwargs),
    )

    result = openlibrary_module._test_openlibrary_connection()

    assert result["success"] is True
    assert captured.get("proxies") == expected
