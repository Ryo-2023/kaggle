#!/usr/bin/env python3
"""Protected-file baseline/verify checker for the O1 Competition Intelligence work.

Hashes a fixed list of paths the O1 mandate marks as protected (submission
entrypoint, deck, Champion/default agent code, Promotion Gate math, Kaggle
packaging/verification scripts, and the production/acceptance configs that
parameterize the currently-tracked long-run Promotion evidence) using git's
own blob SHA (``git hash-object``) — the same hash git already uses to detect
any content change to a tracked file, so no separate hashing scheme is
needed.

Some protected *artifacts* named in the O1 mandate (the fixed Long-run model
file and its run-scoped package under ``runs/``) are git-ignored data that
lives only in the canonical workspace, not necessarily in an isolated
worktree/clone; for those this checker records
``status: "absent_in_this_worktree"`` rather than fabricating a hash, since
there is nothing on disk here that could be modified.

Usage::

    python scripts/check_o1_protected_files.py baseline --output <path>
    python scripts/check_o1_protected_files.py verify --baseline <path>
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

# Git-tracked files whose content must not change as a side effect of this work.
PROTECTED_PATHS: tuple[str, ...] = (
    "main.py",
    "deck.csv",
    "agents/__init__.py",
    "agents/rule_agent.py",
    "agents/rule_agent_v1.py",
    "src/mage_ptcg/evaluation/promotion.py",
    "src/mage_ptcg/offline_training_v1_support/promotion.py",
    "src/mage_ptcg/offline_training_v1_support/statistics.py",
    "scripts/build_submission.py",
    "scripts/build_student_submission.py",
    "scripts/build_kaggle_submission.py",
    "scripts/kaggle_student_runtime.py",
    "scripts/kaggle_student_entrypoint.py",
    "scripts/verify_kaggle_submission.py",
    "configs/offline_training_v1/production.json",
    "configs/offline_training_v1/final_acceptance_plan.json",
)

# Git-ignored data roots that may hold protected artifacts (Long-run model
# file, its run-scoped package, Promotion Gate result directories); these are
# only presence-checked, never hashed, since their content is out-of-repo data.
PROTECTED_DATA_ROOTS: tuple[str, ...] = ("runs/", "submissions/", "data/")

BASELINE_SCHEMA_VERSION = "o1-protected-files-baseline-v1"


class ProtectedFilesError(RuntimeError):
    """Raised when the repository root or a baseline file cannot be read."""


def repo_root() -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=10, check=False
    )
    if result.returncode != 0:
        raise ProtectedFilesError(f"not a git repository: {result.stderr.strip()}")
    return Path(result.stdout.strip())


def git_blob_hash(root: Path, relative_path: str) -> str | None:
    """Git's own blob SHA for a tracked file at HEAD-or-worktree, or ``None``."""
    result = subprocess.run(
        ["git", "-C", str(root), "hash-object", "--", relative_path],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def collect(root: Path) -> dict[str, dict[str, Any]]:
    entries: dict[str, dict[str, Any]] = {}
    for relative_path in PROTECTED_PATHS:
        absolute = root / relative_path
        if not absolute.exists():
            entries[relative_path] = {"status": "absent_in_this_worktree", "git_blob_sha": None}
            continue
        entries[relative_path] = {"status": "present", "git_blob_sha": git_blob_hash(root, relative_path)}
    for data_root in PROTECTED_DATA_ROOTS:
        absolute = root / data_root
        key = f"{data_root} (git-ignored data root, presence-only)"
        entries[key] = {
            "status": "present_not_hashed" if absolute.exists() else "absent_in_this_worktree",
            "git_blob_sha": None,
        }
    return entries


def cmd_baseline(args: argparse.Namespace) -> int:
    root = repo_root()
    entries = collect(root)
    payload = {"schema_version": BASELINE_SCHEMA_VERSION, "repo_root": str(root), "entries": entries}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"baseline_written": str(output), "entry_count": len(entries)}, sort_keys=True))
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    root = repo_root()
    baseline_path = Path(args.baseline)
    try:
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ProtectedFilesError(f"cannot read baseline {baseline_path}: {exc}") from exc
    if baseline.get("schema_version") != BASELINE_SCHEMA_VERSION:
        raise ProtectedFilesError(f"baseline schema_version mismatch: {baseline.get('schema_version')!r}")
    current = collect(root)
    diffs: list[dict[str, Any]] = []
    for relative_path, before in baseline["entries"].items():
        after = current.get(relative_path)
        if after is None or before != after:
            diffs.append({"path": relative_path, "before": before, "after": after})
    result = {"protected_files_unchanged": len(diffs) == 0, "diff_count": len(diffs), "diffs": diffs}
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0 if not diffs else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    baseline_p = sub.add_parser("baseline", help="record the current hashes as a baseline JSON file")
    baseline_p.add_argument("--output", required=True)
    baseline_p.set_defaults(func=cmd_baseline)
    verify_p = sub.add_parser("verify", help="compare current hashes against a previously recorded baseline")
    verify_p.add_argument("--baseline", required=True)
    verify_p.set_defaults(func=cmd_verify)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except ProtectedFilesError as exc:
        print(json.dumps({"error": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
