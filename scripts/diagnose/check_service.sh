#!/usr/bin/env bash

# Report the status and recent journal output for a named service.
# Read-only. Does not restart or modify anything.
#
# Usage:
#   check_service.sh --name=<service> [--lines=20]

set -euo pipefail

SERVICE_NAME=""
LOG_LINES=20

for arg in "$@"; do
  case "$arg" in
    --name=*)  SERVICE_NAME="${arg#*=}" ;;
    --lines=*) LOG_LINES="${arg#*=}" ;;
  esac
done

if [ -z "$SERVICE_NAME" ]; then
  echo "[check_service] ERROR: --name is required" >&2
  exit 1
fi

echo "[check_service] name=${SERVICE_NAME}"
echo ""

echo "--- status ---"
systemctl status "$SERVICE_NAME" --no-pager 2>/dev/null || echo "(systemctl unavailable)"

echo ""
echo "--- last ${LOG_LINES} log lines ---"
journalctl -u "$SERVICE_NAME" -n "$LOG_LINES" --no-pager 2>/dev/null || echo "(journalctl unavailable)"

echo ""
echo "[check_service] done"