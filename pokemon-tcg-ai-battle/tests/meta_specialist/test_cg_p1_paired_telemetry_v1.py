from __future__ import annotations

import pytest

from mage_ptcg.meta_specialist.cg_p1_paired_telemetry_v1 import (
    PAIRED_TELEMETRY_SCHEMA,
    PairedTelemetryError,
    analyze_paired_public_telemetry_v1,
    pair_public_decisions_v1,
)


def _row(*, game_id: str, action_type: int, option_count: int = 2, turn: int = 4) -> dict[str, object]:
    return {
        "schema_version": "cg-public-telemetry-v1",
        "record_type": "decision",
        "game_id": game_id,
        "candidate_id": "cg-lethal-target-v1",
        "seat": 0,
        "decision_index": 0,
        "step": 1,
        "turn": turn,
        "turn_action_count": 1,
        "options": [
            {"option_index": 0, "type": action_type, "type_name": None, "fields": {}},
            {"option_index": 1, "type": 3, "type_name": None, "fields": {}},
        ][:option_count],
        "action": [0],
        "select": {"type": 1, "context": 2, "min_count": 0, "max_count": option_count, "option_count": option_count},
        "board": {"energy_attached": True, "retreated": False, "stadium_played": False, "supporter_played": False},
        "self": {"active": [{"fields": {"id": 678, "hp": 220, "maxHp": 220, "energies_count": 2}}], "bench": [], "status": {}},
        "opponent": {"active": [{"fields": {"id": 721, "hp": 250, "maxHp": 300, "energies_count": 1}}], "bench": [], "status": {}},
    }


def test_pairs_only_same_public_prefix_and_records_operation_difference() -> None:
    p1 = [_row(game_id="g0", action_type=3)]
    p0 = [_row(game_id="g0", action_type=4)]
    p1[0]["action"] = [0]
    p0[0]["action"] = [0]
    pairs = pair_public_decisions_v1(
        p1,
        p0,
        p1_outcomes={"g0": "loss"},
        p0_outcomes={"g0": "win"},
    )
    assert len(pairs) == 1
    assert pairs[0].p1_operation == "TYPE_3"
    assert pairs[0].p0_operation == "TYPE_4"
    assert pairs[0].p1_outcome == "loss"
    assert pairs[0].p0_outcome == "win"


def test_pairing_fails_closed_on_private_key() -> None:
    p1 = [_row(game_id="g0", action_type=3)]
    p0 = [_row(game_id="g0", action_type=3)]
    p1[0]["opponent"] = {"hand": []}
    with pytest.raises(PairedTelemetryError, match="forbidden|private"):
        pair_public_decisions_v1(p1, p0, p1_outcomes={"g0": "loss"}, p0_outcomes={"g0": "win"})


def test_analyzer_requires_support_and_mixed_sign_before_candidate_screen() -> None:
    pairs = []
    for i in range(3):
        p1 = _row(game_id=f"g{i}", action_type=3)
        p0 = _row(game_id=f"g{i}", action_type=4)
        p1["action"] = [0]
        p0["action"] = [0]
        pairs.extend(
            pair_public_decisions_v1(
                [p1], [p0],
                p1_outcomes={f"g{i}": "loss"},
                p0_outcomes={f"g{i}": "win"},
            )
        )
    result = analyze_paired_public_telemetry_v1(pairs, min_support=4, max_candidates=3)
    assert result["schema_version"] == PAIRED_TELEMETRY_SCHEMA
    assert result["action_differences"] == 3
    assert result["ready_for_candidate_screen"] is False
    assert "support" in " ".join(result["reasons"])
