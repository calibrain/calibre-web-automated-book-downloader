---
name: shelfmark
description: Tool for downloading books.
license: Complete terms in LICENSE.txt
---

# Shelfmark Book Download Skill

Use this skill to search for and download books from a local Shelfmark instance using Playwright.

## Prerequisites

- Shelfmark must be running at `http://localhost:8084/`
- Use `playwright-cli` skill for browser automation capabilities
- Python 3.10+ with `playwright` package installed

## Quick Start

```bash
# Show help information for download script
python3 /home/username/.agents/skills/shelfmark/download_books.py -h

# Download a single book
python3 /home/username/.agents/skills/shelfmark/download_books.py '[{"title": "The Great Gatsby", "author": "F. Scott Fitzgerald"}]'

# Download multiple books
python3 /home/username/.agents/skills/shelfmark/download_books.py '[{"title": "The Great Gatsby", "author": "F. Scott Fitzgerald"}, {"title": "Oliver Twist", "author": "Charles Dickens"}, {"title": "Frankenstein", "author": "Marry Shelley"}]'

# Check calibre database before downloading (skip if already present)
python3 /home/username/.agents/skills/shelfmark/download_books.py --check-calibre '[{"title": "Frankenstein", "author": "Marry Shelley"}]'

# Load books from a JSON file
python3 /home/username/.agents/skills/shelfmark/download_books.py --file books.json
```

The JSON file (`books.json`) should contain an array of book objects:

```json
[
  {"title": "The Great Gatsby", "author": "F. Scott Fitzgerald"},
  {"title": "Frankenstein", "author": "Mary Shelley"},
  {"title": "Oliver Twist", "author": "Charles Dickens"}
]
```

Array format is also supported: `[["The Great Gatsby", "F. Scott Fitzgerald"], ["Oliver Twist", "Charles Dickens"], ["Frankenstein", "Marry Shelley"]]`

Title-only (no author) is supported: `["Frankenstein"]`

## Key Characteristics

- **Shelfmark is a React SPA** — raw HTML is a shell; JavaScript dynamically populates the DOM
- **Desktop viewport required** (`1280x900`) — download buttons use `hidden sm:flex` and won't render on mobile
- **Download count pattern** in HTML: `<span>•</span> <span>NUMBER</span> </div>` (the last number before the download button)
- **Download button selector**: `<button ... data-action="download" ...>Download</button>`
- **Search input**: `<input type="search" placeholder="Search Books">`
- **Results indicator**: `<span class="text-sm font-medium whitespace-nowrap">Most relevant</span>`

## How It Works

### 1. Search for a Book

The script navigates to the main page, enters the search query, and waits for "Most relevant" to appear:

```python
# Navigate to main page first to reset SPA state
page.goto("http://localhost:8084/")
page.wait_for_load_state("networkidle")

# Enter search query and submit
page.fill('input[type="search"]', f"{title} {author}")
page.press('input[type="search"]', "Enter")

# Wait for "Most relevant" to appear (indicates search results are fully rendered)
page.wait_for_selector('span.text-sm.font-medium:has-text("Most relevant")', timeout=60000)
```

- Always navigate to the main page before each search to reset React SPA state
- Include both title and author in the search query
- Wait for `span.text-sm.font-medium:has-text("Most relevant")` to appear — this indicates search results are fully loaded
- **Do not use timers** to wait for results — always wait for a specific page element

### 2. Find and Parse Download Buttons

```python
def parse_books(page):
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
```

### 3. Select and Download the Book with Most Downloads

```python
# Filter books matching the search criteria
matching_books = [b for b in books if matches_search(b["title"], b["author"], title, author)]

if matching_books:
    best = max(matching_books, key=lambda x: x["downloads"])

    # CRITICAL: Click on the article h3 to open detail view
    h3s = page.query_selector_all("article h3")
    if best["index"] < len(h3s):
        h3s[best["index"]].click()
        page.wait_for_timeout(300)

    # Then click the download button
    btn = page.query_selector_all('button[data-action="download"]')[best["index"]]
    btn.click()
    page.wait_for_timeout(500)
```

**Important**: The React SPA requires clicking on the article's `<h3>` element first to open the detail view. Simply clicking the download button directly often fails silently.

### 4. Wait for Download to Complete

```python
for i in range(60):
    page.wait_for_timeout(5000)
    activity_text = page.inner_text("aside")

    if "IN PROGRESS" in activity_text:
        print("Download started!")
    elif "Complete" in activity_text or "Saved" in activity_text:
        print("Download complete!")
        break
    elif "No activity" in activity_text:
        print("Download not started")
        break
```

### 5. Clear Completed Downloads

```python
# Click "Clear Completed" using JavaScript
page.evaluate("""
    () => {
        for (const b of document.querySelectorAll('button')) {
            if (b.textContent.includes('Clear Completed')) {
                b.click();
                return;
            }
        }
    }
""")

page.wait_for_timeout(2000)
```

### Checking Calibre Database

Use `--check-calibre` (or `-c`) to check if books are already in your calibre database before downloading:

```bash
python3 download_books.py --check-calibre '[{"title": "Frankenstein", "author": "Mary Shelley"}]'
```

Books found in calibre are skipped with a message. If all books are already present, the script exits early without launching the browser.

## Important Notes

- **Always click the article element first** before clicking the download button — the React SPA requires this to properly initialize the download workflow
- **Wait for "Most relevant" text** to appear after search — this indicates results are fully loaded (don't use timers)
- **Navigate to main page** (`http://localhost:8084/`) before each new search to reset React SPA state
- **Some books may have different authors listed** than what's in your source file — the script falls back to title-only search if author search fails
- **The download count** is the last number in the format `• NUMBER` before the download button
- **Book titles may include series info** in brackets, e.g., `(The Locked Tomb Trilogy)`
- **Use `page.evaluate_handle`** to get parent element HTML for parsing — the button's `parentElement.parentElement` contains the card content
- **Title matching** prefers exact matches over partial matches (e.g., "Yesteryear" matches "Yesteryear: A Novel" but not "The Piers of Yesteryear")
- **Books are passed as JSON** — use `--check-calibre` to optionally skip books already in your calibre database

## Common Issues

| Issue | Solution |
|-------|----------|
| No download buttons found | Use desktop viewport (1280x900), wait for `span.text-sm.font-medium:has-text("Most relevant")` |
| Download button click does nothing | Click the article's `<h3>` element first, then click the download button |
| Search returns no results | The script falls back to title-only search automatically |
| Download count shows 0 | The parsing regex may need adjustment — check the HTML structure |
| Sidebar shows "No activity" after click | Ensure you clicked the `<h3>` element first, and wait at least 2 seconds before checking |
| Search results don't update between books | Navigate to `http://localhost:8084/` before each new search to reset SPA state |
| Book downloaded is wrong title | The script prefers exact title matches — if the title is ambiguous, the author search will help narrow it down |
| Books passed incorrectly | Books must be valid JSON — use `{"title": "...", "author": "..."}` format, not `Title: Author` |
