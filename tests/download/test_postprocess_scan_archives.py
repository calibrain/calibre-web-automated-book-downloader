from __future__ import annotations

import zipfile

from shelfmark.core import formats as formats_mod
from shelfmark.core.models import DownloadTask
from shelfmark.core.utils import DEFAULT_AUDIOBOOK_FORMATS
from shelfmark.download import archive as archive_mod
from shelfmark.download.postprocess import scan as scan_mod

# The shipped default for SUPPORTED_AUDIOBOOK_FORMATS, which includes archives.
_DEFAULT_AUDIOBOOK_FORMATS = list(DEFAULT_AUDIOBOOK_FORMATS)


def _audiobook_task() -> DownloadTask:
    return DownloadTask(task_id="t", source="audiobookbay", title="Test", content_type="audiobook")


def _use_default_audiobook_formats(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        formats_mod, "get_supported_audiobook_formats", lambda: list(_DEFAULT_AUDIOBOOK_FORMATS)
    )
    monkeypatch.setattr(
        archive_mod, "get_supported_audiobook_formats", lambda: list(_DEFAULT_AUDIOBOOK_FORMATS)
    )
    staging = tmp_path / "staging"

    def build_staging_dir(prefix, _task_id):
        path = staging / str(prefix)
        path.mkdir(parents=True, exist_ok=True)
        return path

    monkeypatch.setattr(scan_mod, "build_staging_dir", build_staging_dir)


def _write_series_zip(directory):
    directory.mkdir()
    archive = directory / "Series - Books 1-3.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for n in (1, 2, 3):
            zf.writestr(f"Series/Book {n}/Book {n}.mp3", b"audio")
    return archive


def test_archive_is_not_counted_as_book_file(tmp_path, monkeypatch) -> None:
    _use_default_audiobook_formats(monkeypatch, tmp_path)
    download = tmp_path / "download"
    archive = _write_series_zip(download)

    book_files, _rejected, archive_files, error = scan_mod.scan_directory_tree(
        download, "audiobook"
    )

    assert error is None
    assert book_files == []
    assert archive_files == [archive]


def test_archive_is_extracted_with_default_audiobook_formats(tmp_path, monkeypatch) -> None:
    _use_default_audiobook_formats(monkeypatch, tmp_path)
    download = tmp_path / "download"
    _write_series_zip(download)

    files, _rejected, _cleanup, error = scan_mod.collect_directory_files(
        download,
        _audiobook_task(),
        allow_archive_extraction=True,
    )

    assert error is None
    assert sorted(f.name for f in files) == ["Book 1.mp3", "Book 2.mp3", "Book 3.mp3"]


def test_archive_imported_as_is_when_extraction_disabled(tmp_path, monkeypatch) -> None:
    _use_default_audiobook_formats(monkeypatch, tmp_path)
    download = tmp_path / "download"
    archive = _write_series_zip(download)

    files, _rejected, _cleanup, error = scan_mod.collect_directory_files(
        download,
        _audiobook_task(),
        allow_archive_extraction=False,
    )

    assert error is None
    assert files == [archive]


def test_loose_audio_still_preferred_over_archives(tmp_path, monkeypatch) -> None:
    _use_default_audiobook_formats(monkeypatch, tmp_path)
    download = tmp_path / "download"
    _write_series_zip(download)
    loose = download / "Book.m4b"
    loose.write_bytes(b"audio")

    files, _rejected, _cleanup, error = scan_mod.collect_directory_files(
        download,
        _audiobook_task(),
        allow_archive_extraction=True,
    )

    assert error is None
    assert files == [loose]
