"""Core module - shared models, queue, and utilities."""

from cwa_book_downloader.core.models import BookInfo, QueueItem, SearchFilters, QueueStatus
from cwa_book_downloader.core.queue import BookQueue, book_queue
from cwa_book_downloader.core.logger import setup_logger
