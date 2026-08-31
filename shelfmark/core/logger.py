"""Logging configuration and custom logger with error tracing."""

import logging
import sys
from collections.abc import Mapping
from logging.handlers import RotatingFileHandler
from threading import Lock
from typing import TYPE_CHECKING

from shelfmark.config.env import ENABLE_LOGGING, LOG_FILE, LOG_LEVEL

if TYPE_CHECKING:
    from pathlib import Path


_file_handlers: dict[Path, RotatingFileHandler] = {}
_file_handlers_lock = Lock()


class CustomLogger(logging.Logger):
    """Custom logger class with additional error_trace method."""

    def error_trace(self, msg: object, *args: object, **kwargs: object) -> None:
        """Log an error message with full stack trace."""
        self.log_resource_usage()
        stack_info, stacklevel, extra = _extract_log_kwargs(kwargs)
        self.error(
            msg,
            *args,
            exc_info=True,
            stack_info=stack_info,
            stacklevel=stacklevel,
            extra=extra,
        )

    def debug_trace(self, msg: object, *args: object, **kwargs: object) -> None:
        """Log a debug message (stack trace only if exception active)."""
        stack_info, stacklevel, extra = _extract_log_kwargs(kwargs)
        # Only include exc_info if there's actually an exception
        has_exception = sys.exc_info()[0] is not None
        self.debug(
            msg,
            *args,
            exc_info=has_exception,
            stack_info=stack_info,
            stacklevel=stacklevel,
            extra=extra,
        )

    def log_resource_usage(self) -> None:
        """Log best-effort CPU and memory usage for the current container."""
        try:
            import psutil
        except ImportError:
            return

        # Best-effort only; this should never raise during exception logging.
        try:

            def _get_process_rss_mb(proc: object) -> float | None:
                try:
                    proc_info = getattr(proc, "info", None)
                    if not isinstance(proc_info, Mapping):
                        return None
                    mem = proc_info.get("memory_info")
                    rss = getattr(mem, "rss", None)
                    if isinstance(rss, int | float):
                        return rss / (1024 * 1024)
                except (
                    psutil.NoSuchProcess,
                    psutil.AccessDenied,
                    KeyError,
                    AttributeError,
                ):
                    return None
                return None

            # Sum RSS of all processes for actual app memory (container-friendly),
            # but fall back gracefully on platforms that restrict process enumeration.
            app_memory_mb = 0.0
            try:
                for proc in psutil.process_iter(["memory_info"]):
                    proc_rss_mb = _get_process_rss_mb(proc)
                    if proc_rss_mb is not None:
                        app_memory_mb += proc_rss_mb
            except PermissionError, psutil.AccessDenied, OSError:
                try:
                    app_memory_mb = psutil.Process().memory_info().rss / (1024 * 1024)
                except AttributeError, OSError, psutil.Error:
                    app_memory_mb = 0.0

            memory = psutil.virtual_memory()
            system_used_mb = memory.used / (1024 * 1024)
            available_mb = memory.available / (1024 * 1024)
            cpu_percent = psutil.cpu_percent()
            self.debug(
                f"Container Memory: App={app_memory_mb:.2f} MB, System={system_used_mb:.2f} MB, "
                f"Available={available_mb:.2f} MB, CPU: {cpu_percent:.2f}%"
            )
        except AttributeError, OSError, psutil.Error:
            # Avoid breaking the original log call if psutil is missing or restricted.
            return


def _extract_log_kwargs(
    kwargs: Mapping[str, object],
) -> tuple[bool, int, Mapping[str, object] | None]:
    stack_info = kwargs.get("stack_info")
    normalized_stack_info = stack_info if isinstance(stack_info, bool) else False

    stacklevel = kwargs.get("stacklevel")
    normalized_stacklevel = stacklevel if isinstance(stacklevel, int) else 1

    extra = kwargs.get("extra")
    normalized_extra = _normalize_log_extra(extra)

    return normalized_stack_info, normalized_stacklevel, normalized_extra


def _normalize_log_extra(value: object) -> Mapping[str, object] | None:
    if not isinstance(value, Mapping):
        return None

    if all(isinstance(key, str) for key in value):
        return value

    return None


def _get_file_handler(log_file: Path, formatter: logging.Formatter) -> RotatingFileHandler:
    """Return the process-wide rotating handler for a log file."""
    with _file_handlers_lock:
        handler = _file_handlers.get(log_file)
        if handler is None:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(
                log_file,
                maxBytes=10485760,  # 10MB
                backupCount=5,
            )
            handler.setFormatter(formatter)
            _file_handlers[log_file] = handler
        return handler


def setup_logger(name: str, log_file: Path = LOG_FILE) -> CustomLogger:
    """Set up and configure a logger instance.

    Args:
        name: The name of the logger instance
        log_file: Optional path to log file. If None, logs only to stdout/stderr

    Returns:
        CustomLogger: Configured logger instance with error_trace method

    """
    # Register our custom logger class
    logging.setLoggerClass(CustomLogger)

    # Create logger as CustomLogger instance
    logger = CustomLogger(name)
    log_level = getattr(logging, LOG_LEVEL, logging.INFO)
    logger.setLevel(log_level)

    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s"
    )

    # Console handler for Docker output
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(log_level)
    console_handler.addFilter(
        lambda record: record.levelno < logging.ERROR
    )  # Only allow logs below ERROR to stdout
    logger.addHandler(console_handler)

    # Error handler for stderr
    error_handler = logging.StreamHandler(sys.stderr)
    error_handler.setLevel(logging.ERROR)  # Error and above go to stderr
    error_handler.setFormatter(formatter)
    logger.addHandler(error_handler)

    # File handler if log file is specified
    try:
        if ENABLE_LOGGING:
            logger.addHandler(_get_file_handler(log_file, formatter))
    except (OSError, TypeError, ValueError) as e:
        logger.error_trace(f"Failed to create log file: {e}", exc_info=True)

    return logger
