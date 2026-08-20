import subprocess
from pathlib import Path

import pytest

from biohub.strong_baseline.provenance import (
    OFFICIAL_COMMIT,
    verify_sha256,
    verify_source,
)


def test_verify_sha256_rejects_mismatch(tmp_path: Path) -> None:
    checkpoint = tmp_path / "model.pth"
    checkpoint.write_bytes(b"weights")

    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        verify_sha256(checkpoint, "0" * 64)


def test_verify_source_requires_exact_commit(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="upstream commit"):
        verify_source(tmp_path, OFFICIAL_COMMIT)


def _git_checkout(tmp_path: Path) -> Path:
    root = tmp_path / "upstream"
    root.mkdir()
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
    (root / "source.py").write_text("version = 1\n")
    subprocess.run(["git", "-C", str(root), "add", "source.py"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "fixture"], check=True)
    return root


def test_verify_source_rejects_tracked_worktree_changes(tmp_path: Path) -> None:
    root = _git_checkout(tmp_path)
    commit = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    (root / "source.py").write_text("version = 2\n")

    with pytest.raises(ValueError, match="tracked source/index modifications"):
        verify_source(root, commit)


def test_verify_source_allows_ignored_generated_files(tmp_path: Path) -> None:
    root = _git_checkout(tmp_path)
    (root / ".gitignore").write_text("predictions/\n")
    subprocess.run(["git", "-C", str(root), "add", ".gitignore"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "ignore generated"], check=True)
    (root / "predictions").mkdir()
    (root / "predictions" / "generated.geff").write_text("ignored output")

    verify_source(root, subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip())


def test_verify_source_rejects_nonignored_untracked_source(tmp_path: Path) -> None:
    root = _git_checkout(tmp_path)
    commit = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    (root / "shadow.py").write_text("version = 99\n")

    with pytest.raises(ValueError, match="tracked source/index modifications"):
        verify_source(root, commit)
