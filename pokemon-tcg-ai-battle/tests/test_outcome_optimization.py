from __future__ import annotations

from pathlib import Path

import pytest

from main import read_deck_csv
from mage_ptcg.optimization.outcome import (OutcomeContractError, PolicyParameters, ProposalMixtureController,
                                             baseline_policy, cem_candidates, frozen_schedule)


def _deck() -> list[int]:
    return list(read_deck_csv(Path("deck.csv")))


def _observation(*, select_type: int = 0) -> dict[str, object]:
    card = {"id": 1, "serial": 0, "playerIndex": 0, "hp": 100, "maxHp": 100, "appearThisTurn": False, "energies": [], "energyCards": [], "tools": [], "preEvolution": []}
    player = {"active": [], "asleep": False, "bench": [], "benchMax": 5, "burned": False, "confused": False, "deckCount": 53, "discard": [], "hand": [card], "handCount": 1, "paralyzed": False, "poisoned": False, "prize": [object() for _ in range(6)]}
    return {"current": {"energyAttached": False, "firstPlayer": 0, "players": [player, player], "result": -1, "retreated": False, "stadium": [], "stadiumPlayed": False, "supporterPlayed": False, "turn": 2, "turnActionCount": 3, "yourIndex": 0}, "select": {"context": 0, "maxCount": 1, "minCount": 1, "option": [{"type": 14}, {"type": 13, "attackId": 1}], "type": select_type}, "step": 7}


def test_schema_hash_migration_and_malformed_rejection() -> None:
    policy = baseline_policy(_deck(), deck_id="current")
    assert PolicyParameters.from_payload(policy.payload()).config_hash == policy.config_hash
    legacy = policy.payload() | {"schema_version": 0}
    assert PolicyParameters.migrate(legacy).schema_version == 1
    with pytest.raises(OutcomeContractError):
        PolicyParameters.from_payload({"schema_version": 1})
    with pytest.raises(OutcomeContractError):
        PolicyParameters(**{**policy.payload(), "source_weights": {"rule": 5., "family": 0., "primitive": 0.}}).validate()


def test_controller_preserves_rule_and_tracks_legal_primitive_divergence() -> None:
    deck = _deck(); candidates = cem_candidates(deck=deck, deck_id="current", generation=0, seed=20260725, count=12)
    controller = ProposalMixtureController(candidates[2], deck)
    selected = controller.choose(_observation())
    event = controller.events[-1]
    assert selected == [0] and event.rule_action == (1,) and event.divergence
    assert "rule" in event.proposal_actions and event.error_fallback is False
    assert event.opponent_posterior["families"] == {"UNKNOWN": 1.0}


def test_controller_delegates_on_unsupported_selection_without_error() -> None:
    deck = _deck(); controller = ProposalMixtureController(baseline_policy(deck, deck_id="current"), deck)
    selected = controller.choose(_observation(select_type=99))
    event = controller.events[-1]
    assert selected == list(event.rule_action)
    assert event.planned_rule_delegation and not event.error_fallback


def test_frozen_schedule_is_balanced_and_cem_elite_update_is_deterministic() -> None:
    schedule = frozen_schedule(split="search", games=16, deck_id="current", batch_id="fixed")
    assert [slot.side for slot in schedule].count(0) == [slot.side for slot in schedule].count(1) == 8
    assert [slot.opponent for slot in schedule].count("rule") == [slot.opponent for slot in schedule].count("family") == 8
    first = cem_candidates(deck=_deck(), deck_id="current", generation=0, seed=7, count=12)
    one = cem_candidates(deck=_deck(), deck_id="current", generation=1, seed=7, count=12, parent_id=first[2].candidate_id, elites=[first[2]])
    two = cem_candidates(deck=_deck(), deck_id="current", generation=1, seed=7, count=12, parent_id=first[2].candidate_id, elites=[first[2]])
    assert [item.config_hash for item in one] == [item.config_hash for item in two]
    assert one[0].optimizer_provenance["elite_center"]["primitive"] == first[2].source_weights["primitive"]
