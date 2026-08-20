"""Checks for the fixed upstream source and local checkpoint artifact."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

OFFICIAL_COMMIT = "075fc5f5a52d11077f9dc2b074644618f26939e2"
LOCAL_CHECKPOINT_SHA256 = "347915de9c33883cb2ee69832a8e4552c88b1ec692d0fbfe956422467d3d4235"

_HASH_CHUNK_SIZE = 1024 * 1024


def verify_source(root: Path, expected_commit: str) -> None:
    """Require a clean Git checkout at *expected_commit*.

    Ignored files are intentionally omitted from the status check.  The fixed
    upstream checkout writes ignored prediction artifacts while it runs; those
    generated files are not source provenance.  Staged and unstaged changes to
    tracked files, including index changes, and untracked non-ignored files are
    hard failures.
    """

    root = Path(root)
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or "not a Git checkout"
        raise ValueError(f"Unable to verify upstream commit at {root}: {detail}")

    actual_commit = result.stdout.strip()
    if actual_commit != expected_commit:
        raise ValueError(
            f"upstream commit mismatch at {root}: expected {expected_commit}, got {actual_commit}",
        )

    status = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain=v1", "--untracked-files=all", "--ignored=no"],
        check=False,
        capture_output=True,
        text=True,
    )
    if status.returncode != 0:
        detail = status.stderr.strip() or "unable to inspect tracked source status"
        raise ValueError(f"Unable to verify tracked source status at {root}: {detail}")
    if status.stdout.strip():
        changes = status.stdout.strip().replace("\n", "; ")
        raise ValueError(
            f"tracked source/index modifications at {root}: {changes}",
        )


def verify_sha256(path: Path, expected: str) -> str:
    """Hash *path* in bounded chunks and require the expected SHA-256 digest."""

    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(_HASH_CHUNK_SIZE), b""):
            digest.update(chunk)

    actual = digest.hexdigest()
    if actual != expected:
        raise ValueError(f"SHA-256 mismatch for {path}: expected {expected}, got {actual}")
    return actual
