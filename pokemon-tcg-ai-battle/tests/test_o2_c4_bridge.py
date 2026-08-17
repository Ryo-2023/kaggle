"""O2 match-plan -> C4 actual lineage adapter: pure mapping, no execution."""

from __future__ import annotations

from pathlib import Path

import pytest

from mage_ptcg.dataops.collector import ActualEpisodeLineageInput
from mage_ptcg.o2_training_loop.c4_bridge import build_episode_lineage_inputs, run_o2_actual_collection
from mage_ptcg.o2_training_loop.core import O2ContractError, build_match_matrix, load_deck_pool, load_opponent_pool

REPO_ROOT = Path(__file__).resolve().parents[1]


def _plan(seeds: list[int] = None):
    decks = load_deck_pool(REPO_ROOT / "configs/competition/deck_pool_o2_v1.yaml")
    opponents = load_opponent_pool(REPO_ROOT / "configs/competition/opponent_pool_o2_v1.yaml", deck_ids=decks)
    specs = build_match_matrix(
        decks=decks, opponents=opponents, challenger_id="rule-agent-v0",
        opponent_ids=["random-legal-v0"], seeds=seeds or [9300, 9301], engine_version="cabt",
        created_from_manifest="o2-training-loop-v1",
    )
    return decks, opponents, specs


def test_build_episode_lineage_inputs_maps_seat_and_hashes_from_match_spec() -> None:
    decks, opponents, specs = _plan()
    entries = build_episode_lineage_inputs(
        specs, challenger_id="rule-agent-v0", opponents=opponents, decks=decks, repository_root=REPO_ROOT,
    )
    assert len(entries) == len(specs) == 4
    assert {entry.match_id for entry in entries} == {spec.match_id for spec in specs}
    by_match_id = {spec.match_id: spec for spec in specs}
    for entry in entries:
        assert isinstance(entry, ActualEpisodeLineageInput)
        spec = by_match_id[entry.match_id]
        assert entry.plan_hash == spec.plan_hash
        assert entry.backend_kind == "cabt"
        assert entry.requested_seed == spec.seed
        assert entry.seat_index == spec.first_player
        assert entry.player_side == ("A" if spec.first_player == 0 else "B")
        assert entry.own_agent_id == "rule-agent-v0"
        assert entry.opponent_agent_id == "random-legal-v0"
        assert entry.own_implementation_hash == opponents["rule-agent-v0"].implementation_hash
        assert entry.opponent_implementation_hash == opponents["random-legal-v0"].implementation_hash
        assert entry.pair_id == spec.pair_id
        assert entry.match_spec_hash  # non-empty, deterministic content hash


def test_build_episode_lineage_inputs_is_deterministic() -> None:
    decks, opponents, specs = _plan()
    first = build_episode_lineage_inputs(specs, challenger_id="rule-agent-v0", opponents=opponents, decks=decks, repository_root=REPO_ROOT)
    second = build_episode_lineage_inputs(specs, challenger_id="rule-agent-v0", opponents=opponents, decks=decks, repository_root=REPO_ROOT)
    assert first == second


def test_build_episode_lineage_inputs_rejects_a_spec_whose_declared_seat_is_not_the_challenger() -> None:
    decks, opponents, specs = _plan()
    other = build_match_matrix(
        decks=decks, opponents=opponents, challenger_id="random-legal-v0",
        opponent_ids=["rule-agent-v0"], seeds=[1], engine_version="cabt",
        created_from_manifest="o2-training-loop-v1",
    )
    mixed = list(specs) + [other[0]]
    with pytest.raises(O2ContractError, match="does not place the challenger"):
        build_episode_lineage_inputs(mixed, challenger_id="rule-agent-v0", opponents=opponents, decks=decks, repository_root=REPO_ROOT)


def test_build_episode_lineage_inputs_rejects_more_than_one_opponent_in_the_batch() -> None:
    decks, opponents, specs = _plan()
    from dataclasses import replace as dc_replace

    # Mutate the opponent-facing slot of one spec (whichever slot is not the
    # challenger's declared seat) to a different, still-valid pool agent id,
    # simulating a second opponent slipping into a single collection run.
    target = specs[0]
    if target.first_player == 0:
        tampered_spec = dc_replace(target, player_b_agent="rule-agent-v0")
    else:
        tampered_spec = dc_replace(target, player_a_agent="rule-agent-v0")
    tampered = [tampered_spec] + list(specs[1:])
    with pytest.raises(O2ContractError, match="exactly one own agent and one opponent"):
        build_episode_lineage_inputs(tampered, challenger_id="rule-agent-v0", opponents=opponents, decks=decks, repository_root=REPO_ROOT)


def test_run_o2_actual_collection_wires_lineage_and_opponent_into_collector(monkeypatch: pytest.MonkeyPatch) -> None:
    decks, opponents, specs = _plan()
    captured: dict[str, object] = {}

    def fake_collect_actual_dataset(**kwargs):
        captured.update(kwargs)
        return {"status": "PASS"}

    monkeypatch.setattr("mage_ptcg.o2_training_loop.c4_bridge.collect_actual_dataset", fake_collect_actual_dataset)
    result = run_o2_actual_collection(
        specs=specs, challenger_id="rule-agent-v0", opponents=opponents, decks=decks,
        repository_root=REPO_ROOT, output_root="/tmp/does-not-matter", run_id="cabt",
        base_seed=9300, canonical_base_sha="a" * 40,
    )
    assert result == {"status": "PASS"}
    assert captured["games"] == len(specs)
    assert len(captured["episode_lineage_inputs"]) == len(specs)
    assert captured["opponent_deck_path"] is not None
    assert captured["opponent_agent_factory"] is not None
    assert captured["run_id"] == "cabt"


def test_run_o2_actual_collection_rejects_empty_specs() -> None:
    decks, opponents, _specs = _plan()
    with pytest.raises(O2ContractError):
        run_o2_actual_collection(
            specs=[], challenger_id="rule-agent-v0", opponents=opponents, decks=decks,
            repository_root=REPO_ROOT, output_root="/tmp/does-not-matter", run_id="cabt",
            base_seed=9300, canonical_base_sha="a" * 40,
        )
