"""Tests for the audiobook existence check (does Audible sell this audiobook at all?).

The store answers are real ``/api/search/books`` responses from Audiobookshelf, recorded
in ``fixtures/audible_search`` (descriptions trimmed): a book the US store sells, a book
neither store sells, the canary on both stores, and an alternative-title listing.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import requests

from shelfmark.core import auto_download, existence_check
from shelfmark.core.existence_match import find_match, search_title, titles_match
from shelfmark.core.requests_service import reject_request
from shelfmark.core.user_db import UserDB
from tests.core.fakes import FakeConfig

FIXTURES = Path(__file__).parent / "fixtures" / "audible_search"
NOW = datetime(2026, 10, 11, 18, 0, tzinfo=UTC)


def _recorded(name: str) -> list[dict]:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


# (query sent, provider) -> recorded answer. Anything else is an empty answer, which is
# also what Audiobookshelf gives for a title the store does not know.
RECORDED = {
    ("The Advocate's Dilemma", "audible"): "us_advocates_dilemma",
    ("Young Zaphod Plays It Safe", "audible"): "us_young_zaphod",
    ("Young Zaphod Plays It Safe", "audible.uk"): "uk_young_zaphod",
    ("The Hobbit", "audible"): "us_hobbit",
    ("The Hobbit", "audible.uk"): "uk_hobbit",
    ("The Mummy", "audible"): "us_the_mummy",
}


class FakeStore:
    """Stands in for ``audiobookshelf.search_books``, answering from the recordings."""

    def __init__(self, *, fail_on: str | None = None, canary_lost: bool = False) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self.fail_on = fail_on
        self.canary_lost = canary_lost

    def __call__(self, title, author, provider, *, timeout=None, overrides=None):
        self.calls.append((title, author, provider))
        if self.fail_on == provider:
            raise requests.ConnectionError("Audiobookshelf did not answer")
        if self.canary_lost and title == existence_check.CANARY[0]:
            return []
        name = RECORDED.get((title, provider))
        return _recorded(name) if name else []


@pytest.fixture
def store(monkeypatch):
    fake = FakeStore()
    monkeypatch.setattr(existence_check.audiobookshelf, "search_books", fake)
    return fake


@pytest.fixture
def settings(monkeypatch):
    fake = FakeConfig(
        AUTO_DOWNLOAD_EXISTENCE_CHECK_ENABLED=True,
        AUTO_DOWNLOAD_EXISTENCE_CHECK_AFTER_PASSES=3,
    )
    monkeypatch.setattr(existence_check, "app_config", fake)
    return fake


@pytest.fixture
def user_db(tmp_path):
    db = UserDB(str(tmp_path / "users.db"))
    db.initialize()
    return db


@pytest.fixture
def admin(user_db):
    return user_db.create_user(username="admin", role="admin")


def _request(user_db, admin, title, author, content_type="audiobook"):
    return user_db.create_request(
        user_id=admin["id"],
        content_type=content_type,
        request_level="book",
        policy_mode="request_book",
        book_data={
            "title": title,
            "author": author,
            "provider": "hardcover",
            "provider_id": "1",
            "content_type": content_type,
        },
    )


def _pass(user_db, admin, request, status="no_match", *, now=NOW, content_type="audiobook"):
    row = user_db.get_request(int(request["id"]))
    outcome = auto_download.AutoDownloadOutcome(int(request["id"]), status)
    return existence_check.after_request(
        user_db,
        row,
        outcome,
        admin_user_id=admin["id"],
        content_type=content_type,
        now=now,
    )


def _state(user_db, request):
    book_data = user_db.get_request(int(request["id"]))["book_data"]
    return book_data.get(existence_check.BOOK_DATA_KEY)


class TestMatching:
    @pytest.mark.parametrize(
        ("raw", "author", "expected"),
        [
            ("The Frood: The Authorised and Very Official History", "Jem Roberts", "The Frood"),
            ("The Hobbit, or There and Back Again", "Tolkien", "The Hobbit"),
            ("Running Blind aka The Visitor", "Lee Child", "Running Blind"),
            ("Born a Crime- Stories from a South African Childhood", "", "Born a Crime"),
            ("The Armour of Light (Unabridged)", "", "The Armour of Light"),
            ("[Pillars of the Earth  02] - World Without End", "", "World Without End"),
            ("Robert Langdon, Book 4 - Inferno", "Dan Brown", "Inferno"),
            (
                "Anne Rice - Mayfair Witches 01 - The Witching Hour",
                "Anne Rice",
                "The Witching Hour",
            ),
            (
                "HGTTG [01] The Hitchhiker's Guide to the Galaxy",
                "",
                "The Hitchhiker's Guide to the Galaxy",
            ),
            ("And Another Thing... (Unabridged)", "", "And Another Thing"),
            ("Fahrenheit 451 - A Novel", "", "Fahrenheit 451"),
            ("Catch-22", "", "Catch-22"),
        ],
    )
    def test_search_title(self, raw, author, expected):
        assert search_title(raw, author) == expected

    @pytest.mark.parametrize(
        ("wanted", "candidate"),
        [
            ("The Book of Lost Tales, Part II", "The Book of Lost Tales, Part Two"),
            ("The Book of Lost Tales, Part II", "The Book of Lost Tales: Part 2"),
            ("The Fall of Númenor", "The Fall of Numenor"),
            ("Morgoth’s Ring", "Morgoth's Ring"),
            ("Merry Christmas", "Merry Christmas, Alex Cross"),
            ("Start With Why", "Start with Why 15th Anniversary Edition"),
            ("The Reign of Osiris", "Ramses the Damned: The Reign of Osiris"),
            ("The Mummy, or Ramses the Damned", "The Mummy or Ramses the Damned"),
            ("The Armour of Light", "The Armor of Light"),
        ],
    )
    def test_same_book(self, wanted, candidate):
        assert titles_match(wanted, candidate)

    @pytest.mark.parametrize(
        ("wanted", "candidate"),
        [
            ("Cross", "Cross Fire"),  # one word never matches a longer title
            ("The Lays of Beleriand", "The Lost Road"),
            ("Sauron Defeated", "Sauron"),
        ],
    )
    def test_different_book(self, wanted, candidate):
        assert not titles_match(wanted, candidate)

    def test_author_is_any_surname_in_the_field(self):
        results = [{"title": "The Martian", "author": "Andy Weir", "asin": "B082BHJMFF"}]
        assert find_match("The Martian", "R.C. Bray, Andy Weir", results) is not None
        assert find_match("The Martian", "Kristin Hannah/Polly Stone", results) is None

    def test_result_without_asin_is_skipped(self):
        results = [{"title": "Blood Canticle", "author": "Anne Rice", "asin": ""}]
        assert find_match("Blood Canticle", "Anne Rice", results) is None


class TestLookUp:
    def test_found_on_the_us_store(self, store):
        result = existence_check.look_up("The Advocate's Dilemma", "Teresa Burrell")

        assert result.status == "found"
        assert result.store == "US"
        assert result.match.asin == "B00K23YZJM"
        assert [call[2] for call in store.calls] == ["audible"]

    def test_found_under_its_alternative_title(self, store):
        result = existence_check.look_up("The Mummy, or Ramses the Damned", "Anne Rice")

        assert result.status == "found"
        assert result.match.asin == "B002UUKMQA"

    def test_not_found_on_either_store(self, store):
        result = existence_check.look_up("Young Zaphod Plays It Safe", "Douglas Adams")

        assert result.status == "not_found"
        asked = [(title, provider) for title, _author, provider in store.calls]
        assert asked == [
            ("Young Zaphod Plays It Safe", "audible"),
            ("Young Zaphod Plays It Safe", "audible.uk"),
            ("The Hobbit", "audible"),
            ("The Hobbit", "audible.uk"),
        ]

    def test_store_that_lost_the_canary_too_decides_nothing(self, monkeypatch):
        monkeypatch.setattr(
            existence_check.audiobookshelf, "search_books", FakeStore(canary_lost=True)
        )

        result = existence_check.look_up("Young Zaphod Plays It Safe", "Douglas Adams")

        assert result.status == "unknown"

    @pytest.mark.parametrize("failing", ["audible", "audible.uk"])
    def test_error_is_unknown_not_missing(self, monkeypatch, failing):
        monkeypatch.setattr(
            existence_check.audiobookshelf, "search_books", FakeStore(fail_on=failing)
        )

        result = existence_check.look_up("Young Zaphod Plays It Safe", "Douglas Adams")

        assert result.status == "unknown"
        assert "did not answer" in result.detail

    def test_us_store_is_asked_in_us_spelling(self, store):
        existence_check.look_up("The Armour of Light", "Ken Follett")

        asked = [(title, provider) for title, _author, provider in store.calls[:2]]
        assert asked == [("The Armor of Light", "audible"), ("The Armour of Light", "audible.uk")]

    def test_no_budget_left_asks_nothing(self, store):
        result = existence_check.look_up(
            "Young Zaphod Plays It Safe", "Douglas Adams", budget_seconds=1
        )

        assert result.status == "unknown"
        assert store.calls == []


class TestAfterRequest:
    def test_setting_off_counts_nothing(self, user_db, admin, store, settings):
        settings.values["AUTO_DOWNLOAD_EXISTENCE_CHECK_ENABLED"] = False
        request = _request(user_db, admin, "Young Zaphod Plays It Safe", "Douglas Adams")

        for _ in range(5):
            assert _pass(user_db, admin, request) is None

        assert _state(user_db, request) is None
        assert user_db.get_request(int(request["id"]))["status"] == "pending"
        assert store.calls == []

    def test_ebook_requests_are_not_checked(self, user_db, admin, store, settings):
        request = _request(user_db, admin, "Young Zaphod Plays It Safe", "Douglas Adams", "ebook")

        for _ in range(5):
            assert _pass(user_db, admin, request, content_type="ebook") is None

        assert _state(user_db, request) is None
        assert store.calls == []

    def test_not_listed_is_marked_not_declined(self, user_db, admin, store, settings):
        request = _request(user_db, admin, "Young Zaphod Plays It Safe", "Douglas Adams")

        assert _pass(user_db, admin, request) == "counted"
        assert _pass(user_db, admin, request) == "counted"
        assert store.calls == []
        assert _pass(user_db, admin, request) == "not_listed"

        row = user_db.get_request(int(request["id"]))
        assert row["status"] == "pending"
        assert row["last_failure_reason"] == (
            "Not found on Audible (US/UK) after 3 searches, 2026-10-11"
        )
        assert row["reviewed_by"] is None
        assert _state(user_db, request)["result"] == "not_found"
        # Not asked again inside the week.
        assert _pass(user_db, admin, request) == "waiting"

    def test_listed_stays_pending_and_says_why(self, user_db, admin, store, settings):
        settings.values["AUTO_DOWNLOAD_EXISTENCE_CHECK_AFTER_PASSES"] = 1
        request = _request(user_db, admin, "The Advocate's Dilemma", "Teresa Burrell")

        assert _pass(user_db, admin, request) == "listed"

        row = user_db.get_request(int(request["id"]))
        assert row["status"] == "pending"
        assert row["last_failure_reason"] == (
            "Exists on Audible (B00K23YZJM), not on our sources yet"
        )
        state = _state(user_db, request)
        assert state["result"] == "found"
        assert state["asin"] == "B00K23YZJM"

    def test_listed_book_is_asked_again_after_a_week(self, user_db, admin, store, settings):
        settings.values["AUTO_DOWNLOAD_EXISTENCE_CHECK_AFTER_PASSES"] = 1
        request = _request(user_db, admin, "The Advocate's Dilemma", "Teresa Burrell")
        assert _pass(user_db, admin, request) == "listed"
        asked = len(store.calls)

        assert _pass(user_db, admin, request, now=NOW + timedelta(days=1)) == "waiting"
        assert _pass(user_db, admin, request, now=NOW + timedelta(days=6)) == "waiting"
        assert len(store.calls) == asked

        assert _pass(user_db, admin, request, now=NOW + timedelta(days=7)) == "listed"
        assert len(store.calls) == asked + 1
        assert _state(user_db, request)["checked_at"] == (NOW + timedelta(days=7)).isoformat(
            timespec="seconds"
        )

    def test_lookup_error_changes_nothing_and_asks_again_next_pass(
        self, user_db, admin, settings, monkeypatch
    ):
        settings.values["AUTO_DOWNLOAD_EXISTENCE_CHECK_AFTER_PASSES"] = 1
        failing = FakeStore(fail_on="audible")
        monkeypatch.setattr(existence_check.audiobookshelf, "search_books", failing)
        request = _request(user_db, admin, "Young Zaphod Plays It Safe", "Douglas Adams")

        assert _pass(user_db, admin, request) == "unknown"
        assert _pass(user_db, admin, request) == "unknown"

        row = user_db.get_request(int(request["id"]))
        assert row["status"] == "pending"
        assert row["last_failure_reason"] is None
        assert len(failing.calls) == 2

    def test_a_match_resets_the_count(self, user_db, admin, store, settings):
        request = _request(user_db, admin, "Young Zaphod Plays It Safe", "Douglas Adams")
        _pass(user_db, admin, request)
        _pass(user_db, admin, request)
        assert _state(user_db, request)["no_match_passes"] == 2

        assert _pass(user_db, admin, request, status="queued") is None

        assert _state(user_db, request) is None

    def test_a_search_error_neither_counts_nor_resets(self, user_db, admin, store, settings):
        request = _request(user_db, admin, "Young Zaphod Plays It Safe", "Douglas Adams")
        _pass(user_db, admin, request)

        assert _pass(user_db, admin, request, status="error") is None

        assert _state(user_db, request)["no_match_passes"] == 1

    def test_any_rejection_drops_the_count_so_a_reopen_starts_over(
        self, user_db, admin, store, settings
    ):
        request = _request(user_db, admin, "Young Zaphod Plays It Safe", "Douglas Adams")
        _pass(user_db, admin, request)
        _pass(user_db, admin, request)

        reject_request(user_db, request_id=int(request["id"]), admin_user_id=admin["id"])
        assert _state(user_db, request) is None

        # What the admin reopen does to the row: back to pending, review undone.
        conn = user_db._connect()
        try:
            conn.execute(
                "UPDATE download_requests SET status = 'pending', reviewed_by = NULL, "
                "reviewed_at = NULL WHERE id = ?",
                (int(request["id"]),),
            )
            conn.commit()
        finally:
            conn.close()
        assert _pass(user_db, admin, request) == "counted"
        assert _state(user_db, request)["no_match_passes"] == 1

    def test_pass_works_from_the_stored_row_not_the_stale_one(
        self, user_db, admin, store, settings
    ):
        request = _request(user_db, admin, "Young Zaphod Plays It Safe", "Douglas Adams")
        stale = user_db.get_request(int(request["id"]))
        # The pass recorded a failed release after the row was read.
        book_data = dict(stale["book_data"], failed_releases=[{"source": "s", "source_id": "1"}])
        user_db.update_request(int(request["id"]), book_data=book_data)

        existence_check.after_request(
            user_db,
            stale,
            auto_download.AutoDownloadOutcome(int(request["id"]), "no_match"),
            admin_user_id=admin["id"],
            content_type="audiobook",
            now=NOW,
        )

        stored = user_db.get_request(int(request["id"]))["book_data"]
        assert stored["failed_releases"] == [{"source": "s", "source_id": "1"}]
        assert stored[existence_check.BOOK_DATA_KEY]["no_match_passes"] == 1


def test_the_pass_runs_the_check_and_counts_not_listed(user_db, admin, store, monkeypatch):
    monkeypatch.setattr(
        auto_download,
        "app_config",
        FakeConfig(AUTO_DOWNLOAD_ENABLED=True),
    )
    monkeypatch.setattr(
        existence_check,
        "app_config",
        FakeConfig(
            AUTO_DOWNLOAD_EXISTENCE_CHECK_ENABLED=True,
            AUTO_DOWNLOAD_EXISTENCE_CHECK_AFTER_PASSES=1,
        ),
    )
    monkeypatch.setattr(auto_download, "_configured_source_priority", lambda _t: ["src"])
    monkeypatch.setattr(
        auto_download,
        "auto_download_request",
        lambda _db, row, **_kw: auto_download.AutoDownloadOutcome(int(row["id"]), "no_match"),
    )
    request = _request(user_db, admin, "Young Zaphod Plays It Safe", "Douglas Adams")

    summary = auto_download.auto_download_pending(
        user_db, queue_release=lambda *_a, **_kw: (True, None), admin_user_id=admin["id"]
    )

    assert summary["no_match"] == 1
    assert summary["not_listed"] == 1
    assert user_db.get_request(int(request["id"]))["status"] == "pending"
