"""A torrent the client has queued is waiting for a slot, not stalled."""

from __future__ import annotations

import sys
from threading import Event
from unittest.mock import MagicMock, patch

from shelfmark.core.models import DownloadTask
from shelfmark.download.activity import ACTIVITY_GRACE_STATUS
from shelfmark.download.clients import DownloadState, DownloadStatus
from shelfmark.release_sources.prowlarr.handler import ProwlarrHandler

bh = sys.modules["shelfmark.download.clients.base_handler"]  # loaded by the import above


def _status(state: DownloadState, progress: float = 0.0, **kw) -> DownloadStatus:
    return DownloadStatus(
        progress=progress,
        state=state,
        message="Queued" if state == DownloadState.QUEUED else None,
        complete=kw.get("complete", False),
        file_path=None,
    )


class _Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, str | None]] = []

    def status(self, status: str, message: str | None) -> None:
        self.events.append((status, message))

    @property
    def graces(self) -> list[float]:
        return [float(m) for s, m in self.events if s == ACTIVITY_GRACE_STATUS]


def _poll(statuses: list[DownloadStatus], clock: list[float] | None = None) -> _Recorder:
    """Run the poll loop over a scripted list of client statuses, then cancel."""
    cancel = Event()
    seen = iter(statuses)
    client = MagicMock()
    client.name = "qbittorrent"

    def get_status(_id: str) -> DownloadStatus:
        try:
            return next(seen)
        except StopIteration:
            cancel.set()
            return statuses[-1]

    client.get_status.side_effect = get_status
    rec = _Recorder()
    ticks = iter(clock) if clock is not None else None
    patches = [patch.object(ProwlarrHandler, "_poll_interval", return_value=0.0)]
    if ticks is not None:
        patches.append(patch.object(bh.time, "monotonic", side_effect=lambda: next(ticks)))
    for p in patches:
        p.start()
    try:
        ProwlarrHandler()._poll_and_complete(
            client,
            "hash",
            "torrent",
            DownloadTask(task_id="t", source="prowlarr", title="Book"),
            cancel,
            lambda _p: None,
            rec.status,
        )
    finally:
        patch.stopall()
    return rec


Q, D = DownloadState.QUEUED, DownloadState.DOWNLOADING


def test_a_queued_torrent_asks_for_a_grace_once() -> None:
    rec = _poll([_status(Q)] * 4)

    assert rec.graces[0] == bh.QUEUE_GRACE_SECONDS
    assert rec.graces.count(bh.QUEUE_GRACE_SECONDS) == 1


def test_the_grace_is_released_when_the_torrent_starts() -> None:
    rec = _poll([_status(Q), _status(Q), _status(D, 5.0), _status(D, 6.0)])

    # queue grace, its release, then one movement grace per reading that went up
    assert rec.graces == [
        bh.QUEUE_GRACE_SECONDS,
        0.0,
        bh.MOVING_STALL_SECONDS,
        bh.MOVING_STALL_SECONDS,
    ]


def test_a_torrent_that_never_queued_only_gets_the_moving_window() -> None:
    rec = _poll([_status(D, 1.0), _status(D, 2.0)])

    assert rec.graces == [bh.MOVING_STALL_SECONDS] * 2  # no release, no queue renewal


# --- a torrent that is moving gets a longer window each time it moves ---------------------


def test_every_step_forward_pushes_the_stall_deadline_out() -> None:
    rec = _poll([_status(D, 1.0), _status(D, 2.0), _status(D, 3.5)])

    assert rec.graces == [bh.MOVING_STALL_SECONDS] * 3


def test_a_torrent_that_stops_moving_stops_getting_more_time() -> None:
    rec = _poll([_status(D, 5.0), _status(D, 5.0), _status(D, 5.0), _status(D, 5.0)])

    assert rec.graces == [bh.MOVING_STALL_SECONDS]  # only the first reading moved


def test_a_torrent_gets_a_full_window_to_start_even_before_any_data_arrives() -> None:
    # Metadata and the first peer can take longer than the orchestrator's 5 minutes.
    assert _poll([_status(D, 0.0)] * 4).graces == [bh.MOVING_STALL_SECONDS]


def test_the_start_window_is_given_once_not_renewed_while_stuck_at_zero() -> None:
    graces = _poll([_status(D, 0.0)] * 20).graces

    assert graces.count(bh.MOVING_STALL_SECONDS) == 1


def test_a_torrent_leaving_the_queue_at_zero_gets_a_fresh_start_window() -> None:
    rec = _poll([_status(Q), _status(Q), _status(D, 0.0), _status(D, 0.0)])

    # queue grace, its release, then the start window
    assert rec.graces == [bh.QUEUE_GRACE_SECONDS, 0.0, bh.MOVING_STALL_SECONDS]


def test_a_queued_torrent_is_not_given_the_start_window_yet() -> None:
    assert _poll([_status(Q)] * 4).graces == [bh.QUEUE_GRACE_SECONDS]  # the queue grace only


def test_going_backwards_is_not_movement() -> None:
    rec = _poll([_status(D, 50.0), _status(D, 40.0), _status(D, 40.0)])

    assert rec.graces == [bh.MOVING_STALL_SECONDS]


def test_the_windows_fit_inside_what_the_orchestrator_allows() -> None:
    from shelfmark.download import orchestrator

    cap = orchestrator._MAX_ACTIVITY_GRACE_SECONDS
    assert bh.QUEUE_GRACE_SECONDS <= cap
    assert bh.MOVING_STALL_SECONDS <= cap
    assert bh.MOVING_STALL_SECONDS > orchestrator.STALL_TIMEOUT


def test_a_long_queue_renews_the_grace_but_only_until_the_ceiling() -> None:
    # Each poll reads the clock once; times are seconds since the torrent was first seen queued.
    times = [0, 100, 700, 800, 1400, 7000, 7300, 7400, 7500]
    rec = _poll([_status(Q)] * len(times), clock=[1000 + t for t in times])

    # first request at t=0, renewed after >= 600s (t=700, t=1400, t=7000), none past the 7200s ceiling
    assert rec.graces.count(bh.QUEUE_GRACE_SECONDS) == 4


def test_a_queue_wait_past_the_ceiling_gets_no_more_grace() -> None:
    times = [0, 7300, 7400]
    rec = _poll([_status(Q)] * 3, clock=[5000 + t for t in times])

    assert rec.graces.count(bh.QUEUE_GRACE_SECONDS) == 1
