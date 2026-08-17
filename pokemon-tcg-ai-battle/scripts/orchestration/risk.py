"""Deterministic patch metadata inspection and auto-integration risk gate."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .policy import is_control_plane_path, path_matches


@dataclass(frozen=True)
class PatchMetadata:
    added_lines: int
    deleted_lines: int
    diff_lines: int
    binary: bool
    symlink: bool
    submodule: bool
    large_deletion: bool


@dataclass(frozen=True)
class RiskAssessment:
    level: str
    findings: tuple[str, ...]
    metadata: PatchMetadata


def _git(worktree: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", *args], cwd=worktree, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    if result.returncode:
        raise RuntimeError("cannot inspect Git patch metadata")
    return result.stdout


def inspect_patch_metadata(worktree: Path) -> PatchMetadata:
    added = 0
    deleted = 0
    binary = False
    for line in _git(worktree, "diff", "--numstat").decode(
        "utf-8", "surrogateescape"
    ).splitlines():
        columns = line.split("\t", 2)
        if len(columns) < 3:
            continue
        if columns[0] == "-" or columns[1] == "-":
            binary = True
            continue
        added += int(columns[0])
        deleted += int(columns[1])
    summary = _git(worktree, "diff", "--summary").decode("utf-8", "replace")
    symlink = "mode 120000" in summary
    submodule = "mode 160000" in summary
    return PatchMetadata(
        added_lines=added,
        deleted_lines=deleted,
        diff_lines=added + deleted,
        binary=binary,
        symlink=symlink,
        submodule=submodule,
        large_deletion=deleted >= 500,
    )


def classify_risk(
    paths: Iterable[str],
    *,
    allowed_paths: Iterable[str],
    forbidden_paths: Iterable[str],
    protected_paths: Iterable[str],
    metadata: PatchMetadata,
) -> RiskAssessment:
    """Classify actual changed paths using the repository's glob-aware matcher."""

    findings: list[str] = []
    for path in paths:
        if not path_matches(path, allowed_paths):
            findings.append("outside-allowed-path")
        if path_matches(path, forbidden_paths):
            findings.append("forbidden-path")
        if path_matches(path, protected_paths):
            findings.append("protected-path")
        if is_control_plane_path(path):
            findings.append("control-plane-path")
            findings.append("sensitive-path")
    if metadata.binary:
        findings.append("binary")
    if metadata.symlink:
        findings.append("symlink")
    if metadata.submodule:
        findings.append("submodule")
    if metadata.large_deletion:
        findings.append("large-deletion")
    unique = tuple(dict.fromkeys(findings))
    return RiskAssessment("HIGH" if unique else "LOW", unique, metadata)
