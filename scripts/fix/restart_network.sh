#!/usr/bin/env bash

# Restart the primary or named network interface.
# Resolves 'auto' to the default route interface.
#
# Usage:
#   restart_network.sh [--interface=<name|auto>]

set -euo pipefail

INTERFACE="auto"

for arg in "$@"; do
  case "$arg" in
    --interface=*) INTERFACE="${arg#*=}" ;;
  esac
done

if [ "$INTERFACE" = "auto" ]; then
  INTERFACE=$(ip route show default 2>/dev/null | awk '/dev/ {for(i=1;i<=NF;i++) if($i=="dev") print $(i+1)}' | head -1)
  if [ -z "$INTERFACE" ]; then
    echo "[restart_network] ERROR: could not resolve default interface" >&2
    exit 1
  fi
  echo "[restart_network] resolved interface: $INTERFACE"
fi

echo "[restart_network] bringing down $INTERFACE"
ip link set "$INTERFACE" down

sleep 1

echo "[restart_network] bringing up $INTERFACE"
ip link set "$INTERFACE" up

sleep 2

STATE=$(ip link show "$INTERFACE" 2>/dev/null | grep -o "state [A-Z]*" | awk '{print $2}')
echo "[restart_network] post-restart state: $STATE"

if [ "$STATE" = "UP" ]; then
  echo "[restart_network] OK: $INTERFACE is UP"
  exit 0
else
  echo "[restart_network] WARN: $INTERFACE state is $STATE" >&2
  exit 1
fi