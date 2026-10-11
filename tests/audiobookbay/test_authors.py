"""Reading a book's authors off an AudiobookBay detail page."""

from unittest.mock import patch

import pytest

from shelfmark.release_sources.audiobookbay import scraper
from shelfmark.release_sources.audiobookbay.handler import AudiobookBayHandler


def _page(*paragraphs: str) -> str:
    """A detail page shaped like ABB's: share line, cover, then the post's paragraphs."""
    body = "".join(paragraphs)
    return (
        '<html><body><div class="post"><div class="postContent">'
        '<div class="center"><p class="center">Shared by:<a href="/member/x">rick</a></p>'
        '<p class="center"><a href="/abss/x/"><img alt="cover" src="c.jpg"/></a></p></div>'
        f'<p style="text-align: center;"><span id="more-47"></span></p>{body}'
        "</div></div></body></html>"
    )


# An old post (2009): the listing title has no " - Author", the page has a credit line.
MUMMY_PAGE = _page(
    '<p style="text-align:left;">by Anne Rice (Author)</p>',
    "<p>With this kick-off to a new series, Vampire Chronicler Rice abandons her troupe.</p>",
)
CROSS_PAGE = _page(
    '<p style="text-align:left;">James Patterson (Author), Richard DiLallo (Author), '
    "Dylan Baker (Reader)</p>",
    "<p>Framed as a book written by Patterson's iconic detective, Alex Cross.</p>",
)
# A newer post marks its credits up.
MARKED_UP_PAGE = _page(
    '<p style="left;">Written by <a href="/?s=ram+v."><span class="author" itemprop="author">'
    'Ram V.</span></a>, <a href="/?s=sumit+kumar"><span class="author" itemprop="author">'
    'Sumit Kumar</span></a> <br />Read by <a href="/?s=graphicaudio"><span class="narrator" '
    'itemprop="author">GraphicAudio</span></a></p>'
)
# No credit line at all: the author is only mentioned in the description.
VITTORIO_PAGE = _page(
    '<p itemprop="description">Educated in the Florence of Cosimo de Medici.</p>',
    "<p>Against a backdrop of Renaissance Italy, Anne Rice creates a passionate legend.</p>",
)


@pytest.mark.parametrize(
    ("page", "expected"),
    [
        (MUMMY_PAGE, ["Anne Rice"]),
        (CROSS_PAGE, ["James Patterson", "Richard DiLallo"]),
        (MARKED_UP_PAGE, ["Ram V.", "Sumit Kumar"]),
    ],
)
def test_extract_authors_reads_the_credits(page, expected):
    assert scraper.extract_authors(page) == expected


@pytest.mark.parametrize(
    "page",
    [
        VITTORIO_PAGE,
        # Only a narrator is credited.
        _page("<p>Dylan Baker (Reader)</p>"),
        # A credit inside a sentence is description, not a credit line.
        _page("<p>A sequel to the hit by Anne Rice (Author) of the Vampire Chronicles.</p>"),
        _page("<p>Nothing here</p>"),
        "",
    ],
)
def test_extract_authors_says_nothing_without_a_credit(page):
    assert scraper.extract_authors(page) == []


class TestHandlerListAuthors:
    def test_reads_the_detail_page(self):
        with patch(
            "shelfmark.release_sources.audiobookbay.handler.scraper.fetch_detail_html",
            return_value=CROSS_PAGE,
        ) as fetch:
            authors = AudiobookBayHandler().list_authors(
                {"download_url": "https://audiobookbay.lu/abss/alex-cross-trial/"}
            )
        assert authors == ["James Patterson", "Richard DiLallo"]
        fetch.assert_called_once_with(
            "https://audiobookbay.lu/abss/alex-cross-trial/", "audiobookbay.lu"
        )

    def test_none_when_the_page_credits_nobody(self):
        with patch(
            "shelfmark.release_sources.audiobookbay.handler.scraper.fetch_detail_html",
            return_value=VITTORIO_PAGE,
        ):
            authors = AudiobookBayHandler().list_authors(
                {"download_url": "https://audiobookbay.lu/abss/vittorio/"}
            )
        assert authors is None

    def test_rejects_detail_url_on_other_host(self):
        with patch(
            "shelfmark.release_sources.audiobookbay.handler.scraper.fetch_detail_html"
        ) as fetch:
            authors = AudiobookBayHandler().list_authors(
                {"download_url": "https://evil.example/abss/x/"}
            )
        assert authors is None
        fetch.assert_not_called()
