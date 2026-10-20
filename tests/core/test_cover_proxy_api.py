"""Cover proxy API security tests."""

from __future__ import annotations

import base64
import importlib
from unittest.mock import patch

import pytest

from shelfmark.core.image_cache import ImageCacheService

JPEG_BYTES = b"\xff\xd8\xff\xe0legit"
PNG_BYTES = b"\x89PNG\r\n\x1a\nevil"


@pytest.fixture(scope="module")
def main_module():
    """Import `shelfmark.main` with background startup disabled."""
    with patch("shelfmark.download.orchestrator.start"):
        import shelfmark.main as main

        importlib.reload(main)
        return main


class _FakeResponse:
    def __init__(self, data: bytes, content_type: str = "image/jpeg") -> None:
        self._data = data
        self.headers = {"content-type": content_type}

    def raise_for_status(self) -> None:
        return None

    def iter_content(self, chunk_size: int = 8192):
        yield self._data


def _encode(url: str) -> str:
    return base64.urlsafe_b64encode(url.encode()).decode()


@pytest.fixture
def cover_client(main_module, tmp_path):
    """A logged-in client whose cover cache lives in tmp_path and fetches from `images`."""
    cache = ImageCacheService(tmp_path)
    images: dict[str, bytes] = {}
    fetched: list[str] = []

    def fake_fetch(_self, url: str):
        fetched.append(url)
        data = images.get(url)
        if data is None:
            return None
        content_type = "image/png" if data.startswith(b"\x89PNG") else "image/jpeg"
        return _FakeResponse(data, content_type)

    with (
        patch.object(main_module, "get_auth_mode", return_value="none"),
        patch("shelfmark.config.env.is_covers_cache_enabled", return_value=True),
        patch("shelfmark.core.image_cache.get_image_cache", return_value=cache),
        patch.object(ImageCacheService, "_fetch_safe_response", fake_fetch),
    ):
        yield main_module.app.test_client(), images, fetched


def test_cover_proxy_requires_authentication(main_module) -> None:
    client = main_module.app.test_client()

    with patch.object(main_module, "get_auth_mode", return_value="builtin"):
        response = client.get("/api/covers/test-id")

    assert response.status_code == 401
    assert response.get_json() == {"error": "Unauthorized"}


def test_cover_proxy_caches_by_url_and_serves_hits(cover_client) -> None:
    client, images, fetched = cover_client
    legit_url = "https://covers.example.com/book-1.jpg"
    images[legit_url] = JPEG_BYTES

    first = client.get(f"/api/covers/hardcover_1?url={_encode(legit_url)}")
    second = client.get(f"/api/covers/hardcover_1?url={_encode(legit_url)}")

    assert first.status_code == 200
    assert first.data == JPEG_BYTES
    assert first.headers["X-Cache"] == "MISS"
    assert second.status_code == 200
    assert second.data == JPEG_BYTES
    assert second.headers["X-Cache"] == "HIT"
    assert fetched == [legit_url]


def test_cover_proxy_cannot_be_poisoned_through_a_shared_id(cover_client) -> None:
    """A request for another book's id with a different URL must not replace its image."""
    client, images, fetched = cover_client
    legit_url = "https://covers.example.com/book-1.jpg"
    evil_url = "https://attacker.example.net/anything.png"
    images[legit_url] = JPEG_BYTES
    images[evil_url] = PNG_BYTES

    poison = client.get(f"/api/covers/hardcover_1?url={_encode(evil_url)}")
    assert poison.status_code == 200
    assert poison.data == PNG_BYTES

    legit = client.get(f"/api/covers/hardcover_1?url={_encode(legit_url)}")
    assert legit.status_code == 200
    assert legit.data == JPEG_BYTES
    assert legit.headers["X-Cache"] == "MISS"

    # And the other way round: a cached legitimate image is not overwritten.
    again = client.get(f"/api/covers/hardcover_1?url={_encode(evil_url)}")
    assert again.data == PNG_BYTES
    served = client.get(f"/api/covers/hardcover_1?url={_encode(legit_url)}")
    assert served.data == JPEG_BYTES
    assert served.headers["X-Cache"] == "HIT"
    assert fetched == [evil_url, legit_url]


def test_cover_proxy_same_url_is_shared_across_ids(cover_client) -> None:
    """The id in the path does not pick the entry, so a redacted id still hits the cache."""
    client, images, fetched = cover_client
    legit_url = "https://covers.example.com/book-1.jpg"
    images[legit_url] = JPEG_BYTES

    client.get(f"/api/covers/hardcover_1?url={_encode(legit_url)}")
    other = client.get(f"/api/covers/viewer-id-xyz?url={_encode(legit_url)}")

    assert other.status_code == 200
    assert other.data == JPEG_BYTES
    assert other.headers["X-Cache"] == "HIT"
    assert fetched == [legit_url]


def test_cover_proxy_without_url_is_not_found(cover_client) -> None:
    client, images, _fetched = cover_client
    images["https://covers.example.com/book-1.jpg"] = JPEG_BYTES
    client.get(f"/api/covers/hardcover_1?url={_encode('https://covers.example.com/book-1.jpg')}")

    response = client.get("/api/covers/hardcover_1")

    assert response.status_code == 404
    assert response.get_json() == {"error": "Cover URL not provided"}


def test_cover_proxy_rejects_bad_encoding(cover_client) -> None:
    client, _images, fetched = cover_client

    response = client.get("/api/covers/hardcover_1?url=abc")

    assert response.status_code == 400
    assert fetched == []
