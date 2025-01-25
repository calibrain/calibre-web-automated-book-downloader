#!/bin/bash
export INGEST_DIR=${INGEST_DIR:-/cwa-book-ingest}

mkdir -p /var/log/cwa-book-downloader
mkdir -p "$INGEST_DIR"

if [ "$UID" ] && [ -z "$GID" ]; then
    # Create group if it doesn't exist
    if ! getent group "$GID" >/dev/null; then
        groupadd -g "$GID" abc
    fi

    # Create user if it doesn't exist
    if ! id -u "$UID" >/dev/null 2>&1; then
        useradd -u "$UID" -g "$GID" -d /app -s /sbin/nologin abc
    fi
fi


# Adjust ownership of application directories
mkdir -p "$INGEST_DIR"
chown -R $UID:$GID /app "$INGEST_DIR" /var/log/cwa-book-downloader

# Switch to the created user and execute the main command
cd /app
exec gosu $UID python -m app
