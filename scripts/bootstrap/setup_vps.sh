#!/usr/bin/env bash

# Initial VPS setup: verify required tools, create SwiftBox directories,
# set permissions, and confirm the environment is ready for SwiftBox to run.
#
# This script is safe to run multiple times (idempotent).
# It does not install packages — that is the product's responsibility.

# Usage:
#   setup_vps.sh [--state-dir=state] [--log-dir=logs] [--dry-run]

set -euo pipefail

STATE_DIR="state"
LOG_DIR="logs"
DRY_RUN=false

for arg in "$@"; do
  case "$arg" in
    --state-dir=*) STATE_DIR="${arg#*=}" ;;
    --log-dir=*)   LOG_DIR="${arg#*=}" ;;
    --dry-run)     DRY_RUN=true ;;
  esac
done

echo "[setup_vps] starting bootstrap dry_run=${DRY_RUN}"

#  Required tools check 
REQUIRED_TOOLS=(python3 bash find ps ip systemctl)
MISSING=()

for tool in "${REQUIRED_TOOLS[@]}"; do
  if ! command -v "$tool" &>/dev/null; then
    MISSING+=("$tool")
  fi
done

if [ ${#MISSING[@]} -gt 0 ]; then
  echo "[setup_vps] WARN: missing tools: ${MISSING[*]}"
else
  echo "[setup_vps] OK: all required tools present"
fi

# Directory setup
DIRS=(
  "$STATE_DIR"
  "$STATE_DIR/host_state"
  "$STATE_DIR/repo_cache"
  "$LOG_DIR"
  "scripts/fix"
  "scripts/diagnose"
  "scripts/bootstrap"
)

for dir in "${DIRS[@]}"; do
  if [ "$DRY_RUN" = "true" ]; then
    echo "[dry-run] would create: $dir"
  else
    mkdir -p "$dir"
    echo "[setup_vps] ensured: $dir"
  fi
done

# State file initialization
STATE_FILES=(
  "$STATE_DIR/cache.json:{}"
  "$STATE_DIR/last_actions.json:{}"
)

for entry in "${STATE_FILES[@]}"; do
  file="${entry%%:*}"
  default="${entry#*:}"
  if [ ! -f "$file" ]; then
    if [ "$DRY_RUN" = "true" ]; then
      echo "[dry-run] would init: $file"
    else
      echo "$default" > "$file"
      echo "[setup_vps] initialized: $file"
    fi
  else
    echo "[setup_vps] exists: $file"
  fi
done

# History file (append-only, create if missing)
HISTORY="$STATE_DIR/history.jsonl"
if [ ! -f "$HISTORY" ]; then
  if [ "$DRY_RUN" = "true" ]; then
    echo "[dry-run] would create: $HISTORY"
  else
    touch "$HISTORY"
    echo "[setup_vps] initialized: $HISTORY"
  fi
else
  echo "[setup_vps] exists: $HISTORY"
fi

echo "[setup_vps] bootstrap complete"