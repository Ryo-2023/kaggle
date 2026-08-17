"""破壊的操作の前に worktree の状態を記録し、dirty なら止める。

正典 §20 (o6 と不要 artifact の削除) に対応する。

> 実装開始前に current branch、HEAD、`git status --porcelain=v1` を
> `WorktreeProtectionManifest` へ記録する。現在の dirty worktree では checkout、
> merge、reset、untracked cleanup を行わない。

## なぜ「dirty なら止める」だけでは足りないか

正典が求めるのは記録と保護の両方である。止めるだけでは、後から「あの時点で何が
未コミットだったか」を復元できない。untracked file は Git 履歴に無いため、消えると
復元不能である (§20: 「untracked file は復元不能になり得るため、明示 cleanup
manifest に含まれ、参照がないことを確認したものだけ削除する」)。したがって
``WorktreeProtectionManifestV1`` は porcelain の全行を保持する。

## cleanup scope から必ず外すもの

正典 §22 条項19 は「remote branch / Git history を cleanup scope に含めない」と定める。
``assert_path_is_cleanable_v1`` はそれらと、repository root、`runs/` 全体、
広い glob を拒否する。
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Sequence


WORKTREE_GUARD_SCHEMA_V1 = "meta-specialist-worktree-protection-v1"

# 正典 §20: 「広い glob、repository root、`runs/` 全体を destructive command の
# 対象にしない」。remote / Git history は条項19 が明示的に除外を求める。
_FORBIDDEN_CLEANUP_TARGETS_V1 = ("", ".", "/", "*", "**", ".git", "runs")


class WorktreeGuardV1Error(RuntimeError):
    """Raised when a destructive operation would run against protected state."""


@dataclass(frozen=True, slots=True)
class WorktreeProtectionManifestV1:
    """正典 §7 の ``WorktreeProtectionManifest``.

    ``porcelain`` は ``git status --porcelain=v1`` の全行をそのまま保持する。
    件数だけでは、後から「何が未コミットだったか」を復元できない。
    """

    schema_version: str
    worktree_path: str
    branch: str
    head: str
    porcelain: tuple[str, ...]
    recorded_at_utc: str

    @property
    def is_dirty(self) -> bool:
        return bool(self.porcelain)

    @property
    def modified_count(self) -> int:
        return sum(1 for line in self.porcelain if not line.startswith("??"))

    @property
    def untracked_count(self) -> int:
        return sum(1 for line in self.porcelain if line.startswith("??"))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "worktree_path": self.worktree_path,
            "branch": self.branch,
            "head": self.head,
            "porcelain": list(self.porcelain),
            "recorded_at_utc": self.recorded_at_utc,
            "is_dirty": self.is_dirty,
            "modified_count": self.modified_count,
            "untracked_count": self.untracked_count,
        }

    def manifest_id(self) -> str:
        return hashlib.sha256(
            b"mage_ptcg:worktree-protection:v1\0"
            + json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()


# Kept for callers that only need the coarse view; the manifest is the record.
WorktreeStatusV1 = WorktreeProtectionManifestV1


def _git(worktree_dir: str, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=worktree_dir, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise WorktreeGuardV1Error(
            f"git {' '.join(args)} failed in {worktree_dir}: {result.stderr.strip()[:200]}"
        )
    return result.stdout


def inspect_worktree_status_v1(worktree_dir: str = ".") -> WorktreeProtectionManifestV1:
    """Record branch, HEAD, and every porcelain line before touching anything."""
    from datetime import UTC, datetime

    branch = _git(worktree_dir, "rev-parse", "--abbrev-ref", "HEAD").strip()
    head = _git(worktree_dir, "rev-parse", "HEAD").strip()
    porcelain = tuple(
        line for line in _git(worktree_dir, "status", "--porcelain=v1").splitlines() if line
    )
    return WorktreeProtectionManifestV1(
        schema_version=WORKTREE_GUARD_SCHEMA_V1,
        worktree_path=str(Path(worktree_dir).resolve()),
        branch=branch,
        head=head,
        porcelain=porcelain,
        recorded_at_utc=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


def assert_worktree_clean_for_destructive_operation_v1(
    worktree_dir: str = ".",
) -> WorktreeProtectionManifestV1:
    """Refuse checkout / merge / reset / untracked cleanup on a dirty worktree.

    Returns the manifest on success so the caller can persist what it verified.
    """
    manifest = inspect_worktree_status_v1(worktree_dir)
    if manifest.is_dirty:
        raise WorktreeGuardV1Error(
            f"{manifest.worktree_path} has {manifest.modified_count} modified and "
            f"{manifest.untracked_count} untracked entries on branch "
            f"{manifest.branch!r}. 正典 §20 は dirty worktree での checkout / merge / "
            "reset / untracked cleanup を禁じる。untracked file は Git 履歴に無く "
            "復元不能になり得る。"
        )
    return manifest


def assert_path_is_cleanable_v1(target: str | Path, *, repo_root: str | Path) -> Path:
    """Refuse a cleanup target that the canon puts out of scope.

    正典 §22 条項19 は remote branch と Git history を cleanup scope から外すことを、
    §20 は repository root、``runs/`` 全体、広い glob を destructive command の対象に
    しないことを求める。
    """
    root = Path(repo_root).resolve()
    text = str(target)
    if text.strip() in _FORBIDDEN_CLEANUP_TARGETS_V1:
        raise WorktreeGuardV1Error(f"{text!r} is never a permitted cleanup target")
    if any(ch in text for ch in "*?["):
        raise WorktreeGuardV1Error(
            f"{text!r} is a glob; cleanup targets must be explicit paths (正典 §20)"
        )
    resolved = (root / text).resolve() if not Path(text).is_absolute() else Path(text).resolve()
    if resolved == root:
        raise WorktreeGuardV1Error("the repository root is never a cleanup target")
    if not resolved.is_relative_to(root):
        raise WorktreeGuardV1Error(f"{resolved} lies outside {root}")
    relative = resolved.relative_to(root)
    if relative.parts and relative.parts[0] in {".git", "runs"} and len(relative.parts) == 1:
        raise WorktreeGuardV1Error(
            f"{relative} is out of cleanup scope (Git history / runs/ as a whole)"
        )
    return resolved


def persist_worktree_protection_manifest_v1(
    manifest: WorktreeProtectionManifestV1, output_path: str | Path
) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {**manifest.to_dict(), "manifest_id": manifest.manifest_id()}
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


__all__ = [
    "WORKTREE_GUARD_SCHEMA_V1",
    "WorktreeGuardV1Error",
    "WorktreeProtectionManifestV1",
    "WorktreeStatusV1",
    "assert_path_is_cleanable_v1",
    "assert_worktree_clean_for_destructive_operation_v1",
    "inspect_worktree_status_v1",
    "persist_worktree_protection_manifest_v1",
]
