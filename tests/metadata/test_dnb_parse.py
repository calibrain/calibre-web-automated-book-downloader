import unicodedata

import pytest
import requests

import shelfmark.metadata_providers.dnb as dnb
from shelfmark.core.cache import get_metadata_cache
from shelfmark.core.languages import known_language_codes
from shelfmark.metadata_providers import (
    MetadataSearchOptions,
    SearchType,
    SortOrder,
    get_provider_sort_options,
)
from shelfmark.metadata_providers.dnb import (
    DNBProvider,
    _clean_series,
    _format_author,
    dnb_settings,
)

EXCLUDED = " NOT (mat=film OR mat=music OR mat=microfiches)"
UMLAUT = "\N{COMBINING DIAERESIS}"  # DNB writes "ä" as "a" + this mark


@pytest.fixture(autouse=True)
def _isolated_provider(monkeypatch):
    get_metadata_cache().clear()
    # The limiter is shared across tests; past 60 fake requests it would sleep a minute
    monkeypatch.setattr(dnb._rate_limiter, "wait_if_needed", lambda: None)
    # Independent of a local /config; the book_languages fixture turns the filter on
    original_get = dnb.app_config.get
    monkeypatch.setattr(
        dnb.app_config,
        "get",
        lambda key, *args, **kwargs: (
            False if key == "DNB_USE_BOOK_LANGUAGES" else original_get(key, *args, **kwargs)
        ),
    )


@pytest.fixture
def book_languages(monkeypatch):
    """Set the DNB checkbox and BOOK_LANGUAGE (system-wide and per user)."""
    original_get = dnb.app_config.get

    def configure(system, *, enabled=True, per_user=None):
        def get(key, default=None, user_id=None):
            if key == "DNB_USE_BOOK_LANGUAGES":
                return enabled
            if key == "BOOK_LANGUAGE":
                return (per_user or {}).get(user_id, system)
            return original_get(key, default, user_id=user_id)

        monkeypatch.setattr(dnb.app_config, "get", get)

    return configure


def _field(tag, *subfields, ind2=" "):
    subs = "".join(f'<subfield code="{code}">{value}</subfield>' for code, value in subfields)
    return f'<datafield tag="{tag}" ind1=" " ind2="{ind2}">{subs}</datafield>'


def _record(*fields, idn="1", title="Krabat", author="Preußler, Otfried", fixed=None):
    """A minimal MARC21 record; `fixed` is the 008 fixed-length data field."""
    parts = [f'<controlfield tag="001">{idn}</controlfield>']
    if fixed:
        parts.append(f'<controlfield tag="008">{fixed}</controlfield>')
    if author:
        parts.append(_field("100", ("a", author), ("4", "aut")))
    parts.append(_field("245", ("a", title)))
    return (
        f'<record xmlns="http://www.loc.gov/MARC21/slim">{"".join(parts)}{"".join(fields)}</record>'
    )


# Modern RDA record: series in 830 and repeated as "(Harry Potter 1)" in the title
HARRY_POTTER = """
<record xmlns="http://www.loc.gov/MARC21/slim" type="Bibliographic">
  <controlfield tag="001">1420086006</controlfield>
  <controlfield tag="008">250101s2026    gw ||||| |||| 00||||ger  </controlfield>
  <datafield tag="020" ind1=" " ind2=" ">
    <subfield code="a">9783551323507</subfield>
    <subfield code="c">: EUR 6.99 (DE)</subfield>
  </datafield>
  <datafield tag="020" ind1=" " ind2=" "><subfield code="a">355132350X</subfield></datafield>
  <datafield tag="041" ind1=" " ind2=" "><subfield code="a">ger</subfield></datafield>
  <datafield tag="100" ind1="1" ind2=" ">
    <subfield code="a">Rowling, J. K.</subfield>
    <subfield code="4">aut</subfield>
  </datafield>
  <datafield tag="245" ind1="1" ind2="0">
    <subfield code="a">Harry Potter und der Stein der Weisen (Harry Potter 1)</subfield>
    <subfield code="c">J.K. Rowling</subfield>
  </datafield>
  <datafield tag="264" ind1=" " ind2="1">
    <subfield code="a">Hamburg</subfield>
    <subfield code="b">Carlsen</subfield>
    <subfield code="c">2026</subfield>
  </datafield>
  <datafield tag="300" ind1=" " ind2=" "><subfield code="a">336 Seiten</subfield></datafield>
  <datafield tag="337" ind1=" " ind2=" ">
    <subfield code="a">ohne Hilfsmittel zu benutzen</subfield>
  </datafield>
  <datafield tag="653" ind1=" " ind2=" ">
    <subfield code="a">(Produktform)Paperback / softback</subfield>
  </datafield>
  <datafield tag="653" ind1=" " ind2=" "><subfield code="a">Harry Potter Geschenk</subfield></datafield>
  <datafield tag="655" ind1=" " ind2="7">
    <subfield code="a">Fantasy</subfield>
    <subfield code="2">gnd</subfield>
  </datafield>
  <datafield tag="655" ind1=" " ind2="7">
    <subfield code="a">Kinderbücher, Jugendbücher</subfield>
    <subfield code="2">gatbeg</subfield>
  </datafield>
  <datafield tag="700" ind1="1" ind2=" ">
    <subfield code="a">Fritz, Klaus</subfield>
    <subfield code="4">trl</subfield>
  </datafield>
  <datafield tag="830" ind1=" " ind2="0">
    <subfield code="a">Harry Potter</subfield>
    <subfield code="v">1</subfield>
  </datafield>
  <datafield tag="856" ind1="4" ind2="2">
    <subfield code="u">https://services.dnb.de/plus/idn/1420086006/blurb/</subfield>
    <subfield code="3">Inhaltstext</subfield>
  </datafield>
  <datafield tag="856" ind1="4" ind2="2"><subfield code="u">http://www.carlsen.de</subfield></datafield>
</record>
"""

# Older record: multi-part title in 245 $n/$p, non-sorting markers, ISBD punctuation,
# translator note in the subtitle, 260 instead of 264, no relator code on the author
# (but one on the translator), and a trailing comma after the author's name
RAD_DER_ZEIT = """
<record xmlns="http://www.loc.gov/MARC21/slim" type="Bibliographic">
  <controlfield tag="001">95752128X</controlfield>
  <datafield tag="020" ind1=" " ind2=" "><subfield code="a">3-442-24603-0 kart.</subfield></datafield>
  <datafield tag="041" ind1=" " ind2=" "><subfield code="a">ger</subfield></datafield>
  <datafield tag="100" ind1="1" ind2=" "><subfield code="a">Jordan, Robert,</subfield></datafield>
  <datafield tag="700" ind1="1" ind2=" ">
    <subfield code="a">König, Karin</subfield>
    <subfield code="4">trl</subfield>
  </datafield>
  <datafield tag="245" ind1="1" ind2="0">
    <subfield code="a">&#152;Das&#156; Rad der Zeit</subfield>
    <subfield code="n">3</subfield>
    <subfield code="p">&#152;Die&#156; Suche :</subfield>
    <subfield code="b">Roman / aus dem Amerikanischen von Karin König</subfield>
  </datafield>
  <datafield tag="260" ind1=" " ind2=" ">
    <subfield code="a">München :</subfield>
    <subfield code="b">Goldmann,</subfield>
    <subfield code="c">1999</subfield>
  </datafield>
  <datafield tag="490" ind1="0" ind2=" ">
    <subfield code="a">Goldmann ;</subfield>
    <subfield code="v">24603</subfield>
  </datafield>
</record>
"""

VIDEO = _record(_field("337", ("a", "video")), idn="111", title="Harry Potter")
NO_PERSONS = _record(idn="222", title="Anthologie", author=None)

DIAGNOSTIC_XML = """<?xml version="1.0" encoding="UTF-8"?>
<searchRetrieveResponse xmlns="http://www.loc.gov/zing/srw/"><version>1.1</version>
<diagnostics><diag:diagnostic xmlns:diag="http://www.loc.gov/zing/srw/diagnostic/">
<diag:uri>info:srw/diagnostic/1/1</diag:uri><diag:details>General System Error</diag:details>
<diag:message>'startIndex' must be less-than-or-equal to 'endIndex'</diag:message>
</diag:diagnostic></diagnostics></searchRetrieveResponse>
"""

# defusedxml must refuse to resolve external entities
XXE_XML = (
    '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY leak SYSTEM "file:///etc/passwd">]>'
    "<searchRetrieveResponse>&leak;</searchRetrieveResponse>"
)

BLURB_HTML = """
<html><head></head><body><div style="font-size:14px"><p>Angaben aus der Verlagsmeldung<br /><br />
<h3>Harry Potter und der Stein der Weisen</h3>Bis zu seinem elften Geburtstag glaubt Harry,
er sei ein <i>ganz normaler</i> Junge.<br /><br /><strong>Ein Klassiker<br /></strong>Millionen
von Lesern sind mit Harry aufgewachsen.</p></div></body></html>
"""


def _sru(*records, total=None):
    body = "".join(
        "<record><recordSchema>MARC21-xml</recordSchema><recordPacking>xml</recordPacking>"
        f"<recordData>{record}</recordData><recordPosition>{position}</recordPosition></record>"
        for position, record in enumerate(records, 1)
    )
    count = len(records) if total is None else total
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<searchRetrieveResponse xmlns="http://www.loc.gov/zing/srw/"><version>1.1</version>'
        f"<numberOfRecords>{count}</numberOfRecords><records>{body}</records>"
        "</searchRetrieveResponse>"
    )


class _DnbResponse:
    def __init__(self, text, status_code=200):
        self.text = text
        self.content = text.encode("utf-8")
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)


class _DnbSession:
    """Serves canned SRU responses; queries for which `hits(query)` is false find nothing.

    Records each SRU request as (maximumRecords, startRecord, query).
    """

    def __init__(self, search_xml, hits=lambda query: True, blurb=BLURB_HTML):
        self.search_xml = search_xml
        self.hits = hits
        self.blurb = blurb
        self.calls = 0
        self.queries = []

    def get(self, url, params=None, **kwargs):
        self.calls += 1
        if params is None:
            return _DnbResponse(self.blurb)
        self.queries.append((params["maximumRecords"], params["startRecord"], params["query"]))
        if not self.hits(params["query"]):
            return _DnbResponse(_sru(total=0))
        if params["maximumRecords"] == 0:
            return _DnbResponse(_sru(total=1))
        return _DnbResponse(self.search_xml)


class _SequenceSession:
    """Answers each request with the next canned response, or raises it."""

    def __init__(self, *replies):
        self.replies = list(replies)

    def get(self, url, params=None, **kwargs):
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def _provider(session):
    provider = DNBProvider()
    provider.session = session
    return provider


def _search(*records, **fields):
    """Run a one-request search over the given records."""
    session = _DnbSession(_sru(*records))
    return _provider(session).search(MetadataSearchOptions(query="Krabat", fields=fields))


def _queries(**options):
    """The SRU queries one search over the Harry Potter record sends."""
    session = _DnbSession(_sru(HARRY_POTTER))
    _provider(session).search(MetadataSearchOptions(**options))
    return [query for _, _, query in session.queries]


class TestDnbSearch:
    def test_search_parses_results(self):
        session = _DnbSession(_sru(HARRY_POTTER, RAD_DER_ZEIT, VIDEO, NO_PERSONS, total=10))
        provider = _provider(session)

        result = provider.search_paginated(
            MetadataSearchOptions(query="", fields={"title": "Harry Potter"})
        )

        # The exact phrase has hits, so its first page is the only request
        assert session.queries == [(40, 1, 'tit="Harry Potter"' + EXCLUDED)]
        # Video and person-less records are skipped
        assert [book.provider_id for book in result.books] == ["1420086006", "95752128X"]
        assert result.total_found == 10
        assert result.has_more is True

        book = result.books[0]
        assert book.provider == "dnb"
        assert book.title == "Harry Potter und der Stein der Weisen"
        assert book.authors == ["J. K. Rowling"]  # translator excluded
        assert book.isbn_13 == "9783551323507"
        assert book.isbn_10 == "355132350X"
        assert book.publisher == "Carlsen"
        assert book.publish_year == 2026
        assert book.language == "de"
        assert book.series_name == "Harry Potter"
        assert book.series_position == 1.0
        # Publisher keywords (653) and qualifier-prefixed terms are not genres
        assert book.genres == ["Fantasy", "Kinderbücher", "Jugendbücher"]
        assert book.cover_url is None  # DNB's cover service is behind bot protection
        assert book.source_url == "https://d-nb.info/1420086006"
        assert book.description is None  # blurbs are only fetched for book details
        assert {f.label: f.value for f in book.display_fields} == {"Pages": "336"}

    def test_search_parses_multipart_title(self):
        book = _search(RAD_DER_ZEIT)[0]

        assert book.title == "Die Suche"
        assert book.subtitle == "Roman"
        assert book.authors == ["Robert Jordan"]  # trailing comma dropped, translator excluded
        assert book.series_name == "Das Rad der Zeit"
        assert book.series_position == 3.0
        assert book.isbn_10 == "3442246030"
        assert book.isbn_13 is None
        assert book.publisher == "Goldmann"
        assert book.publish_year == 1999

    @pytest.mark.parametrize(
        ("text", "stage_with_hits", "searched"),
        [
            # A complete title: exactly that title, plus records where a word is a name
            ("Der Schwarm", "tst", "complete title"),
            # A phrase within a title ("Die Verwandlung Kafka" naming a study guide)
            ("Die Verwandlung Kafka", "tit", "title phrase"),
            # Name and title words mixed: every word in the title or a name
            ("Die Verwandlung Kafka", None, "every word"),
        ],
        ids=["complete-title", "title-phrase", "name-and-title-words"],
    )
    def test_search_box_text(self, text, stage_with_hits, searched):
        words = text.split()
        every_word = " AND ".join(f'(tit="{w}" OR per="{w}")' for w in words)
        some_name = " OR ".join(f'per="{w}"' for w in words)
        complete, phrase = f'(tst="{text}")', f'tit="{text}"'
        expected = {
            "complete title": f"({complete} OR ({every_word} AND ({some_name})))",
            "title phrase": f"({phrase} OR ({every_word} AND ({some_name})))",
            "every word": every_word,
        }[searched]
        probes = [complete] if stage_with_hits == "tst" else [complete, phrase]

        def hits(query):
            stage = (
                "tst" if query.startswith("(tst=") else "tit" if query.startswith("tit=") else None
            )
            return stage is None or stage == stage_with_hits

        session = _DnbSession(_sru(HARRY_POTTER), hits=hits)

        _provider(session).search(MetadataSearchOptions(query=text, sort=SortOrder.OLDEST))

        sort = " sortBy jhr/sort.ascending"
        assert session.queries == [(0, 1, probe + EXCLUDED + sort) for probe in probes] + [
            (40, 1, expected + EXCLUDED + sort)
        ]

    def test_search_box_also_tries_a_leading_article(self):
        # "Herr der Ringe" is how people type "Der Herr der Ringe"
        session = _DnbSession(_sru(HARRY_POTTER))

        _provider(session).search(MetadataSearchOptions(query="Herr der Ringe"))

        assert session.queries[0][2].startswith(
            '(tst="Herr der Ringe" OR tst="Der Herr der Ringe" OR tst="Die Herr der Ringe"'
        )

    def test_search_falls_back_to_word_query(self):
        exact = 'per="Rowling" AND tit="Die Stein der Weisen"'
        session = _DnbSession(_sru(HARRY_POTTER), hits=lambda query: exact not in query)

        books = _provider(session).search(
            MetadataSearchOptions(
                query="", fields={"author": "Rowling", "title": "Die Stein der Weisen"}
            )
        )

        assert [book.provider_id for book in books] == ["1420086006"]
        # German articles are dropped from the word-by-word title query
        assert session.queries == [
            (40, 1, exact + EXCLUDED),
            (40, 1, 'per="Rowling" AND tit="Stein" AND tit="Weisen"' + EXCLUDED),
        ]

    def test_later_pages_check_the_hit_count_first(self):
        # So page 2 runs against the same query variant as page 1
        session = _DnbSession(_sru(HARRY_POTTER, total=100))

        _provider(session).search(
            MetadataSearchOptions(query="", fields={"title": "Harry Potter"}, page=2)
        )

        assert session.queries == [
            (0, 1, 'tit="Harry Potter"' + EXCLUDED),
            (40, 41, 'tit="Harry Potter"' + EXCLUDED),
        ]

    def test_page_and_limit_are_clamped(self):
        # A limit of 0 would otherwise page forever through empty pages
        session = _DnbSession(_sru(HARRY_POTTER, total=1))

        result = _provider(session).search_paginated(
            MetadataSearchOptions(query="Rowling", limit=0, page=-3)
        )

        assert session.queries[-1][:2] == (1, 1)  # the page request
        assert (result.page, result.has_more) == (1, False)

    @pytest.mark.parametrize(
        "reply",
        [
            _DnbResponse("<html><body>Wartungsarbeiten</body></html>"),
            _DnbResponse(DIAGNOSTIC_XML),
            _DnbResponse(XXE_XML),
            _DnbResponse("", status_code=503),
            requests.Timeout(),
        ],
        ids=["maintenance-page", "diagnostic", "external-entity", "http-503", "timeout"],
    )
    def test_unusable_responses_are_not_cached(self, reply):
        provider = _provider(_SequenceSession(reply, _DnbResponse(_sru(HARRY_POTTER))))
        options = MetadataSearchOptions(query="", fields={"author": "Rowling"})  # one request

        assert provider.search(options) == []
        assert [book.provider_id for book in provider.search(options)] == ["1420086006"]

    @pytest.mark.parametrize(
        "options",
        [
            MetadataSearchOptions(query='  " \\ '),
            MetadataSearchOptions(query='9783641216177" or tit=*', search_type=SearchType.ISBN),
        ],
        ids=["blank", "isbn-injection"],
    )
    def test_unusable_input_makes_no_request(self, options):
        session = _DnbSession(_sru(HARRY_POTTER))

        assert _provider(session).search(options) == []
        assert session.calls == 0

    def test_author_search_keeps_only_that_persons_books(self):
        # DNB's person index also matches people a book is about
        schaetzing = _record(idn="1", author="Scha" + UMLAUT + "tzing, Frank")
        about_him = _record(idn="2", author="Seibt, Gustav")

        # "Schaetzing" and "Schätzing" compare equal
        books = _search(schaetzing, about_him, author="Schaetzing")

        assert [book.provider_id for book in books] == ["1"]

    def test_author_filter_keeps_page_when_nothing_matches(self):
        # A pseudonym shares no name part with the searched person
        books = _search(_record(author="Bachman, Richard"), author="King")

        assert [book.authors for book in books] == [["Richard Bachman"]]

    def test_people_with_other_roles_are_not_authors(self):
        narrated = _record(_field("700", ("a", "Berben, Iris"), ("4", "nrt")), idn="1", author=None)
        anthology = _record(
            _field("700", ("a", "Usher, Shaun"), ("4", "edt")),
            _field("700", ("a", "Eidinger, Lars"), ("4", "nrt")),
            idn="2",
            author=None,
        )

        books = _search(narrated, anthology)

        # Narrator-only records have no author and are skipped
        assert [(book.provider_id, book.authors) for book in books] == [("2", ["Shaun Usher"])]

    @pytest.mark.parametrize(
        ("fields", "fixed", "year", "publisher"),
        [
            (
                [
                    _field("264", ("c", "©2022"), ind2="4"),
                    _field("264", ("b", "Booket"), ("c", "[2023]"), ind2="1"),
                ],
                None,
                2023,
                "Booket",
            ),
            ([], "240724s2024    gw ||||| |||| 00||||ger  ", 2024, None),
            ([_field("264", ("c", "[9999]"), ind2="1")], "870101s1987", 1987, None),
            (
                [
                    _field("264", ("c", "2021"), ind2="1"),
                    _field("264", ("b", "Druckerei Muster"), ind2="3"),
                ],
                None,
                2021,
                None,
            ),
        ],
        ids=[
            "publication-over-copyright",
            "fixed-field",
            "implausible-year",
            "printer-is-not-publisher",
        ],
    )
    def test_publication_year_and_publisher(self, fields, fixed, year, publisher):
        book = _search(_record(*fields, fixed=fixed))[0]

        assert (book.publish_year, book.publisher) == (year, publisher)

    @pytest.mark.parametrize(
        ("extent", "pages"),
        [
            ("1.024 S.", "1024"),
            ("1 Online-Ressource (224 pages)", "224"),
            ("999999 Seiten", None),
            ("3 CDs", None),
        ],
    )
    def test_page_count(self, extent, pages):
        book = _search(_record(_field("300", ("a", extent))))[0]

        assert [f.value for f in book.display_fields if f.label == "Pages"] == (
            [pages] if pages else []
        )

    @pytest.mark.parametrize(
        ("title", "publisher", "expected"),
        [
            # Spelled-out numbers become the title indexers know; "[s. n.]" means no publisher
            (
                "20000 [Zwanzigtausend] Meilen unter den Meeren",
                "[s. n.] @",
                ("20000 Meilen unter den Meeren", None),
            ),
            # A wholly bracketed title keeps its text; publisher notes are dropped
            (
                "[Briefe]",
                "RM-Buch-und-Medien-Vertrieb [u.a.]",
                ("Briefe", "RM-Buch-und-Medien-Vertrieb"),
            ),
        ],
        ids=["insertion-and-no-publisher", "bracketed-title-and-note"],
    )
    def test_cataloguer_brackets_are_removed(self, title, publisher, expected):
        book = _search(_record(_field("264", ("b", publisher), ind2="1"), title=title))[0]

        assert (book.title, book.publisher) == expected

    def test_all_text_is_composed_unicode(self):
        record = _record(
            _field("264", ("b", "Bu" + UMLAUT + "cher"), ind2="1"),
            _field("520", ("a", "U" + UMLAUT + "ber Gro" + UMLAUT + "ße")),
            title="Verha" + UMLAUT + "ltnis",
            author="Mu" + UMLAUT + "ller, Jo" + UMLAUT + "rg",
        )

        book = _search(record)[0]

        values = [book.title, *book.authors, book.publisher, book.description]
        assert all(unicodedata.is_normalized("NFC", value) for value in values)
        assert book.authors == [
            unicodedata.normalize("NFC", "Jo" + UMLAUT + "rg Mu" + UMLAUT + "ller")
        ]


class TestDnbLookups:
    def test_get_book_fetches_blurb(self):
        session = _DnbSession(_sru(HARRY_POTTER))

        book = _provider(session).get_book("1420086006")

        assert book is not None
        assert session.queries == [(1, 1, "idn=1420086006" + EXCLUDED)]
        assert book.description == (
            "Bis zu seinem elften Geburtstag glaubt Harry, er sei ein ganz normaler Junge.\n\n"
            "Ein Klassiker\n"
            "Millionen von Lesern sind mit Harry aufgewachsen."
        )

    def test_requests_use_the_configured_proxy(self, monkeypatch):
        # The proxy from Settings > Network, which requests' own env handling misses for HTTPS
        proxies = {"https": "http://proxy.local:8118"}
        monkeypatch.setattr(dnb, "get_proxies", lambda url: proxies if "dnb.de" in url else {})
        session = _DnbSession(_sru(HARRY_POTTER))
        serve, used = session.get, []

        def get(url, params=None, **kwargs):
            used.append(kwargs.get("proxies"))
            return serve(url, params, **kwargs)

        monkeypatch.setattr(session, "get", get)
        _provider(session).get_book("1420086006")

        assert used == [proxies, proxies]  # catalog record, then blurb

    def test_get_book_checks_the_id(self):
        session = _DnbSession(_sru(HARRY_POTTER))
        provider = _provider(session)

        assert provider.get_book('1" OR tit="x') is None
        assert session.calls == 0

        assert provider.get_book("1420086007") is None
        assert session.calls == 1  # no blurb fetched for the wrong record

    def test_blurbs_are_only_fetched_from_dnb(self):
        # Catalogue links can point anywhere, including internal addresses
        record = _record(_field("856", ("u", "http://169.254.169.254/latest/meta-data/")), idn="77")
        session = _DnbSession(_sru(record))

        book = _provider(session).get_book("77")

        assert book.description is None
        assert session.calls == 1  # the record lookup only

    def test_description_comes_from_blurb_or_summary(self):
        # Some publishers deliver the summary as HTML, escaped inside the MARC XML
        summary = "Kurz.&lt;br&gt;&lt;br&gt;Zusammengefasst."
        record = HARRY_POTTER.replace("</record>", _field("520", ("a", summary)) + "</record>")
        provider = _provider(_DnbSession(_sru(record)))

        assert provider.search(MetadataSearchOptions(query="Rowling"))[0].description == (
            "Kurz.\n\nZusammengefasst."
        )
        assert provider.get_book("1420086006").description.startswith("Bis zu seinem")

    def test_isbn_in_search_box_uses_the_isbn_lookup(self):
        session = _DnbSession(_sru(HARRY_POTTER))

        result = _provider(session).search_paginated(
            MetadataSearchOptions(query="978-3-551-32350-7")
        )

        assert [book.provider_id for book in result.books] == ["1420086006"]
        assert result.total_found == 1
        assert session.queries == [(5, 1, "num=9783551323507" + EXCLUDED)]

    def test_isbn_lookup_checks_the_record(self):
        # The number index also matches ISSNs and other identifiers
        other = _record(_field("020", ("a", "9783492612418")), idn="2")
        isbn13_only = _record(_field("020", ("a", "9783551323507")), idn="3")

        def lookup(isbn, *records):
            get_metadata_cache().clear()
            book = _provider(_DnbSession(_sru(*records))).search_by_isbn(isbn)
            return book.provider_id if book else None

        assert lookup("9783551323507", other, HARRY_POTTER) == "1420086006"
        assert lookup("9783551323507", other) is None
        assert lookup("3-551-32350-X", isbn13_only) == "3"


class TestDnbLanguageFilter:
    @pytest.mark.parametrize(
        ("enabled", "system", "clause"),
        [
            (False, ["de"], None),
            (True, [], None),
            (True, ["de", "en"], "(spr=ger OR spr=eng)"),
            (True, "de, xx", "(spr=ger)"),
        ],
        ids=["checkbox-off", "no-languages", "german-english", "legacy-string-unknown-skipped"],
    )
    def test_book_languages_restrict_the_search(self, book_languages, enabled, system, clause):
        book_languages(system, enabled=enabled)

        expected = 'per="Rowling"' + (f" AND {clause}" if clause else "")
        assert _queries(query="", fields={"author": "Rowling"}) == [expected + EXCLUDED]

    @pytest.mark.parametrize(
        ("user_id", "clause"),
        [(7, "(spr=ger)"), (8, "(spr=eng)"), (None, "(spr=eng)")],
        ids=["own-setting", "no-own-setting", "not-logged-in"],
    )
    def test_the_searching_users_own_languages_win(self, book_languages, user_id, clause):
        # System default English; user 7 chose German for their own account
        book_languages(["en"], per_user={7: ["de"]})

        assert _queries(query="", fields={"author": "Rowling"}, user_id=user_id) == [
            f'per="Rowling" AND {clause}' + EXCLUDED
        ]

    def test_cached_results_follow_the_languages_not_the_user(self, book_languages):
        # User 7 chose German; users 8 and 9 use the system default English
        book_languages(["en"], per_user={7: ["de"]})
        session = _DnbSession(_sru(HARRY_POTTER))

        for user_id in (7, 8, 9):
            options = MetadataSearchOptions(query="", fields={"author": "Rowling"}, user_id=user_id)
            _provider(session).search(options)

        assert [query for _, _, query in session.queries] == [
            'per="Rowling" AND (spr=ger)' + EXCLUDED,
            'per="Rowling" AND (spr=eng)' + EXCLUDED,  # user 9 gets user 8's cached page
        ]

    def test_every_selectable_language_can_be_filtered(self):
        # A language without a MARC code would silently drop out of the filter
        unmapped = [code for code in known_language_codes() if dnb._language_clause([code]) is None]
        assert unmapped == []

    def test_language_passed_with_the_search_wins(self, book_languages):
        book_languages(["de", "en"])

        assert _queries(query="", fields={"author": "Rowling"}, language="fr") == [
            'per="Rowling" AND (spr=fre)' + EXCLUDED
        ]

    def test_search_box_hit_counts_are_filtered_too(self, book_languages):
        book_languages(["de"])

        queries = _queries(query="Rowling")

        assert len(queries) == 2  # complete-title count, then the page
        assert all(query.endswith(" AND (spr=ger)" + EXCLUDED) for query in queries)

    def test_direct_lookups_ignore_the_setting(self, book_languages):
        # A specific IDN or ISBN must be found whatever its language
        book_languages(["fr"])
        session = _DnbSession(_sru(HARRY_POTTER))
        provider = _provider(session)

        provider.get_book("1420086006")
        provider.search_by_isbn("9783551323507")

        assert [query for _, _, query in session.queries] == [
            "idn=1420086006" + EXCLUDED,
            "num=9783551323507" + EXCLUDED,
        ]


class TestDnbSettings:
    def test_defaults(self):
        # Same sorts as the other providers, and the book languages apply out of the box
        fields = {field.key: field for field in dnb_settings()}

        assert [option["value"] for option in get_provider_sort_options("dnb")] == [
            "relevance",
            "newest",
            "oldest",
        ]
        assert fields["DNB_DEFAULT_SORT"].default == "relevance"
        assert fields["DNB_USE_BOOK_LANGUAGES"].default is True

    @pytest.mark.parametrize(("reply", "success"), [(([], 0), True), (None, False)])
    def test_connection_test_reports_reachability(self, monkeypatch, reply, success):
        monkeypatch.setattr(DNBProvider, "_query", lambda self, cql, **kwargs: reply)

        assert dnb._test_dnb_connection()["success"] is success


class TestDnbHelpers:
    def test_clean_series_drops_publisher_imprints(self):
        assert _clean_series("Goldmann ;", "Goldmann") is None
        assert _clean_series("dtv premium", None) is None
        assert _clean_series("Harry Potter", "Carlsen") == "Harry Potter"

    def test_format_author(self):
        assert _format_author("Rowling, J. K. [Verfasser]") == "J. K. Rowling"
        assert _format_author("Wharram, C. C.,") == "C. C. Wharram"
        assert _format_author("Homer") == "Homer"
