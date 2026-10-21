"""Folder output handler for copying or linking files into a target directory."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import shelfmark.core.config as core_config
from shelfmark.core.logger import setup_logger
from shelfmark.core.utils import is_audiobook as check_audiobook
from shelfmark.download.outputs import StatusCallback, register_output
from shelfmark.download.staging import STAGE_NONE, StageAction

if TYPE_CHECKING:
    from pathlib import Path
    from threading import Event

    from shelfmark.core.models import DownloadTask

logger = setup_logger(__name__)

FOLDER_OUTPUT_MODE = "folder"


def _format_op_counts(op_counts: dict[str, int]) -> str:
    parts = [f"{op}={count}" for op, count in op_counts.items() if count]
    return ", ".join(parts) if parts else "none"


@dataclass(frozen=True)
class _ProcessingPlan:
    destination: Path
    organization_mode: str
    use_hardlink: bool
    allow_archive_extraction: bool
    stage_action: StageAction
    staging_dir: Path
    hardlink_source: Path | None
    output_mode: str = FOLDER_OUTPUT_MODE


def _supports_folder_output(task: DownloadTask) -> bool:
    return True


def _build_processing_plan(
    temp_file: Path,
    task: DownloadTask,
    status_callback: StatusCallback,
) -> _ProcessingPlan | None:
    from shelfmark.download.postprocess.pipeline import (
        build_output_plan,
        get_final_destination,
        validate_destination,
    )
    from shelfmark.download.postprocess.policy import get_file_organization

    is_audiobook = check_audiobook(task.content_type)
    organization_mode = get_file_organization(is_audiobook=is_audiobook)
    destination = get_final_destination(task)

    if not validate_destination(destination, status_callback):
        return None

    output_plan = build_output_plan(
        temp_file,
        task,
        output_mode=FOLDER_OUTPUT_MODE,
        destination=destination,
        status_callback=status_callback,
    )
    if not output_plan.transfer_plan:
        return None

    transfer_plan = output_plan.transfer_plan
    hardlink_source = transfer_plan.source_path if transfer_plan.use_hardlink else None

    return _ProcessingPlan(
        destination=destination,
        organization_mode=organization_mode,
        use_hardlink=transfer_plan.use_hardlink,
        allow_archive_extraction=transfer_plan.allow_archive_extraction,
        stage_action=output_plan.stage_action,
        staging_dir=output_plan.staging_dir,
        hardlink_source=hardlink_source,
    )


@register_output(FOLDER_OUTPUT_MODE, supports_task=_supports_folder_output, priority=0)
def process_folder_output(
    temp_file: Path,
    task: DownloadTask,
    cancel_flag: Event,
    status_callback: StatusCallback,
    *,
    preserve_source_on_failure: bool = False,
) -> str | None:
    """Post-process download to the configured folder destination."""
    from shelfmark.download.postprocess.packs import drop_ignored_files
    from shelfmark.download.postprocess.pipeline import (
        CustomScriptContext,
        CustomScriptTransferSummary,
        cleanup_output_staging,
        is_torrent_source,
        log_plan_steps,
        maybe_run_custom_script,
        prepare_output_files,
        record_step,
        resolve_book_groups,
        transfer_book_files,
    )

    plan = _build_processing_plan(temp_file, task, status_callback)
    if not plan:
        return None

    logger.debug(
        "Processing plan for task %s: mode=%s destination=%s hardlink=%s stage_action=%s extract_archives=%s",
        task.task_id,
        plan.organization_mode,
        plan.destination,
        plan.use_hardlink,
        plan.stage_action,
        plan.allow_archive_extraction,
    )

    prepared = prepare_output_files(
        temp_file,
        task,
        output_mode=plan.output_mode,
        status_callback=status_callback,
        destination=plan.destination,
        preserve_source_on_failure=preserve_source_on_failure,
    )
    if not prepared:
        return None

    steps: list[Any] = []
    if prepared.output_plan.stage_action != STAGE_NONE:
        step_name = f"stage_{prepared.output_plan.stage_action}"
        record_step(
            steps,
            step_name,
            source=str(temp_file),
            dest=str(prepared.output_plan.staging_dir),
        )

    # Custom script is run post-transfer (see below).

    # If we staged into TMP_DIR, transfer from the staged path and disable hardlinking.
    use_hardlink = plan.use_hardlink and prepared.output_plan.stage_action == STAGE_NONE
    source_path = (
        plan.hardlink_source if use_hardlink and plan.hardlink_source else prepared.working_path
    )
    is_torrent = is_torrent_source(source_path, task)

    usenet_action = core_config.config.get("PROWLARR_USENET_ACTION", "move")
    is_usenet = task.source == "prowlarr" and not task.original_download_path

    # For external usenet downloads, always copy from the client path.
    # "Move" is implemented as a client-side cleanup after import.
    preserve_source = is_usenet or preserve_source_on_failure

    copy_for_label = (
        is_torrent or preserve_source or prepared.output_plan.stage_action != STAGE_NONE
    )

    if cancel_flag.is_set():
        logger.info("Task %s: cancelled before final transfer", task.task_id)
        cleanup_output_staging(
            prepared.output_plan,
            prepared.working_path,
            task,
            prepared.cleanup_paths,
        )
        return None

    if use_hardlink:
        op_label = "Hardlinking"
    elif is_usenet and usenet_action == "move" and prepared.output_plan.stage_action == STAGE_NONE:
        # Presented as a move, but implemented as copy + client cleanup.
        op_label = "Moving"
    elif copy_for_label:
        op_label = "Copying"
    else:
        op_label = "Moving"

    status_callback("resolving", f"{op_label} file")
    record_step(
        steps,
        "transfer",
        op=op_label.lower(),
        source=str(source_path),
        dest=str(plan.destination),
        hardlink=use_hardlink,
        torrent=copy_for_label,
    )
    if prepared.output_plan.stage_action != STAGE_NONE:
        record_step(steps, "cleanup_staging", path=str(prepared.working_path))
    log_plan_steps(task.task_id, steps)

    final_paths, error, op_counts = transfer_book_files(
        prepared.files,
        destination=plan.destination,
        task=task,
        use_hardlink=use_hardlink,
        is_torrent=is_torrent,
        preserve_source=preserve_source,
        organization_mode=plan.organization_mode,
        source_root=source_path,
    )

    if error:
        logger.warning("Task %s: transfer failed: %s", task.task_id, error)
        status_callback("error", error)
        return None

    logger.info(
        "Task %s: transferred %d file(s) to %s (ops: %s)",
        task.task_id,
        len(final_paths),
        plan.destination,
        _format_op_counts(op_counts),
    )
    if use_hardlink and op_counts.get("copy", 0):
        logger.warning(
            "Task %s: hardlink requested but %d of %d file(s) copied (fallback)",
            task.task_id,
            op_counts.get("copy", 0),
            len(final_paths),
        )

    script_context = CustomScriptContext(
        task=task,
        phase="post_transfer",
        output_mode=plan.output_mode,
        organization_mode=plan.organization_mode,
        destination=plan.destination,
        final_paths=final_paths,
        transfer=CustomScriptTransferSummary(
            op_counts=op_counts,
            use_hardlink=use_hardlink,
            is_torrent=is_torrent,
            preserve_source=preserve_source,
        ),
    )

    if not maybe_run_custom_script(script_context, status_callback=status_callback, steps=steps):
        if not preserve_source_on_failure:
            cleanup_output_staging(
                prepared.output_plan,
                prepared.working_path,
                task,
                prepared.cleanup_paths,
            )
        return None

    cleanup_output_staging(
        prepared.output_plan,
        prepared.working_path,
        task,
        prepared.cleanup_paths,
    )

    pack_groups = resolve_book_groups(
        task,
        drop_ignored_files(prepared.files, task.ignored_files),
        organization_mode=plan.organization_mode,
    )
    if pack_groups is not None:
        message = f"Complete ({len(pack_groups)} {'book' if len(pack_groups) == 1 else 'books'}, {len(final_paths)} files)"
    elif len(final_paths) == 1:
        message = "Complete"
    else:
        message = f"Complete ({len(final_paths)} files)"
    status_callback("complete", message)

    task.download_paths = [str(path) for path in final_paths]
    return str(final_paths[0])
