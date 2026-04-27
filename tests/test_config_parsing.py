"""
Verify that all YAML config files parse into the expected shapes.
"""

import pytest
import sys
from core.engine import load_host_config
from core.detect import load_healthcheck_config
from core.plan import load_workflows, load_permissions
from core.schemas import ExecutionMode, SafetyLevel
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

CONFIG = Path(__file__).parent.parent / "config"


class TestHostConfig:
    def test_loads(self):
        cfg = load_host_config(CONFIG / "hosts/vps.yml")
        assert cfg.name == "vps"
        assert cfg.os_family == "linux"
        assert cfg.shell == "/bin/bash"
        assert cfg.dry_run is True

    def test_execution_mode_valid(self):
        cfg = load_host_config(CONFIG / "hosts/vps.yml")
        assert isinstance(cfg.execution_mode, ExecutionMode)

    def test_safety_level_valid(self):
        cfg = load_host_config(CONFIG / "hosts/vps.yml")
        assert isinstance(cfg.safety_level, SafetyLevel)

    def test_allowed_actions_not_empty(self):
        cfg = load_host_config(CONFIG / "hosts/vps.yml")
        assert len(cfg.allowed_actions) > 0

    def test_blocked_actions_present(self):
        cfg = load_host_config(CONFIG / "hosts/vps.yml")
        assert "disk.format" in cfg.blocked_actions


class TestHealthChecks:
    def test_loads(self):
        checks = load_healthcheck_config(CONFIG / "healthchecks/server.yml")
        assert len(checks) > 0

    def test_all_have_id_and_adapter(self):
        checks = load_healthcheck_config(CONFIG / "healthchecks/server.yml")
        for c in checks:
            assert c.id, f"Missing id: {c}"
            assert c.adapter, f"Missing adapter for {c.id}"
            assert c.method, f"Missing method for {c.id}"

    def test_known_check_ids_present(self):
        checks = load_healthcheck_config(CONFIG / "healthchecks/server.yml")
        ids = {c.id for c in checks}
        for expected in ["disk_usage_root", "external_connectivity", "service_nginx"]:
            assert expected in ids, f"{expected} not found in healthchecks"

    def test_ssh_is_notify_only(self):
        checks = load_healthcheck_config(CONFIG / "healthchecks/server.yml")
        ssh = next((c for c in checks if c.id == "service_ssh"), None)
        assert ssh is not None
        assert ssh.on_fail == "notify_only"


class TestWorkflows:
    def test_loads(self):
        wf = load_workflows(CONFIG / "workflows.yaml")
        assert len(wf) > 0

    def test_all_have_safety_level(self):
        wf = load_workflows(CONFIG / "workflows.yaml")
        for wid, w in wf.items():
            assert isinstance(w.safety_level, SafetyLevel), f"{wid} has invalid safety_level"

    def test_notify_only_has_no_steps(self):
        wf = load_workflows(CONFIG / "workflows.yaml")
        assert "notify_only" in wf
        assert wf["notify_only"].steps == []

    def test_destructive_workflows_not_present(self):
        wf = load_workflows(CONFIG / "workflows.yaml")
        for wid in wf:
            assert "reboot" not in wid
            assert "shutdown" not in wid
            assert "format" not in wid


class TestPermissions:
    def test_loads(self):
        perms = load_permissions(CONFIG / "permissions.yml")
        assert perms is not None

    def test_kill_switch_defaults_false(self):
        perms = load_permissions(CONFIG / "permissions.yml")
        assert perms.kill_switch is False

    def test_global_dry_run_defaults_true(self):
        perms = load_permissions(CONFIG / "permissions.yml")
        assert perms.global_dry_run is True

    def test_permanently_blocked_populated(self):
        perms = load_permissions(CONFIG / "permissions.yml")
        assert len(perms.permanently_blocked) > 0
        assert "disk.format" in perms.permanently_blocked

    def test_operator_role_exists(self):
        perms = load_permissions(CONFIG / "permissions.yml")
        assert "operator" in perms.role_allowed

    def test_readonly_cannot_write(self):
        perms = load_permissions(CONFIG / "permissions.yml")
        allowed = perms.role_allowed.get("readonly", [])
        for action in allowed:
            assert "restart" not in action
            assert "clean" not in action
            assert "kill" not in action