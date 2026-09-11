"""OceanofPDF search cards and form-based downloads."""

import re
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict
from urllib.parse import quote_plus, urlparse

import requests
from bs4 import BeautifulSoup, Tag

from shelfmark.core import mirrors
from shelfmark.core.config import config
from shelfmark.core.models import SearchFilters
from shelfmark.download import http as downloader
from shelfmark.download import network
from shelfmark.release_sources.direct_download.common import (
    MIN_VALID_FILE_SIZE,
    ParsedSearchResult,
    get_attr,
    html_response_text,
    normalize_size,
    parse_search_page,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from io import BytesIO
    from threading import Event

    from shelfmark.core.search_plan import ReleaseSearchPlan
    from shelfmark.metadata_providers import BookMetadata
    from shelfmark.release_sources import BrowseRecord

_FORMAT_PATTERN = re.compile(r"\b(pdf|epub|mobi|azw3)\b", re.IGNORECASE)
_SIZE_PATTERN = re.compile(r"\b\d+(?:\.\d+)?\s*(?:KB|MB|GB|TB)\b", re.IGNORECASE)
_AUTO_DOWNLOAD_PATTERN = re.compile(
    r"location\s*\.\s*href\s*=\s*(['\"])(?P<url>.+?)\1",
    re.IGNORECASE,
)
_MAX_SEARCH_PAGES = 5
_MAX_SEARCH_RESULTS = 25


class _DownloadForm(TypedDict):
    action: str
    fields: dict[str, str]
    format: str
    size: str | None


def _handles_url(url: str) -> bool:
    """Recognize HTTP URLs on configured OceanofPDF mirrors and their subdomains."""
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme not in {"http", "https"} or not hostname:
        return False
    for mirror_url in mirrors.get_oceanofpdf_mirrors():
        mirror_host = (urlparse(mirror_url).hostname or "").lower().rstrip(".")
        if mirror_host and (hostname == mirror_host or hostname.endswith(f".{mirror_host}")):
            return True
    return False


def _metadata_value(container: Tag | None, label: str) -> str | None:
    """Read a labelled value from an OceanofPDF result card."""
    if container is None:
        return None
    expected = label.casefold().rstrip(":")
    for strong in container.find_all("strong"):
        if strong.get_text(" ", strip=True).casefold().rstrip(":") != expected:
            continue
        values: list[str] = []
        for sibling in strong.next_siblings:
            if isinstance(sibling, Tag) and sibling.name == "br":
                break
            text = sibling.get_text(" ", strip=True) if isinstance(sibling, Tag) else str(sibling)
            if text.strip():
                values.append(text.strip())
        return " ".join(values).strip() or None
    return None


def _formats(text: str) -> list[str]:
    return list(dict.fromkeys(match.casefold() for match in _FORMAT_PATTERN.findall(text)))


def _extract_search_item(article: Tag) -> ParsedSearchResult | None:
    """Extract OceanofPDF-specific fields; shared parsing handles the rest."""
    title_link = article.select_one(".entry-title a[href]")
    if not isinstance(title_link, Tag):
        return None
    detail_url = get_attr(title_link, "href")
    if not detail_url or not _handles_url(detail_url):
        return None

    metadata = article.select_one(".postmetainfo")
    metadata_tag = metadata if isinstance(metadata, Tag) else None
    image = article.select_one(".entry-image[data-src]") or article.select_one(".entry-image[src]")
    image_tag = image if isinstance(image, Tag) else None
    format_text = " ".join(
        [
            get_attr(article, "aria-label") or "",
            title_link.get_text(" ", strip=True),
            (article.select_one(".entry-content") or article).get_text(" ", strip=True),
        ]
    )
    return ParsedSearchResult(
        key=detail_url,
        title=title_link.get_text(" ", strip=True),
        formats=tuple(_formats(format_text)),
        author=_metadata_value(metadata_tag, "Author"),
        language=_metadata_value(metadata_tag, "Language"),
        preview=(
            (get_attr(image_tag, "data-src") or get_attr(image_tag, "src")) if image_tag else None
        ),
        content="ebook",
        source_url=detail_url,
    )


def parse_results(html: str, filters: SearchFilters) -> list[BrowseRecord]:
    """Parse OceanofPDF cards through the shared search-result parser."""
    return parse_search_page(
        html,
        filters,
        provider_id="oceanofpdf",
        item_selector="article.entry",
        extract_item=_extract_search_item,
    )


def parse_download_forms(html: str, detail_url: str) -> list[_DownloadForm]:
    """Extract OceanofPDF's format-specific POST download buttons."""
    soup = BeautifulSoup(html, "html.parser")
    forms: list[_DownloadForm] = []
    for form in soup.select("form[action]"):
        action = downloader.get_absolute_url(detail_url, get_attr(form, "action") or "")
        method = (get_attr(form, "method") or "get").lower()
        if method != "post" or not _handles_url(action):
            continue
        fields = {
            name: value
            for field in form.select("input[name]")
            if (name := get_attr(field, "name")) and (value := get_attr(field, "value"))
        }
        filename = fields.get("filename", "")
        book_format = Path(filename).suffix.lstrip(".").casefold()
        if book_format not in _formats(filename):
            continue
        nearby_text = form.parent.get_text(" ", strip=True) if isinstance(form.parent, Tag) else ""
        size_match = _SIZE_PATTERN.search(nearby_text)
        forms.append(
            {
                "action": action,
                "fields": fields,
                "format": book_format,
                "size": normalize_size(size_match.group(0)) if size_match else None,
            }
        )
    return forms


def _select_download_form(forms: list[_DownloadForm], book_format: str | None) -> _DownloadForm:
    selected = (
        next((form for form in forms if form["format"] == book_format.casefold()), None)
        if book_format
        else (forms[0] if forms else None)
    )
    if selected is None:
        requested = f" for {book_format.upper()}" if book_format else ""
        raise RuntimeError(f"No OceanofPDF download button was found{requested}")
    return selected


def _parse_handoff_form(html: str, response_url: str) -> tuple[str, dict[str, str]]:
    """Extract the form OceanofPDF auto-submits from its resource page."""
    soup = BeautifulSoup(html, "html.parser")
    form = soup.select_one("form#my_form[action]")
    if not isinstance(form, Tag) or (get_attr(form, "method") or "get").casefold() != "post":
        raise RuntimeError("OceanofPDF returned no download handoff form")
    action = downloader.get_absolute_url(response_url, get_attr(form, "action") or "")
    fields = {
        name: get_attr(field, "value") or ""
        for field in form.select("input[name]")
        if (name := get_attr(field, "name"))
    }
    if not action:
        raise RuntimeError("OceanofPDF returned an invalid download handoff form")
    return action, fields


def _parse_auto_download_url(html: str, response_url: str, book_format: str) -> str:
    """Extract and validate the file URL from the handoff page's redirect script."""
    match = _AUTO_DOWNLOAD_PATTERN.search(html)
    href = downloader.get_absolute_url(response_url, match.group("url")) if match else ""
    if not href or not _handles_url(href):
        raise RuntimeError("OceanofPDF returned no automatic download URL")
    if Path(urlparse(href).path).suffix.lstrip(".").casefold() != book_format:
        raise RuntimeError("OceanofPDF returned a download URL in the wrong format")
    return href


def _post_download_form(
    form: _DownloadForm,
    detail_url: str,
    progress_callback: Callable[[float], None] | None,
    cancel_flag: Event | None,
    status_callback: Callable[[str, str | None], None] | None,
) -> BytesIO | None:
    """Follow OceanofPDF's two POST handoff pages to the final file URL."""
    # DOWNLOAD_HEADERS advertises Brotli, but the image has no Brotli decoder. Cloudflare
    # compresses these HTML handoff pages with it, leaving requests.response.text as binary.
    headers = {
        **downloader.DOWNLOAD_HEADERS,
        "Referer": detail_url,
        "Accept-Encoding": "gzip, deflate",
    }
    hostname = urlparse(form["action"]).hostname or ""
    user_agent = downloader.get_cf_user_agent_for_domain(hostname)
    if user_agent:
        headers["User-Agent"] = user_agent
    resource_page = requests.post(
        form["action"],
        data=form["fields"],
        headers=headers,
        cookies=downloader.get_cf_cookies_for_domain(hostname),
        proxies=network.get_proxies(form["action"]),
        timeout=downloader.REQUEST_TIMEOUT,
        verify=network.get_ssl_verify(form["action"]),
    )
    resource_page.raise_for_status()

    handoff_url, handoff_fields = _parse_handoff_form(resource_page.text, resource_page.url)
    headers["Referer"] = resource_page.url
    handoff_page = requests.post(
        handoff_url,
        data=handoff_fields,
        headers=headers,
        proxies=network.get_proxies(handoff_url),
        timeout=downloader.REQUEST_TIMEOUT,
        verify=network.get_ssl_verify(handoff_url),
    )
    handoff_page.raise_for_status()

    file_url = _parse_auto_download_url(handoff_page.text, handoff_page.url, form["format"])
    return downloader.download_url(
        file_url,
        form["size"] or "",
        progress_callback,
        cancel_flag,
        status_callback=status_callback,
        referer=handoff_page.url,
    )


class OceanofPDFProvider:
    """OceanofPDF adapter for the internal Direct Download provider registry."""

    id = "oceanofpdf"
    display_name = "OceanofPDF"

    def is_enabled(self) -> bool:
        if not mirrors.has_oceanofpdf_mirror_configuration():
            return False
        priority = config.get("SOURCE_PRIORITY", [])
        if not isinstance(priority, list):
            return False
        return any(
            isinstance(item, dict) and item.get("id") == self.id and bool(item.get("enabled", True))
            for item in priority
        )

    def handles(self, url: str) -> bool:
        return _handles_url(url)

    def search_query(self, query: str, filters: SearchFilters) -> list[BrowseRecord]:
        """Run one OceanofPDF query."""
        if not self.is_enabled():
            return []
        base_url = mirrors.get_oceanofpdf_primary_url()
        if not base_url:
            return []
        encoded_query = quote_plus(query)
        results: list[BrowseRecord] = []
        seen_ids: set[str] = set()
        for page_number in range(1, _MAX_SEARCH_PAGES + 1):
            search_url = (
                f"{base_url}/?s={encoded_query}"
                if page_number == 1
                else f"{base_url}/page/{page_number}/?s={encoded_query}"
            )
            page = downloader.html_get_page(
                search_url,
                retry=2,
                allow_bypasser_fallback=True,
                success_delay=0,
            )
            html = html_response_text(page)
            if not html:
                break
            soup = BeautifulSoup(html, "html.parser")
            # OceanofPDF cannot apply Shelfmark's format/language filters on the
            # server. Collect a bounded result set across its WordPress pages so
            # filtered-out cards on early pages do not hide later matches.
            for record in parse_results(html, filters):
                if record.id in seen_ids:
                    continue
                seen_ids.add(record.id)
                results.append(record)
                if len(results) >= _MAX_SEARCH_RESULTS:
                    return results
            if not soup.select_one("article.entry"):
                break
        return results

    def search(
        self,
        book: BookMetadata,
        plan: ReleaseSearchPlan,
        *,
        expand_search: bool = False,
        content_type: str = "ebook",
    ) -> list[BrowseRecord]:
        """Search useful query variants from the shared release-search context."""
        del expand_search
        if content_type != "ebook":
            return []

        filters = (
            replace(plan.source_filters)
            if plan.source_filters is not None
            else SearchFilters(lang=plan.languages or [])
        )
        queries = [plan.manual_query or plan.primary_query or book.title]
        if not plan.manual_query and plan.title_variants:
            queries.append(plan.title_variants[0].title)

        for query in dict.fromkeys(value.strip() for value in queries if value and value.strip()):
            records = self.search_query(query, filters)
            if records:
                return records
        return []

    def download(
        self,
        book_info: BrowseRecord,
        book_path: Path,
        progress_callback: Callable[[float], None] | None,
        cancel_flag: Event | None,
        status_callback: Callable[[str, str | None], None] | None,
    ) -> str | None:
        detail_url = book_info.source_url or ""
        if not _handles_url(detail_url):
            return None
        if status_callback:
            status_callback("resolving", "Fetching OceanofPDF download options")
        page = downloader.html_get_page(
            detail_url,
            retry=2,
            cancel_flag=cancel_flag,
            status_callback=status_callback,
            allow_bypasser_fallback=True,
            success_delay=0,
        )
        forms = parse_download_forms(html_response_text(page), detail_url)
        selected = _select_download_form(forms, book_info.format)
        if status_callback:
            status_callback("resolving", f"Resolving {selected['format'].upper()} download")
        data = _post_download_form(
            selected, detail_url, progress_callback, cancel_flag, status_callback
        )
        if not data:
            raise RuntimeError("OceanofPDF returned no file data")
        if data.getbuffer().nbytes < MIN_VALID_FILE_SIZE:
            raise RuntimeError("OceanofPDF returned a file that was too small")
        if cancel_flag and cancel_flag.is_set():
            return None

        data.seek(0)
        with book_path.open("wb") as output:
            output.write(data.getbuffer())
        if progress_callback:
            progress_callback(100.0)
        return detail_url
