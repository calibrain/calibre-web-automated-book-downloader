ARG TARGETPLATFORM
ARG TARGETARCH
ARG BUILDPLATFORM
ARG BUILDARCH

# Frontend build stage.
FROM --platform=$BUILDPLATFORM node:24-alpine@sha256:50c8e8ca1d27439048670df5883f32d57cf81cff6233222c893fd0d9884cbd81 AS frontend-builder

# Helpful debug output to see what platforms BuildKit thinks it's using
RUN echo "BUILDPLATFORM=$BUILDPLATFORM BUILDARCH=$BUILDARCH TARGETPLATFORM=$TARGETPLATFORM TARGETARCH=$TARGETARCH"

WORKDIR /frontend

# Copy frontend package files
COPY src/frontend/package*.json ./

# Install dependencies (cache mount for faster rebuilds)
RUN --mount=type=cache,target=/root/.npm \
    npm ci

# Copy frontend source
COPY src/frontend/ ./

# Build the frontend
RUN npm run build

# uv is a build-time tool only, so it is mounted into the RUNs that need it rather
# than copied into the image. A COPY here would land ~24 MB in a `base` layer that
# every published image inherits, and a later `rm` cannot take it back out again --
# a RUN adds a layer, it does not rewrite the one underneath.
FROM ghcr.io/astral-sh/uv:0.12.13@sha256:b485bd65cc2cf1c9a93b3554012c9c3778cf7b1b5fd3d3096ce9e1226c97e1e6 AS uv

# Use python-slim as the base image
FROM python:3.14.7-slim@sha256:cad9a2c871761c413caa6fdd6441c783451e740a48aaeba60ae62a8b53525ef6 AS base

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
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONIOENCODING=UTF-8 \
    NAME=Shelfmark \
    PATH=/app/.venv/bin:$PATH \
    PYTHONPATH=/app \
    # PUID/PGID will be handled by entrypoint script, but TZ/Locale are still needed
    LANG=en_US.UTF-8 \
    LANGUAGE=en_US:en \
    LC_ALL=en_US.UTF-8

# Set ARG for build-time expansion (FLASK_PORT), ENV for runtime access
ENV FLASK_PORT=8084

# Configure locale, timezone, and perform initial cleanup in a single layer
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    # For building C-extensions (cffi, gevent, etc.)
    gcc \
    g++ \
    libffi-dev \
    python3-dev \
    # For locale
    locales tzdata \
    # For healthcheck
    curl \
    # For entrypoint
    dumb-init \
    # For debug
    zip iputils-ping \
    # For user switching
    gosu \
    # --- Tor support (activated via USING_TOR=true) ---
    tor \
    supervisor \
    iptables \
    # --- WireGuard support (activated via USING_WIREGUARD=true) ---
    wireguard-tools \
    iproute2 \
    procps \
    ca-certificates && \
    # Configure iptables alternatives for tor.sh compatibility
    update-alternatives --set iptables /usr/sbin/iptables-legacy && \
    update-alternatives --set ip6tables /usr/sbin/ip6tables-legacy && \
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

# Create a fixed runtime user/group so hardened Docker/Kubernetes deployments
# can start the container directly as a non-root user with a passwd entry.
RUN groupadd -g 1000 shelfmark && \
    useradd -u 1000 -g shelfmark -d /home/shelfmark -s /usr/sbin/nologin shelfmark && \
    mkdir -p /home/shelfmark && \
    chown 1000:1000 /home/shelfmark

# Set working directory
WORKDIR /app

# Install core Python dependencies first for better layer caching
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=from=uv,source=/uv,target=/usr/local/bin/uv \
    uv sync --locked --no-default-groups

# Runtime dependencies are installed into /app/.venv during the build. Remove the
# base image's system pip so stale installer CVEs do not ship in the final image.
RUN rm -rf \
        /usr/local/bin/pip \
        /usr/local/bin/pip3 \
        /usr/local/bin/pip3.* \
        /usr/local/lib/python*/site-packages/pip \
        /usr/local/lib/python*/site-packages/pip-*.dist-info

# Copy application code *after* dependencies are installed
COPY . .

# Copy built frontend from frontend-builder stage
COPY --from=frontend-builder /frontend/dist /app/frontend-dist

# Final setup: create image-owned runtime paths for the fixed non-root user.
# Root/PUID mode still re-homes ownership at startup when needed.
RUN mkdir -p \
        /config \
        /books \
        /var/log/shelfmark \
        /tmp/shelfmark/seleniumbase/downloaded_files \
        /tmp/shelfmark/seleniumbase/archived_files && \
    rm -rf /app/downloaded_files /app/archived_files && \
    ln -s /tmp/shelfmark/seleniumbase/downloaded_files /app/downloaded_files && \
    ln -s /tmp/shelfmark/seleniumbase/archived_files /app/archived_files && \
    chown -R 1000:1000 /config /books /home/shelfmark /tmp/shelfmark /var/log/shelfmark && \
    chmod -R a+rX /app && \
    chmod +x /app/entrypoint.sh /app/tor.sh /app/wireguard.sh /app/genDebug.sh

# Expose the application port
EXPOSE ${FLASK_PORT}

# Add healthcheck for container status
# Uses /api/health which doesn't require authentication.
# curl needs -f so an HTTP error status fails the probe instead of passing it:
# plain `curl -s` exits 0 on a 500, which reported a broken app as healthy.
# timeout stays well under interval so a hung probe cannot occupy a whole cycle.
# --start-interval matches the daemon default (5s), made explicit so startup
# probing does not depend on that default staying put.
HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --start-interval=5s --retries=3 \
    CMD curl -fsS http://localhost:${FLASK_PORT}/api/health > /dev/null || exit 1

# Use dumb-init as the entrypoint to handle signals properly
ENTRYPOINT ["/usr/bin/dumb-init", "--"]


FROM base AS shelfmark

# --- Chromium (PINNED to 149.0.7827.196) ---
# Debian's chromium 150.0.7871.46-1~deb13u1 security update (trixie-security,
# 2026-07-05) no longer opens the DevTools remote-debugging TCP port at all
# (no listener, no DevToolsActivePort file, even with a custom --user-data-dir;
# the RemoteDebuggingAllowed policy does not restore it). The SeleniumBase
# Pure-CDP driver connects through that port (/json/version), so with 150 every
# internal bypass dies with "Pure CDP browser startup failed" and all
# CF-gated downloads fail. Install the last working version from
# snapshot.debian.org until the bypasser can talk to Chromium >= 150 (e.g.
# pipe-based DevTools / UC mode) or seleniumbase ships a fix.
# Chrome 144+ requires --enable-unsafe-swiftshader for WebGL in Docker.
# This flag is set in internal_bypasser.py _get_browser_args()
ARG CHROMIUM_VERSION=149.0.7827.196-1~deb13u1
ARG CHROMIUM_SNAPSHOT=20260704T000000Z

RUN echo "deb [check-valid-until=no] https://snapshot.debian.org/archive/debian-security/${CHROMIUM_SNAPSHOT}/ trixie-security main" \
        > /etc/apt/sources.list.d/chromium-pin-snapshot.list && \
    apt-get update -o Acquire::Retries=5 && \
    apt-get install -y --no-install-recommends -o Acquire::Retries=5 \
    # For dumb display
    xvfb \
    # For screen recording
    ffmpeg \
    chromium=${CHROMIUM_VERSION} \
    chromium-common=${CHROMIUM_VERSION} \
    # For tkinter (pyautogui)
    python3-tk \
    # For RAR extraction
    unrar-free && \
    # Keep apt from "upgrading" chromium past the pin inside derived images
    printf 'Package: chromium chromium-common\nPin: version %s\nPin-Priority: 1001\n' "${CHROMIUM_VERSION}" \
        > /etc/apt/preferences.d/chromium-pin && \
    rm /etc/apt/sources.list.d/chromium-pin-snapshot.list && \
    # Create symlink so rarfile library can find unrar
    ln -sf /usr/bin/unrar-free /usr/bin/unrar && \
    # Cleanup APT cache
    apt-get purge -y --auto-remove -o APT::AutoRemove::RecommendsImportant=false && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Install the browser automation stack used by the full image
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=from=uv,source=/uv,target=/usr/local/bin/uv \
    uv sync --locked --no-default-groups --extra browser

# Deterministically resolve the Xlib namespace collision.
# pyautogui/mouseinfo pull the stale `python3-xlib` (0.15, 2014), while the
# `--extra browser` set pulls `python-xlib` (0.33). Both packages install into
# the same top-level `Xlib/` namespace, so whichever lands last wins. When the
# 2014 build wins, `Xlib.X` is missing `FamilyServerInterpreted`, which the
# SeleniumBase Pure-CDP driver requires at browser startup -> every bypass fails
# with "module 'Xlib.X' has no attribute 'FamilyServerInterpreted'" and no
# Cloudflare/DDoS-Guard protected download can complete. Drop the stale package
# and force python-xlib 0.33 to own the namespace. pyautogui runs fine against
# 0.33 (superset API).
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=from=uv,source=/uv,target=/usr/local/bin/uv \
    uv pip uninstall --python /app/.venv/bin/python python3-xlib && \
    uv pip install --python /app/.venv/bin/python --reinstall python-xlib==0.33 && \
    /app/.venv/bin/python -c "import Xlib.X; assert hasattr(Xlib.X, 'FamilyServerInterpreted'), 'Xlib.X.FamilyServerInterpreted missing after fix'; print('Xlib namespace OK:', Xlib.__version__)"

# Keep SeleniumBase's bundled driver cache writable for the fixed non-root user.
RUN SELENIUMBASE_DRIVERS_DIR=$(/app/.venv/bin/python -c "import pathlib, seleniumbase; print(pathlib.Path(seleniumbase.__file__).resolve().parent / 'drivers')") && \
    chown -R 1000:1000 "${SELENIUMBASE_DRIVERS_DIR}" && \
    chmod -R u+rwX,go+rX "${SELENIUMBASE_DRIVERS_DIR}" && \
    if [ -f "${SELENIUMBASE_DRIVERS_DIR}/uc_driver" ]; then chmod +x "${SELENIUMBASE_DRIVERS_DIR}/uc_driver"; fi

# Grant read/execute permissions to others
RUN chmod -R o+rx /usr/bin/chromium

# Default command to run the application entrypoint script
CMD ["/app/entrypoint.sh"]

FROM base AS shelfmark-lite

ENV USING_EXTERNAL_BYPASSER=true

CMD ["/app/entrypoint.sh"]
