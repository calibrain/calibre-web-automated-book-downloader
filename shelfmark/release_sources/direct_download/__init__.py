"""Direct Download release source and public entry points.

Importing the source and handler classes registers them with Shelfmark.
"""

from shelfmark.release_sources.direct_download.annas_archive import search_books
from shelfmark.release_sources.direct_download.common import DirectDownloadUnavailableError
from shelfmark.release_sources.direct_download.handler import DirectDownloadHandler
from shelfmark.release_sources.direct_download.source import DirectDownloadSource

__all__ = [
    "DirectDownloadUnavailableError",
    "DirectDownloadHandler",
    "DirectDownloadSource",
    "SearchUnavailableError",
    "search_books",
]

# Compatibility alias for integrations that imported the old module-level name.
SearchUnavailableError = DirectDownloadUnavailableError
