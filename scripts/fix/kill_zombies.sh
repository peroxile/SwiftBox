#!/usr/bin/env bash

# Find zombie processes and signal their parents with SIGCHLD to trigger reaping.
# Does not SIGKILL anything.

set -euo pipefail

echo "[kill_zombies] scanning for zombie processes"

ZOMBIES=$(ps -eo pid,ppid,stat,comm --no-headers 2>/dev/null | awk '$3 ~ /^Z/ {print $1,$2,$4}')

if [ -z "$ZOMBIES" ]; then
  echo "[kill_zombies] no zombies found"
  exit 0
fi

COUNT=0
while IFS=' ' read -r pid ppid name; do
  echo "[kill_zombies] zombie pid=$pid ppid=$ppid name=$name"
  if kill -SIGCHLD "$ppid" 2>/dev/null; then
    echo "[kill_zombies] sent SIGCHLD to ppid=$ppid"
    ((COUNT++)) || true
  else
    echo "[kill_zombies] could not signal ppid=$ppid (may be unreachable)"
  fi
done <<< "$ZOMBIES"

echo "[kill_zombies] done: signaled parents of $COUNT zombie(s)"