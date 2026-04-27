"""
Verify IssueReport normalization from adapter output in core/detect.py.
"""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from core.detect import run_check, _classify
from core.schemas import CheckStatus, HealthCheckDef


import sys
sys.path.insert(0, str(Path(__file__).parent.parent))


def make_check(
    check_id="disk_usage_root",
    adapter="linux.disk",
    method="check_usage",
    args=None,
    thresholds=None,
    on_fail="notify_only",
) -> HealthCheckDef:
    return HealthCheckDef(
        id=check_id,
        adapter=adapter,
        method=method,
        args=args or {"path": "/"},
        on_fail=on_fail,
        thresholds=thresholds,
    )


# Threshold classifier

class TestClassify:
    def test_below_warn_is_ok(self):
        assert _classify(50.0, {"warn": 75, "critical": 90}) == CheckStatus.OK

    def test_at_warn_is_warn(self):
        assert _classify(75.0, {"warn": 75, "critical": 90}) == CheckStatus.WARN

    def test_above_warn_is_warn(self):
        assert _classify(80.0, {"warn": 75, "critical": 90}) == CheckStatus.WARN

    def test_at_critical_is_critical(self):
        assert _classify(90.0, {"warn": 75, "critical": 90}) == CheckStatus.CRITICAL

    def test_no_thresholds_returns_ok(self):
        assert _classify(99.0, None) == CheckStatus.OK


# run_check normalization

class TestRunCheck:
    def test_ok_result(self):
        check = make_check(thresholds={"warn": 75, "critical": 90})
        mock_result = {"value": 50.0, "ok": True, "message": "50%", "context": {}}

        with patch("core.detect._resolve_adapter") as mock_adapter:
            mock_module = MagicMock()
            mock_module.check_usage.return_value = mock_result
            mock_adapter.return_value = mock_module

            report = run_check(check, "vps")

        assert report.status == CheckStatus.OK
        assert report.value == 50.0
        assert report.check_id == "disk_usage_root"

    def test_warn_threshold_triggers(self):
        check = make_check(thresholds={"warn": 75, "critical": 90})
        mock_result = {"value": 80.0, "ok": True, "message": "80%", "context": {}}

        with patch("core.detect._resolve_adapter") as mock_adapter:
            mock_module = MagicMock()
            mock_module.check_usage.return_value = mock_result
            mock_adapter.return_value = mock_module

            report = run_check(check, "vps")

        assert report.status == CheckStatus.WARN

    def test_ok_false_returns_fail(self):
        check = make_check()
        mock_result = {"value": None, "ok": False, "message": "unreachable", "context": {}}

        with patch("core.detect._resolve_adapter") as mock_adapter:
            mock_module = MagicMock()
            mock_module.check_usage.return_value = mock_result
            mock_adapter.return_value = mock_module

            report = run_check(check, "vps")

        assert report.status == CheckStatus.FAIL

    def test_import_error_returns_unknown(self):
        check = make_check(adapter="linux.nonexistent")

        report = run_check(check, "vps")

        assert report.status == CheckStatus.UNKNOWN
        assert "import" in report.message.lower()

    def test_exception_returns_unknown(self):
        check = make_check()

        with patch("core.detect._resolve_adapter") as mock_adapter:
            mock_module = MagicMock()
            mock_module.check_usage.side_effect = RuntimeError("boom")
            mock_adapter.return_value = mock_module

            report = run_check(check, "vps")

        assert report.status == CheckStatus.UNKNOWN
        assert "boom" in report.message