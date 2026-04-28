"""
Disk usage checks and cleanup actions for Linux hosts.
All functions return a normalized dict: {value, ok, message, context}.
"""

from __future__ import annotations

import shutil
import os
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def check_usage(path: str = "/") -> dict:
    """Return disk usage percent for the given mount path."""
    try:
        usage = shutil.disk_usage(path)
        percent = round((usage.used / usage.total) * 100, 2)
        return {
            "value": percent,
            "ok": True,
            "message": f"Disk usage at {path}: {percent}%",
            "context": {
                "path": path,
                "total_gb": round(usage.total / 1e9, 2),
                "used_gb": round(usage.used / 1e9, 2),
                "free_gb": round(usage.free / 1e9, 2),
            },
        }
    except FileNotFoundError:
        return {
            "value": None,
            "ok": False,
            "message": f"Path not found: {path}",
            "context": {"path": path},
        }
    except PermissionError:
        return {
            "value": None,
            "ok": False,
            "message": f"Permission denied reading disk usage at {path}",
            "context": {"path": path},
        }


def clean_tmp(paths: list[str] | None = None, older_than_days: int = 7, dry_run: bool = True) -> dict:
    """
    Remove files older than `older_than_days` from given paths.
    Dry-run by default — passes dry_run=False to actually delete.
    """
    import time

    paths = paths or ["/tmp", "/var/tmp"]
    cutoff = time.time() - (older_than_days * 86400)
    removed = []
    errors = []

    for base in paths:
        base_path = Path(base)
        if not base_path.exists():
            continue
        for item in base_path.iterdir():
            try:
                stat = item.stat()
                if stat.st_mtime < cutoff:
                    if dry_run:
                        removed.append(str(item))
                    else:
                        if item.is_file() or item.is_symlink():
                            item.unlink()
                        elif item.is_dir():
                            import shutil as _shutil
                            _shutil.rmtree(item)
                        removed.append(str(item))
            except Exception as e:  # noqa: BLE001
                errors.append(f"{item}: {e}")

    return {
        "value": len(removed),
        "ok": len(errors) == 0,
        "message": f"{'[dry-run] Would remove' if dry_run else 'Removed'} {len(removed)} items",
        "context": {"removed": removed, "errors": errors, "dry_run": dry_run},
    }
