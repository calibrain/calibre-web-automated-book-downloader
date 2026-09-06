from types import SimpleNamespace

import pytest

from shelfmark.core.models import SearchFilters
from shelfmark.release_sources.direct_download import handler, registry
from shelfmark.release_sources.direct_download.common import ParsedSearchResult, parse_search_page


def test_shared_parser_accepts_provider_specific_extraction():
    from bs4 import BeautifulSoup, Tag

    soup = BeautifulSoup('<li data-url="https://books.example/book">Example</li>', "html.parser")

    def extract(item: Tag) -> ParsedSearchResult:
        return ParsedSearchResult(
            key=item["data-url"],
            title=item.get_text(strip=True),
            formats=("epub", "pdf"),
            language="English",
            source_url=item["data-url"],
        )

    records = parse_search_page(
        soup,
        SearchFilters(format=["epub"], lang=["en"]),
        provider_id="example",
        item_selector="li",
        extract_item=extract,
    )

    assert len(records) == 1
    assert records[0].id.startswith("example:")
    assert records[0].format == "epub"
    assert records[0].language == "en"


def test_web_provider_registry_dispatches_without_source_changes(monkeypatch, tmp_path):
    from shelfmark.release_sources.direct_download import source as dd

    source_url = "https://books.example/example.epub"

    class ExampleProvider:
        id = "example"
        display_name = "Example"

        def is_enabled(self):
            return True

        def handles(self, url):
            return url.startswith("https://books.example/")

        def search(self, book, plan, **kwargs):
            del book, plan, kwargs
            return [
                dd.BrowseRecord(
                    id="example:1",
                    title="Example",
                    source="direct_download",
                    format="epub",
                    source_url=source_url,
                )
            ]

        def download(self, book_info, book_path, *callbacks):
            del book_info, callbacks
            book_path.write_bytes(b"x" * 12_000)
            return source_url

    monkeypatch.setattr(registry, "PROVIDER_TYPES", (ExampleProvider,))
    monkeypatch.setattr(
        registry.config,
        "get",
        lambda key, default=None: True if key == "DIRECT_DOWNLOAD_ENABLED" else default,
    )
    source = dd.DirectDownloadSource()
    plan = SimpleNamespace(
        manual_query="Example",
        primary_query=None,
        source_filters=None,
        languages=None,
    )
    releases = source.search(SimpleNamespace(title="Example"), plan)
    assert len(releases) == 1
    release = releases[0]
    record = ExampleProvider().search(SimpleNamespace(), plan)[0]
    destination = tmp_path / "example.epub"

    assert release.extra["web_provider"] == "example"
    assert handler._download_book(record, destination) == source_url
    assert destination.stat().st_size == 12_000


def test_md5_record_routes_to_annas_archive_cascade(monkeypatch, tmp_path):
    from threading import Event

    from shelfmark.release_sources.direct_download import annas_archive
    from shelfmark.release_sources.direct_download import source as dd

    record = dd.BrowseRecord(
        id="0123456789abcdef0123456789abcdef",
        title="Example",
        source="direct_download",
        format="epub",
    )
    destination = tmp_path / "example.epub"
    cancel_flag = Event()
    calls = []

    def download(*args):
        calls.append(args)
        return "https://mirror.example/file.epub"

    monkeypatch.setattr(annas_archive, "download_book", download)

    assert handler._download_book(record, destination, cancel_flag=cancel_flag) == (
        "https://mirror.example/file.epub"
    )
    assert calls == [(record, destination, None, cancel_flag, None)]


def test_unknown_url_is_not_silently_routed_to_annas_archive(monkeypatch, tmp_path):
    from shelfmark.release_sources.direct_download import annas_archive

    monkeypatch.setattr(
        annas_archive,
        "download_book",
        lambda *_args, **_kwargs: pytest.fail("unknown provider reached Anna's Archive"),
    )
    record = handler.BrowseRecord(
        id="unknown-record",
        title="Example",
        source="direct_download",
        source_url="https://unknown.example/book",
    )

    with pytest.raises(RuntimeError, match="No Direct Download provider owns"):
        handler._download_book(record, tmp_path / "example.epub")


def test_provider_failure_is_suppressed_when_another_provider_succeeds(monkeypatch):
    from shelfmark.release_sources import BrowseRecord
    from shelfmark.release_sources.direct_download.common import (
        DirectDownloadUnavailableError,
    )
    from shelfmark.release_sources.direct_download.source import DirectDownloadSource

    class FailingProvider:
        id = "failing"
        display_name = "Failing"

        def is_enabled(self):
            return True

        def search(self, *_args, **_kwargs):
            raise DirectDownloadUnavailableError("provider unavailable")

    class WorkingProvider:
        id = "working"
        display_name = "Working"

        def is_enabled(self):
            return True

        def search(self, *_args, **_kwargs):
            return [
                BrowseRecord(
                    id="working:1",
                    title="Example",
                    source="direct_download",
                    source_url="https://working.example/book",
                )
            ]

    monkeypatch.setattr(
        registry.config,
        "get",
        lambda key, default=None: True if key == "DIRECT_DOWNLOAD_ENABLED" else default,
    )
    source = DirectDownloadSource()
    source._providers = (FailingProvider(), WorkingProvider())

    releases = source.search(SimpleNamespace(title="Example"), SimpleNamespace())

    assert [release.source_id for release in releases] == ["working:1"]


def test_provider_search_order_follows_slow_source_priority(monkeypatch):
    class Provider:
        def __init__(self, provider_id):
            self.id = provider_id

        def is_enabled(self):
            return True

    monkeypatch.setattr(
        registry.config,
        "get",
        lambda key, default=None: {
            "DIRECT_DOWNLOAD_ENABLED": True,
            "SOURCE_PRIORITY": [
                {"id": "oceanofpdf", "enabled": True},
                {"id": "aa-slow-nowait", "enabled": True},
            ],
        }.get(key, default),
    )

    providers = (Provider("annas_archive"), Provider("oceanofpdf"))

    assert [provider.id for provider in registry.enabled_providers(providers)] == [
        "oceanofpdf",
        "annas_archive",
    ]
