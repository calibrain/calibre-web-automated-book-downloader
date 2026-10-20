"""
slskd release source plugin.

Searches the Soulseek network through a slskd instance and downloads the chosen
files with it. Unlike torrent/usenet sources, search and download are the same
service: a result is a file on a specific peer, and only slskd can fetch it.

Includes:
- SlskdSource: Search integration
- SlskdHandler: Download handling via slskd transfers
"""

# Import submodules to trigger decorator registration
from shelfmark.release_sources.slskd import (
    handler as handler,
)
from shelfmark.release_sources.slskd import (
    settings as settings,
)
from shelfmark.release_sources.slskd import (
    source as source,
)
