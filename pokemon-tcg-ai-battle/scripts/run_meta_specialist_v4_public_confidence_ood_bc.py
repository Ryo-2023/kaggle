#!/usr/bin/env python3
"""Research-only public confidence/OOD BC mask orchestration.

This module is intentionally *not* a training runner.  It is the small
contract layer between a sealed, actor-visible transition fixture and the
public confidence/OOD scorer.  Every input row is kept in recurrent order;
ineligible rows receive a zero supervision weight but remain available as
hidden-state context.  The existing V4 model, optimizer, evaluator, and
production policy are not imported or called here.

The policy manifest points at a two-source seed0/seed1 train reference
bundle.  The bundle is diagnostic-only and never a permit to train, promote,
or launch a long-running job.  Callers requesting any training operation fail
closed.  A single-source reference is rejected by the bundle loader.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from types import MappingProxyType

from mage_ptcg.meta_specialist.actor_visible_features_v1 import (
    SemanticActionV1,
    SpecialistModelInputV1,
    SpecialistStepInputV1,
    SpecialistStepLogitsV1,
)
from mage_ptcg.meta_specialist.public_confidence_ood_v1 import (
    PublicBucketReferenceV1,
    PublicEligibilityPolicyV1,
    PublicStepScoreV1,
    score_public_step_v1,
    supervision_weight_from_public_score_v1,
)


POLICY_SCHEMA_V1 = "meta-specialist-public-confidence-ood-policy-v1"
POLICY_STATUS_V1 = "pre_registered_diagnostic_policy_not_yet_connected_to_training"
CONTRACT_SCHEMA_V1 = "meta-specialist-v4-public-confidence-ood-bc-contract-v1"
SEALED_ROW_SCHEMA_V1 = "meta-specialist-v4-public-confidence-ood-sealed-row-v1"
REFERENCE_BUNDLE_SCHEMA_V1 = "meta-specialist-public-bucket-reference-bundle-v1"
_HEX64 = frozenset("0123456789abcdef")


class PublicOodContractError(ValueError):
    """Raised when the diagnostic overlay cannot be proven safe."""


def _sha(value: object, *, field: str) -> str:
    if type(value) is not str or len(value) != 64 or any(char not in _HEX64 for char in value):
        raise PublicOodContractError(f"{field} must be a lowercase SHA-256 hex string")
    return value


def _closed_mapping(value: object, expected: set[str], *, field: str) -> dict[str, object]:
    if type(value) is not dict:
        raise PublicOodContractError(f"{field} must be a JSON object")
    if set(value) != expected:
        missing = sorted(expected - set(value))
        extra = sorted(set(value) - expected)
        raise PublicOodContractError(
            f"{field} has an open/closed-schema mismatch (missing={missing}, unknown={extra})"
        )
    return value


@dataclass(frozen=True, slots=True)
class PublicOodPolicyManifestV1:
    """Validated diagnostic policy; ``training_permitted`` is always false."""

    manifest: Mapping[str, object]
    manifest_sha256: str | None
    reference_artifact: str
    reference_artifact_sha256: str
    reference_source_list_sha256: str
    reference_source_sha256s: tuple[str, ...]
    reference_source_count: int
    partition: str
    rare_count_threshold: int
    focus_on_ood: bool
    min_normalized_surprisal: float
    max_top1_top2_margin: float | None
    promotion_authority: bool = False
    longrun_allowed: bool = False
    training_permitted: bool = False

    @property
    def eligibility_policy(self) -> PublicEligibilityPolicyV1:
        return PublicEligibilityPolicyV1(
            min_normalized_surprisal=self.min_normalized_surprisal,
            max_top1_top2_margin=self.max_top1_top2_margin,
            focus_on_ood=self.focus_on_ood,
        )

    @property
    def reference_source_sha256(self) -> str:
        """Compatibility alias; the identity is the ordered source-list SHA."""

        return self.reference_source_list_sha256


def _read_manifest_input(value: Mapping[str, object] | Path | str) -> tuple[dict[str, object], str | None]:
    if isinstance(value, Mapping):
        # Make a shallow copy before validation so callers cannot mutate the
        # policy while a mask contract is being materialized.
        return dict(value), None
    path = Path(value)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise PublicOodContractError("policy manifest cannot be read") from exc
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PublicOodContractError("policy manifest is not valid UTF-8 JSON") from exc
    if type(parsed) is not dict:
        raise PublicOodContractError("policy manifest must be a JSON object")
    return parsed, hashlib.sha256(raw).hexdigest()


def validate_public_ood_policy_manifest_v1(
    value: Mapping[str, object] | Path | str,
    *,
    expected_sha256: str | None = None,
    training_requested: bool = False,
) -> PublicOodPolicyManifestV1:
    """Validate the frozen diagnostic manifest and reject training requests.

    The function accepts only the frozen two-source diagnostic manifest.  It
    never turns that source into a training permit; ``training_requested=True``
    is rejected before any fixture is scored.
    """

    parsed, actual_sha = _read_manifest_input(value)
    if expected_sha256 is not None:
        expected = _sha(expected_sha256, field="policy manifest SHA-256")
        if actual_sha is None or actual_sha != expected:
            raise PublicOodContractError("policy manifest bytes do not match expected SHA-256")

    top = _closed_mapping(
        parsed,
        {
            "schema_version", "promotion_authority", "source", "bucket_policy",
            "confidence_policy", "loss_mask_semantics", "privacy", "gate", "status",
        },
        field="policy manifest",
    )
    if top["schema_version"] != POLICY_SCHEMA_V1:
        raise PublicOodContractError("policy manifest schema_version is invalid")
    if top["promotion_authority"] is not False:
        raise PublicOodContractError("promotion_authority must be false for diagnostic policy")
    if top["status"] != POLICY_STATUS_V1:
        raise PublicOodContractError("policy manifest status is not diagnostic-only")

    source = _closed_mapping(
        top["source"],
        {
            "reference_artifact", "reference_artifact_sha256", "reference_source_list_sha256",
            "reference_source_sha256s", "reference_source_count", "partition", "bucket_schema",
        },
        field="policy manifest source",
    )
    if type(source["reference_artifact"]) is not str or not source["reference_artifact"]:
        raise PublicOodContractError("reference_artifact is invalid")
    if source["bucket_schema"] != "meta-specialist-public-confidence-ood-v1":
        raise PublicOodContractError("reference bucket schema is invalid")
    if source["partition"] != "train":
        raise PublicOodContractError("reference partition must be train")
    reference_artifact_sha = _sha(source["reference_artifact_sha256"], field="reference_artifact_sha256")
    reference_source_list_sha = _sha(
        source["reference_source_list_sha256"], field="reference_source_list_sha256",
    )
    source_hashes = source["reference_source_sha256s"]
    if type(source_hashes) is not list or len(source_hashes) < 2:
        raise PublicOodContractError(
            "reference source list must contain at least two distinct source hashes"
        )
    checked_source_hashes = tuple(
        _sha(item, field="reference_source_sha256s[]") for item in source_hashes
    )
    if len(set(checked_source_hashes)) != len(checked_source_hashes):
        raise PublicOodContractError("reference source list contains duplicate source hashes")
    if type(source["reference_source_count"]) is not int or source["reference_source_count"] != len(checked_source_hashes):
        raise PublicOodContractError("reference_source_count does not match source hash list")

    bucket = _closed_mapping(
        top["bucket_policy"], {"rare_count_threshold", "focus_on_ood"}, field="policy manifest bucket_policy",
    )
    rare = bucket["rare_count_threshold"]
    if type(rare) is not int or rare < 0:
        raise PublicOodContractError("rare_count_threshold must be a nonnegative int")
    if type(bucket["focus_on_ood"]) is not bool:
        raise PublicOodContractError("focus_on_ood must be bool")

    confidence = _closed_mapping(
        top["confidence_policy"], {"min_normalized_surprisal", "max_top1_top2_margin"},
        field="policy manifest confidence_policy",
    )
    minimum = confidence["min_normalized_surprisal"]
    if type(minimum) not in (int, float) or type(minimum) is bool or not math.isfinite(float(minimum)) or float(minimum) < 0.0:
        raise PublicOodContractError("min_normalized_surprisal must be finite and nonnegative")
    margin = confidence["max_top1_top2_margin"]
    if margin is not None and (
        type(margin) not in (int, float) or type(margin) is bool
        or not math.isfinite(float(margin)) or float(margin) < 0.0
    ):
        raise PublicOodContractError("max_top1_top2_margin must be null or finite and nonnegative")

    loss = _closed_mapping(
        top["loss_mask_semantics"],
        {"forced_domain", "ineligible", "eligible", "context_rows_in_loss_denominator", "context_rows_advance_recurrent_state"},
        field="policy manifest loss_mask_semantics",
    )
    if loss != {
        "forced_domain": "context_only", "ineligible": "context_only", "eligible": "loss_bearing",
        "context_rows_in_loss_denominator": False, "context_rows_advance_recurrent_state": True,
    }:
        raise PublicOodContractError("loss mask semantics are not the frozen context-only contract")

    privacy = _closed_mapping(
        top["privacy"],
        {"runtime_uses_opponent_id", "runtime_uses_seat", "runtime_uses_policy_identity", "runtime_uses_hidden_fields", "training_component_selection_may_stratify_by_opponent"},
        field="policy manifest privacy",
    )
    for key in ("runtime_uses_opponent_id", "runtime_uses_seat", "runtime_uses_policy_identity", "runtime_uses_hidden_fields"):
        if privacy[key] is not False:
            raise PublicOodContractError(f"{key} must be false in the runtime privacy boundary")
    if privacy["training_component_selection_may_stratify_by_opponent"] is not True:
        raise PublicOodContractError("training component stratification flag is invalid")

    gate = _closed_mapping(
        top["gate"],
        {"fixed_six_games_per_seed", "required_faults", "required_seedwise_non_degradation", "required_seatwise_non_degradation", "shadow_b_only_after_fixed_six_pass", "longrun_allowed"},
        field="policy manifest gate",
    )
    if type(gate["fixed_six_games_per_seed"]) is not int or gate["fixed_six_games_per_seed"] < 1:
        raise PublicOodContractError("fixed_six_games_per_seed must be positive")
    if gate["required_faults"] != 0:
        raise PublicOodContractError("required_faults must be zero")
    for key in ("required_seedwise_non_degradation", "required_seatwise_non_degradation", "shadow_b_only_after_fixed_six_pass"):
        if gate[key] is not True:
            raise PublicOodContractError(f"{key} must be true")
    if gate["longrun_allowed"] is not False:
        raise PublicOodContractError("longrun_allowed must be false for diagnostic policy")

    manifest_copy = MappingProxyType(dict(parsed))
    result = PublicOodPolicyManifestV1(
        manifest=manifest_copy,
        manifest_sha256=actual_sha,
        reference_artifact=str(source["reference_artifact"]),
        reference_artifact_sha256=reference_artifact_sha,
        reference_source_list_sha256=reference_source_list_sha,
        reference_source_sha256s=checked_source_hashes,
        reference_source_count=len(checked_source_hashes),
        partition="train",
        rare_count_threshold=rare,
        focus_on_ood=bool(bucket["focus_on_ood"]),
        min_normalized_surprisal=float(minimum),
        max_top1_top2_margin=None if margin is None else float(margin),
        promotion_authority=False,
        longrun_allowed=False,
        training_permitted=False,
    )
    if training_requested:
        raise PublicOodContractError(
            "public confidence/OOD policy is diagnostic-only; training is not connected or permitted"
        )
    return result


def load_public_ood_reference_bundle_v1(
    value: Mapping[str, object] | Path | str,
    *,
    expected_artifact_sha256: str | None = None,
    expected_source_list_sha256: str | None = None,
    expected_source_sha256s: Sequence[str] | None = None,
) -> PublicBucketReferenceV1:
    """Load and verify the multi-source public reference bundle.

    A single-source reference is deliberately not accepted here.  The
    returned ``PublicBucketReferenceV1.source_sha256`` is the ordered
    ``source_list_sha256`` identity, never one seed's transition-file SHA.
    """

    actual_artifact_sha: str | None = None
    if isinstance(value, Mapping):
        parsed = dict(value)
    else:
        path = Path(value)
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise PublicOodContractError("reference bundle cannot be read") from exc
        actual_artifact_sha = hashlib.sha256(raw).hexdigest()
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PublicOodContractError("reference bundle is not valid UTF-8 JSON") from exc
    if type(parsed) is not dict:
        raise PublicOodContractError("reference bundle must be a JSON object")
    if expected_artifact_sha256 is not None:
        expected_artifact = _sha(expected_artifact_sha256, field="reference artifact SHA-256")
        if actual_artifact_sha is None or actual_artifact_sha != expected_artifact:
            raise PublicOodContractError("reference artifact bytes do not match expected SHA-256")

    bundle = _closed_mapping(
        parsed,
        {
            "schema_version", "bucket_schema_version", "partition", "rare_count_threshold",
            "source_count", "source_list", "source_list_sha256", "source_stats",
            "transition_count", "prefix_count", "forced_prefix_count", "skipped_transition_count",
            "bucket_count", "bucket_counts", "privacy", "promotion_authority",
        },
        field="reference bundle",
    )
    if bundle["schema_version"] != REFERENCE_BUNDLE_SCHEMA_V1:
        raise PublicOodContractError("reference bundle schema is not the multi-source bundle schema")
    if bundle["bucket_schema_version"] != "meta-specialist-public-confidence-ood-v1":
        raise PublicOodContractError("reference bundle bucket schema is invalid")
    if bundle["partition"] != "train":
        raise PublicOodContractError("reference bundle partition must be train")
    rare = bundle["rare_count_threshold"]
    if type(rare) is not int or rare < 0:
        raise PublicOodContractError("reference bundle rare_count_threshold is invalid")
    source_count = bundle["source_count"]
    if type(source_count) is not int or source_count < 2:
        raise PublicOodContractError(
            "single-source reference is not a permitted public reference bundle"
        )
    source_list = bundle["source_list"]
    if type(source_list) is not list or len(source_list) != source_count:
        raise PublicOodContractError("reference bundle source_list/count mismatch")
    checked_sources: list[dict[str, object]] = []
    for ordinal, item in enumerate(source_list):
        source = _closed_mapping(item, {"ordinal", "source_sha256"}, field="reference bundle source_list[]")
        if source["ordinal"] != ordinal:
            raise PublicOodContractError("reference bundle source_list ordinals are not contiguous")
        checked_sources.append({
            "ordinal": ordinal,
            "source_sha256": _sha(source["source_sha256"], field="reference bundle source SHA-256"),
        })
    source_hashes = tuple(str(item["source_sha256"]) for item in checked_sources)
    if len(set(source_hashes)) != len(source_hashes):
        raise PublicOodContractError("reference bundle source_list has duplicate source bytes")
    source_list_sha = _sha(bundle["source_list_sha256"], field="reference bundle source_list_sha256")
    canonical_manifest = {"partition": "train", "source_list": checked_sources}
    actual_source_list_sha = hashlib.sha256(
        json.dumps(canonical_manifest, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if source_list_sha != actual_source_list_sha:
        raise PublicOodContractError("reference bundle source_list_sha256 does not verify")
    if expected_source_list_sha256 is not None and source_list_sha != _sha(
        expected_source_list_sha256, field="expected source_list_sha256"
    ):
        raise PublicOodContractError("reference bundle source_list_sha256 differs from expected identity")
    if expected_source_sha256s is not None:
        expected_sources = tuple(
            _sha(item, field="expected source SHA-256") for item in expected_source_sha256s
        )
        if expected_sources != source_hashes:
            raise PublicOodContractError("reference bundle source hash list differs from expected identity")

    source_stats = bundle["source_stats"]
    if type(source_stats) is not list or len(source_stats) != source_count:
        raise PublicOodContractError("reference bundle source_stats/count mismatch")
    for ordinal, item in enumerate(source_stats):
        stats = _closed_mapping(
            item,
            {"ordinal", "transition_count", "prefix_count", "forced_prefix_count", "skipped_transition_count"},
            field="reference bundle source_stats[]",
        )
        if stats["ordinal"] != ordinal:
            raise PublicOodContractError("reference bundle source_stats ordinals are not contiguous")
        for key in ("transition_count", "prefix_count", "forced_prefix_count", "skipped_transition_count"):
            if type(stats[key]) is not int or stats[key] < 0:
                raise PublicOodContractError(f"reference bundle source_stats {key} is invalid")
    for key in ("transition_count", "prefix_count", "forced_prefix_count", "skipped_transition_count"):
        if type(bundle[key]) is not int or bundle[key] < 0:
            raise PublicOodContractError(f"reference bundle {key} is invalid")
    for key in ("transition_count", "prefix_count", "forced_prefix_count", "skipped_transition_count"):
        if sum(int(stats[key]) for stats in source_stats) != bundle[key]:
            raise PublicOodContractError(f"reference bundle {key} disagrees with source_stats")

    bucket_counts = bundle["bucket_counts"]
    if type(bucket_counts) is not dict:
        raise PublicOodContractError("reference bundle bucket_counts must be an object")
    checked_counts: dict[str, int] = {}
    for bucket_id, count in bucket_counts.items():
        checked_counts[_sha(bucket_id, field="reference bucket id")] = count
        if type(count) is not int or count < 0:
            raise PublicOodContractError("reference bundle bucket counts must be nonnegative ints")
    if bundle["bucket_count"] != len(checked_counts):
        raise PublicOodContractError("reference bundle bucket_count does not match bucket_counts")
    privacy = _closed_mapping(
        bundle["privacy"],
        {"uses_opponent_id", "uses_seat", "uses_policy_identity", "uses_hidden_fields"},
        field="reference bundle privacy",
    )
    if any(privacy[key] is not False for key in privacy):
        raise PublicOodContractError("reference bundle privacy boundary is not public-only")
    if bundle["promotion_authority"] is not False:
        raise PublicOodContractError("reference bundle promotion_authority must be false")
    return PublicBucketReferenceV1(
        source_sha256=source_list_sha,
        bucket_counts=checked_counts,
        rare_count_threshold=rare,
    )


@dataclass(frozen=True, slots=True)
class SealedPublicTransitionV1:
    """One sealed actor-visible row plus opaque recurrent context.

    No opponent, seat, policy identity, or hidden engine payload is accepted.
    ``hidden_context`` is intentionally opaque and is carried by object
    identity so a context-only row cannot be silently dropped or rebuilt.
    """

    record_id: str
    group_id: str
    row_index: int
    episode_start: bool
    hidden_context: object
    model_input: SpecialistModelInputV1
    step_input: SpecialistStepInputV1
    logits: SpecialistStepLogitsV1
    chosen_semantic_action: SemanticActionV1 | None = None
    chosen_is_stop: bool = False

    @property
    def episode_group(self) -> str:
        """Alias used by the recurrent V4 terminology without duplicating state."""

        return self.group_id

    def __post_init__(self) -> None:
        if type(self.record_id) is not str or not self.record_id:
            raise PublicOodContractError("sealed row record_id is invalid")
        if type(self.group_id) is not str or not self.group_id:
            raise PublicOodContractError("sealed row group_id is invalid")
        if type(self.row_index) is not int or self.row_index < 0:
            raise PublicOodContractError("sealed row row_index must be nonnegative")
        if type(self.episode_start) is not bool:
            raise PublicOodContractError("sealed row episode_start must be bool")
        if type(self.model_input) is not SpecialistModelInputV1:
            raise PublicOodContractError("sealed row model_input is invalid")
        if type(self.step_input) is not SpecialistStepInputV1:
            raise PublicOodContractError("sealed row step_input is invalid")
        if type(self.logits) is not SpecialistStepLogitsV1:
            raise PublicOodContractError("sealed row logits are invalid")
        if self.chosen_semantic_action is not None and type(self.chosen_semantic_action) is not SemanticActionV1:
            raise PublicOodContractError("sealed row chosen semantic action is invalid")
        if type(self.chosen_is_stop) is not bool:
            raise PublicOodContractError("sealed row chosen_is_stop must be bool")
        if self.chosen_is_stop and self.chosen_semantic_action is not None:
            raise PublicOodContractError("STOP row cannot also carry a semantic target")


@dataclass(frozen=True, slots=True)
class MaskedPublicTransitionV1:
    source: SealedPublicTransitionV1
    score: PublicStepScoreV1
    supervision_weight: float

    def __post_init__(self) -> None:
        if type(self.source) is not SealedPublicTransitionV1:
            raise PublicOodContractError("masked row source is invalid")
        if type(self.score) is not PublicStepScoreV1:
            raise PublicOodContractError("masked row score is invalid")
        if type(self.supervision_weight) is not float or not math.isfinite(self.supervision_weight) or not 0.0 <= self.supervision_weight <= 1.0:
            raise PublicOodContractError("masked row supervision_weight is invalid")
        expected = supervision_weight_from_public_score_v1(self.score)
        if self.supervision_weight != expected:
            raise PublicOodContractError("masked row weight does not match public score")

    @property
    def context_only(self) -> bool:
        return self.supervision_weight == 0.0


@dataclass(frozen=True, slots=True)
class PublicOodMaskContractV1:
    schema_version: str
    rows: tuple[MaskedPublicTransitionV1, ...]
    record_row_counts: Mapping[str, int]
    group_row_counts: Mapping[str, int]
    loss_bearing_row_count: int
    context_only_row_count: int
    effective_loss_mass: float
    reference_artifact: str
    reference_source_sha256: str
    promotion_authority: bool
    longrun_allowed: bool
    training_permitted: bool

    def __post_init__(self) -> None:
        if self.schema_version != CONTRACT_SCHEMA_V1 or type(self.rows) is not tuple or not self.rows:
            raise PublicOodContractError("mask contract schema/rows are invalid")
        if any(type(row) is not MaskedPublicTransitionV1 for row in self.rows):
            raise PublicOodContractError("mask contract contains an invalid row")
        if self.loss_bearing_row_count + self.context_only_row_count != len(self.rows):
            raise PublicOodContractError("mask contract row counts do not close")
        if self.effective_loss_mass != float(self.loss_bearing_row_count):
            raise PublicOodContractError("mask contract effective loss mass is not the explicit row sum")
        if self.promotion_authority is not False or self.longrun_allowed is not False or self.training_permitted is not False:
            raise PublicOodContractError("mask contract unexpectedly grants authority")


def _score_fixture_row_v1(
    row: SealedPublicTransitionV1,
    *,
    reference: PublicBucketReferenceV1 | None,
    policy: PublicEligibilityPolicyV1 = PublicEligibilityPolicyV1(),
) -> PublicStepScoreV1:
    if type(row) is not SealedPublicTransitionV1:
        raise PublicOodContractError("fixture row must be SealedPublicTransitionV1")
    return score_public_step_v1(
        row.model_input,
        row.step_input,
        row.logits,
        chosen_semantic_action=row.chosen_semantic_action,
        chosen_is_stop=row.chosen_is_stop,
        reference=reference,
        policy=policy,
    )


def _validate_row_topology_v1(rows: tuple[SealedPublicTransitionV1, ...]) -> tuple[dict[str, int], dict[str, int]]:
    if not rows:
        raise PublicOodContractError("sealed transition fixture is empty")
    record_counts: dict[str, int] = {}
    group_counts: dict[str, int] = {}
    seen_groups: set[str] = set()
    seen_records: set[str] = set()
    active_group: str | None = None
    active_record: str | None = None
    next_record_index: dict[str, int] = {}
    group_first = True
    for row in rows:
        if row.group_id != active_group:
            if row.group_id in seen_groups:
                raise PublicOodContractError("sealed fixture group re-entry would break recurrent topology")
            if active_group is not None:
                seen_groups.add(active_group)
            active_group = row.group_id
            group_first = True
        if row.record_id != active_record:
            if row.record_id in seen_records:
                raise PublicOodContractError("sealed fixture record re-entry would break row topology")
            if active_record is not None:
                seen_records.add(active_record)
            active_record = row.record_id
        expected_index = next_record_index.get(row.record_id, 0)
        if row.row_index != expected_index:
            raise PublicOodContractError(
                f"sealed fixture row_index must be contiguous from zero for record {row.record_id}"
            )
        next_record_index[row.record_id] = expected_index + 1
        record_counts[row.record_id] = expected_index + 1
        group_counts[row.group_id] = group_counts.get(row.group_id, 0) + 1
        if group_first:
            if row.episode_start is not True:
                raise PublicOodContractError("each sealed group must begin with episode_start=True")
            group_first = False
        elif row.episode_start:
            raise PublicOodContractError("episode_start may only appear on the first row of a group")
    return record_counts, group_counts


def build_public_ood_mask_contract_v1(
    rows: Sequence[SealedPublicTransitionV1],
    *,
    reference: PublicBucketReferenceV1,
    policy_manifest: Mapping[str, object] | Path | str,
    expected_manifest_sha256: str | None = None,
    training_requested: bool = False,
) -> PublicOodMaskContractV1:
    """Score and mask a sealed fixture without dropping recurrent context."""

    policy_manifest_obj = validate_public_ood_policy_manifest_v1(
        policy_manifest,
        expected_sha256=expected_manifest_sha256,
        training_requested=training_requested,
    )
    if type(reference) is not PublicBucketReferenceV1:
        raise PublicOodContractError("reference must be PublicBucketReferenceV1")
    if reference.source_sha256 != policy_manifest_obj.reference_source_sha256:
        raise PublicOodContractError("reference source SHA differs from policy manifest")
    if reference.rare_count_threshold != policy_manifest_obj.rare_count_threshold:
        raise PublicOodContractError("reference rare threshold differs from policy manifest")
    if type(rows) not in (tuple, list):
        raise PublicOodContractError("sealed rows must be a tuple or list")
    normalized = tuple(
        sealed_public_transition_from_mapping_v1(row) if isinstance(row, Mapping) else row
        for row in rows
    )
    if any(type(row) is not SealedPublicTransitionV1 for row in normalized):
        raise PublicOodContractError("sealed rows must contain only typed public rows")
    record_counts, group_counts = _validate_row_topology_v1(normalized)

    masked: list[MaskedPublicTransitionV1] = []
    for row in normalized:
        score = _score_fixture_row_v1(
            row, reference=reference, policy=policy_manifest_obj.eligibility_policy,
        )
        masked.append(MaskedPublicTransitionV1(
            source=row,
            score=score,
            supervision_weight=float(supervision_weight_from_public_score_v1(score)),
        ))
    loss_bearing = sum(item.supervision_weight > 0.0 for item in masked)
    context_only = len(masked) - loss_bearing
    return PublicOodMaskContractV1(
        schema_version=CONTRACT_SCHEMA_V1,
        rows=tuple(masked),
        record_row_counts=MappingProxyType(dict(record_counts)),
        group_row_counts=MappingProxyType(dict(group_counts)),
        loss_bearing_row_count=loss_bearing,
        context_only_row_count=context_only,
        effective_loss_mass=float(math.fsum(item.supervision_weight for item in masked)),
        reference_artifact=policy_manifest_obj.reference_artifact,
        reference_source_sha256=policy_manifest_obj.reference_source_sha256,
        promotion_authority=policy_manifest_obj.promotion_authority,
        longrun_allowed=policy_manifest_obj.longrun_allowed,
        training_permitted=policy_manifest_obj.training_permitted,
    )


def run_public_confidence_ood_bc_v1(
    rows: Sequence[SealedPublicTransitionV1],
    *,
    policy_manifest: Mapping[str, object] | Path | str,
    reference: PublicBucketReferenceV1 | None = None,
    train: bool = False,
    training_requested: bool = False,
) -> PublicOodMaskContractV1:
    """Explicit orchestration entrypoint; model training is not connected."""

    if train or training_requested:
        raise PublicOodContractError(
            "public confidence/OOD BC model training is not connected in this contract-only runner"
        )
    if reference is None:
        raise PublicOodContractError("contract-only runner requires a sealed public reference fixture")
    return build_public_ood_mask_contract_v1(
        rows,
        reference=reference,
        policy_manifest=policy_manifest,
    )


def sealed_public_transition_to_mapping_v1(row: SealedPublicTransitionV1) -> dict[str, object]:
    """Expose a closed in-memory fixture shape without private metadata."""

    if type(row) is not SealedPublicTransitionV1:
        raise PublicOodContractError("row must be SealedPublicTransitionV1")
    return {
        "schema": SEALED_ROW_SCHEMA_V1,
        "record_id": row.record_id,
        "group_id": row.group_id,
        "row_index": row.row_index,
        "episode_start": row.episode_start,
        "hidden_context": row.hidden_context,
        "model_input": row.model_input,
        "step_input": row.step_input,
        "logits": row.logits,
        "chosen_semantic_action": row.chosen_semantic_action,
        "chosen_is_stop": row.chosen_is_stop,
    }


def sealed_public_transition_from_mapping_v1(payload: Mapping[str, object]) -> SealedPublicTransitionV1:
    """Parse only the closed public fixture shape; reject opponent/seat keys."""

    expected = {
        "schema", "record_id", "group_id", "row_index", "episode_start", "hidden_context",
        "model_input", "step_input", "logits", "chosen_semantic_action", "chosen_is_stop",
    }
    value = _closed_mapping(payload, expected, field="sealed public transition")
    if value["schema"] != SEALED_ROW_SCHEMA_V1:
        raise PublicOodContractError("sealed public transition schema is invalid")
    return SealedPublicTransitionV1(
        record_id=value["record_id"], group_id=value["group_id"], row_index=value["row_index"],
        episode_start=value["episode_start"], hidden_context=value["hidden_context"],
        model_input=value["model_input"], step_input=value["step_input"], logits=value["logits"],
        chosen_semantic_action=value["chosen_semantic_action"], chosen_is_stop=value["chosen_is_stop"],
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Refuse CLI execution instead of accidentally starting a real runner."""

    del argv
    raise PublicOodContractError(
        "this research-only module exposes a fixture contract API; no CLI training/eval runner is connected"
    )


__all__ = [
    "CONTRACT_SCHEMA_V1",
    "MaskedPublicTransitionV1",
    "POLICY_SCHEMA_V1",
    "POLICY_STATUS_V1",
    "PublicOodContractError",
    "PublicOodMaskContractV1",
    "PublicOodPolicyManifestV1",
    "SEALED_ROW_SCHEMA_V1",
    "SealedPublicTransitionV1",
    "build_public_ood_mask_contract_v1",
    "main",
    "run_public_confidence_ood_bc_v1",
    "sealed_public_transition_from_mapping_v1",
    "sealed_public_transition_to_mapping_v1",
    "validate_public_ood_policy_manifest_v1",
]
