"""A reconcile that cannot run must never stop the app from starting."""

from __future__ import annotations

import importlib
from unittest.mock import patch

import pytest

from shelfmark.core.logger import CustomLogger


@pytest.mark.parametrize("error", [TypeError("unexpected row shape"), OSError("disk went away")])
def test_a_failing_startup_reconcile_only_logs_a_warning(error):
    """The module-level guard in main.py runs before the coordinator starts.

    An error it does not catch fails the import of shelfmark.main, and the worker then
    fails to boot on every restart.
    """
    # Patch the class, not main.logger: reloading main builds a new logger object.
    with (
        patch("shelfmark.download.orchestrator.start"),
        patch("shelfmark.download.warmup.start"),
        patch(
            "shelfmark.core.startup_reconcile.reconcile_interrupted_downloads",
            side_effect=error,
        ) as reconcile,
        patch.object(CustomLogger, "warning") as warning,
    ):
        import shelfmark.main as main

        importlib.reload(main)

    # Importing and then reloading runs the module body twice, so only check it was reached.
    assert reconcile.called
    assert any(
        "Could not reconcile interrupted downloads at startup" in str(call.args[0])
        for call in warning.call_args_list
    )
