"""Tests for targeted image cache safety and fetch fallbacks."""

import requests

from shelfmark.core.image_cache import ImageCacheService


def test_fetch_and_cache_rejects_backslash_authority_bypass_before_request(
    tmp_path, monkeypatch
) -> None:
    cache = ImageCacheService(tmp_path)
    calls = []

    def fake_get(url, **_kwargs):
        calls.append(url)
        raise AssertionError("unsafe URL should not be requested")

    monkeypatch.setattr("shelfmark.core.image_cache.requests.get", fake_get)

    assert cache.fetch_and_cache("cover-ssrf", "http://127.0.0.1:6666\\@1.1.1.1") is None
    assert calls == []
    assert "cover-ssrf" not in cache._index


def test_is_safe_url_rejects_encoded_separator_in_authority() -> None:
    assert ImageCacheService._is_safe_url("http://127.0.0.1:6666%5c@1.1.1.1") is False
    assert ImageCacheService._is_safe_url("http://127.0.0.1:6666%2f@1.1.1.1") is False


def test_is_safe_url_rejects_invalid_ipv6_url() -> None:
    assert ImageCacheService._is_safe_url("http://[") is False


def test_fetch_and_cache_blocks_unsafe_redirect(tmp_path, monkeypatch) -> None:
    cache = ImageCacheService(tmp_path)

    def fake_getaddrinfo(hostname, *_args, **_kwargs):
        addresses = {
            "example.com": "93.184.216.34",
            "127.0.0.1": "127.0.0.1",
        }
        return [(None, None, None, None, (addresses[hostname], 0))]

    class RedirectResponse:
        is_redirect = True
        headers = {"location": "http://127.0.0.1/cover.jpg"}

        def close(self):
            return None

    calls = []

    def fake_get(url, **_kwargs):
        calls.append(url)
        return RedirectResponse()

    monkeypatch.setattr("shelfmark.download.network.socket.getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr("shelfmark.core.image_cache.requests.get", fake_get)

    assert cache.fetch_and_cache("cover-redirect", "https://example.com/cover.jpg") is None
    assert calls == ["https://example.com/cover.jpg"]
    assert "cover-redirect" not in cache._index


def test_fetch_and_cache_returns_none_on_request_exception(tmp_path, monkeypatch) -> None:
    cache = ImageCacheService(tmp_path)

    def fake_get(*_args, **_kwargs):
        raise requests.exceptions.TooManyRedirects("too many redirects")

    monkeypatch.setattr("shelfmark.core.image_cache.requests.get", fake_get)

    assert cache.fetch_and_cache("cover-1", "https://example.com/cover.jpg") is None
    assert "cover-1" not in cache._index


def test_cover_cache_key_depends_on_the_url_only() -> None:
    from shelfmark.core.image_cache import cover_cache_key

    key = cover_cache_key("https://example.com/cover.jpg")

    assert key == cover_cache_key("  https://example.com/cover.jpg \n")
    assert key != cover_cache_key("https://example.com/other.jpg")
    assert key != cover_cache_key("https://example.com/cover.jpg?size=large")
    assert key.isalnum() and key == key.lower()
    assert "/" not in key and "." not in key
