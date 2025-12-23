"""
Prowlarr release source plugin.

This plugin integrates with Prowlarr to search for book releases
across multiple indexers (torrent and usenet).

Includes:
- ProwlarrSource: Search integration with Prowlarr
- ProwlarrHandler: Download handling via external clients
- Download clients: qBittorrent (torrents), NZBGet (usenet)
"""

# Import submodules to trigger decorator registration
from cwa_book_downloader.release_sources.prowlarr import source  # noqa: F401
from cwa_book_downloader.release_sources.prowlarr import handler  # noqa: F401
from cwa_book_downloader.release_sources.prowlarr import settings  # noqa: F401

# Import clients to trigger client registration
# This is in a try/except to handle optional dependencies gracefully
try:
    from cwa_book_downloader.release_sources.prowlarr import clients  # noqa: F401
except ImportError as e:
    # Log but don't fail - clients require optional dependencies
    import logging

    logging.getLogger(__name__).debug(f"Prowlarr clients not loaded: {e}")
