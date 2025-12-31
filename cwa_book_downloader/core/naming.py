"""Template-based naming for library organization.

Supports Readarr-style templates with conditional prefix/suffix inclusion.

Examples:
    {Author}/{Title}                    -> "Brandon Sanderson/The Way of Kings"
    {Author}/{Series/}{Title}           -> "Brandon Sanderson/Stormlight Archive/The Way of Kings"
                                        -> "Brandon Sanderson/The Way of Kings" (if no series)
    {Author} - {Title} ({Year})         -> "Brandon Sanderson - The Way of Kings (2010)"
    {SeriesPosition - }{Title}          -> "1 - The Way of Kings" or "The Way of Kings"
"""

import re
from pathlib import Path
from typing import Dict, Optional, Union


# Pattern to match tokens with optional prefix/suffix
# Examples: {Author}, {Series/}, {SeriesPosition - }, {(Year)}
# Group 1: prefix (characters before token name, inside braces)
# Group 2: token name
# Group 3: suffix (characters after token name, inside braces)
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

    # Remove leading/trailing whitespace and dots
    sanitized = sanitized.strip().strip('.')

    # Collapse multiple underscores
    sanitized = re.sub(r'_+', '_', sanitized)

    # Truncate to max length
    return sanitized[:max_length]


def sanitize_path_component(name: str, max_length: int = 245) -> str:
    """Sanitize a string for use as a path component (folder or filename).

    Args:
        name: The string to sanitize
        max_length: Maximum length per component

    Returns:
        Sanitized string safe for filesystem use
    """
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
    """Format series position for display.

    Args:
        position: Series position (can be float for novellas like 1.5)

    Returns:
        Formatted string: "1" for integers, "1.5" for floats
    """
    if position is None:
        return ""

    # Check if it's effectively an integer
    if isinstance(position, float) and position.is_integer():
        return str(int(position))

    return str(position)


def parse_naming_template(
    template: str,
    metadata: Dict[str, Optional[Union[str, int, float]]],
) -> str:
    """Parse a Readarr-style naming template and substitute metadata values.

    The template supports conditional prefix/suffix inclusion:
    - {Token} - simple replacement
    - {Token/} - include trailing slash only if Token has a value (for folders)
    - {Token - } - include trailing " - " only if Token has a value
    - { - Token} - include leading " - " only if Token has a value
    - {(Token)} - include parentheses only if Token has a value

    Args:
        template: Template string with {Token} placeholders
        metadata: Dictionary mapping token names to values
            Expected keys: Author, Title, Year, Series, SeriesPosition, Format

    Returns:
        Processed path string with substitutions made
    """
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

    return result


def build_library_path(
    base_path: str,
    template: str,
    metadata: Dict[str, Optional[Union[str, int, float]]],
    extension: Optional[str] = None,
) -> Path:
    """Build a complete library path from template and metadata.

    Args:
        base_path: Base library directory (e.g., "/books")
        template: Naming template (e.g., "{Author}/{Series/}{Title}")
        metadata: Dictionary with Author, Title, Year, Series, SeriesPosition
        extension: File extension to append (without dot)

    Returns:
        Complete Path object for the destination file

    Raises:
        ValueError: If the resulting path would escape the base directory
    """
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
        full_path = full_path.with_suffix(f'.{ext}')

    return full_path


def same_filesystem(path1: Union[str, Path], path2: Union[str, Path]) -> bool:
    """Check if two paths are on the same filesystem.

    This is required for hardlinking to work.

    Args:
        path1: First path
        path2: Second path

    Returns:
        True if both paths are on the same filesystem, False if different or on error
    """
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
