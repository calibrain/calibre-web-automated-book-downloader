#!/bin/bash

# Set up log paths
LOG_ROOT=${LOG_ROOT:-"/var/log"}
LOG_DIR="$LOG_ROOT/cwa-book-downloader"
OUTPUT_FILE_NAME="cwa-book-downloader-debug_BUILD-${BUILD_VERSION:-local}_RELEASE-${RELEASE_VERSION:-NA}_$(date +%Y%m%d-%H%M%S)"
OUTPUT_FILE="/tmp/$OUTPUT_FILE_NAME.zip"

# Create LOG_DIR if it doesn't exist
mkdir -p "$LOG_DIR"

# Add system information directly to LOG_DIR
echo "=== System Information ===" > "$LOG_DIR/system_info.txt"
echo "Date: $(date)" >> "$LOG_DIR/system_info.txt"
echo "Hostname: $(hostname)" >> "$LOG_DIR/system_info.txt"
echo "Kernel: $(uname -a)" >> "$LOG_DIR/system_info.txt"
echo "" >> "$LOG_DIR/system_info.txt"

# Add disk usage
echo "=== Disk Usage ===" >> "$LOG_DIR/system_info.txt"
df -h >> "$LOG_DIR/system_info.txt"
echo "" >> "$LOG_DIR/system_info.txt"

# Add memory info
echo "=== Memory Info ===" >> "$LOG_DIR/system_info.txt"
free -h >> "$LOG_DIR/system_info.txt"
echo "" >> "$LOG_DIR/system_info.txt"

# Add running processes
echo "=== Running Processes ===" >> "$LOG_DIR/system_info.txt"
ps aux >> "$LOG_DIR/system_info.txt"
echo "" >> "$LOG_DIR/system_info.txt"

# Add network information using basic commands
echo "=== Network Information ===" > "$LOG_DIR/network_info.txt"

# Try to get basic connectivity information
echo "=== Basic Connectivity ===" >> "$LOG_DIR/network_info.txt"
echo "Hostname resolution:" >> "$LOG_DIR/network_info.txt"
cat /etc/hosts 2>/dev/null >> "$LOG_DIR/network_info.txt" || echo "Unable to read /etc/hosts" >> "$LOG_DIR/network_info.txt"
echo "" >> "$LOG_DIR/network_info.txt"

echo "DNS configuration:" >> "$LOG_DIR/network_info.txt"
cat /etc/resolv.conf 2>/dev/null >> "$LOG_DIR/network_info.txt" || echo "Unable to read /etc/resolv.conf" >> "$LOG_DIR/network_info.txt"
echo "" >> "$LOG_DIR/network_info.txt"

# Try to get interface information from /proc
echo "=== Network Interfaces (/proc) ===" >> "$LOG_DIR/network_info.txt"
if [ -f "/proc/net/dev" ]; then
  cat /proc/net/dev >> "$LOG_DIR/network_info.txt"
else
  echo "Not available: /proc/net/dev not found" >> "$LOG_DIR/network_info.txt"
fi
echo "" >> "$LOG_DIR/network_info.txt"

# Try connectivity tests
echo "=== Internet Connectivity ===" >> "$LOG_DIR/network_info.txt"
ping -c 3 1.1.1.1 2>/dev/null >> "$LOG_DIR/network_info.txt" || echo "Ping command failed or not available" >> "$LOG_DIR/network_info.txt"
echo "" >> "$LOG_DIR/network_info.txt"
ping -c 3 one.one.one.one 2>/dev/null >> "$LOG_DIR/network_info.txt" || echo "DNS resolution test failed" >> "$LOG_DIR/network_info.txt"
echo "" >> "$LOG_DIR/network_info.txt"

# Test IPv6 connectivity
echo "=== IPv6 Connectivity ===" >> "$LOG_DIR/network_info.txt"
# Check if IPv6 is enabled
if [ -f "/proc/sys/net/ipv6/conf/all/disable_ipv6" ]; then
  IPV6_DISABLED=$(cat /proc/sys/net/ipv6/conf/all/disable_ipv6)
  if [ "$IPV6_DISABLED" = "0" ]; then
    echo "IPv6 is enabled in the kernel" >> "$LOG_DIR/network_info.txt"
  else
    echo "IPv6 is disabled in the kernel" >> "$LOG_DIR/network_info.txt"
  fi
else
  echo "Unable to determine IPv6 kernel status" >> "$LOG_DIR/network_info.txt"
fi
echo "" >> "$LOG_DIR/network_info.txt"

# Try IPv6 connectivity test using Cloudflare's IPv6 DNS
echo "Testing IPv6 connectivity to Cloudflare DNS:" >> "$LOG_DIR/network_info.txt"
ping6 -c 3 2606:4700:4700::1111 2>/dev/null >> "$LOG_DIR/network_info.txt" || echo "IPv6 ping failed or not available" >> "$LOG_DIR/network_info.txt"
echo "" >> "$LOG_DIR/network_info.txt"

# Test SSL connectivity
echo "=== SSL Connectivity Tests ===" >> "$LOG_DIR/network_info.txt"
echo "Testing SSL connection to Cloudflare (1.1.1.1):" >> "$LOG_DIR/network_info.txt"
echo | openssl s_client -connect 1.1.1.1:443 2>&1 | grep -E "Verify return code:|subject=|issuer=" >> "$LOG_DIR/network_info.txt" || echo "SSL test to Cloudflare failed" >> "$LOG_DIR/network_info.txt"
echo "" >> "$LOG_DIR/network_info.txt"

echo "Testing SSL connection to cloudflare.com:" >> "$LOG_DIR/network_info.txt"
echo | openssl s_client -connect cloudflare.com:443 2>&1 | grep -E "Verify return code:|subject=|issuer=" >> "$LOG_DIR/network_info.txt" || echo "SSL test to cloudflare.com failed" >> "$LOG_DIR/network_info.txt"
echo "" >> "$LOG_DIR/network_info.txt"

# Add installed packages
echo "=== Installed Python Packages ===" > "$LOG_DIR/packages.txt"
pip list 2>/dev/null >> "$LOG_DIR/packages.txt" || echo "pip not found" >> "$LOG_DIR/packages.txt"
echo "" >> "$LOG_DIR/packages.txt"

# Check Permissions
echo "=== Permissions ===" > "$LOG_DIR/permissions.txt"
echo "ls -all /app" >> "$LOG_DIR/permissions.txt"
ls -all /app >> "$LOG_DIR/permissions.txt"
echo "" >> "$LOG_DIR/permissions.txt"
echo "ls -all /cwa-book-ingest" >> "$LOG_DIR/permissions.txt"
ls -all /cwa-book-ingest >> "$LOG_DIR/permissions.txt"
echo "" >> "$LOG_DIR/permissions.txt"
echo "ls -all /var/log/cwa-book-downloader" >> "$LOG_DIR/permissions.txt"
ls -all /var/log/cwa-book-downloader >> "$LOG_DIR/permissions.txt"
echo "" >> "$LOG_DIR/permissions.txt"
echo "ls -all /tmp/cwa-book-downloader" >> "$LOG_DIR/permissions.txt"
ls -all /tmp/cwa-book-downloader >> "$LOG_DIR/permissions.txt"
echo "" >> "$LOG_DIR/permissions.txt"


# Check if running in Docker
echo "=== Container Info ===" > "$LOG_DIR/container_info.txt"
if [ -f /.dockerenv ]; then
  echo "Running in Docker container (found /.dockerenv)" >> "$LOG_DIR/container_info.txt"
elif grep -q docker /proc/1/cgroup 2>/dev/null; then
  echo "Running in Docker container (detected from cgroups)" >> "$LOG_DIR/container_info.txt"
else
  echo "Not running in Docker container" >> "$LOG_DIR/container_info.txt"
fi

# Add environment variables (redacting sensitive info)
env | grep -v -E "(AA_DONATOR_KEY)" | sort > "$LOG_DIR/environment.txt"

echo "--- HTTPBin ---" > $LOG_DIR/network_info.txt
pyrequests https://httpbin.org/get >> $LOG_DIR/network_info.txt
ehco ""
echo "--- HowsMySSL ---" >> $LOG_DIR/network_info.txt
pyrequests https://www.howsmyssl.com/a/check >> $LOG_DIR/network_info.txt
ehco ""
echo "--- IPInfo ---" >> $LOG_DIR/network_info.txt
pyrequests https://ipinfo.io >> $LOG_DIR/network_info.txt
ehco ""
echo "--- Cloudflare Trace ---" >> $LOG_DIR/network_info.txt
pyrequests https://1.1.1.1/cdn-cgi/trace >> $LOG_DIR/network_info.txt

# Create the zip file directly from LOG_DIR
ln -s "$LOG_DIR" /tmp/$OUTPUT_FILE_NAME
(cd /tmp && zip -r "$OUTPUT_FILE" $OUTPUT_FILE_NAME > /dev/null 2>&1)
rm -f /tmp/$OUTPUT_FILE_NAME

if [ -f "$OUTPUT_FILE" ]; then
  echo "$OUTPUT_FILE"
  exit 0
else
  echo "Failed to create debug archive"
  exit 1
fi

