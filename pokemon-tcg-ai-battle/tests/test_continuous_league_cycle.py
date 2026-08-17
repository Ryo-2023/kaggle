from __future__ import annotations

from mage_ptcg.continuous_league.catalog import CatalogEntry, CatalogSnapshot
from mage_ptcg.continuous_league.contracts import content_id
from mage_ptcg.continuous_league.coverage import ReplayCoverage
from mage_ptcg.continuous_league.cycle import CyclePlan
from mage_ptcg.policy_learning.r2d3.sequence import R2D3Transition, SequenceBatch


def _hash(value: str) -> str:
    return content_id("cycle-test", value)


def _entry(asset: str) -> CatalogEntry:
    return CatalogEntry(
        asset_id=asset,
        policy_id=asset,
        deck_id=asset,
        source_id=asset,
        policy_kind="submitted_snapshot",
        runtime_path=f"snapshot:{asset}",
        deck_path=f"{asset}.csv",
        policy_hash=_hash(f"policy:{asset}"),
        deck_hash=_hash(f"deck:{asset}"),
        source_hash=_hash(f"source:{asset}"),
        runtime_config_hash=_hash(f"runtime:{asset}"),
        role="TRAINING_ACTIVE",
        archetype_id="TEST",
    )


def _coverage(entry: CatalogEntry) -> ReplayCoverage:
    step = R2D3Transition(
        public_state=(0.0,), legal_actions=((0.0,),), selected_action=0,
        reward=0.0, discount=0.0, terminal=True,
        behavior_policy_version=_hash("candidate"), behavior_source="cabt",
        opponent_policy_hash=entry.policy_hash, opponent_deck_hash=entry.deck_hash,
        opponent_source_lineage=entry.source_id, opponent_family="TEST",
        own_deck_hash=_hash("own"),
    )
    return ReplayCoverage.from_sequences(
        replay_dataset_version_id=_hash("replay"), population_epoch_id=_hash("epoch"),
        sequences=[SequenceBatch((), (step,), 1.0, "sequence", "episode")],
    )


def test_cycle_plan_collects_only_new_pair_by_default() -> None:
    existing, new = _entry("existing"), _entry("new")
    catalog = CatalogSnapshot.build([existing, new])
    plan = CyclePlan.build(
        catalog=catalog, coverage=_coverage(existing), roles=["TRAINING_ACTIVE"],
        bootstrap_episodes_per_new_opponent=32,
    )
    assert plan.missing_opponent_instance_ids == (new.opponent_instance_id,)
    assert plan.opponent_episode_quotas == ((new.opponent_instance_id, 32),)
    assert plan.to_dict()["collection_required"] is True
