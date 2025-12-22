#!/bin/bash
LOG_DIR=${LOG_ROOT:-/var/log/}/cwa-book-downloader
mkdir -p $LOG_DIR
LOG_FILE=${LOG_DIR}/cwa-bd_tor.log

exec 3>&1 4>&2
exec > >(tee -a $LOG_FILE) 2>&1
echo "Starting tor script"
echo "Log file: $LOG_FILE"

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

cat <<EOF > /etc/tor/torrc
VirtualAddrNetworkIPv4 10.192.0.0/10
AutomapHostsOnResolve 1
TransPort 9040
DNSPort 53
Log notice file /var/log/tor/notices.log

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

# Function to dynamically wait for Tor bootstrap
wait_for_tor() {
    echo "$(date): Waiting for Tor to finish bootstrapping..."

    > /var/log/tor/notices.log 2>/dev/null || true

    sleep 10

    TIMEOUT=300
    ELAPSED=0
    while [ $ELAPSED -lt $TIMEOUT ]; do
        if grep -q "Bootstrapped 100%" /var/log/tor/notices.log 2>/dev/null; then
            echo "$(date): Tor bootstrap complete."
            return 0
        fi
        sleep 5
        ELAPSED=$((ELAPSED + 5))
        # Show progress
        CURRENT=$(tail -n 1 /var/log/tor/notices.log 2>/dev/null | grep -oP 'Bootstrapped \d+%' || echo "waiting...")
        echo "$(date): Bootstrap progress: $CURRENT ($ELAPSED/${TIMEOUT}s)"
    done

    echo "$(date): WARNING - Tor bootstrap timed out after ${TIMEOUT}s"
    return 1
}

FAIL_COUNT=0
while true; do
    # Try to resolve/connect to google.com (timeout 10s)
    if curl -s --head --max-time 10 https://google.com > /dev/null; then
        # Success
        FAIL_COUNT=0
    else
        FAIL_COUNT=$((FAIL_COUNT+1))
        echo "$(date): Healthcheck failed (Count: $FAIL_COUNT)"
    fi

    # If failed 3 times in a row, restart Tor
    if [ "$FAIL_COUNT" -ge 3 ]; then
        echo "$(date): restart trigger - Restarting Tor..."
        supervisorctl restart tor
        FAIL_COUNT=0

        # Wait for it to come back using the dynamic check
        wait_for_tor
    fi

    sleep 30
done
HC
chmod +x /app/tor_healthcheck.sh

echo "[*] Starting Tor via Supervisor..."
/usr/bin/supervisord -c /etc/supervisor/supervisord.conf

# Wait a bit to ensure Tor has bootstrapped
echo "[*] Waiting for Tor to finish bootstrapping... (up to 5 minutes)"
BOOTSTRAP_TIMEOUT=300
BOOTSTRAP_START=$(date +%s)
while true; do
    if grep -q "Bootstrapped 100%" /var/log/tor/notices.log 2>/dev/null; then
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

    CURRENT_LOG=$(tail -n 1 /var/log/tor/notices.log 2>/dev/null)
    printf "\r\033[K[%ds] %s" "$ELAPSED" "$CURRENT_LOG"
    sleep 1
done
echo "[✓] Tor is ready."


echo "[*] Setting up iptables rules..."

iptables -F
iptables -t nat -F

# Allow loopback
iptables -t nat -A OUTPUT -o lo -j RETURN

# Redirect all TCP to Tor's TransPort
iptables -t nat -A OUTPUT -p tcp --syn -j REDIRECT --to-ports 9040

# For UDP DNS queries
iptables -t nat -A OUTPUT -p udp --dport 53 ! -d 127.0.0.1 -j DNAT --to-destination 127.0.0.1:53


# For TCP DNS queries (some DNS queries may use TCP)
iptables -t nat -A OUTPUT -p tcp --dport 53 ! -d 127.0.0.1 -j DNAT --to-destination 127.0.0.1:53

echo "[✓] Transparent Tor routing enabled."

sleep 5
# Check if outgoing IP is using Tor
echo "[*] Verifying Tor connectivity..."
RESULT=$(curl -s https://check.torproject.org/api/ip)
echo "RESULT: $RESULT"
IS_TOR=$(echo "$RESULT" | grep -oP '"IsTor":\s*\K(true|false)')
IP=$(echo "$RESULT" | grep -oP '"IP":\s*"\K[^"]+')
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
(
    rotation_count=0

    # Wait for initial stability
    sleep 120

    while true; do
        rotation_count=$((rotation_count + 1))
        echo "[*] Circuit rotation #$rotation_count at $(date)"

        # Test DNS resolution through Tor
        if ! timeout 10 nslookup google.com 127.0.0.1 > /dev/null 2>&1; then
            echo "[!] $(date): DNS resolution slow/failing, rotating circuits..."
            pkill -HUP tor || true
            sleep 10
        fi

        # Proactively rotate circuits every 5 minutes to keep them fresh
        echo "[*] $(date): Proactive circuit rotation..."
        pkill -HUP tor || true

        # Verify Tor is still responsive after rotation
        sleep 5
        if timeout 10 curl -s --max-time 8 https://check.torproject.org/api/ip > /dev/null 2>&1; then
            echo "[✓] $(date): Circuit rotation successful, Tor responsive"
        else
            echo "[!] $(date): Tor unresponsive after rotation - supervisor healthcheck will handle recovery"
        fi

        sleep 300
    done
) >> $LOG_FILE 2>&1 &

ROTATION_PID=$!
echo "[✓] Tor circuit rotation monitor started in background (PID: $ROTATION_PID)"

# Run the entrypoint script
echo "[*] End of tor script"
