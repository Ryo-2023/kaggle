from __future__ import annotations

from pathlib import Path

import pytest

from main import read_deck_csv
from mage_ptcg.optimization.contextual_abstention import (
    PRE_REGISTERED_V3, ContextualAbstentionError, ContextualAbstentionParameters,
    build_v3_policy, candidate_lifecycle, cross_fit_contexts, gate_blocks, robust_objective, semantic_signature,
)


def _deck() -> list[int]:
    return list(read_deck_csv(Path("deck.csv")))


def _row(*, block: str, signature: str, result: int = 1, rule: float = 0.) -> dict[str, object]:
    return {"evaluation_block": block, "signature": signature, "game_result": result, "rule_block_return": rule}


def _policy() -> ContextualAbstentionParameters:
    return build_v3_policy(_deck(), [{"signature": semantic_signature(proposal_source="family", phase="OPENING", side=1, action_type="13", select_type="0", opponent_bucket="UNKNOWN"), "status": "STABLE_POSITIVE", "out_of_fold_mean": .2, "games_touched": 16, "distinct_blocks": 4}], policy_id="contextual-abstention-v3-01", runtime_revision=2)


def test_semantic_signature_never_uses_option_index() -> None:
    value = semantic_signature(proposal_source="family", phase="OPENING", side=1, action_type="13", select_type="0", opponent_bucket="UNKNOWN")
    assert value == "family|OPENING|1|13|0|UNKNOWN"


def test_v3_requires_positive_posterior_threshold_and_exact_binding() -> None:
    policy = _policy()
    assert not policy.allowed_context_signatures  # UNKNOWN is evidence-free and is denied at runtime.
    with pytest.raises(ContextualAbstentionError):
        ContextualAbstentionParameters.from_payload(policy.payload() | {"minimum_posterior_confidence": 0.0})
    with pytest.raises(ContextualAbstentionError):
        ContextualAbstentionParameters.from_payload(policy.payload() | {"compatibility_config": {"level": "FAMILY"}})


def test_context_denylist_and_new_config_identity() -> None:
    policy = _policy()
    assert policy.denied_context_signatures
    changed = ContextualAbstentionParameters.from_payload(policy.payload() | {"policy_id": "contextual-abstention-v3-02"})
    assert changed.config_hash != policy.config_hash


def test_lifecycle_rejects_retired_identity_and_marks_old_evidence_development_only() -> None:
    policy = _policy()
    entries = candidate_lifecycle(policy)
    assert {entry.status for entry in entries} >= {"RETIRED_UNCONFIRMED", "RETIRED_CONFIRMATION_GATE_FAIL"}
    with pytest.raises(ContextualAbstentionError):
        candidate_lifecycle(ContextualAbstentionParameters.from_payload(policy.payload() | {"policy_id": "sparse-cem-b-00"}))


def test_cross_fit_holds_out_block_and_keeps_thresholds_immutable() -> None:
    signature = semantic_signature(proposal_source="family", phase="OPENING", side=1, action_type="13", select_type="0", opponent_bucket="CONFIDENT")
    rows = [_row(block=f"b{block}", signature=signature) for block in range(8) for _ in range(2)]
    folds, summaries = cross_fit_contexts(rows)
    assert len(folds) == 8 and all(not row["leakage"] and row["heldout_block"] not in row["development_blocks"] for row in folds)
    assert all(row["thresholds"] == PRE_REGISTERED_V3 for row in folds)
    assert summaries[0]["status"] == "STABLE_POSITIVE"


def test_low_support_and_negative_groups_are_not_selected() -> None:
    signature = semantic_signature(proposal_source="family", phase="OPENING", side=1, action_type="13", select_type="0", opponent_bucket="CONFIDENT")
    rows = [_row(block="b0", signature=signature) for _ in range(2)] + [_row(block=f"b{block}", signature=signature, result=-1) for block in range(1, 8) for _ in range(2)]
    _, summaries = cross_fit_contexts(rows)
    assert summaries[0]["status"] in {"UNSTABLE", "SUPPORT_INSUFFICIENT"}


def _block(delta: float, *, divergence: float = .01, faults: int = 0) -> dict[str, object]:
    candidate = {"faults": faults, "divergence_rate": divergence, "multiple_override_games": 0, "game_count": 32, "runtime_mean": .2, "runtime_max": .5, "side_returns": {"0": .0, "1": .0}}
    return {"delta": delta, "candidate": candidate, "rule": {"side_returns": {"0": .0, "1": .0}}, "safety_pass": faults == 0, "effective_policy_pass": PRE_REGISTERED_V3["min_divergence"] <= divergence <= PRE_REGISTERED_V3["max_divergence"]}


def test_robust_objective_penalizes_worst_block_and_faults() -> None:
    good = robust_objective([_block(.1), _block(.1), _block(.1), _block(.1)])
    bad = robust_objective([_block(.4), _block(.4), _block(.4), _block(-.4, faults=1)])
    assert good["objective"] > bad["objective"]


def test_gate_refuses_semantic_duplicate_and_faults() -> None:
    duplicate = gate_blocks([_block(.1, divergence=0.) for _ in range(4)])
    faulty = gate_blocks([_block(.1, faults=1) for _ in range(4)])
    assert not duplicate["passed"] and not faulty["passed"]
