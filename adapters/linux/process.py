"""
Process and memory checks for Linux hosts.
"""

from __future__ import annotations

import logging
import os
import subprocess
# Needed for check_memory
from pathlib import Path

logger = logging.getLogger(__name__)


def check_memory() -> dict:
    """Return memory usage percent from /proc/meminfo."""
    try:
        meminfo = Path("/proc/meminfo").read_text()
        values = {}
        for line in meminfo.splitlines():
            parts = line.split()
            if len(parts) >= 2:
                values[parts[0].rstrip(":")] = int(parts[1])

        total = values.get("MemTotal", 0)
        available = values.get("MemAvailable", 0)
        if total == 0:
            raise ValueError("MemTotal is 0")

        used = total - available
        percent = round((used / total) * 100, 2)
        return {
            "value": percent,
            "ok": True,
            "message": f"Memory usage: {percent}%",
            "context": {
                "total_mb": round(total / 1024, 1),
                "used_mb": round(used / 1024, 1),
                "available_mb": round(available / 1024, 1),
            },
        }
    except Exception as e:  # noqa: BLE001
        return {"value": None, "ok": False, "message": f"Memory check failed: {e}", "context": {}}


def check_load(window: str = "5m") -> dict:
    """
    Return load average normalized by CPU count.
    window: 1m | 5m | 15m
    """
    try:
        load1, load5, load15 = os.getloadavg()
        cpu_count = os.cpu_count() or 1
        window_map = {"1m": load1, "5m": load5, "15m": load15}
        raw = window_map.get(window, load5)
        normalized = round(raw / cpu_count, 4)
        return {
            "value": normalized,
            "ok": True,
            "message": f"Load avg ({window}): {raw:.2f} ({normalized:.2f}x over {cpu_count} CPUs)",
            "context": {
                "load_1m": load1, "load_5m": load5, "load_15m": load15,
                "cpu_count": cpu_count, "window": window,
            },
        }
    except Exception as e:  # noqa: BLE001
        return {"value": None, "ok": False, "message": f"Load check failed: {e}", "context": {}}


def list() -> dict:  # noqa: A001
    """Return top 10 processes by CPU usage."""
    try:
        result = subprocess.run(
            ["ps", "aux", "--sort=-%cpu"],
            capture_output=True, text=True, timeout=10,
        )
        lines = result.stdout.splitlines()
        top = lines[1:11] if len(lines) > 1 else []
        return {
            "value": len(top),
            "ok": True,
            "message": f"Top {len(top)} processes by CPU",
            "context": {"processes": top},
        }
    except Exception as e:  # noqa: BLE001
        return {"value": None, "ok": False, "message": f"Process list failed: {e}", "context": {}}


def kill_zombie() -> dict:
    """
    Identify zombie processes and signal their parents to reap them.
    Does not SIGKILL — only sends SIGCHLD to the parent.
    """
    try:
        result = subprocess.run(
            ["ps", "-eo", "pid,ppid,stat,comm"],
            capture_output=True, text=True, timeout=10,
        )
        zombies = []
        for line in result.stdout.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 3 and parts[2].startswith("Z"):
                zombies.append({"pid": parts[0], "ppid": parts[1], "name": parts[3] if len(parts) > 3 else "?"})

        reaped = []
        for z in zombies:
            try:
                os.kill(int(z["ppid"]), 17)  # SIGCHLD
                reaped.append(z)
            except (ProcessLookupError, PermissionError):
                pass

        return {
            "value": len(reaped),
            "ok": True,
            "message": f"Sent SIGCHLD to parents of {len(reaped)} zombie(s)",
            "context": {"zombies": zombies, "reaped": reaped},
        }
    except Exception as e:  # noqa: BLE001
        return {"value": None, "ok": False, "message": f"Zombie reap failed: {e}", "context": {}}
