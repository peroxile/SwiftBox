"""
Verify fetch and sync adapter behavior without hitting the network.
All HTTP calls are mocked.
"""

from __future__ import annotations

import hashlib
import pytest
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch
from adapters.repo.fetch import _sha256, _verify_checksum, fetch_script, checksum_of
from adapters.repo.sync import SyncEntry, sync, entries_from_manifest

sys.path.insert(0, str(Path(__file__).parent.parent))


# fetch helpers

class TestChecksum:
    def test_sha256_matches_hashlib(self):
        data = b"hello swiftbox"
        expected = hashlib.sha256(data).hexdigest()
        assert _sha256(data) == expected

    def test_verify_checksum_pass(self):
        data = b"content"
        checksum = _sha256(data)
        assert _verify_checksum(data, checksum) is True

    def test_verify_checksum_fail(self):
        assert _verify_checksum(b"content", "wrongchecksum") is False


class TestFetchScript:
    def _mock_urlopen(self, content: bytes):
        mock_resp = MagicMock()
        mock_resp.read.return_value = content
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    def test_fetch_writes_to_cache(self):
        content = b"#!/bin/bash\necho hello"
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("adapters.repo.fetch.urlopen", return_value=self._mock_urlopen(content)):
                path = fetch_script(
                    filepath="scripts/fix/clean_tmp.sh",
                    org="myorg",
                    repo="myrepo",
                    branch="main",
                    cache_dir=tmpdir,
                    verify_checksums=False,
                )
            assert path.exists()
            assert path.read_bytes() == content

    def test_fetch_makes_sh_executable(self):
        content = b"#!/bin/bash\necho hello"
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("adapters.repo.fetch.urlopen", return_value=self._mock_urlopen(content)):
                path = fetch_script(
                    filepath="scripts/fix/clean_tmp.sh",
                    org="myorg",
                    repo="myrepo",
                    cache_dir=tmpdir,
                    verify_checksums=False,
                )
            assert path.stat().st_mode & 0o111  # executable bit set

    def test_fetch_checksum_mismatch_raises(self):
        content = b"real content"
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("adapters.repo.fetch.urlopen", return_value=self._mock_urlopen(content)):
                with pytest.raises(RuntimeError, match="Checksum verification failed"):
                    fetch_script(
                        filepath="scripts/fix/clean_tmp.sh",
                        org="myorg",
                        repo="myrepo",
                        cache_dir=tmpdir,
                        expected_checksum="wrongchecksum",
                        verify_checksums=True,
                    )

    def test_fetch_valid_checksum_succeeds(self):
        content = b"real content"
        checksum = _sha256(content)
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("adapters.repo.fetch.urlopen", return_value=self._mock_urlopen(content)):
                path = fetch_script(
                    filepath="scripts/fix/clean_tmp.sh",
                    org="myorg",
                    repo="myrepo",
                    cache_dir=tmpdir,
                    expected_checksum=checksum,
                    verify_checksums=True,
                )
            assert path.exists()

    def test_checksum_of_local_file(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"swiftbox")
            f.flush()
            expected = hashlib.sha256(b"swiftbox").hexdigest()
            assert checksum_of(f.name) == expected


# sync

class TestSync:
    def _mock_urlopen(self, content: bytes):
        mock_resp = MagicMock()
        mock_resp.read.return_value = content
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    def test_dry_run_writes_nothing(self):
        entries = [SyncEntry(remote_path="scripts/fix/clean_tmp.sh", local_path="/tmp/wont_exist.sh")]
        report = sync(entries, org="myorg", repo="myrepo", dry_run=True)
        assert not Path("/tmp/wont_exist.sh").exists()
        assert report.success

    def test_skips_up_to_date_files(self):
        content = b"#!/bin/bash"
        checksum = _sha256(content)
        with tempfile.TemporaryDirectory() as tmpdir:
            local = Path(tmpdir) / "clean_tmp.sh"
            local.write_bytes(content)
            entries = [SyncEntry(
                remote_path="scripts/fix/clean_tmp.sh",
                local_path=str(local),
                expected_checksum=checksum,
            )]
            report = sync(entries, org="myorg", repo="myrepo", dry_run=False, verify_checksums=True)
            assert len(report.skipped) == 1
            assert len(report.synced) == 0

    def test_sync_writes_new_file(self):
        content = b"#!/bin/bash\necho synced"
        with tempfile.TemporaryDirectory() as tmpdir:
            local = Path(tmpdir) / "new_script.sh"
            cache = Path(tmpdir) / "cache"
            entries = [SyncEntry(
                remote_path="scripts/fix/clean_tmp.sh",
                local_path=str(local),
            )]
            with patch("adapters.repo.fetch.urlopen", return_value=self._mock_urlopen(content)):
                report = sync(
                    entries, org="myorg", repo="myrepo",
                    cache_dir=cache, dry_run=False, verify_checksums=False,
                )
            assert report.success
            assert len(report.synced) == 1
            assert local.exists()

    def test_failed_required_entry_raises(self):
        entries = [SyncEntry(
            remote_path="scripts/fix/missing.sh",
            local_path="/tmp/missing.sh",
            required=True,
        )]
        with patch("adapters.repo.fetch.urlopen", side_effect=Exception("network error")):
            with pytest.raises(RuntimeError):
                sync(entries, org="myorg", repo="myrepo", dry_run=False)

    def test_entries_from_manifest(self):
        manifest = {
            "files": [
                {"remote": "scripts/fix/clean_tmp.sh", "local": "scripts/fix/clean_tmp.sh", "checksum": "abc123"},
                {"remote": "scripts/fix/kill_zombies.sh", "local": "scripts/fix/kill_zombies.sh", "required": False},
            ]
        }
        entries = entries_from_manifest(manifest)
        assert len(entries) == 2
        assert entries[0].expected_checksum == "abc123"
        assert entries[1].required is False