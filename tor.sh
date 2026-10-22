#!/bin/bash

is_truthy() {
    case "${1,,}" in
        true|yes|1|y) return 0 ;;
        *) return 1 ;;
    esac
}

ENABLE_LOGGING_VALUE="${ENABLE_LOGGING:-true}"

LOG_DIR=${LOG_ROOT:-/var/log/}/shelfmark
LOG_FILE="${LOG_DIR}/shelfmark_tor.log"

if is_truthy "$ENABLE_LOGGING_VALUE"; then
    mkdir -p "$LOG_DIR"

    exec 3>&1 4>&2
    exec > >(tee -a "$LOG_FILE") 2>&1
fi
echo "Starting tor script"
if is_truthy "$ENABLE_LOGGING_VALUE"; then
    echo "Log file: $LOG_FILE"
else
    echo "File logging disabled (ENABLE_LOGGING=$ENABLE_LOGGING_VALUE)"
fi

set +x
set -e

# Check if EXT_BYPASSER_URL is defined
if [ -n "$EXT_BYPASSER_URL" ]; then
    echo "Extracting hostname and ip from bypasser into /etc/hosts"

    # Extract hostname
    hostname=$(echo "$EXT_BYPASSER_URL" | cut -d'/' -f3 | cut -d':' -f1)

    # Resolve to IP (using current DNS before switching to TOR)
    ip=$(getent hosts "$hostname" 2>/dev/null | awk '{print $1}')

    # If getent fails, try dig
    if [ -z "$ip" ]; then
        ip=$(dig +short "$hostname" 2>/dev/null | head -n1)
    fi

    # Only proceed if we got an IP and hostname is not already an IP
    if [ -n "$ip" ] && [ "$ip" != "$hostname" ]; then
        # Add to /etc/hosts (remove existing entry first to avoid duplicates)
        sudo sed -i "/[[:space:]]$hostname$/d" /etc/hosts
        echo "$ip $hostname" | sudo tee -a /etc/hosts > /dev/null
        echo "Added to /etc/hosts: $ip $hostname"
    else
        echo "Skipping: $hostname is already an IP or could not be resolved"
    fi
else
    echo "EXT_BYPASSER_URL not defined, skipping /etc/hosts update"
fi

echo "[*] Running tor script..."

echo "Build version: $BUILD_VERSION"
echo "Release version: $RELEASE_VERSION"

echo "[*] Installing Tor and dependencies..."
echo "[*] Writing Tor transparent proxy config..."

TOR_NOTICES_LOG=/var/log/tor/notices.log
# The account Tor drops to once its listeners are bound (DNSPort 53 needs root).
# The NAT rules below exempt this account's traffic from the transparent
# redirect. Tor used to stay root, which the exemption never matched, so every
# relay connection Tor opened after the rules went live was redirected back
# into its own TransPort.
TOR_USER=debian-tor

cat <<EOF > /etc/tor/torrc
User ${TOR_USER}
DataDirectory /var/lib/tor
VirtualAddrNetworkIPv4 10.192.0.0/10
AutomapHostsOnResolve 1
TransPort 9040
DNSPort 53
Log notice file ${TOR_NOTICES_LOG}

# Circuit management to prevent stale circuits after inactivity
MaxCircuitDirtiness 600
NewCircuitPeriod 30
CircuitBuildTimeout 60
LearnCircuitBuildTimeout 0

# Keep circuits alive
KeepalivePeriod 60
CircuitStreamTimeout 60

# Prevent connection timeouts
SocksTimeout 120
EOF

echo "[*] Setting up DNS..."
cat <<EOF > /etc/resolv.conf
nameserver 127.0.0.1
EOF

echo "[*] Starting Tor..."
echo "[*] Configuring Supervisor..."
mkdir -p /var/log/supervisor
cat <<EOF > /etc/supervisor/supervisord.conf
[supervisord]
nodaemon=false
logfile=/var/log/supervisor/supervisord.log
pidfile=/var/run/supervisord.pid
user=root

[unix_http_server]
file=/var/run/supervisor.sock   ; (the path to the socket file)

[rpcinterface:supervisor]
supervisor.rpcinterface_factory = supervisor.rpcinterface:make_main_rpcinterface

[supervisorctl]
serverurl=unix:///var/run/supervisor.sock ; use a unix:// URL  for a unix socket

[program:tor]
command=/usr/bin/tor -f /etc/tor/torrc
autostart=true
autorestart=true
startretries=100
stdout_logfile=/var/log/supervisor/tor.log
stderr_logfile=/var/log/supervisor/tor.err.log

[program:tor-healthcheck]
command=/app/tor_healthcheck.sh
autostart=true
autorestart=true
stdout_logfile=/var/log/supervisor/healthcheck.log
stderr_logfile=/var/log/supervisor/healthcheck.err.log
EOF

# Create healthcheck script
cat <<'HC' > /app/tor_healthcheck.sh
#!/bin/bash

# Tor watchdog.
#
# Once the NAT rules are live every process in the container is pinned to Tor,
# so a Tor that has died or wedged takes the whole app down with it and cannot
# reliably be repaired in place. When Tor stops being usable, tear the container
# down instead: the configured Docker restart policy brings it back and tor.sh
# rebuilds torrc, supervisor and the iptables rules from scratch.

CHECK_INTERVAL=${TOR_CHECK_INTERVAL:-30}
# Strikes before the container is reset. 2 means "one retry": a single failed
# probe is tolerated, a second consecutive failure resets.
MAX_FAILURES=${TOR_MAX_FAILURES:-2}
NOTICES_LOG=${TOR_NOTICES_LOG:-/var/log/tor/notices.log}
TRANS_PORT=${TOR_TRANS_PORT:-9040}
# PID 1 is dumb-init, which tears the container down when it is signalled.
CONTAINER_PID=${TOR_CONTAINER_PID:-1}

tor_process_running() {
    supervisorctl status tor 2>/dev/null | grep -q "RUNNING"
}

tor_has_bootstrapped() {
    grep -q "Bootstrapped 100%" "$NOTICES_LOG" 2>/dev/null
}

# Liveness probe for "Tor is still running but no longer usable". Deliberately a
# loopback check against Tor's own TransPort: probing the clear net from here
# would leak and defeat the point of the proxy.
tor_accepts_connections() {
    timeout 5 bash -c "exec 3<>/dev/tcp/127.0.0.1/${TRANS_PORT}" 2>/dev/null
}

tor_is_healthy() {
    tor_process_running && tor_has_bootstrapped && tor_accepts_connections
}

reset_container() {
    echo "$(date): Tor still unhealthy after ${MAX_FAILURES} checks - resetting container."
    kill -TERM "$CONTAINER_PID" 2>/dev/null
    exit 1
}

# Never police Tor before it has bootstrapped once. A first bootstrap can
# legitimately take minutes on a slow link, and mistaking that for a failure
# would reset the container into an endless restart loop.
echo "$(date): Waiting for initial Tor bootstrap before monitoring..."
while ! tor_has_bootstrapped; do
    sleep "$CHECK_INTERVAL"
done
echo "$(date): Tor bootstrapped - watchdog active (every ${CHECK_INTERVAL}s, ${MAX_FAILURES} strikes)."

FAIL_COUNT=0
while true; do
    if tor_is_healthy; then
        if [ "$FAIL_COUNT" -ne 0 ]; then
            echo "$(date): Tor recovered."
        fi
        FAIL_COUNT=0
    else
        FAIL_COUNT=$((FAIL_COUNT+1))
        echo "$(date): Healthcheck failed (${FAIL_COUNT}/${MAX_FAILURES})"

        if [ "$FAIL_COUNT" -ge "$MAX_FAILURES" ]; then
            reset_container
        fi

        echo "$(date): Retrying in ${CHECK_INTERVAL}s before resetting..."
    fi

    sleep "$CHECK_INTERVAL"
done
HC
chmod +x /app/tor_healthcheck.sh

# Tor appends to its notices log, and the container filesystem survives a
# restart (docker restart, a Docker daemon restart, a host reboot). The
# "Bootstrapped 100%" line left by the previous boot made the wait below, and
# the watchdog, treat a Tor that had not connected to anything yet as ready. The
# NAT rules then went live too early and the connectivity check failed on every
# boot from then on (#1502). Start each boot with an empty log and keep the
# previous one.
if [ -f "$TOR_NOTICES_LOG" ]; then
    mv -f "$TOR_NOTICES_LOG" "${TOR_NOTICES_LOG}.prev" || true
fi
# Tor opens the log after it has dropped to TOR_USER, and refuses to start at
# all if it cannot.
: > "$TOR_NOTICES_LOG"
chown "$TOR_USER" "$TOR_NOTICES_LOG"

echo "[*] Starting Tor via Supervisor..."
/usr/bin/supervisord -c /etc/supervisor/supervisord.conf

# Wait a bit to ensure Tor has bootstrapped
echo "[*] Waiting for Tor to finish bootstrapping... (up to 5 minutes)"
BOOTSTRAP_TIMEOUT=300
BOOTSTRAP_START=$(date +%s)
while true; do
    if grep -q "Bootstrapped 100%" "$TOR_NOTICES_LOG" 2>/dev/null; then
        echo ""
        echo "[✓] Tor bootstrap complete."
        break
    fi

    ELAPSED=$(($(date +%s) - BOOTSTRAP_START))
    if [ $ELAPSED -ge $BOOTSTRAP_TIMEOUT ]; then
        echo ""
        echo "[✗] Tor bootstrap timed out after ${BOOTSTRAP_TIMEOUT}s"
        exit 1
    fi

    CURRENT_LOG=$(tail -n 1 "$TOR_NOTICES_LOG" 2>/dev/null || true)
    printf "\r\033[K[%ds] %s" "$ELAPSED" "$CURRENT_LOG"
    sleep 1
done
echo "[✓] Tor is ready."


echo "[*] Setting up iptables rules..."

iptables -F
iptables -t nat -F
TOR_UID=$(id -u "$TOR_USER")

# Allow loopback
iptables -t nat -A OUTPUT -o lo -j RETURN

# Allow Tor itself to reach the network.
#
# The owner match needs the xt_owner kernel module, which some NAS and embedded
# kernels (Synology DSM in particular) do not ship. There iptables rejects the
# rule with "Extension owner revision 0 not supported, missing kernel module?",
# and under `set -e` that aborted the whole script before any routing rule was
# installed, leaving the container in a restart loop. Treat the exemption as
# best-effort so those hosts keep working as they did before it was introduced.
if iptables -t nat -A OUTPUT -m owner --uid-owner "$TOR_UID" -j RETURN 2>/dev/null; then
    echo "[✓] Tor process (uid $TOR_UID) exempted from transparent redirect."
else
    echo "[!] Warning: this kernel has no iptables owner match (xt_owner module)."
    echo "[!] Continuing without the Tor process exemption. Tor keeps using the"
    echo "[!] connections it opened while bootstrapping, so traffic is still"
    echo "[!] routed through Tor, but if Tor loses its guard relays it may need"
    echo "[!] a container restart to recover."
fi

# For UDP DNS queries
iptables -t nat -A OUTPUT -p udp --dport 53 ! -d 127.0.0.1 -j DNAT --to-destination 127.0.0.1:53

# For TCP DNS queries (some DNS queries may use TCP)
iptables -t nat -A OUTPUT -p tcp --dport 53 ! -d 127.0.0.1 -j DNAT --to-destination 127.0.0.1:53

# Bypass Tor for local/private networks
iptables -t nat -A OUTPUT -d 127.0.0.0/8 -j RETURN
iptables -t nat -A OUTPUT -d 10.0.0.0/8 -j RETURN
iptables -t nat -A OUTPUT -d 172.16.0.0/12 -j RETURN
iptables -t nat -A OUTPUT -d 192.168.0.0/16 -j RETURN

# Redirect all TCP to Tor's TransPort
iptables -t nat -A OUTPUT -p tcp --syn -j REDIRECT --to-ports 9040

echo "[✓] Transparent Tor routing enabled."

sleep 5
# Check if outgoing IP is using Tor
echo "[*] Verifying Tor connectivity..."
# A fresh circuit can fail its first lookup, so retry before giving up. Never
# let a failed curl or an unparseable reply end the script on its own: under
# `set -e` that stopped the container with no message, leaving only curl's
# exit code in `docker inspect` (#1502).
VERIFY_ATTEMPTS=3
RESULT=""
for ((attempt = 1; attempt <= VERIFY_ATTEMPTS; attempt++)); do
    RESULT=$(curl -sS --max-time 30 https://check.torproject.org/api/ip 2>&1) && break
    CURL_STATUS=$?
    echo "[!] Tor connectivity check ${attempt}/${VERIFY_ATTEMPTS} failed (curl exit ${CURL_STATUS}): ${RESULT}"
    RESULT=""
    if [ "$attempt" -lt "$VERIFY_ATTEMPTS" ]; then
        sleep 10
    fi
done
if [ -z "$RESULT" ]; then
    echo "[✗] Could not reach check.torproject.org through Tor. Last Tor log lines:"
    tail -n 20 "$TOR_NOTICES_LOG" 2>/dev/null || true
    exit 1
fi
echo "RESULT: $RESULT"
IS_TOR=$(echo "$RESULT" | grep -oP '"IsTor":\s*\K(true|false)' || true)
IP=$(echo "$RESULT" | grep -oP '"IP":\s*"\K[^"]+' || true)
if [[ "$IS_TOR" == "true" ]]; then
    echo "[✓] Success! Traffic is routed through Tor. Current IP: $IP"
else
    echo "[✗] Warning: Traffic is NOT using Tor. Current IP: $IP"
    exit 1
fi

# Set correct timezone
# First check what is the timezone based on the IP
# Then set the timezone

# Get timezone from IP
sleep 1
TIMEZONE=$(curl -s https://ipapi.co/timezone) || \
TIMEZONE=$(curl -s http://ip-api.com/line?fields=timezone) || \
TIMEZONE=$(curl -s http://worldtimeapi.org/api/ip | grep -oP '"timezone":"\K[^"]+') || \
TIMEZONE=$(curl -s https://ip2tz.isthe.link/v2 | grep -oP '"timezone": *"\K[^"]+') || \
true

# If TIMEZONE is not set, use the default timezone
echo "[*] Current Timezone : $(date +%Z). IP Timezone: $TIMEZONE"

# Set timezone in Docker-compatible way
if [ -f "/usr/share/zoneinfo/$TIMEZONE" ]; then
    # Remove existing symlink if it exists
    rm -f /etc/localtime
    # Create new symlink
    ln -sf /usr/share/zoneinfo/$TIMEZONE /etc/localtime
    # Set timezone file
    echo "$TIMEZONE" > /etc/timezone
    # Set TZ environment variable
    export TZ=$TIMEZONE
    # Verify the change
    echo "[✓] Timezone set to $TIMEZONE"
    echo "[*] Current time: $(date)"
    echo "[*] Timezone verification: $(date +%Z)"
else
    echo "[!] Warning: Timezone file not found: $TIMEZONE"
    echo "[*] Available timezones:"
    ls -la /usr/share/zoneinfo/
    echo "[*] Falling back to container's default timezone: $TZ"
fi

# Start a background circuit rotation process
echo "[*] Starting Tor circuit rotation monitor..."
rotation_monitor() {
    rotation_count=0

    # Wait for initial stability
    sleep 120

    while true; do
        rotation_count=$((rotation_count + 1))
        echo "[*] Circuit rotation #$rotation_count at $(date)"

        # Test DNS resolution through Tor
        dns_ok=true
        if ! timeout 10 nslookup google.com 127.0.0.1 > /dev/null 2>&1; then
            echo "[!] $(date): DNS resolution slow/failing, rotating circuits..."
            pkill -HUP tor || true
            sleep 10
            dns_ok=false
        fi

        # Proactively rotate circuits every 5 minutes to keep them fresh
        # Skip if we already rotated for DNS failure this cycle
        if $dns_ok; then
            echo "[*] $(date): Proactive circuit rotation..."
            pkill -HUP tor || true
        fi

        # Verify Tor is still responsive after rotation
        sleep 5
        if timeout 10 curl -s --max-time 8 https://check.torproject.org/api/ip > /dev/null 2>&1; then
            echo "[✓] $(date): Circuit rotation successful, Tor responsive"
        else
            echo "[!] $(date): Tor unresponsive after rotation - supervisor healthcheck will handle recovery"
        fi

        sleep 300
    done
}

if is_truthy "$ENABLE_LOGGING_VALUE"; then
    rotation_monitor >> "$LOG_FILE" 2>&1 &
else
    rotation_monitor &
fi

ROTATION_PID=$!
echo "[✓] Tor circuit rotation monitor started in background (PID: $ROTATION_PID)"

# Run the entrypoint script
echo "[*] End of tor script"
