"""Archive extraction utilities for downloaded book archives."""

import os
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from cwa_book_downloader.core.logger import setup_logger
from cwa_book_downloader.core.config import config
from cwa_book_downloader.core.naming import parse_naming_template, sanitize_filename
from cwa_book_downloader.core.utils import is_audiobook as check_audiobook
from cwa_book_downloader.download.fs import atomic_write, atomic_move

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


def _get_file_organization(is_audiobook: bool) -> str:
    """Get the file organization mode for the content type."""
    key = "FILE_ORGANIZATION_AUDIOBOOK" if is_audiobook else "FILE_ORGANIZATION"
    mode = config.get(key, "rename")

    # Handle legacy settings migration
    if mode not in ("none", "rename", "organize"):
        legacy_key = "PROCESSING_MODE_AUDIOBOOK" if is_audiobook else "PROCESSING_MODE"
        legacy_mode = config.get(legacy_key, "ingest")
        if legacy_mode == "library":
            return "organize"
        if config.get("USE_BOOK_TITLE", True):
            return "rename"
        return "none"

    return mode


def _get_template(is_audiobook: bool, organization_mode: str) -> str:
    """Get the template for the content type and organization mode."""
    # Determine the correct key based on content type and organization mode
    if is_audiobook:
        if organization_mode == "organize":
            key = "TEMPLATE_AUDIOBOOK_ORGANIZE"
        else:
            key = "TEMPLATE_AUDIOBOOK_RENAME"
    else:
        if organization_mode == "organize":
            key = "TEMPLATE_ORGANIZE"
        else:
            key = "TEMPLATE_RENAME"

    template = config.get(key, "")

    # Fallback to legacy keys if new keys are empty
    if not template:
        legacy_key = "TEMPLATE_AUDIOBOOK" if is_audiobook else "TEMPLATE"
        template = config.get(legacy_key, "")

    if not template:
        legacy_key = "LIBRARY_TEMPLATE_AUDIOBOOK" if is_audiobook else "LIBRARY_TEMPLATE"
        template = config.get(legacy_key, "")

    if not template:
        if organization_mode == "organize":
            return "{Author}/{Title} ({Year})"
        return "{Author} - {Title} ({Year})"

    return template


def _build_filename_from_task(task, extension: str, organization_mode: str) -> str:
    """Build a filename from task metadata using the configured template."""
    is_audiobook = check_audiobook(task.content_type)

    template = _get_template(is_audiobook, organization_mode)
    metadata = {
        "Author": task.author,
        "Title": task.title,
        "Subtitle": getattr(task, 'subtitle', None),
        "Year": task.year,
        "Series": getattr(task, 'series_name', None),
        "SeriesPosition": getattr(task, 'series_position', None),
    }

    filename = parse_naming_template(template, metadata)
    if filename:
        return f"{sanitize_filename(filename)}.{extension}"
    return ""

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
    if check_audiobook(content_type):
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
    content_type: Optional[str] = None,
) -> Tuple[List[Path], List[str], List[Path]]:
    """Extract archive and filter by content type. Returns (matched, warnings, rejected)."""
    suffix = archive_path.suffix.lower().lstrip(".")

    if suffix == "zip":
        extracted_files, warnings = _extract_zip(archive_path, output_dir)
    elif suffix == "rar":
        extracted_files, warnings = _extract_rar(archive_path, output_dir)
    else:
        raise ArchiveExtractionError(f"Unsupported archive format: {suffix}")

    is_audiobook = check_audiobook(content_type)
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


def _extract_files_from_archive(archive, output_dir: Path) -> List[Path]:
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
            logger.warning(f"Skipping suspicious filename in archive: {info.filename!r}")
            continue

        # Extract to output_dir with flat structure
        target_path = output_dir / filename

        # Security: verify resolved path stays within output directory (defense-in-depth)
        try:
            target_path.resolve().relative_to(output_dir.resolve())
        except ValueError:
            logger.warning(f"Path traversal attempt blocked: {info.filename!r}")
            continue

        with archive.open(info) as src:
            data = src.read()
        final_path = atomic_write(target_path, data)
        extracted_files.append(final_path)
        logger.debug(f"Extracted: {filename}")

    return extracted_files


def _extract_zip(archive_path: Path, output_dir: Path) -> Tuple[List[Path], List[str]]:
    """Extract files from a ZIP archive."""
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

            return _extract_files_from_archive(zf, output_dir), []

    except zipfile.BadZipFile as e:
        raise CorruptedArchiveError(f"Invalid or corrupted ZIP: {e}")
    except PermissionError as e:
        raise ArchiveExtractionError(f"Permission denied: {e}")


def _extract_rar(archive_path: Path, output_dir: Path) -> Tuple[List[Path], List[str]]:
    """Extract files from a RAR archive."""
    if not RAR_AVAILABLE:
        raise ArchiveExtractionError("RAR extraction not available - rarfile library not installed")

    try:
        with rarfile.RarFile(archive_path, "r") as rf:
            # Check for password protection
            if rf.needs_password():
                raise PasswordProtectedError("RAR archive is password protected")

            # Test archive integrity
            rf.testrar()

            return _extract_files_from_archive(rf, output_dir), []

    except rarfile.BadRarFile as e:
        raise CorruptedArchiveError(f"Invalid or corrupted RAR: {e}")
    except rarfile.RarCannotExec:
        raise ArchiveExtractionError("unrar binary not found - install unrar package")
    except PermissionError as e:
        raise ArchiveExtractionError(f"Permission denied: {e}")


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
    """Extract archive, filter to supported formats, move to ingest directory."""
    extract_dir = temp_dir / f"extract_{archive_id}"
    content_type = task.content_type if task else None
    is_audiobook = check_audiobook(content_type)
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

        # Determine file organization mode
        is_audiobook = check_audiobook(task.content_type) if task else False
        organization_mode = _get_file_organization(is_audiobook) if task else "none"

        for extracted_file in extracted_files:
            # For multi-file archives (book packs, series), always preserve original filenames
            # since metadata title only applies to the searched book, not the whole pack.
            # For single files, respect FILE_ORGANIZATION setting.
            if len(extracted_files) == 1 and organization_mode != "none" and task:
                # Use the extracted file's actual extension, not the archive's extension
                extracted_format = extracted_file.suffix.lower().lstrip('.')
                filename = _build_filename_from_task(task, extracted_format, organization_mode)
                if not filename:
                    filename = extracted_file.name
            else:
                filename = extracted_file.name

            dest_path = ingest_dir / filename
            final_path = atomic_move(extracted_file, dest_path)
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
