"""Test doubles shared by the core tests."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import logging

    import pytest


class FakeConfig:
    """Stands in for ``shelfmark.core.config.config``, answering from ``values`` only.

    Unlike the real singleton it never reads the environment or saved settings, so a
    stray ``export`` or a leftover config file can't change what a test sees.
    """

    def __init__(self, **values: Any) -> None:
        self.values: dict[str, Any] = dict(values)

    def get(self, key: str, default: Any = None, user_id: int | None = None) -> Any:
        return self.values.get(key, default)


def capture_log(monkeypatch: pytest.MonkeyPatch, logger: logging.Logger, level: str) -> list[str]:
    """Collect what ``logger`` emits at ``level`` ("info", "warning", ...), formatted.

    pytest's ``caplog`` can't see these: ``setup_logger`` loggers don't propagate to
    the root handler it listens on.
    """
    messages: list[str] = []
    monkeypatch.setattr(logger, level, lambda msg, *args, **_: messages.append(msg % args))
    return messages
