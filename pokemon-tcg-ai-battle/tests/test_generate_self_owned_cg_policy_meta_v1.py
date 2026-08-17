from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.generate_self_owned_cg_policy_meta_v1 import (
    SCHEMA,
    SelfOwnedCgPolicyMetaV1Error,
    load_factorial_plan_v1,
)


ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "configs/meta_specialist/self_owned_cg_policy_factorial_v1.json"


def test_factorial_plan_loads_unique_recipes_and_bounded_configs() -> None:
    plan = load_factorial_plan_v1(PLAN)
    assert plan["schema_version"] == SCHEMA
    assert len(plan["deck_recipes"]) == 8
    assert len(plan["policy_variants"]) == 8
    assert len({row["id"] for row in plan["deck_recipes"]}) == 8
    assert len({row["id"] for row in plan["policy_variants"]}) == 8
    assert all(len(row["config_sha256"]) == 64 for row in plan["policy_variants"])


def test_factorial_plan_rejects_unknown_parameter(tmp_path: Path) -> None:
    payload = json.loads(PLAN.read_text(encoding="utf-8"))
    payload["policy_variants"][0]["overrides"]["not_a_p1_parameter"] = 1
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(SelfOwnedCgPolicyMetaV1Error, match="invalid overrides"):
        load_factorial_plan_v1(path)


def test_factorial_plan_rejects_unknown_recipe_reference(tmp_path: Path) -> None:
    payload = json.loads(PLAN.read_text(encoding="utf-8"))
    payload["policy_variants"][0]["deck_recipe_id"] = "missing"
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(SelfOwnedCgPolicyMetaV1Error, match="unknown deck recipe"):
        load_factorial_plan_v1(path)
