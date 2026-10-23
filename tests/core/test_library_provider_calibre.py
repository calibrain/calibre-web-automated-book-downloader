"""Tests for the read-only Calibre metadata.db library provider."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from shelfmark.core import library_index
from shelfmark.core.library_providers import calibre
from shelfmark.metadata_providers import BookMetadata

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
