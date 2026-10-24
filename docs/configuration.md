# Directory and Volume Setup

This guide explains how to configure directories and Docker volumes for Shelfmark. It focuses on the difference between the destination folder and your download client paths, and how to make those paths line up inside containers.

## Conceptual Overview

```
DIRECT DOWNLOADS

Shelfmark downloads directly -> destination

TORRENT / USENET

Prowlarr -> Download client saves to <client path>
         -> Shelfmark reads from <client path>
         -> Shelfmark processes to destination
```

Key point: For torrent and usenet downloads, Shelfmark must see the same file path that your download client reports. The container path must match in both containers.

## Direct Download Volume Setup

If you plan to use Direct Download, it does not use an external download client. A simple two-folder setup is enough.

Required volumes:

| Container path | Purpose | Notes |
| --- | --- | --- |
| `/config` | Settings, database, cover cache | Configurable via `CONFIG_DIR` |
| `/books` | Destination folder for completed files | Configurable via `INGEST_DIR` and Settings -> Downloads -> Destination |

Example `docker-compose`:

```yaml
services:
  shelfmark:
    image: ghcr.io/calibrain/shelfmark:latest
    volumes:
      - /path/to/config:/config
      - /path/to/books:/books
```

Notes:
- Point `/books` to your library ingest folder (Calibre-Web, Grimmory, Audiobookshelf, etc) for automatic import.
- If you set Output Mode to Grimmory (API), books are uploaded via API instead of written to `/books`. Audiobooks still use a destination folder.
- Ensure `PUID`/`PGID` (or legacy `UID`/`GID`) match the owner of the host directories.
- For non-root mode, start the container as `1000:1000`.
- On Kubernetes, set `runAsUser: 1000`, `runAsGroup: 1000`, and `runAsNonRoot: true` together.
- `PUID`/`PGID` keep the default root startup flow.
- In non-root mode, mounted paths must already be writable by `1000:1000`.
- `USING_TOR=true` requires root startup.

## Torrent / Usenet Setup

For torrents and usenet, your download client reports a path (for example `/data/torrents/books/MyBook.epub`). Shelfmark must be able to read that exact path inside its own container.

Required volumes:

| Container path | Purpose | Notes |
| --- | --- | --- |
| `/config` | Settings, database, cover cache | Configurable via `CONFIG_DIR` |
| `/books` | Destination folder for processed files | Configurable via `INGEST_DIR` |
| `<client path>` | Download client path | Must match the download client container path exactly |

Side-by-side example with qBittorrent:

```yaml
services:
  shelfmark:
    volumes:
      - /path/to/config:/config
      - /path/to/books:/books
      - /path/to/downloads:/data/torrents  # Must match client

  qbittorrent:
    volumes:
      - /path/to/downloads:/data/torrents  # Same container path
```

Host paths can be anything. The container path (for example `/data/torrents`) must be identical in both containers.

### Remote Path Mappings

If paths cannot match (different machines or a fixed setup), use Remote Path Mappings.

Where to configure:
- Settings -> Advanced -> Remote Path Mappings

Example:
- Client reports `/data/torrents/books/...`
- Shelfmark can see the same files at `/downloads/books/...`
- Add a mapping from Remote Path `/data/torrents` to Local Path `/downloads`

If the files are copied or synced into Shelfmark on a delay, increase **Completed Path Wait (seconds)**
in Settings -> Advanced. The default is 60 seconds; seedbox or remote-sync setups may need a value
longer than the sync interval.

With qBittorrent, Shelfmark also reads the torrent's own file list and imports only once every file
the torrent downloads is there at its full size, so a sync that copies files in batches and pauses
between them does not cause a partial import. The same wait applies, and files the torrent skips
are not counted.

## File Processing Options

### Hardlink Torrents (Torrent / Usenet Only)

Imported files are copied by default, which works everywhere. Turn on **Hardlink Book Torrents** (and **Hardlink Audiobook Torrents** for audiobooks) to hardlink instead, which preserves seeding without duplicating files.

Hardlink requirements and behavior:
- Source and destination must be on the same filesystem.
- If hardlinking is enabled but not possible, Shelfmark falls back to copying.
- Archive extraction is disabled while hardlinking is enabled.
- Do not use hardlinking if your destination is a library ingest folder.

### File Organization

Shelfmark supports three organization modes for the destination:
- None. Keep original filenames from the source.
- Rename Only. Rename files using a template.
- Rename and Organize. Create folders and rename using templates. Do not use with ingest folders.

Configure templates in Settings -> Downloads. Template syntax details are documented separately.

## Common Mistakes

- "Download failed - file not found": Path mismatch between Shelfmark and the download client. Ensure container paths match or use Remote Path Mappings.
- "Permission denied": `PUID`/`PGID` do not match the host directories. Ensure Shelfmark can read the client path and write to the destination.
- "Permission denied" in non-root Docker/Kubernetes mode: ensure the mounted path is writable by UID/GID `1000:1000`, or switch back to root startup with `PUID`/`PGID`.
- "Hardlinks not working" or "Files being copied instead": Source and destination are on different filesystems. Move the destination or accept copy fallback.
- "Downloads work but library does not see them": Destination does not point to the library ingest folder. Check Settings -> Downloads -> Destination.
- CIFS/SMB shares: Use the `nobrl` mount option to avoid database lock errors. Example: `//server/share /mnt/share cifs nobrl,... 0 0`

## Related Documentation

- Environment Variables Reference: `docs/environment-variables.md`
- Custom Scripts: `docs/custom-scripts.md`
- Installation: `docs/installation.md`
- Troubleshooting: `docs/troubleshooting.md`
