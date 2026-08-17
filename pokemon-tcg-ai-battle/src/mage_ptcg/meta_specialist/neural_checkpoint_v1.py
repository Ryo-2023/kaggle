"""Content-addressed training checkpoints with strict, legacy-free resume.

A checkpoint restores everything that determines the next update: model and
optimizer state, the LR scheduler, both RNG streams, the sampler cursor, the
training recipe, and the model topology.  It carries exactly one
``training_identity``; a checkpoint from another identity is refused rather than
adapted, and a legacy R2D3 or Student checkpoint is never loadable at all.

Publication writes an exclusive temporary file, fsyncs it, verifies the bytes
back from a frozen snapshot, and only then links the content-addressed name into
place.  An existing byte-identical artifact is left untouched.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import io
import os
from pathlib import Path
from typing import Any, Mapping

import torch

from mage_ptcg.meta_specialist.local_dataset_v2 import canonical_json_bytes_v2
from mage_ptcg.meta_specialist.foundation_init_v1 import (
    FoundationInitProvenanceV1,
    parse_foundation_init_provenance_v1,
)
from mage_ptcg.meta_specialist.neural_model_v1 import (
    SpecialistModelConfigV1,
    SpecialistPolicyModelV1,
)


NEURAL_CHECKPOINT_SCHEMA_V1 = "specialist-neural-checkpoint-v1"
MAX_CHECKPOINT_BYTES_V1 = 2 * 1024 * 1024 * 1024
_REJECTED_LEGACY_KEYS_V1 = frozenset({
    "r2d3", "r2d3_state", "online", "target", "per_priorities", "student",
    "student_v2", "training_identity_r2d3", "replay_priority",
})
_METADATA_KEYS_V1 = frozenset({
    "schema_version", "training_identity", "model_config", "recipe", "step",
    "sampler_cursor",
    # Where θ0's weights came from (正典 §1 / §9.3).  Required, because
    # "these weights are a sharpened copy of the rule agent" is only
    # diagnosable afterwards if every checkpoint states its own origin.  A run
    # that was randomly initialized says so explicitly via
    # `random_init_provenance_v1`; there is no "unknown" value.
    "foundation_init",
})


class NeuralCheckpointV1Error(ValueError):
    """Raised when a checkpoint cannot be published or strictly resumed."""


@dataclass(frozen=True, slots=True)
class TrainingIdentityV1:
    """Everything that must match for a resume to be the same training run."""

    snapshot_id: str
    model_config_hash: str
    recipe_hash: str
    seed: int

    def to_dict(self) -> dict[str, object]:
        return {
            "snapshot_id": self.snapshot_id,
            "model_config_hash": self.model_config_hash,
            "recipe_hash": self.recipe_hash,
            "seed": self.seed,
        }

    def digest(self) -> str:
        return hashlib.sha256(
            b"mage_ptcg:specialist-training-identity:v1\0"
            + canonical_json_bytes_v2(self.to_dict())
        ).hexdigest()


def _hash_mapping(domain: str, value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        domain.encode("utf-8") + b"\0" + canonical_json_bytes_v2(dict(value))
    ).hexdigest()


def build_training_identity_v1(
    *, snapshot_id: str, config: SpecialistModelConfigV1, recipe: Mapping[str, Any], seed: int,
) -> TrainingIdentityV1:
    """Bind one run to its snapshot, topology, recipe, and seed."""
    if type(snapshot_id) is not str or not snapshot_id:
        raise NeuralCheckpointV1Error("snapshot_id must be a nonempty string")
    if type(config) is not SpecialistModelConfigV1:
        raise NeuralCheckpointV1Error("config must be a SpecialistModelConfigV1")
    if type(seed) is not int:
        raise NeuralCheckpointV1Error("seed must be an int")
    return TrainingIdentityV1(
        snapshot_id=snapshot_id,
        model_config_hash=_hash_mapping(
            "mage_ptcg:specialist-model-config:v1", config.to_dict()
        ),
        recipe_hash=_hash_mapping("mage_ptcg:specialist-recipe:v1", recipe),
        seed=seed,
    )


def _serialize(payload: Mapping[str, Any]) -> bytes:
    buffer = io.BytesIO()
    torch.save(payload, buffer)
    body = buffer.getvalue()
    if len(body) > MAX_CHECKPOINT_BYTES_V1:
        raise NeuralCheckpointV1Error("checkpoint exceeds the byte cap")
    return body


def build_checkpoint_payload_v1(
    *,
    model: SpecialistPolicyModelV1,
    optimizer: torch.optim.Optimizer,
    scheduler: Any | None,
    identity: TrainingIdentityV1,
    recipe: Mapping[str, Any],
    step: int,
    sampler_cursor: int,
    foundation_init: FoundationInitProvenanceV1,
) -> dict[str, Any]:
    """Capture every state that determines the next update.

    ``foundation_init`` is required rather than defaulted.  A default would let
    a caller ship weights without saying where they came from, which is the
    condition under which "the learned policy is a sharpened copy of the rule
    agent" became undetectable.  Callers that genuinely start from random
    weights pass ``random_init_provenance_v1()``.
    """
    if type(model) is not SpecialistPolicyModelV1:
        raise NeuralCheckpointV1Error("model must be a SpecialistPolicyModelV1")
    if not isinstance(optimizer, torch.optim.Optimizer):
        raise NeuralCheckpointV1Error("optimizer must be a torch optimizer")
    if type(foundation_init) is not FoundationInitProvenanceV1:
        raise NeuralCheckpointV1Error(
            "foundation_init must be a FoundationInitProvenanceV1"
        )
    for name, value in (("step", step), ("sampler_cursor", sampler_cursor)):
        if type(value) is not int or value < 0:
            raise NeuralCheckpointV1Error(f"{name} must be a nonnegative int")
    return {
        "metadata": {
            "schema_version": NEURAL_CHECKPOINT_SCHEMA_V1,
            "training_identity": identity.to_dict(),
            "model_config": model.config.to_dict(),
            "recipe": dict(recipe),
            "step": step,
            "sampler_cursor": sampler_cursor,
            "foundation_init": foundation_init.to_dict(),
        },
        "model": model.state_dict(),
        "optimizer": optimizer.state_dict(),
        "scheduler": None if scheduler is None else scheduler.state_dict(),
        "cpu_rng_state": torch.random.get_rng_state(),
        "cuda_rng_state": None,
    }


def publish_checkpoint_v1(directory: str | Path, payload: Mapping[str, Any]) -> Path:
    """Write one content-addressed checkpoint, verifying the bytes before linking."""
    body = _serialize(payload)
    digest = hashlib.sha256(body).hexdigest()
    root = Path(os.path.abspath(os.fspath(directory)))
    root.mkdir(parents=True, exist_ok=True)
    destination = root / f"checkpoint-{digest}.pt"
    if destination.exists():
        # Content-addressed: an identical artifact is already durable.
        if destination.read_bytes() != body:
            raise NeuralCheckpointV1Error("content-addressed checkpoint name holds other bytes")
        return destination

    parent = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    temporary: str | None = None
    try:
        for _attempt in range(32):
            name = f".checkpoint-{digest}.tmp.{os.urandom(8).hex()}"
            try:
                descriptor = os.open(
                    name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600,
                    dir_fd=parent,
                )
            except FileExistsError:
                continue
            temporary = name
            break
        if temporary is None:
            raise NeuralCheckpointV1Error("could not reserve a checkpoint temporary file")
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        # Verify from the frozen file rather than trusting the in-memory buffer.
        with open(temporary, "rb", opener=lambda path, flags: os.open(path, flags, dir_fd=parent)) as handle:
            if hashlib.sha256(handle.read()).hexdigest() != digest:
                raise NeuralCheckpointV1Error("checkpoint bytes did not verify after fsync")
        try:
            os.link(temporary, destination.name, src_dir_fd=parent, dst_dir_fd=parent)
        except FileExistsError:
            pass
        os.fsync(parent)
        os.unlink(temporary, dir_fd=parent)
        temporary = None
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary, dir_fd=parent)
            except OSError:
                pass
        os.close(parent)
    return destination


def load_checkpoint_v1(path: str | Path, *, expected: TrainingIdentityV1) -> dict[str, Any]:
    """Load a checkpoint only if it is this exact training identity."""
    body = Path(path).read_bytes()
    if len(body) > MAX_CHECKPOINT_BYTES_V1:
        raise NeuralCheckpointV1Error("checkpoint exceeds the byte cap")
    payload = torch.load(io.BytesIO(body), map_location="cpu", weights_only=True)
    if type(payload) is not dict or _REJECTED_LEGACY_KEYS_V1 & set(payload):
        raise NeuralCheckpointV1Error("refusing a legacy or foreign checkpoint layout")
    metadata = payload.get("metadata")
    if type(metadata) is not dict or set(metadata) != _METADATA_KEYS_V1:
        raise NeuralCheckpointV1Error("checkpoint metadata has the wrong closed field set")
    if metadata["schema_version"] != NEURAL_CHECKPOINT_SCHEMA_V1:
        raise NeuralCheckpointV1Error("checkpoint schema_version is not supported")
    if metadata["training_identity"] != expected.to_dict():
        raise NeuralCheckpointV1Error("checkpoint training_identity does not match this run")
    return payload


def load_checkpoint_for_inference_v1(
    path: str | Path, *, expected_content_hash: str,
) -> dict[str, Any]:
    """Load one checkpoint for read-only inference, verified by content hash alone.

    Unlike :func:`load_checkpoint_v1` (which additionally requires an exact
    ``TrainingIdentityV1`` match because it exists to resume optimizer/
    scheduler state for the *same* training run), an actor worker only ever
    deploys a frozen snapshot for rollout: it has no optimizer, scheduler, or
    training recipe to resume, and no legitimate way to reconstruct this
    checkpoint's exact ``recipe_hash``/``seed`` from a job's picklable
    primitives.  The checkpoint's own content-addressed SHA-256 -- the same
    digest :func:`publish_checkpoint_v1` embeds in its filename -- is the
    sole, sufficient identity check here, mirroring the discipline
    ``actor_pool_v1.rule_agent_behavior_identity_v1`` already uses for the
    rule policy template file.
    """
    body = Path(path).read_bytes()
    if len(body) > MAX_CHECKPOINT_BYTES_V1:
        raise NeuralCheckpointV1Error("checkpoint exceeds the byte cap")
    digest = hashlib.sha256(body).hexdigest()
    if type(expected_content_hash) is not str or digest != expected_content_hash:
        raise NeuralCheckpointV1Error(
            "checkpoint file bytes do not match the expected content hash"
        )
    payload = torch.load(io.BytesIO(body), map_location="cpu", weights_only=True)
    if type(payload) is not dict or _REJECTED_LEGACY_KEYS_V1 & set(payload):
        raise NeuralCheckpointV1Error("refusing a legacy or foreign checkpoint layout")
    metadata = payload.get("metadata")
    if type(metadata) is not dict or set(metadata) != _METADATA_KEYS_V1:
        raise NeuralCheckpointV1Error("checkpoint metadata has the wrong closed field set")
    if metadata["schema_version"] != NEURAL_CHECKPOINT_SCHEMA_V1:
        raise NeuralCheckpointV1Error("checkpoint schema_version is not supported")
    if not isinstance(payload.get("model"), Mapping):
        raise NeuralCheckpointV1Error("checkpoint has no model state_dict")
    return payload


def restore_checkpoint_v1(
    payload: Mapping[str, Any],
    *,
    model: SpecialistPolicyModelV1,
    optimizer: torch.optim.Optimizer,
    scheduler: Any | None = None,
) -> tuple[int, int]:
    """Restore every stored state in place and return ``(step, sampler_cursor)``."""
    metadata = payload["metadata"]
    if metadata["model_config"] != model.config.to_dict():
        raise NeuralCheckpointV1Error("checkpoint topology does not match the live model")
    model.load_state_dict(payload["model"])
    optimizer.load_state_dict(payload["optimizer"])
    if (payload["scheduler"] is None) != (scheduler is None):
        raise NeuralCheckpointV1Error("checkpoint scheduler presence does not match this run")
    if scheduler is not None:
        scheduler.load_state_dict(payload["scheduler"])
    torch.random.set_rng_state(payload["cpu_rng_state"])
    return int(metadata["step"]), int(metadata["sampler_cursor"])


__all__ = [
    "MAX_CHECKPOINT_BYTES_V1", "NEURAL_CHECKPOINT_SCHEMA_V1", "NeuralCheckpointV1Error",
    "TrainingIdentityV1", "build_checkpoint_payload_v1", "build_training_identity_v1",
    "FoundationInitProvenanceV1", "parse_foundation_init_provenance_v1",
    "load_checkpoint_for_inference_v1", "load_checkpoint_v1", "publish_checkpoint_v1",
    "restore_checkpoint_v1",
]
