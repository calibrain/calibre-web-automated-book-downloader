"""Shared utility functions for the Shelfmark."""

import base64
import importlib
import os
import re
import sqlite3
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from shelfmark.core.logger import setup_logger
from shelfmark.core.request_helpers import normalize_optional_text

if TYPE_CHECKING:
    from types import ModuleType

logger = setup_logger(__name__)

# How long an app database connection waits for another writer before it raises. SQLite's
# own default is 5 s, which a start-up migration or a restart reconcile can outlast when a
# backup or a second process holds the file.
DB_BUSY_TIMEOUT_SECONDS = 30


def normalize_http_url(
    url: str | None,
    *,
    default_scheme: str = "http",
    strip_trailing_slash: bool = True,
    allow_special: tuple[str, ...] = (),
) -> str:
    """Normalize a configured HTTP URL for requests and links."""
    if not isinstance(url, str):
        return ""

    normalized = url.strip()
    if not normalized:
        return ""

    if (normalized.startswith('"') and normalized.endswith('"')) or (
        normalized.startswith("'") and normalized.endswith("'")
    ):
        normalized = normalized[1:-1].strip()
        if not normalized:
            return ""

    if allow_special:
        special_map = {value.lower(): value for value in allow_special if isinstance(value, str)}
        special_match = special_map.get(normalized.lower())
        if special_match is not None:
            return special_match

    if normalized.startswith(("/", "./", "../")):
        return normalized

    if "://" not in normalized:
        scheme = default_scheme.strip().rstrip(":/")
        if scheme:
            normalized = f"{scheme}://{normalized}"

    # Strip query string and fragment — mirrors are used as base URLs for
    # constructing search requests; params/fragments on the configured URL
    # produce malformed URLs when paths are appended (issue #999).
    parsed = urlparse(normalized)
    if parsed.query or parsed.fragment:
        normalized = parsed._replace(query="", fragment="").geturl()

    if strip_trailing_slash:
        normalized = normalized.rstrip("/")

    return normalized


_xmlrpc_patch_lock = Lock()
_xmlrpc_patch_applied = False
_XMLRPC_PATCH_ERRORS = (ImportError, AttributeError, OSError, RuntimeError)


def get_hardened_xmlrpc_client() -> ModuleType:
    """Return ``xmlrpc.client`` after best-effort defusedxml monkey patching."""
    global _xmlrpc_patch_applied
    if not _xmlrpc_patch_applied:
        with _xmlrpc_patch_lock:
            if not _xmlrpc_patch_applied:
                try:
                    from defusedxml.xmlrpc import monkey_patch

                    monkey_patch()
                    _xmlrpc_patch_applied = True
                except _XMLRPC_PATCH_ERRORS:
                    # Keep runtime behavior unchanged if defusedxml is unavailable.
                    _xmlrpc_patch_applied = False

    return importlib.import_module("xmlrpc.client")


def normalize_base_path(value: str | None) -> str:
    """Normalize a URL base path for reverse proxy subpath deployments."""
    if not isinstance(value, str):
        return ""

    path = value.strip()
    if not path:
        return ""

    if "://" in path:
        parsed = urlparse(path)
        path = parsed.path or ""

    if not path or path == "/":
        return ""

    if not path.startswith("/"):
        path = "/" + path

    return path.rstrip("/")


def is_audiobook(content_type: str | None) -> bool:
    """Check if content type indicates an audiobook."""
    return bool(content_type and "audiobook" in content_type.lower())


# Every audio format an audiobook can legitimately arrive in, and the single source of
# truth for that list. The settings UI, release-source parsing, archive extraction and
# post-download scanning all derive from it, so a format added here becomes selectable,
# searchable AND downloadable at once. These used to be four hand-maintained copies that
# had drifted apart: the settings UI only offered m4b/mp3/m4a, which meant a FLAC
# audiobook could never be enabled, was silently dropped from every search result, and
# was rejected after download as "format not supported".
#
# "mp4" is here because some trackers (MyAnonamouse in particular) ship AAC audiobooks
# as per-chapter .mp4 files - the same ISO-BMFF container as .m4a/.m4b, just with the
# generic extension. Without it those releases downloaded fine and then failed
# post-processing with "No book files found in download".
AUDIOBOOK_FORMATS = ("m4b", "mp3", "m4a", "mp4", "flac", "ogg", "wma", "aac", "wav", "opus")

# Multi-file audiobooks are almost always distributed as an archive. These are containers
# rather than formats: they are what a *release* looks like, and the formats above are
# what comes out of one after extraction.
ARCHIVE_FORMATS = ("zip", "rar")

# What the Supported Book Formats setting holds until someone changes it. The settings
# registry, post-processing and every release source fall back to this one list.
DEFAULT_EBOOK_FORMATS = ("epub", "mobi", "azw3", "fb2", "djvu", "cbz", "cbr")

# What the Supported Audiobook Formats setting holds until someone changes it: every audio
# format, and the archives a multi-file audiobook usually comes in. Same rule as above.
DEFAULT_AUDIOBOOK_FORMATS = (*AUDIOBOOK_FORMATS, *ARCHIVE_FORMATS)


def normalize_format_list(value: object, default: tuple[str, ...] | list[str]) -> list[str]:
    """Read a configured format list, or a comma or space separated string of them.

    Lower-cases, drops a leading dot and empty items, and returns ``default`` when the
    value is not a list or string or nothing is left of it.
    """
    if isinstance(value, str):
        items: list[Any] = value.replace(",", " ").split()
    elif isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
    else:
        return list(default)
    formats = [fmt for fmt in (str(item).strip().lower().lstrip(".") for item in items) if fmt]
    return formats or list(default)


CONTENT_TYPES = [
    "book (fiction)",
    "book (non-fiction)",
    "book (unknown)",
    "magazine",
    "comic book",
    "audiobook",
    "standards document",
    "musical score",
    "other",
]

# Maps AA content types to their config keys for content-type routing
# Used when AA_CONTENT_TYPE_ROUTING is enabled
_AA_CONTENT_TYPE_TO_CONFIG_KEY = {
    "book (fiction)": "AA_CONTENT_TYPE_DIR_FICTION",
    "book (non-fiction)": "AA_CONTENT_TYPE_DIR_NON_FICTION",
    "book (unknown)": "AA_CONTENT_TYPE_DIR_UNKNOWN",
    "magazine": "AA_CONTENT_TYPE_DIR_MAGAZINE",
    "comic book": "AA_CONTENT_TYPE_DIR_COMIC",
    "audiobook": "AA_CONTENT_TYPE_DIR_AUDIOBOOK",
    "standards document": "AA_CONTENT_TYPE_DIR_STANDARDS",
    "musical score": "AA_CONTENT_TYPE_DIR_MUSICAL_SCORE",
    "other": "AA_CONTENT_TYPE_DIR_OTHER",
}

# Legacy mapping - kept for backwards compatibility during migration
_LEGACY_CONTENT_TYPE_TO_CONFIG_KEY = {
    "book (fiction)": "INGEST_DIR_BOOK_FICTION",
    "book (non-fiction)": "INGEST_DIR_BOOK_NON_FICTION",
    "book (unknown)": "INGEST_DIR_BOOK_UNKNOWN",
    "magazine": "INGEST_DIR_MAGAZINE",
    "comic book": "INGEST_DIR_COMIC_BOOK",
    "audiobook": "INGEST_DIR_AUDIOBOOK",
    "standards document": "INGEST_DIR_STANDARDS_DOCUMENT",
    "musical score": "INGEST_DIR_MUSICAL_SCORE",
    "other": "INGEST_DIR_OTHER",
}

_USER_PLACEHOLDER_PATTERN = re.compile(r"\{user\}", re.IGNORECASE)
_INVALID_USER_PATH_CHARS = re.compile(r'[\\/:*?"<>|]')


def _sanitize_user_for_path(username: str) -> str:
    """Sanitize username for path usage in destination placeholders."""
    sanitized = _INVALID_USER_PATH_CHARS.sub("_", username.strip())
    return sanitized.strip(" .")


def _resolve_destination_username(
    user_id: int | None = None,
    username: str | None = None,
) -> str:
    explicit = str(username or "").strip()
    if explicit:
        return explicit

    if user_id is None:
        return ""

    try:
        from shelfmark.core.user_db import UserDB

        user_db = UserDB(str(Path(os.environ.get("CONFIG_DIR", "/config")) / "users.db"))
        user_db.initialize()
        user = user_db.get_user(user_id=user_id)
        if not user:
            return ""
        return str(user.get("username") or "").strip()
    except ImportError, OSError, sqlite3.Error:
        return ""


def _expand_user_destination_placeholder(
    path_value: str,
    user_id: int | None = None,
    username: str | None = None,
) -> str:
    """Expand `{User}` placeholders in destination paths."""
    if not isinstance(path_value, str):
        return path_value

    if not _USER_PLACEHOLDER_PATTERN.search(path_value):
        return path_value

    resolved_username = _sanitize_user_for_path(
        _resolve_destination_username(user_id=user_id, username=username)
    )
    return _USER_PLACEHOLDER_PATTERN.sub(resolved_username, path_value)


def get_global_destination(*, is_audiobook: bool = False) -> str:
    """Return the admin-configured destination template, before {User} expansion.

    The audiobook destination falls back to the books destination when unset.
    """
    from shelfmark.core.config import config

    if is_audiobook:
        audiobook_dest = str(config.get("DESTINATION_AUDIOBOOK", "") or "").strip()
        if audiobook_dest:
            return audiobook_dest
    return str(config.get("DESTINATION", "") or config.get("INGEST_DIR", "/books") or "").strip()


def resolve_user_destination_override(
    override: object,
    base: str,
    *,
    user_id: int | None = None,
    username: str | None = None,
) -> Path | None:
    """Resolve a per-user destination against the global one it must stay inside.

    ``{User}`` is expanded in both values. A relative override is taken relative
    to the global destination. Returns None when the override is empty, contains
    a ``..`` segment, or does not resolve to the global destination or a path
    below it; callers then fall back to the global destination.
    """
    if not isinstance(override, str) or not override.strip():
        return None

    base_expanded = _expand_user_destination_placeholder(
        base.strip(), user_id=user_id, username=username
    )
    base_path = Path(os.path.normpath(base_expanded))
    if not base_path.is_absolute():
        return None

    candidate = _expand_user_destination_placeholder(
        override.strip(), user_id=user_id, username=username
    )
    if ".." in Path(candidate).parts:
        return None
    candidate_path = Path(os.path.normpath(candidate))
    if not candidate_path.is_absolute():
        candidate_path = base_path / candidate_path
    if not candidate_path.is_relative_to(base_path):
        return None
    return candidate_path


def _get_user_destination_override(
    key: str,
    base: str,
    *,
    user_id: int,
    username: str | None,
) -> Path | None:
    """Return the user's own destination for ``key`` when it stays inside ``base``."""
    from shelfmark.core.config import config

    user_value = config.get(key, "", user_id=user_id)
    if user_value == config.get(key, ""):
        return None
    if not isinstance(user_value, str) or not user_value.strip():
        return None

    resolved = resolve_user_destination_override(
        user_value, base, user_id=user_id, username=username
    )
    if resolved is None:
        logger.warning(
            "Ignoring %s override for user %s: %r is outside the configured destination %r",
            key,
            user_id,
            user_value,
            base,
        )
    return resolved


def get_destination(
    *,
    is_audiobook: bool = False,
    user_id: int | None = None,
    username: str | None = None,
) -> Path:
    """Get base destination directory. Audiobooks fall back to main destination.

    A per-user override is only honoured while it resolves inside the global
    destination; anything else falls back to the global value.
    """
    books_base = get_global_destination()

    if is_audiobook:
        audiobook_base = get_global_destination(is_audiobook=True)
        if user_id is not None:
            user_audiobook = _get_user_destination_override(
                "DESTINATION_AUDIOBOOK", audiobook_base, user_id=user_id, username=username
            )
            if user_audiobook is not None:
                return user_audiobook
        if audiobook_base != books_base:
            return Path(
                _expand_user_destination_placeholder(
                    audiobook_base, user_id=user_id, username=username
                )
            )

    if user_id is not None:
        user_destination = _get_user_destination_override(
            "DESTINATION", books_base, user_id=user_id, username=username
        )
        if user_destination is not None:
            return user_destination

    return Path(
        _expand_user_destination_placeholder(books_base, user_id=user_id, username=username)
    )


def get_aa_content_type_dir(content_type: str | None = None) -> Path | None:
    """Get override directory for AA content-type routing if configured."""
    from shelfmark.core.config import config

    # Check if content-type routing is enabled (new or legacy setting)
    if not config.get("AA_CONTENT_TYPE_ROUTING", False) and not config.get(
        "USE_CONTENT_TYPE_DIRECTORIES", False
    ):
        return None

    if not content_type:
        return None

    content_type_lower = content_type.lower().strip()

    # Try new AA-specific config keys first, then legacy keys
    for mapping in (_AA_CONTENT_TYPE_TO_CONFIG_KEY, _LEGACY_CONTENT_TYPE_TO_CONFIG_KEY):
        config_key = mapping.get(content_type_lower)
        if config_key:
            custom_dir = _coerce_config_path(config.get(config_key, ""))
            if custom_dir is not None:
                return custom_dir

    return None


def get_ingest_dir(content_type: str | None = None) -> Path:
    """Return the legacy ingest directory for a content type."""
    from shelfmark.core.config import config

    # Check new DESTINATION setting first, then legacy INGEST_DIR
    default_ingest_dir = _coerce_config_path(config.get("DESTINATION", "")) or _coerce_config_path(
        config.get("INGEST_DIR", "/books")
    )
    if default_ingest_dir is None:
        default_ingest_dir = Path("/books")

    if not content_type:
        return default_ingest_dir

    # Check for content-type override
    override_dir = get_aa_content_type_dir(content_type)
    if override_dir:
        return override_dir

    return default_ingest_dir


def transform_cover_url(cover_url: str | None, cache_id: str) -> str | None:
    """Transform external cover URL to local proxy URL when caching is enabled."""
    if not cover_url:
        return cover_url

    # Skip if already a local URL (starts with /)
    if cover_url.startswith("/"):
        return cover_url

    # Check if cover caching is enabled
    from shelfmark.config.env import is_covers_cache_enabled

    if not is_covers_cache_enabled():
        return cover_url

    from shelfmark.core.config import config as app_config

    # Encode the original URL and create a proxy URL
    encoded_url = base64.urlsafe_b64encode(cover_url.encode()).decode()
    base_path = normalize_base_path(normalize_optional_text(app_config.get("URL_BASE", "")))
    if base_path:
        return f"{base_path}/api/covers/{cache_id}?url={encoded_url}"
    return f"/api/covers/{cache_id}?url={encoded_url}"


def _coerce_config_path(value: object) -> Path | None:
    if isinstance(value, os.PathLike):
        path_value = os.fspath(value)
        if isinstance(path_value, str):
            normalized = path_value.strip()
            if normalized:
                return Path(normalized)
        return None

    if not isinstance(value, str):
        return None

    normalized = value.strip()
    if not normalized:
        return None

    return Path(normalized)
