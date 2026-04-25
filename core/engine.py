"""
core/engine.py

Coordinates the full SwiftBox loop:
  input -> normalize -> decide -> act -> state -> verify

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


# Config loader

def load_host_config(path: str | Path) -> HostConfig:
    data = yaml.safe_load(Path(path).read_text())
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
    )


def _notify(
    records: list[StateRecord],
    targets: list[str],
    log_path: str,
) -> None:
    """Dispatch completed records to all configured notification targets."""
    for target in targets:
        if target == "stdout":
            notify_stdout.emit_all(records)
        elif target == "filelog":
            notify_filelog.emit_all(records, log_path=log_path)
        else:
            logger.warning("Unknown notification target: %s", target)


# Script executor

def _execute_script(req: ActionRequest) -> ActionResult:
    """
    Run the script for an ActionRequest.
    Dry-run returns immediately without executing.
    """
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
        # notify_only workflow — nothing to execute
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
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
        )
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
        if hasattr(obj, "value"):     # Enum
            return obj.value
        return obj

    serialized = _serial(record)

    with history_path.open("a") as f:
        f.write(json.dumps(serialized) + "\n")

    with last_path.open("w") as f:
        json.dump(serialized, f, indent=2)


# Main engine loop

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
    state_dir = Path(state_dir)

    # Load config
    host_config = load_host_config(host_config_path)
    checks = load_healthcheck_config(healthcheck_config_path)
    workflows = load_workflows(workflows_path)
    perms = load_permissions(permissions_path)

    # Read notification targets from host config yaml directly
    _raw = yaml.safe_load(Path(host_config_path).read_text())
    notify_targets: list[str] = _raw.get("notifications", {}).get("targets", ["stdout"])

    check_def_map = {c.id: c for c in checks}

    logger.info(
        "SwiftBox run: host=%s mode=%s dry_run=%s",
        host_config.name, host_config.execution_mode, host_config.dry_run,
    )

    #  Detect
    issues = run_all_checks(checks, host_config.name)
    actionable = [i for i in issues if i.status != CheckStatus.OK]
    logger.info("%d checks run, %d require attention", len(issues), len(actionable))

    if not actionable:
        logger.info("All checks passed. No action required.")
        return []

    #  Plan
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

        verified = all_passed(verifications)
        logger.info(
            "Workflow %s: status=%s verified=%s",
            req.workflow_id, action_result.status, verified,
        )

    _notify(records, notify_targets, host_config.log_path)
    return records