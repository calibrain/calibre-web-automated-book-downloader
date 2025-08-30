# Use python-slim as the base image
FROM python:3.10-slim AS base

# Add build argument for version
ARG BUILD_VERSION
ENV BUILD_VERSION=${BUILD_VERSION}
ARG RELEASE_VERSION
ENV RELEASE_VERSION=${RELEASE_VERSION}

# Set shell to bash with pipefail option
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Consistent environment variables grouped together
ENV DEBIAN_FRONTEND=noninteractive \
    DOCKERMODE=true \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONIOENCODING=UTF-8 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=100 \
    NAME=Calibre-Web-Automated-Book-Downloader \
    PYTHONPATH=/app \
    # UID/GID will be handled by entrypoint script, but TZ/Locale are still needed
    LANG=en_US.UTF-8 \
    LANGUAGE=en_US:en \
    LC_ALL=en_US.UTF-8 \
    APP_ENV=prod

# Set ARG for build-time expansion (FLASK_PORT), ENV for runtime access
ENV FLASK_PORT=8084

# Configure locale, timezone, and perform initial cleanup in a single layer
# User/group creation is removed
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    # For locale
    locales tzdata \
    # For healthcheck
    curl \
    # For entrypoint
    dumb-init \
    # For debug
    zip iputils-ping \
    # For user switching
    sudo && \
    # Cleanup APT cache *after* all installs in this layer
    apt-get purge -y --auto-remove -o APT::AutoRemove::RecommendsImportant=false && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* && \
    # Default to UTC timezone but will be overridden by the entrypoint script
    ln -snf /usr/share/zoneinfo/UTC /etc/localtime && echo UTC > /etc/timezone && \
    # Configure locale
    sed -i '/en_US.UTF-8/s/^# //g' /etc/locale.gen && \
    locale-gen en_US.UTF-8 && \
    echo "LC_ALL=en_US.UTF-8" >> /etc/environment && \
    echo "LANG=en_US.UTF-8" > /etc/locale.conf

# Set working directory
WORKDIR /app

# Install Python dependencies using pip
# Upgrade pip first, then copy requirements and install
# Copying requirements-base.txt separately leverages build cache
COPY requirements-base.txt .
RUN pip install --no-cache-dir -r requirements-base.txt && \
    # Clean root's pip cache
    rm -rf /root/.cache

# Copy application code *after* dependencies are installed
COPY . .

# Final setup: permissions and directories in one layer
# Only creating directories and setting executable bits.
# Ownership will be handled by the entrypoint script.
RUN mkdir -p /var/log/cwa-book-downloader /cwa-book-ingest && \
    chmod +x /app/entrypoint.sh /app/tor.sh /app/genDebug.sh

# Expose the application port
EXPOSE ${FLASK_PORT}

# Add healthcheck for container status
# This will run as root initially, but check localhost which should work if the app binds correctly.
HEALTHCHECK --interval=60s --timeout=60s --start-period=60s --retries=3 \
    CMD curl -s http://localhost:${FLASK_PORT}/request/api/status > /dev/null || exit 1

# Use dumb-init as the entrypoint to handle signals properly
ENTRYPOINT ["/usr/bin/dumb-init", "--"]


FROM base AS cwa-bd

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    # For dumb display
    xvfb \
    # For screen recording
    ffmpeg \
    # --- Chromium ---
    chromium \
    # --- ChromeDriver ---
    chromium-driver \
    # For tkinter (pyautogui)
    python3-tk

# install additional dependencies
COPY requirements-cwa-bd.txt .
RUN pip install --no-cache-dir -r requirements-cwa-bd.txt && \
    # Clean root's pip cache
    rm -rf /root/.cache

# Add this line to grant read/execute permissions to others
RUN chmod -R o+rx /usr/bin/chromium && \
    chmod -R o+rx /usr/bin/chromedriver && \
    chmod -R o+w /usr/local/lib/python3.10/site-packages/seleniumbase/drivers/

# Default command to run the application entrypoint script
CMD ["/app/entrypoint.sh"]

FROM cwa-bd AS cwa-bd-tor

ENV USING_TOR=true

# Install Tor and dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    # --- Tor ---
    tor \
    # --- iptables ---
    iptables && \
    update-alternatives --set iptables /usr/sbin/iptables-legacy && \
    update-alternatives --set ip6tables /usr/sbin/ip6tables-legacy && \
    # Cleanup APT cache *after* all installs in this layer
    apt-get purge -y --auto-remove -o APT::AutoRemove::RecommendsImportant=false && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Override the default command to run Tor
CMD ["/app/entrypoint.sh"]

FROM base AS cwa-bd-extbp

ENV USING_EXTERNAL_BYPASSER=true

CMD ["/app/entrypoint.sh"]
