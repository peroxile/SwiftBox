"""
After a workflow executes, re-run the relevant checks and confirm
the issue is resolved. Produces a VerificationResult per check.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from core.detect import run_check
from core.schemas import (
    ActionResult,
    ActionStatus,
    CheckStatus,
    HealthCheckDef,
    IssueReport,
    VerificationResult,
    WorkflowDef,
)

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _delta(before: Any, after: Any) -> Any:
    """Compute a simple delta for numeric values; None otherwise."""
    if isinstance(before, (int, float)) and isinstance(after, (int, float)):
        return round(after - before, 4)
    return None


# Single verification

def verify_check(
    check_def: HealthCheckDef,
    prior_issue: IssueReport,
    host: str,
) -> VerificationResult:
    """
    Re-run one check and compare against the prior result.
    A pass means the new status is OK (or better than before for WARN).
    """
    new_report = run_check(check_def, host)
    passed = new_report.status == CheckStatus.OK

    # Also accept WARN as resolved if original was CRITICAL
    if prior_issue.status == CheckStatus.CRITICAL and new_report.status == CheckStatus.WARN:
        passed = True

    message = (
        f"Resolved: {prior_issue.status} -> {new_report.status}"
        if passed
        else f"Unresolved: {prior_issue.status} -> {new_report.status}"
    )

    return VerificationResult(
        check_id=check_def.id,
        host=host,
        passed=passed,
        before=prior_issue.value,
        after=new_report.value,
        delta=_delta(prior_issue.value, new_report.value),
        message=message,
        timestamp=_now(),
    )


# Workflow verification

def verify_workflow(
    workflow: WorkflowDef,
    action_result: ActionResult,
    trigger_issue: IssueReport,
    check_def_map: dict[str, HealthCheckDef],
    host: str,
) -> list[VerificationResult]:
    """
    Run all verify_after checks for a workflow.
    If the action failed or was dry-run, still record the result as not verified.
    """
    results: list[VerificationResult] = []

    if action_result.status in (ActionStatus.BLOCKED, ActionStatus.SKIPPED):
        logger.info(
            "Verification skipped: action was %s", action_result.status
        )
        return results

    if action_result.dry_run:
        logger.info("Verification skipped: dry_run=True")
        return results

    if not workflow.verify_after:
        logger.debug("No verify_after checks configured for %s", workflow.id)
        return results

    for check_id in workflow.verify_after:
        check_def = check_def_map.get(check_id)
        if check_def is None:
            logger.warning("Verify check %s not found in check_def_map", check_id)
            results.append(VerificationResult(
                check_id=check_id,
                host=host,
                passed=False,
                before=None,
                after=None,
                delta=None,
                message=f"Check definition {check_id} not found",
                timestamp=_now(),
            ))
            continue

        # Use the original trigger issue for the primary check, else re-run fresh
        prior = trigger_issue if check_id == trigger_issue.check_id else IssueReport(
            check_id=check_id,
            host=host,
            adapter=check_def.adapter,
            method=check_def.method,
            status=CheckStatus.UNKNOWN,
            value=None,
        )

        result = verify_check(check_def, prior, host)
        results.append(result)
        logger.info(
            "Verify %s: passed=%s before=%s after=%s",
            result.check_id, result.passed, result.before, result.after,
        )

    return results


# Summary helper

def all_passed(results: list[VerificationResult]) -> bool:
    if not results:
        return False
    return all(r.passed for r in results)