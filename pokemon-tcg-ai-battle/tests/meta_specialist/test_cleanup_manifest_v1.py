"""削除は明示 manifest に列挙されたものだけ (正典 §22 条項18 / §20)。"""

from __future__ import annotations

from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.cleanup_manifest_v1 import (
    CleanupManifestV1,
    CleanupManifestV1Error,
    CleanupTargetV1,
    plan_cleanup_target_v1,
    validate_deletion_authorization_v1,
)


def _target(path: str = "runs/tmp/old.log", **overrides) -> CleanupTargetV1:
    fields = {
        "relative_path": path,
        "size_bytes": 10,
        "content_sha256": "a" * 64,
        "referenced_by": (),
        "regenerable_by": "scripts/run_teacher_collection.py",
        "retention_reason": "superseded by a later run",
        "restorable": False,
    }
    fields.update(overrides)
    return CleanupTargetV1(**fields)


def test_a_listed_target_is_authorized() -> None:
    manifest = CleanupManifestV1(manifest_id="m1", targets=(_target(),))
    validate_deletion_authorization_v1(manifest, ["runs/tmp/old.log"])


def test_an_unlisted_target_is_refused() -> None:
    manifest = CleanupManifestV1(manifest_id="m1", targets=(_target(),))
    with pytest.raises(CleanupManifestV1Error):
        validate_deletion_authorization_v1(manifest, ["runs/tmp/other.log"])


def test_a_missing_manifest_is_refused_rather_than_treated_as_an_empty_check() -> None:
    """manifest が無い削除は「検査対象ゼロで通過」にしないこと.

    正典 §22 条項18 は「cleanup manifest なしに user artifact を削除しない」と定める。
    None を空検査として通すと、manifest を渡し忘れた呼び出しが素通りする。
    """
    with pytest.raises(CleanupManifestV1Error):
        validate_deletion_authorization_v1(None, ["anything"])


def test_an_irreversible_target_cannot_be_listed() -> None:
    """再生成も復元もできない artifact を削除候補にできないこと (正典 §20)."""
    with pytest.raises(CleanupManifestV1Error):
        _target(regenerable_by="", restorable=False)


def test_a_still_referenced_target_cannot_be_listed() -> None:
    with pytest.raises(CleanupManifestV1Error):
        _target(referenced_by=("scripts/run_bc_distillation.py",))


@pytest.mark.parametrize("path", [
    "opponents/tomatomato_archaludon/deck.csv",
    "configs/meta_specialist/archetypes_v1.json",
    "docs/decisions/2026-08-05-crustle-deck-core-mismatch.md",
])
def test_protected_prefixes_cannot_be_listed(path: str) -> None:
    """正典 §20 が保持を求めるもの (seed deck、registry、判断記録) を守ること."""
    with pytest.raises(CleanupManifestV1Error):
        _target(path)


def test_the_manifest_names_the_exact_bytes_it_authorizes() -> None:
    """content hash 無しの承認を作れないこと.

    path だけの承認は、同じ path に別の内容が置かれた後でも有効に見えてしまう。
    """
    with pytest.raises(CleanupManifestV1Error):
        _target(content_sha256="short")


def test_planning_measures_the_file_rather_than_trusting_the_caller(tmp_path: Path) -> None:
    """size と hash をディスクから測ること."""
    root = tmp_path
    (root / "runs" / "tmp").mkdir(parents=True)
    victim = root / "runs" / "tmp" / "old.log"
    victim.write_bytes(b"0123456789")

    target = plan_cleanup_target_v1(
        root, "runs/tmp/old.log", referenced_by=(),
        regenerable_by="scripts/x.py", retention_reason="superseded", restorable=False,
    )
    assert target.size_bytes == 10
    assert target.content_sha256 != "a" * 64


def test_a_glob_or_the_repository_root_is_never_cleanable(tmp_path: Path) -> None:
    """正典 §20: 広い glob、repository root、runs/ 全体を対象にしない."""
    from mage_ptcg.meta_specialist.worktree_guard_v1 import WorktreeGuardV1Error

    for bad in ("runs/*", "runs", ".", "*"):
        with pytest.raises((CleanupManifestV1Error, WorktreeGuardV1Error)):
            plan_cleanup_target_v1(
                tmp_path, bad, referenced_by=(), regenerable_by="x",
                retention_reason="y", restorable=True,
            )
