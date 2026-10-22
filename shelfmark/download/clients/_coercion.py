"""Shared coercion helpers for download client config and option values."""

from shelfmark.core.utils import is_audiobook, normalize_http_url


def config_text(value: object, default: str = "") -> str:
    """Coerce config values to strings without losing explicit empty defaults."""
    if value is None:
        return default
    if isinstance(value, str):
        return value
    return str(value)


def resolve_download_dir(
    *,
    separate: bool,
    shared_dir: str,
    book_dir: str,
    audiobook_dir: str,
    content_type: object,
) -> str:
    """Pick the client save directory; empty means use the client's own default."""
    if not separate:
        return shared_dir
    if is_audiobook(content_type if isinstance(content_type, str) else None):
        return audiobook_dir
    return book_dir


def normalize_http_config_url(value: object, *, require_string: bool = False) -> str:
    """Normalize HTTP(S) config URLs with optional strict string-only input handling."""
    if require_string and not isinstance(value, str):
        return ""
    return normalize_http_url(config_text(value))


def coerce_optional_int(value: object) -> int | None:
    """Convert optional numeric inputs to ints."""
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        return int(value)
    msg = f"Expected int-compatible value, got {type(value).__name__}"
    raise TypeError(msg)


def coerce_optional_float(value: object) -> float | None:
    """Convert optional numeric inputs to floats."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        return float(value)
    msg = f"Expected float-compatible value, got {type(value).__name__}"
    raise TypeError(msg)
