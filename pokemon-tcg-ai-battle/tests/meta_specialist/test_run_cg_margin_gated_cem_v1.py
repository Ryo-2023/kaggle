from __future__ import annotations

from scripts.run_cg_margin_gated_cem_v1 import _aggregate_pair
from mage_ptcg.meta_specialist.cg_p1_margin_gated_renderer_v1 import MarginGatedConfig


def _row(policy_id: str, block_id: str, outcome: str, opponent_id: str) -> dict[str, object]:
    return {
        "policy_id": policy_id,
        "block_id": block_id,
        "outcome": outcome,
        "opponent_id": opponent_id,
        "seat": 0,
    }


def test_aggregate_pair_keeps_control_in_same_block() -> None:
    rows = [
        _row("candidate", "b0", "win", "opp"),
        _row("cg-margin-gated-p1-control", "b0", "loss", "opp"),
        _row("cg-margin-gated-p1-control", "b1", "win", "opp"),
        _row("cg-margin-gated-p1-control", "b1", "win", "opp"),
    ]
    result = _aggregate_pair(
        rows,
        candidate_id="candidate",
        config=MarginGatedConfig.default(),
        weights={"opp": 1.0},
        block_id="b0",
    )
    assert result["candidate"]["requested_games"] == 1
    assert result["control"]["requested_games"] == 1
    assert result["delta_objective"] == 1.0
