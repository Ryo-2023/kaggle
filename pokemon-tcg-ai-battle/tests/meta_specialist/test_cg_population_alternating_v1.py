from __future__ import annotations

import json
from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.cg_alternating_runtime_v1 import (
    CG_DECK_FIXED_LONG_V1,
    CG_POLICY_FIXED_SHORT_V1,
    CgPackageSpecV1,
    validate_cg_pair_v1,
)
from mage_ptcg.meta_specialist.cg_population_alternating_v1 import (
    CgPopulationScheduleError,
    build_cg_population_schedule_v1,
    load_cg_population_schedule_v1,
    save_cg_population_schedule_v1,
)


ROOT = Path(__file__).resolve().parents[2]
META_MANIFEST = ROOT / "runs/final-sprint-autonomous/meta-distribution-v1/manifest.json"
POOL_MANIFEST = ROOT / "opponents/pool_manifest.json"
P1_PACKAGE = ROOT / (
    "runs/final-sprint-autonomous/cg-policy-screen-v1-retry-safe4-20260814/"
    "candidates/cg-lethal-target-v1/package"
)
P0_PACKAGE = ROOT / "runs/final-sprint-autonomous/root-cg-submission-candidate-v1-20260814/package"


def test_schedule_is_meta_train_only_pool_bound_and_roundtrips(tmp_path: Path) -> None:
    schedule = build_cg_population_schedule_v1(
        manifest_path=META_MANIFEST,
        pool_manifest_path=POOL_MANIFEST,
        split="META_TRAIN",
        count=24,
    )
    assert len(schedule.reference_ids) == 24
    assert schedule.split == "META_TRAIN"
    assert schedule.manifest_sha256
    assert schedule.pool_manifest_sha256
    assert schedule.evaluation_only is True
    assert all(schedule.usage_boundaries[item] == "local_eval_only" for item in schedule.reference_ids)
    assert set(schedule.reference_ids).issubset(set(schedule.pool_ids))
    target = tmp_path / "schedule.json"
    digest = save_cg_population_schedule_v1(schedule, target)
    loaded = load_cg_population_schedule_v1(target, verify_sources=True)
    assert digest
    assert loaded.to_dict() == schedule.to_dict()


def test_schedule_rejects_non_train_and_unknown_pool(tmp_path: Path) -> None:
    with pytest.raises(CgPopulationScheduleError):
        build_cg_population_schedule_v1(
            manifest_path=META_MANIFEST,
            pool_manifest_path=POOL_MANIFEST,
            split="META_FINAL",
            count=24,
        )
    fake_pool = tmp_path / "pool.json"
    fake_pool.write_text(json.dumps([{"id": "unknown", "smoke_ok": True}]), encoding="utf-8")
    with pytest.raises(CgPopulationScheduleError):
        build_cg_population_schedule_v1(
            manifest_path=META_MANIFEST,
            pool_manifest_path=fake_pool,
            split="META_TRAIN",
            count=24,
        )


def test_cg_phase_contract_reuses_population_frozen_identity() -> None:
    candidate = CgPackageSpecV1.from_package(P1_PACKAGE)
    control = CgPackageSpecV1.from_package(P0_PACKAGE)
    assert candidate.deck_sha256 == control.deck_sha256
    pair = validate_cg_pair_v1(
        phase=CG_DECK_FIXED_LONG_V1,
        candidate=candidate,
        control=control,
        stage_games=96,
    )
    assert pair.phase == CG_DECK_FIXED_LONG_V1
    with pytest.raises(Exception):
        validate_cg_pair_v1(
            phase=CG_POLICY_FIXED_SHORT_V1,
            candidate=candidate,
            control=control,
            stage_games=96,
        )


def test_population_stage_contract_is_workers12_and_successive_halving() -> None:
    from scripts.run_cg_population_alternating_v1 import validate_population_stage_contract_v1

    validate_population_stage_contract_v1(stage_games=96, workers=12, worker_recycle_games=16)
    validate_population_stage_contract_v1(stage_games=384, workers=12, worker_recycle_games=64)
    with pytest.raises(Exception):
        validate_population_stage_contract_v1(stage_games=96, workers=4, worker_recycle_games=16)
