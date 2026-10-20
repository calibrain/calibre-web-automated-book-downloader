"""Welib must only hand back a download link for the md5 that was asked for.

Issue #1364: welib answers /md5/<md5> with a search for that md5. It did not have the
file, and the resolver took the first "Download" on the results page - a link for another
book (auto_download/9c8cf85d... for md5 a2c1dc0c...). That download happened to fail, but
nothing stopped it from succeeding with the wrong file.
"""

from unittest.mock import patch

import pytest

from shelfmark.release_sources.direct_download import annas_archive

WANTED = "a2c1dc0c1fb3422b5cf7a7473bc56010"
OTHER = "9c8cf85d9d805ac89b8d3c581598a615"
WELIB_PAGE = f"https://welib.org/md5/{WANTED}"


def _resolve(link: str, page_html: str) -> str:
    with (
        patch.object(annas_archive.downloader, "html_get_page", return_value=page_html),
        patch.object(
            annas_archive.network, "get_aa_base_url", return_value="https://annas-archive.gl"
        ),
        patch.object(annas_archive, "_is_configured_zlib_link", return_value=False),
    ):
        return annas_archive._get_download_url(link, "Crown Me Dead", selector=object())  # type: ignore[arg-type]


def test_a_search_page_for_another_book_resolves_to_nothing():
    page = f'<a href="/auto_download/{OTHER}/0/0">Download</a>'

    assert _resolve(WELIB_PAGE, page) == ""


def test_the_link_for_the_requested_md5_is_picked_over_an_earlier_one():
    page = (
        f'<a href="/auto_download/{OTHER}/0/0">Download</a>'
        f'<a href="/auto_download/{WANTED.upper()}/0/0">Download</a>'
    )

    assert _resolve(WELIB_PAGE, page) == f"https://welib.org/auto_download/{WANTED.upper()}/0/0"


@pytest.mark.parametrize(
    ("link", "expected"),
    [
        (f"https://welib.org/md5/{WANTED}", WANTED),
        (f"https://welib.org/md5/{WANTED.upper()}/", WANTED),
        (f"https://welib.org/md5/{WANTED}0", None),
        (f"https://welib.org/search?q=md5:{WANTED}", None),
        ("https://example.org/book/42", None),
    ],
)
def test_md5_is_read_only_from_an_md5_page_path(link, expected):
    assert annas_archive._md5_from_page_url(link) == expected


def test_a_page_that_is_not_md5_addressed_keeps_taking_the_first_download():
    page = f'<a href="/files/{OTHER}.epub">Download</a>'

    assert (
        _resolve("https://example.org/book/42", page) == f"https://example.org/files/{OTHER}.epub"
    )


def test_a_slow_download_link_is_followed_through_its_countdown():
    """Issue #1447: welib's Download leads to a slow_download page with AA's countdown.

    It was handed straight to the downloader, which got the countdown HTML instead of
    the file. It must be resolved like an AA slow link: wait, re-fetch, take the link.
    """
    slow_url = f"https://welib.org/slow_download/{WANTED}/0/0"
    file_url = f"https://partner.example/files/{WANTED}.epub"
    pages = {
        WELIB_PAGE: [f'<a href="/slow_download/{WANTED}/0/0">Download</a>'],
        slow_url: [
            '<span class="js-partner-countdown">1</span>',
            f'<a href="{file_url}">📚 Download now</a>',
        ],
    }
    fetched: list[str] = []

    def fake_get_page(url, **_kwargs):
        fetched.append(url)
        return pages[url].pop(0)

    with (
        patch.object(annas_archive.downloader, "html_get_page", side_effect=fake_get_page),
        patch.object(
            annas_archive.network, "get_aa_base_url", return_value="https://annas-archive.gl"
        ),
        patch.object(annas_archive, "_is_configured_zlib_link", return_value=False),
    ):
        resolved = annas_archive._get_download_url(WELIB_PAGE, "Amorino", selector=object())  # type: ignore[arg-type]

    assert resolved == file_url
    assert fetched == [WELIB_PAGE, slow_url, slow_url]
