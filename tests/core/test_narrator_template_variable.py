"""{Narrator} naming placeholder, and MyAnonamouse series/narrator reaching the download task."""

import json

import pytest

from shelfmark.core.models import DownloadTask
from shelfmark.core.naming import KNOWN_TOKENS, build_library_path, parse_naming_template
from shelfmark.download import orchestrator
from shelfmark.download.postprocess.transfer import build_metadata_dict
from shelfmark.release_sources.prowlarr.mam import parse_torrent_details

TEMPLATE = "{Author}/{Title} {{Narrator}}/{Title}"


def _metadata(narrator: str | None) -> dict:
    return {"Author": "Christopher Ruocchio", "Title": "Empire of Silence", "Narrator": narrator}


class TestNarratorPlaceholder:
    def test_known_token(self):
        assert "narrator" in KNOWN_TOKENS

    def test_audiobookshelf_braces(self):
        assert parse_naming_template(TEMPLATE, _metadata("Samuel Roukin")) == (
            "Christopher Ruocchio/Empire of Silence {Samuel Roukin}/Empire of Silence"
        )

    @pytest.mark.parametrize("narrator", [None, ""])
    def test_missing_narrator_leaves_no_braces_or_trailing_space(self, narrator):
        assert parse_naming_template(TEMPLATE, _metadata(narrator)) == (
            "Christopher Ruocchio/Empire of Silence/Empire of Silence"
        )

    def test_brace_inside_the_block_prefix(self):
        template = "{Title}{ {Narrator}}"
        assert parse_naming_template(template, _metadata("Samuel Roukin")) == (
            "Empire of Silence {Samuel Roukin}"
        )
        assert parse_naming_template(template, _metadata(None)) == "Empire of Silence"

    def test_plain_and_prefixed_forms(self):
        assert parse_naming_template("{Title}{ - Narrator}", _metadata("A")) == (
            "Empire of Silence - A"
        )
        assert parse_naming_template("{Title} ({Narrator})", _metadata(None)) == (
            "Empire of Silence"
        )

    def test_narrator_is_sanitized(self):
        rendered = parse_naming_template("{Narrator}", {"Narrator": "A/B: C"})
        assert "/" not in rendered.replace("_", "")
        assert ":" not in rendered

    def test_library_path(self, tmp_path):
        path = build_library_path(str(tmp_path), TEMPLATE, _metadata("Samuel Roukin"), "m4b")
        assert path.parent.name == "Empire of Silence {Samuel Roukin}"


def test_task_narrator_reaches_template_metadata():
    task = DownloadTask(task_id="t", source="prowlarr", title="T", narrator="Samuel Roukin")
    assert build_metadata_dict(task)["Narrator"] == "Samuel Roukin"


def test_retry_payload_round_trips_narrator():
    task = DownloadTask(task_id="t", source="prowlarr", title="T", narrator="Samuel Roukin")
    restored = orchestrator._restore_task_from_retry_payload(
        orchestrator.serialize_task_for_retry(task)
    )
    assert restored is not None
    assert restored.narrator == "Samuel Roukin"


def test_mam_series_name_and_position():
    details = parse_torrent_details(
        {
            "id": 1,
            "series_info": json.dumps({"3": ["The Sun Eater", "2.5"], "4": ["Other", "7"]}),
        }
    )
    assert details.series == "The Sun Eater #2.5, Other #7"
    assert details.series_name == "The Sun Eater"
    assert details.series_position == 2.5


def test_mam_series_range_has_no_position():
    details = parse_torrent_details({"id": 1, "series_info": json.dumps({"3": ["Saga", "1-3"]})})
    assert details.series_name == "Saga"
    assert details.series_position is None


class TestQueueRelease:
    @pytest.fixture
    def queued(self, monkeypatch):
        captured: dict[str, DownloadTask] = {}

        def fake_add(task: DownloadTask) -> bool:
            captured["task"] = task
            return True

        monkeypatch.setattr(orchestrator.config, "get", lambda _key, default=None, **_kw: default)
        monkeypatch.setattr(orchestrator, "_source_unavailable_message", lambda _source: None)
        monkeypatch.setattr(orchestrator.book_queue, "add", fake_add)
        monkeypatch.setattr(orchestrator, "ws_manager", None)

        def queue(release: dict) -> DownloadTask:
            ok, error = orchestrator.queue_release(
                {"source": "prowlarr", "source_id": "x", "title": "Empire of Silence", **release},
                0,
            )
            assert ok, error
            return captured["task"]

        return queue

    def test_narrator_and_mam_series_from_extra(self, queued):
        task = queued(
            {
                "extra": {
                    "narrator": "Samuel Roukin",
                    "series_name": "The Sun Eater",
                    "series_position": 1.0,
                }
            }
        )
        assert task.narrator == "Samuel Roukin"
        assert task.series_name == "The Sun Eater"
        assert task.series_position == 1.0

    def test_provider_series_wins_and_is_not_paired_with_another_series_number(self, queued):
        task = queued(
            {
                "series_name": "Sun Eater",
                "extra": {"series_name": "Some Omnibus", "series_position": 3.0},
            }
        )
        assert task.series_name == "Sun Eater"
        assert task.series_position is None

    def test_matching_release_series_can_supply_the_number(self, queued):
        task = queued(
            {
                "series_name": "The Sun Eater",
                "extra": {"series_name": "the sun eater", "series_position": 1.0},
            }
        )
        assert task.series_position == 1.0

    def test_no_narrator(self, queued):
        assert queued({"extra": {"narrator": "  "}}).narrator is None
