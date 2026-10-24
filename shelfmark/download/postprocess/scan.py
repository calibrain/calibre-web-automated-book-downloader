"""Scanning helpers for discovering candidate files after download completion."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from shelfmark.core.logger import setup_logger
from shelfmark.core.utils import AUDIOBOOK_FORMATS
from shelfmark.core.utils import is_audiobook as check_audiobook
from shelfmark.download.archive import ArchiveExtractionError, extract_archive, is_archive
from shelfmark.download.fs import run_blocking_io
from shelfmark.download.permissions_debug import log_path_permission_context
from shelfmark.download.postprocess.policy import (
    get_supported_formats_for as get_supported_formats,
)
from shelfmark.download.staging import build_staging_dir

if TYPE_CHECKING:
    from collections.abc import Callable

    from shelfmark.core.models import DownloadTask

logger = setup_logger("shelfmark.download.postprocess.pipeline")


def _format_not_supported_error(rejected_files: list[Path], task: DownloadTask) -> str:
    content_type = task.content_type
    file_type_label = "audiobook" if check_audiobook(content_type) else "book"
    rejected_exts = sorted({f.suffix.lower() for f in rejected_files})
    rejected_list = ", ".join(rejected_exts)
    supported_formats = get_supported_formats(content_type)

    logger.warning(
        "Task %s: found %d %s(s) but none match supported formats. Rejected formats: %s. Supported: %s",
        task.task_id,
        len(rejected_files),
        file_type_label,
        rejected_list,
        ", ".join(sorted(supported_formats)),
    )

    return (
        f"Found {len(rejected_files)} {file_type_label}(s) but format not supported ({rejected_list}). "
        "Enable in Settings > Formats."
    )


# rarfile extracts by running `unrar` as a subprocess. Under gevent a subprocess can only be
# started on the thread that owns the default loop, so in the IO threadpool it raises
# "TypeError: child watchers are only available on the default loop" and the download fails. The
# patched subprocess waits cooperatively, so these archives are extracted in place instead.
_SUBPROCESS_ARCHIVE_SUFFIXES = frozenset({".rar"})


def extract_archive_files(
    archive_path: Path,
    output_dir: Path,
    task: DownloadTask,
    *,
    cleanup_archive: bool,
) -> tuple[list[Path], list[Path], list[Path], str | None]:
    """Extract an archive and classify the resulting files."""
    content_type = task.content_type

    try:
        if archive_path.suffix.lower() in _SUBPROCESS_ARCHIVE_SUFFIXES:
            extracted_files, warnings, rejected_files = extract_archive(
                archive_path, output_dir, content_type
            )
        else:
            extracted_files, warnings, rejected_files = run_blocking_io(
                extract_archive,
                archive_path,
                output_dir,
                content_type,
            )
    except ArchiveExtractionError as exc:
        logger.warning(
            "Task %s: archive extraction failed for %s: %s",
            task.task_id,
            archive_path.name,
            exc,
        )
        return [], [], [], str(exc)

    if warnings:
        logger.debug(
            "Task %s: archive warnings for %s: %s",
            task.task_id,
            archive_path.name,
            "; ".join(warnings),
        )

    if cleanup_archive:
        run_blocking_io(archive_path.unlink, missing_ok=True)

    cleanup_paths = [output_dir]

    if not extracted_files:
        if rejected_files:
            return (
                [],
                rejected_files,
                cleanup_paths,
                _format_not_supported_error(rejected_files, task),
            )
        file_type_label = "audiobook" if check_audiobook(content_type) else "book"
        return [], rejected_files, cleanup_paths, f"No {file_type_label} files found in archive"

    logger.debug(
        "Task %s: extracted %d file(s) from archive %s",
        task.task_id,
        len(extracted_files),
        archive_path.name,
    )

    return extracted_files, rejected_files, cleanup_paths, None


def scan_directory_tree(
    directory: Path,
    content_type: str | None,
) -> tuple[list[Path], list[Path], list[Path], str | None]:
    """Scan a directory tree for book files, trackable-but-unsupported files, and archives."""
    try:

        def _probe_dir() -> None:
            # Force a fast error if the dir is missing/inaccessible.
            with os.scandir(directory) as it:
                next(it, None)

        run_blocking_io(_probe_dir)
    except PermissionError as exc:
        log_path_permission_context("scan_directory", directory)
        logger.warning("Permission denied scanning directory: %s (%s)", directory, exc)
        return [], [], [], f"Permission denied accessing download folder: {directory}"
    except (FileNotFoundError, NotADirectoryError, OSError) as exc:
        logger.warning("Cannot access download folder: %s (%s)", directory, exc)
        return [], [], [], f"Cannot access download folder: {directory} ({exc})"

    supported_formats = get_supported_formats(content_type)
    supported_exts = {f".{fmt}" for fmt in supported_formats}

    is_audiobook = check_audiobook(content_type)
    if is_audiobook:
        trackable_exts = {f".{fmt}" for fmt in AUDIOBOOK_FORMATS}
    else:
        trackable_exts = {
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

    logged_walk_permission_context = False

    def onerror(error: OSError) -> None:
        nonlocal logged_walk_permission_context

        if isinstance(error, PermissionError):
            if not logged_walk_permission_context:
                error_path = Path(getattr(error, "filename", "") or str(directory))

                log_path_permission_context("scan_directory_walk", error_path)
                logged_walk_permission_context = True

            logger.debug("Skipping inaccessible path during scan: %s", error)
        else:
            logger.debug("Error scanning directory tree: %s", error)

    def _walk_tree() -> tuple[list[Path], list[Path], list[Path]]:
        book_files: list[Path] = []
        rejected_files: list[Path] = []
        archive_files: list[Path] = []

        for root, _, files in os.walk(directory, onerror=onerror):
            for filename in files:
                file_path = Path(root) / filename
                suffix = file_path.suffix.lower()

                # zip/rar can also be enabled as supported formats. Keep archives
                # out of book_files so they are extracted rather than imported whole.
                if suffix in supported_exts and not is_archive(file_path):
                    book_files.append(file_path)
                elif suffix in trackable_exts:
                    rejected_files.append(file_path)

                if is_archive(file_path):
                    archive_files.append(file_path)

        return book_files, rejected_files, archive_files

    try:
        book_files, rejected_files, archive_files = run_blocking_io(_walk_tree)
    except PermissionError as exc:
        log_path_permission_context("scan_directory_walk", directory)
        logger.warning("Permission denied scanning directory: %s (%s)", directory, exc)
        return [], [], [], f"Permission denied accessing download folder: {directory}"
    except (FileNotFoundError, NotADirectoryError, OSError) as exc:
        logger.warning("Cannot access download folder: %s (%s)", directory, exc)
        return [], [], [], f"Cannot access download folder: {directory} ({exc})"

    return book_files, rejected_files, archive_files, None


def collect_directory_files(
    directory: Path,
    task: DownloadTask,
    *,
    allow_archive_extraction: bool,
    status_callback: Callable[[str, str | None], None] | None = None,
    cleanup_archives: bool = False,
) -> tuple[list[Path], list[Path], list[Path], str | None]:
    """Collect supported files from a directory, extracting archives when allowed."""
    content_type = task.content_type
    book_files, rejected_files, archive_files, scan_error = scan_directory_tree(
        directory, content_type
    )
    if scan_error:
        return [], [], [], scan_error

    if book_files:
        if archive_files:
            logger.debug(
                "Task %s: ignoring %d archive(s) - already have %d book file(s)",
                task.task_id,
                len(archive_files),
                len(book_files),
            )
        if rejected_files:
            rejected_exts = sorted({f.suffix.lower() for f in rejected_files})
            logger.debug(
                "Task %s: also found %d file(s) with unsupported formats: %s",
                task.task_id,
                len(rejected_files),
                ", ".join(rejected_exts),
            )
        return book_files, rejected_files, [], None

    if archive_files:
        if not allow_archive_extraction:
            # When extraction is disabled (typically due to torrent hardlinking),
            # treat archives as the final importable "files" rather than failing.
            logger.info(
                "Task %s: archive extraction disabled; importing %d archive(s) as-is from %s",
                task.task_id,
                len(archive_files),
                directory,
            )
            return archive_files, rejected_files, [], None

        if status_callback:
            status_callback("resolving", "Extracting archives")

        logger.info("Task %s: extracting %d archive(s)", task.task_id, len(archive_files))

        all_files: list[Path] = []
        all_errors: list[str] = []
        cleanup_paths: list[Path] = []

        for archive in archive_files:
            extract_dir = build_staging_dir("extract", task.task_id)
            extracted_files, archive_rejected, archive_cleanup, error = extract_archive_files(
                archive_path=archive,
                output_dir=extract_dir,
                task=task,
                cleanup_archive=cleanup_archives,
            )

            if error:
                all_errors.append(f"{archive.name}: {error}")
            if archive_rejected:
                rejected_files.extend(archive_rejected)
            if extracted_files:
                all_files.extend(extracted_files)
            if archive_cleanup:
                cleanup_paths.extend(archive_cleanup)

        if all_files:
            logger.info(
                "Task %s: extracted %d file(s) from %d archive(s)",
                task.task_id,
                len(all_files),
                len(archive_files),
            )
            return all_files, rejected_files, cleanup_paths, None

        if all_errors:
            return [], rejected_files, cleanup_paths, "; ".join(all_errors)

        if rejected_files:
            return (
                [],
                rejected_files,
                cleanup_paths,
                _format_not_supported_error(rejected_files, task),
            )

        return [], rejected_files, cleanup_paths, "No book files found in archives"

    if rejected_files:
        return [], rejected_files, [], _format_not_supported_error(rejected_files, task)

    return [], rejected_files, [], "No book files found in download"


def collect_staged_files(
    working_path: Path,
    task: DownloadTask,
    *,
    allow_archive_extraction: bool,
    status_callback: Callable[[str, str | None], None] | None,
    cleanup_archives: bool,
) -> tuple[list[Path], list[Path], list[Path], str | None]:
    """Collect supported files from a staged file or directory path."""
    if run_blocking_io(working_path.is_dir):
        if status_callback:
            status_callback("resolving", "Processing download folder")
        return collect_directory_files(
            working_path,
            task,
            allow_archive_extraction=allow_archive_extraction,
            status_callback=status_callback,
            cleanup_archives=cleanup_archives,
        )

    if is_archive(working_path) and allow_archive_extraction:
        if status_callback:
            status_callback("resolving", "Extracting archive")

        logger.info("Task %s: extracting archive %s", task.task_id, working_path.name)

        extract_dir = build_staging_dir("extract", task.task_id)
        extracted_files, rejected_files, cleanup_paths, error = extract_archive_files(
            archive_path=working_path,
            output_dir=extract_dir,
            task=task,
            cleanup_archive=cleanup_archives,
        )

        if extracted_files:
            logger.info(
                "Task %s: extracted %d file(s) from archive %s",
                task.task_id,
                len(extracted_files),
                working_path.name,
            )

        return extracted_files, rejected_files, cleanup_paths, error

    if is_archive(working_path) and not allow_archive_extraction:
        # When extraction is disabled (typically due to torrent hardlinking),
        # import the archive as-is rather than treating it as an unsupported file.
        return [working_path], [], [], None

    # Single-file download result (non-archive).
    # Ensure we respect the user's supported format settings.
    suffix = working_path.suffix.lower()
    supported_formats = get_supported_formats(task.content_type)
    supported_exts = {f".{fmt}" for fmt in supported_formats}

    is_audiobook = check_audiobook(task.content_type)
    if is_audiobook:
        trackable_exts = {f".{fmt}" for fmt in AUDIOBOOK_FORMATS}
    else:
        trackable_exts = {
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

    if suffix in supported_exts:
        return [working_path], [], [], None

    if suffix in trackable_exts:
        return [], [working_path], [], _format_not_supported_error([working_path], task)

    file_type_label = "audiobook" if is_audiobook else "book"
    return [], [], [], f"Unsupported {file_type_label} file type: {suffix or working_path.name}"
