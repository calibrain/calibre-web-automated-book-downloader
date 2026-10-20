"""The handler's isolated mode: one folder per download, polled by id, confirmed on disk.

Several apps can share one slskd, and any of them can clear its list of finished transfers.
A download that watched the list would then "lose" a file that had actually arrived. These
tests pin the behaviour that does not: each download gets its own folder, each transfer is
read by its id, the files on disk decide whether it finished, and the finished files stay in
slskd's folder (still shared) while the orchestrator copies them.
"""

from __future__ import annotations

import json
from threading import Event

import pytest

import shelfmark.release_sources.slskd.handler as mod
from shelfmark.core.models import DownloadTask
from shelfmark.download.activity import ACTIVITY_GRACE_STATUS
from shelfmark.release_sources.slskd.api import SlskdBatchUnsupportedError
from shelfmark.release_sources.slskd.handler import SlskdHandler

REMOTE_DIR = "@@share\\Books\\Author"
FILE_A = f"{REMOTE_DIR}\\Book One.epub"
FILE_B = f"{REMOTE_DIR}\\Book Two.epub"
FILES = [{"filename": FILE_A, "size": 5}, {"filename": FILE_B, "size": 7}]
OK = "Completed, Succeeded"


class Recorder:
    def __init__(self) -> None:
        self.progress: list[float] = []
        self.status: list[tuple[str, str | None]] = []

    def progress_callback(self, value: float) -> None:
        self.progress.append(value)

    def status_callback(self, status: str, message: str | None) -> None:
        self.status.append((status, message))

    @property
    def real(self) -> list[tuple[str, str | None]]:
        """Statuses a user would see, without the stall-grace signals."""
        return [(s, m) for s, m in self.status if s != ACTIVITY_GRACE_STATUS]

    @property
    def statuses(self) -> list[str]:
        return [s for s, _ in self.real]

    @property
    def graces(self) -> list[float]:
        return [float(m) for s, m in self.status if s == ACTIVITY_GRACE_STATUS]


class FakeSlskd:
    """slskd as the handler sees it: batches, transfers by id, a list, events."""

    def __init__(self, root, *, script=None, write_on_success=True):
        self.root = root
        # script[filename] = list of states, one per get_transfer call; the last repeats.
        # A state of None means slskd no longer has the transfer (cleared from its list).
        self.script = script or {}
        self.write_on_success = write_on_success
        self.batches: list[dict] = []
        self.cancelled: list[tuple[str, str, bool]] = []
        self.list_calls = 0
        self.events: list[dict] = []
        self.refuse: list[dict] = []
        self.batch_error: Exception | None = None
        self._calls: dict[str, int] = {}
        self._written: set[str] = set()
        self._ids: dict[str, str] = {}
        self.on_get = None

    def enqueue_batch(self, username, files, destination):
        if self.batch_error:
            raise self.batch_error
        self.batches.append({"username": username, "files": files, "destination": destination})
        self._destination = destination
        transfers = []
        for i, f in enumerate(files):
            if any(r["filename"] == f["filename"] for r in self.refuse):
                continue
            tid = f"t{i}"
            self._ids[tid] = f["filename"]
            transfers.append({"id": tid, "filename": f["filename"], "state": "Requested"})
        return {"batch_id": "b-1", "transfers": transfers, "failures": list(self.refuse)}

    def get_transfer(self, username, transfer_id):
        filename = self._ids[transfer_id]
        count = self._calls.get(filename, 0)
        self._calls[filename] = count + 1
        states = self.script.get(filename, [OK])
        state = states[min(count, len(states) - 1)]
        if self.on_get:
            self.on_get(self, filename, state)
        if state is None:
            return None
        size = next(f["size"] for f in self.batches[-1]["files"] if f["filename"] == filename)
        if state == OK and self.write_on_success and filename not in self._written:
            folder = self.root / self._destination
            folder.mkdir(parents=True, exist_ok=True)
            (folder / filename.rsplit("\\", 1)[-1]).write_bytes(b"x" * size)
            self._written.add(filename)
        done = size if state == OK else 0
        return {
            "id": transfer_id,
            "filename": filename,
            "state": state,
            "size": size,
            "bytesTransferred": done,
            "averageSpeed": 1000.0,
            "placeInQueue": 3 if "Remotely" in state else None,
        }

    def list_downloads(self, username):
        self.list_calls += 1
        return [
            {"id": tid, "filename": fn, "state": "InProgress", "removed": False}
            for tid, fn in self._ids.items()
        ]

    def cancel_transfer(self, username, transfer_id, *, remove):
        self.cancelled.append((username, transfer_id, remove))
        return True

    def get_events(self, *, limit=200, offset=0):
        return list(self.events)

    def get_download_directory(self):
        return str(self.root)


@pytest.fixture
def cfg(monkeypatch, tmp_path):
    values: dict = {"SLSKD_DOWNLOAD_PATH": str(tmp_path)}
    monkeypatch.setattr(mod.config, "get", lambda k, d=None: values.get(k, d))
    return values


@pytest.fixture
def handler(monkeypatch):
    monkeypatch.setattr(SlskdHandler, "_poll_interval", lambda self: 0)
    monkeypatch.setattr(SlskdHandler, "_completed_path_retry_interval", lambda self: 0)
    monkeypatch.setattr(SlskdHandler, "_completed_path_timeout_seconds", lambda self: 0)
    monkeypatch.setattr(SlskdHandler, "_queue_timeout_seconds", lambda self: 0)
    monkeypatch.setattr(SlskdHandler, "_cancel_remove_wait", lambda self: None)
    monkeypatch.setattr(mod, "MAX_MISSING_POLLS", 3)
    return SlskdHandler()


def _task(files=None, task_id="slskd:abc"):
    files = files if files is not None else FILES
    return DownloadTask(
        task_id=task_id,
        source="slskd",
        title="Book",
        retry_source_context={"username": "peer", "directory": REMOTE_DIR, "files": files},
    )


def _run(handler, fake, task=None, cancel=None):
    handler._get_client = lambda: fake  # type: ignore[method-assign]
    rec = Recorder()
    task = task or _task()
    result = handler.download(task, cancel or Event(), rec.progress_callback, rec.status_callback)
    return result, task, rec


class TestHappyPath:
    def test_the_task_gets_its_own_folder_and_it_is_what_the_orchestrator_receives(
        self, handler, cfg, tmp_path
    ):
        fake = FakeSlskd(
            tmp_path, script={FILE_A: ["Queued, Remotely", "InProgress", OK], FILE_B: [OK]}
        )

        result, _task_obj, rec = _run(handler, fake)

        folder = tmp_path / "shelfmark" / "slskd_abc"
        assert result == str(folder)
        assert sorted(p.name for p in folder.iterdir()) == ["Book One.epub", "Book Two.epub"]
        assert fake.batches[0]["destination"] == "shelfmark/slskd_abc"
        assert rec.progress[-1] == 100.0
        assert "downloading" in rec.statuses

    def test_the_orchestrator_is_told_the_folder_is_the_clients_so_it_copies_not_moves(
        self, handler, cfg, tmp_path
    ):
        fake = FakeSlskd(tmp_path)

        result, task, _ = _run(handler, fake)

        assert task.original_download_path == result

    def test_transfers_are_read_by_id_never_by_the_list(self, handler, cfg, tmp_path):
        fake = FakeSlskd(tmp_path, script={FILE_A: ["InProgress", OK], FILE_B: ["InProgress", OK]})

        _run(handler, fake)

        assert fake.list_calls == 0

    def test_a_failure_to_name_a_safe_folder_never_escapes_the_downloads_root(
        self, handler, cfg, tmp_path
    ):
        fake = FakeSlskd(tmp_path)

        _run(handler, fake, task=_task(task_id="../../etc/passwd"))

        destination = fake.batches[0]["destination"]
        assert ".." not in destination and not destination.startswith("/")
        assert destination.startswith("shelfmark/")

    def test_the_destination_prefix_is_a_setting_and_a_bad_one_is_ignored(
        self, handler, cfg, tmp_path
    ):
        cfg["SLSKD_DESTINATION_PREFIX"] = "books/incoming"
        fake = FakeSlskd(tmp_path)
        _run(handler, fake)
        assert fake.batches[0]["destination"] == "books/incoming/slskd_abc"

        cfg["SLSKD_DESTINATION_PREFIX"] = "../../elsewhere"
        fake2 = FakeSlskd(tmp_path)
        _run(handler, fake2, task=_task(task_id="slskd:def"))
        assert fake2.batches[0]["destination"] == "shelfmark/slskd_def"


class TestAnotherAppClearsSlskdsList:
    def test_a_transfer_slskd_no_longer_lists_is_confirmed_by_the_files_on_disk(
        self, handler, cfg, tmp_path
    ):
        # Both files arrive, then SoulSync (say) clears the finished transfers.
        fake = FakeSlskd(tmp_path, script={FILE_A: [OK, None], FILE_B: [OK, None]})

        result, _, rec = _run(handler, fake)

        assert result == str(tmp_path / "shelfmark" / "slskd_abc")
        assert "error" not in rec.statuses

    def test_a_file_that_arrived_before_the_clear_is_not_lost(self, handler, cfg, tmp_path):
        folder = tmp_path / "shelfmark" / "slskd_abc"

        def write_a_then_clear(fake, filename, state):
            if filename == FILE_A:
                folder.mkdir(parents=True, exist_ok=True)
                (folder / "Book One.epub").write_bytes(b"x" * 5)

        fake = FakeSlskd(tmp_path, script={FILE_A: [None], FILE_B: ["InProgress", OK]})
        fake.on_get = write_a_then_clear

        result, _, rec = _run(handler, fake)

        assert result == str(folder)
        assert "error" not in rec.statuses

    def test_a_transfer_that_vanishes_with_no_file_is_an_error_not_a_hang(
        self, handler, cfg, tmp_path
    ):
        fake = FakeSlskd(tmp_path, script={FILE_A: [None], FILE_B: [None]}, write_on_success=False)

        result, task, rec = _run(handler, fake)

        assert result is None
        assert rec.real[-1][0] == "error"
        assert "slskd" in (rec.real[-1][1] or "").lower()
        assert task.original_download_path is None

    def test_slskds_events_confirm_a_vanished_transfer_whose_file_is_still_arriving(
        self, handler, cfg, tmp_path
    ):
        folder = tmp_path / "shelfmark" / "slskd_abc"
        fake = FakeSlskd(tmp_path, script={FILE_A: [None], FILE_B: [None]}, write_on_success=False)
        fake.events = [
            {"type": "DownloadFileComplete", "data": json.dumps({"remoteFilename": FILE_A})},
            {"type": "DownloadFileComplete", "data": json.dumps({"remoteFilename": FILE_B})},
        ]
        # The files land a moment after slskd reported the events.
        calls = {"n": 0}
        real_get_events = fake.get_events

        def events_then_files(**kw):
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "Book One.epub").write_bytes(b"x" * 5)
            (folder / "Book Two.epub").write_bytes(b"x" * 7)
            calls["n"] += 1
            return real_get_events(**kw)

        fake.get_events = events_then_files  # type: ignore[method-assign]

        result, _, rec = _run(handler, fake)

        assert calls["n"] >= 1
        assert result == str(folder)
        assert "error" not in rec.statuses


class TestFailuresAndCancel:
    def test_a_failed_transfer_fails_the_task_and_stops_the_rest(self, handler, cfg, tmp_path):
        fake = FakeSlskd(tmp_path, script={FILE_A: ["Completed, Errored"], FILE_B: ["InProgress"]})

        result, task, rec = _run(handler, fake)

        assert result is None
        assert rec.real[-1][0] == "error"
        assert "Errored" in (rec.real[-1][1] or "")
        assert {tid for _, tid, _ in fake.cancelled} == {"t0", "t1"}
        assert task.original_download_path is None

    def test_cancelling_cancels_and_removes_every_transfer(self, handler, cfg, tmp_path):
        cancel = Event()
        fake = FakeSlskd(tmp_path, script={FILE_A: ["InProgress"], FILE_B: ["InProgress"]})
        fake.on_get = lambda f, name, state: cancel.set()

        result, _, rec = _run(handler, fake, cancel=cancel)

        assert result is None
        assert rec.real[-1][0] == "cancelled"
        assert {(tid, remove) for _, tid, remove in fake.cancelled} == {("t0", True), ("t1", True)}

    def test_a_peer_that_never_starts_gives_up_after_the_queue_timeout(
        self, handler, cfg, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(SlskdHandler, "_queue_timeout_seconds", lambda self: 600.0)
        clock = iter([0.0, 1.0, 2.0, 700.0, 701.0, 702.0, 703.0, 704.0, 705.0])
        monkeypatch.setattr(SlskdHandler, "_clock", lambda self: next(clock), raising=False)
        fake = FakeSlskd(
            tmp_path,
            script={FILE_A: ["Queued, Remotely"], FILE_B: ["Queued, Remotely"]},
            write_on_success=False,
        )

        result, _, rec = _run(handler, fake)

        assert result is None
        assert "minutes" in (rec.real[-1][1] or "")
        assert fake.cancelled


class TestPeerQueue:
    def test_a_queued_transfer_asks_the_orchestrator_not_to_stall_it(self, handler, cfg, tmp_path):
        fake = FakeSlskd(
            tmp_path,
            script={
                FILE_A: ["Queued, Remotely", "Queued, Remotely", OK],
                FILE_B: ["Queued, Remotely", "Queued, Remotely", OK],
            },
        )

        _, _, rec = _run(handler, fake)

        assert rec.graces, "no activity grace was requested while the peer queued the files"
        assert rec.graces[0] == mod.QUEUE_GRACE_SECONDS
        # and it is released once the transfers start, so the normal stall timer applies
        assert rec.graces[-1] == 0.0


class TestRejoinAfterARestart:
    def test_files_already_on_disk_are_not_downloaded_again(self, handler, cfg, tmp_path):
        folder = tmp_path / "shelfmark" / "slskd_abc"
        folder.mkdir(parents=True)
        (folder / "Book One.epub").write_bytes(b"x" * 5)
        (folder / "Book Two.epub").write_bytes(b"x" * 7)
        fake = FakeSlskd(tmp_path)

        result, task, rec = _run(handler, fake)

        assert result == str(folder)
        assert fake.batches == []
        assert task.original_download_path == str(folder)
        assert rec.progress[-1] == 100.0

    def test_files_slskd_says_are_already_queued_are_found_by_name_and_rejoined(
        self, handler, cfg, tmp_path
    ):
        fake = FakeSlskd(tmp_path, script={FILE_A: [OK], FILE_B: [OK]})
        fake.refuse = [{"filename": FILE_A, "message": "already queued"}]
        fake._ids["t0"] = FILE_A  # slskd still lists the earlier transfer for it

        result, _, rec = _run(handler, fake)

        assert result is not None
        assert "error" not in rec.statuses

    def test_a_refusal_with_nothing_to_rejoin_is_reported(self, handler, cfg, tmp_path):
        fake = FakeSlskd(tmp_path)
        fake.refuse = [
            {"filename": FILE_A, "message": "denied"},
            {"filename": FILE_B, "message": "denied"},
        ]

        result, _, rec = _run(handler, fake)

        assert result is None
        assert rec.real[-1][0] == "error"
        assert "denied" in (rec.real[-1][1] or "")


class TestFallbackAndSettings:
    def test_an_old_slskd_without_batches_uses_the_original_enqueue(self, handler, cfg, tmp_path):
        class Old(FakeSlskd):
            def __init__(self, root):
                super().__init__(root)
                self.batch_error = SlskdBatchUnsupportedError("no batches")
                self.enqueued: list = []

            def enqueue_downloads(self, username, files):
                self.enqueued.append(files)
                return [
                    {"id": f"o{i}", "filename": f["filename"], "state": "Queued, Locally"}
                    for i, f in enumerate(files)
                ]

        fake = Old(tmp_path)
        # the original path polls the list, so give it finished transfers and the files
        fake.list_downloads = lambda username: [  # type: ignore[method-assign]
            {
                "id": f"o{i}",
                "filename": f["filename"],
                "state": OK,
                "bytesTransferred": f["size"],
                "size": f["size"],
                "removed": False,
            }
            for i, f in enumerate(FILES)
        ]
        for name in ("Book One.epub", "Book Two.epub"):
            target = tmp_path / "Author" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"x" * 5)

        result, _, rec = _run(handler, fake)

        assert fake.enqueued, "the original enqueue was not used"
        assert result is not None
        assert "error" not in rec.statuses

    def test_isolation_can_be_switched_off(self, handler, cfg, tmp_path):
        cfg["SLSKD_ISOLATE_DOWNLOADS"] = False

        class NoBatch(FakeSlskd):
            def enqueue_batch(self, *a, **kw):
                raise AssertionError("the batch endpoint was used with isolation off")

            def enqueue_downloads(self, username, files):
                return []

        fake = NoBatch(tmp_path)
        fake.list_downloads = lambda username: []  # type: ignore[method-assign]

        result, _, rec = _run(handler, fake)

        assert result is None  # the original path found nothing to wait for
        assert rec.real[-1][0] == "error"


class TestCleanup:
    def _done(self, handler, cfg, tmp_path):
        fake = FakeSlskd(tmp_path)
        result, task, _ = _run(handler, fake)
        return fake, result, task

    def test_by_default_finished_files_and_records_are_left_in_slskd_so_they_stay_shared(
        self, handler, cfg, tmp_path
    ):
        fake, _result, task = self._done(handler, cfg, tmp_path)

        handler.post_process_cleanup(task, success=True)

        assert fake.cancelled == []
        assert (tmp_path / "shelfmark" / "slskd_abc").is_dir()

    def test_with_keep_off_the_transfers_and_the_folder_are_removed_after_import(
        self, handler, cfg, tmp_path
    ):
        cfg["SLSKD_KEEP_COMPLETED"] = False
        fake, _result, task = self._done(handler, cfg, tmp_path)

        handler.post_process_cleanup(task, success=True)

        assert {(tid, remove) for _, tid, remove in fake.cancelled} == {("t0", True), ("t1", True)}
        assert not (tmp_path / "shelfmark" / "slskd_abc").exists()

    def test_a_failed_import_leaves_everything_for_a_retry(self, handler, cfg, tmp_path):
        cfg["SLSKD_KEEP_COMPLETED"] = False
        fake, _result, task = self._done(handler, cfg, tmp_path)

        handler.post_process_cleanup(task, success=False)

        assert fake.cancelled == []
        assert (tmp_path / "shelfmark" / "slskd_abc").is_dir()


class TestFolderComplete:
    def _folder(self, tmp_path, files):
        folder = tmp_path / "f"
        folder.mkdir()
        for name, size in files.items():
            (folder / name).write_bytes(b"x" * size)
        return folder

    def test_the_files_asked_for_are_complete(self, tmp_path):
        folder = self._folder(tmp_path, {"a.epub": 5, "b.epub": 7})

        assert mod._folder_complete(folder, [5, 7]) is True

    def test_a_file_of_the_wrong_size_is_not_complete(self, tmp_path):
        # the right number of files, but one is a partial copy
        folder = self._folder(tmp_path, {"a.epub": 5, "b.epub": 3})

        assert mod._folder_complete(folder, [5, 7]) is False

    def test_names_do_not_matter_because_slskd_may_rename_a_duplicate(self, tmp_path):
        folder = self._folder(tmp_path, {"a_1.epub": 5, "totally different.epub": 7})

        assert mod._folder_complete(folder, [7, 5]) is True

    def test_too_few_or_too_many_files_is_not_complete(self, tmp_path):
        assert mod._folder_complete(self._folder(tmp_path, {"a.epub": 5}), [5, 7]) is False
        other = tmp_path / "g"
        other.mkdir()
        for name, size in {"a": 5, "b": 7, "c": 1}.items():
            (other / name).write_bytes(b"x" * size)
        assert mod._folder_complete(other, [5, 7]) is False

    def test_hidden_and_missing_folders_hold_nothing(self, tmp_path):
        folder = self._folder(tmp_path, {".part": 5})

        assert mod._folder_complete(folder, [5]) is False
        assert mod._folder_complete(tmp_path / "nope", [5]) is False

    def test_an_unknown_size_is_checked_by_count_only(self, tmp_path):
        folder = self._folder(tmp_path, {"a.epub": 5, "b.epub": 9})

        assert mod._folder_complete(folder, [0, 0]) is True
        assert mod._folder_complete(folder, [0]) is False

    def test_nothing_asked_for_is_never_complete(self, tmp_path):
        assert mod._folder_complete(self._folder(tmp_path, {"a": 1}), []) is False
