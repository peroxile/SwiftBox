"""
Sync a set of scripts from a trusted remote repo into the local scripts/
directory. Uses adapters/repo/fetch.py for individual file downloads.

Sync is always conservative:
- existing local files are not deleted unless explicitly listed for removal
- checksums are verified before any file is written
- dry-run is the default
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from adapters.repo.fetch import fetch_script, checksum_of

logger = logging.getLogger(__name__)


# Sync manifest entry

@dataclass
class SyncEntry:
    """One file to sync from remote to local."""
    remote_path: str          # path in the remote repo, e.g. scripts/fix/clean_tmp.sh
    local_path: str           # destination path in this repo
    expected_checksum: Optional[str] = None
    required: bool = True     # if True, sync failure raises; otherwise warns


@dataclass
class SyncResult:
    entry: SyncEntry
    status: str               # synced | skipped | failed
    local_path: Optional[Path] = None
    error: Optional[str] = None


@dataclass
class SyncReport:
    synced: list[SyncResult] = field(default_factory=list)
    skipped: list[SyncResult] = field(default_factory=list)
    failed: list[SyncResult] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return len(self.failed) == 0

    def summary(self) -> str:
        return (
            f"sync complete: {len(self.synced)} synced, "
            f"{len(self.skipped)} skipped, "
            f"{len(self.failed)} failed"
        )


# Core sync function

def sync(
    entries: list[SyncEntry],
    org: str,
    repo: str,
    branch: str = "main",
    cache_dir: str | Path = "state/repo_cache",
    verify_checksums: bool = True,
    dry_run: bool = True,
    token: Optional[str] = None,
) -> SyncReport:
    """
    Sync a list of files from a remote GitHub repo to local paths.

    dry_run=True (default): resolve what would change, log it, but write nothing.
    verify_checksums=True: reject downloads that don't match expected_checksum.
    """
    report = SyncReport()

    for entry in entries:
        local = Path(entry.local_path)

        # Skip if already present and checksum matches
        if local.exists() and entry.expected_checksum:
            actual = checksum_of(local)
            if actual == entry.expected_checksum:
                logger.debug("Up to date: %s", local)
                report.skipped.append(SyncResult(entry=entry, status="skipped", local_path=local))
                continue

        if dry_run:
            logger.info("[dry-run] Would sync %s -> %s", entry.remote_path, local)
            report.skipped.append(SyncResult(entry=entry, status="skipped", local_path=local))
            continue

        try:
            local.parent.mkdir(parents=True, exist_ok=True)
            fetched = fetch_script(
                filepath=entry.remote_path,
                org=org,
                repo=repo,
                branch=branch,
                cache_dir=cache_dir,
                expected_checksum=entry.expected_checksum if verify_checksums else None,
                verify_checksums=verify_checksums,
                token=token,
            )
            # Move from cache to final local path
            fetched.replace(local)
            if entry.local_path.endswith(".sh"):
                local.chmod(local.stat().st_mode | 0o111)

            logger.info("Synced: %s -> %s", entry.remote_path, local)
            report.synced.append(SyncResult(entry=entry, status="synced", local_path=local))

        except Exception as e:  # noqa: BLE001
            msg = f"Failed to sync {entry.remote_path}: {e}"
            logger.error(msg)
            result = SyncResult(entry=entry, status="failed", error=str(e))
            report.failed.append(result)
            if entry.required:
                raise RuntimeError(msg) from e

    logger.info(report.summary())
    return report


# Convenience: build entries from a manifest dict (loaded from YAML)


def entries_from_manifest(manifest: dict) -> list[SyncEntry]:
    """
    Parse a sync manifest dict into SyncEntry objects.

    Expected shape:
      files:
        - remote: scripts/fix/clean_tmp.sh
          local: scripts/fix/clean_tmp.sh
          checksum: abc123...
          required: true
    """
    result = []
    for item in manifest.get("files", []):
        result.append(SyncEntry(
            remote_path=item["remote"],
            local_path=item["local"],
            expected_checksum=item.get("checksum"),
            required=item.get("required", True),
        ))
    return result