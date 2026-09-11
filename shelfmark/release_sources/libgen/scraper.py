"""Libgen catalogue scraping: search results and download-link resolution.

This is the pure fetch+parse core of the Libgen source. It talks to the libgen.li
family of mirrors (``index.php?req=`` search, ``ads.php?md5=`` download pages) using
plain HTTP -- these mirrors are not behind DDoS-Guard, so no browser/bypasser is
needed. All shelfmark-stateful behaviour lives in source.py/handler.py.
"""

import re
from http import HTTPStatus
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup, Tag

from shelfmark.core.languages import normalize_language
from shelfmark.core.logger import setup_logger
from shelfmark.download import http as downloader
from shelfmark.download import network
from shelfmark.release_sources import BrowseRecord

logger = setup_logger(__name__)

# The libgen.li results table. Both full (9-cell) and compact (5-cell) rows live in it.
_RESULTS_TABLE_ID = "tablelibgen"

# md5 appears in the row's Mirrors cell as get.php?md5=<hash> and an AA /md5/<hash> link.
_MD5_RE = re.compile(r"md5=([0-9a-f]{32})", re.IGNORECASE)

# Patterns for the keyed GET link on an ads.php page. Kept in sync with the resolution
# libgen download has always used (direct_download._LIBGEN_GET_PATTERNS); duplicated here
# on purpose so the Libgen source stays self-contained and does not import that module's
# internals (which an in-flight upstream refactor is relocating).
_GET_KEY_PATTERNS = [
    re.compile(
        r'<a\s+href=["\']([^"\']*get\.php\?md5=[^"\']+&key=[^"\']+)["\'][^>]*>\s*'
        r"<h2[^>]*>GET</h2>\s*</a>",
        re.IGNORECASE,
    ),
    re.compile(
        r'<a[^>]+href=["\']([^"\']*get\.php\?md5=[^"\']+&(?:amp;)?key=[^"\']+)["\']',
        re.IGNORECASE,
    ),
    re.compile(
        r'<a\s+href=["\']([^"\']*get\.php[^"\']*)["\'][^>]*>[\s\S]*?<h2[^>]*>GET</h2>',
        re.IGNORECASE,
    ),
    re.compile(
        r'href=["\']([^"\']*get\.php\?[^"\']*md5=[^"\']*&[^"\']*key=[^"\']+)["\']',
        re.IGNORECASE,
    ),
]

# Labels that terminate a metadata value on an ads.php page, so e.g. "Year: 2003 ISBN: ..."
# stops Year at "ISBN:" rather than swallowing it. We only emit a subset (see _parse_ads_metadata).
_METADATA_STOP_LABELS = [
    "Title",
    "Series",
    "Author(s)",
    "Publisher",
    "Year",
    "Language",
    "Pages",
    "ISBN",
    "Edition",
    "Extension",
    "Size",
    "Time added",
    "ID",
    "Filename",
    "Description",
]


def fetch_page(url: str, timeout: tuple[int, int] = (5, 15)) -> str | None:
    """GET a libgen page, returning its text on HTTP 200 or None on any failure.

    Public (not underscore-prefixed) because handler.py fetches ads.php pages through it
    and tests patch it. Uses the app's proxy/SSL/DNS configuration so egress stays on
    whatever network the container is bound to (the VPN namespace, in the deployed stack).
    """
    try:
        response = requests.get(
            url,
            headers=downloader.DOWNLOAD_HEADERS,
            timeout=timeout,
            allow_redirects=True,
            proxies=network.get_proxies(url),
            verify=network.get_ssl_verify(url),
        )
    except requests.exceptions.RequestException as exc:
        logger.debug("Libgen fetch failed for %s: %s", url, exc)
        return None
    if response.status_code != HTTPStatus.OK:
        logger.debug("Libgen fetch %s returned %s", url, response.status_code)
        return None
    return response.text


def search_libgen(
    query: str,
    mirrors: list[str],
    *,
    max_results: int,
    timeout: tuple[int, int] = (5, 15),
) -> list[BrowseRecord]:
    """Search each mirror's catalogue until one answers with a results table.

    The first mirror that returns a parseable ``#tablelibgen`` wins -- including when that
    table is empty ([] is returned as final). Mirrors can lag independently, but falling
    through on every empty result would multiply latency under the shared search deadline,
    so an empty-but-well-formed answer is trusted rather than re-queried elsewhere.
    """
    for base in mirrors:
        url = f"{base.rstrip('/')}/index.php?req={quote(query)}&res={max_results}"
        html = fetch_page(url, timeout)
        if html is None:
            continue
        records = _parse_results(html, base)
        if records is not None:
            return records
    return []


def fetch_record_by_md5(
    md5: str,
    mirrors: list[str],
    *,
    timeout: tuple[int, int] = (5, 10),
) -> BrowseRecord | None:
    """Resolve a single record from its md5 by parsing an ads.php page's metadata.

    libgen's ``index.php?req=<md5>`` does not match on md5 (req= indexes title/author/
    description), so md5 -> record must go through the ads.php page instead.
    """
    for base in mirrors:
        html = fetch_page(f"{base.rstrip('/')}/ads.php?md5={md5}", timeout)
        if html is None:
            continue
        record = _parse_ads_metadata(html, md5, base)
        if record is not None:
            return record
    return None


def resolve_download_url(ads_html: str, base_url: str) -> str | None:
    """Extract the keyed get.php download URL from an ads.php page, or None."""
    if "get.php" not in ads_html:
        return None
    for pattern in _GET_KEY_PATTERNS:
        match = pattern.search(ads_html)
        if not match:
            continue
        url = match.group(1).replace("&amp;", "&").replace("&gt;", ">").replace("&lt;", "<")
        if not url.startswith("http"):
            url = f"{base_url.rstrip('/')}/{url.lstrip('/')}"
        return url
    return None


def _cell_text(cell: Tag) -> str:
    """Cell text with runs of whitespace (incl. &nbsp; / \\xa0) collapsed to single spaces."""
    return re.sub(r"\s+", " ", cell.get_text(" ", strip=True)).strip()


def _parse_results(html: str, base_url: str) -> list[BrowseRecord] | None:
    """Parse a libgen search page.

    Returns None when the page has no results table (a challenge/error page -> the caller
    tries the next mirror), or a list (possibly empty) when the table is present.

    Row shapes vary and carry NO rowspans: full rows have 9 cells
    ``[Title, Author, Publisher, Year, Language, Pages, Size, Ext, Mirrors]`` and compact
    rows (extra files under one edition) have 5 ``[Title, Pages, Size, Ext, Mirrors]``. The
    file-level columns are stable from the right, so index from the end: Mirrors[-1] (md5),
    Ext[-2], Size[-3]. Title is always [0]. Author/Language exist only on full rows.

    The two shapes are the only ones libgen.li is known to emit; an unexpected width just
    fails safe (author/language read as None) rather than mis-columning.
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", id=_RESULTS_TABLE_ID)
    if not isinstance(table, Tag):
        return None

    records: list[BrowseRecord] = []
    for row in table.find_all("tr")[1:]:  # skip the header row
        cells = row.find_all("td")
        if len(cells) < 5:
            continue
        # Scope the md5 to the Mirrors cell (last column): scanning the whole row could match
        # an md5-shaped string elsewhere (e.g. a cover-image URL) and misattribute it.
        md5_match = _MD5_RE.search(str(cells[-1]))
        if not md5_match:
            continue  # spacer/section rows carry no md5
        md5 = md5_match.group(1).lower()

        title = _cell_text(cells[0])
        fmt = _cell_text(cells[-2]).lower() or None
        size = _cell_text(cells[-3]) or None

        author = None
        language = None
        if len(cells) >= 9:  # full row: middle metadata columns are present
            author = _cell_text(cells[1]) or None
            language = normalize_language(_cell_text(cells[4]))

        records.append(
            BrowseRecord(
                id=md5,
                title=title,
                source="libgen",
                author=author,
                language=language,
                size=size,
                format=fmt,
                source_url=f"{base_url.rstrip('/')}/ads.php?md5={md5}",
            )
        )
    return records


def _parse_ads_metadata(html: str, md5: str, base_url: str) -> BrowseRecord | None:
    """Build a BrowseRecord from an ads.php page's labelled metadata.

    The page's metadata lives in a deeply nested table, so read it from the visible text by
    label rather than by cell position -- the labels (Title:, Series:, Author(s): ...) are
    stable even though the surrounding markup is not. Returns None if the page has no title.
    """
    text = re.sub(r"\s+", " ", BeautifulSoup(html, "html.parser").get_text(" ", strip=True))

    def field(name: str) -> str | None:
        others = "|".join(
            re.escape(other) + r":" for other in _METADATA_STOP_LABELS if other != name
        )
        match = re.search(re.escape(name) + r":\s*(.*?)\s*(?:" + others + r"|$)", text)
        value = match.group(1).strip() if match else ""
        return value or None

    title = field("Title")
    if not title:
        return None
    return BrowseRecord(
        id=md5,
        title=title,
        source="libgen",
        author=field("Author(s)"),
        publisher=field("Publisher"),
        year=field("Year"),
        language=normalize_language(field("Language") or ""),
        source_url=f"{base_url.rstrip('/')}/ads.php?md5={md5}",
    )
