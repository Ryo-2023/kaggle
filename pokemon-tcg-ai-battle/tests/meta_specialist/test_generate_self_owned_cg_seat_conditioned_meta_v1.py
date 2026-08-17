from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.generate_self_owned_cg_seat_conditioned_meta_v1 import (
    PLAN_SCHEMA,
    load_seat_conditioned_plan_v1,
)


def _write_plan(path: Path, *, overrides: dict[str, object] | None = None) -> None:
    payload = {
        "schema_version": PLAN_SCHEMA,
        "source_epoch": "cg-seat-conditioned-test-20260816",
        "seed_namespace": "cg-seat-conditioned-test-seed-v1",
        "card_database": "data/raw/EN_Card_Data.csv",
        "public_scan_roots": ["runs"],
        "deck_recipes": [
            {
                "id": "recipe-a",
                "spec": "configs/meta_specialist/self_owned_cg_deck_spec_v18_cross_lineage_fire.json",
                "seed": 2026081601,
                "ordinal": 201,
            }
        ],
        "policy_variants": [
            {
                "id": "variant-a",
                "deck_recipe_id": "recipe-a",
                "overrides": overrides or {"seat0_attack_bonus": 12000},
            }
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_plan_loader_normalizes_config_and_paths(tmp_path: Path) -> None:
    path = tmp_path / "plan.json"
    _write_plan(path)
    plan = load_seat_conditioned_plan_v1(path)
    assert plan["schema_version"] == PLAN_SCHEMA
    assert plan["policy_variants"][0]["config"]["seat0_attack_bonus"] == 12000
    assert Path(plan["card_database"]).is_file()


def test_plan_loader_rejects_invalid_override(tmp_path: Path) -> None:
    path = tmp_path / "plan.json"
    _write_plan(path, overrides={"seat0_attack_bonus": 999999})
    with pytest.raises(ValueError, match="invalid overrides"):
        load_seat_conditioned_plan_v1(path)
