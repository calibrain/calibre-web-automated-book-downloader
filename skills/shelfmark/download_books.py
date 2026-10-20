#!/usr/bin/env python3
"""Shelfmark Book Downloader - Downloads books from a local Shelfmark instance."""

from __future__ import annotations

import argparse
import contextlib
import json
import re
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

if TYPE_CHECKING:
    from playwright.sync_api import Page

Book = dict[str, Any]

SHELFMARK_URL = "http://localhost:8084/"


def parse_books(page: Page) -> list[Book]:
    btns = page.query_selector_all('button[data-action="download"]')
    books = []
    for i, btn in enumerate(btns):
        content = btn.evaluate_handle("el => el.parentElement.parentElement").inner_html()

        title_match = re.search(r"<h3[^>]*>(.*?)</h3>", content, re.IGNORECASE | re.DOTALL)
        title = title_match.group(1).strip() if title_match else "Unknown"

        author_match = re.search(
            r'class="min-w-0 truncate text-xs[^"]*"[^>]*>(.*?)<', content, re.IGNORECASE | re.DOTALL
        )
        author = author_match.group(1).strip() if author_match else "Unknown"

        dl_match = re.search(r"<span>•</span>\s*<span>([\d,]+)</span>", content)
        downloads = int(dl_match.group(1).replace(",", "")) if dl_match else 0

        books.append({"index": i, "title": title, "author": author, "downloads": downloads})
    return books


def do_search(
    page: Page, title: str, author: str, search_type: str = "author"
) -> tuple[list[Book], list[Book]] | None:
    """Search with title+author, fall back to title only"""
    # Navigate to main page first to reset SPA state
    page.goto(SHELFMARK_URL)
    page.wait_for_load_state("networkidle")

    # Enter search query and submit
    search_query = f"{title} {author}" if search_type == "author" else title
    page.fill('input[type="search"]', search_query)
    page.press('input[type="search"]', "Enter")

    # Wait for "Most relevant" to appear (indicates search results are fully rendered)
    try:
        page.wait_for_selector('span.text-sm.font-medium:has-text("Most relevant")', timeout=60000)
    except:
        if search_type == "author":
            print("  -> Trying title only...")
            sys.stdout.flush()
            return do_search(page, title, author, search_type="title")
        print("  >> Timeout waiting for search results")
        sys.stdout.flush()
        return None

    books = parse_books(page)

    def clean_title_for_match(book_title: str, search_title: str) -> bool:
        """Clean book title to check if it matches the search title"""
        # Remove common subtitle patterns
        clean = re.sub(
            r"\s*[:–—]\s*(A Novel|Reese\'s Book Club.*?|The Hilarious.*?|A GMA Book Club Pick.*?|Movie Tie-In.*?|eBook.*?|\[.*?\].*?)$",
            "",
            book_title,
            flags=re.IGNORECASE,
        )
        clean = clean.strip()
        # Remove trailing punctuation
        clean = clean.rstrip(":,;.")
        return clean.lower().strip() == search_title.lower().strip()

    def matches_search(
        book_title: str, book_author: str, search_title: str, search_author: str
    ) -> bool:
        """Check if book matches search criteria more strictly"""
        title_match = search_title.lower() in book_title.lower()
        author_match = search_author.lower() in book_author.lower()

        # For title+author search, ensure title starts with search title (not just contains it)
        if title_match and author_match:
            return clean_title_for_match(book_title, search_title) or book_title.lower().startswith(
                search_title.lower()
            )

        # Also check if author name appears in reverse order (e.g., "Grann, David" matches "David Grann")
        if title_match:
            author_parts = search_author.lower().split()
            if len(author_parts) >= 2:
                reversed_author = f"{author_parts[-1]} {author_parts[0]}"
                if reversed_author in book_author.lower() or book_author.lower().startswith(
                    reversed_author
                ):
                    return clean_title_for_match(
                        book_title, search_title
                    ) or book_title.lower().startswith(search_title.lower())

        return False

    if search_type == "author":
        matching = [b for b in books if matches_search(b["title"], b["author"], title, author)]
        print(f"  With author: {len(books)} total, {len(matching)} matching")
        for b in books[:3]:
            print(f"    [{b['index']}] '{b['title']}' by {b['author']} ({b['downloads']})")
        sys.stdout.flush()

        if matching:
            # Check if any matching book is already in the download queue
            with contextlib.suppress(PlaywrightError):
                sidebar_text = page.inner_text("aside")
                if "IN PROGRESS" in sidebar_text:
                    # Get the title of the book currently downloading
                    current_download = (
                        sidebar_text.split("IN PROGRESS")[1]
                        .split("—")[0]
                        .strip()
                        .split("\n")[0]
                        .strip()
                    )
                    # Check if the current download matches our search
                    if title.lower() in current_download.lower():
                        print(f"  >> '{current_download}' already downloading, skipping")
                        sys.stdout.flush()
                        return None

        if matching:
            return matching, books

        # Fall back to title only
        print("  -> Trying title only...")
        sys.stdout.flush()
        return do_search(page, title, author, search_type="title")

    # Title-only search - prefer exact matches first
    matching = [b for b in books if title.lower() in b["title"].lower()]
    exact_matches = [b for b in matching if clean_title_for_match(b["title"], title)]

    if exact_matches:
        matching = exact_matches

    print(
        f"  Title-only: {len(books)} total, {len(matching)} matching ({len(exact_matches)} exact)"
    )
    for b in books[:5]:
        print(f"    [{b['index']}] '{b['title']}' by {b['author']} ({b['downloads']})")
    sys.stdout.flush()

    if not matching:
        return None

    return (matching, books)


def download_book(page: Page, title: str, author: str) -> None:
    result = do_search(page, title, author)
    if not result:
        print("\n  >>> NOT FOUND")
        sys.stdout.flush()
        return

    matching, _all_books = result
    if not matching:
        print("\n  >>> NOT FOUND")
        sys.stdout.flush()
        return

    # Find the best book that is not disabled
    btns = page.query_selector_all('button[data-action="download"]')
    best = None
    for b in sorted(matching, key=lambda x: x["downloads"], reverse=True):
        if b["index"] < len(btns) and not btns[b["index"]].is_disabled():
            best = b
            break

    if not best:
        print("\n  >>> All matching books are already in download queue")
        sys.stdout.flush()
        return

    print(
        f"\n  >>> DOWNLOADING: '{best['title']}' by {best['author']} ({best['downloads']} dl) [idx={best['index']}]"
    )
    sys.stdout.flush()

    # Click on the article h3 to open detail view (required for download to work)
    h3s = page.query_selector_all("article h3")
    if best["index"] < len(h3s):
        h3s[best["index"]].click()
        page.wait_for_timeout(300)
    else:
        print(f"  >> Warning: h3 index {best['index']} out of range ({len(h3s)} h3s)")
        sys.stdout.flush()

    # Click the download button
    btn = btns[best["index"]]
    btn.click()

    # Wait a moment for the click to register
    page.wait_for_timeout(500)
    print("  >> Click sent")
    sys.stdout.flush()

    # Check sidebar immediately
    time.sleep(2)
    try:
        txt = page.inner_text("aside")
        if "IN PROGRESS" in txt:
            print("  >> Download started!")
            sys.stdout.flush()
        else:
            print(f"  >> Sidebar after 2s: {txt[:150]}")
            sys.stdout.flush()
    except:
        print("  >> Sidebar not visible after 2s")
        sys.stdout.flush()

    # Wait for download to complete - check sidebar every 2 seconds, max 5 minutes
    last_state = None
    for _attempt in range(150):
        time.sleep(2)
        try:
            txt = page.inner_text("aside")
            if "IN PROGRESS" in txt:
                state = "downloading"
            elif "Complete" in txt or "Saved" in txt or "No activity" in txt:
                state = "done"
            else:
                state = "other"

            if state != last_state:
                if state == "downloading":
                    print("  >> Download started!")
                elif state == "done":
                    if "No activity" in txt:
                        print("  >> Download not started (no activity)")
                    else:
                        print("  >> Download complete!")
                last_state = state

                if state == "done":
                    break
        except:
            if last_state is None:
                print("  >> Sidebar not visible yet")
                last_state = "no_sidebar"
        sys.stdout.flush()
    else:
        if last_state != "done":
            print("  >> Timeout waiting for download")
        sys.stdout.flush()


def clear_completed_downloads(page: Page) -> None:
    """Clear completed downloads from previous sessions"""
    page.evaluate("""
        () => {
            for (const b of document.querySelectorAll('button')) {
                if (b.textContent.includes('Clear Completed')) { b.click(); return; }
            }
        }
    """)
    page.wait_for_timeout(2000)


def check_calibre(title: str) -> bool:
    """Check if a book is already in the calibre database."""
    import subprocess

    cmd = f'calibredb list --search title:="{title}"'
    result = subprocess.run(cmd, check=False, shell=True, capture_output=True, text=True)
    output = result.stdout.strip()
    # calibredb output has header line "id title authors", count non-header lines
    lines = [l.strip() for l in output.split("\n") if l.strip()]
    # Remove header if present (first line starts with 'id')
    if lines and lines[0].startswith("id"):
        lines = lines[1:]
    return len(lines) > 0


def load_books_from_json(json_str: str, source: str) -> list[tuple[str, str]]:
    """Load books from a JSON string or file path."""
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in {source}: {e}")
        sys.exit(1)

    if not isinstance(data, list):
        print(f"Error: JSON in {source} must be an array of book objects")
        sys.exit(1)

    books = []
    for item in data:
        if isinstance(item, dict):
            if "title" not in item or "author" not in item:
                print(f"Error: Each book object must have 'title' and 'author' keys: {item}")
                sys.exit(1)
            books.append((item["title"].strip(), item["author"].strip()))
        elif isinstance(item, list) and len(item) >= 2:
            books.append((str(item[0]).strip(), str(item[1]).strip()))
        elif isinstance(item, str):
            # Support plain strings as title-only (author will be searched separately)
            books.append((item.strip(), ""))
        else:
            print(f"Warning: Skipping unrecognized book entry: {item}")
    return books


def main() -> None:
    parser = argparse.ArgumentParser(description="Download books from Shelfmark")
    parser.add_argument(
        "books",
        nargs="*",
        help='Books as JSON array of [title, author] pairs or {"title": ..., "author": ...} objects',
    )
    parser.add_argument("--file", "-f", help="Path to JSON file containing an array of books")
    parser.add_argument(
        "-c",
        "--check-calibre",
        action="store_true",
        help="Check calibre database before downloading, skip if already present",
    )
    args = parser.parse_args()

    # Load books from --file, positional JSON arg, or both
    books_to_download = []

    if args.file:
        try:
            with Path(args.file).open() as f:
                books_to_download = load_books_from_json(f.read(), f"file '{args.file}'")
        except FileNotFoundError:
            print(f"Error: File not found: {args.file}")
            sys.exit(1)
        except OSError as e:
            print(f"Error reading file: {e}")
            sys.exit(1)

    for book_arg in args.books:
        books_to_download.extend(load_books_from_json(book_arg, "command line argument"))

    if not books_to_download:
        print("Error: No books specified. Use positional JSON args or --file <path>")
        sys.exit(1)

    # Check calibre database if requested
    if args.check_calibre:
        print("\nChecking calibre database...")
        sys.stdout.flush()
        remaining = []
        for title, author in books_to_download:
            if check_calibre(title):
                print(f"  >> '{title}' already in calibre, skipping")
                sys.stdout.flush()
            else:
                remaining.append((title, author))
        books_to_download = remaining
        if not books_to_download:
            print("All books already in calibre, nothing to download.")
            sys.stdout.flush()
            return

    print(f"\n{'=' * 60}")
    print(f"Downloading {len(books_to_download)} book(s) from Shelfmark")
    print("=" * 60)
    sys.stdout.flush()

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(SHELFMARK_URL)
        page.wait_for_load_state("networkidle")

        # Clear completed downloads at start of batch
        print(f"\n{'=' * 60}")
        print("Clearing completed downloads from previous sessions...")
        print("=" * 60)
        sys.stdout.flush()
        clear_completed_downloads(page)

        for i, (title, author) in enumerate(books_to_download, 1):
            print(f"\n{'=' * 60}")
            print(f"[{i}/{len(books_to_download)}] '{title}' by {author}")
            print("=" * 60)
            sys.stdout.flush()

            download_book(page, title, author)

        browser.close()
        print("\nDone!")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
