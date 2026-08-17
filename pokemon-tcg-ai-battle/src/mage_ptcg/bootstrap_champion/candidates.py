"""Compatibility-filtered deck × policy candidate construction."""

from __future__ import annotations

from mage_ptcg.continuous_league.contracts import content_id, require_sha256

from .contracts import BootstrapContractError, DeckAsset, DeckCompatibility, JointCandidate, PolicyAsset
from .intake import BootstrapAssetRegistry


def is_compatible(deck: DeckAsset, policy: PolicyAsset) -> bool:
    return (
        policy.compatibility is DeckCompatibility.ARBITRARY_LEGAL_DECK
        or policy.exact_deck_hash == deck.deck_hash
    )


def build_joint_candidates(
    registry: BootstrapAssetRegistry,
    *,
    simulator_contract_hash: str,
) -> tuple[JointCandidate, ...]:
    try:
        require_sha256(simulator_contract_hash, "simulator_contract_hash")
    except ValueError as exc:
        raise BootstrapContractError(str(exc)) from exc
    values = [
        JointCandidate(deck, policy, simulator_contract_hash)
        for deck in registry.decks
        for policy in registry.policies
        if is_compatible(deck, policy)
    ]
    unique = {candidate.candidate_id: candidate for candidate in values}
    return tuple(unique[key] for key in sorted(unique))


def candidate_registry_id(
    registry: BootstrapAssetRegistry,
    candidates: tuple[JointCandidate, ...],
) -> str:
    return content_id(
        "bootstrap-candidate-registry-v1",
        {
            "asset_registry_id": registry.asset_registry_id,
            "candidate_ids": [candidate.candidate_id for candidate in candidates],
        },
    )
