from __future__ import annotations

import json
from pathlib import Path

import pytest

from mage_ptcg.opponent_ingest.waterbox_runtime_safe_meta_v1 import (
    WATERBOX_RUNTIME_SAFE_VARIANTS_V1,
    WaterboxRuntimeSafeMetaError,
    _transform_waterbox_runtime_safe,
    seal_waterbox_runtime_safe_meta_v1,
)


SOURCE = b'''\
SEARCH_NUM_WORLDS = 3
SEARCH_LOCAL_FIXED_BUDGET = 0.05
SEARCH_MAX_DECISION_BUDGET = 4.0
SEARCH_MIN_DECISION_BUDGET = 0.3
SEARCH_GLOBAL_GUARD_SECONDS = 360.0
_RAW_STEP = None

def _search_should_run(obs, rule_choice):
    if obs.select.type != SelectType.MAIN:
        return False
    return True
'''


def test_rule_only_and_periodic_transforms_are_deterministic() -> None:
    outputs = []
    for variant in WATERBOX_RUNTIME_SAFE_VARIANTS_V1:
        first = _transform_waterbox_runtime_safe(SOURCE, variant)
        second = _transform_waterbox_runtime_safe(SOURCE, variant)
        assert first == second
        outputs.append(first[0])
    assert len(WATERBOX_RUNTIME_SAFE_VARIANTS_V1) == 12
    assert len(set(outputs)) == 12

    rule_only, recipe = _transform_waterbox_runtime_safe(SOURCE, "RULE_ONLY_V2")
    assert recipe == "WATERBOX_RUNTIME_SAFE_V1:RULE_ONLY_V2"
    assert b"return False  # WATERBOX_RUNTIME_SAFE_RULE_ONLY_V2" in rule_only


def test_unknown_or_drifted_source_fails_closed() -> None:
    with pytest.raises(WaterboxRuntimeSafeMetaError, match="unsupported"):
        _transform_waterbox_runtime_safe(SOURCE, "UNKNOWN")
    with pytest.raises(WaterboxRuntimeSafeMetaError, match="exactly one"):
        _transform_waterbox_runtime_safe(SOURCE.replace(b"SEARCH_NUM_WORLDS = 3\n", b""), "MICRO_005")


def test_seal_emits_fresh_8_2_2_pool(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    base_root = repo_root / "opponents/waterbox_search_v3"
    p1_package = repo_root / "runs/final-sprint-autonomous/cg-policy-screen-v1-retry-safe4-20260814/candidates/cg-lethal-target-v1/package"
    variants = list(WATERBOX_RUNTIME_SAFE_VARIANTS_V1)
    split = {
        variant: ("META_TRAIN" if index < 8 else "META_DEV" if index < 10 else "META_FINAL")
        for index, variant in enumerate(variants)
    }

    report = seal_waterbox_runtime_safe_meta_v1(
        base_root=base_root,
        output_root=tmp_path / "sealed",
        source_epoch="test-waterbox-epoch",
        seed_namespace="test-waterbox-seed",
        p1_package=p1_package,
        variants=variants,
        split_by_variant=split,
        current_pool_manifest=repo_root / "opponents/pool_manifest.json",
        scan_roots=(tmp_path,),
    )

    assert report["status"] == "SEALED"
    assert report["accepted_count"] == 12
    assert report["split_counts"] == {"META_TRAIN": 8, "META_DEV": 2, "META_FINAL": 2}
    pool = json.loads((tmp_path / "sealed/pool_manifest.json").read_text())
    assert len(pool) == 12
    assert len({row["policy_hash"] for row in pool}) == 12
    fresh = json.loads((tmp_path / "sealed/fresh_meta.json").read_text())
    assert all(item["usage_boundary"] == "local_eval_only" for item in fresh["references"])
    assert all(value is False for value in fresh["authority"].values())


def test_checked_in_config_has_explicit_split() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    config = json.loads(
        (repo_root / "configs/meta_specialist/cg_waterbox_runtime_safe_v1.json").read_text()
    )
    assert config["variants"] == list(WATERBOX_RUNTIME_SAFE_VARIANTS_V1)
    assert len(set(config["variants"])) == 12
    assert {
        name: list(config["split_by_variant"].values()).count(name)
        for name in ("META_TRAIN", "META_DEV", "META_FINAL")
    } == {"META_TRAIN": 8, "META_DEV": 2, "META_FINAL": 2}
