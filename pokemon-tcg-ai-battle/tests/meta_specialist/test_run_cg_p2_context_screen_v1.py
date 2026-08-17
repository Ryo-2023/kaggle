from __future__ import annotations

from pathlib import Path

from mage_ptcg.meta_specialist.cg_p2_context_surface_v1 import P2ContextConfig
from mage_ptcg.meta_specialist.cg_weekend_split_v1 import load_weekend_split
from scripts.run_cg_p2_context_screen_v1 import (
    build_context_paired_games,
    summarize_context_rows,
)


ROOT = Path(__file__).resolve().parents[2]
SPLIT = load_weekend_split(ROOT / "configs/meta_specialist/cg_weekend_splits_v1.json")
P2_PACKAGE = ROOT / "runs/final-sprint-autonomous/cg-p1-robust-g01-submission-package-v1/package"


def test_context_games_reuse_exact_seed_and_pair_strata_for_control() -> None:
    games = build_context_paired_games(
        candidate_package=P2_PACKAGE,
        candidate_id="candidate",
        config_sha256=P2ContextConfig.default().config_sha256(),
        split=SPLIT,
        control_package=P2_PACKAGE,
        reference_ids=SPLIT.train_blocks[0],
        base_seed=123,
        repetitions=1,
    )
    candidate = [game for game in games if game.metadata["arm_role"] == "candidate"]
    control = [game for game in games if game.metadata["arm_role"] == "p2_control"]
    assert len(candidate) == len(control) == 8
    assert {(game.metadata["pair_key"], game.seed) for game in candidate} == {
        (game.metadata["pair_key"], game.seed) for game in control
    }
    assert all(game.metadata["context_schema"] == "cg-p2-context-screen-v1" for game in games)


def test_context_summary_is_research_only_and_keeps_faults_in_gate() -> None:
    rows = [
        {"policy_id": "candidate", "opponent_id": "unused", "outcome": "win", "seat": 0},
        {"policy_id": "candidate", "opponent_id": "unused", "outcome": "fault", "seat": 1},
        {"policy_id": "control", "opponent_id": "unused", "outcome": "draw", "seat": 0},
        {"policy_id": "control", "opponent_id": "unused", "outcome": "loss", "seat": 1},
    ]
    summary = summarize_context_rows(
        rows,
        candidate_id="candidate",
        control_id="control",
        weights={"unused": 1.0},
        config=P2ContextConfig.default(),
    )
    assert summary["faults"] == 1
    assert summary["decision"] == "NOT_PROMOTABLE"
    assert summary["promotion_authority"] is False

