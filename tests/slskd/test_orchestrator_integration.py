"""slskd releases survive queueing, retry serialization and a full handler round-trip."""

from pathlib import Path
from threading import Event

import pytest

from shelfmark.core.models import DownloadTask
from shelfmark.download import orchestrator
from shelfmark.release_sources import get_handler
from shelfmark.release_sources.slskd.handler import SlskdHandler, spec_from_task


@pytest.fixture(autouse=True)
def _original_enqueue_path(monkeypatch):
    """These tests cover the original enqueue-and-poll-the-list path; isolated mode has its own
    tests in test_handler_isolated.py."""
    from shelfmark.release_sources.slskd.handler import SlskdHandler

    monkeypatch.setattr(SlskdHandler, "_isolate_enabled", lambda self: False)


FILE = "@@share\\Books\\Author\\Book.epub"


def _release_data():
    return {
        "source": "slskd",
        "source_id": "slskd:deadbeef",
        "title": "Book",
        "format": "epub",
        "protocol": "soulseek",
        "content_type": "ebook",
        "extra": {
            "username": "peer",
            "directory": "@@share\\Books\\Author",
            "files": [{"filename": FILE, "size": 1000}],
            "upload_speed": 1,
        },
    }


def test_queue_release_persists_soulseek_spec(monkeypatch):
    captured: dict[str, DownloadTask] = {}

    def fake_add(task: DownloadTask) -> bool:
        captured["task"] = task
        return True

    monkeypatch.setattr(orchestrator.config, "get", lambda _key, default=None, **_kw: default)
    monkeypatch.setattr(orchestrator, "_source_unavailable_message", lambda _source: None)
    monkeypatch.setattr(orchestrator.book_queue, "add", fake_add)
    monkeypatch.setattr(orchestrator, "ws_manager", None)

    ok, error = orchestrator.queue_release(_release_data(), 0)

    assert ok, error
    task = captured["task"]
    assert task.source == "slskd"
    assert task.task_id == "slskd:deadbeef"
    assert task.retry_download_protocol == "soulseek"
    assert task.retry_download_url is None
    spec = spec_from_task(task)
    assert spec is not None
    assert spec.username == "peer"
    assert spec.filenames == [FILE]


def test_retry_payload_round_trips_soulseek_spec():
    handler = get_handler("slskd")
    fields = handler.build_retry_resolution_fields(_release_data())
    task = DownloadTask(task_id="slskd:deadbeef", source="slskd", title="Book", **fields)

    payload = orchestrator.serialize_task_for_retry(task)
    restored = orchestrator._restore_task_from_retry_payload(payload)

    assert restored is not None
    assert spec_from_task(restored) == spec_from_task(task)


def test_handler_round_trip_from_queued_task(monkeypatch, tmp_path):
    """Queue → task → handler download using the persisted context only (no cache)."""
    import shelfmark.release_sources.slskd.handler as mod

    handler = get_handler("slskd")
    assert isinstance(handler, SlskdHandler)
    fields = handler.build_retry_resolution_fields(_release_data())
    task = DownloadTask(task_id="slskd:deadbeef", source="slskd", title="Book", **fields)

    completed = tmp_path / "Author" / "Book.epub"
    completed.parent.mkdir(parents=True)
    completed.write_bytes(b"epub")

    class Client:
        def enqueue_downloads(self, username, files):
            assert username == "peer"
            return [{"id": "t1", "filename": files[0]["filename"]}]

        def list_downloads(self, username):
            return [
                {
                    "id": "t1",
                    "filename": FILE,
                    "state": "Completed, Succeeded",
                    "bytesTransferred": 1000,
                    "removed": False,
                }
            ]

        def cancel_transfer(self, username, transfer_id, *, remove):
            return True

        def get_download_directory(self):
            return str(tmp_path)

    monkeypatch.setattr(mod.config, "get", lambda k, d=None: d)
    monkeypatch.setattr(SlskdHandler, "_get_client", lambda self: Client())
    monkeypatch.setattr(SlskdHandler, "_poll_interval", lambda self: 0)
    monkeypatch.setattr(SlskdHandler, "_completed_path_timeout_seconds", lambda self: 0)

    statuses: list[tuple[str, str | None]] = []
    result = handler.download(task, Event(), lambda p: None, lambda s, m: statuses.append((s, m)))

    assert result == str(completed)
    assert Path(result).exists()
    assert "error" not in {s for s, _ in statuses}
    assert statuses[0] == ("resolving", "Sending to slskd")
