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
echo "Release version: $RELEASE_VERSION"

# Configure timezone
if [ "$TZ" ]; then
    echo "Setting timezone to $TZ"
    ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone
fi

# Determine user ID with proper precedence:
# 1. PUID (LinuxServer.io standard - recommended)
# 2. UID (legacy, for backward compatibility with existing installs)
# 3. Default to 1000
#
# Note: $UID is a bash builtin that's always set. We use `printenv` to detect
# if UID was explicitly set as an environment variable (e.g., via docker-compose).
if [ -n "$PUID" ]; then
    RUN_UID="$PUID"
    echo "Using PUID=$RUN_UID"
elif printenv UID >/dev/null 2>&1; then
    RUN_UID="$(printenv UID)"
    echo "Using UID=$RUN_UID (legacy - consider migrating to PUID)"
else
    RUN_UID=1000
    echo "Using default UID=$RUN_UID"
fi

# Determine group ID with proper precedence:
# 1. PGID (LinuxServer.io standard - recommended)
# 2. GID (legacy, for backward compatibility with existing installs)
# 3. Default to 1000
if [ -n "$PGID" ]; then
    RUN_GID="$PGID"
    echo "Using PGID=$RUN_GID"
elif [ -n "$GID" ]; then
    RUN_GID="$GID"
    echo "Using GID=$RUN_GID (legacy - consider migrating to PGID)"
else
    RUN_GID=1000
    echo "Using default GID=$RUN_GID"
fi

if ! getent group "$RUN_GID" >/dev/null; then
    echo "Adding group $RUN_GID with name appuser"
    groupadd -g "$RUN_GID" appuser
fi

# Create user if it doesn't exist
if ! id -u "$RUN_UID" >/dev/null 2>&1; then
    echo "Adding user $RUN_UID with name appuser"
    useradd -u "$RUN_UID" -g "$RUN_GID" -d /app -s /sbin/nologin appuser
fi

# Get username for the UID (whether we just created it or it existed)
USERNAME=$(getent passwd "$RUN_UID" | cut -d: -f1)
echo "Username for UID $RUN_UID is $USERNAME"

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
  echo "Changing ownership of $folder to $USERNAME:$RUN_GID"
  chown -R "${RUN_UID}" "${folder}" || echo "Failed to change user ownership for ${folder}, continuing..."
  chown -R ":${RUN_GID}" "${folder}" || echo "Failed to change group ownership for ${folder}, continuing..."
}

change_ownership /app
change_ownership /var/log/cwa-book-downloader
change_ownership /tmp/cwa-book-downloader

# Test write to all folders
make_writable ${CONFIG_DIR:-/config}
make_writable /cwa-book-ingest

# Always run Gunicorn (even when DEBUG=true) to ensure Socket.IO WebSocket
# upgrades work reliably on customer machines.
# Map app LOG_LEVEL (often DEBUG/INFO/...) to gunicorn's --log-level (lowercase).
gunicorn_loglevel=$([ "$DEBUG" = "true" ] && echo debug || echo "${LOG_LEVEL:-info}" | tr '[:upper:]' '[:lower:]')
command="gunicorn --log-level ${gunicorn_loglevel} --access-logfile - --error-logfile - --worker-class geventwebsocket.gunicorn.workers.GeventWebSocketWorker --workers 1 -t 300 -b ${FLASK_HOST:-0.0.0.0}:${FLASK_PORT:-8084} cwa_book_downloader.main:app"

# If DEBUG and not using an external bypass
if [ "$DEBUG" = "true" ] && [ "$USING_EXTERNAL_BYPASSER" != "true" ]; then
    set +e
    set -x
    echo "vvvvvvvvvvvv DEBUG MODE vvvvvvvvvvvv"
    echo "Starting Xvfb for debugging"
    python3 -c "from pyvirtualdisplay import Display; Display(visible=False, size=(1440,1880)).start()"
    id
    free -h
    uname -a
    ulimit -a
    df -h /tmp
    env | sort
    mount
    cat /proc/cpuinfo
    echo "==========================================="
    echo "Debugging Chrome itself"
    chromium --version
    mkdir -p /tmp/chrome_crash_dumps
    timeout --preserve-status 5s chromium \
            --headless=new \
            --no-sandbox \
            --disable-gpu \
            --enable-logging --v=1 --log-level=0 \
            --log-file=/tmp/chrome_entrypoint_test.log \
            --crash-dumps-dir=/tmp/chrome_crash_dumps \
            < /dev/null 
    EXIT_CODE=$?
    echo "Chrome exit code: $EXIT_CODE"
    ls -lh /tmp/chrome_entrypoint_test.log
    ls -lh /tmp/chrome_crash_dumps
    if [[ "$EXIT_CODE" -ne 0 && "$EXIT_CODE" -le 127 ]]; then
        echo "Chrome failed to start. Lets trace it"
        apt-get update && apt-get install -y strace
        timeout --preserve-status 10s strace -f -o "/tmp/chrome_strace.log" chromium \
                --headless=new \
                --no-sandbox \
                --version \
                < /dev/null
        EXIT_CODE=$?
        echo "Strace exit code: $EXIT_CODE"
        echo "Strace log:"
        cat /tmp/chrome_strace.log
    fi

    pkill -9 -f Xvfb
    pkill -9 -f chromium
    sleep 1
    ps aux
    set +x
    set -e
    echo "^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^"
fi

# Verify /tmp has at least 1MB of space and is writable/readable
echo "Verifying /tmp has enough space"
rm -f /tmp/test.cwa-bd
if dd if=/dev/zero of=/tmp/test.cwa-bd bs=1M count=1 2>/dev/null && \
   [ "$(wc -c < /tmp/test.cwa-bd)" -eq 1048576 ]; then
    rm -f /tmp/test.cwa-bd
    echo "Success: /tmp is writable and readable"
else
    echo "Failure: /tmp is not writable or has insufficient space"
    exit 1
fi

echo "Running command: '$command' as '$USERNAME' (debug=$is_debug)"

# Stop logging
exec 1>&3 2>&4
exec 3>&- 4>&-

exec sudo -E -u "$USERNAME" HOME=/app $command
