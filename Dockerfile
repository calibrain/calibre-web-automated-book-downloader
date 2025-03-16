FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive \
    DOCKERMODE=true \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=100 \
    NAME=Calibre-Web-Automated-Book-Downloader \
    FLASK_HOST=0.0.0.0 \
    FLASK_PORT=8084 \
    FLASK_DEBUG=0 \
    STATUS_TIMEOUT=3600 \
    PYTHONPATH=/app \
    USE_CF_BYPASS=true \
    AA_BASE_URL=https://annas-archive.org \
    UID=1000 \
    GID=100

# Install minimal dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends --no-install-suggests \
    curl \
    xvfb \
    chromium-driver \
    dumb-init && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies including playwright
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    rm -rf /root/.cache /app/.cache

COPY . .
RUN chmod +x /app/entrypoint.sh && \
    # Create necessary directories
    mkdir -p /var/log/cwa-book-downloader && \
    mkdir -p /cwa-book-ingest

EXPOSE ${FLASK_PORT}

HEALTHCHECK --interval=30s --timeout=30s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:${FLASK_PORT}/request/api/status || exit 1

ENTRYPOINT ["/usr/bin/dumb-init", "--"]
CMD ["/app/entrypoint.sh"]