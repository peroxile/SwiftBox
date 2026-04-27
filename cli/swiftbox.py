#!/usr/bin/env python3
"""
SwiftBox CLI entrypoint

Usage:
  python cli/swiftbox.py run    [--host=vps] [--role=operator] [--json]
  python cli/swiftbox.py check  [--host=vps] [--json]
  python cli/swiftbox.py exec   --workflow=<id> [--host=vps] [--role=operator] [--json]
  python cli/swiftbox.py status

`run`   — full detect -> plan -> act -> verify loop
`check` — health checks only, no actions taken
`exec`  — dispatch a single named workflow directly (used by products)
`status`— print last recorded state
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

# Resolve project root regardless of where this script is called from
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        level=logging.DEBUG if verbose else logging.WARNING,
        stream=sys.stderr,
    )


def _serialize(obj) -> object:
    if hasattr(obj, "__dataclass_fields__"):
        return {k: _serialize(getattr(obj, k)) for k in obj.__dataclass_fields__}
    if isinstance(obj, list):
        return [_serialize(i) for i in obj]
    if hasattr(obj, "value"):
        return obj.value
    return obj


def cmd_run(args: argparse.Namespace) -> int:
    from core.engine import run

    records = run(
        host_config_path=ROOT / f"config/hosts/{args.host}.yml",
        healthcheck_config_path=ROOT / "config/healthchecks/server.yml",
        workflows_path=ROOT / "config/workflows.yaml",
        permissions_path=ROOT / "config/permissions.yml",
        state_dir=ROOT / "state",
        role=args.role,
    )

    if args.json:
        print(json.dumps([_serialize(r) for r in records], indent=2))
    else:
        if not records:
            print("[swiftbox] all checks passed — no actions taken")
        for r in records:
            action_status = r.action.status.value if r.action else "none"
            verified = r.verification.passed if r.verification else "n/a"
            issue_id = r.issue.check_id if r.issue else "?"
            issue_status = r.issue.status.value if r.issue else "?"
            print(
                f"  workflow={r.workflow_id}"
                f"  issue={issue_id}({issue_status})"
                f"  action={action_status}"
                f"  verified={verified}"
            )
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    from core.detect import run_checks_from_config

    reports = run_checks_from_config(
        config_path=ROOT / "config/healthchecks/server.yml",
        host=args.host,
    )

    if args.json:
        print(json.dumps([_serialize(r) for r in reports], indent=2))
    else:
        for r in reports:
            flag = "✓" if r.status.value == "ok" else "✗"
            print(f"  {flag} {r.check_id:<30} {r.status.value:<10} {r.message}")

    return 1 if any(r.status.value != "ok" for r in reports) else 0


def cmd_exec(args: argparse.Namespace) -> int:
    from core.engine import run_workflow

    try:
        record = run_workflow(
            workflow_id=args.workflow,
            host=args.host,
            role=args.role,
            host_config_path=ROOT / f"config/hosts/{args.host}.yml",
            workflows_path=ROOT / "config/workflows.yaml",
            permissions_path=ROOT / "config/permissions.yml",
            healthcheck_config_path=ROOT / "config/healthchecks/server.yml",
            state_dir=ROOT / "state",
        )
    except (ValueError, PermissionError) as e:
        print(f"[swiftbox] error: {e}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(_serialize(record), indent=2))
    else:
        action_status = record.action.status.value if record.action else "none"
        verified = record.verification.passed if record.verification else "n/a"
        print(f"  workflow={record.workflow_id} action={action_status} verified={verified}")

    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    path = ROOT / "state/last_actions.json"
    if not path.exists():
        print("[swiftbox] no state recorded yet")
        return 0
    print(path.read_text())
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="swiftbox")
    parser.add_argument("--verbose", "-v", action="store_true")
    sub = parser.add_subparsers(dest="command")

    run_p = sub.add_parser("run")
    run_p.add_argument("--host", default="vps")
    run_p.add_argument("--role", default="operator")
    run_p.add_argument("--json", action="store_true")

    check_p = sub.add_parser("check")
    check_p.add_argument("--host", default="vps")
    check_p.add_argument("--json", action="store_true")

    exec_p = sub.add_parser("exec")
    exec_p.add_argument("--workflow", required=True)
    exec_p.add_argument("--host", default="vps")
    exec_p.add_argument("--role", default="operator")
    exec_p.add_argument("--json", action="store_true")

    sub.add_parser("status")

    args = parser.parse_args()
    _setup_logging(args.verbose)

    if args.command == "run":
        return cmd_run(args)
    if args.command == "check":
        return cmd_check(args)
    if args.command == "exec":
        return cmd_exec(args)
    if args.command == "status":
        return cmd_status(args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())