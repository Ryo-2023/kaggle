from __future__ import annotations

from pathlib import Path

import pytest

from mage_ptcg.continuous_league.catalog import CatalogEntry, CatalogSnapshot
from mage_ptcg.continuous_league.collector import CollectionRequest, collect_experience
from mage_ptcg.continuous_league.contracts import content_id
from mage_ptcg.policy_learning.r2d3.online_collection import (
    MixtureManifest,
    MixtureMember,
)


def _hash(value: str) -> str:
    return content_id("test", value)


class _Policy:
    def __init__(self) -> None:
        self.traces = [
            {
                "state": [0.0] * 128,
                "actions": [[0.0] * 64, [0.1] * 64],
                "selected_action": 0,
                "trainable_single_action": True,
            }
        ]


class _Executor:
    class Runtime:
        manifest = {"deck_hash": _hash("own-deck")}

    runtime_policy = Runtime()

    def __init__(self, *, fail_on_call: int | None = None) -> None:
        self.calls = 0
        self.fail_on_call = fail_on_call

    def execute(self, game, _entry):
        self.calls += 1
        if self.calls == self.fail_on_call:
            raise RuntimeError("intentional collection interruption")
        return (
            {
                "outcome": "win",
                "candidate_side": 0 if game.seat == "subject_first" else 1,
            },
            _Policy(),
        )


def test_collection_generates_both_seats_and_complete_chunk(tmp_path: Path) -> None:
    entry = CatalogEntry(
        asset_id="rule",
        policy_id="rule-policy",
        deck_id="rule-deck",
        source_id="rule-source",
        policy_kind="rule_v0",
        runtime_path="builtin:rule_v0",
        deck_path="deck.csv",
        policy_hash=_hash("rule-policy"),
        deck_hash=_hash("rule-deck"),
        source_hash=_hash("rule-source"),
        runtime_config_hash=_hash("rule-config"),
        role="TRAINING_ACTIVE",
        archetype_id="RULE",
    )
    catalog = CatalogSnapshot.build([entry])
    mixture = MixtureManifest.build(
        [
            MixtureMember(
                opponent_policy_id=entry.opponent_instance_id,
                probability=1.0,
                policy_hash=entry.policy_hash,
                source_lineage=entry.source_id,
                family=entry.effective_archetype_id,
                kind=entry.policy_kind,
            )
        ]
    )
    request = CollectionRequest(
        population_epoch_id=_hash("population"),
        candidate_runtime_policy_id=_hash("runtime"),
        episodes=2,
        base_seed=10,
        subject_deck_id="subject",
    )
    interrupted = _Executor(fail_on_call=2)
    with pytest.raises(RuntimeError, match="intentional collection interruption"):
        collect_experience(
            request=request,
            mixture=mixture,
            catalog=catalog,
            executor=interrupted,
            output_root=tmp_path,
        )
    assert interrupted.calls == 2
    assert len(list((tmp_path / "staging" / "games").glob("*.json"))) == 1

    executor = _Executor()
    result = collect_experience(
        request=request,
        mixture=mixture,
        catalog=catalog,
        executor=executor,
        output_root=tmp_path,
    )
    assert result["games"] == 2
    assert result["sequences"] == 2
    assert Path(result["manifest_path"]).is_file()
    assert executor.calls == 1
    assert not (tmp_path / "staging").exists()

    resumed = collect_experience(
        request=request,
        mixture=mixture,
        catalog=catalog,
        executor=executor,
        output_root=tmp_path,
    )
    assert resumed == result
    assert executor.calls == 1
