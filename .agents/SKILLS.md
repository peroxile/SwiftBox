# SwiftBox Agent Instructions

## What SwiftBox Is

SwiftBox is a cross-platform automation runtime and workflow engine. It is a dependency, not a product. Other products depend on it for safe system automation.

It is not the UI. It is not the business logic of any single app. It is the shared execution layer underneath multiple products.

## What SwiftBox Does

SwiftBox turns a situation into a safe action loop:

```
input → normalize → decide → act → state → verify
```

It reads config and state, matches situations to workflows, checks permissions, executes through adapters, verifies results, and writes logs.

## Repository Map

| Path | Purpose |
|------|---------|
| `config/hosts/` | Per-host identity and execution settings |
| `config/healthchecks/` | What to check on a given system |
| `config/workflows.yaml` | What to do when checks fail |
| `config/permissions.yml` | What actions are allowed and by whom |
| `config/default.yml` | Global fallback values |
| `core/schemas.py` | All internal data structures |
| `core/detect.py` | Run checks, produce IssueReports |
| `core/plan.py` | Map issues to ActionRequests |
| `core/verify.py` | Confirm action outcomes |
| `core/engine.py` | Coordinate the full loop |
| `adapters/linux/` | Disk, network, process, service checks |
| `adapters/notify/` | stdout and filelog output |
| `adapters/repo/` | Fetch and sync scripts from trusted remote |
| `scripts/bootstrap/` | Initial environment setup |
| `scripts/diagnose/` | Read-only inspection scripts |
| `scripts/fix/` | Repair actions |
| `state/` | Persistent memory between runs |
| `logs/` | Append-only operational logs |
| `cli/swiftbox.py` | CLI entrypoint |
| `tests/` | Behavior contracts |

## What an Agent May Do

- Read any file in this repo.
- Add config entries to `config/` files.
- Add new workflows to `config/workflows.yaml`.
- Add new scripts to `scripts/`.
- Add new adapters to `adapters/`.
- Add tests to `tests/`.
- Modify `core/` files when a clear functional gap exists.
- Update `config/permissions.yml` to add new roles or allowed actions.

## What an Agent Must Not Do

- Add business logic, UI logic, or product-specific behavior to `core/`.
- Hardcode values that belong in `config/`.
- Run destructive actions without a dry-run check.
- Delete or truncate `state/history.jsonl` or any log file.
- Add actions to `permanently_blocked` bypass paths in code.
- Modify more than one layer at a time without verifying tests still pass.
- Make silent changes to `config/permissions.yml` that expand allowed actions.

## How to Choose the Next Action

1. Read the current structure — do not assume file contents.
2. Identify the smallest change that solves the problem.
3. Check if the change belongs in `config/`, `adapters/`, `scripts/`, or `core/`. In that order of preference.
4. If modifying `core/`, locate all callers and dependencies first.
5. Write or update tests before or alongside the change.
6. Verify the full test suite passes: `pytest tests/ -v`.

## How to Verify Success

- `pytest tests/ -v` must pass with no failures.
- Dry-run the affected workflow and confirm the log output is correct.
- Check `state/last_actions.json` after a run to confirm the record is written.

## Key Design Constraint

**Config decides behavior. Code executes behavior.**

If you are adding a rule, a threshold, a limit, or a policy — it goes in `config/`, not in `core/`.

If you are adding platform-specific behavior — it goes in `adapters/`, not in `core/`.

If you are adding a system action — it goes in `scripts/`, exposed via a workflow in `config/workflows.yaml`.

`core/` should remain small, deterministic, and testable.