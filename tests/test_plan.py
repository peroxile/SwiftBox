"""
Verify permission enforcement and workflow routing in core/plan.py.
"""

import pytest
from pathlib import Path
from unittest.mock import patch
from core.plan import plan, _is_allowed, load_workflows, load_permissions
from core.schemas import (
    CheckStatus,
    ExecutionMode,
    HealthCheckDef,
    IssueReport,
    PermissionsConfig,
    SafetyLevel,
    HostConfig,
)


import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

CONFIG = Path(__file__).parent.parent / "config"


# Fixtures

def make_host(dry_run=True, mode=ExecutionMode.AWARENESS) -> HostConfig:
    return HostConfig(
        name="vps",
        os_family="linux",
        shell="/bin/bash",
        package_manager="apt",
        user="root",
        timezone="UTC",
        execution_mode=mode,
        safety_level=SafetyLevel.HIGH,
        dry_run=dry_run,
        allowed_actions=["disk.clean_tmp", "services.restart"],
        blocked_actions=["disk.format", "system.reboot"],
        log_path="logs/vps.log",
    )


def make_perms(kill_switch=False, global_dry_run=True) -> PermissionsConfig:
    return PermissionsConfig(
        kill_switch=kill_switch,
        global_dry_run=global_dry_run,
        default_mode=ExecutionMode.AWARENESS,
        always_require_approval=["system.reboot"],
        permanently_blocked=["disk.format", "disk.wipe", "system.shutdown"],
        role_allowed={
            "operator": ["disk.clean_tmp", "services.restart", "notify_only", "network.restart_interface", "process.kill_zombie"],
            "readonly": ["disk.check_usage"],
        },
        role_denied={"operator": [], "readonly": []},
        host_overrides={"vps": {"blocked_actions": ["system.reboot"]}},
    )


def make_issue(check_id="disk_usage_root", status=CheckStatus.WARN) -> IssueReport:
    return IssueReport(
        check_id=check_id,
        host="vps",
        adapter="linux.disk",
        method="check_usage",
        status=status,
        value=82.0,
        message="Disk at 82%",
    )


def make_check_def(check_id="disk_usage_root", on_warn="disk.clean_tmp") -> HealthCheckDef:
    return HealthCheckDef(
        id=check_id,
        adapter="linux.disk",
        method="check_usage",
        args={"path": "/"},
        on_warn=on_warn,
        on_fail="notify_only",
        thresholds={"warn": 75, "critical": 90},
    )


# Permission tests

class TestIsAllowed:
    def test_allowed_for_operator(self):
        perms = make_perms()
        ok, reason = _is_allowed("disk.clean_tmp", "operator", "vps", perms)
        assert ok is True
        assert reason == ""

    def test_blocked_by_kill_switch(self):
        perms = make_perms(kill_switch=True)
        ok, reason = _is_allowed("disk.clean_tmp", "operator", "vps", perms)
        assert ok is False
        assert "kill_switch" in reason

    def test_permanently_blocked(self):
        perms = make_perms()
        ok, reason = _is_allowed("disk.format", "operator", "vps", perms)
        assert ok is False
        assert "permanently blocked" in reason

    def test_host_override_block(self):
        perms = make_perms()
        ok, reason = _is_allowed("system.reboot", "operator", "vps", perms)
        assert ok is False

    def test_not_in_role_allowed(self):
        perms = make_perms()
        ok, reason = _is_allowed("disk.clean_tmp", "readonly", "vps", perms)
        assert ok is False
        assert "not in allowed list" in reason


# Plan output tests

class TestPlan:
    def setup_method(self):
        self.workflows = load_workflows(CONFIG / "workflows.yaml")
        self.perms = make_perms()
        self.host = make_host()

    def test_ok_issue_produces_no_request(self):
        issue = make_issue(status=CheckStatus.OK)
        check_map = {"disk_usage_root": make_check_def()}
        requests = plan([issue], check_map, self.workflows, self.perms, self.host)
        assert requests == []

    def test_warn_issue_produces_request(self):
        issue = make_issue(status=CheckStatus.WARN)
        check_map = {"disk_usage_root": make_check_def(on_warn="disk.clean_tmp")}
        requests = plan([issue], check_map, self.workflows, self.perms, self.host)
        assert len(requests) == 1
        assert requests[0].workflow_id == "disk.clean_tmp"

    def test_dry_run_propagated(self):
        issue = make_issue(status=CheckStatus.WARN)
        check_map = {"disk_usage_root": make_check_def()}
        host = make_host(dry_run=True)
        requests = plan([issue], check_map, self.workflows, self.perms, host)
        assert all(r.dry_run for r in requests)

    def test_blocked_action_produces_no_request(self):
        issue = make_issue(status=CheckStatus.WARN)
        check_map = {"disk_usage_root": make_check_def(on_warn="disk.format")}
        requests = plan([issue], check_map, self.workflows, self.perms, self.host)
        assert requests == []

    def test_kill_switch_blocks_all(self):
        issue = make_issue(status=CheckStatus.WARN)
        check_map = {"disk_usage_root": make_check_def()}
        perms = make_perms(kill_switch=True)
        requests = plan([issue], check_map, self.workflows, perms, self.host)
        assert requests == []

    def test_notify_only_on_critical(self):
        issue = make_issue(status=CheckStatus.CRITICAL)
        check_def = HealthCheckDef(
            id="disk_usage_root",
            adapter="linux.disk",
            method="check_usage",
            args={"path": "/"},
            on_critical="notify_only",
            on_warn="disk.clean_tmp",
            thresholds={"warn": 75, "critical": 90},
        )
        check_map = {"disk_usage_root": check_def}
        requests = plan([issue], check_map, self.workflows, self.perms, self.host)
        assert len(requests) == 1
        assert requests[0].workflow_id == "notify_only"

    def test_unknown_check_id_skipped(self):
        issue = make_issue(check_id="nonexistent_check", status=CheckStatus.FAIL)
        requests = plan([issue], {}, self.workflows, self.perms, self.host)
        assert requests == []