"""Booklore output integration for uploading completed downloads."""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

import requests

import shelfmark.core.config as core_config
from shelfmark.core.logger import setup_logger
from shelfmark.core.utils import is_audiobook as check_audiobook
from shelfmark.download.outputs import StatusCallback, register_output
from shelfmark.download.staging import (
    STAGE_COPY,
    STAGE_MOVE,
    STAGE_NONE,
    build_staging_dir,
    get_staging_dir,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from threading import Event

    from shelfmark.core.models import DownloadTask

logger = setup_logger(__name__)

BOOKLORE_OUTPUT_MODE = "booklore"
BOOKLORE_DESTINATION_LIBRARY = "library"
BOOKLORE_DESTINATION_BOOKDROP = "bookdrop"
BOOKLORE_SUPPORTED_EXTENSIONS = {
    ".azw",
    ".azw3",
    ".cb7",
    ".cbr",
    ".cbz",
    ".epub",
    ".fb2",
    ".mobi",
    ".pdf",
}
BOOKLORE_SUPPORTED_FORMATS_LABEL = ", ".join(
    ext.lstrip(".").upper() for ext in sorted(BOOKLORE_SUPPORTED_EXTENSIONS)
)
BOOKLORE_DISPLAY_NAME = "Grimmory"


class BookloreError(Exception):
    """Raised when Booklore integration fails."""


@dataclass(frozen=True)
class BookloreConfig:
    """Configuration required to upload files into Booklore."""

    base_url: str
    username: str
    password: str
    library_id: int
    path_id: int
    verify_tls: bool = True
    upload_to_bookdrop: bool = False
    refresh_after_upload: bool = False


def _parse_int(value: object, label: str) -> int:
    if value is None or value == "":
        msg = f"{label} is required"
        raise BookloreError(msg)
    if not isinstance(value, (int, float, str)):
        msg = f"{label} must be a number"
        raise BookloreError(msg)
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        msg = f"{label} must be a number"
        raise BookloreError(msg) from exc


def _parse_destination(value: object) -> str:
    normalized = str(value or "").strip().lower()
    if normalized == BOOKLORE_DESTINATION_BOOKDROP:
        return BOOKLORE_DESTINATION_BOOKDROP
    return BOOKLORE_DESTINATION_LIBRARY


def build_booklore_config(
    values: Mapping[str, Any],
    user_id: int | None = None,
) -> BookloreConfig:
    """Build and validate the effective Booklore configuration."""
    base_url = str(values.get("BOOKLORE_HOST", "")).strip()
    username = str(values.get("BOOKLORE_USERNAME", "")).strip()
    password = values.get("BOOKLORE_PASSWORD", "") or ""

    if not base_url:
        msg = f"{BOOKLORE_DISPLAY_NAME} URL is required"
        raise BookloreError(msg)
    if not username:
        msg = f"{BOOKLORE_DISPLAY_NAME} username is required"
        raise BookloreError(msg)
    if not password:
        msg = f"{BOOKLORE_DISPLAY_NAME} password is required"
        raise BookloreError(msg)

    destination = _parse_destination(
        values.get("BOOKLORE_DESTINATION", BOOKLORE_DESTINATION_LIBRARY)
    )
    upload_to_bookdrop = destination == BOOKLORE_DESTINATION_BOOKDROP

    # Resolve library/path through config so user override precedence is centralized.
    library_id = 0
    path_id = 0
    if not upload_to_bookdrop:
        if user_id is not None:
            library_id_val = core_config.config.get(
                "BOOKLORE_LIBRARY_ID",
                values.get("BOOKLORE_LIBRARY_ID"),
                user_id=user_id,
            )
            path_id_val = core_config.config.get(
                "BOOKLORE_PATH_ID",
                values.get("BOOKLORE_PATH_ID"),
                user_id=user_id,
            )
        else:
            library_id_val = values.get("BOOKLORE_LIBRARY_ID")
            path_id_val = values.get("BOOKLORE_PATH_ID")

        library_id = _parse_int(library_id_val, f"{BOOKLORE_DISPLAY_NAME} library ID")
        path_id = _parse_int(path_id_val, f"{BOOKLORE_DISPLAY_NAME} path ID")

    return BookloreConfig(
        base_url=base_url.rstrip("/"),
        username=username,
        password=password,
        library_id=library_id,
        path_id=path_id,
        verify_tls=True,
        upload_to_bookdrop=upload_to_bookdrop,
        refresh_after_upload=not upload_to_bookdrop,
    )


def booklore_login(booklore_config: BookloreConfig) -> str:
    """Authenticate with Booklore and return an API token."""
    url = f"{booklore_config.base_url}/api/v1/auth/login"
    payload = {
        "username": booklore_config.username,
        "password": booklore_config.password,
    }

    try:
        response = requests.post(url, json=payload, timeout=30, verify=booklore_config.verify_tls)
    except requests.exceptions.ConnectionError as exc:
        msg = f"Could not connect to {BOOKLORE_DISPLAY_NAME}"
        raise BookloreError(msg) from exc
    except requests.exceptions.Timeout as exc:
        msg = f"{BOOKLORE_DISPLAY_NAME} connection timed out"
        raise BookloreError(msg) from exc
    except requests.exceptions.RequestException as exc:
        msg = f"{BOOKLORE_DISPLAY_NAME} login failed: {exc}"
        raise BookloreError(msg) from exc

    if response.status_code in {401, 403}:
        msg = f"{BOOKLORE_DISPLAY_NAME} authentication failed"
        raise BookloreError(msg)

    try:
        response.raise_for_status()
    except requests.exceptions.HTTPError as exc:
        msg = f"{BOOKLORE_DISPLAY_NAME} login failed ({response.status_code})"
        raise BookloreError(msg) from exc

    try:
        data = response.json()
    except ValueError as exc:
        msg = f"Invalid {BOOKLORE_DISPLAY_NAME} login response"
        raise BookloreError(msg) from exc

    token = data.get("accessToken")
    if not token:
        msg = f"{BOOKLORE_DISPLAY_NAME} did not return an access token"
        raise BookloreError(msg)

    return token


def booklore_list_libraries(booklore_config: BookloreConfig, token: str) -> list[dict[str, Any]]:
    """Fetch the available Booklore libraries for the current user."""
    url = f"{booklore_config.base_url}/api/v1/libraries"
    headers = {"Authorization": f"Bearer {token}"}

    try:
        response = requests.get(url, headers=headers, timeout=30, verify=booklore_config.verify_tls)
        response.raise_for_status()
    except requests.exceptions.RequestException as exc:
        msg = f"Failed to fetch {BOOKLORE_DISPLAY_NAME} libraries: {exc}"
        raise BookloreError(msg) from exc

    try:
        return response.json()
    except ValueError as exc:
        msg = f"Invalid {BOOKLORE_DISPLAY_NAME} libraries response"
        raise BookloreError(msg) from exc


def booklore_upload_file(booklore_config: BookloreConfig, token: str, file_path: Path) -> None:
    """Upload a completed file into Booklore."""
    if booklore_config.upload_to_bookdrop:
        url = f"{booklore_config.base_url}/api/v1/files/upload/bookdrop"
        params = None
    else:
        url = f"{booklore_config.base_url}/api/v1/files/upload"
        params = {
            "libraryId": booklore_config.library_id,
            "pathId": booklore_config.path_id,
        }

    headers = {"Authorization": f"Bearer {token}"}

    response = None

    try:
        with file_path.open("rb") as handle:
            response = requests.post(
                url,
                headers=headers,
                params=params,
                files={"file": (file_path.name, handle)},
                timeout=60,
                verify=booklore_config.verify_tls,
            )
        response.raise_for_status()
    except requests.exceptions.HTTPError as exc:
        message = response.text.strip() if response is not None else ""
        if message:
            message = f": {message[:200]}"
        status_code = response.status_code if response is not None else "unknown"
        msg = f"{BOOKLORE_DISPLAY_NAME} upload failed ({status_code}){message}"
        raise BookloreError(msg) from exc
    except requests.exceptions.ConnectionError as exc:
        msg = f"Could not connect to {BOOKLORE_DISPLAY_NAME}"
        raise BookloreError(msg) from exc
    except requests.exceptions.Timeout as exc:
        msg = f"{BOOKLORE_DISPLAY_NAME} upload timed out"
        raise BookloreError(msg) from exc
    except requests.exceptions.RequestException as exc:
        msg = f"{BOOKLORE_DISPLAY_NAME} upload failed: {exc}"
        raise BookloreError(msg) from exc


def booklore_refresh_library(booklore_config: BookloreConfig, token: str) -> None:
    """Trigger a Booklore library refresh after upload."""
    url = f"{booklore_config.base_url}/api/v1/libraries/{booklore_config.library_id}/refresh"
    headers = {"Authorization": f"Bearer {token}"}

    try:
        response = requests.put(url, headers=headers, timeout=30, verify=booklore_config.verify_tls)
        response.raise_for_status()
    except requests.exceptions.RequestException as exc:
        msg = f"{BOOKLORE_DISPLAY_NAME} refresh failed: {exc}"
        raise BookloreError(msg) from exc


def _supports_booklore(task: DownloadTask) -> bool:
    return not check_audiobook(task.content_type)


def _get_booklore_settings() -> dict[str, Any]:
    return {
        "BOOKLORE_HOST": core_config.config.get("BOOKLORE_HOST", ""),
        "BOOKLORE_USERNAME": core_config.config.get("BOOKLORE_USERNAME", ""),
        "BOOKLORE_PASSWORD": core_config.config.get("BOOKLORE_PASSWORD", ""),
        "BOOKLORE_DESTINATION": core_config.config.get(
            "BOOKLORE_DESTINATION",
            BOOKLORE_DESTINATION_LIBRARY,
        ),
        "BOOKLORE_LIBRARY_ID": core_config.config.get("BOOKLORE_LIBRARY_ID"),
        "BOOKLORE_PATH_ID": core_config.config.get("BOOKLORE_PATH_ID"),
    }


def _booklore_format_error(rejected_files: list[Path]) -> str:
    rejected_exts = sorted({f.suffix.lower() for f in rejected_files})
    rejected_list = ", ".join(rejected_exts)
    return (
        f"{BOOKLORE_DISPLAY_NAME} does not support {rejected_list}. "
        f"Supported formats: {BOOKLORE_SUPPORTED_FORMATS_LABEL}"
    )


def _post_process_booklore(
    temp_file: Path,
    task: DownloadTask,
    cancel_flag: Event,
    status_callback: StatusCallback,
    *,
    preserve_source_on_failure: bool = False,
) -> str | None:
    from shelfmark.download.postprocess.packs import drop_ignored_files
    from shelfmark.download.postprocess.pipeline import (
        CustomScriptContext,
        OutputPlan,
        cleanup_output_staging,
        is_managed_workspace_path,
        maybe_run_custom_script,
        prepare_output_files,
        safe_cleanup_path,
    )

    if cancel_flag.is_set():
        logger.info("Task %s: cancelled before Booklore upload", task.task_id)
        return None

    try:
        booklore_config = build_booklore_config(
            _get_booklore_settings(),
            user_id=task.user_id,
        )
    except BookloreError as e:
        logger.warning("Task %s: Booklore configuration error: %s", task.task_id, e)
        status_callback("error", str(e))
        return None

    status_callback("resolving", f"Preparing {BOOKLORE_DISPLAY_NAME} upload")

    stage_action = STAGE_NONE
    if is_managed_workspace_path(temp_file):
        stage_action = STAGE_COPY if preserve_source_on_failure else STAGE_MOVE
    staging_dir = (
        build_staging_dir("booklore", task.task_id)
        if stage_action != STAGE_NONE
        else get_staging_dir()
    )

    output_plan = OutputPlan(
        mode=BOOKLORE_OUTPUT_MODE,
        stage_action=stage_action,
        staging_dir=staging_dir,
        allow_archive_extraction=True,
    )

    prepared = prepare_output_files(
        temp_file,
        task,
        BOOKLORE_OUTPUT_MODE,
        status_callback,
        output_plan=output_plan,
        preserve_source_on_failure=preserve_source_on_failure,
    )
    if not prepared:
        return None
    # Files the user skipped in the pack review are not uploaded.
    prepared = replace(prepared, files=drop_ignored_files(prepared.files, task.ignored_files))

    logger.debug(
        "Task %s: prepared %d file(s) for Booklore upload",
        task.task_id,
        len(prepared.files),
    )

    success = False
    try:
        if not prepared.files:
            status_callback("error", "No book files found")
            return None

        unsupported_files = [
            file_path
            for file_path in prepared.files
            if file_path.suffix.lower() not in BOOKLORE_SUPPORTED_EXTENSIONS
        ]
        if unsupported_files:
            error_message = _booklore_format_error(unsupported_files)
            logger.warning("Task %s: %s", task.task_id, error_message)
            status_callback("error", error_message)
            return None

        token = booklore_login(booklore_config)
        logger.info(
            "Task %s: uploading %d file(s) to Booklore",
            task.task_id,
            len(prepared.files),
        )

        for index, file_path in enumerate(prepared.files, start=1):
            if cancel_flag.is_set():
                logger.info("Task %s: cancelled during Booklore upload", task.task_id)
                return None
            status_callback(
                "resolving",
                f"Uploading to {BOOKLORE_DISPLAY_NAME} ({index}/{len(prepared.files)})",
            )
            booklore_upload_file(booklore_config, token, file_path)

        if booklore_config.refresh_after_upload:
            try:
                booklore_refresh_library(booklore_config, token)
            except BookloreError as e:
                logger.warning("Task %s: Booklore refresh failed: %s", task.task_id, e)

        logger.info(
            "Task %s: uploaded %d file(s) to Booklore",
            task.task_id,
            len(prepared.files),
        )

        destination: Path | None
        if len(prepared.files) == 1:
            destination = prepared.files[0].parent
        else:
            try:
                destination = Path(os.path.commonpath([str(p.parent) for p in prepared.files]))
            except ValueError:
                destination = prepared.files[0].parent if prepared.files else None

        script_context = CustomScriptContext(
            task=task,
            phase="post_upload",
            output_mode=BOOKLORE_OUTPUT_MODE,
            destination=destination,
            final_paths=prepared.files,
            output_details={
                "booklore": {
                    "base_url": booklore_config.base_url,
                    "destination": (
                        BOOKLORE_DESTINATION_BOOKDROP
                        if booklore_config.upload_to_bookdrop
                        else BOOKLORE_DESTINATION_LIBRARY
                    ),
                    "library_id": (
                        None if booklore_config.upload_to_bookdrop else booklore_config.library_id
                    ),
                    "path_id": None
                    if booklore_config.upload_to_bookdrop
                    else booklore_config.path_id,
                    "refresh_after_upload": bool(booklore_config.refresh_after_upload),
                }
            },
        )
        if not maybe_run_custom_script(script_context, status_callback=status_callback):
            return None

        message = f"Uploaded to {BOOKLORE_DISPLAY_NAME}"
        if len(prepared.files) > 1:
            message = f"Uploaded to {BOOKLORE_DISPLAY_NAME} ({len(prepared.files)} files)"
        status_callback("complete", message)
        success = True
        output_path = f"booklore://{task.task_id}"

    except BookloreError as e:
        logger.warning("Task %s: Booklore upload failed: %s", task.task_id, e)
        status_callback("error", str(e))
        return None
    except (OSError, TypeError, ValueError) as e:
        logger.error_trace("Task %s: unexpected error uploading to Booklore: %s", task.task_id, e)
        status_callback("error", f"{BOOKLORE_DISPLAY_NAME} upload failed: {e}")
        return None
    else:
        return output_path
    finally:
        cleanup_output_staging(
            prepared.output_plan,
            prepared.working_path,
            task,
            prepared.cleanup_paths,
        )
        if preserve_source_on_failure and success:
            safe_cleanup_path(temp_file, task)


@register_output(BOOKLORE_OUTPUT_MODE, supports_task=_supports_booklore, priority=10)
def process_booklore_output(
    temp_file: Path,
    task: DownloadTask,
    cancel_flag: Event,
    status_callback: StatusCallback,
    *,
    preserve_source_on_failure: bool = False,
) -> str | None:
    """Process a completed download through the Booklore output."""
    return _post_process_booklore(
        temp_file,
        task,
        cancel_flag,
        status_callback,
        preserve_source_on_failure=preserve_source_on_failure,
    )
