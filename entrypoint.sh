#!/bin/bash
set -e

# Set UID if not set
if [ -z "$UID" ]; then
    UID=1000
fi

# Set GID if not set
if [ -z "$GID" ]; then
    GID=100
fi

if ! getent group "$GID" >/dev/null; then
    groupadd -g "$GID" appuser
fi

# Create user if it doesn't exist
if ! id -u "$UID" >/dev/null 2>&1; then
    useradd -u "$UID" -g "$GID" -d /app -s /sbin/nologin appuser
fi

# Get username for the UID (whether we just created it or it existed)
USERNAME=$(getent passwd "$UID" | cut -d: -f1)

# Ensure proper ownership of application directories
change_ownership() {
  folder=$1
  chown -R "${UID}:${GID}" "${folder}" || echo "Failed to change ownership for ${folder}, continuing..."
}

change_ownership /app
change_ownership /var/log/cwa-book-downloader
change_ownership /cwa-book-ingest

# Switch to the user (either newly created or existing) and execute the main command
exec su -s /bin/bash "$USERNAME" -c "python -m app" 
