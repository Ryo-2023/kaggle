from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from mage_ptcg.opponent_ingest.rocket_theta_behavior_meta_v2 import (
    ROCKET_THETA_VARIANTS_V2,
    RocketThetaBehaviorMetaError,
    seal_rocket_theta_behavior_meta_v2,
    _transform_rocket_theta,
)


ROOT = Path(__file__).resolve().parents[1]
BASE_ROOT = (
    ROOT
    / "runs/cg-fresh-internal-meta-intake-20260815-f"
    / "internal_ozawa-rocket-rule_de797c3646e9"
)


def _theta_values(source: bytes) -> dict[str, dict[str, object]]:
    tree = ast.parse(source.decode("utf-8"))
    result: dict[str, dict[str, object]] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if isinstance(target, ast.Name) and target.id.startswith("_THETA_"):
            result[target.id] = ast.literal_eval(node.value)
    return result


def test_setup_transform_changes_all_five_tables_without_touching_dispatch() -> None:
    source = (BASE_ROOT / "main.py").read_bytes()
    original = _theta_values(source)

    transformed, recipe = _transform_rocket_theta(source, "SETUP_SHRINK")
    assert transformed != source
    assert recipe == "ROCKET_THETA_BEHAVIOR_V2:SETUP_SHRINK"

    updated = _theta_values(transformed)
    assert set(updated) == {
        "_THETA_GENERAL",
        "_THETA_LUCMIX",
        "_THETA_A09_MERGED",
        "_THETA_A07_MERGED",
        "_THETA_ABOMASNOW_R2",
    }
    assert all(
        updated[name]["a_place_yami"] != original[name]["a_place_yami"]
        for name in updated
    )
    for name in updated:
        for key, value in original[name].items():
            if isinstance(value, bool):
                assert updated[name][key] is value

    original_text = source.decode("utf-8")
    updated_text = transformed.decode("utf-8")
    assert "_SPECIALIST_THETA = {" in updated_text
    assert "_apply_theta(theta: dict)" in updated_text
    assert original_text.count("_dispatch_extract_opponent_card_ids") == updated_text.count(
        "_dispatch_extract_opponent_card_ids"
    )


def test_composed_transform_is_deterministic_and_bounded() -> None:
    source = (BASE_ROOT / "main.py").read_bytes()
    variant = "SETUP_EXPAND+ATTACK_EXPAND"
    first, first_recipe = _transform_rocket_theta(source, variant)
    second, second_recipe = _transform_rocket_theta(source, variant)
    assert first == second
    assert first_recipe == second_recipe == f"ROCKET_THETA_BEHAVIOR_V2:{variant}"

    for values in _theta_values(first).values():
        assert -1.2 <= values["a_place_yami"] <= 1.2
        assert -1.4 <= values["c_tr_mewtwo"] <= 1.4
        assert 1.0 <= values["c_mewtwo_notready_div"] <= 5.0


def test_transformer_rejects_unknown_variant_and_missing_theta_table() -> None:
    source = (BASE_ROOT / "main.py").read_bytes()
    assert "SETUP_SHRINK" in ROCKET_THETA_VARIANTS_V2
    with pytest.raises(RocketThetaBehaviorMetaError, match="unsupported"):
        _transform_rocket_theta(source, "NO_SUCH_VARIANT")

    missing = source.decode("utf-8").replace("_THETA_A07_MERGED = {", "_THETA_A07_MERGED_REMOVED = {", 1)
    with pytest.raises(RocketThetaBehaviorMetaError, match="theta table"):
        _transform_rocket_theta(missing.encode("utf-8"), "SETUP_SHRINK")


def test_public_variant_list_has_twelve_unique_entries() -> None:
    assert len(ROCKET_THETA_VARIANTS_V2) == 12
    assert len(set(ROCKET_THETA_VARIANTS_V2)) == 12
    assert "SETUP_SHRINK+SUPPORTER_FLATTEN" in ROCKET_THETA_VARIANTS_V2


def test_seal_writes_hash_bound_pool_and_explicit_8_2_2_split(tmp_path: Path) -> None:
    split = {
        variant: (
            "META_TRAIN"
            if index < 8
            else "META_DEV"
            if index < 10
            else "META_FINAL"
        )
        for index, variant in enumerate(ROCKET_THETA_VARIANTS_V2)
    }
    output = tmp_path / "rocket-meta"
    report = seal_rocket_theta_behavior_meta_v2(
        base_root=BASE_ROOT,
        output_root=output,
        source_epoch="test-rocket-theta-v2",
        seed_namespace="test-seed",
        p1_package=ROOT / "runs/final-sprint-autonomous/cg-policy-screen-v1-retry-safe4-20260814/candidates/cg-lethal-target-v1/package",
        split_by_variant=split,
    )
    assert report["status"] == "SEALED"
    assert report["accepted_count"] == 12
    assert report["split_counts"] == {"META_TRAIN": 8, "META_DEV": 2, "META_FINAL": 2}

    pool = json.loads((output / "pool_manifest.json").read_text(encoding="utf-8"))
    fresh = json.loads((output / "fresh_meta.json").read_text(encoding="utf-8"))
    split_payload = json.loads((output / "cg_historical_split.json").read_text(encoding="utf-8"))
    assert len(pool) == 12
    assert len({row["policy_hash"] for row in pool}) == 12
    assert all(row["usage_boundary"] == "local_eval_only" for row in pool)
    assert all(row["split"] == split[row["source_label"]] for row in pool)
    assert fresh["authority"] == {
        "training_allowed": False,
        "promotion_allowed": False,
        "submission_allowed": False,
        "longrun_allowed": False,
    }
    assert len(split_payload["splits"]["META_TRAIN"]) == 8
    assert len(split_payload["splits"]["META_DEV"]) == 2
    assert len(split_payload["splits"]["META_FINAL"]) == 2

    with pytest.raises(FileExistsError):
        seal_rocket_theta_behavior_meta_v2(
            base_root=BASE_ROOT,
            output_root=output,
            source_epoch="test-rocket-theta-v2",
            seed_namespace="test-seed",
            p1_package=ROOT / "runs/final-sprint-autonomous/cg-policy-screen-v1-retry-safe4-20260814/candidates/cg-lethal-target-v1/package",
            split_by_variant=split,
        )


def test_seal_rejects_non_reserved_split_shape() -> None:
    split = {variant: "META_TRAIN" for variant in ROCKET_THETA_VARIANTS_V2}
    with pytest.raises(RocketThetaBehaviorMetaError, match="8/2/2"):
        seal_rocket_theta_behavior_meta_v2(
            base_root=BASE_ROOT,
            output_root=ROOT / "runs" / "this-output-must-not-exist-for-test",
            source_epoch="test-rocket-theta-v2",
            seed_namespace="test-seed",
            p1_package=ROOT / "runs/final-sprint-autonomous/cg-policy-screen-v1-retry-safe4-20260814/candidates/cg-lethal-target-v1/package",
            split_by_variant=split,
        )


def test_checked_in_config_reserves_train_dev_final_without_overlap() -> None:
    config = json.loads(
        (ROOT / "configs/meta_specialist/cg_rocket_theta_behavior_v2.json").read_text(
            encoding="utf-8"
        )
    )
    variants = tuple(config["variants"])
    split = config["split_by_variant"]
    assert variants == ROCKET_THETA_VARIANTS_V2
    assert {name: list(split.values()).count(name) for name in ("META_TRAIN", "META_DEV", "META_FINAL")} == {
        "META_TRAIN": 8,
        "META_DEV": 2,
        "META_FINAL": 2,
    }
