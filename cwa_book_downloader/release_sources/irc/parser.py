"""Search results file parser.

Parses the text files sent via DCC that contain search results.
"""

import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from cwa_book_downloader.core.config import config
from cwa_book_downloader.core.logger import setup_logger

logger = setup_logger(__name__)

# All recognized formats for parsing IRC result lines.
# This comprehensive list is used to identify file extensions in results.
# User's configured formats are used separately for filtering.
# Note: IRC source currently only supports ebooks, but audiobook formats
# are included for future-proofing and format detection consistency.
ALL_RECOGNIZED_FORMATS = {
    # Ebook formats
    'epub', 'mobi', 'azw3', 'azw', 'pdf', 'doc', 'docx',
    'html', 'htm', 'rtf', 'txt', 'lit', 'fb2', 'djvu',
    'cbr', 'cbz', 'cdr', 'jpg', 'rar', 'zip',
    # Audiobook formats
    'm4b', 'mp3', 'm4a', 'flac', 'ogg', 'wma', 'aac', 'wav', 'opus'
}


def _get_supported_formats() -> set[str]:
    """Get user's configured supported formats from settings."""
    formats = config.get("SUPPORTED_FORMATS", ["epub", "mobi", "azw3", "fb2", "djvu", "cbz", "cbr"])
    if isinstance(formats, str):
        return {fmt.strip().lower() for fmt in formats.split(",") if fmt.strip()}
    return {fmt.lower() for fmt in formats}

# Regex to parse result lines
# Format: !Server Author - Title.format ::INFO:: size
RESULT_LINE_REGEX = re.compile(
    r'^!(\S+)\s+'           # !ServerName
    r'(.+?)\s+-\s+'         # Author Name -
    r'(.+?)\.(\w+)'         # Title.format
    r'(?:\s+::INFO::\s*(.+?))?'  # Optional ::INFO:: metadata
    r'(?:\s+::HASH::\s*(\S+))?'  # Optional ::HASH::
    r'\s*$'
)

# Simpler fallback pattern
SIMPLE_RESULT_REGEX = re.compile(
    r'^!(\S+)\s+(.+)$'  # !Server everything_else
)


@dataclass
class SearchResult:
    """Parsed search result entry."""
    server: str           # Bot name (without !)
    author: str           # Author name
    title: str            # Book title
    format: str           # File format (epub, mobi, etc)
    size: Optional[str]   # Human-readable size
    full_line: str        # Original line for download request

    @property
    def download_request(self) -> str:
        """The string to send to IRC to request this book."""
        return self.full_line.strip()

    @property
    def display_name(self) -> str:
        """Human-readable display name."""
        return f"{self.author} - {self.title}"


def parse_result_line(line: str) -> Optional[SearchResult]:
    """Parse a single search result line. Returns None if unparseable."""
    line = line.strip()

    # Must start with !
    if not line.startswith('!'):
        return None

    # Try detailed pattern first
    match = RESULT_LINE_REGEX.match(line)
    if match:
        server, author, title, fmt, size, _ = match.groups()
        return SearchResult(
            server=server,
            author=author.strip(),
            title=title.strip(),
            format=fmt.lower(),
            size=size.strip() if size else None,
            full_line=line,
        )

    # Fallback: simpler parsing
    match = SIMPLE_RESULT_REGEX.match(line)
    if match:
        server, rest = match.groups()

        # Try to extract format from the line
        fmt = None
        for known_fmt in ALL_RECOGNIZED_FORMATS:
            if f'.{known_fmt}' in rest.lower():
                fmt = known_fmt
                break

        # Try to split author - title
        if ' - ' in rest:
            parts = rest.split(' - ', 1)
            author = parts[0].strip()
            title_part = parts[1].strip() if len(parts) > 1 else rest
        else:
            author = "Unknown"
            title_part = rest

        # Extract size if present
        size = None
        if '::INFO::' in title_part:
            title_part, info = title_part.split('::INFO::', 1)
            size = info.split('::')[0].strip()

        # Clean up title (remove extension)
        title = title_part
        for known_fmt in ALL_RECOGNIZED_FORMATS:
            title = re.sub(rf'\.{known_fmt}\b', '', title, flags=re.IGNORECASE)

        return SearchResult(
            server=server,
            author=author,
            title=title.strip(),
            format=fmt or 'unknown',
            size=size,
            full_line=line,
        )

    logger.debug(f"Could not parse line: {line[:80]}...")
    return None


def parse_results_file(content: str) -> list[SearchResult]:
    """Parse a search results file into SearchResult objects."""
    results = []
    supported = _get_supported_formats()

    for line in content.splitlines():
        result = parse_result_line(line)
        if result:
            # Filter to user's configured formats
            if result.format in supported or result.format == 'unknown':
                results.append(result)

    logger.info(f"Parsed {len(results)} results from search file")
    return results


def extract_results_from_zip(zip_path: Path) -> str:
    """Extract and return text content from a search results ZIP."""
    with zipfile.ZipFile(zip_path, 'r') as zf:
        # Should contain exactly one text file
        names = zf.namelist()
        if not names:
            raise ValueError("Empty ZIP file")

        # Find the text file
        txt_file = None
        for name in names:
            if name.endswith('.txt'):
                txt_file = name
                break

        if not txt_file:
            # Use first file
            txt_file = names[0]

        content = zf.read(txt_file)

        # Try different encodings
        for encoding in ['utf-8', 'latin-1', 'cp1252']:
            try:
                return content.decode(encoding)
            except UnicodeDecodeError:
                continue

        # Last resort
        return content.decode('utf-8', errors='replace')
