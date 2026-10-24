"""Tests for Shelfmark's logging configuration."""

import io
import logging
from logging.handlers import RotatingFileHandler

import pytest

import shelfmark.core.logger as logger_module


def test_setup_logger_shares_file_handler(monkeypatch, tmp_path):
    """Module loggers must not open the shared log file independently."""
    monkeypatch.setattr(logger_module, "ENABLE_LOGGING", True)
    log_file = tmp_path / "shelfmark.log"

    first_logger = logger_module.setup_logger("shelfmark.test.first", log_file)
    second_logger = logger_module.setup_logger("shelfmark.test.second", log_file)

    first_handler = next(
        handler for handler in first_logger.handlers if isinstance(handler, RotatingFileHandler)
    )
    second_handler = next(
        handler for handler in second_logger.handlers if isinstance(handler, RotatingFileHandler)
    )

    assert first_handler is second_handler
    assert first_handler.stream is not None
    assert second_handler.stream is not None
    assert first_handler.stream.fileno() == second_handler.stream.fileno()

    first_logger.removeHandler(first_handler)
    second_logger.removeHandler(second_handler)
    with logger_module._file_handlers_lock:
        logger_module._file_handlers.pop(log_file)
    first_handler.close()


def test_setup_logger_keeps_separate_handlers_for_separate_files(monkeypatch, tmp_path):
    """Explicitly different log destinations must remain independent."""
    monkeypatch.setattr(logger_module, "ENABLE_LOGGING", True)
    first_file = tmp_path / "first.log"
    second_file = tmp_path / "second.log"

    first_logger = logger_module.setup_logger("shelfmark.test.first_file", first_file)
    second_logger = logger_module.setup_logger("shelfmark.test.second_file", second_file)

    first_handler = next(
        handler for handler in first_logger.handlers if isinstance(handler, RotatingFileHandler)
    )
    second_handler = next(
        handler for handler in second_logger.handlers if isinstance(handler, RotatingFileHandler)
    )

    assert first_handler is not second_handler

    for logger, handler, log_file in (
        (first_logger, first_handler, first_file),
        (second_logger, second_handler, second_file),
    ):
        logger.removeHandler(handler)
        with logger_module._file_handlers_lock:
            logger_module._file_handlers.pop(log_file)
        handler.close()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "Task 12:https://tracker.example/api?t=get&id=42&apikey=s3cr3t: starting download",
            "Task 12:https://tracker.example/api?t=get&id=42&apikey=(removed): starting download",
        ),
        (
            "https://t.example/dl.php?passkey=abc&torrent_pass=def&API_KEY=ghi&r=1",
            "https://t.example/dl.php?passkey=(removed)&torrent_pass=(removed)"
            "&API_KEY=(removed)&r=1",
        ),
        (
            "url='http://prowlarr:9696/1/download?apikey=xyz&link=abc'",
            "url='http://prowlarr:9696/1/download?apikey=(removed)&link=abc'",
        ),
        (
            "https://t.example/rss?id=7&uid=99&key=k",
            "https://t.example/rss?id=7&uid=(removed)&key=(removed)",
        ),
        ("download_id=7 api_key_set=True", "download_id=7 api_key_set=True"),
    ],
)
def test_redact_secrets_masks_credential_query_params(text, expected):
    assert logger_module.redact_secrets(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "proxy socks5://alice:p4ss@proxy.example:1080 unreachable",
            "proxy socks5://(removed)@proxy.example:1080 unreachable",
        ),
        (
            "GET http://admin:secret@qbittorrent:8080/api/v2/torrents/info failed",
            "GET http://(removed)@qbittorrent:8080/api/v2/torrents/info failed",
        ),
        (
            "fetching https://tracker.example/rss/0123456789abcdef0123456789abcdef",
            "fetching https://tracker.example/rss/(removed)",
        ),
        (
            "https://tracker.example/passkey/AbCdEfGh01234567/get.torrent",
            "https://tracker.example/passkey/(removed)/get.torrent",
        ),
        (
            "POST https://api.telegram.org/bot123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw/sendMessage",
            "POST https://api.telegram.org/bot(removed)/sendMessage",
        ),
        (
            "https://discord.com/api/webhooks/123456789012345678/abcDEF_ghi-JKLmno0123456789",
            "https://discord.com/api/webhooks/123456789012345678/(removed)",
        ),
        (
            "https://hooks.slack.com/services/T0ABC123/B0DEF456/XyZ0123456789abcdefABCDEF",
            "https://hooks.slack.com/services/T0ABC123/B0DEF456/(removed)",
        ),
        # Ids in Shelfmark's own routes and short segments stay readable.
        (
            "POST /api/download/0123456789abcdef0123456789abcdef/cancel",
            "POST /api/download/0123456789abcdef0123456789abcdef/cancel",
        ),
        ("https://tracker.example/rss/latest", "https://tracker.example/rss/latest"),
        ("contact me at a@b.example, ok", "contact me at a@b.example, ok"),
        # The authority ends at "?" or "#", so an address after one is not userinfo.
        (
            "https://tracker.example?contact=ops@tracker.example",
            "https://tracker.example?contact=ops@tracker.example",
        ),
        (
            "https://tracker.example#reach-us@tracker.example",
            "https://tracker.example#reach-us@tracker.example",
        ),
    ],
)
def test_redact_secrets_masks_userinfo_and_path_segment_credentials(text, expected):
    assert logger_module.redact_secrets(text) == expected


@pytest.mark.parametrize(
    "module_name",
    ["shelfmark.api.websocket", "shelfmark.download.clients"],
)
def test_modules_log_through_the_redacting_formatter(module_name):
    """A module on a plain logging.getLogger bypassed redaction (S1)."""
    import importlib

    module = importlib.import_module(module_name)
    module_logger = getattr(module, "logger", None) or module._logger

    assert module_logger.handlers
    assert all(
        isinstance(handler.formatter, logger_module.RedactingFormatter)
        for handler in module_logger.handlers
    )


def test_setup_logger_redacts_messages_args_and_tracebacks(monkeypatch):
    """Task ids built from Prowlarr guids carry tracker API keys (#1486)."""
    monkeypatch.setattr(logger_module, "ENABLE_LOGGING", False)
    logger = logger_module.setup_logger("shelfmark.test.redaction")
    stream = io.StringIO()
    for handler in logger.handlers:
        assert isinstance(handler, logging.StreamHandler)
        handler.setStream(stream)

    task_id = "3:https://tracker.example/api?t=get&id=42&apikey=s3cr3t"
    logger.info("Task %s: starting download (%s) - %s", task_id, "Prowlarr", "Title")
    try:
        raise RuntimeError(f"refresh failed for {task_id}")
    except RuntimeError:
        logger.exception("Prowlarr release refresh failed: %s", task_id)

    output = stream.getvalue()
    assert "s3cr3t" not in output
    assert output.count("apikey=(removed)") == 3
    assert "id=42" in output
