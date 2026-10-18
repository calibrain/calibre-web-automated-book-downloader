"""Synthetic SRU/MARC regression cases; no network access or BookOrbit fixtures."""

import unicodedata
from unittest.mock import Mock
from xml.sax.saxutils import escape

import pytest
import requests

from shelfmark.core.cache import get_metadata_cache
from shelfmark.metadata_providers import (
    MetadataSearchOptions,
    SearchType,
    get_provider,
    list_providers,
)


def field(tag, *pairs):
    subs = "".join(f'<subfield code="{code}">{escape(value)}</subfield>' for code, value in pairs)
    return f'<datafield tag="{tag}" ind1=" " ind2=" ">{subs}</datafield>'


def record(*fields, identifier="000123", title="Krabat", namespace=True):
    ns = ' xmlns="http://www.loc.gov/MARC21/slim"' if namespace else ""
    title_field = field("245", ("a", title)) if title else ""
    return (
        f'<record{ns}><controlfield tag="001">{identifier}</controlfield>'
        f"{title_field}{''.join(fields)}</record>"
    )


def response(*records, total=None):
    hits = "".join(f"<record><recordData>{item}</recordData></record>" for item in records)
    return (
        '<searchRetrieveResponse xmlns="http://www.loc.gov/zing/srw/">'
        f"<numberOfRecords>{len(records) if total is None else total}</numberOfRecords>"
        f"<records>{hits}</records></searchRetrieveResponse>"
    ).encode()


@pytest.fixture
def provider():
    get_metadata_cache().clear()
    result = get_provider("dnb")
    result.session = Mock()
    return result


def serve(provider, *bodies):
    provider.session.get.side_effect = [Mock(content=body) for body in bodies]


def search(provider, body):
    serve(provider, body)
    return provider.search(MetadataSearchOptions(query="Krabat"))


def test_registered_without_authentication():
    entry = next((p for p in list_providers() if p["name"] == "dnb"), None)
    assert entry == {
        "name": "dnb",
        "display_name": "Deutsche Nationalbibliothek",
        "requires_auth": False,
    }
    assert get_provider("dnb").is_available()


@pytest.mark.parametrize("count", [1, 2])
def test_unwraps_sru_recorddata_and_each_marc_record(provider, count):
    books = search(provider, response(*(record(identifier=str(i)) for i in range(count))))
    assert [book.provider_id for book in books] == [str(i) for i in range(count)]
    assert all(book.title == "Krabat" for book in books)


def test_sru_wrapper_is_not_itself_a_marc_record(provider):
    books = search(provider, response(record(title=None), record(identifier="000042")))
    assert [book.provider_id for book in books] == ["000042"]


@pytest.mark.parametrize("tag", ["100", "700"])
def test_reads_all_relators_not_just_the_first(provider, tag):
    books = search(
        provider, response(record(field(tag, ("a", "Autor, Änne"), ("4", "art"), ("4", "aut"))))
    )
    assert books[0].authors == ["Autor, Änne"]


def test_translators_and_illustrators_are_not_authors(provider):
    books = search(
        provider,
        response(
            record(
                field("100", ("a", "Autor, Änne")),
                field("700", ("a", "Übersetzer, Uwe"), ("4", "trl")),
                field("700", ("a", "Zeichner, Eva"), ("4", "ill")),
                field("700", ("a", "Unklar, Otto")),
                field("700", ("a", "Autor, Änne"), ("4", "aut")),
            )
        ),
    )
    assert books[0].authors == ["Autor, Änne"]


@pytest.mark.parametrize(
    "main,rest,title,subtitle",
    [
        ("Dr. Jekyll", "und Mr. Hyde", "Dr. Jekyll und Mr. Hyde", None),
        ("Das Seehaus", "Roman", "Das Seehaus", "Roman"),
        ("Das Seehaus.", "ein Roman", "Das Seehaus", "ein Roman"),
        ("Verhältnis", "über Grenzen", "Verhältnis über Grenzen", None),
    ],
)
def test_case_sensitive_subtitle_rule(provider, main, rest, title, subtitle):
    raw = record(title=None, identifier="1").replace(
        "</record>", field("245", ("a", main), ("b", rest)) + "</record>"
    )
    book = search(provider, response(raw))[0]
    assert (book.title, book.subtitle) == (title, subtitle)


@pytest.mark.parametrize("isbn", ["9783641216177", "3641216176"])
def test_matches_every_020a_even_separate_isbn10_field(provider, isbn):
    serve(
        provider,
        response(
            record(
                field("020", ("a", "9783641216177")),
                field("020", ("a", "3641216176")),
            )
        ),
    )
    book = provider.search_by_isbn(isbn)
    assert book is not None
    assert (book.isbn_13, book.isbn_10) == ("9783641216177", "3641216176")
    call = provider.session.get.call_args
    assert call.args[0] == "https://services.dnb.de/sru/dnb"
    assert call.kwargs["params"]["query"] == f"num={isbn}"
    assert call.kwargs["params"]["maximumRecords"] == 1
    assert call.kwargs["params"]["recordSchema"] == "MARC21-xml"
    assert call.kwargs["params"]["version"] == "1.1"
    assert call.kwargs["timeout"] == 15
    assert call.kwargs["verify"] is True


def test_normalizes_hyphenated_isbn(provider):
    serve(provider, response(record(field("020", ("a", "978-3-641-21617-7 (E-Book)")))))
    assert provider.search_by_isbn("978-3-641-21617-7").isbn_13 == "9783641216177"


def test_rejects_wrong_isbn_even_if_sru_returns_record(provider):
    serve(provider, response(record(field("020", ("a", "9783641216177")))))
    assert provider.search_by_isbn("9783492612418") is None


@pytest.mark.parametrize("isbn", ["", "not-an-isbn", "1234", '9783641216177" or tit=*'])
def test_invalid_isbn_does_not_make_request(provider, isbn):
    assert provider.search_by_isbn(isbn) is None
    provider.session.get.assert_not_called()


def test_all_text_is_nfc_and_title_preserves_non_bmp_characters(provider):
    book = search(
        provider,
        response(
            record(
                field("100", ("a", "Mu\u0308ller, Jo\u0308rg")),
                field("264", ("b", "Bu\u0308cher")),
                field("520", ("a", "U\u0308ber Gro\u0308ße")),
                title="Verha\u0308ltnis 🦉",
            )
        ),
    )[0]
    assert book.title == "Verhältnis 🦉"
    assert book.authors == ["Müller, Jörg"]
    assert book.publisher == "Bücher"
    assert book.description == "Über Größe"
    assert all(
        unicodedata.is_normalized("NFC", value)
        for value in [
            book.title,
            *book.authors,
            book.publisher,
            book.description,
        ]
    )


def test_removes_marc_sort_controls_but_decodes_other_entities(provider):
    raw = response(record(title="PLACEHOLDER")).replace(
        b"PLACEHOLDER", b"&#152;Das&#156; &amp; &#xDC;ber"
    )
    assert search(provider, raw)[0].title == "Das & Über"


@pytest.mark.parametrize(
    "extent,pages",
    [
        ("1 Online-Ressource", None),
        ("1 Online-Ressource (352 Seiten)", "352"),
        ("352 Seiten", "352"),
        ("1.024 S.", "1024"),
        ("0 Seiten", None),
        ("999999 Seiten", None),
        ("3 CDs", None),
    ],
)
def test_300_only_reports_actual_page_counts(provider, extent, pages):
    book = search(provider, response(record(field("300", ("a", extent)))))[0]
    values = [item.value for item in book.display_fields if item.label == "Pages"]
    assert values == ([pages] if pages else [])


def test_maps_publication_language_summary_genres_and_series(provider):
    book = search(
        provider,
        response(
            record(
                field("264", ("a", "München"), ("b", "Piper"), ("c", "[2024]")),
                field("041", ("a", "ger"), ("h", "eng")),
                field("520", ("a", "Ein Buch.")),
                field("655", ("a", "Fantasy")),
                field("490", ("a", "Die Reihe"), ("v", "Band 2")),
            )
        ),
    )[0]
    assert book.publisher == "Piper"
    assert book.publish_year == 2024
    assert book.language == "de"
    assert book.description == "Ein Buch."
    assert book.genres == ["Fantasy"]
    assert (book.series_name, book.series_position) == ("Die Reihe", 2)
    assert book.source_url == "https://d-nb.info/000123"
    assert book.cover_url is None


def test_language_falls_back_to_008_and_series_to_830(provider):
    raw = record(field("830", ("a", "Andere Reihe")), title="Buch").replace(
        "</record>", '<controlfield tag="008">' + " " * 35 + "ger" + "</controlfield></record>"
    )
    book = search(provider, response(raw))[0]
    assert book.language == "de"
    assert book.series_name == "Andere Reihe"


def test_title_author_search_precedes_title_only_and_filters_wrong_authors(provider):
    serve(
        provider,
        response(),
        response(
            record(identifier="1"),
            record(field("100", ("a", "Falsch, Fritz")), identifier="2"),
            record(field("100", ("a", "Preußler, Otfried")), identifier="3"),
        ),
    )
    books = provider.search(
        MetadataSearchOptions(
            query="Krabat", search_type=SearchType.TITLE, fields={"author": "Otfried Preußler"}
        )
    )
    queries = [call.kwargs["params"]["query"] for call in provider.session.get.call_args_list]
    assert queries == ['tit="Krabat" and per="Otfried Preußler"', 'tit="Krabat"']
    assert [book.provider_id for book in books] == ["3"]


def test_explicit_title_author_fields_override_general_query(provider):
    serve(provider, response(record(field("100", ("a", "Preußler, Otfried")))))
    books = provider.search(
        MetadataSearchOptions(
            query="ignored",
            fields={
                "title": "Krabat",
                "author": "Otfried Preußler",
            },
        )
    )
    assert books[0].title == "Krabat"
    assert provider.session.get.call_args.kwargs["params"]["query"] == (
        'tit="Krabat" and per="Otfried Preußler"'
    )


def test_cql_quotes_and_backslashes_are_escaped(provider):
    serve(provider, response())
    provider.search(MetadataSearchOptions(query='A "title" \\ B', search_type=SearchType.TITLE))
    assert provider.session.get.call_args.kwargs["params"]["query"] == 'tit="A \\"title\\" \\\\ B"'


@pytest.mark.parametrize(
    "search_type,index", [(SearchType.AUTHOR, "per"), (SearchType.GENERAL, "woe")]
)
def test_search_types_use_dnb_indexes(provider, search_type, index):
    serve(provider, response())
    provider.search(MetadataSearchOptions(query="Test", search_type=search_type))
    assert provider.session.get.call_args.kwargs["params"]["query"] == f'{index}="Test"'


def test_pagination_uses_bounded_page_size_and_sru_totals(provider):
    serve(provider, response(record(), total=250))
    result = provider.search_paginated(MetadataSearchOptions(query="Krabat", limit=150, page=2))
    params = provider.session.get.call_args.kwargs["params"]
    assert (params["maximumRecords"], params["startRecord"]) == (100, 101)
    assert (result.page, result.total_found, result.has_more) == (2, 250, True)


def test_language_filter_is_applied_to_results(provider):
    serve(
        provider,
        response(
            record(field("041", ("a", "eng")), identifier="1"),
            record(field("041", ("a", "ger")), identifier="2"),
        ),
    )
    books = provider.search(MetadataSearchOptions(query="Krabat", language="de"))
    assert [book.provider_id for book in books] == ["2"]


def test_get_book_uses_idn_and_confirms_returned_identifier(provider):
    serve(
        provider,
        response(record(identifier="1188186116")),
        response(record(identifier="different")),
    )
    assert provider.get_book("1188186116").provider_id == "1188186116"
    assert provider.get_book("1188186117") is None
    assert provider.session.get.call_args_list[0].kwargs["params"]["query"] == 'idn="1188186116"'


@pytest.mark.parametrize(
    "body",
    [
        b"broken",
        b"<html/>",
        b'<searchRetrieveResponse xmlns="http://www.loc.gov/zing/srw/"><diagnostics/></searchRetrieveResponse>',
        b'<!DOCTYPE x [<!ENTITY leak SYSTEM "file:///no-such-file">]><x>&leak;</x>',
    ],
)
def test_bad_xml_and_sru_diagnostics_fail_safely_without_poisoning_cache(provider, body):
    serve(provider, body, response(record()))
    options = MetadataSearchOptions(query="Krabat")
    assert provider.search(options) == []
    assert provider.search(options)[0].title == "Krabat"


@pytest.mark.parametrize("error", [requests.Timeout, requests.ConnectionError, requests.HTTPError])
def test_network_failure_is_not_cached(provider, error):
    provider.session.get.side_effect = [error(), Mock(content=response(record()))]
    options = MetadataSearchOptions(query="Krabat")
    assert provider.search(options) == []
    assert provider.search(options)[0].title == "Krabat"


def test_http_status_failure_is_not_parsed(provider):
    reply = Mock(content=response(record()))
    reply.raise_for_status.side_effect = requests.HTTPError()
    provider.session.get.return_value = reply
    assert provider.search(MetadataSearchOptions(query="Krabat")) == []


def test_successful_queries_are_cached(provider):
    serve(provider, response(record()))
    options = MetadataSearchOptions(query="Krabat")
    assert provider.search(options)[0].title == "Krabat"
    assert provider.search(options)[0].title == "Krabat"
    assert provider.session.get.call_count == 1


def test_empty_query_does_not_request_the_entire_catalogue(provider):
    assert provider.search(MetadataSearchOptions(query="  ")) == []
    provider.session.get.assert_not_called()


def test_dnb_precedes_other_enabled_providers_without_overwriting_user_choice(monkeypatch):
    import shelfmark.metadata_providers as registry
    from shelfmark.core.config import config

    monkeypatch.setattr(registry, "is_provider_enabled", lambda name: True)
    assert registry.get_enabled_providers()[0] == "dnb"

    monkeypatch.setattr(config, "refresh", lambda: None)
    monkeypatch.setattr(config, "get", lambda key, default="": "hardcover")
    save = Mock()
    monkeypatch.setattr("shelfmark.core.settings_registry.save_config_file", save)
    registry.sync_metadata_provider_selection()
    save.assert_not_called()


def test_settings_keep_dnb_opt_in():
    from shelfmark.metadata_providers.dnb import dnb_settings

    enabled = next(item for item in dnb_settings() if item.key == "DNB_ENABLED")
    assert enabled.default is False


@pytest.mark.parametrize("search_type", [SearchType.AUTHOR, SearchType.TITLE])
def test_author_search_accepts_a_surname_without_accepting_unrelated_people(provider, search_type):
    serve(
        provider,
        response(
            record(field("100", ("a", "Preußler, Otfried")), identifier="1"),
            record(field("100", ("a", "Falsch, Fritz")), identifier="2"),
        ),
        response(),
    )
    options = MetadataSearchOptions(
        query="Preußler" if search_type == SearchType.AUTHOR else "Krabat",
        search_type=search_type,
        fields={"author": "Preußler"},
    )
    assert [book.provider_id for book in provider.search(options)] == ["1"]
