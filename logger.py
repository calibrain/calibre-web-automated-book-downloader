"""Centralized logging configuration for the book downloader application."""

import logging
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler
from env import FLASK_DEBUG, LOG_FILE, ENABLE_LOGGING
from typing import Any, Optional

class CustomLogger(logging.Logger):
    """Custom logger class with additional error_trace method."""
    
    def error_trace(self, msg: Any, *args: Any, **kwargs: Any) -> None:
        """Log an error message with full stack trace."""
        self.error(msg, *args, exc_info=True, **kwargs)

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
    if FLASK_DEBUG:
        logger.setLevel(logging.DEBUG)
    else:
        logger.setLevel(logging.INFO)
    
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s'
    )

    # Console handler for Docker output
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    if FLASK_DEBUG:
        console_handler.setLevel(logging.DEBUG)
    else:
        console_handler.setLevel(logging.INFO)
    console_handler.addFilter(lambda record: record.levelno < logging.ERROR)  # Only allow logs below ERROR
    logger.addHandler(console_handler)
    
    # Error handler for stderr
    error_handler = logging.StreamHandler(sys.stderr)
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(formatter)
    logger.addHandler(error_handler)
    
    # File handler if log file is specified
    try:
        if ENABLE_LOGGING:
            file_handler = RotatingFileHandler(
                log_file,
                maxBytes=10485760,  # 10MB
                backupCount=5
            )
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
    except Exception as e:
        logger.error_trace(f"Failed to create log file: {e}", exc_info=True)

    return logger