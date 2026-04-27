"""
Emit a StateRecord as human-readable text to stdout.
Used by the CLI and during development.
Products that need structured output should use --json on the CLI instead.
"""

from __future__ import annotations

import sys
from core.schemas import ActionStatus, CheckStatus, StateRecord


_STATUS_SYMBOL = {
    ActionStatus.SUCCESS: "✓",
    ActionStatus.SKIPPED: "~",
    ActionStatus.BLOCKED: "⊘",
    ActionStatus.FAILED:  "✗",
    ActionStatus.PENDING: "…",
}


def emit(record: StateRecord) -> None:
    """Write a single StateRecord summary to stdout."""
    lines: list[str] = []

    issue = record.issue
    action = record.action
    verification = record.verification

    # Header
    host = record.host or "unknown"
    workflow = record.workflow_id or "none"
    lines.append(f"[swiftbox] host={host} workflow={workflow}")

    # Issue
    if issue:
        status_str = issue.status.value if issue.status else "?"
        lines.append(f"  issue   : {issue.check_id} [{status_str}] — {issue.message}")

    # Action
    if action:
        symbol = _STATUS_SYMBOL.get(action.status, "?")
        dry = " (dry-run)" if action.dry_run else ""
        lines.append(f"  action  : {symbol} {action.status.value}{dry}")
        if action.stderr and action.status == ActionStatus.FAILED:
            lines.append(f"  stderr  : {action.stderr.strip()}")

    # Verification
    if verification:
        passed = "passed" if verification.passed else "failed"
        lines.append(f"  verify  : {passed} — {verification.message}")
    elif action and not action.dry_run and action.status == ActionStatus.SUCCESS:
        lines.append("  verify  : skipped")

    print("\n".join(lines), file=sys.stdout, flush=True)


def emit_all(records: list[StateRecord]) -> None:
    if not records:
        print("[swiftbox] all checks passed — no actions taken", flush=True)
        return
    for record in records:
        emit(record)
