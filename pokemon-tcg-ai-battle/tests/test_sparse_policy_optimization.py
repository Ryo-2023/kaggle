from __future__ import annotations

from pathlib import Path

import pytest

from main import read_deck_csv
from mage_ptcg.optimization.sparse import (PRE_REGISTERED, SparseContractError, SparsePolicyParameters,
                                           SparseProposalController, ablation_population, constrained_candidates,
                                           sparse_baseline)


def _deck() -> list[int]: return list(read_deck_csv(Path("deck.csv")))


def _observation(*, turn: int = 2, selection_type: int = 0) -> dict[str, object]:
    card = {"id": 1, "serial": 0, "playerIndex": 0, "hp": 100, "maxHp": 100, "appearThisTurn": False, "energies": [], "energyCards": [], "tools": [], "preEvolution": []}
    player = {"active": [], "asleep": False, "bench": [], "benchMax": 5, "burned": False, "confused": False, "deckCount": 53, "discard": [], "hand": [card], "handCount": 1, "paralyzed": False, "poisoned": False, "prize": [object() for _ in range(6)]}
    return {"current": {"energyAttached": False, "firstPlayer": 0, "players": [player, player], "result": -1, "retreated": False, "stadium": [], "stadiumPlayed": False, "supporterPlayed": False, "turn": turn, "turnActionCount": 3, "yourIndex": 0}, "select": {"context": 0, "maxCount": 1, "minCount": 1, "option": [{"type": 14}, {"type": 13, "attackId": 1}], "type": selection_type}, "step": 7}


def test_sparse_schema_rejects_rule_mask_and_preserves_fixed_gates() -> None:
    policy = sparse_baseline(_deck())
    assert SparsePolicyParameters.from_payload(policy.payload()).config_hash == policy.config_hash
    with pytest.raises(SparseContractError):
        SparsePolicyParameters(**{**policy.payload(), "allowed_sources": ("rule",)}).validate()
    assert PRE_REGISTERED["max_divergence"] == .20 and PRE_REGISTERED["screen_games_per_candidate"] == 32


def test_sparse_budget_cooldown_and_unsupported_delegation() -> None:
    deck = _deck(); policy = ablation_population(deck)[0]; controller = SparseProposalController(policy, deck)
    controller.choose(_observation(selection_type=99)); assert controller.events[-1].planned_rule_delegation and not controller.events[-1].error_fallback
    for _ in range(5): controller.choose(_observation())
    assert controller.overrides <= policy.maximum_overrides_per_game
    if controller.overrides:
        positions = [event.decision_id for event in controller.events if event.divergence]
        assert all(right - left > policy.override_cooldown_decisions for left, right in zip(positions, positions[1:]))


def test_ablation_and_constrained_population_are_sparse_and_reproducible() -> None:
    rows = ablation_population(_deck())
    assert len(rows) == 8 and len({row.config_hash for row in rows}) == 8
    assert all(row.maximum_expected_divergence <= .20 and row.maximum_overrides_per_game <= 2 for row in rows)
    first = constrained_candidates(_deck(), rows[0]); second = constrained_candidates(_deck(), rows[0])
    assert [row.config_hash for row in first] == [row.config_hash for row in second]
    assert all(row.parent_id == rows[0].candidate_id for row in first)
