"""Archive extraction utilities for downloaded book archives."""

import os
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from cwa_book_downloader.core.logger import setup_logger
from cwa_book_downloader.core.config import config
from cwa_book_downloader.config.settings import SUPPORTED_FORMATS

logger = setup_logger(__name__)

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


def _is_book_file(file_path: Path) -> bool:
    """Check if file matches user's SUPPORTED_FORMATS setting."""
    ext = file_path.suffix.lower().lstrip(".")
    supported_exts = {fmt.lower() for fmt in SUPPORTED_FORMATS}
    return ext in supported_exts


def _filter_book_files(extracted_files: List[Path]) -> Tuple[List[Path], List[Path]]:
    """
    Filter extracted files to only book formats.

    Returns:
        Tuple of (book_files, non_book_files)
    """
    book_files = []
    non_book_files = []

    for file_path in extracted_files:
        if _is_book_file(file_path):
            book_files.append(file_path)
        else:
            non_book_files.append(file_path)

    return book_files, non_book_files


def extract_archive(
    archive_path: Path,
    output_dir: Path,
) -> Tuple[List[Path], List[str]]:
    """
    Extract book files from an archive.

    Extracts all files, then filters to only keep recognized book formats.
    Non-book files (HTML, images, etc.) are deleted.

    Args:
        archive_path: Path to the archive file
        output_dir: Directory to extract files to

    Returns:
        Tuple of (extracted_book_file_paths, warnings)

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

    # Filter to only book files, delete non-book files
    book_files, non_book_files = _filter_book_files(extracted_files)

    for non_book_file in non_book_files:
        try:
            non_book_file.unlink()
            logger.debug(f"Deleted non-book file: {non_book_file.name}")
        except OSError as e:
            logger.warning(f"Failed to delete non-book file {non_book_file}: {e}")

    if non_book_files:
        warnings.append(f"Skipped {len(non_book_files)} non-book file(s)")

    return book_files, warnings


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
    Process an archive file: extract, filter to book files, move to ingest.

    This is the main entry point for archive handling, usable by any download handler.

    Args:
        archive_path: Path to the downloaded archive file
        temp_dir: Base temp directory for extraction (e.g., TMP_DIR)
        ingest_dir: Final destination directory for book files
        archive_id: Unique identifier for temp directory naming
        task: Optional download task for filename generation

    Returns:
        ArchiveResult with success status, final paths, and status message
    """
    # Import here to avoid circular import
    from cwa_book_downloader.core.models import DownloadTask
    extract_dir = temp_dir / f"extract_{archive_id}"

    try:
        # Create temp extraction directory
        os.makedirs(extract_dir, exist_ok=True)
        os.makedirs(ingest_dir, exist_ok=True)

        # Extract to temp directory (filters to book files only)
        extracted_files, warnings = extract_archive(archive_path, extract_dir)

        if not extracted_files:
            # Clean up and return error
            shutil.rmtree(extract_dir, ignore_errors=True)
            archive_path.unlink(missing_ok=True)
            return ArchiveResult(
                success=False,
                final_paths=[],
                message="",
                error="No book files found in archive",
            )

        for warning in warnings:
            logger.debug(warning)

        logger.info(f"Extracted {len(extracted_files)} book file(s) from archive")

        # Move book files to ingest folder
        final_paths = []
        for extracted_file in extracted_files:
            # For multi-file archives (book packs, series), always preserve original filenames
            # since metadata title only applies to the searched book, not the whole pack.
            # For single files, respect USE_BOOK_TITLE setting.
            if len(extracted_files) == 1 and config.USE_BOOK_TITLE and task:
                filename = task.get_filename() or extracted_file.name
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

        # Build success message with extracted formats
        formats = [p.suffix.lstrip(".").upper() for p in final_paths]
        if len(formats) == 1:
            message = f"Extracted: {formats[0]}"
        else:
            message = f"Extracted: {len(formats)} files ({', '.join(formats)})"

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
