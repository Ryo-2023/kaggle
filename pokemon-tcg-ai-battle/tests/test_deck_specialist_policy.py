from __future__ import annotations

from pathlib import Path

import pytest

from main import read_deck_csv
from mage_ptcg.optimization.deck_specialist import (DeckBoundPolicy, DeckCompatibility, compile_overlay,
                                                     current_policy, joint_candidate)
from mage_ptcg.optimization.outcome import deck_digest, mutate_deck

# The 2026-07-25-era R2D3/offline-scaleup artifacts these tests read were
# intentionally deleted on 2026-08-03: that model line performed poorly and was
# superseded by `mage_ptcg.meta_specialist`, so the evidence was not worth its
# 58 GB.  The tests are kept rather than removed so the contract they encode
# stays on record, but they cannot run without their inputs.  If a future line
# regenerates equivalent artifacts, delete this guard rather than the tests.
_REQUIRED_ARTIFACT = Path(
    "/home/bfe-lab-ono/kaggle/handoff-artifacts/robust-sparse-policy-optimization-v2-20260725_204500"
)
pytestmark = pytest.mark.skipif(
    not _REQUIRED_ARTIFACT.is_dir(),
    reason="superseded sparse-policy artifacts were intentionally deleted (2026-08-03)",
)



def _deck() -> list[int]: return list(read_deck_csv(Path("deck.csv")))


def test_joint_identity_separates_same_policy_across_decks() -> None:
    deck = _deck(); policy = current_policy(deck); current = joint_candidate(deck, deck_id="current", policy=policy)
    mutation = mutate_deck(deck); mutated_policy = type(policy).from_payload(policy.payload() | {"deck_id": "mutation", "deck_hash": deck_digest(mutation)})
    other = joint_candidate(mutation, deck_id="mutation", policy=mutated_policy)
    assert current.joint_hash != other.joint_hash and current.deck_hash != other.deck_hash


def test_exact_deck_guard_delegates_incompatible_deck_without_error() -> None:
    deck = _deck(); policy = current_policy(deck); candidate = joint_candidate(deck, deck_id="current", policy=policy)
    bound = DeckBoundPolicy(policy, mutate_deck(deck), candidate.compatibility)
    assert not bound.compatible and bound.reason == "DECK_HASH_MISMATCH"


def test_overlay_is_exact_deck_bound_and_preserves_policy_identity() -> None:
    deck = _deck(); policy = current_policy(deck); overlay = compile_overlay(deck, policy, support=1)
    bound = overlay.bind(deck, policy)
    assert overlay.source_policy_hash == policy.config_hash and bound.compatible
    assert not overlay.bind(mutate_deck(deck), policy).compatible
