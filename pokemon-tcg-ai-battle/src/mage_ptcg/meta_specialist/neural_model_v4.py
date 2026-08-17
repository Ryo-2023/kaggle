"""Pooled-equivalence neural policy for :mod:`representation_v4`.

The model consumes only public v4 fields.  A class reference is resolved once
per encoded state and pooled over all of its exchangeable member tokens.
"""

from __future__ import annotations

from dataclasses import dataclass
import dis
import hashlib
import io
import os
from pathlib import Path
import stat
import sys
import types
from types import MappingProxyType
from typing import Mapping

import torch
from torch import nn

from mage_ptcg.meta_specialist import representation_v4 as representation_v4_module
from mage_ptcg.meta_specialist.representation_v4 import (
    ActionCandidateV4,
    EntityTokenV4,
    PUBLIC_INTEGER_MAX_V4,
    PublicEntityClassRefV4,
    REPRESENTATION_V4_SCHEMA,
    RelationalStateV4,
    SemanticPrefixTokenV4,
)


NEURAL_MODEL_SCHEMA_V4 = "specialist-neural-model-v4"
CHECKPOINT_SCHEMA_V4 = "specialist-neural-checkpoint-v4"
_PUBLIC_INTEGER_BITS_V4 = 16
_SEQUENCE_VALUE_WIDTH_V4 = _PUBLIC_INTEGER_BITS_V4 + 3
_IMPLEMENTATION_DIGEST_PREFIX_V4 = b"mage_ptcg:specialist-implementation-closure:v4\0"
_LIVE_CALLABLE_DIGEST_PREFIX_V4 = b"mage_ptcg:specialist-live-callable-closure:v4\0"
_SCHEMA_MARKER_V4 = hashlib.sha256(
    f"{REPRESENTATION_V4_SCHEMA}\0{NEURAL_MODEL_SCHEMA_V4}".encode("ascii")
).digest()


class NeuralModelV4Error(ValueError):
    """Raised when v4 model inputs or topology are invalid."""


@dataclass(frozen=True, slots=True)
class StateEncodingV4:
    global_token: torch.Tensor
    entity_tokens: torch.Tensor
    class_members: Mapping[PublicEntityClassRefV4, tuple[int, ...]]


@dataclass(frozen=True, slots=True)
class PolicyOutputV4:
    logits: torch.Tensor
    global_token: torch.Tensor
    hidden_state: torch.Tensor | None = None


def _seeded(seed: int, factory) -> None:
    if type(seed) is not int:
        raise NeuralModelV4Error("seed must be int")
    rng = torch.random.get_rng_state()
    try:
        torch.manual_seed(seed)
        factory()
    finally:
        torch.random.set_rng_state(rng)


class SpecialistModelV4(nn.Module):
    """Small recurrent policy whose endpoint references are public classes."""

    def __init__(
        self, *, card_vocabulary_size: int, hidden_dim: int = 256, embedding_dim: int = 192,
        seed: int = 0, state_scalar_dim: int = 41,
    ) -> None:
        super().__init__()
        dimensions = (card_vocabulary_size, hidden_dim, embedding_dim, state_scalar_dim)
        if any(type(value) is not int or value < 1 for value in dimensions):
            raise NeuralModelV4Error("model dimensions must be positive")
        self.hidden_dim = hidden_dim
        self.embedding_dim = embedding_dim
        self.card_vocabulary_size = card_vocabulary_size
        self.state_scalar_dim = state_scalar_dim
        self._model_config = MappingProxyType({
            "card_vocabulary_size": card_vocabulary_size,
            "hidden_dim": hidden_dim,
            "embedding_dim": embedding_dim,
            "state_scalar_dim": state_scalar_dim,
        })

        def build() -> None:
            self.card_embedding = nn.Embedding(card_vocabulary_size + 1, embedding_dim, padding_idx=0)
            self.entity_type_embedding = nn.Embedding(32, embedding_dim)
            self.owner_embedding = nn.Embedding(3, embedding_dim)
            self.zone_embedding = nn.Embedding(32, embedding_dim)
            self.feature_value_projection = nn.Sequential(
                nn.Linear(_SEQUENCE_VALUE_WIDTH_V4, embedding_dim), nn.GELU(), nn.Linear(embedding_dim, embedding_dim),
            )
            self.entity_projection = nn.Sequential(nn.Linear(embedding_dim * 7, hidden_dim), nn.GELU(), nn.LayerNorm(hidden_dim))
            self.global_projection = nn.Sequential(nn.Linear(state_scalar_dim, hidden_dim), nn.GELU(), nn.LayerNorm(hidden_dim))
            self.action_type_embedding = nn.Embedding(64, hidden_dim)
            self.selection_step_embedding = nn.Embedding(513, hidden_dim)
            self.position_embedding = nn.Embedding(512, hidden_dim)
            self.count_projection = nn.Sequential(nn.Linear(4, hidden_dim), nn.GELU(), nn.Linear(hidden_dim, hidden_dim))
            self.argument_value_projection = nn.Sequential(
                nn.Linear(_SEQUENCE_VALUE_WIDTH_V4, hidden_dim), nn.GELU(), nn.Linear(hidden_dim, hidden_dim),
            )
            self.relation_projection = nn.Sequential(nn.Linear(hidden_dim * 2, hidden_dim), nn.GELU(), nn.Linear(hidden_dim, hidden_dim))
            self.host_relation_projection = nn.Sequential(
                nn.Linear(hidden_dim * 2, hidden_dim), nn.GELU(), nn.Linear(hidden_dim, hidden_dim),
            )
            self.prefix_mix = nn.Sequential(nn.Linear(hidden_dim * 6, hidden_dim), nn.GELU(), nn.Linear(hidden_dim, hidden_dim))
            self.position_relation = nn.Sequential(nn.Linear(hidden_dim * 2, hidden_dim), nn.GELU(), nn.Linear(hidden_dim, hidden_dim))
            self.prefix_count_projection = nn.Sequential(
                nn.Linear(4, hidden_dim), nn.GELU(), nn.Linear(hidden_dim, hidden_dim),
            )
            self.candidate_mix = nn.Sequential(nn.Linear(hidden_dim * 8, hidden_dim), nn.GELU(), nn.Linear(hidden_dim, hidden_dim), nn.LayerNorm(hidden_dim))
            self.candidate_bias = nn.Linear(hidden_dim, 1)
            self.memory = nn.GRU(hidden_dim, hidden_dim, batch_first=True)
            self.stop_vector = nn.Parameter(torch.empty(hidden_dim))
            self.stop_bias = nn.Parameter(torch.zeros(()))
            for module in self.modules():
                reset = getattr(module, "reset_parameters", None)
                if callable(reset):
                    reset()
            nn.init.normal_(self.stop_vector, mean=0.0, std=0.02)
        _seeded(seed, build)
        self.register_buffer(
            "_schema_marker_v4",
            torch.tensor(list(_SCHEMA_MARKER_V4), dtype=torch.uint8),
            persistent=True,
        )

    @property
    def _device(self) -> torch.device:
        return self.card_embedding.weight.device

    @property
    def _dtype(self) -> torch.dtype:
        return self.card_embedding.weight.dtype

    @staticmethod
    def _bounded_real(value: float) -> float:
        """Keep arbitrary finite public values bounded without truncating fields."""
        return value / (1.0 + abs(value))

    @staticmethod
    def _public_integer_row(value: int, *, position: int, width: int, kind: int) -> list[float]:
        if type(value) is not int or not 0 <= value <= PUBLIC_INTEGER_MAX_V4:
            raise NeuralModelV4Error("public integer is outside its closed v4 range")
        return [float((value >> bit) & 1) for bit in range(_PUBLIC_INTEGER_BITS_V4)] + [
            0.0, float(position + 1) / float(width), float(kind) / 2.0,
        ]

    @classmethod
    def _public_real_row(cls, value: float, *, position: int, width: int, kind: int) -> list[float]:
        return [0.0] * _PUBLIC_INTEGER_BITS_V4 + [
            cls._bounded_real(float(value)), float(position + 1) / float(width), float(kind) / 2.0,
        ]

    def _feature_sequence(self, values: tuple[float | int, ...], *, kind: int) -> torch.Tensor:
        if not values:
            return torch.zeros(self.embedding_dim, dtype=self._dtype, device=self._device)
        rows = torch.tensor([
            self._public_real_row(value, position=position, width=len(values), kind=kind)
            if kind == 1 else self._public_integer_row(value, position=position, width=len(values), kind=kind)
            for position, value in enumerate(values)
        ], dtype=self._dtype, device=self._device)
        # Sum (rather than mean) retains sequence cardinality.  Position is a
        # public schema position, not a physical card locator.
        return self.feature_value_projection(rows).sum(0)

    def _arguments(self, categorical: tuple[int, ...], numeric: tuple[float, ...]) -> torch.Tensor:
        rows = [
            self._public_integer_row(value, position=position, width=max(len(categorical), 1), kind=0)
            for position, value in enumerate(categorical)
        ]
        rows.extend([
            self._public_real_row(value, position=position, width=max(len(numeric), 1), kind=2)
            for position, value in enumerate(numeric)
        ])
        if not rows:
            return torch.zeros(self.hidden_dim, dtype=self._dtype, device=self._device)
        return self.argument_value_projection(torch.tensor(rows, dtype=self._dtype, device=self._device)).sum(0)

    def _base_entity_token(self, entity: EntityTokenV4) -> torch.Tensor:
        if entity.card_id > self.card_vocabulary_size:
            raise NeuralModelV4Error("card_id is outside vocabulary")
        return self.entity_projection(torch.cat([
            self.card_embedding(torch.tensor(entity.card_id, dtype=torch.long, device=self._device)),
            self.entity_type_embedding(torch.tensor(entity.entity_type, dtype=torch.long, device=self._device)),
            self.owner_embedding(torch.tensor(entity.owner, dtype=torch.long, device=self._device)),
            self.zone_embedding(torch.tensor(entity.zone, dtype=torch.long, device=self._device)),
            self._feature_sequence(entity.categorical_features, kind=0),
            self._feature_sequence(entity.scalar_features, kind=1),
            self._feature_sequence(entity.binary_flags, kind=2),
        ]))

    def encode_state_v4(self, state: RelationalStateV4) -> StateEncodingV4:
        if type(state) is not RelationalStateV4:
            raise NeuralModelV4Error("state must be RelationalStateV4")
        ordered = state.canonical_entity_order()
        base_tokens = torch.stack([self._base_entity_token(entity) for entity in ordered]) if ordered else torch.zeros((0, self.hidden_dim), dtype=self._dtype, device=self._device)
        index_by_id = {entity.entity_id: index for index, entity in enumerate(ordered)}
        relation_tokens: list[torch.Tensor] = []
        for index, entity in enumerate(ordered):
            token = base_tokens[index]
            if entity.host_entity_id is not None:
                host_index = index_by_id.get(entity.host_entity_id)
                if host_index is None:
                    raise NeuralModelV4Error("host_entity_id is not bound to encoded state")
                token = token + self.host_relation_projection(torch.cat([token, base_tokens[host_index]]))
            relation_tokens.append(token)
        tokens = torch.stack(relation_tokens) if relation_tokens else base_tokens
        scalar_values = list(state.state_scalars[: self.global_projection[0].in_features])
        scalar_values.extend([0.0] * (self.global_projection[0].in_features - len(scalar_values)))
        pooled = tokens.mean(0) if len(ordered) else torch.zeros(self.hidden_dim, dtype=self._dtype, device=self._device)
        global_token = self.global_projection(torch.tensor(scalar_values, dtype=self._dtype, device=self._device)) + pooled
        members: dict[PublicEntityClassRefV4, list[int]] = {}
        for index, entity in enumerate(ordered):
            if entity.entity_class_ref is not None:
                members.setdefault(entity.entity_class_ref, []).append(index)
        return StateEncodingV4(global_token, tokens, MappingProxyType({ref: tuple(indices) for ref, indices in members.items()}))

    def reference_embedding_v4(self, ref: PublicEntityClassRefV4 | None, encoding: StateEncodingV4) -> torch.Tensor:
        if ref is None:
            return encoding.global_token.new_zeros(self.hidden_dim)
        indices = encoding.class_members.get(ref)
        if not indices:
            raise NeuralModelV4Error("class ref is not bound to encoded state")
        return encoding.entity_tokens[list(indices)].mean(0)

    def _counts(self, candidate: ActionCandidateV4, encoding: StateEncodingV4) -> torch.Tensor:
        ref = candidate.selectable_class_ref
        cardinality = float(len(encoding.class_members.get(ref, ()))) if ref is not None else 0.0
        selected = float(dict(candidate.selected_class_counts).get(ref, 0)) if ref is not None else 0.0
        values = torch.tensor([
            cardinality / 60.0, float(candidate.allowed_alias_count) / 512.0,
            selected / 512.0, float(sum(count for _ref, count in candidate.selected_class_counts)) / 512.0,
        ], dtype=self._dtype, device=self._device)
        return self.count_projection(values)

    def _prefix_embedding(self, state: RelationalStateV4, encoding: StateEncodingV4) -> torch.Tensor:
        if not state.semantic_prefix:
            return encoding.global_token.new_zeros(self.hidden_dim)
        rows: list[torch.Tensor] = []
        for position, token in enumerate(state.semantic_prefix):
            source = self.reference_embedding_v4(token.source_class_ref, encoding)
            target = self.reference_embedding_v4(token.target_class_ref, encoding)
            host = self.reference_embedding_v4(token.host_class_ref, encoding)
            selectable = self.reference_embedding_v4(token.selectable_class_ref, encoding)
            row = self.prefix_mix(torch.cat([
                source, target, host, selectable,
                self.action_type_embedding(torch.tensor(token.action_type, dtype=torch.long, device=self._device)),
                self._arguments(token.categorical_args, token.numeric_args),
            ]))
            if state.prefix_order_sensitive:
                row = self.position_relation(torch.cat([
                    row, self.position_embedding(torch.tensor(position, dtype=torch.long, device=self._device)),
                ]))
            rows.append(row)
        multiplicities: dict[bytes, int] = {}
        for token in state.semantic_prefix:
            key = token.canonical_bytes()
            multiplicities[key] = multiplicities.get(key, 0) + 1
        count_summary = self.prefix_count_projection(torch.tensor([
            float(len(rows)) / 512.0,
            float(len(multiplicities)) / 512.0,
            float(max(multiplicities.values(), default=0)) / 512.0,
            1.0 if state.prefix_order_sensitive else 0.0,
        ], dtype=self._dtype, device=self._device))
        # Sum is count-aware for unordered A vs A,A.  The explicit summary
        # protects this contract even if learned row values approach zero.
        return torch.stack(rows).sum(0) + count_summary

    def encode_candidate_v4(self, candidate: ActionCandidateV4, *, state_encoding: StateEncodingV4) -> torch.Tensor:
        if type(candidate) is not ActionCandidateV4:
            raise NeuralModelV4Error("candidate must be ActionCandidateV4")
        source = self.reference_embedding_v4(candidate.source_class_ref, state_encoding)
        target = self.reference_embedding_v4(candidate.target_class_ref, state_encoding)
        host = self.reference_embedding_v4(candidate.host_class_ref, state_encoding)
        selected = []
        for ref, count in candidate.selected_class_counts:
            selected.extend([self.reference_embedding_v4(ref, state_encoding)] * count)
        selected_embedding = torch.stack(selected).mean(0) if selected else source.new_zeros(self.hidden_dim)
        return self.candidate_mix(torch.cat([
            source, target, host, self.relation_projection(torch.cat([source, target])),
            self.action_type_embedding(torch.tensor(candidate.action_type, dtype=torch.long, device=self._device)),
            self.selection_step_embedding(torch.tensor(candidate.selection_step, dtype=torch.long, device=self._device)),
            selected_embedding + self._counts(candidate, state_encoding),
            self._arguments(candidate.categorical_args, candidate.numeric_args),
        ]))

    def _record_head_output_v4(
        self, state: RelationalStateV4, *, encoding: StateEncodingV4,
        recurrent_token: torch.Tensor, next_hidden: torch.Tensor,
    ) -> PolicyOutputV4:
        """Score one decoder prefix after its physical record was encoded once."""
        global_token = recurrent_token + self._prefix_embedding(state, encoding)
        if not state.candidates:
            logits = global_token.new_zeros((0,))
        else:
            candidates = torch.stack([self.encode_candidate_v4(item, state_encoding=encoding) for item in state.candidates])
            logits = self.candidate_bias(torch.tanh(candidates + global_token)).squeeze(-1)
            mask = torch.tensor([item.excludes_selected_duplicate for item in state.candidates], dtype=torch.bool, device=self._device)
            logits = logits.masked_fill(mask, float("-inf"))
        return PolicyOutputV4(logits, global_token, next_hidden)

    def forward_record_group_v4(
        self, states: tuple[RelationalStateV4, ...], *, hidden_state: torch.Tensor | None = None,
        episode_start: bool = True,
    ) -> tuple[PolicyOutputV4, ...]:
        """Score all prefixes of one physical record with one encode/GRU transition."""
        if type(states) is not tuple or not states or any(type(state) is not RelationalStateV4 for state in states):
            raise NeuralModelV4Error("record group must be a nonempty tuple of RelationalStateV4")
        first = states[0]
        if any(
            state.state_scalars != first.state_scalars or state.entities != first.entities
            for state in states[1:]
        ):
            raise NeuralModelV4Error("record group must share state scalars and entities")
        encoding = self.encode_state_v4(first)
        recurrent, next_hidden = self.memory(
            encoding.global_token.view(1, 1, -1), None if episode_start else hidden_state,
        )
        return tuple(
            self._record_head_output_v4(
                state, encoding=encoding, recurrent_token=recurrent[0, 0], next_hidden=next_hidden,
            )
            for state in states
        )

    def forward_v4(self, state: RelationalStateV4, *, hidden_state: torch.Tensor | None = None, episode_start: bool = True) -> PolicyOutputV4:
        return self.forward_record_group_v4(
            (state,), hidden_state=hidden_state, episode_start=episode_start,
        )[0]

    def step_logits_v4(self, state: RelationalStateV4, *, stop_available: bool) -> tuple[torch.Tensor, torch.Tensor | None]:
        output = self.forward_v4(state)
        return output.logits, self.stop_vector @ output.global_token + self.stop_bias if stop_available else None

    def load_state_dict(self, state_dict: Mapping[str, torch.Tensor], strict: bool = True, assign: bool = False):
        if strict is not True:
            raise NeuralModelV4Error("load_state_dict requires the exact closed v4 state_dict")
        expected_state = super().state_dict()
        if set(state_dict) != set(expected_state):
            raise NeuralModelV4Error("load_state_dict requires the exact closed v4 state_dict")
        for name, expected_tensor in expected_state.items():
            actual_tensor = state_dict[name]
            if (
                type(actual_tensor) is not torch.Tensor
                or actual_tensor.layout != torch.strided
                or actual_tensor.dtype != expected_tensor.dtype
                or actual_tensor.shape != expected_tensor.shape
            ):
                raise NeuralModelV4Error("load_state_dict requires the exact closed v4 state_dict")
            if (actual_tensor.is_floating_point() or actual_tensor.is_complex()) and not torch.isfinite(actual_tensor).all():
                raise NeuralModelV4Error("load_state_dict refuses nonfinite v4 tensors")
        marker = state_dict.get("_schema_marker_v4")
        expected = self._schema_marker_v4.detach().cpu()
        if type(marker) is not torch.Tensor or marker.dtype != torch.uint8 or marker.shape != expected.shape or not torch.equal(marker.detach().cpu(), expected):
            raise NeuralModelV4Error("state_dict does not carry the v4 schema marker")
        return super().load_state_dict(state_dict, strict=True, assign=assign)


def _tensor_state_sha256_v4(state_dict: Mapping[str, torch.Tensor]) -> str:
    digest = hashlib.sha256(b"mage_ptcg:specialist-neural-state:v4\0")
    for name in sorted(state_dict):
        tensor = state_dict[name]
        if type(name) is not str or type(tensor) is not torch.Tensor or tensor.layout != torch.strided:
            raise NeuralModelV4Error("v4 checkpoint state must contain dense named tensors")
        value = tensor.detach().cpu().contiguous()
        if (value.is_floating_point() or value.is_complex()) and not torch.isfinite(value).all():
            raise NeuralModelV4Error("v4 checkpoint state contains nonfinite tensors")
        raw = value.reshape(-1).view(torch.uint8).numpy().tobytes()
        digest.update(name.encode("utf-8") + b"\0")
        digest.update(str(value.dtype).encode("ascii") + b"\0")
        digest.update(str(tuple(value.shape)).encode("ascii") + b"\0")
        digest.update(len(raw).to_bytes(8, "big") + raw)
    return digest.hexdigest()


def _require_sha256_v4(value: object, *, name: str) -> str:
    if type(value) is not str or len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise NeuralModelV4Error(f"{name} must be a lowercase SHA-256")
    return value


def _checkpoint_snapshot_bytes_v4(path: Path, *, expected_file_sha256: str) -> bytes:
    """Read and hash one immutable-in-memory checkpoint snapshot from one FD."""
    if not hasattr(os, "O_NOFOLLOW"):
        raise NeuralModelV4Error("v4 checkpoint cannot be opened safely without O_NOFOLLOW")
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise NeuralModelV4Error("v4 checkpoint cannot be opened safely; symlinks are forbidden") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise NeuralModelV4Error("v4 checkpoint must be a regular file")
        digest = hashlib.sha256()
        chunks: list[bytes] = []
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            digest.update(block)
            chunks.append(block)
        after = os.fstat(descriptor)
    except OSError as exc:
        raise NeuralModelV4Error("v4 checkpoint cannot be read safely") from exc
    finally:
        os.close(descriptor)
    identity_fields = ("st_mode", "st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    if any(getattr(before, field) != getattr(after, field) for field in identity_fields):
        raise NeuralModelV4Error("v4 checkpoint changed while reading")
    raw = b"".join(chunks)
    if len(raw) != before.st_size:
        raise NeuralModelV4Error("v4 checkpoint changed while reading")
    if digest.hexdigest() != expected_file_sha256:
        raise NeuralModelV4Error("v4 checkpoint external file SHA-256 does not match")
    return raw


def _module_source_path_v4(module: object, *, expected_basename: str) -> Path:
    source = getattr(module, "__file__", None)
    spec = getattr(module, "__spec__", None)
    origin = getattr(spec, "origin", None)
    if type(source) is not str or type(origin) is not str:
        raise NeuralModelV4Error("v4 implementation module has no stable source identity")
    source_path = Path(source)
    origin_path = Path(origin)
    if (
        not source_path.is_absolute()
        or source_path.name != expected_basename
        or origin_path != source_path
    ):
        raise NeuralModelV4Error("v4 implementation module source identity is invalid")
    return source_path


def _implementation_source_paths_v4() -> tuple[tuple[str, Path], ...]:
    live_module = sys.modules.get(__name__)
    if live_module is None:
        raise NeuralModelV4Error("v4 neural module is not live")
    representation_path = _module_source_path_v4(
        representation_v4_module, expected_basename="representation_v4.py",
    )
    neural_path = _module_source_path_v4(
        live_module, expected_basename="neural_model_v4.py",
    )
    if representation_path.parent != neural_path.parent:
        raise NeuralModelV4Error("v4 implementation modules must share one package directory")
    return (
        ("representation_v4.py", representation_path),
        ("neural_model_v4.py", neural_path),
    )


def _stable_source_bytes_v4(path: Path) -> bytes:
    if not isinstance(path, Path) or not path.is_absolute():
        raise NeuralModelV4Error("v4 implementation source path is invalid")
    if path.is_symlink():
        raise NeuralModelV4Error("v4 implementation source must not be a symlink")
    try:
        if path.resolve(strict=True) != path:
            raise NeuralModelV4Error("v4 implementation source must not use a symlinked path")
    except OSError as exc:
        raise NeuralModelV4Error("v4 implementation source cannot be resolved") from exc
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise NeuralModelV4Error("v4 implementation source cannot be opened safely") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise NeuralModelV4Error("v4 implementation source must be a regular file")
        chunks: list[bytes] = []
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            chunks.append(block)
        after = os.fstat(descriptor)
    except OSError as exc:
        raise NeuralModelV4Error("v4 implementation source cannot be read") from exc
    finally:
        os.close(descriptor)
    identity_fields = ("st_mode", "st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    if any(getattr(before, field) != getattr(after, field) for field in identity_fields):
        raise NeuralModelV4Error("v4 implementation source changed while reading")
    try:
        live = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise NeuralModelV4Error("v4 implementation source disappeared after reading") from exc
    if any(getattr(after, field) != getattr(live, field) for field in identity_fields):
        raise NeuralModelV4Error("v4 implementation source changed while reading")
    payload = b"".join(chunks)
    if len(payload) != before.st_size:
        raise NeuralModelV4Error("v4 implementation source changed while reading")
    return payload


def _implementation_digest_v4() -> str:
    sources = _implementation_source_paths_v4()
    expected_order = ("representation_v4.py", "neural_model_v4.py")
    if tuple(name for name, _path in sources) != expected_order:
        raise NeuralModelV4Error("v4 implementation source closure is invalid")
    digest = hashlib.sha256(_IMPLEMENTATION_DIGEST_PREFIX_V4)
    for name, path in sources:
        if path.name != name:
            raise NeuralModelV4Error("v4 implementation source identity is invalid")
        payload = _stable_source_bytes_v4(path)
        encoded_name = name.encode("ascii")
        digest.update(b"source\0")
        digest.update(len(encoded_name).to_bytes(4, "big") + encoded_name)
        digest.update(len(payload).to_bytes(8, "big") + payload)
    return digest.hexdigest()


def _framed_live_value_v4(tag: bytes, payload: bytes) -> bytes:
    return len(tag).to_bytes(2, "big") + tag + len(payload).to_bytes(8, "big") + payload


def _live_value_bytes_v4(value: object) -> bytes:
    """Serialize callable semantics without reprs, paths, lines, or addresses."""
    if value is None:
        return _framed_live_value_v4(b"none", b"")
    if value is Ellipsis:
        return _framed_live_value_v4(b"ellipsis", b"")
    if type(value) is bool:
        return _framed_live_value_v4(b"bool", b"1" if value else b"0")
    if type(value) is int:
        return _framed_live_value_v4(b"int", str(value).encode("ascii"))
    if type(value) is float:
        return _framed_live_value_v4(b"float", value.hex().encode("ascii"))
    if type(value) is complex:
        payload = _live_value_bytes_v4(value.real) + _live_value_bytes_v4(value.imag)
        return _framed_live_value_v4(b"complex", payload)
    if type(value) is str:
        return _framed_live_value_v4(b"str", value.encode("utf-8"))
    if type(value) is bytes:
        return _framed_live_value_v4(b"bytes", value)
    if type(value) is tuple:
        return _framed_live_value_v4(b"tuple", b"".join(_live_value_bytes_v4(item) for item in value))
    if type(value) is frozenset:
        members = sorted(_live_value_bytes_v4(item) for item in value)
        return _framed_live_value_v4(b"frozenset", b"".join(members))
    if type(value) is dict:
        pairs = sorted(
            (_live_value_bytes_v4(key), _live_value_bytes_v4(item))
            for key, item in value.items()
        )
        return _framed_live_value_v4(
            b"dict", b"".join(_framed_live_value_v4(b"key", key) + _framed_live_value_v4(b"value", item) for key, item in pairs),
        )
    if type(value) is types.CodeType:
        code = value
        fields = (
            code.co_argcount, code.co_posonlyargcount, code.co_kwonlyargcount,
            code.co_nlocals, code.co_stacksize, code.co_flags, code.co_code,
            code.co_consts, code.co_names, code.co_varnames, code.co_freevars,
            code.co_cellvars, code.co_exceptiontable,
        )
        return _framed_live_value_v4(b"code", _live_value_bytes_v4(fields))
    raise NeuralModelV4Error("v4 live callable semantics contain an unsupported value")


def _referenced_semantic_global_names_v4(code: types.CodeType) -> frozenset[str]:
    """Return globals that this callable (including nested closures) reads.

    ``co_names`` also carries attribute names, so it cannot distinguish
    ``module.attribute`` from a true module-global lookup.  Instruction-level
    collection keeps the binding surface deliberately limited to globals which
    execute in this function's module namespace.
    """
    names: set[str] = set()
    pending = [code]
    while pending:
        current = pending.pop()
        for instruction in dis.get_instructions(current):
            if instruction.opname in {"LOAD_GLOBAL", "LOAD_NAME"} and type(instruction.argval) is str:
                names.add(instruction.argval)
        pending.extend(
            value for value in current.co_consts if type(value) is types.CodeType
        )
    return frozenset(names)


def _semantic_module_global_bindings_v4(function: types.FunctionType) -> tuple[tuple[str, bytes], ...]:
    """Bind value-like globals resolved by a live callable at execution time.

    The callable digest already seals source, bytecode, defaults and every
    local v4 callable.  This narrow addition seals only values whose runtime
    replacement can alter behavior without changing any of those artifacts;
    modules, classes and functions are intentionally not scraped as a broad
    global snapshot.  Their live semantics are either not value-like or are
    sealed by the explicit callable member closure above.
    """
    global_namespace = function.__globals__
    bindings: list[tuple[str, bytes]] = []
    for name in sorted(_referenced_semantic_global_names_v4(function.__code__), key=str.encode):
        if name not in global_namespace:
            continue
        value = global_namespace[name]
        try:
            framed_value = _live_value_bytes_v4(value)
        except NeuralModelV4Error:
            continue
        bindings.append((name, framed_value))
    return tuple(bindings)


def _function_semantics_bytes_v4(function: object) -> bytes:
    if type(function) is not types.FunctionType:
        raise NeuralModelV4Error("v4 live callable closure contains a non-function")
    return _live_value_bytes_v4((
        function.__code__, function.__defaults__, function.__kwdefaults__,
        _semantic_module_global_bindings_v4(function),
    ))


def _live_callable_members_v4() -> tuple[tuple[str, types.FunctionType], ...]:
    live_module = sys.modules.get(__name__)
    if live_module is None:
        raise NeuralModelV4Error("v4 neural module is not live")
    members: list[tuple[str, types.FunctionType]] = []
    for logical_module, module in (
        ("representation_v4", representation_v4_module),
        ("neural_model_v4", live_module),
    ):
        for name, value in vars(module).items():
            if type(value) is types.FunctionType and value.__module__ == module.__name__:
                members.append((f"{logical_module}.{name}", value))
    for name, value in vars(SpecialistModelV4).items():
        if type(value) is types.FunctionType:
            members.append((f"SpecialistModelV4.{name}", value))
        elif type(value) is staticmethod or type(value) is classmethod:
            members.append((f"SpecialistModelV4.{name}", value.__func__))
        elif type(value) is property:
            for accessor_name, accessor in (("fget", value.fget), ("fset", value.fset), ("fdel", value.fdel)):
                if accessor is not None:
                    members.append((f"SpecialistModelV4.{name}.{accessor_name}", accessor))
    ordered = tuple(sorted(members, key=lambda item: item[0].encode("utf-8")))
    names = tuple(name for name, _function in ordered)
    if not ordered or len(set(names)) != len(names):
        raise NeuralModelV4Error("v4 live callable closure is invalid")
    return ordered


def _live_callable_digest_v4() -> str:
    digest = hashlib.sha256(_LIVE_CALLABLE_DIGEST_PREFIX_V4)
    for name, function in _live_callable_members_v4():
        encoded_name = name.encode("utf-8")
        semantics = _function_semantics_bytes_v4(function)
        digest.update(b"callable\0")
        digest.update(len(encoded_name).to_bytes(4, "big") + encoded_name)
        digest.update(len(semantics).to_bytes(8, "big") + semantics)
    return digest.hexdigest()


def _checkpoint_descriptor_v4(model: SpecialistModelV4, state_dict: Mapping[str, torch.Tensor]) -> dict[str, object]:
    if type(model) is not SpecialistModelV4:
        raise NeuralModelV4Error("checkpoint model must be SpecialistModelV4")
    return {
        "checkpoint_schema": CHECKPOINT_SCHEMA_V4,
        "representation_schema": REPRESENTATION_V4_SCHEMA,
        "neural_model_schema": NEURAL_MODEL_SCHEMA_V4,
        "implementation_digest_sha256": _implementation_digest_v4(),
        "live_callable_digest_sha256": _live_callable_digest_v4(),
        "model_config": dict(model._model_config),
        "tensor_state_sha256": _tensor_state_sha256_v4(state_dict),
    }


def _validate_checkpoint_descriptor_v4(
    descriptor: object, model: SpecialistModelV4,
) -> dict[str, object]:
    expected_keys = {
        "checkpoint_schema", "representation_schema", "neural_model_schema",
        "implementation_digest_sha256", "live_callable_digest_sha256",
        "model_config", "tensor_state_sha256",
    }
    if type(descriptor) is not dict or set(descriptor) != expected_keys:
        raise NeuralModelV4Error("artifact is not a closed v4 checkpoint descriptor")
    if (
        type(descriptor["checkpoint_schema"]) is not str
        or descriptor["checkpoint_schema"] != CHECKPOINT_SCHEMA_V4
        or type(descriptor["representation_schema"]) is not str
        or descriptor["representation_schema"] != REPRESENTATION_V4_SCHEMA
        or type(descriptor["neural_model_schema"]) is not str
        or descriptor["neural_model_schema"] != NEURAL_MODEL_SCHEMA_V4
    ):
        raise NeuralModelV4Error("v4 checkpoint descriptor schema binding failed")
    _require_sha256_v4(
        descriptor["implementation_digest_sha256"], name="implementation_digest_sha256",
    )
    _require_sha256_v4(
        descriptor["live_callable_digest_sha256"], name="live_callable_digest_sha256",
    )
    _require_sha256_v4(
        descriptor["tensor_state_sha256"], name="tensor_state_sha256",
    )
    model_config = descriptor["model_config"]
    expected_config = dict(model._model_config)
    if (
        type(model_config) is not dict
        or set(model_config) != set(expected_config)
        or any(type(value) is not int or value < 1 for value in model_config.values())
        or model_config != expected_config
    ):
        raise NeuralModelV4Error("v4 checkpoint descriptor model_config binding failed")
    return descriptor


def save_specialist_checkpoint_v4(path: str | os.PathLike[str], model: SpecialistModelV4) -> dict[str, object]:
    """Atomically write one closed v4 checkpoint artifact."""
    target = Path(path)
    if not target.parent.is_dir():
        raise NeuralModelV4Error("v4 checkpoint parent directory does not exist")
    state = {name: tensor.detach().cpu().clone() for name, tensor in model.state_dict().items()}
    descriptor = _checkpoint_descriptor_v4(model, state)
    payload = {"descriptor": descriptor, "state_dict": state}
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        torch.save(payload, temporary)
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()
    return descriptor


def load_specialist_checkpoint_v4(
    path: str | os.PathLike[str], model: SpecialistModelV4, *,
    expected_file_sha256: str, expected_tensor_state_sha256: str,
) -> dict[str, object]:
    """Strictly validate and load a v4 artifact; v3/raw states are rejected."""
    if type(model) is not SpecialistModelV4:
        raise NeuralModelV4Error("v4 checkpoint target must be SpecialistModelV4")
    expected_file = _require_sha256_v4(expected_file_sha256, name="expected_file_sha256")
    expected_state = _require_sha256_v4(expected_tensor_state_sha256, name="expected_tensor_state_sha256")
    checkpoint_path = Path(path)
    raw = _checkpoint_snapshot_bytes_v4(
        checkpoint_path, expected_file_sha256=expected_file,
    )
    try:
        payload = torch.load(io.BytesIO(raw), map_location="cpu", weights_only=True)
    except (OSError, RuntimeError, ValueError, TypeError, EOFError) as exc:
        raise NeuralModelV4Error("v4 checkpoint cannot be read") from exc
    if type(payload) is not dict or set(payload) != {"descriptor", "state_dict"}:
        raise NeuralModelV4Error("artifact is not a closed v4 checkpoint")
    descriptor = _validate_checkpoint_descriptor_v4(payload["descriptor"], model)
    state = payload["state_dict"]
    if descriptor["implementation_digest_sha256"] != _implementation_digest_v4():
        raise NeuralModelV4Error("v4 checkpoint implementation digest does not match live source closure")
    if descriptor["live_callable_digest_sha256"] != _live_callable_digest_v4():
        raise NeuralModelV4Error("v4 checkpoint live callable digest does not match live callable semantics")
    if (
        type(state) is not dict
        or descriptor["tensor_state_sha256"] != expected_state
        or descriptor["tensor_state_sha256"] != _tensor_state_sha256_v4(state)
    ):
        raise NeuralModelV4Error("v4 checkpoint schema/config/state binding failed")
    model.load_state_dict(state, strict=True)
    return dict(descriptor)


__all__ = [
    "CHECKPOINT_SCHEMA_V4", "NEURAL_MODEL_SCHEMA_V4", "NeuralModelV4Error", "PolicyOutputV4",
    "SpecialistModelV4", "StateEncodingV4", "load_specialist_checkpoint_v4", "save_specialist_checkpoint_v4",
]
