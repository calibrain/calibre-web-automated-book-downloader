# Use Python slim image for smaller size
FROM python:3.12-slim

# Set environment variables
ENV DEBIAN_FRONTEND=noninteractive
ENV DOCKERMODE=true
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PIP_NO_CACHE_DIR=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
ENV PIP_DEFAULT_TIMEOUT=100
ENV NAME=Calibre-Web-Automated-Book-Downloader
ENV FLASK_HOST=0.0.0.0
ENV FLASK_PORT=8084
ENV FLASK_DEBUG=0
ENV CLOUDFLARE_PROXY_URL=http://localhost:8000
ENV INGEST_DIR=/cwa-book-ingest
ENV STATUS_TIMEOUT=3600
ENV PYTHONPATH=/app

# Default UID and GID (can be overridden at runtime)
ENV UID=1000
ENV GID=100

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends --no-install-suggests \
    calibre p7zip curl gosu \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN chmod +x /app/check_health.sh && \
    chmod +x /app/entrypoint.sh

# Expose port
EXPOSE ${FLASK_PORT}

# Health check
HEALTHCHECK --interval=30s --timeout=30s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:${FLASK_PORT}/request/api/status || exit 1

# Entrypoint
ENTRYPOINT ["/app/entrypoint.sh"]

# Start application
CMD ["python", "-m", "app"]

