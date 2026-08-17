"""Research-only coarse public-bucket residual gate.

This module is an adapter contract for the next frozen-residual pilot.  It is
intentionally not imported by the production V4 actor, the CABT runner, or the
existing exact-context sidecar.  A sealed public bucket reference is loaded
from the multi-source bundle produced by
``build_public_confidence_reference_bundle.py``.  A residual is considered
eligible only when all three conditions hold:

* the actor-visible state maps to a bucket present in that reference;
* the semantic action class (or the legal STOP token) is valid and has a
  pre-registered residual entry;
* the residual is finite and bounded by the adapter contract.

Unknown/malformed public inputs fail closed to detached base logits.  The
adapter has no training, promotion, long-run, or performance authority.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import hashlib
import json
import math
from pathlib import Path
from threading import RLock
from types import MappingProxyType

import torch
from torch import Tensor

from mage_ptcg.meta_specialist.actor_visible_features_v1 import (
    SemanticActionV1,
    SpecialistModelInputV1,
    SpecialistStepInputV1,
    SpecialistStepLogitsV1,
)
from mage_ptcg.meta_specialist.public_confidence_ood_v1 import (
    PUBLIC_CONFIDENCE_OOD_SCHEMA_V1,
    PublicBucketReferenceV1,
    score_public_step_v1,
)


COARSE_PUBLIC_RESIDUAL_GATE_SCHEMA_V1 = "specialist-coarse-public-residual-gate-v1"
REFERENCE_BUNDLE_SCHEMA_V1 = "meta-specialist-public-bucket-reference-bundle-v1"
COARSE_COVERAGE_SCHEMA_V1 = "specialist-coarse-public-residual-coverage-v1"
_ACTION_PREFIX = b"mage_ptcg:specialist-frozen-wave6-residual:action:v1\0"
_HEX64 = frozenset("0123456789abcdef")


class CoarsePublicResidualGateError(ValueError):
    """Raised when the research-only coarse gate is not closed."""


def _sha(value: object, *, field: str) -> str:
    if type(value) is not str or len(value) != 64 or any(char not in _HEX64 for char in value):
        raise CoarsePublicResidualGateError(f"{field} must be a lowercase SHA-256")
    return value


def _file_sha(path: Path) -> str:
    if not path.is_file() or path.is_symlink():
        raise CoarsePublicResidualGateError(f"reference bundle is not a regular file: {path}")
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise CoarsePublicResidualGateError("reference bundle cannot be read") from exc
    return digest.hexdigest()


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CoarsePublicResidualGateError("reference bundle is not canonical JSON") from exc


def _closed_mapping(value: object, fields: set[str], *, field: str) -> dict[str, object]:
    if type(value) is not dict:
        raise CoarsePublicResidualGateError(f"{field} must be a JSON object")
    actual = set(value)
    if actual != fields:
        missing = sorted(fields - actual)
        unknown = sorted(actual - fields)
        raise CoarsePublicResidualGateError(f"{field} has an open schema (missing={missing}, unknown={unknown})")
    return value


def _nonnegative_int(value: object, *, field: str) -> int:
    if type(value) is not int or value < 0:
        raise CoarsePublicResidualGateError(f"{field} must be a nonnegative int")
    return value


def semantic_action_key_v1(action: SemanticActionV1) -> str:
    """Return the stable semantic key shared with the exact residual sidecar."""
    if type(action) is not SemanticActionV1:
        raise CoarsePublicResidualGateError("semantic action key requires exact SemanticActionV1")
    SemanticActionV1.__post_init__(action)
    return hashlib.sha256(_ACTION_PREFIX + action.canonical_bytes).hexdigest()


@dataclass(frozen=True, slots=True)
class CoarsePublicReferenceBundleV1:
    """Hash-bound public bucket reference metadata and histogram."""

    bundle_file_sha256: str
    source_list_sha256: str
    partition: str
    rare_count_threshold: int
    source_count: int
    bucket_schema_version: str
    bucket_counts: Mapping[str, int]

    def __post_init__(self) -> None:
        _sha(self.bundle_file_sha256, field="bundle_file_sha256")
        _sha(self.source_list_sha256, field="source_list_sha256")
        if self.partition != "train":
            raise CoarsePublicResidualGateError("coarse residual reference must use the train partition")
        _nonnegative_int(self.rare_count_threshold, field="rare_count_threshold")
        if type(self.source_count) is not int or self.source_count < 2:
            raise CoarsePublicResidualGateError("coarse residual reference requires at least two sources")
        if self.bucket_schema_version != PUBLIC_CONFIDENCE_OOD_SCHEMA_V1:
            raise CoarsePublicResidualGateError("reference bucket schema does not match public OOD v1")
        if not isinstance(self.bucket_counts, Mapping) or not self.bucket_counts:
            raise CoarsePublicResidualGateError("reference bucket_counts must be nonempty")
        checked: dict[str, int] = {}
        for bucket, count in self.bucket_counts.items():
            _sha(bucket, field="bucket_counts key")
            if type(count) is not int or count <= 0:
                raise CoarsePublicResidualGateError("reference bucket counts must be positive ints")
            checked[bucket] = count
        object.__setattr__(self, "bucket_counts", MappingProxyType(checked))

    @property
    def reference(self) -> PublicBucketReferenceV1:
        return PublicBucketReferenceV1(
            source_sha256=self.source_list_sha256,
            bucket_counts=self.bucket_counts,
            rare_count_threshold=self.rare_count_threshold,
        )

    def descriptor(self) -> Mapping[str, object]:
        return MappingProxyType({
            "schema_version": COARSE_PUBLIC_RESIDUAL_GATE_SCHEMA_V1,
            "reference_bundle_file_sha256": self.bundle_file_sha256,
            "reference_source_list_sha256": self.source_list_sha256,
            "partition": self.partition,
            "bucket_schema_version": self.bucket_schema_version,
            "source_count": self.source_count,
            "known_bucket_count": len(self.bucket_counts),
            "training_permitted": False,
            "promotion_authority": False,
            "longrun_allowed": False,
            "performance_evidence": False,
        })


_BUNDLE_FIELDS = {
    "schema_version", "bucket_schema_version", "partition", "rare_count_threshold",
    "source_count", "source_list", "source_list_sha256", "source_stats",
    "transition_count", "prefix_count", "forced_prefix_count", "skipped_transition_count",
    "bucket_count", "bucket_counts", "privacy", "promotion_authority",
}


def load_coarse_public_reference_bundle_v1(
    path: str | Path,
    *,
    expected_file_sha256: str,
) -> CoarsePublicReferenceBundleV1:
    """Strict-load a train-only public bucket bundle and verify its file SHA."""
    bundle_path = Path(path)
    expected = _sha(expected_file_sha256, field="expected bundle SHA-256")
    actual = _file_sha(bundle_path)
    if actual != expected:
        raise CoarsePublicResidualGateError("reference bundle file SHA-256 mismatch")
    try:
        payload = json.loads(bundle_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CoarsePublicResidualGateError("reference bundle cannot be read as JSON") from exc
    body = _closed_mapping(payload, _BUNDLE_FIELDS, field="reference bundle")
    if body["schema_version"] != REFERENCE_BUNDLE_SCHEMA_V1:
        raise CoarsePublicResidualGateError("reference bundle schema is invalid")
    if body["bucket_schema_version"] != PUBLIC_CONFIDENCE_OOD_SCHEMA_V1:
        raise CoarsePublicResidualGateError("reference bucket schema is invalid")
    if body["partition"] != "train":
        raise CoarsePublicResidualGateError("reference bundle must be train partition")
    source_count = _nonnegative_int(body["source_count"], field="source_count")
    if source_count < 2:
        raise CoarsePublicResidualGateError("reference bundle requires at least two sources")
    source_list = body["source_list"]
    if type(source_list) is not list or len(source_list) != source_count:
        raise CoarsePublicResidualGateError("reference source_list count is inconsistent")
    normalized_sources: list[dict[str, object]] = []
    for ordinal, item in enumerate(source_list):
        source = _closed_mapping(item, {"ordinal", "source_sha256"}, field="reference source_list entry")
        if source["ordinal"] != ordinal:
            raise CoarsePublicResidualGateError("reference source_list ordinals are not contiguous")
        normalized_sources.append({"ordinal": ordinal, "source_sha256": _sha(source["source_sha256"], field="source SHA-256")})
    source_hashes = [str(item["source_sha256"]) for item in normalized_sources]
    if len(set(source_hashes)) != len(source_hashes):
        raise CoarsePublicResidualGateError("reference source_list contains duplicate sources")
    source_list_sha = _sha(body["source_list_sha256"], field="source_list_sha256")
    expected_source_list_sha = hashlib.sha256(_canonical_json({"partition": "train", "source_list": normalized_sources})).hexdigest()
    if source_list_sha != expected_source_list_sha:
        raise CoarsePublicResidualGateError("reference source_list SHA-256 mismatch")
    bucket_counts = body["bucket_counts"]
    if type(bucket_counts) is not dict or body["bucket_count"] != len(bucket_counts):
        raise CoarsePublicResidualGateError("reference bucket count is inconsistent")
    checked_counts: dict[str, int] = {}
    for bucket, count in bucket_counts.items():
        _sha(bucket, field="bucket id")
        if type(count) is not int or count <= 0:
            raise CoarsePublicResidualGateError("reference bucket count must be a positive int")
        checked_counts[bucket] = count
    privacy = _closed_mapping(body["privacy"], {"uses_opponent_id", "uses_seat", "uses_policy_identity", "uses_hidden_fields"}, field="reference privacy")
    if any(value is not False for value in privacy.values()) or body["promotion_authority"] is not False:
        raise CoarsePublicResidualGateError("reference bundle grants forbidden authority or contains private fields")
    return CoarsePublicReferenceBundleV1(
        bundle_file_sha256=actual,
        source_list_sha256=source_list_sha,
        partition="train",
        rare_count_threshold=_nonnegative_int(body["rare_count_threshold"], field="rare_count_threshold"),
        source_count=source_count,
        bucket_schema_version=str(body["bucket_schema_version"]),
        bucket_counts=checked_counts,
    )


@dataclass(frozen=True, slots=True)
class CoarseResidualCoverageSnapshotV1:
    """Immutable counters for one coarse-gated evaluation stream."""

    total_decisions: int = 0
    valid_inputs: int = 0
    known_bucket_decisions: int = 0
    valid_action_slots: int = 0
    residual_applied_slots: int = 0
    nonzero_residual_slots: int = 0
    top1_change_decisions: int = 0
    ood_pass_through: int = 0
    stop_slots: int = 0
    known_stop_slots: int = 0
    pass_through_reasons: Mapping[str, int] = field(default_factory=dict)
    bucket_counts: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in (
            "total_decisions", "valid_inputs", "known_bucket_decisions", "valid_action_slots",
            "residual_applied_slots", "nonzero_residual_slots", "top1_change_decisions",
            "ood_pass_through", "stop_slots", "known_stop_slots",
        ):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise CoarsePublicResidualGateError(f"coverage {name} must be a nonnegative int")
        for name in ("pass_through_reasons", "bucket_counts"):
            value = getattr(self, name)
            if not isinstance(value, Mapping):
                raise CoarsePublicResidualGateError(f"coverage {name} must be a mapping")
            checked: dict[str, int] = {}
            for key, count in value.items():
                if type(key) is not str or not key or type(count) is not int or count < 0:
                    raise CoarsePublicResidualGateError(f"coverage {name} contains an invalid entry")
                checked[key] = count
            object.__setattr__(self, name, MappingProxyType(checked))

    @staticmethod
    def _rate(numerator: int, denominator: int) -> float | None:
        return numerator / denominator if denominator else None

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": COARSE_COVERAGE_SCHEMA_V1,
            "total_decisions": self.total_decisions,
            "valid_inputs": self.valid_inputs,
            "known_bucket_decisions": self.known_bucket_decisions,
            "known_bucket_rate": self._rate(self.known_bucket_decisions, self.total_decisions),
            "valid_action_slots": self.valid_action_slots,
            "residual_applied_slots": self.residual_applied_slots,
            "residual_applied_rate": self._rate(self.residual_applied_slots, self.valid_action_slots),
            "nonzero_residual_slots": self.nonzero_residual_slots,
            "nonzero_residual_rate": self._rate(self.nonzero_residual_slots, self.residual_applied_slots),
            "top1_change_decisions": self.top1_change_decisions,
            "top1_change_rate": self._rate(self.top1_change_decisions, self.total_decisions),
            "ood_pass_through": self.ood_pass_through,
            "ood_pass_through_rate": self._rate(self.ood_pass_through, self.total_decisions),
            "stop_slots": self.stop_slots,
            "known_stop_slots": self.known_stop_slots,
            "pass_through_reasons": dict(sorted(self.pass_through_reasons.items())),
            "bucket_counts": dict(sorted(self.bucket_counts.items())),
        }

    def delta(self, previous: "CoarseResidualCoverageSnapshotV1") -> "CoarseResidualCoverageSnapshotV1":
        if type(previous) is not CoarseResidualCoverageSnapshotV1:
            raise CoarsePublicResidualGateError("coverage delta requires exact snapshot")
        scalar_fields = (
            "total_decisions", "valid_inputs", "known_bucket_decisions", "valid_action_slots",
            "residual_applied_slots", "nonzero_residual_slots", "top1_change_decisions",
            "ood_pass_through", "stop_slots", "known_stop_slots",
        )
        values = {name: getattr(self, name) - getattr(previous, name) for name in scalar_fields}
        if any(value < 0 for value in values.values()):
            raise CoarsePublicResidualGateError("coverage snapshots are not monotone")
        def mapping_delta(current: Mapping[str, int], old: Mapping[str, int]) -> dict[str, int]:
            keys = set(current) | set(old)
            result = {key: int(current.get(key, 0)) - int(old.get(key, 0)) for key in keys}
            if any(value < 0 for value in result.values()):
                raise CoarsePublicResidualGateError("coverage mapping is not monotone")
            return {key: value for key, value in result.items() if value}
        return CoarseResidualCoverageSnapshotV1(
            **values,
            pass_through_reasons=mapping_delta(self.pass_through_reasons, previous.pass_through_reasons),
            bucket_counts=mapping_delta(self.bucket_counts, previous.bucket_counts),
        )


class CoarsePublicResidualGateV1:
    """Apply a static, bounded residual table behind a coarse public bucket."""

    def __init__(
        self,
        reference_bundle: CoarsePublicReferenceBundleV1,
        *,
        residual_by_bucket_action: Mapping[str, Mapping[str, float]] | None = None,
        stop_residual_by_bucket: Mapping[str, float] | None = None,
        max_abs_residual: float = 0.25,
    ) -> None:
        if type(reference_bundle) is not CoarsePublicReferenceBundleV1:
            raise CoarsePublicResidualGateError("reference_bundle must be exact CoarsePublicReferenceBundleV1")
        if type(max_abs_residual) not in (int, float) or type(max_abs_residual) is bool or not math.isfinite(float(max_abs_residual)) or not 0.0 < float(max_abs_residual) <= 1.0:
            raise CoarsePublicResidualGateError("max_abs_residual must be finite and in (0, 1]")
        self.reference_bundle = reference_bundle
        self.max_abs_residual = float(max_abs_residual)
        self._residuals = self._validate_residual_table(residual_by_bucket_action or {})
        self._stop_residuals = self._validate_stop_table(stop_residual_by_bucket or {})
        self._lock = RLock()
        self._counts = {
            "total_decisions": 0, "valid_inputs": 0, "known_bucket_decisions": 0,
            "valid_action_slots": 0, "residual_applied_slots": 0,
            "nonzero_residual_slots": 0, "top1_change_decisions": 0,
            "ood_pass_through": 0, "stop_slots": 0, "known_stop_slots": 0,
        }
        self._pass_reasons: dict[str, int] = {}
        self._bucket_counts: dict[str, int] = {}

    def _validate_residual_table(self, value: Mapping[str, Mapping[str, float]]) -> Mapping[str, Mapping[str, float]]:
        if not isinstance(value, Mapping):
            raise CoarsePublicResidualGateError("residual_by_bucket_action must be a mapping")
        checked: dict[str, Mapping[str, float]] = {}
        for bucket, actions in value.items():
            _sha(bucket, field="residual bucket id")
            if bucket not in self.reference_bundle.bucket_counts:
                raise CoarsePublicResidualGateError("residual table contains an unknown bucket")
            if not isinstance(actions, Mapping):
                raise CoarsePublicResidualGateError("residual bucket action table must be a mapping")
            rows: dict[str, float] = {}
            for action_key, residual in actions.items():
                _sha(action_key, field="residual action key")
                if type(residual) not in (int, float) or type(residual) is bool or not math.isfinite(float(residual)) or abs(float(residual)) > self.max_abs_residual:
                    raise CoarsePublicResidualGateError("residual must be finite and within max_abs_residual")
                rows[action_key] = float(residual)
            checked[bucket] = MappingProxyType(rows)
        return MappingProxyType(checked)

    def _validate_stop_table(self, value: Mapping[str, float]) -> Mapping[str, float]:
        if not isinstance(value, Mapping):
            raise CoarsePublicResidualGateError("stop_residual_by_bucket must be a mapping")
        checked: dict[str, float] = {}
        for bucket, residual in value.items():
            _sha(bucket, field="STOP residual bucket id")
            if bucket not in self.reference_bundle.bucket_counts:
                raise CoarsePublicResidualGateError("STOP residual table contains an unknown bucket")
            if type(residual) not in (int, float) or type(residual) is bool or not math.isfinite(float(residual)) or abs(float(residual)) > self.max_abs_residual:
                raise CoarsePublicResidualGateError("STOP residual must be finite and within max_abs_residual")
            checked[bucket] = float(residual)
        return MappingProxyType(checked)

    @staticmethod
    def _top1(values: list[float]) -> int | None:
        return None if not values else max(range(len(values)), key=lambda index: values[index])

    def _record(
        self,
        *,
        valid: bool,
        known_bucket: bool,
        valid_action_slots: int,
        applied: int,
        nonzero: int,
        top1_changed: bool,
        ood: bool,
        reason: str | None,
        bucket_id: str | None,
        stop_available: bool,
        stop_known: bool,
    ) -> None:
        with self._lock:
            self._counts["total_decisions"] += 1
            self._counts["valid_inputs"] += int(valid)
            self._counts["known_bucket_decisions"] += int(known_bucket)
            self._counts["valid_action_slots"] += valid_action_slots
            self._counts["residual_applied_slots"] += applied
            self._counts["nonzero_residual_slots"] += nonzero
            self._counts["top1_change_decisions"] += int(top1_changed)
            self._counts["ood_pass_through"] += int(ood)
            self._counts["stop_slots"] += int(stop_available)
            self._counts["known_stop_slots"] += int(stop_known)
            if reason is not None:
                self._pass_reasons[reason] = self._pass_reasons.get(reason, 0) + 1
            if bucket_id is not None:
                self._bucket_counts[bucket_id] = self._bucket_counts.get(bucket_id, 0) + 1

    def coverage_snapshot(self) -> CoarseResidualCoverageSnapshotV1:
        with self._lock:
            return CoarseResidualCoverageSnapshotV1(
                **self._counts,
                pass_through_reasons=dict(self._pass_reasons),
                bucket_counts=dict(self._bucket_counts),
            )

    def reset_coverage(self) -> CoarseResidualCoverageSnapshotV1:
        with self._lock:
            previous = self.coverage_snapshot()
            for key in self._counts:
                self._counts[key] = 0
            self._pass_reasons.clear()
            self._bucket_counts.clear()
            return previous

    def descriptor(self) -> Mapping[str, object]:
        return MappingProxyType({
            "schema_version": COARSE_PUBLIC_RESIDUAL_GATE_SCHEMA_V1,
            "reference": self.reference_bundle.descriptor(),
            "residual_bucket_count": len(self._residuals),
            "stop_residual_bucket_count": len(self._stop_residuals),
            "max_abs_residual": self.max_abs_residual,
            "training_permitted": False,
            "promotion_authority": False,
            "longrun_allowed": False,
            "performance_evidence": False,
        })

    def adjust_logits(
        self,
        model_input: object,
        step_input: object,
        base_semantic_logits: Tensor,
        base_stop_logit: Tensor | None,
    ) -> tuple[Tensor, Tensor | None]:
        """Return adjusted logits, or detached exact base logits on gate failure."""
        if type(base_semantic_logits) is not Tensor or base_semantic_logits.ndim != 1 or not torch.isfinite(base_semantic_logits).all():
            raise CoarsePublicResidualGateError("base semantic logits must be a finite rank-1 tensor")
        if base_stop_logit is not None and (type(base_stop_logit) is not Tensor or base_stop_logit.ndim != 0 or not torch.isfinite(base_stop_logit).all()):
            raise CoarsePublicResidualGateError("base STOP logit must be a finite scalar tensor")
        detached_semantic = base_semantic_logits.detach()
        detached_stop = None if base_stop_logit is None else base_stop_logit.detach()
        action_count = int(base_semantic_logits.numel())
        try:
            if type(model_input) is not SpecialistModelInputV1 or type(step_input) is not SpecialistStepInputV1:
                raise CoarsePublicResidualGateError("malformed_public_input")
            SpecialistModelInputV1.__post_init__(model_input)
            SpecialistStepInputV1.__post_init__(step_input)
            if step_input.stop_available != (base_stop_logit is not None) or len(step_input.allowed_semantic_classes) != action_count:
                raise CoarsePublicResidualGateError("arity_or_stop_mismatch")
            base_result = SpecialistStepLogitsV1(
                semantic_logits=tuple(float(value) for value in detached_semantic.cpu().tolist()),
                stop_logit=None if detached_stop is None else float(detached_stop.cpu().item()),
            )
            score = score_public_step_v1(
                model_input, step_input, base_result,
                reference=self.reference_bundle.reference,
            )
            bucket_id = score.bucket_id
            known_bucket = int(score.reference_count or 0) > 0
            action_keys = tuple(semantic_action_key_v1(item.semantic_row) for item in step_input.allowed_semantic_classes)
            residual_values = [0.0] * action_count
            applied = nonzero = 0
            if known_bucket:
                rows = self._residuals.get(bucket_id, {})
                for index, key in enumerate(action_keys):
                    if key in rows:
                        residual_values[index] = rows[key]
                        applied += 1
                        nonzero += int(abs(rows[key]) > 1.0e-12)
                stop_residual = self._stop_residuals.get(bucket_id) if step_input.stop_available else None
                if stop_residual is not None:
                    applied += 1
                    nonzero += int(abs(stop_residual) > 1.0e-12)
                else:
                    stop_residual = 0.0
            else:
                stop_residual = 0.0
            adjusted_semantic = detached_semantic + torch.tensor(residual_values, dtype=detached_semantic.dtype, device=detached_semantic.device)
            adjusted_stop = None if detached_stop is None else detached_stop + torch.tensor(stop_residual, dtype=detached_stop.dtype, device=detached_stop.device)
            before = [float(value) for value in detached_semantic.cpu().tolist()]
            after = [float(value) for value in adjusted_semantic.cpu().tolist()]
            if detached_stop is not None:
                before.append(float(detached_stop.cpu().item()))
                after.append(float(adjusted_stop.cpu().item()))
            self._record(
                valid=True, known_bucket=known_bucket,
                valid_action_slots=action_count + int(step_input.stop_available),
                applied=applied, nonzero=nonzero,
                top1_changed=self._top1(before) != self._top1(after),
                ood=not known_bucket,
                reason=None if known_bucket else "unknown_public_bucket",
                bucket_id=bucket_id,
                stop_available=step_input.stop_available,
                stop_known=bool(known_bucket and step_input.stop_available and bucket_id in self._stop_residuals),
            )
            return adjusted_semantic, adjusted_stop
        except (CoarsePublicResidualGateError, ValueError, TypeError, RuntimeError) as exc:
            reason = str(exc) if str(exc) in {"malformed_public_input", "arity_or_stop_mismatch"} else "malformed_public_input"
            self._record(
                valid=False, known_bucket=False, valid_action_slots=0, applied=0, nonzero=0,
                top1_changed=False, ood=True, reason=reason, bucket_id=None,
                stop_available=base_stop_logit is not None, stop_known=False,
            )
            return detached_semantic, detached_stop

    def adjust_step(self, model_input: object, step_input: object, base: SpecialistStepLogitsV1) -> SpecialistStepLogitsV1:
        if type(base) is not SpecialistStepLogitsV1:
            raise CoarsePublicResidualGateError("base logits must be exact SpecialistStepLogitsV1")
        semantic = torch.tensor(base.semantic_logits, dtype=torch.float32)
        stop = None if base.stop_logit is None else torch.tensor(base.stop_logit, dtype=torch.float32)
        adjusted_semantic, adjusted_stop = self.adjust_logits(model_input, step_input, semantic, stop)
        return SpecialistStepLogitsV1(
            semantic_logits=tuple(float(value) for value in adjusted_semantic.cpu().tolist()),
            stop_logit=None if adjusted_stop is None else float(adjusted_stop.cpu().item()),
        )


__all__ = [
    "COARSE_PUBLIC_RESIDUAL_GATE_SCHEMA_V1",
    "COARSE_COVERAGE_SCHEMA_V1",
    "CoarsePublicResidualGateError",
    "CoarsePublicReferenceBundleV1",
    "CoarseResidualCoverageSnapshotV1",
    "CoarsePublicResidualGateV1",
    "semantic_action_key_v1",
    "load_coarse_public_reference_bundle_v1",
]
