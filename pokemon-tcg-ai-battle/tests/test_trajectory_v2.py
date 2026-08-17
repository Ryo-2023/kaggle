from __future__ import annotations

from mage_ptcg.optimization.trajectory_v2 import SemanticProposalGeneratorV2, validate_decisions


def _row(index: int = 0) -> dict[str, object]:
    return {"decision_index": index, "actor_view_digest": "a", "visible_history": [], "turn": 1,
            "legal_action_keys": ["k"], "selected_action_keys": ["k"]}


def test_trace_validator_accepts_complete_public_decision() -> None:
    assert validate_decisions([_row()])["status"] == "TRACE_COMPLETE"


def test_trace_validator_rejects_hidden_outcome_and_illegal_selection() -> None:
    hidden = _row(); hidden["result"] = 1
    illegal = _row(); illegal["selected_action_keys"] = ["other"]
    assert validate_decisions([hidden])["status"] == "TRACE_INVALID"
    assert validate_decisions([illegal])["status"] == "TRACE_INVALID"
