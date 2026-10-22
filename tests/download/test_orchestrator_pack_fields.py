"""Pack fields (multi_book / book_plan) survive queueing and restart-safe retry."""

import pytest

from shelfmark.core.models import DownloadTask
from shelfmark.download import orchestrator

PLAN = [
    {"title": "Leviathan Wakes", "series_position": 1.0, "year": 2011, "files": ["a.m4b"]},
    {"title": "Caliban's War", "series_position": 2.0, "year": 2012, "files": ["b.m4b"]},
]


def test_task_defaults_to_single_book():
    task = DownloadTask(task_id="t", source="prowlarr", title="T")
    assert task.multi_book is False
    assert task.book_plan is None


def test_retry_payload_round_trips_pack_fields():
    task = DownloadTask(task_id="t", source="prowlarr", title="T", multi_book=True, book_plan=PLAN)
    payload = orchestrator.serialize_task_for_retry(task)
    restored = orchestrator._restore_task_from_retry_payload(payload)
    assert restored is not None
    assert restored.multi_book is True
    assert restored.book_plan == PLAN


def test_retry_payload_drops_malformed_plan():
    payload = orchestrator.serialize_task_for_retry(
        DownloadTask(task_id="t", source="prowlarr", title="T")
    )
    payload["book_plan"] = "not a list"
    restored = orchestrator._restore_task_from_retry_payload(payload)
    assert restored is not None
    assert restored.book_plan is None


def test_queue_release_reads_pack_fields(monkeypatch):
    captured: dict[str, DownloadTask] = {}

    def fake_add(task: DownloadTask) -> bool:
        captured["task"] = task
        return True

    monkeypatch.setattr(orchestrator.config, "get", lambda _key, default=None, **_kw: default)
    monkeypatch.setattr(orchestrator, "_source_unavailable_message", lambda _source: None)
    monkeypatch.setattr(orchestrator.book_queue, "add", fake_add)
    monkeypatch.setattr(orchestrator, "ws_manager", None)

    ok, error = orchestrator.queue_release(
        {
            "source": "direct_download",
            "source_id": "abc",
            "title": "The Expanse",
            "content_type": "audiobook",
            "multi_book": True,
            "book_plan": PLAN,
        },
        0,
    )
    assert ok, error
    assert captured["task"].multi_book is True
    assert captured["task"].book_plan == PLAN


def _queue(monkeypatch, release):
    captured: dict[str, DownloadTask] = {}

    def fake_add(task: DownloadTask) -> bool:
        captured["task"] = task
        return True

    monkeypatch.setattr(orchestrator.config, "get", lambda _key, default=None, **_kw: default)
    monkeypatch.setattr(orchestrator, "_source_unavailable_message", lambda _source: None)
    monkeypatch.setattr(orchestrator.book_queue, "add", fake_add)
    monkeypatch.setattr(orchestrator, "ws_manager", None)
    ok, error = orchestrator.queue_release(release, 0)
    return ok, error, captured.get("task")


def test_queue_release_drops_hostile_format(monkeypatch):
    ok, error, task = _queue(
        monkeypatch,
        {
            "source": "direct_download",
            "source_id": "abc",
            "title": ".",
            "format": "/../../../config/x",
        },
    )
    assert ok, error
    assert task is not None
    assert task.format is None


def test_queue_release_normalises_format(monkeypatch):
    ok, _error, task = _queue(
        monkeypatch,
        {"source": "direct_download", "source_id": "abc", "title": "t", "format": "EPUB"},
    )
    assert ok
    assert task is not None
    assert task.format == "epub"


@pytest.mark.parametrize("source", ["direct_download", "libgen"])
@pytest.mark.parametrize("source_id", ["../../x", "a/b", "a\\b", "x..y", "libgen:../../x"])
def test_queue_release_rejects_path_source_id(monkeypatch, source, source_id):
    ok, error, task = _queue(
        monkeypatch,
        {"source": source, "source_id": source_id, "title": "t", "format": "epub"},
    )
    assert not ok
    assert error
    assert task is None
