"""Archive extraction utilities for downloaded book archives."""

import os
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from cwa_book_downloader.core.logger import setup_logger
from cwa_book_downloader.core.config import config
from cwa_book_downloader.core.models import build_filename

logger = setup_logger(__name__)


def _get_supported_formats() -> List[str]:
    """Get current supported formats from config singleton."""
    formats = config.get("SUPPORTED_FORMATS", ["epub", "mobi", "azw3", "fb2", "djvu", "cbz", "cbr"])
    # Handle both list (from MultiSelectField) and comma-separated string (legacy/env)
    if isinstance(formats, str):
        return [fmt.strip().lower() for fmt in formats.split(",") if fmt.strip()]
    return [fmt.lower() for fmt in formats]


def _get_supported_audiobook_formats() -> List[str]:
    """Get current supported audiobook formats from config singleton."""
    formats = config.get("SUPPORTED_AUDIOBOOK_FORMATS", ["m4b", "mp3"])
    # Handle both list (from MultiSelectField) and comma-separated string (legacy/env)
    if isinstance(formats, str):
        return [fmt.strip().lower() for fmt in formats.split(",") if fmt.strip()]
    return [fmt.lower() for fmt in formats]

# Check for rarfile availability at module load
try:
    import rarfile

    RAR_AVAILABLE = True
except ImportError:
    RAR_AVAILABLE = False
    logger.warning("rarfile not installed - RAR extraction disabled")


class ArchiveExtractionError(Exception):
    """Raised when archive extraction fails."""

    pass


class PasswordProtectedError(ArchiveExtractionError):
    """Raised when archive requires a password."""

    pass


class CorruptedArchiveError(ArchiveExtractionError):
    """Raised when archive is corrupted."""

    pass


def is_archive(file_path: Path) -> bool:
    """Check if file is a supported archive format."""
    suffix = file_path.suffix.lower().lstrip(".")
    return suffix in ("zip", "rar")


def _is_supported_file(file_path: Path, content_type: Optional[str] = None) -> bool:
    """Check if file matches user's supported formats setting based on content type."""
    ext = file_path.suffix.lower().lstrip(".")
    if content_type and content_type.lower() == "audiobook":
        supported_formats = _get_supported_audiobook_formats()
    else:
        supported_formats = _get_supported_formats()
    return ext in supported_formats


# All known ebook extensions (superset of what user might enable)
ALL_EBOOK_EXTENSIONS = {'.pdf', '.epub', '.mobi', '.azw', '.azw3', '.fb2', '.djvu', '.cbz', '.cbr', '.doc', '.docx', '.rtf', '.txt'}

# All known audio extensions (superset of what user might enable for audiobooks)
ALL_AUDIO_EXTENSIONS = {'.m4b', '.mp3', '.m4a', '.aac', '.flac', '.ogg', '.wma', '.wav', '.opus'}


def _filter_files(
    extracted_files: List[Path],
    content_type: Optional[str] = None,
) -> Tuple[List[Path], List[Path], List[Path]]:
    """
    Filter extracted files based on content type.

    For audiobooks: filters to audio formats using SUPPORTED_AUDIOBOOK_FORMATS
    For books: filters to book formats using SUPPORTED_FORMATS

    Returns:
        Tuple of (matched_files, rejected_format_files, other_files)
        - matched_files: Match user's supported formats for this content type
        - rejected_format_files: Valid formats for this type but not enabled by user
        - other_files: Unrelated files (images, html, etc)
    """
    is_audiobook = content_type and content_type.lower() == "audiobook"
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
    content_type: Optional[str] = None,
) -> Tuple[List[Path], List[str], List[Path]]:
    """
    Extract files from an archive based on content type.

    Extracts all files, then filters based on content type:
    - Audiobooks: keeps files matching SUPPORTED_AUDIOBOOK_FORMATS
    - Books: keeps files matching SUPPORTED_FORMATS
    Non-matching files (HTML, images, etc.) are deleted.

    Args:
        archive_path: Path to the archive file
        output_dir: Directory to extract files to
        content_type: Content type (e.g., "audiobook") to determine which formats to keep

    Returns:
        Tuple of (matched_files, warnings, rejected_files)
        - matched_files: Paths to extracted files matching supported formats
        - warnings: List of warning messages
        - rejected_files: Files that were rejected (format not enabled)

    Raises:
        ArchiveExtractionError: If extraction fails
        PasswordProtectedError: If archive requires password
        CorruptedArchiveError: If archive is corrupted
    """
    suffix = archive_path.suffix.lower().lstrip(".")

    if suffix == "zip":
        extracted_files, warnings = _extract_zip(archive_path, output_dir)
    elif suffix == "rar":
        extracted_files, warnings = _extract_rar(archive_path, output_dir)
    else:
        raise ArchiveExtractionError(f"Unsupported archive format: {suffix}")

    is_audiobook = content_type and content_type.lower() == "audiobook"
    file_type_label = "audiobook" if is_audiobook else "book"

    # Filter files based on content type
    matched_files, rejected_files, other_files = _filter_files(extracted_files, content_type)

    # Delete rejected files (valid formats but not enabled by user)
    for rejected_file in rejected_files:
        try:
            rejected_file.unlink()
            logger.debug(f"Deleted rejected {file_type_label} file: {rejected_file.name}")
        except OSError as e:
            logger.warning(f"Failed to delete rejected {file_type_label} file {rejected_file}: {e}")

    if rejected_files:
        rejected_exts = sorted(set(f.suffix.lower() for f in rejected_files))
        warnings.append(f"Skipped {len(rejected_files)} {file_type_label}(s) with unsupported format: {', '.join(rejected_exts)}")

    # Delete other files (images, html, etc)
    for other_file in other_files:
        try:
            other_file.unlink()
            logger.debug(f"Deleted non-{file_type_label} file: {other_file.name}")
        except OSError as e:
            logger.warning(f"Failed to delete non-{file_type_label} file {other_file}: {e}")

    if other_files:
        warnings.append(f"Skipped {len(other_files)} non-{file_type_label} file(s)")

    return matched_files, warnings, rejected_files


def _extract_zip(
    archive_path: Path,
    output_dir: Path,
) -> Tuple[List[Path], List[str]]:
    """Extract files from a ZIP archive."""
    extracted_files = []
    warnings = []

    try:
        with zipfile.ZipFile(archive_path, "r") as zf:
            # Check for password protection
            for info in zf.infolist():
                if info.flag_bits & 0x1:  # Encrypted flag
                    raise PasswordProtectedError("ZIP archive is password protected")

            # Test archive integrity
            bad_file = zf.testzip()
            if bad_file:
                raise CorruptedArchiveError(f"Corrupted file in archive: {bad_file}")

            # Extract all files
            for info in zf.infolist():
                if info.is_dir():
                    continue

                # Use only filename, strip directory path (security: prevent path traversal)
                filename = Path(info.filename).name
                if not filename:
                    continue

                # Extract to output_dir with flat structure
                target_path = output_dir / filename
                target_path = _handle_duplicate_filename(target_path)

                with zf.open(info) as src, open(target_path, "wb") as dst:
                    dst.write(src.read())

                extracted_files.append(target_path)
                logger.debug(f"Extracted: {filename}")

    except zipfile.BadZipFile as e:
        raise CorruptedArchiveError(f"Invalid or corrupted ZIP: {e}")
    except PermissionError as e:
        raise ArchiveExtractionError(f"Permission denied: {e}")

    return extracted_files, warnings


def _extract_rar(
    archive_path: Path,
    output_dir: Path,
) -> Tuple[List[Path], List[str]]:
    """Extract files from a RAR archive."""
    if not RAR_AVAILABLE:
        raise ArchiveExtractionError("RAR extraction not available - rarfile library not installed")

    extracted_files = []
    warnings = []

    try:
        with rarfile.RarFile(archive_path, "r") as rf:
            # Check for password protection
            if rf.needs_password():
                raise PasswordProtectedError("RAR archive is password protected")

            # Test archive integrity
            rf.testrar()

            # Extract all files
            for info in rf.infolist():
                if info.is_dir():
                    continue

                # Use only filename, strip directory path (security: prevent path traversal)
                filename = Path(info.filename).name
                if not filename:
                    continue

                # Extract to output_dir with flat structure
                target_path = output_dir / filename
                target_path = _handle_duplicate_filename(target_path)

                with rf.open(info) as src, open(target_path, "wb") as dst:
                    dst.write(src.read())

                extracted_files.append(target_path)
                logger.debug(f"Extracted: {filename}")

    except rarfile.BadRarFile as e:
        raise CorruptedArchiveError(f"Invalid or corrupted RAR: {e}")
    except rarfile.RarCannotExec:
        raise ArchiveExtractionError("unrar binary not found - install unrar package")
    except PermissionError as e:
        raise ArchiveExtractionError(f"Permission denied: {e}")

    return extracted_files, warnings


def _handle_duplicate_filename(target_path: Path) -> Path:
    """Handle duplicate filenames by appending counter."""
    if not target_path.exists():
        return target_path

    base = target_path.stem
    ext = target_path.suffix
    parent = target_path.parent
    counter = 1

    while target_path.exists():
        target_path = parent / f"{base}_{counter}{ext}"
        counter += 1

    return target_path


@dataclass
class ArchiveResult:
    """Result of archive processing."""

    success: bool
    final_paths: List[Path]
    message: str
    error: Optional[str] = None


def process_archive(
    archive_path: Path,
    temp_dir: Path,
    ingest_dir: Path,
    archive_id: str,
    task: Optional["DownloadTask"] = None,
) -> ArchiveResult:
    """
    Process an archive file: extract, filter to supported files, move to ingest.

    This is the main entry point for archive handling, usable by any download handler.
    Filters files based on content type:
    - Audiobooks: keeps files matching SUPPORTED_AUDIOBOOK_FORMATS
    - Books: keeps files matching SUPPORTED_FORMATS

    Args:
        archive_path: Path to the downloaded archive file
        temp_dir: Base temp directory for extraction (e.g., TMP_DIR)
        ingest_dir: Final destination directory for files
        archive_id: Unique identifier for temp directory naming
        task: Optional download task for filename generation and content type

    Returns:
        ArchiveResult with success status, final paths, and status message
    """
    extract_dir = temp_dir / f"extract_{archive_id}"
    content_type = task.content_type if task else None
    is_audiobook = content_type and content_type.lower() == "audiobook"
    file_type_label = "audiobook" if is_audiobook else "book"

    try:
        # Create temp extraction directory
        os.makedirs(extract_dir, exist_ok=True)
        os.makedirs(ingest_dir, exist_ok=True)

        # Extract to temp directory (filters based on content type)
        extracted_files, warnings, rejected_files = extract_archive(archive_path, extract_dir, content_type)

        if not extracted_files:
            # Clean up and return error
            shutil.rmtree(extract_dir, ignore_errors=True)
            archive_path.unlink(missing_ok=True)

            if rejected_files:
                # Found files but they weren't in supported formats
                rejected_exts = sorted(set(f.suffix.lower() for f in rejected_files))
                rejected_list = ", ".join(rejected_exts)
                supported_formats = _get_supported_audiobook_formats() if is_audiobook else _get_supported_formats()
                logger.warning(
                    f"Found {len(rejected_files)} {file_type_label}(s) in archive but format not supported. "
                    f"Rejected: {rejected_list}. Supported: {', '.join(sorted(supported_formats))}"
                )
                return ArchiveResult(
                    success=False,
                    final_paths=[],
                    message="",
                    error=f"Found {len(rejected_files)} {file_type_label}(s) but format not supported ({rejected_list}). Enable in Settings > Formats.",
                )

            return ArchiveResult(
                success=False,
                final_paths=[],
                message="",
                error=f"No {file_type_label} files found in archive",
            )

        for warning in warnings:
            logger.debug(warning)

        logger.info(f"Extracted {len(extracted_files)} {file_type_label} file(s) from archive")

        # Move book files to ingest folder
        final_paths = []
        for extracted_file in extracted_files:
            # For multi-file archives (book packs, series), always preserve original filenames
            # since metadata title only applies to the searched book, not the whole pack.
            # For single files, respect USE_BOOK_TITLE setting.
            if len(extracted_files) == 1 and config.USE_BOOK_TITLE and task:
                # Use the extracted file's actual extension, not the archive's extension
                # (task.download_path points to the archive, so we must use build_filename directly)
                extracted_format = extracted_file.suffix.lower().lstrip('.')
                filename = build_filename(task.title, task.author, task.year, extracted_format)
            else:
                filename = extracted_file.name

            final_path = ingest_dir / filename
            final_path = _handle_duplicate_filename(final_path)
            shutil.move(str(extracted_file), str(final_path))
            final_paths.append(final_path)
            logger.debug(f"Moved to ingest: {final_path.name}")

        # Clean up temp extraction directory and archive
        shutil.rmtree(extract_dir, ignore_errors=True)
        archive_path.unlink(missing_ok=True)

        # Build success message with format info
        formats = [p.suffix.lstrip(".").upper() for p in final_paths]
        if len(formats) == 1:
            message = f"Complete ({formats[0]})"
        else:
            message = f"Complete ({len(formats)} files)"

        return ArchiveResult(
            success=True,
            final_paths=final_paths,
            message=message,
        )

    except PasswordProtectedError:
        logger.error(f"Password-protected archive: {archive_path.name}")
        shutil.rmtree(extract_dir, ignore_errors=True)
        archive_path.unlink(missing_ok=True)
        return ArchiveResult(
            success=False,
            final_paths=[],
            message="",
            error="Archive is password protected",
        )

    except CorruptedArchiveError as e:
        logger.error(f"Corrupted archive: {e}")
        shutil.rmtree(extract_dir, ignore_errors=True)
        archive_path.unlink(missing_ok=True)
        return ArchiveResult(
            success=False,
            final_paths=[],
            message="",
            error=f"Corrupted archive: {e}",
        )

    except ArchiveExtractionError as e:
        logger.error(f"Archive extraction failed: {e}")
        shutil.rmtree(extract_dir, ignore_errors=True)
        archive_path.unlink(missing_ok=True)
        return ArchiveResult(
            success=False,
            final_paths=[],
            message="",
            error=f"Extraction failed: {e}",
        )
