from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from shelfmark.core.models import DownloadTask
from shelfmark.download import archive as archive_mod
from shelfmark.download.postprocess import scan as scan_mod

BOOK = "Author - Title.epub"


def _ebook_task() -> DownloadTask:
    return DownloadTask(task_id="t", source="direct_download", title="Test", content_type="ebook")


@pytest.fixture
def staging(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(scan_mod, "get_book_formats", lambda: ["epub"])
    monkeypatch.setattr(archive_mod, "get_supported_formats", lambda: ["epub"])
    root = tmp_path / "staging"
    counter = iter(range(1000))

    def build_staging_dir(prefix: str, _task_id: str) -> Path:
        path = root / f"{prefix}_{next(counter)}"
        path.mkdir(parents=True)
        return path

    monkeypatch.setattr(scan_mod, "build_staging_dir", build_staging_dir)
    return root


def _zip_bytes(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _write_zip(path: Path, members: dict[str, bytes]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_zip_bytes(members))
    return path


def _stub_rar(
    monkeypatch: pytest.MonkeyPatch, *, error: str | None = None
) -> list[tuple[str, list[str]]]:
    """Stand in for RAR extraction, which needs a RAR tool to build fixtures with.

    Every opened RAR yields one EPUB, or raises ``error``. Returns, per RAR opened, its name
    and the files beside it. ZIPs still go through the real code.
    """
    real_extract = archive_mod.extract_archive
    opened: list[tuple[str, list[str]]] = []

    def extract(archive_path: Path, output_dir: Path, content_type: str | None = None):
        if archive_path.suffix != ".rar":
            return real_extract(archive_path, output_dir, content_type)
        opened.append((archive_path.name, sorted(p.name for p in archive_path.parent.iterdir())))
        if error:
            raise archive_mod.ArchiveExtractionError(error)
        book = output_dir / BOOK
        book.write_bytes(b"epub")
        return [book], [], []

    monkeypatch.setattr(scan_mod, "extract_archive", extract)
    return opened


def test_zip_inside_zip_is_extracted(tmp_path: Path, staging: Path) -> None:
    download = tmp_path / "download"
    inner = _zip_bytes({BOOK: b"epub"})
    _write_zip(download / "outer.zip", {"inner.zip": inner, "notes.html": b"html"})

    files, _rejected, cleanup, error = scan_mod.collect_directory_files(
        download, _ebook_task(), allow_archive_extraction=True
    )

    assert error is None
    assert [f.name for f in files] == [BOOK]
    assert all(path.is_relative_to(staging) for path in cleanup)


def test_rar_volumes_from_separate_zips_are_extracted_together(
    tmp_path: Path, staging: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A RAR split across ZIPs: part-a.zip holds book.rar, part-b.zip holds book.r00.
    download = tmp_path / "download"
    _write_zip(download / "part-a.zip", {"book.rar": b"rar", "cover.jpg": b"jpg"})
    _write_zip(download / "part-b.zip", {"book.r00": b"r00"})
    opened = _stub_rar(monkeypatch)

    files, _rejected, _cleanup, error = scan_mod.collect_directory_files(
        download, _ebook_task(), allow_archive_extraction=True
    )

    assert error is None
    assert opened == [("book.rar", ["book.r00", "book.rar"])]
    assert [f.name for f in files] == [BOOK]


def test_only_first_parts_are_opened(
    tmp_path: Path, staging: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    download = tmp_path / "download"
    _write_zip(
        download / "outer.zip",
        {"book.part1.rar": b"1", "book.part2.rar": b"2", "book.part3.rar": b"3"},
    )
    opened = _stub_rar(monkeypatch)

    files, _rejected, _cleanup, error = scan_mod.collect_directory_files(
        download, _ebook_task(), allow_archive_extraction=True
    )

    assert error is None
    assert [name for name, _beside in opened] == ["book.part1.rar"]
    assert [f.name for f in files] == [BOOK]


def test_volumes_with_the_same_name_are_gathered_once(
    tmp_path: Path, staging: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    download = tmp_path / "download"
    _write_zip(download / "one.zip", {"book.rar": b"first"})
    _write_zip(download / "two.zip", {"book.rar": b"second"})
    opened = _stub_rar(monkeypatch)
    warned: list[tuple[object, ...]] = []
    monkeypatch.setattr(scan_mod.logger, "warning", lambda *args, **_kw: warned.append(args))

    files, _rejected, _cleanup, error = scan_mod.collect_directory_files(
        download, _ebook_task(), allow_archive_extraction=True
    )

    assert error is None
    assert [name for name, _beside in opened] == ["book.rar"]
    assert [f.name for f in files] == [BOOK]
    assert any("already gathered" in str(args[0]) for args in warned)


def test_failure_in_a_nested_archive_is_reported(
    tmp_path: Path, staging: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    download = tmp_path / "download"
    _write_zip(download / "outer.zip", {"book.rar": b"rar"})
    _stub_rar(monkeypatch, error="unrar binary not found")

    files, _rejected, cleanup, error = scan_mod.collect_directory_files(
        download, _ebook_task(), allow_archive_extraction=True
    )

    assert files == []
    assert error is not None
    assert "book.rar: unrar binary not found" in error
    assert all(path.is_relative_to(staging) for path in cleanup)


def test_archive_without_books_or_volumes_still_reports_nothing_found(
    tmp_path: Path, staging: Path
) -> None:
    download = tmp_path / "download"
    _write_zip(download / "outer.zip", {"cover.jpg": b"jpg", "notes.html": b"html"})

    files, _rejected, _cleanup, error = scan_mod.collect_directory_files(
        download, _ebook_task(), allow_archive_extraction=True
    )

    assert files == []
    assert error is not None
    assert "No book files found" in error


def test_single_archive_download_with_a_nested_rar_is_extracted(
    tmp_path: Path, staging: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = _write_zip(tmp_path / "download.zip", {"book.rar": b"rar"})
    _stub_rar(monkeypatch)

    files, _rejected, cleanup, error = scan_mod.collect_staged_files(
        archive,
        _ebook_task(),
        allow_archive_extraction=True,
        status_callback=None,
        cleanup_archives=False,
    )

    assert error is None
    assert [f.name for f in files] == [BOOK]
    assert all(path.is_relative_to(staging) for path in cleanup)


def test_single_archive_download_reports_a_nested_failure(
    tmp_path: Path, staging: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = _write_zip(tmp_path / "download.zip", {"book.rar": b"rar"})
    _stub_rar(monkeypatch, error="unrar binary not found")

    files, _rejected, _cleanup, error = scan_mod.collect_staged_files(
        archive,
        _ebook_task(),
        allow_archive_extraction=True,
        status_callback=None,
        cleanup_archives=False,
    )

    assert files == []
    assert error is not None
    assert "unrar binary not found" in error
    assert "No book files found in archive" in error, (
        "the outer message (or its format hint) is kept"
    )


@pytest.mark.parametrize(
    ("name", "volume", "first"),
    [
        ("book.zip", True, True),
        ("book.rar", True, True),
        ("book.r00", True, False),
        ("book.R12", True, False),
        ("book.part1.rar", True, True),
        ("book.part01.rar", True, True),
        ("book.part2.rar", True, False),
        ("book.epub", False, False),
        ("book.txt", False, False),
    ],
)
def test_volume_detection(name: str, volume: bool, first: bool) -> None:
    assert archive_mod.is_archive_volume(Path(name)) is volume
    assert archive_mod.is_first_volume(Path(name)) is first
