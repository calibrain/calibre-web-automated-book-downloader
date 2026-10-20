"""Stall-detection grace signalling for long single-shot download operations.

The orchestrator cancels a download after `STALL_TIMEOUT` seconds without activity, where
"activity" means a *changed* status event or a *changed* progress value. That de-duplication
is deliberate - a keep-alive that repeats the same payload on a timer proves nothing about
whether the operation is still making progress, so letting it refresh the stall clock would
make a genuinely wedged download immortal.

Operations that legitimately take longer than `STALL_TIMEOUT` but cannot report incremental
progress therefore declare an explicit upper bound up front instead:

    request_activity_grace(status_callback, my_worst_case_seconds)
    try:
        ...one long blocking call...
    finally:
        release_activity_grace(status_callback)

The grace is a single absolute deadline. It is never extended, so the operation still dies
if it overruns its own declared budget - just at *its* bound rather than at a global 300s.

The signal rides on the existing `status_callback` channel using a sentinel status, which
avoids threading a new parameter through every handler, post-processor and output module.
`shelfmark.download.orchestrator`'s per-task `status_callback` closure intercepts the
sentinel and never forwards it to `update_download_status`.

Adopters should be operations that yield to the gevent hub while blocking (`requests`,
patched `subprocess`). An operation that blocks the hub outright - `shutil.copy2`, sqlite -
will still be killed by the gunicorn worker timeout regardless of any grace, and must go
through `shelfmark.download.fs.run_blocking_io` first.

Current adopters: `shelfmark.download.http.html_get_page` (protection bypass).
Candidates: `download.clients.base_handler._wait_for_completed_path`, archive extraction in
`download.postprocess.scan`, large-file copies in `download.outputs.folder`, email/BookLore
uploads, and the Anna's Archive countdown in `release_sources.direct_download` (which today
refreshes the stall clock on every tick of a loop that proves nothing about the remote).
"""

from collections.abc import Callable

# Seconds without a changed status or progress value before the orchestrator cancels a
# download. Lives here so the settings module can derive its minimum without importing the
# orchestrator.
STALL_TIMEOUT_SECONDS = 300

# Not a QueueStatus value, so `update_download_status` would reject it anyway; the
# orchestrator's status_callback intercepts it before that point.
ACTIVITY_GRACE_STATUS = "__activity_grace__"

StatusCallback = Callable[[str, str | None], None]

# A status_callback is caller-supplied and may raise; a failed liveness hint must never
# break the operation it was protecting. Mirrors http._STATUS_CALLBACK_ERRORS.
_CALLBACK_ERRORS = (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError)


def request_activity_grace(status_callback: StatusCallback | None, seconds: float) -> None:
    """Ask the orchestrator to suppress stall detection for up to `seconds` from now."""
    _emit(status_callback, seconds)


def release_activity_grace(status_callback: StatusCallback | None) -> None:
    """Drop any outstanding grace and count now as activity."""
    _emit(status_callback, 0)


def parse_activity_grace(status: str, message: str | None) -> float | None:
    """Return the requested grace in seconds, or None if this is not a grace event.

    Never raises: a malformed sentinel is treated as "not a grace event" so a bad emitter
    cannot take down the status pipeline.
    """
    if status != ACTIVITY_GRACE_STATUS:
        return None
    try:
        return max(float(message or 0), 0.0)
    except TypeError, ValueError:
        return 0.0


def _emit(status_callback: StatusCallback | None, seconds: float) -> None:
    if status_callback is None:
        return
    try:
        status_callback(ACTIVITY_GRACE_STATUS, str(float(seconds)))
    except _CALLBACK_ERRORS:
        return
