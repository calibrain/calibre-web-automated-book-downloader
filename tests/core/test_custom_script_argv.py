"""The file name handed to a custom script must not be readable as an option."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from shelfmark.download.postprocess.custom_script import (
    CustomScriptExecution,
    run_custom_script,
)


def _execution(target_arg: str, mode: str) -> CustomScriptExecution:
    destination = Path("/library")
    return CustomScriptExecution(
        script_path="/scripts/hook.sh",
        target_arg=Path(target_arg),
        target_abs=destination / target_arg,
        destination=destination,
        mode=mode,
        phase="after_move",
        payload_json=None,
    )


def _argv_for(execution: CustomScriptExecution) -> list[str]:
    with patch(
        "shelfmark.download.postprocess.custom_script.run_blocking_io",
        return_value=MagicMock(stdout=""),
    ) as run:
        assert run_custom_script(execution, task_id="t1", status_callback=MagicMock())
    return list(run.call_args.args[1])


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("-rf", "./-rf"),
        ("--help.epub", "./--help.epub"),
        ("-Author/book.epub", "./-Author/book.epub"),
        ("normal.epub", "normal.epub"),
        ("Author/-odd.epub", "Author/-odd.epub"),
    ],
)
def test_relative_mode_target_never_starts_with_a_dash(target, expected):
    assert _argv_for(_execution(target, "relative")) == ["/scripts/hook.sh", expected]


def test_absolute_mode_is_unchanged():
    execution = CustomScriptExecution(
        script_path="/scripts/hook.sh",
        target_arg=Path("/library/-rf"),
        target_abs=Path("/library/-rf"),
        destination=Path("/library"),
        mode="absolute",
        phase="after_move",
        payload_json=None,
    )
    assert _argv_for(execution) == ["/scripts/hook.sh", "/library/-rf"]
