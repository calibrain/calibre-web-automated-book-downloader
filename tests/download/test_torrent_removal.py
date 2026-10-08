"""A cancelled torrent that never started is removed from the client."""

from __future__ import annotations

from threading import Event
from unittest.mock import MagicMock, patch

import pytest

from shelfmark.core.models import DownloadTask
from shelfmark.download.clients import DownloadState, DownloadStatus
from shelfmark.release_sources.prowlarr.handler import ProwlarrHandler


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


Q, D = DownloadState.QUEUED, DownloadState.DOWNLOADING


# --- removing a torrent that never started -----------------------------------------------


def _cancel(status: DownloadStatus, *, existing: bool, protocol: str = "torrent") -> MagicMock:
    client = MagicMock()
    client.name = "qbittorrent"
    client.find_existing.return_value = ("dl", status) if existing else None
    client.add_download.return_value = "dl"
    client.remove.return_value = True
    cancel = Event()

    def get_status(_id: str) -> DownloadStatus:
        cancel.set()  # the user cancels while the download is being polled
        return status

    client.get_status.side_effect = get_status
    release = {"protocol": protocol, "magnetUrl": "magnet:?xt=urn:btih:abc123"}
    with (
        patch("shelfmark.release_sources.prowlarr.handler.get_release", return_value=release),
        patch("shelfmark.release_sources.prowlarr.handler.get_client", return_value=client),
        patch("shelfmark.release_sources.prowlarr.handler.POLL_INTERVAL", 0.01),
    ):
        ProwlarrHandler().download(
            task=DownloadTask(task_id="c", source="prowlarr", title="Book"),
            cancel_flag=cancel,
            progress_callback=lambda _p: None,
            status_callback=_Recorder().status,
        )
    return client


@pytest.mark.parametrize("state", [DownloadState.QUEUED, DownloadState.DOWNLOADING])
def test_a_cancelled_torrent_that_downloaded_nothing_is_removed(state: DownloadState) -> None:
    client = _cancel(_status(state, 0.0), existing=False)

    client.remove.assert_called_once_with("dl", delete_files=True)


def test_a_cancelled_torrent_with_data_is_left_for_seeding() -> None:
    client = _cancel(_status(D, 12.0), existing=False)

    client.remove.assert_not_called()


def test_a_cancelled_torrent_the_user_already_had_is_never_removed() -> None:
    client = _cancel(_status(Q, 0.0), existing=True)

    client.remove.assert_not_called()


def test_a_complete_or_seeding_torrent_is_not_removed() -> None:
    client = _cancel(_status(DownloadState.SEEDING, 100.0, complete=True), existing=False)

    client.remove.assert_not_called()


def test_a_failed_removal_does_not_break_the_cancel() -> None:
    status = _status(Q, 0.0)
    client = MagicMock()
    client.remove.side_effect = OSError("boom")
    client.get_status.return_value = status

    ProwlarrHandler()._handle_cancelled_download(
        client, "dl", "torrent", lambda *_a: None, remove_if_unstarted=True
    )  # does not raise
