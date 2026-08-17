from __future__ import annotations

import pytest

from mage_ptcg.meta_specialist.cg_p1_parameterization_v1 import P1ParameterConfig
from mage_ptcg.opponent_ingest.adversarial_source_cem_v1 import (
    AdversarialSourceError,
    aggregate_source_rows_v1,
    build_source_pool_row_v1,
    source_candidate_id_v1,
)


def test_source_candidate_id_is_domain_separated_and_deterministic() -> None:
    config = P1ParameterConfig.default()

    first = source_candidate_id_v1(config, generation=0, index=0)
    second = source_candidate_id_v1(config, generation=0, index=0)

    assert first == second
    assert first.startswith("adversarial-source-g00-c00-")
    assert "cg-p1-cem-g00-c00-" not in first


def test_source_objective_uses_only_terminal_outcome_and_requires_seat_safety() -> None:
    rows = [
        {"policy_id": "source", "opponent_id": "p1", "seat": 0, "outcome": "win"},
        {"policy_id": "source", "opponent_id": "p1", "seat": 0, "outcome": "loss"},
        {"policy_id": "source", "opponent_id": "p1", "seat": 1, "outcome": "draw"},
        {"policy_id": "source", "opponent_id": "p1", "seat": 1, "outcome": "draw"},
    ]

    result = aggregate_source_rows_v1(rows, candidate_policy_id="source", opponent_id="p1")

    assert result["wins"] == 1
    assert result["draws"] == 2
    assert result["losses"] == 1
    assert result["faults"] == 0
    assert result["source_score"] == pytest.approx(0.5)
    assert result["valid"] is True
    assert result["action_trace_used"] is False


def test_source_objective_rejects_faults_and_seat_collapse() -> None:
    rows = [
        {"policy_id": "source", "opponent_id": "p1", "seat": 0, "outcome": "win"},
        {"policy_id": "source", "opponent_id": "p1", "seat": 0, "outcome": "win"},
        {"policy_id": "source", "opponent_id": "p1", "seat": 1, "outcome": "fault"},
        {"policy_id": "source", "opponent_id": "p1", "seat": 1, "outcome": "loss"},
    ]

    result = aggregate_source_rows_v1(rows, candidate_policy_id="source", opponent_id="p1")

    assert result["faults"] == 1
    assert result["valid"] is False
    assert result["fault_rate"] == pytest.approx(0.25)


def test_source_pool_row_is_closed_and_local_eval_only() -> None:
    row = build_source_pool_row_v1(
        candidate_id="adversarial-source-g00-c00-test",
        policy_sha256="a" * 64,
        canonical_deck_hash="b" * 64,
        smoke_ok=True,
    )

    assert row == {
        "id": "adversarial-source-g00-c00-test",
        "policy_hash": "a" * 64,
        "canonical_deck_hash": "b" * 64,
        "source": "self_owned_adversarial_source_cem",
        "usage_boundary": "local_eval_only",
        "smoke_ok": True,
    }

    with pytest.raises(AdversarialSourceError, match="lowercase SHA"):
        build_source_pool_row_v1(
            candidate_id="bad",
            policy_sha256="A" * 64,
            canonical_deck_hash="b" * 64,
            smoke_ok=True,
        )
