"""Calibre library provider.

Reads a Calibre ``metadata.db`` (plain Calibre, Calibre-Web or Calibre-Web Automated)
read-only and indexes each book's title, authors, series and identifiers.

The database is opened with ``mode=ro`` first: on a WAL-mode library that sees the
un-checkpointed writes as long as the ``-wal``/``-shm`` files beside it are visible.
When that open fails (typically a read-only mount where SQLite cannot create those
files, or a locked database) it is retried once with ``immutable=1``, a snapshot that
can lag until Calibre next checkpoints. The file mtime is the change fingerprint, so a
write or checkpoint refreshes the index ahead of the cache TTL.
"""

from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import TYPE_CHECKING, Any

from shelfmark.core.config import config as app_config
from shelfmark.core.library_providers import LibraryEntry, setting
from shelfmark.core.logger import setup_logger
from shelfmark.core.text_match import isbn_variants, tokens

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = setup_logger(__name__)

# "(Alex Cross Series #11)", "(Book 2)": a bracketed segment with a number in it.
_NUMBERED_PARENTHETICAL = re.compile(r"[(\[][^)\]]*\d[^)\]]*[)\]]")
# "Label NN: Title", a series label and number in front of the work's own title. The
# label often abbreviates the series ("HHG 5: Mostly Harmless" for The Hitchhiker's
# Guide to the Galaxy), so its words are in neither the series nor the author name.
_SERIES_LABEL = re.compile(r"^\s*(?P<label>[^:]{1,40}?)\s+\d+(?:\.\d+)?\s*:\s+\S")

_DEFAULT_DB_PATH = "/calibre-library/metadata.db"
_BUSY_TIMEOUT_SECONDS = 5

_ISBN_TYPES = frozenset({"isbn", "isbn13", "isbn-13", "isbn10", "isbn-10"})
_ASIN_TYPES = frozenset({"amazon", "mobi-asin", "asin"})
# Calibre identifier type -> Shelfmark metadata provider name.
_EXTERNAL_ID_TYPES = {
    "dnb-idn": "dnb",
    "hardcover-id": "hardcover",
    "google": "googlebooks",
    "openlibrary": "openlibrary",
    "olid": "openlibrary",
}

_BOOKS_SQL = "SELECT id, title FROM books"
_AUTHORS_SQL = "SELECT l.book, a.name FROM books_authors_link l JOIN authors a ON a.id = l.author"
_SERIES_SQL = "SELECT l.book, s.name FROM books_series_link l JOIN series s ON s.id = l.series"
_IDENTIFIERS_SQL = "SELECT book, type, val FROM identifiers"


def _db_path(overrides: Mapping[str, Any] | None = None) -> Path:
    return Path(
        str(
            setting(app_config, "CALIBRE_LIBRARY_DB_PATH", _DEFAULT_DB_PATH, overrides)
            or _DEFAULT_DB_PATH
        )
    )


def _probe(conn: sqlite3.Connection) -> sqlite3.Connection:
    """Force the first read so open failures surface here rather than mid-query."""
    try:
        conn.execute("PRAGMA schema_version").fetchone()
    except sqlite3.Error:
        conn.close()
        raise
    return conn


def _connect(path: Path) -> sqlite3.Connection:
    uri = f"{path.absolute().as_uri()}?mode=ro"
    try:
        conn = _probe(sqlite3.connect(uri, uri=True, timeout=_BUSY_TIMEOUT_SECONDS))
    except sqlite3.OperationalError as exc:
        logger.info(
            "library check: %s cannot be read in place (%s); using an immutable snapshot, "
            "which may lag until Calibre checkpoints the library",
            path,
            exc,
        )
        conn = _probe(sqlite3.connect(f"{uri}&immutable=1", uri=True))
    return conn


def _read_entries(conn: sqlite3.Connection) -> list[LibraryEntry]:
    titles: dict[int, str] = dict(conn.execute(_BOOKS_SQL).fetchall())
    series: dict[int, str] = dict(conn.execute(_SERIES_SQL).fetchall())

    authors: dict[int, list[str]] = {}
    for book_id, name in conn.execute(_AUTHORS_SQL):
        authors.setdefault(book_id, []).append(name)

    isbns: dict[int, set[str]] = {}
    asins: dict[int, set[str]] = {}
    external_ids: dict[int, set[tuple[str, str]]] = {}
    for book_id, id_type, raw in conn.execute(_IDENTIFIERS_SQL):
        value = str(raw or "").strip()
        kind = str(id_type or "").strip().lower()
        if not value:
            continue
        if kind in _ISBN_TYPES:
            isbns.setdefault(book_id, set()).update(isbn_variants(value))
        elif kind in _ASIN_TYPES:
            asins.setdefault(book_id, set()).add(value.upper())
        elif provider := _EXTERNAL_ID_TYPES.get(kind):
            external_ids.setdefault(book_id, set()).add((provider, value))

    entries: list[LibraryEntry] = []
    for book_id, title in titles.items():
        # A numbered parenthetical is series or volume metadata by convention rather
        # than part of the work name, and Calibre users often put it there instead of
        # in the series field. It stays in the recall set below and is dropped from the
        # title set, which is what decides whether the shelf holds a different book.
        # An unnumbered one describes the edition, so its words stay in the title set:
        # the matcher ignores printing words there ("Illustrated Edition", "Unabridged")
        # but reads anything else ("Full Cast Edition", "Dramatized Adaptation") as a
        # different recording of the work, which is not the one being asked for.
        title_tok = set(tokens(_NUMBERED_PARENTHETICAL.sub(" ", title)))
        context_tok = set(tokens(series.get(book_id)))
        # Only for a book with a series: there a leading "Label NN:" is the series
        # label, not words naming another work. Without one it is left to the matcher.
        if book_id in series and (label := _SERIES_LABEL.match(title)):
            context_tok |= set(tokens(label.group("label")))
        for name in authors.get(book_id, ()):
            context_tok |= set(tokens(name))
        tok = title_tok | context_tok
        entries.append(
            LibraryEntry(
                frozenset(tok),
                frozenset(isbns.get(book_id, ())),
                frozenset(asins.get(book_id, ())),
                frozenset(external_ids.get(book_id, ())),
                frozenset(title_tok),
                frozenset(context_tok),
            )
        )
    return entries


class CalibreLibrary:
    """Ebook ownership via a read-only Calibre ``metadata.db``."""

    name = "calibre"
    display_name = "Calibre"
    content_types = frozenset({"ebook"})

    def __init__(self, overrides: Mapping[str, Any] | None = None) -> None:
        self._overrides = overrides

    def is_enabled(self) -> bool:
        return bool(setting(app_config, "LIBRARY_CHECK_CALIBRE_ENABLED", False, self._overrides))

    def describe(self) -> str:
        return f"Calibre library at {_db_path(self._overrides)}"

    def fingerprint(self) -> float | None:
        path = _db_path(self._overrides)
        candidates = (path, path.with_name(f"{path.name}-wal"))
        return max((p.stat().st_mtime for p in candidates if p.exists()), default=None)

    def fetch_entries(self) -> list[LibraryEntry]:
        path = _db_path(self._overrides)
        if not path.is_file():
            raise FileNotFoundError(f"No Calibre database at {path}")
        with closing(_connect(path)) as conn:
            return _read_entries(conn)
