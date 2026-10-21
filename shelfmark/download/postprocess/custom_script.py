"""Custom script execution helpers for post-processing hooks."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import shelfmark.core.config as core_config
from shelfmark.core.logger import setup_logger
from shelfmark.download.fs import run_blocking_io

from .steps import log_plan_steps, record_step

if TYPE_CHECKING:
    from collections.abc import Callable

    from shelfmark.core.models import DownloadTask

    from .types import PlanStep

logger = setup_logger(__name__)

DEFAULT_CUSTOM_SCRIPT_TIMEOUT_SECONDS = 300  # 5 minutes


def resolve_custom_script_target(target_path: Path, destination: Path, path_mode: str) -> Path:
    """Resolve the path that should be passed as the custom script argument.

    In absolute mode, we pass the full target path.

    In relative mode, we pass a path relative to the destination folder. If the
    target is not within the destination, fall back to just the filename to
    avoid leaking unrelated absolute paths.
    """
    mode = (path_mode or "absolute").strip().lower()
    if mode != "relative":
        return target_path

    try:
        return target_path.relative_to(destination)
    except ValueError:
        if target_path.is_absolute():
            return Path(target_path.name)
    return target_path


@dataclass(frozen=True)
class CustomScriptExecution:
    """Resolved command inputs for a single custom script run."""

    script_path: str
    target_arg: Path
    target_abs: Path
    destination: Path
    mode: str
    phase: str
    payload_json: str | None = None


@dataclass(frozen=True)
class CustomScriptTransferSummary:
    """Transfer metadata exposed to custom post-process scripts."""

    op_counts: dict[str, int]
    use_hardlink: bool
    is_torrent: bool
    preserve_source: bool


@dataclass(frozen=True)
class CustomScriptContext:
    """Runtime context exposed to custom post-process scripts."""

    task: DownloadTask
    phase: str
    output_mode: str
    destination: Path | None = None
    final_paths: list[Path] = field(default_factory=list)
    target_path: Path | None = None
    organization_mode: str | None = None
    transfer: CustomScriptTransferSummary | None = None
    output_details: dict[str, Any] = field(default_factory=dict)


def prepare_custom_script_execution(
    script_path: str,
    *,
    target_path: Path,
    destination: Path,
    path_mode: str,
    phase: str,
    payload: dict[str, Any] | None = None,
) -> CustomScriptExecution:
    """Resolve script arguments and payload for a custom hook invocation."""
    mode = (path_mode or "absolute").strip().lower()
    if mode != "relative":
        mode = "absolute"

    target_arg = resolve_custom_script_target(target_path, destination, mode)
    return CustomScriptExecution(
        script_path=str(script_path),
        target_arg=target_arg,
        target_abs=target_path,
        destination=destination,
        mode=mode,
        phase=phase,
        payload_json=json.dumps(payload, indent=2, sort_keys=True) + "\n" if payload else None,
    )


def _target_argument(execution: CustomScriptExecution) -> str:
    """The target as one argv entry; a relative name starting with "-" gets "./" first.

    Many scripts read a leading "-" as an option. Path() drops "./", so this has
    to be applied to the string. Absolute mode starts with "/" and is unchanged.
    """
    target = str(execution.target_arg)
    if execution.mode == "relative" and target.startswith("-"):
        return f"./{target}"
    return target


def run_custom_script(
    execution: CustomScriptExecution,
    *,
    task_id: str,
    status_callback: Callable[[str, str | None], None],
    timeout_seconds: int = DEFAULT_CUSTOM_SCRIPT_TIMEOUT_SECONDS,
) -> bool:
    """Run a prepared custom script and report success."""
    cwd: str | None = None
    if execution.mode == "relative":
        # Make relative paths unambiguous by running the script from the destination folder.
        cwd = str(execution.destination)

    logger.info(
        "Task %s: running custom script %s on %s (%s)",
        task_id,
        execution.script_path,
        execution.target_arg,
        execution.phase,
    )

    try:
        run_kwargs: dict[str, Any] = {
            "check": True,
            "timeout": timeout_seconds,
            "capture_output": True,
            "text": True,
            "cwd": cwd,
        }
        # If we are not sending a JSON payload, close stdin so scripts that try
        # to read it won't block indefinitely. When we do send a payload, let
        # subprocess.run manage stdin implicitly via `input=` to avoid passing
        # both arguments at once.
        if execution.payload_json is None:
            run_kwargs["stdin"] = subprocess.DEVNULL
        else:
            run_kwargs["input"] = execution.payload_json

        result = run_blocking_io(
            subprocess.run,
            [execution.script_path, _target_argument(execution)],
            **run_kwargs,
        )
        if result.stdout:
            logger.debug("Task %s: custom script stdout: %s", task_id, result.stdout.strip())
    except FileNotFoundError:
        logger.exception("Task %s: custom script not found: %s", task_id, execution.script_path)
        status_callback("error", f"Custom script not found: {execution.script_path}")
        return False
    except PermissionError:
        logger.exception(
            "Task %s: custom script not executable: %s", task_id, execution.script_path
        )
        status_callback("error", f"Custom script not executable: {execution.script_path}")
        return False
    except subprocess.TimeoutExpired:
        logger.exception(
            "Task %s: custom script timed out after %ss: %s",
            task_id,
            timeout_seconds,
            execution.script_path,
        )
        status_callback("error", "Custom script timed out")
        return False
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.strip() if exc.stderr else "No error output"
        logger.exception(
            "Task %s: custom script failed (exit code %s): %s",
            task_id,
            exc.returncode,
            stderr,
        )
        status_callback("error", f"Custom script failed: {stderr[:100]}")
        return False
    else:
        return True


def _choose_custom_script_target(
    *,
    explicit_target: Path | None,
    destination: Path | None,
    final_paths: list[Path],
) -> Path | None:
    if explicit_target is not None:
        return explicit_target

    if len(final_paths) == 1:
        return final_paths[0]

    if len(final_paths) > 1:
        try:
            return Path(os.path.commonpath([str(p.parent) for p in final_paths]))
        except ValueError:
            return destination or final_paths[0].parent

    return destination


def _build_custom_script_payload(
    context: CustomScriptContext, *, target_path: Path
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "version": 1,
        "phase": context.phase,
        "task": {
            "task_id": context.task.task_id,
            "source": context.task.source,
            "search_mode": context.task.search_mode.value if context.task.search_mode else None,
            "title": context.task.title,
            "author": context.task.author,
            "year": context.task.year,
            "format": context.task.format,
            "content_type": context.task.content_type,
            "series_name": context.task.series_name,
            "series_position": context.task.series_position,
            "subtitle": context.task.subtitle,
            "language": context.task.language,
            "original_download_path": context.task.original_download_path,
        },
        "output": {
            "mode": context.output_mode,
            "organization_mode": context.organization_mode,
        },
        "paths": {
            "destination": str(context.destination) if context.destination else None,
            "target": str(target_path),
            "final_paths": [str(p) for p in context.final_paths],
        },
    }

    if context.output_details:
        payload["output"]["details"] = context.output_details

    if context.transfer:
        payload["transfer"] = {
            "op_counts": context.transfer.op_counts,
            "use_hardlink": context.transfer.use_hardlink,
            "is_torrent": context.transfer.is_torrent,
            "preserve_source": context.transfer.preserve_source,
        }

    return payload


def maybe_run_custom_script(
    context: CustomScriptContext,
    *,
    status_callback: Callable[[str, str | None], None],
    steps: list[PlanStep] | None = None,
) -> bool:
    """Run the custom script hook (if configured).

    The output handler provides a `CustomScriptContext` describing what it did.
    This function is responsible for choosing the script target, building the
    optional JSON payload, and executing the script.
    """
    script_path = getattr(core_config.config, "CUSTOM_SCRIPT", None)
    if not isinstance(script_path, str) or not script_path.strip():
        return True

    target_path = _choose_custom_script_target(
        explicit_target=context.target_path,
        destination=context.destination,
        final_paths=context.final_paths,
    )
    if not target_path:
        logger.warning(
            "Task %s: custom script configured but no target could be determined; skipping",
            context.task.task_id,
        )
        return True

    configured_path_mode = core_config.config.get("CUSTOM_SCRIPT_PATH_MODE", "absolute")
    path_mode = configured_path_mode if isinstance(configured_path_mode, str) else "absolute"

    payload: dict[str, Any] | None = None
    if core_config.config.get("CUSTOM_SCRIPT_JSON_PAYLOAD", False):
        payload = _build_custom_script_payload(context, target_path=target_path)

    # If no destination is available for this output, fall back to the target's
    # parent directory so the script can still run consistently.
    execution_destination = context.destination or target_path.parent

    execution = prepare_custom_script_execution(
        script_path,
        target_path=target_path,
        destination=execution_destination,
        path_mode=path_mode,
        phase=context.phase,
        payload=payload,
    )

    if steps is not None:
        payload_bytes = len(execution.payload_json.encode("utf-8")) if execution.payload_json else 0
        record_step(
            steps,
            "custom_script",
            script=str(execution.script_path),
            target=str(execution.target_arg),
            target_abs=str(execution.target_abs),
            mode=str(execution.mode),
            phase=str(execution.phase),
            payload_stdin=bool(execution.payload_json),
            payload_bytes=payload_bytes,
        )
        log_plan_steps(context.task.task_id, steps)

    return run_custom_script(
        execution, task_id=context.task.task_id, status_callback=status_callback
    )
