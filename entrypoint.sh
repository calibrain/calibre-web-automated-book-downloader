#!/bin/bash
LOG_DIR=${LOG_ROOT:-/var/log/}/cwa-book-downloader
mkdir -p $LOG_DIR
LOG_FILE=${LOG_DIR}/cwa-bd_entrypoint.log

# Cleanup any existing files or folders in the log directory
rm -rf $LOG_DIR/*

(
    if [ "$USING_TOR" = "true" ]; then
        ./tor.sh
    fi
)

exec 3>&1 4>&2
exec > >(tee -a $LOG_FILE) 2>&1
echo "Starting entrypoint script"
echo "Log file: $LOG_FILE"
set -e

# Print build version
echo "Build version: $BUILD_VERSION"

# Configure timezone
if [ "$TZ" ]; then
    echo "Setting timezone to $TZ"
    ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone
fi

# Set UID if not set
if [ -z "$UID" ]; then
    UID=1000
fi

# Set GID if not set
if [ -z "$GID" ]; then
    GID=100
fi

if ! getent group "$GID" >/dev/null; then
    echo "Adding group $GID with name appuser"
    groupadd -g "$GID" appuser
fi

# Create user if it doesn't exist
if ! id -u "$UID" >/dev/null 2>&1; then
    echo "Adding user $UID with name appuser"
    useradd -u "$UID" -g "$GID" -d /app -s /sbin/nologin appuser
fi

# Get username for the UID (whether we just created it or it existed)
USERNAME=$(getent passwd "$UID" | cut -d: -f1)
echo "Username for UID $UID is $USERNAME"

test_write() {
    folder=$1
    test_file=$folder/calibre-web-automated-book-downloader_TEST_WRITE
    mkdir -p $folder
    (
        echo 0123456789_TEST | sudo -E -u "$USERNAME" HOME=/app tee $test_file > /dev/null
    )
    FILE_CONTENT=$(cat $test_file || echo "")
    rm -f $test_file
    [ "$FILE_CONTENT" = "0123456789_TEST" ]
    result=$?
    if [ $result -eq 0 ]; then
        result_text="true"
    else
        result_text="false"
    fi
    echo "Test write to $folder by $USERNAME: $result_text"
    return $result
}

make_writable() {
    folder=$1
    set +e
    test_write $folder
    is_writable=$?
    set -e
    if [ $is_writable -eq 0 ]; then
        echo "Folder $folder is writable, no need to change ownership"
    else
        echo "Folder $folder is not writable, changing ownership"
        change_ownership $folder
        chmod g+r,g+w $folder || echo "Failed to change group permissions for ${folder}, continuing..."
    fi
    test_write $folder || echo "Failed to test write to ${folder}, continuing..."
}

# Ensure proper ownership of application directories
change_ownership() {
  folder=$1
  mkdir -p $folder
  echo "Changing ownership of $folder to $USERNAME:$GID"
  chown -R "${UID}" "${folder}" || echo "Failed to change user ownership for ${folder}, continuing..."
  chown -R ":${GID}" "${folder}" || echo "Failed to change group ownership for ${folder}, continuing..."
}

change_ownership /app
change_ownership /var/log/cwa-book-downloader
change_ownership /tmp/cwa-book-downloader

# Test write to all folders
make_writable /cwa-book-ingest

# Set the command to run based on the environment
is_prod=$(echo "$APP_ENV" | tr '[:upper:]' '[:lower:]')
if [ "$is_prod" = "prod" ]; then 
    command="gunicorn -t 300 -b ${FLASK_HOST:-0.0.0.0}:${FLASK_PORT:-8084} app:app"
else
    command="python3 app.py"
fi

# Hacky way to verify /tmp has at least 1MB of space and is writable/readable
echo "Verifying /tmp has enough space"
rm -f /tmp/test.cwa-bd
for i in {1..150000}; do printf "%04d\n" $i; done > /tmp/test.cwa-bd
sum=$(python3 -c "print(sum(int(l.strip()) for l in open('/tmp/test.cwa-bd').readlines()))")
[ "$sum" == 11250075000 ] && echo "Success: /tmp is writable" || (echo "Failure: /tmp is not writable" && exit 1)
rm /tmp/test.cwa-bd

echo "Running command: '$command' as '$USERNAME' in '$APP_ENV' mode"

# Stop logging
exec 1>&3 2>&4
exec 3>&- 4>&-

exec sudo -E -u "$USERNAME" HOME=/app $command
