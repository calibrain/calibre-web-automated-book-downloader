"""The wall-clock budget on a release search.

Issue #1276: `/api/releases` is synchronous and nothing bounded it, while the bypass path
it can reach was allowed ~840s per URL. A search that ran into an unsolvable protection
challenge therefore outlived every reverse proxy in front of it, and the user was shown
"Server unavailable (504)" - a gateway timeout that names their proxy rather than the
challenge that actually failed.
"""

import threading

import pytest

from shelfmark.core import search_deadline


@pytest.fixture(autouse=True)
def _no_ambient_deadline():
    """Each test starts outside any budget."""
    token = search_deadline._current.set(None)
    yield
    search_deadline._current.reset(token)


# --------------------------------------------------------------------------- #
# The budget itself
# --------------------------------------------------------------------------- #
def test_no_budget_outside_a_search():
    """A queued download must not inherit a search's budget."""
    assert search_deadline.current() is None
    assert search_deadline.expired() is False
    assert search_deadline.cancel_event() is None


def test_budget_applies_inside_the_context_and_not_after():
    with search_deadline.search_deadline(60) as deadline:
        assert search_deadline.current() is deadline
        assert search_deadline.expired() is False
    assert search_deadline.current() is None


def test_expiry_trips_the_cancel_event():
    """The Event is the mechanism: the bypassers poll it and know nothing of deadlines."""
    with search_deadline.search_deadline(0.05):
        event = search_deadline.cancel_event()
        assert isinstance(event, threading.Event)
        assert event.wait(timeout=5) is True
        assert search_deadline.expired() is True


def test_timer_is_cancelled_on_exit():
    """A finished search must not leave a timer running to fire later."""
    with search_deadline.search_deadline(3600) as deadline:
        pass
    assert deadline._timer.finished.is_set()


def test_message_names_the_challenge_not_the_proxy():
    with search_deadline.search_deadline(120):
        message = search_deadline.deadline_message()
    assert "120s" in message
    assert "protection challenge" in message
    assert "504" not in message


# --------------------------------------------------------------------------- #
# Reading the setting
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        (600, 600.0),
        ("450", 450.0),
        (None, search_deadline.DEFAULT_SEARCH_BUDGET_SECONDS),
        ("nonsense", search_deadline.DEFAULT_SEARCH_BUDGET_SECONDS),
        (0, search_deadline.DEFAULT_SEARCH_BUDGET_SECONDS),
        (True, search_deadline.DEFAULT_SEARCH_BUDGET_SECONDS),
        (5, 30.0),  # clamped up: below this nothing can finish
        (99999, 1800.0),  # clamped down
    ],
)
def test_budget_seconds_coerces_and_clamps(monkeypatch, configured, expected):
    from shelfmark.core.config import config as app_config

    monkeypatch.setattr(
        app_config,
        "get",
        lambda key, default=None: configured if key == "RELEASE_SEARCH_TIMEOUT" else default,
    )

    assert search_deadline.budget_seconds() == expected


# --------------------------------------------------------------------------- #
# What the search path does with it
# --------------------------------------------------------------------------- #
def test_html_get_page_will_not_start_a_bypass_on_a_spent_budget(monkeypatch):
    """A minutes-long solve nobody is still waiting for is worse than a clear failure."""
    import shelfmark.download.http as http

    started: list[str] = []
    monkeypatch.setattr(
        http, "get_bypassed_page", lambda *a, **k: started.append(a[0]) or "<html/>"
    )
    monkeypatch.setattr(http, "_is_cf_bypass_enabled", lambda: True)
    monkeypatch.setattr(http, "_bypass_grace_seconds", lambda: 1.0)
    monkeypatch.setattr(http.network, "should_rotate_dns_for_url", lambda _url: True)
    monkeypatch.setattr(http, "get_proxies", lambda _url: {})
    monkeypatch.setattr(http.time, "sleep", lambda _s: None)

    class _Selector:
        current_base = "https://annas-archive.gl"
        attempts_this_dns = 0
        last_failure = None

        def rewrite(self, url):
            return url

    with search_deadline.search_deadline(60) as deadline:
        deadline.event.set()
        result = http.html_get_page(
            "https://annas-archive.gl/search",
            retry=1,
            selector=_Selector(),
            use_bypasser=True,
            success_delay=0,
        )

    assert result == ""
    assert started == [], "no solve should have been started"


def test_search_budget_becomes_the_cancel_flag(monkeypatch):
    """This is what makes the budget bite on a solve already running."""
    import shelfmark.download.http as http

    seen: list[object] = []

    def fake_bypass(_url, _selector=None, cancel_flag=None):
        seen.append(cancel_flag)
        return "<html>solved</html>"

    monkeypatch.setattr(http, "get_bypassed_page", fake_bypass)
    monkeypatch.setattr(http, "_is_cf_bypass_enabled", lambda: True)
    monkeypatch.setattr(http, "_bypass_grace_seconds", lambda: 1.0)
    monkeypatch.setattr(http.network, "should_rotate_dns_for_url", lambda _url: True)
    monkeypatch.setattr(http, "get_proxies", lambda _url: {})
    monkeypatch.setattr(http.time, "sleep", lambda _s: None)

    class _Selector:
        current_base = "https://annas-archive.gl"
        attempts_this_dns = 0
        last_failure = None

        def rewrite(self, url):
            return url

    with search_deadline.search_deadline(60) as deadline:
        http.html_get_page(
            "https://annas-archive.gl/search",
            retry=1,
            selector=_Selector(),
            use_bypasser=True,
            success_delay=0,
        )

    assert seen == [deadline.event]


def test_a_callers_own_cancel_flag_is_not_replaced(monkeypatch):
    """A queued download brings its own and must keep it."""
    import shelfmark.download.http as http

    seen: list[object] = []

    def fake_bypass(_url, _selector=None, cancel_flag=None):
        seen.append(cancel_flag)
        return "<html>solved</html>"

    monkeypatch.setattr(http, "get_bypassed_page", fake_bypass)
    monkeypatch.setattr(http, "_is_cf_bypass_enabled", lambda: True)
    monkeypatch.setattr(http, "_bypass_grace_seconds", lambda: 1.0)
    monkeypatch.setattr(http.network, "should_rotate_dns_for_url", lambda _url: True)
    monkeypatch.setattr(http, "get_proxies", lambda _url: {})
    monkeypatch.setattr(http.time, "sleep", lambda _s: None)

    class _Selector:
        current_base = "https://annas-archive.gl"
        attempts_this_dns = 0
        last_failure = None

        def rewrite(self, url):
            return url

    own_flag = threading.Event()
    with search_deadline.search_deadline(60):
        http.html_get_page(
            "https://annas-archive.gl/search",
            retry=1,
            selector=_Selector(),
            cancel_flag=own_flag,
            use_bypasser=True,
            success_delay=0,
        )

    assert seen == [own_flag]


def test_expired_budget_reports_the_challenge_not_a_cancellation(monkeypatch):
    """The budget trips the same flag a user's cancel does; the messages must differ."""
    import shelfmark.download.http as http
    from shelfmark.bypass import BypassCancelledError

    def fake_bypass(*_a, **_k):
        msg = "Bypass cancelled"
        raise BypassCancelledError(msg)

    monkeypatch.setattr(http, "get_bypassed_page", fake_bypass)
    monkeypatch.setattr(http, "_is_cf_bypass_enabled", lambda: True)
    monkeypatch.setattr(http, "_bypass_grace_seconds", lambda: 1.0)
    monkeypatch.setattr(http.network, "should_rotate_dns_for_url", lambda _url: True)
    monkeypatch.setattr(http, "get_proxies", lambda _url: {})
    monkeypatch.setattr(http.time, "sleep", lambda _s: None)

    failures: list[str] = []

    class _Selector:
        current_base = "https://annas-archive.gl"
        attempts_this_dns = 0

        def rewrite(self, url):
            return url

        @property
        def last_failure(self):
            return None

        @last_failure.setter
        def last_failure(self, value):
            if value:
                failures.append(value)

    with search_deadline.search_deadline(60) as deadline:
        # Expire mid-solve: the flag is set, but the caller reaches the handler by way of
        # BypassCancelledError, which on its own reads as "someone cancelled this".
        deadline.expires_at = 0.0
        http.html_get_page(
            "https://annas-archive.gl/search",
            retry=1,
            selector=_Selector(),
            use_bypasser=True,
            success_delay=0,
        )

    assert failures, "a give-up reason should have been recorded"
    assert "ran out of time" in failures[-1]
    assert "cancelled" not in failures[-1]


# --------------------------------------------------------------------------- #
# Slicing the budget between providers (issue #1531)
# --------------------------------------------------------------------------- #
def test_unit_deadline_is_a_no_op_outside_a_search():
    with search_deadline.unit_deadline(waiting=2) as unit:
        assert unit is None
        assert search_deadline.current() is None


def test_a_lone_unit_gets_the_whole_budget():
    with search_deadline.search_deadline(300) as parent, search_deadline.unit_deadline() as unit:
        assert unit is not None
        assert unit.budget_seconds == pytest.approx(parent.remaining, abs=1)
        assert unit.budget_seconds > 299


def test_waiting_units_each_keep_a_reserve():
    with search_deadline.search_deadline(300), search_deadline.unit_deadline(waiting=2) as unit:
        assert unit is not None
        assert unit.budget_seconds == pytest.approx(240, abs=1)


def test_a_unit_is_never_given_less_than_the_reserve_while_the_parent_has_it():
    with search_deadline.search_deadline(60), search_deadline.unit_deadline(waiting=5) as unit:
        assert unit is not None
        assert unit.budget_seconds == pytest.approx(30, abs=1)


def test_a_unit_never_outlives_its_parent():
    with (
        search_deadline.search_deadline(10) as parent,
        search_deadline.unit_deadline(waiting=3) as unit,
    ):
        assert unit is not None
        assert unit.expires_at <= parent.expires_at + 0.01


def test_the_parent_is_back_in_force_after_a_unit():
    with search_deadline.search_deadline(300) as parent:
        with search_deadline.unit_deadline(waiting=1):
            pass
        assert search_deadline.current() is parent


def test_a_stuck_unit_stops_at_its_slice_and_the_next_still_runs(monkeypatch):
    """The unit's own limit trips while the parent still has the reserve left."""
    monkeypatch.setattr(search_deadline, "DEFAULT_UNIT_RESERVE_SECONDS", 0.2)
    with search_deadline.search_deadline(1.0):
        with search_deadline.unit_deadline(waiting=1, reserve=0.5) as first:
            assert first is not None
            assert first.event.wait(timeout=5) is True
            assert search_deadline.expired() is True
        assert search_deadline.expired() is False
        with search_deadline.unit_deadline(waiting=0):
            assert search_deadline.expired() is False
