"""Library ownership providers.

Each provider indexes one library the user already owns into matchable
``LibraryEntry`` rows. ``shelfmark.core.library_index`` caches those rows per provider
and answers whether a requested book is already on the shelf.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import Mapping


@dataclass(frozen=True)
class LibraryEntry:
    """Normalized, matchable representation of one library item."""

    tokens: frozenset[str]
    isbns: frozenset[str]
    asins: frozenset[str]
    # (metadata provider name, id in that provider) pairs, e.g. ("hardcover", "446681").
    external_ids: frozenset[tuple[str, str]] = frozenset()
    # Title tokens alone, without the author and series that `tokens` also carries.
    # The matcher needs them to tell "the shelf title says more than the search did"
    # from "the shelf entry merely has an author", which decides whether a shorter
    # search title is the same book or a different one in the same series.
    title_tokens: frozenset[str] = frozenset()
    # Series and author words. Shelf titles often bake these in ("Alex Cross 25:
    # Cross Kill"), so the matcher must not read them as naming a different work.
    context_tokens: frozenset[str] = frozenset()


class LibraryProvider(Protocol):
    """One owned-book library the ownership check can consult."""

    name: str
    display_name: str
    content_types: frozenset[str]

    def is_enabled(self) -> bool: ...

    def fetch_entries(self) -> list[LibraryEntry]: ...

    def describe(self) -> str: ...

    def fingerprint(self) -> object | None:
        """Cheap change token (e.g. a file mtime); None when the library has none."""
        ...


def setting(
    config: Any, key: str, default: object = "", overrides: Mapping[str, Any] | None = None
) -> object:
    """Read ``key`` from ``config``, preferring a non-empty unsaved form value in ``overrides``."""
    if overrides:
        value = overrides.get(key)
        if value not in (None, ""):
            return value
    return config.get(key, default)


def all_providers(overrides: Mapping[str, Any] | None = None) -> list[LibraryProvider]:
    """Concrete providers, in a fixed order. ``overrides`` are unsaved form values.

    Adding a library means writing a module with the :class:`LibraryProvider` shape and
    listing it here. Nothing above this function knows which libraries exist.
    """
    from shelfmark.core.library_providers.audiobookshelf import AudiobookshelfLibrary
    from shelfmark.core.library_providers.calibre import CalibreLibrary

    return [CalibreLibrary(overrides), AudiobookshelfLibrary(overrides)]
