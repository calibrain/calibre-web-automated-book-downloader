"""Which torrents Shelfmark removes from the client, and when it deletes their files."""

from __future__ import annotations

from pathlib import Path
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


@pytest.mark.parametrize(
    "status",
    [
        # A failed lookup reports 0% whatever the torrent holds.
        DownloadStatus.error("qbittorrent get_status failed (ConnectionError): offline"),
        # Re-checking data already on disk starts from 0% too.
        _status(DownloadState.CHECKING, 0.0),
        _status(DownloadState.UNKNOWN, 0.0),
    ],
)
def test_a_torrent_at_zero_is_not_removed_unless_it_really_has_not_started(
    status: DownloadStatus,
) -> None:
    client = MagicMock()
    client.name = "qbittorrent"
    client.get_status.return_value = status  # what the client reports once the user cancels

    ProwlarrHandler()._handle_cancelled_download(
        client, "dl", "torrent", lambda *_a: None, remove_if_unstarted=True
    )

    client.remove.assert_not_called()


def test_a_paused_torrent_that_downloaded_nothing_is_removed() -> None:
    client = _cancel(_status(DownloadState.PAUSED, 0.0), existing=False)

    client.remove.assert_called_once_with("dl", delete_files=True)


def test_an_unstarted_torrent_is_removed_without_files_where_the_client_cannot_delete_them() -> (
    None
):
    from shelfmark.download.clients.rtorrent import RTorrentClient

    client = MagicMock()
    client.name = "rtorrent"
    client.can_delete_files = RTorrentClient.can_delete_files
    client.get_status.return_value = _status(Q, 0.0)
    client.remove.return_value = True

    removed = ProwlarrHandler()._remove_unstarted_torrent(client, "dl")

    assert removed is True
    client.remove.assert_called_once_with("dl", delete_files=False)


# --- Remove & Delete Files only deletes what this task downloaded ------------------------


def _import_then_clean_up(existing: DownloadStatus | None, torrent_action: str) -> MagicMock:
    """Download through the handler, import successfully, then run the completion action."""
    client = MagicMock()
    client.name = "qbittorrent"
    client.find_existing.return_value = ("dl", existing) if existing else None
    client.add_download.return_value = "dl"
    client.get_status.return_value = _status(DownloadState.COMPLETE, 100.0, complete=True)
    client.remove.return_value = True
    handler = ProwlarrHandler()
    task = DownloadTask(task_id="r", source="prowlarr", title="Book")
    release = {"protocol": "torrent", "magnetUrl": "magnet:?xt=urn:btih:abc123"}
    with (
        patch("shelfmark.release_sources.prowlarr.handler.get_release", return_value=release),
        patch("shelfmark.release_sources.prowlarr.handler.get_client", return_value=client),
        patch("shelfmark.release_sources.prowlarr.handler.remove_release"),
        patch("shelfmark.release_sources.prowlarr.handler.POLL_INTERVAL", 0.01),
        patch.object(
            ProwlarrHandler,
            "_wait_for_completed_path",
            return_value=(Path("/downloads/Book"), None),
        ),
        patch(
            "shelfmark.download.clients.base_handler.config.get",
            side_effect=lambda key, default=None, **_kw: (
                torrent_action if key == "PROWLARR_TORRENT_ACTION" else default
            ),
        ),
    ):
        result = handler.download(
            task=task,
            cancel_flag=Event(),
            progress_callback=lambda _p: None,
            status_callback=_Recorder().status,
        )
        assert result == str(Path("/downloads/Book"))
        handler.post_process_cleanup(task, success=True)
    return client


def test_remove_and_delete_deletes_the_files_of_a_torrent_this_task_added() -> None:
    client = _import_then_clean_up(None, "remove_and_delete")

    client.add_download.assert_called_once()
    client.remove.assert_called_once_with("dl", delete_files=True)


@pytest.mark.parametrize(
    "existing",
    [
        _status(DownloadState.SEEDING, 100.0, complete=True),  # the user's own finished seed
        _status(D, 40.0),  # an in-progress torrent the task joined
    ],
)
def test_remove_and_delete_keeps_the_files_of_a_torrent_the_client_already_had(
    existing: DownloadStatus,
) -> None:
    client = _import_then_clean_up(existing, "remove_and_delete")

    client.add_download.assert_not_called()
    client.remove.assert_called_once_with("dl", delete_files=False)


def test_remove_still_removes_a_torrent_the_client_already_had() -> None:
    client = _import_then_clean_up(_status(DownloadState.SEEDING, 100.0, complete=True), "remove")

    client.remove.assert_called_once_with("dl", delete_files=False)
