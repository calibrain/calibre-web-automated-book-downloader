# Direct Download website adapters

`source.py` owns release-source integration; `handler.py` owns download routing
and staging. `__init__.py` exposes the public API and triggers registration.
`annas_archive.py` owns Anna's Archive search planning, parsing, page caching,
and its MD5 mirror cascade. `oceanofpdf.py` owns OceanofPDF parsing and downloads.
Both implement `DirectDownloadProvider` and join the same search and download
lifecycle through `PROVIDER_TYPES` in `registry.py`.

To add a website:

1. Implement `DirectDownloadProvider` from `common.py` in a site-specific module. Keep HTML
   selectors, URL recognition, and download resolution in that module.
2. Use `parse_search_page` or `parse_search_items` with a site-specific extractor
   returning `ParsedSearchResult`. These helpers normalize languages, filter
   formats, and generate stable IDs per provider and format. Preserve native IDs
   with `record_id` when available, as Anna's Archive does for MD5 records.
3. Use provider-namespaced record IDs and set `source_url` to the user-facing
   record URL. `parse_search_page` does the namespacing automatically when no
   native `record_id` is supplied. URL recognition remains a compatibility fallback.
4. Implement `download` to write to the supplied staging path and return the
   successful URL (or `None`). Honor cancellation and forward progress/status
   callbacks. Reuse `shelfmark.download.http` for supported HTTP and bypass flows.
5. Add the provider class to `PROVIDER_TYPES` in `registry.py`, add its configuration if
   needed, and cover parsing and download behavior with fixture-based tests.

Adapters must not import `source.py`, `handler.py`, or `registry.py`; shared code belongs in `common.py`
or the existing download utilities. A website adapter is internal to the
`direct_download` release source, so it does not need `register_source` or
`register_handler`.

Providers receive the complete book and release-search plan, so Anna's Archive
retains ISBN priority, localized-title variants, and language retries while simpler
providers can derive one or more plain-text queries. Direct Download is available
when it is enabled and at least one configured provider is enabled.
