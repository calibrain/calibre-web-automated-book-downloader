"""Hide release-source details from non-admin viewers (#1418).

A download queued from an approved request carries the release an admin picked.
Its task id is that release's ``source_id``, which for Prowlarr is
``<indexer id>:<guid>``, and a private tracker's guid is a URL into the tracker.
The requester never browsed those releases, so for them a request-linked
download is addressed by an opaque id instead. Request rows keep only the
release fields the activity cards display, and a download path shrinks to the
file name, which the browser download reveals anyway.

Downloads a user queued directly keep their real id: the user picked that
release from search results that already showed its ``source_id``, and the
release list matches its buttons to the queue by that id.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from pathlib import PurePath
from typing import TYPE_CHECKING, Any

from shelfmark.core.request_helpers import normalize_positive_int

if TYPE_CHECKING:
    from collections.abc import Iterable

PUBLIC_DOWNLOAD_ID_PREFIX = "dl_"

# Release fields a non-admin's activity cards display. Everything else (source_id,
# indexer, info and download URLs, torrent attributes) stays server-side.
_RELEASE_DISPLAY_FIELDS = frozenset(
    {
        "author",
        "content_type",
        "extension",
        "filetype",
        "format",
        "language",
        "preview",
        "series_count",
        "series_name",
        "series_position",
        "size",
        "source",
        "source_display_name",
        "subtitle",
        "title",
        "year",
    }
)

# Replaced with a key derived from the app secret at startup, so public ids survive
# restarts. A random key still keeps them unguessable until then.
_public_id_key = os.urandom(32)


def configure_public_id_key(secret: bytes) -> None:
    """Derive the public download id key from the app's persisted secret."""
    global _public_id_key
    _public_id_key = hmac.new(secret, b"shelfmark-public-download-id", hashlib.sha256).digest()


def public_download_id(task_id: str) -> str:
    """Return the opaque, stable id a non-admin sees for ``task_id``.

    Keyed so the task id can't be recovered by hashing guesses: tracker torrent
    ids are sequential, so a plain hash would be easy to reverse.
    """
    digest = hmac.new(_public_id_key, task_id.encode(), hashlib.sha256).hexdigest()
    return f"{PUBLIC_DOWNLOAD_ID_PREFIX}{digest[:32]}"


def is_public_download_id(value: object) -> bool:
    """Whether ``value`` is an opaque id produced by :func:`public_download_id`."""
    return isinstance(value, str) and value.startswith(PUBLIC_DOWNLOAD_ID_PREFIX)


def viewer_download_id(task_id: str, request_id: object) -> str:
    """Return the id a non-admin sees for a download, given its request link."""
    if normalize_positive_int(request_id) is None:
        return task_id
    return public_download_id(task_id)


def match_public_download_id(download_id: str, task_ids: Iterable[str]) -> str | None:
    """Return the task among ``task_ids`` whose public id is ``download_id``."""
    if not is_public_download_id(download_id):
        return None
    for task_id in task_ids:
        if hmac.compare_digest(public_download_id(task_id), download_id):
            return task_id
    return None


def redact_release_data(release_data: object) -> object:
    """Keep only the release fields a non-admin's activity cards display."""
    if not isinstance(release_data, dict):
        return release_data
    return {key: value for key, value in release_data.items() if key in _RELEASE_DISPLAY_FIELDS}


def redact_request_row(row: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of a request row that is safe to show its non-admin owner."""
    if "release_data" not in row:
        return row
    return {**row, "release_data": redact_release_data(row["release_data"])}


def redact_download_payload(payload: dict[str, Any], task_id: str | None = None) -> dict[str, Any]:
    """Return a copy of a download payload that is safe to show its non-admin owner."""
    redacted = dict(payload)
    raw_id = task_id if task_id is not None else payload.get("id")
    if isinstance(raw_id, str) and raw_id:
        viewer_id = viewer_download_id(raw_id, payload.get("request_id"))
        if viewer_id != raw_id:
            redacted["id"] = viewer_id
            # Live queue covers are proxied under the task id as their cache key.
            preview = payload.get("preview")
            if isinstance(preview, str):
                redacted["preview"] = preview.replace(
                    f"/api/covers/{raw_id}?", f"/api/covers/{viewer_id}?"
                )

    download_path = payload.get("download_path")
    if isinstance(download_path, str) and download_path:
        redacted["download_path"] = PurePath(download_path).name or None
    return redacted


def redact_status(status: dict[str, Any]) -> dict[str, Any]:
    """Redact every download in a status dict, re-keying request-linked ones."""
    redacted: dict[str, Any] = {}
    for bucket, entries in status.items():
        if not isinstance(entries, dict):
            redacted[bucket] = entries
            continue
        bucket_entries: dict[str, Any] = {}
        for task_id, payload in entries.items():
            if not isinstance(payload, dict):
                bucket_entries[task_id] = payload
                continue
            redacted_payload = redact_download_payload(payload, task_id=task_id)
            bucket_entries[viewer_download_id(task_id, payload.get("request_id"))] = (
                redacted_payload
            )
        redacted[bucket] = bucket_entries
    return redacted
