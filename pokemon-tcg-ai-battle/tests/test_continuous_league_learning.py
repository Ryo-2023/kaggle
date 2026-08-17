from __future__ import annotations

from pathlib import Path

import pytest

from mage_ptcg.continuous_league.candidate_runtime import load_runtime_policy
from mage_ptcg.continuous_league.contracts import (
    LeagueContractError,
    atomic_write_json,
    content_id,
    load_json,
)
from mage_ptcg.continuous_league.experience import sequence_record, write_experience_chunk
from mage_ptcg.continuous_league.learner_service import (
    ContinuousLearner,
    ContinuousLearnerConfig,
    learner_progress_status,
    updates_for_replay_passes,
)
from mage_ptcg.continuous_league.population_epoch import (
    PopulationEpoch,
    apply_population_rollover,
    build_rollover_manifest,
)
from mage_ptcg.continuous_league.replay_sealer import (
    import_replay_dataset,
    load_sealed_replay,
    seal_replay_dataset,
)
from mage_ptcg.policy_learning.r2d3.learner import LearnerConfig, R2D3Learner
from mage_ptcg.policy_learning.r2d3.model import (
    R2D3ModelConfig,
    RecurrentDistributionalQ,
)
from mage_ptcg.policy_learning.r2d3.replay import PrioritizedSequenceReplay
from mage_ptcg.policy_learning.r2d3.sequence import R2D3Transition, SequenceBatch
from mage_ptcg.bootstrap_champion.contracts import (
    BootstrapChampionManifest,
    DeckAsset,
    DeckCompatibility,
    InitializationMode,
    JointCandidate,
    PolicyAsset,
)
from mage_ptcg.bootstrap_champion.initializer import initialize_from_checkpoint
from mage_ptcg.observability.cabt_trace import canonical_deck_sha256


torch = pytest.importorskip("torch")


def _hash(value: str) -> str:
    return content_id("test", value)


def _sequence(sequence_id: str) -> SequenceBatch:
    transition = R2D3Transition(
        public_state=(0.1,) * 128,
        legal_actions=((0.1,) * 64, (0.2,) * 64),
        selected_action=0,
        reward=1.0,
        discount=0.0,
        terminal=True,
        behavior_policy_version=_hash("runtime"),
        behavior_source="test",
        opponent_policy_hash=_hash("opponent-policy"),
        opponent_deck_hash=_hash("opponent-deck"),
        opponent_source_lineage=_hash("lineage"),
        opponent_family="TEST",
        own_deck_hash=_hash("own-deck"),
    )
    return SequenceBatch((), (transition,), 1.0, sequence_id, sequence_id)


def _chunk(
    root: Path,
    *,
    population_id: str,
    opponent_id: str,
    prefix: str,
) -> Path:
    records = [
        sequence_record(
            game_id=f"{prefix}-game-{seat}",
            sequence=_sequence(f"{prefix}-sequence-{seat}"),
            candidate_runtime_policy_id=_hash("runtime"),
            opponent_instance_id=opponent_id,
            population_epoch_id=population_id,
            candidate_seat=seat,
            result="win",
        )
        for seat in (0, 1)
    ]
    manifest = write_experience_chunk(
        output_root=root, records=records, collector_id=_hash(f"collector-{prefix}")
    )
    return root / manifest["experience_chunk_id"] / "manifest.json"


def test_imported_compatible_replay_is_sealed_without_jsonl_expansion(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    source_replay = source_root / "replay.json"
    replay = PrioritizedSequenceReplay(capacity=4)
    replay.add(_sequence("external-sequence"))
    saved = replay.save(source_replay)
    source_manifest = source_root / "replay_manifest.json"
    atomic_write_json(
        source_manifest,
        {
            "schema": "r2d3-e2e-replay-manifest-v1",
            "replay_sha256": saved["sha256"],
        },
    )

    version = import_replay_dataset(
        source_replay_path=source_replay,
        source_manifest_path=source_manifest,
        output_root=tmp_path / "sealed",
        population_epoch_id=_hash("external-population"),
        source_label="test-external-replay",
    )

    assert version.sequence_count == 1
    assert load_sealed_replay(version.manifest_path).sequences()[0].sequence_id == (
        "external-sequence"
    )
    manifest = load_json(version.manifest_path)
    assert manifest["schema_version"] == 2
    assert manifest["source_replay"]["label"] == "test-external-replay"
    assert not list((tmp_path / "sealed").rglob("records.jsonl"))


def test_short_continuous_learning_checkpoint_publish_and_strict_resume(
    tmp_path: Path,
) -> None:
    population = PopulationEpoch.build({_hash("opponent"): 1.0})
    chunk = _chunk(
        tmp_path / "chunks",
        population_id=population.population_epoch_id,
        opponent_id=_hash("opponent"),
        prefix="initial",
    )
    replay_version = seal_replay_dataset(
        chunk_manifests=[chunk],
        output_root=tmp_path / "replays",
        population_epoch_id=population.population_epoch_id,
    )
    model_config = R2D3ModelConfig(hidden_size=16, atoms=5)
    service_config = ContinuousLearnerConfig(
        batch_size=1,
        checkpoint_interval=2,
        progress_interval_seconds=60,
        seed=123,
    )
    learner_config = LearnerConfig(target_update_interval=2)
    service = ContinuousLearner(
        replay_manifest_path=replay_version.manifest_path,
        population_epoch_id=population.population_epoch_id,
        output_root=tmp_path / "learner",
        deck=[3] * 60,
        model_config=model_config,
        learner_config=learner_config,
        service_config=service_config,
    )
    before = {
        key: value.detach().clone() for key, value in service.model.state_dict().items()
    }
    result = service.run(max_updates=2)
    assert result["status"] == "COMPLETED"
    assert result["step"] == 2
    assert result["last_metrics"]["loss"] > 0
    assert "sequence_priorities" not in result["last_metrics"]
    assert any(
        not torch.equal(before[key], value)
        for key, value in service.model.state_dict().items()
    )
    checkpoint_path = Path(result["last_checkpoint"]["checkpoint_path"])
    assert checkpoint_path.is_file()
    progress = load_json(tmp_path / "learner" / "progress_summary.json")
    assert progress["status"] == "COMPLETED"
    assert "sequence_priorities" not in progress["last_metrics"]
    durable_replay_manifest = (
        tmp_path
        / "learner"
        / "replay_inputs"
        / replay_version.replay_dataset_version_id
        / "manifest.json"
    )
    assert Path(progress["replay_manifest_path"]) == durable_replay_manifest
    assert durable_replay_manifest.is_file()
    assert (durable_replay_manifest.parent / "replay.json").is_file()
    runtime_id = result["last_checkpoint"]["published"]["runtime_policy_id"]
    runtime = load_runtime_policy(
        tmp_path / "learner" / "stream" / "runtime_policies" / runtime_id
    )
    assert runtime.create(game_id="deck-smoke", seat=0)({}, None) == [3] * 60

    replay_version.manifest_path.unlink()
    replay_version.replay_path.unlink()
    resumed = ContinuousLearner(
        replay_manifest_path=durable_replay_manifest,
        population_epoch_id=population.population_epoch_id,
        output_root=tmp_path / "resumed",
        deck=[3] * 60,
        model_config=model_config,
        learner_config=learner_config,
        service_config=service_config,
        resume_checkpoint=checkpoint_path,
    )
    resumed_result = resumed.run(max_updates=1)
    assert resumed_result["step"] == 3
    assert resumed_result["last_metrics"]["loss"] > 0


def test_replay_pass_budget_and_stale_progress_are_explicit() -> None:
    assert updates_for_replay_passes(
        sequence_count=1_024, batch_size=512, replay_passes=30
    ) == 60
    with pytest.raises(LeagueContractError, match="smaller than one learner batch"):
        updates_for_replay_passes(
            sequence_count=1_024, batch_size=512, replay_passes=0.1
        )
    status = learner_progress_status(
        {"status": "RUNNING", "updated_at": "2026-08-01T00:00:00+00:00"},
        stale_after_seconds=90,
        now=__import__("datetime").datetime(2026, 8, 1, 0, 2, tzinfo=__import__("datetime").timezone.utc),
    )
    assert status == {
        "status": "STALE", "heartbeat_age_seconds": 120.0, "is_stale": True
    }


def test_continuous_learner_bootstrap_initializes_weights_but_not_resume_step(
    tmp_path: Path,
) -> None:
    population = PopulationEpoch.build({_hash("opponent"): 1.0})
    chunk = _chunk(tmp_path / "chunks", population_id=population.population_epoch_id, opponent_id=_hash("opponent"), prefix="bootstrap")
    replay = seal_replay_dataset(chunk_manifests=[chunk], output_root=tmp_path / "replays", population_epoch_id=population.population_epoch_id)
    config = R2D3ModelConfig(hidden_size=16, atoms=5)
    source_model = RecurrentDistributionalQ(config)
    source = tmp_path / "source.pt"
    torch.save({"schema": "r2d3-checkpoint-v3", "model": source_model.state_dict()}, source)
    cards = [3] * 60
    deck_path = tmp_path / "deck.csv"
    deck_path.write_text("\n".join(map(str, cards)) + "\n", encoding="utf-8")
    deck = DeckAsset("deck", canonical_deck_sha256(cards), str(deck_path), "test", _hash("deck-source"))
    policy = PolicyAsset("policy", _hash("policy"), "runtime_policy", "runtime", _hash("adapter"), _hash("runtime"), DeckCompatibility.EXACT_DECK, deck.deck_hash, "test", _hash("policy-source"))
    champion = BootstrapChampionManifest.build(candidate_registry_id=_hash("registry"), screen_benchmark_id=_hash("screen"), validation_benchmark_id=_hash("validation"), candidate=JointCandidate(deck, policy, _hash("sim")), initialization_mode=InitializationMode.DIRECT_CHECKPOINT, score_summary={"fault_count": 0})
    model_hash = content_id("bootstrap-model-config-v1", __import__("dataclasses").asdict(config))
    action_hash = content_id("bootstrap-action-schema-v1", {"state_encoder_version": "semantic-public-state-v1", "action_encoder_version": "semantic-legal-action-v1", "state_size": config.state_size, "action_size": config.action_size})
    initialize_from_checkpoint(source_checkpoint=source, champion=champion, model_config_hash=model_hash, action_schema_hash=action_hash, output=tmp_path / "bootstrap-bundle", expected_model=RecurrentDistributionalQ(config))

    service = ContinuousLearner(
        replay_manifest_path=replay.manifest_path,
        population_epoch_id=population.population_epoch_id,
        output_root=tmp_path / "learner",
        deck=cards,
        model_config=config,
        service_config=ContinuousLearnerConfig(batch_size=1, checkpoint_interval=1, progress_interval_seconds=60),
        bootstrap_checkpoint=tmp_path / "bootstrap-bundle",
    )

    assert service.learner.steps == 0
    assert service.bootstrap_checkpoint_id is not None
    assert all(torch.equal(value, source_model.state_dict()[key]) for key, value in service.model.state_dict().items())


def test_population_rollover_requires_both_seats_and_resumes(tmp_path: Path) -> None:
    old = PopulationEpoch.build({_hash("old-opponent"): 1.0})
    old_chunk = _chunk(
        tmp_path / "old-chunks",
        population_id=old.population_epoch_id,
        opponent_id=_hash("old-opponent"),
        prefix="old",
    )
    old_replay = seal_replay_dataset(
        chunk_manifests=[old_chunk],
        output_root=tmp_path / "old-replay",
        population_epoch_id=old.population_epoch_id,
    )
    config = R2D3ModelConfig(hidden_size=16, atoms=5)
    service_config = ContinuousLearnerConfig(
        batch_size=1,
        checkpoint_interval=1,
        progress_interval_seconds=60,
        seed=321,
    )
    learner_config = LearnerConfig(target_update_interval=2)
    old_service = ContinuousLearner(
        replay_manifest_path=old_replay.manifest_path,
        population_epoch_id=old.population_epoch_id,
        output_root=tmp_path / "old-learner",
        deck=[3] * 60,
        model_config=config,
        learner_config=learner_config,
        service_config=service_config,
    )
    old_result = old_service.run(max_updates=1)
    source_checkpoint = Path(old_result["last_checkpoint"]["checkpoint_path"])

    new_opponent = _hash("new-opponent")
    new = PopulationEpoch.build(
        {_hash("old-opponent"): 0.5, new_opponent: 0.5},
        parent_population_epoch_id=old.population_epoch_id,
    )
    new_chunk = _chunk(
        tmp_path / "new-chunks",
        population_id=new.population_epoch_id,
        opponent_id=new_opponent,
        prefix="new",
    )
    new_replay = seal_replay_dataset(
        chunk_manifests=[new_chunk],
        output_root=tmp_path / "new-replay",
        population_epoch_id=new.population_epoch_id,
        parent_replay_manifest=old_replay.manifest_path,
    )
    assert load_sealed_replay(new_replay.manifest_path).sequences().__len__() == 4
    transition = build_rollover_manifest(
        old_epoch=old,
        new_epoch=new,
        new_opponent_instance_ids=[new_opponent],
        bootstrap_chunk_manifests=[new_chunk],
        global_step=1,
        replay_dataset_version_id=new_replay.replay_dataset_version_id,
        inherit_optimizer=True,
    )
    replay = load_sealed_replay(new_replay.manifest_path)
    model = RecurrentDistributionalQ(config)
    optimizer = torch.optim.AdamW(model.parameters(), lr=service_config.learning_rate)
    learner = R2D3Learner(model, optimizer, config=learner_config)
    scheduler = torch.optim.lr_scheduler.LambdaLR(
        optimizer, lr_lambda=lambda _step: 1.0
    )
    rollover_checkpoint = tmp_path / "rollover.pt"
    apply_population_rollover(
        source_checkpoint_path=source_checkpoint,
        destination_checkpoint_path=rollover_checkpoint,
        model=model,
        target=learner.target,
        optimizer=optimizer,
        scheduler=scheduler,
        replay=replay,
        old_population_epoch_id=old.population_epoch_id,
        old_replay_dataset_version_id=old_replay.replay_dataset_version_id,
        new_population_epoch_id=new.population_epoch_id,
        new_replay_dataset_version_id=new_replay.replay_dataset_version_id,
        transition_manifest=transition,
        inherit_optimizer=True,
        seed=99,
    )
    assert len(set(replay.priority_state()["priorities"])) == 1
    resumed = ContinuousLearner(
        replay_manifest_path=new_replay.manifest_path,
        population_epoch_id=new.population_epoch_id,
        output_root=tmp_path / "new-learner",
        deck=[3] * 60,
        model_config=config,
        learner_config=learner_config,
        service_config=service_config,
        resume_checkpoint=rollover_checkpoint,
        resume_training_identity_hash=transition["population_transition_id"],
    )
    assert resumed.run(max_updates=1)["step"] == 2
