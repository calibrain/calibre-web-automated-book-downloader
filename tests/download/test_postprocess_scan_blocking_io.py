from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from shelfmark.core.models import DownloadTask
from shelfmark.download.postprocess import scan as scan_mod


def test_scan_directory_tree_runs_walk_via_run_blocking_io(tmp_path, monkeypatch) -> None:
    (tmp_path / "book.epub").write_text("x", encoding="utf-8")

    inside_run_blocking = False
    walk_called = False

    original_walk = scan_mod.os.walk

    def walk_wrapper(*args, **kwargs):
        nonlocal walk_called
        walk_called = True
        assert inside_run_blocking, "os.walk should run within run_blocking_io"
        return original_walk(*args, **kwargs)

    def run_blocking_io_stub(func, *args, **kwargs):
        nonlocal inside_run_blocking
        inside_run_blocking = True
        try:
            return func(*args, **kwargs)
        finally:
            inside_run_blocking = False

    monkeypatch.setattr(scan_mod.os, "walk", walk_wrapper)
    monkeypatch.setattr(scan_mod, "run_blocking_io", run_blocking_io_stub)

    scan_mod.scan_directory_tree(tmp_path, content_type=None)

    assert walk_called, "Expected scan_directory_tree to call os.walk"


def test_extract_archive_files_runs_extract_via_run_blocking_io(tmp_path, monkeypatch) -> None:
    inside_run_blocking = False
    extract_called = False

    def extract_archive_stub(*args, **kwargs):
        nonlocal extract_called
        extract_called = True
        assert inside_run_blocking, "extract_archive should run within run_blocking_io"
        return [], [], []

    def run_blocking_io_stub(func, *args, **kwargs):
        nonlocal inside_run_blocking
        inside_run_blocking = True
        try:
            return func(*args, **kwargs)
        finally:
            inside_run_blocking = False

    monkeypatch.setattr(scan_mod, "extract_archive", extract_archive_stub)
    monkeypatch.setattr(scan_mod, "run_blocking_io", run_blocking_io_stub)

    task = DownloadTask(task_id="t", source="prowlarr", title="Test", content_type="book")
    scan_mod.extract_archive_files(
        archive_path=Path("/tmp/fake.zip"),
        output_dir=tmp_path,
        task=task,
        cleanup_archive=False,
    )

    assert extract_called, "Expected extract_archive_files to call extract_archive"


def test_extract_archive_files_extracts_a_rar_in_place_not_in_the_io_threadpool(
    tmp_path, monkeypatch
) -> None:
    # rarfile runs unrar as a subprocess, which gevent cannot start from its IO threadpool.
    extracted = []

    def extract_archive_stub(*args, **kwargs):
        extracted.append(args[0])
        return [], [], []

    def run_blocking_io_stub(func, *args, **kwargs):
        raise AssertionError("a RAR must not be extracted through run_blocking_io")

    monkeypatch.setattr(scan_mod, "extract_archive", extract_archive_stub)
    monkeypatch.setattr(scan_mod, "run_blocking_io", run_blocking_io_stub)

    task = DownloadTask(task_id="t", source="prowlarr", title="Test", content_type="book")
    scan_mod.extract_archive_files(
        archive_path=Path("/tmp/fake.RAR"),
        output_dir=tmp_path,
        task=task,
        cleanup_archive=False,
    )

    assert extracted == [Path("/tmp/fake.RAR")]


def test_a_rar_extraction_that_starts_a_process_works_under_gevent(tmp_path) -> None:
    pytest.importorskip("gevent")

    repo_root = Path(__file__).resolve().parents[2]
    script = textwrap.dedent(
        """
        import os
        import subprocess
        import sys
        import tempfile
        from pathlib import Path

        from gevent import monkey

        monkey.patch_all()
        os.environ.setdefault("LOG_ROOT", tempfile.mkdtemp(prefix="shelfmark-log-root-"))

        from shelfmark.core.models import DownloadTask
        from shelfmark.download.postprocess import scan

        def start_a_process_like_rarfile(*args, **kwargs):
            subprocess.run([sys.executable, "-c", "pass"], check=True)
            return [], [], []

        scan.extract_archive = start_a_process_like_rarfile
        task = DownloadTask(task_id="t", source="prowlarr", title="T", content_type="book")
        scan.extract_archive_files(
            archive_path=Path("book.rar"),
            output_dir=Path(sys.argv[1]),
            task=task,
            cleanup_archive=False,
        )
        """
    )

    completed = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, (
        f"exit code {completed.returncode}\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    )
