"""A release search uses one author, not every contributor the book lists.

`bookTransformers.ts` joins a book's authors with ", " for display, and the release modal
sends that display string back as the `author` request parameter; several providers set
`search_author` from equally joined text. `pick_search_author` returned it verbatim while
the authors[] fallback beside it deliberately narrowed to the first name, so a book with
translators was searched for as

    Blindness Jose Saramago, Giovanni Pontiero, <persian translator>

which matches nothing on Anna's Archive. The bypass succeeds, the search returns empty,
and the user is told the book has no releases. Reported on issue #1252.
"""

from shelfmark.core.search_plan import build_release_search_plan, first_author, pick_search_author
from shelfmark.metadata_providers import BookMetadata

# The exact string from the report, as the frontend would join it.
JOINED = "José Saramago, Giovanni Pontiero, زهره افتخاری"


def _book(**kwargs) -> BookMetadata:
    base = {
        "provider": "manual",
        "provider_id": "x",
        "title": "Blindness",
        "search_title": "Blindness",
    }
    return BookMetadata(**{**base, **kwargs})


def _queries(book: BookMetadata) -> list[str]:
    plan = build_release_search_plan(book, languages=["en"])
    return [f"{v.title} {plan.author}".strip() for v in plan.grouped_title_variants]


def test_search_author_is_narrowed_to_the_first_author():
    book = _book(search_author=JOINED, authors=[a.strip() for a in JOINED.split(",")])

    assert _queries(book) == ["Blindness José Saramago"]


def test_the_authors_fallback_still_narrows():
    """Unchanged behaviour, kept under test so the two paths cannot drift apart again."""
    book = _book(authors=[JOINED])

    assert _queries(book) == ["Blindness José Saramago"]


def test_a_single_author_is_left_alone():
    book = _book(search_author="José Saramago")

    assert _queries(book) == ["Blindness José Saramago"]


def test_a_book_with_no_author_still_searches_by_title():
    book = _book(authors=[])

    assert _queries(book) == ["Blindness"]


def test_last_first_collapses_to_the_surname():
    """Still a usable search term, and what the fallback has always done."""
    assert first_author("Saramago, José") == "Saramago"


def test_first_author_trims_and_tolerates_odd_input():
    assert first_author("  José Saramago  ") == "José Saramago"
    assert first_author("") == ""
    assert first_author(",") == ""
    assert first_author("José Saramago,") == "José Saramago"


def test_manual_query_is_untouched():
    """A manual query is the user's own words; narrowing it would rewrite their search."""
    book = _book(search_author=JOINED)
    plan = build_release_search_plan(book, manual_query=JOINED)

    assert plan.manual_query == JOINED
    assert plan.author == ""


def test_irc_query_uses_one_author_too():
    """The IRC source builds its own query and had the same verbatim preference.

    It now posts the surname rather than the full name - a search bot ANDs its terms
    against a filename - but the invariant this pins is unchanged: one contributor's
    name reaches the query, never the whole credit list.
    """
    from shelfmark.release_sources.irc.source import IRCReleaseSource

    book = _book(search_author=JOINED, authors=[a.strip() for a in JOINED.split(",")])

    query = IRCReleaseSource()._build_query(book, build_release_search_plan(book))

    assert query == "Blindness Saramago"


def test_a_blank_leading_contributor_falls_back_instead_of_dropping_the_author():
    """`authors.join(', ')` does not drop an empty entry, so the join can start with ",".

    Narrowing that to "" and stopping would search by title alone - losing an author the
    book was holding all along, in authors[], one line below.
    """
    book = _book(search_author=", Giovanni Pontiero", authors=["", "Giovanni Pontiero"])

    assert _queries(book) == ["Blindness Giovanni Pontiero"]


def test_a_blank_leading_contributor_does_not_strand_the_irc_query_either():
    from shelfmark.release_sources.irc.source import IRCReleaseSource

    book = _book(search_author=", Giovanni Pontiero", authors=["", "Giovanni Pontiero"])

    query = IRCReleaseSource()._build_query(book, build_release_search_plan(book))

    assert query == "Blindness Pontiero"


def test_an_all_blank_author_leaves_a_clean_title_only_query():
    """No trailing space, no empty part: the query is just the title."""
    from shelfmark.release_sources.irc.source import IRCReleaseSource

    book = _book(search_author=", ,", authors=["", " "])

    assert _queries(book) == ["Blindness"]
    assert IRCReleaseSource()._build_query(book, build_release_search_plan(book)) == "Blindness"


def test_a_bare_string_in_authors_is_not_iterated_character_by_character():
    """The IRC source guarded against this before it shared `pick_search_author`."""
    book = _book(authors="José Saramago")

    assert pick_search_author(book) == "José Saramago"


def test_the_producers_narrow_before_the_field_is_ever_set():
    """The two places that join also hold the split, so they set search_author from it.

    Narrowing downstream cannot tell a comma that joins contributors from one inside a
    single name; here the information is still present. See the same-named finding.
    """
    from shelfmark.release_sources import BrowseRecord, browse_record_to_book_metadata

    record = BrowseRecord(id="abc", title="Blindness", source="direct_download")
    book = browse_record_to_book_metadata(record, author_override=JOINED)

    assert book.search_author == "José Saramago"
    assert book.authors == [a.strip() for a in JOINED.split(",")]
