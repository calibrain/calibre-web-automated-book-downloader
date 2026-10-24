"""Jackett release source plugin.

This plugin integrates with Jackett to search for book releases across its
configured torrent indexers via Jackett's Torznab endpoints.

Includes:
- JackettSource: Search integration with Jackett
- JackettHandler: Download handling via external clients
"""

from importlib import import_module

# Import submodules to trigger decorator registration
from shelfmark.release_sources.jackett import (
    handler as handler,
)
from shelfmark.release_sources.jackett import (
    settings as settings,
)
from shelfmark.release_sources.jackett import (
    source as source,
)

# Import shared download clients/settings to trigger registration.
try:
    import_module("shelfmark.download.clients")
    import_module("shelfmark.download.clients.settings")
except ImportError as e:
    from shelfmark.core.logger import setup_logger

    setup_logger(__name__).debug("Download clients not loaded: %s", e)
