from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.generate_self_owned_cg_deck_conditioned_adversarial_meta_v1 import (
    PLAN_SCHEMA,
    DeckConditionedAdversarialPlanError,
    load_deck_conditioned_adversarial_plan_v1,
)


def _plan(tmp_path: Path) -> Path:
    card_db = tmp_path / "cards.csv"
    card_db.write_text("id,name\n6,Fighting\n", encoding="utf-8")
    spec = tmp_path / "spec.json"
    spec.write_text("{}\n", encoding="utf-8")
    (tmp_path / "p1").mkdir()
    payload = {
        "schema_version": PLAN_SCHEMA,
        "source_epoch": "epoch-test",
        "seed_namespace": "seed-test",
        "card_database": str(card_db),
        "p1_source_package": str(tmp_path / "p1"),
        "public_scan_roots": [str(tmp_path)],
        "deck_recipes": [
            {"id": "deck-a", "spec": str(spec), "seed": 1, "ordinal": 0},
        ],
        "policy_variants": [
            {"id": "policy-a", "deck_recipe_id": "deck-a", "overrides": {}},
        ],
    }
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    return path


def test_plan_loader_accepts_canonical_recipe_and_variant(tmp_path: Path) -> None:
    loaded = load_deck_conditioned_adversarial_plan_v1(_plan(tmp_path))
    assert loaded["source_epoch"] == "epoch-test"
    assert loaded["deck_recipes"][0]["id"] == "deck-a"
    assert loaded["policy_variants"][0]["config_sha256"]


def test_plan_loader_rejects_unknown_top_level_field(tmp_path: Path) -> None:
    path = _plan(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["unexpected"] = True
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(DeckConditionedAdversarialPlanError, match="schema or fields"):
        load_deck_conditioned_adversarial_plan_v1(path)
