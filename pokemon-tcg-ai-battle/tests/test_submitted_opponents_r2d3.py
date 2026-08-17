"""Focused contracts for submitted-population and R2D3 feature flags."""
from __future__ import annotations

import csv
from dataclasses import replace
import hashlib
import inspect
import json
from pathlib import Path
import random
import subprocess
import importlib.util
import sys
import time
from types import SimpleNamespace

import pytest

from mage_ptcg.policy_learning.league import PopulationMember, PSROState
from mage_ptcg.policy_learning.r2d3.checkpoint import load_checkpoint, save_checkpoint
from mage_ptcg.policy_learning.r2d3.distributional_q import project_categorical
from mage_ptcg.policy_learning.r2d3.actor import select_legal_action
from mage_ptcg.policy_learning.r2d3.candidate import R2D3CandidatePolicy
from mage_ptcg.policy_learning.r2d3.learner import LearnerConfig, R2D3Learner
from mage_ptcg.policy_learning.r2d3.replay import PrioritizedSequenceReplay, ReplaySample
from mage_ptcg.policy_learning.r2d3.sequence import R2D3Transition, SequenceBatch, public_prize_potential, shape_episode_rewards, split_episode
from mage_ptcg.policy_learning.r2d3.inference_server import CentralInferenceServer, InferenceRequest, QueuedCentralInferenceServer
from mage_ptcg.policy_learning.r2d3.model import R2D3ModelConfig, RecurrentDistributionalQ
from mage_ptcg.policy_learning.r2d3.online_collection import MixtureManifest, MixtureMember, collection_record
from mage_ptcg.policy_learning.r2d3.semantic_action import encode_legal_action
from mage_ptcg.policy_learning.r2d3.semantic_state import FEATURE_REGISTRY, encode_public_state
from mage_ptcg.policy_learning.submitted_opponents import SubmittedAsset, SubmittedOpponentError, assert_no_leakage, load_registry, split_assets
from mage_ptcg.policy_learning.submitted_runtime import SubmittedRuntimeError, pin_snapshot


PERFORMANCE_RUNNER = Path(__file__).parents[1] / "scripts" / "policy_learning" / "run_r2d3_multiseed_psro_performance.py"


def _performance_module():
    spec = importlib.util.spec_from_file_location("r2d3_performance_runner", PERFORMANCE_RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def _row(index: int) -> dict[str, str]:
    return {"asset_id": f"agents/a{index}", "ref": f"origin/agents/a{index}", "source_commit": f"{index:040x}", "deck_hash": f"d{index:063x}", "deck_id": "deck.csv", "policy_hash": f"p{index:063x}", "policy_id": f"a{index}", "local_runtime_status": "PROXY_RUNTIME_PASSED", "entrypoint": "main.py:agent", "smoke_games": "8", "teacher_eligible": "True", "official_runtime_evidence": "False"}


def test_registry_split_is_deterministic_and_policy_lineage_disjoint(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.csv"; rows = [_row(index) for index in range(10)]
    with ledger.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=sorted({key for row in rows for key in row})); writer.writeheader(); writer.writerows(rows)
    assets = load_registry(tmp_path, ledger, include_discovered=False); first = split_assets(assets, seed=7); second = split_assets(assets, seed=7)
    assert {name: [value.asset_id for value in group] for name, group in first.items()} == {name: [value.asset_id for value in group] for name, group in second.items()}
    assert_no_leakage(first)
    assert len(first["training"]) >= 5 and all(value.qualification == "TRAINING_ELIGIBLE" for value in first["training"])
    deck_sets = {
        name: {value.deck_hash for value in values}
        for name, values in first.items()
    }
    assert all(
        deck_sets[left].isdisjoint(deck_sets[right])
        for index, left in enumerate(deck_sets)
        for right in list(deck_sets)[index + 1:]
    )


def test_no_split_leakage_hard_fails() -> None:
    asset = type("A", (), {"policy_hash": "p", "source_lineage": "l", "deck_hash": "d"})()
    with pytest.raises(SubmittedOpponentError): assert_no_leakage({"training": [asset], "final_holdout": [asset]})


def test_split_groups_transitive_policy_lineage_and_deck_identities(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.csv"; rows = [_row(index) for index in range(6)]
    with ledger.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=sorted({key for row in rows for key in row}))
        writer.writeheader(); writer.writerows(rows)
    assets = load_registry(tmp_path, ledger, include_discovered=False)
    # a0--a1 share a deck; a1--a2 share a policy.  The whole connected
    # component must stay in one split even though a0/a2 share neither value.
    assets[0] = replace(assets[0], deck_hash="shared-deck")
    assets[1] = replace(assets[1], deck_hash="shared-deck", policy_hash="shared-policy")
    assets[2] = replace(assets[2], policy_hash="shared-policy")
    splits = split_assets(assets, seed=11)
    location = {
        asset.asset_id: split
        for split, values in splits.items()
        for asset in values
    }
    assert location["agents/a0"] == location["agents/a1"] == location["agents/a2"]
    assert_no_leakage(splits)


def test_current_submitted_ledger_is_deck_disjoint_when_available() -> None:
    module = _performance_module()
    ledger = Path(module.load_e2e().LEDGER)
    if not ledger.is_file():
        pytest.skip("local submitted-asset ledger is unavailable")
    assets = load_registry(Path(__file__).parents[1], ledger)
    splits = split_assets(assets, seed=71000)
    assert_no_leakage(splits)
    deck_sets = {name: {asset.deck_hash for asset in values} for name, values in splits.items()}
    assert all(
        deck_sets[left].isdisjoint(deck_sets[right])
        for index, left in enumerate(deck_sets)
        for right in list(deck_sets)[index + 1:]
    )


def _transition(*, terminal: bool = False, demo: bool = False, source: str = "online") -> R2D3Transition:
    return R2D3Transition((0.0,) * 8, ((0.0,) * 4, (1.0,) * 4), 0, 0.0, 0.0 if terminal else 0.99, terminal, "v" * 64, source, "p" * 64, "d" * 64, "lineage", "UNKNOWN", "o" * 64, demonstration=demo)


def test_semantic_encoders_keep_explicit_structure_and_residual_identity() -> None:
    state = {
        "actor": 1, "first_player": 0, "step": 8, "turn": 4, "turn_action_count": 2,
        "select": {"min_count": 1, "max_count": 2, "option_count": 5, "type": 3},
        "self": {"hand_count": 7, "deck_count": 40, "prize_count": 4, "active": [{}], "bench": [{}, {}], "bench_max": 5, "discard": [{}], "status": {}},
        "opponent": {"hand_count": 5, "deck_count": 42, "prize_count": 5, "active": [{}], "bench": [{}], "bench_max": 5, "discard": [], "status": {}},
        "board": {"stadium": None, "stadium_played": False, "supporter_played": True, "energy_attached": False, "retreated": False},
        "observed_result": None,
    }
    encoded = encode_public_state(state)
    assert FEATURE_REGISTRY["schema"] == "r2d3-semantic-state-v2"
    assert encoded[0] == 1.0 and encoded[3] == pytest.approx(4 / 64)
    changed = encode_public_state({**state, "turn": 5})
    assert changed[3] == pytest.approx(5 / 64) and changed != encoded
    action = encode_legal_action({"digest": "a" * 64, "action_type": 13, "card_id": 1234, "selection_order": 2, "optional": True})
    assert action[0] == pytest.approx(13 / 32) and action[1] == pytest.approx(0.1234)
    assert action[3] == pytest.approx(2 / 32) and action[4] == 1.0


def test_public_prize_potential_shaping_uses_only_actor_visible_counts() -> None:
    state = {"self": {"prize_count": 5}, "opponent": {"prize_count": 6}}
    assert public_prize_potential(state) == pytest.approx(1 / 60)
    rewards = shape_episode_rewards([0.0, 1 / 60], outcome=1.0, gamma=.99)
    assert rewards[0] == pytest.approx(.99 / 60)
    assert rewards[1] == pytest.approx(1.0 - 1 / 60)
    with pytest.raises(ValueError, match="actor-visible"):
        public_prize_potential({"opponent_prizes": [1, 2]})


def test_recurrent_replay_keeps_episode_boundary_and_demo_sampling() -> None:
    sequences = split_episode([_transition(demo=True), _transition(terminal=True, demo=True)], burn_in=1, unroll=1)
    replay = PrioritizedSequenceReplay(4); [replay.add(value) for value in sequences]
    sample = replay.sample(2, beta=.4, demonstration_ratio=1.0, seed=7)
    assert sample.demonstrations == 2 and all(0 < value <= 1 for value in sample.weights)
    assert select_legal_action([1.0, 1.0]) == 0
    with pytest.raises(ValueError): SequenceBatch((_transition(terminal=True),), (_transition(),), 1.0, "bad")


def test_source_balanced_replay_is_cached_and_keeps_each_source_in_a_batch() -> None:
    replay = PrioritizedSequenceReplay(16)
    for index in range(8):
        replay.add(SequenceBatch((), (_transition(source="dominant"),), 1.0, f"dominant-{index}"))
    for source in ("rare-a", "rare-b"):
        replay.add(SequenceBatch((), (_transition(source=source),), 1.0, source))
    sample = replay.sample(6, beta=.4, source_balanced=True, seed=7)
    sources = [sequence.learner[0].behavior_source for sequence in sample.sequences]
    assert {"dominant", "rare-a", "rare-b"} <= set(sources)
    cached = replay._source_group_cache
    assert cached is not None
    replay.sample(6, beta=.4, source_balanced=True, seed=8)
    assert replay._source_group_cache is cached
    assert all(0 < weight <= 1 for weight in sample.weights)


def test_replay_sequence_stride_creates_overlapping_r2d3_windows() -> None:
    transitions = [_transition(terminal=index == 29) for index in range(30)]
    sequences = split_episode(transitions, burn_in=8, unroll=20, stride=4, prefix="overlap")
    assert [value.sequence_id for value in sequences] == [
        "overlap-0", "overlap-4", "overlap-8", "overlap-12",
        "overlap-16", "overlap-20", "overlap-24", "overlap-28",
    ]
    assert len(sequences[0].learner) == 20 and len(sequences[0].lookahead) == 5
    assert all(not any(step.terminal for step in value.burn_in) for value in sequences)


def test_windowed_replay_materializes_burn_unroll_and_n_step_tail_once() -> None:
    transitions = tuple(_transition(terminal=index == 39) for index in range(40))
    source = PrioritizedSequenceReplay(100)
    source.add(SequenceBatch((), transitions, 1.0, "episode", "episode"))
    replay = PrioritizedSequenceReplay.windowed(source, stride=4)
    first = replay._sequence_at(0)
    second = replay._sequence_at(1)
    assert (len(first.burn_in), len(first.learner), len(first.lookahead)) == (0, 20, 5)
    assert (len(second.burn_in), len(second.learner), len(second.lookahead)) == (4, 20, 5)
    assert replay._items[0].learner is transitions


def test_learner_batch_uses_lookahead_for_last_trainable_n_step_target() -> None:
    torch = pytest.importorskip("torch")
    sequence = SequenceBatch(
        (),
        tuple(_transition() for _ in range(20)),
        1.0,
        "sequence",
        "episode",
        tuple(_transition() for _ in range(5)),
    )
    sample = ReplaySample((sequence,), (0,), (1.0,), 0)
    batch = _performance_module().load_e2e()._learner_batch(sample, torch.device("cpu"))
    assert batch["states"].shape[1] == 25
    assert batch["sequence_mask"][0, :20].all() and not batch["sequence_mask"][0, 20:].any()
    assert int(batch["bootstrap_indices"][0, 19]) == 24


def test_performance_protocol_profiles_and_stage_contract_are_fail_closed() -> None:
    module = _performance_module()
    smoke, production = module.PROFILES["smoke"], module.PROFILES["production"]
    assert module.STAGES == ("source_freeze", "scale_benchmark", "teacher_calibration", "replay_collection", "replay_freeze", "learner_scale_benchmark", "architecture_screen", "multiseed_training", "full_training", "development_validation", "deck_holdout_gate", "psro_payoff", "psro_online_collection", "psro_best_response", "final_holdout_gate", "promotion_decision")
    assert smoke.scale_games_per_config == 8
    assert smoke.replay_games == 128 and smoke.replay_quality_interval == 64
    assert smoke.replay_checkpoint_games == 64 and production.replay_checkpoint_games == 256
    assert smoke.screen_updates == smoke.multiseed_updates == smoke.psro_best_response_updates == 20
    assert len(smoke.screen_architectures) == 6 and smoke.multiseed_top_k == 2 and smoke.multiseed_seeds == (0, 1, 2)
    assert production.replay_games == 5000 and production.replay_quality_interval == 1250 and production.minimum_replay_sequences == 25000
    assert production.screen_updates == 10000 and production.multiseed_updates == 50000 and production.full_training_updates == 150000
    assert smoke.screen_validation_games == 4 and smoke.development_validation_games == 8
    assert smoke.deck_holdout_games == smoke.final_holdout_games == 8
    assert smoke.psro_pair_games == 2 and smoke.psro_online_games == 4
    assert production.psro_online_games == 2000
    assert production.deck_holdout_games == production.final_holdout_games == 1024
    assert smoke.cabt_workers == 4 and production.cabt_workers == 12
    assert smoke.cuda_validation_workers == 4 and production.cuda_validation_workers == 12
    # Submitted search opponents spend a wall-clock budget per decision, so an
    # oversubscribed core does not fail loudly -- it buys the opponent fewer
    # rollouts and inflates the candidate win rate.  Both profiles must stay
    # bounded by physical cores and keep timeout headroom over the opponent's own
    # 4.0s per-decision cap.
    assert smoke.validation_reserved_cores == production.validation_reserved_cores == 2
    assert smoke.validation_callback_timeout_seconds == 8.0
    assert production.validation_callback_timeout_seconds == 12.0
    for profile in (smoke, production):
        assert profile.cuda_validation_workers <= module.physical_cores() - profile.validation_reserved_cores
        assert profile.validation_callback_timeout_seconds >= 2 * 4.0
    assert smoke.learner_batch_size == 32 and production.learner_batch_size == 128
    assert smoke.learner_batch_candidates == (32, 64, 128)
    assert production.learner_batch_candidates == (64, 128, 256, 512, 1024, 2048, 3072)
    assert smoke.learner_peak_reserved_limit_mb is None
    assert production.learner_peak_reserved_limit_mb == 40_000.0
    assert smoke.model_hidden_size == 128 and production.model_hidden_size == 256
    assert smoke.training_log_interval == 1 and production.training_log_interval == 100
    assert smoke.replay_sequence_stride == 20 and production.replay_sequence_stride == 4
    assert inspect.signature(module.durable_psro_payoff_prefix).parameters["workers"].default == 12


def test_validation_schedule_balances_every_asset_and_seat() -> None:
    module = _performance_module()
    assets = [SimpleNamespace(asset_id="dev/a"), SimpleNamespace(asset_id="dev/b")]
    schedule = module.validation_schedule(assets, 8, seed_namespace="development")
    assert len(schedule) == 8
    assert {(entry["asset_index"], entry["candidate_side"]) for entry in schedule} == {
        (0, 0), (0, 1), (1, 0), (1, 1),
    }
    assert all(sum(entry["asset_index"] == asset and entry["candidate_side"] == side for entry in schedule) == 2
               for asset in range(2) for side in range(2))
    assert [entry["seed"] for entry in schedule] != [
        entry["seed"] for entry in module.validation_schedule(assets, 8, seed_namespace="selection")
    ]
    with pytest.raises(ValueError, match="asset×seat"):
        module.validation_schedule(assets, 6, seed_namespace="development")


def test_psro_quota_schedule_enforces_member_floor() -> None:
    module = _performance_module()
    members = [
        MixtureMember("rule-v0", .97, "a" * 64, "l0", "RULE_V0", "rule_v0"),
        MixtureMember("rule-v1", .01, "b" * 64, "l1", "RULE_V1", "rule_v1"),
        MixtureMember("ppo", .01, "c" * 64, "l2", "PPO", "ppo"),
        MixtureMember("r2d3", .01, "d" * 64, "l3", "R2D3", "r2d3"),
    ]
    quotas = module.balanced_mixture_quotas(members, 2_000, floor_probability=.15)
    assert sum(quotas.values()) == 2_000
    assert all(value >= 300 for value in quotas.values())


def test_controller_gpu_lease_rejects_concurrent_runs_and_recovers(tmp_path: Path) -> None:
    module = _performance_module()
    path = tmp_path / "gpu.lock"
    first = module.ControllerLease(path)
    second = module.ControllerLease(path)
    first.acquire({"pid": 1, "artifact_root": "first"})
    try:
        with pytest.raises(RuntimeError, match="active R2D3 performance controller"):
            second.acquire({"pid": 2, "artifact_root": "second"})
    finally:
        first.close()
    second.acquire({"pid": 2, "artifact_root": "second"})
    second.close()


def test_learner_memory_projection_skips_unsafe_larger_batch() -> None:
    module = _performance_module()
    rows = [{
        "status": "PASS",
        "batch_size": 1024,
        "peak_reserved_mb": 17_244.0,
    }]
    assert module.projected_learner_peak_reserved_mb(2048, rows) == 34_488.0
    assert module.projected_learner_peak_reserved_mb(3072, rows) == 51_732.0
    assert module.projected_learner_peak_reserved_mb(64, []) is None


def test_cuda_validation_workers_are_not_cpu_scale_workers() -> None:
    module = _performance_module()
    controller = object.__new__(module.Controller)
    controller.profile = module.PROFILES["production"]
    expected = min(module.PROFILES["production"].cuda_validation_workers,
                   module.physical_cores() - module.PROFILES["production"].validation_reserved_cores)
    # The CPU-collection benchmark measures in-process games, so its winner must
    # never raise *or* lower validation concurrency in either direction.
    for cabt in (1, 8, 28):
        controller.cabt_workers = lambda value=cabt: value
        assert controller.validation_workers() == expected


def test_validation_workers_never_oversubscribe_physical_cores(monkeypatch) -> None:
    module = _performance_module()
    controller = object.__new__(module.Controller)
    controller.profile = module.PROFILES["production"]
    controller.cabt_workers = lambda: 28
    # A submitted opponent that misses its wall-clock budget plays weaker instead
    # of failing, so the clamp is the only thing protecting the win-rate gate.
    monkeypatch.setattr(module, "physical_cores", lambda: 6)
    assert controller.validation_workers() == 4
    monkeypatch.setattr(module, "physical_cores", lambda: 3)
    assert controller.validation_workers() == 1


def test_continuation_imports_verified_final_checkpoint_and_rejects_identity_mismatch(tmp_path: Path) -> None:
    """A parent artifact cannot be reused unless its immutable lineage matches."""
    module = _performance_module()
    parent = tmp_path / "v15"; artifact = tmp_path / "v16"
    checkpoint = parent / "checkpoints" / "psro-best-response-seed0" / "r2d3-step-000020.pt"
    full_checkpoint = parent / "checkpoints" / "full-training" / "r2d3-step-000040.pt"
    for path, content in ((checkpoint, b"final-br-checkpoint"), (full_checkpoint, b"full-checkpoint"),
                          (parent / "replay.json", b"offline-replay"),
                          (parent / "psro_online_replay.json", b"online-replay"),
                          (parent / "runtime_source_manifest.json", b"runtime-source"),
                          (parent / "snapshots" / "dev" / "opponent" / "commit" / ".submitted_snapshot_manifest.json", b"snapshot")):
        path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(content)
    source = {
        "protected_before": {"main.py": "main", "deck.csv": "deck", "agents/rule_agent.py": "rule"},
        "semantic_feature_version": "semantic", "submitted_registry_hash": "registry",
        "source_artifact": "/source", "source_artifact_manifest_hash": "source-manifest",
        "deck_pool_file_hash": "pool", "population_hash": "population",
    }
    module.atomic_json(parent / "source_identity.json", source)
    module.atomic_json(parent / "replay_manifest.json", {"replay_sha256": module.sha(parent / "replay.json")})
    module.atomic_json(parent / "psro_online_replay_manifest.json", {"replay_sha256": module.sha(parent / "psro_online_replay.json")})
    for stage in module.CONTINUATION_INHERITED_STAGES:
        output: dict[str, object] = {"stage": stage, "status": "PASS", "fault_count": 0}
        if stage == "learner_scale_benchmark": output["selected"] = {"status": "PASS", "batch_size": 32}
        if stage == "multiseed_training": output["selected"] = {"architecture": "gru_demo_0", "seed": 0}
        if stage == "full_training":
            output.update({"checkpoint": str(full_checkpoint), "checkpoint_hash": module.sha(full_checkpoint),
                           "training_identity_hash": "full-identity", "updates": 40, "architecture": "gru_demo_0"})
        stage_dir = parent / "stages" / stage
        module.atomic_json(stage_dir / "status.json", {"stage": stage, "status": "PASS"})
        module.atomic_json(stage_dir / "output_manifest.json", output)
    module.atomic_json(parent / "checkpoints" / "psro-best-response-seed0" / "training_manifest.json", {
        "name": "psro-best-response-seed0", "updates": 20, "training_identity_hash": "br-identity",
        "checkpoint": {"step": 20, "sha256": module.sha(checkpoint)},
    })
    controller = object.__new__(module.Controller)
    controller.args = SimpleNamespace(continue_from_artifact=parent, resume=False)
    controller.artifact = artifact; controller.profile = module.PROFILES["smoke"]
    controller.context = {"source": source}; controller.monitor = module.TerminalProgress("quiet")

    controller.import_parent_continuation()

    imported = artifact / "checkpoints" / "psro-best-response-seed0" / "r2d3-step-000020.pt"
    manifest = json.loads((artifact / "continuation_manifest.json").read_text())
    assert imported.read_bytes() == b"final-br-checkpoint"
    assert manifest["parent_checkpoint"]["sha256"] == module.sha(checkpoint)
    assert controller.inherited_stage("full_training")
    assert (artifact / "snapshots" / "dev" / "opponent" / "commit" / ".submitted_snapshot_manifest.json").read_bytes() == b"snapshot"
    (artifact / "snapshots" / "dev" / "opponent" / "commit" / ".submitted_snapshot_manifest.json").unlink()
    controller.repair_parent_continuation()
    assert (artifact / "snapshots" / "dev" / "opponent" / "commit" / ".submitted_snapshot_manifest.json").read_bytes() == b"snapshot"
    legacy_manifest = {key: value for key, value in manifest.items() if key not in {"runtime_source", "snapshots"}}
    module.atomic_json(artifact / "continuation_manifest.json", legacy_manifest)
    controller.context.pop("continuation")
    (artifact / "snapshots" / "dev" / "opponent" / "commit" / ".submitted_snapshot_manifest.json").unlink()
    controller.repair_parent_continuation()
    assert (artifact / "snapshots" / "dev" / "opponent" / "commit" / ".submitted_snapshot_manifest.json").read_bytes() == b"snapshot"

    mismatch = object.__new__(module.Controller)
    mismatch.args = SimpleNamespace(continue_from_artifact=parent, resume=False)
    mismatch.artifact = tmp_path / "mismatch"; mismatch.profile = module.PROFILES["smoke"]
    mismatch.context = {"source": {**source, "population_hash": "different"}}; mismatch.monitor = module.TerminalProgress("quiet")
    with pytest.raises(RuntimeError, match="continuation source identity differs"):
        mismatch.import_parent_continuation()


def test_run_stage_skips_an_inherited_parent_stage_without_resume(tmp_path: Path) -> None:
    """The child must not spend a completed parent stage a second time."""
    module = _performance_module()
    controller = object.__new__(module.Controller)
    controller.args = SimpleNamespace(resume=False)
    controller.artifact = tmp_path; controller.started = time.monotonic()
    controller.context = {"continuation": {"inherited_stages": ["full_training"]}}
    controller.monitor = module.TerminalProgress("quiet")
    module.atomic_json(controller.stage_dir("full_training") / "status.json", {"stage": "full_training", "status": "PASS", "inherited_from": "/parent"})
    controller.run_stage("full_training", lambda: (_ for _ in ()).throw(AssertionError("inherited stage ran")))


def test_imported_checkpoint_must_match_the_final_update_and_recorded_hash(tmp_path: Path) -> None:
    """A stale or modified parent checkpoint must never become a child resume point."""
    module = _performance_module()
    checkpoint = tmp_path / "checkpoints" / "psro-best-response-seed0" / "r2d3-step-000020.pt"
    checkpoint.parent.mkdir(parents=True); checkpoint.write_bytes(b"verified")
    controller = object.__new__(module.Controller)
    controller.artifact = tmp_path
    controller.context = {"continuation": {"parent_checkpoint": {
        "name": "psro-best-response-seed0", "child_path": str(checkpoint),
        "sha256": module.sha(checkpoint), "step": 20, "updates": 20,
        "training_identity_hash": "historical-identity",
    }}}
    record = controller.imported_final_checkpoint("psro-best-response-seed0", updates=20)
    assert record["training_identity_hash"] == "historical-identity"
    with pytest.raises(RuntimeError, match="final-step"):
        controller.imported_final_checkpoint("psro-best-response-seed0", updates=21)
    checkpoint.write_bytes(b"modified")
    with pytest.raises(RuntimeError, match="hash differs"):
        controller.imported_final_checkpoint("psro-best-response-seed0", updates=20)


def test_resume_recovers_parent_path_from_the_child_continuation_manifest(tmp_path: Path) -> None:
    """An interrupted child resumes with --resume, not a fragile repeated parent flag."""
    module = _performance_module()
    parent = tmp_path / "v15"; parent.mkdir()
    controller = object.__new__(module.Controller)
    controller.args = SimpleNamespace(continue_from_artifact=None, resume=True)
    controller.artifact = tmp_path / "v16"; controller.artifact.mkdir()
    controller.context = {}
    module.atomic_json(controller.artifact / "continuation_manifest.json", {"parent_artifact": str(parent)})
    assert controller._continuation_parent() == parent


def test_continuation_source_rebaseline_is_limited_to_pre_validation_repair(tmp_path: Path) -> None:
    """A controller repair may continue a failed validation, never a spent holdout."""
    module = _performance_module()
    parent = tmp_path / "v15"; parent.mkdir()
    source = {"protected_before": {}, "semantic_feature_version": "semantic", "submitted_registry_hash": "registry",
              "source_artifact": "/source", "source_artifact_manifest_hash": "source-manifest",
              "deck_pool_file_hash": "pool", "population_hash": "population"}
    module.atomic_json(parent / "source_identity.json", source)
    controller = object.__new__(module.Controller)
    controller.args = SimpleNamespace(continue_from_artifact=parent, resume=True)
    controller.artifact = tmp_path / "v16"; controller.artifact.mkdir()
    controller.run_root = tmp_path / "run"; controller.run_root.mkdir()
    controller.state_path = controller.run_root / "r2d3_performance_controller_identity.json"
    controller.context = {"source": source, "continuation": {"parent_artifact": str(parent)}}
    controller.monitor = module.TerminalProgress("quiet")
    module.atomic_json(controller.artifact / "continuation_manifest.json", controller.context["continuation"])
    module.atomic_json(controller.stage_dir("full_training") / "status.json", {"stage": "full_training", "status": "PASS", "inherited_from": str(parent)})
    module.atomic_json(controller.stage_dir("psro_best_response") / "status.json", {"stage": "psro_best_response", "status": "FAIL"})
    previous = {"profile_hash": "profile", "source_identity_hash": "old", "source_patch_hash": "old", "head": "old", "artifact_root": str(controller.artifact)}
    current = {"profile_hash": "profile", "source_identity_hash": "new", "source_patch_hash": "new", "head": "new", "artifact_root": str(controller.artifact)}
    controller.rebaseline_continuation_identity(previous, current)
    assert json.loads(controller.state_path.read_text()) == current
    module.atomic_json(controller.artifact / "deck_holdout_used.json", {"status": "USED"})
    with pytest.raises(RuntimeError, match="holdout"):
        controller.rebaseline_continuation_identity(current, {**current, "head": "newer"})

def test_campaign_source_identity_covers_runtime_and_split_dependencies() -> None:
    module = _performance_module()
    paths = set(module.source_identity_files(Path(__file__).parents[1]))
    assert {
        "scripts/test_sim.py",
        "src/mage_ptcg/offline_scaleup/candidate_runtime.py",
        "src/mage_ptcg/offline_scaleup/pipeline.py",
        "src/mage_ptcg/policy_learning/submitted_opponents.py",
        "src/mage_ptcg/policy_learning/runtime.py",
        "src/mage_ptcg/policy_learning/training.py",
        "scripts/policy_learning/run_r2d3_multiseed_psro_performance.py",
    }.issubset(paths)


def test_performance_monitor_coalesces_non_tty_progress(capsys: pytest.CaptureFixture[str]) -> None:
    module = _performance_module()
    monitor = module.TerminalProgress("summary")
    for completed in range(1, 6):
        monitor.update("replay_collection", completed, 5, faults=0, extra={"games": completed})
    output = capsys.readouterr().out.splitlines()
    assert len(output) == 2
    assert "stage=replay_collection 1/5" in output[0]
    assert "stage=replay_collection 5/5" in output[1]


def test_replay_source_counts_reject_bucket_name_confusion() -> None:
    rows = [
        {"bucket": "submitted_agents_dev"}, {"bucket": "rule_v0_v1"},
        {"bucket": "bc_recurrent"}, {"bucket": "family_alakazam"},
    ]
    counts = {
        "ppo_submitted_rule": sum(row["bucket"] in {"submitted_agents_dev", "rule_v0_v1"} for row in rows),
        "bc_recurrent": sum(row["bucket"] == "bc_recurrent" for row in rows),
        "family_alakazam": sum(row["bucket"] == "family_alakazam" for row in rows),
    }
    assert counts == {"ppo_submitted_rule": 2, "bc_recurrent": 1, "family_alakazam": 1}


def test_central_inference_batches_first_requests_and_rejects_stale_policy() -> None:
    torch = pytest.importorskip("torch")
    model = RecurrentDistributionalQ(R2D3ModelConfig(state_size=8, action_size=4, hidden_size=8, atoms=5))
    server = CentralInferenceServer(model, max_batch_size=2)
    requests = [InferenceRequest(f"g{index}", 0, "s", "v1", torch.rand(1, 8), torch.rand(1, index + 1, 4), torch.ones(1, index + 1, dtype=torch.bool)) for index in range(2)]
    output = server.infer_many(requests, expected_policy_version="v1")
    assert len(output) == 2 and server.metrics["batches"] == 1
    assert [item["q"].shape[1] for item in output] == [1, 2]
    recurrent = server.infer_many(requests, expected_policy_version="v1")
    assert len(recurrent) == 2 and server.metrics["batches"] == 2
    with pytest.raises(ValueError): server.infer(requests[0], expected_policy_version="v2")


def test_queued_central_inference_microbatches_concurrent_actors() -> None:
    from concurrent.futures import ThreadPoolExecutor
    import threading
    torch = pytest.importorskip("torch")
    model = RecurrentDistributionalQ(R2D3ModelConfig(state_size=8, action_size=4, hidden_size=8, atoms=5))
    server = QueuedCentralInferenceServer(model, max_batch_size=4, max_delay_ms=20)
    barrier = threading.Barrier(4)
    def infer(index: int) -> object:
        barrier.wait()
        request = InferenceRequest(f"g{index}", 0, "s", "v", torch.rand(1, 8), torch.rand(1, 2, 4), torch.ones(1, 2, dtype=torch.bool))
        return server.infer(request, expected_policy_version="v")
    with ThreadPoolExecutor(max_workers=4) as executor:
        output = list(executor.map(infer, range(4)))
    metrics = server.metrics; server.close()
    assert len(output) == 4 and max(metrics["batch_sizes"]) == 4


def test_replay_save_reload_preserves_identity_and_sampling(tmp_path: Path) -> None:
    replay = PrioritizedSequenceReplay(8)
    replay.add(SequenceBatch((), (_transition(demo=True),), 2.0, "demo"))
    replay.add(SequenceBatch((), (_transition(),), 1.0, "online"))
    saved = replay.save(tmp_path / "replay.json")
    assert saved["sha256"] == hashlib.sha256((tmp_path / "replay.json").read_bytes()).hexdigest()
    loaded = PrioritizedSequenceReplay.load(tmp_path / "replay.json")
    sample = loaded.sample(2, beta=.4, demonstration_ratio=.5, seed=3)
    assert len(loaded) == 2 and sample.demonstrations >= 1
    assert sample.sequences[0].learner[0].opponent_source_lineage == "lineage"


def test_replay_save_atomically_replaces_existing_file(tmp_path: Path) -> None:
    path = tmp_path / "replay.json"
    path.write_text("incomplete")
    replay = PrioritizedSequenceReplay(8)
    replay.add(SequenceBatch((), (_transition(terminal=True),), 1.0, "online"))
    saved = replay.save(path)
    assert saved["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert not path.with_suffix(".json.tmp").exists()
    assert len(PrioritizedSequenceReplay.load(path)) == 1


def test_compact_window_replay_save_reload_and_sampling(tmp_path: Path) -> None:
    transitions = [_transition(terminal=index == 7, demo=True) for index in range(8)]
    base = PrioritizedSequenceReplay(32)
    base.add(SequenceBatch((), tuple(transitions), 1.0, "episode"))
    replay = PrioritizedSequenceReplay.windowed(base, stride=2)
    assert replay.is_windowed and len(replay) == 4
    sample = replay.sample(4, beta=.4, demonstration_ratio=1.0, seed=3)
    assert sample.demonstrations == 4 and {sequence.sequence_id for sequence in sample.sequences} <= {"episode-window-0", "episode-window-2", "episode-window-4", "episode-window-6"}
    replay.save(tmp_path / "compact-replay.json")
    loaded = PrioritizedSequenceReplay.load(tmp_path / "compact-replay.json")
    assert loaded.is_windowed and len(loaded) == 4
    assert loaded.sample(1, beta=.4, seed=4).sequences[0].learner


def test_replay_collection_chunks_are_contiguous_and_restart_loadable(tmp_path: Path) -> None:
    module = _performance_module()
    controller = object.__new__(module.Controller)
    controller.artifact = tmp_path
    controller.profile = module.PROFILES["smoke"]
    controller.context = {"collection_identity_hash": "collection"}
    first_rows = [{"bucket": "submitted_agents_dev"}, {"bucket": "rule_v0_v1"}]
    second_rows = [{"bucket": "submitted_agents_dev"}, {"bucket": "rule_v0_v1"}]
    first_episodes = [{"game_id": "g0"}, {"game_id": "g1"}]
    second_episodes = [{"game_id": "g2"}, {"game_id": "g3"}]
    controller._save_replay_chunk(
        source="ppo_submitted_rule", start=0, rows=first_rows, episodes=first_episodes
    )
    controller._save_replay_chunk(
        source="ppo_submitted_rule", start=2, rows=second_rows, episodes=second_episodes
    )
    rows, episodes, manifests = controller._load_replay_chunks()
    assert rows == first_rows + second_rows
    assert episodes == first_episodes + second_episodes
    assert [item["end"] for item in manifests] == [2, 4]
    invalid_content = {
        "schema": "r2d3-replay-collection-chunk-v2",
        "collection_identity_hash": "collection",
        "source": "ppo_submitted_rule",
        "start": 5,
        "end": 6,
        "rows": [{"bucket": "submitted_agents_dev"}],
        "episodes": [{"game_id": "bad"}],
    }
    with pytest.raises(RuntimeError, match="non-contiguous"):
        module.atomic_json(
            tmp_path / "replay_collection_chunks" / "chunk-000006.json",
            {**invalid_content, "content_hash": module.digest(invalid_content)},
        )
        controller._load_replay_chunks()


def test_checkpoint_resume_restores_bound_model_target_and_step(tmp_path: Path) -> None:
    torch = pytest.importorskip("torch")
    config = R2D3ModelConfig(state_size=8, action_size=4, hidden_size=8, atoms=5)
    model = RecurrentDistributionalQ(config); optimizer = torch.optim.AdamW(model.parameters(), lr=1e-3)
    learner = R2D3Learner(model, optimizer, config=LearnerConfig(target_update_interval=1))
    states = torch.rand(2, 8); actions = torch.rand(2, 2, 4); mask = torch.ones(2, 2, dtype=torch.bool)
    metrics = learner.update(states, actions, mask, torch.tensor([0, 1]), torch.zeros(2), torch.full((2,), .99),
                             states, actions, mask, torch.ones(2), torch.tensor([True, False]),
                             opponent_embedding_target=torch.zeros(2, 8), deck_family_target=torch.zeros(2),
                             next_action_type_target=torch.zeros(2))
    assert metrics["target_updated"] == 1.0 and metrics["auxiliary_loss"] > 0
    path = tmp_path / "checkpoint.pt"
    saved = save_checkpoint(path, model=model, target=learner.target, optimizer=optimizer, population_hash="p", replay_manifest_hash="r", step=1)
    restored_model = RecurrentDistributionalQ(config); restored_optimizer = torch.optim.AdamW(restored_model.parameters(), lr=1e-3)
    restored_learner = R2D3Learner(restored_model, restored_optimizer, config=LearnerConfig(target_update_interval=1))
    step = load_checkpoint(path, model=restored_model, target=restored_learner.target, optimizer=restored_optimizer,
                           expected_population_hash="p", expected_replay_manifest_hash="r", map_location="cpu")
    assert step == 1 and saved["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert all(torch.equal(left, right) for left, right in zip(model.state_dict().values(), restored_model.state_dict().values(), strict=True))
    with pytest.raises(ValueError):
        load_checkpoint(path, model=restored_model, target=restored_learner.target, optimizer=restored_optimizer,
                        expected_population_hash="wrong", expected_replay_manifest_hash="r", map_location="cpu")


def test_direct_bc_loss_is_applied_only_for_demonstration_actions() -> None:
    torch = pytest.importorskip("torch")
    config = R2D3ModelConfig(state_size=8, action_size=4, hidden_size=8, atoms=5)
    model = RecurrentDistributionalQ(config)
    learner = R2D3Learner(model, torch.optim.AdamW(model.parameters(), lr=1e-3),
                          config=LearnerConfig(bc_weight=.25, target_update_interval=10))
    states = torch.rand(2, 8); actions = torch.rand(2, 2, 4); legal = torch.ones(2, 2, dtype=torch.bool)
    metrics = learner.update(states, actions, legal, torch.tensor([0, 1]), torch.zeros(2), torch.full((2,), .99),
                             states, actions, legal, torch.ones(2), torch.tensor([True, False]))
    assert metrics["bc_loss"] > 0.0 and metrics["loss"] > metrics["distributional_loss"]


def _priority_replay() -> PrioritizedSequenceReplay:
    replay = PrioritizedSequenceReplay(8)
    for index in range(4):
        replay.add(SequenceBatch((), (_transition(terminal=True),), 1.0, f"sequence-{index}"))
    return replay


def test_checkpoint_restores_per_state_and_next_sample_exactly(tmp_path: Path) -> None:
    torch = pytest.importorskip("torch")
    config = R2D3ModelConfig(state_size=8, action_size=4, hidden_size=8, atoms=5)
    model = RecurrentDistributionalQ(config)
    optimizer = torch.optim.AdamW(model.parameters(), lr=1e-3)
    live = _priority_replay()
    live.update_priorities((0, 1, 2, 3), (100.0, 2.0, 3.0, 4.0))
    path = tmp_path / "checkpoint-with-per.pt"
    saved = save_checkpoint(
        path, model=model, target=model, optimizer=optimizer,
        population_hash="population", replay_manifest_hash="replay", step=7,
        replay=live, training_identity_hash="training",
    )
    expected = live.sample(8, beta=.7, seed=91)

    restored_replay = _priority_replay()
    restored_model = RecurrentDistributionalQ(config)
    restored_optimizer = torch.optim.AdamW(restored_model.parameters(), lr=1e-3)
    assert load_checkpoint(
        path, model=restored_model, target=restored_model, optimizer=restored_optimizer,
        expected_population_hash="population", expected_replay_manifest_hash="replay",
        map_location="cpu", replay=restored_replay,
        expected_training_identity_hash="training",
    ) == 7
    actual = restored_replay.sample(8, beta=.7, seed=91)
    assert saved["schema"] == "r2d3-checkpoint-v2"
    assert restored_replay._priorities == live._priorities
    assert (actual.indices, actual.weights) == (expected.indices, expected.weights)

    wrong = PrioritizedSequenceReplay(8)
    wrong.add(SequenceBatch((), (_transition(terminal=True),), 1.0, "different"))
    with pytest.raises(ValueError, match="priority state identity"):
        load_checkpoint(
            path, model=restored_model, target=restored_model, optimizer=restored_optimizer,
            expected_population_hash="population", expected_replay_manifest_hash="replay",
            map_location="cpu", replay=wrong,
            expected_training_identity_hash="training",
        )
    with pytest.raises(ValueError, match="training identity"):
        load_checkpoint(
            path, model=restored_model, target=restored_model, optimizer=restored_optimizer,
            expected_population_hash="population", expected_replay_manifest_hash="replay",
            map_location="cpu", expected_training_identity_hash="other",
        )


def test_interrupted_training_matches_uninterrupted_after_per_resume(tmp_path: Path) -> None:
    torch = pytest.importorskip("torch")
    module = _performance_module()
    e2e = module.load_e2e()
    config = R2D3ModelConfig(state_size=8, action_size=4, hidden_size=8, atoms=5)
    torch.manual_seed(123)
    initial = RecurrentDistributionalQ(config).state_dict()

    def build():
        model = RecurrentDistributionalQ(config)
        model.load_state_dict(initial)
        optimizer = torch.optim.AdamW(model.parameters(), lr=1e-3)
        learner = R2D3Learner(
            model, optimizer, config=LearnerConfig(target_update_interval=2)
        )
        return model, optimizer, learner

    def advance(model, learner, replay, start: int, end: int) -> None:
        for step in range(start, end + 1):
            sample = replay.sample(2, beta=.4 + step * .01, seed=700 + step)
            metrics = learner.update(**e2e._learner_batch(sample, torch.device("cpu")))
            replay.update_priorities(sample.indices, metrics["sequence_priorities"])

    full_model, _full_optimizer, full_learner = build()
    full_replay = _priority_replay()
    advance(full_model, full_learner, full_replay, 1, 12)

    partial_model, partial_optimizer, partial_learner = build()
    partial_replay = _priority_replay()
    advance(partial_model, partial_learner, partial_replay, 1, 6)
    path = tmp_path / "step-6.pt"
    save_checkpoint(
        path, model=partial_model, target=partial_learner.target,
        optimizer=partial_optimizer, population_hash="p", replay_manifest_hash="r",
        step=6, replay=partial_replay, training_identity_hash="identity",
    )

    resumed_model, resumed_optimizer, resumed_learner = build()
    resumed_replay = _priority_replay()
    resumed_learner.steps = load_checkpoint(
        path, model=resumed_model, target=resumed_learner.target,
        optimizer=resumed_optimizer, expected_population_hash="p",
        expected_replay_manifest_hash="r", map_location="cpu", replay=resumed_replay,
        expected_training_identity_hash="identity",
    )
    advance(resumed_model, resumed_learner, resumed_replay, 7, 12)
    assert resumed_learner.steps == full_learner.steps == 12
    assert full_replay._priorities == resumed_replay._priorities
    assert all(
        torch.equal(left, right)
        for left, right in zip(
            full_model.state_dict().values(), resumed_model.state_dict().values(), strict=True
        )
    )
    assert all(
        torch.equal(left, right)
        for left, right in zip(
            full_learner.target.state_dict().values(),
            resumed_learner.target.state_dict().values(),
            strict=True,
        )
    )
    expected = full_replay.sample(4, beta=.8, seed=999)
    actual = resumed_replay.sample(4, beta=.8, seed=999)
    assert (actual.indices, actual.weights) == (expected.indices, expected.weights)


def test_alternating_replay_priority_state_round_trip() -> None:
    from mage_ptcg.policy_learning.r2d3.online_collection import AlternatingReplayPartitions
    live = AlternatingReplayPartitions(_priority_replay(), _priority_replay())
    live.offline.update_priorities((0,), (10.0,))
    live.online.update_priorities((1,), (20.0,))
    state = live.priority_state()
    restored = AlternatingReplayPartitions(_priority_replay(), _priority_replay())
    restored.load_priority_state(state)
    assert restored.offline._priorities == live.offline._priorities
    assert restored.online._priorities == live.online._priorities


def test_psro_checkpoint_skips_orphan_and_corrupt_latest_pair(tmp_path: Path) -> None:
    module = _performance_module()
    controller = object.__new__(module.Controller)
    controller.artifact = tmp_path
    controller.profile = module.PROFILES["smoke"]
    directory = tmp_path / "psro_online_checkpoints"
    replay = _priority_replay()
    replay_path = directory / "checkpoint-000002-replay.json"
    saved = replay.save(replay_path)
    rows = [
        {
            "game_id": f"psro-online-{index:06d}",
            "meta_strategy_hash": "mixture",
            "candidate_policy_version": "policy",
            "result": "DONE",
            "sequence_count": 2,
        }
        for index in range(2)
    ]
    content = {
        "schema": "r2d3-psro-online-checkpoint-v2",
        "games": 2,
        "sequences": 4,
        "replay_sha256": saved["sha256"],
        "mixture_hash": "mixture",
        "candidate_policy_version": "policy",
        "rows": rows,
    }
    module.atomic_json(
        directory / "checkpoint-000002-state.json",
        {**content, "content_hash": module.digest(content)},
    )
    # A replay-only checkpoint is an interrupted pair and must be ignored.
    replay.save(directory / "checkpoint-000004-replay.json")
    # A newer corrupt state must fall back to the latest complete pair.
    (directory / "checkpoint-000003-state.json").write_text("{")
    loaded, loaded_rows = controller._load_psro_online_checkpoint(
        mixture_hash="mixture", policy_hash="policy",
        replay_type=PrioritizedSequenceReplay,
    )
    assert loaded is not None and len(loaded) == 4
    assert loaded_rows == rows
    recovery = json.loads((tmp_path / "psro_online_checkpoint_rejections.json").read_text())
    assert recovery["status"] == "RECOVERED_FROM_PRIOR_VALID"


def test_psro_payoff_checkpoint_resumes_exact_remaining_prefix(tmp_path: Path) -> None:
    module = _performance_module()
    path = tmp_path / "payoff_checkpoint.json"
    jobs = [
        {"index": index, "game_id": f"game-{index}", "seed": 100 + index}
        for index in range(5)
    ]
    first_calls: list[int] = []

    def interrupted(job):
        first_calls.append(job["index"])
        if job["index"] == 2:
            raise RuntimeError("simulated WSL stop")
        return {"status": "DONE", "payoff_left": 1.0, "winner": 0}

    with pytest.raises(RuntimeError, match="simulated WSL stop"):
        module.durable_psro_payoff_prefix(
            path, identity_hash="identity", jobs=jobs, play=interrupted,
        )
    state = json.loads(path.read_text())
    assert state["completed"] == 2 and first_calls == [0, 1, 2]

    resumed_calls: list[int] = []

    def resumed(job):
        resumed_calls.append(job["index"])
        return {"status": "DONE", "payoff_left": -1.0, "winner": 1}

    rows = module.durable_psro_payoff_prefix(
        path, identity_hash="identity", jobs=jobs, play=resumed,
    )
    assert len(rows) == 5 and resumed_calls == [2, 3, 4]
    module.durable_psro_payoff_prefix(
        path, identity_hash="identity", jobs=jobs,
        play=lambda _job: pytest.fail("completed payoff game was replayed"),
    )


class _InlineThreadPool:
    """Stand in for a spawn pool so parallel ordering is testable in-process."""

    def __init__(self, max_workers=None, mp_context=None):
        from concurrent.futures import ThreadPoolExecutor
        del mp_context
        self._executor = ThreadPoolExecutor(max_workers=max_workers)

    def submit(self, function, *args, **kwargs):
        return self._executor.submit(function, *args, **kwargs)

    def __enter__(self):
        return self

    def __exit__(self, *exception):
        self._executor.shutdown(wait=True)
        return False


def test_psro_payoff_parallel_prefix_preserves_schedule_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _performance_module()
    monkeypatch.setattr("concurrent.futures.ProcessPoolExecutor", _InlineThreadPool)
    path = tmp_path / "payoff_checkpoint.json"
    jobs = [
        {"index": index, "game_id": f"game-{index}", "seed": 100 + index}
        for index in range(8)
    ]

    def out_of_order(item):
        # Later games finish first; the checkpoint must still advance in order.
        time.sleep((len(jobs) - int(item["index"])) * 0.01)
        return int(item["index"]), {"status": "DONE", "winner": 0,
                                    "payoff_left": 1.0 if int(item["index"]) % 2 else -1.0}

    rows = module.durable_psro_payoff_prefix(
        path, identity_hash="identity", jobs=jobs,
        play=lambda _item: pytest.fail("parallel mode must not use the sequential path"),
        workers=4, job=out_of_order,
    )
    assert [row["job"]["index"] for row in rows] == list(range(8))
    assert [row["payoff_left"] for row in rows] == [-1.0, 1.0] * 4
    state = json.loads(path.read_text())
    assert state["completed"] == 8
    assert [row["job"]["game_id"] for row in state["rows"]] == [f"game-{i}" for i in range(8)]
    # A completed schedule must never replay a game on resume.
    module.durable_psro_payoff_prefix(
        path, identity_hash="identity", jobs=jobs,
        play=lambda _item: pytest.fail("completed payoff game was replayed"),
        workers=4, job=lambda _item: pytest.fail("completed payoff game was replayed"),
    )


def test_psro_payoff_parallel_prefix_persists_only_contiguous_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _performance_module()
    monkeypatch.setattr("concurrent.futures.ProcessPoolExecutor", _InlineThreadPool)
    path = tmp_path / "payoff_checkpoint.json"
    jobs = [
        {"index": index, "game_id": f"game-{index}", "seed": 100 + index}
        for index in range(6)
    ]

    def failing(item):
        index = int(item["index"])
        if index == 1:
            # Fail last so every other game in the window has already reported:
            # the hole at 1 is what must stop the checkpoint, not a lost race.
            time.sleep(0.2)
            raise RuntimeError("simulated WSL stop")
        return index, {"status": "DONE", "winner": 0, "payoff_left": 1.0}

    with pytest.raises(RuntimeError, match="simulated WSL stop"):
        module.durable_psro_payoff_prefix(
            path, identity_hash="identity", jobs=jobs,
            play=lambda _item: pytest.fail("sequential path"),
            workers=4, job=failing,
        )
    # Game 0 landed; games 2..5 may have finished but sit after the hole at 1, so
    # they must not be recorded or the resume would skip game 1 entirely.
    state = json.loads(path.read_text())
    assert state["completed"] == 1
    assert [row["job"]["index"] for row in state["rows"]] == [0]

    replayed: list[int] = []

    def recovered(item):
        replayed.append(int(item["index"]))
        return int(item["index"]), {"status": "DONE", "winner": 1, "payoff_left": -1.0}

    rows = module.durable_psro_payoff_prefix(
        path, identity_hash="identity", jobs=jobs,
        play=lambda _item: pytest.fail("sequential path"),
        workers=4, job=recovered,
    )
    assert sorted(replayed) == [1, 2, 3, 4, 5]
    assert [row["job"]["index"] for row in rows] == list(range(6))


def test_submitted_runtime_error_survives_a_process_boundary() -> None:
    import pickle

    from mage_ptcg.policy_learning.submitted_runtime import SubmittedRuntimeError

    # A pool worker pickles this to report a fault.  Losing `code` turned a
    # CALLBACK_TIMEOUT into an opaque BrokenProcessPool at the CABT fault gate.
    original = SubmittedRuntimeError("CALLBACK_TIMEOUT", "submitted callback exceeded 8.000s")
    restored = pickle.loads(pickle.dumps(original))
    assert isinstance(restored, SubmittedRuntimeError)
    assert restored.code == "CALLBACK_TIMEOUT"
    assert str(restored) == "submitted callback exceeded 8.000s"


def test_rebaseline_refuses_to_retain_any_started_stage(tmp_path: Path) -> None:
    module = _performance_module()
    controller = object.__new__(module.Controller)
    controller.artifact = tmp_path / "artifact"
    controller.profile = module.PROFILES["smoke"]
    stage = controller.stage_dir("source_freeze")
    stage.mkdir(parents=True)
    (stage / "status.json").write_text("{}")
    identity = {
        "artifact_root": str(controller.artifact),
        "profile_hash": module.digest(module.asdict(controller.profile)),
    }
    with pytest.raises(RuntimeError, match="source identity change"):
        controller.rebaseline_identity(
            {**identity, "source_identity_hash": "old"},
            {**identity, "source_identity_hash": "new"},
        )


def test_each_training_run_gets_an_isolated_per_state(tmp_path: Path) -> None:
    module = _performance_module()
    replay = _priority_replay()
    replay.save(tmp_path / "replay.json")
    (tmp_path / "replay_manifest.json").write_text("{}")
    controller = object.__new__(module.Controller)
    controller.artifact = tmp_path
    controller.context = {}
    first = controller.fresh_training_replay()
    second = controller.fresh_training_replay()
    first.update_priorities((0,), (100.0,))
    assert first._priorities != second._priorities
    assert second._priorities == replay._priorities
    assert first._items is not second._items
    assert first._items[0] is second._items[0]


@pytest.mark.parametrize("priority", [float("nan"), float("inf"), float("-inf")])
def test_replay_rejects_non_finite_priorities(priority: float) -> None:
    replay = _priority_replay()
    with pytest.raises(ValueError, match="invalid replay priority"):
        replay.update_priorities((0,), (priority,))


@pytest.mark.parametrize("core", ["gru", "lru"])
def test_true_recurrent_core_processes_twenty_steps_and_history_changes_q(core: str) -> None:
    torch = pytest.importorskip("torch")
    torch.manual_seed(17)
    model = RecurrentDistributionalQ(R2D3ModelConfig(state_size=8, action_size=4, hidden_size=8, atoms=5, recurrent_core=core))
    current = torch.zeros(1, 20, 8)
    actions = torch.rand(1, 20, 2, 4)
    legal = torch.ones(1, 20, 2, dtype=torch.bool)
    burn_a = torch.zeros(1, 8, 8)
    burn_b = burn_a.clone(); burn_b[:, :, 0] = 1.0
    mask = torch.ones(1, 8, dtype=torch.bool)
    hidden_a = model.burn_in(burn_a, mask)
    hidden_b = model.burn_in(burn_b, mask)
    output_a = model(current, actions, legal, hidden_a)
    output_b = model(current, actions, legal, hidden_b)
    assert output_a["q"].shape == (1, 20, 2)
    assert not torch.allclose(hidden_a, hidden_b)
    assert not torch.allclose(output_a["q"], output_b["q"])


@pytest.mark.parametrize("core", ["gru", "lru"])
def test_late_unroll_loss_backpropagates_through_earlier_recurrent_steps(core: str) -> None:
    torch = pytest.importorskip("torch")
    torch.manual_seed(23)
    model = RecurrentDistributionalQ(R2D3ModelConfig(state_size=8, action_size=4, hidden_size=8, atoms=5, recurrent_core=core))
    states = torch.rand(1, 20, 8, requires_grad=True)
    output = model(states, torch.rand(1, 20, 2, 4), torch.ones(1, 20, 2, dtype=torch.bool))
    output["q"][:, -1].sum().backward()
    assert states.grad is not None
    assert float(states.grad[:, 0].abs().sum()) > 0.0


def test_sequence_learner_uses_all_steps_cql_and_itemwise_priority() -> None:
    torch = pytest.importorskip("torch")
    from scripts.policy_learning.run_submitted_r2d3_e2e import _learner_batch
    first = [_transition(terminal=index == 5, demo=True) for index in range(6)]
    second = [replace(_transition(terminal=index == 3), reward=1.0 if index == 3 else 0.0) for index in range(4)]
    sample = type("Sample", (), {
        "sequences": (
            SequenceBatch(tuple(first[:2]), tuple(first[2:]), 1.0, "a", "episode-a"),
            SequenceBatch((), tuple(second), 1.0, "b", "episode-b"),
        ),
        "weights": (1.0, 0.7),
    })()
    batch = _learner_batch(sample, torch.device("cpu"))
    assert batch["states"].shape[:2] == (2, 4)
    assert batch["burn_in_mask"].sum(dim=1).tolist() == [2, 0]
    model = RecurrentDistributionalQ(R2D3ModelConfig(state_size=8, action_size=4, hidden_size=8, atoms=5))
    learner = R2D3Learner(model, torch.optim.AdamW(model.parameters(), lr=1e-3),
                         config=LearnerConfig(conservative_weight=.1, target_update_interval=1))
    metrics = learner.update(**batch)
    assert metrics["sequence_length"] == 4.0
    assert metrics["conservative_loss"] >= 0.0
    assert len(metrics["sequence_priorities"]) == 2
    assert metrics["sequence_priorities"][0] != metrics["sequence_priorities"][1]


def test_episode_first_sampling_caps_overlapping_windows_when_possible() -> None:
    base = PrioritizedSequenceReplay(32)
    for episode in range(4):
        transitions = [_transition(terminal=index == 7) for index in range(8)]
        base.add(SequenceBatch((), tuple(transitions), 1.0, f"source-{episode}", f"episode-{episode}"))
    replay = PrioritizedSequenceReplay.windowed(base, stride=2)
    sample = replay.sample(4, beta=.4, seed=5, episode_first=True)
    assert len({sequence.episode_id for sequence in sample.sequences}) == 4
    updates = replay.update_priorities(sample.indices, [1.0, 2.0, 3.0, 4.0], importance=sample.weights)
    assert all({"sample_id", "sequence_id", "old_priority", "new_priority", "importance_weight"} <= set(row) for row in updates)


def test_episode_first_sampling_is_deterministic_after_cached_group_build() -> None:
    base = PrioritizedSequenceReplay(64)
    for episode in range(8):
        transitions = [_transition(terminal=index == 7) for index in range(8)]
        base.add(SequenceBatch((), tuple(transitions), 1.0, f"source-{episode}", f"episode-{episode}"))
    replay = PrioritizedSequenceReplay.windowed(base, stride=2)
    first = replay.sample(8, beta=.4, seed=17, episode_first=True)
    second = replay.sample(8, beta=.4, seed=17, episode_first=True)
    assert first.indices == second.indices
    assert len({sequence.episode_id for sequence in first.sequences}) == 8


def test_episode_sampler_reports_exhaustion_without_relying_on_float_total() -> None:
    from mage_ptcg.policy_learning.r2d3.replay import _FenwickEpisodeSampler

    # Draining the tree subtracts floats, so the running total can settle on a
    # tiny *positive* residue.  Exhaustion must be detected by entry count, or a
    # caller's `total <= 0` refill check is skipped and the next draw lands on an
    # already-removed episode.
    for weights in ([1 / 3, 1 / 3, 1 / 3], [0.1] * 5, [0.7, 0.1, 0.1, 0.05, 0.05]):
        sampler = _FenwickEpisodeSampler(list(weights))
        rng = random.Random(0)
        drained = [sampler.pop(rng) for _ in range(len(weights))]
        assert sorted(drained) == list(range(len(weights)))
        assert sampler.remaining == 0
        with pytest.raises(RuntimeError, match="episode sampler is empty"):
            sampler.pop(rng)


def test_episode_first_sampling_refills_when_batch_exceeds_episode_count() -> None:
    # PSRO best-response trains on a small online partition, so the batch can ask
    # for more episodes than exist and force a refill.  This crashed the smoke
    # campaign at psro_best_response with "selected a removed entry".
    base = PrioritizedSequenceReplay(32)
    for episode in range(3):
        transitions = [_transition(terminal=index == 3) for index in range(4)]
        base.add(SequenceBatch((), tuple(transitions), 1.0, f"source-{episode}", f"episode-{episode}"))
    sample = base.sample(9, beta=.4, seed=3, episode_first=True)
    assert len(sample.sequences) == 9
    assert {sequence.episode_id for sequence in sample.sequences} == {f"episode-{i}" for i in range(3)}


def test_vectorized_categorical_projection_matches_atom_reference() -> None:
    torch = pytest.importorskip("torch")
    torch.manual_seed(31)
    support = torch.linspace(-1.0, 1.0, 51)
    rewards = torch.rand(13) * 2.0 - 1.0
    discounts = torch.rand(13)
    probabilities = torch.softmax(torch.randn(13, 51), dim=-1)
    projected = project_categorical(rewards, discounts, probabilities, support)

    target = (rewards[:, None] + discounts[:, None] * support[None]).clamp(-1.0, 1.0)
    position = (target + 1.0) / (2.0 / 50.0)
    lower, upper = position.floor().long(), position.ceil().long()
    reference = torch.zeros_like(probabilities)
    for atom in range(51):
        reference.scatter_add_(
            1,
            lower[:, atom:atom + 1],
            probabilities[:, atom:atom + 1]
            * (
                upper[:, atom:atom + 1].float()
                - position[:, atom:atom + 1]
                + (lower[:, atom:atom + 1] == upper[:, atom:atom + 1]).float()
            ),
        )
        reference.scatter_add_(
            1,
            upper[:, atom:atom + 1],
            probabilities[:, atom:atom + 1]
            * (position[:, atom:atom + 1] - lower[:, atom:atom + 1].float()),
        )
    torch.testing.assert_close(projected, reference, rtol=1e-6, atol=1e-7)
    torch.testing.assert_close(projected.sum(dim=1), torch.ones(13), rtol=1e-6, atol=1e-6)


def test_psro_mixture_sampling_is_frozen_deterministic_and_fully_attributed() -> None:
    mixture = MixtureManifest.build([
        MixtureMember("rule-v0", .25, "a" * 64, "lineage-a", "RULE", "rule"),
        MixtureMember("submitted", .75, "b" * 64, "lineage-b", "ALAKAZAM", "submitted"),
    ])
    assert mixture.sample(seed=7) == mixture.sample(seed=7)
    sampled = mixture.sample(seed=9)
    record = collection_record(game_id="g", mixture=mixture, member=sampled, candidate_policy_version="c" * 64,
                               result="DONE", winner=0, candidate_side=0, sequence_count=3)
    assert record["meta_strategy_hash"] == mixture.mixture_hash
    assert record["sampled_opponent"] == sampled.opponent_policy_id
    assert record["sampling_probability"] == sampled.probability
    assert record["source_policy_hash"] == sampled.policy_hash
    with pytest.raises(ValueError):
        MixtureManifest.build([MixtureMember("bad", .5, "a", "l", "f", "k")])


def test_snapshot_uses_qualified_commit_and_rejects_hash_mismatch(tmp_path: Path) -> None:
    repo = tmp_path / "repo"; repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    policy = "def agent(observation):\n    return []\n"; deck = "\n".join(["1"] * 60) + "\n"
    (repo / "main.py").write_text(policy); (repo / "deck.csv").write_text(deck)
    subprocess.run(["git", "add", "main.py", "deck.csv"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=repo, check=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    asset = SubmittedAsset("agents/test", "origin/agents/test", commit, commit, f"{commit}:agents/test", "PROXY", "deck.csv",
        hashlib.sha256(deck.encode()).hexdigest(), "test", hashlib.sha256(policy.encode()).hexdigest(), "", "", "TEST", "main.py:agent",
        "PROXY_RUNTIME_PASSED", False, True, True, current_ref_commit="f" * 40, ref_matches_source_commit=False,
        qualification="TRAINING_ELIGIBLE")
    manifest = pin_snapshot(repo, asset, tmp_path / "snapshot")
    assert manifest["source_commit"] == commit and manifest["ref_drift"] is True
    bad = replace(asset, policy_hash="0" * 64)
    with pytest.raises(SubmittedRuntimeError, match="expected"):
        pin_snapshot(repo, bad, tmp_path / "bad-snapshot")


def test_candidate_exact_deck_and_psro_duplicate_guard() -> None:
    torch = pytest.importorskip("torch")
    model = RecurrentDistributionalQ(R2D3ModelConfig())
    with pytest.raises(ValueError, match="60-card"):
        R2D3CandidatePolicy(model, deck=[1] * 59, device=torch.device("cpu"), policy_version="v")
    state = PSROState(); member = PopulationMember("a", "baseline", "A", "hash-a"); state.add_member(member)
    with pytest.raises(ValueError):
        state.add_member(member, against_existing=[0.0])
    assert state.meta_strategy() == {"a": 1.0}


@pytest.mark.skipif(not __import__("torch").cuda.is_available(), reason="CUDA is unavailable")
def test_cuda_forward_backward_bf16_and_memory_telemetry() -> None:
    torch = pytest.importorskip("torch")
    device = torch.device("cuda:0"); torch.cuda.reset_peak_memory_stats()
    model = RecurrentDistributionalQ(R2D3ModelConfig(state_size=8, action_size=4, hidden_size=8, atoms=5)).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=1e-3); learner = R2D3Learner(model, optimizer)
    states = torch.rand(2, 8, device=device); actions = torch.rand(2, 2, 4, device=device); mask = torch.ones(2, 2, dtype=torch.bool, device=device)
    with torch.autocast(device_type="cuda", dtype=torch.bfloat16, enabled=torch.cuda.is_bf16_supported()):
        learner.update(states, actions, mask, torch.tensor([0, 1], device=device), torch.zeros(2, device=device), torch.full((2,), .99, device=device),
                       states, actions, mask, torch.ones(2, device=device))
    assert torch.cuda.max_memory_allocated() > 0


def _holdout_controller(module, tmp_path: Path, stages: dict[str, dict]):
    """A Controller bound to an artifact whose prior stage outputs are fixtures."""
    controller = object.__new__(module.Controller)
    controller.artifact = tmp_path / "artifact"
    controller.profile = module.PROFILES["smoke"]
    controller.consumed: list[str] = []
    for name, output in stages.items():
        directory = controller.artifact / "stages" / name
        directory.mkdir(parents=True, exist_ok=True)
        module.atomic_json(directory / "output_manifest.json", {"stage": name, "status": "PASS", "fault_count": 0, **output})
    controller.load_full_model = lambda: (object(), "policy-hash", "/checkpoints/full/r2d3-step-000020.pt", "gru")
    def validate(model, policy_hash, split, games, *, label, checkpoint=None, core=None, seed_namespace=None):
        assert seed_namespace == label
        controller.consumed.append(split)
        return [{"winner": index % 2, "candidate_side": 0, "legal": True} for index in range(games)]
    controller.validate = validate
    return controller


def _passing_stages(module, *, deck_win_rate: float | None = None) -> dict[str, dict]:
    threshold = module.PROFILES["smoke"].holdout_min_win_rate
    deck_win_rate = threshold if deck_win_rate is None else deck_win_rate
    return {
        "development_validation": {"win_rate": threshold, "games": 16},
        "deck_holdout_gate": {"holdout_used": True, "win_rate": deck_win_rate, "games": 16},
        "psro_payoff": {"population": 4, "games": 24},
        "psro_online_collection": {"games": 8, "sequences": 8, "mixture_hash": "m"},
        "psro_best_response": {"seeds": 1, "updates_each": 20, "win_rate": threshold},
    }


def test_final_holdout_requires_every_upstream_gate_not_only_development(tmp_path: Path) -> None:
    module = _performance_module()
    threshold = module.PROFILES["smoke"].holdout_min_win_rate
    for missing in ("deck_holdout_gate", "psro_payoff", "psro_online_collection", "psro_best_response"):
        stages = _passing_stages(module)
        stages.pop(missing)
        controller = _holdout_controller(module, tmp_path / missing, stages)
        result = controller.conditional_holdout(stage="final_holdout", split="final_holdout", games=8,
                                                filename="final_holdout_results.json")
        assert result["holdout_used"] is False, f"{missing} absent must not consume the final holdout"
        assert controller.consumed == []
        assert not (controller.artifact / "final_holdout_holdout_used.json").exists()
        recorded = json.loads((controller.artifact / "final_holdout_results.json").read_text())
        assert recorded["status"] == "NOT_USED" and any(missing in str(item) for item in recorded["prerequisites"])
    # A deck holdout that ran but lost is also an unmet prerequisite.
    controller = _holdout_controller(module, tmp_path / "deck-lost", _passing_stages(module, deck_win_rate=threshold - .01))
    result = controller.conditional_holdout(stage="final_holdout", split="final_holdout", games=8,
                                            filename="final_holdout_results.json")
    assert result["holdout_used"] is False and controller.consumed == []


def test_final_holdout_is_consumed_once_all_prerequisites_hold(tmp_path: Path) -> None:
    module = _performance_module()
    controller = _holdout_controller(module, tmp_path, _passing_stages(module))
    result = controller.conditional_holdout(stage="final_holdout", split="final_holdout", games=8,
                                            filename="final_holdout_results.json")
    assert result["holdout_used"] is True and controller.consumed == ["final_holdout"]
    marker = json.loads((controller.artifact / "final_holdout_holdout_used.json").read_text())
    assert marker["status"] == "USED" and marker["stage"] == "final_holdout"
    with pytest.raises(RuntimeError, match="one-time"):
        controller.conditional_holdout(stage="final_holdout", split="final_holdout", games=8,
                                       filename="final_holdout_results.json")


@pytest.mark.parametrize(
    ("stage", "split"),
    [("deck_holdout", "deck_holdout"), ("final_holdout", "final_holdout")],
)
def test_interrupted_holdout_stays_consumed_and_is_never_replayed(
    tmp_path: Path, stage: str, split: str
) -> None:
    module = _performance_module()
    controller = _holdout_controller(module, tmp_path, _passing_stages(module))
    def exploding(model, policy_hash, split, games, *, label, checkpoint=None, core=None, seed_namespace=None):
        assert seed_namespace == label
        controller.consumed.append(split)
        raise RuntimeError("CABT crashed after the holdout games started")
    controller.validate = exploding
    with pytest.raises(RuntimeError, match="CABT crashed"):
        controller.conditional_holdout(stage=stage, split=split, games=8,
                                       filename=f"{split}_results.json")
    # The reservation must survive the crash, so a retry cannot silently replay it.
    marker = json.loads((controller.artifact / f"{split}_holdout_used.json").read_text())
    assert marker["status"] == "RESERVED"
    controller.validate = lambda *a, **k: []
    with pytest.raises(RuntimeError, match="one-time"):
        controller.conditional_holdout(stage=stage, split=split, games=8,
                                       filename=f"{split}_results.json")


def test_holdout_reservation_and_used_markers_are_durable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _performance_module()
    controller = _holdout_controller(module, tmp_path, _passing_stages(module))
    original = module.atomic_json
    marker_writes: list[bool] = []

    def recording(path, value, *, durable=False):
        if str(path).endswith("_holdout_used.json"):
            marker_writes.append(bool(durable))
        return original(path, value, durable=durable)

    monkeypatch.setattr(module, "atomic_json", recording)
    controller.conditional_holdout(
        stage="deck_holdout", split="deck_holdout", games=8,
        filename="deck_holdout_results.json",
    )
    assert marker_writes == [True, True]


def test_promotion_requires_holdout_win_rates_not_only_holdout_usage(tmp_path: Path) -> None:
    module = _performance_module()
    threshold = module.PROFILES["smoke"].holdout_min_win_rate
    stages = _passing_stages(module)
    stages["final_holdout_gate"] = {"holdout_used": True, "win_rate": threshold - .01, "games": 8}
    controller = _holdout_controller(module, tmp_path, stages)
    assert controller.run_promotion()["decision"] == "NO_PROMOTION_RECOMMENDED"
    stages["final_holdout_gate"] = {"holdout_used": True, "win_rate": threshold, "games": 8}
    controller = _holdout_controller(module, tmp_path / "ok", stages)
    assert controller.run_promotion()["decision"] == "PROMOTION_ELIGIBLE"


def _legacy_sample(replay, batch_size: int, *, beta: float, demonstration_ratio: float = 0.0, seed: int | None = None):
    """The pre-optimisation sampler, verbatim, as the equivalence oracle."""
    import random as _random
    rng = _random.Random(seed)
    weights = [value ** replay.alpha for value in replay._priorities]
    total = sum(weights); probabilities = [value / total for value in weights]
    demos = [index for index in range(len(replay)) if replay._is_demonstration(index)]
    requested = min(batch_size, round(batch_size * demonstration_ratio))
    selected = rng.choices(demos, k=requested) if demos else []
    selected.extend(rng.choices(range(len(replay)), weights=probabilities, k=batch_size - len(selected)))
    correction = [(len(replay) * probabilities[index]) ** (-beta) for index in selected]
    maximum = max(correction)
    return tuple(selected), tuple(value / maximum for value in correction), len([i for i in selected if i in demos])


def test_cached_sampler_is_bit_identical_to_the_previous_implementation() -> None:
    base = PrioritizedSequenceReplay(4096)
    for index in range(240):
        transitions = [_transition(terminal=step == 12, demo=index % 4 == 0) for step in range(13)]
        base.add(SequenceBatch((), tuple(transitions), 1.0 + 0.01 * index, f"episode-{index}"))
    replay = PrioritizedSequenceReplay.windowed(base, stride=4)
    assert len(replay) == 240 * 4
    for round_index in range(12):
        for ratio in (0.0, 1 / 16, 0.5, 1.0):
            expected = _legacy_sample(replay, 32, beta=.4 + .01 * round_index, demonstration_ratio=ratio, seed=round_index)
            sample = replay.sample(32, beta=.4 + .01 * round_index, demonstration_ratio=ratio, seed=round_index)
            assert (sample.indices, sample.weights, sample.demonstrations) == expected
        # Priority updates must invalidate the cached tables, not survive them.
        replay.update_priorities(sample.indices, [0.5 + 0.001 * index for index in range(len(sample.indices))])
    # Appending new sequences must also invalidate demonstration membership.
    fresh = PrioritizedSequenceReplay(16)
    fresh.add(SequenceBatch((), (_transition(terminal=True),), 1.0, "online"))
    assert fresh.sample(1, beta=.4, demonstration_ratio=1.0, seed=1).demonstrations == 0
    fresh.add(SequenceBatch((), (_transition(terminal=True, demo=True),), 1.0, "demo"))
    assert fresh.sample(4, beta=.4, demonstration_ratio=1.0, seed=1).demonstrations == 4
