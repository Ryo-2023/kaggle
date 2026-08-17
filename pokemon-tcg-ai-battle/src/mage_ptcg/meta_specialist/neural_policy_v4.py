"""Research-only runtime adapter for closed :class:`SpecialistModelV4` artifacts.

The CABT runtime remains the authority for semantic decoding, STOP handling,
and the local lexicographic alias dispatcher.  This module only adapts the
actor-visible v1 model/step inputs into V4 relational states and supplies the
resulting class-level logits through the existing ``SpecialistDecisionPolicyV2``
boundary.
"""

from __future__ import annotations

import hashlib
import io
from pathlib import Path
from typing import Callable, Mapping

import torch

from mage_ptcg.meta_specialist.actor_visible_features_v1 import (
    SpecialistModelInputV1,
    SpecialistStepInputV1,
    SpecialistStepLogitsV1,
    canonical_step_input_bytes_v1,
    derive_model_input_id_v1,
)
from mage_ptcg.meta_specialist.neural_model_v4 import (
    NeuralModelV4Error,
    SpecialistModelV4,
    StateEncodingV4,
    load_specialist_checkpoint_v4,
)
from mage_ptcg.meta_specialist.representation_v4 import representation_v4_from_step_input_v1
from mage_ptcg.meta_specialist.runtime import (
    CommittedSemanticDecisionV2,
    PolicyTelemetrySnapshot,
    SpecialistDecisionPolicyV2,
    SpecialistDecisionSessionV2,
)


class NeuralPolicyV4Error(ValueError):
    """Raised when a V4 runtime policy cannot be loaded or evaluated safely."""


_HEX64 = frozenset("0123456789abcdef")
_MAX_INFERENCE_THREADS_V4 = 2


def _require_hex64(value: object, name: str) -> str:
    if type(value) is not str or len(value) != 64 or any(char not in _HEX64 for char in value):
        raise NeuralPolicyV4Error(f"{name} must be a 64-character lowercase hex SHA-256 string")
    return value


def _clamp_inference_threads_v4() -> None:
    if torch.get_num_threads() > _MAX_INFERENCE_THREADS_V4:
        torch.set_num_threads(_MAX_INFERENCE_THREADS_V4)


class SpecialistNeuralDecisionSessionV4:
    """One complete CABT action evaluated from one fixed incoming GRU state."""

    def __init__(
        self,
        model: SpecialistModelV4,
        incoming_hidden: torch.Tensor | None,
        on_commit: Callable[[torch.Tensor | None], None] | None = None,
    ) -> None:
        self._model = model
        self._incoming_hidden = incoming_hidden
        self._on_commit = on_commit
        self._next_hidden: torch.Tensor | None = None
        self._recurrent_token: torch.Tensor | None = None
        self._state_encodings: dict[str, StateEncodingV4] = {}
        self._reference_embeddings: dict[tuple[str, object], torch.Tensor] = {}
        self._argument_embeddings: dict[tuple[tuple[int, ...], tuple[float, ...]], torch.Tensor] = {}
        self._cache: dict[tuple[str, bytes], SpecialistStepLogitsV1] = {}

    @property
    def next_recurrent_state_token(self) -> torch.Tensor | None:
        return self._next_hidden

    def _reference_embedding(
        self, *, model_input_id: str, reference: object, encoding: StateEncodingV4,
    ) -> torch.Tensor:
        key = (model_input_id, reference)
        cached = self._reference_embeddings.get(key)
        if cached is None:
            cached = self._model.reference_embedding_v4(reference, encoding)
            self._reference_embeddings[key] = cached
        return cached

    def _candidate_tokens(
        self, *, state, encoding: StateEncodingV4, model_input_id: str,
    ) -> torch.Tensor:
        """Evaluate one legal class domain in one candidate-MLP invocation.

        ``SpecialistModelV4.encode_candidate_v4`` is intentionally a scalar
        reference implementation.  CABT can require 20+ semantic picks from
        one domain, so invoking that scalar MLP for every candidate and every
        prefix exceeds the frozen callback limit.  This is the same expression
        evaluated over the candidate batch; weights and feature construction
        remain owned by the closed model.
        """
        candidates = state.candidates
        if not candidates:
            return encoding.global_token.new_zeros((0, self._model.hidden_dim))
        selected_counts = candidates[0].selected_class_counts
        if any(candidate.selected_class_counts != selected_counts for candidate in candidates[1:]):
            raise NeuralPolicyV4Error("V4 candidate domain has inconsistent selected class counts")

        def reference(value: object) -> torch.Tensor:
            return self._reference_embedding(
                model_input_id=model_input_id, reference=value, encoding=encoding,
            )

        sources = torch.stack([reference(candidate.source_class_ref) for candidate in candidates])
        targets = torch.stack([reference(candidate.target_class_ref) for candidate in candidates])
        hosts = torch.stack([reference(candidate.host_class_ref) for candidate in candidates])
        selected_rows = [
            reference(ref)
            for ref, count in selected_counts
            for _ in range(count)
        ]
        selected_embedding = (
            torch.stack(selected_rows).mean(0)
            if selected_rows else encoding.global_token.new_zeros(self._model.hidden_dim)
        )
        count_total = sum(count for _ref, count in selected_counts)
        count_values = torch.tensor([
            [
                float(len(encoding.class_members.get(candidate.selectable_class_ref, ()))) / 60.0
                if candidate.selectable_class_ref is not None else 0.0,
                float(candidate.allowed_alias_count) / 512.0,
                float(dict(selected_counts).get(candidate.selectable_class_ref, 0)) / 512.0
                if candidate.selectable_class_ref is not None else 0.0,
                float(count_total) / 512.0,
            ]
            for candidate in candidates
        ], dtype=self._model._dtype, device=self._model._device)
        arguments: list[torch.Tensor] = []
        for candidate in candidates:
            key = (candidate.categorical_args, candidate.numeric_args)
            cached = self._argument_embeddings.get(key)
            if cached is None:
                cached = self._model._arguments(*key)
                self._argument_embeddings[key] = cached
            arguments.append(cached)
        candidate_tokens = self._model.candidate_mix(torch.cat([
            sources,
            targets,
            hosts,
            self._model.relation_projection(torch.cat([sources, targets], dim=-1)),
            self._model.action_type_embedding(torch.tensor(
                [candidate.action_type for candidate in candidates],
                dtype=torch.long, device=self._model._device,
            )),
            self._model.selection_step_embedding(torch.tensor(
                [candidate.selection_step for candidate in candidates],
                dtype=torch.long, device=self._model._device,
            )),
            selected_embedding.expand(len(candidates), -1) + self._model.count_projection(count_values),
            torch.stack(arguments),
        ], dim=-1))
        return candidate_tokens

    def logits(
        self, model_input: SpecialistModelInputV1, step_input: SpecialistStepInputV1,
    ) -> SpecialistStepLogitsV1:
        model_input_id = derive_model_input_id_v1(model_input)
        key = (model_input_id, canonical_step_input_bytes_v1(step_input))
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        try:
            state = representation_v4_from_step_input_v1(
                model_input, step_input, allow_unbound_selected=True,
            )
            with torch.no_grad():
                encoding = self._state_encodings.get(model_input_id)
                if encoding is None:
                    encoding = self._model.encode_state_v4(state)
                    self._state_encodings[model_input_id] = encoding
                if self._recurrent_token is None:
                    recurrent, next_hidden = self._model.memory(
                        encoding.global_token.view(1, 1, -1), self._incoming_hidden,
                    )
                    self._recurrent_token = recurrent[0, 0]
                    self._next_hidden = next_hidden.detach()
                global_token = self._recurrent_token + self._model._prefix_embedding(state, encoding)
                candidate_tokens = self._candidate_tokens(
                    state=state, encoding=encoding, model_input_id=model_input_id,
                )
                semantic_logits = self._model.candidate_bias(
                    torch.tanh(candidate_tokens + global_token)
                ).squeeze(-1)
                mask = torch.tensor(
                    [candidate.excludes_selected_duplicate for candidate in state.candidates],
                    dtype=torch.bool, device=semantic_logits.device,
                )
                semantic_logits = semantic_logits.masked_fill(mask, float("-inf"))
                stop = (
                    self._model.stop_vector @ global_token + self._model.stop_bias
                    if step_input.stop_available else None
                )
        except (NeuralModelV4Error, ValueError, TypeError) as exc:
            raise NeuralPolicyV4Error("V4 policy cannot project or score this runtime step") from exc
        if semantic_logits.numel() != len(step_input.allowed_semantic_classes):
            raise NeuralPolicyV4Error("V4 semantic logits do not match the runtime legal class domain")
        result = SpecialistStepLogitsV1(
            semantic_logits=tuple(float(value) for value in semantic_logits.tolist()),
            stop_logit=None if stop is None else float(stop.item()),
        )
        # All prefixes of one runtime action must observe precisely the same
        # incoming hidden state.  The first output is the one and only token
        # committed after the runtime commits the complete action.
        self._cache[key] = result
        return result

    def commit(self, outcome: CommittedSemanticDecisionV2) -> None:
        del outcome
        if self._on_commit is not None:
            self._on_commit(self._next_hidden)

    def abort(self) -> None:
        self._cache.clear()
        self._state_encodings.clear()
        self._reference_embeddings.clear()
        self._argument_embeddings.clear()


class SpecialistNeuralPolicyV4:
    """V4 recurrent policy compatible with ``runtime.make_agent``.

    A policy instance is deliberately per game.  Use
    :class:`SpecialistNeuralPolicyV4Factory` when a caller needs the fresh
    object discipline required by ``make_agent`` or actor-pool wrappers.
    """

    def __init__(self, model: SpecialistModelV4, *, policy_identity: str, checkpoint_lineage_id: str) -> None:
        if type(model) is not SpecialistModelV4:
            raise NeuralPolicyV4Error("model must be an exact SpecialistModelV4")
        self._model = model.eval()
        self._policy_identity = _require_hex64(policy_identity, "policy_identity")
        self._checkpoint_lineage_id = _require_hex64(checkpoint_lineage_id, "checkpoint_lineage_id")
        self._recurrent_state: torch.Tensor | None = None
        self._fallback_count = 0

    def reset(self) -> None:
        self._recurrent_state = None

    def begin_decision(self) -> SpecialistDecisionSessionV2:
        def commit(next_hidden: torch.Tensor | None) -> None:
            if next_hidden is not None:
                self._recurrent_state = next_hidden

        return SpecialistNeuralDecisionSessionV4(self._model, self._recurrent_state, commit)

    def policy_telemetry(self) -> PolicyTelemetrySnapshot:
        return PolicyTelemetrySnapshot(
            policy_identity=self._policy_identity,
            candidate_class="checkpointed_specialist",
            model_loaded=True,
            checkpoint_lineage_id=self._checkpoint_lineage_id,
            checkpoint_lineage_reason=None,
            fallback_count=self._fallback_count,
        )


class SpecialistNeuralPolicyV4Factory:
    """Return a fresh recurrent V4 policy while sharing immutable loaded weights."""

    def __init__(self, policy: SpecialistNeuralPolicyV4) -> None:
        if type(policy) is not SpecialistNeuralPolicyV4:
            raise NeuralPolicyV4Error("policy must be an exact SpecialistNeuralPolicyV4")
        self._model = policy._model
        self._policy_identity = policy._policy_identity
        self._checkpoint_lineage_id = policy._checkpoint_lineage_id

    def new_policy(self) -> SpecialistDecisionPolicyV2:
        return SpecialistNeuralPolicyV4(
            self._model,
            policy_identity=self._policy_identity,
            checkpoint_lineage_id=self._checkpoint_lineage_id,
        )


def _checkpoint_config_v4(path: Path, expected_file_sha256: str) -> Mapping[str, int]:
    """Read only a hash-bound topology descriptor before the strict loader binds it."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise NeuralPolicyV4Error("V4 checkpoint cannot be read") from exc
    if hashlib.sha256(raw).hexdigest() != expected_file_sha256:
        raise NeuralPolicyV4Error("V4 checkpoint file SHA-256 does not match the expected value")
    try:
        payload = torch.load(io.BytesIO(raw), map_location="cpu", weights_only=True)
        config = payload["descriptor"]["model_config"]
    except (KeyError, OSError, RuntimeError, TypeError, ValueError, EOFError) as exc:
        raise NeuralPolicyV4Error("V4 checkpoint has no readable model_config descriptor") from exc
    required = {"card_vocabulary_size", "hidden_dim", "embedding_dim", "state_scalar_dim"}
    if type(config) is not dict or set(config) != required or any(type(value) is not int or value < 1 for value in config.values()):
        raise NeuralPolicyV4Error("V4 checkpoint model_config is invalid")
    return config


def load_specialist_neural_policy_from_checkpoint_v4(
    checkpoint_path: str | Path,
    *,
    expected_file_sha256: str,
    expected_tensor_state_sha256: str,
    checkpoint_lineage_id: str,
) -> SpecialistNeuralPolicyV4:
    """Load a closed V4 artifact with mandatory independent file and tensor hashes."""
    _clamp_inference_threads_v4()
    file_hash = _require_hex64(expected_file_sha256, "expected_file_sha256")
    tensor_hash = _require_hex64(expected_tensor_state_sha256, "expected_tensor_state_sha256")
    lineage = _require_hex64(checkpoint_lineage_id, "checkpoint_lineage_id")
    config = _checkpoint_config_v4(Path(checkpoint_path), file_hash)
    model = SpecialistModelV4(**config, seed=0)
    try:
        load_specialist_checkpoint_v4(
            checkpoint_path,
            model,
            expected_file_sha256=file_hash,
            expected_tensor_state_sha256=tensor_hash,
        )
    except NeuralModelV4Error as exc:
        raise NeuralPolicyV4Error("V4 checkpoint failed strict artifact validation") from exc
    return SpecialistNeuralPolicyV4(model, policy_identity=file_hash, checkpoint_lineage_id=lineage)


__all__ = [
    "NeuralPolicyV4Error",
    "SpecialistNeuralDecisionSessionV4",
    "SpecialistNeuralPolicyV4",
    "SpecialistNeuralPolicyV4Factory",
    "load_specialist_neural_policy_from_checkpoint_v4",
]
