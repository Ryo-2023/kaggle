from __future__ import annotations

from pathlib import Path

from mage_ptcg.meta_specialist.cg_p1_parameterization_v1 import P1ParameterConfig
from mage_ptcg.meta_specialist.cg_weekend_split_v1 import load_weekend_split
from scripts.run_cg_candidate_confirmation_v1 import (
    build_confirmation_games,
    summarize_confirmation_rows,
)


ROOT = Path(__file__).resolve().parents[2]
SPLIT = load_weekend_split(ROOT / "configs/meta_specialist/cg_weekend_splits_v1.json")
P1_PACKAGE = (
    ROOT
    / "runs/final-sprint-autonomous/cg-policy-screen-v1-retry-safe4-20260814"
    / "candidates/cg-lethal-target-v1/package"
)
P2_PACKAGE = ROOT / "runs/final-sprint-autonomous/cg-p1-robust-g01-submission-package-v1/package"


def test_confirmation_games_pair_candidate_and_control_on_same_strata() -> None:
    games = build_confirmation_games(
        candidate_package=P1_PACKAGE,
        candidate_id="candidate",
        config_sha256=P1ParameterConfig.default().config_sha256(),
        split=SPLIT,
        control_package=P2_PACKAGE,
        reference_ids=SPLIT.train_blocks[0],
        base_seed=123,
        repetitions=1,
    )

    assert len(games) == 16
    candidate = [game for game in games if game.metadata["arm_role"] == "candidate"]
    control = [game for game in games if game.metadata["arm_role"] == "p1_control"]
    assert {game.metadata["pair_key"] for game in candidate} == {
        game.metadata["pair_key"] for game in control
    }
    assert [game.seed for game in candidate] == [game.seed for game in control]


def test_confirmation_summary_is_research_only_and_requires_fault_free_rows() -> None:
    rows = [
        {"policy_id": "candidate", "opponent_id": "unused", "outcome": "win", "seat": 0},
        {"policy_id": "candidate", "opponent_id": "unused", "outcome": "loss", "seat": 1},
        {"policy_id": "control", "opponent_id": "unused", "outcome": "draw", "seat": 0},
        {"policy_id": "control", "opponent_id": "unused", "outcome": "loss", "seat": 1},
    ]

    summary = summarize_confirmation_rows(
        rows,
        candidate_id="candidate",
        control_id="control",
        weights={"unused": 1.0},
        config=P1ParameterConfig.default(),
    )

    assert summary["delta_points"] == 25.0
    assert summary["research_only"] is True
    assert summary["promotion_authority"] is False
