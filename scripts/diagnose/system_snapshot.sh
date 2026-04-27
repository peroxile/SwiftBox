#!/usr/bin/env bash

# Collect a point-in-time snapshot of system health.
# Output is structured text — one section per category.
# Does not write to disk, does not change system state.
# Safe to run at any time.
#
# Usage:
#   system_snapshot.sh [--output=stdout|file] [--file=path]

set -euo pipefail

OUTPUT_TARGET="stdout"
OUTPUT_FILE="logs/snapshot.txt"

for arg in "$@"; do
  case "$arg" in
    --output=*) OUTPUT_TARGET="${arg#*=}" ;;
    --file=*)   OUTPUT_FILE="${arg#*=}" ;;
  esac
done

TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

collect() {
  echo ""
  echo "=== $1 ==="
  shift
  "$@" 2>/dev/null || echo "(unavailable)"
}

snapshot() {
  echo "swiftbox_snapshot timestamp=${TIMESTAMP}"

  collect "DISK USAGE" df -h

  collect "MEMORY" free -h

  collect "CPU LOAD" uptime

  collect "TOP PROCESSES (CPU)" ps aux --sort=-%cpu --no-headers | head -10

  collect "SERVICES (failed)" systemctl list-units --state=failed --no-pager --no-legend 2>/dev/null || echo "(systemctl unavailable)"

  collect "NETWORK INTERFACES" ip -brief addr 2>/dev/null || ifconfig 2>/dev/null || echo "(unavailable)"

  collect "OPEN PORTS" ss -tlnp 2>/dev/null || netstat -tlnp 2>/dev/null || echo "(unavailable)"

  collect "ZOMBIE PROCESSES" ps -eo pid,ppid,stat,comm --no-headers | awk '$3 ~ /^Z/' || echo "none"

  collect "LAST 10 KERNEL MESSAGES" dmesg 2>/dev/null | tail -10 || echo "(unavailable)"

  echo ""
  echo "=== END SNAPSHOT ==="
}

if [ "$OUTPUT_TARGET" = "file" ]; then
  mkdir -p "$(dirname "$OUTPUT_FILE")"
  snapshot | tee "$OUTPUT_FILE"
  echo "[system_snapshot] written to $OUTPUT_FILE"
else
  snapshot
fi