# URL Search Parameters

You can trigger searches directly via URL by adding query parameters. This enables bookmarking searches and sharing links.

## Basic Usage

```
http://your-server:8084/?q=harry+potter
```

## Supported Parameters

| Parameter | Description | Example |
|-----------|-------------|---------|
| `q` or `query` | Main search query | `/?q=dune` |
| `author` | Filter by author name | `/?author=frank+herbert` |
| `title` | Filter by book title | `/?title=foundation` |
| `isbn` | Filter by ISBN | `/?isbn=978-0747532699` |
| `lang` | Filter by language (ISO 639-1 code) | `/?lang=en` |
| `format` | Filter by file format | `/?format=epub` |
| `content` | Filter by content type | `/?content=fiction` |
| `sort` | Sort order for results | `/?sort=newest` |

## Multiple Values

Some parameters support multiple values by repeating the parameter:

```
/?lang=en&lang=de&lang=fr
/?format=epub&format=mobi&format=azw3
```

## Examples

**Simple search:**
```
/?q=lord+of+the+rings
```

**Search with author filter:**
```
/?q=dune&author=frank+herbert
```

**Search with format and language:**
```
/?q=harry+potter&format=epub&lang=en
```

**Author search with multiple formats:**
```
/?author=stephen+king&format=epub&format=mobi
```

**Search with sort order:**
```
/?q=science+fiction&sort=newest
```

## Search Mode Behavior

### Direct Download Mode (default)

All parameters are used to filter results from Anna's Archive.

### Universal Mode

Only `q` and `sort` are used. Other parameters (author, title, format, etc.) are silently ignored since metadata providers have their own search capabilities.

## Notes

- URL parameters are read once on page load
- The URL is not updated when you perform searches manually
- Spaces should be encoded as `+` or `%20`
- Invalid or unknown parameters are silently ignored
