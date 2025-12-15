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

#!/bin/bash

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
127.0.0.1
EOF

echo "[*] Starting Tor..."
service tor start

# Wait a bit to ensure Tor has bootstrapped
echo "[*] Waiting for Tor to finish bootstrapping... (up to 5 minutes)"
timeout 300 bash -c '
  while ! grep -q "Bootstrapped 100%" <(tail -n 20 -F /var/log/tor/notices.log 2>/dev/null); do
    printf "\r\033[KCurrent log: %s" "$(tail -n 1 /var/log/tor/notices.log 2>/dev/null)"
    sleep 1
  done
  # Print a newline when finished.
  echo ""
'
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

# Note: ICMP (ping) is NOT routed through Tor as Tor only supports TCP.
# ICMP will use default routing. If you need to test connectivity, use:
# curl -s https://check.torproject.org/api/ip
# or: curl -s https://icanhazip.com

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

# Start a background health check process to monitor Tor
echo "[*] Starting Tor health check monitor..."
(
    check_count=0
    while true; do
        sleep 300  # Check every 5 minutes
        check_count=$((check_count + 1))
        echo "[*] Tor health check #$check_count at $(date)"
        
        # Check if Tor service is running
        if ! service tor status > /dev/null 2>&1; then
            echo "[!] $(date): Tor service not running, restarting..."
            service tor restart
            sleep 10
        fi
        
        # Test DNS resolution through Tor
        if ! timeout 10 nslookup google.com 127.0.0.1 > /dev/null 2>&1; then
            echo "[!] $(date): DNS resolution failed, reloading Tor..."
            service tor reload
            sleep 5
            # Verify DNS works after reload
            if timeout 10 nslookup google.com 127.0.0.1 > /dev/null 2>&1; then
                echo "[✓] $(date): DNS resolution restored"
            else
                echo "[✗] $(date): DNS still failing after reload, restarting Tor..."
                service tor restart
                sleep 10
            fi
        fi
        
        # Send SIGHUP to Tor to rotate circuits (helps with stale circuits)
        echo "[*] $(date): Rotating Tor circuits..."
        pkill -HUP tor || true
    done
) >> $LOG_FILE 2>&1 &

TOR_MONITOR_PID=$!
echo "[✓] Tor health check monitor started in background (PID: $TOR_MONITOR_PID)"

# Run the entrypoint script
echo "[*] End of tor script"
