"""Which file formats are enabled, by content type.

The one place that reads the Supported Book Formats and Supported Audiobook Formats
settings. It sits in ``core`` and imports nothing heavy, so release sources and
post-processing can both use it without an import cycle.
"""

from __future__ import annotations

import shelfmark.core.config as core_config
from shelfmark.core.utils import (
    DEFAULT_AUDIOBOOK_FORMATS,
    DEFAULT_EBOOK_FORMATS,
    is_audiobook,
    normalize_format_list,
)


def get_supported_formats() -> list[str]:
    """Get current supported book formats from the config singleton."""
    formats = core_config.config.get("SUPPORTED_FORMATS", None)
    return normalize_format_list(formats, DEFAULT_EBOOK_FORMATS)


def get_supported_audiobook_formats() -> list[str]:
    """Get current supported audiobook formats from the config singleton."""
    formats = core_config.config.get("SUPPORTED_AUDIOBOOK_FORMATS", None)
    return normalize_format_list(formats, DEFAULT_AUDIOBOOK_FORMATS)


def get_supported_formats_for(content_type: str | None) -> list[str]:
    """Supported formats for a task's content type: audiobook formats or book formats."""
    if is_audiobook(content_type):
        return get_supported_audiobook_formats()
    return get_supported_formats()
