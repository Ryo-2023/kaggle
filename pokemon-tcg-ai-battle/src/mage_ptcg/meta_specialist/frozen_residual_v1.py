"""Research-only frozen Wave6 residual sidecar.

This module deliberately sits outside the packaged V4 policy path.  It is a
small contract for a future bounded pilot:

* the Wave6 policy supplies the base semantic/STOP logits and remains frozen;
* a zero-initialised sidecar can add a bounded residual only for hash-bound,
  in-domain public contexts and semantic action keys;
* malformed or OOD context fails closed to an exact base-logit pass-through;
* the sidecar never owns GRU state, semantic decoding, legality, or commit.

The implementation is intentionally useful before a trainer/evaluator
integration exists.  A future runner can build a manifest containing the
Wave6 checkpoint file/tensor SHA, the allowed public context IDs and action
keys, then compose :class:`FrozenResidualPolicyV1` around the existing
research policy factory without changing production V4 code.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
import hashlib
import math
from threading import RLock
from types import MappingProxyType
from typing import Any

import torch
from torch import Tensor, nn
from torch.nn import functional as F

from mage_ptcg.meta_specialist.actor_visible_features_v1 import (
    SpecialistModelInputV1,
    SpecialistStepInputV1,
    SpecialistStepLogitsV1,
    canonical_model_input_bytes_v1,
    canonical_step_input_bytes_v1,
)
from mage_ptcg.meta_specialist.runtime import (
    CommittedSemanticDecisionV2,
    PolicyTelemetrySnapshot,
    SpecialistDecisionSessionV2,
)


FROZEN_RESIDUAL_SCHEMA_V1 = "specialist-frozen-wave6-residual-v1"
STOP_ACTION_KEY_V1 = hashlib.sha256(
    b"mage_ptcg:specialist-frozen-wave6-residual:stop:v1\0"
).hexdigest()
_CONTEXT_PREFIX = b"mage_ptcg:specialist-frozen-wave6-residual:context:v1\0"
_ACTION_PREFIX = b"mage_ptcg:specialist-frozen-wave6-residual:action:v1\0"
_STOP_FEATURE_PREFIX = b"mage_ptcg:specialist-frozen-wave6-residual:stop-feature:v1\0"
_STATE_FEATURE_DIM = 16
_ACTION_FEATURE_DIM = 8
_HEX64 = frozenset("0123456789abcdef")


class FrozenResidualError(ValueError):
    """Raised when a sidecar contract or research adapter input is invalid."""


def _require_sha256(value: object, *, field: str) -> str:
    if type(value) is not str or len(value) != 64 or any(char not in _HEX64 for char in value):
        raise FrozenResidualError(f"{field} must be a lowercase SHA-256")
    return value


def _require_finite_float(value: object, *, field: str) -> float:
    if type(value) not in (int, float) or type(value) is bool or not math.isfinite(float(value)):
        raise FrozenResidualError(f"{field} must be a finite number")
    return float(value)


def _bounded_scalar(value: int) -> float:
    if type(value) is not int or value < 0:
        raise FrozenResidualError("public state scalar must be a nonnegative int")
    return float(value) / (1.0 + float(value))


def _digest_features(prefix: bytes, value: bytes, width: int = _ACTION_FEATURE_DIM) -> tuple[float, ...]:
    digest = hashlib.sha256(prefix + value).digest()
    if width < 1 or width > len(digest):
        raise FrozenResidualError("digest feature width is outside its closed range")
    return tuple((float(byte) - 127.5) / 127.5 for byte in digest[:width])


def _action_key(semantic_bytes: bytes) -> str:
    return hashlib.sha256(_ACTION_PREFIX + semantic_bytes).hexdigest()


@dataclass(frozen=True, slots=True)
class ResidualContextV1:
    """Validated serial-free public context consumed by the residual head."""

    schema_version: str
    context_id: str
    state_features: tuple[float, ...]
    action_keys: tuple[str, ...]
    action_features: tuple[tuple[float, ...], ...]
    stop_available: bool

    def __post_init__(self) -> None:
        if self.schema_version != FROZEN_RESIDUAL_SCHEMA_V1:
            raise FrozenResidualError("residual context schema is not v1")
        _require_sha256(self.context_id, field="context_id")
        if type(self.state_features) is not tuple or len(self.state_features) != _STATE_FEATURE_DIM:
            raise FrozenResidualError("residual context state feature width is invalid")
        if any(type(value) not in (int, float) or type(value) is bool or not math.isfinite(float(value)) or not -1.0 <= float(value) <= 1.0 for value in self.state_features):
            raise FrozenResidualError("residual context state features must be bounded finite values")
        if type(self.action_keys) is not tuple or type(self.action_features) is not tuple:
            raise FrozenResidualError("residual context action fields must be tuples")
        if len(self.action_keys) != len(self.action_features):
            raise FrozenResidualError("residual context action key/feature arity mismatch")
        if any(_require_sha256(key, field="action_key") != key for key in self.action_keys):
            raise FrozenResidualError("residual context action key is invalid")
        for row in self.action_features:
            if type(row) is not tuple or len(row) != _ACTION_FEATURE_DIM:
                raise FrozenResidualError("residual context action feature width is invalid")
            if any(type(value) not in (int, float) or type(value) is bool or not math.isfinite(float(value)) or not -1.0 <= float(value) <= 1.0 for value in row):
                raise FrozenResidualError("residual context action features must be bounded finite values")
        if type(self.stop_available) is not bool:
            raise FrozenResidualError("residual context stop_available must be bool")

    @property
    def stop_features(self) -> tuple[float, ...]:
        return _digest_features(_STOP_FEATURE_PREFIX, b"STOP")


def build_residual_context_v1(
    model_input: SpecialistModelInputV1,
    step_input: SpecialistStepInputV1,
) -> ResidualContextV1:
    """Build a deterministic residual context from public V1 model/step data.

    No physical serial, local action ID, opponent ID, seat, or policy identity
    is included.  Action features are semantic canonical-byte hashes only.
    """
    if type(model_input) is not SpecialistModelInputV1 or type(step_input) is not SpecialistStepInputV1:
        raise FrozenResidualError("residual context requires exact public V1 input types")
    SpecialistModelInputV1.__post_init__(model_input)
    SpecialistStepInputV1.__post_init__(step_input)
    scalars = tuple(_bounded_scalar(value) for value in model_input.state_scalars[:12])
    state_features = scalars + (
        float(len(model_input.candidate_rows)) / 512.0,
        float(len(step_input.semantic_prefix)) / 512.0,
        1.0 if step_input.order_semantics == "ordered_sequence" else 0.0,
        1.0 if step_input.stop_available else 0.0,
    )
    if len(state_features) != _STATE_FEATURE_DIM:
        raise FrozenResidualError("residual state feature construction changed width")
    action_bytes = tuple(item.semantic_row.canonical_bytes for item in step_input.allowed_semantic_classes)
    action_keys = tuple(_action_key(value) for value in action_bytes)
    action_features = tuple(_digest_features(b"mage_ptcg:specialist-frozen-wave6-residual:action-feature:v1\0", value) for value in action_bytes)
    context_payload = canonical_model_input_bytes_v1(model_input) + b"\0" + canonical_step_input_bytes_v1(step_input)
    context_id = hashlib.sha256(_CONTEXT_PREFIX + context_payload).hexdigest()
    return ResidualContextV1(
        schema_version=FROZEN_RESIDUAL_SCHEMA_V1,
        context_id=context_id,
        state_features=state_features,
        action_keys=action_keys,
        action_features=action_features,
        stop_available=step_input.stop_available,
    )


@dataclass(frozen=True, slots=True)
class ResidualLogitsV1:
    semantic: Tensor
    stop: Tensor | None


@dataclass(frozen=True, slots=True)
class ResidualLossBreakdownV1:
    imitation: Tensor
    anchor_kl: Tensor
    residual_l2: Tensor
    total: Tensor


@dataclass(frozen=True, slots=True)
class ResidualCoverageSnapshotV1:
    """Immutable runtime coverage ledger for one sidecar instance.

    The sidecar is deliberately allowed to remain a pure research adapter, so
    this snapshot contains counters only.  It does not expose observations,
    opponent IDs, seats, private state, or any authority bit.  A factory can
    take a snapshot before/after a game and attach the resulting delta to its
    own opponent/seat cell ledger.
    """

    total_decisions: int = 0
    valid_context_decisions: int = 0
    exact_known_context: int = 0
    eligible_action_slots: int = 0
    known_action_slots: int = 0
    residual_applied_slots: int = 0
    nonzero_residual_slots: int = 0
    top1_change_decisions: int = 0
    ood_pass_through: int = 0
    stop_decisions: int = 0
    known_stop_decisions: int = 0
    nonzero_stop_decisions: int = 0
    action_type_total: Mapping[str, int] = field(default_factory=dict)
    action_type_known: Mapping[str, int] = field(default_factory=dict)
    action_type_nonzero: Mapping[str, int] = field(default_factory=dict)
    pass_through_reasons: Mapping[str, int] = field(default_factory=dict)
    residual_magnitudes: tuple[float, ...] = ()

    def __post_init__(self) -> None:
        counters = (
            "total_decisions", "valid_context_decisions", "exact_known_context",
            "eligible_action_slots", "known_action_slots", "residual_applied_slots",
            "nonzero_residual_slots", "top1_change_decisions", "ood_pass_through",
            "stop_decisions", "known_stop_decisions", "nonzero_stop_decisions",
        )
        for field_name in counters:
            value = getattr(self, field_name)
            if type(value) is not int or value < 0:
                raise FrozenResidualError(f"coverage {field_name} must be a nonnegative int")
        for name in ("action_type_total", "action_type_known", "action_type_nonzero", "pass_through_reasons"):
            value = getattr(self, name)
            if not isinstance(value, Mapping):
                raise FrozenResidualError(f"coverage {name} must be a mapping")
            checked = {}
            for key, count in value.items():
                if type(key) is not str or not key or type(count) is not int or count < 0:
                    raise FrozenResidualError(f"coverage {name} contains an invalid entry")
                checked[key] = count
            object.__setattr__(self, name, MappingProxyType(checked))
        if type(self.residual_magnitudes) is not tuple or any(
            type(value) not in (int, float) or type(value) is bool
            or not math.isfinite(float(value)) or float(value) < 0.0
            for value in self.residual_magnitudes
        ):
            raise FrozenResidualError("coverage residual magnitudes must be finite nonnegative values")

    @staticmethod
    def _map_delta(current: Mapping[str, int], previous: Mapping[str, int]) -> dict[str, int]:
        keys = set(current) | set(previous)
        return {key: int(current.get(key, 0)) - int(previous.get(key, 0)) for key in keys if int(current.get(key, 0)) - int(previous.get(key, 0))}

    def delta(self, previous: "ResidualCoverageSnapshotV1") -> "ResidualCoverageSnapshotV1":
        """Return the monotonic counter delta since ``previous``."""
        if type(previous) is not ResidualCoverageSnapshotV1:
            raise FrozenResidualError("coverage delta requires a ResidualCoverageSnapshotV1")
        counters = {
            name: getattr(self, name) - getattr(previous, name)
            for name in (
                "total_decisions", "valid_context_decisions", "exact_known_context",
                "eligible_action_slots", "known_action_slots", "residual_applied_slots",
                "nonzero_residual_slots", "top1_change_decisions", "ood_pass_through",
                "stop_decisions", "known_stop_decisions", "nonzero_stop_decisions",
            )
        }
        if any(value < 0 for value in counters.values()):
            raise FrozenResidualError("coverage counters are not monotonic")
        magnitudes = self.residual_magnitudes
        previous_magnitudes = previous.residual_magnitudes
        if len(previous_magnitudes) > len(magnitudes) or magnitudes[:len(previous_magnitudes)] != previous_magnitudes:
            raise FrozenResidualError("coverage magnitude ledger is not append-only")
        return ResidualCoverageSnapshotV1(
            **counters,
            action_type_total=self._map_delta(self.action_type_total, previous.action_type_total),
            action_type_known=self._map_delta(self.action_type_known, previous.action_type_known),
            action_type_nonzero=self._map_delta(self.action_type_nonzero, previous.action_type_nonzero),
            pass_through_reasons=self._map_delta(self.pass_through_reasons, previous.pass_through_reasons),
            residual_magnitudes=magnitudes[len(previous_magnitudes):],
        )

    @staticmethod
    def _rate(numerator: int, denominator: int) -> float | None:
        return float(numerator) / float(denominator) if denominator else None

    @staticmethod
    def _percentile(values: tuple[float, ...], fraction: float) -> float | None:
        if not values:
            return None
        ordered = sorted(float(value) for value in values)
        index = (len(ordered) - 1) * fraction
        lower = int(math.floor(index))
        upper = int(math.ceil(index))
        if lower == upper:
            return ordered[lower]
        weight = index - lower
        return ordered[lower] * (1.0 - weight) + ordered[upper] * weight

    def to_dict(self) -> dict[str, object]:
        magnitudes = self.residual_magnitudes
        magnitude_summary = {
            "count": len(magnitudes),
            "mean": None if not magnitudes else math.fsum(magnitudes) / len(magnitudes),
            "p50": self._percentile(magnitudes, 0.50),
            "p95": self._percentile(magnitudes, 0.95),
            "max": None if not magnitudes else max(magnitudes),
        }
        return {
            "schema_version": "specialist-frozen-wave6-residual-coverage-v1",
            "total_decisions": self.total_decisions,
            "valid_context_decisions": self.valid_context_decisions,
            "exact_known_context": self.exact_known_context,
            "exact_known_context_rate": self._rate(self.exact_known_context, self.total_decisions),
            "eligible_action_slots": self.eligible_action_slots,
            "known_action_slots": self.known_action_slots,
            "known_action": self.known_action_slots,
            "known_action_rate": self._rate(self.known_action_slots, self.eligible_action_slots),
            "residual_applied_slots": self.residual_applied_slots,
            "residual_applied_rate": self._rate(self.residual_applied_slots, self.eligible_action_slots),
            "nonzero_residual_slots": self.nonzero_residual_slots,
            "nonzero_residual": self.nonzero_residual_slots,
            "nonzero_residual_rate": self._rate(self.nonzero_residual_slots, self.residual_applied_slots),
            "top1_change_decisions": self.top1_change_decisions,
            "top1_change_rate": self._rate(self.top1_change_decisions, self.total_decisions),
            "ood_pass_through": self.ood_pass_through,
            "ood_pass_through_rate": self._rate(self.ood_pass_through, self.total_decisions),
            "stop_decisions": self.stop_decisions,
            "stop": self.stop_decisions,
            "known_stop_decisions": self.known_stop_decisions,
            "nonzero_stop_decisions": self.nonzero_stop_decisions,
            "action_type": {
                key: {
                    "eligible": int(value),
                    "known": int(self.action_type_known.get(key, 0)),
                    "nonzero": int(self.action_type_nonzero.get(key, 0)),
                }
                for key, value in sorted(self.action_type_total.items())
            },
            "pass_through_reasons": dict(sorted(self.pass_through_reasons.items())),
            "residual_magnitude": magnitude_summary,
        }


class FrozenResidualSidecarV1(nn.Module):
    """Zero-init bounded additive residual head for a frozen Wave6 policy."""

    def __init__(
        self,
        *,
        state_feature_dim: int = _STATE_FEATURE_DIM,
        action_feature_dim: int = _ACTION_FEATURE_DIM,
        hidden_dim: int = 32,
        max_abs_residual: float = 0.25,
        known_context_ids: Iterable[str] = (),
        known_action_keys: Iterable[str] = (),
        base_checkpoint_file_sha256: str | None = None,
        base_checkpoint_tensor_sha256: str | None = None,
    ) -> None:
        super().__init__()
        dims = (state_feature_dim, action_feature_dim, hidden_dim)
        if any(type(value) is not int or value < 1 for value in dims):
            raise FrozenResidualError("residual sidecar dimensions must be positive ints")
        max_abs = _require_finite_float(max_abs_residual, field="max_abs_residual")
        if not 0.0 < max_abs <= 1.0:
            raise FrozenResidualError("max_abs_residual must be in (0, 1]")
        def hashes(values: Iterable[str], *, field: str) -> frozenset[str]:
            try:
                result = frozenset(values)
            except TypeError as exc:
                raise FrozenResidualError(f"{field} must be an iterable of SHA-256 strings") from exc
            for item in result:
                _require_sha256(item, field=field)
            return result
        self.state_feature_dim = state_feature_dim
        self.action_feature_dim = action_feature_dim
        self.hidden_dim = hidden_dim
        self.max_abs_residual = max_abs
        self.known_context_ids = hashes(known_context_ids, field="known_context_ids")
        self.known_action_keys = hashes(known_action_keys, field="known_action_keys")
        self.base_checkpoint_file_sha256 = None if base_checkpoint_file_sha256 is None else _require_sha256(base_checkpoint_file_sha256, field="base_checkpoint_file_sha256")
        self.base_checkpoint_tensor_sha256 = None if base_checkpoint_tensor_sha256 is None else _require_sha256(base_checkpoint_tensor_sha256, field="base_checkpoint_tensor_sha256")
        if (self.base_checkpoint_file_sha256 is None) != (self.base_checkpoint_tensor_sha256 is None):
            raise FrozenResidualError("base checkpoint file/tensor SHA must be supplied as a pair")
        self._coverage_lock = RLock()
        self._coverage_counts: dict[str, int] = {
            "total_decisions": 0,
            "valid_context_decisions": 0,
            "exact_known_context": 0,
            "eligible_action_slots": 0,
            "known_action_slots": 0,
            "residual_applied_slots": 0,
            "nonzero_residual_slots": 0,
            "top1_change_decisions": 0,
            "ood_pass_through": 0,
            "stop_decisions": 0,
            "known_stop_decisions": 0,
            "nonzero_stop_decisions": 0,
        }
        self._coverage_action_type_total: dict[str, int] = {}
        self._coverage_action_type_known: dict[str, int] = {}
        self._coverage_action_type_nonzero: dict[str, int] = {}
        self._coverage_pass_through_reasons: dict[str, int] = {}
        self._coverage_residual_magnitudes: list[float] = []
        self.input_projection = nn.Sequential(
            nn.Linear(state_feature_dim + action_feature_dim, hidden_dim),
            nn.Tanh(),
            nn.Linear(hidden_dim, hidden_dim),
            nn.Tanh(),
        )
        self.output = nn.Linear(hidden_dim, 1)
        # Zero-init is the key safety property: at construction alpha == 0.
        nn.init.zeros_(self.output.weight)
        nn.init.zeros_(self.output.bias)

    @staticmethod
    def _top1(values: Sequence[float]) -> int:
        if not values:
            raise FrozenResidualError("coverage top1 requires a nonempty logit domain")
        # ``max`` is stable, so equal values preserve the runtime's first
        # candidate tie order.  This is a diagnostic comparison only; the
        # existing decoder remains authoritative for actual action choice.
        return max(range(len(values)), key=lambda index: values[index])

    @staticmethod
    def _action_types(value: Sequence[int] | None, count: int) -> tuple[str, ...]:
        if value is None:
            return tuple("unknown" for _ in range(count))
        try:
            values = tuple(value)
        except TypeError:
            return tuple("unknown" for _ in range(count))
        if len(values) != count or any(type(item) is not int or item < 0 or item > 16 for item in values):
            return tuple("unknown" for _ in range(count))
        return tuple(str(item) for item in values)

    def _record_coverage(
        self,
        *,
        valid_context: bool,
        exact_known_context: bool,
        eligible_action_slots: int,
        known_action_slots: int,
        residual_applied_slots: int,
        nonzero_residual_slots: int,
        top1_changed: bool,
        ood_pass_through: bool,
        pass_through_reason: str | None,
        action_types: tuple[str, ...],
        known_action_types: tuple[str, ...],
        nonzero_action_types: tuple[str, ...],
        stop_available: bool,
        stop_known: bool,
        stop_nonzero: bool,
        residual_magnitudes: Sequence[float],
    ) -> None:
        if any(type(value) is not int or value < 0 for value in (
            eligible_action_slots, known_action_slots, residual_applied_slots,
            nonzero_residual_slots,
        )):
            raise FrozenResidualError("coverage slot counters are invalid")
        with self._coverage_lock:
            self._coverage_counts["total_decisions"] += 1
            self._coverage_counts["valid_context_decisions"] += int(valid_context)
            self._coverage_counts["exact_known_context"] += int(exact_known_context)
            self._coverage_counts["eligible_action_slots"] += eligible_action_slots
            self._coverage_counts["known_action_slots"] += known_action_slots
            self._coverage_counts["residual_applied_slots"] += residual_applied_slots
            self._coverage_counts["nonzero_residual_slots"] += nonzero_residual_slots
            self._coverage_counts["top1_change_decisions"] += int(top1_changed)
            self._coverage_counts["ood_pass_through"] += int(ood_pass_through)
            self._coverage_counts["stop_decisions"] += int(stop_available)
            self._coverage_counts["known_stop_decisions"] += int(stop_known)
            self._coverage_counts["nonzero_stop_decisions"] += int(stop_nonzero)
            for key in action_types:
                self._coverage_action_type_total[key] = self._coverage_action_type_total.get(key, 0) + 1
            for key in known_action_types:
                self._coverage_action_type_known[key] = self._coverage_action_type_known.get(key, 0) + 1
            for key in nonzero_action_types:
                self._coverage_action_type_nonzero[key] = self._coverage_action_type_nonzero.get(key, 0) + 1
            if pass_through_reason is not None:
                self._coverage_pass_through_reasons[pass_through_reason] = self._coverage_pass_through_reasons.get(pass_through_reason, 0) + 1
            for value in residual_magnitudes:
                magnitude = float(value)
                if not math.isfinite(magnitude) or magnitude < 0.0:
                    raise FrozenResidualError("coverage residual magnitude is invalid")
                self._coverage_residual_magnitudes.append(magnitude)

    def coverage_snapshot(self) -> ResidualCoverageSnapshotV1:
        """Return a stable counter snapshot for an evaluator ledger."""
        with self._coverage_lock:
            return ResidualCoverageSnapshotV1(
                **self._coverage_counts,
                action_type_total=dict(self._coverage_action_type_total),
                action_type_known=dict(self._coverage_action_type_known),
                action_type_nonzero=dict(self._coverage_action_type_nonzero),
                pass_through_reasons=dict(self._coverage_pass_through_reasons),
                residual_magnitudes=tuple(self._coverage_residual_magnitudes),
            )

    def reset_coverage(self) -> ResidualCoverageSnapshotV1:
        """Clear counters and return the pre-reset snapshot for a new ledger."""
        with self._coverage_lock:
            previous = self.coverage_snapshot()
            for key in self._coverage_counts:
                self._coverage_counts[key] = 0
            self._coverage_action_type_total.clear()
            self._coverage_action_type_known.clear()
            self._coverage_action_type_nonzero.clear()
            self._coverage_pass_through_reasons.clear()
            self._coverage_residual_magnitudes.clear()
            return previous

    def _zero(self, *, count: int, device: torch.device, dtype: torch.dtype, stop: bool) -> ResidualLogitsV1:
        return ResidualLogitsV1(
            semantic=torch.zeros(count, device=device, dtype=dtype),
            stop=torch.zeros((), device=device, dtype=dtype) if stop else None,
        )

    def _validate_context(self, context: object) -> ResidualContextV1:
        if type(context) is not ResidualContextV1:
            raise FrozenResidualError("residual context must be exact ResidualContextV1")
        ResidualContextV1.__post_init__(context)
        if len(context.state_features) != self.state_feature_dim or any(len(row) != self.action_feature_dim for row in context.action_features):
            raise FrozenResidualError("residual context feature width does not match sidecar")
        return context

    def residuals(self, context: ResidualContextV1) -> ResidualLogitsV1:
        context = self._validate_context(context)
        parameter = next(self.parameters())
        if context.context_id not in self.known_context_ids:
            return self._zero(count=len(context.action_keys), device=parameter.device, dtype=parameter.dtype, stop=context.stop_available)
        known = torch.tensor(
            [key in self.known_action_keys for key in context.action_keys],
            dtype=torch.bool, device=parameter.device,
        )
        state = torch.tensor(context.state_features, device=parameter.device, dtype=parameter.dtype)
        if context.action_features:
            action = torch.tensor(context.action_features, device=parameter.device, dtype=parameter.dtype)
            repeated_state = state.expand(len(context.action_features), -1)
            values = self.max_abs_residual * torch.tanh(self.output(self.input_projection(torch.cat([repeated_state, action], dim=-1))).squeeze(-1))
            semantic = torch.where(known, values, torch.zeros_like(values))
        else:
            semantic = torch.zeros((0,), device=parameter.device, dtype=parameter.dtype)
        stop_value: Tensor | None = None
        if context.stop_available:
            stop_key_known = STOP_ACTION_KEY_V1 in self.known_action_keys
            stop_action = torch.tensor(context.stop_features, device=parameter.device, dtype=parameter.dtype)
            stop_input = torch.cat([state, stop_action], dim=0).view(1, -1)
            stop_value = self.max_abs_residual * torch.tanh(self.output(self.input_projection(stop_input)).squeeze())
            if not stop_key_known:
                stop_value = torch.zeros((), device=parameter.device, dtype=parameter.dtype)
        return ResidualLogitsV1(semantic=semantic, stop=stop_value)

    def adjust_logits(
        self,
        base_semantic_logits: Tensor,
        base_stop_logit: Tensor | None,
        context: object,
        *,
        action_types: Sequence[int] | None = None,
    ) -> ResidualLogitsV1:
        """Add residuals to a detached base, failing closed on malformed context."""
        if type(base_semantic_logits) is not Tensor or base_semantic_logits.ndim != 1 or not torch.isfinite(base_semantic_logits).all():
            raise FrozenResidualError("base semantic logits must be a finite rank-1 tensor")
        if base_stop_logit is not None and (type(base_stop_logit) is not Tensor or base_stop_logit.ndim != 0 or not torch.isfinite(base_stop_logit).all()):
            raise FrozenResidualError("base STOP logit must be a finite scalar tensor")
        action_type_labels = self._action_types(action_types, int(base_semantic_logits.numel()))
        try:
            checked = self._validate_context(context)
        except (FrozenResidualError, TypeError, ValueError):
            self._record_coverage(
                valid_context=False, exact_known_context=False,
                eligible_action_slots=0, known_action_slots=0,
                residual_applied_slots=0, nonzero_residual_slots=0,
                top1_changed=False, ood_pass_through=True,
                pass_through_reason="malformed_context",
                action_types=action_type_labels, known_action_types=(),
                nonzero_action_types=(), stop_available=base_stop_logit is not None,
                stop_known=False, stop_nonzero=False, residual_magnitudes=(),
            )
            return ResidualLogitsV1(base_semantic_logits.detach(), None if base_stop_logit is None else base_stop_logit.detach())
        if checked.stop_available != (base_stop_logit is not None) or len(checked.action_keys) != base_semantic_logits.numel():
            self._record_coverage(
                valid_context=False, exact_known_context=False,
                eligible_action_slots=0, known_action_slots=0,
                residual_applied_slots=0, nonzero_residual_slots=0,
                top1_changed=False, ood_pass_through=True,
                pass_through_reason="arity_or_stop_mismatch",
                action_types=action_type_labels, known_action_types=(),
                nonzero_action_types=(), stop_available=base_stop_logit is not None,
                stop_known=False, stop_nonzero=False, residual_magnitudes=(),
            )
            return ResidualLogitsV1(base_semantic_logits.detach(), None if base_stop_logit is None else base_stop_logit.detach())
        exact_known_context = checked.context_id in self.known_context_ids
        semantic_known = tuple(key in self.known_action_keys for key in checked.action_keys)
        stop_known = bool(checked.stop_available and STOP_ACTION_KEY_V1 in self.known_action_keys)
        residual = self.residuals(checked)
        semantic_residual = residual.semantic.to(device=base_semantic_logits.device, dtype=base_semantic_logits.dtype)
        stop_residual = None if residual.stop is None else residual.stop.to(device=base_semantic_logits.device, dtype=base_semantic_logits.dtype)
        detached_semantic = base_semantic_logits.detach()
        detached_stop = None if base_stop_logit is None else base_stop_logit.detach()
        adjusted_semantic = detached_semantic + semantic_residual
        adjusted_stop = None if detached_stop is None else detached_stop + stop_residual
        semantic_nonzero = tuple(bool(abs(float(value)) > 1.0e-12) for value in semantic_residual.detach().cpu().tolist())
        nonzero_action_types = tuple(
            label for label, known, nonzero in zip(action_type_labels, semantic_known, semantic_nonzero)
            if known and nonzero
        )
        known_action_types = tuple(label for label, known in zip(action_type_labels, semantic_known) if exact_known_context and known)
        residual_magnitudes = tuple(
            abs(float(value)) for value, known in zip(semantic_residual.detach().cpu().tolist(), semantic_known)
            if exact_known_context and known
        )
        if adjusted_stop is not None and exact_known_context and stop_known and residual.stop is not None:
            residual_magnitudes += (abs(float(residual.stop.detach().cpu().item())),)
        base_values = [float(value) for value in detached_semantic.detach().cpu().tolist()]
        adjusted_values = [float(value) for value in adjusted_semantic.detach().cpu().tolist()]
        if detached_stop is not None:
            base_values.append(float(detached_stop.detach().cpu().item()))
            adjusted_values.append(float(adjusted_stop.detach().cpu().item()))
        self._record_coverage(
            valid_context=True, exact_known_context=exact_known_context,
            eligible_action_slots=len(checked.action_keys) + int(checked.stop_available),
            known_action_slots=(sum(semantic_known) if exact_known_context else 0) + int(exact_known_context and stop_known),
            residual_applied_slots=(sum(semantic_known) if exact_known_context else 0) + int(exact_known_context and stop_known),
            nonzero_residual_slots=(sum(semantic_nonzero[index] and semantic_known[index] for index in range(len(semantic_known))) if exact_known_context else 0)
            + int(exact_known_context and stop_known and residual.stop is not None and abs(float(residual.stop.detach().cpu().item())) > 1.0e-12),
            top1_changed=bool(base_values) and self._top1(base_values) != self._top1(adjusted_values),
            ood_pass_through=not exact_known_context,
            pass_through_reason=None if exact_known_context else "unknown_context",
            action_types=action_type_labels,
            known_action_types=known_action_types,
            nonzero_action_types=nonzero_action_types,
            stop_available=checked.stop_available,
            stop_known=stop_known if exact_known_context else False,
            stop_nonzero=bool(exact_known_context and stop_known and residual.stop is not None and abs(float(residual.stop.detach().cpu().item())) > 1.0e-12),
            residual_magnitudes=residual_magnitudes,
        )
        return ResidualLogitsV1(semantic=adjusted_semantic, stop=adjusted_stop)

    def adjust_step(
        self,
        base: SpecialistStepLogitsV1,
        context: object,
        *,
        action_types: Sequence[int] | None = None,
    ) -> SpecialistStepLogitsV1:
        if type(base) is not SpecialistStepLogitsV1:
            raise FrozenResidualError("base policy result must be SpecialistStepLogitsV1")
        semantic = torch.tensor(base.semantic_logits, dtype=next(self.parameters()).dtype, device=next(self.parameters()).device)
        stop = None if base.stop_logit is None else torch.tensor(base.stop_logit, dtype=semantic.dtype, device=semantic.device)
        adjusted = self.adjust_logits(semantic, stop, context, action_types=action_types)
        return SpecialistStepLogitsV1(
            semantic_logits=tuple(float(value) for value in adjusted.semantic.detach().cpu().tolist()),
            stop_logit=None if adjusted.stop is None else float(adjusted.stop.detach().cpu().item()),
        )

    def descriptor(self) -> Mapping[str, object]:
        """Return the manifest fields required to bind a sidecar to Wave6."""
        return MappingProxyType({
            "schema_version": FROZEN_RESIDUAL_SCHEMA_V1,
            "state_feature_dim": self.state_feature_dim,
            "action_feature_dim": self.action_feature_dim,
            "hidden_dim": self.hidden_dim,
            "max_abs_residual": self.max_abs_residual,
            "known_context_count": len(self.known_context_ids),
            "known_action_count": len(self.known_action_keys),
            "base_checkpoint_file_sha256": self.base_checkpoint_file_sha256,
            "base_checkpoint_tensor_sha256": self.base_checkpoint_tensor_sha256,
            "base_frozen": True,
            "semantic_decoder_owner": "cabt-runtime",
            "gru_state_owner": "wave6-base-policy",
        })


def frozen_residual_loss_v1(
    base_logits: Tensor,
    residual_logits: Tensor,
    target_index: Tensor,
    *,
    anchor_kl_weight: float = 1.0,
    residual_l2_weight: float = 1.0e-4,
) -> ResidualLossBreakdownV1:
    """Compute target loss plus a frozen-base KL anchor and residual L2."""
    if type(base_logits) is not Tensor or type(residual_logits) is not Tensor or type(target_index) is not Tensor:
        raise FrozenResidualError("residual loss requires tensors")
    if base_logits.ndim != 2 or residual_logits.shape != base_logits.shape or target_index.ndim != 1 or target_index.shape[0] != base_logits.shape[0]:
        raise FrozenResidualError("residual loss tensor shapes are invalid")
    if not torch.isfinite(base_logits).all() or not torch.isfinite(residual_logits).all():
        raise FrozenResidualError("residual loss logits must be finite")
    if target_index.dtype not in (torch.int64, torch.int32) or (target_index < 0).any() or (target_index >= base_logits.shape[1]).any():
        raise FrozenResidualError("residual loss targets are outside the legal domain")
    anchor_weight = _require_finite_float(anchor_kl_weight, field="anchor_kl_weight")
    l2_weight = _require_finite_float(residual_l2_weight, field="residual_l2_weight")
    if anchor_weight < 0.0 or l2_weight < 0.0:
        raise FrozenResidualError("residual loss weights must be nonnegative")
    frozen_base = base_logits.detach()
    adjusted = frozen_base + residual_logits
    imitation = F.cross_entropy(adjusted, target_index)
    anchor = F.kl_div(
        F.log_softmax(adjusted, dim=-1),
        F.softmax(frozen_base, dim=-1),
        reduction="batchmean",
    )
    residual_l2 = residual_logits.square().mean()
    total = imitation + anchor_weight * anchor + l2_weight * residual_l2
    return ResidualLossBreakdownV1(imitation, anchor, residual_l2, total)


def frozen_residual_signed_behavior_loss_v1(
    base_logits: Tensor,
    residual_logits: Tensor,
    target_index: Tensor,
    signed_weight: Tensor,
    *,
    anchor_kl_weight: float = 1.0,
    residual_l2_weight: float = 1.0e-4,
) -> ResidualLossBreakdownV1:
    """Compute a signed behavior-log-probability residual objective.

    This is deliberately separate from :func:`frozen_residual_loss_v1`.
    ``signed_weight`` is a sealed cross-fitted outcome target in ``[-1, 1]``;
    positive values reinforce the selected behavior and negative values
    reverse its gradient.  The frozen base logits are detached before both
    the signed log-probability and anchor KL are evaluated.
    """
    if type(base_logits) is not Tensor or type(residual_logits) is not Tensor or type(target_index) is not Tensor or type(signed_weight) is not Tensor:
        raise FrozenResidualError("signed residual loss requires tensors")
    if (
        base_logits.ndim != 2
        or residual_logits.shape != base_logits.shape
        or target_index.ndim != 1
        or target_index.shape[0] != base_logits.shape[0]
        or signed_weight.ndim != 1
        or signed_weight.shape[0] != base_logits.shape[0]
    ):
        raise FrozenResidualError("signed residual loss tensor shapes are invalid")
    if not torch.is_floating_point(signed_weight) or not torch.isfinite(signed_weight).all():
        raise FrozenResidualError("signed_weight must be a finite floating tensor")
    if (signed_weight < -1.0).any() or (signed_weight > 1.0).any():
        raise FrozenResidualError("signed_weight must be in [-1, 1]")
    if not torch.isfinite(base_logits).all() or not torch.isfinite(residual_logits).all():
        raise FrozenResidualError("signed residual loss logits must be finite")
    if target_index.dtype not in (torch.int64, torch.int32) or (target_index < 0).any() or (target_index >= base_logits.shape[1]).any():
        raise FrozenResidualError("signed residual loss targets are outside the legal domain")
    anchor_weight = _require_finite_float(anchor_kl_weight, field="anchor_kl_weight")
    l2_weight = _require_finite_float(residual_l2_weight, field="residual_l2_weight")
    if anchor_weight < 0.0 or l2_weight < 0.0:
        raise FrozenResidualError("signed residual loss weights must be nonnegative")
    frozen_base = base_logits.detach()
    adjusted = frozen_base + residual_logits
    selected_log_probability = F.log_softmax(adjusted, dim=-1).gather(
        1, target_index.reshape(-1, 1),
    ).reshape(-1)
    weights = signed_weight.to(device=adjusted.device, dtype=adjusted.dtype)
    imitation = -(weights * selected_log_probability).mean()
    anchor = F.kl_div(
        F.log_softmax(adjusted, dim=-1),
        F.softmax(frozen_base, dim=-1),
        reduction="batchmean",
    )
    residual_l2 = residual_logits.square().mean()
    total = imitation + anchor_weight * anchor + l2_weight * residual_l2
    return ResidualLossBreakdownV1(imitation, anchor, residual_l2, total)


class FrozenResidualPolicyV1:
    """Research wrapper preserving the base policy's decoder and GRU commit."""

    def __init__(self, base_policy: object, sidecar: FrozenResidualSidecarV1) -> None:
        if not callable(getattr(base_policy, "reset", None)) or not callable(getattr(base_policy, "begin_decision", None)):
            raise FrozenResidualError("base policy must expose reset() and begin_decision()")
        if type(sidecar) is not FrozenResidualSidecarV1:
            raise FrozenResidualError("sidecar must be exact FrozenResidualSidecarV1")
        self._base_policy = base_policy
        self._sidecar = sidecar

    @property
    def sidecar(self) -> FrozenResidualSidecarV1:
        return self._sidecar

    def reset(self) -> None:
        self._base_policy.reset()

    def policy_telemetry(self) -> PolicyTelemetrySnapshot:
        telemetry = self._base_policy.policy_telemetry()
        if not isinstance(telemetry, PolicyTelemetrySnapshot):
            raise FrozenResidualError("base policy telemetry is not canonical")
        return telemetry

    def begin_decision(self) -> "FrozenResidualDecisionSessionV1":
        base_session = self._base_policy.begin_decision()
        if not isinstance(base_session, SpecialistDecisionSessionV2):
            raise FrozenResidualError("base policy returned an invalid decision session")
        return FrozenResidualDecisionSessionV1(base_session, self._sidecar)


class FrozenResidualDecisionSessionV1:
    def __init__(self, base_session: SpecialistDecisionSessionV2, sidecar: FrozenResidualSidecarV1) -> None:
        self._base_session = base_session
        self._sidecar = sidecar
        self._finished = False

    @property
    def next_recurrent_state_token(self) -> object:
        return getattr(self._base_session, "next_recurrent_state_token", None)

    def logits(self, model_input: SpecialistModelInputV1, step_input: SpecialistStepInputV1) -> SpecialistStepLogitsV1:
        if self._finished:
            raise FrozenResidualError("residual decision session is already finished")
        base = self._base_session.logits(model_input, step_input)
        if type(base) is not SpecialistStepLogitsV1:
            raise FrozenResidualError("base policy returned a non-canonical logits object")
        try:
            context = build_residual_context_v1(model_input, step_input)
        except (FrozenResidualError, TypeError, ValueError):
            context = None
        return self._sidecar.adjust_step(
            base,
            context,
            action_types=tuple(item.semantic_row.option_type for item in step_input.allowed_semantic_classes),
        )

    def commit(self, outcome: CommittedSemanticDecisionV2) -> None:
        if self._finished:
            raise FrozenResidualError("residual decision session is already finished")
        if type(outcome) is not CommittedSemanticDecisionV2:
            raise FrozenResidualError("residual commit requires canonical semantic outcome")
        self._base_session.commit(outcome)
        self._finished = True

    def abort(self) -> None:
        if self._finished:
            return
        self._base_session.abort()
        self._finished = True


__all__ = [
    "FROZEN_RESIDUAL_SCHEMA_V1",
    "STOP_ACTION_KEY_V1",
    "FrozenResidualError",
    "ResidualContextV1",
    "ResidualLogitsV1",
    "ResidualLossBreakdownV1",
    "ResidualCoverageSnapshotV1",
    "FrozenResidualSidecarV1",
    "FrozenResidualPolicyV1",
    "FrozenResidualDecisionSessionV1",
    "build_residual_context_v1",
    "frozen_residual_loss_v1",
    "frozen_residual_signed_behavior_loss_v1",
]
