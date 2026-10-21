import requests

from shelfmark.core.cache import get_metadata_cache
from shelfmark.metadata_providers import MetadataSearchOptions, SearchType
from shelfmark.metadata_providers.databazeknih import (
    DatabazeKnihProvider,
    _id_from_link,
    _names_match,
    _valid_isbn,
)

# Shortened copies of real pages from https://www.databazeknih.cz
BOOK_HTML = r"""<html><head>
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "Book",
  "name": "Krev elfů",
  "url": "https://www.databazeknih.cz/prehled-knihy/zaklinac-saga-krev-elfu-30482",
    "isbn": "80-85951-05-3",
    "author": [
          {
        "@type": "Person",
        "name": "Andrzej Sapkowski"
      }      ],
  "image": "https:\/\/www.databazeknih.cz\/img\/books\/30_\/30482\/bmid_krev-elfu-Jqe-30482.jpg",
    "aggregateRating": {
    "@type": "AggregateRating",
    "ratingValue": "4.6",
    "ratingCount": "5790",
    "bestRating": "5",
    "worstRating": "1"
  },
    "description": "První část…",
    "publisher": [
          {
        "@type": "Organization",
        "name": "Leonardo"
      }      ],
    "inLanguage": "cs",
  "genre": [
          "Literatura světová",          "Romány",          "Fantasy"      ]}
</script></head><body>
<div class="lora book_detail_serie_info">
    <p class="inline">
        <a class="odright_pet" href='/serie/zaklinac-saga---7063?lang=cz' title='Zaklínač: sága'>
            Zaklínač: sága
        </a> série</p>
    <span class="nowrap">
        <span class="odright_pet">1. díl</span>
    </span>
    <br />
    <p class="inline">
        <a class="odright_pet" href='/serie/zaklinac-zaklinac-13665?lang=cz' title='Zaklínač'>
            Zaklínač
        </a> série</p>
    3. díl
</div>
<h1 class="oddown_zero">Krev elfů</h1>
<div class="lora lineHeightMid">
    <a href='/zanry/fantasy-21'>Fantasy</a><br />
    1998
    <span class='pozn'>,</span> <a href='/nakladatelstvi/leonardo-338'>Leonardo</a>
</div>
<div id='left'>
    <h2>O knize <em class="nobold">Krev elfů</em></h2>
    <p class='new2 odtop'>První část ságy o Zaklínači.
Geralt, Ciri, Yennefer a další.</p>
</div>
</body></html>"""

MORE_INFO_HTML = """
<div class='book-details__row'><dt>Originální název</dt><dd>Krew elfów, 1994</dd></div>
<div class='book-details__row'><dt>Počet stran</dt><dd><span itemprop='numberOfPages'>308</span></dd></div>
<div class='book-details__row'><dt>Jazyk vydání</dt><dd>český</dd></div>
<div class='book-details__row'><dt>ISBN</dt><dd>80-85951-05-3</dd></div>
"""

SEARCH_HTML = """<html><body>
<p class='new'>
    <a href="/prehled-knihy/zaklinac-saga-krev-elfu-30482"><picture>
        <img src="https://www.databazeknih.cz/img/books/30_/30482/krev-elfu-Jqe-30482.jpg?v=1577582533"
             alt="Obálka knihy Krev elfů" class="img_39_left odtopm" /></picture></a>
    <img src='img/content/squares/red.gif' title='91%' alt='91%' class='odr' /><a class='new' type='book'
        href='/prehled-knihy/zaklinac-saga-krev-elfu-30482'>Krev elfů</a>
    <br /><span class='pozn'>
        1998,
        Andrzej Sapkowski
    </span>
</p>
<p class='new'>
    <img src='img/content/squares/blue.gif' title='47%' alt='47%' class='odr' /><a class='new' type='book'
        href='/prehled-knihy/john-sinclair-na-stope-hruzy-zaklinac-40113'>Zaklínač</a>
    <br /><span class='pozn'>
        1998,
        Jason Dark (p)
    </span>
</p>
<div class="pager"><div class="pagination">
    <span class="current">1</span>
    <span class="next"><a rel="next" href="/vyhledavani/knihy?q=krev&amp;page=2">&gt;</a></span>
</div></div>
</body></html>"""

AUTHOR_SEARCH_HTML = """<html><body>
<a href="autori/andrzej-sapkowski-269">Andrzej Sapkowski</a>
<a href="autori/andrzej-sapkowski-ml-99999">Andrzej Nowak</a>
</body></html>"""

AUTHOR_BOOKS_HTML = """<html><body>
<h3 class='midlRowHeight oddown'>
    <a href='/prehled-knihy/krev-elfu-433' title='Krev elfů'>Krev elfů</a>
    <span class='pozn odl'> 2011 (1. vydání 1994) </span>
</h3>
<h3 class='midlRowHeight oddown'>
    <a href='/prehled-knihy/magicke-stripky-30500' title='Magické střípky'>Magické střípky</a>
    <span class='pozn odl'> 2002 (1. vydání) </span>
</h3>
<h3>Až vyjde nová kniha autora</h3>
</body></html>"""


class _DbkResponse:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        return None


class _DbkSession:
    """Serves canned HTML per URL substring, first match wins."""

    def __init__(self, pages):
        self.pages = pages
        self.urls = []
        self.headers = {}

    @property
    def calls(self):
        return len(self.urls)

    def get(self, url, **kwargs):
        self.urls.append(url)
        for fragment, html in self.pages.items():
            if fragment in url:
                return _DbkResponse(html)
        raise requests.HTTPError(f"unexpected URL: {url}")


class _FlakyDbkSession(_DbkSession):
    def get(self, url, **kwargs):
        if not self.urls:
            self.urls.append(url)
            raise requests.Timeout
        return super().get(url, **kwargs)


def _provider(session):
    provider = DatabazeKnihProvider()
    provider.session = session
    return provider


class TestDatabazeKnihSearch:
    def test_search_parses_results(self):
        get_metadata_cache().clear()
        provider = _provider(_DbkSession({"/vyhledavani/knihy": SEARCH_HTML}))

        books = provider.search(MetadataSearchOptions(query="krev elfů"))

        assert [b.provider_id for b in books] == ["30482", "40113"]
        book = books[0]
        assert book.provider == "databazeknih"
        assert book.title == "Krev elfů"
        assert book.authors == ["Andrzej Sapkowski"]
        assert book.publish_year == 1998
        assert book.cover_url.startswith("https://www.databazeknih.cz/img/books/30_/30482/")
        assert book.source_url == "https://www.databazeknih.cz/prehled-knihy/kniha-30482"
        assert {f.label: f.value for f in book.display_fields} == {"Rating": "91%"}
        # Pseudonym marker removed, missing cover stays empty
        assert books[1].authors == ["Jason Dark"]
        assert books[1].cover_url is None

    def test_search_passes_page_and_language(self):
        get_metadata_cache().clear()
        session = _DbkSession({"/vyhledavani/knihy": SEARCH_HTML})
        provider = _provider(session)

        provider.search(MetadataSearchOptions(query="krev", page=2, language="sk"))

        assert "page=2" in session.urls[0]
        assert "lang=sk" in session.urls[0]

    def test_search_redirected_to_single_book(self):
        get_metadata_cache().clear()
        provider = _provider(_DbkSession({"/vyhledavani/knihy": BOOK_HTML}))

        books = provider.search(MetadataSearchOptions(query="krev elfů"))

        assert [b.provider_id for b in books] == ["30482"]
        assert books[0].series_name == "Zaklínač: sága"

    def test_title_and_author_filters_on_author(self):
        get_metadata_cache().clear()
        provider = _provider(_DbkSession({"/vyhledavani/knihy": SEARCH_HTML}))

        books = provider.search(
            MetadataSearchOptions(query="", fields={"title": "zaklínač", "author": "jason dark"})
        )

        assert [b.provider_id for b in books] == ["40113"]

    def test_author_only_lists_author_books(self):
        get_metadata_cache().clear()
        session = _DbkSession(
            {"in=authors": AUTHOR_SEARCH_HTML, "/vydane-knihy/": AUTHOR_BOOKS_HTML}
        )
        provider = _provider(session)

        books = provider.search(
            MetadataSearchOptions(query="", fields={"author": "Sapkowski Andrzej"})
        )

        assert session.urls[1] == "https://www.databazeknih.cz/vydane-knihy/autor-269"
        assert [(b.provider_id, b.title, b.publish_year) for b in books] == [
            ("433", "Krev elfů", 1994),
            ("30500", "Magické střípky", 2002),
        ]
        assert books[0].authors == ["Sapkowski Andrzej"]

    def test_unknown_author_returns_nothing(self):
        get_metadata_cache().clear()
        session = _DbkSession({"in=authors": AUTHOR_SEARCH_HTML})
        provider = _provider(session)

        books = provider.search(
            MetadataSearchOptions(query="Terry Pratchett", search_type=SearchType.AUTHOR)
        )

        assert books == []
        assert session.calls == 1

    def test_search_does_not_cache_request_failures(self):
        get_metadata_cache().clear()
        session = _FlakyDbkSession({"/vyhledavani/knihy": SEARCH_HTML})
        provider = _provider(session)
        options = MetadataSearchOptions(query="krev elfů")

        assert provider.search(options) == []

        books = provider.search(options)

        assert session.calls == 2
        assert [book.provider_id for book in books] == ["30482", "40113"]


class TestDatabazeKnihGetBook:
    def test_get_book_parses_details(self):
        get_metadata_cache().clear()
        provider = _provider(
            _DbkSession({"/prehled-knihy/": BOOK_HTML, "/book-detail-more-info/": MORE_INFO_HTML})
        )

        book = provider.get_book("30482")

        assert book is not None
        assert book.title == "Krev elfů"
        assert book.authors == ["Andrzej Sapkowski"]
        assert book.isbn_10 == "8085951053"
        assert book.isbn_13 is None
        assert book.publisher == "Leonardo"
        assert book.publish_year == 1998
        assert book.language == "cs"
        assert book.genres == ["Literatura světová", "Romány", "Fantasy"]
        assert book.cover_url.endswith("/30482/bmid_krev-elfu-Jqe-30482.jpg")
        assert book.source_url.endswith("/prehled-knihy/zaklinac-saga-krev-elfu-30482")
        # The whole description from the page, not the shortened JSON-LD one
        assert book.description.startswith("První část ságy o Zaklínači.\nGeralt")
        assert (book.series_id, book.series_name, book.series_position) == (
            "7063",
            "Zaklínač: sága",
            1.0,
        )
        fields = {f.label: f.value for f in book.display_fields}
        assert fields == {
            "Rating": "4.6",
            "Ratings": "5,790",
            "Pages": "308",
            "Original title": "Krew elfów, 1994",
        }

    def test_get_book_rejects_non_book_page(self):
        get_metadata_cache().clear()
        provider = _provider(_DbkSession({"/prehled-knihy/": SEARCH_HTML}))

        assert provider.get_book("12345") is None

    def test_isbn_search_resolves_redirected_book(self):
        get_metadata_cache().clear()
        session = _DbkSession(
            {
                "/search?": BOOK_HTML,
                "/prehled-knihy/": BOOK_HTML,
                "/book-detail-more-info/": MORE_INFO_HTML,
            }
        )
        provider = _provider(session)

        books = provider.search(
            MetadataSearchOptions(query="80-85951-05-3", search_type=SearchType.ISBN)
        )

        assert "q=8085951053" in session.urls[0]
        assert [b.provider_id for b in books] == ["30482"]
        assert books[0].isbn_10 == "8085951053"

    def test_isbn_search_ignores_invalid_isbn(self):
        session = _DbkSession({})
        provider = _provider(session)

        assert provider.search_by_isbn("2024") is None
        assert session.calls == 0


class TestHelpers:
    def test_id_from_link(self):
        assert _id_from_link("/prehled-knihy/krev-elfu-433") == "433"
        assert _id_from_link("/serie/zaklinac-saga---7063?lang=cz") == "7063"
        assert _id_from_link("autori/andrzej-sapkowski-269") == "269"
        assert _id_from_link("/prehled-knihy/") == ""

    def test_names_match_ignores_accents_and_order(self):
        assert _names_match("Karel Čapek", "capek karel")
        assert _names_match("Stanisław Lem", "Stanislaw Lem")
        assert not _names_match("Andrzej Nowak", "Andrzej Sapkowski")

    def test_valid_isbn(self):
        assert _valid_isbn("978-80-257-1234-5") == "9788025712345"
        assert _valid_isbn("80-85951-05-x") == "808595105X"
        assert _valid_isbn("2024") is None
