# Environment Variables

This document lists all configuration options that can be set via environment variables.

> **Auto-generated** - Do not edit manually. Run `python scripts/generate_env_docs.py` to regenerate.

## Table of Contents

- [Bootstrap Configuration](#bootstrap-configuration)
- [Egress / VPN Routing](#egress--vpn-routing)
- [General](#general)
- [Search Mode](#search-mode)
- [Downloads](#downloads)
- [Security](#security)
- [Network](#network)
- [Advanced](#advanced)
- [Prowlarr](#prowlarr)
- [Newznab](#newznab)
- [AudiobookBay](#audiobookbay)
- [IRC](#irc)
- [Download Clients](#download-clients)
- [Metadata Providers](#metadata-providers)
  - [Hardcover](#metadata-providers-hardcover)
  - [Open Library](#metadata-providers-open-library)
  - [Google Books](#metadata-providers-google-books)
  - [Moly.hu](#metadata-providers-moly.hu)
- [Direct Download](#direct-download)
  - [Download Sources](#direct-download-download-sources)
  - [Cloudflare Bypass](#direct-download-cloudflare-bypass)
  - [Mirrors](#direct-download-mirrors)

---

## Bootstrap Configuration

These environment variables are used at startup before the settings system loads. They typically configure paths, server settings, and authentication startup behavior.

| Variable | Description | Type | Default |
|----------|-------------|------|---------|
| `CONFIG_DIR` | Directory for storing configuration files and plugin settings. | string (path) | `/config` |
| `LOG_ROOT` | Root directory for log files. | string (path) | `/var/log/` |
| `TMP_DIR` | Staging directory for downloads before moving to destination. | string (path) | `/tmp/shelfmark` |
| `ENABLE_LOGGING` | Enable file logging under LOG_ROOT/shelfmark/ (including shelfmark.log and startup logs). | boolean | `true` |
| `FLASK_HOST` | Host address for the Flask web server. | string | `0.0.0.0` |
| `FLASK_PORT` | Port number for the Flask web server. | number | `8084` |
| `SESSION_COOKIE_SECURE` | Enable secure cookies (requires HTTPS). | boolean | `false` |
| `CWA_DB_PATH` | Path to the Calibre-Web database for authentication integration. | string (path) | `/auth/app.db` |
| `HIDE_LOCAL_AUTH` | Hide the username/password login form when OIDC is active. | boolean | `false` |
| `DISABLE_LOCAL_AUTH` | Disable username/password login and remove the local-admin prerequisite for OIDC. Implies HIDE_LOCAL_AUTH; with AUTH_METHOD=builtin, everyone is locked out until auth env vars are changed. | boolean | `false` |
| `OIDC_AUTO_REDIRECT` | Automatically redirect to the OIDC provider instead of showing the login page. | boolean | `false` |
| `DOCKERMODE` | Indicates the application is running inside a Docker container. | boolean | `false` |
| `ONBOARDING` | Show the onboarding wizard on first run. Set to false to skip (useful for ephemeral storage). | boolean | `true` |

<details>
<summary>Detailed descriptions</summary>

#### `CONFIG_DIR`

Directory for storing configuration files and plugin settings.

- **Type:** string (path)
- **Default:** `/config`

#### `LOG_ROOT`

Root directory for log files.

- **Type:** string (path)
- **Default:** `/var/log/`

#### `TMP_DIR`

Staging directory for downloads before moving to destination.

- **Type:** string (path)
- **Default:** `/tmp/shelfmark`

#### `ENABLE_LOGGING`

Enable file logging under LOG_ROOT/shelfmark/ (including shelfmark.log and startup logs).

- **Type:** boolean
- **Default:** `true`

#### `FLASK_HOST`

Host address for the Flask web server.

- **Type:** string
- **Default:** `0.0.0.0`

#### `FLASK_PORT`

Port number for the Flask web server.

- **Type:** number
- **Default:** `8084`

#### `SESSION_COOKIE_SECURE`

Enable secure cookies (requires HTTPS).

- **Type:** boolean
- **Default:** `false`

#### `CWA_DB_PATH`

Path to the Calibre-Web database for authentication integration.

- **Type:** string (path)
- **Default:** `/auth/app.db`

#### `HIDE_LOCAL_AUTH`

Hide the username/password login form when OIDC is active.

- **Type:** boolean
- **Default:** `false`

#### `DISABLE_LOCAL_AUTH`

Disable username/password login and remove the local-admin prerequisite for OIDC. Implies HIDE_LOCAL_AUTH; with AUTH_METHOD=builtin, everyone is locked out until auth env vars are changed.

- **Type:** boolean
- **Default:** `false`

#### `OIDC_AUTO_REDIRECT`

Automatically redirect to the OIDC provider instead of showing the login page.

- **Type:** boolean
- **Default:** `false`

#### `DOCKERMODE`

Indicates the application is running inside a Docker container.

- **Type:** boolean
- **Default:** `false`

#### `ONBOARDING`

Show the onboarding wizard on first run. Set to false to skip (useful for ephemeral storage).

- **Type:** boolean
- **Default:** `true`

</details>

## Egress / VPN Routing

These startup-only variables are consumed by `entrypoint.sh` / `wireguard.sh` to select and configure the WireGuard transparent-egress kill-switch. `USING_WIREGUARD` and [`USING_TOR`](#using_tor) (documented under Network) are mutually exclusive; both require root startup.

| Variable | Description | Type | Default |
|----------|-------------|------|---------|
| `USING_WIREGUARD` | Route all traffic through a WireGuard VPN tunnel with a fail-closed iptables kill-switch (non-tunnel egress is dropped). Requires root startup and NET_ADMIN (plus NET_RAW). Mutually exclusive with USING_TOR. | boolean | `false` |
| `WIREGUARD_CONFIG` | Path to the mounted wg-quick configuration file. | string (path) | `/config/wg0.conf` |
| `WIREGUARD_INTERFACE` | WireGuard interface name brought up by wg-quick. | string | `wg0` |
| `LAN_NETWORK` | Comma-separated CIDRs kept off the tunnel so the WebUI and internal download clients (Prowlarr, qBittorrent) stay reachable. | string (comma-separated) | `127.0.0.0/8,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16` |
| `WIREGUARD_ENFORCE_DNS` | Pin the container's resolver so DNS cannot silently fall back to an off-tunnel path. The resolver used is WIREGUARD_DNS if set, else the tunnel config's DNS = line. This does NOT force queries through the tunnel: it is designed for a trusted LAN resolver kept reachable off-tunnel via LAN_NETWORK (the query leaves over the LAN; the resolver encrypts upstream while the download still egresses via the tunnel). Special case: when Docker's embedded resolver (nameserver 127.0.0.11) is present, it is PRESERVED so container-name resolution (e.g. prowlarr, qbittorrent) keeps working, and the embedded resolver's upstream must be pinned via the container's compose dns: list. Fails closed (refuses to start) only when no embedded resolver is present AND no resolver is defined, or /etc/resolv.conf is not writable. | boolean | `true` |
| `WIREGUARD_DNS` | Explicit resolver(s) (comma/space separated) to pin when WIREGUARD_ENFORCE_DNS is true and Docker's embedded resolver is NOT in use. Use when the VPN's pushed DNS filters domains you need; point it at a resolver reachable via the tunnel or an allowed LAN resolver. NOTE: when the embedded resolver (127.0.0.11) is present it is preserved and this value cannot repoint its upstream from inside the container — set the container's compose dns: list to the trusted resolver instead. | string (comma-separated) | `unset (uses config DNS = line)` |
| `WIREGUARD_DISABLE_IPV6` | Strip IPv6 Address/AllowedIPs/DNS from the tunnel config before wg-quick (many container kernels lack the ip6tables raw table wg-quick needs) and remove IPv6 as a leak surface. | boolean | `true` |
| `WIREGUARD_ALLOW_IPV6_LEAK` | Escape hatch: continue startup even when an IPv6 kill-switch cannot be installed AND IPv6 cannot be disabled. Only set when the container has no IPv6 connectivity, as IPv6 egress may otherwise bypass the tunnel. | boolean | `false` |
| `WIREGUARD_ALLOW_WEBUI_OFFTUNNEL` | When false (default) the kill-switch is strictly fail-closed: the only off-tunnel egress permitted is loopback, the tunnel device and the LAN allowlist. Set true only if a NON-LAN client (e.g. a public reverse proxy on a different segment) must reach the WebUI; it permits app-server REPLY packets (--sport FLASK_PORT, conntrack REPLY) to leave off-tunnel. Server replies only, never client-initiated egress, so it cannot leak outbound browsing/downloads or the real IP for outbound requests, but it is still an off-tunnel path while the tunnel is down, hence opt-in. LAN WebUI clients never need it (covered by LAN_NETWORK). | boolean | `false` |
| `WIREGUARD_STALE_AFTER` | Seconds since the last WireGuard handshake before the healthcheck bounces the tunnel. | number | `180` |

<details>
<summary>Detailed descriptions</summary>

#### `USING_WIREGUARD`

Route all traffic through a WireGuard VPN tunnel with a fail-closed iptables kill-switch (non-tunnel egress is dropped). Requires root startup and NET_ADMIN (plus NET_RAW). Mutually exclusive with USING_TOR.

- **Type:** boolean
- **Default:** `false`

#### `WIREGUARD_CONFIG`

Path to the mounted wg-quick configuration file.

- **Type:** string (path)
- **Default:** `/config/wg0.conf`

#### `WIREGUARD_INTERFACE`

WireGuard interface name brought up by wg-quick.

- **Type:** string
- **Default:** `wg0`

#### `LAN_NETWORK`

Comma-separated CIDRs kept off the tunnel so the WebUI and internal download clients (Prowlarr, qBittorrent) stay reachable.

- **Type:** string (comma-separated)
- **Default:** `127.0.0.0/8,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16`

#### `WIREGUARD_ENFORCE_DNS`

Pin the container's resolver so DNS cannot silently fall back to an off-tunnel path. The resolver used is WIREGUARD_DNS if set, else the tunnel config's DNS = line. This does NOT force queries through the tunnel: it is designed for a trusted LAN resolver kept reachable off-tunnel via LAN_NETWORK (the query leaves over the LAN; the resolver encrypts upstream while the download still egresses via the tunnel). Special case: when Docker's embedded resolver (nameserver 127.0.0.11) is present, it is PRESERVED so container-name resolution (e.g. prowlarr, qbittorrent) keeps working, and the embedded resolver's upstream must be pinned via the container's compose dns: list. Fails closed (refuses to start) only when no embedded resolver is present AND no resolver is defined, or /etc/resolv.conf is not writable.

- **Type:** boolean
- **Default:** `true`

#### `WIREGUARD_DNS`

Explicit resolver(s) (comma/space separated) to pin when WIREGUARD_ENFORCE_DNS is true and Docker's embedded resolver is NOT in use. Use when the VPN's pushed DNS filters domains you need; point it at a resolver reachable via the tunnel or an allowed LAN resolver. NOTE: when the embedded resolver (127.0.0.11) is present it is preserved and this value cannot repoint its upstream from inside the container — set the container's compose dns: list to the trusted resolver instead.

- **Type:** string (comma-separated)
- **Default:** `unset (uses config DNS = line)`

#### `WIREGUARD_DISABLE_IPV6`

Strip IPv6 Address/AllowedIPs/DNS from the tunnel config before wg-quick (many container kernels lack the ip6tables raw table wg-quick needs) and remove IPv6 as a leak surface.

- **Type:** boolean
- **Default:** `true`

#### `WIREGUARD_ALLOW_IPV6_LEAK`

Escape hatch: continue startup even when an IPv6 kill-switch cannot be installed AND IPv6 cannot be disabled. Only set when the container has no IPv6 connectivity, as IPv6 egress may otherwise bypass the tunnel.

- **Type:** boolean
- **Default:** `false`

#### `WIREGUARD_ALLOW_WEBUI_OFFTUNNEL`

When false (default) the kill-switch is strictly fail-closed: the only off-tunnel egress permitted is loopback, the tunnel device and the LAN allowlist. Set true only if a NON-LAN client (e.g. a public reverse proxy on a different segment) must reach the WebUI; it permits app-server REPLY packets (--sport FLASK_PORT, conntrack REPLY) to leave off-tunnel. Server replies only, never client-initiated egress, so it cannot leak outbound browsing/downloads or the real IP for outbound requests, but it is still an off-tunnel path while the tunnel is down, hence opt-in. LAN WebUI clients never need it (covered by LAN_NETWORK).

- **Type:** boolean
- **Default:** `false`

#### `WIREGUARD_STALE_AFTER`

Seconds since the last WireGuard handshake before the healthcheck bounces the tunnel.

- **Type:** number
- **Default:** `180`

</details>

## General

| Variable | Description | Type | Default |
|----------|-------------|------|---------|
| `SEARCH_PAGE_TITLE` | Title shown above the main search box on the homepage. | string | `Shelfmark` |
| `CALIBRE_WEB_URL` | Adds a navigation button to your book library (Calibre-Web Automated, Grimmory, etc). | string | _none_ |
| `AUDIOBOOK_LIBRARY_URL` | Adds a separate navigation button for your audiobook library (Audiobookshelf, Plex, etc). When both URLs are set, icons are shown instead of text. | string | _none_ |
| `SUPPORTED_FORMATS` | Book formats to include in search results. ZIP/RAR archives are extracted automatically and book files are used if found. | string (comma-separated) | `epub,mobi,azw3,fb2,djvu,cbz,cbr` |
| `SUPPORTED_AUDIOBOOK_FORMATS` | Audiobook formats to include in search results. ZIP/RAR archives are extracted automatically and audiobook files are used if found. | string (comma-separated) | `m4b,mp3,m4a,mp4,flac,ogg,wma,aac,wav,opus,zip,rar` |
| `BOOK_LANGUAGE` | Default language filter for searches. | string (comma-separated) | `en` |

<details>
<summary>Detailed descriptions</summary>

#### `SEARCH_PAGE_TITLE`

**Search Page Title**

Title shown above the main search box on the homepage.

- **Type:** string
- **Default:** `Shelfmark`

#### `CALIBRE_WEB_URL`

**Library URL**

Adds a navigation button to your book library (Calibre-Web Automated, Grimmory, etc).

- **Type:** string
- **Default:** _none_

#### `AUDIOBOOK_LIBRARY_URL`

**Audiobook Library URL**

Adds a separate navigation button for your audiobook library (Audiobookshelf, Plex, etc). When both URLs are set, icons are shown instead of text.

- **Type:** string
- **Default:** _none_

#### `SUPPORTED_FORMATS`

**Supported Book Formats**

Book formats to include in search results. ZIP/RAR archives are extracted automatically and book files are used if found.

- **Type:** string (comma-separated)
- **Default:** `epub,mobi,azw3,fb2,djvu,cbz,cbr`

#### `SUPPORTED_AUDIOBOOK_FORMATS`

**Supported Audiobook Formats**

Audiobook formats to include in search results. ZIP/RAR archives are extracted automatically and audiobook files are used if found.

- **Type:** string (comma-separated)
- **Default:** `m4b,mp3,m4a,mp4,flac,ogg,wma,aac,wav,opus,zip,rar`

</details>

## Search Mode

| Variable | Description | Type | Default |
|----------|-------------|------|---------|
| `SEARCH_MODE` | How you want to search for and download books. | string (choice) | `universal` |
| `BOOK_LANGUAGE` | Default language filter for searches. Users can override this for their own account. | string (comma-separated) | `en` |
| `AA_DEFAULT_SORT` | Default sort order for search results. | string (choice) | `relevance` |
| `SHOW_RELEASE_SOURCE_LINKS` | Show clickable release-source links in release and details modals. Metadata provider links stay enabled. | boolean | `true` |
| `SHOW_COMBINED_SELECTOR` | Show the option to search for and download both a book and audiobook together. | boolean | `true` |
| `FORCE_COMBINED_SEARCH` | Force combined search whenever it's available. Locks the combined toggle on. | boolean | `false` |
| `METADATA_PROVIDER` | Choose which metadata provider to use for book searches. | string (choice) | `openlibrary` |
| `METADATA_PROVIDER_AUDIOBOOK` | Metadata provider for audiobook searches. Uses the book provider if not set. | string (choice) | _empty string_ |
| `METADATA_PROVIDER_COMBINED` | Metadata provider for combined mode searches. Uses the book provider if not set. | string (choice) | _empty string_ |
| `DEFAULT_RELEASE_SOURCE` | The release source tab to open by default in the release modal for books. Leave unset to use the first available source. | string (choice) | _empty string_ |
| `DEFAULT_RELEASE_SOURCE_AUDIOBOOK` | The release source tab to open by default in the release modal for audiobooks. Uses the book release source if not set. | string (choice) | _empty string_ |

<details>
<summary>Detailed descriptions</summary>

#### `SEARCH_MODE`

**Search Mode**

How you want to search for and download books.

- **Type:** string (choice)
- **Default:** `universal`
- **Options:** `direct` (Direct), `universal` (Universal)

#### `BOOK_LANGUAGE`

**Default Book Languages**

Default language filter for searches. Users can override this for their own account.

- **Type:** string (comma-separated)
- **Default:** `en`

#### `AA_DEFAULT_SORT`

**Default Sort Order**

Default sort order for search results.

- **Type:** string (choice)
- **Default:** `relevance`
- **Options:** `relevance` (Most relevant), `newest` (Newest (publication year)), `oldest` (Oldest (publication year)), `largest` (Largest (filesize)), `smallest` (Smallest (filesize)), `newest_added` (Newest (open sourced)), `oldest_added` (Oldest (open sourced))

#### `SHOW_RELEASE_SOURCE_LINKS`

**Show Release Source Links**

Show clickable release-source links in release and details modals. Metadata provider links stay enabled.

- **Type:** boolean
- **Default:** `true`

#### `SHOW_COMBINED_SELECTOR`

**Show Combined Download Selector**

Show the option to search for and download both a book and audiobook together.

- **Type:** boolean
- **Default:** `true`

#### `FORCE_COMBINED_SEARCH`

**Always Use Combined Search**

Force combined search whenever it's available. Locks the combined toggle on.

- **Type:** boolean
- **Default:** `false`

#### `METADATA_PROVIDER`

**Book Metadata Provider**

Choose which metadata provider to use for book searches.

- **Type:** string (choice)
- **Default:** `openlibrary`
- **Options:** `""` (No providers enabled)

#### `METADATA_PROVIDER_AUDIOBOOK`

**Audiobook Metadata Provider**

Metadata provider for audiobook searches. Uses the book provider if not set.

- **Type:** string (choice)
- **Default:** _empty string_
- **Options:** `""` (Use book provider), `""` (No providers enabled)

#### `METADATA_PROVIDER_COMBINED`

**Combined Mode Metadata Provider**

Metadata provider for combined mode searches. Uses the book provider if not set.

- **Type:** string (choice)
- **Default:** _empty string_
- **Options:** `""` (Use book provider), `""` (No providers enabled)

#### `DEFAULT_RELEASE_SOURCE`

**Default Book Release Source**

The release source tab to open by default in the release modal for books. Leave unset to use the first available source.

- **Type:** string (choice)
- **Default:** _empty string_
- **Options:** `""` (Use first available source)

#### `DEFAULT_RELEASE_SOURCE_AUDIOBOOK`

**Default Audiobook Release Source**

The release source tab to open by default in the release modal for audiobooks. Uses the book release source if not set.

- **Type:** string (choice)
- **Default:** _empty string_
- **Options:** `""` (Use book release source)

</details>

## Downloads

| Variable | Description | Type | Default |
|----------|-------------|------|---------|
| `BOOKS_OUTPUT_MODE` | Choose where completed book files are sent. | string (choice) | `folder` |
| `INGEST_DIR` | Directory where downloaded files are saved. Use {User} for per-user folders (e.g. /books/{User}). | string | `/books` |
| `FILE_ORGANIZATION` | Choose how downloaded book files are named and organized. | string (choice) | `rename` |
| `TEMPLATE_RENAME` | Variables: {Author}, {FirstAuthor} (first of several authors), {Title}, {Year}, {Language}, {User}, {OriginalName} (source filename without extension). Universal adds: {Series}, {SeriesPosition}, {Subtitle}, {PrimaryTitle}. Use arbitrary prefix/suffix: {Vol. SeriesPosition - } outputs 'Vol. 2 - ' when set, nothing when empty. Rename templates are filename-only (no '/' or '\'); use Organize for folders. Applies to single-file downloads. | string | `{Author} - {Title} ({Year})` |
| `TEMPLATE_ORGANIZE` | Use / to create folders. Variables: {Author}, {FirstAuthor} (first of several authors), {Title}, {Year}, {Language}, {User}, {OriginalName} (source filename without extension). Universal adds: {Series}, {SeriesPosition}, {Subtitle}, {PrimaryTitle}. Use arbitrary prefix/suffix: {Vol. SeriesPosition - } outputs 'Vol. 2 - ' when set, nothing when empty. | string | `{Author}/{Title} ({Year})` |
| `HARDLINK_TORRENTS` | Create hardlinks instead of copying. Preserves seeding but archives won't be extracted. Don't use if destination is a library ingest folder. | boolean | `false` |
| `BOOKLORE_HOST` | Base URL of your Grimmory instance | string | _none_ |
| `BOOKLORE_USERNAME` | Grimmory account username | string | _none_ |
| `BOOKLORE_PASSWORD` | Grimmory account password | string (secret) | _none_ |
| `BOOKLORE_DESTINATION` | Choose whether uploads go directly to a specific library path or to Bookdrop for review. | string (choice) | `library` |
| `BOOKLORE_LIBRARY_ID` | Grimmory library to upload into. | string (choice) | _none_ |
| `BOOKLORE_PATH_ID` | Grimmory library path for uploads. | string (choice) | _none_ |
| `EMAIL_RECIPIENT` | Optional fallback email address when no per-user email recipient override is configured. | string | _none_ |
| `EMAIL_ATTACHMENT_SIZE_LIMIT_MB` | Maximum total attachment size per email. Email encoding adds overhead; keep this below your provider's limit. | number | `25` |
| `EMAIL_SMTP_HOST` | SMTP server hostname or IP (e.g., smtp.gmail.com). | string | _none_ |
| `EMAIL_SMTP_PORT` | SMTP server port (587 is typical for STARTTLS, 465 for SSL). | number | `587` |
| `EMAIL_SMTP_SECURITY` | Transport security mode for SMTP. | string (choice) | `starttls` |
| `EMAIL_SMTP_USERNAME` | SMTP username (leave empty for no authentication). | string | _none_ |
| `EMAIL_SMTP_PASSWORD` | SMTP password (required if Username is set). | string (secret) | _none_ |
| `EMAIL_FROM` | From address used for the email. You can include a display name (e.g., Shelfmark <mail@example.com>). Leave blank to default to the SMTP username (when it is an email address). | string | _none_ |
| `EMAIL_SUBJECT_TEMPLATE` | Email subject. Variables: {Author}, {Title}, {PrimaryTitle}, {Year}, {Series}, {SeriesPosition}, {Subtitle}, {Format}. | string | `{Title}` |
| `EMAIL_SMTP_TIMEOUT_SECONDS` | How long to wait for SMTP operations before failing. | number | `60` |
| `EMAIL_ALLOW_UNVERIFIED_TLS` | Disable TLS certificate verification (not recommended). | boolean | `false` |
| `DESTINATION_AUDIOBOOK` | Directory where downloaded audiobook files are saved. Leave empty to use the Books destination. | string | _none_ |
| `FILE_ORGANIZATION_AUDIOBOOK` | Choose how downloaded audiobook files are named and organized. | string (choice) | `rename` |
| `TEMPLATE_AUDIOBOOK_RENAME` | Variables: {Author}, {FirstAuthor} (first of several authors), {Title}, {Year}, {Language}, {User}, {OriginalName} (source filename without extension), {Series}, {SeriesPosition}, {Subtitle}, {PrimaryTitle}, {PartNumber}. Use arbitrary prefix/suffix: {Vol. SeriesPosition - } outputs 'Vol. 2 - ' when set, nothing when empty. Rename templates are filename-only (no '/' or '\'); use Organize for folders. Applies to single-file downloads. | string | `{Author} - {Title}` |
| `TEMPLATE_AUDIOBOOK_ORGANIZE` | Use / to create folders. Variables: {Author}, {FirstAuthor} (first of several authors), {Title}, {Year}, {Language}, {User}, {OriginalName} (source filename without extension), {Series}, {SeriesPosition}, {Subtitle}, {PrimaryTitle}, {PartNumber}. Use arbitrary prefix/suffix: {Vol. SeriesPosition - } outputs 'Vol. 2 - ' when set, nothing when empty. | string | `{Author}/{Title}/{Title}` |
| `HARDLINK_TORRENTS_AUDIOBOOK` | Create hardlinks instead of copying. Preserves seeding but archives won't be extracted. Don't use if destination is a library ingest folder. | boolean | `true` |
| `AUTO_OPEN_DOWNLOADS_SIDEBAR` | Automatically open the downloads sidebar when a new download is queued. | boolean | `false` |
| `DOWNLOAD_TO_BROWSER_CONTENT_TYPES` | Automatically download completed files to your browser for the selected content types. | string (comma-separated) | _empty list_ |
| `MAX_CONCURRENT_DOWNLOADS` | Maximum number of simultaneous downloads. | number | `3` |
| `STATUS_TIMEOUT` | How long to keep completed/failed downloads in the queue display. | number | `3600` |

<details>
<summary>Detailed descriptions</summary>

#### `BOOKS_OUTPUT_MODE`

**Output Mode**

Choose where completed book files are sent.

- **Type:** string (choice)
- **Default:** `folder`
- **Options:** `folder` (Folder), `email` (Email (SMTP)), `booklore` (Grimmory (API))

#### `INGEST_DIR`

**Destination**

Directory where downloaded files are saved. Use {User} for per-user folders (e.g. /books/{User}).

- **Type:** string
- **Default:** `/books`
- **Required:** Yes

#### `FILE_ORGANIZATION`

**File Organization**

Choose how downloaded book files are named and organized.

- **Type:** string (choice)
- **Default:** `rename`
- **Options:** `none` (None), `rename` (Rename Only), `organize` (Rename and Organize)

#### `TEMPLATE_RENAME`

**Naming Template**

Variables: {Author}, {FirstAuthor} (first of several authors), {Title}, {Year}, {Language}, {User}, {OriginalName} (source filename without extension). Universal adds: {Series}, {SeriesPosition}, {Subtitle}, {PrimaryTitle}. Use arbitrary prefix/suffix: {Vol. SeriesPosition - } outputs 'Vol. 2 - ' when set, nothing when empty. Rename templates are filename-only (no '/' or '\'); use Organize for folders. Applies to single-file downloads.

- **Type:** string
- **Default:** `{Author} - {Title} ({Year})`

#### `TEMPLATE_ORGANIZE`

**Path Template**

Use / to create folders. Variables: {Author}, {FirstAuthor} (first of several authors), {Title}, {Year}, {Language}, {User}, {OriginalName} (source filename without extension). Universal adds: {Series}, {SeriesPosition}, {Subtitle}, {PrimaryTitle}. Use arbitrary prefix/suffix: {Vol. SeriesPosition - } outputs 'Vol. 2 - ' when set, nothing when empty.

- **Type:** string
- **Default:** `{Author}/{Title} ({Year})`

#### `HARDLINK_TORRENTS`

**Hardlink Book Torrents**

Create hardlinks instead of copying. Preserves seeding but archives won't be extracted. Don't use if destination is a library ingest folder.

- **Type:** boolean
- **Default:** `false`

#### `BOOKLORE_HOST`

**Grimmory URL**

Base URL of your Grimmory instance

- **Type:** string
- **Default:** _none_
- **Required:** Yes

#### `BOOKLORE_USERNAME`

**Username**

Grimmory account username

- **Type:** string
- **Default:** _none_
- **Required:** Yes

#### `BOOKLORE_PASSWORD`

**Password**

Grimmory account password

- **Type:** string (secret)
- **Default:** _none_
- **Required:** Yes

#### `BOOKLORE_DESTINATION`

**Upload Destination**

Choose whether uploads go directly to a specific library path or to Bookdrop for review.

- **Type:** string (choice)
- **Default:** `library`
- **Options:** `library` (Specific Library), `bookdrop` (Bookdrop)

#### `BOOKLORE_LIBRARY_ID`

**Library**

Grimmory library to upload into.

- **Type:** string (choice)
- **Default:** _none_
- **Required:** Yes

#### `BOOKLORE_PATH_ID`

**Path**

Grimmory library path for uploads.

- **Type:** string (choice)
- **Default:** _none_
- **Required:** Yes

#### `EMAIL_RECIPIENT`

**Default Email Recipient**

Optional fallback email address when no per-user email recipient override is configured.

- **Type:** string
- **Default:** _none_

#### `EMAIL_ATTACHMENT_SIZE_LIMIT_MB`

**Attachment Size Limit (MB)**

Maximum total attachment size per email. Email encoding adds overhead; keep this below your provider's limit.

- **Type:** number
- **Default:** `25`
- **Constraints:** min: 1, max: 600

#### `EMAIL_SMTP_HOST`

**SMTP Host**

SMTP server hostname or IP (e.g., smtp.gmail.com).

- **Type:** string
- **Default:** _none_
- **Required:** Yes

#### `EMAIL_SMTP_PORT`

**SMTP Port**

SMTP server port (587 is typical for STARTTLS, 465 for SSL).

- **Type:** number
- **Default:** `587`
- **Constraints:** min: 1, max: 65535

#### `EMAIL_SMTP_SECURITY`

**SMTP Security**

Transport security mode for SMTP.

- **Type:** string (choice)
- **Default:** `starttls`
- **Options:** `none` (None), `starttls` (STARTTLS), `ssl` (SSL/TLS)

#### `EMAIL_SMTP_USERNAME`

**Username**

SMTP username (leave empty for no authentication).

- **Type:** string
- **Default:** _none_

#### `EMAIL_SMTP_PASSWORD`

**Password**

SMTP password (required if Username is set).

- **Type:** string (secret)
- **Default:** _none_

#### `EMAIL_FROM`

**From Address**

From address used for the email. You can include a display name (e.g., Shelfmark <mail@example.com>). Leave blank to default to the SMTP username (when it is an email address).

- **Type:** string
- **Default:** _none_

#### `EMAIL_SUBJECT_TEMPLATE`

**Subject Template**

Email subject. Variables: {Author}, {Title}, {PrimaryTitle}, {Year}, {Series}, {SeriesPosition}, {Subtitle}, {Format}.

- **Type:** string
- **Default:** `{Title}`

#### `EMAIL_SMTP_TIMEOUT_SECONDS`

**SMTP Timeout (seconds)**

How long to wait for SMTP operations before failing.

- **Type:** number
- **Default:** `60`
- **Constraints:** min: 1, max: 600

#### `EMAIL_ALLOW_UNVERIFIED_TLS`

**Allow Unverified TLS**

Disable TLS certificate verification (not recommended).

- **Type:** boolean
- **Default:** `false`

#### `DESTINATION_AUDIOBOOK`

**Destination**

Directory where downloaded audiobook files are saved. Leave empty to use the Books destination.

- **Type:** string
- **Default:** _none_

#### `FILE_ORGANIZATION_AUDIOBOOK`

**File Organization**

Choose how downloaded audiobook files are named and organized.

- **Type:** string (choice)
- **Default:** `rename`
- **Options:** `none` (None), `rename` (Rename Only), `organize` (Rename and Organize), `rename_and_group` (Rename and Group)

#### `TEMPLATE_AUDIOBOOK_RENAME`

**Naming Template**

Variables: {Author}, {FirstAuthor} (first of several authors), {Title}, {Year}, {Language}, {User}, {OriginalName} (source filename without extension), {Series}, {SeriesPosition}, {Subtitle}, {PrimaryTitle}, {PartNumber}. Use arbitrary prefix/suffix: {Vol. SeriesPosition - } outputs 'Vol. 2 - ' when set, nothing when empty. Rename templates are filename-only (no '/' or '\'); use Organize for folders. Applies to single-file downloads.

- **Type:** string
- **Default:** `{Author} - {Title}`

#### `TEMPLATE_AUDIOBOOK_ORGANIZE`

**Path Template**

Use / to create folders. Variables: {Author}, {FirstAuthor} (first of several authors), {Title}, {Year}, {Language}, {User}, {OriginalName} (source filename without extension), {Series}, {SeriesPosition}, {Subtitle}, {PrimaryTitle}, {PartNumber}. Use arbitrary prefix/suffix: {Vol. SeriesPosition - } outputs 'Vol. 2 - ' when set, nothing when empty.

- **Type:** string
- **Default:** `{Author}/{Title}/{Title}`

#### `HARDLINK_TORRENTS_AUDIOBOOK`

**Hardlink Audiobook Torrents**

Create hardlinks instead of copying. Preserves seeding but archives won't be extracted. Don't use if destination is a library ingest folder.

- **Type:** boolean
- **Default:** `true`

#### `AUTO_OPEN_DOWNLOADS_SIDEBAR`

**Auto-Open Downloads Sidebar**

Automatically open the downloads sidebar when a new download is queued.

- **Type:** boolean
- **Default:** `false`

#### `DOWNLOAD_TO_BROWSER_CONTENT_TYPES`

**Download to Browser**

Automatically download completed files to your browser for the selected content types.

- **Type:** string (comma-separated)
  
- **Default:** _empty list_

#### `MAX_CONCURRENT_DOWNLOADS`

**Max Concurrent Downloads**

Maximum number of simultaneous downloads.

- **Type:** number
- **Default:** `3`
- **Requires restart:** Yes
- **Constraints:** min: 1, max: 10

#### `STATUS_TIMEOUT`

**Status Timeout (seconds)**

How long to keep completed/failed downloads in the queue display.

- **Type:** number
- **Default:** `3600`
- **Constraints:** min: 60, max: 86400

</details>

## Security

| Variable | Description | Type | Default |
|----------|-------------|------|---------|
| `AUTH_METHOD` | Select the authentication method for accessing Shelfmark. Restart container after changing Calibre-Web passwords. | string (choice) | `none` |
| `PROXY_AUTH_USER_HEADER` | The HTTP header your proxy uses to pass the authenticated username. | string | `X-Auth-User` |
| `PROXY_AUTH_LOGOUT_URL` | The URL to redirect users to for logging out. Leave empty to disable logout functionality. | string | _empty string_ |
| `PROXY_AUTH_ADMIN_GROUP_HEADER` | Optional: header your proxy uses to pass user groups/roles. | string | `X-Auth-Groups` |
| `PROXY_AUTH_ADMIN_GROUP_NAME` | Optional: users in this group are treated as admins. Leave blank to skip group-based admin detection. | string | _empty string_ |
| `OIDC_DISCOVERY_URL` | OpenID Connect discovery endpoint URL. Usually ends with /.well-known/openid-configuration. | string | _none_ |
| `OIDC_CLIENT_ID` | OAuth2 client ID from your identity provider. | string | _none_ |
| `OIDC_CLIENT_SECRET` | OAuth2 client secret from your identity provider. | string (secret) | _none_ |
| `OIDC_SCOPES` | OAuth2 scopes to request from the identity provider. Managed automatically: includes essential scopes and the group claim when using admin group authorization. | string (comma-separated) | `openid,email,profile` |
| `OIDC_GROUP_CLAIM` | The name of the claim in the ID token that contains user groups. | string | `groups` |
| `OIDC_ADMIN_GROUP` | Users in this group will be given admin access (if enabled below). Leave empty to use database roles only. | string | _empty string_ |
| `OIDC_USE_ADMIN_GROUP` | When enabled, users in the Admin Group are granted admin access. When disabled, admin access is determined solely by database roles. | boolean | `true` |
| `OIDC_AUTO_PROVISION` | Automatically create a user account on first OIDC login. When disabled, users must be pre-created by an admin. | boolean | `true` |
| `OIDC_BUTTON_LABEL` | Custom label for the OIDC sign-in button on the login page. | string | _empty string_ |

<details>
<summary>Detailed descriptions</summary>

#### `AUTH_METHOD`

**Authentication Method**

Select the authentication method for accessing Shelfmark. Restart container after changing Calibre-Web passwords.

- **Type:** string (choice)
- **Default:** `none`
- **Options:** `none` (No Authentication), `builtin` (Local), `proxy` (Proxy Authentication), `oidc` (OIDC (OpenID Connect)), `cwa` (Calibre-Web Database)

#### `PROXY_AUTH_USER_HEADER`

**Proxy Auth User Header**

The HTTP header your proxy uses to pass the authenticated username.

- **Type:** string
- **Default:** `X-Auth-User`

#### `PROXY_AUTH_LOGOUT_URL`

**Proxy Auth Logout URL**

The URL to redirect users to for logging out. Leave empty to disable logout functionality.

- **Type:** string
- **Default:** _empty string_

#### `PROXY_AUTH_ADMIN_GROUP_HEADER`

**Proxy Auth Admin Group Header**

Optional: header your proxy uses to pass user groups/roles.

- **Type:** string
- **Default:** `X-Auth-Groups`

#### `PROXY_AUTH_ADMIN_GROUP_NAME`

**Proxy Auth Admin Group**

Optional: users in this group are treated as admins. Leave blank to skip group-based admin detection.

- **Type:** string
- **Default:** _empty string_

#### `OIDC_DISCOVERY_URL`

**Discovery URL**

OpenID Connect discovery endpoint URL. Usually ends with /.well-known/openid-configuration.

- **Type:** string
- **Default:** _none_
- **Required:** Yes

#### `OIDC_CLIENT_ID`

**Client ID**

OAuth2 client ID from your identity provider.

- **Type:** string
- **Default:** _none_
- **Required:** Yes

#### `OIDC_CLIENT_SECRET`

**Client Secret**

OAuth2 client secret from your identity provider.

- **Type:** string (secret)
- **Default:** _none_
- **Required:** Yes

#### `OIDC_SCOPES`

**Scopes**

OAuth2 scopes to request from the identity provider. Managed automatically: includes essential scopes and the group claim when using admin group authorization.

- **Type:** string (comma-separated)
- **Default:** `openid,email,profile`

#### `OIDC_GROUP_CLAIM`

**Group Claim Name**

The name of the claim in the ID token that contains user groups.

- **Type:** string
- **Default:** `groups`

#### `OIDC_ADMIN_GROUP`

**Admin Group Name**

Users in this group will be given admin access (if enabled below). Leave empty to use database roles only.

- **Type:** string
- **Default:** _empty string_

#### `OIDC_USE_ADMIN_GROUP`

**Use Admin Group for Authorization**

When enabled, users in the Admin Group are granted admin access. When disabled, admin access is determined solely by database roles.

- **Type:** boolean
- **Default:** `true`

#### `OIDC_AUTO_PROVISION`

**Auto-Provision Users**

Automatically create a user account on first OIDC login. When disabled, users must be pre-created by an admin.

- **Type:** boolean
- **Default:** `true`

#### `OIDC_BUTTON_LABEL`

**Login Button Label**

Custom label for the OIDC sign-in button on the login page.

- **Type:** string
- **Default:** _empty string_

</details>

## Network

| Variable | Description | Type | Default |
|----------|-------------|------|---------|
| `CERTIFICATE_VALIDATION` | Controls SSL/TLS certificate verification for outbound connections. Disable for self-signed certificates on internal services (e.g. OIDC providers, Prowlarr). | string (choice) | `enabled` |
| `CUSTOM_DNS` | DNS provider for domain resolution. 'Auto' rotates through providers on failure. | string (choice) | `auto` |
| `CUSTOM_DNS_MANUAL` | Comma-separated list of DNS server IP addresses (e.g., 8.8.8.8, 1.1.1.1). | string | _none_ |
| `USE_DOH` | Use encrypted DNS queries for improved reliability and privacy. | boolean | `true` |
| `USING_TOR` | Route all traffic through Tor for enhanced privacy. Requires root startup. | boolean | `false` |
| `PROXY_MODE` | Choose proxy type. SOCKS5 handles all traffic through a single proxy. | string (choice) | `none` |
| `HTTP_PROXY` | HTTP proxy URL (e.g., http://proxy:8080) | string | _none_ |
| `HTTPS_PROXY` | HTTPS proxy URL (leave empty to use HTTP proxy for HTTPS) | string | _none_ |
| `SOCKS5_PROXY` | SOCKS5 proxy URL. Supports auth: socks5://user:pass@host:port | string | _none_ |
| `NO_PROXY` | Comma-separated hosts to bypass proxy (e.g., localhost,127.0.0.1,10.*,*.local) | string | _none_ |

<details>
<summary>Detailed descriptions</summary>

#### `CERTIFICATE_VALIDATION`

**Certificate Validation**

Controls SSL/TLS certificate verification for outbound connections. Disable for self-signed certificates on internal services (e.g. OIDC providers, Prowlarr).

- **Type:** string (choice)
- **Default:** `enabled`
- **Options:** `enabled` (Enabled (Recommended)), `disabled_local` (Disabled for Local Addresses), `disabled` (Disabled)

#### `CUSTOM_DNS`

**DNS Provider**

DNS provider for domain resolution. 'Auto' rotates through providers on failure.

- **Type:** string (choice)
- **Default:** `auto`
- **Options:** `auto` (Auto (Recommended)), `system` (System), `google` (Google), `cloudflare` (Cloudflare), `quad9` (Quad9), `opendns` (OpenDNS), `manual` (Manual)

#### `CUSTOM_DNS_MANUAL`

**Manual DNS Servers**

Comma-separated list of DNS server IP addresses (e.g., 8.8.8.8, 1.1.1.1).

- **Type:** string
- **Default:** _none_

#### `USE_DOH`

**Use DNS over HTTPS**

Use encrypted DNS queries for improved reliability and privacy.

- **Type:** boolean
- **Default:** `true`

#### `USING_TOR`

**Tor Routing**

Route all traffic through Tor for enhanced privacy. Requires root startup.

- **Type:** boolean
- **Default:** `false`

#### `PROXY_MODE`

**Proxy Mode**

Choose proxy type. SOCKS5 handles all traffic through a single proxy.

- **Type:** string (choice)
- **Default:** `none`
- **Options:** `none` (None (Direct Connection)), `http` (HTTP/HTTPS Proxy), `socks5` (SOCKS5 Proxy)

#### `HTTP_PROXY`

**HTTP Proxy**

HTTP proxy URL (e.g., http://proxy:8080)

- **Type:** string
- **Default:** _none_

#### `HTTPS_PROXY`

**HTTPS Proxy**

HTTPS proxy URL (leave empty to use HTTP proxy for HTTPS)

- **Type:** string
- **Default:** _none_

#### `SOCKS5_PROXY`

**SOCKS5 Proxy**

SOCKS5 proxy URL. Supports auth: socks5://user:pass@host:port

- **Type:** string
- **Default:** _none_

#### `NO_PROXY`

**No Proxy**

Comma-separated hosts to bypass proxy (e.g., localhost,127.0.0.1,10.*,*.local)

- **Type:** string
- **Default:** _none_

</details>

## Advanced

| Variable | Description | Type | Default |
|----------|-------------|------|---------|
| `URL_BASE` | Optional URL path prefix. Use a path like /shelfmark (no hostname). Leave blank for root. | string | _none_ |
| `DEBUG` | Enable verbose logging to console and file. Not recommended for normal use. | boolean | `false` |
| `LOG_LEVEL` | Lowest severity written to the console and log file. Ignored while Debug Mode is on, which forces Debug. | string (choice) | `INFO` |
| `MAIN_LOOP_SLEEP_TIME` | How often the download queue is checked for new items. | number | `5` |
| `DOWNLOAD_PROGRESS_UPDATE_INTERVAL` | How often download progress is broadcast to the UI. | number | `1` |
| `CUSTOM_SCRIPT` | Path to a script to run after each successful download. Must be executable. | string | _none_ |
| `CUSTOM_SCRIPT_PATH_MODE` | Pass the path to the custom script as an absolute path or relative to the destination folder. | string (choice) | `absolute` |
| `CUSTOM_SCRIPT_JSON_PAYLOAD` | Send a JSON payload to the script via stdin. Useful for multi-file imports (audiobooks) or richer metadata without relying on path parsing. | boolean | `false` |
| `DOWNLOAD_CLIENT_COMPLETED_PATH_TIMEOUT` | How long to wait after a torrent or usenet client reports completion for the completed file path to become visible to Shelfmark. Increase this for seedbox or remote-sync workflows. | number | `60` |
| `COVERS_CACHE_ENABLED` | Cache book covers on the server for faster loading. | boolean | `true` |
| `COVERS_CACHE_TTL` | How long to keep cached covers. Set to 0 to keep forever (recommended for static artwork). | number | `0` |
| `COVERS_CACHE_MAX_SIZE_MB` | Maximum disk space for cached covers. Oldest images are removed when limit is reached. | number | `500` |
| `METADATA_CACHE_ENABLED` | When disabled, all metadata searches hit the provider API directly. | boolean | `true` |
| `METADATA_CACHE_SEARCH_TTL` | How long to cache search results. Default: 300 (5 minutes). Max: 604800 (7 days). | number | `300` |
| `METADATA_CACHE_BOOK_TTL` | How long to cache individual book details. Default: 600 (10 minutes). Max: 604800 (7 days). | number | `600` |

<details>
<summary>Detailed descriptions</summary>

#### `URL_BASE`

**Base Path**

Optional URL path prefix. Use a path like /shelfmark (no hostname). Leave blank for root.

- **Type:** string
- **Default:** _none_
- **Requires restart:** Yes

#### `DEBUG`

**Debug Mode**

Enable verbose logging to console and file. Not recommended for normal use.

- **Type:** boolean
- **Default:** `false`
- **Requires restart:** Yes

#### `LOG_LEVEL`

**Log Level**

Lowest severity written to the console and log file. Ignored while Debug Mode is on, which forces Debug.

- **Type:** string (choice)
- **Default:** `INFO`
- **Requires restart:** Yes
- **Options:** `DEBUG` (Debug), `INFO` (Info), `WARNING` (Warning), `ERROR` (Error), `CRITICAL` (Critical)

#### `MAIN_LOOP_SLEEP_TIME`

**Queue Check Interval (seconds)**

How often the download queue is checked for new items.

- **Type:** number
- **Default:** `5`
- **Requires restart:** Yes
- **Constraints:** min: 1, max: 60

#### `DOWNLOAD_PROGRESS_UPDATE_INTERVAL`

**Progress Update Interval (seconds)**

How often download progress is broadcast to the UI.

- **Type:** number
- **Default:** `1`
- **Requires restart:** Yes
- **Constraints:** min: 1, max: 10

#### `CUSTOM_SCRIPT`

**Custom Script Path**

Path to a script to run after each successful download. Must be executable.

- **Type:** string
- **Default:** _none_

#### `CUSTOM_SCRIPT_PATH_MODE`

**Custom Script Path Mode**

Pass the path to the custom script as an absolute path or relative to the destination folder.

- **Type:** string (choice)
- **Default:** `absolute`
- **Options:** `absolute` (Absolute), `relative` (Relative)

#### `CUSTOM_SCRIPT_JSON_PAYLOAD`

**Custom Script JSON Payload**

Send a JSON payload to the script via stdin. Useful for multi-file imports (audiobooks) or richer metadata without relying on path parsing.

- **Type:** boolean
- **Default:** `false`

#### `DOWNLOAD_CLIENT_COMPLETED_PATH_TIMEOUT`

**Completed Path Wait (seconds)**

How long to wait after a torrent or usenet client reports completion for the completed file path to become visible to Shelfmark. Increase this for seedbox or remote-sync workflows.

- **Type:** number
- **Default:** `60`
- **Constraints:** min: 0, max: 3600

#### `COVERS_CACHE_ENABLED`

**Enable Cover Cache**

Cache book covers on the server for faster loading.

- **Type:** boolean
- **Default:** `true`

#### `COVERS_CACHE_TTL`

**Cache TTL (days)**

How long to keep cached covers. Set to 0 to keep forever (recommended for static artwork).

- **Type:** number
- **Default:** `0`
- **Constraints:** min: 0, max: 365

#### `COVERS_CACHE_MAX_SIZE_MB`

**Max Cache Size (MB)**

Maximum disk space for cached covers. Oldest images are removed when limit is reached.

- **Type:** number
- **Default:** `500`
- **Constraints:** min: 50, max: 5000

#### `METADATA_CACHE_ENABLED`

**Enable Metadata Caching**

When disabled, all metadata searches hit the provider API directly.

- **Type:** boolean
- **Default:** `true`

#### `METADATA_CACHE_SEARCH_TTL`

**Search Results Cache (seconds)**

How long to cache search results. Default: 300 (5 minutes). Max: 604800 (7 days).

- **Type:** number
- **Default:** `300`
- **Constraints:** min: 60, max: 604800

#### `METADATA_CACHE_BOOK_TTL`

**Book Details Cache (seconds)**

How long to cache individual book details. Default: 600 (10 minutes). Max: 604800 (7 days).

- **Type:** number
- **Default:** `600`
- **Constraints:** min: 60, max: 604800

</details>

## Prowlarr

| Variable | Description | Type | Default |
|----------|-------------|------|---------|
| `PROWLARR_ENABLED` | Enable searching for books via Prowlarr indexers | boolean | `false` |
| `PROWLARR_URL` | Base URL of your Prowlarr instance | string | _none_ |
| `PROWLARR_API_KEY` | Found in Prowlarr: Settings > General > API Key | string (secret) | _none_ |
| `PROWLARR_INDEXERS` | Select which indexers to search. 📚 = has book categories. Leave empty to search all. | string (comma-separated) | _empty list_ |
| `PROWLARR_INDEXER_TIMEOUT` | How long to wait for a single indexer to answer a search. Indexers behind FlareSolverr can need 90 seconds or more while a cold Cloudflare challenge is solved; raise this if searches come back empty and the Prowlarr log shows the search still running. | number | `90` |
| `PROWLARR_AUTO_EXPAND` | Automatically retry search without category filtering if no results are found | boolean | `false` |
| `PROWLARR_COLLAPSE_DUPLICATES` | Collapse a release that several indexer entries returned down to a single row, keeping the entry with the best Prowlarr priority. Turn this off to see every entry that carried it, which is what makes results from filter-specific entries (freeleech and the like) visible. | boolean | `true` |
| `PROWLARR_USE_SEED_PREFERENCES` | Apply per-indexer seed time and ratio preferences from Prowlarr when sending torrents to the download client | boolean | `false` |

<details>
<summary>Detailed descriptions</summary>

#### `PROWLARR_ENABLED`

**Enable Prowlarr source**

Enable searching for books via Prowlarr indexers

- **Type:** boolean
- **Default:** `false`

#### `PROWLARR_URL`

**Prowlarr URL**

Base URL of your Prowlarr instance

- **Type:** string
- **Default:** _none_
- **Required:** Yes

#### `PROWLARR_API_KEY`

**API Key**

Found in Prowlarr: Settings > General > API Key

- **Type:** string (secret)
- **Default:** _none_
- **Required:** Yes

#### `PROWLARR_INDEXERS`

**Indexers to Search**

Select which indexers to search. 📚 = has book categories. Leave empty to search all.

- **Type:** string (comma-separated)
- **Default:** _empty list_

#### `PROWLARR_INDEXER_TIMEOUT`

**Indexer Search Timeout (seconds)**

How long to wait for a single indexer to answer a search. Indexers behind FlareSolverr can need 90 seconds or more while a cold Cloudflare challenge is solved; raise this if searches come back empty and the Prowlarr log shows the search still running.

- **Type:** number
- **Default:** `90`
- **Constraints:** min: 5, max: 300

#### `PROWLARR_AUTO_EXPAND`

**Auto-expand search on no results**

Automatically retry search without category filtering if no results are found

- **Type:** boolean
- **Default:** `false`

#### `PROWLARR_COLLAPSE_DUPLICATES`

**Show one row per release**

Collapse a release that several indexer entries returned down to a single row, keeping the entry with the best Prowlarr priority. Turn this off to see every entry that carried it, which is what makes results from filter-specific entries (freeleech and the like) visible.

- **Type:** boolean
- **Default:** `true`

#### `PROWLARR_USE_SEED_PREFERENCES`

**Use Prowlarr seed preferences**

Apply per-indexer seed time and ratio preferences from Prowlarr when sending torrents to the download client

- **Type:** boolean
- **Default:** `false`

</details>

## Newznab

| Variable | Description | Type | Default |
|----------|-------------|------|---------|
| `NEWZNAB_ENABLED` | Enable searching for books via a Newznab-compatible indexer | boolean | `false` |
| `NEWZNAB_INDEXERS` | Named Newznab connections. Each row accepts `name`, `url`, and `api_key`. | JSON array | `[]` |
| `NEWZNAB_URL` | Legacy single-indexer URL, used when `NEWZNAB_INDEXERS` is empty | string | _none_ |
| `NEWZNAB_API_KEY` | Legacy single-indexer API key | string (secret) | _none_ |
| `NEWZNAB_EBOOK_CATEGORIES` | Newznab category IDs searched for ebooks. Most indexers use the standard 7000, but some use custom IDs. Leave empty to use 7000. | string (comma-separated) | `7000` |
| `NEWZNAB_AUDIOBOOK_CATEGORIES` | Newznab category IDs searched for audiobooks. Most indexers use the standard 3030, but some use custom IDs. Leave empty to use 3030. | string (comma-separated) | `3030` |
| `NEWZNAB_AUTO_EXPAND` | Automatically retry search without category filtering if no results are found | boolean | `false` |

<details>
<summary>Detailed descriptions</summary>

#### `NEWZNAB_ENABLED`

**Enable Newznab source**

Enable searching for books via a Newznab-compatible indexer

- **Type:** boolean
- **Default:** `false`

#### `NEWZNAB_INDEXERS`

**Named Indexers**

Configure multiple named Newznab-compatible indexers. The name is shown beside each search result. For environment-based configuration, provide a JSON array:

```json
[
  {"name":"NZBGeek","url":"https://api.nzbgeek.info","api_key":"..."},
  {"name":"DrunkenSlug","url":"https://drunkenslug.com","api_key":"..."}
]
```

- **Type:** JSON array
- **Default:** `[]`

#### `NEWZNAB_URL`

**Legacy Newznab URL**

Single-indexer fallback used only when `NEWZNAB_INDEXERS` is empty.

- **Type:** string
- **Default:** _none_

#### `NEWZNAB_API_KEY`

**Legacy API Key**

API key for the legacy Newznab URL.

- **Type:** string (secret)
- **Default:** _none_

#### `NEWZNAB_EBOOK_CATEGORIES`

**Ebook Categories**

Newznab category IDs searched for ebooks. Most indexers use the standard 7000, but some use custom IDs. Leave empty to use 7000.

- **Type:** string (comma-separated)
- **Default:** `7000`

#### `NEWZNAB_AUDIOBOOK_CATEGORIES`

**Audiobook Categories**

Newznab category IDs searched for audiobooks. Most indexers use the standard 3030, but some use custom IDs. Leave empty to use 3030.

- **Type:** string (comma-separated)
- **Default:** `3030`

#### `NEWZNAB_AUTO_EXPAND`

**Auto-expand search on no results**

Automatically retry search without category filtering if no results are found

- **Type:** boolean
- **Default:** `false`

</details>

## AudiobookBay

| Variable | Description | Type | Default |
|----------|-------------|------|---------|
| `ABB_ENABLED` | Enable AudiobookBay as a release source for audiobooks. | boolean | `false` |
| `ABB_HOSTNAME` | AudiobookBay domain (e.g., audiobookbay.lu, audiobookbay.is). Required to enable searches. | string | _empty string_ |
| `ABB_PAGE_LIMIT` | Maximum number of search result pages to fetch (1-10). | number | `1` |
| `ABB_EXACT_PHRASE` | Wrap generated queries in quotes for stricter matching. If no results are found, Shelfmark retries without quotes. | boolean | `false` |
| `ABB_RATE_LIMIT_DELAY` | Delay between requests in seconds to avoid rate limiting (0-10). | number | `1.0` |

<details>
<summary>Detailed descriptions</summary>

#### `ABB_ENABLED`

**Enable AudiobookBay**

Enable AudiobookBay as a release source for audiobooks.

- **Type:** boolean
- **Default:** `false`

#### `ABB_HOSTNAME`

**Hostname**

AudiobookBay domain (e.g., audiobookbay.lu, audiobookbay.is). Required to enable searches.

- **Type:** string
- **Default:** _empty string_
- **Required:** Yes

#### `ABB_PAGE_LIMIT`

**Max Pages to Search**

Maximum number of search result pages to fetch (1-10).

- **Type:** number
- **Default:** `1`
- **Constraints:** min: 1, max: 10

#### `ABB_EXACT_PHRASE`

**Prefer Exact-Phrase Search**

Wrap generated queries in quotes for stricter matching. If no results are found, Shelfmark retries without quotes.

- **Type:** boolean
- **Default:** `false`

#### `ABB_RATE_LIMIT_DELAY`

**Rate Limit Delay (seconds)**

Delay between requests in seconds to avoid rate limiting (0-10).

- **Type:** number
- **Default:** `1.0`
- **Constraints:** min: 0.0, max: 10.0

</details>

## IRC

| Variable | Description | Type | Default |
|----------|-------------|------|---------|
| `IRC_SERVER` | IRC server hostname | string | _none_ |
| `IRC_PORT` | IRC server port (usually 6697 for TLS, 6667 for plain) | number | `6697` |
| `IRC_USE_TLS` | Enable TLS/SSL encryption for the IRC connection. Disable for servers that don't support TLS. | boolean | `true` |
| `IRC_CHANNEL` | Channel name without the # prefix. Used for all searches unless a separate audiobook channel is configured below. | string | _none_ |
| `IRC_NICK` | Your IRC nickname (required). Must be unique on the IRC network. | string | _none_ |
| `IRC_SEARCH_BOT` | The search bot to address queries to (required). Searches are sent as "@<bot> <query>". | string | _none_ |
| `IRC_AUDIOBOOK_CHANNEL` | Optional. Channel name (without the # prefix) for networks that index audiobooks separately, such as Undernet's bookz. Leave blank (the usual setting) to search the main channel above for audiobooks too. | string | _none_ |
| `IRC_AUDIOBOOK_SEARCH_BOT` | Optional. Search bot for the audiobook channel. Leave blank to reuse the main search bot above. Only used when an audiobook channel is set. | string | _none_ |
| `IRC_CACHE_TTL` | How long to keep cached search results before they expire. | string (choice) | `2592000` |

<details>
<summary>Detailed descriptions</summary>

#### `IRC_SERVER`

**Server**

IRC server hostname

- **Type:** string
- **Default:** _none_
- **Required:** Yes

#### `IRC_PORT`

**Port**

IRC server port (usually 6697 for TLS, 6667 for plain)

- **Type:** number
- **Default:** `6697`

#### `IRC_USE_TLS`

**Use TLS**

Enable TLS/SSL encryption for the IRC connection. Disable for servers that don't support TLS.

- **Type:** boolean
- **Default:** `true`

#### `IRC_CHANNEL`

**Channel**

Channel name without the # prefix. Used for all searches unless a separate audiobook channel is configured below.

- **Type:** string
- **Default:** _none_
- **Required:** Yes

#### `IRC_NICK`

**Nickname**

Your IRC nickname (required). Must be unique on the IRC network.

- **Type:** string
- **Default:** _none_
- **Required:** Yes

#### `IRC_SEARCH_BOT`

**Search bot**

The search bot to address queries to (required). Searches are sent as "@<bot> <query>".

- **Type:** string
- **Default:** _none_
- **Required:** Yes

#### `IRC_AUDIOBOOK_CHANNEL`

**Audiobook channel**

Optional. Channel name (without the # prefix) for networks that index audiobooks separately, such as Undernet's bookz. Leave blank (the usual setting) to search the main channel above for audiobooks too.

- **Type:** string
- **Default:** _none_

#### `IRC_AUDIOBOOK_SEARCH_BOT`

**Audiobook search bot**

Optional. Search bot for the audiobook channel. Leave blank to reuse the main search bot above. Only used when an audiobook channel is set.

- **Type:** string
- **Default:** _none_

#### `IRC_CACHE_TTL`

**Cache Duration**

How long to keep cached search results before they expire.

- **Type:** string (choice)
- **Default:** `2592000`
- **Options:** `2592000` (30 days), `0` (Forever (until manually cleared))

</details>

## Download Clients

| Variable | Description | Type | Default |
|----------|-------------|------|---------|
| `PROWLARR_TORRENT_CLIENT` | Choose which torrent client to use | string (choice) | _empty string_ |
| `ALLDEBRID_API_KEY` | AllDebrid API Key (apiv4) from your AllDebrid account settings | string (secret) | _none_ |
| `REALDEBRID_API_KEY` | Real-Debrid API Key (Secret Token) from your Real-Debrid account settings | string (secret) | _none_ |
| `QBITTORRENT_URL` | Web UI URL of your qBittorrent instance | string | _none_ |
| `QBITTORRENT_USERNAME` | qBittorrent Web UI username | string | _none_ |
| `QBITTORRENT_PASSWORD` | qBittorrent Web UI password | string (secret) | _none_ |
| `QBITTORRENT_API_KEY` | Found in qBittorrent: Options > Web UI > API Key (qBittorrent 5.2.0+). Used instead of the username and password when set. | string (secret) | _none_ |
| `QBITTORRENT_CATEGORY` | Category to assign to book downloads in qBittorrent | string | `books` |
| `QBITTORRENT_CATEGORY_AUDIOBOOK` | Category for audiobook downloads. Leave empty to use the book category. | string | _empty string_ |
| `QBITTORRENT_DOWNLOAD_DIR` | Server-side directory where torrents are downloaded (optional, uses qBittorrent default if not specified) | string | _none_ |
| `QBITTORRENT_TAG` | Tag(s) to assign to qBittorrent downloads. Leave empty for no tags. | string (comma-separated) | _empty list_ |
| `TRANSMISSION_URL` | URL of your Transmission instance (use https:// for TLS) | string | _none_ |
| `TRANSMISSION_USERNAME` | Transmission RPC username (if authentication enabled) | string | _none_ |
| `TRANSMISSION_PASSWORD` | Transmission RPC password | string (secret) | _none_ |
| `TRANSMISSION_CATEGORY` | Label to assign to book downloads in Transmission | string | `books` |
| `TRANSMISSION_CATEGORY_AUDIOBOOK` | Label for audiobook downloads. Leave empty to use the book label. | string | _empty string_ |
| `TRANSMISSION_DOWNLOAD_DIR` | Server-side directory where torrents are downloaded (optional, uses Transmission default if not specified) | string | _none_ |
| `DELUGE_HOST` | Hostname/IP or full URL of your Deluge Web UI (deluge-web) | string | `localhost` |
| `DELUGE_PORT` | Deluge Web UI port (default: 8112) | string | `8112` |
| `DELUGE_PASSWORD` | Deluge Web UI password (default: deluge) | string (secret) | _none_ |
| `DELUGE_CATEGORY` | Label to assign to book downloads in Deluge | string | `books` |
| `DELUGE_CATEGORY_AUDIOBOOK` | Label for audiobook downloads. Leave empty to use the book label. | string | _empty string_ |
| `DELUGE_DOWNLOAD_DIR` | Server-side directory where torrents are downloaded (optional, uses Deluge default if not specified) | string | _none_ |
| `RTORRENT_URL` | XML-RPC URL of your rTorrent instance | string | _none_ |
| `RTORRENT_USERNAME` | HTTP Basic auth username (if authentication enabled) | string | _none_ |
| `RTORRENT_PASSWORD` | HTTP Basic auth password | string (secret) | _none_ |
| `RTORRENT_LABEL` | Label to assign to ebook downloads in rTorrent | string | `cwabd` |
| `RTORRENT_AUDIOBOOK_LABEL` | Label to assign to audiobook downloads in rTorrent (falls back to Book Label if not set) | string | _none_ |
| `RTORRENT_DOWNLOAD_DIR` | Server-side directory where torrents are downloaded (optional, uses rTorrent default if not specified) | string | _none_ |
| `PROWLARR_TORRENT_ACTION` | Choose whether to keep, remove, or move the torrent to another category or label after import | string (choice) | `keep` |
| `PROWLARR_TORRENT_POST_IMPORT_CATEGORY` | Category or label to assign after a successful import | string | _empty string_ |
| `PROWLARR_USENET_CLIENT` | Choose which usenet client to use | string (choice) | _empty string_ |
| `NZBGET_URL` | URL of your NZBGet instance | string | _none_ |
| `NZBGET_USERNAME` | NZBGet control username | string | `nzbget` |
| `NZBGET_PASSWORD` | NZBGet control password | string (secret) | _none_ |
| `NZBGET_CATEGORY` | Category to assign to book downloads in NZBGet | string | `Books` |
| `NZBGET_CATEGORY_AUDIOBOOK` | Category for audiobook downloads. Leave empty to use the book category. | string | _empty string_ |
| `SABNZBD_URL` | URL of your SABnzbd instance | string | _none_ |
| `SABNZBD_API_KEY` | Found in SABnzbd: Config > General > API Key | string (secret) | _none_ |
| `SABNZBD_CATEGORY` | Category to assign to book downloads in SABnzbd | string | `books` |
| `SABNZBD_CATEGORY_AUDIOBOOK` | Category for audiobook downloads. Leave empty to use the book category. | string | _empty string_ |
| `PROWLARR_USENET_ACTION` | Move deletes the job from your usenet client after import; Copy keeps it in the client | string (choice) | `move` |

<details>
<summary>Detailed descriptions</summary>

#### `PROWLARR_TORRENT_CLIENT`

**Torrent Client**

Choose which torrent client to use

- **Type:** string (choice)
- **Default:** _empty string_
- **Options:** `""` (None), `alldebrid` (AllDebrid), `qbittorrent` (qBittorrent), `realdebrid` (Real-Debrid), `transmission` (Transmission), `deluge` (Deluge), `rtorrent` (rTorrent)

#### `ALLDEBRID_API_KEY`

**API Key**

AllDebrid API Key (apiv4) from your AllDebrid account settings

- **Type:** string (secret)
- **Default:** _none_

#### `REALDEBRID_API_KEY`

**API Key**

Real-Debrid API Key (Secret Token) from your Real-Debrid account settings

- **Type:** string (secret)
- **Default:** _none_

#### `QBITTORRENT_URL`

**qBittorrent URL**

Web UI URL of your qBittorrent instance

- **Type:** string
- **Default:** _none_

#### `QBITTORRENT_USERNAME`

**Username**

qBittorrent Web UI username

- **Type:** string
- **Default:** _none_

#### `QBITTORRENT_PASSWORD`

**Password**

qBittorrent Web UI password

- **Type:** string (secret)
- **Default:** _none_

#### `QBITTORRENT_API_KEY`

**API Key**

Found in qBittorrent: Options > Web UI > API Key (qBittorrent 5.2.0+). Used instead of the username and password when set.

- **Type:** string (secret)
- **Default:** _none_

#### `QBITTORRENT_CATEGORY`

**Book Category**

Category to assign to book downloads in qBittorrent

- **Type:** string
- **Default:** `books`

#### `QBITTORRENT_CATEGORY_AUDIOBOOK`

**Audiobook Category**

Category for audiobook downloads. Leave empty to use the book category.

- **Type:** string
- **Default:** _empty string_

#### `QBITTORRENT_DOWNLOAD_DIR`

**Download Directory**

Server-side directory where torrents are downloaded (optional, uses qBittorrent default if not specified)

- **Type:** string
- **Default:** _none_

#### `QBITTORRENT_TAG`

**Tags**

Tag(s) to assign to qBittorrent downloads. Leave empty for no tags.

- **Type:** string (comma-separated)
- **Default:** _empty list_

#### `TRANSMISSION_URL`

**Transmission URL**

URL of your Transmission instance (use https:// for TLS)

- **Type:** string
- **Default:** _none_

#### `TRANSMISSION_USERNAME`

**Username**

Transmission RPC username (if authentication enabled)

- **Type:** string
- **Default:** _none_

#### `TRANSMISSION_PASSWORD`

**Password**

Transmission RPC password

- **Type:** string (secret)
- **Default:** _none_

#### `TRANSMISSION_CATEGORY`

**Book Label**

Label to assign to book downloads in Transmission

- **Type:** string
- **Default:** `books`

#### `TRANSMISSION_CATEGORY_AUDIOBOOK`

**Audiobook Label**

Label for audiobook downloads. Leave empty to use the book label.

- **Type:** string
- **Default:** _empty string_

#### `TRANSMISSION_DOWNLOAD_DIR`

**Download Directory**

Server-side directory where torrents are downloaded (optional, uses Transmission default if not specified)

- **Type:** string
- **Default:** _none_

#### `DELUGE_HOST`

**Deluge Web UI Host/URL**

Hostname/IP or full URL of your Deluge Web UI (deluge-web)

- **Type:** string
- **Default:** `localhost`

#### `DELUGE_PORT`

**Deluge Web UI Port**

Deluge Web UI port (default: 8112)

- **Type:** string
- **Default:** `8112`

#### `DELUGE_PASSWORD`

**Password**

Deluge Web UI password (default: deluge)

- **Type:** string (secret)
- **Default:** _none_

#### `DELUGE_CATEGORY`

**Book Label**

Label to assign to book downloads in Deluge

- **Type:** string
- **Default:** `books`

#### `DELUGE_CATEGORY_AUDIOBOOK`

**Audiobook Label**

Label for audiobook downloads. Leave empty to use the book label.

- **Type:** string
- **Default:** _empty string_

#### `DELUGE_DOWNLOAD_DIR`

**Download Directory**

Server-side directory where torrents are downloaded (optional, uses Deluge default if not specified)

- **Type:** string
- **Default:** _none_

#### `RTORRENT_URL`

**rTorrent URL**

XML-RPC URL of your rTorrent instance

- **Type:** string
- **Default:** _none_

#### `RTORRENT_USERNAME`

**Username**

HTTP Basic auth username (if authentication enabled)

- **Type:** string
- **Default:** _none_

#### `RTORRENT_PASSWORD`

**Password**

HTTP Basic auth password

- **Type:** string (secret)
- **Default:** _none_

#### `RTORRENT_LABEL`

**Book Label**

Label to assign to ebook downloads in rTorrent

- **Type:** string
- **Default:** `cwabd`

#### `RTORRENT_AUDIOBOOK_LABEL`

**Audiobook Label**

Label to assign to audiobook downloads in rTorrent (falls back to Book Label if not set)

- **Type:** string
- **Default:** _none_

#### `RTORRENT_DOWNLOAD_DIR`

**Download Directory**

Server-side directory where torrents are downloaded (optional, uses rTorrent default if not specified)

- **Type:** string
- **Default:** _none_

#### `PROWLARR_TORRENT_ACTION`

**Torrent Completion Action**

Choose whether to keep, remove, or move the torrent to another category or label after import

- **Type:** string (choice)
- **Default:** `keep`
- **Options:** `keep` (Keep), `remove` (Remove), `change_category` (Change Category)

#### `PROWLARR_TORRENT_POST_IMPORT_CATEGORY`

**Post-Import Category**

Category or label to assign after a successful import

- **Type:** string
- **Default:** _empty string_

#### `PROWLARR_USENET_CLIENT`

**Usenet Client**

Choose which usenet client to use

- **Type:** string (choice)
- **Default:** _empty string_
- **Options:** `""` (None), `nzbget` (NZBGet), `sabnzbd` (SABnzbd)

#### `NZBGET_URL`

**NZBGet URL**

URL of your NZBGet instance

- **Type:** string
- **Default:** _none_

#### `NZBGET_USERNAME`

**Username**

NZBGet control username

- **Type:** string
- **Default:** `nzbget`

#### `NZBGET_PASSWORD`

**Password**

NZBGet control password

- **Type:** string (secret)
- **Default:** _none_

#### `NZBGET_CATEGORY`

**Book Category**

Category to assign to book downloads in NZBGet

- **Type:** string
- **Default:** `Books`

#### `NZBGET_CATEGORY_AUDIOBOOK`

**Audiobook Category**

Category for audiobook downloads. Leave empty to use the book category.

- **Type:** string
- **Default:** _empty string_

#### `SABNZBD_URL`

**SABnzbd URL**

URL of your SABnzbd instance

- **Type:** string
- **Default:** _none_

#### `SABNZBD_API_KEY`

**API Key**

Found in SABnzbd: Config > General > API Key

- **Type:** string (secret)
- **Default:** _none_

#### `SABNZBD_CATEGORY`

**Book Category**

Category to assign to book downloads in SABnzbd

- **Type:** string
- **Default:** `books`

#### `SABNZBD_CATEGORY_AUDIOBOOK`

**Audiobook Category**

Category for audiobook downloads. Leave empty to use the book category.

- **Type:** string
- **Default:** _empty string_

#### `PROWLARR_USENET_ACTION`

**NZB Completion Action**

Move deletes the job from your usenet client after import; Copy keeps it in the client

- **Type:** string (choice)
- **Default:** `move`
- **Options:** `move` (Move), `copy` (Copy)

</details>

## Metadata Providers

### Metadata Providers: Hardcover

| Variable | Description | Type | Default |
|----------|-------------|------|---------|
| `HARDCOVER_ENABLED` | Enable Hardcover as a metadata provider for book searches | boolean | `false` |
| `HARDCOVER_API_KEY` | Get your API key from hardcover.app/account/api (starts with hc_pat_) | string (secret) | _none_ |
| `HARDCOVER_DEFAULT_SORT` | Default sort order for Hardcover search results. | string (choice) | `relevance` |
| `HARDCOVER_EXCLUDE_COMPILATIONS` | Filter out compilations, anthologies, and omnibus editions from search results | boolean | `false` |
| `HARDCOVER_EXCLUDE_UNRELEASED` | Filter out books with a release year in the future | boolean | `false` |
| `HARDCOVER_AUTO_REMOVE_ON_DOWNLOAD` | Automatically remove a book from the active Hardcover list when you download it | boolean | `true` |

<details>
<summary>Detailed descriptions</summary>

#### `HARDCOVER_ENABLED`

**Enable Hardcover**

Enable Hardcover as a metadata provider for book searches

- **Type:** boolean
- **Default:** `false`

#### `HARDCOVER_API_KEY`

**API Key**

Get your API key from hardcover.app/account/api (starts with hc_pat_)

- **Type:** string (secret)
- **Default:** _none_
- **Required:** Yes

#### `HARDCOVER_DEFAULT_SORT`

**Default Sort Order**

Default sort order for Hardcover search results.

- **Type:** string (choice)
- **Default:** `relevance`
- **Options:** `relevance` (Most relevant), `popularity` (Most popular), `rating` (Highest rated), `newest` (Newest), `oldest` (Oldest)

#### `HARDCOVER_EXCLUDE_COMPILATIONS`

**Exclude Compilations**

Filter out compilations, anthologies, and omnibus editions from search results

- **Type:** boolean
- **Default:** `false`

#### `HARDCOVER_EXCLUDE_UNRELEASED`

**Exclude Unreleased Books**

Filter out books with a release year in the future

- **Type:** boolean
- **Default:** `false`

#### `HARDCOVER_AUTO_REMOVE_ON_DOWNLOAD`

**Auto-Remove from List on Download**

Automatically remove a book from the active Hardcover list when you download it

- **Type:** boolean
- **Default:** `true`

</details>

### Metadata Providers: Open Library

| Variable | Description | Type | Default |
|----------|-------------|------|---------|
| `OPENLIBRARY_ENABLED` | Enable Open Library as a metadata provider for book searches | boolean | `false` |
| `OPENLIBRARY_DEFAULT_SORT` | Default sort order for Open Library search results. | string (choice) | `relevance` |

<details>
<summary>Detailed descriptions</summary>

#### `OPENLIBRARY_ENABLED`

**Enable Open Library**

Enable Open Library as a metadata provider for book searches

- **Type:** boolean
- **Default:** `false`

#### `OPENLIBRARY_DEFAULT_SORT`

**Default Sort Order**

Default sort order for Open Library search results.

- **Type:** string (choice)
- **Default:** `relevance`
- **Options:** `relevance` (Most relevant), `newest` (Newest), `oldest` (Oldest)

</details>

### Metadata Providers: Google Books

| Variable | Description | Type | Default |
|----------|-------------|------|---------|
| `GOOGLEBOOKS_ENABLED` | Enable Google Books as a metadata provider for book searches | boolean | `false` |
| `GOOGLEBOOKS_API_KEY` | Get your API key from Google Cloud Console (APIs & Services > Credentials) | string (secret) | _none_ |
| `GOOGLEBOOKS_DEFAULT_SORT` | Default sort order for Google Books search results. | string (choice) | `relevance` |

<details>
<summary>Detailed descriptions</summary>

#### `GOOGLEBOOKS_ENABLED`

**Enable Google Books**

Enable Google Books as a metadata provider for book searches

- **Type:** boolean
- **Default:** `false`

#### `GOOGLEBOOKS_API_KEY`

**API Key**

Get your API key from Google Cloud Console (APIs & Services > Credentials)

- **Type:** string (secret)
- **Default:** _none_
- **Required:** Yes

#### `GOOGLEBOOKS_DEFAULT_SORT`

**Default Sort Order**

Default sort order for Google Books search results.

- **Type:** string (choice)
- **Default:** `relevance`
- **Options:** `relevance` (Most relevant), `newest` (Newest)

</details>

### Metadata Providers: Moly.hu

| Variable | Description | Type | Default |
|----------|-------------|------|---------|
| `MOLY_ENABLED` | Enable Moly.hu as a metadata provider for book searches | boolean | `false` |

<details>
<summary>Detailed descriptions</summary>

#### `MOLY_ENABLED`

**Enable Moly.hu**

Enable Moly.hu as a metadata provider for book searches

- **Type:** boolean
- **Default:** `false`

</details>

## Direct Download

### Direct Download: Download Sources

| Variable | Description | Type | Default |
|----------|-------------|------|---------|
| `DIRECT_DOWNLOAD_ENABLED` | Show Direct Download in release-source lists and allow Direct mode searches. Add your own mirror URLs in the Mirrors tab before using it. | boolean | `false` |
| `DIRECT_DOWNLOAD_LANGUAGE_FROM_PATH` | When language metadata is missing or unknown, parse the distant path (file path shown in search results) for language tags like [BD FR] or [En]. Also enables local language filtering so lgli files without AA language metadata are not excluded before the distant path can be checked. | boolean | `false` |
| `AA_DONATOR_KEY` | Enables fast download access on AA. Get this from your donator account page. | string (secret) | _none_ |
| `FAST_SOURCES_DISPLAY` | Always tried first, no waiting or bypass required. | JSON array | _see UI for defaults_ |
| `SOURCE_PRIORITY` | Fallback sources, may have waiting. Requires bypasser. Drag to reorder. | JSON array | _see UI for defaults_ |
| `MAX_RETRY` | Maximum retry attempts for failed downloads. | number | `10` |
| `DEFAULT_SLEEP` | Wait time between download retry attempts. | number | `5` |
| `RELEASE_SEARCH_TIMEOUT` | How long one release search may run before it gives up and reports why. A first search on a cold start pays for a browser solve, so leave room for one. If you use a reverse proxy, its read timeout should be at least this high or it will cut the search off with a 504 first. | number | `300` |
| `AA_CONTENT_TYPE_ROUTING` | Override destination based on content type metadata. | boolean | `false` |
| `AA_CONTENT_TYPE_DIR_FICTION` | Fiction Books | string | _none_ |
| `AA_CONTENT_TYPE_DIR_NON_FICTION` | Non-Fiction Books | string | _none_ |
| `AA_CONTENT_TYPE_DIR_UNKNOWN` | Unknown Books | string | _none_ |
| `AA_CONTENT_TYPE_DIR_MAGAZINE` | Magazines | string | _none_ |
| `AA_CONTENT_TYPE_DIR_COMIC` | Comic Books | string | _none_ |
| `AA_CONTENT_TYPE_DIR_STANDARDS` | Standards Documents | string | _none_ |
| `AA_CONTENT_TYPE_DIR_MUSICAL_SCORE` | Musical Scores | string | _none_ |
| `AA_CONTENT_TYPE_DIR_OTHER` | Other | string | _none_ |

<details>
<summary>Detailed descriptions</summary>

#### `DIRECT_DOWNLOAD_ENABLED`

**Enable Direct Download Source**

Show Direct Download in release-source lists and allow Direct mode searches. Add your own mirror URLs in the Mirrors tab before using it.

- **Type:** boolean
- **Default:** `false`

#### `DIRECT_DOWNLOAD_LANGUAGE_FROM_PATH`

**Detect Language From Distant Path**

When language metadata is missing or unknown, parse the distant path (file path shown in search results) for language tags like [BD FR] or [En]. Also enables local language filtering so lgli files without AA language metadata are not excluded before the distant path can be checked.

- **Type:** boolean
- **Default:** `false`

#### `AA_DONATOR_KEY`

**Account Donator Key**

Enables fast download access on AA. Get this from your donator account page.

- **Type:** string (secret)
- **Default:** _none_

#### `FAST_SOURCES_DISPLAY`

**Fast downloads**

Always tried first, no waiting or bypass required.

- **Type:** JSON array
- **Default:** _see UI for defaults_

#### `SOURCE_PRIORITY`

**Slow downloads**

Fallback sources, may have waiting. Requires bypasser. Drag to reorder.

- **Type:** JSON array
- **Default:** _see UI for defaults_

#### `MAX_RETRY`

**Max Retries**

Maximum retry attempts for failed downloads.

- **Type:** number
- **Default:** `10`
- **Constraints:** min: 1, max: 50

#### `DEFAULT_SLEEP`

**Retry Delay (seconds)**

Wait time between download retry attempts.

- **Type:** number
- **Default:** `5`
- **Constraints:** min: 1, max: 60

#### `RELEASE_SEARCH_TIMEOUT`

**Release Search Timeout (seconds)**

How long one release search may run before it gives up and reports why. A first search on a cold start pays for a browser solve, so leave room for one. If you use a reverse proxy, its read timeout should be at least this high or it will cut the search off with a 504 first.

- **Type:** number
- **Default:** `300`
- **Constraints:** min: 30, max: 1800

#### `AA_CONTENT_TYPE_ROUTING`

**Enable Content-Type Routing**

Override destination based on content type metadata.

- **Type:** boolean
- **Default:** `false`

#### `AA_CONTENT_TYPE_DIR_FICTION`

**Fiction Books**

- **Type:** string
- **Default:** _none_

#### `AA_CONTENT_TYPE_DIR_NON_FICTION`

**Non-Fiction Books**

- **Type:** string
- **Default:** _none_

#### `AA_CONTENT_TYPE_DIR_UNKNOWN`

**Unknown Books**

- **Type:** string
- **Default:** _none_

#### `AA_CONTENT_TYPE_DIR_MAGAZINE`

**Magazines**

- **Type:** string
- **Default:** _none_

#### `AA_CONTENT_TYPE_DIR_COMIC`

**Comic Books**

- **Type:** string
- **Default:** _none_

#### `AA_CONTENT_TYPE_DIR_STANDARDS`

**Standards Documents**

- **Type:** string
- **Default:** _none_

#### `AA_CONTENT_TYPE_DIR_MUSICAL_SCORE`

**Musical Scores**

- **Type:** string
- **Default:** _none_

#### `AA_CONTENT_TYPE_DIR_OTHER`

**Other**

- **Type:** string
- **Default:** _none_

</details>

### Direct Download: Cloudflare Bypass

| Variable | Description | Type | Default |
|----------|-------------|------|---------|
| `USE_CF_BYPASS` | Attempt to bypass Cloudflare protection on download sites. | boolean | `true` |
| `USING_EXTERNAL_BYPASSER` | Use FlareSolverr or similar external service instead of built-in bypasser. Caution: May have limitations with custom DNS, Tor and proxies. You may experience slower downloads and and poorer reliability compared to the internal bypasser. | boolean | `false` |
| `EXT_BYPASSER_URL` | URL of the external bypasser service (e.g., FlareSolverr). | string | `http://flaresolverr:8191` |
| `EXT_BYPASSER_PATH` | API path for the external bypasser. | string | `/v1` |
| `EXT_BYPASSER_TIMEOUT` | Timeout for external bypasser requests in milliseconds. | number | `60000` |
| `BYPASS_PAGE_SOURCE_TIMEOUT` | How long to wait for a solved page to produce its content before the bypass is retried. Raise it if solves succeed but searches still fail. | number | `20` |
| `BYPASS_BROWSER_IDLE_TIMEOUT` | How long the bypass helper process may sit unused before it is shut down. Higher keeps more searches fast, lower frees memory sooner. | number | `180` |

<details>
<summary>Detailed descriptions</summary>

#### `USE_CF_BYPASS`

**Enable Cloudflare Bypass**

Attempt to bypass Cloudflare protection on download sites.

- **Type:** boolean
- **Default:** `true`
- **Requires restart:** Yes

#### `USING_EXTERNAL_BYPASSER`

**Use External Bypasser**

Use FlareSolverr or similar external service instead of built-in bypasser. Caution: May have limitations with custom DNS, Tor and proxies. You may experience slower downloads and and poorer reliability compared to the internal bypasser.

- **Type:** boolean
- **Default:** `false`
- **Requires restart:** Yes

#### `EXT_BYPASSER_URL`

**External Bypasser URL**

URL of the external bypasser service (e.g., FlareSolverr).

- **Type:** string
- **Default:** `http://flaresolverr:8191`
- **Requires restart:** Yes

#### `EXT_BYPASSER_PATH`

**External Bypasser Path**

API path for the external bypasser.

- **Type:** string
- **Default:** `/v1`
- **Requires restart:** Yes

#### `EXT_BYPASSER_TIMEOUT`

**External Bypasser Timeout (ms)**

Timeout for external bypasser requests in milliseconds.

- **Type:** number
- **Default:** `60000`
- **Requires restart:** Yes
- **Constraints:** min: 10000, max: 300000

#### `BYPASS_PAGE_SOURCE_TIMEOUT`

**Page Read Timeout (seconds)**

How long to wait for a solved page to produce its content before the bypass is retried. Raise it if solves succeed but searches still fail.

- **Type:** number
- **Default:** `20`
- **Constraints:** min: 1, max: 120

#### `BYPASS_BROWSER_IDLE_TIMEOUT`

**Bypasser Idle Timeout (seconds)**

How long the bypass helper process may sit unused before it is shut down. Higher keeps more searches fast, lower frees memory sooner.

- **Type:** number
- **Default:** `180`
- **Requires restart:** Yes
- **Constraints:** min: 30, max: 3600

</details>

### Direct Download: Mirrors

| Variable | Description | Type | Default |
|----------|-------------|------|---------|
| `AA_BASE_URL` | Select Auto to try mirrors from your list on startup and fail over on errors. Choosing a specific mirror pins Shelfmark to that URL. | string (choice) | `auto` |
| `AA_MIRROR_URLS` | List the Anna's Archive mirror URLs you want Shelfmark to use. Type a URL and press Enter to add it. Order matters when Auto is selected. | string (comma-separated) | _empty list_ |
| `LIBGEN_MIRROR_URLS` | Mirrors are tried in the order you add them until one works. | string (comma-separated) | _empty list_ |
| `ZLIB_MIRROR_URLS` | Only the first mirror in the list is used. | string (comma-separated) | _empty list_ |
| `WELIB_MIRROR_URLS` | Only the first mirror in the list is used. | string (comma-separated) | _empty list_ |

<details>
<summary>Detailed descriptions</summary>

#### `AA_BASE_URL`

**Primary Mirror**

Select Auto to try mirrors from your list on startup and fail over on errors. Choosing a specific mirror pins Shelfmark to that URL.

- **Type:** string (choice)
- **Default:** `auto`
- **Options:** `auto` (Auto (Recommended))

#### `AA_MIRROR_URLS`

**Mirrors**

List the Anna's Archive mirror URLs you want Shelfmark to use. Type a URL and press Enter to add it. Order matters when Auto is selected.

- **Type:** string (comma-separated)
- **Default:** _empty list_

#### `LIBGEN_MIRROR_URLS`

**LibGen**

Mirrors are tried in the order you add them until one works.

- **Type:** string (comma-separated)
- **Default:** _empty list_

#### `ZLIB_MIRROR_URLS`

**Z-Library**

Only the first mirror in the list is used.

- **Type:** string (comma-separated)
- **Default:** _empty list_

#### `WELIB_MIRROR_URLS`

**Welib**

Only the first mirror in the list is used.

- **Type:** string (comma-separated)
- **Default:** _empty list_

</details>
