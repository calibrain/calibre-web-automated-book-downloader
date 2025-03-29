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

| Variable          | Description             | Default Value      |
| ----------------- | ----------------------- | ------------------ |
| `FLASK_PORT`      | Web interface port      | `8084`             |
| `FLASK_DEBUG`     | Debug mode toggle       | `false`            |
| `FLASK_HOST`      | Web interface binding   | `0.0.0.0`          |
| `INGEST_DIR`      | Book download directory | `/cwa-book-ingest` |
| `UID`             | Runtime user ID         | `1000`             |
| `GID`             | Runtime group ID        | `100`              |
| `ENABLE_LOGGING`  | Enable log file         | `true`             |
| `LOG_LEVEL`       | Log level to use        | `info`             |

If logging is enabld, log folder default location is `/var/log/cwa-book-downloader`
Available log levels: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`. Higher levels show fewer messages.

#### Download Settings

| Variable               | Description                                               | Default Value                     |
| ---------------------- | --------------------------------------------------------- | --------------------------------- |
| `MAX_RETRY`            | Maximum retry attempts                                    | `3`                               |
| `DEFAULT_SLEEP`        | Retry delay (seconds)                                     | `5`                               |
| `MAIN_LOOP_SLEEP_TIME` | Processing loop delay (seconds)                           | `5`                               |
| `SUPPORTED_FORMATS`    | Supported book formats                                    | `epub,mobi,azw3,fb2,djvu,cbz,cbr` |
| `BOOK_LANGUAGE`        | Preferred language for books                              | `en`                              |
| `AA_DONATOR_KEY`       | Optional Donator key for Anna's Archive fast download API | ``                                |
| `USE_BOOK_TITLE`       | Use book title as filename instead of ID                  | `false`                           |

If you change `BOOK_LANGUAGE`, you can add multiple comma separated languages, such as `en,fr,ru` etc.  

#### AA 

| Variable               | Description                                               | Default Value                     |
| ---------------------- | --------------------------------------------------------- | --------------------------------- |
| `AA_BASE_URL`          | Base URL of Annas-Archive (could be changed for a proxy)  | `https://annas-archive.org`       |
| `USE_CF_BYPASS`        | Disable CF bypass and use alternative links instead       | `true`                           |

If you are a donator on AA, you can use your Key in `AA_DONATOR_KEY` to speed up downloads and bypass the wait times.
If disabling the cloudflare bypass, you will be using alternative download hosts, such as libgen or z-lib, but they usually have a delay before getting the more recent books and their collection is not as big as aa's. But this setting should work for the majority of books.

#### Network Settings

| Variable               | Description                     | Default Value           |
| ---------------------- | ------------------------------- | ----------------------- |
| `AA_ADDITIONAL_URLS`   | Proxy URLs for AA (, separated) | ``                      |
| `HTTP_PROXY`           | HTTP proxy URL                  | ``                      |
| `HTTPS_PROXY`          | HTTPS proxy URL                 | ``                      |
| `CUSTOM_DNS`           | Custom DNS IP                   | ``                      |
| `USE_DOH`              | Use DNS over HTTPS              | `false`                 |

For proxy configuration, you can specify URLs in the following format:
```bash
# Basic proxy
HTTP_PROXY=http://proxy.example.com:8080
HTTPS_PROXY=http://proxy.example.com:8080

# Proxy with authentication
HTTP_PROXY=http://username:password@proxy.example.com:8080
HTTPS_PROXY=http://username:password@proxy.example.com:8080
```


The `CUSTOM_DNS` setting supports two formats:

1. **Custom DNS Servers**: A comma-separated list of DNS server IP addresses
   - Example: `127.0.0.53,127.0.1.53` (useful for PiHole)
   - Supports both IPv4 and IPv6 addresses in the same string

2. **Preset DNS Providers**: Use one of these predefined options:
   - `google` - Google DNS
   - `quad9` - Quad9 DNS
   - `cloudflare` - Cloudflare DNS
   - `opendns` - OpenDNS

For users experiencing ISP-level website blocks (such as Virgin Media in the UK), using alternative DNS providers like Cloudflare may help bypass these restrictions

If a `CUSTOM_DNS` is specified from the preset providers, you can also set a `USE_DOH=true` to force using DNS over HTTPS,
which might also help in certain network situations. Note that only `google`, `quad9`, `cloudflare` and `opendns` are 
supported for now, and any other value in `CUSTOM_DNS` will make the `USE_DOH` flag ignored.

Try something like this :
```bash
CUSTOM_DNS=cloudflare
USE_DOH=true
```

#### Custom configuration

| Variable               | Description                                                 | Default Value           |
| ---------------------- | ----------------------------------------------------------- | ----------------------- |
| `CUSTOM_SCRIPT`        | Path to an executable script that tuns after each download  | ``                      |

If `CUSTOM_SCRIPT` is set, it will be executed after each successful download but before the file is moved to the ingest directory. This allows for custom processing like format conversion or validation.

The script is called with the full path of the downloaded file as its argument. Important notes:
- The script must preserve the original filename for proper processing
- The file can be modified or even deleted if needed
- The file will be moved to `/cwa-book-ingest` after the script execution (if not deleted)

You can specify these configuration in this format :
```
environment:
  - CUSTOM_SCRIPT=/scripts/process-book.sh

volumes:
  - local/scripts/custom_script.sh:/scripts/process-book.sh
```

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

