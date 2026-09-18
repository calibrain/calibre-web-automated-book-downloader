"""Background scheduler for Hardcover wishlist sync + auto-download.

Runs an in-process daemon thread (mirroring the download coordinator pattern in
``shelfmark.download.orchestrator``) that periodically syncs configured Hardcover
shelves into requests and auto-downloads them. Also exposes a manual trigger used
by the "Sync now" settings action button.
"""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING, Any

from shelfmark.core.config import config as app_config
from shelfmark.core.logger import setup_logger

if TYPE_CHECKING:
    from collections.abc import Callable

    from shelfmark.core.user_db import UserDB

logger = setup_logger(__name__)

_WARMUP_SECONDS = 60  # Delay before the first cycle after startup.
_MIN_INTERVAL_SECONDS = 60  # Floor so a misconfigured interval can't hammer the API.
_UNIT_SECONDS = {"minutes": 60.0, "hours": 3600.0}

_ctx_lock = threading.Lock()
_ctx: dict[str, Any] = {}

_thread: threading.Thread | None = None
_thread_lock = threading.Lock()

# Prevents a scheduled cycle and a manual "Sync now" from overlapping.
_run_lock = threading.Lock()


def configure(
    user_db: UserDB,
    queue_release: Callable[..., tuple[bool, str | None]],
    db_path: str | None,
) -> None:
    """Store the runtime context the background work needs."""
    with _ctx_lock:
        _ctx["user_db"] = user_db
        _ctx["queue_release"] = queue_release
        _ctx["db_path"] = db_path


def _interval_seconds() -> float:
    try:
        value = float(app_config.get("HARDCOVER_SYNC_INTERVAL", 6))
    except (TypeError, ValueError):
        value = 6.0
    unit = str(app_config.get("HARDCOVER_SYNC_INTERVAL_UNIT", "hours") or "hours").strip().lower()
    multiplier = _UNIT_SECONDS.get(unit, _UNIT_SECONDS["hours"])
    return max(value * multiplier, _MIN_INTERVAL_SECONDS)


def _enabled() -> bool:
    return bool(app_config.get("HARDCOVER_SYNC_ENABLED", False)) or bool(
        app_config.get("AUTO_DOWNLOAD_ENABLED", False)
    )


def run_once(*, force: bool = False) -> dict[str, Any]:
    """Run one sync + auto-download pass. Returns a combined summary.

    ``force=True`` (the manual "Sync now" path) syncs regardless of the scheduler
    toggle; auto-download always remains gated by ``AUTO_DOWNLOAD_ENABLED`` inside
    ``auto_download_pending``. Skips (returns ``{"status": "busy"}``) if another run
    is in progress.
    """
    if not _run_lock.acquire(blocking=False):
        return {"status": "busy"}
    try:
        with _ctx_lock:
            user_db = _ctx.get("user_db")
            queue_release = _ctx.get("queue_release")
            db_path = _ctx.get("db_path")

        if user_db is None or queue_release is None:
            return {"status": "unconfigured"}

        from shelfmark.core.auto_download import auto_download_pending
        from shelfmark.core.hardcover_sync import sync_wishlist

        sync_summary: dict[str, Any] | None = None
        if force or bool(app_config.get("HARDCOVER_SYNC_ENABLED", False)):
            sync_summary = sync_wishlist(user_db, db_path=db_path)
        auto_summary = auto_download_pending(user_db, queue_release=queue_release)
        return {"status": "ok", "sync": sync_summary, "auto_download": auto_summary}
    finally:
        _run_lock.release()


def trigger_async(*, force: bool = True) -> bool:
    """Kick off a one-off run in a background thread. Returns False if one is active."""
    if _run_lock.locked():
        return False
    thread = threading.Thread(
        target=lambda: run_once(force=force), daemon=True, name="HardcoverSyncManual"
    )
    thread.start()
    return True


def _scheduler_loop() -> None:
    logger.info("Hardcover scheduler started")
    time.sleep(_WARMUP_SECONDS)
    while True:
        try:
            if _enabled():
                logger.info("Hardcover scheduler: starting cycle")
                result = run_once()
                logger.info("Hardcover scheduler: cycle result %s", result)
        except Exception:
            logger.exception("Hardcover scheduler: cycle failed")
        time.sleep(_interval_seconds())


def start(
    user_db: UserDB,
    queue_release: Callable[..., tuple[bool, str | None]],
    db_path: str | None,
) -> None:
    """Configure context and start the scheduler thread (idempotent)."""
    configure(user_db, queue_release, db_path)
    global _thread
    with _thread_lock:
        if _thread is not None and _thread.is_alive():
            logger.debug("Hardcover scheduler already running")
            return
        _thread = threading.Thread(
            target=_scheduler_loop, daemon=True, name="HardcoverScheduler"
        )
        _thread.start()
    logger.info("Hardcover scheduler thread launched")
