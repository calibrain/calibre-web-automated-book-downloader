"""Files the user skipped in the pack review are not emailed or uploaded to BookLore."""

from __future__ import annotations

from pathlib import Path
from threading import Event
from unittest.mock import MagicMock, patch

import pytest

from shelfmark.core.models import DownloadTask

EMAIL = "shelfmark.download.outputs.email"
BOOKLORE = "shelfmark.download.outputs.booklore"


def _pack(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = tmp_path / "Series Pack"
    kept = root / "Book 1" / "one.epub"
    skipped = root / "Book 2" / "two.epub"
    for book in (kept, skipped):
        book.parent.mkdir(parents=True)
        book.write_bytes(b"epub")
    return root, kept, skipped


def _task(ignored: list[str]) -> DownloadTask:
    return DownloadTask(
        task_id="pack",
        source="prowlarr",
        title="Series Pack",
        output_args={"to": "reader@example.com"},
        ignored_files=ignored,
    )


def _send_by_email(root: Path, task: DownloadTask) -> tuple[list[Path] | None, list[tuple]]:
    from shelfmark.download.outputs.email import process_email_output

    sent: list[Path] = []
    statuses: list[tuple] = []
    with (
        patch(f"{EMAIL}._get_email_settings", return_value={}),
        patch(f"{EMAIL}.build_email_smtp_config", return_value=MagicMock()),
        patch(
            f"{EMAIL}.compose_email_message",
            side_effect=lambda _config, **kw: sent.extend(kw["files"]),
        ),
        patch(f"{EMAIL}.send_email_message") as send,
    ):
        process_email_output(root, task, Event(), lambda *s: statuses.append(s))
    return (sent if send.called else None), statuses


def _upload_to_booklore(root: Path, task: DownloadTask) -> tuple[list[Path] | None, list[tuple]]:
    from shelfmark.download.outputs.booklore import process_booklore_output

    statuses: list[tuple] = []
    with (
        patch(f"{BOOKLORE}._get_booklore_settings", return_value={}),
        patch(
            f"{BOOKLORE}.build_booklore_config",
            return_value=MagicMock(refresh_after_upload=False),
        ),
        patch(f"{BOOKLORE}.booklore_login", return_value="token"),
        patch(f"{BOOKLORE}.booklore_upload_file") as upload,
    ):
        process_booklore_output(root, task, Event(), lambda *s: statuses.append(s))
    uploaded = [c.args[2] for c in upload.call_args_list]
    return (uploaded or None), statuses


@pytest.mark.parametrize("deliver", [_send_by_email, _upload_to_booklore])
def test_a_skipped_book_is_not_delivered(deliver, tmp_path: Path) -> None:
    root, kept, _skipped = _pack(tmp_path)

    delivered, _statuses = deliver(root, _task(["Series Pack/Book 2/two.epub"]))

    assert delivered == [kept]


@pytest.mark.parametrize("deliver", [_send_by_email, _upload_to_booklore])
def test_nothing_is_delivered_when_every_file_was_skipped(deliver, tmp_path: Path) -> None:
    root, _kept, _skipped = _pack(tmp_path)

    delivered, statuses = deliver(
        root, _task(["Series Pack/Book 1/one.epub", "Series Pack/Book 2/two.epub"])
    )

    assert delivered is None
    assert ("error", "No book files found") in statuses
