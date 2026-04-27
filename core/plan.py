"""
Map IssueReports to ActionRequests.
Applies permissions, host config, and workflow definitions.
Does not execute anything — only produces a decision.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from core.schemas import (
    ActionRequest,
    ActionStatus,
    CheckStatus,
    ExecutionMode,
    HostConfig,
    IssueReport,
    PermissionsConfig,
    SafetyLevel,
    WorkflowDef,
)

logger = logging.getLogger(__name__)


# Config loaders

def load_workflows(path: str | Path) -> dict[str, WorkflowDef]:
    data = yaml.safe_load(Path(path).read_text())
    result: dict[str, WorkflowDef] = {}
    for wid, entry in data.get("workflows", {}).items():
        result[wid] = WorkflowDef(
            id=wid,
            description=entry.get("description", ""),
            safety_level=SafetyLevel(entry.get("safety_level", "low")),
            reversible=entry.get("reversible", False),
            dry_run_safe=entry.get("dry_run_safe", False),
            steps=entry.get("steps", []),
            verify_after=entry.get("verify_after", []),
            on_failure=entry.get("on_failure"),
            requires_approval=entry.get("requires_approval", False),
        )
    return result


def load_permissions(path: str | Path) -> PermissionsConfig:
    data = yaml.safe_load(Path(path).read_text())
    roles = data.get("roles", {})
    return PermissionsConfig(
        kill_switch=data.get("kill_switch", False),
        global_dry_run=data.get("global_dry_run", True),
        default_mode=ExecutionMode(data.get("default_mode", "awareness")),
        always_require_approval=data.get("always_require_approval", []),
        permanently_blocked=data.get("permanently_blocked", []),
        role_allowed={r: v.get("allowed_actions", []) for r, v in roles.items()},
        role_denied={r: v.get("denied_actions", []) for r, v in roles.items()},
        host_overrides=data.get("host_overrides", {}),
    )


# Permission checks


def _is_allowed(
    action_id: str,
    role: str,
    host: str,
    perms: PermissionsConfig,
) -> tuple[bool, str]:
    """Return (allowed, reason). Reason is non-empty when blocked."""
    if perms.kill_switch:
        return False, "kill_switch is active"

    if action_id in perms.permanently_blocked:
        return False, f"{action_id} is permanently blocked"

    host_overrides = perms.host_overrides.get(host, {})
    host_blocked = host_overrides.get("blocked_actions", [])
    if action_id in host_blocked:
        return False, f"{action_id} is blocked for host {host}"

    denied = perms.role_denied.get(role, [])
    if action_id in denied:
        return False, f"{action_id} is denied for role {role}"

    allowed = perms.role_allowed.get(role, [])
    if action_id not in allowed:
        return False, f"{action_id} is not in allowed list for role {role}"

    return True, ""


def _requires_approval(
    workflow: WorkflowDef,
    action_id: str,
    perms: PermissionsConfig,
    host_config: HostConfig,
) -> bool:
    if workflow.requires_approval:
        return True
    if action_id in perms.always_require_approval:
        return True
    if host_config.execution_mode == ExecutionMode.AWARENESS:
        return True
    return False


# Workflow selector


def _select_workflow_id(issue: IssueReport, check_def_map: dict[str, Any]) -> str | None:
    """
    Pick the correct workflow id from the health check definition
    based on the issue's status.
    """
    check_def = check_def_map.get(issue.check_id)
    if check_def is None:
        return None

    if issue.status == CheckStatus.CRITICAL:
        return check_def.on_critical or check_def.on_fail
    if issue.status == CheckStatus.WARN:
        return check_def.on_warn or check_def.on_fail
    if issue.status == CheckStatus.FAIL:
        return check_def.on_fail
    return None


# Core planning function

def plan(
    issues: list[IssueReport],
    check_def_map: dict[str, Any],     # check_id -> HealthCheckDef
    workflows: dict[str, WorkflowDef],
    perms: PermissionsConfig,
    host_config: HostConfig,
    role: str = "operator",
) -> list[ActionRequest]:
    """
    For each issue that needs action, produce an ActionRequest.
    Issues with status OK are skipped.
    """
    requests: list[ActionRequest] = []

    for issue in issues:
        if issue.status == CheckStatus.OK:
            logger.debug("Check %s is OK — no action needed", issue.check_id)
            continue

        workflow_id = _select_workflow_id(issue, check_def_map)
        if not workflow_id:
            logger.debug("No workflow mapped for check %s status %s", issue.check_id, issue.status)
            continue

        workflow = workflows.get(workflow_id)
        if not workflow:
            logger.warning("Workflow %s not found in workflows.yaml", workflow_id)
            continue

        allowed, reason = _is_allowed(workflow_id, role, host_config.name, perms)
        if not allowed:
            logger.warning("Action blocked for %s: %s", workflow_id, reason)
            continue

        effective_dry_run = (
            perms.global_dry_run
            or host_config.dry_run
            or (not workflow.dry_run_safe and host_config.execution_mode == ExecutionMode.DRY_RUN)
        )

        approved = not _requires_approval(workflow, workflow_id, perms, host_config)

        steps = workflow.steps
        if not steps:
            # notify_only or empty — still produce a record
            script_path = ""
            script_args: dict[str, Any] = {}
        else:
            first_step = steps[0]
            script_path = first_step.get("script", "")
            script_args = dict(first_step.get("args", {}))
            # Inject runtime context for template args
            script_args["_issue"] = {
                "check_id": issue.check_id,
                "status": issue.status,
                "value": issue.value,
                "host": issue.host,
            }

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
        requests.append(req)
        logger.info(
            "Planned: workflow=%s host=%s dry_run=%s approved=%s",
            workflow_id, host_config.name, effective_dry_run, approved,
        )

    return requests
