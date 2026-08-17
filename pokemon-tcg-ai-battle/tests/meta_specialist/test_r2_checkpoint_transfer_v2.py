"""Research-only v1 checkpoint to representation-v2 transfer tests."""

from __future__ import annotations

import importlib
import hashlib
from pathlib import Path

import pytest

torch = pytest.importorskip("torch")

from mage_ptcg.meta_specialist.neural_model_v1 import (  # noqa: E402
    SpecialistModelConfigV1,
    build_specialist_policy_model_v1,
)
from mage_ptcg.meta_specialist.actor_visible_features_v1 import (  # noqa: E402
    build_specialist_step_input_v1,
    extract_specialist_model_input_v1,
    make_test_card_vocabulary_v1,
)
from mage_ptcg.meta_specialist.actor_visible_v2 import (  # noqa: E402
    build_actor_visible_decision_state_v2,
)
from mage_ptcg.meta_specialist.neural_checkpoint_v1 import (  # noqa: E402
    load_checkpoint_for_inference_v1,
)
from mage_ptcg.meta_specialist.neural_policy_v1 import (  # noqa: E402
    load_specialist_neural_policy_from_checkpoint_v1,
)
from mage_ptcg.meta_specialist.train_from_trajectories_v1 import (  # noqa: E402
    _load_bootstrap_weights_v1,
)
from tests.meta_specialist.test_training_example_envelope_v2 import _observation  # noqa: E402


def test_transfer_module_exposes_the_research_only_migrator() -> None:
    """Break caught: removing the explicit migration entry point."""
    try:
        module = importlib.import_module(
            "mage_ptcg.meta_specialist.r2_checkpoint_transfer_v2"
        )
    except ModuleNotFoundError:
        module = None

    assert callable(getattr(module, "transfer_v1_checkpoint_to_v2", None))


def _legacy_payload_from_target(model, *, snapshot_id: str) -> dict[str, object]:
    """Construct a fully-shaped legacy v1 checkpoint with hand-checkable weights."""
    config = model.config
    legacy: dict[str, object] = {}
    for index, (name, tensor) in enumerate(model.state_dict().items(), start=1):
        if name.startswith("pokemon_count_encoder") or name.startswith("opponent_value_embedding"):
            continue
        if name == "pokemon_encoder.weight":
            tensor = torch.full((config.hidden_dim, config.card_dim + 6), 101.0)
        elif name == "endpoint_encoder.weight":
            tensor = torch.full((config.hidden_dim, config.card_dim * 2 + config.symbol_dim * 2 + 1), 202.0)
        else:
            tensor = torch.full_like(tensor, float(index))
        legacy[name] = tensor
    return {
        "metadata": {
            "schema_version": "specialist-neural-checkpoint-v1",
            "training_identity": {
                "snapshot_id": snapshot_id,
                "model_config_hash": "b" * 64,
                "recipe_hash": "c" * 64,
                "seed": 0,
            },
            "model_config": {
                "schema_version": "specialist-neural-model-v1",
                "card_vocabulary_size": config.card_vocabulary_size,
                "hidden_dim": config.hidden_dim,
                "card_dim": config.card_dim,
                "symbol_dim": config.symbol_dim,
            },
            "recipe": {},
            "step": 24,
            "sampler_cursor": 72,
            "foundation_init": {
                "schema_version": "specialist-foundation-init-v1",
                "init_kind": "bc_distilled",
                "teachers": [{
                    "teacher_id": "qualified_teacher",
                    "teacher_kind": "external_submission_agent",
                    "policy_hash": "d" * 64,
                    "usage_boundary": "local_eval_only",
                    "derivation_boundary": "derivation_qualified",
                    "decision_ref": "docs/decisions/test.md",
                }],
                "parent_checkpoint_sha256": "",
                "notes": "synthetic legacy source",
            },
        },
        "model": legacy,
        "optimizer": {},
        "scheduler": None,
        "cpu_rng_state": torch.random.get_rng_state(),
        "cuda_rng_state": None,
    }


def _write_legacy_checkpoint(tmp_path: Path, payload: dict[str, object]) -> tuple[Path, str]:
    path = tmp_path / "legacy.pt"
    torch.save(payload, path)
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def test_transfer_preserves_only_semantically_shared_columns_and_strictly_reloads(
    tmp_path: Path,
) -> None:
    """Break caught: prefix-copying a v1 encoder silently assigns wrong v2 features."""
    module = importlib.import_module("mage_ptcg.meta_specialist.r2_checkpoint_transfer_v2")
    config = SpecialistModelConfigV1(card_vocabulary_size=23, hidden_dim=8, card_dim=4, symbol_dim=2)
    target = build_specialist_policy_model_v1(config, seed=31)
    scalar_before = {name: value.clone() for name, value in target.state_dict().items() if name.startswith("scalar_encoder")}
    source_path, source_sha256 = _write_legacy_checkpoint(
        tmp_path, _legacy_payload_from_target(target, snapshot_id="a" * 40)
    )
    result = module.transfer_v1_checkpoint_to_v2(
        source_path=source_path,
        expected_source_sha256=source_sha256,
        expected_source_snapshot_id="a" * 40,
        target_model=target,
    )

    migrated = target.state_dict()
    source = torch.load(source_path, map_location="cpu", weights_only=True)["model"]
    assert result.source_sha256 == source_sha256
    assert torch.equal(migrated["card_embedding.weight"], source["card_embedding.weight"])
    assert torch.equal(migrated["scalar_encoder.weight"], scalar_before["scalar_encoder.weight"])
    assert torch.equal(migrated["scalar_encoder.bias"], scalar_before["scalar_encoder.bias"])
    # v1 pokemon columns are [card_embedding, six scalars]; v2 inserts a zone
    # embedding before the same six scalars and appends entirely new observations.
    assert torch.equal(migrated["pokemon_encoder.weight"][:, :4], source["pokemon_encoder.weight"][:, :4])
    assert torch.equal(migrated["pokemon_encoder.weight"][:, 6:12], source["pokemon_encoder.weight"][:, 4:10])
    assert torch.count_nonzero(migrated["pokemon_encoder.weight"][:, 4:6]) == 0
    assert torch.count_nonzero(migrated["pokemon_encoder.weight"][:, 12:]) == 0
    # Existing endpoint columns keep their meanings; nested Pokemon and its flag
    # are new and must start neutral.
    assert torch.equal(migrated["endpoint_encoder.weight"][:, :13], source["endpoint_encoder.weight"])
    assert torch.count_nonzero(migrated["endpoint_encoder.weight"][:, 13:]) == 0
    assert torch.equal(migrated["pokemon_encoder.bias"], source["pokemon_encoder.bias"])
    assert torch.equal(migrated["endpoint_encoder.bias"], source["endpoint_encoder.bias"])

    reloaded = build_specialist_policy_model_v1(config, seed=99)
    reloaded.load_state_dict(migrated, strict=True)


@pytest.mark.parametrize("mutation", ("wrong_sha", "wrong_schema"))
def test_transfer_rejects_unverified_or_nonlegacy_source_schema(
    tmp_path: Path, mutation: str,
) -> None:
    """Break caught: accepting a foreign checkpoint or bytes not bound to the caller."""
    module = importlib.import_module("mage_ptcg.meta_specialist.r2_checkpoint_transfer_v2")
    config = SpecialistModelConfigV1(card_vocabulary_size=23, hidden_dim=8, card_dim=4, symbol_dim=2)
    target = build_specialist_policy_model_v1(config, seed=31)
    payload = _legacy_payload_from_target(target, snapshot_id="a" * 40)
    if mutation == "wrong_schema":
        payload["metadata"]["model_config"]["schema_version"] = "specialist-neural-model-v2"
    source_path, source_sha256 = _write_legacy_checkpoint(tmp_path, payload)

    with pytest.raises(ValueError):
        module.transfer_v1_checkpoint_to_v2(
            source_path=source_path,
            expected_source_sha256="0" * 64 if mutation == "wrong_sha" else source_sha256,
            expected_source_snapshot_id="a" * 40,
            target_model=target,
        )


def test_transferred_model_passes_a_finite_runtime_forward_probe(tmp_path: Path) -> None:
    """Break caught: a strict-loadable transfer still produces NaN/Inf runtime logits."""
    module = importlib.import_module("mage_ptcg.meta_specialist.r2_checkpoint_transfer_v2")
    config = SpecialistModelConfigV1(card_vocabulary_size=1_400)
    target = build_specialist_policy_model_v1(config, seed=31)
    source_path, source_sha256 = _write_legacy_checkpoint(
        tmp_path, _legacy_payload_from_target(target, snapshot_id="a" * 40)
    )
    module.transfer_v1_checkpoint_to_v2(
        source_path=source_path,
        expected_source_sha256=source_sha256,
        expected_source_snapshot_id="a" * 40,
        target_model=target,
    )
    vocabulary = make_test_card_vocabulary_v1(())
    state = build_actor_visible_decision_state_v2(_observation())
    model_input = extract_specialist_model_input_v1(state, vocabulary).model_input
    step_input = build_specialist_step_input_v1(
        extract_specialist_model_input_v1(state, vocabulary), ()
    )

    validator = getattr(module, "validate_transferred_forward_v2", None)
    assert callable(validator)
    validator(target_model=target, model_input=model_input, step_input=step_input)


def test_published_transfer_is_a_runtime_and_bootstrap_loadable_v2_checkpoint(
    tmp_path: Path,
) -> None:
    """Break caught: a custom transfer artifact cannot be used by BC or actor runtime."""
    module = importlib.import_module("mage_ptcg.meta_specialist.r2_checkpoint_transfer_v2")
    config = SpecialistModelConfigV1(card_vocabulary_size=23, hidden_dim=8, card_dim=4, symbol_dim=2)
    source_model = build_specialist_policy_model_v1(config, seed=31)
    source_path, source_sha256 = _write_legacy_checkpoint(
        tmp_path, _legacy_payload_from_target(source_model, snapshot_id="a" * 40)
    )

    published = module.publish_transferred_v2_bootstrap_checkpoint(
        source_path=source_path,
        expected_source_sha256=source_sha256,
        expected_source_snapshot_id="a" * 40,
        target_runtime_snapshot_id="b" * 40,
        target_config=config,
        target_model_seed=19,
        output_directory=tmp_path / "published",
    )

    assert published.path.name == f"checkpoint-{published.content_sha256}.pt"
    payload = load_checkpoint_for_inference_v1(
        published.path, expected_content_hash=published.content_sha256,
    )
    metadata = payload["metadata"]
    assert metadata["model_config"] == config.to_dict()
    assert metadata["step"] == 0 and metadata["sampler_cursor"] == 0
    assert metadata["recipe"] == {
        "objective": "research_legacy_v1_to_v2_transfer_bootstrap",
        "transfer_schema_version": module.TRANSFER_SCHEMA_V2,
        "legacy_source_sha256": source_sha256,
        "legacy_source_snapshot_id": "a" * 40,
        "column_map_version": module.SEMANTIC_COLUMN_MAP_VERSION_V2,
        "optimizer": "adamw",
        "learning_rate": 0.001,
    }
    assert metadata["foundation_init"]["init_kind"] == "warm_start"
    assert metadata["foundation_init"]["parent_checkpoint_sha256"] == source_sha256
    assert payload["optimizer"]["state"] == {}

    policy = load_specialist_neural_policy_from_checkpoint_v1(
        published.path,
        expected_content_hash=published.content_sha256,
        checkpoint_lineage_id="e" * 64,
    )
    assert policy.policy_telemetry().model_loaded
    bootstrap_target = build_specialist_policy_model_v1(config, seed=999)
    warm = _load_bootstrap_weights_v1(
        bootstrap_target, published.path, expected_config=config,
    )
    assert warm.parent_checkpoint_sha256 == published.content_sha256
    assert all(
        torch.equal(bootstrap_target.state_dict()[name], payload["model"][name])
        for name in payload["model"]
    )
