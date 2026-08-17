from __future__ import annotations

import json
from pathlib import Path

from scripts.generate_self_owned_cg_independent_policy_meta_v1 import (
    load_factorial_plan_v1,
    run_generation_v1,
)


ROOT = Path(__file__).resolve().parents[1]
ROOT_PACKAGE = ROOT / "runs/final-sprint-autonomous/root-cg-submission-candidate-v1-20260814/package"


def test_independent_plan_loader_binds_independent_schema(tmp_path: Path) -> None:
    plan = {
        "schema_version": "self-owned-cg-independent-factorial-plan-v1",
        "source_epoch": "test-independent-epoch",
        "seed_namespace": "test-independent-seed",
        "card_database": "data/raw/EN_Card_Data.csv",
        "public_scan_roots": [],
        "deck_recipes": [
            {
                "id": "recipe-00",
                "spec": "configs/meta_specialist/self_owned_cg_deck_spec_v5_broad_support.json",
                "seed": 20269900,
                "ordinal": 0,
            }
        ],
        "policy_variants": [
            {
                "id": "baseline-00",
                "deck_recipe_id": "recipe-00",
                "overrides": {"lethal_bonus": 12000},
            }
        ],
    }
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    loaded = load_factorial_plan_v1(path)
    assert loaded["schema_version"] == plan["schema_version"]
    assert loaded["policy_variants"][0]["config_sha256"]


def test_independent_generation_stages_official_data_batch(tmp_path: Path) -> None:
    plan = {
        "schema_version": "self-owned-cg-independent-factorial-plan-v1",
        "source_epoch": "test-independent-generation-epoch",
        "seed_namespace": "test-independent-generation-seed",
        "card_database": "data/raw/EN_Card_Data.csv",
        "public_scan_roots": [],
        "deck_recipes": [
            {
                "id": "recipe-00",
                "spec": "configs/meta_specialist/self_owned_cg_deck_spec_v5_broad_support.json",
                "seed": 20269901,
                "ordinal": 0,
            }
        ],
        "policy_variants": [
            {
                "id": "baseline-00",
                "deck_recipe_id": "recipe-00",
                "overrides": {"lethal_bonus": 12000},
            }
        ],
    }
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    result = run_generation_v1(
        plan=plan_path,
        output=tmp_path / "generated",
        root_source_package=ROOT_PACKAGE,
    )
    assert result["status"] == "STAGED"
    batch = json.loads((tmp_path / "generated/staged/batch_manifest.json").read_text(encoding="utf-8"))
    assert batch["source_epoch"] == "test-independent-generation-epoch"
    source_id = batch["source_ids"][0]
    source = json.loads(
        (tmp_path / "generated/staged" / source_id / "source_manifest.json").read_text(encoding="utf-8")
    )
    assert source["source_kind"] == "self_owned_official_card_data_deck_with_independent_root_policy"
    assert source["parent_policy_sha256"] == "617a23c060084c8b2601800b4f729238563925165f3520628d938eab065aebef"

