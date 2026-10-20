"""Unit tests for the slskd download handler."""

from pathlib import Path
from threading import Event
from unittest.mock import MagicMock

import pytest

from shelfmark.core.models import DownloadTask
from shelfmark.release_sources import get_handler
from shelfmark.release_sources.slskd.api import SlskdError
from shelfmark.release_sources.slskd.handler import (
    SlskdDownloadSpec,
    SlskdHandler,
    _snapshot,
    local_path_for,
    resolve_local_download_root,
    spec_from_release_data,
    spec_from_task,
)


@pytest.fixture(autouse=True)
def _original_enqueue_path(monkeypatch):
    """These tests cover the original enqueue-and-poll-the-list path; isolated mode has its own
    tests in test_handler_isolated.py."""
    from shelfmark.release_sources.slskd.handler import SlskdHandler

    monkeypatch.setattr(SlskdHandler, "_isolate_enabled", lambda self: False)


# ── helpers ────────────────────────────────────────────────────────────────────

REMOTE_DIR = "@@share\\Books\\Author"
FILE_A = f"{REMOTE_DIR}\\Book.epub"
FILE_B = f"{REMOTE_DIR}\\Book.mobi"


class ProgressRecorder:
    def __init__(self):
        self.progress_values: list[float] = []
        self.status_updates: list[tuple[str, str | None]] = []

    def progress_callback(self, v: float):
        self.progress_values.append(v)

    def status_callback(self, status: str, message: str | None):
        self.status_updates.append((status, message))

    @property
    def last_status(self) -> str | None:
        return self.status_updates[-1][0] if self.status_updates else None

    @property
    def last_message(self) -> str | None:
        return self.status_updates[-1][1] if self.status_updates else None

    @property
    def statuses(self) -> list[str]:
        return [s[0] for s in self.status_updates]


class FakeSlskd:
    """In-memory stand-in for SlskdClient that scripts transfer state per poll."""

    def __init__(self, *, download_dir="/downloads/complete", polls=None):
        self.download_dir = download_dir
        # Each poll returns the next list of transfer dicts; the last repeats forever.
        self.polls = list(polls or [])
        self.enqueued: list[tuple[str, list[dict]]] = []
        self.cancelled: list[tuple[str, str, bool]] = []
        self.enqueue_error: Exception | None = None
        self.poll_count = 0

    def enqueue_downloads(self, username, files):
        if self.enqueue_error:
            raise self.enqueue_error
        self.enqueued.append((username, files))
        return [
            {"id": f"t{i}", "filename": f["filename"], "state": "Queued, Locally"}
            for i, f in enumerate(files)
        ]

    def list_downloads(self, username):
        self.poll_count += 1
        if not self.polls:
            return []
        index = min(self.poll_count - 1, len(self.polls) - 1)
        return list(self.polls[index])

    def cancel_transfer(self, username, transfer_id, *, remove):
        """Mimic slskd: a live transfer is cancelled (record kept), a complete one removed."""
        self.cancelled.append((username, transfer_id, remove))
        for poll in self.polls:
            for transfer in poll:
                if transfer.get("id") != transfer_id:
                    continue
                if transfer["state"].startswith("Completed"):
                    if remove:
                        transfer["removed"] = True
                else:
                    transfer["state"] = "Completed, Cancelled"
        return True

    def get_download_directory(self):
        return self.download_dir


def _transfer(filename, state, *, transferred=0, size=1000, tid=None, speed=0, **extra):
    return {
        "id": tid or f"id-{filename}",
        "filename": filename,
        "state": state,
        "bytesTransferred": transferred,
        "size": size,
        "averageSpeed": speed,
        "removed": False,
        **extra,
    }


def _release_data(files=None, username="peer", directory=REMOTE_DIR):
    files = files if files is not None else [{"filename": FILE_A, "size": 1000}]
    return {
        "source": "slskd",
        "source_id": "slskd:abc",
        "title": "Book",
        "extra": {"username": username, "directory": directory, "files": files},
    }


def _task(files=None, task_id="slskd:abc", username="peer"):
    files = files if files is not None else [{"filename": FILE_A, "size": 1000}]
    return DownloadTask(
        task_id=task_id,
        source="slskd",
        title="Book",
        retry_source_context={"username": username, "directory": REMOTE_DIR, "files": files},
    )


@pytest.fixture
def fast_handler(monkeypatch):
    """Handler with no sleeps between polls / path checks."""
    monkeypatch.setattr(SlskdHandler, "_poll_interval", lambda self: 0)
    monkeypatch.setattr(SlskdHandler, "_completed_path_retry_interval", lambda self: 0)
    monkeypatch.setattr(SlskdHandler, "_completed_path_timeout_seconds", lambda self: 0)
    monkeypatch.setattr(SlskdHandler, "_queue_timeout_seconds", lambda self: 0)
    monkeypatch.setattr(SlskdHandler, "_cancel_remove_wait", lambda self: None)
    return SlskdHandler()


@pytest.fixture
def config_values(monkeypatch):
    import shelfmark.release_sources.slskd.handler as mod

    values: dict = {}
    monkeypatch.setattr(mod.config, "get", lambda k, d=None: values.get(k, d))
    return values


def _mount(fast_handler, fake):
    fast_handler._get_client = lambda: fake  # type: ignore[method-assign]


def _write_completed(root: Path, *filenames: str) -> list[Path]:
    paths = []
    for filename in filenames:
        path = local_path_for(root, filename)
        assert path is not None
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * 10)
        paths.append(path)
    return paths


# ── spec parsing ───────────────────────────────────────────────────────────────


class TestSpec:
    def test_from_release_data(self):
        spec = spec_from_release_data(_release_data())
        assert spec == SlskdDownloadSpec(
            username="peer", directory=REMOTE_DIR, files=[{"filename": FILE_A, "size": 1000}]
        )
        assert spec.filenames == [FILE_A]
        assert spec.total_size == 1000

    def test_from_release_data_without_extra(self):
        assert spec_from_release_data({"source": "slskd"}) is None
        assert spec_from_release_data({"extra": {"username": "peer", "files": []}}) is None
        assert (
            spec_from_release_data({"extra": {"username": "", "files": [{"filename": "x"}]}})
            is None
        )

    def test_directory_derived_when_missing(self):
        spec = spec_from_release_data(_release_data(directory=""))
        assert spec is not None
        assert spec.directory == REMOTE_DIR

    def test_from_task_round_trip(self):
        handler = SlskdHandler()
        fields = handler.build_retry_resolution_fields(_release_data())
        assert fields["retry_download_protocol"] == "soulseek"
        task = DownloadTask(task_id="x", source="slskd", title="t", **fields)
        assert spec_from_task(task) == spec_from_release_data(_release_data())

    def test_from_task_without_context(self):
        assert spec_from_task(DownloadTask(task_id="x", source="slskd", title="t")) is None

    def test_bad_sizes_become_zero(self):
        spec = spec_from_release_data(_release_data(files=[{"filename": FILE_A, "size": "n/a"}]))
        assert spec is not None
        assert spec.files[0]["size"] == 0

    def test_build_retry_fields_empty_without_spec(self):
        assert SlskdHandler().build_retry_resolution_fields({"extra": {}}) == {}

    def test_list_files(self):
        files = SlskdHandler().list_files(
            _release_data(files=[{"filename": FILE_A, "size": 10}, {"filename": FILE_B, "size": 0}])
        )
        assert files is not None
        assert [(f.path, f.size) for f in files] == [("Book.epub", 10), ("Book.mobi", None)]

    def test_list_files_none_without_spec(self):
        assert SlskdHandler().list_files({"extra": {}}) is None


# ── local path resolution ──────────────────────────────────────────────────────


class TestLocalPaths:
    def test_local_path_uses_last_folder_only(self):
        assert local_path_for(Path("/dl"), FILE_A) == Path("/dl/Author/Book.epub")

    def test_local_path_forward_slashes(self):
        assert local_path_for(Path("/dl"), "share/Music/Album/01.mp3") == Path("/dl/Album/01.mp3")

    def test_local_path_bare_file(self):
        assert local_path_for(Path("/dl"), "Book.epub") == Path("/dl/Book.epub")

    def test_local_path_refuses_traversal(self):
        assert local_path_for(Path("/dl"), "a\\..\\Book.epub") is None
        assert local_path_for(Path("/dl"), "a\\Author\\..") is None
        assert local_path_for(Path("/dl"), "a\\Author\\") is None

    def test_root_override_wins(self, config_values):
        config_values["SLSKD_DOWNLOAD_PATH"] = "/mnt/slskd "
        client = MagicMock()
        root, error = resolve_local_download_root(client)
        assert (root, error) == (Path("/mnt/slskd"), None)
        client.get_download_directory.assert_not_called()

    def test_root_from_slskd_options(self, config_values):
        client = MagicMock()
        client.get_download_directory.return_value = "/downloads/complete"
        assert resolve_local_download_root(client) == (Path("/downloads/complete"), None)

    def test_root_remapped_by_path_mapping(self, config_values):
        config_values["PROWLARR_REMOTE_PATH_MAPPINGS"] = [
            {"host": "slskd", "remotePath": "/downloads", "localPath": "/mnt/slskd"},
            {"host": "qbittorrent", "remotePath": "/downloads", "localPath": "/mnt/qb"},
        ]
        client = MagicMock()
        client.get_download_directory.return_value = "/downloads/complete"
        assert resolve_local_download_root(client) == (Path("/mnt/slskd/complete"), None)

    def test_root_error_when_slskd_reports_nothing(self, config_values):
        client = MagicMock()
        client.get_download_directory.return_value = None
        root, error = resolve_local_download_root(client)
        assert root is None
        assert error and "Downloads Path" in error

    def test_root_error_when_slskd_unreachable(self, config_values):
        client = MagicMock()
        client.get_download_directory.side_effect = SlskdError("down")
        root, error = resolve_local_download_root(client)
        assert root is None
        assert error and "down" in error


# ── snapshot aggregation ───────────────────────────────────────────────────────


class TestSnapshot:
    def _spec(self):
        return SlskdDownloadSpec(
            username="peer",
            directory=REMOTE_DIR,
            files=[{"filename": FILE_A, "size": 1000}, {"filename": FILE_B, "size": 1000}],
        )

    def test_all_succeeded(self):
        snap = _snapshot(
            [
                _transfer(FILE_A, "Completed, Succeeded", transferred=1000),
                _transfer(FILE_B, "Completed, Succeeded", transferred=1000),
            ],
            self._spec(),
        )
        assert snap.all_succeeded
        assert snap.bytes_transferred == 2000
        assert snap.failed_state is None

    def test_partial_progress(self):
        snap = _snapshot(
            [
                _transfer(FILE_A, "InProgress", transferred=500, speed=100.0),
                _transfer(FILE_B, "Queued, Remotely", placeInQueue=3),
            ],
            self._spec(),
        )
        assert not snap.all_succeeded
        assert snap.bytes_transferred == 500
        assert snap.in_progress == 1
        assert snap.queued_remotely == 1
        assert snap.queue_positions == [3]
        assert snap.speed == 100.0

    def test_failure_detected(self):
        snap = _snapshot(
            [
                _transfer(FILE_A, "Completed, Succeeded", transferred=1000),
                _transfer(FILE_B, "Completed, Errored"),
            ],
            self._spec(),
        )
        assert snap.failed_state == "Completed, Errored"
        assert snap.failed_filename == FILE_B

    def test_ignores_unrelated_and_removed(self):
        snap = _snapshot(
            [
                _transfer("other\\thing.epub", "InProgress"),
                _transfer(FILE_A, "InProgress", removed=True),
            ],
            self._spec(),
        )
        assert snap.found == 0

    def test_prefers_live_record_over_old_failure(self):
        # slskd keeps a failed attempt around after a retry re-enqueues the file.
        snap = _snapshot(
            [
                _transfer(FILE_A, "Completed, Errored", requestedAt="2026-01-01T00:00:00"),
                _transfer(FILE_A, "InProgress", transferred=10, requestedAt="2026-01-01T00:01:00"),
                _transfer(FILE_B, "InProgress"),
            ],
            self._spec(),
        )
        assert snap.failed_state is None
        assert snap.in_progress == 2


# ── download lifecycle ─────────────────────────────────────────────────────────


class TestDownload:
    def test_registered(self):
        assert isinstance(get_handler("slskd"), SlskdHandler)

    def test_single_file_success_returns_client_path(self, fast_handler, config_values, tmp_path):
        config_values["SLSKD_DOWNLOAD_PATH"] = str(tmp_path)
        fake = FakeSlskd(
            polls=[
                [_transfer(FILE_A, "Queued, Remotely")],
                [_transfer(FILE_A, "InProgress", transferred=400, speed=2 * 1024 * 1024)],
                [_transfer(FILE_A, "Completed, Succeeded", transferred=1000, tid="done")],
            ]
        )
        _mount(fast_handler, fake)
        (expected,) = _write_completed(tmp_path, FILE_A)
        rec = ProgressRecorder()

        result = fast_handler.download(_task(), Event(), rec.progress_callback, rec.status_callback)

        assert result == str(expected)
        assert fake.enqueued == [("peer", [{"filename": FILE_A, "size": 1000}])]
        assert rec.statuses[0] == "resolving"
        assert ("downloading", "Queued at peer") in rec.status_updates
        assert any(m and m.startswith("40% (2.0 MB/s)") for _, m in rec.status_updates)
        assert rec.progress_values[-1] == 100.0
        assert "error" not in rec.statuses
        assert fast_handler._cleanup_refs[_task().task_id][2] == ["done"]

    def test_multi_file_success_stages_into_tmp(
        self, fast_handler, config_values, tmp_path, monkeypatch
    ):
        import shelfmark.download.staging as staging

        root = tmp_path / "slskd"
        staging_root = tmp_path / "staging"
        staging_root.mkdir()
        monkeypatch.setattr(staging, "get_staging_dir", lambda: staging_root)
        config_values["SLSKD_DOWNLOAD_PATH"] = str(root)
        files = [{"filename": FILE_A, "size": 10}, {"filename": FILE_B, "size": 10}]
        fake = FakeSlskd(
            polls=[
                [
                    _transfer(FILE_A, "Completed, Succeeded", transferred=10),
                    _transfer(FILE_B, "Completed, Succeeded", transferred=10),
                ]
            ]
        )
        _mount(fast_handler, fake)
        originals = _write_completed(root, FILE_A, FILE_B)
        # A stranger's file in the same per-folder directory must not come along.
        stranger = originals[0].parent / "stranger.epub"
        stranger.write_bytes(b"nope")
        rec = ProgressRecorder()

        result = fast_handler.download(
            _task(files=files), Event(), rec.progress_callback, rec.status_callback
        )

        assert result is not None
        staged = Path(result)
        assert staged.parent == staging_root
        assert sorted(p.name for p in staged.iterdir()) == ["Book.epub", "Book.mobi"]
        assert all(not p.exists() for p in originals)
        assert stranger.exists()

    def test_failed_transfer_reports_outcome_and_removes(self, fast_handler, config_values):
        fake = FakeSlskd(polls=[[_transfer(FILE_A, "Completed, Rejected", tid="x")]])
        _mount(fast_handler, fake)
        rec = ProgressRecorder()

        result = fast_handler.download(_task(), Event(), rec.progress_callback, rec.status_callback)

        assert result is None
        assert rec.last_status == "error"
        assert rec.last_message == "Peer transfer failed: Rejected"
        assert fake.cancelled == [("peer", "x", True)]

    def test_failed_file_named_in_multi_file_release(self, fast_handler, config_values):
        files = [{"filename": FILE_A, "size": 10}, {"filename": FILE_B, "size": 10}]
        fake = FakeSlskd(
            polls=[
                [
                    _transfer(FILE_A, "Completed, Succeeded", transferred=10),
                    _transfer(FILE_B, "Completed, TimedOut"),
                ]
            ]
        )
        _mount(fast_handler, fake)
        rec = ProgressRecorder()

        result = fast_handler.download(
            _task(files=files), Event(), rec.progress_callback, rec.status_callback
        )

        assert result is None
        assert rec.last_message == "Peer transfer failed: TimedOut (Book.mobi)"

    def test_cancel_flag_cancels_transfers(self, fast_handler, config_values):
        fake = FakeSlskd(polls=[[_transfer(FILE_A, "InProgress", transferred=10, tid="live")]])
        _mount(fast_handler, fake)
        cancel = Event()
        rec = ProgressRecorder()

        def status_callback(status, message):
            rec.status_callback(status, message)
            if status == "downloading":
                cancel.set()

        result = fast_handler.download(_task(), cancel, rec.progress_callback, status_callback)

        assert result is None
        assert rec.last_status == "cancelled"
        # First pass cancels the live transfer, second pass removes the leftover record.
        assert fake.cancelled == [("peer", "live", True), ("peer", "live", True)]
        assert all(t["removed"] for poll in fake.polls for t in poll)

    def test_cancelled_before_start(self, fast_handler, config_values):
        fake = FakeSlskd()
        _mount(fast_handler, fake)
        cancel = Event()
        cancel.set()
        rec = ProgressRecorder()

        assert (
            fast_handler.download(_task(), cancel, rec.progress_callback, rec.status_callback)
            is None
        )
        assert rec.last_status == "cancelled"
        assert fake.enqueued == []

    def test_missing_spec_errors(self, fast_handler, config_values):
        _mount(fast_handler, FakeSlskd())
        rec = ProgressRecorder()
        task = DownloadTask(task_id="slskd:none", source="slskd", title="t")

        assert (
            fast_handler.download(task, Event(), rec.progress_callback, rec.status_callback) is None
        )
        assert rec.last_status == "error"
        assert "missing" in (rec.last_message or "")

    def test_unconfigured_client_errors(self, fast_handler, config_values):
        fast_handler._get_client = lambda: None  # type: ignore[method-assign]
        rec = ProgressRecorder()

        assert (
            fast_handler.download(_task(), Event(), rec.progress_callback, rec.status_callback)
            is None
        )
        assert rec.last_status == "error"
        assert rec.last_message == "slskd is not configured"

    def test_enqueue_failure_errors(self, fast_handler, config_values):
        fake = FakeSlskd()
        fake.enqueue_error = SlskdError("slskd refused 1 file(s): Not shared")
        _mount(fast_handler, fake)
        rec = ProgressRecorder()

        assert (
            fast_handler.download(_task(), Event(), rec.progress_callback, rec.status_callback)
            is None
        )
        assert rec.last_status == "error"
        assert "Not shared" in (rec.last_message or "")

    def test_transfer_vanishing_errors_after_grace(self, fast_handler, config_values, monkeypatch):
        import shelfmark.release_sources.slskd.handler as mod

        monkeypatch.setattr(mod, "MAX_MISSING_POLLS", 3)
        fake = FakeSlskd(polls=[[]])
        _mount(fast_handler, fake)
        rec = ProgressRecorder()

        assert (
            fast_handler.download(_task(), Event(), rec.progress_callback, rec.status_callback)
            is None
        )
        assert rec.last_status == "error"
        assert rec.last_message == "Transfer disappeared from slskd"
        # Three polls to give up, then the cleanup pass looks the transfers up again.
        assert fake.poll_count >= 3

    def test_a_file_vanishing_from_a_multi_file_release_cancels_the_others(
        self, fast_handler, config_values, monkeypatch
    ):
        import shelfmark.release_sources.slskd.handler as mod

        monkeypatch.setattr(mod, "MAX_MISSING_POLLS", 3)
        files = [{"filename": FILE_A, "size": 1000}, {"filename": FILE_B, "size": 1000}]
        # FILE_B is never listed again; FILE_A keeps downloading.
        fake = FakeSlskd(polls=[[_transfer(FILE_A, "InProgress", transferred=10, tid="live")]])
        _mount(fast_handler, fake)
        rec = ProgressRecorder()

        result = fast_handler.download(
            _task(files=files), Event(), rec.progress_callback, rec.status_callback
        )

        assert result is None
        assert rec.last_message == "Transfer disappeared from slskd"
        assert ("peer", "live", True) in fake.cancelled

    def test_queue_timeout_errors(self, fast_handler, config_values, monkeypatch):
        import shelfmark.release_sources.slskd.handler as mod

        monkeypatch.setattr(SlskdHandler, "_queue_timeout_seconds", lambda self: 120)
        clock = iter([0, 0, 200, 200, 200])
        monkeypatch.setattr(mod.time, "monotonic", lambda: next(clock))
        fake = FakeSlskd(polls=[[_transfer(FILE_A, "Queued, Remotely", tid="q")]])
        _mount(fast_handler, fake)
        rec = ProgressRecorder()

        assert (
            fast_handler.download(_task(), Event(), rec.progress_callback, rec.status_callback)
            is None
        )
        assert rec.last_status == "error"
        assert rec.last_message == "Peer did not start the transfer within 2 minutes"
        assert fake.cancelled == [("peer", "q", True), ("peer", "q", True)]
        assert all(t["removed"] for poll in fake.polls for t in poll)

    def test_completed_file_missing_errors(self, fast_handler, config_values, tmp_path):
        config_values["SLSKD_DOWNLOAD_PATH"] = str(tmp_path)
        fake = FakeSlskd(polls=[[_transfer(FILE_A, "Completed, Succeeded", transferred=1000)]])
        _mount(fast_handler, fake)
        rec = ProgressRecorder()

        assert (
            fast_handler.download(_task(), Event(), rec.progress_callback, rec.status_callback)
            is None
        )
        assert rec.last_status == "error"
        assert "Completed file not found" in (rec.last_message or "")
        assert str(tmp_path / "Author" / "Book.epub") in (rec.last_message or "")

    def test_completed_file_appears_after_wait(
        self, fast_handler, config_values, tmp_path, monkeypatch
    ):
        config_values["SLSKD_DOWNLOAD_PATH"] = str(tmp_path)
        monkeypatch.setattr(SlskdHandler, "_completed_path_retry_interval", lambda self: 1)
        monkeypatch.setattr(SlskdHandler, "_completed_path_timeout_seconds", lambda self: 2)
        fake = FakeSlskd(polls=[[_transfer(FILE_A, "Completed, Succeeded", transferred=1000)]])
        _mount(fast_handler, fake)
        rec = ProgressRecorder()
        expected = local_path_for(tmp_path, FILE_A)
        assert expected is not None

        cancel = MagicMock(spec=Event)
        cancel.is_set.return_value = False

        def wait(timeout=None):
            _write_completed(tmp_path, FILE_A)
            return False

        cancel.wait.side_effect = wait

        result = fast_handler.download(_task(), cancel, rec.progress_callback, rec.status_callback)

        assert result == str(expected)
        assert ("locating", "Waiting for completed files...") in rec.status_updates

    def test_unsafe_remote_name_errors(self, fast_handler, config_values, tmp_path):
        config_values["SLSKD_DOWNLOAD_PATH"] = str(tmp_path)
        evil = "a\\..\\Book.epub"
        fake = FakeSlskd(polls=[[_transfer(evil, "Completed, Succeeded", transferred=10)]])
        _mount(fast_handler, fake)
        rec = ProgressRecorder()

        task = _task(files=[{"filename": evil, "size": 10}])
        assert (
            fast_handler.download(task, Event(), rec.progress_callback, rec.status_callback) is None
        )
        assert rec.last_status == "error"
        assert "unsafe" in (rec.last_message or "")

    def test_status_check_errors_are_retried(self, fast_handler, config_values, tmp_path):
        config_values["SLSKD_DOWNLOAD_PATH"] = str(tmp_path)
        fake = FakeSlskd(polls=[[_transfer(FILE_A, "Completed, Succeeded", transferred=1000)]])
        calls = {"n": 0}
        original = fake.list_downloads

        def flaky(username):
            calls["n"] += 1
            if calls["n"] == 1:
                raise SlskdError("hiccup")
            return original(username)

        fake.list_downloads = flaky
        _mount(fast_handler, fake)
        _write_completed(tmp_path, FILE_A)
        rec = ProgressRecorder()

        result = fast_handler.download(_task(), Event(), rec.progress_callback, rec.status_callback)

        assert result is not None
        assert ("resolving", "Waiting for slskd...") in rec.status_updates


# ── post-processing cleanup ────────────────────────────────────────────────────


class TestCleanup:
    def _completed(self, fast_handler, config_values, tmp_path):
        config_values["SLSKD_DOWNLOAD_PATH"] = str(tmp_path)
        fake = FakeSlskd(
            polls=[[_transfer(FILE_A, "Completed, Succeeded", transferred=1000, tid="t")]]
        )
        _mount(fast_handler, fake)
        (path,) = _write_completed(tmp_path, FILE_A)
        rec = ProgressRecorder()
        task = _task()
        assert fast_handler.download(task, Event(), rec.progress_callback, rec.status_callback)
        return fake, task, path

    def test_success_removes_transfer_and_empty_folder(self, fast_handler, config_values, tmp_path):
        fake, task, path = self._completed(fast_handler, config_values, tmp_path)
        path.unlink()  # orchestrator moved it into the library

        fast_handler.post_process_cleanup(task, success=True)

        assert fake.cancelled == [("peer", "t", True)]
        assert not path.parent.exists()
        assert task.task_id not in fast_handler._cleanup_refs

    def test_success_keeps_folder_with_other_files(self, fast_handler, config_values, tmp_path):
        _fake, task, path = self._completed(fast_handler, config_values, tmp_path)
        path.unlink()
        (path.parent / "other.epub").write_bytes(b"x")

        fast_handler.post_process_cleanup(task, success=True)

        assert path.parent.exists()

    def test_success_respects_remove_setting(self, fast_handler, config_values, tmp_path):
        fake, task, _path = self._completed(fast_handler, config_values, tmp_path)
        config_values["SLSKD_REMOVE_COMPLETED"] = False

        fast_handler.post_process_cleanup(task, success=True)

        assert fake.cancelled == []

    def test_failure_leaves_everything(self, fast_handler, config_values, tmp_path):
        fake, task, path = self._completed(fast_handler, config_values, tmp_path)

        fast_handler.post_process_cleanup(task, success=False)

        assert fake.cancelled == []
        assert path.exists()
        assert task.task_id not in fast_handler._cleanup_refs

    def test_cleanup_without_refs_is_noop(self, fast_handler):
        fast_handler.post_process_cleanup(_task(), success=True)

    def test_cancel_drops_refs(self, fast_handler, config_values, tmp_path):
        _fake, task, _path = self._completed(fast_handler, config_values, tmp_path)
        assert fast_handler.cancel(task.task_id) is True
        assert task.task_id not in fast_handler._cleanup_refs


class TestCancelWhileLocatingFiles:
    def test_a_cancel_after_the_transfers_finished_still_removes_their_records(
        self, fast_handler, config_values, tmp_path
    ):
        config_values["SLSKD_DOWNLOAD_PATH"] = str(tmp_path)
        cancel = Event()
        finished = [_transfer(FILE_A, "Completed, Succeeded", transferred=1000, tid="t0")]

        class Fake(FakeSlskd):
            def list_downloads(self, username):
                result = super().list_downloads(username)
                cancel.set()  # the cancel lands as the last transfer is reported finished
                return result

        fake = Fake(polls=[finished])
        _mount(fast_handler, fake)
        rec = ProgressRecorder()

        result = fast_handler.download(_task(), cancel, rec.progress_callback, rec.status_callback)

        assert result is None
        assert rec.last_status == "cancelled"
        assert ("peer", "t0", True) in fake.cancelled
