# 📚 Calibre-Web-Automated-Book-Downloader

![Calibre-Web Automated Book Downloader](static/media/logo.png 'Calibre-Web Automated Book Downloader')

An intuitive web interface for searching and requesting book downloads, designed to work seamlessly with [Calibre-Web-Automated](https://github.com/crocodilestick/Calibre-Web-Automated). This project streamlines the process of downloading books and preparing them for integration into your Calibre library.

## ✨ Features

- 🌐 User-friendly web interface for book search and download
- 🔄 Automated download to your specified ingest folder
- 🔌 Seamless integration with Calibre-Web-Automated
- 📖 Support for multiple book formats (epub, mobi, azw3, fb2, djvu, cbz, cbr)
- 🛡️ Cloudflare bypass capability for reliable downloads
- 🐳 Docker-based deployment for quick setup

## 🖼️ Screenshots

![Main search interface Screenshot](README_images/search.png 'Main search interface')

![Details modal Screenshot placeholder](README_images/details.png 'Details modal')

![Download queue Screenshot placeholder](README_images/downloading.png 'Download queue')

## 🚀 Quick Start

### Prerequisites

- Docker
- Docker Compose
- A running instance of [Calibre-Web-Automated](https://github.com/crocodilestick/Calibre-Web-Automated) (recommended)

### Installation Steps

1. Get the docker-compose.yml:

   ```bash
   curl -O https://raw.githubusercontent.com/calibrain/calibre-web-automated-book-downloader/refs/heads/main/docker-compose.yml
   ```

2. Start the service:

   ```bash
   docker compose up -d
   ```

3. Access the web interface at `http://localhost:8084`

## ⚙️ Configuration

### Environment Variables

#### Application Settings

| Variable      | Description             | Default Value      |
| ------------- | ----------------------- | ------------------ |
| `FLASK_PORT`  | Web interface port      | `8084`             |
| `FLASK_DEBUG` | Debug mode toggle       | `false`            |
| `FLASK_HOST`  | Web interface binding   | `0.0.0.0`          |
| `INGEST_DIR`  | Book download directory | `/cwa-book-ingest` |
| `UID`         | Runtime user ID         | `1000`             |
| `GID`         | Runtime group ID        | `100`              |

#### Download Settings

| Variable               | Description                                               | Default Value                     |
| ---------------------- | --------------------------------------------------------- | --------------------------------- |
| `MAX_RETRY`            | Maximum retry attempts                                    | `3`                               |
| `DEFAULT_SLEEP`        | Retry delay (seconds)                                     | `5`                               |
| `MAIN_LOOP_SLEEP_TIME` | Processing loop delay (seconds)                           | `5`                               |
| `SUPPORTED_FORMATS`    | Supported book formats                                    | `epub,mobi,azw3,fb2,djvu,cbz,cbr` |
| `BOOK_LANGUAGE`        | Preferred language for books                              | `en`                              |
| `AA_DONATOR_KEY`       | Optional Donator key for Anna's Archive fast download API | ``                                |

Note that PDF are NOT supported at the moment (they do not get ingested by CWA, but if you want to just dowload them loclaly, you can add `pdf` to the `SUPPORTED_FORMATS` env

If you are a donator on AA, you can use your Key in `AA_DONATOR_API_KEY` to speed up downloads and bypass the wait times.

#### Network Settings

| Variable               | Description                   | Default Value           |
| ---------------------- | ----------------------------- | ----------------------- |
| `CLOUDFLARE_PROXY_URL` | Cloudflare bypass service URL | `http://localhost:8000` |
| `PORT`                 | Container external port       | `8084`                  |

### Volume Configuration

```yaml
volumes:
  - /your/local/path:/cwa-book-ingest
```

Mount should align with your Calibre-Web-Automated ingest folder.

## 🏗️ Architecture

The application consists of two key services:

1. **calibre-web-automated-bookdownloader**: Main application providing web interface and download functionality
2. **cloudflarebypassforscraping**: Support service for handling Cloudflare-protected websites

## 🏥 Health Monitoring

Built-in health checks monitor:

- Web interface availability
- Download service status
- Cloudflare bypass service connection

Checks run every 30 seconds with a 30-second timeout and 3 retries.

## 📝 Logging

Logs are available in:

- Container: `/var/logs/calibre-web-automated-bookdownloader.log`
- Docker logs: Access via `docker logs`

## 🤝 Contributing

Contributions are welcome! Feel free to submit a Pull Request.

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## ⚠️ Important Disclaimers

### Copyright Notice

While this tool can access various sources including those that might contain copyrighted material (e.g., Anna's Archive), it is designed for legitimate use only. Users are responsible for:

- Ensuring they have the right to download requested materials
- Respecting copyright laws and intellectual property rights
- Using the tool in compliance with their local regulations

### Duplicate Downloads Warning

Please note that the current version:

- Does not check for existing files in the download directory
- Does not verify if books already exist in your Calibre database
- Exercise caution when requesting multiple books to avoid duplicates

## 💬 Support

For issues or questions, please file an issue on the GitHub repository.
