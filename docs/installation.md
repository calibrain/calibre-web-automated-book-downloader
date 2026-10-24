# Installation

Shelfmark is typically deployed with Docker Compose.

## Quick Start

1. Download the compose file from the repository:

```bash
curl -O https://raw.githubusercontent.com/calibrain/shelfmark/main/compose/docker-compose.yml
```

2. Start the service:

```bash
docker compose up -d
```

3. Open `http://localhost:8084`

4. Configure the sources, metadata providers, and delivery settings you want to use

## Next Steps

- For volume and path setup, see [Directory and Volume Setup](configuration.md)
- For environment-based setup, see [Environment Variables](environment-variables.md)
- For authentication and user management, see [Users & Requests](users-and-requests.md) and [OIDC](oidc.md)

## Notes

- Universal search is the default mode for new installs
- Direct Download is optional and must be enabled and configured before it can be used
- Torrent and usenet setups require matching download paths between Shelfmark and your download client
- The Lite image and the Tor and WireGuard variants have their own compose files, described under Docker Variants in the [README](../readme.md)
