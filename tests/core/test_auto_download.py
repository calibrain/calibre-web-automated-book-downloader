"""Tests for strict release matching and auto-download of pending requests."""

import pytest

from shelfmark.core import auto_download, library_index, text_match
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

    @pytest.mark.parametrize("seeders", [0])
    def test_torrent_below_minimum_seeders_rejected(self, seeders):
        assert _strict(_release(seeders=seeders), min_seeders=1) is False

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

    @pytest.mark.parametrize("priority", [[], "garbage", [{"id": "unknown"}]])
    def test_falls_back_to_all_enabled_sources(self, monkeypatch, priority):
        self._configure(monkeypatch, priority)

        assert auto_download._configured_source_priority("audiobook") == ["first", "second"]


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
        monkeypatch.setattr(auto_download, "app_config", _Config())
        monkeypatch.setattr(
            "shelfmark.core.library_index.is_in_library", lambda *_args, **_kwargs: True
        )

        def _never_search(*_args, **_kwargs):
            raise AssertionError("release search must not run")

        monkeypatch.setattr(auto_download, "search_book_releases", _never_search)

        outcome = self._run(user_db, row, admin)

        assert outcome.status == "in_library"
        assert user_db.get_request(row["id"])["status"] == "pending"

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
            "search_book_releases",
            lambda *_a, **_k: pytest.fail("a request must not be searched for blind"),
        )

        outcome = self._run(user_db, row, admin)

        assert outcome.status == "error"
        assert "library" in outcome.detail
        assert user_db.get_request(row["id"])["status"] == "pending"

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
        monkeypatch.setattr(auto_download, "app_config", _Config(SUPPORTED_FORMATS=["pdf"]))

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
