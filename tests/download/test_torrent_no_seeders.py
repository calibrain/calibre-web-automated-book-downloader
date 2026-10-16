"""A torrent that is not moving and has too few seeds is given up on, and the card says why."""

from __future__ import annotations

import sys
from threading import Event
from unittest.mock import MagicMock, patch

from shelfmark.core.models import DownloadTask
from shelfmark.download.clients import DownloadState, DownloadStatus
from shelfmark.download.clients.qbittorrent import _non_negative_int
from shelfmark.release_sources.prowlarr.handler import ProwlarrHandler

bh = sys.modules["shelfmark.download.clients.base_handler"]  # loaded by the import above

D = DownloadState.DOWNLOADING
WAIT_MINUTES = 5


def _status(
    progress: float = 0.0,
    *,
    seeders: int | None = 0,
    swarm_seeders: int | None = None,
    state: DownloadState = D,
) -> DownloadStatus:
    return DownloadStatus(
        progress=progress,
        state=state,
        message=None,
        complete=False,
        file_path=None,
        seeders=seeders,
        swarm_seeders=swarm_seeders,
    )


def _poll(
    statuses: list[DownloadStatus],
    clock: list[float],
    *,
    wait_minutes: float = WAIT_MINUTES,
    min_seeders: int = 1,
    added_by_shelfmark: bool = True,
) -> tuple[list[tuple[str, str | None]], MagicMock, str | None]:
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
    client.remove.return_value = True
    events: list[tuple[str, str | None]] = []
    ticks = iter(clock)
    with (
        patch.object(ProwlarrHandler, "_poll_interval", return_value=0.0),
        patch.object(ProwlarrHandler, "_no_seeders_wait_seconds", return_value=wait_minutes * 60.0),
        patch.object(ProwlarrHandler, "_min_seeders", return_value=min_seeders),
        patch.object(bh.time, "monotonic", side_effect=lambda: next(ticks)),
    ):
        result = ProwlarrHandler()._poll_and_complete(
            client,
            "hash",
            "torrent",
            DownloadTask(task_id="t", source="prowlarr", title="Book"),
            cancel,
            lambda _p: None,
            lambda status, message: events.append((status, message)),
            added_by_shelfmark=added_by_shelfmark,
        )
    return events, client, result


def _messages(events: list[tuple[str, str | None]]) -> list[str]:
    return [m or "" for s, m in events if s == "downloading"]


def test_gives_up_when_nothing_moves_and_no_seeds_are_seen() -> None:
    events, client, result = _poll([_status()] * 4, [0.0, 100.0, 200.0, 301.0])

    assert result is None
    assert events[-1] == ("error", "No seeders: 0 found, 1 needed, for 5 min (0% downloaded)")
    client.remove.assert_called_once_with("hash", delete_files=True)


def test_the_card_counts_down_while_it_waits() -> None:
    events, _client, _result = _poll([_status()] * 4, [0.0, 100.0, 200.0, 301.0])

    assert _messages(events)[:3] == [
        "0% · 0 seeds, giving up in 5m",
        "0% · 0 seeds, giving up in 4m",
        "0% · 0 seeds, giving up in 2m",
    ]


def test_a_torrent_with_no_seed_at_all_is_given_up_on_even_while_it_moves() -> None:
    statuses = [_status(1.0), _status(2.0), _status(3.0), _status(4.0)]
    events, client, _result = _poll(statuses, [0.0, 400.0, 800.0, 1200.0])

    assert events[-1] == ("error", "No seeders: 0 found, 1 needed, for 5 min (2% downloaded)")
    client.remove.assert_called_once_with("hash", delete_files=True)


def test_a_torrent_that_is_moving_is_kept_while_it_still_has_a_seed() -> None:
    statuses = [_status(1.0, seeders=1), _status(2.0, seeders=1), _status(3.0, seeders=1)]
    events, client, _result = _poll(statuses, [0.0, 400.0, 800.0], min_seeders=3)

    assert not any(s == "error" for s, _m in events)
    client.remove.assert_not_called()


def test_the_clock_restarts_when_progress_appears() -> None:
    statuses = [_status(0.0, seeders=1), _status(0.0, seeders=1), _status(1.0, seeders=1)]
    events, _client, _result = _poll(statuses, [0.0, 200.0, 500.0], min_seeders=3)

    assert not any(s == "error" for s, _m in events)


def test_enough_seeds_keeps_a_motionless_torrent_waiting() -> None:
    events, _client, _result = _poll([_status(seeders=2)] * 3, [0.0, 600.0, 1200.0])

    assert not any(s == "error" for s, _m in events)


def test_the_tracker_count_also_counts_as_a_seed() -> None:
    events, _client, _result = _poll(
        [_status(seeders=0, swarm_seeders=4)] * 3, [0.0, 600.0, 1200.0]
    )

    assert not any(s == "error" for s, _m in events)


def test_a_higher_minimum_is_honoured() -> None:
    events, _client, _result = _poll([_status(seeders=1)] * 3, [0.0, 200.0, 400.0], min_seeders=3)

    assert events[-1][0] == "error"
    assert events[-1][1] == "No seeders: 1 found, 3 needed, for 5 min (0% downloaded)"


def test_off_by_default_never_gives_up() -> None:
    events, _client, _result = _poll([_status()] * 3, [0.0, 600.0, 1200.0], wait_minutes=0)

    assert not any(s == "error" for s, _m in events)


def test_a_client_that_reports_no_counts_is_left_to_the_stall_timer() -> None:
    events, _client, _result = _poll([_status(seeders=None)] * 3, [0.0, 600.0, 1200.0])

    assert not any(s == "error" for s, _m in events)


def test_a_torrent_that_was_already_in_the_client_is_not_removed() -> None:
    events, client, _result = _poll(
        [_status()] * 4, [0.0, 100.0, 200.0, 301.0], added_by_shelfmark=False
    )

    assert events[-1][0] == "error"
    client.remove.assert_not_called()


def test_a_queued_torrent_is_not_judged() -> None:
    events, _client, _result = _poll(
        [_status(state=DownloadState.QUEUED)] * 3, [0.0, 600.0, 1200.0, 1800.0, 2400.0]
    )

    assert not any(s == "error" for s, _m in events)


def test_seed_counts_show_beside_progress_when_nothing_is_wrong() -> None:
    events, _client, _result = _poll(
        [_status(10.0, seeders=3), _status(11.0, seeders=1)], [0.0, 1.0]
    )

    assert _messages(events)[:2] == ["10% · 3 seeds", "11% · 1 seed"]


def test_a_clients_unknown_count_is_not_a_zero() -> None:
    assert _non_negative_int(-1) is None
    assert _non_negative_int(None) is None
    assert _non_negative_int(True) is None
    assert _non_negative_int(0) == 0
    assert _non_negative_int(7) == 7


def test_a_torrent_that_started_with_enough_seeds_keeps_going_as_they_leave() -> None:
    # Met the minimum of 3 when it started, then dropped to 1 while still moving.
    statuses = [
        _status(10.0, seeders=3),
        _status(30.0, seeders=2),
        _status(50.0, seeders=1),
        _status(60.0, seeders=1),
        _status(70.0, seeders=1),
    ]
    events, client, _result = _poll(
        statuses, [0.0, 600.0, 1200.0, 1800.0, 2400.0], min_seeders=3, added_by_shelfmark=False
    )

    assert not any(s == "error" for s, _m in events)
    client.remove.assert_not_called()


def test_the_last_seed_leaving_starts_the_clock_even_if_it_is_still_moving() -> None:
    statuses = [
        _status(10.0, seeders=3),
        _status(50.0, seeders=0),
        _status(60.0, seeders=0),
        _status(70.0, seeders=0),
    ]
    events, _client, _result = _poll(statuses, [0.0, 150.0, 400.0], min_seeders=3)

    assert events[-1] == ("error", "No seeders: 0 found, 3 needed, for 5 min (70% downloaded)")


def test_no_seed_is_not_judged_when_the_minimum_is_zero() -> None:
    events, _client, _result = _poll([_status()] * 3, [0.0, 600.0, 1200.0], min_seeders=0)

    assert not any(s == "error" for s, _m in events)


def test_it_gives_up_only_once_a_torrent_that_lost_its_seeds_stops_moving() -> None:
    statuses = [_status(50.0, seeders=1), _status(50.0, seeders=0), _status(50.0, seeders=0)]
    events, _client, _result = _poll(statuses, [0.0, 100.0, 400.0], min_seeders=3)

    assert events[-1][0] == "error"
