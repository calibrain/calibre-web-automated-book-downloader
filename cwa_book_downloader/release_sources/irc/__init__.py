"""IRC Highway release source plugin.

This plugin enables searching and downloading ebooks from IRC Highway's
#ebooks channel using the DCC (Direct Client-to-Client) protocol.

Special thanks to the OpenBooks project (https://github.com/evan-buss/openbooks),
an MIT-licensed IRC ebook downloader written in Go, for serving as inspiration for the   
high-level design and reference for IRC Highway best practices, DCC protocol handling, and result parsing.
"""

# Import to trigger decorator registration
from cwa_book_downloader.release_sources.irc import source  # noqa: F401
from cwa_book_downloader.release_sources.irc import handler  # noqa: F401
from cwa_book_downloader.release_sources.irc import settings  # noqa: F401
