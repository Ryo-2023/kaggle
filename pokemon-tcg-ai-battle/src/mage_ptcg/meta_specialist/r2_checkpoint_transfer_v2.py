"""Research-only semantic warm-start from legacy v1 policy checkpoints.

The strict checkpoint-resume path deliberately rejects legacy checkpoints.  This
module is separate from it: it can only initialize an already-created
representation-v2 model after verifying a particular legacy file's SHA-256,
training snapshot, schema, topology, and complete tensor layout.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import io
from pathlib import Path
from typing import Any, Mapping

import torch

from mage_ptcg.meta_specialist.foundation_init_v1 import (
    FoundationInitProvenanceV1,
    INIT_KIND_WARM_START_V1,
    assert_primary_teacher_is_not_rule_v0_v1,
    parse_foundation_init_provenance_v1,
)
from mage_ptcg.meta_specialist.neural_checkpoint_v1 import (
    build_checkpoint_payload_v1,
    build_training_identity_v1,
    publish_checkpoint_v1,
)
from mage_ptcg.meta_specialist.neural_model_v1 import (
    SpecialistModelConfigV1,
    SpecialistPolicyModelV1,
    build_specialist_policy_model_v1,
)


TRANSFER_SCHEMA_V2 = "r2-checkpoint-transfer-v2"
SEMANTIC_COLUMN_MAP_VERSION_V2 = "r2-v1-to-v2-semantic-column-map-v1"
_LEGACY_CHECKPOINT_TOP_LEVEL_KEYS = frozenset({
    "metadata", "model", "optimizer", "scheduler", "cpu_rng_state", "cuda_rng_state",
})
_LEGACY_METADATA_KEYS = frozenset({
    "schema_version", "training_identity", "model_config", "recipe", "step",
    "sampler_cursor", "foundation_init",
})
_LEGACY_TRAINING_IDENTITY_KEYS = frozenset({
    "snapshot_id", "model_config_hash", "recipe_hash", "seed",
})
_LEGACY_MODEL_CONFIG_KEYS = frozenset({
    "schema_version", "card_vocabulary_size", "hidden_dim", "card_dim", "symbol_dim",
})
_NEW_V2_TENSORS = frozenset({
    "pokemon_count_encoder.weight", "pokemon_count_encoder.bias",
    "opponent_value_embedding.weight",
})
_SCALAR_REINITIALIZED_TENSORS = frozenset({
    "scalar_encoder.weight", "scalar_encoder.bias",
})
_EXPANDED_ENCODER_TENSORS = frozenset({
    "pokemon_encoder.weight", "pokemon_encoder.bias",
    "endpoint_encoder.weight", "endpoint_encoder.bias",
})


class R2CheckpointTransferV2Error(ValueError):
    """Raised when a legacy checkpoint cannot be proven safe for warm-start."""


@dataclass(frozen=True, slots=True)
class CheckpointTransferV2Result:
    """Auditable provenance for a research-only v1-to-v2 warm start."""

    source_sha256: str
    source_snapshot_id: str
    copied_tensors: tuple[str, ...]
    remapped_tensors: tuple[str, ...]
    reinitialized_tensors: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": TRANSFER_SCHEMA_V2,
            "source_sha256": self.source_sha256,
            "source_snapshot_id": self.source_snapshot_id,
            "copied_tensors": list(self.copied_tensors),
            "remapped_tensors": list(self.remapped_tensors),
            "reinitialized_tensors": list(self.reinitialized_tensors),
            "new_feature_columns": {
                "pokemon_encoder.weight": "zero-initialized before named v1 column mapping",
                "endpoint_encoder.weight": "zero-initialized before named v1 column mapping",
            },
        }


@dataclass(frozen=True, slots=True)
class PublishedTransferV2BootstrapCheckpoint:
    """A published v2 checkpoint usable by existing runtime and BC loaders."""

    path: Path
    content_sha256: str
    transfer: CheckpointTransferV2Result


def _require_sha256(value: object, *, field: str) -> str:
    if type(value) is not str or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise R2CheckpointTransferV2Error(f"{field} must be a lowercase SHA-256 hex string")
    return value


def _require_snapshot_id(value: object, *, field: str) -> str:
    if type(value) is not str or len(value) != 40 or any(char not in "0123456789abcdef" for char in value):
        raise R2CheckpointTransferV2Error(f"{field} must be a lowercase 40-character source snapshot id")
    return value


def _load_and_validate_legacy_payload(
    *, source_path: str | Path, expected_source_sha256: str, expected_source_snapshot_id: str,
    target_model: SpecialistPolicyModelV1,
) -> tuple[dict[str, Any], str, Mapping[str, Any]]:
    if type(target_model) is not SpecialistPolicyModelV1:
        raise R2CheckpointTransferV2Error("target_model must be a SpecialistPolicyModelV1")
    if target_model.config.representation_version != 2:
        raise R2CheckpointTransferV2Error("target_model must use representation_version=2")
    expected_sha256 = _require_sha256(expected_source_sha256, field="expected_source_sha256")
    expected_source_snapshot_id = _require_snapshot_id(
        expected_source_snapshot_id, field="expected_source_snapshot_id",
    )
    body = Path(source_path).read_bytes()
    actual_sha256 = hashlib.sha256(body).hexdigest()
    if actual_sha256 != expected_sha256:
        raise R2CheckpointTransferV2Error("legacy checkpoint bytes do not match expected_source_sha256")
    payload = torch.load(io.BytesIO(body), map_location="cpu", weights_only=True)
    if type(payload) is not dict or set(payload) != _LEGACY_CHECKPOINT_TOP_LEVEL_KEYS:
        raise R2CheckpointTransferV2Error("legacy checkpoint has the wrong top-level schema")
    metadata = payload["metadata"]
    if type(metadata) is not dict or set(metadata) != _LEGACY_METADATA_KEYS:
        raise R2CheckpointTransferV2Error("legacy checkpoint metadata has the wrong schema")
    if metadata["schema_version"] != "specialist-neural-checkpoint-v1":
        raise R2CheckpointTransferV2Error("legacy checkpoint schema_version is not supported")
    identity = metadata["training_identity"]
    if type(identity) is not dict or set(identity) != _LEGACY_TRAINING_IDENTITY_KEYS:
        raise R2CheckpointTransferV2Error("legacy checkpoint training_identity has the wrong schema")
    if identity["snapshot_id"] != expected_source_snapshot_id:
        raise R2CheckpointTransferV2Error("legacy checkpoint snapshot_id does not match the expected source")
    config = metadata["model_config"]
    if type(config) is not dict or set(config) != _LEGACY_MODEL_CONFIG_KEYS:
        raise R2CheckpointTransferV2Error("legacy checkpoint model_config has the wrong schema")
    if config["schema_version"] != "specialist-neural-model-v1":
        raise R2CheckpointTransferV2Error("legacy checkpoint is not a representation-v1 model")
    expected_config = target_model.config
    for name in ("card_vocabulary_size", "hidden_dim", "card_dim", "symbol_dim"):
        if config[name] != getattr(expected_config, name):
            raise R2CheckpointTransferV2Error(f"legacy checkpoint {name} does not match target topology")
    model_state = payload["model"]
    if not isinstance(model_state, Mapping):
        raise R2CheckpointTransferV2Error("legacy checkpoint has no model state_dict")
    return dict(model_state), actual_sha256, metadata


def _copy_named_columns(
    *, source: torch.Tensor, target: torch.Tensor,
    named_map: tuple[tuple[str, int, int, int], ...], tensor_name: str,
) -> torch.Tensor:
    """Zero all v2 columns then copy only old feature columns with the same meaning."""
    if source.ndim != 2 or target.ndim != 2 or source.shape[0] != target.shape[0]:
        raise R2CheckpointTransferV2Error(f"{tensor_name} has an incompatible rank or output width")
    if source.dtype != target.dtype:
        raise R2CheckpointTransferV2Error(f"{tensor_name} dtype does not match target")
    mapped = torch.zeros_like(target)
    for feature, source_offset, target_offset, width in named_map:
        if source_offset + width > source.shape[1] or target_offset + width > target.shape[1]:
            raise R2CheckpointTransferV2Error(f"{tensor_name} {feature} column map is outside tensor bounds")
        mapped[:, target_offset:target_offset + width] = source[:, source_offset:source_offset + width]
    return mapped


def _semantic_column_maps(target_model: SpecialistPolicyModelV1) -> tuple[
    tuple[tuple[str, int, int, int], ...], tuple[tuple[str, int, int, int], ...],
]:
    """Return explicit v1-to-v2 maps derived from frozen encoder feature order."""
    card, symbol = target_model.config.card_dim, target_model.config.symbol_dim
    pokemon_map = (
        ("card_id", 0, 0, card),
        ("owner_role", card, card + symbol, 1),
        ("hp_over_max_hp", card + 1, card + symbol + 1, 1),
        ("log1p_max_hp", card + 2, card + symbol + 2, 1),
        ("appear_this_turn", card + 3, card + symbol + 3, 1),
        ("log1p_total_energy", card + 4, card + symbol + 4, 1),
        ("log1p_tool_count", card + 5, card + symbol + 5, 1),
    )
    endpoint_map = (
        ("card_id", 0, 0, card),
        ("host_card_id", card, card, card),
        ("semantic_zone", card * 2, card * 2, symbol),
        ("visibility", card * 2 + symbol, card * 2 + symbol, symbol),
        ("owner_role", card * 2 + symbol * 2, card * 2 + symbol * 2, 1),
    )
    return pokemon_map, endpoint_map


def _validate_legacy_state_layout(
    *, legacy_state: Mapping[str, Any], target_state: Mapping[str, torch.Tensor],
    target_model: SpecialistPolicyModelV1,
) -> None:
    expected = set(target_state) - _NEW_V2_TENSORS
    if set(legacy_state) != expected:
        raise R2CheckpointTransferV2Error("legacy model state_dict does not match the explicit v1 allowlist")
    card, symbol, hidden = (
        target_model.config.card_dim, target_model.config.symbol_dim, target_model.config.hidden_dim,
    )
    legacy_shapes = {
        "pokemon_encoder.weight": (hidden, card + 6),
        "endpoint_encoder.weight": (hidden, card * 2 + symbol * 2 + 1),
    }
    for name, value in legacy_state.items():
        if type(value) is not torch.Tensor:
            raise R2CheckpointTransferV2Error(f"legacy tensor {name} is not a tensor")
        expected_shape = legacy_shapes.get(name, tuple(target_state[name].shape))
        if tuple(value.shape) != expected_shape:
            raise R2CheckpointTransferV2Error(f"legacy tensor {name} has an incompatible shape")
        if value.dtype != target_state[name].dtype:
            raise R2CheckpointTransferV2Error(f"legacy tensor {name} has an incompatible dtype")
        if value.is_floating_point() and not bool(torch.isfinite(value).all()):
            raise R2CheckpointTransferV2Error(f"legacy tensor {name} is not finite")


def transfer_v1_checkpoint_to_v2(
    *, source_path: str | Path, expected_source_sha256: str, expected_source_snapshot_id: str,
    target_model: SpecialistPolicyModelV1,
) -> CheckpointTransferV2Result:
    """Apply the audited v1-to-v2 mapping to ``target_model`` in place.

    This is intentionally unsuitable for resume: optimizer, scheduler, RNG,
    and all scalar-encoder semantics are discarded.  It merely supplies a
    BC-finetuning initialization candidate whose copied information is explicit.
    """
    legacy_state, source_sha256, _metadata = _load_and_validate_legacy_payload(
        source_path=source_path,
        expected_source_sha256=expected_source_sha256,
        expected_source_snapshot_id=expected_source_snapshot_id,
        target_model=target_model,
    )
    target_state = target_model.state_dict()
    _validate_legacy_state_layout(
        legacy_state=legacy_state, target_state=target_state, target_model=target_model,
    )
    transferred = {name: value.clone() for name, value in target_state.items()}
    copied: list[str] = []
    for name in sorted(set(legacy_state) - _SCALAR_REINITIALIZED_TENSORS - _EXPANDED_ENCODER_TENSORS):
        # These names are the complete, shape-equal allowlist; no prefix or
        # partial tensor copying is permitted.
        transferred[name] = legacy_state[name].clone()
        copied.append(name)
    pokemon_map, endpoint_map = _semantic_column_maps(target_model)
    transferred["pokemon_encoder.weight"] = _copy_named_columns(
        source=legacy_state["pokemon_encoder.weight"], target=target_state["pokemon_encoder.weight"],
        named_map=pokemon_map, tensor_name="pokemon_encoder.weight",
    )
    transferred["pokemon_encoder.bias"] = legacy_state["pokemon_encoder.bias"].clone()
    transferred["endpoint_encoder.weight"] = _copy_named_columns(
        source=legacy_state["endpoint_encoder.weight"], target=target_state["endpoint_encoder.weight"],
        named_map=endpoint_map, tensor_name="endpoint_encoder.weight",
    )
    transferred["endpoint_encoder.bias"] = legacy_state["endpoint_encoder.bias"].clone()
    target_model.load_state_dict(transferred, strict=True)
    result = CheckpointTransferV2Result(
        source_sha256=source_sha256,
        source_snapshot_id=expected_source_snapshot_id,
        copied_tensors=tuple(copied),
        remapped_tensors=(
            "pokemon_encoder.weight", "pokemon_encoder.bias",
            "endpoint_encoder.weight", "endpoint_encoder.bias",
        ),
        reinitialized_tensors=tuple(sorted(_SCALAR_REINITIALIZED_TENSORS | _NEW_V2_TENSORS)),
    )
    return result


def publish_transferred_v2_bootstrap_checkpoint(
    *, source_path: str | Path, expected_source_sha256: str, expected_source_snapshot_id: str,
    target_runtime_snapshot_id: str, target_config: SpecialistModelConfigV1,
    target_model_seed: int, output_directory: str | Path,
) -> PublishedTransferV2BootstrapCheckpoint:
    """Publish a new, runtime-loadable v2 bootstrap checkpoint from legacy weights.

    The result is intentionally a new training checkpoint: optimizer state,
    counters, RNG provenance, identity, and recipe describe the v2 bootstrap;
    none are copied from the legacy training run.
    """
    if type(target_config) is not SpecialistModelConfigV1:
        raise R2CheckpointTransferV2Error("target_config must be a SpecialistModelConfigV1")
    if target_config.representation_version != 2:
        raise R2CheckpointTransferV2Error("target_config must use representation_version=2")
    if type(target_model_seed) is not int:
        raise R2CheckpointTransferV2Error("target_model_seed must be an int")
    target_runtime_snapshot_id = _require_snapshot_id(
        target_runtime_snapshot_id, field="target_runtime_snapshot_id",
    )
    model = build_specialist_policy_model_v1(target_config, seed=target_model_seed)
    transfer = transfer_v1_checkpoint_to_v2(
        source_path=source_path,
        expected_source_sha256=expected_source_sha256,
        expected_source_snapshot_id=expected_source_snapshot_id,
        target_model=model,
    )
    # The source is loaded once more only to rebuild and validate its nested
    # provenance; weights remain those already accepted by the transfer call.
    _legacy_state, _source_sha, source_metadata = _load_and_validate_legacy_payload(
        source_path=source_path,
        expected_source_sha256=expected_source_sha256,
        expected_source_snapshot_id=expected_source_snapshot_id,
        target_model=model,
    )
    try:
        source_foundation = parse_foundation_init_provenance_v1(
            source_metadata["foundation_init"]
        )
    except ValueError as exc:
        raise R2CheckpointTransferV2Error("legacy checkpoint has invalid foundation provenance") from exc
    foundation_init = FoundationInitProvenanceV1(
        init_kind=INIT_KIND_WARM_START_V1,
        teachers=source_foundation.teachers,
        parent_checkpoint_sha256=transfer.source_sha256,
        notes=(
            "research v1-to-v2 semantic transfer bootstrap; "
            f"source_snapshot={transfer.source_snapshot_id}; "
            f"map={SEMANTIC_COLUMN_MAP_VERSION_V2}"
        ),
    )
    try:
        assert_primary_teacher_is_not_rule_v0_v1(foundation_init)
    except ValueError as exc:
        raise R2CheckpointTransferV2Error("legacy foundation provenance is not bootstrap-qualified") from exc
    recipe = {
        "objective": "research_legacy_v1_to_v2_transfer_bootstrap",
        "transfer_schema_version": TRANSFER_SCHEMA_V2,
        "legacy_source_sha256": transfer.source_sha256,
        "legacy_source_snapshot_id": transfer.source_snapshot_id,
        "column_map_version": SEMANTIC_COLUMN_MAP_VERSION_V2,
        "optimizer": "adamw",
        "learning_rate": 0.001,
    }
    optimizer = torch.optim.AdamW(model.parameters(), lr=recipe["learning_rate"])
    identity = build_training_identity_v1(
        snapshot_id=target_runtime_snapshot_id,
        config=target_config,
        recipe=recipe,
        seed=target_model_seed,
    )
    payload = build_checkpoint_payload_v1(
        model=model,
        optimizer=optimizer,
        scheduler=None,
        identity=identity,
        recipe=recipe,
        step=0,
        sampler_cursor=0,
        foundation_init=foundation_init,
    )
    path = publish_checkpoint_v1(output_directory, payload)
    content_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    if path.name != f"checkpoint-{content_sha256}.pt":  # pragma: no cover - publisher enforces it
        raise R2CheckpointTransferV2Error("published checkpoint name is not content addressed")
    return PublishedTransferV2BootstrapCheckpoint(
        path=path, content_sha256=content_sha256, transfer=transfer,
    )


def validate_transferred_forward_v2(
    *, target_model: SpecialistPolicyModelV1, model_input: object, step_input: object,
) -> None:
    """Fail closed unless the transferred v2 model produces finite runtime logits."""
    if type(target_model) is not SpecialistPolicyModelV1:
        raise R2CheckpointTransferV2Error("target_model must be a SpecialistPolicyModelV1")
    try:
        with torch.no_grad():
            semantic_logits, stop_logit = target_model.step_logits(model_input, step_input)
    except (TypeError, ValueError, RuntimeError) as exc:
        raise R2CheckpointTransferV2Error("transferred model failed the runtime forward probe") from exc
    probes = [semantic_logits]
    if stop_logit is not None:
        probes.append(stop_logit.reshape(1))
    if any(not bool(torch.isfinite(probe).all()) for probe in probes):
        raise R2CheckpointTransferV2Error("transferred model emitted non-finite runtime logits")


__all__ = [
    "TRANSFER_SCHEMA_V2", "SEMANTIC_COLUMN_MAP_VERSION_V2",
    "CheckpointTransferV2Result", "PublishedTransferV2BootstrapCheckpoint",
    "R2CheckpointTransferV2Error", "transfer_v1_checkpoint_to_v2",
    "publish_transferred_v2_bootstrap_checkpoint", "validate_transferred_forward_v2",
]
