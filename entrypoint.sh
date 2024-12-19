#!/bin/bash
set -e

mkdir -p /var/logs
mkdir -p "$INGEST_DIR"

# Create group if it doesn't exist
if ! getent group "$GID" >/dev/null; then
    groupadd -g "$GID" abc
fi

# Create user if it doesn't exist
if ! id -u "$UID" >/dev/null 2>&1; then
    useradd -u "$UID" -g "$GID" -d /app -s /sbin/nologin abc
fi

# Adjust ownership of application directories
chown -R $UID:$GID /app "$INGEST_DIR" /var/logs

# Switch to the created user and execute the main command
exec gosu $UID "$@"

