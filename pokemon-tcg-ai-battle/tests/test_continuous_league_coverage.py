from __future__ import annotations

import pytest

from mage_ptcg.continuous_league.benchmark import ExposureCohort, ExposureSnapshot
from mage_ptcg.continuous_league.catalog import CatalogEntry, CatalogSnapshot
from mage_ptcg.continuous_league.contracts import LeagueContractError, content_id
from mage_ptcg.continuous_league.coverage import ReplayCoverage
from mage_ptcg.policy_learning.r2d3.sequence import R2D3Transition, SequenceBatch


def _hash(value: str) -> str:
    return content_id("coverage-test", value)


def _entry(asset: str, *, policy_hash: str, deck_hash: str, role: str = "BENCHMARK_VISIBLE") -> CatalogEntry:
    return CatalogEntry(
        asset_id=asset,
        policy_id=f"policy-{asset}",
        deck_id=f"deck-{asset}",
        source_id=f"source-{asset}",
        policy_kind="submitted_snapshot",
        runtime_path=f"snapshot:{asset}",
        deck_path=f"{asset}.csv",
        policy_hash=policy_hash,
        deck_hash=deck_hash,
        source_hash=_hash(f"source-{asset}"),
        runtime_config_hash=_hash(f"runtime-{asset}"),
        role=role,
        archetype_id="GRIMMSNARL" if asset == "hard" else "OTHER",
    )


def _step(*, policy_hash: str, deck_hash: str, lineage: str = "remote", family: str = "GRIMMSNARL") -> R2D3Transition:
    return R2D3Transition(
        public_state=(0.0,),
        legal_actions=((0.0,),),
        selected_action=0,
        reward=0.0,
        discount=0.99,
        terminal=False,
        behavior_policy_version=_hash("candidate"),
        behavior_source="continuous_cabt_online",
        opponent_policy_hash=policy_hash,
        opponent_deck_hash=deck_hash,
        opponent_source_lineage=lineage,
        opponent_family=family,
        own_deck_hash=_hash("own"),
    )


def _sequence(sequence_id: str, *steps: R2D3Transition, episode_id: str = "episode") -> SequenceBatch:
    return SequenceBatch(
        burn_in=(), learner=tuple(steps), priority=1.0,
        sequence_id=sequence_id, episode_id=episode_id,
    )


def test_replay_coverage_uses_observed_policy_deck_pairs_not_catalog_membership() -> None:
    known_policy = _hash("known-policy")
    known_deck = _hash("known-deck")
    hard = _entry("hard", policy_hash=known_policy, deck_hash=known_deck)
    same_deck = _entry("same-deck", policy_hash=_hash("other-policy"), deck_hash=known_deck)
    same_policy = _entry("same-policy", policy_hash=known_policy, deck_hash=_hash("other-deck"))
    catalog = CatalogSnapshot.build([hard, same_deck, same_policy])
    coverage = ReplayCoverage.from_sequences(
        replay_dataset_version_id=_hash("replay"),
        population_epoch_id=_hash("population"),
        sequences=[
            _sequence("one", _step(policy_hash=known_policy, deck_hash=known_deck)),
            _sequence("two", _step(policy_hash=known_policy, deck_hash=known_deck), episode_id="episode"),
        ],
    )
    assert coverage.pairs[0].sequence_count == 2
    assert coverage.pairs[0].episode_count == 1
    assert ReplayCoverage.from_dict(coverage.to_dict()) == coverage

    exposure = ExposureSnapshot.from_replay_coverage(coverage=coverage, catalog=catalog)
    assert exposure.classify(hard) == ExposureCohort.EXACT_KNOWN
    assert exposure.classify(same_deck) == ExposureCohort.KNOWN_DECK_NOVEL_POLICY
    assert exposure.classify(same_policy) == ExposureCohort.NOVEL_DECK_KNOWN_POLICY
    assert ExposureSnapshot.from_dict(exposure.to_dict()) == exposure


def test_replay_coverage_rejects_mixed_opponents_inside_learner_sequence() -> None:
    with pytest.raises(LeagueContractError, match="multiple opponent identities"):
        ReplayCoverage.from_sequences(
            replay_dataset_version_id=_hash("replay"),
            population_epoch_id=_hash("population"),
            sequences=[
                _sequence(
                    "bad",
                    _step(policy_hash=_hash("a"), deck_hash=_hash("a")),
                    _step(policy_hash=_hash("b"), deck_hash=_hash("b")),
                )
            ],
        )
