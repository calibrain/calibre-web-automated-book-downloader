"""Archive extraction utilities for downloaded book archives."""

import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, cast

from shelfmark.core.logger import setup_logger
from shelfmark.core.utils import AUDIOBOOK_FORMATS
from shelfmark.core.utils import is_audiobook as check_audiobook
from shelfmark.download.fs import atomic_move
from shelfmark.download.postprocess.policy import (
    get_supported_audiobook_formats,
    get_supported_formats,
)

logger = setup_logger(__name__)
_ARCHIVE_COPY_CHUNK_SIZE = 1024 * 1024

if TYPE_CHECKING:
    import rarfile

    ArchiveType = zipfile.ZipFile | rarfile.RarFile
else:
    ArchiveType = zipfile.ZipFile


def _delete_file_with_logging(file_path: Path, file_type_label: str, *, rejected: bool) -> None:
    """Delete a file and log the outcome."""
    try:
        file_path.unlink()
        if rejected:
            logger.debug("Deleted rejected %s file: %s", file_type_label, file_path.name)
        else:
            logger.debug("Deleted non-%s file: %s", file_type_label, file_path.name)
    except OSError as e:
        if rejected:
            logger.warning(
                "Failed to delete rejected %s file %s: %s", file_type_label, file_path, e
            )
        else:
            logger.warning("Failed to delete non-%s file %s: %s", file_type_label, file_path, e)


# Check for rarfile availability at module load
try:
    import rarfile

    RAR_AVAILABLE = True
except ImportError:
    RAR_AVAILABLE = False
    logger.warning("rarfile not installed - RAR extraction disabled")


class ArchiveExtractionError(Exception):
    """Raised when archive extraction fails."""


class PasswordProtectedError(ArchiveExtractionError):
    """Raised when archive requires a password."""


class CorruptedArchiveError(ArchiveExtractionError):
    """Raised when archive is corrupted."""


def is_archive(file_path: Path) -> bool:
    """Check if file is a supported archive format."""
    suffix = file_path.suffix.lower().lstrip(".")
    return suffix in ("zip", "rar")


_RAR_VOLUME_RE = re.compile(r"\.r\d{2}$", re.IGNORECASE)
_LATER_RAR_PART_RE = re.compile(r"\.part0*(\d+)\.rar$", re.IGNORECASE)


def is_archive_volume(file_path: Path) -> bool:
    """An archive, or a volume of a split RAR (``.r00``, ``.r01``, ...)."""
    return is_archive(file_path) or bool(_RAR_VOLUME_RE.search(file_path.name))


def is_first_volume(file_path: Path) -> bool:
    """An archive to open: a ZIP, a RAR, or the first part of ``name.partN.rar``."""
    if not is_archive(file_path):
        return False
    part = _LATER_RAR_PART_RE.search(file_path.name)
    return part is None or int(part.group(1)) == 1


def _is_supported_file(file_path: Path, content_type: str | None = None) -> bool:
    """Check if file matches user's supported formats setting based on content type."""
    ext = file_path.suffix.lower().lstrip(".")
    if check_audiobook(content_type):
        supported_formats = get_supported_audiobook_formats()
    else:
        supported_formats = get_supported_formats()
    return ext in supported_formats


# All known ebook extensions (superset of what user might enable)
ALL_EBOOK_EXTENSIONS = {
    ".pdf",
    ".epub",
    ".mobi",
    ".azw",
    ".azw3",
    ".fb2",
    ".djvu",
    ".cbz",
    ".cbr",
    ".doc",
    ".docx",
    ".rtf",
    ".txt",
}

# All known audio extensions (superset of what user might enable for audiobooks)
ALL_AUDIO_EXTENSIONS = {f".{fmt}" for fmt in AUDIOBOOK_FORMATS}


def _filter_files(
    extracted_files: list[Path],
    content_type: str | None = None,
) -> tuple[list[Path], list[Path], list[Path]]:
    """Filter files by content type. Returns (matched, rejected_format, other)."""
    is_audiobook = check_audiobook(content_type)
    known_extensions = ALL_AUDIO_EXTENSIONS if is_audiobook else ALL_EBOOK_EXTENSIONS

    matched_files = []
    rejected_format_files = []
    other_files = []

    for file_path in extracted_files:
        if _is_supported_file(file_path, content_type):
            matched_files.append(file_path)
        elif file_path.suffix.lower() in known_extensions:
            rejected_format_files.append(file_path)
        else:
            other_files.append(file_path)

    return matched_files, rejected_format_files, other_files


def extract_archive(
    archive_path: Path,
    output_dir: Path,
    content_type: str | None = None,
) -> tuple[list[Path], list[str], list[Path]]:
    """Extract archive and filter by content type. Returns (matched, warnings, rejected)."""
    suffix = archive_path.suffix.lower().lstrip(".")

    if suffix == "zip":
        extracted_files, warnings = _extract_zip(archive_path, output_dir)
    elif suffix == "rar":
        extracted_files, warnings = _extract_rar(archive_path, output_dir)
    else:
        msg = f"Unsupported archive format: {suffix}"
        raise ArchiveExtractionError(msg)

    is_audiobook = check_audiobook(content_type)
    file_type_label = "audiobook" if is_audiobook else "book"

    # Filter files based on content type
    matched_files, rejected_files, other_files = _filter_files(extracted_files, content_type)

    # Delete rejected files (valid formats but not enabled by user)
    for rejected_file in rejected_files:
        _delete_file_with_logging(rejected_file, file_type_label, rejected=True)

    if rejected_files:
        rejected_exts = sorted({f.suffix.lower() for f in rejected_files})
        warnings.append(
            f"Skipped {len(rejected_files)} {file_type_label}(s) with unsupported format: {', '.join(rejected_exts)}"
        )

    # Keep archive volumes for a nested pass (some downloads ship RARs inside ZIPs);
    # delete other files (images, html, etc)
    nested_volumes = [f for f in other_files if is_archive_volume(f)]
    other_files = [f for f in other_files if not is_archive_volume(f)]
    for other_file in other_files:
        _delete_file_with_logging(other_file, file_type_label, rejected=False)

    if nested_volumes:
        warnings.append(f"Kept {len(nested_volumes)} nested archive volume(s)")
    if other_files:
        warnings.append(f"Skipped {len(other_files)} non-{file_type_label} file(s)")

    return matched_files, warnings, rejected_files


def extract_archive_raw(
    archive_path: Path,
    output_dir: Path,
) -> tuple[list[Path], list[str]]:
    """Extract archive without filtering (returns all extracted files)."""
    suffix = archive_path.suffix.lower().lstrip(".")

    if suffix == "zip":
        return _extract_zip(archive_path, output_dir)
    if suffix == "rar":
        return _extract_rar(archive_path, output_dir)

    msg = f"Unsupported archive format: {suffix}"
    raise ArchiveExtractionError(msg)


def _extract_files_from_archive(archive: ArchiveType, output_dir: Path) -> list[Path]:
    """Extract files from ZipFile or RarFile to output_dir with security checks."""
    extracted_files = []

    for info in archive.infolist():
        if info.is_dir():
            continue

        # Use only filename, strip directory path (security: prevent path traversal)
        filename = Path(info.filename).name
        if not filename:
            continue

        # Security: reject filenames with null bytes or path separators
        # Check both / and \ since archives may be created on different OSes
        if "\x00" in filename or "/" in filename or "\\" in filename:
            logger.warning("Skipping suspicious filename in archive: %r", info.filename)
            continue

        # Extract to output_dir with flat structure
        target_path = output_dir / filename

        # Security: verify resolved path stays within output directory (defense-in-depth)
        try:
            target_path.resolve().relative_to(output_dir.resolve())
        except ValueError:
            logger.warning("Path traversal attempt blocked: %r", info.filename)
            continue

        temp_path: Path | None = None
        try:
            with (
                archive.open(info) as src,
                tempfile.NamedTemporaryFile(
                    dir=output_dir,
                    prefix=".shelfmark-extract-",
                    suffix=".tmp",
                    delete=False,
                ) as temp_file,
            ):
                temp_path = Path(temp_file.name)
                shutil.copyfileobj(src, temp_file, length=_ARCHIVE_COPY_CHUNK_SIZE)

            final_path = atomic_move(cast("Path", temp_path), target_path)
        except Exception:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
            raise
        extracted_files.append(final_path)
        logger.debug("Extracted: %s", filename)

    return extracted_files


def _extract_zip(archive_path: Path, output_dir: Path) -> tuple[list[Path], list[str]]:
    """Extract files from a ZIP archive."""
    try:
        with zipfile.ZipFile(archive_path, "r") as zf:
            # Check for password protection
            for info in zf.infolist():
                if info.flag_bits & 0x1:  # Encrypted flag
                    msg = "ZIP archive is password protected"
                    raise PasswordProtectedError(msg)

            # Test archive integrity
            bad_file = zf.testzip()
            if bad_file:
                msg = f"Corrupted file in archive: {bad_file}"
                raise CorruptedArchiveError(msg)

            return _extract_files_from_archive(zf, output_dir), []

    except zipfile.BadZipFile as e:
        msg = f"Invalid or corrupted ZIP: {e}"
        raise CorruptedArchiveError(msg) from e
    except PermissionError as e:
        msg = f"Permission denied: {e}"
        raise ArchiveExtractionError(msg) from e


def _extract_rar(archive_path: Path, output_dir: Path) -> tuple[list[Path], list[str]]:
    """Extract files from a RAR archive."""
    if not RAR_AVAILABLE:
        msg = "RAR extraction not available - rarfile library not installed"
        raise ArchiveExtractionError(msg)

    try:
        with rarfile.RarFile(archive_path, "r") as rf:
            # Check for password protection
            if rf.needs_password():
                msg = "RAR archive is password protected"
                raise PasswordProtectedError(msg)

            # Test archive integrity
            rf.testrar()

            return _extract_files_from_archive(rf, output_dir), []

    except rarfile.BadRarFile as e:
        msg = f"Invalid or corrupted RAR: {e}"
        raise CorruptedArchiveError(msg) from e
    except rarfile.RarCannotExec as e:
        msg = "unrar binary not found - install unrar package"
        raise ArchiveExtractionError(msg) from e
    except PermissionError as e:
        msg = f"Permission denied: {e}"
        raise ArchiveExtractionError(msg) from e
