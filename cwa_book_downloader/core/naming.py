"""Template-based naming for library organization."""

import re
from pathlib import Path
from typing import Dict, Optional, Union


TOKEN_PATTERN = re.compile(
    r'\{([- ._/\[(]*)'   # prefix: space, dash, dot, underscore, slash, brackets
    r'([A-Za-z]+)'        # token name
    r'([- ._/\])]*)\}'    # suffix: space, dash, dot, underscore, slash, brackets
)

# Characters that are invalid in filenames on various filesystems
INVALID_FILENAME_CHARS = re.compile(r'[\\:*?"<>|]')

# Characters invalid in path components (allow forward slash for folder separation)
INVALID_PATH_CHARS = re.compile(r'[\\:*?"<>|]')


def sanitize_filename(name: str, max_length: int = 245) -> str:
    """Sanitize a string for use as a filename.

    Args:
        name: The string to sanitize
        max_length: Maximum length (default 245 to leave room for extension)

    Returns:
        Sanitized string safe for filesystem use
    """
    if not name:
        return ""

    # Replace invalid characters with underscore
    sanitized = INVALID_FILENAME_CHARS.sub('_', name)

    # Remove leading/trailing whitespace and dots (strip both together to handle ". file .")
    sanitized = re.sub(r'^[\s.]+|[\s.]+$', '', sanitized)

    # Collapse multiple underscores
    sanitized = re.sub(r'_+', '_', sanitized)

    # Truncate to max length
    return sanitized[:max_length]


def sanitize_path_component(name: str, max_length: int = 245) -> str:
    if not name:
        return ""

    # Replace invalid characters with underscore
    sanitized = INVALID_PATH_CHARS.sub('_', name)

    # Remove leading/trailing whitespace and dots
    sanitized = sanitized.strip().strip('.')

    # Collapse multiple underscores
    sanitized = re.sub(r'_+', '_', sanitized)

    # Truncate to max length
    return sanitized[:max_length]


def format_series_position(position: Optional[Union[int, float]]) -> str:
    if position is None:
        return ""

    # Check if it's effectively an integer
    if isinstance(position, float) and position.is_integer():
        return str(int(position))

    return str(position)


# Pads numbers to 9 digits for natural sorting (e.g., "Part 2" -> "Part 000000002")
PAD_NUMBERS_PATTERN = re.compile(r'\d+')


def natural_sort_key(path: Union[str, Path]) -> str:
    """Generate a sort key with padded numbers for natural sorting."""
    filename = Path(path).name.lower()
    return PAD_NUMBERS_PATTERN.sub(lambda m: m.group().zfill(9), filename)


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
    metadata: Dict[str, Optional[Union[str, int, float]]],
) -> str:
    if not template:
        return ""

    # Normalize metadata keys to lowercase for case-insensitive matching
    normalized = {k.lower(): v for k, v in metadata.items()}

    def replace_token(match: re.Match) -> str:
        prefix = match.group(1)
        token_name = match.group(2).lower()
        suffix = match.group(3)

        # Get the value for this token
        value = normalized.get(token_name)

        # Special handling for series position
        if token_name == 'seriesposition':
            value = format_series_position(value)

        # Convert to string
        if value is None:
            value = ""
        else:
            value = str(value).strip()

        # If value is empty, return empty string (no prefix/suffix)
        if not value:
            return ""

        # Sanitize the value
        # If suffix contains a slash, this is meant to be a folder component
        if '/' in suffix:
            value = sanitize_path_component(value)
        else:
            value = sanitize_filename(value)

        return f"{prefix}{value}{suffix}"

    # Replace all tokens
    result = TOKEN_PATTERN.sub(replace_token, template)

    # Clean up any double slashes that might result from empty tokens
    result = re.sub(r'/+', '/', result)

    # Remove leading/trailing slashes
    result = result.strip('/')

    # Clean up any orphaned separators (e.g., " - " at start/end, or " -  - ")
    result = re.sub(r'^[\s\-_.]+', '', result)
    result = re.sub(r'[\s\-_.]+$', '', result)
    result = re.sub(r'(\s*-\s*){2,}', ' - ', result)

    # Clean up empty parentheses/brackets
    result = re.sub(r'\(\s*\)', '', result)
    result = re.sub(r'\[\s*\]', '', result)

    # Final trim of any trailing separators left after cleanup
    result = re.sub(r'[\s\-_.]+$', '', result)

    return result


def build_library_path(
    base_path: str,
    template: str,
    metadata: Dict[str, Optional[Union[str, int, float]]],
    extension: Optional[str] = None,
) -> Path:
    relative = parse_naming_template(template, metadata)

    if not relative:
        # Fallback to title if template produces empty result
        title = metadata.get('Title') or metadata.get('title') or 'Unknown'
        relative = sanitize_filename(str(title))

    # Remove any path traversal attempts
    relative = relative.replace('..', '')

    base = Path(base_path).resolve()
    full_path = (base / relative).resolve()

    # Verify the path is within the base directory
    try:
        full_path.relative_to(base)
    except ValueError:
        raise ValueError(f"Path traversal detected: template would escape library directory")

    if extension:
        ext = extension.lstrip('.')
        # Don't use with_suffix() - it replaces everything after the first dot
        # e.g., "2.5 - Title" would become "2.epub" instead of "2.5 - Title.epub"
        full_path = Path(f"{full_path}.{ext}")

    return full_path


def same_filesystem(path1: Union[str, Path], path2: Union[str, Path]) -> bool:
    import os

    path1 = Path(path1)
    path2 = Path(path2)

    def get_device(p: Path) -> Optional[int]:
        """Get device ID, walking up to find existing ancestor."""
        try:
            while not p.exists():
                p = p.parent
                if p == p.parent:  # Reached root
                    break
            return os.stat(p).st_dev
        except (OSError, PermissionError):
            return None

    dev1 = get_device(path1)
    dev2 = get_device(path2)

    if dev1 is None or dev2 is None:
        return False  # Can't determine, assume different filesystems (safe fallback)

    return dev1 == dev2
