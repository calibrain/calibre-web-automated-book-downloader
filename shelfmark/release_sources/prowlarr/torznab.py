"""Torznab/Newznab (RSS/XML) helpers for Prowlarr.

Used to fetch richer metadata from specific indexers (e.g., MyAnonamouse) that
isn't available via Prowlarr's JSON search endpoint.
"""

from __future__ import annotations

from typing import Any

from defusedxml import ElementTree as DefusedElementTree
from defusedxml.common import DefusedXmlException


def _local_name(tag: str) -> str:
    """Return tag name without namespace."""
    if tag.startswith("{"):
        return tag.split("}", 1)[1]
    return tag


def _coerce_int(value: str | None) -> int | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _coerce_float(value: str | None) -> float | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _strip_author_from_title(title: str, author: str | None) -> str:
    """Strip duplicate trailing author text from a Torznab title.

    Prowlarr's MyAnonamouse parser appends " by {author}" into the title while
    also emitting author/booktitle fields. Shelfmark's UI shows author
    separately, so strip the duplicated " by author" segment when present.
    """
    if not title or not author:
        return title

    needle = f" by {author}"
    if needle in title:
        return title.replace(needle, "", 1).strip()

    return title


def parse_torznab_xml(xml_text: str) -> list[dict[str, Any]]:
    """Parse a Torznab/Newznab XML response into Prowlarr-like result dicts.

    This keeps the parsed shape close to Prowlarr's JSON search results.
    """
    if not xml_text or not xml_text.strip():
        return []

    try:
        root = DefusedElementTree.fromstring(xml_text)
    except DefusedElementTree.ParseError, DefusedXmlException:
        return []

    items = root.findall(".//item")
    results: list[dict[str, Any]] = []

    for item in items:
        title = (item.findtext("title") or "").strip()
        guid = (item.findtext("guid") or "").strip() or None
        download_url = (item.findtext("link") or "").strip() or None
        info_url = (item.findtext("comments") or "").strip() or None
        pub_date = (item.findtext("pubDate") or "").strip() or None

        size = _coerce_int(item.findtext("size"))

        enclosure = item.find("enclosure")
        enclosure_type = enclosure.get("type") if enclosure is not None else None
        enclosure_url = enclosure.get("url") if enclosure is not None else None

        protocol: str | None = None
        if enclosure_type == "application/x-bittorrent":
            protocol = "torrent"
        elif enclosure_type == "application/x-nzb":
            protocol = "usenet"

        if not download_url and enclosure_url:
            download_url = enclosure_url.strip() or None

        # Prowlarr tags each item with <prowlarrindexer id="12">, Jackett with
        # <jackettindexer id="mytracker"> (a slug, so only Prowlarr's id is numeric).
        indexer_el = item.find("prowlarrindexer")
        if indexer_el is None:
            indexer_el = item.find("jackettindexer")
        indexer_key = (
            ((indexer_el.get("id") or "").strip() or None) if indexer_el is not None else None
        )
        indexer_id = _coerce_int(indexer_key)
        indexer_name = (indexer_el.text or "").strip() if indexer_el is not None else ""

        categories: list[int] = []
        for cat_el in item.findall("category"):
            cat_id = _coerce_int(cat_el.text)
            if cat_id is not None:
                categories.append(cat_id)

        # Collect torznab/newznab attr elements (namespaced).
        attrs: dict[str, str] = {}
        tags: list[str] = []
        for el in item.iter():
            if _local_name(el.tag) != "attr":
                continue
            name = (el.get("name") or "").strip()
            value = (el.get("value") or "").strip()
            if not name:
                continue
            if name == "tag" and value:
                tags.append(value)
                continue
            if value:
                attrs[name] = value

        seeders = _coerce_int(attrs.get("seeders"))
        peers = _coerce_int(attrs.get("peers"))
        leechers: int | None = None
        if peers is not None and seeders is not None and peers >= seeders:
            leechers = peers - seeders

        author = attrs.get("author") or None
        book_title = attrs.get("booktitle") or None
        info_hash = attrs.get("infohash") or None

        download_volume_factor = _coerce_float(attrs.get("downloadvolumefactor"))
        upload_volume_factor = _coerce_float(attrs.get("uploadvolumefactor"))
        cleaned_title = _strip_author_from_title(title, author)

        results.append(
            {
                "title": cleaned_title or title,
                "guid": guid or info_url or download_url or f"{indexer_id}:{title}",
                "size": size,
                "protocol": protocol or "unknown",
                "downloadUrl": download_url,
                "infoUrl": info_url,
                "publishDate": pub_date,
                "indexer": indexer_name or None,
                "indexerId": indexer_id,
                "indexerKey": indexer_key,
                "categories": categories,
                "seeders": seeders,
                "leechers": leechers,
                "files": _coerce_int(attrs.get("files")),
                "grabs": _coerce_int(attrs.get("grabs")),
                "infoHash": info_hash,
                "indexerFlags": tags,
                # Optional richer fields (not available via JSON search)
                "author": author,
                "bookTitle": book_title,
                "downloadVolumeFactor": download_volume_factor,
                "uploadVolumeFactor": upload_volume_factor,
                # Pass through all torznab attributes for tooltip display
                "torznabAttrs": attrs,
            }
        )

    return results
