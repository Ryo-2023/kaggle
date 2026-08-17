"""封印済み teacher snapshot から θ0 を作る behavior-cloning 蒸留。

正典 §9.3 (教師データの重み) と §1 の Foundation θ0 に対応する。

## これが解く問題

正典 §1 は curriculum の起点を Foundation θ0 と定める。以前の実装はここが乱数
初期化 + rule agent コーパスの模倣であり、実測でも学習後の方策が「rule agent の
鋭くした複製」へ収束していた (experiments/2026-08-04-...round1.md)。この module は
θ0 を **既知の強い teacher の複製**として作る。

## V-trace ではなく BC である理由

teacher は我々の action 空間上の確率分布を持たない。V-trace は behavior policy の
log-prob を要求し、それが無い以上 importance ratio は定義できない (正典 §10.3:
「V-trace ratio が補正するのは subject behavior と learner policy の差だけ」)。
したがってここでは ratio を持たない教師あり学習だけを行う。RL は θ0 の後段で
`train_from_trajectories_v1` が担う。

## 損失

各 example の ``loss_rows`` は、teacher の complete action を decode する各段の
「その段で選ぶべき class の目標質量」である (``local_dataset_v2`` が到達可能性まで
検証済み)。損失は段ごとの cross-entropy を ``example_quality_weight`` で重み付けした
和とする。

正典 §9.3 に従い、**敗局を落とさない**。outcome は ``value_target`` にだけ反映され、
policy 側の重みを 0 にしない。value head の学習は本 module の対象外であり、
``value_target`` は θ0 の段階では記録のみで使用しない (critic は RL 段で較正する)。
"""

from __future__ import annotations

from typing import Any, Mapping


BC_DISTILL_SCHEMA_V1 = "specialist-bc-distill-v1"


class BcDistillV1Error(ValueError):
    """Raised when a distillation input or step is not usable as specified."""








def teacher_ids_in_snapshot_v1(snapshot: Mapping[str, Any]) -> tuple[str, ...]:
    """Every source artifact the snapshot's examples came from.

    Used to build the FoundationInit provenance: θ0 must name the teachers it
    was distilled from, and those must be exactly the artifacts the snapshot
    actually contains -- not a caller-supplied list that could drift from the
    data.
    """
    artifacts = snapshot.get("source_artifacts")
    if not isinstance(artifacts, (list, tuple)) or not artifacts:
        raise BcDistillV1Error("snapshot has no source_artifacts to attribute θ0 to")
    return tuple(sorted(str(item["artifact_sha256"]) for item in artifacts))


def foundation_init_from_snapshot_v1(
    snapshot: Mapping[str, Any],
    *,
    teacher_id: str,
    usage_boundary: str,
    derivation_boundary: str,
    decision_ref: str,
    notes: str = "",
):
    """Build the θ0 provenance naming the teacher this snapshot came from.

    The teacher's policy hash is read out of the snapshot rather than taken on
    the caller's word, so a checkpoint cannot claim a teacher whose data it was
    not trained on.
    """
    from mage_ptcg.meta_specialist.foundation_init_v1 import (
        FoundationInitProvenanceV1,
        INIT_KIND_BC_DISTILLED_V1,
        TeacherRefV1,
        assert_primary_teacher_is_not_rule_v0_v1,
    )

    hashes = teacher_ids_in_snapshot_v1(snapshot)
    if len(hashes) != 1:
        raise BcDistillV1Error(
            f"expected exactly one source artifact to attribute θ0 to, got {len(hashes)}"
        )
    provenance = FoundationInitProvenanceV1(
        init_kind=INIT_KIND_BC_DISTILLED_V1,
        teachers=(
            TeacherRefV1(
                teacher_id=teacher_id,
                teacher_kind="external_submission_agent",
                policy_hash=hashes[0],
                usage_boundary=usage_boundary,
                derivation_boundary=derivation_boundary,
                decision_ref=decision_ref,
            ),
        ),
        parent_checkpoint_sha256="",
        notes=notes,
    )
    assert_primary_teacher_is_not_rule_v0_v1(provenance)
    return provenance


__all__ = [
    "BC_DISTILL_SCHEMA_V1",
    "BcDistillV1Error",
    "foundation_init_from_snapshot_v1",
    "teacher_ids_in_snapshot_v1",
]
