from __future__ import annotations

from pathlib import Path

import pytest
import torch

import mage_ptcg.bootstrap_champion.initializer as bootstrap_initializer
from mage_ptcg.bootstrap_champion.contracts import (
    BootstrapChampionManifest,
    DeckAsset,
    DeckCompatibility,
    InitializationMode,
    JointCandidate,
    PolicyAsset,
)
from mage_ptcg.bootstrap_champion.initializer import (
    initialize_from_checkpoint,
    initialize_from_distillation,
    load_bootstrap_weights,
)
from mage_ptcg.continuous_league.candidate_runtime import load_runtime_policy
from mage_ptcg.continuous_league.contracts import content_id
from mage_ptcg.observability.cabt_trace import canonical_deck_sha256
from mage_ptcg.policy_learning.r2d3.model import (
    R2D3ModelConfig,
    RecurrentDistributionalQ,
)


def _sha(character: str) -> str:
    return character * 64


def _champion(tmp_path: Path) -> BootstrapChampionManifest:
    cards = [1] * 60
    deck_path = tmp_path / "deck.csv"
    deck_path.write_text("\n".join(map(str, cards)) + "\n", encoding="utf-8")
    deck = DeckAsset("deck", canonical_deck_sha256(cards), str(deck_path), "source", _sha("a"))
    policy = PolicyAsset("policy", _sha("b"), "runtime_policy", "runtime", _sha("c"), _sha("d"), DeckCompatibility.EXACT_DECK, deck.deck_hash, "source", _sha("e"))
    return BootstrapChampionManifest.build(
        candidate_registry_id=_sha("1"),
        screen_benchmark_id=_sha("2"),
        validation_benchmark_id=_sha("3"),
        candidate=JointCandidate(deck, policy, _sha("4")),
        initialization_mode=InitializationMode.DIRECT_CHECKPOINT,
        score_summary={"fault_count": 0},
    )


def test_direct_initializer_copies_online_weights_and_not_resume_state(tmp_path: Path) -> None:
    model = torch.nn.Linear(2, 2)
    source = tmp_path / "source.pt"
    torch.save({"schema": "r2d3-checkpoint-v3", "model": model.state_dict(), "target": {"wrong": torch.tensor(1)}, "optimizer": {"stale": True}, "step": 99}, source)

    manifest = initialize_from_checkpoint(
        source_checkpoint=source,
        champion=_champion(tmp_path),
        model_config_hash=_sha("5"),
        action_schema_hash=_sha("6"),
        output=tmp_path / "bootstrap",
    )
    restored = torch.nn.Linear(2, 2)
    target = torch.nn.Linear(2, 2)
    load_bootstrap_weights(tmp_path / "bootstrap", model=restored, target=target, expected_manifest=manifest)

    assert manifest.source_checkpoint_id
    assert not manifest.teacher_dataset_id
    assert all(torch.equal(value, model.state_dict()[name]) for name, value in restored.state_dict().items())
    assert all(torch.equal(value, restored.state_dict()[name]) for name, value in target.state_dict().items())


def test_direct_initializer_can_resume_an_identical_step_zero_write(tmp_path: Path) -> None:
    model = torch.nn.Linear(2, 2)
    source = tmp_path / "source.pt"
    torch.save({"schema": "r2d3-checkpoint-v3", "model": model.state_dict()}, source)
    arguments = {
        "source_checkpoint": source,
        "champion": _champion(tmp_path),
        "model_config_hash": _sha("5"),
        "action_schema_hash": _sha("6"),
        "output": tmp_path / "bootstrap",
    }

    first = initialize_from_checkpoint(**arguments)
    second = initialize_from_checkpoint(**arguments)

    assert second.bootstrap_checkpoint_id == first.bootstrap_checkpoint_id


def test_direct_initializer_rejects_source_weights_with_different_shape(tmp_path: Path) -> None:
    source = tmp_path / "source.pt"
    torch.save({"schema": "r2d3-checkpoint-v3", "model": torch.nn.Linear(3, 2).state_dict()}, source)

    with pytest.raises(ValueError, match="shape"):
        initialize_from_checkpoint(
            source_checkpoint=source,
            champion=_champion(tmp_path),
            model_config_hash=_sha("5"),
            action_schema_hash=_sha("6"),
            output=tmp_path / "bootstrap",
            expected_model=torch.nn.Linear(2, 2),
        )


def test_direct_initializer_accepts_runtime_weight_directory(tmp_path: Path) -> None:
    model = torch.nn.Linear(2, 2)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    torch.save(model.state_dict(), runtime / "weights.pt")
    (runtime / "manifest.json").write_text('{"weights_file":"weights.pt"}', encoding="utf-8")

    manifest = initialize_from_checkpoint(
        source_checkpoint=runtime,
        champion=_champion(tmp_path),
        model_config_hash=_sha("5"),
        action_schema_hash=_sha("6"),
        output=tmp_path / "bootstrap",
        expected_model=torch.nn.Linear(2, 2),
    )

    assert manifest.initialization_mode is InitializationMode.DIRECT_CHECKPOINT


def test_teacher_initializer_creates_a_step_zero_bundle_without_optimizer(tmp_path: Path) -> None:
    model = torch.nn.Linear(2, 2)
    distilled = tmp_path / "distilled_weights.pt"
    torch.save(model.state_dict(), distilled)

    manifest = initialize_from_distillation(
        distilled_weights=distilled,
        champion=_champion(tmp_path),
        model_config_hash=_sha("5"),
        action_schema_hash=_sha("6"),
        teacher_dataset_id=_sha("7"),
        output=tmp_path / "bootstrap",
        expected_model=torch.nn.Linear(2, 2),
    )

    restored = torch.nn.Linear(2, 2)
    target = torch.nn.Linear(2, 2)
    load_bootstrap_weights(tmp_path / "bootstrap", model=restored, target=target)
    assert manifest.initialization_mode is InitializationMode.TEACHER_DISTILLATION
    assert manifest.teacher_dataset_id == _sha("7")


def test_bootstrap_bundle_can_be_published_as_a_collectable_runtime_policy(
    tmp_path: Path,
) -> None:
    config = R2D3ModelConfig(
        hidden_size=4,
        atoms=3,
        opponent_classes=2,
        deck_family_classes=2,
        action_type_classes=2,
    )
    source_model = RecurrentDistributionalQ(config)
    distilled = tmp_path / "distilled_weights.pt"
    torch.save(source_model.state_dict(), distilled)
    bundle = tmp_path / "bootstrap"
    manifest = initialize_from_distillation(
        distilled_weights=distilled,
        champion=_champion(tmp_path),
        model_config_hash=content_id("bootstrap-model-config-v1", {
            "state_size": 128,
            "action_size": 64,
            "hidden_size": 4,
            "recurrent_core": "gru",
            "atoms": 3,
            "v_min": -1.1,
            "v_max": 1.1,
            "opponent_classes": 2,
            "deck_family_classes": 2,
            "action_type_classes": 2,
        }),
        action_schema_hash=content_id("bootstrap-action-schema-v1", {
            "state_encoder_version": "semantic-public-state-v1",
            "action_encoder_version": "semantic-legal-action-v1",
            "state_size": 128,
            "action_size": 64,
        }),
        teacher_dataset_id=_sha("7"),
        output=bundle,
        expected_model=RecurrentDistributionalQ(config),
    )

    published = bootstrap_initializer.publish_bootstrap_runtime(
        bootstrap_checkpoint=bundle,
        output_root=tmp_path / "published",
        model_config=config,
        deck=[1] * 60,
    )

    runtime_dir = Path(published["runtime_manifest_path"]).parent
    runtime = load_runtime_policy(runtime_dir)
    assert runtime.runtime_policy_id == published["runtime_policy_id"]
    assert runtime.manifest["bootstrap_checkpoint_id"] == manifest.bootstrap_checkpoint_id
    assert runtime.deck == [1] * 60
