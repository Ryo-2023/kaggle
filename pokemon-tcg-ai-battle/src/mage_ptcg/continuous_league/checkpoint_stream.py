"""TrainingCheckpoint から model-only RuntimePolicy を原子的に公開する。"""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from mage_ptcg.policy_learning.r2d3.candidate import deck_hash
from mage_ptcg.policy_learning.r2d3.model import R2D3ModelConfig

from .contracts import (
    LeagueContractError,
    atomic_write_bytes,
    atomic_write_json,
    content_id,
    file_sha256,
    load_json,
    utc_now,
)


def canonical_model_state_hash(state_dict: Mapping[str, Any]) -> str:
    """torch の zip/pickle 表現に依存しない model state の semantic hash。"""

    digest = hashlib.sha256()
    digest.update(b"r2d3-model-state-v1\0")
    for name in sorted(state_dict):
        tensor = state_dict[name]
        if not hasattr(tensor, "detach"):
            raise LeagueContractError(f"model state {name} is not a tensor")
        value = tensor.detach().cpu().contiguous()
        descriptor = {
            "name": name,
            "dtype": str(value.dtype),
            "shape": list(value.shape),
        }
        digest.update(content_id("tensor-descriptor-v1", descriptor).encode("ascii"))
        digest.update(value.view(__import__("torch").uint8).numpy().tobytes(order="C"))
    return digest.hexdigest()


def _atomic_torch_save(path: Path, payload: Any) -> None:
    import torch

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        torch.save(payload, temporary)
        handle = os.open(temporary, os.O_RDONLY)
        try:
            os.fsync(handle)
        finally:
            os.close(handle)
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary.exists():
            temporary.unlink()


def publish_checkpoint(
    *,
    checkpoint_path: Path,
    output_root: Path,
    model_config: R2D3ModelConfig,
    deck: Sequence[int],
    state_encoder_version: str = "semantic-public-state-v1",
    action_encoder_version: str = "semantic-legal-action-v1",
    legal_mask_version: str = "cabt-decision-state-v1",
    recurrent_contract_version: str = "reset-per-game-update-per-decision-v1",
    tie_break_version: str = "lowest-legal-option-index-v1",
) -> dict[str, Any]:
    import torch

    checkpoint_path = Path(checkpoint_path)
    if len(deck) != 60 or any(type(card_id) is not int for card_id in deck):
        raise LeagueContractError("runtime policy requires an exact 60-card integer deck")
    if model_config.state_size != 128 or model_config.action_size != 64:
        raise LeagueContractError(
            "current semantic runtime requires state_size=128 and action_size=64"
        )
    try:
        payload = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
    except (OSError, RuntimeError, ValueError) as exc:
        raise LeagueContractError(f"cannot load training checkpoint: {exc}") from exc
    required = {
        "schema",
        "population_hash",
        "replay_manifest_hash",
        "step",
        "model",
        "target",
        "optimizer",
        "rng",
    }
    missing = required.difference(payload)
    if missing or payload.get("schema") not in {
        "r2d3-checkpoint-v1",
        "r2d3-checkpoint-v2",
        "r2d3-checkpoint-v3",
    }:
        raise LeagueContractError(
            f"invalid R2D3 training checkpoint; missing={sorted(missing)}"
        )
    checkpoint_sha256 = file_sha256(checkpoint_path)
    training_identity = {
        "checkpoint_sha256": checkpoint_sha256,
        "schema": payload["schema"],
        "population_epoch_id": str(payload["population_hash"]),
        "replay_dataset_version_id": str(payload["replay_manifest_hash"]),
        "training_step": int(payload["step"]),
        "training_identity_hash": payload.get("training_identity_hash"),
    }
    training_checkpoint_id = content_id("training-checkpoint-v1", training_identity)

    model_state_hash = canonical_model_state_hash(payload["model"])
    runtime_identity = {
        "model_state_hash": model_state_hash,
        "model_config": asdict(model_config),
        "state_encoder_version": state_encoder_version,
        "action_encoder_version": action_encoder_version,
        "action_mode": "greedy",
        "q_reduction": "categorical-expected-value",
        "legal_mask_version": legal_mask_version,
        "recurrent_contract_version": recurrent_contract_version,
        "tie_break_version": tie_break_version,
        "deck": list(deck),
        "deck_hash": deck_hash(list(deck)),
        "runtime_device": "cpu",
        "torch_threads": 1,
    }
    runtime_policy_id = content_id("runtime-policy-v1", runtime_identity)

    output_root = Path(output_root)
    training_dir = output_root / "training_checkpoints" / training_checkpoint_id
    runtime_dir = output_root / "runtime_policies" / runtime_policy_id
    training_manifest = {
        "schema_version": 1,
        "training_checkpoint_id": training_checkpoint_id,
        **training_identity,
        "published_at": utc_now(),
        "runtime_policy_id": runtime_policy_id,
    }
    runtime_manifest = {
        "schema_version": 1,
        "runtime_policy_id": runtime_policy_id,
        "training_checkpoint_id": training_checkpoint_id,
        **runtime_identity,
        "weights_file": "model_weights.pt",
        "published_at": utc_now(),
    }

    existing_training = (
        load_json(training_dir / "manifest.json")
        if (training_dir / "manifest.json").exists()
        else None
    )
    existing_runtime = (
        load_json(runtime_dir / "manifest.json")
        if (runtime_dir / "manifest.json").exists()
        else None
    )
    if existing_training:
        comparable = dict(existing_training)
        comparable.pop("published_at", None)
        expected = dict(training_manifest)
        expected.pop("published_at", None)
        if comparable != expected:
            raise LeagueContractError("training checkpoint ID collision")
        stored_checkpoint = training_dir / "training_checkpoint.pt"
        if (
            not stored_checkpoint.is_file()
            or file_sha256(stored_checkpoint) != checkpoint_sha256
        ):
            raise LeagueContractError("published training checkpoint is corrupt")
    else:
        training_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(
            training_dir / "training_checkpoint.pt", checkpoint_path.read_bytes()
        )
        atomic_write_json(training_dir / "manifest.json", training_manifest)

    if existing_runtime:
        comparable = dict(existing_runtime)
        comparable.pop("published_at", None)
        expected = dict(runtime_manifest)
        expected.pop("published_at", None)
        if comparable != expected:
            raise LeagueContractError("runtime policy ID collision")
        stored_state = torch.load(
            runtime_dir / "model_weights.pt", map_location="cpu", weights_only=True
        )
        if canonical_model_state_hash(stored_state) != model_state_hash:
            raise LeagueContractError("runtime policy weights are corrupt")
    else:
        runtime_dir.mkdir(parents=True, exist_ok=True)
        _atomic_torch_save(runtime_dir / "model_weights.pt", payload["model"])
        atomic_write_json(runtime_dir / "manifest.json", runtime_manifest)

    event = {
        "schema_version": 1,
        "training_checkpoint_id": training_checkpoint_id,
        "runtime_policy_id": runtime_policy_id,
        "training_step": int(payload["step"]),
        "population_epoch_id": str(payload["population_hash"]),
        "replay_dataset_version_id": str(payload["replay_manifest_hash"]),
    }
    atomic_write_json(
        output_root / "events" / f"{training_checkpoint_id}.json", event
    )
    return {
        **event,
        "training_manifest_path": str(training_dir / "manifest.json"),
        "runtime_manifest_path": str(runtime_dir / "manifest.json"),
    }
