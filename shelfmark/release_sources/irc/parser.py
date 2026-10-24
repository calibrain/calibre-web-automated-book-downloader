"""Search results file parser.

Parses the text files sent via DCC that contain search results.
"""

import re
import zipfile
from dataclasses import dataclass
from typing import TYPE_CHECKING

from shelfmark.core.formats import get_supported_formats_for
from shelfmark.core.logger import setup_logger
from shelfmark.core.utils import ARCHIVE_FORMATS, AUDIOBOOK_FORMATS
from shelfmark.core.utils import is_audiobook as check_audiobook

if TYPE_CHECKING:
    from pathlib import Path

logger = setup_logger(__name__)

# Ebook formats recognized in IRC result lines.
EBOOK_FORMATS = (
    "epub",
    "mobi",
    "azw3",
    "azw",
    "pdf",
    "doc",
    "docx",
    "html",
    "htm",
    "rtf",
    "txt",
    "lit",
    "fb2",
    "djvu",
    "cbr",
    "cbz",
    "cdr",
    "jpg",
)

# All recognized formats for parsing IRC result lines.
# This comprehensive list is used to identify file extensions in results.
# User-configured formats are used separately for filtering.
# Ordered longest-first so that scanning a line matches "azw3" before "azw" and "docx"
# before "doc". It used to be a set, which made the winning format for a line naming more
# than one extension depend on set iteration order, and therefore vary between restarts.
ALL_RECOGNIZED_FORMATS = tuple(
    sorted({*EBOOK_FORMATS, *ARCHIVE_FORMATS, *AUDIOBOOK_FORMATS}, key=len, reverse=True)
)


def _get_supported_formats(content_type: str | None = None) -> set[str]:
    """Get the supported formats for the requested content type."""
    return set(get_supported_formats_for(content_type))


# Regex to parse result lines
# Format: !Server Author - Title.format ::INFO:: size
#
# The extension is matched against the known formats rather than a bare \w+. A bare \w+
# happily matched the decimal point in the size, so a line with no file extension parsed
# as format="5mb" out of "::INFO:: 620.5MB" - taking the title and size down with it, and
# leaving the result to be discarded by every format filter downstream. Restricting the
# alternation makes such a line fall through to SIMPLE_RESULT_REGEX and come back as
# "unknown", which is what the rest of the parser already expects.
_FORMAT_ALTERNATION = "|".join(re.escape(fmt) for fmt in ALL_RECOGNIZED_FORMATS)
RESULT_LINE_REGEX = re.compile(
    r"^!(\S+)\s+"  # !ServerName
    r"(.+?)\s+-\s+"  # Author Name -
    rf"(.+?)\.({_FORMAT_ALTERNATION})\b"  # Title.format
    r"(?:\s+::INFO::\s*(.+?))?"  # Optional ::INFO:: metadata
    r"(?:\s+::HASH::\s*(\S+))?"  # Optional ::HASH::
    r"\s*$",
    re.IGNORECASE,
)

# Simpler fallback pattern
SIMPLE_RESULT_REGEX = re.compile(
    r"^!(\S+)\s+(.+)$"  # !Server everything_else
)


@dataclass
class SearchResult:
    """Parsed search result entry."""

    server: str  # Bot name (without !)
    author: str  # Author name
    title: str  # Book title
    format: str  # File format (epub, mobi, etc)
    size: str | None  # Human-readable size
    full_line: str  # Original line for download request

    @property
    def download_request(self) -> str:
        """The string to send to IRC to request this book."""
        return self.full_line.strip()

    @property
    def display_name(self) -> str:
        """Human-readable display name."""
        return f"{self.author} - {self.title}"


def parse_result_line(line: str) -> SearchResult | None:
    """Parse a single search result line. Returns None if unparseable."""
    line = line.strip()

    # Must start with !
    if not line.startswith("!"):
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
            if f".{known_fmt}" in rest.lower():
                fmt = known_fmt
                break

        # Try to split author - title
        if " - " in rest:
            parts = rest.split(" - ", 1)
            author = parts[0].strip()
            title_part = parts[1].strip() if len(parts) > 1 else rest
        else:
            author = "Unknown"
            title_part = rest

        # Extract size if present
        size = None
        if "::INFO::" in title_part:
            title_part, info = title_part.split("::INFO::", 1)
            size = info.split("::")[0].strip()

        # Clean up title (remove extension)
        title = title_part
        for known_fmt in ALL_RECOGNIZED_FORMATS:
            title = re.sub(rf"\.{known_fmt}\b", "", title, flags=re.IGNORECASE)

        return SearchResult(
            server=server,
            author=author,
            title=title.strip(),
            format=fmt or "unknown",
            size=size,
            full_line=line,
        )

    logger.debug("Could not parse line: %s...", line[:80])
    return None


# Words that mark an archive as holding an audiobook rather than an ebook. Multi-file
# audiobooks ship as .rar/.zip, so for those the extension says nothing about the content
# and the release name is the only evidence there is.
_AUDIOBOOK_MARKER_REGEX = re.compile(
    r"\b(?:audio ?books?|unabridged|abridged|narrat(?:ed|or)|audible|\d+ ?kbps|"
    + "|".join(re.escape(fmt) for fmt in AUDIOBOOK_FORMATS)
    + r")\b",
    re.IGNORECASE,
)

_AUDIOBOOK_FORMAT_SET = frozenset(AUDIOBOOK_FORMATS)
_EBOOK_FORMAT_SET = frozenset(EBOOK_FORMATS)


def detect_content_type(result: SearchResult) -> str:
    """Classify a parsed result as an audiobook or an ebook.

    Extension alone is not enough. It settles the plain cases, but the common audiobook
    release is a .rar or .zip of MP3s, which is indistinguishable by extension from an
    ebook archive - so for containers (and for lines with no usable extension) the
    release name decides.
    """
    if result.format in _AUDIOBOOK_FORMAT_SET:
        return "audiobook"
    if result.format in _EBOOK_FORMAT_SET:
        return "ebook"
    return "audiobook" if _AUDIOBOOK_MARKER_REGEX.search(result.full_line) else "ebook"


def parse_results_file(content: str, content_type: str | None = None) -> list[SearchResult]:
    """Parse a search results file into SearchResult objects."""
    results = []
    supported = _get_supported_formats(content_type)
    requested = "audiobook" if check_audiobook(content_type) else "ebook"

    for line in content.splitlines():
        result = parse_result_line(line)
        if not result:
            continue
        # Classify first, then apply the user's format filter within that bucket. Doing it
        # the other way round is what lost audiobooks entirely: an audiobook .rar matched
        # neither the ebook nor the audiobook format list, so it fell out of both.
        if detect_content_type(result) != requested:
            continue
        if result.format in supported or result.format == "unknown":
            results.append(result)

    logger.info("Parsed %s %s results from search file", len(results), requested)
    return results


def extract_results_from_zip(zip_path: Path) -> str:
    """Extract and return text content from a search results ZIP."""
    with zipfile.ZipFile(zip_path, "r") as zf:
        # Should contain exactly one text file
        names = zf.namelist()
        if not names:
            msg = "Empty ZIP file"
            raise ValueError(msg)

        # Find the text file
        txt_file = None
        for name in names:
            if name.endswith(".txt"):
                txt_file = name
                break

        if not txt_file:
            # Use first file
            txt_file = names[0]

        content = zf.read(txt_file)

        # Try different encodings
        for encoding in ["utf-8", "latin-1", "cp1252"]:
            decoded = _decode_content_with_encoding(content, encoding)
            if decoded is not None:
                return decoded

        # Last resort
        return content.decode("utf-8", errors="replace")


def _decode_content_with_encoding(content: bytes, encoding: str) -> str | None:
    """Decode bytes using one encoding, returning None when it fails."""
    try:
        return content.decode(encoding)
    except UnicodeDecodeError:
        return None
