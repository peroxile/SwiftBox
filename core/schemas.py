"""
Internal data structures for SwiftBox.
All config and runtime objects normalize to these types.
Keep flat, serializable, and free of business logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


# Enums

class SafetyLevel(str, Enum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ExecutionMode(str, Enum):
    DRY_RUN = "dry-run"
    AWARENESS = "awareness"
    AUTO = "auto"


class CheckStatus(str, Enum):
    OK = "ok"
    WARN = "warn"
    CRITICAL = "critical"
    FAIL = "fail"
    UNKNOWN = "unknown"


class ActionStatus(str, Enum):
    PENDING = "pending"
    SKIPPED = "skipped"
    SUCCESS = "success"
    FAILED = "failed"
    BLOCKED = "blocked"


# Config objects (loaded from YAML)


@dataclass
class HostConfig:
    name: str
    os_family: str                   # linux | macos | windows
    shell: str
    package_manager: str
    user: str
    timezone: str
    execution_mode: ExecutionMode
    safety_level: SafetyLevel
    dry_run: bool
    allowed_actions: list[str]
    blocked_actions: list[str]
    log_path: str
    repo_source: Optional[str] = None
    repo_branch: str = "main"
    verify_checksums: bool = True


@dataclass
class HealthCheckDef:
    """One health check entry from config/healthchecks/server.yml."""
    id: str
    adapter: str                     # e.g. linux.disk
    method: str                      # e.g. check_usage
    args: dict[str, Any]
    on_fail: Optional[str] = None    # workflow id
    on_warn: Optional[str] = None
    on_critical: Optional[str] = None
    thresholds: Optional[dict[str, float]] = None


@dataclass
class WorkflowDef:
    id: str
    description: str
    safety_level: SafetyLevel
    reversible: bool
    dry_run_safe: bool
    steps: list[dict[str, Any]]
    verify_after: list[str]
    on_failure: Optional[str]
    requires_approval: bool = False


@dataclass
class PermissionsConfig:
    kill_switch: bool
    global_dry_run: bool
    default_mode: ExecutionMode
    always_require_approval: list[str]
    permanently_blocked: list[str]
    role_allowed: dict[str, list[str]]   # role -> allowed action ids
    role_denied: dict[str, list[str]]
    host_overrides: dict[str, dict[str, Any]]


# Runtime objects


@dataclass
class IssueReport:
    """Normalized output from detect.py."""
    check_id: str
    host: str
    adapter: str
    method: str
    status: CheckStatus
    value: Any                          # raw measured value
    threshold: Optional[float] = None
    message: str = ""
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class ActionRequest:
    """Decision output from plan.py — describes what should be run."""
    workflow_id: str
    host: str
    role: str
    trigger_issue: IssueReport
    script_path: str
    script_args: dict[str, Any]
    dry_run: bool
    approved: bool = False


@dataclass
class ActionResult:
    """Output from executing one workflow step."""
    workflow_id: str
    script_path: str
    host: str
    status: ActionStatus
    exit_code: Optional[int]
    stdout: str
    stderr: str
    duration_seconds: float
    dry_run: bool
    timestamp: str                      # ISO 8601


@dataclass
class VerificationResult:
    check_id: str
    host: str
    passed: bool
    before: Any
    after: Any
    delta: Any
    message: str
    timestamp: str


@dataclass
class StateRecord:
    """Written to state/ after any meaningful action."""
    event: str                          # e.g. "workflow.executed"
    host: str
    workflow_id: Optional[str]
    issue: Optional[IssueReport]
    action: Optional[ActionResult]
    verification: Optional[VerificationResult]
    timestamp: str