"""Tests for WebshareHandler: link resolution, streaming, cleanup on failure."""

import threading
from unittest.mock import MagicMock, patch

from shelfmark.core.models import DownloadTask
from shelfmark.release_sources.webshare import handler as webshare_handler
from shelfmark.release_sources.webshare.api import WebshareError
from shelfmark.release_sources.webshare.handler import WebshareHandler


def _task(task_id="webshare:abc123", fmt="epub"):
    return DownloadTask(task_id=task_id, source="webshare", title="Krysar", format=fmt)


def _http_response(body: bytes):
    response = MagicMock()
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    response.headers = {"Content-Length": str(len(body))}
    response.raise_for_status.return_value = None
    response.iter_content.return_value = [body[i : i + 4096] for i in range(0, len(body), 4096)]
    return response


def _run(tmp_path, *, file_link, body=b"x" * 20000, cancel=None, task=None):
    status = MagicMock()
    progress = MagicMock()
    cancel = cancel or threading.Event()
    with (
        patch.object(webshare_handler, "TMP_DIR", tmp_path),
        patch.object(
            webshare_handler.config,
            "get",
            side_effect=lambda k, d=None: "none" if k == "FILE_ORGANIZATION" else d,
        ),
        patch.object(webshare_handler, "get_credentials", return_value=("user", "pw")),
        patch.object(webshare_handler.WebshareClient, "file_link", side_effect=file_link),
        patch.object(webshare_handler.requests, "get", return_value=_http_response(body)),
    ):
        result = WebshareHandler().download(task or _task(), cancel, progress, status)
    return result, status, progress


def test_download_streams_file_named_after_link(tmp_path):
    idents = []

    def file_link(ident):
        idents.append(ident)
        return "https://dl.webshare.cz/abc/Krysar%20%28CZ%29.epub"

    result, _, progress = _run(tmp_path, file_link=file_link)

    expected = tmp_path / "Krysar (CZ).epub"
    assert result == str(expected)
    assert expected.read_bytes() == b"x" * 20000
    assert idents == ["abc123"]
    progress.assert_called_with(100.0)


def test_too_small_file_is_rejected_and_removed(tmp_path):
    result, status, _ = _run(
        tmp_path, file_link=lambda ident: "https://dl.webshare.cz/f.epub", body=b"tiny"
    )
    assert result is None
    assert not (tmp_path / "f.epub").exists()
    status.assert_called_with("error", "Webshare returned an invalid file")


def test_link_error_reports_message(tmp_path):
    def file_link(ident):
        msg = "File not found."
        raise WebshareError(msg, "FILE_LINK_FATAL_1")

    result, status, _ = _run(tmp_path, file_link=file_link)
    assert result is None
    status.assert_called_with("error", "File not found.")


def test_cancel_before_start(tmp_path):
    cancel = threading.Event()
    cancel.set()
    file_link = MagicMock()
    result, status, _ = _run(tmp_path, file_link=file_link, cancel=cancel)
    assert result is None
    file_link.assert_not_called()
    status.assert_called_with("cancelled", "Cancelled")
