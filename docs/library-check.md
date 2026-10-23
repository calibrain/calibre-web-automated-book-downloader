# Library Check

Shelfmark can mark search results you already own, so you do not download a second copy
of a book that is already on your shelf. The check is read only and off by default.

## Calibre

Point Shelfmark at the `metadata.db` of a Calibre library (Calibre, Calibre-Web,
Calibre-Web-Automated, anything that keeps the standard Calibre format) and it reads the
database directly. No HTTP call, no API token, and nothing is ever written back.

1. Mount the library folder into the container read only, for example
   `/path/to/calibre-library:/calibre-library:ro`. Mount the folder rather than the file
   so the `-wal` and `-shm` sidecars are visible, otherwise a library that is being
   written to can read as out of date.
2. In **Settings, General**, turn on **Mark books already in your Calibre library**.
3. Leave **Calibre metadata.db path** at `/calibre-library/metadata.db` unless you mounted
   it somewhere else.
4. Press **Test Calibre library**. It reports how many books it indexed.

A result that matches the library then carries an **In library** badge in the card, list
and compact views, and in the details dialog.

## Audiobookshelf

Shelfmark asks the Audiobookshelf server over its REST API for the title, author, ISBN and
ASIN of every item, and nothing is written back.

1. In **Settings, General**, turn on **Mark audiobooks already in your Audiobookshelf
   library**.
2. Set **Audiobookshelf URL** to an address the Shelfmark container can reach (often the
   host's LAN IP, not the public URL), for example `http://10.0.0.91:13378`.
3. Paste an **Audiobookshelf API Token** (Audiobookshelf **Settings, Users, your user, API
   Token**). Read access is enough.
4. Leave **Audiobookshelf Library IDs** blank to check every book library, or list the IDs
   to check (comma separated).
5. Press **Test Audiobookshelf library**. It reports how many items it indexed, using the
   values on the form, so it works before you save.

Audiobookshelf titles are often raw folder names such as `Alex Cross 02: Kiss the Girls`.
When the server reports a series for the item, the leading label and number are read as the
series, not as a different book, so a search for `Kiss the Girls` still matches.

## How a match is decided

In order of confidence:

1. An external id the metadata provider and the library agree on.
2. An ISBN, compared in both ISBN-10 and ISBN-13 form.
3. Fuzzy title tokens plus the author surname, the same rule the rest of the app uses for
   book matching.

## Behaviour worth knowing

Sequels and other near-miss titles fail the title rule; the same title by a different author fails the surname rule. A series label in front of a library title ("Alex Cross 02: Kiss the Girls", "HHG 5: Mostly Harmless") is read as the series, not as part of the title, when the entry is in a series, so renaming a shelf to show reading order doesn't make its books look unowned. A shelf title whose bracketed words name another edition ("Full Cast Edition", "Dramatized Adaptation") is not the book either: owning the adaptation is not owning the recording asked for. Printing words ("Illustrated Edition", "Unabridged") change nothing, and in a Calibre title a numbered parenthetical ("(Alex Cross Series #11)") is read as the series, not as an edition.

- **It fails open for badges.** If a library cannot be read, Shelfmark logs a warning, reuses
  the last successful read if it has one, and otherwise treats the book as not owned. A
  broken path degrades the badge, it never blocks a search.
- **It fails closed for anything that acts on the answer.** The Hardcover sync and automatic
  downloads would otherwise request or download books the library already holds, so when an
  enabled library cannot be read and has not been read before, the sync cycle is skipped and
  the request stays pending until the next one. A library that listed items a moment ago and
  now lists none is treated as unreadable too, not as an emptied shelf.
- **Results are cached** for ten minutes, and refreshed early when the database file
  changes, so a large library costs one read rather than one per search.
- **Each library answers for its own format.** Calibre holds ebooks and Audiobookshelf holds
  audiobooks, so an ebook result is only checked against Calibre and an audiobook against
  Audiobookshelf. The provider interface in `shelfmark/core/library_providers/` takes more libraries: add a
  module with the `LibraryProvider` shape and list it in `all_providers()`. Nothing above
  that function knows which libraries exist.
