"""Internal Cloudflare bypass implementation using SeleniumBase and CDP helpers."""

import _thread
import asyncio
import json
import os
import random
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from contextlib import suppress
from datetime import UTC, datetime
from http import HTTPStatus
from pathlib import Path
from threading import Event
from typing import Any, Protocol, TypedDict, TypeGuard
from urllib.parse import urlparse

import requests
from seleniumbase import cdp_driver
from seleniumbase.undetected.cdp_driver.connection import ProtocolException

from shelfmark.bypass import BypassCancelledError
from shelfmark.bypass.challenge import CLOUDFLARE_INDICATORS, DDOS_GUARD_INDICATORS
from shelfmark.bypass.cookie_store import (
    clear_cf_cookies,
    export_store,
    get_cf_cookies_for_domain,
    get_cf_user_agent_for_domain,
    import_store,
    store_extracted_cookies,
)
from shelfmark.bypass.fingerprint import get_screen_size
from shelfmark.bypass.waiting_room import WaitingRoomTimeoutError, is_aa_waiting_room
from shelfmark.config import env
from shelfmark.config.env import LOG_DIR
from shelfmark.config.settings import RECORDING_DIR
from shelfmark.core.config import config as app_config
from shelfmark.core.logger import setup_logger
from shelfmark.download import network
from shelfmark.download.network import get_proxies, get_ssl_verify

logger = setup_logger(__name__)

SELENIUMBASE_RUNTIME_ROOT = Path(tempfile.gettempdir()) / "shelfmark" / "seleniumbase"
SELENIUMBASE_DOWNLOADS_DIR = SELENIUMBASE_RUNTIME_ROOT / "downloaded_files"
BROWSER_RUNTIME_ROOT = Path(tempfile.gettempdir()) / "shelfmark" / "browser"
BROWSER_HOME_DIR = BROWSER_RUNTIME_ROOT / "home"
BROWSER_XDG_RUNTIME_DIR = BROWSER_RUNTIME_ROOT / "runtime"
_BYPASSED_BODY_LENGTH_MIN = 100_000
_BYPASS_EMOJI_MATCH_MIN = 3
_LOADING_BODY_LENGTH_MAX = 50
_PAGE_BODY_PREVIEW_CHARS = 500
_BROWSER_START_TIMEOUT_SECONDS = 45.0
_BYPASS_SUBPROCESS_TIMEOUT_SECONDS = 420.0
# How long a cancelled bypass may take to close its browser before the calling thread
# stops waiting for it. Counted on top of the bypass deadline, so every budget below is
# set to leave room for it.
_CDP_UNWIND_GRACE_SECONDS = 15.0
# Same wall-clock budget as the Docker helper process, applied to the in-process CDP path
# so both branches of get() are bounded the same way: the deadline plus the unwind grace
# comes to _BYPASS_SUBPROCESS_TIMEOUT_SECONDS either way.
_IN_PROCESS_BYPASS_TIMEOUT_SECONDS = _BYPASS_SUBPROCESS_TIMEOUT_SECONDS - _CDP_UNWIND_GRACE_SECONDS
_BYPASS_CHILD_ENV = "SHELFMARK_INTERNAL_BYPASSER_CHILD"
# The helper bounds each bypass below the parent's deadline, so it is the side that gives
# up first: it still gets to report the timeout and close its browser, and stays available
# for the next request. A parent that hit its deadline first could only kill the helper,
# throwing away a process the next request would have to start again. The 30s covers the
# unwind grace as well, so a helper that times out and closes its browser as slowly as it
# is allowed to still answers with 15s to spare.
_CHILD_BYPASS_TIMEOUT_SECONDS = _BYPASS_SUBPROCESS_TIMEOUT_SECONDS - 30.0
# The helper publishes its answer by writing the result file the request named, so the
# parent waits by watching for that file rather than by reading a stream it would have to
# demultiplex from the helper's log output.
_HELPER_RESULT_POLL_SECONDS = 0.05
# Closing the helper's stdin asks it to shut down; this is how long it may take to finish
# what it is doing and exit before its session is killed instead.
_HELPER_SHUTDOWN_GRACE_SECONDS = 15.0
_HELPER_IDLE_TIMEOUT_DEFAULT = 180.0
# How long to wait for a solved page to produce its document before the attempt is
# abandoned. SeleniumBase's own get_page_source() allows one second; see _read_page_source.
_PAGE_SOURCE_TIMEOUT_DEFAULT = 20.0
# Leave time inside the existing browser watchdog for challenge solving and cleanup.
_AA_WAITING_ROOM_TIMEOUT_SECONDS = 300.0
_AA_WAITING_ROOM_POLL_SECONDS = 1.0
_PARENT_WATCHDOG_INTERVAL_SECONDS = 5.0
# How much of ffmpeg's stderr to quote when reporting that it died.
_FFMPEG_ERROR_TAIL_CHARS = 500


class _WaitingRoomSnapshot(TypedDict):
    html: str
    title: str
    body: str
    url: str
    waiting: bool


class _DisplayState(TypedDict):
    ffmpeg: subprocess.Popen[bytes] | None
    ffmpeg_output: Path | None
    ffmpeg_error_log: Path | None


class _PageWithWindowRect(Protocol):
    async def set_window_rect(self, x: int, _y: int, width: int, height: int) -> object: ...


class _BrowserWithWindowRectPage(Protocol):
    page: _PageWithWindowRect


DISPLAY: _DisplayState = {
    "ffmpeg": None,
    "ffmpeg_output": None,
    "ffmpeg_error_log": None,
}
LOCKED = threading.Lock()
_PROC_ROOT = Path("/proc")
_BROWSER_PROCESS_PATTERNS = ("chrome", "chromium", "Xvfb", "ffmpeg")
_RNG = random.SystemRandom()

_CDP_OPERATION_ERRORS = (
    asyncio.TimeoutError,
    AttributeError,
    NameError,
    OSError,
    ProtocolException,
    RuntimeError,
    TypeError,
    ValueError,
)
_PATH_INSPECTION_ERRORS = (OSError, RuntimeError, TypeError, ValueError)
_REQUEST_OPERATION_ERRORS = (
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
    requests.RequestException,
)
_SUBPROCESS_OPERATION_ERRORS = (
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
    subprocess.SubprocessError,
)
_NATIVE_ATTR_ERRORS = (ImportError, AttributeError, RuntimeError)


def _get_native_attr(module: str, name: str, fallback: Any) -> Any:
    """Return an unpatched stdlib attribute when running under gevent."""
    try:
        from gevent import monkey

        original = monkey.get_original(module, name)
    except _NATIVE_ATTR_ERRORS:
        return fallback
    else:
        return original or fallback


_NATIVE_START_NEW_THREAD = _get_native_attr("_thread", "start_new_thread", _thread.start_new_thread)
_NATIVE_EVENT = _get_native_attr("threading", "Event", threading.Event)
_NATIVE_LOCK = _get_native_attr("threading", "Lock", threading.Lock)


def _coerce_positive_int(value: object, default: int) -> int:
    """Return a positive integer config value or the provided default."""
    if isinstance(value, bool):
        return default
    if isinstance(value, int) and value > 0:
        return value
    return default


def _coerce_non_negative_float(value: object, default: float) -> float:
    """Return a non-negative float config value or the provided default."""
    if isinstance(value, bool):
        return default
    if isinstance(value, int | float) and value >= 0:
        return float(value)
    return default


def _has_window_rect_page(candidate: object) -> TypeGuard[_BrowserWithWindowRectPage]:
    """Check whether a browser wrapper exposes page.set_window_rect()."""
    page = getattr(candidate, "page", None)
    return callable(getattr(page, "set_window_rect", None))


def _describe_runtime_path(path: str | Path) -> str:
    """Return compact ownership/mode info for a runtime path."""
    try:
        path = Path(path)
        link_target = ""
        if path.is_symlink():
            link_target = f" -> {path.readlink()}"
        st = path.stat()
        mode = stat.S_IMODE(st.st_mode)
        return f"{path}{link_target} exists uid={st.st_uid} gid={st.st_gid} mode={oct(mode)}"
    except FileNotFoundError:
        return f"{path} missing"
    except _PATH_INSPECTION_ERRORS as e:
        return f"{path} error={type(e).__name__}: {e}"


class _CdpWorker:
    def __init__(self) -> None:
        self._thread_id: int | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ready = _NATIVE_EVENT()
        self._lock = _NATIVE_LOCK()

    def _run(self) -> None:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            self._ready.set()
            loop.run_forever()
            with suppress(Exception):
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()
        finally:
            self._thread_id = None

    def start(self) -> None:
        with self._lock:
            if self._loop and self._loop.is_running() and not self._loop.is_closed():
                return
            self._loop = None
            self._ready.clear()
            self._thread_id = _NATIVE_START_NEW_THREAD(self._run, ())
        if not self._ready.wait(timeout=10):
            msg = "CDP worker loop failed to start"
            raise RuntimeError(msg)

    @staticmethod
    async def _bounded(coro: Any, timeout: float | None) -> Any:
        """Run the coroutine under its deadline, on the loop that owns it.

        The deadline has to be enforced from inside the loop rather than by the calling
        thread: asyncio.wait_for() cancels the bypass and then *waits for it to unwind*,
        so `finally: await _close_cdp_driver(driver)` has finished by the time this
        raises. Cancelling from outside returns the moment the cancellation is scheduled,
        which in a helper serving many requests let the abandoned bypass close its browser
        while the next one was already opening its own - on the same loop, sharing the
        DISPLAY globals and one process group.
        """
        if timeout is None:
            return await coro
        return await asyncio.wait_for(coro, timeout)

    def run(self, coro: Any, timeout: float | None = None) -> Any:
        self.start()
        if not self._loop or self._loop.is_closed():
            msg = "CDP worker loop not available"
            raise RuntimeError(msg)
        future = asyncio.run_coroutine_threadsafe(self._bounded(coro, timeout), self._loop)
        # Backstop for an unwind that wedges too: _close_cdp_driver awaits websockets that
        # a dead browser may never answer, and _bounded cannot outlive its own cleanup.
        wait_for = None if timeout is None else timeout + _CDP_UNWIND_GRACE_SECONDS
        try:
            return future.result(timeout=wait_for)
        except TimeoutError:
            # Otherwise the coroutine keeps running in the worker loop after we stop
            # waiting, holding the browser and racing the next bypass.
            future.cancel()
            raise


_CDP_WORKER = _CdpWorker()


async def _extract_cookies_from_cdp(driver: Any, page: Any, url: str) -> None:
    """Extract cookies from a CDP browser after successful bypass."""
    try:
        try:
            all_cookies = await driver.cookies.get_all(requests_cookie_format=True)
        except _CDP_OPERATION_ERRORS as e:
            logger.debug("Failed to get cookies via CDP: %s", e)
            return

        try:
            user_agent = await page.evaluate("navigator.userAgent")
        except _CDP_OPERATION_ERRORS:
            user_agent = None

        store_extracted_cookies(url=url, cookies=all_cookies, user_agent=user_agent)

    except _CDP_OPERATION_ERRORS as e:
        logger.debug("Failed to extract cookies: %s", e)


def _read_process_cmdline(proc_dir: Path) -> str:
    """Return a process's full command line, or "" when it cannot be read."""
    try:
        raw = (proc_dir / "cmdline").read_bytes()
    except OSError:
        return ""
    return raw.replace(b"\x00", b" ").decode("utf-8", "replace").strip()


def _read_process_pgid(proc_dir: Path) -> int | None:
    """Return a process's group id from /proc/<pid>/stat, or None when unreadable."""
    try:
        stat_line = (proc_dir / "stat").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    # Field 2 (comm) is parenthesised and may itself contain spaces and parens, so the
    # fields are only unambiguous after the last ')': state, ppid, pgrp, ...
    fields = stat_line.rpartition(")")[2].split()
    pgrp_index = 2
    if len(fields) <= pgrp_index:
        return None
    try:
        return int(fields[pgrp_index])
    except ValueError:
        return None


def _find_browser_processes() -> list[tuple[int, int, str]]:
    """Return (pid, pgid, cmdline) for every browser-ish process visible in /proc."""
    found: list[tuple[int, int, str]] = []
    try:
        entries = list(_PROC_ROOT.iterdir())
    except OSError as e:
        logger.debug("Could not list %s: %s", _PROC_ROOT, e)
        return found

    for entry in entries:
        if not entry.name.isdigit():
            continue
        cmdline = _read_process_cmdline(entry)
        if not cmdline or not any(name in cmdline for name in _BROWSER_PROCESS_PATTERNS):
            continue
        pgid = _read_process_pgid(entry)
        if pgid is None:
            continue
        found.append((int(entry.name), pgid, cmdline))
    return found


def _kill_process(pid: int, cmdline: str) -> bool:
    """SIGKILL one process, reporting whether it was actually signalled."""
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return False
    except OSError as e:
        logger.warning("Failed to kill pid %s: %s", pid, e)
        return False
    logger.debug("Killed leftover process %s: %s", pid, cmdline[:120])
    return True


def _cleanup_orphan_processes() -> int:
    """Kill leftover Chrome/Xvfb/ffmpeg processes. Only runs in Docker mode.

    Scoped to this bypass session's process group plus groups whose leader has died.
    A container-wide sweep (the old `pkill -9 -f chrome`) also matched the browsers a
    concurrently running bypass was still driving, so with MAX_CONCURRENT_DOWNLOADS > 1
    every worker that started a solve killed the others' browsers (#1231).
    """
    if not env.DOCKERMODE:
        return 0

    _stop_ffmpeg_recording()

    logger.debug("Checking for leftover browser processes...")
    logger.log_resource_usage()

    if not _PROC_ROOT.is_dir():
        logger.warning("Skipping browser-process cleanup because %s is unavailable", _PROC_ROOT)
        return 0

    own_pid = os.getpid()
    own_pgid = os.getpgrp()
    total_killed = 0

    for pid, pgid, cmdline in _find_browser_processes():
        if pid == own_pid:
            continue
        # Another live process group means another bypass session: its browsers are in
        # use, not orphans. Only our own group and groups whose leader is gone (a helper
        # that died or was killed, leaving its browser behind) are ours to clean up.
        if pgid != own_pgid and (_PROC_ROOT / str(pgid)).exists():
            logger.debug("Leaving pid %s to its live bypass session (pgid %s)", pid, pgid)
            continue
        if _kill_process(pid, cmdline):
            total_killed += 1

    if total_killed > 0:
        time.sleep(1)
        logger.info("Cleaned up %s leftover browser process(es)", total_killed)
        logger.log_resource_usage()
    else:
        logger.debug("No leftover browser processes found")

    return total_killed


async def _get_page_info(page: Any) -> tuple[str, str, str]:
    """Extract page title, body text, and current URL safely."""
    try:
        title = (await page.get_title() or "").lower()
    except _CDP_OPERATION_ERRORS:
        title = ""
    try:
        body = await page.evaluate("document.body ? document.body.innerText : ''")
        body = (body or "").lower()
    except _CDP_OPERATION_ERRORS:
        body = ""
    try:
        current_url = await page.get_current_url() or ""
    except _CDP_OPERATION_ERRORS:
        current_url = ""
    return title, body, current_url


def _check_indicators(title: str, body: str, indicators: list[str]) -> str | None:
    """Check if any indicator is present in title or body. Returns the found indicator or None."""
    for indicator in indicators:
        if indicator in title or indicator in body:
            return indicator
    return None


def _has_cloudflare_patterns(body: str, url: str) -> bool:
    """Check for Cloudflare-specific patterns in body or URL."""
    return "cf-" in body or "cloudflare" in url.lower() or "/cdn-cgi/" in url


async def _detect_challenge_type(page: Any) -> str:
    """Detect challenge type: 'cloudflare', 'ddos_guard', or 'none'."""
    title, body, current_url = await _get_page_info(page)

    # DDOS-Guard indicators
    if found := _check_indicators(title, body, DDOS_GUARD_INDICATORS):
        logger.debug("DDOS-Guard indicator found: '%s'", found)
        return "ddos_guard"

    # Cloudflare indicators
    if found := _check_indicators(title, body, CLOUDFLARE_INDICATORS):
        logger.debug("Cloudflare indicator found: '%s'", found)
        return "cloudflare"

    # Check URL patterns
    if _has_cloudflare_patterns(body, current_url):
        return "cloudflare"

    return "none"


async def _is_bypassed(page: Any, *, escape_emojis: bool = True) -> bool:
    """Check if the protection has been bypassed."""
    title, body, current_url = await _get_page_info(page)
    return _is_bypassed_content(title, body, current_url, escape_emojis=escape_emojis)


def _is_bypassed_content(
    title: str, body: str, current_url: str, *, escape_emojis: bool = True
) -> bool:
    """Apply the same protection checks to one consistent page snapshot."""
    title, body = title.lower(), body.lower()
    body_len = len(body.strip())

    # Long page content = probably bypassed
    if body_len > _BYPASSED_BODY_LENGTH_MIN:
        logger.debug("Page content too long, probably bypassed (len: %s)", body_len)
        return True

    # Multiple emojis = probably real content
    if escape_emojis:
        import emoji

        if len(emoji.emoji_list(body)) >= _BYPASS_EMOJI_MATCH_MIN:
            logger.debug("Detected emojis in page, probably bypassed")
            return True

    # Check for protection indicators (means NOT bypassed)
    if _check_indicators(title, body, CLOUDFLARE_INDICATORS + DDOS_GUARD_INDICATORS):
        return False

    # Cloudflare URL patterns
    if _has_cloudflare_patterns(body, current_url):
        logger.debug("Cloudflare patterns detected in page")
        return False

    # Page too short = still loading
    if body_len < _LOADING_BODY_LENGTH_MAX:
        logger.debug("Page content too short, might still be loading")
        return False

    logger.debug("Bypass check passed - Title: '%s', Body length: %s", title[:100], body_len)
    return True


async def _bypass_method_humanlike(page: Any) -> bool:
    """Human-like behavior with scroll, wait, and reload."""
    try:
        logger.debug("Attempting bypass: human-like interaction")
        await asyncio.sleep(_RNG.uniform(6, 10))

        try:
            await page.evaluate("window.scrollTo(0, 10000);")
            await page.wait()
            await asyncio.sleep(_RNG.uniform(1, 2))
            await page.evaluate("window.scrollTo(0, 0);")
            await page.wait()
            await asyncio.sleep(_RNG.uniform(2, 3))
        except _CDP_OPERATION_ERRORS as e:
            logger.debug("Scroll behavior failed: %s", e)

        if await _is_bypassed(page):
            return True

        logger.debug("Trying page refresh...")
        await page.reload(ignore_cache=True)
        await asyncio.sleep(_RNG.uniform(5, 8))

        if await _is_bypassed(page):
            return True

        try:
            await page.solve_captcha()
            await asyncio.sleep(_RNG.uniform(3, 5))
        except _CDP_OPERATION_ERRORS as e:
            logger.debug("Final captcha click failed: %s", e)

        return await _is_bypassed(page)
    except _CDP_OPERATION_ERRORS as e:
        logger.debug("Human-like method failed: %s", e)
        return False


CDP_CLICK_SELECTORS = [
    "#turnstile-widget div",  # Cloudflare Turnstile
    "#cf-turnstile div",  # Alternative CF Turnstile
    "iframe[src*='challenges']",  # CF challenge iframe
    "input[type='checkbox']",  # Generic checkbox (DDOS-Guard)
    "[class*='checkbox']",  # Class-based checkbox
    "#challenge-running",  # CF challenge indicator
]


async def _bypass_method_cdp_click(page: Any) -> bool:
    """CDP Mode with native clicking - no PyAutoGUI dependency."""
    try:
        logger.debug("Attempting bypass: CDP native click")

        for selector in CDP_CLICK_SELECTORS:
            try:
                if not await page.is_element_visible(selector):
                    continue

                logger.debug("CDP clicking: %s", selector)
                await page.click(selector)
                await asyncio.sleep(_RNG.uniform(2, 4))

                if await _is_bypassed(page):
                    return True
            except _CDP_OPERATION_ERRORS as e:
                logger.debug("CDP click on '%s' failed: %s", selector, e)

        return await _is_bypassed(page)
    except _CDP_OPERATION_ERRORS as e:
        logger.debug("CDP Mode click failed: %s", e)
        return False


CDP_GUI_CLICK_SELECTORS = [
    "#turnstile-widget div",  # Cloudflare Turnstile
    "#cf-turnstile div",  # Alternative CF Turnstile
    "#challenge-stage div",  # CF challenge stage
    "input[type='checkbox']",  # Generic checkbox
    "[class*='cb-i']",  # DDOS-Guard checkbox
]


async def _bypass_method_cdp_gui_click(page: Any) -> bool:
    """CDP Mode with gui_click-style behavior."""
    try:
        logger.debug("Attempting bypass: CDP gui_click (mouse-based)")

        try:
            logger.debug("Trying solve_captcha()")
            await page.solve_captcha()
            await asyncio.sleep(_RNG.uniform(3, 5))

            if await _is_bypassed(page):
                return True
        except _CDP_OPERATION_ERRORS as e:
            logger.debug("solve_captcha() failed: %s", e)

        for selector in CDP_GUI_CLICK_SELECTORS:
            try:
                if not await page.is_element_visible(selector):
                    continue

                logger.debug("CDP click_with_offset: %s", selector)
                await page.click_with_offset(selector, 0, 0, center=True)
                await asyncio.sleep(_RNG.uniform(3, 5))

                if await _is_bypassed(page):
                    return True
            except _CDP_OPERATION_ERRORS as e:
                logger.debug("CDP gui_click on '%s' failed: %s", selector, e)

        return await _is_bypassed(page)
    except _CDP_OPERATION_ERRORS as e:
        logger.debug("CDP Mode gui_click failed: %s", e)
        return False


# Ordered cheapest-first, and deliberately without a bare `solve_captcha()` entry:
# _bypass_method_cdp_gui_click opens by doing exactly that and returns the moment it
# works, so a separate method ahead of it could only ever repeat the half that had
# already failed - one wasted round trip plus the backoff before the next attempt, on
# every solve that gets this far. Measured at ~5.5s of the ~26s each solve cost, and
# 0/19 successes for the standalone method against DDoS-Guard. See issue #1285.
BYPASS_METHODS = [
    _bypass_method_cdp_gui_click,
    _bypass_method_cdp_click,
    _bypass_method_humanlike,
]

MAX_CONSECUTIVE_SAME_CHALLENGE = 3

# How many method attempts one _bypass() pass may make. Deliberately *not* MAX_RETRY:
# that value is already the outer page-load retry in _run_bypass_in_current_process, and
# reading it here too squared the budget - the default 10 meant 10 page loads x 4 methods
# = 40 solve attempts on one browser, which overruns the worker deadline and reports
# `TimeoutError` instead of a plain "bypass failed". One full pass through the methods
# plus a spare is all this loop can use anyway: the stuck-challenge guard below aborts at
# len(BYPASS_METHODS) + 1, so a larger number here only ever showed up in the logs.
_BYPASS_METHOD_ATTEMPTS = len(BYPASS_METHODS) + 1

# The undisturbed window a passive challenge gets before any method runs. Sized off the
# real thing: a desktop browser clears Anna's Archive's DDoS-Guard JS check in under 10s.
_PASSIVE_SOLVE_SECONDS = 15.0
_PASSIVE_SOLVE_POLL_SECONDS = 1.0

# Head-room the retry loop leaves itself so it can return a real failure rather than be
# cancelled at the worker deadline. Enough for the pass in flight to unwind and the
# browser to close.
_RESERVE_FOR_CLEAN_FAILURE_SECONDS = 60.0


def _check_cancellation(cancel_flag: Event | None, message: str) -> None:
    """Check if cancellation was requested and raise if so."""
    if cancel_flag and cancel_flag.is_set():
        logger.info(message)
        msg = "Bypass cancelled"
        raise BypassCancelledError(msg)


async def _wait_for_passive_solve(page: Any, cancel_flag: Event | None = None) -> bool:
    """Poll for a challenge that clears itself, without touching the page.

    Returns True as soon as the page looks bypassed, False once the window is spent.
    """
    logger.info("Waiting up to %.0fs for the challenge to clear itself...", _PASSIVE_SOLVE_SECONDS)
    deadline = time.monotonic() + _PASSIVE_SOLVE_SECONDS
    while time.monotonic() < deadline:
        _check_cancellation(cancel_flag, "Bypass cancelled while waiting for a passive solve")
        await asyncio.sleep(_PASSIVE_SOLVE_POLL_SECONDS)
        if await _is_bypassed(page):
            return True
    return False


async def _bypass(
    page: Any, max_retries: int | None = None, cancel_flag: Event | None = None
) -> bool:
    """Attempt to bypass Cloudflare/DDOS-Guard protection using multiple methods."""
    max_retries = max_retries if max_retries is not None else _BYPASS_METHOD_ATTEMPTS

    last_challenge_type = None
    consecutive_same_challenge = 0
    # Allow at least one full pass through all bypass methods before aborting due to a "stuck" challenge.
    min_same_challenge_before_abort = max(MAX_CONSECUTIVE_SAME_CHALLENGE, len(BYPASS_METHODS) + 1)

    for try_count in range(max_retries):
        _check_cancellation(cancel_flag, "Bypass cancelled by user")

        if await _is_bypassed(page):
            if try_count == 0:
                logger.info("Page already bypassed")
            return True

        challenge_type = await _detect_challenge_type(page)
        logger.debug("Challenge detected: %s", challenge_type)

        # Give a passive check the undisturbed window it needs before touching the page.
        # DDoS-Guard's JS check on Anna's Archive has no click target: it runs, then
        # navigates on its own - a desktop browser clears it in well under 15s. Every
        # method below either clicks a selector that is not there or reloads, and a reload
        # restarts an in-flight check (which DDoS-Guard also throttles), so going straight
        # to them meant the one thing that actually solves this challenge was the one
        # thing never tried. Costs one 15s window per solve against a minutes-long budget,
        # and a challenge that needs interaction simply falls through to the methods.
        if try_count == 0 and challenge_type != "none":
            if await _wait_for_passive_solve(page, cancel_flag):
                logger.info("Bypass successful: %s challenge cleared itself", challenge_type)
                return True
            logger.debug("Challenge did not clear on its own; trying bypass methods")

        # No challenge detected but page doesn't look bypassed - wait and retry
        if challenge_type == "none":
            logger.info("No challenge detected, waiting for page to settle...")
            await asyncio.sleep(_RNG.uniform(2, 3))
            if await _is_bypassed(page):
                return True
            # Try a simple refresh instead of captcha methods
            try:
                await page.reload(ignore_cache=True)
                await asyncio.sleep(_RNG.uniform(1, 2))
                if await _is_bypassed(page):
                    logger.info("Bypass successful after refresh")
                    return True
            except _CDP_OPERATION_ERRORS as e:
                logger.debug("Refresh during no-challenge wait failed: %s", e)
            continue

        if challenge_type == last_challenge_type:
            consecutive_same_challenge += 1
            if consecutive_same_challenge >= min_same_challenge_before_abort:
                logger.warning(
                    "Same challenge (%s) detected %s times - aborting",
                    challenge_type,
                    consecutive_same_challenge,
                )
                return False
        else:
            consecutive_same_challenge = 1
        last_challenge_type = challenge_type

        method = BYPASS_METHODS[try_count % len(BYPASS_METHODS)]
        logger.info("Bypass attempt %s/%s using %s", try_count + 1, max_retries, method.__name__)

        if try_count > 0:
            wait_time = min(_RNG.uniform(2, 4) * try_count, 12)
            logger.info("Waiting %0.1fs before trying...", wait_time)
            for _ in range(int(wait_time)):
                _check_cancellation(cancel_flag, "Bypass cancelled during wait")
                await asyncio.sleep(1)
            await asyncio.sleep(wait_time - int(wait_time))

        try:
            if await method(page):
                logger.info("Bypass successful using %s", method.__name__)
                return True
        except BypassCancelledError:
            raise
        except _CDP_OPERATION_ERRORS as e:
            logger.warning("Exception in %s: %s", method.__name__, e)

        logger.info("Bypass method %s failed.", method.__name__)

    logger.warning("Exceeded maximum retries. Bypass failed.")
    return False


def _get_browser_args() -> list[str]:
    """Build extra Chrome arguments, pre-resolving hostnames via patched DNS.

    Pre-resolves AA hostnames and passes IPs to Chrome via --host-resolver-rules,
    bypassing Chrome's DNS entirely for those hosts.
    """
    arguments = [
        "--ignore-certificate-errors",
        "--ignore-ssl-errors",
        "--allow-running-insecure-content",
        "--ignore-certificate-errors-spki-list",
        "--ignore-certificate-errors-skip-list",
        # Chrome 144+ disabled automatic SwiftShader fallback for WebGL (security reasons).
        # Without this flag, WebGL is broken in headless/Docker which triggers bot detection.
        # See: https://issues.chromium.org/issues/40277080
        "--enable-unsafe-swiftshader",
    ]

    if app_config.get("DEBUG", False):
        arguments.extend(
            ["--enable-logging", "--v=1", "--log-file=" + str(LOG_DIR / "chrome_browser.log")]
        )

    host_rules = _build_host_resolver_rules()
    if host_rules:
        arguments.append(f"--host-resolver-rules={', '.join(host_rules)}")
        logger.debug("Chrome: Using host resolver rules for %s hosts", len(host_rules))
    else:
        logger.warning("Chrome: No hosts could be pre-resolved")

    return arguments


def _build_host_resolver_rules() -> list[str]:
    """Pre-resolve AA hostnames and build Chrome host resolver rules."""
    host_rules = []

    try:
        for url in network.get_available_aa_urls():
            hostname = urlparse(url).hostname
            if not hostname:
                continue

            try:
                results = socket.getaddrinfo(hostname, 443, socket.AF_INET)
                if results:
                    ip = results[0][4][0]
                    host_rules.append(f"MAP {hostname} {ip}")
                    logger.debug("Chrome: Pre-resolved %s -> %s", hostname, ip)
                else:
                    logger.warning("Chrome: No addresses returned for %s", hostname)
            except socket.gaierror as e:
                logger.warning("Chrome: Could not pre-resolve %s: %s", hostname, e)
    except (OSError, RuntimeError, TypeError, ValueError) as e:
        logger.error_trace(f"Error pre-resolving hostnames for Chrome: {e}")

    return host_rules


DRIVER_RESET_ERRORS = {"ProtocolException", "RuntimeError", "TimeoutError"}


async def _read_page_source(page: Any) -> str:
    """Read a solved page's HTML, waiting for the document to arrive.

    `get_page_source()` waits one second for the `html` element. A page released from a
    challenge is often still navigating to the real content, so the read times out even
    though the solve succeeded: the whole attempt is retried, and the repeated requests
    are what earn a 429 from a host that was about to serve us.
    """
    timeout = _coerce_non_negative_float(
        app_config.get("BYPASS_PAGE_SOURCE_TIMEOUT", _PAGE_SOURCE_TIMEOUT_DEFAULT),
        _PAGE_SOURCE_TIMEOUT_DEFAULT,
    )
    element = await page.find("html", timeout=timeout)
    return await element.get_html_async()


async def _read_waiting_room_snapshot(
    page: Any, cancel_flag: Event | None
) -> _WaitingRoomSnapshot | None:
    """Read one DOM snapshot while still checking cancellation during a stalled read."""
    task = asyncio.create_task(
        page.evaluate("""({
        html: document.documentElement?.outerHTML || '',
        title: document.title,
        body: document.body?.innerText || '',
        url: location.href,
        waiting: !!document.querySelector('.js-partner-countdown')
    })""")
    )
    try:
        while not task.done():
            _check_cancellation(cancel_flag, "Bypass cancelled in Anna's waiting room")
            # Keep a slow CDP request alive. Cancelling and reissuing it on every
            # poll can break the listener when a late response targets a cancelled
            # SeleniumBase transaction. Cancel only when this browser is unwinding.
            await asyncio.wait({task}, timeout=_AA_WAITING_ROOM_POLL_SECONDS)
        _check_cancellation(cancel_flag, "Bypass cancelled in Anna's waiting room")
        return task.result()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def _wait_for_aa_download_page(
    page: Any, url: str, html: str, cancel_flag: Event | None = None
) -> str:
    """Let the waiting room's own JavaScript countdown and navigation finish.

    Returning the timer HTML closes this incognito browser. Sleeping in the HTTP
    downloader and fetching again then starts a different session, losing queue state.
    Keep the original tab alive, including through zero and automatic page reloads.
    """
    if not is_aa_waiting_room(url, html):
        return html

    logger.info("Waiting for Anna's Archive queue in the same browser session...")
    started = time.monotonic()
    try:
        async with asyncio.timeout(_AA_WAITING_ROOM_TIMEOUT_SECONDS):
            while True:
                try:
                    # Read readiness and HTML atomically: navigation between separate
                    # CDP reads could validate a new page but return an old interstitial.
                    snapshot = await _read_waiting_room_snapshot(page, cancel_flag)
                except _CDP_OPERATION_ERRORS:
                    # A frame/context can disappear during automatic navigation.
                    snapshot = None
                if (
                    snapshot
                    and not snapshot["waiting"]
                    and _is_bypassed_content(snapshot["title"], snapshot["body"], snapshot["url"])
                ):
                    logger.info(
                        "Anna's Archive waiting room finished after %.0fs",
                        time.monotonic() - started,
                    )
                    return snapshot["html"]
                # A zero timer, empty document, or protection page is not completion.
                await asyncio.sleep(_AA_WAITING_ROOM_POLL_SECONDS)
    except TimeoutError as exc:
        raise WaitingRoomTimeoutError(
            f"Anna's Archive waiting room did not finish within {_AA_WAITING_ROOM_TIMEOUT_SECONDS:g}s"
        ) from exc


async def _get(url: str, driver: Any, cancel_flag: Event | None = None) -> str:
    """Fetch URL with Cloudflare bypass using a CDP browser."""
    _check_cancellation(cancel_flag, "Bypass cancelled before starting")

    logger.debug("CDP_GET: %s", url)

    logger.debug("Opening URL with SeleniumBase CDP...")
    page = await driver.get(url)
    with suppress(Exception):
        await page.wait()

    _check_cancellation(cancel_flag, "Bypass cancelled after page load")

    try:
        current_url = await page.get_current_url()
        title = await page.get_title()
        logger.debug("Page loaded - URL: %s, Title: %s", current_url, title)
    except _CDP_OPERATION_ERRORS as e:
        logger.debug("Could not get page info: %s", e)

    logger.debug("Starting bypass process...")
    if await _bypass(page, cancel_flag=cancel_flag):
        html = await _read_page_source(page)
        html = await _wait_for_aa_download_page(page, url, html, cancel_flag)
        await _extract_cookies_from_cdp(driver, page, url)
        return html

    logger.warning("Bypass completed but page still shows protection")
    try:
        body = await page.evaluate("document.body ? document.body.innerText : ''")
        if body:
            preview = body
            if len(body) > _PAGE_BODY_PREVIEW_CHARS:
                preview = body[:_PAGE_BODY_PREVIEW_CHARS] + "..."
            logger.debug("Page content: %s", preview)
    except _CDP_OPERATION_ERRORS as exc:
        logger.debug("Could not inspect protected page body: %s", exc)

    return ""


def _run_bypass_in_current_process(url: str, retry: int, cancel_flag: Event | None = None) -> str:
    """Run the CDP bypass in the current process."""
    timeout = (
        _CHILD_BYPASS_TIMEOUT_SECONDS
        if os.environ.get(_BYPASS_CHILD_ENV) == "1"
        else _IN_PROCESS_BYPASS_TIMEOUT_SECONDS
    )

    async def _run_bypass() -> str:
        driver = None
        # Stop retrying while there is still time to say so. A challenge nothing can solve
        # would otherwise spend every one of `retry` passes and be cut off mid-pass by the
        # worker deadline, which surfaces to the caller as `RuntimeError: TimeoutError` -
        # a message that says nothing about protection and sent users looking at their
        # reverse proxy. Giving up a pass early returns the real "bypass failed" instead.
        deadline = time.monotonic() + timeout - _RESERVE_FOR_CLEAN_FAILURE_SECONDS
        try:
            driver = await _create_cdp_browser(url)

            for attempt in range(retry):
                _check_cancellation(cancel_flag, "Bypass cancelled before attempt")
                if attempt > 0 and time.monotonic() >= deadline:
                    logger.warning(
                        "Bypass budget spent after %s/%s attempts; giving up on %s",
                        attempt,
                        retry,
                        url,
                    )
                    break

                try:
                    result = await _get(url, driver, cancel_flag)
                    if result:
                        return result
                except BypassCancelledError, WaitingRoomTimeoutError:
                    # Retrying would restart the same queue in another browser.
                    raise
                except _CDP_OPERATION_ERRORS as e:
                    error_details = f"{type(e).__name__}: {e}"
                    logger.warning(
                        "Bypass failed (attempt %s/%s): %s", attempt + 1, retry, error_details
                    )
                    logger.debug("Stack trace: %s", traceback.format_exc())

                    # On CDP errors, quit and create a fresh browser
                    if type(e).__name__ in DRIVER_RESET_ERRORS:
                        logger.info("Restarting Chrome due to browser error...")
                        await _close_cdp_driver(driver)
                        driver = await _create_cdp_browser(url)

            logger.error("Bypass failed for %s", url)
            return ""
        finally:
            if driver:
                await _close_cdp_driver(driver)

    # Bound the wait: this holds the module-wide LOCKED for its whole duration, and neither
    # page.get() nor page.wait() has a timeout of its own. Without a deadline here a single
    # wedged CDP session blocks every subsequent bypass in the process forever.
    #
    # The helper goes through the worker too, rather than asyncio.run: that owns a loop for
    # one call and closes it on the way out, so a helper serving many requests would build
    # and tear down a loop per bypass and would carry no deadline of its own. The worker's
    # loop lives in a thread, outlives any single bypass, and cancels the coroutine when the
    # deadline passes. `_run_bypass` aims to finish inside this same budget of its own
    # accord, so reaching this deadline now means a wedged session rather than a stubborn
    # challenge - which is the only case worth reporting as a timeout.
    return _CDP_WORKER.run(_run_bypass(), timeout=timeout)


def _store_child_bypass_state(payload: dict[str, Any]) -> None:
    import_store(payload.get("cookies"), payload.get("user_agents"))


def _prepare_child_browser_env(env_vars: dict[str, str]) -> dict[str, str]:
    """Force writable browser runtime paths for the helper subprocess."""
    home_dir = BROWSER_HOME_DIR
    config_dir = home_dir / ".config"
    cache_dir = home_dir / ".cache"
    runtime_dir = BROWSER_XDG_RUNTIME_DIR

    for path in (home_dir, config_dir, cache_dir, runtime_dir):
        path.mkdir(parents=True, exist_ok=True)

    with suppress(OSError):
        runtime_dir.chmod(stat.S_IRWXU)

    env_vars["HOME"] = str(home_dir)
    env_vars["XDG_CONFIG_HOME"] = str(config_dir)
    env_vars["XDG_CACHE_HOME"] = str(cache_dir)
    env_vars["XDG_RUNTIME_DIR"] = str(runtime_dir)
    return env_vars


def _terminate_helper_session(proc: subprocess.Popen[str]) -> None:
    """Kill the bypass helper and every process it spawned.

    start_new_session makes the helper a session leader, so its pid doubles as the
    process-group id of the browser tree underneath it and one killpg reaches all of it.
    """
    if hasattr(os, "killpg"):
        with suppress(OSError):
            os.killpg(proc.pid, signal.SIGKILL)
    with suppress(OSError):
        proc.kill()
    with suppress(OSError, subprocess.SubprocessError):
        proc.wait(timeout=5)


def _part_path(result_path: Path) -> Path:
    """Where the helper stages a result before renaming it into place."""
    return result_path.with_name(result_path.name + ".part")


class _BypassHelper:
    """The helper subprocess that runs the bypasses, kept alive across them.

    Spawning it costs about 4.5 seconds of interpreter start and imports before any work
    begins, paid on every protected request - and a single search issues several. What it
    keeps is the process, not the browser: each bypass still starts and closes its own
    Chrome, so nothing accumulates between requests.

    Protocol: one JSON request per line on stdin, answered by writing the result file that
    request named. stdout and stderr stay attached to the parent's, so helper logs keep
    showing up in `docker logs` as before.

    Only one request is ever in flight - get() serializes every bypass behind LOCKED. The
    lock here is for the idle reaper, which runs on a timer thread.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._proc: subprocess.Popen[str] | None = None
        self._last_used = 0.0
        self._idle_timer: threading.Timer | None = None

    def _idle_timeout(self) -> float:
        return _coerce_non_negative_float(
            app_config.get("BYPASS_BROWSER_IDLE_TIMEOUT", _HELPER_IDLE_TIMEOUT_DEFAULT),
            _HELPER_IDLE_TIMEOUT_DEFAULT,
        )

    def _spawn(self) -> subprocess.Popen[str]:
        env_vars = os.environ.copy()
        env_vars[_BYPASS_CHILD_ENV] = "1"
        env_vars = _prepare_child_browser_env(env_vars)
        return subprocess.Popen(
            [sys.executable, "-m", "shelfmark.bypass.internal_bypasser"],
            stdin=subprocess.PIPE,
            text=True,
            env=env_vars,
            # Give the helper its own session: Chrome, Xvfb and ffmpeg inherit its process
            # group, which is what lets the cleanup sweep tell this helper's browsers apart
            # from a concurrent worker's (#1231) and lets us kill the whole tree below.
            start_new_session=True,
        )

    def _running(self) -> subprocess.Popen[str] | None:
        proc = self._proc
        if proc is None:
            return None
        if proc.poll() is not None or proc.stdin is None or proc.stdin.closed:
            return None
        return proc

    def _ensure_running(self) -> subprocess.Popen[str]:
        proc = self._running()
        if proc is not None:
            return proc
        if self._proc is not None:
            logger.info("Bypass helper exited (code %s), starting a new one", self._proc.returncode)
            self._discard()
        self._proc = self._spawn()
        return self._proc

    def _discard(self, *, wait_for_exit: bool = True) -> None:
        """Stop the helper and forget it.

        `wait_for_exit` belongs to a helper that could still act on the closed pipe: an
        idle one is sitting in its stdin read, notices EOF and exits on its own. A helper
        dropped mid-bypass is blocked inside the solve and will not return to that read,
        so the grace cannot end in anything but the kill below - and the caller waiting it
        out is a user cancelling a download, holding LOCKED while every other bypass in
        the worker queues behind them.
        """
        proc = self._proc
        self._proc = None
        if proc is None:
            return

        # Closing stdin ends the helper's request loop, so an idle helper gets to exit on
        # its own. One mid-bypass cannot answer, and is killed below.
        with suppress(OSError):
            if proc.stdin is not None and not proc.stdin.closed:
                proc.stdin.close()
        if wait_for_exit:
            try:
                proc.wait(timeout=_HELPER_SHUTDOWN_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                logger.warning("Bypass helper did not exit on request, killing its session")

        # Tear the session down either way: a helper killed mid-bypass leaves its Chrome
        # and Xvfb running, and those leftovers are what made the next worker's browser
        # fail to start. Harmless once it has already exited.
        _terminate_helper_session(proc)

    def _cancel_idle_timer(self) -> None:
        if self._idle_timer is not None:
            self._idle_timer.cancel()
            self._idle_timer = None

    def _arm_idle_timer(self) -> None:
        self._cancel_idle_timer()
        timeout = self._idle_timeout()
        if self._proc is None or timeout <= 0:
            return
        timer = threading.Timer(timeout, self._reap_if_idle)
        timer.daemon = True
        self._idle_timer = timer
        timer.start()

    def _reap_if_idle(self) -> None:
        with self._lock:
            if self._proc is None:
                return
            idle_for = time.monotonic() - self._last_used
            timeout = self._idle_timeout()
            if idle_for < timeout:
                # A bypass started while this timer was waiting for the lock.
                self._arm_idle_timer()
                return
            logger.info("Closing idle bypass helper after %.0fs without work", idle_for)
            self._discard()

    def run(
        self,
        payload: dict[str, Any],
        timeout: float,
        cancel_flag: Event | None,
    ) -> dict[str, Any]:
        with self._lock:
            self._cancel_idle_timer()
            try:
                return self._exchange(payload, timeout, cancel_flag)
            finally:
                self._last_used = time.monotonic()
                self._arm_idle_timer()

    def _exchange(
        self,
        payload: dict[str, Any],
        timeout: float,
        cancel_flag: Event | None,
    ) -> dict[str, Any]:
        request_line = json.dumps(payload) + "\n"
        proc = self._ensure_running()
        try:
            self._write(proc, request_line)
        except OSError as exc:
            # A live helper can die between the liveness check and the write, so one retry
            # on a fresh process. A fresh one failing here is a real failure.
            logger.info("Bypass helper closed its pipe (%s), retrying on a new one", exc)
            # Nothing to ask of a helper we cannot write to: its read end is already gone.
            self._discard(wait_for_exit=False)
            proc = self._ensure_running()
            self._write(proc, request_line)

        return self._await_result(proc, Path(str(payload["result_path"])), timeout, cancel_flag)

    def _write(self, proc: subprocess.Popen[str], request_line: str) -> None:
        if proc.stdin is None:
            msg = "Bypass helper has no stdin pipe"
            raise OSError(msg)
        proc.stdin.write(request_line)
        proc.stdin.flush()

    def _await_result(
        self,
        proc: subprocess.Popen[str],
        result_path: Path,
        timeout: float,
        cancel_flag: Event | None,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        try:
            while not result_path.exists():
                if proc.poll() is not None:
                    returncode = proc.returncode
                    self._discard(wait_for_exit=False)
                    msg = f"Internal bypasser helper exited without a result (code {returncode})"
                    raise RuntimeError(msg)
                if cancel_flag is not None and cancel_flag.is_set():
                    # The helper is mid-bypass and cannot be told to stop, so it goes.
                    self._discard(wait_for_exit=False)
                    _check_cancellation(cancel_flag, "Bypass cancelled while waiting for helper")
                if time.monotonic() >= deadline:
                    self._discard(wait_for_exit=False)
                    msg = "Internal bypasser helper process timed out"
                    raise TimeoutError(msg)
                time.sleep(_HELPER_RESULT_POLL_SECONDS)

            return json.loads(result_path.read_text(encoding="utf-8"))
        finally:
            # Every way out of here is final for this request: either the answer has been
            # read, or the helper that would have written it has just been killed. Nothing
            # will write these paths afterwards and nothing will come looking for them, so
            # they are cleaned on the failure paths too - otherwise every cancelled
            # download and every wedged solve leaves one behind for the container's life.
            for path in (result_path, _part_path(result_path)):
                with suppress(OSError):
                    path.unlink()


_BYPASS_HELPER = _BypassHelper()


def _get_via_subprocess(url: str, retry: int, cancel_flag: Event | None = None) -> str:
    """Run the browser bypass in a helper process isolated from gunicorn/gevent."""
    _check_cancellation(cancel_flag, "Bypass cancelled before helper process")
    result_path = (
        Path(tempfile.gettempdir()) / f"shelfmark-bypass-{os.getpid()}-{time.time_ns()}.json"
    )
    # DNS provider state lives only in the parent's memory (no disk persistence), so the
    # freshly spawned helper would otherwise pre-resolve AA hostnames against the system
    # resolver - which may be blocked or hijacked by the user's ISP. Pass the parent's
    # active DNS config so the helper mirrors it (e.g. DoH) when building Chrome's host
    # resolver rules. Sent with every request, not just at spawn, because a helper outlives
    # changes the parent makes to its DNS provider.
    payload = {
        "url": url,
        "retry": retry,
        "result_path": str(result_path),
        "dns_config": network.get_dns_config(),
    }
    result = _BYPASS_HELPER.run(payload, _BYPASS_SUBPROCESS_TIMEOUT_SECONDS, cancel_flag)

    if not isinstance(result, dict):
        msg = "Internal bypasser helper returned an invalid result"
        raise TypeError(msg)

    if not result.get("ok"):
        error_type = result.get("error_type", "RuntimeError")
        error = result.get("error", "Internal bypasser helper failed")
        trace = result.get("traceback")
        if trace:
            logger.debug("Internal bypasser helper traceback: %s", trace)
        if error_type == WaitingRoomTimeoutError.__name__:
            raise WaitingRoomTimeoutError(error)
        msg = f"{error_type}: {error}"
        raise RuntimeError(msg)

    _store_child_bypass_state(result)
    html = result.get("html", "")
    return html if isinstance(html, str) else ""


def get(url: str, retry: int | None = None, cancel_flag: Event | None = None) -> str:
    """Fetch a URL with protection bypass. Creates fresh Chrome instance for each bypass."""
    retry = retry if retry is not None else _coerce_positive_int(app_config.MAX_RETRY, 10)

    with LOCKED:
        # Try cookies first - another request may have completed bypass while waiting
        cached_result = _try_with_cached_cookies(url, urlparse(url).hostname or "")
        if cached_result:
            return cached_result

        # Re-checked after the cached attempt, not just in get_bypassed_page: that check
        # ran before the queue, and this call may have spent minutes holding for LOCKED
        # while another request collected a 429 (or collected one itself, just above).
        # A solve cannot clear a throttle - the challenge renders, the solve "succeeds",
        # and the cleared request is refused again while the backoff is renewed.
        remaining = network.host_cooldown_remaining(url)
        if remaining > 0:
            hostname = urlparse(url).hostname or url
            msg = (
                f"{hostname} is rate-limited (429); skipping bypass for ~{remaining:.0f}s "
                "until the cooldown clears."
            )
            raise network.RateLimitedError(msg)

        if env.DOCKERMODE and os.environ.get(_BYPASS_CHILD_ENV) != "1":
            return _get_via_subprocess(url, retry, cancel_flag)
        return _run_bypass_in_current_process(url, retry, cancel_flag)


def _get_proxy_string(url: str) -> str | None:
    """Return a single proxy string for CDP, honoring NO_PROXY."""
    proxies = get_proxies(url)
    if not proxies:
        return None
    proxy_url = proxies.get("https") or proxies.get("http")
    return proxy_url or None


async def _create_cdp_browser(url: str) -> Any:
    """Create a fresh CDP browser instance."""
    browser_args = _get_browser_args()
    screen_width, screen_height = get_screen_size()
    display_width = screen_width + 100
    display_height = screen_height + 150
    proxy = _get_proxy_string(url)

    logger.debug("Creating Pure CDP browser with args: %s", browser_args)
    logger.debug("Browser screen size: %sx%s", screen_width, screen_height)

    try:
        driver = await asyncio.wait_for(
            cdp_driver.start_async(
                headless=False,
                headed=False,
                xvfb=True,
                xvfb_metrics=f"{display_width},{display_height}",
                sandbox=False,
                lang="en",
                incognito=True,
                ad_block=True,
                proxy=proxy,
                browser_args=browser_args,
            ),
            timeout=_BROWSER_START_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        logger.warning(
            "Pure CDP browser startup timed out after %.0fs",
            _BROWSER_START_TIMEOUT_SECONDS,
        )
        if env.DOCKERMODE:
            _cleanup_orphan_processes()
        raise
    except Exception as e:
        logger.warning("Pure CDP browser startup failed: %s: %s", type(e).__name__, e)
        logger.warning(
            "SeleniumBase runtime paths: cwd=%s; %s; %s; %s; %s",
            Path.cwd(),
            _describe_runtime_path(SELENIUMBASE_DOWNLOADS_DIR),
            _describe_runtime_path("/app/downloaded_files"),
            _describe_runtime_path("downloaded_files"),
            _describe_runtime_path(tempfile.gettempdir()),
        )
        if env.DOCKERMODE:
            _cleanup_orphan_processes()
        msg = f"Pure CDP browser startup failed: {e}"
        raise RuntimeError(msg) from e

    if _has_window_rect_page(driver):
        try:
            await driver.page.set_window_rect(0, 0, screen_width, screen_height)
        except _CDP_OPERATION_ERRORS as e:
            logger.debug("Failed to set window size: %s", e)

    # Start FFmpeg recording if debug mode (record each bypass session)
    if app_config.get("DEBUG", False) and not DISPLAY.get("ffmpeg"):
        _start_ffmpeg_recording(display=os.environ.get("DISPLAY", ":0"))

    await asyncio.sleep(_coerce_non_negative_float(app_config.DEFAULT_SLEEP, 5.0))
    logger.info("Chrome browser ready (Pure CDP)")
    logger.log_resource_usage()
    return driver


async def _close_cdp_driver(driver: Any) -> None:
    """Close CDP connections and stop the browser."""
    if not driver:
        return

    logger.debug("Quitting Chrome browser (CDP)...")

    _stop_ffmpeg_recording()

    try:
        connections = []
        if hasattr(driver, "connection") and driver.connection:
            connections.append(driver.connection)
        if hasattr(driver, "targets") and driver.targets:
            connections.extend(driver.targets)
        for conn in connections:
            await _close_websocket_connection(conn)
    except _CDP_OPERATION_ERRORS as e:
        logger.debug("Error during connection cleanup: %s", e)

    try:
        driver.stop()
        logger.debug("Stopped CDP browser")
    except _CDP_OPERATION_ERRORS as e:
        logger.debug("CDP stop: %s", e)

    if env.DOCKERMODE:
        await asyncio.sleep(0.3)
        try:
            pid = getattr(driver, "_process_pid", None)

            def _pid_alive(check_pid: int) -> bool:
                try:
                    os.kill(check_pid, 0)
                except ProcessLookupError:
                    return False
                except PermissionError:
                    return True
                return True

            if pid and _pid_alive(pid):
                try:
                    os.kill(pid, signal.SIGTERM)
                    await asyncio.sleep(0.1)
                    if _pid_alive(pid):
                        os.kill(pid, signal.SIGKILL)
                    logger.debug("Killed Chrome pid %s", pid)
                except (OSError, RuntimeError, TypeError, ValueError) as e:
                    logger.debug("Failed to kill Chrome pid %s: %s", pid, e)
        except (OSError, RuntimeError, TypeError, ValueError) as e:
            logger.debug("Process cleanup failed: %s", e)

    logger.log_resource_usage()


async def _close_websocket_connection(conn: Any) -> None:
    """Close one websocket-like connection, ignoring best-effort failures."""
    try:
        await conn.aclose()
    except _CDP_OPERATION_ERRORS as e:
        logger.debug("Failed to close websocket connection: %s", e)


def _start_ffmpeg_recording(display: str) -> None:
    """Start FFmpeg screen recording for debug mode."""
    global DISPLAY
    RECORDING_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%y%m%d-%H%M%S")
    output_file = RECORDING_DIR / f"screen_recording_{timestamp}.mp4"

    screen_width, screen_height = get_screen_size()
    display_width = screen_width + 100
    display_height = screen_height + 150

    ffmpeg_cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "x11grab",
        "-video_size",
        f"{display_width}x{display_height}",
        "-i",
        display,
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-maxrate",
        "700k",
        "-bufsize",
        "1400k",
        "-crf",
        "36",
        "-pix_fmt",
        "yuv420p",
        "-tune",
        "animation",
        "-x264-params",
        "bframes=0:deblock=-1,-1",
        "-r",
        "15",
        "-an",
        output_file.as_posix(),
        "-nostats",
        # Was "0", which discards everything including the reason it could not start.
        # Recordings have been arriving empty with no explanation anywhere: on issue
        # #1276 all three of a session's recordings were gone and the log said only
        # "FFmpeg already stopped", because ffmpeg exits before creating the file when
        # it cannot open the X display. Errors only - this is a debug-mode recorder, not
        # something to make chatty.
        "-loglevel",
        "error",
    ]
    logger.debug("Starting FFmpeg recording to %s", output_file)
    logger.debug_trace(f"FFmpeg command: {' '.join(ffmpeg_cmd)}")
    # Kept beside the recording so it travels in the debug bundle, which is the only
    # place anyone will look for it. A file rather than a pipe: nothing here would drain
    # a pipe, and a full one would wedge ffmpeg partway through a capture.
    error_log = output_file.with_suffix(".ffmpeg.log")
    try:
        stderr_handle = error_log.open("wb")
    except OSError as exc:
        logger.debug("Could not open FFmpeg error log %s: %s", error_log, exc)
        stderr_handle = None
    DISPLAY["ffmpeg"] = subprocess.Popen(
        ffmpeg_cmd, stderr=stderr_handle, stdout=subprocess.DEVNULL
    )
    if stderr_handle is not None:
        # The child holds its own descriptor; this one has done its job.
        stderr_handle.close()
    DISPLAY["ffmpeg_output"] = output_file
    DISPLAY["ffmpeg_error_log"] = error_log


def _ffmpeg_error_summary() -> str:
    """What ffmpeg wrote to stderr, for the log line that reports it died."""
    error_log = DISPLAY.get("ffmpeg_error_log")
    if not error_log:
        return "No FFmpeg error log was captured."
    try:
        text = Path(error_log).read_text(encoding="utf-8", errors="replace").strip()
    except OSError as exc:
        return f"FFmpeg error log unreadable ({exc})."
    if not text:
        return f"FFmpeg logged nothing to {error_log}."
    return f"FFmpeg said: {text[-_FFMPEG_ERROR_TAIL_CHARS:]}"


def _stop_ffmpeg_recording() -> None:
    """Stop FFmpeg screen recording if running."""
    import signal

    global DISPLAY
    proc = DISPLAY.get("ffmpeg")
    if not proc:
        return
    if proc.poll() is not None:
        # Not "already stopped" - ffmpeg was asked to record until now and is gone, so
        # the recording for this bypass does not exist. Say so, with the reason, rather
        # than leaving an empty recording/ directory to be discovered later.
        logger.warning(
            "FFmpeg exited early (code %s); no recording for this bypass. %s",
            proc.returncode,
            _ffmpeg_error_summary(),
        )
        DISPLAY["ffmpeg"] = None
        DISPLAY["ffmpeg_output"] = None
        DISPLAY["ffmpeg_error_log"] = None
        return
    try:
        proc.send_signal(signal.SIGINT)
        proc.wait(timeout=5)
        logger.debug("Stopped ffmpeg recording")
    except _SUBPROCESS_OPERATION_ERRORS as e:
        logger.debug("ffmpeg stop: %s", e)
        with suppress(Exception):
            proc.terminate()
            proc.wait(timeout=2)
        with suppress(Exception):
            proc.kill()
    DISPLAY["ffmpeg"] = None
    DISPLAY["ffmpeg_output"] = None
    DISPLAY["ffmpeg_error_log"] = None


def _try_with_cached_cookies(url: str, hostname: str) -> str | None:
    """Attempt request with cached cookies before using Chrome."""
    cookies = get_cf_cookies_for_domain(hostname)
    if not cookies:
        return None

    try:
        headers = {}
        stored_ua = get_cf_user_agent_for_domain(hostname)
        if stored_ua:
            headers["User-Agent"] = stored_ua

        logger.debug("Trying request with cached cookies: %s", url)
        response = requests.get(
            url,
            cookies=cookies,
            headers=headers,
            proxies=get_proxies(url),
            timeout=(5, 10),
            verify=get_ssl_verify(url),
        )
        if response.status_code == HTTPStatus.OK:
            if is_aa_waiting_room(url, response.text):
                # Clearance is valid, but HTTP cannot run the queue's JavaScript.
                # Enforce this here for both cache checks, including the locked one.
                return None
            logger.debug("Cached cookies worked, skipped Chrome bypass")
            return response.text
        if response.status_code == HTTPStatus.TOO_MANY_REQUESTS:
            # Throttled, not challenged. The clearance is still good - the origin is
            # rate-limiting this IP and would answer 429 to a browser holding the very
            # same cookies. Discarding it here (as every other rejection does) meant a
            # solve won seconds earlier was thrown away and the next query bought its
            # own 20-60s browser solve, which is itself more traffic at a host that has
            # just asked for less. Keep it, arm the backoff, and let the caller wait.
            wait = network.note_rate_limited(url)
            logger.debug(
                "Cached cookies hit a 429 for %s; keeping them and backing off ~%.0fs",
                url,
                wait,
            )
            return None
        logger.debug(
            "Cached cookies rejected (%s) for %s; discarding them",
            response.status_code,
            url,
        )
    except _REQUEST_OPERATION_ERRORS as exc:
        # A redirect loop lands here too: DDoS-Guard answers a dead clearance cookie
        # with an endless ?check=1 bounce rather than a status we can read.
        logger.debug("Cached cookie retry failed for %s: %s", url, exc)

    # Reached only when the cached cookies did not produce a page, so they are no
    # longer clearance. Dropping them now means the imminent Chrome solve starts from
    # a clean slate and later requests cannot re-present the same rejected cookie.
    # Guarded because clear_cf_cookies("") means "every host", which would wipe
    # clearance for sites that are working fine.
    if hostname:
        clear_cf_cookies(hostname)
    return None


def max_duration_seconds() -> float:
    """Upper bound on how long get_bypassed_page() can take for one URL.

    Both branches of get() are capped at _BYPASS_SUBPROCESS_TIMEOUT_SECONDS, and
    get_bypassed_page() may call it twice (once, then again after a mirror/DNS rotation).
    Callers use this to declare a stall-detection grace; see shelfmark.download.activity.
    """
    return 2 * _BYPASS_SUBPROCESS_TIMEOUT_SECONDS


def get_bypassed_page(
    url: str, selector: network.AAMirrorSelector | None = None, cancel_flag: Event | None = None
) -> str | None:
    """Fetch HTML content from a URL using the internal Cloudflare Bypasser."""
    sel = selector or network.AAMirrorSelector()
    attempt_url = sel.rewrite(url)
    hostname = urlparse(attempt_url).hostname or ""

    # A 429 means the origin is throttling this IP; the challenge still renders, so a
    # solve "succeeds" but the cleared request is rejected again and the throttle is only
    # renewed. Never spend a minutes-long Chrome solve on a cooling-down host - fail fast
    # so the caller waits the backoff out instead of looping the solve.
    remaining = network.host_cooldown_remaining(attempt_url)
    if remaining > 0:
        msg = (
            f"{hostname} is rate-limited (429); skipping bypass for ~{remaining:.0f}s "
            "until the cooldown clears."
        )
        raise network.RateLimitedError(msg)

    cached_result = _try_with_cached_cookies(attempt_url, hostname)
    if cached_result:
        return cached_result

    try:
        response_html = get(attempt_url, cancel_flag=cancel_flag)
    except BypassCancelledError, WaitingRoomTimeoutError:
        raise
    except _CDP_OPERATION_ERRORS + _REQUEST_OPERATION_ERRORS:
        _check_cancellation(cancel_flag, "Bypass cancelled")
        new_base, action = sel.next_mirror_or_rotate_dns()
        if action in ("mirror", "dns") and new_base:
            attempt_url = sel.rewrite(url)
            response_html = get(attempt_url, cancel_flag=cancel_flag)
        else:
            raise

    if not response_html.strip():
        msg = "Failed to bypass Cloudflare"
        raise requests.exceptions.RequestException(msg)

    return response_html


def _dns_fingerprint(dns_config: dict[str, Any]) -> tuple[str, tuple[str, ...], bool]:
    """Reduce a DNS config to what has to match for two of them to be the same one."""
    provider = str(dns_config.get("provider") or "").strip().lower()
    servers = dns_config.get("servers") if provider == "manual" else None
    server_list = tuple(str(server) for server in servers) if isinstance(servers, list) else ()
    return (provider, server_list, bool(dns_config.get("doh_enabled")))


def _apply_parent_dns_config(dns_config: dict[str, Any]) -> None:
    """Mirror the parent process's active DNS provider in this helper subprocess.

    DNS state is in-memory only, so a helper left to itself would pre-resolve AA hostnames
    (for Chrome's --host-resolver-rules) against a resolver that may be blocked or
    hijacked. Re-applying the parent's provider keeps the helper on the same DoH/custom
    resolver the parent already validated.

    Compared against what this process is *actually* resolving through, rather than
    against the last config it happened to be handed. The helper now outlives the request,
    so it has to be able to travel back to auto as well as away from it - which a user
    flipping CUSTOM_DNS in settings does live, without a restart - and asking the network
    module what it is doing beats keeping a second, drifting copy of that answer here.
    """
    wanted = _dns_fingerprint(dns_config)
    provider, servers, use_doh = wanted
    if not provider:
        return
    # set_dns_provider() rebuilds the resolvers, so it is worth doing only on a real change.
    if wanted == _dns_fingerprint(network.get_dns_config()):
        return

    try:
        network.set_dns_provider(provider, list(servers) or None, use_doh=use_doh)
    except (OSError, RuntimeError, ValueError) as exc:
        logger.warning("Could not apply parent DNS config (%s): %s", provider, exc)


def _terminate_own_session() -> None:
    """SIGKILL this process and every process it spawned, browser included."""
    if hasattr(os, "killpg") and os.getpgrp() == os.getpid():
        with suppress(OSError):
            os.killpg(os.getpgrp(), signal.SIGKILL)
    # A thread cannot end the process any other way; sys.exit would only end itself.
    os._exit(1)


def _watch_parent_process(original_ppid: int, interval: float) -> None:
    """Take the browser down with us once the app process that spawned us is gone.

    Cleanup only reclaims process groups whose leader has died, so a helper that outlives
    its parent (worker restart, OOM kill) would sit there holding a browser that no later
    bypass is allowed to touch.
    """
    while os.getppid() == original_ppid:
        time.sleep(interval)
    logger.warning("Bypass helper lost its parent process; taking the browser down")
    _terminate_own_session()


def _start_parent_watchdog() -> None:
    """Watch the spawning process in the background for the life of this helper."""
    threading.Thread(
        target=_watch_parent_process,
        args=(os.getppid(), _PARENT_WATCHDOG_INTERVAL_SECONDS),
        daemon=True,
        name="BypassParentWatchdog",
    ).start()


def _publish_result(result_path: Path, payload: dict[str, Any]) -> None:
    """Write the result file atomically.

    The parent decides the request is answered the moment this path exists, so it must
    never observe a half-written file. Rename within the same directory is atomic.
    """
    tmp_path = _part_path(result_path)
    tmp_path.write_text(json.dumps(payload), encoding="utf-8")
    tmp_path.replace(result_path)


def _handle_child_request(request_line: str) -> int:
    """Answer one request from the parent."""
    request = json.loads(request_line or "{}")
    result_path = Path(str(request["result_path"]))
    url = str(request["url"])
    retry = _coerce_positive_int(
        request.get("retry"), _coerce_positive_int(app_config.MAX_RETRY, 10)
    )

    dns_config = request.get("dns_config")
    if isinstance(dns_config, dict):
        _apply_parent_dns_config(dns_config)

    # The parent owns the cookie store; this process only solves. Starting each request
    # from an empty store is what a helper spawned per request gave for free, and losing
    # it is what let clearance the parent had deliberately purged for some *other* host
    # survive here and get merged back over the parent's copy by the export below - the
    # dead-cookie resurrection that http.py's _redirect_loop_handoff purges to avoid.
    # Nothing is lost by dropping it: get() below re-checks cached cookies, and the
    # parent already ran that same check against a store that is a superset of this one.
    clear_cf_cookies()

    try:
        html = get(url, retry=retry)
        cookies, user_agents = export_store()
        payload = {
            "ok": True,
            "html": html,
            "cookies": cookies,
            "user_agents": user_agents,
        }
        _publish_result(result_path, payload)
    except Exception as exc:  # noqa: BLE001 - helper boundary must serialize failures.
        payload = {
            "ok": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }
        _publish_result(result_path, payload)
        return 1
    return 0


def _run_child_process() -> int:
    """CLI entrypoint used by the Docker helper subprocess.

    Serves one request per line of stdin until the parent closes the pipe, so a burst of
    protected requests - a single search is several - pays the interpreter start and imports
    once instead of per request. Each bypass still gets its own browser, closed before the
    answer is published.
    """
    exit_code = 0
    for line in sys.stdin:
        request_line = line.strip()
        if not request_line:
            continue
        exit_code = _handle_child_request(request_line)
    return exit_code


if __name__ == "__main__":
    # Started here rather than in _run_child_process() so it only ever watches a real
    # spawned helper, never a test or an embedded call.
    _start_parent_watchdog()
    raise SystemExit(_run_child_process())
