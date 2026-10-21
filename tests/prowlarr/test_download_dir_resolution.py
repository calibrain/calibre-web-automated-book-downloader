"""Tests for per-content-type torrent client download directories."""

import pytest

from shelfmark.download.clients._coercion import resolve_download_dir


@pytest.mark.parametrize(
    ("separate", "content_type", "expected"),
    [
        (False, "audiobook", "/shared"),
        (False, "ebook", "/shared"),
        (False, None, "/shared"),
        (True, "audiobook", "/audio"),
        (True, "ebook", "/books"),
        (True, None, "/books"),
    ],
)
def test_resolve_download_dir(separate, content_type, expected):
    assert (
        resolve_download_dir(
            separate=separate,
            shared_dir="/shared",
            book_dir="/books",
            audiobook_dir="/audio",
            content_type=content_type,
        )
        == expected
    )


def test_separate_dirs_do_not_fall_back_to_shared_dir():
    """Empty per-type directories mean the client default, never the shared directory."""
    for content_type in ("audiobook", "ebook"):
        assert (
            resolve_download_dir(
                separate=True,
                shared_dir="/shared",
                book_dir="",
                audiobook_dir="",
                content_type=content_type,
            )
            == ""
        )
