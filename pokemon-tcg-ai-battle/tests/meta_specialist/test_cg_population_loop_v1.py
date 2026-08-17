from __future__ import annotations

import json
from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.cg_population_loop_v1 import (
    CgPopulationLoopError,
    build_population_loop_plan_v1,
    load_population_loop_checkpoint_v1,
    run_population_loop_v1,
    save_population_loop_checkpoint_v1,
    stage_seed_v1,
)


def test_stage_seeds_are_disjoint_and_deterministic() -> None:
    assert stage_seed_v1(base_seed=40700000, stage_games=96) == 40700000
    assert stage_seed_v1(base_seed=40700000, stage_games=384) != stage_seed_v1(
        base_seed=40700000, stage_games=96
    )
    assert stage_seed_v1(base_seed=40700000, stage_games=384) == stage_seed_v1(
        base_seed=40700000, stage_games=384
    )


def test_loop_plan_is_bounded_and_authority_free() -> None:
    plan = build_population_loop_plan_v1(
        base_seed=40700000,
        start_stage_games=96,
        max_stage_games=768,
        phase="DECK_FIXED_LONG",
    )
    assert plan.stage_games == (96, 384, 768)
    assert plan.authority["training"] is False
    assert plan.authority["longrun"] is False
    assert plan.research_only is True


def test_checkpoint_roundtrip_is_no_clobber(tmp_path: Path) -> None:
    checkpoint = {
        "schema_version": "meta-specialist-cg-population-loop-checkpoint-v1",
        "status": "STOP",
        "completed_stage_games": 96,
        "decision": "NOT_PROMOTABLE",
        "authority": {
            "training": False,
            "promotion": False,
            "submission": False,
            "longrun": False,
        },
        "research_only": True,
    }
    target = tmp_path / "checkpoint.json"
    digest = save_population_loop_checkpoint_v1(checkpoint, target)
    loaded = load_population_loop_checkpoint_v1(target)
    assert digest
    assert loaded == checkpoint
    with pytest.raises(FileExistsError):
        save_population_loop_checkpoint_v1(checkpoint, target)


def test_checkpoint_rejects_authority(tmp_path: Path) -> None:
    target = tmp_path / "bad.json"
    payload = {
        "schema_version": "meta-specialist-cg-population-loop-checkpoint-v1",
        "status": "STOP",
        "completed_stage_games": 96,
        "decision": "NOT_PROMOTABLE",
        "authority": {"training": True, "promotion": False, "submission": False, "longrun": False},
        "research_only": True,
    }
    target.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(CgPopulationLoopError):
        load_population_loop_checkpoint_v1(target)


def test_loop_stops_at_first_non_promotable_stage(tmp_path: Path) -> None:
    calls: list[int] = []

    def stage_runner(**kwargs: object) -> dict[str, object]:
        stage = int(kwargs["stage_games"])
        calls.append(stage)
        decision = "POSITIVE_CONTINUE" if stage == 96 else "NOT_PROMOTABLE"
        return {
            "status": "COMPLETE",
            "summary": {"decision": decision, "stage_games": stage},
        }

    plan = build_population_loop_plan_v1(
        base_seed=40700000,
        start_stage_games=96,
        max_stage_games=768,
        phase="DECK_FIXED_LONG",
    )
    result = run_population_loop_v1(
        plan=plan,
        stage_runner=stage_runner,
        output_root=tmp_path / "loop",
        execute=True,
    )
    assert calls == [96, 384]
    assert result["status"] == "STOP"
    assert result["last_decision"] == "NOT_PROMOTABLE"
    assert len(result["checkpoints"]) == 2
