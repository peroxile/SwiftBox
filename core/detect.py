"""
Run health checks defined in config/healthchecks/server.yml and normalize
each result into an IssueReport. Does not decide what to do — only reports.
"""

from __future__ import annotations

import importlib
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from core.schemas import CheckStatus, HealthCheckDef, IssueReport

logger = logging.getLogger(__name__)


# Config loader

def load_healthcheck_config(path: str | Path) -> list[HealthCheckDef]:
    """Parse healthchecks YAML into a flat list of HealthCheckDef objects."""
    data = yaml.safe_load(Path(path).read_text())
    checks: list[HealthCheckDef] = []

    for _category, entries in data.get("healthchecks", {}).items():
        for entry in entries:
            checks.append(HealthCheckDef(
                id=entry["id"],
                adapter=entry["adapter"],
                method=entry["method"],
                args=entry.get("args", {}),
                on_fail=entry.get("on_fail"),
                on_warn=entry.get("on_warn"),
                on_critical=entry.get("on_critical"),
                thresholds=entry.get("thresholds"),
            ))

    return checks


# Adapter resolver

def _resolve_adapter(adapter_path: str) -> Any:
    """
    Turn 'linux.disk' into the adapters.linux.disk module.
    Raises ImportError if the adapter doesn't exist.
    """
    parts = adapter_path.split(".")
    module_path = "adapters." + ".".join(parts)
    return importlib.import_module(module_path)


# Status classifier

def _classify(value: float, thresholds: dict[str, float] | None) -> CheckStatus:
    """Map a numeric value to a CheckStatus using warn/critical thresholds."""
    if thresholds is None:
        return CheckStatus.OK
    if value >= thresholds.get("critical", float("inf")):
        return CheckStatus.CRITICAL
    if value >= thresholds.get("warn", float("inf")):
        return CheckStatus.WARN
    return CheckStatus.OK


# Single check runner

def run_check(check: HealthCheckDef, host: str) -> IssueReport:
    """
    Execute one health check and return a normalized IssueReport.
    Never raises — exceptions are caught and returned as UNKNOWN status.
    """
    try:
        adapter = _resolve_adapter(check.adapter)
        method = getattr(adapter, check.method)
        result = method(**check.args)

        # result is expected to be a dict with at minimum {"value": ..., "message": ...}
        value = result.get("value")
        message = result.get("message", "")
        context = result.get("context", {})

        if isinstance(value, (int, float)) and check.thresholds:
            status = _classify(float(value), check.thresholds)
        elif result.get("ok") is False:
            status = CheckStatus.FAIL
        else:
            status = CheckStatus.OK

        threshold = None
        if check.thresholds:
            threshold = check.thresholds.get("warn")

        return IssueReport(
            check_id=check.id,
            host=host,
            adapter=check.adapter,
            method=check.method,
            status=status,
            value=value,
            threshold=threshold,
            message=message,
            context=context,
        )

    except ImportError as e:
        logger.error("Adapter not found for check %s: %s", check.id, e)
        return IssueReport(
            check_id=check.id,
            host=host,
            adapter=check.adapter,
            method=check.method,
            status=CheckStatus.UNKNOWN,
            value=None,
            message=f"Adapter import failed: {e}",
        )
    except Exception as e:  # noqa: BLE001
        logger.error("Check %s failed with unexpected error: %s", check.id, e)
        return IssueReport(
            check_id=check.id,
            host=host,
            adapter=check.adapter,
            method=check.method,
            status=CheckStatus.UNKNOWN,
            value=None,
            message=f"Unexpected error: {e}",
        )


# Batch runner

def run_all_checks(
    checks: list[HealthCheckDef],
    host: str,
) -> list[IssueReport]:
    """Run every check in the list and return all reports."""
    reports: list[IssueReport] = []
    for check in checks:
        logger.debug("Running check: %s", check.id)
        report = run_check(check, host)
        reports.append(report)
        logger.debug(
            "Check %s -> status=%s value=%s",
            report.check_id, report.status, report.value,
        )
    return reports


def run_checks_from_config(config_path: str | Path, host: str) -> list[IssueReport]:
    """Convenience: load config and run all checks in one call."""
    checks = load_healthcheck_config(config_path)
    return run_all_checks(checks, host)
