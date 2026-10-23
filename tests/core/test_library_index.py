"""Tests for the library ownership facade: matcher, routing, caching and fail-open."""

from __future__ import annotations

from typing import Any

import pytest

from shelfmark.core import library_index
from shelfmark.core.library_providers import LibraryEntry
from shelfmark.core.text_match import isbn_variants
from shelfmark.metadata_providers import BookMetadata


def _book(**overrides: Any) -> BookMetadata:
    fields: dict[str, Any] = {
        "provider": "hardcover",
        "provider_id": "446681",
        "title": "Dungeon Crawler Carl",
        "authors": ["Matt Dinniman"],
    }
    fields.update(overrides)
    return BookMetadata(**fields)


def _entry(
    *words: str,
    isbns: frozenset[str] = frozenset(),
    external_ids: frozenset[tuple[str, str]] = frozenset(),
) -> LibraryEntry:
    return LibraryEntry(frozenset(words), isbns, frozenset(), external_ids)


_DCC_ENTRY = _entry("dungeon", "crawler", "carl", "matt", "dinniman")


class _Provider:
    def __init__(
        self,
        name: str,
        content_types: set[str],
        entries: list[LibraryEntry] | None = None,
        *,
        enabled: bool = True,
        error: Exception | None = None,
        token: object | None = None,
    ) -> None:
        self.name = name
        self.display_name = name.title()
        self.content_types = frozenset(content_types)
        self.entries = entries or []
        self.enabled = enabled
        self.error = error
        self.token = token
        self.fetches = 0

    def is_enabled(self) -> bool:
        return self.enabled

    def describe(self) -> str:
        return f"{self.display_name} (test)"

    def fingerprint(self) -> object | None:
        return self.token

    def fetch_entries(self) -> list[LibraryEntry]:
        self.fetches += 1
        if self.error is not None:
            raise self.error
        return list(self.entries)


@pytest.fixture
def providers(monkeypatch: pytest.MonkeyPatch) -> list[_Provider]:
    registered: list[_Provider] = []
    monkeypatch.setattr(library_index, "all_providers", lambda overrides=None: list(registered))
    monkeypatch.setattr(library_index, "_cache", {})
    return registered


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    now = [1000.0]
    monkeypatch.setattr(library_index.time, "monotonic", lambda: now[0])
    return now


def _warnings(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    messages: list[str] = []
    monkeypatch.setattr(
        library_index.logger, "warning", lambda msg, *args: messages.append(msg % args)
    )
    return messages


# --------------------------------------------------------------------------- #
# ISBN forms
# --------------------------------------------------------------------------- #
def test_isbn_variants_add_the_isbn13_form_of_an_isbn10() -> None:
    assert isbn_variants("0-593-82024-X") == {"059382024X", "9780593820247"}
    assert isbn_variants("978-0-593-82024-7") == {"9780593820247"}
    assert isbn_variants(None) == frozenset()
    assert isbn_variants("n/a") == frozenset()


# --------------------------------------------------------------------------- #
# Pure matcher
# --------------------------------------------------------------------------- #
def test_no_entries_never_match() -> None:
    assert library_index.book_matches_entries(_book(), []) is False


def test_isbn13_exact_match() -> None:
    entries = [_entry("unrelated", isbns=frozenset({"9780593820247"}))]
    book = _book(title="Different Title", authors=["Someone Else"], isbn_13="978-0-593-82024-7")

    assert library_index.book_matches_entries(book, entries) is True


def test_isbn10_book_matches_isbn13_entry() -> None:
    entries = [_entry("unrelated", isbns=frozenset({"9780593820247"}))]
    book = _book(title="Different Title", authors=["Someone Else"], isbn_10="059382024X")

    assert library_index.book_matches_entries(book, entries) is True


def test_isbn13_book_matches_entry_indexed_from_an_isbn10() -> None:
    entries = [_entry("unrelated", isbns=isbn_variants("059382024X"))]
    book = _book(title="Different Title", authors=["Someone Else"], isbn_13="9780593820247")

    assert library_index.book_matches_entries(book, entries) is True


def test_external_id_match_requires_the_same_provider() -> None:
    entries = [_entry("unrelated", external_ids=frozenset({("hardcover", "446681")}))]
    book = _book(title="Different Title", authors=["Someone Else"])

    assert library_index.book_matches_entries(book, entries) is True
    assert library_index.book_matches_entries(book, [_entry("x")]) is False
    other_provider = _book(provider="googlebooks", title="Different", authors=["Else"])
    assert library_index.book_matches_entries(other_provider, entries) is False


def test_fuzzy_title_and_author_surname_match() -> None:
    assert library_index.book_matches_entries(_book(), [_DCC_ENTRY]) is True


def test_near_miss_title_fails() -> None:
    sequel = _book(title="Dungeon Crawler Carl 2: Carl's Doomsday Scenario")

    assert library_index.book_matches_entries(sequel, [_DCC_ENTRY]) is False


def test_right_title_wrong_author_fails() -> None:
    assert library_index.book_matches_entries(_book(authors=["Andy Weir"]), [_DCC_ENTRY]) is False


def test_search_fields_take_precedence_over_display_fields() -> None:
    book = _book(
        title="Dungeon Crawler Carl: A LitRPG Adventure", search_title="Dungeon Crawler Carl"
    )

    assert library_index.book_matches_entries(book, [_DCC_ENTRY]) is True


# --------------------------------------------------------------------------- #
# Routing
# --------------------------------------------------------------------------- #
def test_ebook_request_consults_only_the_ebook_provider(providers: list[_Provider]) -> None:
    audio = _Provider("audio", {"audiobook"}, [_DCC_ENTRY])
    ebook = _Provider("ebook", {"ebook"}, [])
    providers.extend([audio, ebook])

    assert library_index.is_in_library(_book(), "ebook") is False
    assert (audio.fetches, ebook.fetches) == (0, 1)

    assert library_index.is_in_library(_book(), "audiobook") is True
    assert (audio.fetches, ebook.fetches) == (1, 1)


def test_no_content_type_consults_every_enabled_provider(providers: list[_Provider]) -> None:
    audio = _Provider("audio", {"audiobook"}, [])
    ebook = _Provider("ebook", {"ebook"}, [_DCC_ENTRY])
    providers.extend([audio, ebook])

    assert library_index.is_in_library(_book()) is True
    assert (audio.fetches, ebook.fetches) == (1, 1)


def test_disabled_provider_is_not_consulted(providers: list[_Provider]) -> None:
    disabled = _Provider("ebook", {"ebook"}, [_DCC_ENTRY], enabled=False)
    providers.append(disabled)

    assert library_index.is_in_library(_book(), "ebook") is False
    assert disabled.fetches == 0


def test_any_provider_enabled(providers: list[_Provider]) -> None:
    assert library_index.any_provider_enabled() is False

    providers.append(_Provider("ebook", {"ebook"}, enabled=False))
    assert library_index.any_provider_enabled() is False

    providers.append(_Provider("audio", {"audiobook"}))
    assert library_index.any_provider_enabled() is True


# --------------------------------------------------------------------------- #
# Fail-open + cache
# --------------------------------------------------------------------------- #
def test_provider_error_fails_open_without_a_cache(
    providers: list[_Provider], monkeypatch: pytest.MonkeyPatch
) -> None:
    providers.append(_Provider("ebook", {"ebook"}, error=OSError("no such file")))
    warnings = _warnings(monkeypatch)

    assert library_index.is_in_library(_book(), "ebook") is False
    assert warnings == ["library check: Ebook (test) unavailable (no such file); failing open"]


def test_provider_error_keeps_answering_from_the_stale_cache(
    providers: list[_Provider], monkeypatch: pytest.MonkeyPatch, clock: list[float]
) -> None:
    provider = _Provider("ebook", {"ebook"}, [_DCC_ENTRY])
    providers.append(provider)
    assert library_index.is_in_library(_book(), "ebook") is True

    provider.error = RuntimeError("boom")
    clock[0] += library_index._CACHE_TTL_SECONDS + 1
    warnings = _warnings(monkeypatch)

    assert library_index.is_in_library(_book(), "ebook") is True
    assert provider.fetches == 2
    assert len(warnings) == 1


def test_a_failing_library_is_tried_once_per_search_not_once_per_result(
    providers: list[_Provider], monkeypatch: pytest.MonkeyPatch, clock: list[float]
) -> None:
    provider = _Provider("ebook", {"ebook"}, [_DCC_ENTRY])
    providers.append(provider)
    assert library_index.is_in_library(_book(), "ebook") is True

    provider.error = RuntimeError("boom")
    clock[0] += library_index._CACHE_TTL_SECONDS + 1
    warnings = _warnings(monkeypatch)

    for _ in range(40):  # one search page, every result asks
        assert library_index.is_in_library(_book(), "ebook") is True

    assert provider.fetches == 2  # the first index, then one failed attempt
    assert len(warnings) == 1


def test_a_failing_library_with_no_cache_is_not_retried_for_every_result(
    providers: list[_Provider], clock: list[float]
) -> None:
    provider = _Provider("ebook", {"ebook"}, error=OSError("no such file"))
    providers.append(provider)

    for _ in range(10):
        assert library_index.is_in_library(_book(), "ebook") is False

    assert provider.fetches == 1


def test_a_failing_library_is_retried_once_the_back_off_has_passed(
    providers: list[_Provider], clock: list[float]
) -> None:
    provider = _Provider("ebook", {"ebook"}, [_DCC_ENTRY], error=OSError("down"))
    providers.append(provider)
    assert library_index.is_in_library(_book(), "ebook") is False

    provider.error = None
    clock[0] += library_index._FAILURE_BACKOFF_SECONDS - 1
    assert library_index.is_in_library(_book(), "ebook") is False
    assert provider.fetches == 1

    clock[0] += 2
    assert library_index.is_in_library(_book(), "ebook") is True
    assert provider.fetches == 2


def test_entries_are_cached_until_the_ttl_expires(
    providers: list[_Provider], clock: list[float]
) -> None:
    provider = _Provider("ebook", {"ebook"}, [_DCC_ENTRY])
    providers.append(provider)

    library_index.is_in_library(_book(), "ebook")
    library_index.is_in_library(_book(), "ebook")
    assert provider.fetches == 1

    clock[0] += library_index._CACHE_TTL_SECONDS - 1
    library_index.is_in_library(_book(), "ebook")
    assert provider.fetches == 1

    clock[0] += 2
    library_index.is_in_library(_book(), "ebook")
    assert provider.fetches == 2


def test_fingerprint_change_refreshes_before_the_ttl(
    providers: list[_Provider], clock: list[float]
) -> None:
    provider = _Provider("ebook", {"ebook"}, [], token=1.0)
    providers.append(provider)

    assert library_index.is_in_library(_book(), "ebook") is False
    clock[0] += 5
    library_index.is_in_library(_book(), "ebook")
    assert provider.fetches == 1

    provider.entries = [_DCC_ENTRY]
    provider.token = 2.0
    assert library_index.is_in_library(_book(), "ebook") is True
    assert provider.fetches == 2


def test_cache_is_kept_per_provider(providers: list[_Provider]) -> None:
    audio = _Provider("audio", {"audiobook"}, [])
    ebook = _Provider("ebook", {"ebook"}, [_DCC_ENTRY])
    providers.extend([audio, ebook])

    assert library_index.is_in_library(_book(), "audiobook") is False
    assert library_index.is_in_library(_book(), "ebook") is True
    assert library_index.is_in_library(_book(), "audiobook") is False
    assert (audio.fetches, ebook.fetches) == (1, 1)


# --------------------------------------------------------------------------- #
# Settings test button
# --------------------------------------------------------------------------- #
def test_connection_reports_the_count_and_primes_the_cache(providers: list[_Provider]) -> None:
    provider = _Provider("ebook", {"ebook"}, [_DCC_ENTRY, _entry("other")])
    providers.append(provider)

    result = library_index.test_connection("ebook", None)

    assert result == {"success": True, "message": "Ebook (test): indexed 2 item(s)."}
    assert library_index.is_in_library(_book(), "ebook") is True
    assert provider.fetches == 1


def test_connection_reports_provider_errors(providers: list[_Provider]) -> None:
    providers.append(_Provider("ebook", {"ebook"}, error=OSError("no such file")))

    result = library_index.test_connection("ebook", None)

    assert result == {"success": False, "message": "Ebook (test): no such file"}


def test_connection_rejects_unknown_providers(providers: list[_Provider]) -> None:
    result = library_index.test_connection("nope", None)

    assert result == {"success": False, "message": "Unknown library provider: nope"}


def test_unsaved_form_values_override_saved_settings_for_test_connection(tmp_path):
    from shelfmark.core.library_providers import setting
    from shelfmark.core.library_providers.calibre import CalibreLibrary

    class _Saved:
        def get(self, key, default=None, user_id=None):
            return {"CALIBRE_LIBRARY_DB_PATH": "/saved/metadata.db"}.get(key, default)

    assert (
        setting(_Saved(), "CALIBRE_LIBRARY_DB_PATH", "", {"CALIBRE_LIBRARY_DB_PATH": "/form/x.db"})
        == "/form/x.db"
    )
    assert (
        setting(_Saved(), "CALIBRE_LIBRARY_DB_PATH", "", {"CALIBRE_LIBRARY_DB_PATH": ""})
        == "/saved/metadata.db"
    )
    assert setting(_Saved(), "CALIBRE_LIBRARY_DB_PATH", "", None) == "/saved/metadata.db"

    provider = CalibreLibrary({"CALIBRE_LIBRARY_DB_PATH": str(tmp_path / "form.db")})
    assert provider.describe().endswith("form.db")


def test_ownership_reports_only_formats_with_an_enabled_library(providers):
    owned = LibraryEntry(
        frozenset({"dungeon", "crawler", "carl"}),
        frozenset({"9780593820247"}),
        frozenset(),
        frozenset(),
    )
    providers.append(_Provider("calibre", {"ebook"}, [owned]))
    providers.append(_Provider("audiobooks", {"audiobook"}, [], enabled=False))

    assert library_index.ownership(_book(isbn_13="9780593820247")) == {"ebook": "owned"}
    assert library_index.ownership(_book(isbn_13="9999999999999", title="Something Else")) == {
        "ebook": None
    }


def test_ownership_is_none_when_no_library_check_is_enabled(providers):
    providers.append(_Provider("calibre", {"ebook"}, [], enabled=False))

    assert library_index.ownership(_book()) is None


def test_ownership_covers_both_formats_when_both_libraries_are_enabled(providers):
    entry = LibraryEntry(
        frozenset({"dungeon", "crawler", "carl"}),
        frozenset({"9780593820247"}),
        frozenset(),
        frozenset(),
    )
    providers.append(_Provider("calibre", {"ebook"}, []))
    providers.append(_Provider("audiobooks", {"audiobook"}, [entry]))

    assert library_index.ownership(_book(isbn_13="9780593820247")) == {
        "ebook": None,
        "audiobook": "owned",
    }


def _shelf_entry(title_tokens: set[str], context: set[str] | None = None) -> LibraryEntry:
    context = context or set()
    return LibraryEntry(
        frozenset(title_tokens | context),
        frozenset(),
        frozenset(),
        frozenset(),
        frozenset(title_tokens),
        frozenset(context),
    )


def test_a_longer_shelf_title_naming_another_work_is_not_a_match() -> None:
    # "Dune" is a subset of "Dune Messiah", which is a different book.
    shelf = [_shelf_entry({"dune", "messiah"}, {"frank", "herbert"})]
    book = _book(title="Dune", authors=["Frank Herbert"])

    assert library_index.match_entries(book, shelf) is None


def test_the_series_and_volume_a_shelf_title_repeats_do_not_block_a_match() -> None:
    # Calibre titles often carry the series and its number: "Alex Cross 25: Cross Kill".
    shelf = [_shelf_entry({"alex", "cross", "25", "kill"}, {"alex", "cross", "james", "patterson"})]
    book = _book(title="Cross Kill", authors=["James Patterson"])

    assert library_index.match_entries(book, shelf) == "owned"


def test_an_edition_word_does_not_block_a_match() -> None:
    shelf = [_shelf_entry({"dune", "illustrated", "edition"}, {"frank", "herbert"})]

    assert (
        library_index.match_entries(_book(title="Dune", authors=["Frank Herbert"]), shelf)
        == "owned"
    )


def test_a_collection_reports_itself_as_a_collection() -> None:
    shelf = [_shelf_entry({"dungeon", "crawler", "carl", "books", "1", "6"}, {"matt", "dinniman"})]
    book = _book(title="Dungeon Crawler Carl", authors=["Matt Dinniman"])

    assert library_index.match_entries(book, shelf) == "collection"


def test_a_collection_word_the_search_title_carries_is_the_book_itself() -> None:
    shelf = [_shelf_entry({"the", "complete", "maus"}, {"art", "spiegelman"})]
    book = _book(title="The Complete Maus", authors=["Art Spiegelman"])

    assert library_index.match_entries(book, shelf) == "owned"


def test_a_megapack_reports_itself_as_a_collection() -> None:
    shelf = [_shelf_entry({"dungeon", "crawler", "carl", "megapack"}, {"matt", "dinniman"})]

    assert library_index.match_entries(_book(), shelf) == "collection"


def test_an_exact_shelf_title_outranks_a_collection_holding_the_same_book() -> None:
    collection = _shelf_entry({"dungeon", "crawler", "carl", "omnibus"}, {"matt", "dinniman"})
    single = _shelf_entry({"dungeon", "crawler", "carl"}, {"matt", "dinniman"})
    book = _book(title="Dungeon Crawler Carl", authors=["Matt Dinniman"])

    assert library_index.match_entries(book, [collection, single]) == "owned"


# --------------------------------------------------------------------------- #
# Fail closed for callers that act on the answer
# --------------------------------------------------------------------------- #
def test_a_library_that_suddenly_lists_nothing_keeps_answering_from_the_earlier_index(
    providers: list[_Provider], monkeypatch: pytest.MonkeyPatch, clock: list[float]
) -> None:
    provider = _Provider("audio", {"audiobook"}, [_DCC_ENTRY])
    providers.append(provider)
    assert library_index.is_in_library(_book(), "audiobook") is True

    provider.entries = []  # the server's answer changed shape and every item vanished
    clock[0] += library_index._CACHE_TTL_SECONDS + 1
    warnings = _warnings(monkeypatch)

    assert library_index.is_in_library(_book(), "audiobook") is True
    assert library_index.is_in_library(_book(), "audiobook", strict=True) is True
    assert provider.fetches == 2  # the second answer came from the back-off, not a refetch
    assert len(warnings) == 1
    assert "listed no items" in warnings[0]


def test_a_library_that_was_never_indexed_may_be_empty(providers: list[_Provider]) -> None:
    providers.append(_Provider("audio", {"audiobook"}, []))

    assert library_index.is_in_library(_book(), "audiobook") is False
    assert library_index.is_in_library(_book(), "audiobook", strict=True) is False


def test_a_strict_check_still_refuses_during_the_back_off(
    providers: list[_Provider], clock: list[float]
) -> None:
    provider = _Provider("ebook", {"ebook"}, error=OSError("mount gone"))
    providers.append(provider)
    with pytest.raises(library_index.LibraryUnavailableError):
        library_index.is_in_library(_book(), "ebook", strict=True)

    # Inside the back-off nothing is fetched, and the answer must not fall back to "not owned".
    with pytest.raises(library_index.LibraryUnavailableError):
        library_index.is_in_library(_book(), "ebook", strict=True)
    assert provider.fetches == 1


def test_a_strict_check_refuses_to_guess_when_a_library_is_down_and_nothing_is_cached(
    providers: list[_Provider],
) -> None:
    providers.append(_Provider("ebook", {"ebook"}, error=OSError("mount gone")))

    with pytest.raises(library_index.LibraryUnavailableError, match=r"Ebook \(test\)"):
        library_index.is_in_library(_book(), "ebook", strict=True)


def test_a_strict_check_still_answers_from_a_stale_cache(
    providers: list[_Provider], clock: list[float]
) -> None:
    provider = _Provider("ebook", {"ebook"}, [_DCC_ENTRY])
    providers.append(provider)
    assert library_index.is_in_library(_book(), "ebook", strict=True) is True

    provider.error = RuntimeError("boom")
    clock[0] += library_index._CACHE_TTL_SECONDS + 1

    assert library_index.is_in_library(_book(), "ebook", strict=True) is True


def test_a_down_library_leaves_the_badges_fail_open(providers: list[_Provider]) -> None:
    providers.append(_Provider("ebook", {"ebook"}, error=OSError("mount gone")))

    assert library_index.ownership(_book()) == {"ebook": None}


def test_require_ready_names_the_library_that_cannot_be_read(providers: list[_Provider]) -> None:
    providers.append(_Provider("audiobook", {"audiobook"}, [_DCC_ENTRY]))
    providers.append(_Provider("ebook", {"ebook"}, error=OSError("mount gone")))

    with pytest.raises(library_index.LibraryUnavailableError, match=r"Ebook \(test\)"):
        library_index.require_ready()


def test_require_ready_passes_once_every_enabled_library_has_been_read(
    providers: list[_Provider],
) -> None:
    providers.append(_Provider("ebook", {"ebook"}, [_DCC_ENTRY]))
    providers.append(_Provider("audiobook", {"audiobook"}, [_DCC_ENTRY]))

    library_index.require_ready()  # does not raise


def test_require_ready_ignores_a_disabled_library(providers: list[_Provider]) -> None:
    providers.append(_Provider("ebook", {"ebook"}, enabled=False, error=OSError("mount gone")))

    library_index.require_ready()  # does not raise


def test_require_ready_only_checks_the_libraries_for_the_content_type(
    providers: list[_Provider],
) -> None:
    providers.append(_Provider("ebook", {"ebook"}, error=OSError("no such file")))
    providers.append(_Provider("audio", {"audiobook"}, [_entry("carl")]))

    library_index.require_ready("audiobook")

    with pytest.raises(library_index.LibraryUnavailableError, match="Ebook"):
        library_index.require_ready("ebook")


def test_matching_tokenises_the_title_once_for_all_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    """The title does not change between entries, so it is split into words once."""
    from shelfmark.core import text_match

    calls: list[str | None] = []
    real = text_match.significant_tokens

    def counting(text: str | None) -> list[str]:
        calls.append(text)
        return real(text)

    monkeypatch.setattr(text_match, "significant_tokens", counting)
    entries = [_entry("unrelated", f"word{i}", "someone") for i in range(50)]

    assert library_index.match_entries(_book(), entries) is None
    assert len(calls) <= 2
