from __future__ import annotations

from pathlib import Path

from mage_ptcg.meta_specialist.cg_p2_context_surface_v1 import P2ContextConfig
from mage_ptcg.meta_specialist.cg_weekend_split_v1 import load_weekend_split
from scripts.run_cg_p2_context_confirmation_v1 import (
    build_confirmation_games,
    summarize_confirmation_rows,
)


ROOT = Path(__file__).resolve().parents[2]
SPLIT = load_weekend_split(ROOT / "configs/meta_specialist/cg_weekend_splits_v1.json")
P2_PACKAGE = ROOT / "runs/final-sprint-autonomous/cg-p1-robust-g01-submission-package-v1/package"


def test_confirmation_uses_a_fresh_seed_grid_with_shared_control_strata() -> None:
    games = build_confirmation_games(
        candidate_package=P2_PACKAGE,
        candidate_id="candidate",
        config=P2ContextConfig(near_lethal_attack_bonus=12000),
        split=SPLIT,
        control_package=P2_PACKAGE,
        reference_ids=SPLIT.train_blocks[0],
        base_seed=48386000,
        repetitions=1,
    )
    candidate = [game for game in games if game.metadata["arm_role"] == "candidate"]
    control = [game for game in games if game.metadata["arm_role"] == "p2_control"]
    assert len(candidate) == len(control) == 8
    assert min(game.seed for game in games) == 48386000
    assert {(game.metadata["pair_key"], game.seed) for game in candidate} == {
        (game.metadata["pair_key"], game.seed) for game in control
    }


def test_confirmation_cannot_grant_promotion_on_reused_meta() -> None:
    rows = [
        {"policy_id": "candidate", "opponent_id": "unused", "outcome": "win", "seat": 0},
        {"policy_id": "candidate", "opponent_id": "unused", "outcome": "win", "seat": 1},
        {"policy_id": "control", "opponent_id": "unused", "outcome": "loss", "seat": 0},
        {"policy_id": "control", "opponent_id": "unused", "outcome": "loss", "seat": 1},
    ]
    summary = summarize_confirmation_rows(
        rows,
        candidate_id="candidate",
        control_id="control",
        weights={"unused": 1.0},
        config=P2ContextConfig.default(),
        meta_provenance="reused_meta_train",
    )
    assert summary["delta_points"] > 0
    assert summary["decision"] == "NOT_PROMOTABLE_REUSED_META"
    assert summary["promotion_authority"] is False

