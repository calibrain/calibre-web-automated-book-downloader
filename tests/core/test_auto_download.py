"""Tests for strict release matching and auto-download of pending requests."""

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from shelfmark.core import auto_download, library_index, text_match
from shelfmark.core.download_history_service import DownloadHistoryService
from shelfmark.core.user_db import UserDB
from shelfmark.metadata_providers import BookMetadata
from shelfmark.release_sources import Release, ReleaseProtocol
from tests.core.fakes import FakeConfig

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

    # --- language: English only unless the requester's default languages say otherwise -------

    def test_a_release_in_another_language_is_rejected(self):
        assert _strict(_release(language="hi"), languages=["en"]) is False

    def test_a_release_in_a_wanted_language_passes(self):
        assert _strict(_release(language="en"), languages=["en"]) is True
        assert _strict(_release(language="fr"), languages=["en", "fr"]) is True

    def test_a_language_given_as_a_name_is_understood(self):
        assert _strict(_release(language="Hindi"), languages=["en"]) is False
        assert _strict(_release(language="English"), languages=["en"]) is True

    def test_an_unreported_language_with_a_plain_title_passes(self):
        assert _strict(_release(language=None), languages=["en"]) is True

    @pytest.mark.parametrize(
        "title",
        [
            "Dungeon Crawler Carl (Hindi Edition) - Matt Dinniman",
            "Dungeon Crawler Carl [Spanish Version] - Matt Dinniman",
            "Dungeon Crawler Carl (German Translation) - Matt Dinniman",
            "Dungeon Crawler Carl (French audiobook) - Matt Dinniman",
        ],
    )
    def test_a_title_that_names_another_language_is_rejected_when_none_is_reported(self, title):
        assert _strict(_release(title=title, language=None), languages=["en"]) is False

    def test_a_title_naming_a_wanted_language_is_kept(self):
        title = "Dungeon Crawler Carl (English Edition) - Matt Dinniman"
        assert _strict(_release(title=title, language=None), languages=["en"]) is True
        french = "Dungeon Crawler Carl (French Edition) - Matt Dinniman"
        assert _strict(_release(title=french, language=None), languages=["en", "fr"]) is True

    def test_a_reported_language_beats_the_title(self):
        title = "Dungeon Crawler Carl (Hindi Edition) - Matt Dinniman"
        assert _strict(_release(title=title, language="en"), languages=["en"]) is True

    def test_a_title_mostly_in_another_script_is_rejected_unless_that_script_is_wanted(self):
        # Latin enough to match the book, but most of the letters are Cyrillic.
        title = (
            "Dungeon Crawler Carl - Matt Dinniman "
            + "\u041f\u043e\u0434\u0437\u0435\u043c\u0435\u043b\u044c\u0435 " * 6
        )
        assert _strict(_release(title=title, language=None), languages=["en"]) is False
        assert _strict(_release(title=title, language=None), languages=["ru"]) is True

    def test_no_wanted_languages_means_every_language_is_fine(self):
        assert _strict(_release(language="hi"), languages=None) is True
        assert _strict(_release(language="hi")) is True  # the default, for callers that do not say

    @pytest.mark.parametrize(
        ("configured", "expected"),
        [
            (["en"], ["en"]),
            ("english", ["en"]),
            (["en", "fr"], ["en", "fr"]),
            ("all", None),
            (None, None),
        ],
    )
    def test_the_wanted_languages_come_from_the_default_setting(
        self, monkeypatch, configured, expected
    ):
        from shelfmark.core import search_plan

        monkeypatch.setattr(search_plan.config, "get", lambda key, default=None, **kw: configured)

        assert auto_download._wanted_languages(1) == expected

    def test_a_settings_failure_does_not_switch_the_language_filter_off(self, monkeypatch):
        from shelfmark.core import search_plan

        def _boom(*_a, **_k):
            raise OSError("settings unreadable")

        monkeypatch.setattr(search_plan.config, "get", _boom)

        assert auto_download._wanted_languages(1) == ["en"]

    def test_a_pack_named_in_the_listing_title_is_rejected_even_if_the_row_shows_one_book(self):
        # AudioBookBay lists a pack's files as rows titled after each book, but the listing
        # keeps the pack's own name in extra["title_raw"].
        release = _release(
            extra={"title_raw": "Dungeon Crawler Carl (#1-7) - Matt Dinniman"},
        )

        assert _strict(release) is False

    def test_a_single_book_listing_title_still_passes(self):
        release = _release(extra={"title_raw": "Dungeon Crawler Carl - Matt Dinniman [M4B]"})

        assert _strict(release) is True

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

    def test_torrent_below_minimum_seeders_rejected(self):
        assert _strict(_release(seeders=0), min_seeders=1) is False

    def test_torrent_without_seeder_count_is_not_judged_on_seeders(self):
        release = _release(seeders=None)

        assert auto_download.strict_match(release, _book(), min_seeders=1)
        assert auto_download.strict_match(release, _book(), min_seeders=50)

    def test_audiobook_only_source_counts_as_an_audiobook_signal(self):
        release = _release(
            format=None, title="Dungeon Crawler Carl - Matt Dinniman", content_type="audiobook"
        )

        assert auto_download.strict_match(release, _book())
        assert not auto_download.strict_match(release, _book(), content_type="ebook")

    def test_non_torrent_ignores_seeders(self):
        release = _release(protocol=ReleaseProtocol.NZB, seeders=None)
        assert _strict(release, min_seeders=5) is True

    def test_a_release_without_a_title_does_not_match(self):
        assert _strict(_release(title=None)) is False


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

        assert auto_download._configured_source_priority("audiobook") == ["second"]

    @pytest.mark.parametrize("priority", [[], "garbage", ["not-a-dict"]])
    def test_falls_back_to_all_enabled_sources_when_nothing_is_configured(
        self, monkeypatch, priority
    ):
        self._configure(monkeypatch, priority)

        assert auto_download._configured_source_priority("audiobook") == ["first", "second"]

    @pytest.mark.parametrize(
        "priority",
        [
            [{"id": "first", "enabled": False}, {"id": "second", "enabled": False}],
            [{"id": "unavailable"}],
            [{"id": "unknown"}],
        ],
    )
    def test_a_configured_list_with_nothing_usable_searches_nothing(self, monkeypatch, priority):
        self._configure(monkeypatch, priority)

        assert auto_download._configured_source_priority("audiobook") == []


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
        monkeypatch.setattr(auto_download, "app_config", _Config())
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

        def _search(source_name, _book_arg, **kwargs):
            searched.append((source_name, kwargs["user_id"]))
            if source_name == "first":
                epub = _release(source="first", title="Dungeon Crawler Carl.epub", format="epub")
                return None, [epub], None
            return None, [chosen], None

        monkeypatch.setattr(auto_download, "search_source_releases", _search)
        queued = []

        def _queue(release_data, priority, *, user_id, username):
            queued.append((release_data, priority, user_id, username))
            return True, None

        outcome = self._run(user_db, row, admin, queue_release=_queue)

        assert outcome.status == "queued"
        assert outcome.source == "second"
        # Searched as the requester, so their default languages apply.
        assert searched == [("first", reader["id"]), ("second", reader["id"])]

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
            "search_source_releases",
            lambda *_args, **_kwargs: (None, [chosen], None),
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
            "search_source_releases",
            lambda *_args, **_kwargs: (None, [wrong], None),
        )

        def _never_queue(*_args, **_kwargs):
            raise AssertionError("nothing should be queued")

        outcome = self._run(user_db, row, admin, queue_release=_never_queue)

        assert outcome.status == "no_match"
        assert user_db.get_request(row["id"])["status"] == "pending"

    def test_book_already_in_library_is_closed_before_searching(self, user_db, monkeypatch):
        admin = user_db.create_user(username="admin", role="admin")
        reader = user_db.create_user(username="reader", role="user")
        row = _pending_request(user_db, reader["id"])
        _stub_provider(monkeypatch, _book())
        monkeypatch.setattr(auto_download, "app_config", _Config())
        monkeypatch.setattr(
            "shelfmark.core.library_index.is_in_library", lambda *_args, **_kwargs: True
        )

        def _never_search(*_args, **_kwargs):
            raise AssertionError("release search must not run")

        monkeypatch.setattr(auto_download, "search_source_releases", _never_search)

        outcome = self._run(user_db, row, admin)

        assert outcome.status == "in_library"
        closed = user_db.get_request(row["id"])
        assert closed["status"] == "fulfilled"
        assert closed["delivery_state"] == "complete"
        assert closed["admin_note"] == auto_download.IN_LIBRARY_NOTE
        assert closed["reviewed_by"] == admin["id"]
        assert closed["release_data"] is None

    def test_owned_book_request_changed_mid_pass_is_left_alone(self, user_db, monkeypatch):
        admin = user_db.create_user(username="admin", role="admin")
        reader = user_db.create_user(username="reader", role="user")
        row = _pending_request(user_db, reader["id"])
        # An admin rejected it after the pass listed it as pending. The up-front re-read is
        # switched off so this reaches the guard that protects the narrow window after it.
        user_db.update_request(row["id"], status="rejected")
        monkeypatch.setattr(auto_download, "_acted_on_since_listed", lambda *_args: False)
        _stub_provider(monkeypatch, _book())
        monkeypatch.setattr(auto_download, "app_config", FakeConfig(LIBRARY_CHECK_ENABLED=True))
        monkeypatch.setattr("shelfmark.core.library_index.any_provider_enabled", lambda: True)
        monkeypatch.setattr(
            "shelfmark.core.library_index.is_in_library", lambda *_args, **_kwargs: True
        )

        outcome = self._run(user_db, row, admin)

        assert outcome.status == "in_library"
        assert user_db.get_request(row["id"])["status"] == "rejected"

    def _searched_request(self, user_db, monkeypatch, *, change=None, get_request=None):
        """Run one request through a search that records whether it ran and what it queued."""
        admin = user_db.create_user(username="admin", role="admin")
        reader = user_db.create_user(username="reader", role="user")
        row = _pending_request(user_db, reader["id"])
        _stub_provider(monkeypatch, _book())
        if change:
            user_db.update_request(row["id"], **change)
        if get_request:
            monkeypatch.setattr(user_db, "get_request", get_request)
        searches = []
        chosen = _release(source="first")

        def _search(*_args, **_kwargs):
            searches.append(1)
            return None, [chosen], None

        monkeypatch.setattr(auto_download, "search_source_releases", _search)
        outcome = self._run(user_db, row, admin)
        return outcome, searches

    def test_a_request_handled_by_hand_mid_pass_is_not_searched_again(self, user_db, monkeypatch):
        # The pass listed it as pending, then an admin fulfilled it through the UI.
        outcome, searches = self._searched_request(
            user_db, monkeypatch, change={"status": "fulfilled", "delivery_state": "queued"}
        )

        assert outcome.status == "skipped"
        assert searches == []

    def test_a_request_rejected_mid_pass_is_not_searched_again(self, user_db, monkeypatch):
        outcome, searches = self._searched_request(
            user_db, monkeypatch, change={"status": "rejected"}
        )

        assert outcome.status == "skipped"
        assert searches == []

    def test_a_request_still_pending_is_searched_and_queued(self, user_db, monkeypatch):
        outcome, searches = self._searched_request(user_db, monkeypatch)

        assert outcome.status == "queued"
        assert searches == [1]

    def test_an_unreadable_request_is_still_searched(self, user_db, monkeypatch):
        # A failed re-read must never stand between a request and its download.
        real = user_db.get_request
        calls = []

        def _broken_once(request_id):
            calls.append(request_id)
            if len(calls) == 1:  # the up-front re-read; fulfilling reads it again later
                raise RuntimeError("database is locked")
            return real(request_id)

        outcome, searches = self._searched_request(user_db, monkeypatch, get_request=_broken_once)

        assert outcome.status == "queued"
        assert searches == [1]

    def test_a_missing_request_row_is_still_searched(self, user_db, monkeypatch):
        outcome, searches = self._searched_request(
            user_db, monkeypatch, get_request=lambda _request_id: None
        )

        assert searches == [1]
        assert outcome.status in {"queued", "error"}  # fulfil may fail on a row that is gone

    def test_a_library_that_cannot_be_read_stops_the_request_before_searching(
        self, user_db, monkeypatch
    ):
        admin = user_db.create_user(username="admin", role="admin")
        reader = user_db.create_user(username="reader", role="user")
        row = _pending_request(user_db, reader["id"])
        _stub_provider(monkeypatch, _book())
        monkeypatch.setattr(auto_download, "app_config", _Config())

        def _is_in_library(_book, _content_type=None, *, strict=False):
            if strict:
                raise library_index.LibraryUnavailableError("Audiobookshelf is unavailable")
            return False  # what a fail-open check would have answered

        monkeypatch.setattr("shelfmark.core.library_index.is_in_library", _is_in_library)
        monkeypatch.setattr(
            auto_download,
            "search_source_releases",
            lambda *_a, **_k: pytest.fail("a request must not be searched for blind"),
        )

        outcome = self._run(user_db, row, admin)

        assert outcome.status == "error"
        assert "library" in outcome.detail
        assert user_db.get_request(row["id"])["status"] == "pending"

    def test_a_source_that_errored_is_an_error_not_a_missing_book(self, user_db, monkeypatch):
        admin = user_db.create_user(username="admin", role="admin")
        reader = user_db.create_user(username="reader", role="user")
        row = _pending_request(user_db, reader["id"])
        _stub_provider(monkeypatch, _book())
        monkeypatch.setattr(
            auto_download,
            "search_source_releases",
            lambda *_args, **_kwargs: (None, [], "indexer returned HTTP 503"),
        )

        outcome = self._run(user_db, row, admin)

        assert outcome.status == "error"
        assert "HTTP 503" in outcome.detail
        assert user_db.get_request(row["id"])["status"] == "pending"

    def test_a_working_source_still_queues_when_an_earlier_one_errored(self, user_db, monkeypatch):
        admin = user_db.create_user(username="admin", role="admin")
        reader = user_db.create_user(username="reader", role="user")
        row = _pending_request(user_db, reader["id"])
        _stub_provider(monkeypatch, _book())
        good = _release(title="Dune [M4B]")
        answers = {"first": (None, [], "timeout"), "second": (None, [good], None)}
        monkeypatch.setattr(
            auto_download,
            "search_source_releases",
            lambda source, *_args, **_kwargs: answers[source],
        )
        monkeypatch.setattr(auto_download, "strict_match", lambda *_a, **_k: True)
        queued = []

        outcome = self._run(
            user_db,
            row,
            admin,
            sources=["first", "second"],
            queue_release=lambda *a, **k: queued.append(a) or (True, None),
        )

        assert outcome.status == "queued"
        assert queued

    def test_every_source_clean_and_empty_is_still_no_match(self, user_db, monkeypatch):
        admin = user_db.create_user(username="admin", role="admin")
        reader = user_db.create_user(username="reader", role="user")
        row = _pending_request(user_db, reader["id"])
        _stub_provider(monkeypatch, _book())
        monkeypatch.setattr(
            auto_download, "search_source_releases", lambda *_a, **_k: (None, [], None)
        )

        assert self._run(user_db, row, admin).status == "no_match"

    # --- multi-book packs: the UI asks "single book or split?"; auto-download cannot ask -----

    @staticmethod
    def _inspector(monkeypatch, packs, calls=None, raises=None):
        """Stand in for the pre-download inspection the UI uses; `packs` are the pack source ids."""

        def _inspect(data):
            if calls is not None:
                calls.append(data["source_id"])
            if raises is not None:
                raise raises
            is_pack = data["source_id"] in packs
            return {"inspected": True, "plan": {"is_pack": is_pack, "books": []}}

        monkeypatch.setattr("shelfmark.core.release_inspect_routes.inspect_release", _inspect)

    def _pack_run(self, user_db, monkeypatch, releases):
        admin = user_db.create_user(username="admin", role="admin")
        reader = user_db.create_user(username="reader", role="user")
        row = _pending_request(user_db, reader["id"])
        _stub_provider(monkeypatch, _book())
        monkeypatch.setattr(
            auto_download,
            "search_source_releases",
            lambda *_a, **_k: (None, releases, None),
        )
        queued = []

        def _queue(release_data, priority, *, user_id, username):
            queued.append(release_data["source_id"])
            return True, None

        return self._run(user_db, row, admin, queue_release=_queue, sources=("first",)), queued

    @staticmethod
    def _file_inspector(monkeypatch, files_by_id, calls=None):
        def _inspect(data):
            if calls is not None:
                calls.append(data["source_id"])
            files = [{"path": p, "size": 1} for p in files_by_id[data["source_id"]]]
            return {"inspected": True, "files": files, "plan": {"is_pack": False, "books": []}}

        monkeypatch.setattr("shelfmark.core.release_inspect_routes.inspect_release", _inspect)

    def test_a_single_m4b_file_beats_a_better_ranked_chapter_set(self, user_db, monkeypatch):
        parts = [f"Part {n:02d} - Title.mp3" for n in range(1, 34)]
        self._file_inspector(monkeypatch, {"mp3set": parts, "one": ["Book.m4b"]})
        releases = [
            _release(
                source_id="mp3set",
                title="Dungeon Crawler Carl - Matt Dinniman",
                format=None,
                content_type="audiobook",
                seeders=50,
            ),
            _release(
                source_id="one",
                title="Dungeon Crawler Carl - Matt Dinniman",
                format=None,
                content_type="audiobook",
                seeders=5,
            ),
        ]

        outcome, queued = self._pack_run(user_db, monkeypatch, releases)

        assert (outcome.status, queued) == ("queued", ["one"])

    def test_with_no_single_file_release_the_best_ranked_is_queued(self, user_db, monkeypatch):
        parts = ["01.mp3", "02.mp3"]
        self._file_inspector(monkeypatch, {"a": parts, "b": parts})
        releases = [
            _release(
                source_id="a",
                title="Dungeon Crawler Carl - Matt Dinniman",
                format=None,
                content_type="audiobook",
                seeders=50,
            ),
            _release(
                source_id="b",
                title="Dungeon Crawler Carl - Matt Dinniman",
                format=None,
                content_type="audiobook",
                seeders=5,
            ),
        ]

        outcome, queued = self._pack_run(user_db, monkeypatch, releases)

        assert (outcome.status, queued) == ("queued", ["a"])

    def test_a_tagged_m4b_is_not_followed_by_extra_inspections(self, user_db, monkeypatch):
        calls: list[str] = []
        self._file_inspector(monkeypatch, {"a": ["x.m4b"], "b": ["y.m4b"]}, calls)
        releases = [_release(source_id="a", seeders=9), _release(source_id="b", seeders=1)]

        _outcome, queued = self._pack_run(user_db, monkeypatch, releases)

        assert (queued, calls) == (["a"], ["a"])

    # --- a thin single file must not beat a fuller copy just because it is one m4b -------------

    MB = 1_000_000

    def _sized(self, source_id, mb, *, fmt=None, seeders=1):
        return _release(
            source_id=source_id,
            title="Dungeon Crawler Carl - Matt Dinniman",
            format=fmt,
            content_type="audiobook",
            seeders=seeders,
            size_bytes=None if mb is None else mb * self.MB,
        )

    def _files(self, monkeypatch, one, sets):
        files = {sid: ["Book.m4b"] for sid in one}
        files.update({sid: [f"{n:02d}.mp3" for n in range(1, 30)] for sid in sets})
        self._file_inspector(monkeypatch, files)

    def test_a_release_in_the_wrong_language_is_passed_over_for_the_next_best(
        self, user_db, monkeypatch
    ):
        monkeypatch.setattr(auto_download, "_wanted_languages", lambda _uid: ["en"])
        self._files(monkeypatch, one=["hindi", "english"], sets=[])
        releases = [
            _release(
                source_id="hindi",
                title="Dungeon Crawler Carl (Hindi Edition) - Matt Dinniman",
                seeders=50,
            ),
            _release(source_id="english", title="Dungeon Crawler Carl - Matt Dinniman", seeders=5),
        ]

        _outcome, queued = self._pack_run(user_db, monkeypatch, releases)

        assert queued == ["english"]

    def test_when_every_release_is_in_the_wrong_language_nothing_is_queued(
        self, user_db, monkeypatch
    ):
        monkeypatch.setattr(auto_download, "_wanted_languages", lambda _uid: ["en"])
        releases = [_release(source_id="a", language="hi"), _release(source_id="b", language="es")]

        outcome, queued = self._pack_run(user_db, monkeypatch, releases)

        assert (outcome.status, queued) == ("no_match", [])

    # --- Soulseek results have no seeders: the peer's slot, queue and speed rank them ---------

    @staticmethod
    def _peer(source_id, *, slot, queue=0, speed=0, size=100):
        return _release(
            source="slskd",
            source_id=source_id,
            seeders=None,
            size_bytes=size * 1_000_000,
            extra={"has_free_upload_slot": slot, "queue_length": queue, "upload_speed": speed},
        )

    def test_a_peer_with_a_free_slot_beats_a_bigger_copy_that_must_queue(self):
        queued = self._peer("queued", slot=False, queue=3, size=900)
        free = self._peer("free", slot=True, size=100)

        assert auto_download.pick_best_release([queued, free]).source_id == "free"

    def test_a_free_slot_alone_beats_a_bigger_copy_with_the_same_queue_and_speed(self):
        taken = self._peer("taken", slot=False, queue=0, speed=100_000, size=900)
        free = self._peer("free", slot=True, queue=0, speed=100_000, size=100)

        assert auto_download.pick_best_release([taken, free]).source_id == "free"

    def test_a_shorter_queue_beats_a_longer_one(self):
        long_q = self._peer("long", slot=False, queue=40)
        short_q = self._peer("short", slot=False, queue=2)

        assert auto_download.pick_best_release([long_q, short_q]).source_id == "short"

    def test_a_faster_peer_beats_a_slower_one_when_the_rest_ties(self):
        slow = self._peer("slow", slot=True, speed=50_000)
        fast = self._peer("fast", slot=True, speed=900_000)

        assert auto_download.pick_best_release([slow, fast]).source_id == "fast"

    def test_releases_without_peer_details_still_rank_by_size(self):
        small = _release(source_id="small", size_bytes=100)
        big = _release(source_id="big", size_bytes=900)

        assert auto_download.pick_best_release([small, big]).source_id == "big"

    def test_format_still_outranks_the_peer(self):
        mp3 = self._peer("mp3", slot=True)
        mp3.format = "mp3"
        m4b = self._peer("m4b", slot=False, queue=9)
        m4b.format = "m4b"

        assert auto_download.pick_best_release([mp3, m4b]).source_id == "m4b"

    def test_a_thin_m4b_loses_to_a_fuller_chapter_set(self, user_db, monkeypatch):
        self._files(monkeypatch, one=["thin"], sets=["full"])
        releases = [self._sized("thin", 120, fmt="m4b"), self._sized("full", 480, fmt="mp3")]

        _outcome, queued = self._pack_run(user_db, monkeypatch, releases)

        assert queued == ["full"]

    def test_a_single_m4b_of_similar_size_still_wins(self, user_db, monkeypatch):
        self._files(monkeypatch, one=["one"], sets=["full"])
        releases = [self._sized("one", 340, fmt="m4b"), self._sized("full", 480, fmt="mp3")]

        _outcome, queued = self._pack_run(user_db, monkeypatch, releases)

        assert queued == ["one"]

    def test_an_unknown_size_is_not_treated_as_thin(self, user_db, monkeypatch):
        self._files(monkeypatch, one=["one"], sets=["full", "other"])
        releases = [
            self._sized("one", None, fmt="m4b"),
            self._sized("full", 480, fmt="mp3"),
            self._sized("other", 450, fmt="mp3"),
        ]

        _outcome, queued = self._pack_run(user_db, monkeypatch, releases)

        assert queued == ["one"]

    def _min_size(self, monkeypatch, value):
        orig = auto_download.app_config.get
        monkeypatch.setattr(
            auto_download.app_config,
            "get",
            lambda key, default=None, **kw: (
                value if key == auto_download.MIN_SIZE_SETTING else orig(key, default, **kw)
            ),
        )

    def test_the_default_is_two_thirds_of_the_largest_copy(self, user_db, monkeypatch):
        assert auto_download.DEFAULT_MIN_SIZE_PERCENT == 67
        self._files(monkeypatch, one=["one"], sets=["full"])
        # 310/480 is 64.6%: under two thirds, so it loses; 330/480 is 68.8%, so it wins
        _o, thin = self._pack_run(
            user_db, monkeypatch, [self._sized("one", 310, fmt="m4b"), self._sized("full", 480)]
        )
        assert thin == ["full"]

    def test_a_lower_setting_accepts_a_smaller_copy(self, user_db, monkeypatch):
        self._min_size(monkeypatch, 50)
        self._files(monkeypatch, one=["one"], sets=["full"])
        releases = [self._sized("one", 310, fmt="m4b"), self._sized("full", 480, fmt="mp3")]

        _outcome, queued = self._pack_run(user_db, monkeypatch, releases)

        assert queued == ["one"]

    def test_a_higher_setting_rejects_a_copy_that_the_default_accepts(self, user_db, monkeypatch):
        self._min_size(monkeypatch, 75)
        self._files(monkeypatch, one=["one"], sets=["full"])
        releases = [self._sized("one", 340, fmt="m4b"), self._sized("full", 480, fmt="mp3")]

        _outcome, queued = self._pack_run(user_db, monkeypatch, releases)

        assert queued == ["full"]

    def test_zero_turns_the_size_guard_off(self, user_db, monkeypatch):
        self._min_size(monkeypatch, 0)
        self._files(monkeypatch, one=["thin"], sets=["full"])
        releases = [self._sized("thin", 50, fmt="m4b"), self._sized("full", 480, fmt="mp3")]

        _outcome, queued = self._pack_run(user_db, monkeypatch, releases)

        assert queued == ["thin"]

    @pytest.mark.parametrize(
        ("configured", "percent"),
        [
            (None, 67),
            ("", 67),
            ("soon", 67),
            (float("nan"), 67),
            (50, 50),
            ("75", 75),
            (0, 0),
            (-5, 0),
            (250, 100),
        ],
    )
    def test_the_setting_is_read_and_kept_in_range(self, monkeypatch, configured, percent):
        self._min_size(monkeypatch, configured)

        assert auto_download._min_size_percent() == percent

    def test_the_setting_is_offered_with_its_range(self):
        from shelfmark.config import hardcover_sync_settings  # noqa: F401
        from shelfmark.core.settings_registry import get_settings_field_map

        field, _tab = get_settings_field_map()[auto_download.MIN_SIZE_SETTING]

        assert (field.default, field.min_value, field.max_value) == (67, 0, 100)

    def test_a_thin_m4b_is_still_queued_when_it_is_all_there_is(self, user_db, monkeypatch):
        self._files(monkeypatch, one=["thin"], sets=[])
        releases = [self._sized("thin", 120, fmt="m4b")]

        _outcome, queued = self._pack_run(user_db, monkeypatch, releases)

        assert queued == ["thin"]

    def test_an_enormous_outlier_does_not_make_every_real_copy_look_thin(
        self, user_db, monkeypatch
    ):
        self._files(monkeypatch, one=["one"], sets=["full", "huge"])
        releases = [
            self._sized("one", 400, fmt="m4b"),
            self._sized("full", 480, fmt="mp3"),
            self._sized("huge", 3000, fmt="mp3"),
        ]

        _outcome, queued = self._pack_run(user_db, monkeypatch, releases)

        assert queued == ["one"]

    def test_undersized_copies_are_moved_behind_the_rest_in_order(self):
        rels = [self._sized("a", 100, fmt="m4b"), self._sized("b", 500), self._sized("c", 450)]

        out = auto_download._demote_undersized(rels, "audiobook")

        assert [r.source_id for r in out] == ["b", "c", "a"]

    def test_ebooks_are_never_reordered_by_size(self):
        rels = [self._sized("a", 1, fmt="epub"), self._sized("b", 500, fmt="pdf")]

        assert auto_download._demote_undersized(rels, "ebook") == rels

    def test_a_tagged_m4b_that_cannot_be_inspected_is_still_not_followed_by_more_checks(
        self, user_db, monkeypatch
    ):
        calls: list[str] = []

        def _inspect(data):
            calls.append(data["source_id"])
            return {"inspected": False, "plan": None}

        monkeypatch.setattr("shelfmark.core.release_inspect_routes.inspect_release", _inspect)
        releases = [self._sized("a", 300, fmt="m4b"), self._sized("b", 290, fmt="m4b")]

        _outcome, queued = self._pack_run(user_db, monkeypatch, releases)

        assert (queued, calls) == (["a"], ["a"])

    def test_single_audio_file_detection(self):
        f = auto_download._is_single_audio_file
        assert f([{"path": "B/Book.m4b"}, {"path": "B/cover.jpg"}])
        assert not f([{"path": "01.mp3"}])  # one mp3 is not a container
        assert not f([{"path": "1.m4b"}, {"path": "2.m4b"}])
        assert not f(None)

    def test_a_pack_is_skipped_for_the_next_best_single_book(self, user_db, monkeypatch):
        self._inspector(monkeypatch, packs={"pack"})
        releases = [
            _release(source="audiobookbay", source_id="pack", seeders=50),
            _release(source="audiobookbay", source_id="single", seeders=5),
        ]

        outcome, queued = self._pack_run(user_db, monkeypatch, releases)

        assert (outcome.status, queued) == ("queued", ["single"])

    def test_when_every_candidate_is_a_pack_nothing_is_queued(self, user_db, monkeypatch):
        self._inspector(monkeypatch, packs={"p1", "p2"})
        releases = [_release(source_id="p1", seeders=9), _release(source_id="p2", seeders=8)]

        outcome, queued = self._pack_run(user_db, monkeypatch, releases)

        assert (outcome.status, queued) == ("no_match", [])
        assert "pack" in outcome.detail

    def test_only_the_best_few_candidates_are_inspected(self, user_db, monkeypatch):
        calls: list[str] = []
        ids = [f"p{n}" for n in range(6)]
        self._inspector(monkeypatch, packs=set(ids), calls=calls)
        releases = [_release(source_id=sid, seeders=100 - n) for n, sid in enumerate(ids)]

        outcome, queued = self._pack_run(user_db, monkeypatch, releases)

        assert len(calls) == auto_download.MAX_PACK_CHECKS == 3
        assert calls == ["p0", "p1", "p2"]  # best first
        assert (outcome.status, queued) == ("no_match", [])

    def test_a_release_the_source_cannot_inspect_is_queued_as_before(self, user_db, monkeypatch):
        monkeypatch.setattr(
            "shelfmark.core.release_inspect_routes.inspect_release",
            lambda _d: {"inspected": False, "plan": None},
        )

        outcome, queued = self._pack_run(user_db, monkeypatch, [_release(source_id="only")])

        assert (outcome.status, queued) == ("queued", ["only"])

    def test_an_inspector_that_fails_does_not_stop_the_download(self, user_db, monkeypatch):
        self._inspector(monkeypatch, packs=set(), raises=OSError("detail page down"))

        outcome, queued = self._pack_run(user_db, monkeypatch, [_release(source_id="only")])

        assert (outcome.status, queued) == ("queued", ["only"])

    def test_unregistered_provider_is_skipped(self, user_db, monkeypatch):
        admin = user_db.create_user(username="admin", role="admin")
        reader = user_db.create_user(username="reader", role="user")
        row = _pending_request(user_db, reader["id"], provider="nope")
        monkeypatch.setattr("shelfmark.metadata_providers.is_provider_registered", lambda _n: False)

        outcome = self._run(user_db, row, admin)

        assert outcome.status == "skipped"
        assert outcome.detail == "no usable provider/id"


def test_pending_pass_without_an_admin_queues_nothing(user_db, monkeypatch):
    monkeypatch.setattr(auto_download, "app_config", _Config(AUTO_DOWNLOAD_ENABLED=True))
    user_db.create_user(username="reader", role="user")

    summary = auto_download.auto_download_pending(
        user_db, queue_release=lambda *_args, **_kwargs: (True, None)
    )

    assert summary == {"queued": 0, "no_match": 0, "in_library": 0, "skipped": 0, "error": 0}


class TestDownloadCountRanking:
    def test_most_downloaded_copy_wins_before_size(self):
        big_obscure = _release(
            format="epub", seeders=None, size_bytes=9_000_000, extra={"downloads": 3}
        )
        popular = _release(
            format="epub", seeders=None, size_bytes=1_000_000, extra={"downloads": 4_200}
        )

        assert auto_download.pick_best_release([big_obscure, popular], "ebook") is popular

    def test_format_still_outranks_download_count(self):
        popular_mobi = _release(format="mobi", extra={"downloads": 10_000})
        quiet_epub = _release(format="epub", extra={"downloads": 2})

        assert auto_download.pick_best_release([popular_mobi, quiet_epub], "ebook") is quiet_epub

    def test_torrents_without_download_stats_fall_back_to_seeders(self):
        few = _release(format="m4b", seeders=2)
        many = _release(format="m4b", seeders=40)

        assert auto_download.pick_best_release([few, many], "audiobook") is many


class TestContentTypeAwareMatching:
    def test_epub_matches_an_ebook_request_but_not_an_audiobook_one(self):
        release = _release(format="epub", title="Dungeon Crawler Carl - Matt Dinniman.epub")

        assert auto_download.strict_match(release, _book(), content_type="ebook")
        assert not auto_download.strict_match(release, _book(), content_type="audiobook")

    def test_m4b_matches_an_audiobook_request_but_not_an_ebook_one(self):
        release = _release(format="m4b", title="Dungeon Crawler Carl - Matt Dinniman [M4B]")

        assert auto_download.strict_match(release, _book(), content_type="audiobook")
        assert not auto_download.strict_match(release, _book(), content_type="ebook")

    @pytest.mark.parametrize("fmt", ["rar", "zip", "7z", "tar", "gz"])
    def test_an_archive_is_never_an_audiobook_match(self, fmt):
        # A release that carries the requested content type is not enough: what is inside an
        # archive is unknown, and a Calibre ebook export once got through this way.
        release = _release(
            format=fmt,
            content_type="audiobook",
            title="Dungeon Crawler Carl - Matt Dinniman (html)",
        )

        assert not auto_download.strict_match(release, _book(), content_type="audiobook")

    def test_an_archive_is_not_an_ebook_match_either(self):
        release = _release(format="rar", title="Dungeon Crawler Carl - Matt Dinniman (html)")

        assert not auto_download.strict_match(release, _book(), content_type="ebook")

    def test_a_folder_of_audio_files_is_still_an_audiobook_match(self):
        release = _release(
            format="mp3", content_type="audiobook", title="Dungeon Crawler Carl - Matt Dinniman"
        )

        assert auto_download.strict_match(release, _book(), content_type="audiobook")

    def test_ebook_signal_in_title_counts_without_a_format(self):
        release = _release(format=None, title="Dungeon Crawler Carl - Matt Dinniman.epub")

        assert auto_download.strict_match(release, _book(), content_type="ebook")

    def test_audiobook_marker_disqualifies_an_ebook_match(self):
        release = _release(format="epub", title="Dungeon Crawler Carl - Matt Dinniman (Unabridged)")

        assert not auto_download.strict_match(release, _book(), content_type="ebook")

    def test_ebook_ranking_prefers_epub_then_azw3_then_mobi(self):
        mobi = _release(format="mobi", seeders=50)
        azw3 = _release(format="azw3", seeders=1)
        epub = _release(format="epub", seeders=0)

        assert auto_download.pick_best_release([mobi, azw3, epub], "ebook") is epub
        assert auto_download.pick_best_release([mobi, azw3], "ebook") is azw3

    def test_ebook_formats_follow_supported_formats_setting(self, monkeypatch):
        monkeypatch.setattr("shelfmark.core.config.config", _Config(SUPPORTED_FORMATS=["pdf"]))

        assert auto_download._ebook_formats() == {"pdf"}
        assert auto_download.strict_match(
            _release(format="pdf", title="Dungeon Crawler Carl - Matt Dinniman"),
            _book(),
            content_type="ebook",
        )


class TestContentTypeSourcePriority:
    @pytest.fixture(autouse=True)
    def _sources(self, monkeypatch):
        monkeypatch.setattr(
            "shelfmark.release_sources.list_available_sources",
            lambda: [
                {"name": "audio_only", "enabled": True, "supported_content_types": ["audiobook"]},
                {"name": "ebook_only", "enabled": True, "supported_content_types": ["ebook"]},
                {"name": "both", "enabled": True},
            ],
        )

    def test_ebook_priority_uses_its_own_key_and_only_ebook_capable_sources(self, monkeypatch):
        monkeypatch.setattr(
            auto_download,
            "app_config",
            _Config(
                AUTO_DOWNLOAD_SOURCE_PRIORITY=[{"id": "audio_only"}],
                AUTO_DOWNLOAD_EBOOK_SOURCE_PRIORITY=[{"id": "both"}, {"id": "audio_only"}],
            ),
        )

        assert auto_download._configured_source_priority("ebook") == ["both"]
        assert auto_download._configured_source_priority("audiobook") == ["audio_only"]

    def test_fallback_is_filtered_by_content_type(self, monkeypatch):
        monkeypatch.setattr(auto_download, "app_config", _Config())

        assert auto_download._configured_source_priority("ebook") == ["ebook_only", "both"]
        assert auto_download._configured_source_priority("audiobook") == ["audio_only", "both"]


def test_pending_pass_follows_each_request_content_type(user_db, monkeypatch):
    monkeypatch.setattr(auto_download, "app_config", _Config(AUTO_DOWNLOAD_ENABLED=True))
    admin = user_db.create_user(username="admin", role="admin")
    for content_type in ("ebook", "audiobook"):
        user_db.create_request(
            user_id=admin["id"],
            content_type=content_type,
            request_level="book",
            policy_mode="request_book",
            book_data={"title": "T", "author": "A", "provider": "hardcover", "provider_id": "1"},
        )
    seen: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(
        auto_download,
        "_configured_source_priority",
        lambda content_type: ["src"] if content_type == "ebook" else [],
    )

    def _fake_request(_user_db, row, *, sources, content_type, **_kwargs):
        seen.append((content_type, sources))
        return auto_download.AutoDownloadOutcome(int(row["id"]), "no_match")

    monkeypatch.setattr(auto_download, "auto_download_request", _fake_request)

    summary = auto_download.auto_download_pending(
        user_db, queue_release=lambda *_args, **_kwargs: (True, None)
    )

    assert seen == [("ebook", ["src"])]
    assert summary["no_match"] == 1
    assert summary["skipped"] == 1  # the audiobook request had no usable sources


class TestNoMatchBackoff:
    """A request that keeps finding nothing is searched less often."""

    NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)

    @staticmethod
    def _book_data(passes, hours_ago, now=NOW):
        return {
            auto_download.BACKOFF_KEY: {
                "no_match_passes": passes,
                "last_searched_at": (now - timedelta(hours=hours_ago)).isoformat(),
            }
        }

    @pytest.mark.parametrize(
        ("passes", "hours_ago", "due"),
        [
            (0, 0, True),
            (2, 0, True),  # the first passes run at full rate
            (3, 6, False),  # then once a day
            (3, 23.5, True),  # a cycle a little early is on time
            (3, 24, True),
            (4, 30, False),  # then every two days
            (4, 48, True),
            (5, 60, False),  # then at most the setting (3 days)
            (5, 72, True),
            (50, 60, False),
            (50, 72, True),
        ],
    )
    def test_the_wait_grows_with_the_passes_and_stops_at_the_setting(self, passes, hours_ago, due):
        book_data = self._book_data(passes, hours_ago)

        assert auto_download._search_is_due(book_data, self.NOW, 3) is due

    def test_a_smaller_setting_caps_the_wait(self):
        book_data = self._book_data(5, 30)

        assert auto_download._search_is_due(book_data, self.NOW, 1) is True
        assert auto_download._search_is_due(book_data, self.NOW, 3) is False

    def test_zero_turns_the_backoff_off(self):
        assert auto_download._search_is_due(self._book_data(50, 0), self.NOW, 0) is True

    @pytest.mark.parametrize(
        "book_data",
        [
            {},
            {auto_download.BACKOFF_KEY: "garbage"},
            {auto_download.BACKOFF_KEY: {"no_match_passes": 9}},
            {auto_download.BACKOFF_KEY: {"no_match_passes": 9, "last_searched_at": "not a date"}},
            {auto_download.BACKOFF_KEY: {"no_match_passes": "many", "last_searched_at": None}},
        ],
    )
    def test_anything_doubtful_is_searched(self, book_data):
        assert auto_download._search_is_due(book_data, self.NOW, 3) is True

    def _pass(self, user_db, monkeypatch, *, outcome="no_match"):
        user_db.create_user(username="admin", role="admin")
        reader = user_db.create_user(username="reader", role="user")
        row = _pending_request(user_db, reader["id"])
        monkeypatch.setattr(auto_download, "app_config", FakeConfig(AUTO_DOWNLOAD_ENABLED=True))
        monkeypatch.setattr(auto_download, "_configured_source_priority", lambda _t: ["src"])
        searched = []

        def _fake_request(_db, request_row, **_kwargs):
            searched.append(int(request_row["id"]))
            return auto_download.AutoDownloadOutcome(int(request_row["id"]), outcome)

        monkeypatch.setattr(auto_download, "auto_download_request", _fake_request)
        return row, searched

    def _run_pass(self, user_db, **kwargs):
        return auto_download.auto_download_pending(
            user_db, queue_release=lambda *_a, **_k: (True, None), **kwargs
        )

    def _stored(self, user_db, row):
        return user_db.get_request(row["id"])["book_data"].get(auto_download.BACKOFF_KEY)

    def test_each_no_match_pass_is_counted_and_dated(self, user_db, monkeypatch):
        row, searched = self._pass(user_db, monkeypatch)

        self._run_pass(user_db)
        self._run_pass(user_db)

        stored = self._stored(user_db, row)
        assert searched == [int(row["id"])] * 2
        assert stored["no_match_passes"] == 2
        assert datetime.fromisoformat(stored["last_searched_at"]).tzinfo is not None

    def test_a_request_inside_its_wait_is_skipped_not_searched(self, user_db, monkeypatch):
        row, searched = self._pass(user_db, monkeypatch)
        book_data = {**row["book_data"], **self._book_data(3, 1, datetime.now(UTC))}
        user_db.update_request(row["id"], book_data=book_data)

        summary = self._run_pass(user_db)

        assert searched == []
        assert summary["skipped"] == 1

    def test_sync_now_searches_a_request_inside_its_wait(self, user_db, monkeypatch):
        row, searched = self._pass(user_db, monkeypatch)
        book_data = {**row["book_data"], **self._book_data(3, 1, datetime.now(UTC))}
        user_db.update_request(row["id"], book_data=book_data)

        self._run_pass(user_db, ignore_backoff=True)

        assert searched == [int(row["id"])]

    def test_the_setting_at_zero_searches_every_pass(self, user_db, monkeypatch):
        row, searched = self._pass(user_db, monkeypatch)
        monkeypatch.setattr(
            auto_download,
            "app_config",
            FakeConfig(AUTO_DOWNLOAD_ENABLED=True, AUTO_DOWNLOAD_BACKOFF_MAX_DAYS=0),
        )
        book_data = {**row["book_data"], **self._book_data(9, 1, datetime.now(UTC))}
        user_db.update_request(row["id"], book_data=book_data)

        self._run_pass(user_db)

        assert searched == [int(row["id"])]

    @pytest.mark.parametrize("outcome", ["queued", "in_library"])
    def test_a_match_starts_the_count_over(self, user_db, monkeypatch, outcome):
        row, _searched = self._pass(user_db, monkeypatch, outcome=outcome)
        book_data = {**row["book_data"], **self._book_data(5, 99, datetime.now(UTC))}
        user_db.update_request(row["id"], book_data=book_data)

        self._run_pass(user_db)

        assert self._stored(user_db, row) is None

    @pytest.mark.parametrize("outcome", ["error", "skipped"])
    def test_a_failed_search_does_not_count_against_the_request(
        self, user_db, monkeypatch, outcome
    ):
        row, _searched = self._pass(user_db, monkeypatch, outcome=outcome)

        self._run_pass(user_db)

        assert self._stored(user_db, row) is None

    def test_the_other_book_data_survives_the_bookkeeping(self, user_db, monkeypatch):
        row, _searched = self._pass(user_db, monkeypatch)
        book_data = {**row["book_data"], "failed_releases": [{"source": "x", "source_id": "1"}]}
        user_db.update_request(row["id"], book_data=book_data)

        self._run_pass(user_db)

        stored = user_db.get_request(row["id"])["book_data"]
        assert stored["failed_releases"] == [{"source": "x", "source_id": "1"}]
        assert stored["title"] == "Dungeon Crawler Carl"
        assert stored[auto_download.BACKOFF_KEY]["no_match_passes"] == 1

    def test_a_bookkeeping_failure_never_takes_the_pass_with_it(self, user_db, monkeypatch):
        row, searched = self._pass(user_db, monkeypatch)
        real = user_db.update_request

        def _fail_for_book_data(request_id, **kwargs):
            if "book_data" in kwargs:
                raise RuntimeError("database is locked")
            return real(request_id, **kwargs)

        monkeypatch.setattr(user_db, "update_request", _fail_for_book_data)

        summary = self._run_pass(user_db)

        assert searched == [int(row["id"])]
        assert summary["no_match"] == 1


def test_one_metadata_provider_serves_a_whole_pass(user_db, monkeypatch):
    admin = user_db.create_user(username="admin", role="admin")
    reader = user_db.create_user(username="reader", role="user")
    rows = [_pending_request(user_db, reader["id"], provider_id=str(n)) for n in (1, 2, 3)]
    built = []

    class _Provider:
        def get_book(self, _book_id):
            return None  # "book not found": the request ends before any search

    def _get_provider(_name, **_kwargs):
        built.append(1)
        return _Provider()

    monkeypatch.setattr(
        "shelfmark.metadata_providers.is_provider_registered", lambda name: name == "hardcover"
    )
    monkeypatch.setattr("shelfmark.metadata_providers.get_provider_kwargs", lambda _n: {})
    monkeypatch.setattr("shelfmark.metadata_providers.get_provider", _get_provider)
    providers: dict = {}

    for row in rows:
        auto_download.auto_download_request(
            user_db,
            row,
            sources=["src"],
            content_type="audiobook",
            min_seeders=1,
            queue_release=lambda *_a, **_k: (True, None),
            admin_user_id=admin["id"],
            providers=providers,
        )

    assert len(built) == 1


def test_without_a_shared_dict_each_request_still_builds_its_own_provider(user_db, monkeypatch):
    admin = user_db.create_user(username="admin", role="admin")
    reader = user_db.create_user(username="reader", role="user")
    rows = [_pending_request(user_db, reader["id"], provider_id=str(n)) for n in (1, 2)]
    built = []

    class _Provider:
        def get_book(self, _book_id):
            return None

    monkeypatch.setattr(
        "shelfmark.metadata_providers.is_provider_registered", lambda name: name == "hardcover"
    )
    monkeypatch.setattr("shelfmark.metadata_providers.get_provider_kwargs", lambda _n: {})
    monkeypatch.setattr(
        "shelfmark.metadata_providers.get_provider",
        lambda _n, **_k: built.append(1) or _Provider(),
    )

    for row in rows:
        auto_download.auto_download_request(
            user_db,
            row,
            sources=["src"],
            content_type="audiobook",
            min_seeders=1,
            queue_release=lambda *_a, **_k: (True, None),
            admin_user_id=admin["id"],
        )

    assert len(built) == 2


class TestPackGuard:
    @pytest.mark.parametrize(
        "pack_title",
        [
            "Jack Reacher 1-28 + Short Stories - Complete to date as of 2023 Dick Hill, Jeff Harding",
            "Jack Reacher (#1-24)",
            "Jack Reacher Series Books 1 to 17 ( Multi-file )",
            "Jack Reacher Collection (1-22)",
            "Jack Reacher Series (All 17 Audiobooks)",
            "Jack Reacher 29 In Too Deep + Short Stories, Killing Floor better quality || Complete to date 2024",
        ],
    )
    def test_multi_book_packs_are_rejected(self, pack_title):
        book = BookMetadata(
            provider="hardcover", provider_id="1", title="In Too Deep", authors=["Lee Child"]
        )
        release = _release(title=pack_title, format="m4b", extra={"author": "Lee Child"})

        assert text_match.is_bundle_title(release.title, book.title)
        assert not auto_download.strict_match(release, book, content_type="audiobook")

    @pytest.mark.parametrize(
        ("wanted", "release_title"),
        [
            ("61 Hours", "61 Hours (JR Book 14)"),
            ("Deep Down", "Jack Reacher 16.5: Deep Down"),
            ("The Midnight Line", "[Jack Reacher 22] - The Midnight Line"),
            ("The Complete Persepolis", "The Complete Persepolis - Marjane Satrapi"),
            ("Catch-22", "Catch-22 - Joseph Heller"),
        ],
    )
    def test_single_titles_are_not_mistaken_for_packs(self, wanted, release_title):
        assert not text_match.is_bundle_title(release_title, wanted)


class TestOtherWorkGuard:
    @pytest.mark.parametrize(
        ("wanted", "release_title"),
        [
            ("Dune", "Dune Messiah - Frank Herbert [m4b]"),
            ("Dune", "Frank Herbert - Children of Dune (Unabridged)"),
            ("Dune", "Dune Messiah (Dune Chronicles #2) by Frank Herbert"),
            ("Dune", "Frank.Herbert.-.Dune.Messiah.2007.m4b"),
        ],
    )
    def test_a_longer_title_naming_another_work_is_rejected(self, wanted, release_title):
        book = _book(title=wanted, authors=["Frank Herbert"])
        release = _release(title=release_title)

        assert not auto_download.strict_match(release, book, audiobook_formats=AUDIOBOOK_FORMATS)

    @pytest.mark.parametrize(
        ("wanted", "author", "series", "release_title"),
        [
            ("Dune", "Frank Herbert", None, "Dune - Frank Herbert (Narrated by Scott Brick) [m4b]"),
            (
                "Dune",
                "Frank Herbert",
                None,
                "Dune by Frank Herbert - Read by Simon Vance [Unabridged]",
            ),
            ("Dune", "Frank Herbert", None, "Dune: Deluxe Edition - Frank Herbert [m4b]"),
            ("Dune", "Frank Herbert", None, "Frank.Herbert.-.Dune.2007.Unabridged.m4b"),
            (
                "Leviathan Wakes",
                "James S. A. Corey",
                None,
                "Leviathan Wakes: The Expanse, Book 1 - James S. A. Corey [m4b]",
            ),
            (
                "Cross Kill",
                "James Patterson",
                "Alex Cross",
                "Alex Cross 25 - Cross Kill - James Patterson [m4b]",
            ),
            ("Star Wars: Thrawn", "Timothy Zahn", None, "Star Wars: Thrawn - Timothy Zahn [m4b]"),
        ],
    )
    def test_the_same_book_wrapped_in_release_noise_still_matches(
        self, wanted, author, series, release_title
    ):
        book = _book(title=wanted, authors=[author], series_name=series)
        release = _release(title=release_title)

        assert auto_download.strict_match(release, book, audiobook_formats=AUDIOBOOK_FORMATS)


def _matches(book, release_title):
    release = _release(title=release_title)
    return auto_download.strict_match(release, book, audiobook_formats=AUDIOBOOK_FORMATS)


class TestDerivativeGuard:
    """Study aids, cut-down recordings and dramatisations are not the book, wherever named."""

    @pytest.mark.parametrize(
        "release_title",
        [
            "Dune: Summary & Analysis - Frank Herbert [m4b]",
            "Dune: Summary of the Novel by Frank Herbert [m4b]",
            "Dune (Abridged) - Frank Herbert [m4b]",
            "Dune - Abridged Edition - Frank Herbert [m4b]",
            "Dune [Dramatized Adaptation] - Frank Herbert [m4b]",
            "Dune - Full Cast Dramatization - Frank Herbert [m4b]",
            "Dune (BBC Radio Adaptation) - Frank Herbert [m4b]",
            "Dune: A Study Guide - Frank Herbert [m4b]",
            "Dune (Workbook) - Frank Herbert [m4b]",
            "Dune (Graphic Novel) - Frank Herbert [m4b]",
        ],
    )
    def test_a_derivative_named_in_any_segment_is_rejected(self, release_title):
        book = _book(title="Dune", authors=["Frank Herbert"])

        assert not _matches(book, release_title)

    @pytest.mark.parametrize(
        "release_title",
        [
            "Dune (Unabridged) - Frank Herbert [m4b]",
            "Dune (Un-Abridged) - Frank Herbert [m4b]",
            "Dune - Complete and Unabridged - Frank Herbert [m4b]",
            "Dune - Frank Herbert - Unabridged Audiobook [m4b]",
        ],
    )
    def test_an_unabridged_recording_still_matches(self, release_title):
        book = _book(title="Dune", authors=["Frank Herbert"])

        assert _matches(book, release_title)

    def test_a_marker_the_requested_title_carries_is_not_held_against_the_release(self):
        summary = _book(title="The Summary", authors=["Some Author"])
        graphic = _book(title="Dune: The Graphic Novel, Book 1", authors=["Frank Herbert"])

        assert _matches(summary, "The Summary - Some Author [m4b]")
        assert not auto_download._names_derivative(
            graphic, "Dune: The Graphic Novel, Book 1 - Frank Herbert"
        )


class TestTwoInOneGuard:
    @pytest.mark.parametrize(
        "release_title",
        [
            "Dune / Dune Messiah - Frank Herbert [m4b]",
            "Dune Messiah / Dune - Frank Herbert [m4b]",
            "Dune / Dune Messiah / Children of Dune - Frank Herbert [m4b]",
        ],
    )
    def test_two_slash_separated_works_are_rejected(self, release_title):
        book = _book(title="Dune", authors=["Frank Herbert"], series_name="Dune")

        assert not _matches(book, release_title)

    @pytest.mark.parametrize(
        "release_title",
        [
            "Dune / Frank Herbert / Scott Brick [m4b]",
            "Dune / Dune (Unabridged) - Frank Herbert [m4b]",
            "Dune - Frank Herbert [m4b/64kbps]",
        ],
    )
    def test_a_slash_that_separates_anything_else_is_fine(self, release_title):
        book = _book(title="Dune", authors=["Frank Herbert"], series_name="Dune")

        assert _matches(book, release_title)


class TestSeriesLabelGuard:
    """A segment that is just the series name does not vouch for a one-word title."""

    @pytest.mark.parametrize(
        "release_title",
        [
            "Alex Cross - Cross Justice - James Patterson [m4b]",
            "Alex Cross: Cross Justice - James Patterson [m4b]",
            "Alex Cross 25 - Cross Kill - James Patterson [m4b]",
            "Alex Cross Series #11 - Cross Justice - James Patterson [m4b]",
            "Double Cross - James Patterson [m4b]",
        ],
    )
    def test_another_book_in_the_series_is_rejected(self, release_title):
        book = _book(title="Cross", authors=["James Patterson"], series_name="Alex Cross")

        assert not _matches(book, release_title)

    @pytest.mark.parametrize(
        "release_title",
        [
            "Cross - James Patterson [m4b]",
            "Alex Cross - Cross - James Patterson [m4b]",
            "Alex Cross 12: Cross - James Patterson [m4b]",
            "Cross (Alex Cross #12) - James Patterson [m4b]",
            "Cross - Alex Cross 12 - James Patterson [m4b]",
            "Alex Cross Series #12 - Cross - James Patterson [m4b]",
        ],
    )
    def test_the_book_itself_under_its_series_label_still_matches(self, release_title):
        book = _book(title="Cross", authors=["James Patterson"], series_name="Alex Cross")

        assert _matches(book, release_title)

    @pytest.mark.parametrize(
        "release_title",
        [
            "Star Wars Thrawn - Timothy Zahn [m4b]",
            "Star.Wars.Thrawn.Timothy.Zahn.m4b",
            "Thrawn - Timothy Zahn (Star Wars: Thrawn) [m4b]",
        ],
    )
    def test_a_franchise_label_written_into_the_title_still_matches(self, release_title):
        book = _book(title="Thrawn", authors=["Timothy Zahn"], series_name="Star Wars: Thrawn")

        assert _matches(book, release_title)

    def test_a_later_book_under_the_franchise_label_is_rejected(self):
        book = _book(title="Thrawn", authors=["Timothy Zahn"], series_name="Star Wars: Thrawn")

        assert not _matches(book, "Star Wars: Thrawn Ascendancy - Timothy Zahn [m4b]")


class TestSequelNumberGuard:
    @pytest.mark.parametrize(
        ("wanted", "series", "release_title"),
        [
            ("Mistborn", "Mistborn", "Mistborn 2 - Brandon Sanderson [m4b]"),
            ("Mistborn", "Mistborn", "Mistborn Book 2 - Brandon Sanderson [m4b]"),
            ("Mistborn", "Mistborn", "Mistborn 2: The Well of Ascension - Brandon Sanderson"),
            ("Dune", "Dune Chronicles", "Dune Chronicles Book 2 - Frank Herbert [m4b]"),
            ("Dune", "Dune", "Dune.2.Frank.Herbert.m4b"),
        ],
    )
    def test_a_bare_number_after_the_title_names_a_sequel(self, wanted, series, release_title):
        author = "Brandon Sanderson" if wanted == "Mistborn" else "Frank Herbert"
        book = _book(title=wanted, authors=[author], series_name=series)

        assert not _matches(book, release_title)

    @pytest.mark.parametrize(
        ("wanted", "series", "release_title"),
        [
            ("Mistborn", "Mistborn", "Mistborn 1 - The Final Empire - Brandon Sanderson [m4b]"),
            ("Dune", "Dune", "Dune 1 - Frank Herbert [m4b]"),
            ("Dune", "Dune", "Dune.2021.Frank.Herbert.m4b"),
            ("Dune", "Dune Chronicles", "Dune (Dune Chronicles, Book 1) - Frank Herbert [m4b]"),
            ("Dune", "Dune Chronicles", "Dune Chronicles Book 1 - Frank Herbert [m4b]"),
            ("Dune", "Dune", "Dune - Frank Herbert - Dune Chronicles Book 1 [m4b]"),
            ("Dune", "Dune", "Dune (40th Anniversary Edition) - Frank Herbert [m4b]"),
            ("Dune", "Dune", "Dune (2 of 6) - Frank Herbert [m4b]"),
        ],
    )
    def test_a_first_volume_year_or_index_is_not_a_sequel(self, wanted, series, release_title):
        author = "Brandon Sanderson" if wanted == "Mistborn" else "Frank Herbert"
        book = _book(title=wanted, authors=[author], series_name=series)

        assert _matches(book, release_title)

    def test_a_number_the_title_carries_is_not_a_sequel(self):
        book = _book(title="Catch-22", authors=["Joseph Heller"])

        assert _matches(book, "Catch-22 - Joseph Heller [m4b]")


def test_every_processable_audiobook_format_outranks_an_unknown_one():
    flac = _release(format="flac", title="Dungeon Crawler Carl - Matt Dinniman [FLAC]")
    unknown = _release(format="xyz", title="Dungeon Crawler Carl - Matt Dinniman", seeders=99)
    m4b = _release(format="m4b")

    assert auto_download.pick_best_release([unknown, flac]) is flac
    assert auto_download.pick_best_release([flac, m4b]) is m4b


def _abb(title, author=None, *, fmt="mp3", keywords=None):
    """A release the way the AudiobookBay source builds one: the title split off the
    author, the listing title kept raw, no seeder count."""
    raw = f"{title} - {author}" if author else title
    extra = {"author": author, "title_raw": raw}
    if keywords is not None:
        extra["keywords"] = keywords
    return _release(
        source="audiobookbay",
        source_id=raw,
        title=title,
        format=fmt,
        seeders=None,
        indexer="AudiobookBay",
        content_type="audiobook",
        extra=extra,
    )


def _tolkien(title, **overrides):
    fields = {"title": title, "authors": ["J.R.R. Tolkien"], "search_author": "Tolkien"}
    fields.update(overrides)
    return _book(**fields)


class TestAccentedTitles:
    """A provider's accents and a release's need not agree: Húrin is Hurin."""

    @pytest.mark.parametrize(
        ("wanted", "release_title", "author"),
        [
            ("The Children of Hurin", "The Children of Húrin", "J. R. R. Tolkien"),
            ("The Children of Hurin", "The Children of Húrin", "J. R. R. Tolkien"),
            ("The Children of Húrin", "The Children of Hurin", "J.R.R. Tolkien"),
            ("The Fall of Númenor", "The Fall of Numenor", "J.R.R. Tolkien, Brian Sibley (editor)"),
            ("Beren and Lúthien", "Beren and Luthien", "J.R.R. Tolkien"),
        ],
    )
    def test_the_same_title_with_or_without_accents_matches(self, wanted, release_title, author):
        book = _tolkien(wanted)

        assert auto_download.strict_match(
            _abb(release_title, author), book, audiobook_formats=AUDIOBOOK_FORMATS
        )

    def test_the_provider_search_title_is_folded_too(self):
        book = _tolkien(
            "The Fall of Númenor: and Other Tales from the Second Age of Middle-earth",
            subtitle="and Other Tales from the Second Age of Middle-earth",
            search_title="The Fall of Númenor",
        )
        release = _abb("The Fall of Numenor", "J.R.R. Tolkien, Brian Sibley (editor)", fmt="m4b")

        assert auto_download.strict_match(release, book, audiobook_formats=AUDIOBOOK_FORMATS)

    @pytest.mark.parametrize(
        ("wanted", "release_title", "author"),
        [
            ("The Children of Húrin", "The Fall of Numenor", "J.R.R. Tolkien"),
            ("The Fall of Númenor", "The Fall of Gondolin", "J.R.R. Tolkien"),
            ("The Children of Húrin", "Beren and Luthien", "J.R.R. Tolkien"),
            ("The Children of Húrin", "The Children of Húrin: Summary & Analysis", "Tolkien"),
            (
                "The Children of Húrin",
                "Lord of the Rings (Complete Tolkien works)",
                "J.R.R Tolkien",
            ),
            ("The Children of Húrin", "The Children of Húrin", "Alan Lee"),
        ],
    )
    def test_another_title_edition_or_author_is_still_rejected(self, wanted, release_title, author):
        book = _tolkien(wanted)

        assert not auto_download.strict_match(
            _abb(release_title, author), book, audiobook_formats=AUDIOBOOK_FORMATS
        )


class TestAlternateTitles:
    """ "The Hobbit, or There and Back Again" is matched as "The Hobbit", the way the
    provider now searches it, and a release spelling out the whole pair is the same book."""

    HOBBIT = {
        "title": "The Hobbit, or There and Back Again",
        "search_title": "The Hobbit",
        "series_name": "Middle Earth",
    }
    MUMMY = {
        "title": "The Mummy, or Ramses the Damned",
        "search_title": "The Mummy",
        "authors": ["Anne Rice"],
        "series_name": "Ramses the Damned",
        "subtitle": "The Passion of Cleopatra",
    }

    @pytest.mark.parametrize(
        ("release_title", "author"),
        [
            ("The Hobbit", "J.R.R. Tolkien"),
            ("The Hobbit", "J. R. R. Tolkien"),
            ("The Hobbit (Andy Serkis)", "J. R. R. Tolkien"),
            ("The Hobbit, or There and Back Again", "J.R.R. Tolkien"),
            ("The Hobbit: There and Back Again (Unabridged)", "J.R.R. Tolkien"),
        ],
    )
    def test_the_hobbit_with_or_without_its_alternate_title_matches(self, release_title, author):
        book = _tolkien(**self.HOBBIT)

        assert auto_download.strict_match(
            _abb(release_title, author), book, audiobook_formats=AUDIOBOOK_FORMATS
        )

    @pytest.mark.parametrize(
        ("release_title", "author"),
        [
            ("The Hobbit: 1979 Mind’s Eye Radio Drama", "J.R.R. Tolkien"),
            ("The Hobbit: The BBC TV Soundtrack of the Jackanory Multi-Voice Reading", "Tolkien"),
            (
                "The Hobbit + Lord of the Rings Trilogy (Theatrical Experience), Chapterized",
                "Tolkien",
            ),
            ("The Hobbit + The Lord of the Rings Trilogy, Chapterized [128 kbps]", "Tolkien"),
            ("The Hobbit (Graphic Novel)", "J.R.R. Tolkien"),
            ("The Hobbit (Abridged)", "J.R.R. Tolkien"),
            ("The Hobbit", "Peter Jackson"),
            ("The Hobbit: An Unexpected Journey", "Jude Fisher"),
        ],
    )
    def test_dramatisations_packs_and_other_hobbits_are_still_rejected(self, release_title, author):
        book = _tolkien(**self.HOBBIT)

        assert not auto_download.strict_match(
            _abb(release_title, author), book, audiobook_formats=AUDIOBOOK_FORMATS
        )

    def test_the_mummy_matches_the_bare_main_title_once_the_author_is_known(self):
        book = _book(**self.MUMMY)

        assert auto_download.strict_match(
            _abb("The Mummy or Ramses the Damned", "Anne Rice"),
            book,
            audiobook_formats=AUDIOBOOK_FORMATS,
        )
        assert auto_download.strict_match(
            _abb("The Mummy", "Anne Rice"), book, audiobook_formats=AUDIOBOOK_FORMATS
        )

    @pytest.mark.parametrize(
        ("release_title", "author"),
        [
            # Without an author the listing is not trusted, main title or not.
            ("The Mummy or Ramses the Damned", None),
            # Another author's Mummy.
            ("The Mummy", "Max Allan Collins"),
            ("The Mummy: Tomb of the Dragon Emperor", "Max Allan Collins"),
            # The sequel, whose title the provider repeats as this book's subtitle.
            ("Ramses the Damned: The Passion of Cleopatra", "Anne Rice"),
            ("The Mummy, or Ramses the Damned (Abridged)", "Anne Rice"),
            ("Anne Rice Collection", "Anne Rice"),
        ],
    )
    def test_other_mummies_and_the_sequel_are_still_rejected(self, release_title, author):
        book = _book(**self.MUMMY)

        assert not auto_download.strict_match(
            _abb(release_title, author), book, audiobook_formats=AUDIOBOOK_FORMATS
        )


class TestNumberedVolumes:
    """ "Part II", "Part Two" and "Part 2" are one volume; "Part One" is another."""

    LOST_TALES = {
        "title": "The Book of Lost Tales, Part II",
        "subtitle": "Part II",
        "series_name": "The History of Middle-Earth",
    }

    @pytest.mark.parametrize(
        "release_title",
        [
            "The Book of Lost Tales, Part Two",
            "The Book of Lost Tales, Part 2",
            "The Book of Lost Tales, Part II",
            "The Book of Lost Tales Part 02",
        ],
    )
    def test_the_volume_matches_however_its_number_is_written(self, release_title):
        book = _tolkien(**self.LOST_TALES)

        assert auto_download.strict_match(
            _abb(release_title, "J.R.R. Tolkien"), book, audiobook_formats=AUDIOBOOK_FORMATS
        )

    @pytest.mark.parametrize(
        ("release_title", "author"),
        [
            ("The Book of Lost Tales, Part One", "J.R.R. Tolkien"),
            ("The Book of Lost Tales, Part 1", "J.R.R. Tolkien"),
            ("The Book of Lost Tales, Part I", "J.R.R. Tolkien"),
            ("The Book of Lost Tales 1", "J.R.R. Tolkien"),
            # Which part? Not this one's to guess.
            ("The Book of Lost Tales", "J.R.R. Tolkien"),
            ("The Book of Lost Tales, Parts 1-2", "J.R.R. Tolkien"),
            ("The Book of Lost Tales, Part Two (Abridged)", "J.R.R. Tolkien"),
            ("The Book of Lost Tales, Part Two", "Christopher Lee"),
            ("Lord of the Rings (Complete Tolkien works)", "J.R.R Tolkien"),
        ],
    )
    def test_the_other_part_the_bare_title_and_packs_are_rejected(self, release_title, author):
        book = _tolkien(**self.LOST_TALES)

        assert not auto_download.strict_match(
            _abb(release_title, author), book, audiobook_formats=AUDIOBOOK_FORMATS
        )

    @pytest.mark.parametrize(
        ("wanted", "author", "release_title"),
        [
            ("Ready Player One", "Ernest Cline", "Ready Player Two - Ernest Cline [m4b]"),
            ("Ready Player Two", "Ernest Cline", "Ready Player One - Ernest Cline [m4b]"),
            ("Ready Player Two", "Ernest Cline", "Ready Player 1 - Ernest Cline [m4b]"),
            ("Mistborn", "Brandon Sanderson", "Mistborn II - Brandon Sanderson [m4b]"),
            ("Mistborn", "Brandon Sanderson", "Mistborn Two - Brandon Sanderson [m4b]"),
            ("Dune", "Frank Herbert", "Dune Book Two - Frank Herbert [m4b]"),
            # The title's own number does not hide a sequel number after it.
            ("The Two Towers", "J.R.R. Tolkien", "The Two Towers 2 - J.R.R. Tolkien [m4b]"),
        ],
    )
    def test_a_sequel_numbered_in_words_or_roman_numerals_is_rejected(
        self, wanted, author, release_title
    ):
        book = _book(title=wanted, authors=[author])

        assert not _matches(book, release_title)

    @pytest.mark.parametrize(
        ("wanted", "author", "release_title"),
        [
            ("Ready Player One", "Ernest Cline", "Ready Player One - Ernest Cline [m4b]"),
            ("Ready Player One", "Ernest Cline", "Ready Player 1 - Ernest Cline [m4b]"),
            ("Station Eleven", "Emily St. John Mandel", "Station 11 - Emily St. John Mandel"),
            ("Henry V", "William Shakespeare", "Henry V - William Shakespeare [m4b]"),
            ("Dune", "Frank Herbert", "Dune I - Frank Herbert [m4b]"),
            ("The Two Towers", "J.R.R. Tolkien", "The Two Towers - J.R.R. Tolkien [m4b]"),
            # "I" the pronoun reads as a numeral on both sides alike.
            ("What I Loved", "Siri Hustvedt", "What I Loved - Siri Hustvedt [m4b]"),
            ("I, Robot", "Isaac Asimov", "I, Robot - Isaac Asimov [m4b]"),
            (
                "The Seven Habits of Highly Effective People",
                "Stephen R. Covey",
                "The 7 Habits of Highly Effective People - Stephen R. Covey [m4b]",
            ),
        ],
    )
    def test_the_book_itself_matches_in_any_numeral_form(self, wanted, author, release_title):
        book = _book(title=wanted, authors=[author])

        assert _matches(book, release_title)


class TestAuthorLookup:
    """An AudiobookBay listing title without " - Author" names nobody; the detail page
    is asked, for a few releases that fail on the author alone, and the strict match
    then judges what it says."""

    MUMMY = {"title": "The Mummy, or Ramses the Damned", "authors": ["Anne Rice"]}
    TRIAL = {
        "title": "Alex Cross's Trial",
        "authors": ["Richard DiLallo", "James Patterson"],
        "series_name": "Alex Cross",
    }

    @pytest.fixture(autouse=True)
    def _no_inspection(self, monkeypatch):
        monkeypatch.setattr(auto_download, "_inspect_candidate", lambda _data: (False, False))

    @staticmethod
    def _handler(monkeypatch, authors_by_url):
        asked = []

        class _Handler:
            def list_authors(self, release_data):
                asked.append(release_data["download_url"])
                return authors_by_url.get(release_data["download_url"])

        monkeypatch.setattr("shelfmark.release_sources.get_handler", lambda _source: _Handler())
        return asked

    @staticmethod
    def _authorless(title, slug=None):
        slug = slug or title
        return _release(
            source="audiobookbay",
            source_id=slug,
            title=title,
            format="mp3",
            seeders=None,
            indexer="AudiobookBay",
            content_type="audiobook",
            download_url=f"https://audiobookbay.lu/abss/{slug}/",
            extra={"author": None, "title_raw": title},
        )

    def _try(self, user_db, book, releases, monkeypatch):
        admin = user_db.create_user(username="admin", role="admin")
        reader = user_db.create_user(username="reader", role="user")
        row = _pending_request(user_db, reader["id"])
        _stub_provider(monkeypatch, book)
        monkeypatch.setattr(
            auto_download, "search_source_releases", lambda *_a, **_k: (None, releases, None)
        )
        return auto_download.auto_download_request(
            user_db,
            row,
            sources=["audiobookbay"],
            content_type="audiobook",
            min_seeders=1,
            queue_release=lambda *_a, **_k: (True, None),
            admin_user_id=admin["id"],
        )

    @pytest.mark.parametrize(
        ("book_fields", "title", "credited"),
        [
            (MUMMY, "The Mummy or Ramses the Damned", ["Anne Rice"]),
            (TRIAL, "Alex Cross\u2019s TRIAL", ["James Patterson", "Richard DiLallo"]),
        ],
    )
    def test_the_detail_page_author_lets_the_release_match(
        self, user_db, monkeypatch, book_fields, title, credited
    ):
        release = self._authorless(title, "one")
        asked = self._handler(monkeypatch, {release.download_url: credited})

        outcome = self._try(user_db, _book(**book_fields), [release], monkeypatch)

        assert asked == [release.download_url]
        assert isinstance(outcome, auto_download.AutoDownloadOutcome)
        assert outcome.status == "queued"

    @pytest.mark.parametrize("credited", [["Stephen King"], None, []])
    def test_another_author_or_no_credit_still_rejects(self, user_db, monkeypatch, credited):
        release = self._authorless("The Mummy or Ramses the Damned", "one")
        self._handler(monkeypatch, {release.download_url: credited})

        outcome = self._try(user_db, _book(**self.MUMMY), [release], monkeypatch)

        assert outcome.status == "no_match"

    @pytest.mark.parametrize(
        "title",
        [
            "Anne Rice Collection",  # pack
            "The Mummy (Abridged)",  # derivative
            "The Mummy 2",  # sequel number
            "The Mummy Returns",  # another work
            "Interview with the Vampire",  # another title
        ],
    )
    def test_releases_failing_any_other_rule_are_not_looked_up(self, user_db, monkeypatch, title):
        release = self._authorless(title, "one")
        asked = self._handler(monkeypatch, {release.download_url: ["Anne Rice"]})
        book = _book(title="The Mummy", authors=["Anne Rice"])

        outcome = self._try(user_db, book, [release], monkeypatch)

        assert asked == []
        assert outcome.status == "no_match"

    def test_at_most_three_pages_are_read(self, user_db, monkeypatch):
        releases = [self._authorless("The Mummy", f"copy-{n}") for n in range(5)]
        asked = self._handler(monkeypatch, {})

        outcome = self._try(
            user_db, _book(title="The Mummy", authors=["Anne Rice"]), releases, monkeypatch
        )

        assert len(asked) == auto_download.MAX_AUTHOR_LOOKUPS == 3
        assert outcome.status == "no_match"

    def test_no_lookup_when_a_release_already_matches(self, user_db, monkeypatch):
        named = self._authorless("The Mummy", "named")
        named.extra["author"] = "Anne Rice"
        asked = self._handler(monkeypatch, {})

        outcome = self._try(
            user_db,
            _book(title="The Mummy", authors=["Anne Rice"]),
            [self._authorless("The Mummy", "unnamed"), named],
            monkeypatch,
        )

        assert asked == []
        assert outcome.status == "queued"

    def test_a_failing_lookup_leaves_the_release_unmatched(self, user_db, monkeypatch):
        class _Broken:
            def list_authors(self, _data):
                raise OSError("timed out")

        monkeypatch.setattr("shelfmark.release_sources.get_handler", lambda _source: _Broken())

        outcome = self._try(
            user_db,
            _book(**self.MUMMY),
            [self._authorless("The Mummy or Ramses the Damned")],
            monkeypatch,
        )

        assert outcome.status == "no_match"


class TestQueryVariants:
    """Simpler titles searched when the request's own title finds no strict match."""

    @pytest.mark.parametrize(
        ("fields", "expected"),
        [
            (
                {
                    "title": "Find Your Why: A Practical Guide to Discovering Purpose",
                    "subtitle": "A Practical Guide to Discovering Purpose",
                    "authors": ["Simon Sinek"],
                },
                [("without subtitle", "Find Your Why")],
            ),
            (
                {
                    "title": "Children of Dune - Deluxe Edition",
                    "authors": ["Frank Herbert"],
                    "series_name": "Dune Chronicles",
                },
                [("without subtitle", "Children of Dune")],
            ),
            (
                {"title": "The Hobbit, or There and Back Again", "authors": ["J.R.R. Tolkien"]},
                [("without alternate title", "The Hobbit")],
            ),
            (
                {"title": "The Book of Lost Tales, Part II", "authors": ["J.R.R. Tolkien"]},
                [("numerals as digits", "The Book of Lost Tales, Part 2")],
            ),
            (
                {
                    "title": "The Book of Lost Tales, Part Two: Tales of the Elder Days",
                    "authors": ["J.R.R. Tolkien"],
                    "series_name": "The History of Middle-earth",
                },
                [
                    ("without subtitle", "The Book of Lost Tales, Part Two"),
                    (
                        "numerals as digits",
                        "The Book of Lost Tales, Part 2: Tales of the Elder Days",
                    ),
                ],
            ),
            # The prefix is the series or the author: it names no single book.
            (
                {
                    "title": "Star Wars: Thrawn",
                    "authors": ["Timothy Zahn"],
                    "series_name": "Star Wars: Thrawn",
                },
                [],
            ),
            (
                {
                    "title": "Alex Cross: Kiss the Girls",
                    "authors": ["James Patterson"],
                    "series_name": "Alex Cross",
                },
                [],
            ),
            (
                {
                    "title": "Tolkien: A Biography",
                    "subtitle": "A Biography",
                    "authors": ["J.R.R. Tolkien"],
                },
                [],
            ),
            (
                {
                    "title": "Star Wars: Thrawn",
                    "subtitle": "Thrawn",
                    "authors": ["Timothy Zahn"],
                    "series_name": "Thrawn",
                },
                [],
            ),
            # Nothing says the part after the colon is a subtitle rather than the title.
            ({"title": "Star Wars: Thrawn", "authors": ["Timothy Zahn"]}, []),
            (
                {"title": "Star Wars: Thrawn", "subtitle": "A Novel", "authors": ["Timothy Zahn"]},
                [],
            ),
            # Nothing simpler to try.
            ({"title": "Dune", "authors": ["Frank Herbert"]}, []),
            # A hyphen inside a word is not a separator.
            (
                {
                    "title": "Spider-Man - Forever Young",
                    "subtitle": "Forever Young",
                    "authors": ["Stefan Petrucha"],
                },
                [("without subtitle", "Spider-Man")],
            ),
            # The search title is what was searched; it is the starting point.
            (
                {
                    "title": "An Appetite for Wonder: The Making of a Scientist",
                    "search_title": "An Appetite for Wonder",
                    "authors": ["Richard Dawkins"],
                },
                [],
            ),
        ],
    )
    def test_variants(self, fields, expected):
        assert auto_download.query_variants(_book(**fields)) == expected

    def test_at_most_three(self):
        book = _book(
            title="The Mummy, or Ramses the Damned: Part Two of Three",
            series_name="Ramses the Damned",
        )
        assert len(auto_download.query_variants(book)) <= auto_download.MAX_QUERY_VARIANTS == 3


class TestQueryLadder:
    """When the request's own query finds no strict match, simpler queries are tried in
    order, each judged by the same strict match, stopping at the first that matches."""

    FIND_YOUR_WHY = "Find Your Why: A Practical Guide to Discovering Purpose for You and Your Team"

    @pytest.fixture(autouse=True)
    def _library_check_off(self, monkeypatch):
        monkeypatch.setattr(auto_download, "app_config", FakeConfig(LIBRARY_CHECK_ENABLED=False))
        monkeypatch.setattr(
            "shelfmark.core.library_index.is_in_library", lambda *_args, **_kwargs: False
        )
        monkeypatch.setattr(auto_download, "_inspect_candidate", lambda _data: (False, False))

    def _find_your_why(self):
        return _book(
            title=self.FIND_YOUR_WHY,
            subtitle=self.FIND_YOUR_WHY.split(": ", 1)[1],
            authors=["Simon Sinek"],
        )

    def _run(self, user_db, monkeypatch, book, results_by_title, sources=("first",)):
        """Run one request; ``results_by_title`` maps a searched title to release titles."""
        admin = user_db.create_user(username="admin", role="admin")
        reader = user_db.create_user(username="reader", role="user")
        row = _pending_request(user_db, reader["id"])
        _stub_provider(monkeypatch, book)
        searched = []

        def _search(source_name, searched_book, **_kwargs):
            title = searched_book.search_title or searched_book.title
            searched.append((source_name, title))
            releases = [
                _release(source=source_name, source_id=f"{title}|{name}", title=name)
                for name in results_by_title.get(title, [])
            ]
            return None, releases, None

        monkeypatch.setattr(auto_download, "search_source_releases", _search)
        outcome = auto_download.auto_download_request(
            user_db,
            row,
            sources=list(sources),
            content_type="audiobook",
            min_seeders=1,
            queue_release=lambda *_args, **_kwargs: (True, None),
            admin_user_id=admin["id"],
        )
        return outcome, searched

    def test_a_simpler_query_finds_the_book(self, user_db, monkeypatch):
        book = self._find_your_why()

        outcome, searched = self._run(
            user_db,
            monkeypatch,
            book,
            {"Find Your Why": ["Find Your Why - Simon Sinek [m4b]"]},
            sources=("first", "second"),
        )

        assert outcome.status == "queued"
        assert outcome.detail == "Find Your Why - Simon Sinek [m4b]"
        assert searched == [
            ("first", self.FIND_YOUR_WHY),
            ("second", self.FIND_YOUR_WHY),
            ("first", "Find Your Why"),
        ]

    def test_the_own_query_wins_without_trying_variants(self, user_db, monkeypatch):
        book = _book(title="The Book of Lost Tales, Part II", authors=["J.R.R. Tolkien"])

        outcome, searched = self._run(
            user_db,
            monkeypatch,
            book,
            {
                "The Book of Lost Tales, Part II": [
                    "The Book of Lost Tales, Part Two - Tolkien [mp3]"
                ]
            },
        )

        assert outcome.status == "queued"
        assert searched == [("first", "The Book of Lost Tales, Part II")]

    def test_the_ladder_stops_at_the_first_variant_that_matches(self, user_db, monkeypatch):
        book = _book(
            title="The Book of Lost Tales, Part II: Tales of the Elder Days",
            authors=["J.R.R. Tolkien"],
            series_name="The History of Middle-earth",
        )
        found = ["The Book of Lost Tales, Part Two - J.R.R. Tolkien [mp3]"]

        outcome, searched = self._run(
            user_db,
            monkeypatch,
            book,
            {
                "The Book of Lost Tales, Part II": found,
                "The Book of Lost Tales, Part 2: Tales of the Elder Days": found,
            },
        )

        assert outcome.status == "queued"
        assert [title for _source, title in searched] == [
            "The Book of Lost Tales, Part II: Tales of the Elder Days",
            "The Book of Lost Tales, Part II",
        ]

    @pytest.mark.parametrize(
        "release_title",
        [
            "Find Your Why Workbook - Simon Sinek [m4b]",  # derivative
            "Find Your Why (Abridged) - Simon Sinek [m4b]",  # another edition
            "Find Your Why 2 - Simon Sinek [m4b]",  # sequel number
            "Find Your Why Again - Simon Sinek [m4b]",  # another work
            "Simon Sinek Collection: Start With Why, Find Your Why [m4b]",  # pack
            "Find Your Why - Someone Else [m4b]",  # wrong author
        ],
    )
    def test_a_simpler_query_does_not_relax_the_match(self, user_db, monkeypatch, release_title):
        book = self._find_your_why()

        outcome, searched = self._run(
            user_db, monkeypatch, book, {"Find Your Why": [release_title]}
        )

        assert outcome.status == "no_match"
        assert ("first", "Find Your Why") in searched

    def test_a_series_prefix_is_never_searched_on_its_own(self, user_db, monkeypatch):
        book = _book(
            title="Star Wars: Thrawn", authors=["Timothy Zahn"], series_name="Star Wars: Thrawn"
        )

        outcome, searched = self._run(
            user_db, monkeypatch, book, {"Star Wars": ["Star Wars - Timothy Zahn [m4b]"]}
        )

        assert outcome.status == "no_match"
        assert searched == [("first", "Star Wars: Thrawn")]

    def test_at_most_three_extra_walks(self, user_db, monkeypatch):
        book = _book(
            title="The Mummy, or Ramses the Damned: Part Two of Three",
            authors=["Anne Rice"],
            series_name="Ramses the Damned",
        )

        outcome, searched = self._run(user_db, monkeypatch, book, {}, sources=("first", "second"))

        assert outcome.status == "no_match"
        variants = {title for _source, title in searched} - {book.title}
        assert 1 <= len(variants) <= 3
        assert len(searched) == 2 * (1 + len(variants))


class TestRetryAfterCooldown:
    """A synced download that never arrived goes back to pending after a cooldown.

    The latest history row says why it never arrived. "active" (or no row) means the process
    stopped under it, so the same release is worth trying again. "error" means the download
    itself failed, so that release is remembered and not picked again. "cancelled" is left alone.
    """

    NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)

    @classmethod
    def _request(
        cls,
        user_db,
        user_id,
        *,
        provider_id,
        state="error",
        history="active",
        history_message="x",
        days_ago=8.0,
        status="fulfilled",
        provider="hardcover",
        failed_releases=None,
    ):
        row = _pending_request(user_db, user_id, provider=provider, provider_id=provider_id)
        book_data = dict(row["book_data"])
        if failed_releases is not None:
            book_data["failed_releases"] = failed_releases
        stamp = (cls.NOW - timedelta(days=days_ago)).isoformat(timespec="seconds")
        row = user_db.update_request(
            row["id"],
            status=status,
            delivery_state=state,
            delivery_updated_at=stamp,
            book_data=book_data,
            release_data={"source": "audiobookbay", "source_id": f"rel-{provider_id}"},
        )
        if history is not None:
            service = DownloadHistoryService(user_db._db_path)
            task_id = f"task-{provider_id}"
            service.record_download(
                task_id=task_id,
                user_id=user_id,
                username="reader",
                request_id=row["id"],
                source="audiobookbay",
                source_display_name=None,
                title=f"Book {provider_id}",
                author=None,
                file_format=None,
                size=None,
                preview=None,
                content_type="audiobook",
                downloads=None,
                origin="requested",
            )
            if history != "active":
                service.finalize_download(
                    task_id=task_id, final_status=history, status_message=history_message
                )
        return row

    def _reopen(self, user_db, **kwargs):
        kwargs.setdefault("cooldown_days", 7)
        kwargs.setdefault("db_path", user_db._db_path)
        return auto_download.reopen_stale_failures(user_db, now=self.NOW, **kwargs)

    @staticmethod
    def _user(user_db):
        return user_db.create_user(username="reader", role="user")

    def test_an_interrupted_download_is_reopened_and_may_use_the_same_release(self, user_db):
        row = self._request(user_db, self._user(user_db)["id"], provider_id="1", history="active")

        assert self._reopen(user_db) == 1

        stored = user_db.get_request(row["id"])
        assert stored["status"] == "pending"
        assert stored["delivery_state"] == "none"
        assert stored["release_data"] is None
        assert "interrupted" in stored["last_failure_reason"]
        assert "7-day cooldown" in stored["last_failure_reason"]
        # Nothing was wrong with the release, so it is not held against it.
        assert "failed_releases" not in stored["book_data"]

    def test_an_error_the_startup_sweep_labelled_interrupted_is_not_held_against_its_release(
        self, user_db
    ):
        row = self._request(
            user_db,
            self._user(user_db)["id"],
            provider_id="1",
            history="error",
            history_message="Interrupted",
        )

        assert self._reopen(user_db) == 1

        assert "failed_releases" not in user_db.get_request(row["id"])["book_data"]

    def test_a_request_stuck_queued_with_no_history_counts_as_interrupted(self, user_db):
        row = self._request(
            user_db, self._user(user_db)["id"], provider_id="1", state="queued", history=None
        )

        assert self._reopen(user_db) == 1
        assert user_db.get_request(row["id"])["status"] == "pending"

    def test_a_genuine_failure_is_reopened_and_its_release_is_remembered(self, user_db):
        row = self._request(user_db, self._user(user_db)["id"], provider_id="1", history="error")

        assert self._reopen(user_db) == 1

        stored = user_db.get_request(row["id"])
        assert stored["status"] == "pending"
        assert "failed" in stored["last_failure_reason"]
        assert stored["book_data"]["failed_releases"] == [
            {"source": "audiobookbay", "source_id": "rel-1"}
        ]

    def test_a_release_is_only_remembered_once(self, user_db):
        seen = [{"source": "audiobookbay", "source_id": "rel-1"}]
        row = self._request(
            user_db,
            self._user(user_db)["id"],
            provider_id="1",
            history="error",
            failed_releases=seen,
        )

        assert self._reopen(user_db) == 1
        assert user_db.get_request(row["id"])["book_data"]["failed_releases"] == seen

    @pytest.mark.parametrize(
        "message",
        [
            "Failed to add to qbittorrent: Failed to connect to qBittorrent. This i",
            "Cannot connect to qBittorrent at http://x",
            "Connection refused",
        ],
    )
    def test_a_download_whose_client_was_down_is_not_held_against_its_release(
        self, user_db, message
    ):
        row = self._request(
            user_db,
            self._user(user_db)["id"],
            provider_id="1",
            history="error",
            history_message=message,
        )

        assert self._reopen(user_db) == 1

        assert "failed_releases" not in user_db.get_request(row["id"])["book_data"]

    def test_it_gives_up_after_three_retries(self, user_db):
        user_id = self._user(user_db)["id"]
        three = [{"source": "s", "source_id": f"r{n}"} for n in range(3)]
        two = three[:2]
        spent = self._request(
            user_db, user_id, provider_id="1", history="error", failed_releases=three
        )
        left = self._request(
            user_db, user_id, provider_id="2", history="error", failed_releases=two
        )

        assert self._reopen(user_db) == 1

        assert user_db.get_request(spent["id"])["status"] == "fulfilled"
        assert user_db.get_request(left["id"])["status"] == "pending"
        assert len(user_db.get_request(left["id"])["book_data"]["failed_releases"]) == 3

    def test_a_cancelled_download_is_never_retried(self, user_db):
        user_id = self._user(user_db)["id"]
        with_history = self._request(
            user_db, user_id, provider_id="1", state="cancelled", history="cancelled", days_ago=400
        )
        without = self._request(
            user_db, user_id, provider_id="2", state="cancelled", history=None, days_ago=400
        )

        assert self._reopen(user_db) == 0
        assert user_db.get_request(with_history["id"])["status"] == "fulfilled"
        assert user_db.get_request(without["id"])["status"] == "fulfilled"

    STALL_MESSAGE = "Download stalled (no activity for 300s)"

    def test_a_download_the_stall_timer_cancelled_is_retried_with_its_release_remembered(
        self, user_db
    ):
        row = self._request(
            user_db,
            self._user(user_db)["id"],
            provider_id="1",
            state="cancelled",
            history="cancelled",
            history_message=self.STALL_MESSAGE,
        )

        assert self._reopen(user_db) == 1

        stored = user_db.get_request(row["id"])
        assert stored["status"] == "pending"
        assert "stalled" in stored["last_failure_reason"]
        assert stored["book_data"]["failed_releases"] == [
            {"source": "audiobookbay", "source_id": "rel-1"}
        ]

    @pytest.mark.parametrize("message", ["Cancelled", "Queued", "Fetching metadata", ""])
    def test_any_other_cancel_stays_a_decision(self, user_db, message):
        row = self._request(
            user_db,
            self._user(user_db)["id"],
            provider_id="1",
            state="cancelled",
            history="cancelled",
            history_message=message,
        )

        assert self._reopen(user_db) == 0
        assert user_db.get_request(row["id"])["status"] == "fulfilled"

    def test_a_failed_download_can_be_retried_after_minutes_not_days(self, user_db):
        user_id = self._user(user_db)["id"]
        failed = self._request(
            user_db, user_id, provider_id="1", history="error", days_ago=30 / 1440
        )

        ids: list[int] = []
        count = self._reopen(
            user_db,
            cooldown_days=0,
            failure_cooldown=timedelta(minutes=20),
            only_kinds=frozenset({"failed", "stalled"}),
            reopened_ids=ids,
        )

        assert count == 1
        assert ids == [failed["id"]]
        stored = user_db.get_request(failed["id"])
        assert stored["status"] == "pending"
        assert "20-minute cooldown" in stored["last_failure_reason"]
        assert stored["book_data"]["failed_releases"] == [
            {"source": "audiobookbay", "source_id": "rel-1"}
        ]

    def test_a_failure_newer_than_the_short_wait_is_left_alone(self, user_db):
        row = self._request(
            user_db, self._user(user_db)["id"], provider_id="1", history="error", days_ago=5 / 1440
        )

        assert self._reopen(user_db, cooldown_days=0, failure_cooldown=timedelta(minutes=20)) == 0
        assert user_db.get_request(row["id"])["status"] == "fulfilled"

    def test_a_stalled_download_also_gets_the_short_wait(self, user_db):
        row = self._request(
            user_db,
            self._user(user_db)["id"],
            provider_id="1",
            state="cancelled",
            history="cancelled",
            history_message=self.STALL_MESSAGE,
            days_ago=30 / 1440,
        )

        assert self._reopen(user_db, cooldown_days=0, failure_cooldown=timedelta(minutes=20)) == 1
        assert user_db.get_request(row["id"])["status"] == "pending"

    def test_an_interrupted_download_keeps_the_long_wait(self, user_db):
        row = self._request(
            user_db, self._user(user_db)["id"], provider_id="1", history="active", days_ago=1.0
        )

        assert self._reopen(user_db, cooldown_days=7, failure_cooldown=timedelta(minutes=20)) == 0
        assert user_db.get_request(row["id"])["status"] == "fulfilled"

    def test_the_short_retry_can_be_limited_to_failures(self, user_db):
        row = self._request(
            user_db, self._user(user_db)["id"], provider_id="1", history="active", days_ago=30
        )

        count = self._reopen(
            user_db,
            cooldown_days=7,
            failure_cooldown=timedelta(minutes=20),
            only_kinds=frozenset({"failed", "stalled"}),
        )

        assert count == 0
        assert user_db.get_request(row["id"])["status"] == "fulfilled"

    def _quick_retry(self, user_db, monkeypatch, **config):
        monkeypatch.setattr(
            auto_download,
            "app_config",
            FakeConfig(AUTO_DOWNLOAD_ENABLED=True, **config),
        )
        passes: list[frozenset[int]] = []

        def fake_pending(_user_db, **kwargs):
            passes.append(kwargs["only_request_ids"])
            return {"queued": 1, "no_match": 0, "in_library": 0, "skipped": 0, "error": 0}

        monkeypatch.setattr(auto_download, "auto_download_pending", fake_pending)
        result = auto_download.retry_failed_downloads(
            user_db,
            queue_release=lambda *_a, **_k: (True, None),
            db_path=user_db._db_path,
        )
        return result, passes

    def test_the_quick_retry_is_off_unless_set(self, user_db, monkeypatch):
        self._request(user_db, self._user(user_db)["id"], provider_id="1", history="error")

        result, passes = self._quick_retry(user_db, monkeypatch)

        assert result is None
        assert passes == []

    def test_the_quick_retry_searches_only_the_requests_it_reopened(self, user_db, monkeypatch):
        user_id = self._user(user_db)["id"]
        failed = self._request(user_db, user_id, provider_id="1", history="error")
        self._request(user_db, user_id, provider_id="2", history="active")  # interrupted

        result, passes = self._quick_retry(
            user_db, monkeypatch, AUTO_DOWNLOAD_FAILURE_RETRY_MINUTES=20
        )

        assert result is not None
        assert passes == [frozenset({failed["id"]})]
        assert user_db.get_request(failed["id"])["book_data"]["failed_releases"]

    def test_the_quick_retry_does_nothing_when_nothing_failed(self, user_db, monkeypatch):
        self._request(user_db, self._user(user_db)["id"], provider_id="1", history="active")

        result, passes = self._quick_retry(
            user_db, monkeypatch, AUTO_DOWNLOAD_FAILURE_RETRY_MINUTES=20
        )

        assert result is None
        assert passes == []

    def test_a_request_the_status_poll_already_reopened_is_searched_too(self, user_db, monkeypatch):
        user_id = self._user(user_db)["id"]
        row = _pending_request(user_db, user_id, provider_id="9")
        book_data = {**row["book_data"], "failed_releases": [{"source": "s", "source_id": "r"}]}
        user_db.update_request(row["id"], book_data=book_data, last_failure_reason="No seeders")

        result, passes = self._quick_retry(
            user_db, monkeypatch, AUTO_DOWNLOAD_FAILURE_RETRY_MINUTES=20
        )

        assert result is not None
        assert passes == [frozenset({row["id"]})]

    def test_the_pass_can_be_limited_to_some_requests(self, user_db, monkeypatch):
        monkeypatch.setattr(auto_download, "app_config", FakeConfig(AUTO_DOWNLOAD_ENABLED=True))
        user_id = self._user(user_db)["id"]
        user_db.create_user(username="admin", role="admin")
        a = _pending_request(user_db, user_id, provider_id="1")
        b = _pending_request(user_db, user_id, provider_id="2")
        handled: list[int] = []

        def fake_request(_db, row, **_kwargs):
            handled.append(row["id"])
            return auto_download.AutoDownloadOutcome(row["id"], "no_match", "x", source=None)

        monkeypatch.setattr(auto_download, "auto_download_request", fake_request)
        monkeypatch.setattr(auto_download, "_configured_source_priority", lambda _c: ["first"])

        auto_download.auto_download_pending(
            user_db,
            queue_release=lambda *_a, **_k: (True, None),
            db_path=user_db._db_path,
            only_request_ids=frozenset({b["id"]}),
        )

        assert handled == [b["id"]]
        assert a["id"] not in handled

    def test_a_stall_cancelled_request_is_given_up_on_after_three_tries(self, user_db):
        tried = [{"source": "audiobookbay", "source_id": f"old-{n}"} for n in range(3)]
        row = self._request(
            user_db,
            self._user(user_db)["id"],
            provider_id="1",
            state="cancelled",
            history="cancelled",
            history_message=self.STALL_MESSAGE,
            failed_releases=tried,
        )

        assert self._reopen(user_db) == 0
        assert user_db.get_request(row["id"])["status"] == "fulfilled"

    def test_a_stall_cancel_obeys_the_cooldown(self, user_db):
        self._request(
            user_db,
            self._user(user_db)["id"],
            provider_id="1",
            state="cancelled",
            history="cancelled",
            history_message=self.STALL_MESSAGE,
            days_ago=6.99,
        )

        assert self._reopen(user_db) == 0

    @pytest.mark.parametrize(("days_ago", "reopened"), [(6.99, 0), (7.01, 1)])
    def test_the_cooldown_is_seven_days(self, user_db, days_ago, reopened):
        self._request(user_db, self._user(user_db)["id"], provider_id="1", days_ago=days_ago)

        assert self._reopen(user_db) == reopened

    @pytest.mark.parametrize(
        ("state", "status", "history"),
        [
            ("complete", "fulfilled", "complete"),
            ("none", "fulfilled", None),
            ("none", "rejected", None),  # a rejection is a decision, never retried
            ("error", "rejected", "error"),
            ("error", "fulfilled", "complete"),  # the history disagrees, so do not guess
        ],
    )
    def test_everything_else_is_left_alone(self, user_db, state, status, history):
        row = self._request(
            user_db,
            self._user(user_db)["id"],
            provider_id="1",
            state=state,
            status=status,
            history=history,
        )

        assert self._reopen(user_db) == 0
        assert user_db.get_request(row["id"])["status"] == status

    def test_requests_from_other_providers_are_left_alone(self, user_db):
        row = self._request(
            user_db, self._user(user_db)["id"], provider_id="1", provider="openlibrary"
        )

        assert self._reopen(user_db) == 0
        assert user_db.get_request(row["id"])["status"] == "fulfilled"

    def test_a_backlog_is_spread_over_passes_oldest_first(self, user_db):
        user_id = self._user(user_db)["id"]
        ages = [30, 12, 9, 20, 8, 15, 25]
        rows = {
            days: self._request(user_db, user_id, provider_id=str(days), days_ago=days)
            for days in ages
        }

        assert self._reopen(user_db, max_reopen=5) == 5

        reopened = {
            days
            for days, row in rows.items()
            if user_db.get_request(row["id"])["status"] == "pending"
        }
        assert reopened == {30, 25, 20, 15, 12}  # the five oldest; 9 and 8 wait for the next pass
        assert self._reopen(user_db, max_reopen=5) == 2

    @pytest.mark.parametrize("days", [0, -3])
    def test_a_zero_or_negative_cooldown_turns_retries_off(self, user_db, days):
        row = self._request(user_db, self._user(user_db)["id"], provider_id="1", days_ago=400)

        assert self._reopen(user_db, cooldown_days=days) == 0
        assert user_db.get_request(row["id"])["status"] == "fulfilled"

    def test_without_readable_history_nothing_is_guessed(self, user_db):
        row = self._request(user_db, self._user(user_db)["id"], provider_id="1", days_ago=400)

        assert self._reopen(user_db, db_path=None) == 0
        assert user_db.get_request(row["id"])["status"] == "fulfilled"

    def test_a_history_read_that_fails_retries_nothing(self, user_db, tmp_path):
        row = self._request(user_db, self._user(user_db)["id"], provider_id="1", days_ago=400)
        no_history_table = tmp_path / "empty.db"
        sqlite3.connect(no_history_table).close()

        assert self._reopen(user_db, db_path=str(no_history_table)) == 0
        assert user_db.get_request(row["id"])["status"] == "fulfilled"

    def test_an_unstamped_failure_ages_from_when_it_was_created(self, user_db):
        user_id = self._user(user_db)["id"]
        row = _pending_request(user_db, user_id, provider="hardcover", provider_id="1")
        user_db.update_request(row["id"], status="fulfilled", delivery_state="error")
        created = datetime.now(UTC)

        early = auto_download.reopen_stale_failures(
            user_db, cooldown_days=7, db_path=user_db._db_path, now=created + timedelta(days=6)
        )
        late = auto_download.reopen_stale_failures(
            user_db, cooldown_days=7, db_path=user_db._db_path, now=created + timedelta(days=8)
        )

        assert (early, late) == (0, 1)

    def test_a_reopened_request_is_picked_up_in_the_same_pass(
        self, user_db, fake_app_config, monkeypatch
    ):
        fake_app_config.values.update(AUTO_DOWNLOAD_ENABLED=True)
        admin = user_db.create_user(username="admin", role="admin")
        row = self._request(user_db, admin["id"], provider_id="1")
        monkeypatch.setattr(auto_download, "_configured_source_priority", lambda _t: ["src"])
        seen: list[int] = []

        def _fake_request(_user_db, request_row, **_kwargs):
            seen.append(int(request_row["id"]))
            return auto_download.AutoDownloadOutcome(int(request_row["id"]), "no_match")

        monkeypatch.setattr(auto_download, "auto_download_request", _fake_request)
        monkeypatch.setattr(
            auto_download, "reopen_stale_failures", self._with_fixed_clock(auto_download)
        )

        auto_download.auto_download_pending(
            user_db,
            queue_release=lambda *_args, **_kwargs: (True, None),
            db_path=user_db._db_path,
        )

        assert seen == [row["id"]]

    def test_the_setting_can_turn_retries_off(self, user_db, fake_app_config, monkeypatch):
        fake_app_config.values.update(AUTO_DOWNLOAD_ENABLED=True, AUTO_DOWNLOAD_RETRY_DAYS=0)
        admin = user_db.create_user(username="admin", role="admin")
        self._request(user_db, admin["id"], provider_id="1", days_ago=400)
        monkeypatch.setattr(auto_download, "_configured_source_priority", lambda _t: ["src"])
        monkeypatch.setattr(
            auto_download,
            "auto_download_request",
            lambda *_a, **_k: pytest.fail("a retry must not run with the setting at 0"),
        )
        monkeypatch.setattr(
            auto_download, "reopen_stale_failures", self._with_fixed_clock(auto_download)
        )

        auto_download.auto_download_pending(
            user_db,
            queue_release=lambda *_args, **_kwargs: (True, None),
            db_path=user_db._db_path,
        )

    def test_a_release_that_failed_before_is_not_picked_again(self, user_db, monkeypatch):
        admin = user_db.create_user(username="admin", role="admin")
        reader = user_db.create_user(username="reader", role="user")
        row = _pending_request(user_db, reader["id"])
        row = user_db.update_request(
            row["id"],
            book_data={
                **row["book_data"],
                "failed_releases": [{"source": "prowlarr", "source_id": "bad"}],
            },
        )
        _stub_provider(monkeypatch, _book())
        bad = _release(source="prowlarr", source_id="bad", seeders=99)
        good = _release(source="prowlarr", source_id="good", seeders=1)
        monkeypatch.setattr(
            auto_download,
            "search_source_releases",
            lambda *_a, **_k: (None, [bad, good], None),
        )
        queued: list[str] = []

        def _queue(release_data, _priority, *, user_id, username):
            queued.append(release_data["source_id"])
            return True, None

        outcome = auto_download.auto_download_request(
            user_db,
            user_db.get_request(row["id"]),
            sources=["prowlarr"],
            content_type="audiobook",
            min_seeders=1,
            queue_release=_queue,
            admin_user_id=admin["id"],
        )

        assert outcome.status == "queued"
        assert queued == ["good"]  # the better-seeded release is the one that failed

    def _with_fixed_clock(self, module):
        real = module.reopen_stale_failures
        return lambda *args, **kwargs: real(*args, now=self.NOW, **kwargs)


def test_one_request_that_blows_up_does_not_abort_the_pass(user_db, monkeypatch):
    user_db.create_user(username="admin", role="admin")
    reader = user_db.create_user(username="reader", role="user")
    first = _pending_request(user_db, reader["id"], provider_id="1")
    second = _pending_request(user_db, reader["id"], provider_id="2")
    monkeypatch.setattr(auto_download, "app_config", _Config(AUTO_DOWNLOAD_ENABLED=True))
    monkeypatch.setattr(auto_download, "_configured_source_priority", lambda _t: ["src"])
    seen = []

    def _fake_request(_user_db, row, **_kwargs):
        seen.append(int(row["id"]))
        if len(seen) == 1:  # whichever request is walked first
            raise RuntimeError("provider exploded")
        return auto_download.AutoDownloadOutcome(int(row["id"]), "queued")

    monkeypatch.setattr(auto_download, "auto_download_request", _fake_request)

    summary = auto_download.auto_download_pending(
        user_db, queue_release=lambda *_args, **_kwargs: (True, None)
    )

    assert sorted(seen) == sorted([int(first["id"]), int(second["id"])])
    assert summary["error"] == 1
    assert summary["queued"] == 1


class TestDeadSources:
    """A peer or post known to be dead is tried after the others, never excluded."""

    NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)

    def _history(self, user_db, *rows):
        service = DownloadHistoryService(user_db._db_path)
        for n, (peer, status, message) in enumerate(rows):
            task_id = f"slskd:{n}"
            service.record_download(
                task_id=task_id,
                user_id=None,
                username=None,
                request_id=None,
                source="slskd",
                source_display_name=None,
                title=f"File {n}",
                author=None,
                file_format=None,
                size=None,
                preview=None,
                content_type="audiobook",
                downloads=None,
                origin="requested",
                retry_payload={"retry_source_context": {"username": peer}},
            )
            service.finalize_download(task_id=task_id, final_status=status, status_message=message)

    def _peers(self, user_db):
        return auto_download.recent_failed_peers(user_db._db_path, now=datetime.now(UTC))

    def test_a_peer_that_only_failed_is_dead(self, user_db):
        self._history(user_db, ("flaky", "error", "Peer transfer failed: TimedOut"))

        assert self._peers(user_db) == {"flaky"}

    def test_a_peer_that_also_delivered_is_not(self, user_db):
        self._history(
            user_db,
            ("mixed", "error", "Peer transfer failed: Errored"),
            ("mixed", "complete", "Complete"),
        )

        assert self._peers(user_db) == frozenset()

    def test_a_peer_that_never_answered_the_queue_request_is_dead(self, user_db):
        self._history(
            user_db, ("squickle", "error", "Failed to add to slskd: 500 Server Error (timed out)")
        )

        assert self._peers(user_db) == {"squickle"}

    def test_a_failure_that_was_not_the_peers_does_not_count(self, user_db):
        self._history(
            user_db,
            ("fine", "error", "slskd refused 1 file(s): already queued"),
            ("other", "error", "Download failed: TypeError"),
        )

        assert self._peers(user_db) == frozenset()

    def test_old_failures_are_forgotten(self, user_db):
        self._history(user_db, ("flaky", "error", "Peer transfer failed: TimedOut"))

        later = datetime.now(UTC) + timedelta(days=auto_download.DEAD_PEER_DAYS + 1)
        assert auto_download.recent_failed_peers(user_db._db_path, now=later) == frozenset()

    def test_no_history_database_means_no_dead_peers(self):
        assert auto_download.recent_failed_peers(None) == frozenset()

    def _soulseek(self, peer, source_id):
        return _release(source="slskd", source_id=source_id, extra={"username": peer}, seeders=None)

    def test_releases_from_a_dead_peer_move_behind_the_rest(self):
        ranked = [
            self._soulseek("flaky", "a"),
            self._soulseek("good", "b"),
            self._soulseek("ok", "c"),
        ]

        result = auto_download._demote_dead_sources(ranked, frozenset({"flaky"}))

        assert [r.source_id for r in result] == ["b", "c", "a"]

    def test_the_only_release_is_still_offered_when_its_peer_is_dead(self):
        ranked = [self._soulseek("flaky", "a")]

        assert auto_download._demote_dead_sources(ranked, frozenset({"flaky"})) == ranked

    def _abb(self, source_id):
        return _release(
            source="audiobookbay",
            source_id=source_id,
            download_url=f"https://audiobookbay.lu/abss/{source_id}/",
            seeders=None,
        )

    def _complaints(self, monkeypatch, counts):
        class _Handler:
            def seed_complaints(self, release_data):
                return counts[release_data["download_url"].rstrip("/").rsplit("/", 1)[-1]]

        monkeypatch.setattr("shelfmark.release_sources.get_handler", lambda _name: _Handler())

    def test_a_post_whose_comments_say_it_has_no_seeders_moves_behind_the_rest(self, monkeypatch):
        self._complaints(monkeypatch, {"a": 3, "b": 0})

        result = auto_download._demote_dead_sources([self._abb("a"), self._abb("b")], frozenset())

        assert [r.source_id for r in result] == ["b", "a"]

    def test_a_page_that_cannot_be_read_is_not_judged(self, monkeypatch):
        self._complaints(monkeypatch, {"a": None, "b": 0})

        result = auto_download._demote_dead_sources([self._abb("a"), self._abb("b")], frozenset())

        assert [r.source_id for r in result] == ["a", "b"]

    def test_only_the_best_few_posts_have_their_comments_read(self, monkeypatch):
        looked: list[str] = []

        class _Handler:
            def seed_complaints(self, release_data):
                looked.append(release_data["download_url"])
                return 0

        monkeypatch.setattr("shelfmark.release_sources.get_handler", lambda _name: _Handler())
        ranked = [self._abb(str(n)) for n in range(auto_download.MAX_COMPLAINT_CHECKS + 3)]

        auto_download._demote_dead_sources(ranked, frozenset())

        assert len(looked) == auto_download.MAX_COMPLAINT_CHECKS

    def test_other_sources_are_never_asked_for_comments(self, monkeypatch):
        def explode(_name):
            raise AssertionError("no handler lookup expected")

        monkeypatch.setattr("shelfmark.release_sources.get_handler", explode)

        ranked = [_release(source="prowlarr", source_id="x")]

        assert auto_download._demote_dead_sources(ranked, frozenset()) == ranked


class TestSharedDeadReleases:
    """A torrent that stalled for one request is not offered again to another."""

    def _history(self, user_db, task_id, status, message, *, source="audiobookbay"):
        service = DownloadHistoryService(user_db._db_path)
        service.record_download(
            task_id=task_id,
            user_id=None,
            username=None,
            request_id=None,
            source=source,
            source_display_name=None,
            title="A release",
            author=None,
            file_format=None,
            size=None,
            preview=None,
            content_type="audiobook",
            downloads=None,
            origin="requested",
            retry_payload=None,
        )
        service.finalize_download(task_id=task_id, final_status=status, status_message=message)

    def _dead(self, user_db):
        return auto_download.recent_dead_releases(user_db._db_path)

    def test_a_stalled_release_is_dead(self, user_db):
        self._history(user_db, "abc", "cancelled", "Download stalled (no activity for 300s)")

        assert self._dead(user_db) == {("audiobookbay", "abc")}

    def test_a_release_with_no_seeders_is_dead(self, user_db):
        self._history(user_db, "abc", "error", "No seeders: 0 found, 1 needed, for 5 min (0%)")

        assert self._dead(user_db) == {("audiobookbay", "abc")}

    def test_a_release_that_also_completed_is_not(self, user_db):
        self._history(user_db, "abc", "cancelled", "Download stalled (no activity for 300s)")
        self._history(user_db, "abc", "complete", "Complete")

        assert self._dead(user_db) == frozenset()

    def test_a_person_cancelling_does_not_count(self, user_db):
        self._history(user_db, "abc", "cancelled", "Downloading")

        assert self._dead(user_db) == frozenset()

    def test_soulseek_is_left_to_the_dead_peer_rule(self, user_db):
        self._history(
            user_db, "slskd:1", "error", "Download stalled (no activity for 300s)", source="slskd"
        )

        assert self._dead(user_db) == frozenset()

    def test_old_failures_are_forgotten(self, user_db):
        self._history(user_db, "abc", "cancelled", "Download stalled (no activity for 300s)")

        later = datetime.now(UTC) + timedelta(days=auto_download.failed_release_retry_days() + 1)
        assert auto_download.recent_dead_releases(user_db._db_path, now=later) == frozenset()

    def test_no_history_database_means_none_dead(self):
        assert auto_download.recent_dead_releases(None) == frozenset()

    def test_another_requests_dead_release_is_not_picked(self, user_db, monkeypatch):
        admin = user_db.create_user(username="admin", role="admin")
        row = _pending_request(user_db, admin["id"])
        _stub_provider(monkeypatch, _book())
        dead = _release(source="prowlarr", source_id="dead", seeders=99)
        good = _release(source="prowlarr", source_id="good", seeders=1)
        monkeypatch.setattr(
            auto_download, "search_source_releases", lambda *_a, **_k: (None, [dead, good], None)
        )
        queued: list[str] = []

        def _queue(release_data, _priority, *, user_id, username):
            queued.append(release_data["source_id"])
            return True, None

        outcome = auto_download.auto_download_request(
            user_db,
            user_db.get_request(row["id"]),
            sources=["prowlarr"],
            content_type="audiobook",
            min_seeders=1,
            queue_release=_queue,
            admin_user_id=admin["id"],
            dead_releases=frozenset({("prowlarr", "dead")}),
        )

        assert outcome.status == "queued"
        assert queued == ["good"]


class TestFailedReleaseIsTriedAgain:
    """A failed release is kept out for a number of days, then given another chance."""

    def _history(self, user_db, task_id, status, message, *, source="audiobookbay"):
        service = DownloadHistoryService(user_db._db_path)
        service.record_download(
            task_id=task_id,
            user_id=None,
            username=None,
            request_id=None,
            source=source,
            source_display_name=None,
            title="A release",
            author=None,
            file_format=None,
            size=None,
            preview=None,
            content_type="audiobook",
            downloads=None,
            origin="requested",
            retry_payload=None,
        )
        service.finalize_download(task_id=task_id, final_status=status, status_message=message)

    def _recent(self, user_db, **kwargs):
        return auto_download.recent_failed_releases(user_db._db_path, **kwargs)

    def test_a_recent_failure_counts(self, user_db):
        self._history(user_db, "abc", "error", "Peer transfer failed: Rejected", source="slskd")
        self._history(user_db, "def", "cancelled", "Download stalled (no activity for 300s)")

        assert self._recent(user_db) == {("slskd", "abc"), ("audiobookbay", "def")}

    def test_an_old_failure_no_longer_counts(self, user_db):
        self._history(user_db, "abc", "cancelled", "Download stalled (no activity for 300s)")

        later = datetime.now(UTC) + timedelta(days=auto_download.failed_release_retry_days() + 1)
        assert self._recent(user_db, now=later) == frozenset()

    def test_a_release_that_later_completed_is_not_failed(self, user_db):
        self._history(user_db, "abc", "cancelled", "Download stalled (no activity for 300s)")
        self._history(user_db, "abc", "complete", "Complete")

        assert self._recent(user_db) == frozenset()

    @pytest.mark.parametrize("message", ["Interrupted", "Superseded by a completed download"])
    def test_a_row_that_is_not_the_releases_fault_does_not_count(self, user_db, message):
        self._history(user_db, "abc", "error", message)

        assert self._recent(user_db) == frozenset()

    def test_the_window_comes_from_the_setting(self, monkeypatch):
        monkeypatch.setattr(auto_download.app_config, "get", lambda key, default=None: 30)
        assert auto_download.failed_release_retry_days() == 30

    @pytest.mark.parametrize("value", [None, "soon", 0, -3])
    def test_a_bad_setting_falls_back_or_is_at_least_a_day(self, monkeypatch, value):
        monkeypatch.setattr(auto_download.app_config, "get", lambda key, default=None: value)
        assert auto_download.failed_release_retry_days() >= 1

    def _queue(self, user_db, monkeypatch, remembered, recent_failures):
        admin = user_db.create_user(username="admin", role="admin")
        row = _pending_request(user_db, admin["id"])
        user_db.update_request(
            row["id"],
            book_data={
                **row["book_data"],
                "failed_releases": [{"source": "prowlarr", "source_id": s} for s in remembered],
            },
        )
        _stub_provider(monkeypatch, _book())
        failed = _release(source="prowlarr", source_id="failed", seeders=99)
        other = _release(source="prowlarr", source_id="other", seeders=1)
        monkeypatch.setattr(
            auto_download, "search_source_releases", lambda *_a, **_k: (None, [failed, other], None)
        )
        queued: list[str] = []

        def _queue_release(release_data, _priority, *, user_id, username):
            queued.append(release_data["source_id"])
            return True, None

        auto_download.auto_download_request(
            user_db,
            user_db.get_request(row["id"]),
            sources=["prowlarr"],
            content_type="audiobook",
            min_seeders=1,
            queue_release=_queue_release,
            admin_user_id=admin["id"],
            recent_failures=recent_failures,
        )
        return queued

    def test_a_recently_failed_release_is_skipped(self, user_db, monkeypatch):
        queued = self._queue(user_db, monkeypatch, ["failed"], frozenset({("prowlarr", "failed")}))

        assert queued == ["other"]

    def test_a_release_whose_failure_has_aged_out_is_picked_again(self, user_db, monkeypatch):
        queued = self._queue(user_db, monkeypatch, ["failed"], frozenset())

        assert queued == ["failed"]  # the better-seeded release gets its second chance

    def test_without_a_history_every_remembered_failure_still_counts(self, user_db, monkeypatch):
        queued = self._queue(user_db, monkeypatch, ["failed"], None)

        assert queued == ["other"]


class TestSettleDeliveryStatesFromHistory:
    """A request still saying downloading is corrected from history once the download ended."""

    def _fulfilled(self, user_db, *, state, delivery_updated_at):
        admin = user_db.create_user(username="admin", role="admin")
        row = _pending_request(user_db, admin["id"])
        user_db.update_request(
            row["id"],
            status="fulfilled",
            delivery_state=state,
            delivery_updated_at=delivery_updated_at,
            release_data={"source": "audiobookbay", "source_id": "rel1"},
        )
        return row["id"]

    def _record(self, user_db, request_id, status, message, *, ended):
        service = DownloadHistoryService(user_db._db_path)
        service.record_download(
            task_id=f"t{request_id}{status}",
            user_id=None,
            username=None,
            request_id=request_id,
            source="audiobookbay",
            source_display_name=None,
            title="A book",
            author=None,
            file_format=None,
            size=None,
            preview=None,
            content_type="audiobook",
            downloads=None,
            origin="requested",
            retry_payload=None,
        )
        service.finalize_download(
            task_id=f"t{request_id}{status}", final_status=status, status_message=message
        )
        with sqlite3.connect(user_db._db_path) as conn:
            conn.execute(
                "UPDATE download_history SET terminal_at = ? WHERE task_id = ?",
                (ended, f"t{request_id}{status}"),
            )

    def _state(self, user_db, request_id):
        return user_db.get_request(request_id)["delivery_state"]

    def test_a_cancelled_task_moves_the_request_off_downloading(self, user_db):
        rid = self._fulfilled(
            user_db, state="downloading", delivery_updated_at="2026-10-16T20:56:38+00:00"
        )
        self._record(
            user_db,
            rid,
            "cancelled",
            "Download stalled (no activity for 300s)",
            ended="2026-10-18T19:49:23+00:00",
        )

        assert (
            auto_download.settle_delivery_states_from_history(user_db, db_path=user_db._db_path)
            == 1
        )
        assert self._state(user_db, rid) == "cancelled"

    def test_a_row_that_ended_before_the_request_last_changed_is_left_alone(self, user_db):
        rid = self._fulfilled(
            user_db, state="queued", delivery_updated_at="2026-10-16T20:56:38+00:00"
        )
        self._record(
            user_db, rid, "error", "Peer transfer failed", ended="2026-10-10T00:00:00+00:00"
        )

        assert (
            auto_download.settle_delivery_states_from_history(user_db, db_path=user_db._db_path)
            == 0
        )
        assert self._state(user_db, rid) == "queued"

    def test_a_row_still_active_is_left_alone(self, user_db):
        rid = self._fulfilled(
            user_db, state="downloading", delivery_updated_at="2026-10-16T20:56:38+00:00"
        )
        DownloadHistoryService(user_db._db_path).record_download(
            task_id="live",
            user_id=None,
            username=None,
            request_id=rid,
            source="audiobookbay",
            source_display_name=None,
            title="A book",
            author=None,
            file_format=None,
            size=None,
            preview=None,
            content_type="audiobook",
            downloads=None,
            origin="requested",
            retry_payload=None,
        )

        assert (
            auto_download.settle_delivery_states_from_history(user_db, db_path=user_db._db_path)
            == 0
        )
        assert self._state(user_db, rid) == "downloading"

    def test_a_request_without_history_is_left_alone(self, user_db):
        rid = self._fulfilled(
            user_db, state="queued", delivery_updated_at="2026-10-16T20:56:38+00:00"
        )

        assert (
            auto_download.settle_delivery_states_from_history(user_db, db_path=user_db._db_path)
            == 0
        )
        assert self._state(user_db, rid) == "queued"

    def test_the_stale_sweep_then_reopens_a_stalled_download(self, user_db):
        rid = self._fulfilled(
            user_db, state="downloading", delivery_updated_at="2026-10-16T20:56:38+00:00"
        )
        self._record(
            user_db,
            rid,
            "cancelled",
            "Download stalled (no activity for 300s)",
            ended="2026-10-18T19:49:23+00:00",
        )

        reopened = auto_download.reopen_stale_failures(
            user_db,
            cooldown_days=0,
            db_path=user_db._db_path,
            provider_filter=None,
            failure_cooldown=timedelta(minutes=20),
        )

        assert reopened == 1
        assert user_db.get_request(rid)["status"] == "pending"


class TestHistoryOrder:
    """A reused release keeps its old row id, so the newest attempt is not the highest id."""

    def test_the_settle_follows_queue_time_not_row_id(self, user_db):
        suite = TestSettleDeliveryStatesFromHistory()
        rid = suite._fulfilled(
            user_db, state="downloading", delivery_updated_at="2026-10-16T20:56:38+00:00"
        )
        # The older attempt is recorded second, as when a reused release row is written again.
        suite._record(
            user_db,
            rid,
            "cancelled",
            "Download stalled (no activity for 300s)",
            ended="2026-10-18T19:49:23+00:00",
        )
        suite._record(
            user_db, rid, "error", "Peer transfer failed", ended="2026-10-12T04:16:12+00:00"
        )
        with sqlite3.connect(user_db._db_path) as conn:
            conn.execute(
                "UPDATE download_history SET queued_at = ? WHERE final_status = 'cancelled'",
                ("2026-10-16T20:55:43+00:00",),
            )
            conn.execute(
                "UPDATE download_history SET queued_at = ? WHERE final_status = 'error'",
                ("2026-10-12T03:36:39+00:00",),
            )

        assert (
            auto_download.settle_delivery_states_from_history(user_db, db_path=user_db._db_path)
            == 1
        )
        assert suite._state(user_db, rid) == "cancelled"
