"""OceanofPDF search and download.

Unlike the Z-Library, LibGen and Welib sources, which only download files Anna's Archive
found, OceanofPDF searches and downloads on its own. The whole site sits behind Cloudflare,
so every page goes through `html_get_page` and its bypasser handoff.

The download is three hops:

1. The book page carries one POST form per format, targeting ``Fetching_Resource.php``
   with hidden ``id`` (a file server) and ``filename`` fields.
2. That endpoint answers with an interstitial page whose ``<meta http-equiv="refresh">``
   points at a signed, expiring file URL on a separate file host.
3. The file URL serves the book itself.
"""

import re
from typing import TYPE_CHECKING
from urllib.parse import quote_plus, urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag

from shelfmark.bypass.challenge import challenge_marker
from shelfmark.core import search_deadline
from shelfmark.core.config import config
from shelfmark.core.logger import setup_logger
from shelfmark.core.models import SearchFilters
from shelfmark.download import http as downloader
from shelfmark.download import network
from shelfmark.release_sources.direct_download.common import (
    MIN_VALID_FILE_SIZE,
    DirectDownloadUnavailableError,
    ParsedSearchResult,
    get_attr,
    html_response_text,
    normalize_size,
    parse_search_page,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path
    from threading import Event

    from shelfmark.core.search_plan import ReleaseSearchPlan
    from shelfmark.metadata_providers import BookMetadata
    from shelfmark.release_sources import BrowseRecord

logger = setup_logger(__name__)

PROVIDER_ID = "oceanofpdf"

_FORMAT_TAG_PATTERN = re.compile(r"\[([A-Za-z0-9]{2,5})\]")
# Result summaries read "[PDF] [EPUB] Dune Download by Frank Herbert. Download Dune by...".
# Anchored on the following "Download" because author names carry their own full stops
# ("Anissa M. Bouziane").
_AUTHOR_PATTERN = re.compile(r"\bDownload by (.+?)\.\s+Download\b")
_META_REFRESH_PATTERN = re.compile(r"^\s*(\d+)?\s*;?\s*url\s*=\s*['\"]?([^'\"]+)", re.IGNORECASE)
_FILE_SIZE_PATTERN = re.compile(
    r"\b([A-Z0-9]{2,5}) File Size:\s*([\d.,]+\s*[KMGT]B)", re.IGNORECASE
)
_FETCH_FORM_MARKER = "fetching_resource"
# Every page of the site's Genesis theme has this skip link; a challenge page does not.
_SITE_PAGE_MARKER = "genesis-content"
_HTML_SIGNATURES = (b"<!doctype", b"<html")
# The interstitial asks for a few seconds before redirecting. Honour it, but never let a
# changed page park a download for minutes.
_MAX_REDIRECT_DELAY_SECONDS = 10
_POST_TIMEOUT = (10, 30)
_MAX_POST_REDIRECTS = 5
_HTTP_TEMP_REDIRECT = 307
_HTTP_PERM_REDIRECT = 308
_HTTP_FORBIDDEN = 403
_HTTP_SERVICE_UNAVAILABLE = 503


def _base_url() -> str | None:
    from shelfmark.core import mirrors

    return mirrors.get_oceanofpdf_base_url()


def _is_switched_on() -> bool:
    """Whether OceanofPDF's toggle in Download Sources -> Slow downloads is on.

    A saved list with no OceanofPDF entry was saved before this provider existed. The
    settings page shows a missing option as off, so it counts as off here too - otherwise
    the page would show it off while searches still used it. With nothing saved at all,
    the field's default applies, and that has it on.
    """
    entries = config.get("SOURCE_PRIORITY")
    if not isinstance(entries, list):
        return True
    for entry in entries:
        if isinstance(entry, dict) and entry.get("id") == PROVIDER_ID:
            return bool(entry.get("enabled", True))
    return False


def _is_site_url(url: str) -> bool:
    """Whether ``url`` is a page of the configured OceanofPDF mirror itself.

    The book page, the download form's action and any redirect between them must stay on
    the mirror, same scheme and port, so a record or a page cannot point the fetch at
    another host. The signed file link is the one URL that legitimately sits elsewhere.
    """
    base_url = _base_url()
    if not base_url:
        return False
    try:
        parsed, base = urlparse(url), urlparse(base_url)
        return (
            parsed.scheme == base.scheme
            and (parsed.hostname or "").lower().rstrip(".") == (base.hostname or "").lower()
            and parsed.port == base.port
        )
    except ValueError:
        return False


def _search_url(base_url: str, query: str) -> str:
    return f"{base_url}/?s={quote_plus(query)}"


def _image_src(article: Tag) -> str | None:
    """Return the cover URL, skipping the lazy-load SVG placeholder in ``src``."""
    for img in article.find_all("img"):
        if not isinstance(img, Tag):
            continue
        for attr in ("data-src", "src"):
            value = get_attr(img, attr)
            if value and not value.startswith("data:"):
                return value
    return None


def _extract_search_result(article: Tag) -> ParsedSearchResult | None:
    """Extract one OceanofPDF search result for the shared parser."""
    link = article.select_one("h2.entry-title a[href]")
    if not isinstance(link, Tag):
        return None
    book_url = get_attr(link, "href")
    title = link.get_text(" ", strip=True)
    if not book_url or not title:
        return None

    label = get_attr(article, "aria-label") or ""
    formats = tuple(dict.fromkeys(tag.lower() for tag in _FORMAT_TAG_PATTERN.findall(label)))

    summary = article.select_one(".entry-content, .entry-summary")
    summary_text = summary.get_text(" ", strip=True) if isinstance(summary, Tag) else ""
    author_match = _AUTHOR_PATTERN.search(summary_text)

    return ParsedSearchResult(
        key=book_url,
        title=title,
        formats=formats,
        author=author_match.group(1).strip() if author_match else None,
        preview=_image_src(article),
        content="book (unknown)",
        source_url=book_url,
    )


def parse_search_results(html: str, filters: SearchFilters | None) -> list[BrowseRecord]:
    """Parse an OceanofPDF search page into one record per result and format."""
    return parse_search_page(
        html,
        filters,
        provider_id=PROVIDER_ID,
        item_selector="article",
        extract_item=_extract_search_result,
    )


def _fetch_forms(soup: BeautifulSoup) -> list[Tag]:
    return [
        form
        for form in soup.find_all("form")
        if isinstance(form, Tag) and _FETCH_FORM_MARKER in (get_attr(form, "action") or "").lower()
    ]


def _form_fields(form: Tag) -> dict[str, str]:
    fields: dict[str, str] = {}
    for field in form.find_all("input"):
        if not isinstance(field, Tag):
            continue
        name = get_attr(field, "name")
        if name:
            fields[name] = get_attr(field, "value") or ""
    return fields


def pick_download_form(
    soup: BeautifulSoup, book_format: str | None
) -> tuple[str, dict[str, str]] | None:
    """Return the form action and fields that fetch ``book_format``.

    Without a requested format the first form wins, which is the page's own order.
    """
    candidates = [
        (get_attr(form, "action") or "", _form_fields(form)) for form in _fetch_forms(soup)
    ]
    if not candidates:
        return None
    if not book_format:
        return candidates[0]
    suffix = f".{book_format.lower()}"
    for action, fields in candidates:
        if fields.get("filename", "").lower().endswith(suffix):
            return action, fields
    return None


def file_size_from_page(soup: BeautifulSoup, book_format: str | None) -> str | None:
    """Read the "EPUB File Size: 952 KB" line for the requested format, if present."""
    if not book_format:
        return None
    for label, size in _FILE_SIZE_PATTERN.findall(soup.get_text(" ", strip=True)):
        if label.lower() == book_format.lower():
            return normalize_size(size)
    return None


def parse_file_redirect(html: str, page_url: str) -> tuple[str, int] | None:
    """Return the signed file URL and redirect delay from the interstitial page."""
    soup = BeautifulSoup(html, "html.parser")
    for meta in soup.find_all("meta"):
        if not isinstance(meta, Tag):
            continue
        if (get_attr(meta, "http-equiv") or "").lower() != "refresh":
            continue
        match = _META_REFRESH_PATTERN.match(get_attr(meta, "content") or "")
        if match:
            return urljoin(page_url, match.group(2).strip()), int(match.group(1) or 0)

    # Fallback for a page that drops the meta refresh for a plain link.
    for anchor in soup.find_all("a", href=True):
        href = get_attr(anchor, "href") or ""
        if urlparse(href).path.lower().endswith((".epub", ".pdf")):
            return urljoin(page_url, href), 0
    return None


def _post_fetch_form(action_url: str, fields: dict[str, str], referer: str) -> requests.Response:
    """POST the download form, presenting any clearance the bypasser won for this host."""
    hostname = urlparse(action_url).hostname or ""
    # Not DOWNLOAD_HEADERS wholesale: its Accept-Encoding offers "br", Cloudflare answers
    # in Brotli when offered, and requests cannot decode it without the brotli package -
    # the page came back as compressed bytes with no link to find. Leaving Accept-Encoding
    # to requests offers only what it can decode.
    headers = {
        "User-Agent": downloader.get_cf_user_agent_for_domain(hostname)
        or downloader.DOWNLOAD_HEADERS["User-Agent"],
        "Accept-Language": downloader.DOWNLOAD_HEADERS["Accept-Language"],
        "Referer": referer,
        "Origin": f"{urlparse(referer).scheme}://{urlparse(referer).netloc}",
    }
    method = "POST"
    url = action_url
    for _ in range(_MAX_POST_REDIRECTS + 1):
        if not _is_site_url(url):
            msg = f"OceanofPDF download form points off the mirror: {url}"
            raise requests.exceptions.InvalidURL(msg)
        network.ensure_public_destination(url)
        common = {
            "headers": headers,
            "cookies": downloader.get_cf_cookies_for_domain(hostname),
            "timeout": _POST_TIMEOUT,
            "proxies": network.get_proxies(url),
            "verify": network.get_ssl_verify(url),
            "allow_redirects": False,
        }
        response = (
            requests.post(url, data=fields, **common)
            if method == "POST"
            else requests.get(url, **common)
        )
        location = response.headers.get("Location") if response.is_redirect else None
        if not location:
            return response
        url = urljoin(url, location)
        if response.status_code not in (_HTTP_TEMP_REDIRECT, _HTTP_PERM_REDIRECT):
            method = "GET"
    msg = "OceanofPDF download form redirected too many times"
    raise requests.exceptions.TooManyRedirects(msg)


def _log_unresolved_fetch_page(response: requests.Response) -> None:
    """Record what the download form returned when no file link could be read off it.

    The response body is not in the debug bundle, so without this a changed page, a
    challenge and an undecoded body all look the same: "did not return a file link".
    Diagnostics must never fail a download, so this swallows its own errors.
    """
    try:
        text = response.text or ""
        title_match = re.search(
            r"<title[^>]*>(.*?)</title>", text[:8000], re.IGNORECASE | re.DOTALL
        )
        logger.warning(
            "OceanofPDF download page had no file link: status=%s url=%s bytes=%d "
            "content-type=%r content-encoding=%r title=%r meta_refresh=%s challenge_marker=%r",
            response.status_code,
            response.url,
            len(text),
            response.headers.get("Content-Type"),
            response.headers.get("Content-Encoding"),
            " ".join(title_match.group(1).split())[:120] if title_match else None,
            "http-equiv" in text.lower() and "refresh" in text.lower(),
            challenge_marker(text),
        )
    except Exception:
        logger.debug("Could not describe the OceanofPDF download page", exc_info=True)


def _is_challenge(response: requests.Response) -> bool:
    return response.status_code in (
        _HTTP_FORBIDDEN,
        _HTTP_SERVICE_UNAVAILABLE,
    ) and bool(challenge_marker(response.text))


class OceanofPDFProvider:
    """OceanofPDF provider: searches the site and downloads from its file host."""

    id = PROVIDER_ID
    display_name = "OceanofPDF"

    def is_enabled(self) -> bool:
        # Every page is behind Cloudflare, so without the bypass a search can only fail.
        return (
            _base_url() is not None
            and bool(config.get("USE_CF_BYPASS", True))
            and _is_switched_on()
        )

    def handles(self, url: str) -> bool:
        base_url = _base_url()
        if not base_url:
            return False
        hostname = (urlparse(url).hostname or "").lower().rstrip(".")
        return bool(hostname) and hostname == (urlparse(base_url).hostname or "").lower()

    # --- Search ---

    def _fetch_search_page(self, base_url: str, query: str) -> str:
        url = _search_url(base_url, query)
        selector = network.AAMirrorSelector()
        response = downloader.html_get_page(url, selector=selector, allow_bypasser_fallback=True)
        html = html_response_text(response)
        if not html:
            detail = getattr(selector, "last_failure", None) or "The site could not be reached."
            msg = f"Unable to reach OceanofPDF. {detail}"
            raise DirectDownloadUnavailableError(msg)
        # Checked against the theme's own markup, not just the marker: a protected site's
        # real pages can carry the protection's markers too (see #1289/#1292 for AA).
        if challenge_marker(html) and _SITE_PAGE_MARKER not in html:
            msg = (
                "OceanofPDF answered with a Cloudflare challenge that was not cleared. "
                "Check that the bypass is enabled and working."
            )
            raise DirectDownloadUnavailableError(msg)
        return html

    def _queries(self, plan: ReleaseSearchPlan) -> list[str]:
        """Title + author first, then the bare title as a looser fallback.

        WordPress search ANDs every word across the post, and each post names its author,
        so adding the author narrows "Dune" from forty pages to the right book.
        """
        if plan.manual_query:
            return [plan.manual_query]
        queries: list[str] = []
        for variant in plan.grouped_title_variants:
            for query in (f"{variant.title} {plan.author}".strip(), variant.title.strip()):
                if query and query not in queries:
                    queries.append(query)
        return queries

    def search(
        self,
        book: BookMetadata,
        plan: ReleaseSearchPlan,
        *,
        expand_search: bool = False,
        content_type: str = "ebook",
    ) -> list[BrowseRecord]:
        """Search OceanofPDF, stopping at the first query that finds anything."""
        del book, expand_search, content_type
        base_url = _base_url()
        if not base_url:
            return []

        if plan.source_filters is not None:
            filters = plan.source_filters
            queries = [plan.manual_query] if plan.manual_query else []
        else:
            filters = SearchFilters(lang=plan.languages)
            queries = self._queries(plan)

        for query in queries:
            if search_deadline.expired():
                logger.info("Release search budget spent; skipping remaining OceanofPDF queries")
                break
            logger.debug("Searching OceanofPDF: %s", query)
            records = parse_search_results(self._fetch_search_page(base_url, query), filters)
            if records:
                logger.info("Found %s OceanofPDF releases for %r", len(records), query)
                return records
        return []

    # --- Download ---

    def _resolve_file_url(
        self,
        action_url: str,
        fields: dict[str, str],
        book_url: str,
        cancel_flag: Event | None,
    ) -> tuple[str, int] | None:
        """POST the download form and read the file URL off the interstitial."""
        response = _post_fetch_form(action_url, fields, book_url)
        if _is_challenge(response):
            # The clearance expired between the book page and this POST. Bypassers only
            # replay GETs, so re-solve on the book page and post again with fresh cookies.
            logger.info("OceanofPDF download form was challenged; refreshing clearance")
            downloader.html_get_page(book_url, cancel_flag=cancel_flag, use_bypasser=True)
            response = _post_fetch_form(action_url, fields, book_url)
        response.raise_for_status()
        resolved = parse_file_redirect(response.text, response.url or action_url)
        if resolved is None:
            _log_unresolved_fetch_page(response)
        return resolved

    def download(
        self,
        book_info: BrowseRecord,
        book_path: Path,
        progress_callback: Callable[[float], None] | None,
        cancel_flag: Event | None,
        status_callback: Callable[[str, str | None], None] | None,
    ) -> str | None:
        """Download the requested format of an OceanofPDF book. Returns the file URL."""
        book_url = book_info.source_url
        if not book_url:
            msg = f"OceanofPDF record {book_info.id!r} has no book page URL"
            raise RuntimeError(msg)
        if not self.is_enabled():
            msg = "OceanofPDF is not enabled"
            raise RuntimeError(msg)
        if not _is_site_url(book_url):
            msg = f"OceanofPDF record {book_info.id!r} does not point at the configured mirror"
            raise RuntimeError(msg)

        def status(message: str) -> None:
            if status_callback:
                status_callback("resolving", message)

        try:
            status("Fetching OceanofPDF book page")
            page = html_response_text(
                downloader.html_get_page(
                    book_url,
                    cancel_flag=cancel_flag,
                    status_callback=status_callback,
                    allow_bypasser_fallback=True,
                )
            )
            if not page:
                logger.warning("OceanofPDF book page could not be fetched: %s", book_url)
                return None

            soup = BeautifulSoup(page, "html.parser")
            form = pick_download_form(soup, book_info.format)
            if form is None:
                logger.warning(
                    "No OceanofPDF download form for format %r on %s", book_info.format, book_url
                )
                return None
            action, fields = form
            action_url = urljoin(book_url, action)
            if not _is_site_url(action_url):
                logger.warning("OceanofPDF download form points off the mirror: %s", action_url)
                return None

            if cancel_flag and cancel_flag.is_set():
                return None
            status("Requesting OceanofPDF download link")
            resolved = self._resolve_file_url(action_url, fields, book_url, cancel_flag)
            if resolved is None:
                logger.warning("OceanofPDF did not return a file link for %s", book_url)
                return None
            file_url, delay = resolved
            if urlparse(file_url).scheme not in ("http", "https"):
                logger.warning("OceanofPDF file link is not an http(s) URL: %s", file_url)
                return None

            wait = min(delay, _MAX_REDIRECT_DELAY_SECONDS)
            if wait > 0:
                status(f"OceanofPDF - Waiting {wait}s")
                if cancel_flag and cancel_flag.wait(timeout=wait):
                    return None

            logger.info("Resolved OceanofPDF file URL for %s", book_info.title)
            data = downloader.download_url(
                file_url,
                file_size_from_page(soup, book_info.format) or book_info.size or "",
                progress_callback,
                cancel_flag,
                None,
                status_callback,
                referer=action_url,
            )
            if not data:
                logger.warning("No data received from OceanofPDF for %s", book_info.title)
                return None

            size = data.getbuffer().nbytes
            data.seek(0)
            head = data.read(64).lstrip().lower()
            if size < MIN_VALID_FILE_SIZE or head.startswith(_HTML_SIGNATURES):
                logger.warning(
                    "OceanofPDF returned an HTML page or a stub (%s bytes), not a book", size
                )
                return None

            with book_path.open("wb") as f:
                f.write(data.getbuffer())
        except (requests.exceptions.RequestException, OSError, ValueError) as e:
            logger.warning("OceanofPDF download failed for %s: %s", book_url, e)
            return None
        else:
            return file_url
