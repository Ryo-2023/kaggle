from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.dynamic_meta_train_curriculum_v1 import (
    DynamicMetaTrainCurriculumError,
    build_dynamic_curriculum_plan_v1,
    build_dynamic_curriculum_manifest_v1,
    verify_dynamic_curriculum_manifest_v1,
)
from mage_ptcg.meta_specialist.meta_distribution_v1 import MetaDistributionRowV1


def _row(
    opponent_id: str,
    *,
    family: str,
    split: str = "META_TRAIN",
    weight: float = 0.1,
    evaluation_allowed: bool = True,
    usage_boundary: str = "local_eval_only",
) -> MetaDistributionRowV1:
    return MetaDistributionRowV1(
        opponent_id=opponent_id,
        pair_id=f"pair::{opponent_id}",
        deck_sha256="1" * 64,
        policy_sha256=("2" * 63) + str((len(opponent_id) % 8) + 1),
        archetype=family,
        runtime_class="native_fast",
        source="public",
        source_sha256="3" * 64,
        usage_boundary=usage_boundary,
        evaluation_allowed=evaluation_allowed,
        training_allowed=False,
        behavior_allowed=False,
        submission_allowed=False,
        observed_strength=0.5,
        observed_games=96,
        observed_fault_rate=0.0,
        frequency_proxy=0.5,
        hard_negative_score=0.5,
        diversity_contribution=0.5,
        top_meta_component=0.05,
        hard_negative_component=0.025,
        diversity_component=0.015,
        weight=weight,
        split=split,
        runtime_status="smoke_pass_fast",
        evidence_status="observed",
    )


def _rows() -> tuple[MetaDistributionRowV1, ...]:
    return (
        _row("a1", family="A"),
        _row("a2", family="A"),
        _row("b1", family="B"),
        _row("c1", family="C"),
        _row("dev", family="D", split="META_DEV"),
        _row("final", family="E", split="META_FINAL"),
    )


def test_plan_keeps_dev_final_at_zero_and_enforces_family_diversity() -> None:
    plan = build_dynamic_curriculum_plan_v1(
        rows=_rows(),
        selected_opponent_ids=("a1", "a2", "b1", "c1", "dev", "final"),
        quota=24,
        seed="curriculum-seed",
        iteration=0,
        outcomes=(),
        max_opponent_weight=0.45,
        max_family_weight=0.55,
        min_family_quota=2,
    )
    entries = {entry.opponent_id: entry for entry in plan.entries}
    assert entries["dev"].weight == entries["final"].weight == 0.0
    assert entries["dev"].quota == entries["final"].quota == 0
    assert entries["dev"].training_exposure_allowed is False
    assert entries["final"].training_exposure_allowed is False
    train = [entry for entry in plan.entries if entry.split == "META_TRAIN"]
    assert sum(entry.weight for entry in train) == pytest.approx(1.0)
    assert sum(entry.quota for entry in train) == 24
    family_quota: dict[str, int] = {}
    for entry in train:
        family_quota[entry.family] = family_quota.get(entry.family, 0) + entry.quota
        assert entry.weight <= 0.45 + 1e-12
        assert entry.teacher_behavior_allowed is False
    assert all(value >= 2 for value in family_quota.values())
    assert max(
        sum(entry.weight for entry in train if entry.family == family)
        for family in family_quota
    ) <= 0.55 + 1e-12


def test_iteration_outcomes_increase_hard_negative_and_track_seat_fault_exposure() -> None:
    baseline = build_dynamic_curriculum_plan_v1(
        rows=_rows(),
        selected_opponent_ids=("a1", "a2", "b1", "c1", "dev", "final"),
        quota=24,
        seed="same-seed",
        iteration=0,
        outcomes=(),
    )
    outcomes = (
        {"opponent_id": "b1", "candidate_score": 0.0, "fault": False, "seat": 0},
        {"opponent_id": "b1", "candidate_score": 0.0, "fault": False, "seat": 1},
        {"opponent_id": "a1", "candidate_score": 1.0, "fault": False, "seat": 0},
        {"opponent_id": "a1", "candidate_score": 1.0, "fault": False, "seat": 1},
        {"opponent_id": "c1", "candidate_score": 0.0, "fault": True, "seat": 0},
    )
    updated = build_dynamic_curriculum_plan_v1(
        rows=_rows(),
        selected_opponent_ids=("a1", "a2", "b1", "c1", "dev", "final"),
        quota=24,
        seed="same-seed",
        iteration=1,
        outcomes=outcomes,
    )
    before = {entry.opponent_id: entry for entry in baseline.entries}
    after = {entry.opponent_id: entry for entry in updated.entries}
    assert after["b1"].weight > before["b1"].weight
    assert after["b1"].statistics["candidate_score"] == 0.0
    assert after["b1"].statistics["seat_exposure"] == {"0": 1, "1": 1}
    assert after["c1"].statistics["fault_rate"] == 1.0
    assert "fault_reliability_penalty" in after["c1"].reason


def test_plan_is_deterministic_and_fails_closed_on_permission_or_holdout_ledger() -> None:
    kwargs = dict(
        rows=_rows(),
        selected_opponent_ids=("a1", "a2", "b1", "c1", "dev", "final"),
        quota=24,
        seed="stable",
        iteration=0,
        outcomes=(),
    )
    first = build_dynamic_curriculum_plan_v1(**kwargs)
    second = build_dynamic_curriculum_plan_v1(**kwargs)
    assert [asdict(entry) for entry in first.entries] == [asdict(entry) for entry in second.entries]

    bad_rows = list(_rows())
    bad_rows[0] = _row("a1", family="A", evaluation_allowed=False)
    with pytest.raises(DynamicMetaTrainCurriculumError, match="evaluation permission"):
        build_dynamic_curriculum_plan_v1(**{**kwargs, "rows": tuple(bad_rows)})

    with pytest.raises(DynamicMetaTrainCurriculumError, match="held-out"):
        build_dynamic_curriculum_plan_v1(
            **{
                **kwargs,
                "outcomes": (
                    {"opponent_id": "final", "candidate_score": 0.5, "fault": False, "seat": 0},
                ),
            }
        )


def test_actual_common24_iteration0_manifest_is_hash_bound_and_heldout_clean(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[2]
    output = tmp_path / "manifest.json"
    built = build_dynamic_curriculum_manifest_v1(
        repo_root=root,
        meta_manifest_path=root / "runs/final-sprint-autonomous/meta-distribution-v1/manifest.json",
        meta_schedule_path=root / "runs/final-sprint-autonomous/meta-distribution-v1/meta_schedule.json",
        broad_pool_config_path=root / "configs/meta_specialist/performance_first_broad_pool_v1.json",
        output_manifest_path=output,
        quota=96,
        seed="common24-dynamic-curriculum-v1",
        iteration=0,
    )
    verified = verify_dynamic_curriculum_manifest_v1(output, root)
    assert verified == built
    assert built["summary"] == {
        "selected_by_split": {"META_DEV": 0, "META_FINAL": 4, "META_TRAIN": 20},
        "nonzero_exposure_by_split": {"META_DEV": 0, "META_FINAL": 0, "META_TRAIN": 20},
        "teacher_behavior_eligible_count": 0,
        "training_family_count": 12,
    }
    assert built["authority"] == {
        "external_execution_authority": False,
        "promotion_authority": False,
        "submission_authority": False,
        "training_authority": False,
    }
    with pytest.raises(FileExistsError):
        build_dynamic_curriculum_manifest_v1(
            repo_root=root,
            meta_manifest_path=root / "runs/final-sprint-autonomous/meta-distribution-v1/manifest.json",
            meta_schedule_path=root / "runs/final-sprint-autonomous/meta-distribution-v1/meta_schedule.json",
            broad_pool_config_path=root / "configs/meta_specialist/performance_first_broad_pool_v1.json",
            output_manifest_path=output,
            quota=96,
            seed="common24-dynamic-curriculum-v1",
            iteration=0,
        )

    tampered = json.loads(output.read_text(encoding="utf-8"))
    tampered["entries"][0]["quota"] += 1
    output.write_text(
        json.dumps(tampered, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    with pytest.raises(DynamicMetaTrainCurriculumError, match="semantic SHA"):
        verify_dynamic_curriculum_manifest_v1(output, root)
