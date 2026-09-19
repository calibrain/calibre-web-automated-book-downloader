"""Tests for strict release matching and auto-download of pending requests."""

import pytest

from shelfmark.core import auto_download
from shelfmark.core.user_db import UserDB
from shelfmark.metadata_providers import BookMetadata
from shelfmark.release_sources import Release, ReleaseProtocol

AUDIOBOOK_FORMATS = {"m4b", "mp3"}


class _Config:
    def __init__(self, **values):
        self.values = values

    def get(self, key, default=None, user_id=None):
        return self.values.get(key, default)


def _book(**overrides):
    fields = {
        "provider": "hardcover",
        "provider_id": "42",
        "title": "Dungeon Crawler Carl",
        "authors": ["Matt Dinniman"],
    }
    fields.update(overrides)
    return BookMetadata(**fields)


def _release(**overrides):
    fields = {
        "source": "prowlarr",
        "source_id": "rel-1",
        "title": "Dungeon Crawler Carl - Matt Dinniman [M4B]",
        "format": "m4b",
        "protocol": ReleaseProtocol.TORRENT,
        "seeders": 10,
    }
    fields.update(overrides)
    return Release(**fields)


def _strict(release, **kwargs):
    kwargs.setdefault("audiobook_formats", AUDIOBOOK_FORMATS)
    return auto_download.strict_match(release, _book(), **kwargs)


@pytest.fixture
def user_db(tmp_path):
    db = UserDB(str(tmp_path / "users.db"))
    db.initialize()
    return db


def _pending_request(user_db, user_id, **book_data_overrides):
    book_data = {
        "title": "Dungeon Crawler Carl",
        "author": "Matt Dinniman",
        "provider": "hardcover",
        "provider_id": "42",
        "content_type": "audiobook",
    }
    book_data.update(book_data_overrides)
    return user_db.create_request(
        user_id=user_id,
        content_type="audiobook",
        request_level="book",
        policy_mode="request_book",
        book_data=book_data,
    )


def _stub_provider(monkeypatch, book):
    class _Provider:
        def get_book(self, book_id):
            assert book_id == "42"
            return book

    monkeypatch.setattr(
        "shelfmark.metadata_providers.is_provider_registered", lambda name: name == "hardcover"
    )
    monkeypatch.setattr("shelfmark.metadata_providers.get_provider_kwargs", lambda _name: {})
    monkeypatch.setattr(
        "shelfmark.metadata_providers.get_provider", lambda _name, **_kwargs: _Provider()
    )


class TestStrictMatch:
    def test_matching_release_passes(self):
        assert _strict(_release()) is True

    def test_title_mismatch_rejected(self):
        assert _strict(_release(title="Some Other Book - Matt Dinniman [M4B]")) is False

    def test_missing_author_surname_rejected(self):
        assert _strict(_release(title="Dungeon Crawler Carl [M4B]", indexer=None)) is False

    def test_author_found_in_extra_metadata(self):
        release = _release(title="Dungeon Crawler Carl [M4B]", extra={"author": "Matt Dinniman"})
        assert _strict(release) is True

    def test_ebook_only_release_rejected(self):
        release = _release(title="Dungeon Crawler Carl - Matt Dinniman", format="epub")
        assert _strict(release) is False

    def test_audiobook_signal_in_title_accepted_without_format(self):
        release = _release(title="Dungeon Crawler Carl by Matt Dinniman (Unabridged)", format=None)
        assert _strict(release) is True

    @pytest.mark.parametrize("seeders", [0, None])
    def test_torrent_below_minimum_seeders_rejected(self, seeders):
        assert _strict(_release(seeders=seeders), min_seeders=1) is False

    def test_torrent_without_seeder_count_allowed_when_minimum_is_zero(self):
        assert _strict(_release(seeders=None), min_seeders=0) is True

    def test_non_torrent_ignores_seeders(self):
        release = _release(protocol=ReleaseProtocol.NZB, seeders=None)
        assert _strict(release, min_seeders=5) is True


class TestPickBestRelease:
    def test_empty_returns_none(self):
        assert auto_download.pick_best_release([]) is None

    def test_m4b_outranks_mp3_regardless_of_seeders(self):
        mp3 = _release(source_id="mp3", format="mp3", seeders=500)
        m4b = _release(source_id="m4b", format="m4b", seeders=1)
        assert auto_download.pick_best_release([mp3, m4b]) is m4b

    def test_m4b_in_title_ranks_as_m4b_when_format_missing(self):
        mp3 = _release(source_id="mp3", format="mp3", seeders=500)
        untagged = _release(source_id="untagged", format=None, seeders=1)
        assert auto_download.pick_best_release([mp3, untagged]) is untagged

    def test_seeders_break_format_ties(self):
        few = _release(source_id="few", seeders=2, size_bytes=10)
        many = _release(source_id="many", seeders=20, size_bytes=1)
        assert auto_download.pick_best_release([few, many]) is many

    def test_size_breaks_seeder_ties(self):
        small = _release(source_id="small", seeders=5, size_bytes=100)
        large = _release(source_id="large", seeders=5, size_bytes=200)
        assert auto_download.pick_best_release([small, large]) is large


class TestBuildReleaseData:
    def test_drops_none_values(self):
        release = _release(size=None, size_bytes=None, language=None, info_url=None)

        data = auto_download.build_release_data(release, _book(), "audiobook")

        assert data["source"] == "prowlarr"
        assert data["source_id"] == "rel-1"
        assert data["protocol"] == "torrent"
        assert data["author"] == "Matt Dinniman"
        assert data["content_type"] == "audiobook"
        assert not {"size", "size_bytes", "language", "info_url", "preview", "year"} & data.keys()

    def test_carries_cover_series_and_book_metadata(self):
        book = _book(
            cover_url="https://img.example/cover.jpg",
            publish_year=2020,
            subtitle="Book One",
            series_name="Dungeon Crawler Carl",
            series_position=1.0,
        )
        release = _release(content_type=None, extra={"preview": "https://img.example/rel.jpg"})

        data = auto_download.build_release_data(release, book, "audiobook")

        assert data["preview"] == "https://img.example/cover.jpg"
        assert data["year"] == 2020
        assert data["subtitle"] == "Book One"
        assert data["series_name"] == "Dungeon Crawler Carl"
        assert data["series_position"] == 1.0
        assert data["content_type"] == "audiobook"
        assert data["extra"] == {"preview": "https://img.example/rel.jpg"}

    def test_falls_back_to_release_preview_without_book_cover(self):
        release = _release(extra={"preview": "https://img.example/rel.jpg"})

        data = auto_download.build_release_data(release, _book(), "audiobook")

        assert data["preview"] == "https://img.example/rel.jpg"


class TestConfiguredSourcePriority:
    @pytest.fixture(autouse=True)
    def _available_sources(self, monkeypatch):
        monkeypatch.setattr(
            "shelfmark.release_sources.list_available_sources",
            lambda: [
                {"name": "first", "enabled": True},
                {"name": "second", "enabled": True},
                {"name": "unavailable", "enabled": False},
            ],
        )

    def _configure(self, monkeypatch, priority):
        monkeypatch.setattr(
            auto_download, "app_config", _Config(AUTO_DOWNLOAD_SOURCE_PRIORITY=priority)
        )

    def test_respects_configured_order_and_skips_unusable_entries(self, monkeypatch):
        self._configure(
            monkeypatch,
            [
                {"id": "second"},
                {"id": "first", "enabled": False},
                {"id": "unavailable"},
                {"id": "unknown"},
                "not-a-dict",
            ],
        )

        assert auto_download._configured_source_priority() == ["second"]

    @pytest.mark.parametrize("priority", [[], "garbage", [{"id": "unknown"}]])
    def test_falls_back_to_all_enabled_sources(self, monkeypatch, priority):
        self._configure(monkeypatch, priority)

        assert auto_download._configured_source_priority() == ["first", "second"]


class TestAutoDownloadPending:
    def test_disabled_returns_zeros_without_touching_the_db(self, monkeypatch):
        monkeypatch.setattr(auto_download, "app_config", _Config(AUTO_DOWNLOAD_ENABLED=False))

        class _UntouchableDb:
            def list_requests(self, **_kwargs):
                raise AssertionError("pending requests must not be queried")

        summary = auto_download.auto_download_pending(
            _UntouchableDb(), queue_release=lambda *_args, **_kwargs: (True, None)
        )

        assert summary == {"queued": 0, "no_match": 0, "in_library": 0, "skipped": 0, "error": 0}


class TestAutoDownloadRequest:
    @pytest.fixture(autouse=True)
    def _library_check_off(self, monkeypatch):
        monkeypatch.setattr(auto_download, "app_config", _Config(LIBRARY_CHECK_ENABLED=False))
        monkeypatch.setattr(
            "shelfmark.core.library_index.is_in_library", lambda *_args, **_kwargs: False
        )

    def _run(self, user_db, row, admin, *, queue_release=None, sources=("first", "second")):
        return auto_download.auto_download_request(
            user_db,
            row,
            sources=list(sources),
            content_type="audiobook",
            min_seeders=1,
            queue_release=queue_release or (lambda *_args, **_kwargs: (True, None)),
            admin_user_id=admin["id"],
        )

    def test_queues_first_strict_match_via_fulfil_path(self, user_db, monkeypatch):
        admin = user_db.create_user(username="admin", role="admin")
        reader = user_db.create_user(username="reader", role="user")
        row = _pending_request(user_db, reader["id"])
        _stub_provider(monkeypatch, _book(cover_url="https://img.example/cover.jpg"))

        chosen = _release(source="second", source_id="good", seeders=5)
        searched = []

        def _search(_book_arg, *, sources, **_kwargs):
            searched.extend(sources)
            if sources == ["first"]:
                epub = _release(source="first", title="Dungeon Crawler Carl.epub", format="epub")
                return [epub], {"first": [epub]}, []
            return [chosen], {"second": [chosen]}, []

        monkeypatch.setattr(auto_download, "search_book_releases", _search)
        queued = []

        def _queue(release_data, priority, *, user_id, username):
            queued.append((release_data, priority, user_id, username))
            return True, None

        outcome = self._run(user_db, row, admin, queue_release=_queue)

        assert outcome.status == "queued"
        assert outcome.source == "second"
        assert searched == ["first", "second"]

        stored = user_db.get_request(row["id"])
        assert stored["status"] == "fulfilled"
        assert stored["delivery_state"] == "queued"
        assert stored["reviewed_by"] == admin["id"]
        assert stored["release_data"]["source_id"] == "good"

        release_data, priority, user_id, username = queued[0]
        assert release_data["_request_id"] == row["id"]
        assert release_data["preview"] == "https://img.example/cover.jpg"
        assert priority == 0
        assert user_id == reader["id"]
        assert username == "reader"

    def test_queue_failure_rolls_request_back_to_pending(self, user_db, monkeypatch):
        admin = user_db.create_user(username="admin", role="admin")
        reader = user_db.create_user(username="reader", role="user")
        row = _pending_request(user_db, reader["id"])
        _stub_provider(monkeypatch, _book())
        chosen = _release(source="first")
        monkeypatch.setattr(
            auto_download,
            "search_book_releases",
            lambda *_args, **_kwargs: ([chosen], {"first": [chosen]}, []),
        )

        outcome = self._run(
            user_db, row, admin, queue_release=lambda *_args, **_kwargs: (False, "client down")
        )

        assert outcome.status == "error"
        assert outcome.detail == "client down"
        stored = user_db.get_request(row["id"])
        assert stored["status"] == "pending"
        assert stored["release_data"] is None

    def test_no_strict_match_leaves_request_pending(self, user_db, monkeypatch):
        admin = user_db.create_user(username="admin", role="admin")
        reader = user_db.create_user(username="reader", role="user")
        row = _pending_request(user_db, reader["id"])
        _stub_provider(monkeypatch, _book())
        wrong = _release(title="A Different Audiobook [M4B]")
        monkeypatch.setattr(
            auto_download,
            "search_book_releases",
            lambda *_args, **_kwargs: ([wrong], {"first": [wrong], "second": [wrong]}, []),
        )

        def _never_queue(*_args, **_kwargs):
            raise AssertionError("nothing should be queued")

        outcome = self._run(user_db, row, admin, queue_release=_never_queue)

        assert outcome.status == "no_match"
        assert user_db.get_request(row["id"])["status"] == "pending"

    def test_book_already_in_library_is_skipped_before_searching(self, user_db, monkeypatch):
        admin = user_db.create_user(username="admin", role="admin")
        reader = user_db.create_user(username="reader", role="user")
        row = _pending_request(user_db, reader["id"])
        _stub_provider(monkeypatch, _book())
        monkeypatch.setattr(auto_download, "app_config", _Config(LIBRARY_CHECK_ENABLED=True))
        monkeypatch.setattr(
            "shelfmark.core.library_index.is_in_library", lambda *_args, **_kwargs: True
        )

        def _never_search(*_args, **_kwargs):
            raise AssertionError("release search must not run")

        monkeypatch.setattr(auto_download, "search_book_releases", _never_search)

        outcome = self._run(user_db, row, admin)

        assert outcome.status == "in_library"
        assert user_db.get_request(row["id"])["status"] == "pending"

    def test_unregistered_provider_is_skipped(self, user_db, monkeypatch):
        admin = user_db.create_user(username="admin", role="admin")
        reader = user_db.create_user(username="reader", role="user")
        row = _pending_request(user_db, reader["id"], provider="nope")
        monkeypatch.setattr("shelfmark.metadata_providers.is_provider_registered", lambda _n: False)

        outcome = self._run(user_db, row, admin)

        assert outcome.status == "skipped"
        assert outcome.detail == "no usable provider/id"
