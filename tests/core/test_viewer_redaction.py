"""Tests for hiding release-source details from non-admin viewers (#1418)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from shelfmark.api.websocket import WebSocketManager
from shelfmark.core import viewer_redaction
from shelfmark.core.viewer_redaction import (
    match_public_download_id,
    public_download_id,
    redact_download_payload,
    redact_release_data,
    redact_status,
    viewer_download_id,
)

_TRACKER_ID = "21:https://www.myanonamouse.net/t/123456"


@pytest.fixture(autouse=True)
def _restore_key():
    original = viewer_redaction._public_id_key
    yield
    viewer_redaction._public_id_key = original


def test_public_id_is_stable_opaque_and_keyed():
    viewer_redaction.configure_public_id_key(b"a" * 64)
    first = public_download_id(_TRACKER_ID)

    assert first == public_download_id(_TRACKER_ID)
    assert first.startswith("dl_")
    assert "myanonamouse" not in first
    # Keyed: a plain hash of a guessed tracker URL must not reproduce it.
    viewer_redaction.configure_public_id_key(b"b" * 64)
    assert public_download_id(_TRACKER_ID) != first


def test_only_request_linked_downloads_get_an_opaque_id():
    assert viewer_download_id(_TRACKER_ID, 7) == public_download_id(_TRACKER_ID)
    assert viewer_download_id("direct-task", None) == "direct-task"


def test_an_opaque_id_resolves_only_among_the_given_tasks():
    viewer_id = public_download_id(_TRACKER_ID)

    assert match_public_download_id(viewer_id, ["other", _TRACKER_ID]) == _TRACKER_ID
    assert match_public_download_id(viewer_id, ["other"]) is None
    assert match_public_download_id(_TRACKER_ID, [_TRACKER_ID]) is None


def test_release_data_keeps_only_display_fields():
    redacted = redact_release_data(
        {
            "source": "prowlarr",
            "source_id": _TRACKER_ID,
            "indexer": "MyAnonamouse",
            "info_url": "https://www.myanonamouse.net/t/123456",
            "download_url": "https://prowlarr.local/api/v1/indexer/21/download?apikey=k",
            "protocol": "torrent",
            "extra": {"info_hash": "abc"},
            "title": "Book",
            "format": "m4b",
            "size": "300 MB",
        }
    )

    assert redacted == {"source": "prowlarr", "title": "Book", "format": "m4b", "size": "300 MB"}
    assert redact_release_data(None) is None


def test_download_payload_loses_its_path_and_tracker_id():
    payload = {
        "id": _TRACKER_ID,
        "request_id": 7,
        "preview": f"/api/covers/{_TRACKER_ID}?url=abc",
        "download_path": "/audiobooks/Example Author/Example Title/Example Title.m4b",
    }

    redacted = redact_download_payload(payload)

    viewer_id = public_download_id(_TRACKER_ID)
    assert redacted["id"] == viewer_id
    assert redacted["preview"] == f"/api/covers/{viewer_id}?url=abc"
    assert redacted["download_path"] == "Example Title.m4b"
    assert payload["id"] == _TRACKER_ID  # the caller's copy is untouched


def test_status_is_rekeyed_only_for_request_linked_downloads():
    status = {
        "complete": {
            _TRACKER_ID: {"id": _TRACKER_ID, "request_id": 7},
            "direct-task": {"id": "direct-task", "request_id": None},
        },
        "queued": {},
    }

    redacted = redact_status(status)

    assert set(redacted["complete"]) == {public_download_id(_TRACKER_ID), "direct-task"}
    assert redacted["queued"] == {}


def test_progress_reaches_the_requester_under_the_opaque_id():
    manager = WebSocketManager()
    socketio = MagicMock()
    manager.init_app(MagicMock(), socketio)
    manager._user_rooms["user_5"] = 1

    manager.broadcast_download_progress(_TRACKER_ID, 42.0, "downloading", user_id=5, request_id=7)

    emitted = {call.kwargs["to"]: call.args[1] for call in socketio.emit.call_args_list}
    assert emitted["admins"]["book_id"] == _TRACKER_ID
    assert emitted["user_5"]["book_id"] == public_download_id(_TRACKER_ID)
