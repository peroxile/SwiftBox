#!/usr/bin/env bash

# Restart a named systemd service.
# Verifies it is active after restart.
#
# Usage:
#   restart_service.sh --name=<service>

set -euo pipefail

SERVICE_NAME=""

for arg in "$@"; do
  case "$arg" in
    --name=*) SERVICE_NAME="${arg#*=}" ;;
  esac
done

if [ -z "$SERVICE_NAME" ]; then
  echo "[restart_service] ERROR: --name is required" >&2
  exit 1
fi

echo "[restart_service] restarting: $SERVICE_NAME"

if ! command -v systemctl &>/dev/null; then
  echo "[restart_service] ERROR: systemctl not available" >&2
  exit 1
fi

systemctl restart "$SERVICE_NAME"

# Verify
sleep 1
STATE=$(systemctl is-active "$SERVICE_NAME" 2>/dev/null || echo "unknown")
echo "[restart_service] post-restart state: $STATE"

if [ "$STATE" = "active" ]; then
  echo "[restart_service] OK: $SERVICE_NAME is active"
  exit 0
else
  echo "[restart_service] FAIL: $SERVICE_NAME is $STATE after restart" >&2
  exit 1
fi