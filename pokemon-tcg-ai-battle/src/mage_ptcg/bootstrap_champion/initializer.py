"""Step-zero weight bundles, intentionally distinct from resume checkpoints."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

from mage_ptcg.continuous_league.checkpoint_stream import canonical_model_state_hash
from mage_ptcg.continuous_league.contracts import (
    atomic_write_json,
    content_id,
    file_sha256,
    load_json,
    utc_now,
)
from mage_ptcg.observability.cabt_trace import canonical_deck_sha256
from mage_ptcg.policy_learning.r2d3.candidate import deck_hash
from mage_ptcg.policy_learning.r2d3.model import R2D3ModelConfig, RecurrentDistributionalQ

from .contracts import (
    BootstrapChampionManifest,
    BootstrapCheckpointManifest,
    BootstrapContractError,
    InitializationMode,
    write_manifest,
)


_BUNDLE_SCHEMA = "bootstrap-checkpoint-v1"


def _load_torch(path: Path) -> Mapping[str, Any]:
    import torch

    try:
        payload = torch.load(path, map_location="cpu", weights_only=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise BootstrapContractError(f"cannot read Bootstrap weights: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise BootstrapContractError("Bootstrap weights payload must be an object")
    return payload


def _save_bundle(
    *,
    state: Mapping[str, Any],
    champion: BootstrapChampionManifest,
    initialization_mode: InitializationMode,
    model_config_hash: str,
    action_schema_hash: str,
    output: Path,
    teacher_dataset_id: str | None = None,
    source_checkpoint_id: str | None = None,
) -> BootstrapCheckpointManifest:
    import torch

    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    weights_path = output / "weights.pt"
    if weights_path.exists():
        existing = _load_torch(weights_path)
        existing_state = existing.get("model")
        if (
            existing.get("schema") != _BUNDLE_SCHEMA
            or not isinstance(existing_state, Mapping)
            or canonical_model_state_hash(existing_state)
            != canonical_model_state_hash(state)
        ):
            raise BootstrapContractError(
                f"Bootstrap weights already exist with different content: {weights_path}"
            )
    else:
        temporary = weights_path.with_suffix(".pt.tmp")
        torch.save({"schema": _BUNDLE_SCHEMA, "model": dict(state)}, temporary)
        temporary.replace(weights_path)
    manifest = BootstrapCheckpointManifest.build(
        bootstrap_champion_id=champion.bootstrap_champion_id,
        initialization_mode=initialization_mode,
        model_config_hash=model_config_hash,
        action_schema_hash=action_schema_hash,
        deck_hash=champion.candidate.deck.deck_hash,
        online_weights_sha256=file_sha256(weights_path),
        teacher_dataset_id=teacher_dataset_id,
        source_checkpoint_id=source_checkpoint_id,
    )
    manifest_path = output / "manifest.json"
    write_manifest(manifest_path, manifest.to_dict())
    return manifest


def _validate_state_for_model(state: Mapping[str, Any], model: Any) -> None:
    expected = model.state_dict()
    if set(state) != set(expected):
        raise ValueError("Bootstrap source checkpoint weight keys differ")
    for name, tensor in expected.items():
        source = state[name]
        if not hasattr(source, "shape") or source.shape != tensor.shape:
            raise ValueError(f"Bootstrap source checkpoint weight shape differs: {name}")


def initialize_from_checkpoint(
    *,
    source_checkpoint: Path,
    champion: BootstrapChampionManifest,
    model_config_hash: str,
    action_schema_hash: str,
    output: Path,
    expected_model: Any | None = None,
) -> BootstrapCheckpointManifest:
    """Extract only `model` from a supported R2D3 resume checkpoint."""

    source_checkpoint = Path(source_checkpoint)
    if source_checkpoint.is_dir():
        runtime_manifest = load_json(source_checkpoint / "manifest.json")
        if not isinstance(runtime_manifest, Mapping):
            raise BootstrapContractError("runtime source manifest must be an object")
        weights_name = runtime_manifest.get("weights_file")
        if not isinstance(weights_name, str) or not weights_name:
            raise BootstrapContractError("runtime source manifest has no weights_file")
        weights_path = source_checkpoint / weights_name
        payload = _load_torch(weights_path)
        state = payload.get("model", payload)
        source_id = file_sha256(weights_path)
    else:
        payload = _load_torch(source_checkpoint)
        if payload.get("schema") not in {"r2d3-checkpoint-v1", "r2d3-checkpoint-v2", "r2d3-checkpoint-v3"}:
            raise BootstrapContractError("unsupported source checkpoint schema")
        state = payload.get("model")
        source_id = file_sha256(source_checkpoint)
    if not isinstance(state, Mapping):
        raise BootstrapContractError("source checkpoint has no model weights")
    if expected_model is not None:
        _validate_state_for_model(state, expected_model)
    return _save_bundle(
        state=state,
        champion=champion,
        initialization_mode=InitializationMode.DIRECT_CHECKPOINT,
        model_config_hash=model_config_hash,
        action_schema_hash=action_schema_hash,
        output=output,
        source_checkpoint_id=source_id,
    )


def initialize_from_distillation(
    *,
    distilled_weights: Path,
    champion: BootstrapChampionManifest,
    model_config_hash: str,
    action_schema_hash: str,
    teacher_dataset_id: str,
    output: Path,
    expected_model: Any | None = None,
) -> BootstrapCheckpointManifest:
    payload = _load_torch(Path(distilled_weights))
    state = payload.get("model", payload)
    if not isinstance(state, Mapping):
        raise BootstrapContractError("distilled weights have no model state")
    if expected_model is not None:
        _validate_state_for_model(state, expected_model)
    return _save_bundle(
        state=state,
        champion=champion,
        initialization_mode=InitializationMode.TEACHER_DISTILLATION,
        model_config_hash=model_config_hash,
        action_schema_hash=action_schema_hash,
        output=output,
        teacher_dataset_id=teacher_dataset_id,
    )


def load_bootstrap_manifest(path: Path) -> BootstrapCheckpointManifest:
    payload = load_json(Path(path) / "manifest.json")
    if not isinstance(payload, Mapping):
        raise BootstrapContractError("Bootstrap manifest must be an object")
    return BootstrapCheckpointManifest.from_dict(payload)


def load_bootstrap_weights(
    path: Path,
    *,
    model: Any,
    target: Any,
    expected_manifest: BootstrapCheckpointManifest | None = None,
) -> BootstrapCheckpointManifest:
    """Load step-zero model weights and deliberately reset target from online."""

    root = Path(path)
    manifest = load_bootstrap_manifest(root)
    if expected_manifest is not None and manifest.bootstrap_checkpoint_id != expected_manifest.bootstrap_checkpoint_id:
        raise BootstrapContractError("Bootstrap checkpoint manifest differs from expected manifest")
    weights_path = root / "weights.pt"
    if file_sha256(weights_path) != manifest.online_weights_sha256:
        raise BootstrapContractError("Bootstrap weights hash differs from manifest")
    payload = _load_torch(weights_path)
    if payload.get("schema") != _BUNDLE_SCHEMA or not isinstance(payload.get("model"), Mapping):
        raise BootstrapContractError("unsupported Bootstrap weights schema")
    _validate_state_for_model(payload["model"], model)
    model.load_state_dict(payload["model"], strict=True)
    target.load_state_dict(model.state_dict(), strict=True)
    for parameter in target.parameters():
        parameter.requires_grad_(False)
    return manifest


def publish_bootstrap_runtime(
    *,
    bootstrap_checkpoint: Path,
    output_root: Path,
    model_config: R2D3ModelConfig,
    deck: list[int],
    state_encoder_version: str = "semantic-public-state-v1",
    action_encoder_version: str = "semantic-legal-action-v1",
    legal_mask_version: str = "cabt-decision-state-v1",
    recurrent_contract_version: str = "reset-per-game-update-per-decision-v1",
    tie_break_version: str = "lowest-legal-option-index-v1",
) -> dict[str, str]:
    """Publish step-zero weights as a model-only policy for fresh collection.

    A Bootstrap checkpoint deliberately has no optimizer or replay lineage, so it
    must not be routed through ``publish_checkpoint``.  This publisher writes
    the same executable RuntimePolicy contract while retaining the Bootstrap
    checkpoint as its explicit provenance.
    """

    if len(deck) != 60 or any(type(card_id) is not int for card_id in deck):
        raise BootstrapContractError("runtime policy requires an exact 60-card integer deck")
    if model_config.state_size != 128 or model_config.action_size != 64:
        raise BootstrapContractError(
            "current semantic runtime requires state_size=128 and action_size=64"
        )
    bootstrap_checkpoint = Path(bootstrap_checkpoint)
    manifest = load_bootstrap_manifest(bootstrap_checkpoint)
    if canonical_deck_sha256(deck) != manifest.deck_hash:
        raise BootstrapContractError("runtime deck differs from Bootstrap Champion deck")
    expected_model_hash = content_id("bootstrap-model-config-v1", asdict(model_config))
    expected_action_hash = content_id(
        "bootstrap-action-schema-v1",
        {
            "state_encoder_version": state_encoder_version,
            "action_encoder_version": action_encoder_version,
            "state_size": model_config.state_size,
            "action_size": model_config.action_size,
        },
    )
    if manifest.model_config_hash != expected_model_hash:
        raise BootstrapContractError("Bootstrap model configuration differs from runtime")
    if manifest.action_schema_hash != expected_action_hash:
        raise BootstrapContractError("Bootstrap action schema differs from runtime")
    weights_path = bootstrap_checkpoint / "weights.pt"
    if file_sha256(weights_path) != manifest.online_weights_sha256:
        raise BootstrapContractError("Bootstrap weights hash differs from manifest")
    payload = _load_torch(weights_path)
    if payload.get("schema") != _BUNDLE_SCHEMA or not isinstance(payload.get("model"), Mapping):
        raise BootstrapContractError("unsupported Bootstrap weights schema")
    state = payload["model"]
    _validate_state_for_model(state, RecurrentDistributionalQ(model_config))
    model_state_hash = canonical_model_state_hash(state)
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
        "deck_hash": deck_hash(deck),
        "runtime_device": "cpu",
        "torch_threads": 1,
    }
    runtime_policy_id = content_id("runtime-policy-v1", runtime_identity)
    runtime_dir = Path(output_root) / "runtime_policies" / runtime_policy_id
    runtime_manifest = {
        "schema_version": 1,
        "runtime_policy_id": runtime_policy_id,
        "bootstrap_checkpoint_id": manifest.bootstrap_checkpoint_id,
        **runtime_identity,
        "weights_file": "model_weights.pt",
        "published_at": utc_now(),
    }
    manifest_path = runtime_dir / "manifest.json"
    if manifest_path.exists():
        existing = load_json(manifest_path)
        expected = dict(runtime_manifest)
        comparable = dict(existing)
        expected.pop("published_at", None)
        comparable.pop("published_at", None)
        if comparable != expected:
            raise BootstrapContractError("runtime policy ID collision")
        stored = _load_torch(runtime_dir / "model_weights.pt")
        if canonical_model_state_hash(stored) != model_state_hash:
            raise BootstrapContractError("published Bootstrap runtime weights are corrupt")
    else:
        import torch

        runtime_dir.mkdir(parents=True, exist_ok=True)
        temporary = runtime_dir / ".model_weights.pt.tmp"
        torch.save(dict(state), temporary)
        temporary.replace(runtime_dir / "model_weights.pt")
        atomic_write_json(manifest_path, runtime_manifest)
    return {
        "bootstrap_checkpoint_id": manifest.bootstrap_checkpoint_id,
        "runtime_policy_id": runtime_policy_id,
        "runtime_manifest_path": str(manifest_path),
    }
