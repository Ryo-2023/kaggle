"""Public-only confidence and structural OOD scoring for research preflights.

This module deliberately accepts only the actor-visible V1 model/step objects
and the corresponding semantic logits.  Opponent identity, seat, policy
identity, physical aliases, and hidden engine payloads have no API surface.
The result is diagnostic/research metadata; it does not alter the V4 policy.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from mage_ptcg.meta_specialist.actor_visible_features_v1 import (
    SemanticActionV1,
    SpecialistModelInputV1,
    SpecialistStepInputV1,
    SpecialistStepLogitsV1,
)


PUBLIC_CONFIDENCE_OOD_SCHEMA_V1 = "meta-specialist-public-confidence-ood-v1"
_BUCKET_DOMAIN = b"mage-ptcg:public-confidence-ood-bucket:v1\0"
_HEX64 = frozenset("0123456789abcdef")


def _require_sha256(value: object, *, field: str) -> str:
    if type(value) is not str or len(value) != 64 or any(char not in _HEX64 for char in value):
        raise ValueError(f"{field} must be a lowercase SHA-256 hex string")
    return value


def _bin(value: int, *, zero: str = "0") -> str:
    if value < 0:
        raise ValueError("bucket value must be nonnegative")
    if value == 0:
        return zero
    if value == 1:
        return "1"
    if value == 2:
        return "2"
    if value <= 4:
        return "3-4"
    if value <= 8:
        return "5-8"
    return "9+"


def _bucket_id(model_input: SpecialistModelInputV1, step_input: SpecialistStepInputV1, effective_domain: int) -> str:
    state_scalars = model_input.state_scalars
    option_types = tuple(sorted({item.semantic_row.option_type for item in step_input.allowed_semantic_classes}))
    card_mask_count = sum(
        sum(int(value) for value in bag.mask)
        for bag in model_input.card_bags.values()
    )
    payload = {
        "schema": PUBLIC_CONFIDENCE_OOD_SCHEMA_V1,
        "selection_type": state_scalars[4],
        "selection_context": state_scalars[5],
        "effective_domain_bin": _bin(effective_domain),
        "prefix_depth_bin": "0" if not step_input.semantic_prefix else ("1" if len(step_input.semantic_prefix) == 1 else "2+"),
        "stop_available": step_input.stop_available,
        "option_types": option_types,
        "pokemon_entity_bin": _bin(len(model_input.pokemon_entities)),
        "card_bag_mask_bin": _bin(card_mask_count),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(_BUCKET_DOMAIN + encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class PublicBucketReferenceV1:
    """Frozen public bucket histogram used by an OOD decision."""

    source_sha256: str
    bucket_counts: Mapping[str, int]
    rare_count_threshold: int = 2

    def __post_init__(self) -> None:
        _require_sha256(self.source_sha256, field="source_sha256")
        if type(self.rare_count_threshold) is not int or self.rare_count_threshold < 0:
            raise ValueError("rare_count_threshold must be a nonnegative int")
        if not isinstance(self.bucket_counts, Mapping):
            raise ValueError("bucket_counts must be a mapping")
        checked: dict[str, int] = {}
        for key, value in self.bucket_counts.items():
            _require_sha256(key, field="bucket id")
            if type(value) is not int or value < 0:
                raise ValueError("bucket counts must be nonnegative ints")
            checked[key] = value
        object.__setattr__(self, "bucket_counts", MappingProxyType(checked))


@dataclass(frozen=True, slots=True)
class PublicEligibilityPolicyV1:
    """Immutable pre-registered focus rule; no performance result is read."""

    min_normalized_surprisal: float = 0.5
    max_top1_top2_margin: float | None = None
    focus_on_ood: bool = True

    def __post_init__(self) -> None:
        if type(self.min_normalized_surprisal) not in (int, float) or type(self.min_normalized_surprisal) is bool:
            raise ValueError("min_normalized_surprisal must be numeric")
        if not math.isfinite(float(self.min_normalized_surprisal)) or self.min_normalized_surprisal < 0.0:
            raise ValueError("min_normalized_surprisal must be finite and nonnegative")
        if self.max_top1_top2_margin is not None:
            if type(self.max_top1_top2_margin) not in (int, float) or type(self.max_top1_top2_margin) is bool:
                raise ValueError("max_top1_top2_margin must be numeric or None")
            if not math.isfinite(float(self.max_top1_top2_margin)) or self.max_top1_top2_margin < 0.0:
                raise ValueError("max_top1_top2_margin must be finite and nonnegative")
        if type(self.focus_on_ood) is not bool:
            raise ValueError("focus_on_ood must be bool")


@dataclass(frozen=True, slots=True)
class PublicStepScoreV1:
    schema_version: str
    effective_domain: int
    forced: bool
    top1_top2_margin: float | None
    entropy: float
    target_nll: float | None
    normalized_surprisal: float | None
    bucket_id: str
    reference_sha256: str | None
    reference_count: int | None
    ood_unseen: bool | None
    ood_rare: bool | None
    eligible: bool
    reason: str


def supervision_weight_from_public_score_v1(score: PublicStepScoreV1) -> float:
    """Return the explicit loss mask while retaining recurrent context.

    ``0.0`` means context-only: the caller must still run the row through the
    recurrent state, but must exclude it from the supervised loss numerator and
    denominator.  This helper is intentionally separate from the trainer so a
    research overlay cannot silently alter V4 production behavior.
    """

    if type(score) is not PublicStepScoreV1:
        raise ValueError("score must be an exact PublicStepScoreV1")
    if score.forced or not score.eligible:
        return 0.0
    return 1.0


def _target_index(
    step_input: SpecialistStepInputV1,
    *,
    chosen_semantic_action: SemanticActionV1 | None,
    chosen_is_stop: bool,
) -> int | None:
    if chosen_is_stop:
        if chosen_semantic_action is not None or not step_input.stop_available:
            raise ValueError("STOP target does not match step input")
        return len(step_input.allowed_semantic_classes)
    if chosen_semantic_action is None:
        return None
    key = chosen_semantic_action.canonical_bytes
    for index, item in enumerate(step_input.allowed_semantic_classes):
        if item.semantic_row.canonical_bytes == key:
            return index
    raise ValueError("chosen semantic action is outside the public legal domain")


def score_public_step_v1(
    model_input: SpecialistModelInputV1,
    step_input: SpecialistStepInputV1,
    logits: SpecialistStepLogitsV1,
    *,
    chosen_semantic_action: SemanticActionV1 | None = None,
    chosen_is_stop: bool = False,
    reference: PublicBucketReferenceV1 | None = None,
    policy: PublicEligibilityPolicyV1 = PublicEligibilityPolicyV1(),
) -> PublicStepScoreV1:
    """Score one public step without accepting opponent/private metadata."""

    if type(model_input) is not SpecialistModelInputV1:
        raise ValueError("model_input must be an exact SpecialistModelInputV1")
    if type(step_input) is not SpecialistStepInputV1:
        raise ValueError("step_input must be an exact SpecialistStepInputV1")
    if type(logits) is not SpecialistStepLogitsV1:
        raise ValueError("logits must be an exact SpecialistStepLogitsV1")
    if type(chosen_is_stop) is not bool:
        raise ValueError("chosen_is_stop must be bool")
    if type(policy) is not PublicEligibilityPolicyV1:
        raise ValueError("policy must be an exact PublicEligibilityPolicyV1")
    if len(logits.semantic_logits) != len(step_input.allowed_semantic_classes):
        raise ValueError("logit domain does not match public legal domain")
    if step_input.stop_available != (logits.stop_logit is not None):
        raise ValueError("STOP logit availability does not match step input")

    values = [float(value) for value in logits.semantic_logits]
    if logits.stop_logit is not None:
        values.append(float(logits.stop_logit))
    effective_domain = len(values)
    if effective_domain < 1:
        raise ValueError("public step has no effective legal domain")
    largest = max(values)
    exponentials = [math.exp(value - largest) for value in values]
    normalizer = sum(exponentials)
    probabilities = [value / normalizer for value in exponentials]
    ordered = sorted(values, reverse=True)
    margin = ordered[0] - ordered[1] if len(ordered) >= 2 else None
    entropy = -sum(probability * math.log(probability) for probability in probabilities if probability > 0.0)
    target_index = _target_index(
        step_input,
        chosen_semantic_action=chosen_semantic_action,
        chosen_is_stop=chosen_is_stop,
    )
    target_nll = None
    normalized_surprisal = None
    if target_index is not None:
        target_probability = probabilities[target_index]
        target_nll = -math.log(max(target_probability, 1e-300))
        normalized_surprisal = target_nll / math.log(max(2, effective_domain))
    forced = effective_domain == 1
    bucket_id = _bucket_id(model_input, step_input, effective_domain)

    reference_count: int | None = None
    ood_unseen: bool | None = None
    ood_rare: bool | None = None
    if reference is None:
        return PublicStepScoreV1(
            schema_version=PUBLIC_CONFIDENCE_OOD_SCHEMA_V1,
            effective_domain=effective_domain,
            forced=forced,
            top1_top2_margin=margin,
            entropy=entropy,
            target_nll=target_nll,
            normalized_surprisal=normalized_surprisal,
            bucket_id=bucket_id,
            reference_sha256=None,
            reference_count=None,
            ood_unseen=None,
            ood_rare=None,
            eligible=False,
            reason="forced_domain" if forced else "missing_reference",
        )

    reference_count = int(reference.bucket_counts.get(bucket_id, 0))
    ood_unseen = reference_count == 0
    ood_rare = reference_count <= reference.rare_count_threshold
    if forced:
        eligible = False
        reason = "forced_domain"
    else:
        high_surprisal = (
            normalized_surprisal is not None
            and normalized_surprisal >= float(policy.min_normalized_surprisal)
        )
        low_margin = (
            policy.max_top1_top2_margin is None
            or margin is None
            or margin <= float(policy.max_top1_top2_margin)
        )
        focus = (policy.focus_on_ood and (ood_unseen or ood_rare)) or high_surprisal
        eligible = bool(focus and low_margin)
        if not eligible:
            reason = "below_focus_threshold"
        elif ood_unseen:
            reason = "unseen_public_bucket"
        elif ood_rare:
            reason = "rare_public_bucket"
        else:
            reason = "high_normalized_surprisal"
    return PublicStepScoreV1(
        schema_version=PUBLIC_CONFIDENCE_OOD_SCHEMA_V1,
        effective_domain=effective_domain,
        forced=forced,
        top1_top2_margin=margin,
        entropy=entropy,
        target_nll=target_nll,
        normalized_surprisal=normalized_surprisal,
        bucket_id=bucket_id,
        reference_sha256=reference.source_sha256,
        reference_count=reference_count,
        ood_unseen=ood_unseen,
        ood_rare=ood_rare,
        eligible=eligible,
        reason=reason,
    )


__all__ = [
    "PUBLIC_CONFIDENCE_OOD_SCHEMA_V1",
    "PublicBucketReferenceV1",
    "PublicEligibilityPolicyV1",
    "PublicStepScoreV1",
    "score_public_step_v1",
    "supervision_weight_from_public_score_v1",
]
