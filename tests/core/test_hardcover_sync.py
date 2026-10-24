"""Tests for syncing Hardcover status shelves into pending requests."""

import sqlite3
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from shelfmark.core import hardcover_sync, library_index
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


def _insert_history(
    db_path,
    *,
    title,
    author,
    final_status="complete",
    content_type=None,
    request_id=None,
    terminal_at=None,
):
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO download_history "
            "(task_id, source, title, author, final_status, content_type, request_id, "
            "terminal_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP))",
            (
                f"task-{title}-{final_status}-{request_id}",
                "prowlarr",
                title,
                author,
                final_status,
                content_type,
                request_id,
                terminal_at,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _request(user_db, user_id, provider_id, content_type="audiobook", title="Some Book"):
    """A request for a Hardcover book, the shape the sync and the UI both create."""
    row = user_db.create_request(
        user_id=user_id,
        content_type=content_type,
        request_level="book",
        policy_mode="request_book",
        book_data={
            "title": title,
            "author": "Matt Dinniman",
            "provider": "hardcover",
            "provider_id": provider_id,
            "content_type": content_type,
        },
    )
    return int(row["id"])


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


def test_existing_request_keys_pair_provider_id_with_content_type(user_db):
    user = user_db.create_user(username="reader", role="user")
    for book_data in (
        {"title": "A", "author": "X", "provider": "hardcover", "provider_id": 11},
        {"title": "B", "author": "X", "provider": "hardcover", "id": "22"},
        {"title": "C", "author": "X", "provider": "openlibrary", "id": "OL22W"},
        {"title": "D", "author": "X", "provider": "googlebooks", "provider_id": 33},
        {"title": "E", "author": "X", "provider": "manual"},
    ):
        user_db.create_request(
            user_id=user["id"],
            content_type="audiobook",
            request_level="book",
            policy_mode="request_book",
            book_data=book_data,
        )

    assert hardcover_sync._existing_request_keys(user_db) == {
        ("11", "audiobook"),
        ("22", "audiobook"),
    }


def _already_downloaded(db_path, *args, **kwargs):
    with hardcover_sync._history_connection(db_path) as conn:
        return hardcover_sync._already_downloaded(conn, *args, **kwargs)


def test_already_downloaded_is_keyed_on_the_request_book_not_the_release_title(user_db, db_path):
    user = user_db.create_user(username="reader", role="user")
    request_id = _request(user_db, user["id"], "42", title="Dungeon Crawler Carl")
    # The history row carries the release name, which never equals the book's title.
    _insert_history(
        db_path,
        title="Dungeon Crawler Carl - Matt Dinniman [M4B]",
        author="Matt Dinniman",
        request_id=request_id,
    )

    assert _already_downloaded(db_path, "hardcover", "42", "audiobook")
    assert not _already_downloaded(db_path, "hardcover", "43", "audiobook")
    assert not _already_downloaded(db_path, "openlibrary", "42", "audiobook")
    assert not _already_downloaded(None, "hardcover", "42", "audiobook")


def test_a_download_with_no_request_does_not_count(user_db, db_path):
    user = user_db.create_user(username="reader", role="user")
    _request(user_db, user["id"], "42", title="Dungeon Crawler Carl")
    _insert_history(db_path, title="Dungeon Crawler Carl", author="Matt Dinniman")

    assert not _already_downloaded(db_path, "hardcover", "42", "audiobook")


def test_a_provider_id_stored_as_a_number_still_matches(user_db, db_path):
    user = user_db.create_user(username="reader", role="user")
    request_id = _request(user_db, user["id"], 42)
    _insert_history(db_path, title="Some Book", author="Matt Dinniman", request_id=request_id)

    assert _already_downloaded(db_path, "hardcover", "42", "audiobook")


@pytest.mark.parametrize(
    ("final_status", "counts"),
    [("complete", True), ("active", True), ("error", False), ("cancelled", False)],
)
def test_only_a_finished_or_running_download_counts(user_db, db_path, final_status, counts):
    user = user_db.create_user(username="reader", role="user")
    request_id = _request(user_db, user["id"], "42", content_type="ebook")
    _insert_history(
        db_path,
        title="Dungeon Crawler Carl",
        author="Matt Dinniman",
        final_status=final_status,
        request_id=request_id,
    )

    assert _already_downloaded(db_path, "hardcover", "42", "ebook") is counts


@pytest.mark.parametrize(
    ("terminal_at", "counts"),
    [
        ("2026-10-11T10:00:00+00:00", True),
        ("2026-10-11 10:00:00", True),
        ("2026-10-10T11:00:00+00:00", False),
        ("2026-10-10 11:00:00", False),
        ("not a time", False),
    ],
)
def test_an_active_row_older_than_a_day_is_a_lost_download(user_db, db_path, terminal_at, counts):
    user = user_db.create_user(username="reader", role="user")
    request_id = _request(user_db, user["id"], "42")
    _insert_history(
        db_path,
        title="Some Book",
        author="Matt Dinniman",
        final_status="active",
        request_id=request_id,
        terminal_at=terminal_at,
    )
    now = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)

    assert _already_downloaded(db_path, "hardcover", "42", "audiobook", now=now) is counts


def test_a_finished_download_counts_however_old_it_is(user_db, db_path):
    user = user_db.create_user(username="reader", role="user")
    request_id = _request(user_db, user["id"], "42")
    _insert_history(
        db_path,
        title="Some Book",
        author="Matt Dinniman",
        request_id=request_id,
        terminal_at="2025-01-01T00:00:00+00:00",
    )

    assert _already_downloaded(db_path, "hardcover", "42", "audiobook")


def test_already_downloaded_fails_open_without_history_table(tmp_path):
    missing = str(tmp_path / "empty.db")

    assert _already_downloaded(missing, "hardcover", "1", "ebook") is False


class TestSyncWishlist:
    @pytest.fixture(autouse=True)
    def _defaults(self, monkeypatch):
        _configure(monkeypatch, HARDCOVER_SYNC_STATUSES="1")
        monkeypatch.setattr(
            "shelfmark.core.library_index.is_in_library", lambda *_args, **_kwargs: False
        )

    def test_a_sync_pass_shares_one_history_connection(self, user_db, db_path, monkeypatch):
        user_db.create_user(username="admin", role="admin")
        provider = _Provider({1: [[_book(1, "One"), _book(2, "Two"), _book(3, "Three")]]})
        self._use_provider(monkeypatch, provider)
        seen = []
        real = hardcover_sync._already_downloaded

        def _spy(conn, *args, **kwargs):
            seen.append(conn)
            return real(conn, *args, **kwargs)

        monkeypatch.setattr(hardcover_sync, "_already_downloaded", _spy)

        hardcover_sync.sync_wishlist(user_db, db_path=db_path)

        assert len(seen) > 1
        assert isinstance(seen[0], sqlite3.Connection)
        assert all(conn is seen[0] for conn in seen)

    def _use_provider(self, monkeypatch, provider):
        monkeypatch.setattr(hardcover_sync, "_build_provider", lambda: provider)

    def test_a_failed_shelf_fetch_is_counted_as_an_error(self, user_db, db_path, monkeypatch):
        user = user_db.create_user(username="reader", role="user")

        class _Down(_Provider):
            def _fetch_current_user_books_by_status(self, status_id, page, limit):
                raise OSError("hardcover is down")

        self._use_provider(monkeypatch, _Down({}))

        summary = hardcover_sync.sync_wishlist(user_db, db_path=db_path, user_id=user["id"])

        assert summary["errors"] >= 1
        assert summary["added"] == 0

    def test_a_fetch_that_fails_on_a_later_page_keeps_the_books_already_read(
        self, user_db, db_path, monkeypatch
    ):
        user = user_db.create_user(username="reader", role="user")

        class _Flaky(_Provider):
            def _fetch_current_user_books_by_status(self, status_id, page, limit):
                if page == 2:
                    raise OSError("rate limited")
                return SimpleNamespace(books=[_book(1, "Dungeon Crawler Carl")], has_more=True)

        self._use_provider(monkeypatch, _Flaky({}))

        summary = hardcover_sync.sync_wishlist(user_db, db_path=db_path, user_id=user["id"])

        assert summary["added"] == 1
        assert summary["errors"] >= 1

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
        # Another user's request for book 11 was fulfilled; this user's sync cannot see
        # the request, so the history is what skips it.
        other = user_db.create_user(username="other", role="user")
        _insert_history(
            db_path,
            title="Already Downloaded - Matt Dinniman [M4B]",
            author="Matt Dinniman",
            request_id=_request(user_db, other["id"], "11", title="Already Downloaded"),
        )
        _configure(monkeypatch, HARDCOVER_SYNC_STATUSES="1", LIBRARY_CHECK_ENABLED=True)
        monkeypatch.setattr("shelfmark.core.library_index.any_provider_enabled", lambda: True)
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
        titles = sorted(
            row["book_data"]["title"] for row in user_db.list_requests(user_id=user["id"])
        )
        assert titles == ["Already Requested", "Brand New"]

    def test_a_library_that_cannot_be_read_stops_the_sync_before_anything_is_requested(
        self, user_db, db_path, monkeypatch
    ):
        user = user_db.create_user(username="reader", role="user")
        provider = _Provider({1: [[_book(1, "Dungeon Crawler Carl")]]})
        self._use_provider(monkeypatch, provider)
        _configure(monkeypatch, HARDCOVER_SYNC_STATUSES="1")

        def _down(*_args, **_kwargs):
            raise library_index.LibraryUnavailableError("Audiobookshelf is unavailable")

        monkeypatch.setattr("shelfmark.core.library_index.require_ready", _down)

        summary = hardcover_sync.sync_wishlist(user_db, db_path=db_path, user_id=user["id"])

        # Skipping is the safe side: syncing now would request books the library already holds.
        assert summary == {"added": 0, "skipped": 0, "in_library": 0, "errors": 1}
        assert provider.calls == []  # Hardcover was not even asked
        assert user_db.list_requests() == []

    def test_unavailable_provider_counts_as_error(self, user_db, db_path, monkeypatch):
        self._use_provider(monkeypatch, None)

        summary = hardcover_sync.sync_wishlist(user_db, db_path=db_path)

        assert summary == {"added": 0, "skipped": 0, "in_library": 0, "errors": 1}
        assert user_db.list_requests() == []


class TestResolveRequestOwner:
    def test_returns_lowest_id_admin(self, user_db):
        user_db.create_user(username="reader", role="user")
        first_admin = user_db.create_user(username="ops", role="admin")
        user_db.create_user(username="second", role="admin")

        assert hardcover_sync.resolve_request_owner(user_db) == first_admin["id"]

    def test_returns_none_without_admins(self, user_db):
        user_db.create_user(username="reader", role="user")

        assert hardcover_sync.resolve_request_owner(user_db) is None

    def test_sync_without_admin_counts_an_error_and_creates_nothing(self, user_db, db_path):
        user_db.create_user(username="reader", role="user")

        summary = hardcover_sync.sync_wishlist(user_db, db_path=db_path)

        assert summary == {"added": 0, "skipped": 0, "in_library": 0, "errors": 1}
        assert user_db.list_requests() == []

    def test_sync_defaults_to_the_lowest_id_admin(self, user_db, db_path, monkeypatch):
        user_db.create_user(username="reader", role="user")
        admin = user_db.create_user(username="ops", role="admin")
        provider = _Provider({1: [[_book(1, "Dungeon Crawler Carl")]]})
        monkeypatch.setattr(hardcover_sync, "_build_provider", lambda: provider)

        summary = hardcover_sync.sync_wishlist(user_db, db_path=db_path)

        assert summary["added"] == 1
        assert user_db.list_requests()[0]["user_id"] == admin["id"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("ebook", ["ebook"]),
        ("audiobook", ["audiobook"]),
        ("both", ["ebook", "audiobook"]),
        ("BOTH ", ["ebook", "audiobook"]),
        ("garbage", ["audiobook"]),
        (None, ["audiobook"]),
    ],
)
def test_configured_content_types(monkeypatch, raw, expected):
    _configure(monkeypatch, HARDCOVER_SYNC_CONTENT_TYPE=raw)

    assert hardcover_sync._configured_content_types() == expected


def test_already_downloaded_is_per_content_type(user_db, db_path):
    user = user_db.create_user(username="reader", role="user")
    request_id = _request(user_db, user["id"], "42", content_type="ebook")
    # The source's own content label, as Anna's Archive writes it; the request's type decides.
    _insert_history(
        db_path,
        title="Dungeon Crawler Carl",
        author="Matt Dinniman",
        content_type="\U0001f4d5 book (fiction)",
        request_id=request_id,
    )

    assert _already_downloaded(db_path, "hardcover", "42", "ebook")
    assert not _already_downloaded(db_path, "hardcover", "42", "audiobook")


class TestSyncBothFormats:
    def test_both_creates_an_ebook_and_an_audiobook_request_per_book(
        self, user_db, db_path, monkeypatch
    ):
        admin = user_db.create_user(username="ops", role="admin")
        _configure(monkeypatch, HARDCOVER_SYNC_STATUSES="1", HARDCOVER_SYNC_CONTENT_TYPE="both")
        provider = _Provider({1: [[_book(1, "Dungeon Crawler Carl")]]})
        monkeypatch.setattr(hardcover_sync, "_build_provider", lambda: provider)
        checked: list[str] = []
        monkeypatch.setattr("shelfmark.core.library_index.any_provider_enabled", lambda: True)
        monkeypatch.setattr(
            "shelfmark.core.library_index.is_in_library",
            lambda _book, content_type=None: (
                checked.append(content_type) or content_type == "ebook"
            ),
        )

        summary = hardcover_sync.sync_wishlist(user_db, db_path=db_path, user_id=admin["id"])

        assert summary == {"added": 1, "skipped": 0, "in_library": 1, "errors": 0}
        assert checked == ["ebook", "audiobook"]
        rows = user_db.list_requests()
        assert [row["content_type"] for row in rows] == ["audiobook"]
        assert rows[0]["book_data"]["content_type"] == "audiobook"

    def test_second_run_skips_both_existing_requests(self, user_db, db_path, monkeypatch):
        admin = user_db.create_user(username="ops", role="admin")
        _configure(monkeypatch, HARDCOVER_SYNC_STATUSES="1", HARDCOVER_SYNC_CONTENT_TYPE="both")
        provider = _Provider({1: [[_book(1, "Dungeon Crawler Carl")]]})
        monkeypatch.setattr(hardcover_sync, "_build_provider", lambda: provider)

        first = hardcover_sync.sync_wishlist(user_db, db_path=db_path, user_id=admin["id"])
        second = hardcover_sync.sync_wishlist(user_db, db_path=db_path, user_id=admin["id"])

        assert first["added"] == 2
        assert second == {"added": 0, "skipped": 2, "in_library": 0, "errors": 0}
        assert sorted(row["content_type"] for row in user_db.list_requests()) == [
            "audiobook",
            "ebook",
        ]
