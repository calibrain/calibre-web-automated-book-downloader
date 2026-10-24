# Hardcover Sync

Configure in **Settings → Hardcover Sync**.

Hardcover Sync pulls books from your [Hardcover](https://hardcover.app) reading shelves into Shelfmark as requests, optionally downloads them without an admin picking a release, and can skip books you already own in a library you have set up under [Library Check](library-check.md). Every part is opt-in and off by default; the tab does nothing until you enable it.

## Capabilities

### Shelf sync

On a schedule, Shelfmark reads the selected Hardcover shelf (by default **Want to Read**) through the Hardcover metadata provider and creates a **pending** request for each book that is not already known. Requests carry Hardcover's metadata, including the cover, and are owned by the lowest-id admin account.

A book is skipped when:

- a request with the same Hardcover book id already exists, in any status
- a request for the same Hardcover book and format, by any user, has a download in the history that finished or is still running (an unfinished download older than a day no longer counts as running)
- the library check (below) says you already own it
- a pending request with the same title, author, and content type already exists

### Automatic downloads

When enabled, each pending request that came from Hardcover is searched across your release sources in the priority order configured for its content type (audiobook requests use the audiobook list, ebook requests the ebook list). The first source that yields a **strict match** wins, and the best candidate from that source is queued through the normal request fulfilment path, so it appears in the activity view and download history exactly like a request an admin approved.

A release is a strict match only when all of the following hold:

- at least 85% of the significant words in the book's title appear in the release title. Numbers from 1 to 20 count as the same word however they are written, so *Part II*, *Part Two* and *Part 2* agree, while *Part One* never stands in for *Part Two*;
- the author's surname appears in the release title, indexer name or author field (a book with no author metadata matches on title alone). Some AudiobookBay listings carry no author in their title (*The Mummy or Ramses the Damned*). When a source turns up no strict match, the detail pages of up to three such releases that pass every other rule are read, and the author they credit (a narrator does not count) is checked like any other. A page that credits nobody, or someone else, leaves the release rejected;
- the release title doesn't name a different book that contains the requested one. A "Dune" request won't take *Dune Messiah* or *Children of Dune*, and a bare number after the title (*Mistborn 2*, *Mistborn Two*, *Dune Chronicles Book II*) names a sequel. A title with a number of its own (*Ready Player One*) takes no other number after it, 1 included. Only the parts of the release name that hold the title are judged, so narrators, uploader tags and the book's own series, author and subtitle words don't count against it; a part that is just the series name (*Alex Cross 25*) does not vouch for a one-word title, so a "Cross" request won't take *Alex Cross: Cross Justice*. Two slash-separated names that both hold the title (*Dune / Dune Messiah*) are two books;
- the release is not a derivative of the work: *summary*, *analysis*, *study guide*, *workbook*, *abridged*, *dramatized*, *adaptation* or *graphic novel* anywhere in the name disqualifies it, unless the requested title itself carries the word. *Unabridged* is a full recording and is fine;
- the release is not a multi-book pack: ranges like `1-24` or `Books 1 to 17`, or words like *complete*, *collection*, *box set*, *omnibus*, *trilogy*, *megapack*, *+ short stories*, unless the requested title itself carries them. "Complete and Unabridged" describes one recording and doesn't count. The library check uses the same word list;
- the format fits the request — an audiobook request needs an audiobook signal (`m4b`/`mp3`, a release from an audiobook-only source, or "audiobook"/"unabridged" in the title) and is never an ebook-only file; an ebook request needs one of your **Supported Book Formats** (or an `.epub`-style marker in the title) and no audiobook signal;
- for torrents that report a seeder count, it meets **Minimum seeders** — sources that report none (AudiobookBay) are not judged on seeders.

AudiobookBay requires every word of a query, and its posts for one volume of a multi-part book often spell the part differently from the request ("Part Two" for "Part II"), so a query carrying the part finds only the packs that list it. When no row a query returns carries the requested title, the source tries the next query, ending with the title minus its trailing *, Part N* or *Volume N*. The strict match above still decides which part was found.

When no source yields a strict match for the request's own search title, up to three simpler titles are tried in turn, each across the sources in priority order and all within the request's search budget: the title without a subtitle after `:` or ` - `, the title without an alternate title (*The Hobbit, or There and Back Again* becomes *The Hobbit*), and the title with its numbers written as digits (*Part II* becomes *Part 2*). The first one that yields a strict match wins, and the log names it. A simpler title is matched by exactly the rules above, so a release has to name the shorter title, the right author, and no sequel, edition, derivative or pack. The subtitle is only dropped when the book's metadata says it is one (a series name is known, or the book's subtitle is what follows the separator), and never when what is left is only the series or author name: *Star Wars: Thrawn* is not searched as *Star Wars*.

Among matching releases from the winning source, format ranks first (`m4b` over `mp3` for audiobooks; `epub` over `azw3` over `mobi` for ebooks), then the copy other people chose — more downloads (direct-download sources report them), then more seeders — then larger size. The exception is an audiobook copy far smaller than the others for the same book, which is probably a lower-bitrate copy: it is tried after the others (see **Smallest acceptable audiobook copy** below). If no source produces a confident match the request stays **pending** for manual review; nothing is guessed.

A request whose book turns out to be in your library already is closed, not left pending: it is marked fulfilled with the admin note `[auto] Already in the library.`, so it stops being looked up every pass. Before this it sat in Needs Review indefinitely. **Clear Completed** clears these too.

Hardcover allows a short burst of lookups and then answers 429. Each pass paces its book lookups and backs off when told to, following `Retry-After`. A lookup that still fails counts as an error for that request, not as "book not found", and the request is tried again next pass.

A download that fails, or is interrupted (a restart while it was queued, say), does not stay that way forever. Once the request has sat there for **Retry failed downloads after (days)** (7 by default), it goes back to pending and the next pass searches again. Each pass reopens at most five, oldest first, so a backlog is spread out instead of hitting your sources at once. An interrupted download may pick the same release again, since a torrent already in your client is joined instead of added twice. A download that itself failed, or that Shelfmark's stall timer cancelled, has its release remembered and skipped next time, and the request is given up on after three failed retries. Requests you or an admin cancelled are never retried, and neither are rejected ones: both are decisions. Set the value to 0 to turn retries off.

A request that no source has a match for is not searched every cycle for ever. After three passes in a row with no match it is searched once a day, then every two days, and no further apart than **Longest wait between searches for an unmatched request (days)** (3 by default, 0 searches every unmatched request every cycle). A match, a search that failed (which says nothing about the book) or **Sync now** starts it over.
**Trying another release sooner.** Set **Try another release after a failed download (minutes)** (off by default) and a download that fails, or that is given up on (stalled, or no seeders when **Give Up With No Seeders** is on), does not wait for the days above. Between sync cycles Shelfmark looks for such requests, reopens them after that many minutes and searches again, for those requests only. The release that failed is skipped, and a request is still given up on after three failed attempts.

**Sources known to be dead are tried last.** When choosing among matching releases, a Soulseek peer whose transfers failed in the last three days and who delivered nothing in that time comes after the others. AudiobookBay shows no seed counts, so the comments on its five best posts are read, and a post where comments say it has no seeders comes after the others. Nothing is excluded: if a dead-looking release is the only match, it is still tried.

### Audiobooks no store sells

Some requested books were never recorded, so no source will ever match them and the request waits in Needs Review for good. With **Mark audiobooks no store sells** on, an audiobook request that has gone **Look the book up after (unmatched passes)** consecutive passes (3 by default) without a strict match is looked up on Audible's US and UK stores, through the metadata search of the Audiobookshelf server set up for the library check (its URL and token are needed; the library check itself can stay off).

- **On neither store:** the request stays pending and its failure reason reads `Not found on Audible (US/UK) after <N> searches, <date>`. It is not declined: Audible search misses real recordings (novellas sold only in a collection, books retitled before release, older or fan recordings), so the call is left to you. The store is asked again a week later.
- **On a store:** the request stays pending and its failure reason reads `Exists on Audible (<ASIN>), not on our sources yet`. The store is asked again a week later, not every pass.
- **Lookup failed:** nothing changes and the next pass asks again. A failed lookup never marks a request. Audiobookshelf answers a failed Audible search with an empty list, which looks like "no such book", so before marking a request as not found Shelfmark also looks up a book both stores are known to sell (*The Hobbit*). If that comes back missing too, the answer is treated as a failure.

A store result counts as the book when its title matches the request's once both are normalised (accents, punctuation, "Part II"/"Part Two"/"Part 2", a subtitle after `:` or `, or`, bracketed notes like "(Unabridged)" and a leading series label are all ignored, and a title that begins the other with at least two words also counts) and the request author's surname is in its author field. The matching leans towards "found": a wrong "found" only leaves the request pending. The pass counter is reset when a source matches the book and when the request is rejected, so a reopened request counts afresh. Store lookups come out of the request's search time; when too little is left, the lookup waits for the next pass. Ebook requests are not checked.

### Library check

Both shelf sync and automatic downloads ask the libraries you have enabled under [Library Check](library-check.md) whether the book is already there, for the content type being requested, and skip it if so. There is no separate switch on this tab. A library that is not enabled, or that does not hold the requested content type, never causes a skip.

The check **fails closed** here: if an enabled library cannot be read and has not been read before, the sync cycle is skipped and an automatic download is held, and the request stays pending until the next cycle. Skipping is the safe side, because carrying on would request or download books the library already holds. Badges in search results still fail open.

## Settings

| Key | Label | Default | Notes |
|-----|-------|---------|-------|
| `HARDCOVER_SYNC_ENABLED` | Enable scheduled sync | Off | Runs shelf sync on the interval below |
| `HARDCOVER_SYNC_TOKEN` | Hardcover API Token | — | Leave blank to reuse the Hardcover metadata provider's token |
| `HARDCOVER_SYNC_STATUSES` | Shelves to sync | Want to Read | Hardcover reading status to pull from |
| `HARDCOVER_SYNC_CONTENT_TYPE` | Request as | Audiobooks | `Audiobooks`, `Ebooks`, or `Ebooks and audiobooks` — the last creates one request of each per shelf book, each checked against its own library |
| `HARDCOVER_SYNC_INTERVAL` | Sync interval | 6 | Combined with the unit below |
| `HARDCOVER_SYNC_INTERVAL_UNIT` | Interval unit | Hours | Minutes or hours. The effective interval never drops below 60 seconds |
| `AUTO_DOWNLOAD_ENABLED` | Enable automatic downloads | Off | Auto-approve and queue strict matches for Hardcover requests |
| `AUTO_DOWNLOAD_SOURCE_PRIORITY` | Audiobook source priority | All usable sources | Drag to order; used for audiobook requests. Sources you disable here, or that are not configured, are skipped |
| `AUTO_DOWNLOAD_EBOOK_SOURCE_PRIORITY` | Ebook source priority | All usable sources | Same, for ebook requests |
| `AUTO_DOWNLOAD_MIN_SEEDERS` | Minimum seeders (torrents) | 1 | Torrent releases below this are ignored |
| `AUTO_DOWNLOAD_AUDIOBOOK_MIN_SIZE_PERCENT` | Smallest acceptable audiobook copy (%) | 67 | A copy smaller than this share of the largest copy found for the same book is tried after the others, and a single m4b that small is not preferred over a fuller set of chapter files. 0 turns the check off |
| `AUTO_DOWNLOAD_RETRY_DAYS` | Retry failed downloads after (days) | 7 | A failed or interrupted download goes back to pending after this long. 0 turns retries off |
| `AUTO_DOWNLOAD_BACKOFF_MAX_DAYS` | Longest wait between searches for an unmatched request (days) | 3 | 0 searches every unmatched request every cycle |
| `AUTO_DOWNLOAD_FAILURE_RETRY_MINUTES` | Try another release after a failed download (minutes) | 0 (off) | Reopens failed or given-up downloads and picks another release this long after, between sync cycles |
| `AUTO_DOWNLOAD_RETRY_FAILED_RELEASE_DAYS` | Try a failed release again after (days) | 7 | A release that failed or stalled is kept out of the search for this long, then may be picked again; a request is still given up on after three failed attempts |
| `AUTO_DOWNLOAD_EXISTENCE_CHECK_ENABLED` | Mark audiobooks no store sells | Off | See [Audiobooks no store sells](#audiobooks-no-store-sells). Needs the Audiobookshelf URL and token |
| `AUTO_DOWNLOAD_EXISTENCE_CHECK_AFTER_PASSES` | Look the book up after (unmatched passes) | 3 | Consecutive passes without a strict match before the stores are asked |

One action button sits at the bottom of the tab:

- **Sync now** runs a sync and automatic-download pass immediately using the saved settings. Sync runs even when scheduled sync is off; automatic downloads still require their own toggle.

## Request Flow

1. Shelf sync creates a **pending** request per new Hardcover book, with cover, year, and subtitle where Hardcover has them.
2. If automatic downloads are enabled, the request is searched source by source and the first strict match is queued through the normal fulfil path. The request becomes **fulfilled** and its delivery state is tracked like any other.
3. If no source yields a confident match, the request stays **pending** in the admin queue for manual review, exactly as if a user had requested the book.

Only requests whose provider is Hardcover are considered for automatic downloads, and requests that have already been dispatched are left alone.

## Scheduler

The scheduler starts with the app and waits **60 seconds** before its first cycle. Each cycle runs a shelf sync (when scheduled sync is enabled) followed by an automatic-download pass (when that is enabled), then sleeps for the configured interval. Intervals shorter than 60 seconds are raised to 60 seconds so a typo cannot hammer the Hardcover API. If the interval cannot be read at all, the scheduler logs the error and tries again after six hours instead of stopping. When neither toggle is on the scheduler idles.

Only one run happens at a time. If a scheduled cycle is in progress, **Sync now** reports that a sync is already running.

## Troubleshooting

**Sync adds nothing and the log says "no Hardcover token configured"** — set **Hardcover API Token** on this tab, or configure the token on the Hardcover metadata provider. A sync without a usable token counts as an error and creates no requests.

**Requests appear but nothing downloads** — check **Enable automatic downloads** is on, that the source-priority list for the request's content type contains at least one configured source, and look for `no strict match` lines in the log. Lowering **Minimum seeders** helps on quiet trackers; loosening the title or author match is deliberately not an option.

**Books you own keep being added** — enable the library check for the format you request (see [Library Check](library-check.md)) and use its test button. A library that cannot be read shows up in the log as a skipped cycle.

**Too many requests at once** — the first sync of a large shelf creates one request per book, and the per-user pending limit from **Users & Requests** does not apply to synced requests. Start with a shelf you are happy to see in the queue in full.
