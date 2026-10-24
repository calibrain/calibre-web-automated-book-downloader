"""
Newznab release source plugin.

Integrates with any Newznab-compatible indexer or aggregator (e.g. NZBHydra2,
NZBGeek, Drunkenslug) to search for book releases via the standard Newznab API.

Includes:
- NewznabSource: Search integration
- NewznabHandler: Download handling via configured usenet/torrent client
"""

from importlib import import_module

# Import submodules to trigger decorator registration
from shelfmark.release_sources.newznab import (
    handler as handler,
)
from shelfmark.release_sources.newznab import (
    settings as settings,
)
from shelfmark.release_sources.newznab import (
    source as source,
)

# Import shared download clients/settings to trigger registration.
try:
    import_module("shelfmark.download.clients")
    import_module("shelfmark.download.clients.settings")
except ImportError as e:
    from shelfmark.core.logger import setup_logger

    setup_logger(__name__).debug("Download clients not loaded: %s", e)
