"""Tests for Shelfmark's logging configuration."""

from logging.handlers import RotatingFileHandler

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
