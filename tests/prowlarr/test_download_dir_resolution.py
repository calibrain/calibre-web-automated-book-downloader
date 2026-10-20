"""Tests for per-content-type torrent client download directories."""

import pytest

from shelfmark.download.clients._coercion import resolve_download_dir


@pytest.mark.parametrize(
    ("default_dir", "audiobook_dir", "content_type", "expected"),
    [
        ("/books", "/audio", "audiobook", "/audio"),
        ("/books", "/audio", "ebook", "/books"),
        ("/books", "/audio", None, "/books"),
        ("/books", "", "audiobook", "/books"),
        ("", "/audio", "audiobook", "/audio"),
        ("", "", "audiobook", ""),
    ],
)
def test_resolve_download_dir(default_dir, audiobook_dir, content_type, expected):
    assert resolve_download_dir(default_dir, audiobook_dir, content_type) == expected
