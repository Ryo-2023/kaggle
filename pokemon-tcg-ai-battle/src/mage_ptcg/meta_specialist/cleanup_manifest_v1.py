"""削除は明示 manifest に列挙されたものだけを許す。

正典 §20 (o6 と不要 artifact の削除) に対応する。

> `runs/`、Replay、checkpoint、cache は一括削除しない。cleanup planner が path、
> size、content hash、参照元、再生成方法、保持理由、復元可能性を列挙する。

## なぜ path だけでは足りないか

正典が列挙を求める 7 項目は、それぞれ別の失敗を防ぐ。``regenerable_by`` が空で
``restorable`` が偽の artifact は、消したら二度と戻らない (untracked file がこれに
当たる)。``referenced_by`` が空でないものは、消すと参照元が壊れる。``retention_reason``
は「なぜ残すのか」を後から読めるようにする。path と理由だけの manifest では、
「消してよいか」を判断した根拠が残らない。

## 保持対象は削除候補にできない

正典 §20 は「現 champion、seed deck、teacher checkpoint、公開データ provenance、
meta snapshot、再現 manifest、ユーザーの dirty worktree 変更は保持する」と定める。
``PROTECTED_PREFIXES_V1`` に該当する path は manifest へ載せた時点で拒否する。
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Sequence


CLEANUP_MANIFEST_SCHEMA_V1 = "meta-specialist-cleanup-manifest-v1"

# 正典 §20 が明示的に保持を求めるもの。
PROTECTED_PREFIXES_V1: tuple[str, ...] = (
    "opponents",            # seed deck / 公開データ provenance
    "configs",              # archetype registry
    "docs/decisions",       # 判断記録
    "quarantine",           # 隔離済みの証跡
    "vendor_opponent_pilots",
)


class CleanupManifestV1Error(ValueError):
    """Raised when a deletion is unauthorized or a target is protected."""


@dataclass(frozen=True, slots=True)
class CleanupTargetV1:
    """正典 §20 が cleanup planner に列挙させる 7 項目。

    ``regenerable_by`` と ``restorable`` の両方が空/偽なら、その artifact は消したら
    復元できない。``require_recoverable`` を満たせないので manifest に載らない。
    """

    relative_path: str
    size_bytes: int
    content_sha256: str
    referenced_by: tuple[str, ...]
    regenerable_by: str
    retention_reason: str
    restorable: bool

    def __post_init__(self) -> None:
        if not self.relative_path or Path(self.relative_path).is_absolute():
            raise CleanupManifestV1Error("relative_path must be a non-empty relative path")
        if type(self.size_bytes) is not int or self.size_bytes < 0:
            raise CleanupManifestV1Error(f"{self.relative_path}: size_bytes must be >= 0")
        if len(self.content_sha256) != 64:
            raise CleanupManifestV1Error(
                f"{self.relative_path}: content_sha256 must be a 64-hex digest so the "
                "manifest names the exact bytes it authorizes removing"
            )
        if not self.retention_reason:
            raise CleanupManifestV1Error(
                f"{self.relative_path}: retention_reason must say why this may go"
            )
        normalized = Path(self.relative_path).as_posix()
        for prefix in PROTECTED_PREFIXES_V1:
            if normalized == prefix or normalized.startswith(f"{prefix}/"):
                raise CleanupManifestV1Error(
                    f"{self.relative_path} is under the protected prefix {prefix!r}; "
                    "正典 §20 はこれらの保持を求める"
                )
        if self.referenced_by:
            raise CleanupManifestV1Error(
                f"{self.relative_path} is still referenced by {list(self.referenced_by)}; "
                "参照が残る artifact は削除候補にできない"
            )
        if not self.regenerable_by and not self.restorable:
            raise CleanupManifestV1Error(
                f"{self.relative_path} is neither regenerable nor restorable; deleting it "
                "would be irreversible (正典 §20: untracked file は復元不能になり得る)"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "relative_path": self.relative_path,
            "size_bytes": self.size_bytes,
            "content_sha256": self.content_sha256,
            "referenced_by": list(self.referenced_by),
            "regenerable_by": self.regenerable_by,
            "retention_reason": self.retention_reason,
            "restorable": self.restorable,
        }


@dataclass(frozen=True, slots=True)
class CleanupManifestV1:
    manifest_id: str
    targets: tuple[CleanupTargetV1, ...]

    def is_authorized(self, path: str | Path) -> bool:
        normalized = Path(path).as_posix()
        return any(Path(t.relative_path).as_posix() == normalized for t in self.targets)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": CLEANUP_MANIFEST_SCHEMA_V1,
            "manifest_id": self.manifest_id,
            "targets": [t.to_dict() for t in self.targets],
        }

    def content_hash(self) -> str:
        return hashlib.sha256(
            b"mage_ptcg:cleanup-manifest:v1\0"
            + json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()


def validate_deletion_authorization_v1(
    manifest: CleanupManifestV1 | None, target_paths: Sequence[str | Path]
) -> None:
    """Refuse any deletion the manifest does not explicitly authorize.

    ``manifest=None`` is refused rather than treated as "nothing to check": the
    canon's rule is that a deletion without a manifest does not happen, so the
    absence of one must fail loudly instead of passing an empty check.
    """
    if manifest is None:
        raise CleanupManifestV1Error(
            "deletion requires an explicit cleanup manifest; none was supplied "
            "(正典 §22 条項18: cleanup manifest なしに user artifact を削除しない)"
        )
    if type(manifest) is not CleanupManifestV1:
        raise CleanupManifestV1Error("manifest must be a CleanupManifestV1")
    for path in target_paths:
        if not manifest.is_authorized(path):
            raise CleanupManifestV1Error(
                f"deletion of {path} is not authorized by manifest {manifest.manifest_id}"
            )


def plan_cleanup_target_v1(
    repo_root: str | Path,
    relative_path: str,
    *,
    referenced_by: Sequence[str],
    regenerable_by: str,
    retention_reason: str,
    restorable: bool,
) -> CleanupTargetV1:
    """Measure size and content hash from disk rather than trusting the caller."""
    root = Path(repo_root).resolve()
    from mage_ptcg.meta_specialist.worktree_guard_v1 import assert_path_is_cleanable_v1

    resolved = assert_path_is_cleanable_v1(relative_path, repo_root=root)
    if not resolved.is_file():
        raise CleanupManifestV1Error(f"{relative_path} is not an existing regular file")
    body = resolved.read_bytes()
    return CleanupTargetV1(
        relative_path=Path(relative_path).as_posix(),
        size_bytes=len(body),
        content_sha256=hashlib.sha256(body).hexdigest(),
        referenced_by=tuple(referenced_by),
        regenerable_by=regenerable_by,
        retention_reason=retention_reason,
        restorable=bool(restorable),
    )


__all__ = [
    "CLEANUP_MANIFEST_SCHEMA_V1",
    "PROTECTED_PREFIXES_V1",
    "CleanupManifestV1",
    "CleanupManifestV1Error",
    "CleanupTargetV1",
    "plan_cleanup_target_v1",
    "validate_deletion_authorization_v1",
]
