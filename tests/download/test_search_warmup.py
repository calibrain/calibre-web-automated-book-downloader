"""Boot-time search warm-up.

The warm-up exists to move the cold DDoS-Guard solve off the user's first search. It
is an optimisation, so the load-bearing property is that it can never affect startup:
a source that is down, misconfigured or raising must leave the app running.
"""

import pytest


@pytest.fixture
def warmup(monkeypatch):
    import shelfmark.download.warmup as warmup_module

    monkeypatch.setattr(warmup_module, "_warmup_thread", None)
    return warmup_module


def _patch_config(monkeypatch, warmup, values: dict):
    def fake_get(key, default=None):
        return values.get(key, default)

    monkeypatch.setattr(warmup.config, "get", fake_get)


def test_disabled_by_setting(monkeypatch, warmup):
    _patch_config(monkeypatch, warmup, {"SEARCH_WARMUP_ENABLED": False})
    assert warmup.is_enabled() is False
    assert warmup.start() is False


def test_disabled_by_string_false(monkeypatch, warmup):
    """Deployment ENV arrives as a string, not a bool."""
    _patch_config(monkeypatch, warmup, {"SEARCH_WARMUP_ENABLED": "false"})
    assert warmup.is_enabled() is False


def test_skipped_when_direct_download_is_off(monkeypatch, warmup):
    _patch_config(monkeypatch, warmup, {"DIRECT_DOWNLOAD_ENABLED": False})
    assert warmup.is_enabled() is False


def test_enabled_by_default(monkeypatch, warmup):
    _patch_config(monkeypatch, warmup, {})
    assert warmup.is_enabled() is True


def test_query_defaults_and_is_configurable(monkeypatch, warmup):
    _patch_config(monkeypatch, warmup, {})
    assert warmup.warmup_query() == "The Great Gatsby"

    _patch_config(monkeypatch, warmup, {"SEARCH_WARMUP_QUERY": "Dune"})
    assert warmup.warmup_query() == "Dune"

    # A blank override must not send an empty query at the source.
    _patch_config(monkeypatch, warmup, {"SEARCH_WARMUP_QUERY": "   "})
    assert warmup.warmup_query() == "The Great Gatsby"


def test_skipped_when_no_mirrors_configured(monkeypatch, warmup):
    _patch_config(monkeypatch, warmup, {})
    import shelfmark.core.mirrors as mirrors

    monkeypatch.setattr(mirrors, "has_aa_mirror_configuration", lambda: False)

    called: list[str] = []
    import shelfmark.release_sources.direct_download as dd

    monkeypatch.setattr(dd, "search_books", lambda q, f: called.append(q))

    assert warmup.run_warmup() is False
    assert called == []


def test_successful_warmup_reports_true(monkeypatch, warmup):
    _patch_config(monkeypatch, warmup, {})
    import shelfmark.core.mirrors as mirrors
    import shelfmark.release_sources.direct_download as dd

    monkeypatch.setattr(mirrors, "has_aa_mirror_configuration", lambda: True)
    seen: list[str] = []

    def fake_search(query, _filters):
        seen.append(query)
        return ["a", "b"], None, 2

    monkeypatch.setattr(dd, "search_books", fake_search)

    assert warmup.run_warmup() is True
    assert seen == ["The Great Gatsby"]


def test_empty_results_are_not_an_error(monkeypatch, warmup):
    _patch_config(monkeypatch, warmup, {})
    import shelfmark.core.mirrors as mirrors
    import shelfmark.release_sources.direct_download as dd

    monkeypatch.setattr(mirrors, "has_aa_mirror_configuration", lambda: True)
    monkeypatch.setattr(dd, "search_books", lambda q, f: ([], None, 0))

    assert warmup.run_warmup() is False


def test_search_failure_is_swallowed(monkeypatch, warmup):
    """A source that is down at boot must not propagate out of the warm-up."""
    _patch_config(monkeypatch, warmup, {})
    import shelfmark.core.mirrors as mirrors
    import shelfmark.release_sources.direct_download as dd

    monkeypatch.setattr(mirrors, "has_aa_mirror_configuration", lambda: True)

    def boom(_query, _filters):
        msg = "mirrors are blocked"
        raise RuntimeError(msg)

    monkeypatch.setattr(dd, "search_books", boom)

    assert warmup.run_warmup() is False


def test_start_schedules_a_daemon_thread_and_is_idempotent(monkeypatch, warmup):
    _patch_config(monkeypatch, warmup, {})

    assert warmup.start(delay_seconds=30) is True
    thread = warmup._warmup_thread
    assert thread is not None
    assert thread.daemon is True

    # A second call must not stack up another timer.
    assert warmup.start(delay_seconds=30) is False
    assert warmup._warmup_thread is thread

    thread.cancel()


def test_start_does_not_run_the_search_inline(monkeypatch, warmup):
    """Startup must not block on a search that can take a minute."""
    _patch_config(monkeypatch, warmup, {})
    ran: list[bool] = []
    monkeypatch.setattr(warmup, "run_warmup", lambda: ran.append(True))

    warmup.start(delay_seconds=30)
    assert ran == []

    if warmup._warmup_thread:
        warmup._warmup_thread.cancel()


def test_env_var_can_disable_the_warmup(monkeypatch, warmup):
    """SEARCH_WARMUP_ENABLED is not in the settings registry, so config.get never
    sees it - the documented off-switch only works if os.environ is consulted."""
    _patch_config(monkeypatch, warmup, {})  # config knows nothing about the key
    monkeypatch.setenv("SEARCH_WARMUP_ENABLED", "false")

    assert warmup.is_enabled() is False
    assert warmup.start() is False


def test_env_var_can_set_the_query(monkeypatch, warmup):
    _patch_config(monkeypatch, warmup, {})
    monkeypatch.setenv("SEARCH_WARMUP_QUERY", "Moby Dick")

    assert warmup.warmup_query() == "Moby Dick"


def test_env_var_absent_falls_back_to_config(monkeypatch, warmup):
    monkeypatch.delenv("SEARCH_WARMUP_QUERY", raising=False)
    _patch_config(monkeypatch, warmup, {"SEARCH_WARMUP_QUERY": "From Config"})

    assert warmup.warmup_query() == "From Config"
