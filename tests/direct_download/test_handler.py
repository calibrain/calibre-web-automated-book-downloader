from threading import Event

from shelfmark.core.config import config
from shelfmark.core.models import DownloadTask
from shelfmark.release_sources.direct_download import DirectDownloadHandler


def test_direct_download_handler_builds_staging_filename_from_browse_record(monkeypatch):
    captured = {}

    def fake_download_book(book_info, book_path, progress_callback, cancel_flag, status_callback):
        captured["title"] = book_info.title
        captured["year"] = book_info.year
        captured["path"] = book_path
        return "https://example.com/file.epub"

    from shelfmark.release_sources.direct_download import handler as dd

    monkeypatch.setattr(dd, "_download_book", fake_download_book)
    monkeypatch.setattr(
        config,
        "get",
        lambda key, default=None: "rename" if key == "FILE_ORGANIZATION" else default,
    )

    task = DownloadTask(
        task_id="92c7879138d18678b763118250228955",
        source="direct_download",
        title="Project Hail Mary: A Novel",
        author="Andy Weir",
        year="2021",
        format="epub",
    )

    handler = DirectDownloadHandler()
    result = handler.download(task, Event(), lambda _progress: None, lambda _status, _message: None)

    assert result is not None
    assert captured["title"] == "Project Hail Mary: A Novel"
    assert captured["year"] == "2021"
    assert captured["path"].name == "Andy Weir - Project Hail Mary_ A Novel (2021).epub"


def test_direct_download_handler_uses_source_id_filename_when_organization_disabled(monkeypatch):
    captured = {}

    def fake_download_book(book_info, book_path, progress_callback, cancel_flag, status_callback):
        captured["path"] = book_path
        return "https://example.com/file.epub"

    from shelfmark.release_sources.direct_download import handler as dd

    monkeypatch.setattr(dd, "_download_book", fake_download_book)
    monkeypatch.setattr(
        config,
        "get",
        lambda key, default=None: "none" if key == "FILE_ORGANIZATION" else default,
    )

    task = DownloadTask(
        task_id="aa-md5-hash",
        source="direct_download",
        title="Ignored Human Title",
        author="Ignored Author",
        year="2024",
        format="epub",
    )

    handler = DirectDownloadHandler()
    result = handler.download(task, Event(), lambda _progress: None, lambda _status, _message: None)

    assert result is not None
    assert captured["path"].name == "aa-md5-hash.epub"


def test_direct_download_handler_skips_download_when_cancelled_before_start(monkeypatch):
    status_updates: list[tuple[str, str | None]] = []

    def unexpected_download(*_args, **_kwargs):
        raise AssertionError("_download_book should not run when the task is already cancelled")

    from shelfmark.release_sources.direct_download import handler as dd

    monkeypatch.setattr(dd, "_download_book", unexpected_download)

    task = DownloadTask(
        task_id="cancel-me",
        source="direct_download",
        title="Cancelled Book",
        format="epub",
    )
    cancel_flag = Event()
    cancel_flag.set()

    handler = DirectDownloadHandler()
    result = handler.download(
        task,
        cancel_flag,
        lambda _progress: None,
        lambda status, message: status_updates.append((status, message)),
    )

    assert result is None
    assert status_updates == [("cancelled", "Cancelled")]


def test_direct_download_handler_removes_partial_file_when_cancelled_after_download(
    monkeypatch, tmp_path
):
    status_updates: list[tuple[str, str | None]] = []

    def fake_download_book(book_info, book_path, progress_callback, cancel_flag, status_callback):
        book_path.write_text("partial")
        cancel_flag.set()
        return "https://example.com/file.epub"

    from shelfmark.release_sources.direct_download import handler as dd

    monkeypatch.setattr(dd, "_download_book", fake_download_book)
    monkeypatch.setattr(dd, "TMP_DIR", tmp_path)
    monkeypatch.setattr(
        config,
        "get",
        lambda key, default=None: "rename" if key == "FILE_ORGANIZATION" else default,
    )

    task = DownloadTask(
        task_id="cancel-after-download",
        source="direct_download",
        title="Partial Book",
        author="A. Author",
        year="2024",
        format="epub",
    )
    cancel_flag = Event()

    handler = DirectDownloadHandler()
    result = handler.download(
        task,
        cancel_flag,
        lambda _progress: None,
        lambda status, message: status_updates.append((status, message)),
    )

    expected_path = tmp_path / "A. Author - Partial Book (2024).epub"

    assert result is None
    assert not expected_path.exists()
    assert status_updates[-1] == ("cancelled", "Cancelled")
