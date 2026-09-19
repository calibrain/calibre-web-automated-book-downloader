"""Tests for syncing Hardcover status shelves into pending requests."""

import sqlite3
from types import SimpleNamespace

import pytest

from shelfmark.core import hardcover_sync
from shelfmark.core.user_db import UserDB
from shelfmark.core.utils import transform_cover_url
from shelfmark.metadata_providers import BookMetadata


class _Config:
    def __init__(self, **values):
        self.values = values

    def get(self, key, default=None, user_id=None):
        return self.values.get(key, default)


class _Provider:
    """Stub exposing the private paging method ``sync_wishlist`` relies on."""

    def __init__(self, pages_by_status):
        self.pages_by_status = pages_by_status
        self.calls = []

    def _fetch_current_user_books_by_status(self, status_id, page, limit):
        self.calls.append((status_id, page, limit))
        pages = self.pages_by_status.get(status_id, [])
        books = pages[page - 1] if page <= len(pages) else []
        return SimpleNamespace(books=books, has_more=page < len(pages))


def _book(provider_id, title, author="Matt Dinniman", **overrides):
    fields = {
        "provider": "hardcover",
        "provider_id": provider_id,
        "title": title,
        "authors": [author],
    }
    fields.update(overrides)
    return BookMetadata(**fields)


def _insert_history(db_path, *, title, author):
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO download_history (task_id, source, title, author, final_status) "
            "VALUES (?, ?, ?, ?, ?)",
            (f"task-{title}", "prowlarr", title, author, "complete"),
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "users.db")


@pytest.fixture
def user_db(db_path):
    db = UserDB(db_path)
    db.initialize()
    return db


def _configure(monkeypatch, **values):
    monkeypatch.setattr(hardcover_sync, "app_config", _Config(**values))


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ({"HARDCOVER_SYNC_TOKEN": "  sync-token  ", "HARDCOVER_API_KEY": "api"}, "sync-token"),
        ({"HARDCOVER_SYNC_TOKEN": "", "HARDCOVER_API_KEY": "api-key"}, "api-key"),
        ({}, ""),
    ],
)
def test_configured_token_falls_back_to_provider_api_key(monkeypatch, values, expected):
    _configure(monkeypatch, **values)

    assert hardcover_sync._configured_token() == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("3", [3]),
        (" 2 ", [2]),
        (["1", "3"], [1, 3]),
        ((1, "x", None), [1]),
        ("garbage", [1]),
        ([], [1]),
        (42, [1]),
    ],
)
def test_configured_statuses_parsing(monkeypatch, raw, expected):
    _configure(monkeypatch, HARDCOVER_SYNC_STATUSES=raw)

    assert hardcover_sync._configured_statuses() == expected


def test_configured_statuses_default(monkeypatch):
    _configure(monkeypatch)

    assert hardcover_sync._configured_statuses() == [1]


def test_book_to_book_data_includes_optional_fields_only_when_present():
    full = _book(
        7,
        "Dungeon Crawler Carl",
        cover_url="https://img.example/c.jpg",
        publish_year=2020,
        subtitle="Book One",
        search_author="Dinniman",
    )
    sparse = BookMetadata(provider="hardcover", provider_id="8", title="Untitled")

    assert hardcover_sync._book_to_book_data(full, "audiobook") == {
        "title": "Dungeon Crawler Carl",
        "author": "Dinniman",
        "provider": "hardcover",
        "provider_id": "7",
        "content_type": "audiobook",
        "preview": transform_cover_url("https://img.example/c.jpg", "hardcover_7"),
        "year": 2020,
        "subtitle": "Book One",
    }
    assert hardcover_sync._book_to_book_data(sparse, "ebook") == {
        "title": "Untitled",
        "author": "Unknown",
        "provider": "hardcover",
        "provider_id": "8",
        "content_type": "ebook",
    }


def test_existing_provider_ids_reads_provider_id_or_id(user_db):
    user = user_db.create_user(username="reader", role="user")
    for book_data in (
        {"title": "A", "author": "X", "provider": "hardcover", "provider_id": 11},
        {"title": "B", "author": "X", "provider": "openlibrary", "id": "OL22W"},
        {"title": "C", "author": "X", "provider": "manual"},
    ):
        user_db.create_request(
            user_id=user["id"],
            content_type="audiobook",
            request_level="book",
            policy_mode="request_book",
            book_data=book_data,
        )

    assert hardcover_sync._existing_provider_ids(user_db) == {"11", "OL22W"}


def test_already_downloaded_matches_history_case_insensitively(user_db, db_path):
    _insert_history(db_path, title="Dungeon Crawler Carl", author="Matt Dinniman")

    assert hardcover_sync._already_downloaded(db_path, "dungeon crawler CARL", "MATT dinniman")
    assert not hardcover_sync._already_downloaded(db_path, "Dungeon Crawler Carl", "Someone")
    assert not hardcover_sync._already_downloaded(None, "Dungeon Crawler Carl", "Matt Dinniman")


def test_already_downloaded_fails_open_without_history_table(tmp_path):
    missing = str(tmp_path / "empty.db")

    assert hardcover_sync._already_downloaded(missing, "Anything", "Anyone") is False


class TestSyncWishlist:
    @pytest.fixture(autouse=True)
    def _defaults(self, monkeypatch):
        _configure(monkeypatch, HARDCOVER_SYNC_STATUSES="1")
        monkeypatch.setattr(
            "shelfmark.core.library_index.is_in_library", lambda *_args, **_kwargs: False
        )

    def _use_provider(self, monkeypatch, provider):
        monkeypatch.setattr(hardcover_sync, "_build_provider", lambda: provider)

    def test_adds_new_requests_across_pages(self, user_db, db_path, monkeypatch):
        user = user_db.create_user(username="reader", role="user")
        provider = _Provider(
            {
                1: [
                    [_book(1, "Dungeon Crawler Carl", cover_url="https://img.example/1.jpg")],
                    [_book(2, "Carl's Doomsday Scenario", publish_year=2021)],
                ]
            }
        )
        self._use_provider(monkeypatch, provider)

        summary = hardcover_sync.sync_wishlist(user_db, db_path=db_path, user_id=user["id"])

        assert summary == {"added": 2, "skipped": 0, "in_library": 0, "errors": 0}
        assert provider.calls == [(1, 1, 25), (1, 2, 25)]
        rows = {row["book_data"]["provider_id"]: row for row in user_db.list_requests()}
        assert set(rows) == {"1", "2"}
        assert rows["1"]["status"] == "pending"
        assert rows["1"]["content_type"] == "audiobook"
        assert rows["1"]["book_data"]["preview"] == transform_cover_url(
            "https://img.example/1.jpg", "hardcover_1"
        )
        assert rows["2"]["book_data"]["year"] == 2021
        assert "preview" not in rows["2"]["book_data"]

    def test_skips_known_downloaded_owned_and_duplicate_books(self, user_db, db_path, monkeypatch):
        user = user_db.create_user(username="reader", role="user")
        user_db.create_request(
            user_id=user["id"],
            content_type="audiobook",
            request_level="book",
            policy_mode="request_book",
            book_data={
                "title": "Already Requested",
                "author": "Matt Dinniman",
                "provider": "hardcover",
                "provider_id": "10",
            },
        )
        _insert_history(db_path, title="Already Downloaded", author="Matt Dinniman")
        _configure(monkeypatch, HARDCOVER_SYNC_STATUSES="1", LIBRARY_CHECK_ENABLED=True)
        monkeypatch.setattr(
            "shelfmark.core.library_index.is_in_library",
            lambda book, *_args, **_kwargs: book.title == "Already Owned",
        )
        provider = _Provider(
            {
                1: [
                    [
                        _book(10, "Already Requested"),
                        _book(11, "Already Downloaded"),
                        _book(12, "Already Owned"),
                        _book(13, "Brand New"),
                        _book(14, "Brand New"),
                    ]
                ]
            }
        )
        self._use_provider(monkeypatch, provider)

        summary = hardcover_sync.sync_wishlist(user_db, db_path=db_path, user_id=user["id"])

        assert summary == {"added": 1, "skipped": 3, "in_library": 1, "errors": 0}
        titles = sorted(row["book_data"]["title"] for row in user_db.list_requests())
        assert titles == ["Already Requested", "Brand New"]

    def test_unavailable_provider_counts_as_error(self, user_db, db_path, monkeypatch):
        self._use_provider(monkeypatch, None)

        summary = hardcover_sync.sync_wishlist(user_db, db_path=db_path)

        assert summary == {"added": 0, "skipped": 0, "in_library": 0, "errors": 1}
        assert user_db.list_requests() == []
