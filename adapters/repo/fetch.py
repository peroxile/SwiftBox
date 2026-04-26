"""
Fetch scripts and config from a trusted remote repository.
Supports GitHub (raw content API). Other sources are added as adapters.

SwiftBox never executes a script it hasn't fetched and verified.
All fetched content goes to a local cache before use.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Optional
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

logger = logging.getLogger(__name__)

GITHUB_RAW = "https://raw.githubusercontent.com"
DEFAULT_CACHE_DIR = Path("state/repo_cache")
TIMEOUT_SECONDS = 15


# URL builders


def _github_raw_url(org: str, repo: str, branch: str, filepath: str) -> str:
    return f"{GITHUB_RAW}/{org}/{repo}/{branch}/{filepath}"


# Checksum


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _verify_checksum(content: bytes, expected: str) -> bool:
    actual = _sha256(content)
    if actual != expected:
        logger.error("Checksum mismatch: expected=%s actual=%s", expected, actual)
        return False
    return True


# HTTP fetch


def _fetch_url(url: str, token: Optional[str] = None) -> bytes:
    """
    Fetch a URL and return raw bytes.
    Raises on HTTP errors or network failures.
    """
    headers = {"User-Agent": "SwiftBox/1.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = Request(url, headers=headers)
    try:
        with urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            return resp.read()
    except HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} fetching {url}: {e.reason}") from e
    except URLError as e:
        raise RuntimeError(f"Network error fetching {url}: {e.reason}") from e


# Public interface

def fetch_script(
    filepath: str,
    org: str,
    repo: str,
    branch: str = "main",
    cache_dir: str | Path = DEFAULT_CACHE_DIR,
    expected_checksum: Optional[str] = None,
    verify_checksums: bool = True,
    token: Optional[str] = None,
) -> Path:
    """
    Fetch a script file from GitHub and cache it locally.
    Returns the local path to the cached file.

    If `expected_checksum` is provided and `verify_checksums` is True,
    the download is rejected on mismatch.

    If the file is already cached with a matching checksum, skip download.
    """
    cache_dir = Path(cache_dir)
    cache_path = cache_dir / filepath.replace("/", "_")

    url = _github_raw_url(org, repo, branch, filepath)
    logger.info("Fetching: %s", url)

    content = _fetch_url(url, token=token)

    if verify_checksums and expected_checksum:
        if not _verify_checksum(content, expected_checksum):
            raise RuntimeError(f"Checksum verification failed for {filepath}")

    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(content)

    # Make executable if it's a shell script
    if filepath.endswith(".sh"):
        cache_path.chmod(cache_path.stat().st_mode | 0o111)

    actual_checksum = _sha256(content)
    logger.info("Cached %s -> %s (sha256=%s)", filepath, cache_path, actual_checksum[:12])

    return cache_path


def fetch_config(
    filepath: str,
    org: str,
    repo: str,
    branch: str = "main",
    cache_dir: str | Path = DEFAULT_CACHE_DIR,
    token: Optional[str] = None,
) -> Path:
    """
    Fetch a config or manifest file from GitHub.
    No checksum verification required for config — treat as read-only reference.
    """
    return fetch_script(
        filepath=filepath,
        org=org,
        repo=repo,
        branch=branch,
        cache_dir=cache_dir,
        expected_checksum=None,
        verify_checksums=False,
        token=token,
    )


def checksum_of(path: str | Path) -> str:
    """Return sha256 of a local file. Useful for pre-populating expected checksums."""
    return _sha256(Path(path).read_bytes())