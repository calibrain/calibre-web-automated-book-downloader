"""Post-download processing policy.

This module holds configuration-driven *policy* decisions that are shared across
post-download processing components, but are not specific to archive extraction.

Examples:
- Which file formats are enabled
- How files should be organized (none/rename/organize)
- Which naming templates to use

Implementation note:
Keep this module free of dependencies on archive extraction mechanics to avoid
circular imports (`archive` is used by the pipeline).

"""

from __future__ import annotations

import shelfmark.core.config as core_config
from shelfmark.core.formats import (
    get_supported_audiobook_formats,
    get_supported_formats,
    get_supported_formats_for,
)


def _config_text(value: object) -> str:
    if isinstance(value, str):
        return value
    return ""


__all__ = [
    "get_supported_audiobook_formats",
    "get_supported_formats",
    "get_supported_formats_for",
]


def get_file_organization(*, is_audiobook: bool) -> str:
    """Get the file organization mode for the content type."""
    key = "FILE_ORGANIZATION_AUDIOBOOK" if is_audiobook else "FILE_ORGANIZATION"
    mode = _config_text(core_config.config.get(key, "rename")).strip().lower()
    return mode if mode in ("none", "rename", "rename_and_group", "organize") else "rename"


def get_word_separator() -> str:
    """Get the configured word separator for naming template values.

    Replaces whitespace inside each placeholder's rendered value (e.g. "Conan
    Doyle" -> "Conan.Doyle"). The setting holds the separator character
    directly (e.g. "." or "_"); empty means a plain space, which leaves
    values unchanged.
    """
    return _config_text(core_config.config.get("NAMING_WORD_SEPARATOR", "")) or " "


def get_template(*, is_audiobook: bool, organization_mode: str) -> str:
    """Get the template for the content type and organization mode."""
    if is_audiobook:
        if organization_mode == "organize":
            key = "TEMPLATE_AUDIOBOOK_ORGANIZE"
        else:
            key = "TEMPLATE_AUDIOBOOK_RENAME"
    else:
        key = "TEMPLATE_ORGANIZE" if organization_mode == "organize" else "TEMPLATE_RENAME"

    template = _config_text(core_config.config.get(key, ""))

    if not template:
        if organization_mode == "organize":
            return "{Author}/{Title} ({Year})"
        return "{Author} - {Title} ({Year})"

    return template
