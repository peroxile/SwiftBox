"""
Run all SwiftBox health and security checks on a remote host via SSH.
Each function takes an SSHExecutor and returns the standard adapter dict:
    {value, ok, message, context}

This module is the remote equivalent of adapters/linux/*.
The engine calls run_ssh_checks() instead of run_all_checks()
when the host config has an ssh block.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from core.detect import _classify
from core.schemas import CheckStatus, HealthCheckDef, IssueReport
from adapters.ssh.executor import SSHExecutor

if TYPE_CHECKING:
    from core.schemas import HostConfig

logger = logging.getLogger(__name__)


# Helpers

def _run(ssh: SSHExecutor, command: str) -> tuple[str, str, int]:
    try:
        return ssh.run(command)
    except Exception as e:  # noqa: BLE001
        return "", str(e), 1


def _ok(value, message, context=None):
    return {"value": value, "ok": True, "message": message, "context": context or {}}


def _fail(message, context=None):
    return {"value": None, "ok": False, "message": message, "context": context or {}}


# System checks (mirrors adapters/linux/)

def check_disk_usage(ssh: SSHExecutor, path: str = "/") -> dict:
    stdout, stderr, code = _run(ssh, f"df -BG --output=size,used,avail,pcent {path} | tail -1")
    if code != 0:
        return _fail(f"df failed: {stderr}")
    parts = stdout.split()
    if len(parts) < 4:
        return _fail(f"Unexpected df output: {stdout}")
    try:
        percent = float(parts[3].rstrip("%"))
        total_gb = int(parts[0].rstrip("G"))
        used_gb = int(parts[1].rstrip("G"))
        free_gb = int(parts[2].rstrip("G"))
        return _ok(percent, f"Disk usage at {path}: {percent}%", {
            "path": path, "total_gb": total_gb,
            "used_gb": used_gb, "free_gb": free_gb,
        })
    except ValueError:
        return _fail(f"Could not parse df output: {stdout}")


def check_memory(ssh: SSHExecutor) -> dict:
    stdout, stderr, code = _run(ssh, "free -m | awk '/^Mem:/ {print $2, $3, $7}'")
    if code != 0:
        return _fail(f"free failed: {stderr}")
    parts = stdout.split()
    if len(parts) < 3:
        return _fail(f"Unexpected free output: {stdout}")
    try:
        total, used, available = int(parts[0]), int(parts[1]), int(parts[2])
        percent = round((used / total) * 100, 2) if total else 0
        return _ok(percent, f"Memory usage: {percent}%", {
            "total_mb": total, "used_mb": used, "available_mb": available,
        })
    except ValueError:
        return _fail(f"Could not parse memory output: {stdout}")


def check_cpu_load(ssh: SSHExecutor, window: str = "5m") -> dict:
    stdout, stderr, code = _run(ssh, "nproc && cat /proc/loadavg")
    if code != 0:
        return _fail(f"Load check failed: {stderr}")
    lines = stdout.strip().splitlines()
    if len(lines) < 2:
        return _fail(f"Unexpected output: {stdout}")
    try:
        cpu_count = int(lines[0].strip())
        load_parts = lines[1].split()
        load_map = {"1m": float(load_parts[0]), "5m": float(load_parts[1]), "15m": float(load_parts[2])}
        raw = load_map.get(window, float(load_parts[1]))
        normalized = round(raw / cpu_count, 4) if cpu_count else raw
        return _ok(normalized, f"Load avg ({window}): {raw:.2f} ({normalized:.2f}x over {cpu_count} CPUs)", {
            "load_1m": load_map["1m"], "load_5m": load_map["5m"],
            "load_15m": load_map["15m"], "cpu_count": cpu_count,
        })
    except (ValueError, IndexError):
        return _fail(f"Could not parse load output: {stdout}")


def check_service_status(ssh: SSHExecutor, name: str) -> dict:
    stdout, _, code = _run(ssh, f"systemctl is-active {name} 2>/dev/null || echo inactive")
    state = stdout.strip() or "unknown"
    active = state == "active"
    return {
        "value": 1 if active else 0,
        "ok": active,
        "message": f"Service {name}: {state}",
        "context": {"service": name, "state": state},
    }


def check_connectivity(ssh: SSHExecutor, target: str = "8.8.8.8", timeout: int = 5) -> dict:
    _, _, code = _run(ssh, f"ping -c 1 -W {timeout} {target} >/dev/null 2>&1")
    ok = code == 0
    return {
        "value": 1 if ok else 0,
        "ok": ok,
        "message": f"Connectivity to {target}: {'reachable' if ok else 'unreachable'}",
        "context": {"target": target},
    }


def check_dns(ssh: SSHExecutor, target: str = "google.com") -> dict:
    stdout, stderr, code = _run(ssh, f"getent hosts {target} | awk '{{print $1}}' | head -1")
    ok = code == 0 and bool(stdout.strip())
    ip = stdout.strip() or None
    return {
        "value": 1 if ok else 0,
        "ok": ok,
        "message": f"DNS resolved {target} -> {ip}" if ok else f"DNS resolution failed for {target}",
        "context": {"target": target, "resolved_ip": ip},
    }


# Security checks (the product's value)


def check_ssh_root_login(ssh: SSHExecutor) -> dict:
    stdout, _, code = _run(ssh, "sshd -T 2>/dev/null | grep -i '^permitrootlogin'")
    if code != 0 or not stdout:
        # Fallback: read config file directly
        stdout, _, _ = _run(ssh, "grep -i PermitRootLogin /etc/ssh/sshd_config 2>/dev/null | grep -v '^#' | tail -1")

    value = stdout.strip().lower()
    enabled = "yes" in value and "no" not in value.replace("yes", "", 1)
    return {
        "value": 1 if enabled else 0,
        "ok": not enabled,
        "message": "SSH root login is ENABLED — high risk" if enabled else "SSH root login is disabled",
        "context": {"raw": stdout.strip()},
    }


def check_ssh_password_auth(ssh: SSHExecutor) -> dict:
    stdout, _, _ = _run(ssh, "sshd -T 2>/dev/null | grep -i '^passwordauthentication'")
    if not stdout:
        stdout, _, _ = _run(ssh, "grep -i PasswordAuthentication /etc/ssh/sshd_config 2>/dev/null | grep -v '^#' | tail -1")
    enabled = "yes" in stdout.lower()
    return {
        "value": 1 if enabled else 0,
        "ok": not enabled,
        "message": "SSH password authentication is ENABLED — brute-force risk" if enabled else "SSH password auth is disabled (key-only)",
        "context": {"raw": stdout.strip()},
    }


def check_firewall(ssh: SSHExecutor) -> dict:
    # Try ufw first, then iptables
    stdout, _, code = _run(ssh, "ufw status 2>/dev/null | head -1")
    status_lower = stdout.lower()
    if "inactive" in status_lower:
        return {"value": 0, "ok": False, "message": "Firewall (ufw) is INACTIVE — no traffic filtering", "context": {"tool": "ufw"}}
    if "active" in status_lower:
        return _ok(1, "Firewall (ufw) is active", {"tool": "ufw", "status": stdout.strip()})

    # iptables fallback
    stdout, _, code = _run(ssh, "iptables -L INPUT 2>/dev/null | wc -l")
    try:
        rules = int(stdout.strip())
        if rules > 3:
            return _ok(1, f"iptables has {rules} INPUT rules", {"tool": "iptables", "rules": rules})
    except ValueError:
        pass

    return {"value": 0, "ok": False, "message": "No active firewall detected (ufw or iptables)", "context": {}}


def check_fail2ban(ssh: SSHExecutor) -> dict:
    stdout, _, code = _run(ssh, "systemctl is-active fail2ban 2>/dev/null || echo inactive")
    active = stdout.strip() == "active"
    if active:
        return _ok(1, "fail2ban is active", {"state": "active"})

    # Check if installed but not running
    _, _, installed_code = _run(ssh, "command -v fail2ban-client >/dev/null 2>&1")
    if installed_code == 0:
        return {"value": 0, "ok": False, "message": "fail2ban is installed but NOT running", "context": {}}

    return {"value": 0, "ok": False, "message": "fail2ban is NOT installed — no brute-force protection", "context": {}}


def check_unattended_upgrades(ssh: SSHExecutor) -> dict:
    _, _, code = _run(ssh, "dpkg -l unattended-upgrades 2>/dev/null | grep -q '^ii'")
    if code != 0:
        # Try yum/dnf
        _, _, yum_code = _run(ssh, "rpm -q dnf-automatic 2>/dev/null")
        if yum_code == 0:
            return _ok(1, "Automatic security updates configured (dnf-automatic)", {})
        return {"value": 0, "ok": False, "message": "Automatic security updates NOT configured", "context": {}}

    # Check if enabled
    stdout, _, _ = _run(ssh, "cat /etc/apt/apt.conf.d/20auto-upgrades 2>/dev/null")
    enabled = 'Unattended-Upgrade "1"' in stdout or "Unattended-Upgrade" in stdout
    return _ok(1, "Unattended upgrades configured", {}) if enabled else {
        "value": 0, "ok": False,
        "message": "unattended-upgrades installed but may not be enabled",
        "context": {"config": stdout[:200]},
    }


def check_open_ports(ssh: SSHExecutor, expected: list[int] | None = None) -> dict:
    expected = expected or [22, 80, 443]
    stdout, _, code = _run(ssh, "ss -tlnp 2>/dev/null | awk 'NR>1 {print $4}' | grep -oE '[0-9]+$' | sort -un")
    if code != 0 or not stdout:
        return _fail("Could not read open ports (ss unavailable)")

    try:
        open_ports = [int(p) for p in stdout.strip().splitlines() if p.strip().isdigit()]
        unexpected = [p for p in open_ports if p not in expected]
        ok = len(unexpected) == 0
        return {
            "value": len(unexpected),
            "ok": ok,
            "message": f"Unexpected open ports: {unexpected}" if unexpected else f"Only expected ports open: {expected}",
            "context": {"open": open_ports, "expected": expected, "unexpected": unexpected},
        }
    except ValueError:
        return _fail(f"Could not parse port list: {stdout}")


def check_ssh_port(ssh: SSHExecutor) -> dict:
    stdout, _, _ = _run(ssh, "sshd -T 2>/dev/null | grep '^port ' | awk '{print $2}'")
    if not stdout:
        stdout, _, _ = _run(ssh, "grep -E '^Port ' /etc/ssh/sshd_config 2>/dev/null | awk '{print $2}' | tail -1")
    try:
        port = int(stdout.strip()) if stdout.strip() else 22
        default = port == 22
        return {
            "value": port,
            "ok": not default,
            "message": "SSH is on default port 22 — consider changing it" if default else f"SSH is on non-default port {port}",
            "context": {"port": port},
        }
    except ValueError:
        return _fail(f"Could not determine SSH port: {stdout}")


# Check dispatch table
# Maps check_id from server.yml to the function that runs it over SSH


_CHECK_MAP = {
    # System
    "disk_usage_root": lambda ssh, check: check_disk_usage(ssh, check.args.get("path", "/")),
    "disk_usage_var": lambda ssh, check: check_disk_usage(ssh, check.args.get("path", "/var")),
    "memory_usage": lambda ssh, check: check_memory(ssh),
    "cpu_load": lambda ssh, check: check_cpu_load(ssh, check.args.get("window", "5m")),
    "external_connectivity": lambda ssh, check: check_connectivity(ssh, check.args.get("target", "8.8.8.8")),
    "dns_resolution": lambda ssh, check: check_dns(ssh, check.args.get("target", "google.com")),
    "service_nginx": lambda ssh, check: check_service_status(ssh, check.args.get("name", "nginx")),
    "service_ssh": lambda ssh, check: check_service_status(ssh, check.args.get("name", "ssh")),
    "service_cron": lambda ssh, check: check_service_status(ssh, check.args.get("name", "cron")),
    # Security
    "security_ssh_root_login": lambda ssh, check: check_ssh_root_login(ssh),
    "security_ssh_password_auth": lambda ssh, check: check_ssh_password_auth(ssh),
    "security_firewall": lambda ssh, check: check_firewall(ssh),
    "security_fail2ban": lambda ssh, check: check_fail2ban(ssh),
    "security_unattended_upgrades": lambda ssh, check: check_unattended_upgrades(ssh),
    "security_open_ports": lambda ssh, check: check_open_ports(ssh, check.args.get("expected", [22, 80, 443])),
    "security_ssh_port": lambda ssh, check: check_ssh_port(ssh),
}


def _make_issue(check: HealthCheckDef, host: str, result: dict) -> IssueReport:
    value = result.get("value")
    ok = result.get("ok", False)
    thresholds = check.thresholds

    if isinstance(value, (int, float)) and thresholds:
        status = _classify(float(value), thresholds)
    elif not ok:
        status = CheckStatus.FAIL
    else:
        status = CheckStatus.OK

    return IssueReport(
        check_id=check.id,
        host=host,
        adapter=check.adapter,
        method=check.method,
        status=status,
        value=value,
        threshold=thresholds.get("warn") if thresholds else None,
        message=result.get("message", ""),
        context=result.get("context", {}),
    )


def run_ssh_checks(
    host_config: HostConfig,
    checks: list[HealthCheckDef],
) -> list[IssueReport]:
    """
    Run all health checks on a remote host via SSH.
    Opens one connection, runs all checks, closes.
    Returns IssueReport list — same shape as local run_all_checks().
    """
    reports: list[IssueReport] = []

    try:
        executor = SSHExecutor.from_config(host_config)
        executor.connect()
    except Exception as e:  # noqa: BLE001
        logger.error("SSH connection failed for %s: %s", host_config.name, e)
        # Return one UNKNOWN report per check so the engine has something to record
        for check in checks:
            reports.append(IssueReport(
                check_id=check.id,
                host=host_config.name,
                adapter=check.adapter,
                method=check.method,
                status=CheckStatus.UNKNOWN,
                value=None,
                message=f"SSH connection failed: {e}",
            ))
        return reports

    try:
        for check in checks:
            fn = _CHECK_MAP.get(check.id)
            if fn is None:
                logger.warning("No SSH handler for check %s — skipping", check.id)
                continue
            try:
                result = fn(executor, check)
                issue = _make_issue(check, host_config.name, result)
                reports.append(issue)
                logger.debug("SSH check %s -> %s (%s)", check.id, issue.status, issue.message)
            except Exception as e:  # noqa: BLE001
                logger.error("SSH check %s failed: %s", check.id, e)
                reports.append(IssueReport(
                    check_id=check.id,
                    host=host_config.name,
                    adapter=check.adapter,
                    method=check.method,
                    status=CheckStatus.UNKNOWN,
                    value=None,
                    message=f"Check error: {e}",
                ))
    finally:
        executor.close()

    return reports
