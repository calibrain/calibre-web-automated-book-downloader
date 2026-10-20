"""A torrent the client has queued is waiting for a slot, not stalled."""

from __future__ import annotations

import sys
from threading import Event
from unittest.mock import MagicMock, patch

import pytest

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


WINDOW = 15 * 60.0  # what `_poll` configures unless a test asks for something else


def _poll(
    statuses: list[DownloadStatus],
    clock: list[float] | None = None,
    *,
    minutes: object = 15,
    protocol: str = "torrent",
) -> _Recorder:
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
    patches = [
        patch.object(ProwlarrHandler, "_poll_interval", return_value=0.0),
        patch.object(
            bh.config,
            "get",
            side_effect=lambda key, default=None, **_kw: (
                minutes if key == bh.STALL_TIMEOUT_SETTING and minutes is not None else default
            ),
        ),
    ]
    if ticks is not None:
        patches.append(patch.object(bh.time, "monotonic", side_effect=lambda: next(ticks)))
    for p in patches:
        p.start()
    try:
        ProwlarrHandler()._poll_and_complete(
            client,
            "hash",
            protocol,
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
    # A clock that starts below the renew interval, like a host that booted a minute ago.
    rec = _poll([_status(Q)] * 4, clock=[5.0, 5.0, 6.0, 7.0])

    assert rec.graces[0] == bh.QUEUE_GRACE_SECONDS
    assert rec.graces.count(bh.QUEUE_GRACE_SECONDS) == 1


def test_the_grace_is_released_when_the_torrent_starts() -> None:
    rec = _poll([_status(Q), _status(Q), _status(D, 5.0), _status(D, 6.0)])

    # queue grace, its release, then one movement grace per reading that went up
    assert rec.graces == [
        bh.QUEUE_GRACE_SECONDS,
        0.0,
        WINDOW,
        WINDOW,
    ]


def test_a_torrent_that_never_queued_only_gets_the_moving_window() -> None:
    rec = _poll([_status(D, 1.0), _status(D, 2.0)])

    assert rec.graces == [WINDOW] * 2  # no release, no queue renewal


# --- a torrent that is moving gets a longer window each time it moves ---------------------


def test_every_step_forward_pushes_the_stall_deadline_out() -> None:
    rec = _poll([_status(D, 1.0), _status(D, 2.0), _status(D, 3.5)])

    assert rec.graces == [WINDOW] * 3


def test_a_torrent_that_stops_moving_stops_getting_more_time() -> None:
    rec = _poll([_status(D, 5.0), _status(D, 5.0), _status(D, 5.0), _status(D, 5.0)])

    assert rec.graces == [WINDOW]  # only the first reading moved


def test_a_torrent_gets_a_full_window_to_start_even_before_any_data_arrives() -> None:
    # Metadata and the first peer can take longer than the orchestrator's 5 minutes.
    assert _poll([_status(D, 0.0)] * 4).graces == [WINDOW]


def test_the_start_window_is_given_once_not_renewed_while_stuck_at_zero() -> None:
    graces = _poll([_status(D, 0.0)] * 20).graces

    assert graces.count(WINDOW) == 1


def test_a_torrent_leaving_the_queue_at_zero_gets_a_fresh_start_window() -> None:
    rec = _poll([_status(Q), _status(Q), _status(D, 0.0), _status(D, 0.0)])

    # queue grace, its release, then the start window
    assert rec.graces == [bh.QUEUE_GRACE_SECONDS, 0.0, WINDOW]


def test_a_queued_torrent_is_not_given_the_start_window_yet() -> None:
    assert _poll([_status(Q)] * 4).graces == [bh.QUEUE_GRACE_SECONDS]  # the queue grace only


def test_going_backwards_is_not_movement() -> None:
    rec = _poll([_status(D, 50.0), _status(D, 40.0), _status(D, 40.0)])

    assert rec.graces == [WINDOW]


def test_the_windows_fit_inside_what_the_orchestrator_allows() -> None:
    from shelfmark.download import orchestrator

    cap = orchestrator._MAX_ACTIVITY_GRACE_SECONDS
    assert bh.QUEUE_GRACE_SECONDS <= cap
    assert bh.STALL_TIMEOUT_MAX_MINUTES * 60 <= cap
    assert bh.STALL_TIMEOUT_MIN_MINUTES * 60 == orchestrator.STALL_TIMEOUT


# --- the window is the user's to set; the default leaves behaviour exactly as it was ---------


def test_by_default_a_moving_torrent_gets_the_fifteen_minute_window() -> None:
    rec = _poll([_status(D, 1.0), _status(D, 2.0), _status(D, 2.0)], minutes=None)

    assert rec.graces == [WINDOW, WINDOW]


def test_by_default_a_new_torrent_gets_the_fifteen_minute_start_window() -> None:
    assert _poll([_status(D, 0.0)] * 4, minutes=None).graces == [WINDOW]


def test_the_default_is_the_fifteen_minutes_that_shipped() -> None:
    assert bh.STALL_TIMEOUT_DEFAULT_MINUTES == 15


def test_a_queued_torrent_still_gets_its_grace_by_default() -> None:
    assert _poll([_status(Q)] * 3, minutes=None).graces == [bh.QUEUE_GRACE_SECONDS]


def test_a_longer_setting_sets_the_window() -> None:
    rec = _poll([_status(D, 1.0), _status(D, 2.0)], minutes=30)

    assert rec.graces == [1800.0, 1800.0]


def test_the_start_window_follows_the_setting_too() -> None:
    assert _poll([_status(D, 0.0)] * 3, minutes=30).graces == [1800.0]


def test_five_minutes_is_the_orchestrators_own_timeout_so_it_adds_nothing() -> None:
    assert _poll([_status(D, 1.0), _status(D, 2.0)], minutes=5).graces == []


def test_a_value_above_the_maximum_is_capped() -> None:
    rec = _poll([_status(D, 1.0)], minutes=500)

    assert rec.graces == [bh.STALL_TIMEOUT_MAX_MINUTES * 60.0]


@pytest.mark.parametrize(
    ("configured", "seconds"),
    [
        (None, 900.0),  # not set
        (5, 300.0),
        (15, 900.0),
        ("30", 1800.0),  # an env var arrives as text
        (7.5, 450.0),
        (1, 300.0),  # below the minimum
        (0, 300.0),
        (-10, 300.0),
        (500, 3600.0),  # above the maximum
        ("soon", 900.0),  # not a number: the default
        ("", 900.0),
        (float("nan"), 900.0),
        (float("inf"), 900.0),
    ],
)
def test_the_configured_window_is_read_and_kept_in_range(
    configured: object, seconds: float
) -> None:
    with patch.object(bh.config, "get", return_value=configured):
        # `get` returns the configured value; None stands in for "unset" via the default
        value = ProwlarrHandler._stall_window_seconds()

    assert value == seconds


def test_the_setting_is_offered_in_the_download_clients_tab() -> None:
    from shelfmark.core.settings_registry import get_settings_field_map
    from shelfmark.download.clients import settings as client_settings  # noqa: F401

    field, tab_name = get_settings_field_map("prowlarr_clients")[bh.STALL_TIMEOUT_SETTING]
    assert tab_name == "prowlarr_clients"

    assert (field.min_value, field.max_value) == (
        bh.STALL_TIMEOUT_MIN_MINUTES,
        bh.STALL_TIMEOUT_MAX_MINUTES,
    )
    assert field.default == bh.STALL_TIMEOUT_DEFAULT_MINUTES


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


def test_the_first_queue_grace_is_sent_on_a_freshly_booted_host() -> None:
    assert bh.QUEUE_GRACE_RENEW_SECONDS > 5.0
    rec = _poll([_status(Q)] * 2, clock=[1.0, 2.0])

    assert rec.graces == [bh.QUEUE_GRACE_SECONDS]


def test_a_torrent_requeued_by_the_client_gets_its_window_back_when_it_leaves() -> None:
    rec = _poll([_status(D, 0.0), _status(Q), _status(Q), _status(D, 0.0), _status(D, 0.0)])

    # start window, queue grace, release, then a new window instead of the bare 5 minutes
    assert rec.graces == [WINDOW, bh.QUEUE_GRACE_SECONDS, 0.0, WINDOW]


def test_a_moving_torrent_that_gets_requeued_is_not_left_on_the_short_timeout() -> None:
    rec = _poll([_status(D, 5.0), _status(Q, 5.0), _status(D, 5.0), _status(D, 5.0)])

    assert rec.graces == [WINDOW, bh.QUEUE_GRACE_SECONDS, 0.0, WINDOW]


def test_a_requeued_torrent_follows_the_setting_when_it_leaves_the_queue() -> None:
    rec = _poll([_status(D, 1.0), _status(Q, 1.0), _status(D, 1.0)], minutes=30)

    assert rec.graces == [1800.0, bh.QUEUE_GRACE_SECONDS, 0.0, 1800.0]


def test_usenet_does_not_get_the_torrent_stall_window() -> None:
    rec = _poll([_status(D, 1.0), _status(D, 2.0), _status(D, 0.0)], minutes=30, protocol="usenet")

    assert rec.graces == []


def test_the_minimum_window_is_derived_from_the_orchestrators_timeout() -> None:
    from shelfmark.download import orchestrator
    from shelfmark.download.activity import STALL_TIMEOUT_SECONDS

    assert orchestrator.STALL_TIMEOUT == STALL_TIMEOUT_SECONDS
    assert bh.STALL_TIMEOUT_MIN_MINUTES * 60 == STALL_TIMEOUT_SECONDS
