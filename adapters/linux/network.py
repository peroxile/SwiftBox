"""
Network connectivity checks for Linux hosts.
"""

from __future__ import annotations

import logging
import socket
import subprocess

logger = logging.getLogger(__name__)


def check_connectivity(target: str = "8.8.8.8", timeout: int = 5) -> dict:
    """Ping target to verify external connectivity."""
    try:
        result = subprocess.run(
            ["ping", "-c", "1", "-W", str(timeout), target],
            capture_output=True,
            text=True,
            timeout=timeout + 2,
        )
        ok = result.returncode == 0
        return {
            "value": 1 if ok else 0,
            "ok": ok,
            "message": f"Connectivity to {target}: {'reachable' if ok else 'unreachable'}",
            "context": {"target": target, "returncode": result.returncode},
        }
    except subprocess.TimeoutExpired:
        return {
            "value": 0,
            "ok": False,
            "message": f"Ping to {target} timed out",
            "context": {"target": target},
        }
    except FileNotFoundError:
        # ping not available — fall back to socket
        return _socket_check(target, timeout)


def _socket_check(target: str, timeout: int) -> dict:
    """TCP socket fallback when ping is unavailable."""
    try:
        socket.setdefaulttimeout(timeout)
        socket.getaddrinfo(target, None)
        return {
            "value": 1,
            "ok": True,
            "message": f"DNS resolution of {target} succeeded",
            "context": {"target": target, "method": "socket"},
        }
    except (socket.gaierror, OSError) as e:
        return {
            "value": 0,
            "ok": False,
            "message": f"Could not reach {target}: {e}",
            "context": {"target": target, "method": "socket"},
        }


def check_dns(target: str = "google.com") -> dict:
    """Verify DNS resolution for a hostname."""
    try:
        addrs = socket.getaddrinfo(target, None)
        ip = addrs[0][4][0] if addrs else None
        return {
            "value": 1,
            "ok": True,
            "message": f"DNS resolved {target} -> {ip}",
            "context": {"target": target, "resolved_ip": ip},
        }
    except socket.gaierror as e:
        return {
            "value": 0,
            "ok": False,
            "message": f"DNS resolution failed for {target}: {e}",
            "context": {"target": target},
        }


def restart_interface(interface: str = "auto") -> dict:
    """
    Restart the primary network interface.
    'auto' resolves to the first non-loopback interface via `ip route`.
    """
    resolved: str | None = None

    if interface == "auto":
        try:
            result = subprocess.run(
                ["ip", "route", "show", "default"],
                capture_output=True, text=True, timeout=5,
            )
            # output: "default via 10.0.0.1 dev eth0 ..."
            parts = result.stdout.split()
            dev_idx = parts.index("dev") if "dev" in parts else -1
            resolved = parts[dev_idx + 1] if dev_idx >= 0 and dev_idx + 1 < len(parts) else None
        except Exception as e:  # noqa: BLE001
            return {"value": 0, "ok": False, "message": f"Could not resolve interface: {e}", "context": {}}
    else:
        resolved = interface

    if not resolved:
        return {"value": 0, "ok": False, "message": "No interface found", "context": {}}

    try:
        subprocess.run(["ip", "link", "set", resolved, "down"], check=True, timeout=10)
        subprocess.run(["ip", "link", "set", resolved, "up"], check=True, timeout=10)
        return {
            "value": 1,
            "ok": True,
            "message": f"Restarted interface {resolved}",
            "context": {"interface": resolved},
        }
    except subprocess.CalledProcessError as e:
        return {
            "value": 0,
            "ok": False,
            "message": f"Failed to restart {resolved}: {e}",
            "context": {"interface": resolved},
        }