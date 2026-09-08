"""Template-based naming for library organization."""

import re
from pathlib import Path
from typing import TYPE_CHECKING

from shelfmark.core.languages import LANGUAGE_PLACEHOLDERS, normalize_language
from shelfmark.core.logger import setup_logger

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = setup_logger(__name__)


# Known variable tokens, sorted longest-first to avoid partial matches
# e.g., "SeriesPosition" must match before "Series", "FirstAuthor" before "Author"
KNOWN_TOKENS = [
    "seriesposition",
    "primarytitle",
    "originalname",
    "firstauthor",
    "partnumber",
    "language",
    "subtitle",
    "author",
    "series",
    "title",
    "year",
    "user",
]

# Authors reach naming already joined as "First Author, Second Author, ...".
# {FirstAuthor} keeps only the first entry. A single name written "Last, First"
# is split on the comma too and renders as "Last" -- the source metadata does
# not mark which form it is (see #930).
AUTHOR_LIST_SEPARATOR = re.compile(r"\s*[,;]\s*")

# Match any {...} block for template parsing
BRACE_PATTERN = re.compile(r"\{([^}]+)\}")

# Characters that are invalid in filenames on various filesystems
INVALID_CHARS = re.compile(r'[\\/:*?"<>|]')


def _sanitize(name: str | None, max_length: int = 245) -> str:
    """Sanitize a string for filesystem use."""
    if not name:
        return ""

    sanitized = INVALID_CHARS.sub("_", name)
    sanitized = re.sub(r"^[\s.]+|[\s.]+$", "", sanitized)  # Strip whitespace and dots
    sanitized = re.sub(r"_+", "_", sanitized)  # Collapse underscores
    return sanitized[:max_length]


def sanitize_filename(name: str | None, max_length: int = 245) -> str:
    """Sanitize a string for use as a filename or path component."""
    return _sanitize(name, max_length)


# Alias for backwards compatibility
sanitize_path_component = sanitize_filename


def first_author(value: object) -> str:
    """Return the first entry from an author string joined with ',' or ';'."""
    text = " ".join(str(value or "").split())
    if not text:
        return ""
    return AUTHOR_LIST_SEPARATOR.split(text, maxsplit=1)[0].strip()


def format_series_position(position: str | float | None) -> str:
    """Format a series position for naming templates."""
    if position is None:
        return ""

    # Display as integer if whole number
    if isinstance(position, float) and position.is_integer():
        return str(int(position))

    return str(position)


def normalize_language_code(language: str | None) -> str:
    """Resolve a release language to the single spelling used in a path.

    Sources report the same language in different shapes: "en", "eng", "English".
    All of them have to collapse to one code, or the editions they identify end
    up in separate folders, which is the collision this token exists to prevent.
    Placeholder values render empty so `{ (Language)}` disappears entirely
    rather than labelling a folder "(unknown)".

    A language the bundled data does not know is kept, casefolded, rather than
    dropped: it still separates editions, and it cannot collide with a resolved
    code precisely because nothing resolves it.
    """
    if not language:
        return ""

    resolved = normalize_language(language)
    if resolved is not None:
        return resolved

    normalized = " ".join(str(language).split()).strip().casefold()
    if normalized in LANGUAGE_PLACEHOLDERS:
        return ""

    return normalized


def derive_primary_title(title: str | None, subtitle: str | None) -> str:
    """Return the title without an explicit subtitle suffix when possible."""
    title_value = " ".join(str(title or "").split()).strip()
    if not title_value:
        return ""

    subtitle_value = " ".join(str(subtitle or "").split()).strip()
    if not subtitle_value:
        return title_value

    pattern = rf"^(?P<primary>.+?)(?:\s*:\s*|\s+-\s+){re.escape(subtitle_value)}$"
    match = re.match(pattern, title_value, flags=re.IGNORECASE)
    if not match:
        return title_value

    primary = match.group("primary").strip()
    return primary or title_value


# Pads numbers to 9 digits for natural sorting (e.g., "Part 2" -> "Part 000000002")
PAD_NUMBERS_PATTERN = re.compile(r"\d+")


def natural_sort_key(path: str | Path) -> str:
    """Generate a sort key with padded numbers for natural sorting."""
    return PAD_NUMBERS_PATTERN.sub(lambda m: m.group().zfill(9), str(path).lower())


def assign_part_numbers(
    files: list[Path],
    zero_pad_width: int = 2,
) -> list[tuple[Path, str]]:
    """Sort files naturally and assign sequential part numbers (1, 2, 3...)."""
    if not files:
        return []

    sorted_files = sorted(files, key=natural_sort_key)
    return [
        (file_path, str(part_num).zfill(zero_pad_width))
        for part_num, file_path in enumerate(sorted_files, start=1)
    ]


def parse_naming_template(
    template: str,
    metadata: Mapping[str, str | int | float | None],
    *,
    allow_path_separators: bool = True,
) -> str:
    """Render a naming template with Shelfmark metadata placeholders."""
    if not template:
        return ""

    # Normalize metadata keys to lowercase for case-insensitive matching
    normalized = {k.lower(): v for k, v in metadata.items()}

    def find_placeholder(content: str) -> tuple[str | None, int]:
        content_lower = content.lower()
        for placeholder_name in KNOWN_TOKENS:
            idx = content_lower.find(placeholder_name)
            if idx != -1:
                return placeholder_name, idx
        return None, -1

    def placeholder_value(placeholder_name: str) -> str:
        value = normalized.get(placeholder_name)
        if placeholder_name == "seriesposition":
            value = format_series_position(value)
        elif placeholder_name == "firstauthor" and not value:
            value = first_author(normalized.get("author"))
        if value is None:
            return ""
        return str(value).strip()

    def render_block(content: str) -> str | None:
        placeholder_name, idx = find_placeholder(content)
        if placeholder_name is None:
            return None

        prefix = content[:idx]
        suffix = content[idx + len(placeholder_name) :]
        value = placeholder_value(placeholder_name)
        if not value:
            return ""

        if not allow_path_separators:
            value = value.replace("/", "_")
        value = sanitize_filename(value)
        return f"{prefix}{value}{suffix}"

    # Process brace blocks in order so we can support conditional literal blocks like:
    # { - Part }{PartNumber}
    matches = list(BRACE_PATTERN.finditer(template))
    if not matches:
        result = template
    else:
        parts: list[str] = []
        cursor = 0
        for idx, match in enumerate(matches):
            parts.append(template[cursor : match.start()])
            content = match.group(1)
            rendered = render_block(content)

            if rendered is not None:
                parts.append(rendered)
            else:
                conditional_literal = False
                include_literal = False
                if idx + 1 < len(matches) and match.end() == matches[idx + 1].start():
                    next_content = matches[idx + 1].group(1)
                    next_placeholder_name, _next_idx = find_placeholder(next_content)
                    if next_placeholder_name is not None:
                        conditional_literal = True
                        include_literal = bool(placeholder_value(next_placeholder_name))
                if include_literal:
                    parts.append(content)
                elif not conditional_literal and re.search(r"\s", content):
                    # Preserve blocks that look like literal text, but treat bare unknown
                    # placeholders as missing variables.
                    parts.append(match.group(0))

            cursor = match.end()

        parts.append(template[cursor:])
        result = "".join(parts)

    # Clean up any double slashes that might result from empty tokens
    result = re.sub(r"/+", "/", result)

    # Remove leading/trailing slashes
    result = result.strip("/")

    # Clean up any orphaned separators (e.g., " - " at start/end, or " -  - ")
    result = re.sub(r"^[\s\-_.]+", "", result)
    result = re.sub(r"[\s\-_.]+$", "", result)
    result = re.sub(r"(\s*-\s*){2,}", " - ", result)

    # Clean up empty parentheses/brackets
    result = re.sub(r"\(\s*\)", "", result)
    result = re.sub(r"\[\s*\]", "", result)

    # Final trim of any trailing separators left after cleanup
    return re.sub(r"[\s\-_.]+$", "", result)


def build_library_path(
    base_path: str,
    template: str,
    metadata: Mapping[str, str | int | float | None],
    extension: str | None = None,
) -> Path:
    """Build a final library path from a template and metadata."""
    relative = parse_naming_template(template, metadata, allow_path_separators=True)

    if not relative:
        # Fallback to title if template produces empty result
        title = metadata.get("Title") or metadata.get("title") or "Unknown"
        relative = sanitize_filename(str(title))

    # Remove any path traversal attempts
    relative = relative.replace("..", "")

    base = Path(base_path).resolve()
    full_path = (base / relative).resolve()

    # Verify the path is within the base directory
    try:
        full_path.relative_to(base)
    except ValueError as exc:
        msg = "Path traversal detected: template would escape library directory"
        raise ValueError(msg) from exc

    if extension:
        ext = extension.lstrip(".")
        # Don't use with_suffix() - it replaces everything after the first dot
        # e.g., "2.5 - Title" would become "2.epub" instead of "2.5 - Title.epub"
        full_path = Path(f"{full_path}.{ext}")

    return full_path


def same_filesystem(path1: str | Path, path2: str | Path) -> bool:
    """Check if two paths are on the same filesystem."""
    path1 = Path(path1)
    path2 = Path(path2)

    def get_device(p: Path) -> int | None:
        try:
            while not p.exists():
                p = p.parent
                if p == p.parent:
                    break
            return p.stat().st_dev
        except (OSError, PermissionError) as e:
            logger.debug("Cannot stat %s: %s", p, e)
            return None

    dev1 = get_device(path1)
    dev2 = get_device(path2)

    if dev1 is None or dev2 is None:
        logger.warning("Cannot determine filesystem for hardlink check, falling back to copy")
        return False

    return dev1 == dev2
