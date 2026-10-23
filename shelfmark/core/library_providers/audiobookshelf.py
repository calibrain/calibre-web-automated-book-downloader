"""Audiobookshelf library provider.

Fetches the libraries of an Audiobookshelf (ABS) server over its REST API, with title,
author, ISBN and ASIN per item. ABS metadata is often messy (titles are raw folder
names, the author is frequently empty), so each item is tokenized from title, author and
folder path.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

import requests

from shelfmark.core.config import config as app_config
from shelfmark.core.library_providers import LibraryEntry, setting
from shelfmark.core.text_match import isbn_variants, tokens
from shelfmark.core.utils import normalize_http_url

if TYPE_CHECKING:
    from collections.abc import Mapping

_REQUEST_TIMEOUT = 15
_ITEMS_PAGE_LIMIT = 500

# "Alex Cross 02: ", "DCC 1: ", "Reacher 19.5: ": a short label, a number and a colon.
_SERIES_PREFIX = re.compile(r"^(?P<label>[^\d:]{1,40}?)\s+(?P<number>\d+(?:\.\d+)?)\s*:\s*(?=\S)")


def _config(overrides: Mapping[str, Any] | None = None) -> tuple[bool, str, str, list[str]]:
    enabled = bool(setting(app_config, "LIBRARY_CHECK_AUDIOBOOKSHELF_ENABLED", False, overrides))
    url = normalize_http_url(str(setting(app_config, "AUDIOBOOKSHELF_URL", "", overrides) or ""))
    token = str(setting(app_config, "AUDIOBOOKSHELF_TOKEN", "", overrides) or "").strip()
    lib_ids_raw = str(setting(app_config, "AUDIOBOOKSHELF_LIBRARY_IDS", "", overrides) or "")
    lib_ids = [s.strip() for s in lib_ids_raw.split(",") if s.strip()]
    return enabled, url, token, lib_ids


def _session(url: str, token: str) -> requests.Session:
    from shelfmark.download.network import get_ssl_verify

    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {token}"})
    # The URL lets CERTIFICATE_VALIDATION=disabled_local skip verification for a LAN
    # server, which is where Audiobookshelf usually lives.
    session.verify = get_ssl_verify(url)
    return session


def _first_series_name(value: object) -> str | None:
    """The first series in Audiobookshelf's ``seriesName`` ("Name #1, Other Name #3")."""
    first = str(value or "").split(", ", 1)[0].strip()
    name = re.sub(r"\s+#\S+$", "", first).strip()
    return name or None


def _item_to_entry(item: dict[str, Any]) -> LibraryEntry:
    media = item.get("media") or {}
    meta = media.get("metadata") or {}
    title = meta.get("title") or ""
    author = meta.get("authorName") or ""
    rel_path = item.get("relPath") or ""

    context_tok = set(tokens(author))
    # A book in a series is often titled "Alex Cross 02: Kiss the Girls" so players that
    # ignore the series number still show the order. Those leading words name the series,
    # not another work, so they go with the author as context.
    prefix = _SERIES_PREFIX.match(title) if _first_series_name(meta.get("seriesName")) else None
    if prefix:
        context_tok |= set(tokens(f"{prefix['label']} {prefix['number']}"))
        title = title[prefix.end() :]
    title_tok = set(tokens(title))
    tok = title_tok | context_tok | set(tokens(rel_path))

    asin = str(meta.get("asin") or "").strip().upper()
    return LibraryEntry(
        frozenset(tok),
        isbn_variants(meta.get("isbn")),
        frozenset({asin} if asin else set()),
        frozenset(),
        frozenset(title_tok),
        frozenset(context_tok),
    )


def _fetch_library_entries(url: str, token: str, lib_ids: list[str]) -> list[LibraryEntry]:
    session = _session(url, token)

    resp = session.get(f"{url}/api/libraries", timeout=_REQUEST_TIMEOUT)
    resp.raise_for_status()
    libraries = resp.json().get("libraries", [])

    selected: list[str] = []
    for lib in libraries:
        lib_id = lib.get("id")
        if not lib_id:
            continue
        if lib_ids:
            if lib_id in lib_ids:
                selected.append(lib_id)
        elif lib.get("mediaType") == "book":
            selected.append(lib_id)

    entries: list[LibraryEntry] = []
    for lib_id in selected:
        page = 0
        while True:
            resp = session.get(
                f"{url}/api/libraries/{lib_id}/items",
                params={"limit": _ITEMS_PAGE_LIMIT, "page": page},
                timeout=_REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results") if isinstance(data, dict) else None
            if not isinstance(results, list):
                # An answer without an item list is not an empty library.
                msg = f"Audiobookshelf's item list for library {lib_id} has an unexpected shape"
                raise TypeError(msg)
            total = int(data.get("total", 0) or 0)
            if page == 0 and not results and total > 0:
                msg = f"Audiobookshelf reports {total} items in library {lib_id} but listed none"
                raise ValueError(msg)
            entries.extend(_item_to_entry(item) for item in results)
            page += 1
            if not results or page * _ITEMS_PAGE_LIMIT >= total:
                break

    return entries


def search_books(
    title: str,
    author: str,
    provider: str,
    *,
    timeout: float = _REQUEST_TIMEOUT,
    overrides: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Ask Audiobookshelf's metadata search (``GET /api/search/books``) about a book.

    ``provider`` names one of the server's metadata providers: ``audible`` is the US
    store, ``audible.uk`` the UK one. The result is the server's list of matches, best
    first, each with at least ``title``, ``author`` and (for Audible) ``asin``. Raises
    ValueError without a URL and token, and the requests error when the server or the
    store fails; the caller decides what an error means.
    """
    _enabled, url, token, _lib_ids = _config(overrides)
    if not url or not token:
        raise ValueError("Set the Audiobookshelf URL and token first.")
    resp = _session(url, token).get(
        f"{url}/api/search/books",
        params={"title": title, "author": author, "provider": provider},
        timeout=timeout,
    )
    resp.raise_for_status()
    payload = resp.json()
    if not isinstance(payload, list):
        msg = "Audiobookshelf answered the book search with something other than a list"
        raise ValueError(msg)  # noqa: TRY004 - a bad answer from the server, not a bad argument
    return [item for item in payload if isinstance(item, dict)]


class AudiobookshelfLibrary:
    """Audiobook ownership via the Audiobookshelf REST API."""

    name = "audiobookshelf"
    display_name = "Audiobookshelf"
    content_types = frozenset({"audiobook"})

    def __init__(self, overrides: Mapping[str, Any] | None = None) -> None:
        self._overrides = overrides

    def is_enabled(self) -> bool:
        enabled, url, token, _lib_ids = _config(self._overrides)
        return enabled and bool(url) and bool(token)

    def describe(self) -> str:
        _enabled, url, _token, _lib_ids = _config(self._overrides)
        return f"Audiobookshelf at {url}" if url else "Audiobookshelf"

    def fingerprint(self) -> None:
        return None

    def fetch_entries(self) -> list[LibraryEntry]:
        _enabled, url, token, lib_ids = _config(self._overrides)
        if not url or not token:
            msg = "Set the Audiobookshelf URL and token first."
            raise ValueError(msg)
        return _fetch_library_entries(url, token, lib_ids)
