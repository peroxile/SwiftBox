"""
Systemd service checks and restart actions for Linux hosts.
"""

from __future__ import annotations

import logging
import subprocess

logger = logging.getLogger(__name__)


def check_status(name: str) -> dict:
    """Return whether a systemd service is active."""
    try:
        result = subprocess.run(
            ["systemctl", "is-active", name],
            capture_output=True, text=True, timeout=10,
        )
        active = result.stdout.strip() == "active"
        state = result.stdout.strip() or "unknown"
        return {
            "value": 1 if active else 0,
            "ok": active,
            "message": f"Service {name}: {state}",
            "context": {"service": name, "state": state},
        }
    except FileNotFoundError:
        return {
            "value": None,
            "ok": False,
            "message": "systemctl not found — not a systemd host",
            "context": {"service": name},
        }
    except subprocess.TimeoutExpired:
        return {
            "value": None,
            "ok": False,
            "message": f"Timeout checking service {name}",
            "context": {"service": name},
        }


def restart(name: str) -> dict:
    """Restart a named systemd service."""
    try:
        result = subprocess.run(
            ["systemctl", "restart", name],
            capture_output=True, text=True, timeout=30,
        )
        ok = result.returncode == 0
        return {
            "value": 1 if ok else 0,
            "ok": ok,
            "message": f"Service {name} restart {'succeeded' if ok else 'failed'}",
            "context": {
                "service": name,
                "returncode": result.returncode,
                "stderr": result.stderr.strip(),
            },
        }
    except FileNotFoundError:
        return {
            "value": 0,
            "ok": False,
            "message": "systemctl not found",
            "context": {"service": name},
        }
    except subprocess.TimeoutExpired:
        return {
            "value": 0,
            "ok": False,
            "message": f"Timeout restarting service {name}",
            "context": {"service": name},
        }