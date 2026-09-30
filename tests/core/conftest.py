"""Fixtures shared by the core tests."""

from __future__ import annotations

import pytest

from tests.core.fakes import FakeConfig


@pytest.fixture
def fake_app_config(monkeypatch: pytest.MonkeyPatch) -> FakeConfig:
    """Answer every config read from a dict the test controls.

    Patches ``get`` on the config singleton, which each module imports as
    ``app_config``, so one patch covers all of them. The library-index cache is
    emptied too, since entries cached under one test's config would leak into the
    next. Turn it on for a whole module with
    ``pytestmark = pytest.mark.usefixtures("fake_app_config")``.
    """
    from shelfmark.core import library_index
    from shelfmark.core.config import config

    fake = FakeConfig()
    monkeypatch.setattr(config, "get", fake.get)
    monkeypatch.setattr(library_index, "_cache", {})
    return fake
