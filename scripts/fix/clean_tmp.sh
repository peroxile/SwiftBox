#!/usr/bin/env bash

# Remove stale files from /tmp and /var/tmp.
# Safe by default: dry-run unless --dry-run=false is passed.
#
# Usage:
#   clean_tmp.sh [--older-than-days=N] [--paths=/tmp,/var/tmp] [--dry-run=false]

set -euo pipefail

OLDER_THAN_DAYS=7
PATHS="/tmp /var/tmp"
DRY_RUN=true

for arg in "$@"; do
  case "$arg" in
    --older_than_days=*) OLDER_THAN_DAYS="${arg#*=}" ;;
    --paths=*)           PATHS="${arg#*=}" ;;
    --dry-run=false)     DRY_RUN=false ;;
  esac
done

echo "[clean_tmp] older_than=${OLDER_THAN_DAYS}d dry_run=${DRY_RUN}"

for dir in $PATHS; do
  if [ ! -d "$dir" ]; then
    echo "[clean_tmp] skip: $dir does not exist"
    continue
  fi

  echo "[clean_tmp] scanning $dir"
  count=0

  while IFS= read -r -d '' item; do
    if [ "$DRY_RUN" = "true" ]; then
      echo "[dry-run] would remove: $item"
    else
      rm -rf "$item"
      echo "[removed] $item"
    fi
    ((count++)) || true
  done < <(find "$dir" -maxdepth 1 -mindepth 1 -mtime "+${OLDER_THAN_DAYS}" -print0 2>/dev/null)

  echo "[clean_tmp] $dir: $count items affected"
done

echo "[clean_tmp] done"