"""Keep Anna's JavaScript waiting room alive until its own navigation completes."""

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import requests

import shelfmark.bypass.internal_bypasser as b
from shelfmark.bypass import BypassCancelledError
from shelfmark.bypass.waiting_room import WaitingRoomTimeoutError, is_aa_waiting_room

URL = "https://annas-archive.gl/slow_download/abc/0/0"
WAIT = '<html><span class="js-partner-countdown">25</span></html>'
ZERO = WAIT.replace(">25<", ">0<")
READY = '<html><a href="https://files.example/book.epub">Download now</a></html>'


def _snapshot(html=READY, *, waiting=False, title="Anna's Archive", body=None):
    return {
        "html": html,
        "waiting": waiting,
        "title": title,
        "url": URL,
        "body": body
        if body is not None
        else "Your download is ready. Follow the download link to obtain the requested file.",
    }


@pytest.fixture
def cached_http(monkeypatch):
    response = SimpleNamespace(status_code=200, text=WAIT)
    cleared = Mock()
    monkeypatch.setattr(b, "get_cf_cookies_for_domain", lambda _: {"cf_clearance": "valid"})
    monkeypatch.setattr(b, "clear_cf_cookies", cleared)
    monkeypatch.setattr(b.requests, "get", Mock(return_value=response))
    monkeypatch.setattr(b.network, "host_cooldown_remaining", lambda _: 0)
    return response, cleared


@pytest.mark.parametrize(
    ("url", "html", "expected"),
    [
        (URL, WAIT, True),
        (URL, ZERO, True),
        (URL, READY, False),
        (URL.replace("/slow_download/abc/0/0", "/search"), WAIT, False),
        (URL, '<script>document.querySelector(".js-partner-countdown")</script>', False),
    ],
)
def test_waiting_room_detection(url, html, expected):
    assert is_aa_waiting_room(url, html) is expected


def test_get_keeps_same_tab_through_zero_reload_and_protection(monkeypatch):
    page = SimpleNamespace(
        wait=AsyncMock(),
        get_current_url=AsyncMock(return_value=URL),
        get_title=AsyncMock(return_value="Anna's Archive"),
    )
    driver = SimpleNamespace(get=AsyncMock(return_value=page))
    monkeypatch.setattr(b, "_bypass", AsyncMock(return_value=True))
    # A transient protocol error and a protection page can occur during navigation.
    read = AsyncMock(return_value=WAIT)
    page.evaluate = AsyncMock(
        side_effect=[
            _snapshot(WAIT, waiting=True),
            _snapshot(ZERO, waiting=True),
            b.ProtocolException("navigating"),
            _snapshot("<html>DDOS-GUARD</html>", title="DDOS-GUARD"),
            _snapshot("", body=""),
            _snapshot(),
        ]
    )
    monkeypatch.setattr(b, "_read_page_source", read)
    cookies = AsyncMock()
    monkeypatch.setattr(b, "_extract_cookies_from_cdp", cookies)
    monkeypatch.setattr(b.asyncio, "sleep", AsyncMock())

    assert asyncio.run(b._get(URL, driver)) == READY
    driver.get.assert_awaited_once_with(URL)
    read.assert_awaited_once_with(page)
    cookies.assert_awaited_once_with(driver, page, URL)


def test_wait_can_be_cancelled(monkeypatch):
    cancel = threading.Event()

    async def cancel_during_sleep(_delay):
        cancel.set()

    monkeypatch.setattr(b.asyncio, "sleep", cancel_during_sleep)
    page = SimpleNamespace(evaluate=AsyncMock(return_value=_snapshot(WAIT, waiting=True)))
    with pytest.raises(BypassCancelledError):
        asyncio.run(b._wait_for_aa_download_page(page, URL, WAIT, cancel))


def test_stuck_queue_has_a_deadline(monkeypatch):
    monkeypatch.setattr(b, "_AA_WAITING_ROOM_TIMEOUT_SECONDS", 0.01)
    with pytest.raises(WaitingRoomTimeoutError, match="waiting room"):
        page = SimpleNamespace(evaluate=AsyncMock(return_value=_snapshot(WAIT, waiting=True)))
        asyncio.run(b._wait_for_aa_download_page(page, URL, WAIT))


def test_queue_timeout_does_not_open_another_browser(monkeypatch):
    driver = object()
    create = AsyncMock(return_value=driver)
    close = AsyncMock()
    get = AsyncMock(side_effect=WaitingRoomTimeoutError("Queue timed out"))
    monkeypatch.setattr(b, "_create_cdp_browser", create)
    monkeypatch.setattr(b, "_close_cdp_driver", close)
    monkeypatch.setattr(b, "_get", get)
    monkeypatch.setattr(b, "_CDP_WORKER", SimpleNamespace(run=lambda task, **kw: asyncio.run(task)))
    with pytest.raises(WaitingRoomTimeoutError, match="Queue timed out"):
        b._run_bypass_in_current_process(URL, retry=10)
    create.assert_awaited_once()
    get.assert_awaited_once()
    close.assert_awaited_once_with(driver)


@pytest.mark.parametrize("html", [READY, "<html>ordinary page</html>"])
def test_non_waiting_page_returns_without_polling(html):
    assert asyncio.run(b._wait_for_aa_download_page(object(), URL, html)) == html


@pytest.mark.parametrize(
    ("enabled", "external", "fallback", "handoff"),
    [
        (True, False, True, True),
        (False, False, True, False),
        (True, True, True, False),
        (True, False, False, False),
    ],
)
def test_http_200_timer_handoff_honors_browser_settings(
    monkeypatch, enabled, external, fallback, handoff
):
    import shelfmark.download.http as http

    response = requests.Response()
    response.status_code = 200
    response.url = URL
    response._content = WAIT.encode()
    monkeypatch.setattr(http.requests, "get", lambda *a, **kw: response)
    monkeypatch.setattr(http, "_apply_cf_bypass", lambda *a: {})
    monkeypatch.setattr(http, "_is_cf_bypass_enabled", lambda: enabled)
    monkeypatch.setattr(http, "_is_using_external_bypasser", lambda: external)
    monkeypatch.setattr(http, "_bypass_grace_seconds", lambda: 450)
    calls = []
    monkeypatch.setattr(http, "get_bypassed_page", lambda url, *a: calls.append(url) or READY)
    selector = SimpleNamespace(rewrite=lambda url: url)
    result = http.html_get_page(
        URL, retry=1, selector=selector, success_delay=0, allow_bypasser_fallback=fallback
    )
    assert result == (READY if handoff else WAIT)
    assert calls == ([URL] if handoff else [])


def test_deadline_also_bounds_a_stalled_page_read(monkeypatch):
    async def stalled_read(_expression):
        await asyncio.Event().wait()

    monkeypatch.setattr(b, "_AA_WAITING_ROOM_TIMEOUT_SECONDS", 0.01)
    page = SimpleNamespace(evaluate=stalled_read)
    with pytest.raises(WaitingRoomTimeoutError):
        asyncio.run(b._wait_for_aa_download_page(page, URL, WAIT))


def test_helper_preserves_waiting_room_timeout_type(monkeypatch):
    monkeypatch.setattr(
        b,
        "_BYPASS_HELPER",
        SimpleNamespace(
            run=lambda *args: {
                "ok": False,
                "error_type": "WaitingRoomTimeoutError",
                "error": "Queue timed out",
            }
        ),
    )
    with pytest.raises(WaitingRoomTimeoutError, match="Queue timed out"):
        b._get_via_subprocess(URL, retry=10)


def test_queue_timeout_does_not_rotate_mirror_and_restart_wait(monkeypatch):
    monkeypatch.setattr(b.network, "host_cooldown_remaining", lambda _: 0)
    monkeypatch.setattr(b, "_try_with_cached_cookies", lambda *_: None)

    def timeout(*args, **kwargs):
        raise WaitingRoomTimeoutError("Queue timed out")

    monkeypatch.setattr(b, "get", timeout)
    # No rotation method: trying to rotate after this failure is an error.
    selector = SimpleNamespace(rewrite=lambda url: url)
    with pytest.raises(WaitingRoomTimeoutError, match="Queue timed out"):
        b.get_bypassed_page(URL, selector)


def test_http_reports_queue_timeout_without_blaming_cloudflare(monkeypatch):
    import shelfmark.download.http as http

    def timeout(*args, **kwargs):
        raise WaitingRoomTimeoutError("Anna's Archive waiting room did not finish within 300s")

    monkeypatch.setattr(http, "_is_cf_bypass_enabled", lambda: True)
    monkeypatch.setattr(http, "_bypass_grace_seconds", lambda: 450)
    monkeypatch.setattr(http, "get_bypassed_page", timeout)
    selector = SimpleNamespace(rewrite=lambda url: url, last_failure=None)
    result = http.html_get_page(URL, retry=10, selector=selector, use_bypasser=True)
    assert result == ""
    assert selector.last_failure == "Anna's Archive waiting room did not finish within 300s"


@pytest.mark.parametrize("docker_mode", [False, True])
@pytest.mark.parametrize("cached", [WAIT, READY])
def test_cached_pages_through_both_entry_points(monkeypatch, cached_http, docker_mode, cached):
    response, cleared = cached_http
    response.text = cached
    monkeypatch.setattr(b.env, "DOCKERMODE", docker_mode)
    monkeypatch.delenv(b._BYPASS_CHILD_ENV, raising=False)
    calls = []
    target = "_get_via_subprocess" if docker_mode else "_run_bypass_in_current_process"
    monkeypatch.setattr(b, target, lambda url, *a, **kw: calls.append(url) or READY)
    selector = SimpleNamespace(rewrite=lambda url: url)

    assert b.get_bypassed_page(URL, selector) == READY
    assert calls == ([URL] if cached == WAIT else [])
    # A waiting room does not invalidate the clearance cookies it was served with.
    cleared.assert_not_called()


def test_page_readiness_and_returned_html_belong_to_the_same_snapshot(monkeypatch):
    old_page = "<html>DDOS-GUARD</html>"
    page = SimpleNamespace(
        evaluate=AsyncMock(
            side_effect=[
                _snapshot(old_page, title="DDOS-GUARD"),
                _snapshot(),
            ]
        )
    )
    # Reproduce navigation after the HTML read: a separate live-page check already
    # sees a ready page. The old loop incorrectly returned the captured interstitial.
    monkeypatch.setattr(b, "_read_page_source", AsyncMock(return_value=old_page))
    monkeypatch.setattr(b, "_is_bypassed", AsyncMock(return_value=True))
    monkeypatch.setattr(b.asyncio, "sleep", AsyncMock())
    assert asyncio.run(b._wait_for_aa_download_page(page, URL, WAIT)) == READY


def test_cancellation_interrupts_a_stalled_browser_read(monkeypatch):
    cancel = threading.Event()

    async def stalled_read(_expression):
        cancel.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(b, "_AA_WAITING_ROOM_POLL_SECONDS", 0.01)
    page = SimpleNamespace(evaluate=stalled_read)
    with pytest.raises(BypassCancelledError):
        asyncio.run(b._wait_for_aa_download_page(page, URL, WAIT, cancel))


def test_cancellation_after_read_wins_over_ready_result(monkeypatch):
    cancel = threading.Event()

    async def finish_and_cancel(_expression):
        cancel.set()
        return _snapshot()

    page = SimpleNamespace(evaluate=finish_and_cancel)
    with pytest.raises(BypassCancelledError):
        asyncio.run(b._wait_for_aa_download_page(page, URL, WAIT, cancel))


@pytest.mark.parametrize("transient", [TimeoutError(), TypeError("missing CDP result"), None])
def test_transient_read_failure_keeps_the_same_tab(monkeypatch, transient):
    page = SimpleNamespace(evaluate=AsyncMock(side_effect=[transient, _snapshot()]))
    monkeypatch.setattr(b.asyncio, "sleep", AsyncMock())
    assert asyncio.run(b._wait_for_aa_download_page(page, URL, WAIT)) == READY
    assert page.evaluate.await_count == 2


def test_slow_snapshot_is_not_cancelled_and_reissued(monkeypatch):
    async def slow_read(_expression):
        await asyncio.sleep(0.03)
        return _snapshot()

    monkeypatch.setattr(b, "_AA_WAITING_ROOM_POLL_SECONDS", 0.005)
    page = SimpleNamespace(evaluate=AsyncMock(side_effect=slow_read))
    assert asyncio.run(b._wait_for_aa_download_page(page, URL, WAIT)) == READY
    page.evaluate.assert_awaited_once()
