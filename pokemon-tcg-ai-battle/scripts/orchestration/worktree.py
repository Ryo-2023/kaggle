"""Worker diff capture and clean verification worktree helpers."""

from __future__ import annotations

import shutil
from pathlib import Path

from .snapshot import SnapshotError, git


def prepare_snapshot_baseline(worktree: Path) -> None:
    """Stage the materialized snapshot so later diffs contain worker changes only."""

    git(worktree, "add", "-A")


def changed_paths(worktree: Path) -> tuple[str, ...]:
    """Return all worker changes relative to the staged snapshot baseline."""

    untracked = [
        item.decode("utf-8", errors="surrogateescape")
        for item in git(worktree, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0")
        if item
    ]
    if untracked:
        git(worktree, "add", "-N", "--", *untracked)
    paths = [
        item.decode("utf-8", errors="surrogateescape")
        for item in git(
            worktree,
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--name-only",
            "-z",
        ).split(b"\0")
        if item
    ]
    return tuple(paths)


def capture_patch(worktree: Path) -> bytes:
    """Capture the authoritative binary patch from actual worktree state."""

    return git(
        worktree,
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        "--binary",
        "--full-index",
    )


def apply_patch(worktree: Path, patch: bytes, check_only: bool = False) -> None:
    """Apply or preflight an authoritative patch in *worktree*."""

    if not patch:
        raise SnapshotError("captured patch is empty")
    arguments = ["apply", "--binary", "--whitespace=nowarn"]
    if check_only:
        arguments.append("--check")
    arguments.append("-")
    git(worktree, *arguments, input_bytes=patch)


def remove_worktree(repository_root: Path, worktree: Path) -> None:
    """Remove a registered temporary worktree and prune stale Git metadata."""

    try:
        git(repository_root, "worktree", "remove", "--force", str(worktree))
    except SnapshotError:
        if worktree.exists():
            shutil.rmtree(worktree)
        git(repository_root, "worktree", "prune")
    try:
        worktree.parent.rmdir()
    except OSError:
        pass


def cleanup_run_worktrees(repository_root: Path, run_worktrees_root: Path) -> None:
    """Remove registered and orphaned worktrees belonging to one run."""

    parent = run_worktrees_root.parent.resolve()
    target = run_worktrees_root.resolve(strict=False)
    if not target.is_relative_to(parent) or target == parent:
        raise SnapshotError(f"unsafe worktree cleanup target: {run_worktrees_root}")
    listing = git(repository_root, "worktree", "list", "--porcelain").decode(
        errors="replace"
    )
    registered = [
        Path(line.removeprefix("worktree "))
        for line in listing.splitlines()
        if line.startswith("worktree ")
    ]
    for worktree in registered:
        if worktree.resolve(strict=False).is_relative_to(target):
            remove_worktree(repository_root, worktree)
    if run_worktrees_root.exists():
        shutil.rmtree(run_worktrees_root)
    git(repository_root, "worktree", "prune")
