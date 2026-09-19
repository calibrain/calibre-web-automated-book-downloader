# Hardcover Sync

Configure in **Settings → Hardcover Sync**.

Hardcover Sync pulls books from your [Hardcover](https://hardcover.app) reading shelves into Shelfmark as requests, optionally downloads them without an admin picking a release, and can skip books you already own in Audiobookshelf. Every part is opt-in and off by default; the tab does nothing until you enable it.

## Capabilities

### Shelf sync

On a schedule, Shelfmark reads the selected Hardcover shelf (by default **Want to Read**) through the Hardcover metadata provider and creates a **pending** request for each book that is not already known. Requests carry Hardcover's metadata, including the cover, and are owned by the lowest-id admin account.

A book is skipped when:

- a request with the same Hardcover book id already exists, in any status
- a download with the same title and author is already in the download history
- the library check (below) says it is already in Audiobookshelf
- a pending request with the same title, author, and content type already exists

### Automatic downloads

When enabled, each pending request that came from Hardcover is searched across your release sources in the configured priority order. The first source that yields a **strict match** wins, and the best candidate from that source is queued through the normal request fulfilment path, so it appears in the activity view and download history exactly like a request an admin approved.

A release is a strict match only when all of the following hold:

- at least 85% of the significant title words appear in the release title
- the author's surname appears in the release title, indexer name, or author field
- the release is an audiobook: an audiobook format (`m4b`, `mp3`, or your configured audiobook formats), or "audiobook"/"unabridged" in the title. Ebook-only formats such as `epub` and `pdf` are rejected
- for torrents, seeders meet **Minimum seeders**; other protocols ignore this check

Among matching releases from the winning source, `m4b` outranks `mp3`, then more seeders, then larger size. If no source produces a confident match the request stays **pending** for manual review; nothing is guessed.

### Library check (Audiobookshelf)

When enabled, both shelf sync and automatic downloads ask your Audiobookshelf server whether the book is already there, by ISBN or by fuzzy title plus author surname, and skip it if so. The library is fetched at most every 10 minutes.

The check **fails open**: if Audiobookshelf is unreachable or returns an error, Shelfmark logs a warning, reuses the last successful fetch if it has one, and otherwise treats the book as not owned so processing continues.

## Settings

| Key | Label | Default | Notes |
|-----|-------|---------|-------|
| `HARDCOVER_SYNC_ENABLED` | Enable scheduled sync | Off | Runs shelf sync on the interval below |
| `HARDCOVER_SYNC_TOKEN` | Hardcover API Token | — | Leave blank to reuse the Hardcover metadata provider's token |
| `HARDCOVER_SYNC_STATUSES` | Shelves to sync | Want to Read | Hardcover reading status to pull from |
| `HARDCOVER_SYNC_CONTENT_TYPE` | Request as | Audiobooks | Content type given to synced requests and targeted by automatic downloads |
| `HARDCOVER_SYNC_INTERVAL` | Sync interval | 6 | Combined with the unit below |
| `HARDCOVER_SYNC_INTERVAL_UNIT` | Interval unit | Hours | Minutes or hours. The effective interval never drops below 60 seconds |
| `AUTO_DOWNLOAD_ENABLED` | Enable automatic downloads | Off | Auto-approve and queue strict matches for Hardcover requests |
| `AUTO_DOWNLOAD_SOURCE_PRIORITY` | Source priority | All usable sources | Drag to order. Sources you disable here, or that are not configured, are skipped |
| `AUTO_DOWNLOAD_MIN_SEEDERS` | Minimum seeders (torrents) | 1 | Torrent releases below this are ignored |
| `LIBRARY_CHECK_ENABLED` | Skip books already in Audiobookshelf | Off | Applies to both sync and automatic downloads |
| `AUDIOBOOKSHELF_URL` | Audiobookshelf URL | — | Must be reachable from the Shelfmark container, e.g. `http://10.0.0.91:13378` |
| `AUDIOBOOKSHELF_TOKEN` | Audiobookshelf API Token | — | From Audiobookshelf **Settings → Users → your user → API Token** |
| `AUDIOBOOKSHELF_LIBRARY_IDS` | Library IDs | — | Comma-separated. Blank checks every book library |

Two action buttons sit at the bottom of the tab:

- **Test library connection** verifies the Audiobookshelf URL and token and reports how many items were indexed.
- **Sync now** runs a sync and automatic-download pass immediately using the saved settings. Sync runs even when scheduled sync is off; automatic downloads still require their own toggle.

## Request Flow

1. Shelf sync creates a **pending** request per new Hardcover book, with cover, year, and subtitle where Hardcover has them.
2. If automatic downloads are enabled, the request is searched source by source and the first strict match is queued through the normal fulfil path. The request becomes **fulfilled** and its delivery state is tracked like any other.
3. If no source yields a confident match, the request stays **pending** in the admin queue for manual review, exactly as if a user had requested the book.

Only requests whose provider is Hardcover are considered for automatic downloads, and requests that have already been dispatched are left alone.

## Scheduler

The scheduler starts with the app and waits **60 seconds** before its first cycle. Each cycle runs a shelf sync (when scheduled sync is enabled) followed by an automatic-download pass (when that is enabled), then sleeps for the configured interval. Intervals shorter than 60 seconds are raised to 60 seconds so a typo cannot hammer the Hardcover API. When neither toggle is on the scheduler idles.

Only one run happens at a time. If a scheduled cycle is in progress, **Sync now** reports that a sync is already running.

## Troubleshooting

**Sync adds nothing and the log says "no Hardcover token configured"** — set **Hardcover API Token** on this tab, or configure the token on the Hardcover metadata provider. A sync without a usable token counts as an error and creates no requests.

**Requests appear but nothing downloads** — check **Enable automatic downloads** is on, that **Source priority** contains at least one configured source that supports audiobooks, and look for `no strict match` lines in the log. Lowering **Minimum seeders** helps on quiet trackers; loosening the title or author match is deliberately not an option.

**Books you own keep being added** — enable the library check and use **Test library connection**. Because the check fails open, a wrong URL or token only produces a log warning rather than blocking the sync; the test button is where a bad connection is reported plainly.

**Too many requests at once** — the first sync of a large shelf creates one request per book, and the per-user pending limit from **Users & Requests** does not apply to synced requests. Start with a shelf you are happy to see in the queue in full.
