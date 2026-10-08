"""File transfer helpers for post-processing output delivery."""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path
from typing import TYPE_CHECKING

import shelfmark.core.config as core_config
from shelfmark.core.logger import setup_logger
from shelfmark.core.naming import (
    assign_part_numbers,
    build_library_path,
    derive_primary_title,
    normalize_language_code,
    parse_naming_template,
    sanitize_filename,
)
from shelfmark.core.utils import is_audiobook as check_audiobook
from shelfmark.download.archive import is_archive
from shelfmark.download.fs import (
    atomic_copy,
    atomic_hardlink,
    atomic_move,
    run_blocking_io,
)
from shelfmark.download.postprocess.policy import (
    get_file_organization,
    get_template,
    get_word_separator,
)

from .packs import BookGroup, PackBook, group_files_into_books, match_plan_to_files
from .scan import collect_directory_files, scan_directory_tree
from .types import TransferPlan
from .workspace import safe_cleanup_path

if TYPE_CHECKING:
    from collections.abc import Callable

    from shelfmark.core.models import DownloadTask

logger = setup_logger("shelfmark.download.postprocess.pipeline")
_TRANSFER_PROCESS_ERRORS = (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError)


def should_hardlink(task: DownloadTask) -> bool:
    """Check if hardlinking is enabled for this torrent-backed task."""
    if not task.original_download_path:
        return False

    is_audiobook = check_audiobook(task.content_type)
    key = "HARDLINK_TORRENTS_AUDIOBOOK" if is_audiobook else "HARDLINK_TORRENTS"

    hardlink_enabled = core_config.config.get(key)
    if hardlink_enabled is None:
        hardlink_enabled = core_config.config.get("TORRENT_HARDLINK", False)

    return bool(hardlink_enabled)


def build_metadata_dict(task: DownloadTask) -> dict:
    """Build template metadata from a download task."""
    primary_title = derive_primary_title(task.title, task.subtitle)
    return {
        "Author": task.author,
        "Title": task.title,
        "PrimaryTitle": primary_title,
        "Subtitle": task.subtitle,
        "Year": task.year,
        "Series": task.series_name,
        "SeriesPosition": task.series_position,
        "Narrator": task.narrator,
        "Language": normalize_language_code(task.language),
        "User": task.username,
    }


def build_file_metadata(
    task: DownloadTask, source_file: Path, part_number: str | None = None
) -> dict:
    """Build template metadata for a specific source file."""
    metadata = build_metadata_dict(task)
    metadata["OriginalName"] = source_file.stem
    if part_number is not None:
        metadata["PartNumber"] = part_number
    return metadata


def resolve_hardlink_source(
    temp_file: Path,
    task: DownloadTask,
    destination: Path | None,
    status_callback: Callable[[str, str | None], None] | None = None,
) -> TransferPlan:
    """Resolve hardlink eligibility and source path for transfers."""
    use_hardlink = False
    source_path = temp_file
    hardlink_enabled = should_hardlink(task)

    if hardlink_enabled and task.original_download_path:
        hardlink_source = Path(task.original_download_path)
        hardlink_source_exists = run_blocking_io(hardlink_source.exists)
        if hardlink_source_exists:
            use_hardlink = True
            source_path = hardlink_source
            logger.info(
                "Hardlink enabled for task %s; attempting link from %s to %s",
                task.task_id,
                hardlink_source,
                destination,
            )
        else:
            logger.warning(
                "Hardlink enabled for task %s, but source path does not exist: %s",
                task.task_id,
                hardlink_source,
            )

    return TransferPlan(
        source_path=source_path,
        use_hardlink=use_hardlink,
        allow_archive_extraction=not hardlink_enabled,
        hardlink_enabled=hardlink_enabled,
    )


def is_torrent_source(source_path: Path, task: DownloadTask) -> bool:
    """Check if source is the torrent client path (needs copy to preserve seeding)."""
    if not task.original_download_path:
        return False

    original_path = Path(task.original_download_path)
    try:
        return run_blocking_io(source_path.resolve) == run_blocking_io(original_path.resolve)
    except OSError, ValueError:
        return os.path.normpath(str(source_path)) == os.path.normpath(str(original_path))


def _max_attempts_for_batch(file_count: int, default: int = 100) -> int:
    if file_count <= 1:
        return default
    return max(default, file_count + default)


def _transfer_single_file(
    source_path: Path,
    dest_path: Path,
    *,
    use_hardlink: bool,
    is_torrent: bool,
    preserve_source: bool = False,
    max_attempts: int = 100,
) -> tuple[Path, str]:
    if use_hardlink:
        final_path = atomic_hardlink(source_path, dest_path, max_attempts=max_attempts)
        try:
            if run_blocking_io(source_path.stat).st_ino == run_blocking_io(final_path.stat).st_ino:
                return final_path, "hardlink"
        except OSError:
            return final_path, "hardlink"
        return final_path, "copy"

    if is_torrent or preserve_source:
        return atomic_copy(source_path, dest_path, max_attempts=max_attempts), "copy"

    return atomic_move(source_path, dest_path, max_attempts=max_attempts), "move"


def _group_folder_name(source_root: Path | None) -> str:
    """Name the folder a grouped multi-file audiobook is transferred into.

    A directory names the group directly. A file cannot hold several book files
    on its own, so a non-directory source that produced more than one means
    `collect_staged_files` extracted an archive: the stem is the release name and
    the suffix is packaging, which is why `Book.zip` groups into `Book/` rather
    than `Book.zip/` or, worse, not at all.
    """
    if source_root is None:
        return ""
    if run_blocking_io(source_root.is_dir):
        return sanitize_filename(source_root.name)
    if is_archive(source_root):
        return sanitize_filename(source_root.stem)
    return ""


def transfer_book_files(
    book_files: list[Path],
    destination: Path,
    task: DownloadTask,
    *,
    use_hardlink: bool,
    is_torrent: bool,
    preserve_source: bool = False,
    organization_mode: str | None = None,
    source_root: Path | None = None,
) -> tuple[list[Path], str | None, dict[str, int]]:
    """Transfer discovered book files into their final destination layout."""
    if not book_files:
        return [], "No book files found", {"hardlink": 0, "copy": 0, "move": 0}

    is_audiobook = check_audiobook(task.content_type)
    organization_mode = organization_mode or get_file_organization(is_audiobook=is_audiobook)
    word_separator = get_word_separator()

    groups = resolve_book_groups(task, book_files, organization_mode=organization_mode)
    if groups is not None:
        return _transfer_book_groups(
            groups,
            destination,
            task,
            use_hardlink=use_hardlink,
            is_torrent=is_torrent,
            preserve_source=preserve_source,
            organization_mode=organization_mode,
        )

    max_attempts = _max_attempts_for_batch(len(book_files))

    final_paths: list[Path] = []
    op_counts: dict[str, int] = {"hardlink": 0, "copy": 0, "move": 0}

    if organization_mode == "organize":
        template = get_template(is_audiobook=is_audiobook, organization_mode="organize")

        if len(book_files) == 1:
            source_file = book_files[0]
            ext = source_file.suffix.lstrip(".") or task.format or ""
            file_metadata = build_file_metadata(task, source_file)
            dest_path = run_blocking_io(
                build_library_path,
                str(destination),
                template,
                file_metadata,
                extension=ext or None,
                word_separator=word_separator,
            )
            run_blocking_io(dest_path.parent.mkdir, parents=True, exist_ok=True)

            final_path, op = _transfer_single_file(
                source_file,
                dest_path,
                use_hardlink=use_hardlink,
                is_torrent=is_torrent,
                preserve_source=preserve_source,
                max_attempts=max_attempts,
            )
            final_paths.append(final_path)
            op_counts[op] = op_counts.get(op, 0) + 1
            logger.debug("%s to destination: %s", op.capitalize(), final_path.name)
        else:
            zero_pad_width = max(len(str(len(book_files))), 2)
            files_with_parts = assign_part_numbers(book_files, zero_pad_width)

            for source_file, part_number in files_with_parts:
                ext = source_file.suffix.lstrip(".") or task.format or ""
                file_metadata = build_file_metadata(task, source_file, part_number=part_number)
                dest_path = run_blocking_io(
                    build_library_path,
                    str(destination),
                    template,
                    file_metadata,
                    extension=ext or None,
                    word_separator=word_separator,
                )
                run_blocking_io(dest_path.parent.mkdir, parents=True, exist_ok=True)

                final_path, op = _transfer_single_file(
                    source_file,
                    dest_path,
                    use_hardlink=use_hardlink,
                    is_torrent=is_torrent,
                    preserve_source=preserve_source,
                    max_attempts=max_attempts,
                )
                final_paths.append(final_path)
                op_counts[op] = op_counts.get(op, 0) + 1
                logger.debug("%s to destination: %s", op.capitalize(), final_path.name)

        return final_paths, None, op_counts

    transfer_destination = destination
    if is_audiobook and len(book_files) > 1 and organization_mode == "rename_and_group":
        source_folder = _group_folder_name(source_root)
        if source_folder:
            transfer_destination = destination / source_folder
            run_blocking_io(transfer_destination.mkdir, parents=True, exist_ok=True)

    for book_file in book_files:
        if len(book_files) == 1 and organization_mode != "none":
            if not task.format:
                task.format = book_file.suffix.lower().lstrip(".")

            template = get_template(is_audiobook=is_audiobook, organization_mode="rename")
            metadata = build_file_metadata(task, book_file)
            extension = book_file.suffix.lstrip(".") or task.format or ""

            filename = parse_naming_template(
                template, metadata, allow_path_separators=False, word_separator=word_separator
            )
            filename = Path(filename).name if filename else ""
            if filename and extension:
                filename = f"{sanitize_filename(filename)}.{extension}"
            else:
                filename = book_file.name
        else:
            filename = book_file.name

        dest_path = transfer_destination / filename
        final_path, op = _transfer_single_file(
            book_file,
            dest_path,
            use_hardlink=use_hardlink,
            is_torrent=is_torrent,
            preserve_source=preserve_source,
            max_attempts=max_attempts,
        )
        final_paths.append(final_path)
        op_counts[op] = op_counts.get(op, 0) + 1
        logger.debug("%s to destination: %s", op.capitalize(), final_path.name)

    return final_paths, None, op_counts


def resolve_book_groups(
    task: DownloadTask,
    book_files: list[Path],
    *,
    organization_mode: str,
) -> list[BookGroup] | None:
    """Split a multi-book pack into per-book groups, or None to file as one book.

    An approved `book_plan` wins; a bare `multi_book` flag falls back to heuristic
    grouping. Organization `none` keeps files as-is, and a split that yields a single
    group is not a pack at all.
    """
    if organization_mode == "none" or not (task.book_plan or task.multi_book):
        return None
    if task.book_plan:
        plan = [
            PackBook(
                title=str(entry.get("title") or ""),
                series_position=entry.get("series_position"),
                year=entry.get("year"),
                files=list(entry.get("files") or []),
            )
            for entry in task.book_plan
            if isinstance(entry, dict)
        ]
        groups = match_plan_to_files(
            plan, book_files, series_name=task.series_name, author_name=task.author
        )
    else:
        groups = group_files_into_books(
            book_files, series_name=task.series_name, author_name=task.author
        )
    return groups if len(groups) > 1 else None


def _transfer_book_groups(
    groups: list[BookGroup],
    destination: Path,
    task: DownloadTask,
    *,
    use_hardlink: bool,
    is_torrent: bool,
    preserve_source: bool,
    organization_mode: str,
) -> tuple[list[Path], str | None, dict[str, int]]:
    """Transfer each book of a pack through the normal single-book path.

    Each book gets an isolated task copy (the single-file path mutates `task.format`)
    carrying its own title, position and year; the searched book's position must not
    leak onto its siblings, while author and series name apply to all of them.
    """
    all_paths: list[Path] = []
    totals: dict[str, int] = {"hardlink": 0, "copy": 0, "move": 0}
    errors: list[str] = []

    for group in groups:
        book_task = dataclasses.replace(
            task,
            title=group.title or task.title,
            year=str(group.year) if group.year is not None else None,
            subtitle=None,
            series_position=group.series_position,
            multi_book=False,
            book_plan=None,
        )
        paths, error, op_counts = transfer_book_files(
            group.files,
            destination,
            book_task,
            use_hardlink=use_hardlink,
            is_torrent=is_torrent,
            preserve_source=preserve_source,
            organization_mode=organization_mode,
            source_root=group.files[0].parent,
        )
        for op, count in op_counts.items():
            totals[op] = totals.get(op, 0) + count
        if error:
            errors.append(f"{group.title}: {error}")
            logger.warning("Task %s: pack book %r failed: %s", task.task_id, group.title, error)
            continue
        all_paths.extend(paths)

    if not all_paths:
        return [], "; ".join(errors) or "No book files found", totals
    if errors:
        logger.warning(
            "Task %s: pack filed with %d failed book(s): %s",
            task.task_id,
            len(errors),
            "; ".join(errors),
        )
    return all_paths, None, totals


def process_directory(
    directory: Path,
    ingest_dir: Path,
    task: DownloadTask,
    *,
    allow_archive_extraction: bool = True,
    use_hardlink: bool | None = None,
) -> tuple[list[Path], str | None]:
    """Process staged directory: find book files, extract archives, move to ingest."""
    try:
        is_torrent = is_torrent_source(directory, task)
        book_files, _, cleanup_paths, error = collect_directory_files(
            directory,
            task,
            allow_archive_extraction=allow_archive_extraction,
            status_callback=None,
            cleanup_archives=not is_torrent,
        )

        if error:
            if not is_torrent:
                safe_cleanup_path(directory, task)
                for cleanup_path in cleanup_paths:
                    safe_cleanup_path(cleanup_path, task)
            return [], error

        if use_hardlink is None:
            use_hardlink = should_hardlink(task)

        final_paths, error, _op_counts = transfer_book_files(
            book_files,
            destination=ingest_dir,
            task=task,
            use_hardlink=use_hardlink,
            is_torrent=is_torrent,
        )

        if error:
            return [], error

        if not is_torrent:
            safe_cleanup_path(directory, task)
            for cleanup_path in cleanup_paths:
                safe_cleanup_path(cleanup_path, task)

        processed_paths = final_paths

    except _TRANSFER_PROCESS_ERRORS as exc:
        logger.error_trace(
            "Task %s: error processing directory %s: %s", task.task_id, directory, exc
        )
        if not is_torrent_source(directory, task):
            safe_cleanup_path(directory, task)
        return [], str(exc)
    else:
        return processed_paths, None


def transfer_file_to_library(
    source_path: Path,
    library_base: str,
    template: str,
    metadata: dict,
    task: DownloadTask,
    temp_file: Path | None,
    status_callback: Callable[[str, str | None], None],
    *,
    use_hardlink: bool,
) -> str | None:
    """Transfer a single file into a library path derived from metadata."""
    extension = source_path.suffix.lstrip(".") or task.format
    template_metadata = dict(metadata)
    template_metadata.setdefault("OriginalName", source_path.stem)
    dest_path = run_blocking_io(
        build_library_path,
        library_base,
        template,
        template_metadata,
        extension,
        word_separator=get_word_separator(),
    )
    run_blocking_io(dest_path.parent.mkdir, parents=True, exist_ok=True)

    is_torrent = is_torrent_source(source_path, task)
    final_path, op = _transfer_single_file(
        source_path,
        dest_path,
        use_hardlink=use_hardlink,
        is_torrent=is_torrent,
        max_attempts=_max_attempts_for_batch(1),
    )
    logger.info("Library %s: %s", op, final_path)
    if use_hardlink and op != "hardlink":
        logger.warning(
            "Library hardlink requested but %s used instead for %s",
            op,
            final_path,
        )

    if use_hardlink and temp_file and not is_torrent_source(temp_file, task):
        safe_cleanup_path(temp_file, task)

    status_callback("complete", "Complete")
    return str(final_path)


def transfer_directory_to_library(
    source_dir: Path,
    library_base: str,
    template: str,
    metadata: dict,
    task: DownloadTask,
    temp_file: Path | None,
    status_callback: Callable[[str, str | None], None],
    *,
    use_hardlink: bool,
) -> str | None:
    """Transfer a directory tree into a library path derived from metadata."""
    content_type = task.content_type.lower() if task.content_type else None
    source_files, _, _, scan_error = scan_directory_tree(source_dir, content_type)
    if scan_error:
        logger.warning(scan_error)
        status_callback("error", scan_error)
        if temp_file:
            safe_cleanup_path(temp_file, task)
        return None

    if not source_files:
        logger.warning("No supported files in %s", source_dir.name)
        status_callback("error", "No supported file formats found")
        if temp_file:
            safe_cleanup_path(temp_file, task)
        return None

    word_separator = get_word_separator()
    base_library_path = run_blocking_io(
        build_library_path,
        library_base,
        template,
        metadata,
        extension=None,
        word_separator=word_separator,
    )
    run_blocking_io(base_library_path.parent.mkdir, parents=True, exist_ok=True)

    is_torrent = is_torrent_source(source_dir, task)
    transferred_paths: list[Path] = []
    op_counts: dict[str, int] = {"hardlink": 0, "copy": 0, "move": 0}
    max_attempts = _max_attempts_for_batch(len(source_files))

    if len(source_files) == 1:
        source_file = source_files[0]
        ext = source_file.suffix.lstrip(".")
        dest_path = base_library_path.with_suffix(f".{ext}")
        final_path, op = _transfer_single_file(
            source_file,
            dest_path,
            use_hardlink=use_hardlink,
            is_torrent=is_torrent,
            max_attempts=max_attempts,
        )
        logger.debug("Library %s: %s -> %s", op, source_file.name, final_path)
        transferred_paths.append(final_path)
        op_counts[op] = op_counts.get(op, 0) + 1
    else:
        zero_pad_width = max(len(str(len(source_files))), 2)
        files_with_parts = assign_part_numbers(source_files, zero_pad_width)

        for source_file, part_number in files_with_parts:
            ext = source_file.suffix.lstrip(".")
            file_metadata = {**metadata, "PartNumber": part_number}
            file_path = run_blocking_io(
                build_library_path,
                library_base,
                template,
                file_metadata,
                extension=ext,
                word_separator=word_separator,
            )
            run_blocking_io(file_path.parent.mkdir, parents=True, exist_ok=True)

            final_path, op = _transfer_single_file(
                source_file,
                file_path,
                use_hardlink=use_hardlink,
                is_torrent=is_torrent,
                max_attempts=max_attempts,
            )
            logger.debug("Library %s: %s -> %s", op, source_file.name, final_path)
            transferred_paths.append(final_path)
            op_counts[op] = op_counts.get(op, 0) + 1

    op_summary = ", ".join(f"{op}={count}" for op, count in op_counts.items() if count) or "none"
    logger.info(
        "Created %d library file(s) in %s (ops: %s)",
        len(transferred_paths),
        base_library_path.parent,
        op_summary,
    )
    if use_hardlink and op_counts.get("copy", 0):
        logger.warning(
            "Library hardlink requested but %d of %d file(s) copied (fallback)",
            op_counts.get("copy", 0),
            len(transferred_paths),
        )

    if use_hardlink and temp_file and not is_torrent_source(temp_file, task):
        safe_cleanup_path(temp_file, task)
    elif not is_torrent:
        safe_cleanup_path(temp_file, task)
        safe_cleanup_path(source_dir, task)

    message = (
        f"Complete ({len(transferred_paths)} files)" if len(transferred_paths) > 1 else "Complete"
    )
    status_callback("complete", message)

    return str(transferred_paths[0])
