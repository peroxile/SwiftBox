"""
Coordinates the full SwiftBox loop:
  input -> normalize -> decide -> act -> state -> verify
  
Entry point for external callers
Does not contain policy — reads everything from config.
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from core.detect import load_healthcheck_config, run_all_checks
from core.plan import load_permissions, load_workflows, plan
from core.schemas import (
    ActionRequest,
    ActionResult,
    ActionStatus,
    CheckStatus,
    ExecutionMode,
    HostConfig,
    IssueReport,
    SafetyLevel,
    StateRecord,
    VerificationResult,
)
from core.verify import all_passed, verify_workflow
from adapters.notify import filelog as notify_filelog
from adapters.notify import stdout as notify_stdout

logger = logging.getLogger(__name__)


# Config loaders

def _load_defaults(root: Path) -> dict:
    """Load config/default.yml if it exists. Returns empty dict if not found."""
    default_path = root / "config/default.yml"
    if default_path.exists():
        return yaml.safe_load(default_path.read_text()) or {}
    return {}


def _merge(base: dict, override: dict) -> dict:
    """Shallow merge: override wins on conflict at top level and one level deep."""
    result = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = {**result[k], **v}
        else:
            result[k] = v
    return result


def load_host_config(path: str | Path) -> HostConfig:
    path = Path(path)
    root = path.parent.parent.parent  # config/hosts/<host>.yml -> repo root
    defaults = _load_defaults(root)
    host_raw = yaml.safe_load(path.read_text())
    data = _merge(defaults, host_raw)
    h = data["host"]
    exe = data.get("execution", {})
    return HostConfig(
        name=h["name"],
        os_family=h["os_family"],
        shell=h["shell"],
        package_manager=h["package_manager"],
        user=h["user"],
        timezone=h["timezone"],
        execution_mode=ExecutionMode(exe.get("mode", "awareness")),
        safety_level=SafetyLevel(exe.get("safety_level", "high")),
        dry_run=exe.get("dry_run", True),
        allowed_actions=data.get("allowed_actions", []),
        blocked_actions=data.get("blocked_actions", []),
        log_path=data.get("notifications", {}).get("log_path", "logs/swiftbox.log"),
        repo_source=data.get("repo", {}).get("source"),
        repo_branch=data.get("repo", {}).get("branch", "main"),
        verify_checksums=data.get("repo", {}).get("verify_checksums", True),
        ssh_config=data.get("ssh"),   # None for local hosts
    )


def _notify_targets_for(host_config_path: Path) -> list[str]:
    raw = yaml.safe_load(host_config_path.read_text())
    return raw.get("notifications", {}).get("targets", ["stdout"])


# Notify dispatcher

def _notify(records: list[StateRecord], targets: list[str], log_path: str) -> None:
    for target in targets:
        if target == "stdout":
            notify_stdout.emit_all(records)
        elif target == "filelog":
            notify_filelog.emit_all(records, log_path=log_path)
        else:
            logger.warning("Unknown notification target: %s", target)


# Script executor


def _execute_script(req: ActionRequest) -> ActionResult:
    timestamp = datetime.now(timezone.utc).isoformat()

    if req.dry_run:
        logger.info("[dry-run] Would execute: %s args=%s", req.script_path, req.script_args)
        return ActionResult(
            workflow_id=req.workflow_id,
            script_path=req.script_path,
            host=req.host,
            status=ActionStatus.SKIPPED,
            exit_code=None,
            stdout="",
            stderr="",
            duration_seconds=0.0,
            dry_run=True,
            timestamp=timestamp,
        )

    if not req.approved:
        logger.warning("Action %s requires approval — skipping", req.workflow_id)
        return ActionResult(
            workflow_id=req.workflow_id,
            script_path=req.script_path,
            host=req.host,
            status=ActionStatus.BLOCKED,
            exit_code=None,
            stdout="",
            stderr="Awaiting approval",
            duration_seconds=0.0,
            dry_run=False,
            timestamp=timestamp,
        )

    if not req.script_path:
        # notify_only workflow — no script to run
        return ActionResult(
            workflow_id=req.workflow_id,
            script_path="",
            host=req.host,
            status=ActionStatus.SUCCESS,
            exit_code=0,
            stdout="notify_only: no script",
            stderr="",
            duration_seconds=0.0,
            dry_run=False,
            timestamp=timestamp,
        )

    script = Path(req.script_path)
    if not script.exists():
        logger.error("Script not found: %s", req.script_path)
        return ActionResult(
            workflow_id=req.workflow_id,
            script_path=req.script_path,
            host=req.host,
            status=ActionStatus.FAILED,
            exit_code=None,
            stdout="",
            stderr=f"Script not found: {req.script_path}",
            duration_seconds=0.0,
            dry_run=False,
            timestamp=timestamp,
        )

    env_args = {k: str(v) for k, v in req.script_args.items() if not k.startswith("_")}
    cmd = [str(script)] + [f"--{k}={v}" for k, v in env_args.items()]

    start = time.monotonic()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        duration = time.monotonic() - start
        status = ActionStatus.SUCCESS if proc.returncode == 0 else ActionStatus.FAILED
        return ActionResult(
            workflow_id=req.workflow_id,
            script_path=req.script_path,
            host=req.host,
            status=status,
            exit_code=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            duration_seconds=round(duration, 3),
            dry_run=False,
            timestamp=timestamp,
        )
    except subprocess.TimeoutExpired:
        return ActionResult(
            workflow_id=req.workflow_id,
            script_path=req.script_path,
            host=req.host,
            status=ActionStatus.FAILED,
            exit_code=None,
            stdout="",
            stderr="Timed out",
            duration_seconds=round(time.monotonic() - start, 3),
            dry_run=False,
            timestamp=timestamp,
        )


# State writer

def _write_state(record: StateRecord, state_dir: Path) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    history_path = state_dir / "history.jsonl"
    last_path = state_dir / "last_actions.json"

    def _serial(obj: Any) -> Any:
        if hasattr(obj, "__dataclass_fields__"):
            return {k: _serial(getattr(obj, k)) for k in obj.__dataclass_fields__}
        if isinstance(obj, (list, tuple)):
            return [_serial(i) for i in obj]
        if hasattr(obj, "value"):
            return obj.value
        return obj

    serialized = _serial(record)

    with history_path.open("a") as f:
        f.write(json.dumps(serialized) + "\n")

    with last_path.open("w") as f:
        json.dump(serialized, f, indent=2)


# Full detect -> plan -> act -> verify loop

def run(
    host_config_path: str | Path = "config/hosts/vps.yml",
    healthcheck_config_path: str | Path = "config/healthchecks/server.yml",
    workflows_path: str | Path = "config/workflows.yaml",
    permissions_path: str | Path = "config/permissions.yml",
    state_dir: str | Path = "state",
    role: str = "operator",
) -> list[StateRecord]:
    """
    Execute one full SwiftBox loop.
    Returns all StateRecords produced in this run.
    """
    host_config_path = Path(host_config_path)
    state_dir = Path(state_dir)

    host_config = load_host_config(host_config_path)
    checks = load_healthcheck_config(healthcheck_config_path)
    workflows = load_workflows(workflows_path)
    perms = load_permissions(permissions_path)
    notify_targets = _notify_targets_for(host_config_path)
    check_def_map = {c.id: c for c in checks}

    logger.info(
        "SwiftBox run: host=%s mode=%s dry_run=%s",
        host_config.name, host_config.execution_mode, host_config.dry_run,
    )

    
    if host_config.ssh_config:
        logger.info("SSH mode: running checks remotely on %s", host_config.ssh_config.get("host"))
        from adapters.ssh.checks import run_ssh_checks
        issues = run_ssh_checks(host_config, checks)
    else:
        issues = run_all_checks(checks, host_config.name)
    actionable = [i for i in issues if i.status != CheckStatus.OK]
    logger.info("%d checks run, %d require attention", len(issues), len(actionable))

    if not actionable:
        logger.info("All checks passed. No action required.")
        return []

    # Plan
    requests = plan(actionable, check_def_map, workflows, perms, host_config, role)
    logger.info("%d action requests planned", len(requests))

    # Act + Verify + State
    records: list[StateRecord] = []

    for req in requests:
        action_result = _execute_script(req)

        workflow = workflows.get(req.workflow_id)
        verifications: list[VerificationResult] = []

        if workflow:
            verifications = verify_workflow(
                workflow, action_result, req.trigger_issue, check_def_map, host_config.name
            )

        record = StateRecord(
            event="workflow.executed",
            host=host_config.name,
            workflow_id=req.workflow_id,
            issue=req.trigger_issue,
            action=action_result,
            verification=verifications[0] if verifications else None,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        _write_state(record, state_dir)
        records.append(record)

        logger.info(
            "Workflow %s: status=%s verified=%s",
            req.workflow_id, action_result.status, all_passed(verifications),
        )

    _notify(records, notify_targets, host_config.log_path)
    return records


# Single workflow dispatch
# Used by products that already know what workflow to run.


def run_workflow(
    workflow_id: str,
    host_config_path: str | Path = "config/hosts/vps.yml",
    workflows_path: str | Path = "config/workflows.yaml",
    permissions_path: str | Path = "config/permissions.yml",
    healthcheck_config_path: str | Path = "config/healthchecks/server.yml",
    state_dir: str | Path = "state",
    role: str = "operator",
    issue: IssueReport | None = None,
) -> StateRecord:
    """
    Execute one specific workflow by id and return a single StateRecord.

    The caller decides which workflow applies. SwiftBox enforces permissions,
    dry-run, and verification.

    `issue` is optional context from the caller (e.g. the parsed CLI error).
    If not provided, a minimal synthetic IssueReport is created.
    """
    from core.plan import _is_allowed, _requires_approval

    host_config_path = Path(host_config_path)
    state_dir = Path(state_dir)

    host_config = load_host_config(host_config_path)
    workflows = load_workflows(workflows_path)
    perms = load_permissions(permissions_path)
    checks = load_healthcheck_config(healthcheck_config_path)
    check_def_map = {c.id: c for c in checks}
    notify_targets = _notify_targets_for(host_config_path)

    workflow = workflows.get(workflow_id)
    if not workflow:
        raise ValueError(f"Workflow '{workflow_id}' not found in {workflows_path}")

    if issue is None:
        issue = IssueReport(
            check_id=f"external.{workflow_id}",
            host=host_config.name,
            adapter="external",
            method="dispatch",
            status=CheckStatus.FAIL,
            value=None,
            message=f"Dispatched directly by caller: {workflow_id}",
        )

    allowed, reason = _is_allowed(workflow_id, role, host_config.name, perms)
    if not allowed:
        logger.warning("run_workflow blocked: %s", reason)
        action_result = ActionResult(
            workflow_id=workflow_id,
            script_path="",
            host=host_config.name,
            status=ActionStatus.BLOCKED,
            exit_code=None,
            stdout="",
            stderr=reason,
            duration_seconds=0.0,
            dry_run=host_config.dry_run,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        record = StateRecord(
            event="workflow.blocked",
            host=host_config.name,
            workflow_id=workflow_id,
            issue=issue,
            action=action_result,
            verification=None,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        _write_state(record, state_dir)
        _notify([record], notify_targets, host_config.log_path)
        return record

    effective_dry_run = perms.global_dry_run or host_config.dry_run
    approved = not _requires_approval(workflow, workflow_id, perms, host_config)

    steps = workflow.steps
    script_path = steps[0].get("script", "") if steps else ""
    script_args: dict[str, Any] = dict(steps[0].get("args", {})) if steps else {}

    req = ActionRequest(
        workflow_id=workflow_id,
        host=host_config.name,
        role=role,
        trigger_issue=issue,
        script_path=script_path,
        script_args=script_args,
        dry_run=effective_dry_run,
        approved=approved,
    )

    action_result = _execute_script(req)
    verifications = verify_workflow(workflow, action_result, issue, check_def_map, host_config.name)

    record = StateRecord(
        event="workflow.executed",
        host=host_config.name,
        workflow_id=workflow_id,
        issue=issue,
        action=action_result,
        verification=verifications[0] if verifications else None,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
    _write_state(record, state_dir)
    _notify([record], notify_targets, host_config.log_path)
    return record
