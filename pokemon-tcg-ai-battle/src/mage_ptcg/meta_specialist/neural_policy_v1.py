"""Deployable neural policy adapter for C1 v2 decision runtime (Slice L4)."""

from __future__ import annotations

import hashlib
import io
from pathlib import Path
from typing import Any, Mapping

import torch

from mage_ptcg.meta_specialist.actor_visible_features_v1 import (
    CardVocabularyV1,
    SpecialistModelInputV1,
    SpecialistStepInputV1,
    SpecialistStepLogitsV1,
    canonical_step_input_bytes_v1,
    derive_model_input_id_v1,
)
from mage_ptcg.meta_specialist.neural_checkpoint_v1 import load_checkpoint_for_inference_v1
from mage_ptcg.meta_specialist.neural_export_v1 import EXPORTED_POLICY_SCHEMA_V1
from mage_ptcg.meta_specialist.neural_model_v1 import (
    SpecialistModelConfigV1,
    SpecialistPolicyModelV1,
    build_specialist_policy_model_v1,
)
from mage_ptcg.meta_specialist.runtime import (
    CommittedSemanticDecisionV2,
    PolicyTelemetrySnapshot,
    RuntimeContractError,
    SpecialistDecisionPolicyV2,
    SpecialistDecisionSessionV2,
)


class NeuralPolicyV1Error(ValueError):
    """Raised when loading or executing a neural policy fails."""


# A deployed policy runs one small decision at a time under a per-decision time
# budget, never a big batch. Torch's default of one intra-op thread per core is
# actively harmful at that size -- measured 4.0x slower at 14 threads than at 1
# on this model -- and it multiplies: N actor workers each taking every core
# oversubscribes the host by N times. Left unclamped on a 28-core host, 12
# concurrent workers blew the engine's per-decision budget and faulted 79% of
# games, against 4% at 2 workers.
#
# Two is also exactly the Kaggle submission budget (2 vCPU), so the same clamp
# serves the runtime constraint and local collection.
_MAX_INFERENCE_THREADS_V1 = 2


def _clamp_inference_threads_v1() -> None:
    """Bound this process's intra-op threads before any policy forward runs."""
    if torch.get_num_threads() > _MAX_INFERENCE_THREADS_V1:
        torch.set_num_threads(_MAX_INFERENCE_THREADS_V1)


class SpecialistNeuralDecisionSessionV1:
    """Single decision session for SpecialistNeuralPolicyV1."""

    def __init__(
        self,
        model: SpecialistPolicyModelV1,
        recurrent_state: torch.Tensor | None,
    ) -> None:
        self._model = model
        self._recurrent_state = recurrent_state
        self._cache: dict[tuple[str, bytes], tuple[tuple[float, ...], float | None]] = {}

    def step_logits(
        self,
        model_input: SpecialistModelInputV1,
        step_input: SpecialistStepInputV1,
    ) -> tuple[tuple[float, ...], float | None]:
        input_id = derive_model_input_id_v1(model_input)
        step_bytes = canonical_step_input_bytes_v1(step_input)
        key = (input_id, step_bytes)

        if key in self._cache:
            return self._cache[key]

        with torch.no_grad():
            semantic_logits, stop_logit = self._model.step_logits(
                model_input,
                step_input,
            )

        values = tuple(float(v) for v in semantic_logits.tolist())
        stop_val = None if stop_logit is None else float(stop_logit.item())
        result = (values, stop_val)
        self._cache[key] = result
        return result

    def logits(
        self,
        model_input: SpecialistModelInputV1,
        step_input: SpecialistStepInputV1,
    ) -> SpecialistStepLogitsV1:
        values, stop_val = self.step_logits(model_input, step_input)
        return SpecialistStepLogitsV1(semantic_logits=values, stop_logit=stop_val)

    def commit(self, outcome: CommittedSemanticDecisionV2) -> None:
        if outcome.next_recurrent_state_token is not None:
            if isinstance(outcome.next_recurrent_state_token, torch.Tensor):
                self._recurrent_state = outcome.next_recurrent_state_token

    def abort(self) -> None:
        self._cache.clear()


class SpecialistNeuralPolicyV1:
    """CPU-clamped, validated neural policy matching SpecialistDecisionPolicyV2."""

    def __init__(
        self,
        model: SpecialistPolicyModelV1,
        policy_identity: str,
        checkpoint_lineage_id: str,
    ) -> None:
        self._model = model
        self._policy_identity = policy_identity
        self._checkpoint_lineage_id = checkpoint_lineage_id
        self._recurrent_state: torch.Tensor | None = None
        self._fallback_count = 0

    def reset(self) -> None:
        self._recurrent_state = None

    def begin_decision(self) -> SpecialistDecisionSessionV2:
        return SpecialistNeuralDecisionSessionV1(
            model=self._model,
            recurrent_state=self._recurrent_state,
        )

    def policy_telemetry(self) -> PolicyTelemetrySnapshot:
        return PolicyTelemetrySnapshot(
            policy_identity=self._policy_identity,
            candidate_class="checkpointed_specialist",
            model_loaded=True,
            checkpoint_lineage_id=self._checkpoint_lineage_id,
            checkpoint_lineage_reason=None,
            fallback_count=self._fallback_count,
        )


def load_specialist_neural_policy_v1(
    exported_bytes: bytes,
    lineage_id: str,
    card_vocabulary: CardVocabularyV1,
) -> SpecialistNeuralPolicyV1:
    """Safely load and validate an exported neural policy into a SpecialistNeuralPolicyV1."""
    if type(exported_bytes) is not bytes or not exported_bytes:
        raise NeuralPolicyV1Error("exported_bytes must be nonempty bytes")

    if type(lineage_id) is not str or len(lineage_id) != 64:
        raise NeuralPolicyV1Error("lineage_id must be a 64-character hex SHA-256 string")

    _clamp_inference_threads_v1()

    try:
        buffer = io.BytesIO(exported_bytes)
        payload = torch.load(buffer, weights_only=True)
    except Exception as exc:
        raise NeuralPolicyV1Error(f"failed to load neural policy bytes: {exc}") from exc

    if not isinstance(payload, dict):
        raise NeuralPolicyV1Error("exported policy payload must be a dict")

    if payload.get("schema_version") != EXPORTED_POLICY_SCHEMA_V1:
        raise NeuralPolicyV1Error("unsupported exported policy schema version")

    stored_lineage_id = payload.get("lineage_id")
    if stored_lineage_id != lineage_id:
        raise NeuralPolicyV1Error(
            f"exported lineage_id ({stored_lineage_id}) does not match expected ({lineage_id})"
        )

    config_dict = payload.get("topology_config")
    if not isinstance(config_dict, dict):
        raise NeuralPolicyV1Error("topology_config must be a dict")

    state_dict = payload.get("state_dict")
    if not isinstance(state_dict, dict):
        raise NeuralPolicyV1Error("state_dict must be a dict")

    default_vocab_size = (
        max(card_vocabulary.recognized_card_ids) + 1
        if card_vocabulary.recognized_card_ids
        else 1_400
    )

    config = SpecialistModelConfigV1(
        card_vocabulary_size=config_dict.get("card_vocabulary_size", default_vocab_size),
        hidden_dim=config_dict.get("hidden_dim", 128),
        card_dim=config_dict.get("card_dim", 64),
        symbol_dim=config_dict.get("symbol_dim", 16),
    )

    model = build_specialist_policy_model_v1(config, seed=42)

    try:
        model.load_state_dict(state_dict)
    except Exception as exc:
        raise NeuralPolicyV1Error(f"failed to load state_dict into model: {exc}") from exc

    model.eval()

    # Compute policy identity from SHA-256 digest of raw exported bytes
    policy_identity = hashlib.sha256(exported_bytes).hexdigest()

    return SpecialistNeuralPolicyV1(
        model=model,
        policy_identity=policy_identity,
        checkpoint_lineage_id=lineage_id,
    )


def load_specialist_neural_policy_from_checkpoint_v1(
    checkpoint_path: str | Path,
    *,
    expected_content_hash: str,
    checkpoint_lineage_id: str,
) -> SpecialistNeuralPolicyV1:
    """Load one frozen ``neural_checkpoint_v1`` training checkpoint for actor rollout.

    This is the actor-pool loading path, distinct from
    :func:`load_specialist_neural_policy_v1` (which deploys a *different*,
    already-exported ``neural_export_v1`` byte format meant for a submission
    archive).  A checkpoint's model weights are deployed as-is -- no
    optimizer/scheduler/RNG resume -- verified solely by its content-addressed
    SHA-256 via :func:`neural_checkpoint_v1.load_checkpoint_for_inference_v1`.
    The model's topology is read from the checkpoint's own stored
    ``model_config`` (authoritative; no vocabulary-derived fallback guess is
    needed here since it is not a guess -- it is what this exact checkpoint
    was actually trained with).

    ``checkpoint_lineage_id`` is supplied by the caller, not derived from the
    checkpoint bytes: ``runtime.MetaSpecialistRuntime`` requires a
    ``"checkpointed_specialist"`` policy's reported lineage id to equal the
    live ``DeckLockDecision.policy_lineage_id`` for the game being played
    (the same anti-cross-lineage binding ``package.py`` already enforces for
    a submission archive's model member) -- the actor pool binds this
    honestly rather than bypassing that check.
    """
    # The actor pool reaches inference through *this* loader, not the exported-
    # bytes one, so the clamp has to be here too or a collection run gets the
    # unclamped default.
    _clamp_inference_threads_v1()
    payload = load_checkpoint_for_inference_v1(
        checkpoint_path, expected_content_hash=expected_content_hash,
    )
    metadata = payload["metadata"]
    config_dict = metadata["model_config"]
    config = SpecialistModelConfigV1(
        card_vocabulary_size=config_dict["card_vocabulary_size"],
        hidden_dim=config_dict["hidden_dim"],
        card_dim=config_dict["card_dim"],
        symbol_dim=config_dict["symbol_dim"],
    )
    # The initialization seed only matters for weights this load_state_dict
    # call immediately overwrites in full; it carries no information into
    # the deployed policy.
    model = build_specialist_policy_model_v1(config, seed=0)
    try:
        model.load_state_dict(payload["model"])
    except Exception as exc:
        raise NeuralPolicyV1Error(f"failed to load checkpoint state_dict into model: {exc}") from exc
    model.eval()

    return SpecialistNeuralPolicyV1(
        model=model,
        policy_identity=expected_content_hash,
        checkpoint_lineage_id=checkpoint_lineage_id,
    )


__all__ = [
    "NeuralPolicyV1Error",
    "SpecialistNeuralDecisionSessionV1",
    "SpecialistNeuralPolicyV1",
    "load_specialist_neural_policy_from_checkpoint_v1",
    "load_specialist_neural_policy_v1",
]
