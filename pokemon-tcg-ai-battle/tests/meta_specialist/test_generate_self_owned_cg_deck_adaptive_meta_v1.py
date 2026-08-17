from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.generate_self_owned_cg_deck_adaptive_meta_v1 import (
    DeckAdaptivePlanError,
    load_deck_adaptive_plan_v1,
    run_generation_v1,
)


ROOT = Path(__file__).resolve().parents[2]
PLAN = ROOT / "configs/meta_specialist/self_owned_cg_deck_adaptive_family_v1.json"
RUNTIME_PACKAGE = (
    ROOT
    / "runs/final-sprint-autonomous/cg-policy-screen-v1-retry-safe4-20260814"
    / "candidates/cg-lethal-target-v1/package"
)


def test_loader_rejects_duplicate_variant_ids(tmp_path: Path) -> None:
    payload = json.loads(PLAN.read_text(encoding="utf-8"))
    payload["policy_variants"] = [payload["policy_variants"][0], payload["policy_variants"][0]]
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(DeckAdaptivePlanError, match="duplicate policy variant"):
        load_deck_adaptive_plan_v1(duplicate)


def test_generation_creates_distinct_staged_packages(tmp_path: Path) -> None:
    result = run_generation_v1(plan=PLAN, output=tmp_path / "epoch", runtime_package=RUNTIME_PACKAGE)
    assert result["status"] == "STAGED"
    assert result["source_count"] == 6
    assert result["deck_count"] == 6
    assert result["policy_count"] == 6
    assert (tmp_path / "epoch/staged/pool_manifest.json").is_file()
