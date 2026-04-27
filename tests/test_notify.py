"""
Verify stdout and filelog adapters handle StateRecords correctly.
"""

from __future__ import annotations
import json
import sys
import pytest
import tempfile
from io import StringIO
from pathlib import Path
from adapters.notify import stdout as notify_stdout
from adapters.notify import filelog as notify_filelog
from core.schemas import (
    ActionResult,
    ActionStatus,
    CheckStatus,
    IssueReport,
    StateRecord,
    VerificationResult,
)


sys.path.insert(0, str(Path(__file__).parent.parent))

# Fixtures


def make_record(
    workflow_id="disk.clean_tmp",
    action_status=ActionStatus.SKIPPED,
    dry_run=True,
    verified=True,
) -> StateRecord:
    issue = IssueReport(
        check_id="disk_usage_root",
        host="vps",
        adapter="linux.disk",
        method="check_usage",
        status=CheckStatus.WARN,
        value=82.0,
        message="Disk at 82%",
    )
    action = ActionResult(
        workflow_id=workflow_id,
        script_path="scripts/fix/clean_tmp.sh",
        host="vps",
        status=action_status,
        exit_code=0 if action_status == ActionStatus.SUCCESS else None,
        stdout="",
        stderr="",
        duration_seconds=0.0,
        dry_run=dry_run,
        timestamp="2025-01-01T00:00:00+00:00",
    )
    verification = VerificationResult(
        check_id="disk_usage_root",
        host="vps",
        passed=verified,
        before=82.0,
        after=60.0,
        delta=-22.0,
        message="Resolved: warn -> ok",
        timestamp="2025-01-01T00:00:01+00:00",
    )
    return StateRecord(
        event="workflow.executed",
        host="vps",
        workflow_id=workflow_id,
        issue=issue,
        action=action,
        verification=verification,
        timestamp="2025-01-01T00:00:01+00:00",
    )


# stdout adapter

class TestStdoutNotify:
    def _capture(self, fn, *args, **kwargs) -> str:
        buf = StringIO()
        original = sys.stdout
        sys.stdout = buf
        try:
            fn(*args, **kwargs)
        finally:
            sys.stdout = original
        return buf.getvalue()

    def test_emit_contains_host_and_workflow(self):
        record = make_record()
        out = self._capture(notify_stdout.emit, record)
        assert "vps" in out
        assert "disk.clean_tmp" in out

    def test_emit_contains_issue_check_id(self):
        record = make_record()
        out = self._capture(notify_stdout.emit, record)
        assert "disk_usage_root" in out

    def test_emit_dry_run_label(self):
        record = make_record(dry_run=True, action_status=ActionStatus.SKIPPED)
        out = self._capture(notify_stdout.emit, record)
        assert "dry-run" in out

    def test_emit_all_empty_prints_all_clear(self):
        out = self._capture(notify_stdout.emit_all, [])
        assert "all checks passed" in out

    def test_emit_all_multiple_records(self):
        records = [make_record(), make_record(workflow_id="services.restart")]
        out = self._capture(notify_stdout.emit_all, records)
        assert "disk.clean_tmp" in out
        assert "services.restart" in out

    def test_emit_failed_action_shows_stderr(self):
        record = make_record(action_status=ActionStatus.FAILED, dry_run=False)
        record.action.stderr = "script not found"
        out = self._capture(notify_stdout.emit, record)
        assert "script not found" in out


# filelog adapter


class TestFilelogNotify:
    def test_emit_writes_valid_json_line(self):
        record = make_record()
        with tempfile.NamedTemporaryFile(mode="r", suffix=".log", delete=False) as f:
            path = f.name
        notify_filelog.emit(record, log_path=path)
        lines = Path(path).read_text().splitlines()
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["host"] == "vps"
        assert parsed["workflow_id"] == "disk.clean_tmp"

    def test_emit_all_writes_one_line_per_record(self):
        records = [make_record(), make_record(workflow_id="services.restart")]
        with tempfile.NamedTemporaryFile(mode="r", suffix=".log", delete=False) as f:
            path = f.name
        notify_filelog.emit_all(records, log_path=path)
        lines = Path(path).read_text().splitlines()
        assert len(lines) == 2

    def test_emit_appends_not_overwrites(self):
        record = make_record()
        with tempfile.NamedTemporaryFile(mode="r", suffix=".log", delete=False) as f:
            path = f.name
        notify_filelog.emit(record, log_path=path)
        notify_filelog.emit(record, log_path=path)
        lines = Path(path).read_text().splitlines()
        assert len(lines) == 2

    def test_emit_creates_parent_dirs(self):
        record = make_record()
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "nested" / "dir" / "swiftbox.log"
            notify_filelog.emit(record, log_path=log_path)
            assert log_path.exists()

    def test_emit_record_has_timestamp(self):
        record = make_record()
        with tempfile.NamedTemporaryFile(mode="r", suffix=".log", delete=False) as f:
            path = f.name
        notify_filelog.emit(record, log_path=path)
        parsed = json.loads(Path(path).read_text().strip())
        assert "timestamp" in parsed
        assert parsed["timestamp"]

    def test_emit_bad_path_does_not_raise(self):
        record = make_record()
        # Should log error internally but never raise
        notify_filelog.emit(record, log_path="/proc/nonexistent/swiftbox.log")