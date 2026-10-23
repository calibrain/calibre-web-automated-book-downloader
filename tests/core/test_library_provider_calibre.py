"""Tests for the read-only Calibre metadata.db library provider."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from shelfmark.core import library_index
from shelfmark.core.library_providers import calibre
from shelfmark.metadata_providers import BookMetadata

if TYPE_CHECKING:
    from collections.abc import Callable

_SCHEMA = """
CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT NOT NULL, uuid TEXT);
CREATE TABLE authors (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE books_authors_link (id INTEGER PRIMARY KEY, book INTEGER NOT NULL, author INTEGER NOT NULL);
CREATE TABLE series (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE books_series_link (id INTEGER PRIMARY KEY, book INTEGER NOT NULL, series INTEGER NOT NULL);
CREATE TABLE identifiers (id INTEGER PRIMARY KEY, book INTEGER NOT NULL, type TEXT NOT NULL, val TEXT NOT NULL);
"""

_BOOKS = [
    (1, "Dungeon Crawler Carl", "u1"),
    (2, "Good Omens", "u2"),
    (3, "Untitled Draft", "u3"),
    (4, "The Martian", "u4"),
]
_AUTHORS = [(1, "Matt Dinniman"), (2, "Terry Pratchett"), (3, "Neil Gaiman"), (4, "Andy Weir")]
_AUTHOR_LINKS = [(1, 1), (2, 2), (2, 3), (4, 4)]  # book 3 has no author
_SERIES = [(1, "Dungeon Crawler Carl")]
_SERIES_LINKS = [(1, 1)]
_IDENTIFIERS = [
    (1, "isbn", "9780593820247"),
    (1, "mobi-asin", "b08bkgyqxw"),
    (1, "hardcover-id", "446681"),
    (1, "google", "yEem0QEACAAJ"),
    (2, "isbn-10", "0060853980"),
    (2, "amazon", "B000FC0V3W"),
    (2, "goodreads", "12067"),
    (4, "isbn-13", "978-0-8041-3902-1"),
    (4, "openlibrary", "OL17091839W"),
    (4, "dnb-idn", "1061425843"),
]


def _make_library(path: Path, *, wal: bool = False) -> None:
    conn = sqlite3.connect(path)
    if wal:
        conn.execute("PRAGMA journal_mode=wal")
    conn.executescript(_SCHEMA)
    conn.executemany("INSERT INTO books(id, title, uuid) VALUES (?, ?, ?)", _BOOKS)
    conn.executemany("INSERT INTO authors(id, name) VALUES (?, ?)", _AUTHORS)
    conn.executemany("INSERT INTO books_authors_link(book, author) VALUES (?, ?)", _AUTHOR_LINKS)
    conn.executemany("INSERT INTO series(id, name) VALUES (?, ?)", _SERIES)
    conn.executemany("INSERT INTO books_series_link(book, series) VALUES (?, ?)", _SERIES_LINKS)
    conn.executemany("INSERT INTO identifiers(book, type, val) VALUES (?, ?, ?)", _IDENTIFIERS)
    conn.commit()
    conn.close()


def _configure(monkeypatch: pytest.MonkeyPatch, path: Path, *, enabled: bool = True) -> None:
    values: dict[str, Any] = {
        "CALIBRE_LIBRARY_DB_PATH": str(path),
        "LIBRARY_CHECK_CALIBRE_ENABLED": enabled,
    }
    monkeypatch.setattr(
        calibre.app_config,
        "get",
        lambda key, default=None, user_id=None: values.get(key, default),
    )
    monkeypatch.setattr(library_index, "_cache", {})


@pytest.fixture
def calibre_library(monkeypatch: pytest.MonkeyPatch) -> Callable[..., Path]:
    """Point the provider at a library, building it first unless ``create=False``."""

    def make(*, path: Path, create: bool = True, enabled: bool = True) -> Path:
        if create:
            _make_library(path)
        _configure(monkeypatch, path, enabled=enabled)
        return path

    return make


def _infos(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    messages: list[str] = []
    monkeypatch.setattr(calibre.logger, "info", lambda msg, *args: messages.append(msg % args))
    return messages


def _by_token(entries: list[Any], token: str) -> Any:
    matches = [entry for entry in entries if token in entry.tokens]
    assert len(matches) == 1, token
    return matches[0]


def _dcc() -> BookMetadata:
    return BookMetadata(
        provider="hardcover",
        provider_id="446681",
        title="Dungeon Crawler Carl",
        authors=["Matt Dinniman"],
    )


def test_indexes_titles_authors_series_and_identifiers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "metadata.db"
    _make_library(path)
    _configure(monkeypatch, path)

    entries = calibre.CalibreLibrary().fetch_entries()

    assert len(entries) == 4

    dcc = _by_token(entries, "dinniman")
    assert {"dungeon", "crawler", "carl", "matt"} <= dcc.tokens
    assert dcc.isbns == {"9780593820247"}
    assert dcc.asins == {"B08BKGYQXW"}
    assert dcc.external_ids == {("hardcover", "446681"), ("googlebooks", "yEem0QEACAAJ")}

    omens = _by_token(entries, "omens")
    assert {"pratchett", "gaiman"} <= omens.tokens
    assert omens.isbns == {"0060853980", "9780060853983"}
    assert omens.asins == {"B000FC0V3W"}
    assert omens.external_ids == frozenset()

    martian = _by_token(entries, "martian")
    assert martian.isbns == {"9780804139021"}
    assert martian.external_ids == {("openlibrary", "OL17091839W"), ("dnb", "1061425843")}

    draft = _by_token(entries, "draft")
    assert draft.tokens == {"untitled", "draft"}
    assert (draft.isbns, draft.asins, draft.external_ids) == (frozenset(), frozenset(), frozenset())


def test_reads_a_read_only_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "metadata.db"
    _make_library(path)
    path.chmod(0o444)
    _configure(monkeypatch, path)

    assert len(calibre.CalibreLibrary().fetch_entries()) == 4


def test_wal_reads_see_uncheckpointed_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "metadata.db"
    _make_library(path, wal=True)
    _configure(monkeypatch, path)
    infos = _infos(monkeypatch)

    writer = sqlite3.connect(path)
    writer.execute("PRAGMA wal_autocheckpoint=0")
    writer.execute("INSERT INTO books(id, title, uuid) VALUES (5, 'Fresh Arrival', 'u5')")
    writer.commit()
    try:
        assert (tmp_path / "metadata.db-wal").exists()
        entries = calibre.CalibreLibrary().fetch_entries()
    finally:
        writer.close()

    assert len(entries) == 5
    assert infos == []


@pytest.mark.skipif(os.geteuid() == 0, reason="root is not bound by directory permissions")
def test_wal_without_side_files_on_a_read_only_mount_uses_a_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lib_dir = tmp_path / "library"
    lib_dir.mkdir()
    path = lib_dir / "metadata.db"
    _make_library(path, wal=True)
    for suffix in ("-wal", "-shm"):
        (lib_dir / f"metadata.db{suffix}").unlink(missing_ok=True)
    _configure(monkeypatch, path)
    infos = _infos(monkeypatch)

    lib_dir.chmod(0o555)
    try:
        entries = calibre.CalibreLibrary().fetch_entries()
    finally:
        lib_dir.chmod(0o755)

    assert len(entries) == 4
    assert not (lib_dir / "metadata.db-wal").exists()
    assert len(infos) == 1
    assert "immutable snapshot" in infos[0]


def test_in_place_open_failure_falls_back_to_an_immutable_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "metadata.db"
    _make_library(path)
    _configure(monkeypatch, path)
    infos = _infos(monkeypatch)

    real_connect = sqlite3.connect
    uris: list[str] = []

    def fake_connect(database: str, **kwargs: Any) -> sqlite3.Connection:
        uris.append(database)
        if "immutable=1" not in database:
            raise sqlite3.OperationalError("attempt to write a readonly database")
        return real_connect(database, **kwargs)

    monkeypatch.setattr(calibre.sqlite3, "connect", fake_connect)

    assert len(calibre.CalibreLibrary().fetch_entries()) == 4
    assert [uri.endswith("?mode=ro") for uri in uris] == [True, False]
    assert uris[1].endswith("?mode=ro&immutable=1")
    assert "attempt to write a readonly database" in infos[0]


def test_locked_database_falls_back_to_a_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "metadata.db"
    _make_library(path)
    _configure(monkeypatch, path)
    monkeypatch.setattr(calibre, "_BUSY_TIMEOUT_SECONDS", 0.01)
    infos = _infos(monkeypatch)

    writer = sqlite3.connect(path, isolation_level=None)
    writer.execute("BEGIN EXCLUSIVE")
    writer.execute("INSERT INTO books(id, title, uuid) VALUES (5, 'Pending', 'u5')")
    try:
        entries = calibre.CalibreLibrary().fetch_entries()
    finally:
        writer.rollback()
        writer.close()

    assert len(entries) == 4
    assert "database is locked" in infos[0]


def test_unreadable_database_raises_and_the_facade_fails_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "metadata.db"
    _make_library(path)
    _configure(monkeypatch, path)

    def always_fail(database: str, **kwargs: Any) -> sqlite3.Connection:
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(calibre.sqlite3, "connect", always_fail)
    warnings: list[str] = []
    monkeypatch.setattr(
        library_index.logger, "warning", lambda msg, *args: warnings.append(msg % args)
    )

    with pytest.raises(sqlite3.OperationalError):
        calibre.CalibreLibrary().fetch_entries()
    assert library_index.is_in_library(_dcc(), "ebook") is False
    assert warnings == [
        f"library check: Calibre library at {path} unavailable (disk I/O error); failing open"
    ]


def test_missing_file_is_enabled_but_fetch_raises_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "missing.db"
    _configure(monkeypatch, path)
    provider = calibre.CalibreLibrary()

    assert provider.is_enabled() is True
    assert provider.describe() == f"Calibre library at {path}"
    assert provider.fingerprint() is None
    with pytest.raises(FileNotFoundError):
        provider.fetch_entries()

    result = library_index.test_connection("calibre", None)
    assert result["success"] is False
    assert str(path) in result["message"]
    assert library_index.is_in_library(_dcc(), "ebook") is False


def test_disabled_provider_is_not_consulted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "metadata.db"
    _make_library(path)
    _configure(monkeypatch, path, enabled=False)

    assert calibre.CalibreLibrary().is_enabled() is False
    assert library_index.any_provider_enabled() is False
    assert library_index.is_in_library(_dcc(), "ebook") is False


def test_enabled_provider_answers_ebook_lookups_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "metadata.db"
    _make_library(path)
    _configure(monkeypatch, path)

    assert library_index.any_provider_enabled() is True
    assert library_index.is_in_library(_dcc(), "ebook") is True
    assert library_index.is_in_library(_dcc(), "audiobook") is False
    assert library_index.test_connection("calibre", None) == {
        "success": True,
        "message": f"Calibre library at {path}: indexed 4 item(s).",
    }


def test_fingerprint_follows_the_database_and_wal_mtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "metadata.db"
    _make_library(path)
    _configure(monkeypatch, path)
    provider = calibre.CalibreLibrary()

    assert provider.fingerprint() == path.stat().st_mtime

    wal = tmp_path / "metadata.db-wal"
    wal.write_bytes(b"")
    later = path.stat().st_mtime + 60
    os.utime(wal, (later, later))

    assert provider.fingerprint() == later


def _library_with(path: Path, books: list[tuple[int, str, str, str | None]]) -> None:
    """A library of (id, title, author, series) rows."""
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    series_ids: dict[str, int] = {}
    for book_id, title, author, series_name in books:
        conn.execute(
            "INSERT INTO books(id, title, uuid) VALUES (?, ?, ?)", (book_id, title, f"u{book_id}")
        )
        conn.execute("INSERT INTO authors(id, name) VALUES (?, ?)", (book_id, author))
        conn.execute(
            "INSERT INTO books_authors_link(book, author) VALUES (?, ?)", (book_id, book_id)
        )
        if series_name:
            sid = series_ids.setdefault(series_name, len(series_ids) + 1)
            conn.execute("INSERT OR IGNORE INTO series(id, name) VALUES (?, ?)", (sid, series_name))
            conn.execute(
                "INSERT INTO books_series_link(book, series) VALUES (?, ?)", (book_id, sid)
            )
    conn.commit()
    conn.close()


def _owned(entries: list[Any], title: str, author: str) -> str | None:
    book = BookMetadata(provider="hardcover", provider_id="x", title=title, authors=[author])
    return library_index.match_entries(book, entries)


def test_an_abbreviated_series_label_in_the_title_does_not_hide_an_owned_book(
    tmp_path: Path, calibre_library: Callable[..., Path]
) -> None:
    # The library names books "Label NN: Title". When the label abbreviates the series
    # ("HHG" for The Hitchhiker's Guide to the Galaxy) its words are in neither the
    # series nor the author, and the book read as not owned, so it was fetched again.
    path = tmp_path / "metadata.db"
    _library_with(
        path,
        [
            (1, "HHG 5: Mostly Harmless", "Douglas Adams", "The Hitchhiker's Guide to the Galaxy"),
            (2, "DCC 7: This Inevitable Ruin", "Matt Dinniman", "Dungeon Crawler Carl"),
        ],
    )
    calibre_library(path=path, create=False)
    entries = calibre.CalibreLibrary().fetch_entries()

    assert _owned(entries, "Mostly Harmless", "Douglas Adams") == "owned"
    assert _owned(entries, "This Inevitable Ruin", "Matt Dinniman") == "owned"


def test_a_series_label_still_does_not_make_a_different_book_match(
    tmp_path: Path, calibre_library: Callable[..., Path]
) -> None:
    path = tmp_path / "metadata.db"
    _library_with(
        path,
        [(1, "HHG 5: Mostly Harmless", "Douglas Adams", "The Hitchhiker's Guide to the Galaxy")],
    )
    calibre_library(path=path, create=False)
    entries = calibre.CalibreLibrary().fetch_entries()

    assert _owned(entries, "Harmless", "Douglas Adams") is None
    assert _owned(entries, "Mostly Harmless", "Someone Else") is None


def test_a_title_prefix_without_a_series_is_left_alone(
    tmp_path: Path, calibre_library: Callable[..., Path]
) -> None:
    # Only a book that has a series gets its leading "Label NN:" treated as the label.
    path = tmp_path / "metadata.db"
    _library_with(path, [(1, "Messiah 2: Dune", "Frank Herbert", None)])
    calibre_library(path=path, create=False)
    entries = calibre.CalibreLibrary().fetch_entries()

    assert _owned(entries, "Dune", "Frank Herbert") is None


@pytest.mark.parametrize(
    "shelf_title",
    [
        "Dungeon Crawler Carl (Full Cast Edition)",
        "Dungeon Crawler Carl (Dramatized Adaptation)",
        "Dungeon Crawler Carl [GraphicAudio]",
    ],
)
def test_an_edition_marker_in_the_title_is_a_different_edition(
    tmp_path: Path, calibre_library: Callable[..., Path], shelf_title: str
) -> None:
    # A bracketed marker with no number in it describes the edition. Owning a full-cast
    # or dramatized recording is not owning the book it adapts, so the request must
    # not be closed as already in the library.
    path = tmp_path / "metadata.db"
    _library_with(path, [(1, shelf_title, "Matt Dinniman", None)])
    calibre_library(path=path, create=False)
    entries = calibre.CalibreLibrary().fetch_entries()

    assert _owned(entries, "Dungeon Crawler Carl", "Matt Dinniman") is None
    # The same edition, asked for by its own name, is on the shelf.
    assert _owned(entries, shelf_title, "Matt Dinniman") == "owned"


@pytest.mark.parametrize(
    ("shelf_title", "series"),
    [
        ("Kiss the Girls (Alex Cross Series #11)", None),
        ("Kiss the Girls (Alex Cross, Book 2)", None),
        ("Alex Cross 02: Kiss the Girls", "Alex Cross"),
        ("Kiss the Girls (Illustrated Edition)", None),
        ("Kiss the Girls (Unabridged)", None),
    ],
)
def test_series_metadata_and_printing_words_in_the_title_do_not_hide_the_book(
    tmp_path: Path, calibre_library: Callable[..., Path], shelf_title: str, series: str | None
) -> None:
    # A numbered parenthetical is the series by Calibre convention, often in place of
    # the series field, and a printing word changes nothing about which work it is.
    path = tmp_path / "metadata.db"
    _library_with(path, [(1, shelf_title, "James Patterson", series)])
    calibre_library(path=path, create=False)
    entries = calibre.CalibreLibrary().fetch_entries()

    assert _owned(entries, "Kiss the Girls", "James Patterson") == "owned"
