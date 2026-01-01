# 📚 Book Downloader
*calibre-web-automated-book-downloader*

<img src="src/frontend/public/logo.png" alt="Book Downloader" width="200">

A unified web interface for searching and downloading books from multiple sources — all in one place. Works out of the box with popular web sources, no configuration required. Add metadata providers, additional release sources, and download clients to create a single hub for building your digital library.

**Fully standalone** — no external dependencies required. Works great alongside library tools like [Calibre-Web-Automated](https://github.com/crocodilestick/Calibre-Web-Automated) or [Booklore](https://github.com/booklore-app/booklore) for automatic import.

## ✨ Features

- **One-Stop Interface** - A clean, modern UI to search, browse, and download from multiple sources in one place
- **Real-Time Progress** - Unified download queue with live status updates across all sources
- **Two Search Modes**:
  - **Direct Download** - Search and download from popular web sources
  - **Universal Mode** - Search metadata providers (Hardcover, Open Library) for richer book discovery and multi-source downloads *(additional sources in development - coming soon!)*
- **Format Support** - EPUB, MOBI, AZW3, FB2, DJVU, CBZ, CBR and more
- **Cloudflare Bypass** - Built-in bypasser for reliable access to protected sources
- **PWA Support** - Install as a mobile app for quick access
- **Docker Deployment** - Up and running in minutes

## 🖼️ Screenshots

**Home screen**
![Home screen](README_images/homescreen.png 'Home screen')

**Search results**
![Search results](README_images/search-results.png 'Search results')

**Multi-source downloads**
![Multi-source downloads](README_images/multi-source.png 'Multi-source downloads')

**Download queue**
![Download queue](README_images/downloads.png 'Download queue')

## 🚀 Quick Start

### Prerequisites

- Docker & Docker Compose

### Installation

1. Download the docker-compose file:
   ```bash
   curl -O https://raw.githubusercontent.com/calibrain/calibre-web-automated-book-downloader/main/docker-compose.yml
   ```

2. Start the service:
   ```bash
   docker compose up -d
   ```

3. Open `http://localhost:8084`

That's it! Configure settings through the web interface as needed.

### Volume Setup

```yaml
volumes:
  - /your/config/path:/config             # Config, database, and artwork cache directory
  - /your/download/path:/cwa-book-ingest  # Downloaded books
```

> **Tip**: Point the download volume to your CWA or Booklore ingest folder for automatic import.

> **Note**: CIFS shares require `nobrl` mount option to avoid database lock errors.

## ⚙️ Configuration

### Search Modes

**Direct Download Mode** (default)
- Works out of the box, no setup required
- Searches a huge library of books directly
- Returns downloadable releases immediately

**Universal Mode**
- Cleaner search results via metadata providers (Hardcover, Open Library)
- Aggregates releases from multiple configured sources
- Requires manual setup (API keys, additional sources)

Set the mode via Settings or `SEARCH_MODE` environment variable.

### Environment Variables

Environment variables work for initial setup and Docker deployments. They serve as defaults that can be overridden in the web interface.

| Variable | Description | Default |
|----------|-------------|---------|
| `FLASK_PORT` | Web interface port | `8084` |
| `INGEST_DIR` | Book download directory | `/cwa-book-ingest` |
| `TZ` | Container timezone | `UTC` |
| `PUID` / `PGID` | Runtime user/group ID (also supports legacy `UID`/`GID`) | `1000` / `1000` |
| `SEARCH_MODE` | `direct` or `universal` | `direct` |

Some of the additional options available in Settings:
- **AA Donator Key** - Use your paid account to skip Cloudflare challenges entirely and use faster, direct downloads
- **Preferred source** - Enable/disable and reorder available web sources for downloads
- **Library Link** - Add a link to your Calibre-Web or Booklore instance in the UI header
- **Content Folders** - Route fiction, non-fiction, comics, etc. to separate directories
- **Network Resilience** - Auto DNS rotation and mirror fallback when sources are unreachable. Custom proxy support (SOCK5 + HTTP/S), Tor routing.
- **Format & Language** - Filter downloads by preferred formats, languages and sorting order
- **Metadata Providers** - Configure API keys for Hardcover, Open Library, etc.

## 🐳 Docker Variants

### Standard
```bash
docker compose up -d
```

### Tor Variant
Routes all traffic through Tor for enhanced privacy:
```bash
curl -O https://raw.githubusercontent.com/calibrain/calibre-web-automated-book-downloader/main/docker-compose.tor.yml
docker compose -f docker-compose.tor.yml up -d
```

**Notes:**
- Requires `NET_ADMIN` and `NET_RAW` capabilities
- Timezone is auto-detected from Tor exit node
- Custom DNS/proxy settings are ignored

### External Cloudflare Resolver
Use FlareSolverr or ByParr instead of the built-in bypasser:
```bash
curl -O https://raw.githubusercontent.com/calibrain/calibre-web-automated-book-downloader/main/docker-compose.extbp.yml
docker compose -f docker-compose.extbp.yml up -d
```

Configure the resolver URL in Settings under the Cloudflare tab.

**When to use external vs internal bypasser:**
- **External** is useful if you already run FlareSolverr for other services (saves resources) or if you rarely need bypassing
- **Internal** (default) is faster and more reliable for most users - it's optimized specifically for this application

## 🔐 Authentication

Authentication is optional but recommended for shared or exposed instances. Enable in Settings.

**Alternative**: If you're running Calibre-Web, you can reuse its user database by mounting it:

```yaml
volumes:
  - /path/to/calibre-web/app.db:/auth/app.db:ro
```

## Health Monitoring

The application exposes a health endpoint at `/api/health` (no authentication required). Add a health check to your compose:

```yaml
healthcheck:
  test: ["CMD", "curl", "-sf", "http://localhost:8084/api/health"]
  interval: 30s
  timeout: 30s
  retries: 3
```

## Logging

Logs are available via:
- `docker logs <container-name>`
- `/var/log/cwa-book-downloader/` inside the container (when `ENABLE_LOGGING=true`)

Log level is configurable via Settings or `LOG_LEVEL` environment variable.

## Development

```bash
# Frontend development
make install     # Install dependencies
make dev         # Start Vite dev server (localhost:5173)
make build       # Production build
make typecheck   # TypeScript checks

# Backend (Docker)
make up          # Start backend via docker-compose.dev.yml
make down        # Stop services
make refresh     # Rebuild and restart
make restart     # Restart container
```

The frontend dev server proxies to the backend on port 8084.

### Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      Web Interface                          │
│                 (React + TypeScript + Vite)                 │
├─────────────────────────────────────────────────────────────┤
│                      Flask Backend                          │
│                   (REST API + WebSocket)                    │
├───────────────────┬─────────────────────┬───────────────────┤
│ Metadata Providers│   Download Queue    │  Cloudflare       │
│                   │   & Orchestrator    │  Bypass           │
├───────────────────┼─────────────────────┼───────────────────┤
│ • Hardcover       │ • Task scheduling   │ • Internal        │
│ • Open Library    │ • Progress tracking │ • External        │
│                   │ • Retry logic       │   (FlareSolverr)  │
├───────────────────┴─────────────────────┴───────────────────┤
│                     Release Sources                         │
├─────────────────────────────────────────────────────────────┤
│ • Direct Download (Anna's Archive → Libgen → Welib)         │
├─────────────────────────────────────────────────────────────┤
│                     Network Layer                           │
├─────────────────────────────────────────────────────────────┤
│ • Auto DNS rotation  • Mirror failover  • Resume support    │
└─────────────────────────────────────────────────────────────┘
```

The backend uses a plugin architecture. Metadata providers and release sources register via decorators and are automatically discovered.

## Contributing

Contributions are welcome! Please file issues or submit pull requests on GitHub.

> **Note**: Additional release sources and download clients are under active development. Want to add support for your favorite source? Check out the plugin architecture above and submit a PR!

## License

MIT License - see [LICENSE](LICENSE) for details.

## ⚠️ Disclaimers

### Copyright Notice

This tool can access various sources including those that might contain copyrighted material. Users are responsible for:
- Ensuring they have the right to download requested materials
- Respecting copyright laws and intellectual property rights
- Using the tool in compliance with their local regulations

### Library Integration

Downloads are written atomically (via intermediate `.crdownload` files) to prevent partial files from being ingested. However, if your library tool (CWA, Booklore, Calibre) is actively scanning or importing, there's a small chance of race conditions. If you experience database errors or import failures, try pausing your library's auto-import during bulk downloads.

## Support

For issues or questions, please [file an issue](https://github.com/calibrain/calibre-web-automated-book-downloader/issues) on GitHub.
