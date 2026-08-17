"""Research-only runtime adapter for closed :class:`SpecialistModelV5` artifacts.

The adapter deliberately exposes only the public ``SpecialistModelInputV1`` /
``SpecialistStepInputV1`` boundary.  A complete CABT action is evaluated from
one incoming recurrent state: all decoder prefixes share one state encoding and
one GRU transition, and the hidden state is advanced only by ``commit``.  The
existing runtime remains responsible for legal semantic decoding and STOP
control; this module only supplies V5 semantic logits and the base-global STOP
logit.
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
from mage_ptcg.meta_specialist.neural_model_v5 import (
    NeuralModelV5Error,
    SpecialistModelV5,
    StateEncodingV4,
    _checkpoint_snapshot_bytes_v5,
    load_specialist_checkpoint_v5,
)
from mage_ptcg.meta_specialist.representation_v4 import representation_v4_from_step_input_v1
from mage_ptcg.meta_specialist.runtime import (
    CommittedSemanticDecisionV2,
    PolicyTelemetrySnapshot,
    SpecialistDecisionPolicyV2,
    SpecialistDecisionSessionV2,
)


class NeuralPolicyV5Error(ValueError):
    """Raised when a V5 runtime policy cannot be loaded or evaluated safely."""


_HEX64 = frozenset("0123456789abcdef")
_MAX_INFERENCE_THREADS_V5 = 2


def _require_hex64(value: object, name: str) -> str:
    if type(value) is not str or len(value) != 64 or any(char not in _HEX64 for char in value):
        raise NeuralPolicyV5Error(f"{name} must be a 64-character lowercase hex SHA-256 string")
    return value


def _clamp_inference_threads_v5() -> None:
    if torch.get_num_threads() > _MAX_INFERENCE_THREADS_V5:
        torch.set_num_threads(_MAX_INFERENCE_THREADS_V5)


class SpecialistNeuralDecisionSessionV5:
    """One semantic decision evaluated from one fixed incoming GRU state."""

    def __init__(
        self,
        model: SpecialistModelV5,
        incoming_hidden: torch.Tensor | None,
        on_commit: Callable[[torch.Tensor | None], None] | None = None,
    ) -> None:
        if type(model) is not SpecialistModelV5:
            raise NeuralPolicyV5Error("model must be an exact SpecialistModelV5")
        if incoming_hidden is not None and type(incoming_hidden) is not torch.Tensor:
            raise NeuralPolicyV5Error("incoming hidden state must be a tensor or null")
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
        self,
        *,
        model_input_id: str,
        reference: object,
        encoding: StateEncodingV4,
    ) -> torch.Tensor:
        key = (model_input_id, reference)
        cached = self._reference_embeddings.get(key)
        if cached is None:
            cached = self._model.reference_embedding_v4(reference, encoding)
            self._reference_embeddings[key] = cached
        return cached

    def _candidate_tokens(
        self,
        *,
        state,
        encoding: StateEncodingV4,
        model_input_id: str,
    ) -> torch.Tensor:
        """Batch the V4 candidate expression while retaining its exact schema."""
        candidates = state.candidates
        if not candidates:
            return encoding.global_token.new_zeros((0, self._model.hidden_dim))
        selected_counts = candidates[0].selected_class_counts
        if any(candidate.selected_class_counts != selected_counts for candidate in candidates[1:]):
            raise NeuralPolicyV5Error("V5 candidate domain has inconsistent selected class counts")

        def reference(value: object) -> torch.Tensor:
            return self._reference_embedding(
                model_input_id=model_input_id,
                reference=value,
                encoding=encoding,
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
        return self._model.candidate_mix(torch.cat([
            sources,
            targets,
            hosts,
            self._model.relation_projection(torch.cat([sources, targets], dim=-1)),
            self._model.action_type_embedding(torch.tensor(
                [candidate.action_type for candidate in candidates],
                dtype=torch.long,
                device=self._model._device,
            )),
            self._model.selection_step_embedding(torch.tensor(
                [candidate.selection_step for candidate in candidates],
                dtype=torch.long,
                device=self._model._device,
            )),
            selected_embedding.expand(len(candidates), -1) + self._model.count_projection(count_values),
            torch.stack(arguments),
        ], dim=-1))

    def logits(
        self,
        model_input: SpecialistModelInputV1,
        step_input: SpecialistStepInputV1,
    ) -> SpecialistStepLogitsV1:
        if type(model_input) is not SpecialistModelInputV1:
            raise NeuralPolicyV5Error("model input must be SpecialistModelInputV1")
        if type(step_input) is not SpecialistStepInputV1:
            raise NeuralPolicyV5Error("step input must be SpecialistStepInputV1")
        try:
            SpecialistModelInputV1.__post_init__(model_input)
            SpecialistStepInputV1.__post_init__(step_input)
            model_input_id = derive_model_input_id_v1(model_input)
            key = (model_input_id, canonical_step_input_bytes_v1(step_input))
            cached = self._cache.get(key)
            if cached is not None:
                return cached
            state = representation_v4_from_step_input_v1(
                model_input,
                step_input,
                allow_unbound_selected=True,
            )
            with torch.no_grad():
                encoding = self._state_encodings.get(model_input_id)
                if encoding is None:
                    encoding = self._model.encode_state_v4(state)
                    self._state_encodings[model_input_id] = encoding
                if self._recurrent_token is None:
                    recurrent, next_hidden = self._model.memory(
                        encoding.global_token.view(1, 1, -1),
                        self._incoming_hidden,
                    )
                    self._recurrent_token = recurrent[0, 0]
                    self._next_hidden = next_hidden.detach()
                base_global = self._recurrent_token + self._model._prefix_embedding(state, encoding)
                candidate_tokens = self._candidate_tokens(
                    state=state,
                    encoding=encoding,
                    model_input_id=model_input_id,
                )
                if candidate_tokens.numel() == 0:
                    semantic_logits = candidate_tokens.new_zeros((0,))
                else:
                    invalid_mask = torch.tensor(
                        [candidate.excludes_selected_duplicate for candidate in state.candidates],
                        dtype=torch.bool,
                        device=self._model._device,
                    )
                    context = self._model._candidate_set_context_v5(
                        candidate_tokens,
                        ~invalid_mask,
                        base_global=base_global,
                    )
                    base_logits = self._model.candidate_bias(
                        torch.tanh(candidate_tokens + base_global)
                    ).squeeze(-1)
                    residual_input = torch.cat([
                        candidate_tokens,
                        context.expand(candidate_tokens.shape[0], -1),
                        candidate_tokens * context.expand(candidate_tokens.shape[0], -1),
                    ], dim=-1)
                    residual = self._model.candidate_residual_head(residual_input).squeeze(-1)
                    semantic_logits = (base_logits + residual).masked_fill(invalid_mask, float("-inf"))
                stop = (
                    self._model.stop_vector @ base_global + self._model.stop_bias
                    if step_input.stop_available else None
                )
                if not torch.isfinite(semantic_logits).all() or (stop is not None and not torch.isfinite(stop)):
                    raise NeuralPolicyV5Error("V5 policy produced a nonfinite runtime logit")
            if semantic_logits.numel() != len(step_input.allowed_semantic_classes):
                raise NeuralPolicyV5Error("V5 semantic logits do not match the runtime legal class domain")
            result = SpecialistStepLogitsV1(
                semantic_logits=tuple(float(value) for value in semantic_logits.tolist()),
                stop_logit=None if stop is None else float(stop.item()),
            )
        except NeuralPolicyV5Error:
            raise
        except (NeuralModelV5Error, ValueError, TypeError) as exc:
            raise NeuralPolicyV5Error("V5 policy cannot project or score this runtime step") from exc
        self._cache[key] = result
        return result

    def commit(self, outcome: CommittedSemanticDecisionV2) -> None:
        if type(outcome) is not CommittedSemanticDecisionV2:
            raise NeuralPolicyV5Error("V5 commit requires CommittedSemanticDecisionV2")
        if self._on_commit is not None:
            self._on_commit(self._next_hidden)

    def abort(self) -> None:
        self._cache.clear()
        self._state_encodings.clear()
        self._reference_embeddings.clear()
        self._argument_embeddings.clear()
        self._recurrent_token = None
        self._next_hidden = None


class SpecialistNeuralPolicyV5:
    """V5 recurrent policy compatible with the existing runtime protocol."""

    def __init__(
        self,
        model: SpecialistModelV5,
        *,
        policy_identity: str,
        checkpoint_lineage_id: str,
    ) -> None:
        if type(model) is not SpecialistModelV5:
            raise NeuralPolicyV5Error("model must be an exact SpecialistModelV5")
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
                self._recurrent_state = next_hidden.detach()

        return SpecialistNeuralDecisionSessionV5(self._model, self._recurrent_state, commit)

    def policy_telemetry(self) -> PolicyTelemetrySnapshot:
        return PolicyTelemetrySnapshot(
            policy_identity=self._policy_identity,
            candidate_class="checkpointed_specialist",
            model_loaded=True,
            checkpoint_lineage_id=self._checkpoint_lineage_id,
            checkpoint_lineage_reason=None,
            fallback_count=self._fallback_count,
        )


class SpecialistNeuralPolicyV5Factory:
    """Return fresh recurrent V5 policies while sharing immutable model weights."""

    def __init__(self, policy: SpecialistNeuralPolicyV5) -> None:
        if type(policy) is not SpecialistNeuralPolicyV5:
            raise NeuralPolicyV5Error("policy must be an exact SpecialistNeuralPolicyV5")
        self._model = policy._model
        self._policy_identity = policy._policy_identity
        self._checkpoint_lineage_id = policy._checkpoint_lineage_id

    def new_policy(self) -> SpecialistDecisionPolicyV2:
        return SpecialistNeuralPolicyV5(
            self._model,
            policy_identity=self._policy_identity,
            checkpoint_lineage_id=self._checkpoint_lineage_id,
        )


def _checkpoint_config_v5(path: Path, expected_file_sha256: str) -> Mapping[str, int]:
    try:
        raw = _checkpoint_snapshot_bytes_v5(path, expected_file_sha256=expected_file_sha256)
        payload = torch.load(io.BytesIO(raw), map_location="cpu", weights_only=True)
        descriptor = payload["descriptor"]
        config = descriptor["model_config"]
    except (KeyError, OSError, RuntimeError, TypeError, ValueError, EOFError, NeuralModelV5Error) as exc:
        raise NeuralPolicyV5Error("V5 checkpoint has no readable model_config descriptor") from exc
    required = {"card_vocabulary_size", "hidden_dim", "embedding_dim", "state_scalar_dim"}
    if (
        type(config) is not dict
        or set(config) != required
        or any(type(value) is not int or value < 1 for value in config.values())
    ):
        raise NeuralPolicyV5Error("V5 checkpoint model_config is invalid")
    return config


def load_specialist_neural_policy_from_checkpoint_v5(
    checkpoint_path: str | Path,
    *,
    expected_file_sha256: str,
    expected_tensor_state_sha256: str,
    checkpoint_lineage_id: str,
) -> SpecialistNeuralPolicyV5:
    """Load a V5 sidecar with independent file, tensor and lineage bindings."""
    _clamp_inference_threads_v5()
    file_hash = _require_hex64(expected_file_sha256, "expected_file_sha256")
    tensor_hash = _require_hex64(expected_tensor_state_sha256, "expected_tensor_state_sha256")
    lineage = _require_hex64(checkpoint_lineage_id, "checkpoint_lineage_id")
    config = _checkpoint_config_v5(Path(checkpoint_path), file_hash)
    model = SpecialistModelV5(**config, seed=0)
    try:
        load_specialist_checkpoint_v5(
            checkpoint_path,
            model,
            expected_file_sha256=file_hash,
            expected_tensor_state_sha256=tensor_hash,
        )
    except NeuralModelV5Error as exc:
        raise NeuralPolicyV5Error("V5 checkpoint failed strict artifact validation") from exc
    return SpecialistNeuralPolicyV5(
        model,
        policy_identity=file_hash,
        checkpoint_lineage_id=lineage,
    )


__all__ = [
    "NeuralPolicyV5Error",
    "SpecialistNeuralDecisionSessionV5",
    "SpecialistNeuralPolicyV5",
    "SpecialistNeuralPolicyV5Factory",
    "load_specialist_neural_policy_from_checkpoint_v5",
]
