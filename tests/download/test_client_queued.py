"""A torrent queued inside its client is not working yet, and the task says so."""

from __future__ import annotations

from threading import Event
from unittest.mock import MagicMock, patch

from shelfmark.core.models import DownloadTask
from shelfmark.download.clients import DownloadState, DownloadStatus
from shelfmark.release_sources.prowlarr.handler import ProwlarrHandler


def _status(state: DownloadState) -> DownloadStatus:
    return DownloadStatus(progress=0.0, state=state, message=None, complete=False, file_path=None)


def _poll(states: list[DownloadState]) -> tuple[DownloadTask, list[bool]]:
    cancel = Event()
    seen = iter(states)
    flags: list[bool] = []
    task = DownloadTask(task_id="t", source="prowlarr", title="Book")
    client = MagicMock()
    client.name = "qbittorrent"

    def get_status(_id: str) -> DownloadStatus:
        flags.append(task.client_queued)  # what the previous poll left behind
        try:
            return _status(next(seen))
        except StopIteration:
            cancel.set()
            return _status(states[-1])

    client.get_status.side_effect = get_status
    with patch.object(ProwlarrHandler, "_poll_interval", return_value=0.0):
        ProwlarrHandler()._poll_and_complete(
            client, "hash", "torrent", task, cancel, lambda _p: None, lambda *_a: None
        )
    return task, flags


def test_the_task_is_marked_while_its_torrent_waits_in_the_clients_queue():
    queued, downloading = DownloadState.QUEUED, DownloadState.DOWNLOADING
    _task, flags = _poll([queued, queued, downloading, downloading])

    assert flags[:5] == [False, True, True, False, False]


def test_a_task_that_never_waited_is_never_marked():
    _task, flags = _poll([DownloadState.DOWNLOADING] * 3)

    assert not any(flags)
