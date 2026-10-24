"""Prowlarr release source plugin.

This plugin integrates with Prowlarr to search for book releases
across multiple indexers (torrent and usenet).

Includes:
- ProwlarrSource: Search integration with Prowlarr
- ProwlarrHandler: Download handling via external clients
"""

from importlib import import_module

# Import submodules to trigger decorator registration
from shelfmark.release_sources.prowlarr import (
    handler as handler,
)
from shelfmark.release_sources.prowlarr import (
    settings as settings,
)
from shelfmark.release_sources.prowlarr import (
    source as source,
)

# Import shared download clients/settings to trigger registration.
# This is in a try/except to handle optional dependencies gracefully.
try:
    import_module("shelfmark.download.clients")
    import_module("shelfmark.download.clients.settings")
except ImportError as e:
    from shelfmark.core.logger import setup_logger

    setup_logger(__name__).debug("Download clients not loaded: %s", e)
