"""Tests for LibgenHandler: prefix stripping, ads.php resolution, mirror fallthrough."""

import io
import threading
from unittest.mock import MagicMock, patch

from shelfmark.core.models import DownloadTask
from shelfmark.release_sources.libgen import handler as libgen_handler
from shelfmark.release_sources.libgen.handler import LibgenHandler
from tests.libgen import sample_html as html


def _task(task_id, fmt="cbr"):
    return DownloadTask(
        task_id=task_id, source="libgen", title="One Piece 515", format=fmt, size="6 MB"
    )


def _buf(nbytes=20000):
    buf = io.BytesIO(b"x" * nbytes)
    buf.seek(0, io.SEEK_END)  # download_url returns the buffer positioned at its end
    return buf


def _run(task, tmp_path, *, mirrors_list, fetch_page, download_url):
    status = MagicMock()
    cancel = threading.Event()
    with (
        patch.object(libgen_handler, "TMP_DIR", tmp_path),
        patch.object(
            libgen_handler.config,
            "get",
            side_effect=lambda k, d=None: "none" if k == "FILE_ORGANIZATION" else d,
        ),
        patch("shelfmark.core.mirrors.get_libgen_mirrors", return_value=mirrors_list),
        patch.object(libgen_handler.scraper, "fetch_page", side_effect=fetch_page),
        patch.object(libgen_handler.downloader, "download_url", side_effect=download_url),
    ):
        result = LibgenHandler().download(task, cancel, MagicMock(), status)
    return result, status


def test_download_strips_prefix_resolves_and_writes(tmp_path):
    captured = {}

    def fetch_page(url, timeout=(5, 10)):
        return html.ADS_HTML

    def download_url(link, size, prog, cancel, sel, status, referer=None):
        captured["link"] = link
        captured["referer"] = referer
        captured["selector"] = sel
        return _buf()

    result, _ = _run(
        _task(f"libgen:{html.MD5_A}"),
        tmp_path,
        mirrors_list=["https://libgen.li"],
        fetch_page=fetch_page,
        download_url=download_url,
    )
    expected = tmp_path / f"{html.MD5_A}.cbr"
    assert result == str(expected)
    assert expected.exists()
    assert captured["link"] == f"https://libgen.li/get.php?md5={html.MD5_A}&key={html.GET_KEY}"
    assert captured["referer"] == f"https://libgen.li/ads.php?md5={html.MD5_A}"
    assert captured["selector"] is None  # no AAMirrorSelector constructed


def test_download_accepts_bare_md5_task_id(tmp_path):
    result, _ = _run(
        _task(html.MD5_A),
        tmp_path,
        mirrors_list=["https://libgen.li"],
        fetch_page=lambda url, timeout=(5, 10): html.ADS_HTML,
        download_url=lambda *a, **k: _buf(),
    )
    assert result == str(tmp_path / f"{html.MD5_A}.cbr")


def test_download_falls_through_to_second_mirror(tmp_path):
    def fetch_page(url, timeout=(5, 10)):
        return None if "dead" in url else html.ADS_HTML

    result, _ = _run(
        _task(f"libgen:{html.MD5_A}"),
        tmp_path,
        mirrors_list=["https://dead.example", "https://libgen.li"],
        fetch_page=fetch_page,
        download_url=lambda *a, **k: _buf(),
    )
    assert result == str(tmp_path / f"{html.MD5_A}.cbr")


def test_download_all_mirrors_fail_returns_none(tmp_path):
    result, status = _run(
        _task(f"libgen:{html.MD5_A}"),
        tmp_path,
        mirrors_list=["https://a", "https://b"],
        fetch_page=lambda url, timeout=(5, 10): None,
        download_url=lambda *a, **k: _buf(),
    )
    assert result is None
    status.assert_any_call("error", "All Libgen mirrors failed")


def test_download_too_small_file_is_rejected(tmp_path):
    result, status = _run(
        _task(f"libgen:{html.MD5_A}"),
        tmp_path,
        mirrors_list=["https://libgen.li"],
        fetch_page=lambda url, timeout=(5, 10): html.ADS_HTML,
        download_url=lambda *a, **k: _buf(100),  # below _MIN_VALID_FILE_SIZE
    )
    assert result is None
    status.assert_any_call("error", "All Libgen mirrors failed")


def test_download_cancelled_before_start(tmp_path):
    status = MagicMock()
    cancel = threading.Event()
    cancel.set()
    with (
        patch.object(libgen_handler, "TMP_DIR", tmp_path),
        patch("shelfmark.core.mirrors.get_libgen_mirrors", return_value=["https://libgen.li"]),
    ):
        result = LibgenHandler().download(
            _task(f"libgen:{html.MD5_A}"), cancel, MagicMock(), status
        )
    assert result is None
    status.assert_any_call("cancelled", "Cancelled")
