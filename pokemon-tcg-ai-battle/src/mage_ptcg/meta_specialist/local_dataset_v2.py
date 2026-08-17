"""Closed, local-only specialist decision records and training loader.

Task 5 deliberately keeps actor-visible C1 state in the local record, while
returning only the serial-free Task 3 feature payload to training callers.
"""

from __future__ import annotations

import copy
import json
import math
import hashlib
import os
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType
# `Mapping` は runtime の isinstance に使う。`typing.Mapping` の
# `__instancecheck__` は Python 実装で、`collections.abc` の C 実装より桁違いに
# 遅い。`_validate_json_bounds` は JSON ノード 1 個ごとに isinstance を呼ぶため、
# これが封印の支配的コストだった (実測: 20 局 1745 record の封印 393 秒のうち
# `typing.__instancecheck__` が 1.2 億回 102 秒)。
from collections.abc import Iterable, Mapping
from typing import Any
from pathlib import Path

from mage_ptcg.meta_specialist.actor_visible_features_v1 import (
    ACTOR_VISIBLE_FEATURE_DOMAIN_V1,
    CardVocabularyV1,
    ExtractedSpecialistModelInputV1,
    FEATURE_SCHEMA_HASH_V1,
    STATE_SCALAR_NAMES_V1,
    build_specialist_step_input_v1,
    extract_specialist_model_input_v1,
)
from mage_ptcg.meta_specialist.actor_visible_v2 import (
    ActorVisibleDecisionStateV2,
    ActorVisibleV2Error,
    C1_V2_SCHEMA_VERSION,
    deserialize_actor_visible_decision_state_v2,
    serialize_actor_visible_decision_state_v2,
)
from mage_ptcg.meta_specialist.cabt_json_contract_v1 import is_ordered_selection
from mage_ptcg.decision_state import ACTION_KEY_SCHEMA_VERSION


MAX_LOCAL_RECORD_BYTES_V2 = 16 * 1024 * 1024
MAX_LOCAL_CANDIDATES_V2 = 512
MAX_COMPLETE_ACTION_ROWS_V2 = 65_536
MAX_CANONICAL_JSON_DEPTH_V2 = 128
MAX_CANONICAL_JSON_NODES_V2 = 100_000
MAX_CANONICAL_JSON_CONTAINER_ITEMS_V2 = 65_536
# Kept equal to the envelope-side bound in `training_example_envelope_v2`; see the
# rationale there for why 512 MiB made two of four measured teacher lanes
# unsealable.  Diverging the two would just move the failure one stage later.
MAX_TRAINING_SPOOL_BYTES_V2 = 4 * 1024 * 1024 * 1024
# See `ubiquitous_near_duplicate_ids_v2`: a position recurring in at least this
# share of the episodes (never fewer than the floor) is a constant of the task,
# not a leak, and must not act as a union edge in the grouped split.
_UBIQUITY_EPISODE_FRACTION_V2 = 0.05
_UBIQUITY_MIN_EPISODES_V2 = 8
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_RFC3339_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_PERMISSION_KEYS = frozenset({
    "schema_version", "permission_manifest_id", "content_hash",
    "artifact_sha256", "source_kind", "allowed_usages", "revision", "issuer",
    "valid_from_utc", "expires_at_utc",
})
_PERMISSION_USAGES = frozenset({"audit-local", "training-local", "submission-bundle"})
_RECORD_KEYS = frozenset({
    "schema_version", "record_id", "content_hash", "decision_id", "model_input_id",
    "episode_id_hash", "decision_index", "information_state", "selection", "legal_actions",
    "behavior", "teacher", "student", "source", "provenance", "privacy", "public_audit",
    "near_duplicate_id",
})
_SOURCE_KEYS = frozenset({
    "kind", "artifact_sha256", "synthetic", "synthetic_fields", "training_eligible",
    "usage_class", "permission_manifest_id",
})
_PROVENANCE_KEYS = frozenset({"source_record_ordinal"})
_PRIVACY_KEYS = frozenset({"classification", "export_allowed"})
_PUBLIC_AUDIT_KEYS = frozenset({"projection_status", "collision_sizes", "c5_record_id"})
_CANDIDATE_KEYS = frozenset({
    "local_action_id", "action_key_digest", "action_key_payload", "public_action_id",
    "public_payload", "actor_binding", "semantic_action", "features",
})
_INFORMATION_VIEW_KEYS = frozenset({
    "actor", "self_player", "opponent_player", "private_state", "board_stadium",
    "stadium_played", "supporter_played", "energy_attached", "retreated", "first_player",
    "observed_result", "step", "turn", "turn_action_count", "remain_damage_counter",
    "remain_energy_cost", "selection_type", "selection_context", "min_count", "max_count",
})
_MANIFEST_KEYS = frozenset({
    "schema_version", "manifest_id", "content_hash", "record_schema_version",
    "record_content_hash_domain", "c1_schema_version", "action_key_schema_version",
    "feature_domain", "feature_schema_hash", "feature_dimension", "environment_version",
    "deck_fingerprint", "source_artifacts", "permission_references", "usage_rights",
    "export_allowed", "record_count", "record_content_hashes",
})
_MANIFEST_SOURCE_KEYS = frozenset({"kind", "artifact_sha256"})
_MANIFEST_PERMISSION_KEYS = frozenset({
    "permission_manifest_id", "permission_content_hash", "trusted_bytes_sha256",
})


class LocalDatasetV2Error(ValueError):
    """Raised for closed-schema local dataset contract violations."""


def _hash(domain: str, value: object) -> str:
    return hashlib.sha256(domain.encode("utf-8") + b"\0" + canonical_json_bytes_v2(value)).hexdigest()


def _sha256(value: object, *, field: str) -> str:
    if type(value) is not str or _HEX64.fullmatch(value) is None:
        raise LocalDatasetV2Error(f"{field} must be lowercase 64-hex")
    return value


def _bounded_string(value: object, *, field: str, maximum: int = 256) -> str:
    if type(value) is not str or not value or len(value) > maximum:
        raise LocalDatasetV2Error(f"{field} must be a nonempty bounded string")
    return value


def _exact_mapping(value: object, *, field: str, keys: frozenset[str]) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys:
        raise LocalDatasetV2Error(f"{field} has the wrong closed field set")
    return value


def _utc(value: object, *, field: str) -> str | None:
    if value is None:
        return None
    if type(value) is not str or _RFC3339_UTC.fullmatch(value) is None:
        raise LocalDatasetV2Error(f"{field} must be a canonical RFC3339 UTC timestamp or null")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise LocalDatasetV2Error(f"{field} must be a canonical RFC3339 UTC timestamp or null") from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise LocalDatasetV2Error(f"{field} must be a canonical RFC3339 UTC timestamp or null")
    return value


def _permission_identity(payload: dict[str, Any]) -> dict[str, object]:
    return {
        "schema_version": payload["schema_version"],
        "artifact_sha256": payload["artifact_sha256"],
        "source_kind": payload["source_kind"],
        "allowed_usages": payload["allowed_usages"],
        "revision": payload["revision"], "issuer": payload["issuer"],
        "valid_from_utc": payload["valid_from_utc"],
        "expires_at_utc": payload["expires_at_utc"],
    }


def validate_source_permission_manifest_v1(value: object) -> dict[str, object]:
    """Validate a sealed permission and recompute both frozen hashes."""
    payload = _exact_mapping(value, field="source permission manifest", keys=_PERMISSION_KEYS)
    if payload["schema_version"] != "specialist-source-permission-v1":
        raise LocalDatasetV2Error("source permission manifest schema_version is invalid")
    _sha256(payload["artifact_sha256"], field="permission artifact_sha256")
    _bounded_string(payload["source_kind"], field="permission source_kind")
    usages = payload["allowed_usages"]
    if (
        type(usages) is not list or not usages or len(usages) > len(_PERMISSION_USAGES)
        or any(type(item) is not str or item not in _PERMISSION_USAGES for item in usages)
        or usages != sorted(set(usages))
    ):
        raise LocalDatasetV2Error("permission allowed_usages must be a sorted unique nonempty allowed subset")
    _bounded_string(payload["revision"], field="permission revision")
    _bounded_string(payload["issuer"], field="permission issuer")
    start = _utc(payload["valid_from_utc"], field="permission valid_from_utc")
    end = _utc(payload["expires_at_utc"], field="permission expires_at_utc")
    if start is not None and end is not None and start >= end:
        raise LocalDatasetV2Error("permission validity interval is invalid")
    identity = _permission_identity(payload)
    expected_id = _hash("mage_ptcg:specialist-source-permission:v1", identity)
    if payload["permission_manifest_id"] != expected_id:
        raise LocalDatasetV2Error("permission_manifest_id does not verify")
    expected_content = _hash(
        "mage_ptcg:specialist-source-permission-content:v1",
        {**identity, "permission_manifest_id": expected_id},
    )
    if payload["content_hash"] != expected_content:
        raise LocalDatasetV2Error("permission content_hash does not verify")
    return dict(payload)


def make_source_permission_manifest_v1(
    *, artifact_sha256: str, source_kind: str, allowed_usages: tuple[str, ...],
    revision: str, issuer: str, valid_from_utc: str | None, expires_at_utc: str | None,
) -> dict[str, object]:
    """Build one locally sealed permission manifest from explicit authority fields."""
    identity: dict[str, object] = {
        "schema_version": "specialist-source-permission-v1",
        "artifact_sha256": artifact_sha256, "source_kind": source_kind,
        "allowed_usages": list(allowed_usages), "revision": revision, "issuer": issuer,
        "valid_from_utc": valid_from_utc, "expires_at_utc": expires_at_utc,
    }
    # Validate caller fields before deriving an ID, but do not accept caller hashes.
    probe = {**identity, "permission_manifest_id": "0" * 64, "content_hash": "0" * 64}
    payload = _permission_identity(_exact_mapping(probe, field="permission build", keys=_PERMISSION_KEYS))
    _sha256(payload["artifact_sha256"], field="permission artifact_sha256")
    _bounded_string(payload["source_kind"], field="permission source_kind")
    usages = payload["allowed_usages"]
    if type(usages) is not list or not usages or any(type(item) is not str or item not in _PERMISSION_USAGES for item in usages) or usages != sorted(set(usages)):
        raise LocalDatasetV2Error("permission allowed_usages must be sorted unique allowed values")
    _bounded_string(payload["revision"], field="permission revision")
    _bounded_string(payload["issuer"], field="permission issuer")
    start = _utc(payload["valid_from_utc"], field="permission valid_from_utc")
    end = _utc(payload["expires_at_utc"], field="permission expires_at_utc")
    if start is not None and end is not None and start >= end:
        raise LocalDatasetV2Error("permission validity interval is invalid")
    permission_id = _hash("mage_ptcg:specialist-source-permission:v1", payload)
    record = {**payload, "permission_manifest_id": permission_id}
    record["content_hash"] = _hash("mage_ptcg:specialist-source-permission-content:v1", record)
    return validate_source_permission_manifest_v1(record)


@dataclass(frozen=True, slots=True)
class TrustedPermissionV1:
    """Permission bytes supplied out-of-band; dataset bytes can never create this."""

    permission_manifest_id: str
    content_hash: str
    raw_bytes: bytes
    raw_sha256: str


def build_trusted_permission_set_v1(raw_manifests: tuple[bytes, ...]) -> MappingProxyType:
    """Parse immutable exact permission bytes supplied by the local operator."""
    if type(raw_manifests) is not tuple:
        raise LocalDatasetV2Error("trusted permissions must be an immutable tuple of bytes")
    trusted: dict[str, TrustedPermissionV1] = {}
    for raw in raw_manifests:
        parsed = parse_canonical_json_bytes_v2(raw)
        manifest = validate_source_permission_manifest_v1(parsed)
        permission_id = manifest["permission_manifest_id"]
        if permission_id in trusted:
            raise LocalDatasetV2Error("trusted permissions contain a duplicate permission ID")
        trusted[permission_id] = TrustedPermissionV1(
            permission_manifest_id=permission_id, content_hash=manifest["content_hash"],
            raw_bytes=raw, raw_sha256=hashlib.sha256(raw).hexdigest(),
        )
    return MappingProxyType(trusted)


def _reparse_trusted_permission_v1(entry: object, *, permission_id: str) -> dict[str, object]:
    """Make every trust use depend on the originally sealed raw bytes, not fields."""
    if type(entry) is not TrustedPermissionV1 or entry.permission_manifest_id != permission_id:
        raise LocalDatasetV2Error("trusted permission entry has an invalid identity")
    if hashlib.sha256(entry.raw_bytes).hexdigest() != entry.raw_sha256:
        raise LocalDatasetV2Error("trusted permission raw bytes no longer match their SHA-256")
    permission = validate_source_permission_manifest_v1(parse_canonical_json_bytes_v2(entry.raw_bytes))
    if permission["permission_manifest_id"] != permission_id or permission["content_hash"] != entry.content_hash:
        raise LocalDatasetV2Error("trusted permission raw bytes fail their sealed identity")
    return permission


def _strict_nonnegative_int(value: object, *, field: str) -> int:
    if type(value) is not int or value < 0:
        raise LocalDatasetV2Error(f"{field} must be a nonnegative non-bool int")
    return value


def _strict_selection(
    value: object, *, field: str, legal_ids: frozenset[str], ordered: bool,
    minimum: int, maximum: int, allow_empty: bool = False,
) -> list[str]:
    if type(value) is not list or len(value) > MAX_LOCAL_CANDIDATES_V2:
        raise LocalDatasetV2Error(f"{field} must be a bounded list")
    if not allow_empty and not minimum <= len(value) <= maximum:
        raise LocalDatasetV2Error(f"{field} violates the selection cardinality")
    if any(type(item) is not str or _HEX64.fullmatch(item) is None for item in value):
        raise LocalDatasetV2Error(f"{field} contains an invalid local action ID")
    if len(value) != len(set(value)) or any(item not in legal_ids for item in value):
        raise LocalDatasetV2Error(f"{field} contains duplicate or illegal local IDs")
    if not ordered and value != sorted(value):
        raise LocalDatasetV2Error(f"{field} must be sorted for an unordered decision")
    return list(value)


def _selection_contract(view: Mapping[str, Any]) -> dict[str, int]:
    return {
        "selection_type": view["selection_type"], "selection_context": view["selection_context"],
        "min_count": view["min_count"], "max_count": view["max_count"],
    }


def _information_state_id(information_state: Mapping[str, Any]) -> str:
    core = copy.deepcopy(dict(information_state))
    core.pop("observed_result", None)
    return _hash("mage_ptcg:specialist-information-state:v1", core)


def _decision_id(information_state: Mapping[str, Any], local_ids: list[str]) -> str:
    contract = _selection_contract(information_state)
    return _hash("mage_ptcg:specialist-decision:v2", {
        "information_state_id": _information_state_id(information_state),
        "selection_contract": contract, "sorted_local_action_ids": sorted(local_ids),
    })


def derive_complete_action_id_v1(
    *, decision_id: str, selection_type: int, selection_context: int, selection: tuple[str, ...],
) -> str:
    """Derive the frozen local complete-action identity after canonical legality."""
    _sha256(decision_id, field="decision_id")
    try:
        ordered = is_ordered_selection(selection_type, selection_context)
    except ValueError as exc:
        raise LocalDatasetV2Error("complete action has an unsupported selection schema") from exc
    if any(type(item) is not str or _HEX64.fullmatch(item) is None for item in selection):
        raise LocalDatasetV2Error("complete action selection contains an invalid local ID")
    if not ordered and tuple(sorted(selection)) != selection:
        raise LocalDatasetV2Error("unordered complete action must use sorted local IDs")
    return _hash("mage_ptcg:specialist-complete-action:v1", {
        "decision_id": decision_id,
        "order_semantics": "ordered_sequence" if ordered else "unordered_set",
        "selection": list(selection),
    })


def _near_duplicate_id(extracted: ExtractedSpecialistModelInputV1) -> str:
    model_input = extracted.model_input
    return _hash("mage_ptcg:specialist-near-duplicate:v1", {
        "feature_domain": model_input.feature_domain,
        "feature_schema_hash": model_input.feature_schema_hash,
        "model_input": model_input.to_dict(),
    })


def _record_id(*, decision_id: str, episode_id_hash: str, decision_index: int) -> str:
    return _hash("mage_ptcg:specialist-record:v2", {
        "decision_id": decision_id, "episode_id_hash": episode_id_hash,
        "decision_index": decision_index,
    })


def _record_content_hash(record: Mapping[str, Any]) -> str:
    content = dict(record)
    content.pop("content_hash", None)
    return _hash("mage_ptcg:specialist-record-content:v2", content)


def _validate_source(value: object) -> dict[str, Any]:
    source = _exact_mapping(value, field="record source", keys=_SOURCE_KEYS)
    _bounded_string(source["kind"], field="record source.kind")
    _sha256(source["artifact_sha256"], field="record source.artifact_sha256")
    if type(source["synthetic"]) is not bool or type(source["training_eligible"]) is not bool:
        raise LocalDatasetV2Error("record source synthetic/training_eligible must be bool")
    fields = source["synthetic_fields"]
    if (
        type(fields) is not list or len(fields) > 64
        or any(type(item) is not str or not item or len(item) > 128 for item in fields)
        or fields != sorted(set(fields))
    ):
        raise LocalDatasetV2Error("record source.synthetic_fields must be sorted unique bounded strings")
    if source["usage_class"] not in {"audit_only_unqualified", "qualified_training"}:
        raise LocalDatasetV2Error("record source.usage_class is invalid")
    permission_id = source["permission_manifest_id"]
    if permission_id is not None:
        _sha256(permission_id, field="record source.permission_manifest_id")
    if source["training_eligible"]:
        if source["synthetic"] or fields or source["usage_class"] != "qualified_training" or permission_id is None:
            raise LocalDatasetV2Error("record source.training_eligible has an invalid redundant projection")
    elif source["usage_class"] == "qualified_training":
        raise LocalDatasetV2Error("unqualified record cannot claim qualified_training")
    if source["synthetic"] and permission_id is not None:
        raise LocalDatasetV2Error("synthetic audit source cannot carry a permission manifest")
    return source


def _validate_provenance(value: object) -> dict[str, Any]:
    result = _exact_mapping(value, field="record provenance", keys=_PROVENANCE_KEYS)
    _strict_nonnegative_int(result["source_record_ordinal"], field="source_record_ordinal")
    return result


def _validate_privacy(value: object) -> dict[str, Any]:
    result = _exact_mapping(value, field="record privacy", keys=_PRIVACY_KEYS)
    if result["classification"] != "local-actor-visible-v2" or result["export_allowed"] is not False:
        raise LocalDatasetV2Error("record privacy cannot authorize export")
    return result


def _validate_public_audit(value: object, *, expected_collision_sizes: list[int]) -> dict[str, Any]:
    audit = _exact_mapping(value, field="record public_audit", keys=_PUBLIC_AUDIT_KEYS)
    expected_status = "duplicate-public-identity" if expected_collision_sizes else "representable"
    if audit["projection_status"] != expected_status or audit["collision_sizes"] != expected_collision_sizes:
        raise LocalDatasetV2Error("record public_audit does not match deterministic collision projection")
    # No C5 serializer is emitted by this module, so no record may claim one.
    if audit["c5_record_id"] is not None:
        raise LocalDatasetV2Error("record public_audit cannot fabricate a C5 record")
    return audit


def _validate_behavior(value: object, *, selection: list[str], legal_ids: frozenset[str], ordered: bool, minimum: int, maximum: int) -> dict[str, Any]:
    if type(value) is not dict or value.get("status") not in {"on_policy", "action_only", "unavailable"}:
        raise LocalDatasetV2Error("behavior has an invalid closed status")
    status = value["status"]
    if status == "unavailable":
        result = _exact_mapping(value, field="behavior", keys=frozenset({"status", "reason"}))
        _bounded_string(result["reason"], field="behavior reason")
        return result
    result = _exact_mapping(value, field="behavior", keys=frozenset({"status", "selection"}))
    selected = _strict_selection(result["selection"], field="behavior selection", legal_ids=legal_ids, ordered=ordered, minimum=minimum, maximum=maximum)
    if selected != selection:
        raise LocalDatasetV2Error("behavior selection must equal the record selection")
    return result


def _validate_teacher(
    value: object, *, decision_id: str, model_input_id: str, legal_ids: frozenset[str],
    ordered: bool, selection_type: int, selection_context: int, minimum: int, maximum: int,
    extracted: ExtractedSpecialistModelInputV1,
) -> dict[str, Any]:
    if type(value) is not dict or value.get("status") not in {"unavailable", "available"}:
        raise LocalDatasetV2Error("teacher has an invalid closed status")
    if value["status"] == "unavailable":
        result = _exact_mapping(value, field="teacher", keys=frozenset({"status", "reason"}))
        _bounded_string(result["reason"], field="teacher reason")
        return result
    keys = frozenset({"status", "teacher_id", "teacher_revision", "input_id", "target_kind", "quality_weight", "value_target", "mass_rows"})
    result = _exact_mapping(value, field="teacher", keys=keys)
    _bounded_string(result["teacher_id"], field="teacher_id")
    _bounded_string(result["teacher_revision"], field="teacher_revision")
    if result["input_id"] != model_input_id:
        raise LocalDatasetV2Error("teacher input_id does not match model_input_id")
    target_kind = result["target_kind"]
    if target_kind not in {"hard_selection", "visit_count", "probability_mass"}:
        raise LocalDatasetV2Error("teacher target_kind is invalid")
    quality = result["quality_weight"]
    if type(quality) not in {int, float} or type(quality) is bool or not math.isfinite(quality) or not 0 < quality <= 1:
        raise LocalDatasetV2Error("teacher quality_weight must be finite in (0,1]")
    target = result["value_target"]
    if target is not None and (type(target) not in {int, float} or type(target) is bool or not math.isfinite(target) or not -1 <= target <= 1):
        raise LocalDatasetV2Error("teacher value_target must be null or finite in [-1,1]")
    rows = result["mass_rows"]
    if type(rows) is not list or not rows or len(rows) > MAX_COMPLETE_ACTION_ROWS_V2:
        raise LocalDatasetV2Error("teacher mass_rows has an invalid bounded length")
    complete_action_count = 0
    candidate_count = len(legal_ids)
    for width in range(minimum, maximum + 1):
        choices = math.perm(candidate_count, width) if ordered else math.comb(candidate_count, width)
        complete_action_count += choices
        if complete_action_count > MAX_COMPLETE_ACTION_ROWS_V2:
            complete_action_count = MAX_COMPLETE_ACTION_ROWS_V2
            break
    if len(rows) > complete_action_count:
        raise LocalDatasetV2Error("teacher rows exceed the exact legal complete-action count")
    previous = ""
    seen_selections: set[tuple[str, ...]] = set()
    weights: list[float] = []
    for row in rows:
        item = _exact_mapping(row, field="teacher mass row", keys=frozenset({"complete_action_id", "selection", "weight"}))
        selected = _strict_selection(item["selection"], field="teacher mass row selection", legal_ids=legal_ids, ordered=ordered, minimum=minimum, maximum=maximum)
        try:
            build_specialist_step_input_v1(extracted, tuple(selected))
        except ValueError as exc:
            raise LocalDatasetV2Error("teacher mass row is not a reachable complete action") from exc
        expected = derive_complete_action_id_v1(
            decision_id=decision_id, selection_type=selection_type,
            selection_context=selection_context, selection=tuple(selected),
        )
        if item["complete_action_id"] != expected or item["complete_action_id"] <= previous:
            raise LocalDatasetV2Error("teacher mass rows must have sorted recomputed complete-action IDs")
        previous = item["complete_action_id"]
        key = tuple(selected)
        if key in seen_selections:
            raise LocalDatasetV2Error("teacher mass rows duplicate a complete selection")
        seen_selections.add(key)
        weight = item["weight"]
        if target_kind in {"hard_selection", "visit_count"}:
            if type(weight) is not int or weight < 0:
                raise LocalDatasetV2Error("teacher count weight must be a nonnegative non-bool int")
        elif type(weight) not in {int, float} or type(weight) is bool or not math.isfinite(weight) or weight < 0:
            raise LocalDatasetV2Error("teacher probability weight must be finite and nonnegative")
        weights.append(float(weight))
    if target_kind == "hard_selection" and (len(rows) != 1 or rows[0]["weight"] != 1):
        raise LocalDatasetV2Error("hard_selection teacher must have one unit row")
    if target_kind == "visit_count" and not any(weight > 0 for weight in weights):
        raise LocalDatasetV2Error("visit_count teacher needs a positive count")
    if target_kind == "probability_mass" and abs(math.fsum(weights) - 1.0) > 1e-12:
        raise LocalDatasetV2Error("probability_mass teacher weights must sum to one")
    return result


def _validate_student(
    value: object, *, decision_id: str, legal_ids: frozenset[str], ordered: bool,
    selection_type: int, selection_context: int, minimum: int, maximum: int,
    extracted: ExtractedSpecialistModelInputV1,
) -> dict[str, Any]:
    if type(value) is not dict or value.get("status") not in {"fallback", "available"}:
        raise LocalDatasetV2Error("student has an invalid closed status")
    if value["status"] == "fallback":
        result = _exact_mapping(value, field="student", keys=frozenset({"status", "selection", "scores", "reason"}))
        if result["selection"] != [] or result["scores"] != []:
            raise LocalDatasetV2Error("student fallback must not fabricate selection or scores")
        _bounded_string(result["reason"], field="student fallback reason")
        return result
    result = _exact_mapping(value, field="student", keys=frozenset({"status", "selection", "scores", "complete_action_id", "log_probability"}))
    decoded = _strict_selection(result["selection"], field="student selection", legal_ids=legal_ids, ordered=ordered, minimum=minimum, maximum=maximum)
    try:
        build_specialist_step_input_v1(extracted, tuple(decoded))
    except ValueError as exc:
        raise LocalDatasetV2Error("student selection is not a reachable complete action") from exc
    expected_complete_id = derive_complete_action_id_v1(
        decision_id=decision_id, selection_type=selection_type,
        selection_context=selection_context, selection=tuple(decoded),
    )
    if result["complete_action_id"] != expected_complete_id:
        raise LocalDatasetV2Error("student complete_action_id does not verify")
    supplied_log_probability = result["log_probability"]
    if type(supplied_log_probability) not in {int, float} or type(supplied_log_probability) is bool or not math.isfinite(supplied_log_probability):
        raise LocalDatasetV2Error("student log_probability must be finite")
    scores = result["scores"]
    if type(scores) is not list or len(scores) > MAX_LOCAL_CANDIDATES_V2:
        raise LocalDatasetV2Error("student scores must be a bounded list")
    expected_rows: list[tuple[dict[str, object], object]] = []
    for depth in range(len(decoded) + 1):
        step = build_specialist_step_input_v1(extracted, tuple(decoded[:depth]))
        if not step.allowed_semantic_classes and step.stop_available:
            continue  # forced STOP has no model logit domain and no score row.
        if depth == len(decoded):
            if not step.stop_available:
                raise LocalDatasetV2Error("student completion cannot legally STOP")
            expected_token: object = "stop"
        else:
            expected_token = extracted.model_input.candidate_rows[
                extracted.local_action_id_to_candidate_row_index[decoded[depth]]
            ].to_dict()
        expected_rows.append(({
            "semantic_prefix": [row.to_dict() for row in step.semantic_prefix],
            "semantic_classes": [item.semantic_row.to_dict() for item in step.allowed_semantic_classes],
            "stop_available": step.stop_available,
        }, expected_token))
    if len(scores) != len(expected_rows):
        raise LocalDatasetV2Error("student scores do not cover the exact decode path")
    recomputed_log_probability = 0.0
    for score, (expected, selected_token) in zip(scores, expected_rows, strict=True):
        row = _exact_mapping(score, field="student score row", keys=frozenset({"semantic_prefix", "token_scores"}))
        if row["semantic_prefix"] != expected["semantic_prefix"]:
            raise LocalDatasetV2Error("student score prefix does not match shared semantic legality")
        token_scores = row["token_scores"]
        if type(token_scores) is not list:
            raise LocalDatasetV2Error("student token_scores must be a list")
        expected_tokens: list[dict[str, object]] = [
            {"kind": "semantic", "semantic_action": item}
            for item in expected["semantic_classes"]
        ] + ([{"kind": "stop"}] if expected["stop_available"] else [])
        if len(token_scores) != len(expected_tokens):
            raise LocalDatasetV2Error("student token_scores do not cover the exact legal logit domain")
        logits: list[float] = []
        selected_logit: float | None = None
        for score_token, expected_token_shape in zip(token_scores, expected_tokens, strict=True):
            expected_keys = frozenset({*expected_token_shape, "logit"})
            token = _exact_mapping(score_token, field="student token score", keys=expected_keys)
            for key, expected_value in expected_token_shape.items():
                if token[key] != expected_value:
                    raise LocalDatasetV2Error("student token score does not match the shared logit domain")
            logit = token["logit"]
            if type(logit) not in {int, float} or type(logit) is bool or not math.isfinite(logit):
                raise LocalDatasetV2Error("student token score logit must be finite")
            value_float = float(logit)
            logits.append(value_float)
            if (selected_token == "stop" and token["kind"] == "stop") or (selected_token != "stop" and token["kind"] == "semantic" and token["semantic_action"] == selected_token):
                selected_logit = value_float
        if selected_logit is None:
            raise LocalDatasetV2Error("student score path token is absent from the legal logit domain")
        maximum_logit = max(logits)
        log_normalizer = maximum_logit + math.log(math.fsum(math.exp(logit - maximum_logit) for logit in logits))
        recomputed_log_probability += selected_logit - log_normalizer
    if not math.isclose(float(supplied_log_probability), recomputed_log_probability, rel_tol=0.0, abs_tol=1e-12):
        raise LocalDatasetV2Error("student log_probability does not match exact token scores")
    return result


def _candidate_rows_from_state(state: ActorVisibleDecisionStateV2, extracted: ExtractedSpecialistModelInputV1) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for action in state.legal_actions:
        feature = extracted.model_input.candidate_rows[
            extracted.local_action_id_to_candidate_row_index[action.local_action_id]
        ].to_dict()
        rows.append({
            "local_action_id": action.local_action_id,
            "action_key_digest": action.action_key.digest,
            "action_key_payload": action.action_key.to_canonical_payload(),
            "public_action_id": action.public_action_id,
            "public_payload": action.action_key.to_public_trace_payload(),
            "actor_binding": action.binding.core.to_identity_dict(),
            "semantic_action": feature, "features": feature,
        })
    return sorted(rows, key=lambda row: row["local_action_id"])


def build_local_record_v2(
    *, state: ActorVisibleDecisionStateV2, vocabulary: CardVocabularyV1, episode_id_hash: str,
    decision_index: int, selection: tuple[str, ...], behavior: dict[str, object],
    teacher: dict[str, object], student: dict[str, object], source: dict[str, object],
    provenance: dict[str, object],
) -> dict[str, object]:
    """Construct a closed local record from typed C1 and shared feature extraction."""
    if not isinstance(state, ActorVisibleDecisionStateV2):
        raise LocalDatasetV2Error("state must be an ActorVisibleDecisionStateV2")
    if not isinstance(vocabulary, CardVocabularyV1):
        raise LocalDatasetV2Error("vocabulary must be an explicit CardVocabularyV1")
    _sha256(episode_id_hash, field="episode_id_hash")
    _strict_nonnegative_int(decision_index, field="decision_index")
    serialized = serialize_actor_visible_decision_state_v2(state)
    information_state = serialized["information_view"]
    extracted = extract_specialist_model_input_v1(state, vocabulary)
    legal_actions = _candidate_rows_from_state(state, extracted)
    local_ids = [row["local_action_id"] for row in legal_actions]
    view = information_state
    try:
        ordered = is_ordered_selection(view["selection_type"], view["selection_context"])
    except ValueError as exc:
        raise LocalDatasetV2Error("record has an unsupported selection schema") from exc
    selected = list(selection)
    _strict_selection(selected, field="selection", legal_ids=frozenset(local_ids), ordered=ordered, minimum=view["min_count"], maximum=view["max_count"])
    decision_id = _decision_id(view, local_ids)
    collision_sizes = sorted(count for count in Counter(row["public_action_id"] for row in legal_actions).values() if count > 1)
    record: dict[str, object] = {
        "schema_version": "specialist-local-record-v2", "record_id": _record_id(
            decision_id=decision_id, episode_id_hash=episode_id_hash, decision_index=decision_index),
        "content_hash": None, "decision_id": decision_id, "model_input_id": extracted.model_input_id,
        "episode_id_hash": episode_id_hash, "decision_index": decision_index,
        "information_state": information_state, "selection": selected, "legal_actions": legal_actions,
        "behavior": behavior, "teacher": teacher, "student": student, "source": source,
        "provenance": provenance,
        "privacy": {"classification": "local-actor-visible-v2", "export_allowed": False},
        "public_audit": {"projection_status": "duplicate-public-identity" if collision_sizes else "representable", "collision_sizes": collision_sizes, "c5_record_id": None},
        "near_duplicate_id": _near_duplicate_id(extracted),
    }
    record["content_hash"] = _record_content_hash(record)
    validate_local_record_v2(record, vocabulary=vocabulary)
    return record


def _state_payload_from_record(information_state: dict[str, Any], legal_actions: list[dict[str, Any]]) -> dict[str, object]:
    counts = Counter(row["public_action_id"] for row in legal_actions)
    return {
        "schema_version": 2, "information_view": information_state,
        "legal_actions": [{
            "binding": {
                "core": row["actor_binding"], "action_key_digest": row["action_key_digest"],
                "public_action_id": row["public_action_id"], "local_action_id": row["local_action_id"],
            },
            "action_key": {"payload": row["action_key_payload"], "digest": row["action_key_digest"]},
        } for row in legal_actions],
        "public_collision_groups": [[public_id, count] for public_id, count in sorted(counts.items()) if count > 1],
    }


def validate_local_record_v2(record: object, *, vocabulary: CardVocabularyV1) -> tuple[dict[str, object], dict[str, object]]:
    """Rebuild every local candidate against C1 before exposing serial-free features."""
    if not isinstance(vocabulary, CardVocabularyV1):
        raise LocalDatasetV2Error("vocabulary must be an explicit CardVocabularyV1")
    payload = _exact_mapping(record, field="local record", keys=_RECORD_KEYS)
    if payload["schema_version"] != "specialist-local-record-v2":
        raise LocalDatasetV2Error("local record schema_version is invalid")
    _sha256(payload["record_id"], field="record_id")
    _sha256(payload["content_hash"], field="content_hash")
    _sha256(payload["decision_id"], field="decision_id")
    _sha256(payload["model_input_id"], field="model_input_id")
    _sha256(payload["episode_id_hash"], field="episode_id_hash")
    index = _strict_nonnegative_int(payload["decision_index"], field="decision_index")
    information_state = _exact_mapping(payload["information_state"], field="information_state", keys=_INFORMATION_VIEW_KEYS)
    legal_actions_raw = payload["legal_actions"]
    if type(legal_actions_raw) is not list or len(legal_actions_raw) > MAX_LOCAL_CANDIDATES_V2:
        raise LocalDatasetV2Error("legal_actions must be a bounded list")
    legal_actions = [_exact_mapping(item, field="legal candidate", keys=_CANDIDATE_KEYS) for item in legal_actions_raw]
    local_ids = [item["local_action_id"] for item in legal_actions]
    if any(type(item) is not str or _HEX64.fullmatch(item) is None for item in local_ids) or local_ids != sorted(local_ids) or len(local_ids) != len(set(local_ids)):
        raise LocalDatasetV2Error("legal candidates must have sorted unique local_action_id values")
    try:
        rebuilt_state = deserialize_actor_visible_decision_state_v2(_state_payload_from_record(information_state, legal_actions))
    except (ActorVisibleV2Error, KeyError, TypeError, ValueError) as exc:
        raise LocalDatasetV2Error("candidate failed mandatory state-aware C1 binding rebuild") from exc
    try:
        extracted = extract_specialist_model_input_v1(rebuilt_state, vocabulary)
    except ValueError as exc:
        raise LocalDatasetV2Error("candidate failed shared feature re-extraction") from exc
    rebuilt_by_id = {action.local_action_id: action for action in rebuilt_state.legal_actions}
    if set(rebuilt_by_id) != set(local_ids):
        raise LocalDatasetV2Error("rebuilt C1 candidates differ from stored local IDs")
    for row in legal_actions:
        action = rebuilt_by_id[row["local_action_id"]]
        feature = extracted.model_input.candidate_rows[
            extracted.local_action_id_to_candidate_row_index[action.local_action_id]
        ].to_dict()
        if (
            row["action_key_digest"] != action.action_key.digest
            or row["public_action_id"] != action.public_action_id
            or row["action_key_payload"] != action.action_key.to_canonical_payload()
            or row["public_payload"] != action.action_key.to_public_trace_payload()
            or row["actor_binding"] != action.binding.core.to_identity_dict()
            or row["semantic_action"] != feature or row["features"] != feature
        ):
            raise LocalDatasetV2Error("candidate does not match state-aware rebuilt identity or features")
    view = information_state
    try:
        ordered = is_ordered_selection(view["selection_type"], view["selection_context"])
    except ValueError as exc:
        raise LocalDatasetV2Error("record selection schema is unsupported") from exc
    selected = _strict_selection(payload["selection"], field="record selection", legal_ids=frozenset(local_ids), ordered=ordered, minimum=view["min_count"], maximum=view["max_count"])
    try:
        build_specialist_step_input_v1(extracted, tuple(selected))
    except ValueError as exc:
        raise LocalDatasetV2Error("record selection is not a reachable complete action") from exc
    expected_decision = _decision_id(view, local_ids)
    if payload["decision_id"] != expected_decision:
        raise LocalDatasetV2Error("decision_id does not verify")
    if payload["model_input_id"] != extracted.model_input_id:
        raise LocalDatasetV2Error("model_input_id does not match the shared extractor")
    if payload["near_duplicate_id"] != _near_duplicate_id(extracted):
        raise LocalDatasetV2Error("near_duplicate_id does not verify")
    expected_record_id = _record_id(decision_id=expected_decision, episode_id_hash=payload["episode_id_hash"], decision_index=index)
    if payload["record_id"] != expected_record_id:
        raise LocalDatasetV2Error("record_id does not verify")
    if payload["content_hash"] != _record_content_hash(payload):
        raise LocalDatasetV2Error("content_hash does not verify")
    _validate_behavior(payload["behavior"], selection=selected, legal_ids=frozenset(local_ids), ordered=ordered, minimum=view["min_count"], maximum=view["max_count"])
    _validate_teacher(payload["teacher"], decision_id=expected_decision, model_input_id=extracted.model_input_id, legal_ids=frozenset(local_ids), ordered=ordered, selection_type=view["selection_type"], selection_context=view["selection_context"], minimum=view["min_count"], maximum=view["max_count"], extracted=extracted)
    _validate_student(payload["student"], decision_id=expected_decision, legal_ids=frozenset(local_ids), ordered=ordered, selection_type=view["selection_type"], selection_context=view["selection_context"], minimum=view["min_count"], maximum=view["max_count"], extracted=extracted)
    _validate_source(payload["source"])
    _validate_provenance(payload["provenance"])
    _validate_privacy(payload["privacy"])
    collision_sizes = sorted(count for count in Counter(row["public_action_id"] for row in legal_actions).values() if count > 1)
    _validate_public_audit(payload["public_audit"], expected_collision_sizes=collision_sizes)
    return extracted.model_input.to_dict(), {"behavior": dict(payload["behavior"]), "teacher": dict(payload["teacher"]), "student": dict(payload["student"])}


def semantic_loss_rows_from_record_v2(record: object, *, vocabulary: CardVocabularyV1) -> list[dict[str, object]]:
    """Push a legal teacher distribution over private completions to semantic step targets.

    The returned rows intentionally contain no local IDs, bindings, serials, or
    payloads: aliases are summed before the model's class-logit target is made.
    """
    validate_local_record_v2(record, vocabulary=vocabulary)
    payload = _exact_mapping(record, field="local record", keys=_RECORD_KEYS)
    teacher = payload["teacher"]
    if teacher["status"] == "unavailable":
        return []
    information_state = _exact_mapping(payload["information_state"], field="information_state", keys=_INFORMATION_VIEW_KEYS)
    legal_actions = [_exact_mapping(item, field="legal candidate", keys=_CANDIDATE_KEYS) for item in payload["legal_actions"]]
    try:
        state = deserialize_actor_visible_decision_state_v2(_state_payload_from_record(information_state, legal_actions))
        extracted = extract_specialist_model_input_v1(state, vocabulary)
    except (ActorVisibleV2Error, ValueError) as exc:  # defensive; validation above already ran.
        raise LocalDatasetV2Error("teacher loss cannot reconstruct a valid state") from exc
    raw_rows = teacher["mass_rows"]
    raw_weights = [float(row["weight"]) for row in raw_rows]
    total = math.fsum(sorted(raw_weights))
    semantic_completions: dict[tuple[bytes, ...], dict[str, Any]] = {}
    for mass_row, raw_weight in zip(raw_rows, raw_weights, strict=True):
        if raw_weight == 0:
            continue
        selected = tuple(mass_row["selection"])
        if not is_ordered_selection(
            information_state["selection_type"], information_state["selection_context"],
        ):
            selected = tuple(sorted(selected, key=lambda local_id: (
                extracted.model_input.candidate_rows[
                    extracted.local_action_id_to_candidate_row_index[local_id]
                ].canonical_bytes,
                local_id,
            )))
        semantic_selection = tuple(
            extracted.model_input.candidate_rows[
                extracted.local_action_id_to_candidate_row_index[local_id]
            ].canonical_bytes
            for local_id in selected
        )
        completion = semantic_completions.setdefault(
            semantic_selection, {"selected": selected, "parts": []},
        )
        if selected < completion["selected"]:
            completion["selected"] = selected
        completion["parts"].append(raw_weight)
    groups: dict[bytes, dict[str, Any]] = {}
    for semantic_selection in sorted(semantic_completions):
        completion = semantic_completions[semantic_selection]
        selected = completion["selected"]
        mass = math.fsum(sorted(completion["parts"])) / total
        # Each private path reaches an equivalent semantic prefix.  Its concrete
        # alias is used only to ask the shared legality primitive for a class set.
        for depth in range(len(selected) + 1):
            step = build_specialist_step_input_v1(extracted, selected[:depth])
            prefix = [row.to_dict() for row in step.semantic_prefix]
            prefix_key = canonical_json_bytes_v2(prefix)
            semantic_domain = {
                canonical_json_bytes_v2(item.semantic_row.to_dict()): item.semantic_row.to_dict()
                for item in step.allowed_semantic_classes
            }
            if not semantic_domain and step.stop_available:
                # Forced STOP has probability one and deliberately makes no loss.
                if depth != len(selected):
                    raise LocalDatasetV2Error("teacher completion continues beyond forced STOP")
                continue
            group = groups.setdefault(prefix_key, {
                "semantic_prefix": prefix,
                "semantic_domain": semantic_domain,
                "stop_available": step.stop_available,
                "masses": {},
            })
            if (
                group["semantic_domain"] != semantic_domain
                or group["stop_available"] is not step.stop_available
            ):
                raise LocalDatasetV2Error("semantic prefix has an inconsistent legal token domain")
            for token_key, semantic in semantic_domain.items():
                group["masses"].setdefault(
                    ("semantic", token_key),
                    {"kind": "semantic", "semantic_action": semantic, "parts": []},
                )
            if step.stop_available:
                group["masses"].setdefault(("stop", b""), {"kind": "stop", "parts": []})
            if depth == len(selected):
                if not step.stop_available:
                    raise LocalDatasetV2Error("teacher completion cannot legally STOP")
                mass_key = ("stop", b"")
            else:
                local_id = selected[depth]
                semantic = extracted.model_input.candidate_rows[
                    extracted.local_action_id_to_candidate_row_index[local_id]
                ].to_dict()
                token_key = canonical_json_bytes_v2(semantic)
                mass_key = ("semantic", token_key)
                if token_key not in semantic_domain:
                    raise LocalDatasetV2Error("teacher completion has an unreachable semantic next token")
            group["masses"][mass_key]["parts"].append(mass)
    output: list[dict[str, object]] = []
    for key in sorted(groups):
        group = groups[key]
        absolute_masses = {
            token_key: math.fsum(sorted(item["parts"]))
            for token_key, item in group["masses"].items()
        }
        reach = math.fsum(sorted(absolute_masses.values()))
        if reach <= 0:
            continue
        ordered_keys = [
            ("semantic", token_key) for token_key in sorted(group["semantic_domain"])
        ]
        if group["stop_available"]:
            ordered_keys.append(("stop", b""))
        tokens = [{
            **{key: value for key, value in group["masses"][token_key].items() if key != "parts"},
            "mass": absolute_masses[token_key] / reach,
        } for token_key in ordered_keys]
        output.append({
            "semantic_prefix": group["semantic_prefix"], "token_masses": tokens,
            "reach_mass": reach,
        })
    return output


def _manifest_id(manifest: Mapping[str, Any]) -> str:
    identity = dict(manifest)
    identity.pop("manifest_id", None)
    identity.pop("content_hash", None)
    return _hash("mage_ptcg:specialist-dataset-manifest:v2", identity)


def _manifest_content_hash(manifest: Mapping[str, Any]) -> str:
    content = dict(manifest)
    content.pop("content_hash", None)
    return _hash("mage_ptcg:specialist-dataset-manifest-content:v2", content)


def _basic_record_content_hash(record: object) -> str:
    payload = _exact_mapping(record, field="local record", keys=_RECORD_KEYS)
    _sha256(payload["content_hash"], field="record content_hash")
    expected = _record_content_hash(payload)
    if payload["content_hash"] != expected:
        raise LocalDatasetV2Error("record content_hash does not verify")
    return expected


def _validate_manifest(value: object) -> dict[str, Any]:
    manifest = _exact_mapping(value, field="local dataset manifest", keys=_MANIFEST_KEYS)
    if manifest["schema_version"] != "specialist-local-dataset-manifest-v2":
        raise LocalDatasetV2Error("local dataset manifest schema_version is invalid")
    _sha256(manifest["manifest_id"], field="manifest_id")
    _sha256(manifest["content_hash"], field="manifest content_hash")
    if manifest["record_schema_version"] != "specialist-local-record-v2" or manifest["record_content_hash_domain"] != "mage_ptcg:specialist-record-content:v2":
        raise LocalDatasetV2Error("manifest record contract is incompatible")
    if manifest["c1_schema_version"] != C1_V2_SCHEMA_VERSION or manifest["action_key_schema_version"] != ACTION_KEY_SCHEMA_VERSION:
        raise LocalDatasetV2Error("manifest C1 or ActionKey version is incompatible")
    if (
        manifest["feature_domain"] != ACTOR_VISIBLE_FEATURE_DOMAIN_V1
        or manifest["feature_schema_hash"] != FEATURE_SCHEMA_HASH_V1
        or manifest["feature_dimension"] != len(STATE_SCALAR_NAMES_V1)
    ):
        raise LocalDatasetV2Error("manifest feature contract is incompatible")
    _bounded_string(manifest["environment_version"], field="manifest environment_version")
    _sha256(manifest["deck_fingerprint"], field="manifest deck_fingerprint")
    sources = manifest["source_artifacts"]
    if type(sources) is not list or len(sources) > MAX_COMPLETE_ACTION_ROWS_V2:
        raise LocalDatasetV2Error("manifest source_artifacts has an invalid bounded length")
    normalized_sources: list[dict[str, Any]] = []
    for item in sources:
        entry = _exact_mapping(item, field="manifest source artifact", keys=_MANIFEST_SOURCE_KEYS)
        _bounded_string(entry["kind"], field="manifest source artifact kind")
        _sha256(entry["artifact_sha256"], field="manifest source artifact hash")
        normalized_sources.append(entry)
    if sources != sorted(normalized_sources, key=lambda item: (item["kind"], item["artifact_sha256"])) or len({(item["kind"], item["artifact_sha256"]) for item in normalized_sources}) != len(normalized_sources):
        raise LocalDatasetV2Error("manifest source_artifacts must be sorted unique")
    refs = manifest["permission_references"]
    if type(refs) is not list or len(refs) > MAX_COMPLETE_ACTION_ROWS_V2:
        raise LocalDatasetV2Error("manifest permission_references has an invalid bounded length")
    normalized_refs: list[dict[str, Any]] = []
    for item in refs:
        entry = _exact_mapping(item, field="manifest permission reference", keys=_MANIFEST_PERMISSION_KEYS)
        for key in _MANIFEST_PERMISSION_KEYS:
            _sha256(entry[key], field=f"manifest permission reference {key}")
        normalized_refs.append(entry)
    if refs != sorted(normalized_refs, key=lambda item: item["permission_manifest_id"]) or len({item["permission_manifest_id"] for item in normalized_refs}) != len(normalized_refs):
        raise LocalDatasetV2Error("manifest permission_references must be sorted unique")
    usages = manifest["usage_rights"]
    if type(usages) is not list or not usages or any(type(item) is not str or item not in {"audit-local", "training-local"} for item in usages) or usages != sorted(set(usages)):
        raise LocalDatasetV2Error("manifest usage_rights must be sorted local-only values")
    if manifest["export_allowed"] is not False:
        raise LocalDatasetV2Error("local dataset manifest must prohibit export")
    count = _strict_nonnegative_int(manifest["record_count"], field="manifest record_count")
    hashes = manifest["record_content_hashes"]
    if type(hashes) is not list or len(hashes) != count or len(hashes) > MAX_COMPLETE_ACTION_ROWS_V2:
        raise LocalDatasetV2Error("manifest record_content_hashes has an invalid length")
    if any(type(item) is not str or _HEX64.fullmatch(item) is None for item in hashes) or hashes != sorted(hashes) or len(set(hashes)) != len(hashes):
        raise LocalDatasetV2Error("manifest record_content_hashes must be sorted unique hashes")
    if manifest["manifest_id"] != _manifest_id(manifest):
        raise LocalDatasetV2Error("manifest_id does not verify")
    if manifest["content_hash"] != _manifest_content_hash(manifest):
        raise LocalDatasetV2Error("manifest content_hash does not verify")
    return manifest


def build_local_dataset_manifest_v2(
    *, records: tuple[dict[str, object], ...], environment_version: str,
    deck_fingerprint: str, trusted_permissions: Mapping[str, TrustedPermissionV1],
) -> dict[str, object]:
    """Build a local-only manifest that cross-references out-of-band permissions."""
    if type(records) is not tuple:
        raise LocalDatasetV2Error("manifest records/trusted permissions have invalid container types")
    return build_local_dataset_manifest_streaming_v2(
        records=records, environment_version=environment_version,
        deck_fingerprint=deck_fingerprint, trusted_permissions=trusted_permissions,
    )


def build_local_dataset_manifest_streaming_v2(
    *, records: Iterable[Mapping[str, object]], environment_version: str,
    deck_fingerprint: str, trusted_permissions: Mapping[str, TrustedPermissionV1],
) -> dict[str, object]:
    """Same manifest as :func:`build_local_dataset_manifest_v2`, from a stream.

    The tuple form requires the whole corpus resident, which a teacher corpus
    cannot afford.  Measured on ``t1-alakazam``: 271,100 records occupy 8.6 GiB
    as JSONL but **35.4 GB as parsed dicts** (3.7x), so sealing it died in the
    caller's prefetch long before any bounded stage ran.

    Only three things about a record survive into the manifest -- its content
    hash, its source identity, and its permission id -- so one pass that keeps
    just those is enough.  Memory becomes proportional to the hash list (about
    24 MB for that corpus) instead of to the corpus.

    The result is byte-identical to the tuple form for the same records; that
    form now delegates here, so the two cannot drift apart.
    """
    if not isinstance(trusted_permissions, Mapping):
        raise LocalDatasetV2Error("manifest records/trusted permissions have invalid container types")
    hashes: list[str] = []
    seen_hashes: set[str] = set()
    sources: dict[tuple[str, str], dict[str, str]] = {}
    permission_ids: set[str] = set()
    for record in records:
        content_hash = _basic_record_content_hash(record)
        if content_hash in seen_hashes:
            raise LocalDatasetV2Error("dataset cannot contain duplicate record content hashes")
        seen_hashes.add(content_hash)
        hashes.append(content_hash)
        payload = _exact_mapping(record, field="local record", keys=_RECORD_KEYS)
        source = _validate_source(payload["source"])
        sources[(source["kind"], source["artifact_sha256"])] = {
            "kind": source["kind"], "artifact_sha256": source["artifact_sha256"],
        }
        if source["permission_manifest_id"] is not None:
            permission_ids.add(source["permission_manifest_id"])
    refs: list[dict[str, str]] = []
    for permission_id in sorted(permission_ids):
        entry = trusted_permissions.get(permission_id)
        if type(entry) is not TrustedPermissionV1:
            raise LocalDatasetV2Error("manifest cannot reference an untrusted permission")
        _reparse_trusted_permission_v1(entry, permission_id=permission_id)
        refs.append({
            "permission_manifest_id": permission_id, "permission_content_hash": entry.content_hash,
            "trusted_bytes_sha256": entry.raw_sha256,
        })
    manifest: dict[str, object] = {
        "schema_version": "specialist-local-dataset-manifest-v2", "manifest_id": None,
        "content_hash": None, "record_schema_version": "specialist-local-record-v2",
        "record_content_hash_domain": "mage_ptcg:specialist-record-content:v2",
        "c1_schema_version": C1_V2_SCHEMA_VERSION, "action_key_schema_version": ACTION_KEY_SCHEMA_VERSION,
        "feature_domain": ACTOR_VISIBLE_FEATURE_DOMAIN_V1, "feature_schema_hash": FEATURE_SCHEMA_HASH_V1,
        "feature_dimension": len(STATE_SCALAR_NAMES_V1), "environment_version": environment_version,
        "deck_fingerprint": deck_fingerprint,
        "source_artifacts": sorted(sources.values(), key=lambda item: (item["kind"], item["artifact_sha256"])),
        "permission_references": refs, "usage_rights": ["audit-local", "training-local"],
        "export_allowed": False, "record_count": len(hashes), "record_content_hashes": sorted(hashes),
    }
    manifest["manifest_id"] = _manifest_id(manifest)
    manifest["content_hash"] = _manifest_content_hash(manifest)
    return _validate_manifest(manifest)


def _verify_manifest_trust(manifest: Mapping[str, Any], trusted_permissions: Mapping[str, TrustedPermissionV1]) -> dict[str, dict[str, object]]:
    if not isinstance(trusted_permissions, Mapping):
        raise LocalDatasetV2Error("trusted permissions must be supplied separately from the dataset")
    verified: dict[str, dict[str, object]] = {}
    for reference in manifest["permission_references"]:
        permission_id = reference["permission_manifest_id"]
        entry = trusted_permissions.get(permission_id)
        if type(entry) is not TrustedPermissionV1:
            raise LocalDatasetV2Error("dataset permission reference is untrusted")
        if entry.permission_manifest_id != permission_id or entry.content_hash != reference["permission_content_hash"] or entry.raw_sha256 != reference["trusted_bytes_sha256"]:
            raise LocalDatasetV2Error("dataset permission reference does not match trusted exact bytes")
        verified[permission_id] = _reparse_trusted_permission_v1(entry, permission_id=permission_id)
    return verified


def _qualified_for_training(
    source_value: object, *, permissions: Mapping[str, dict[str, object]],
    qualification_time_utc: str,
) -> bool:
    source = _validate_source(source_value)
    if source["synthetic"]:
        if source["training_eligible"] or source["usage_class"] != "audit_only_unqualified":
            raise LocalDatasetV2Error("synthetic source cannot be qualified for training")
        return False
    permission_id = source["permission_manifest_id"]
    if permission_id is None:
        if source["training_eligible"] or source["usage_class"] != "audit_only_unqualified":
            raise LocalDatasetV2Error("nonsynthetic training claim lacks a permission")
        return False
    permission = permissions.get(permission_id)
    if permission is None:
        raise LocalDatasetV2Error("record refers to an untrusted dataset permission")
    if permission["artifact_sha256"] != source["artifact_sha256"] or permission["source_kind"] != source["kind"]:
        raise LocalDatasetV2Error("permission does not bind the record source artifact/kind")
    if "training-local" not in permission["allowed_usages"]:
        raise LocalDatasetV2Error("permission does not authorize training-local")
    start = permission["valid_from_utc"]
    end = permission["expires_at_utc"]
    if (start is not None and qualification_time_utc < start) or (end is not None and qualification_time_utc >= end):
        raise LocalDatasetV2Error("permission is not live at the requested qualification time")
    if not source["training_eligible"] or source["usage_class"] != "qualified_training" or source["synthetic_fields"]:
        raise LocalDatasetV2Error("record source eligibility projection disagrees with trusted permission")
    return True


def require_qualified_training_record_v2(
    record: object, *, vocabulary: CardVocabularyV1,
    trusted_permissions: Mapping[str, TrustedPermissionV1], qualification_time_utc: str,
) -> tuple[dict[str, object], dict[str, object]]:
    """Validate a local record and its out-of-band training authority together.

    Gate runners use this public narrow wrapper instead of reimplementing the
    synthetic/source/permission/time checks around the private dataset helper.
    """
    model_payload, labels = validate_local_record_v2(record, vocabulary=vocabulary)
    payload = _exact_mapping(record, field="local record", keys=_RECORD_KEYS)
    permissions: dict[str, dict[str, object]] = {}
    for permission_id, entry in trusted_permissions.items():
        permissions[permission_id] = _reparse_trusted_permission_v1(entry, permission_id=permission_id)
    if not _qualified_for_training(payload["source"], permissions=permissions, qualification_time_utc=qualification_time_utc):
        raise LocalDatasetV2Error("record is not qualified for local training")
    if labels["teacher"]["status"] != "available":
        raise LocalDatasetV2Error("record lacks an available teacher target")
    return model_payload, labels


def atomic_write_local_dataset_v2(path: str | Path, *, records: tuple[dict[str, object], ...], manifest: dict[str, object]) -> None:
    """Atomically replace one canonical JSONL local dataset only after manifest checks."""
    manifest_data = _validate_manifest(manifest)
    hashes = [_basic_record_content_hash(record) for record in records]
    if len(records) != manifest_data["record_count"] or sorted(hashes) != manifest_data["record_content_hashes"]:
        raise LocalDatasetV2Error("records do not match the manifest count/content hashes")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor: int | None = None
    temporary: Path | None = None
    try:
        # O_EXCL creates a private inode rather than following/reusing a
        # same-PID filename (or a pre-created symlink) under concurrent writers.
        for nonce in range(1024):
            candidate = target.with_name(f".{target.name}.tmp-{os.getpid()}-{nonce}")
            try:
                descriptor = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                continue
            temporary = candidate
            break
        if descriptor is None or temporary is None:
            raise LocalDatasetV2Error("could not reserve a unique atomic dataset temporary file")
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = None
            for record in records:
                handle.write(canonical_json_bytes_v2(record))
                handle.write(b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        try:
            directory_fd = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            # Some supported filesystems do not allow directory fsync.  The
            # file bytes were still synced before the atomic replacement.
            pass
    except Exception:
        if descriptor is not None:
            os.close(descriptor)
        if temporary is not None and temporary.exists():
            temporary.unlink()
        raise


def _validate_dataset_stream_v2(
    handle: Any, *, manifest_data: Mapping[str, Any], vocabulary: CardVocabularyV1,
    permissions: Mapping[str, dict[str, object]], qualification_time_utc: str, spool: Any,
) -> tuple[int, str, int]:
    """Validate a complete stream and seal trainable outputs to an unlinked spool."""
    expected_sources = {(item["kind"], item["artifact_sha256"]) for item in manifest_data["source_artifacts"]}
    expected_permissions = {item["permission_manifest_id"] for item in manifest_data["permission_references"]}
    seen_hashes: list[str] = []
    seen_sources: set[tuple[str, str]] = set()
    seen_permissions: set[str] = set()
    spool_bytes = 0
    source_bytes = 0
    snapshot_digest = hashlib.sha256()
    for raw_line in handle:
        snapshot_digest.update(raw_line)
        source_bytes += len(raw_line)
        if not raw_line.endswith(b"\n") or raw_line == b"\n":
            raise LocalDatasetV2Error("dataset JSONL contains an invalid blank or unterminated line")
        record = parse_canonical_json_bytes_v2(raw_line[:-1])
        model_input, labels = validate_local_record_v2(record, vocabulary=vocabulary)
        payload = _exact_mapping(record, field="local record", keys=_RECORD_KEYS)
        seen_hashes.append(payload["content_hash"])
        source = _validate_source(payload["source"])
        seen_sources.add((source["kind"], source["artifact_sha256"]))
        if source["permission_manifest_id"] is not None:
            seen_permissions.add(source["permission_manifest_id"])
        qualified = _qualified_for_training(source, permissions=permissions, qualification_time_utc=qualification_time_utc)
        if qualified and labels["teacher"]["status"] == "available":
            loss_rows = semantic_loss_rows_from_record_v2(record, vocabulary=vocabulary)
            if loss_rows:
                example = {
                    "model_input": model_input, "loss_rows": loss_rows,
                    "value_target": labels["teacher"]["value_target"],
                }
                encoded = canonical_json_bytes_v2(example)
                spool_bytes += len(encoded) + 1
                if spool_bytes > MAX_TRAINING_SPOOL_BYTES_V2:
                    raise LocalDatasetV2Error("sealed training spool exceeds its bounded byte limit")
                spool.write(encoded)
                spool.write(b"\n")
    if len(seen_hashes) != manifest_data["record_count"] or sorted(seen_hashes) != manifest_data["record_content_hashes"]:
        raise LocalDatasetV2Error("dataset stream does not match manifest record hashes")
    if seen_sources != expected_sources or seen_permissions != expected_permissions:
        raise LocalDatasetV2Error("dataset stream source/permission references do not match manifest")
    return spool_bytes, snapshot_digest.hexdigest(), source_bytes


def _open_handle_sha256(handle: Any, *, expected_bytes: int) -> str:
    """Hash an already-open source descriptor without trusting its pathname."""
    if type(expected_bytes) is not int or expected_bytes < 0:
        raise LocalDatasetV2Error("validated source snapshot byte count is invalid")
    handle.seek(0)
    digest = hashlib.sha256()
    remaining = expected_bytes
    while remaining:
        chunk = handle.read(min(1024 * 1024, remaining))
        if not chunk:
            raise LocalDatasetV2Error("source snapshot was truncated after validation")
        digest.update(chunk)
        remaining -= len(chunk)
    if handle.read(1):
        raise LocalDatasetV2Error("source snapshot changed after the sealed validation phase")
    return digest.hexdigest()


def iter_training_examples_v2(
    path: str | Path, *, manifest: dict[str, object], vocabulary: CardVocabularyV1,
    trusted_permissions: Mapping[str, TrustedPermissionV1], qualification_time_utc: str,
):
    """Project sealed L1A envelopes to the legacy exact three-key training shape."""
    from mage_ptcg.meta_specialist.training_example_envelope_v2 import (
        iter_training_example_envelopes_v2,
    )

    for envelope in iter_training_example_envelopes_v2(
        path, manifest=manifest, vocabulary=vocabulary,
        trusted_permissions=trusted_permissions, qualification_time_utc=qualification_time_utc,
    ):
        yield envelope.training_example()


def near_duplicate_ubiquity_threshold_v2(episode_count: int) -> int:
    """How many distinct episodes a position must span before it stops being a leak.

    Scaled to the corpus, with an absolute floor so that a handful of episodes can
    never trip the rule: on a three-episode fixture every recurring position would
    otherwise count as ubiquitous and grouping would stop working entirely.
    """
    if type(episode_count) is not int or episode_count < 0:
        raise LocalDatasetV2Error("episode_count must be a nonnegative int")
    return max(
        _UBIQUITY_MIN_EPISODES_V2,
        math.ceil(_UBIQUITY_EPISODE_FRACTION_V2 * episode_count),
    )


def ubiquitous_near_duplicate_ids_v2(
    grouping_keys: tuple[tuple[str, str, str], ...],
) -> frozenset[str]:
    """Near-duplicate keys that recur so widely they cannot leak an episode.

    A near-duplicate key exists to keep one *position* out of two splits, so a
    test score cannot be earned by memorising a position the training split
    already contained.  A position that recurs across a large fraction of the
    episodes is not that: the opening decision is byte-identical in every game of
    a lane, so holding it out measures nothing.  It is a constant of the task
    rather than something a particular episode reveals.

    Using such a key as a union edge is actively harmful.  It transitively merges
    every episode containing it into one component, and components are assigned
    to splits as units.  Measured on two 300-game teacher corpora, the opening
    decision alone merged 50.8% of all examples into a single component; that
    component landed in ``train`` for one lane (68% train) and in ``development``
    for another (15% train), making the usable training-set size a one-in-three
    lottery rather than a property of the data.
    """
    if type(grouping_keys) is not tuple:
        raise LocalDatasetV2Error("grouping keys must be an immutable tuple")
    episodes_by_key: dict[str, set[str]] = {}
    episodes: set[str] = set()
    for key in grouping_keys:
        if type(key) is not tuple or len(key) != 3:
            raise LocalDatasetV2Error("grouping key must be a record/episode/near-duplicate triple")
        _record_id, episode, near_duplicate = key
        episodes_by_key.setdefault(near_duplicate, set()).add(episode)
        episodes.add(episode)
    threshold = near_duplicate_ubiquity_threshold_v2(len(episodes))
    return frozenset(
        near_duplicate
        for near_duplicate, seen in episodes_by_key.items()
        if len(seen) >= threshold
    )


def _split_bucket_bounds_v2(split_weights: tuple[float, ...] | None, count: int) -> tuple[float, ...]:
    """Cumulative upper bounds in ``[0,1]``, one per split, from relative weights."""
    if split_weights is None:
        return tuple((index + 1) / count for index in range(count))
    if type(split_weights) is not tuple or len(split_weights) != count:
        raise LocalDatasetV2Error("split_weights must give one weight per split name")
    if any(type(weight) is not float or not math.isfinite(weight) or weight <= 0.0 for weight in split_weights):
        raise LocalDatasetV2Error("every split weight must be a finite positive float")
    total = math.fsum(split_weights)
    bounds: list[float] = []
    running = 0.0
    for weight in split_weights:
        running += weight
        bounds.append(running / total)
    bounds[-1] = 1.0
    return tuple(bounds)


def assign_grouped_splits_from_keys_v2(
    grouping_keys: tuple[tuple[str, str, str], ...],
    *,
    split_names: tuple[str, ...],
    split_weights: tuple[float, ...] | None = None,
) -> dict[str, str]:
    """Assign connected episode/near-duplicate components to one deterministic split.

    Each key is ``(record_id, episode_id_hash, near_duplicate_id)``.  Callers that
    hold raw records use :func:`assign_grouped_splits_v2`; callers that only hold
    sealed envelopes pass the same three opaque hashes directly, so both paths
    produce identical components from one implementation.

    Near-duplicate keys classified as ubiquitous by
    :func:`ubiquitous_near_duplicate_ids_v2` contribute no union edges; their
    records are grouped by episode alone.  See that function for why linking on
    them destroys the split.

    ``split_weights`` gives the relative share of *components* each split
    receives; ``None`` divides them equally.  Components are whole episodes here,
    so with a few hundred of them the record counts track the weights closely
    without any component ever being cut in half.
    """
    if type(grouping_keys) is not tuple or type(split_names) is not tuple or len(split_names) < 2:
        raise LocalDatasetV2Error("split assignment needs immutable records and at least two split names")
    if any(type(name) is not str or not name for name in split_names) or len(set(split_names)) != len(split_names):
        raise LocalDatasetV2Error("split names must be unique nonempty strings")
    bounds = _split_bucket_bounds_v2(split_weights, len(split_names))
    ubiquitous = ubiquitous_near_duplicate_ids_v2(grouping_keys)
    parent: dict[str, str] = {}

    def find(item: str) -> str:
        root = item
        while parent[root] != root:
            root = parent[root]
        while parent[item] != item:
            item, parent[item] = parent[item], root
        return root

    def union(left: str, right: str) -> None:
        root_left, root_right = find(left), find(right)
        if root_left != root_right:
            parent[max(root_left, root_right)] = min(root_left, root_right)

    by_episode: dict[str, str] = {}
    by_near_duplicate: dict[str, str] = {}
    for key in grouping_keys:
        if type(key) is not tuple or len(key) != 3:
            raise LocalDatasetV2Error("grouping key must be a record/episode/near-duplicate triple")
        record_id, episode, near_duplicate = key
        _sha256(record_id, field="record_id")
        _sha256(episode, field="episode_id_hash")
        _sha256(near_duplicate, field="near_duplicate_id")
        if record_id in parent:
            raise LocalDatasetV2Error("split assignment received duplicate record_id")
        parent[record_id] = record_id
        if episode in by_episode:
            union(record_id, by_episode[episode])
        else:
            by_episode[episode] = record_id
        if near_duplicate in ubiquitous:
            continue
        if near_duplicate in by_near_duplicate:
            union(record_id, by_near_duplicate[near_duplicate])
        else:
            by_near_duplicate[near_duplicate] = record_id
    roots: dict[str, list[str]] = {}
    for record_id in parent:
        roots.setdefault(find(record_id), []).append(record_id)
    result: dict[str, str] = {}
    for members in roots.values():
        component_key = canonical_json_bytes_v2(sorted(members))
        digest = int.from_bytes(
            hashlib.sha256(b"mage_ptcg:specialist-grouped-split:v1\0" + component_key).digest()[:8],
            "big",
        )
        position = digest / 2 ** 64
        bucket = next(index for index, bound in enumerate(bounds) if position < bound)
        for record_id in members:
            result[record_id] = split_names[bucket]
    return result


def assign_grouped_splits_v2(
    records: tuple[dict[str, object], ...],
    *,
    split_names: tuple[str, ...],
    split_weights: tuple[float, ...] | None = None,
) -> dict[str, str]:
    """Assign raw records to leakage-safe grouped splits.

    This is intentionally a local planning helper.  The grouping identity uses
    only sealed record IDs plus the already serial-free near-duplicate core.
    """
    if type(records) is not tuple:
        raise LocalDatasetV2Error("split assignment needs immutable records and at least two split names")
    keys: list[tuple[str, str, str]] = []
    for record in records:
        payload = _exact_mapping(record, field="local record", keys=_RECORD_KEYS)
        keys.append(
            (payload["record_id"], payload["episode_id_hash"], payload["near_duplicate_id"])
        )
    return assign_grouped_splits_from_keys_v2(
        tuple(keys), split_names=split_names, split_weights=split_weights
    )


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise LocalDatasetV2Error("JSON object contains a duplicate key")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> None:
    raise LocalDatasetV2Error(f"JSON contains non-finite number {value!r}")


def _validate_json_bounds(value: object, *, max_nodes: int = MAX_CANONICAL_JSON_NODES_V2) -> None:
    """Bound the accepted JSON tree before it reaches a canonical hash.

    ``max_nodes`` defaults to the tight untrusted-dataset bound so every
    existing caller keeps today's limit unchanged. A caller that hashes a
    structurally different, trusted aggregate (see ``canonical_json_bytes_v2``)
    may pass a larger explicit bound; the depth and per-container limits stay
    fixed for every caller.
    """
    pending: list[tuple[object, int]] = [(value, 0)]
    nodes = 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if nodes > max_nodes:
            raise LocalDatasetV2Error("canonical JSON exceeds the node limit")
        if depth > MAX_CANONICAL_JSON_DEPTH_V2:
            raise LocalDatasetV2Error("canonical JSON exceeds the depth limit")
        if isinstance(item, Mapping):
            if len(item) > MAX_CANONICAL_JSON_CONTAINER_ITEMS_V2:
                raise LocalDatasetV2Error("canonical JSON object exceeds the container limit")
            for key, child in item.items():
                if type(key) is not str:
                    raise LocalDatasetV2Error("canonical JSON object keys must be strings")
                pending.append((child, depth + 1))
        elif type(item) in {list, tuple}:
            if len(item) > MAX_CANONICAL_JSON_CONTAINER_ITEMS_V2:
                raise LocalDatasetV2Error("canonical JSON array exceeds the container limit")
            pending.extend((child, depth + 1) for child in item)
        elif item is None or type(item) in {bool, int, str}:
            continue
        elif type(item) is float:
            if not math.isfinite(item):
                raise LocalDatasetV2Error("canonical JSON contains a non-finite number")
        else:
            raise LocalDatasetV2Error("canonical JSON contains an unsupported scalar")


def canonical_json_bytes_v2(
    value: object, *, max_nodes: int = MAX_CANONICAL_JSON_NODES_V2,
    max_bytes: int = MAX_LOCAL_RECORD_BYTES_V2,
) -> bytes:
    """Encode exactly one finite, deterministic JSON value for hashing/storage.

    ``max_nodes``/``max_bytes`` default to the tight untrusted single-decision
    bounds, so every existing caller (including every untrusted-dataset path
    in this module) is unaffected. A caller that hashes a legitimately larger,
    trusted aggregate -- e.g. a whole-game record spanning dozens of
    transitions -- may pass explicit, still-finite bounds sized for that
    aggregate; this never loosens the default used by the untrusted path.
    """
    _validate_json_bounds(value, max_nodes=max_nodes)
    try:
        raw = json.dumps(
            value, ensure_ascii=False, allow_nan=False, sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise LocalDatasetV2Error("value is not finite canonical JSON") from exc
    if len(raw) > max_bytes:
        raise LocalDatasetV2Error(f"canonical JSON exceeds the {max_bytes}-byte cap")
    return raw


def parse_canonical_json_bytes_v2(
    raw: object, *, max_nodes: int = MAX_CANONICAL_JSON_NODES_V2,
    max_bytes: int = MAX_LOCAL_RECORD_BYTES_V2,
) -> object:
    """Parse only bytes already in canonical finite JSON form.

    See ``canonical_json_bytes_v2`` for the ``max_nodes``/``max_bytes``
    contract: both default to the tight untrusted bound and must match
    whatever bound the original ``canonical_json_bytes_v2`` call used, or the
    canonical round-trip check below will spuriously reject valid bytes.
    """
    if not isinstance(raw, bytes) or len(raw) > max_bytes:
        raise LocalDatasetV2Error("canonical JSON bytes are invalid or exceed the cap")
    try:
        value = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, LocalDatasetV2Error) as exc:
        raise LocalDatasetV2Error("canonical JSON bytes are malformed") from exc
    _validate_json_bounds(value, max_nodes=max_nodes)
    if canonical_json_bytes_v2(value, max_nodes=max_nodes, max_bytes=max_bytes) != raw:
        raise LocalDatasetV2Error("JSON bytes are not in canonical form")
    return value


__all__ = [
    "LocalDatasetV2Error", "MAX_CANONICAL_JSON_CONTAINER_ITEMS_V2",
    "MAX_CANONICAL_JSON_DEPTH_V2", "MAX_CANONICAL_JSON_NODES_V2",
    "MAX_COMPLETE_ACTION_ROWS_V2", "MAX_LOCAL_CANDIDATES_V2", "MAX_LOCAL_RECORD_BYTES_V2",
    "MAX_TRAINING_SPOOL_BYTES_V2",
    "TrustedPermissionV1", "assign_grouped_splits_from_keys_v2", "assign_grouped_splits_v2", "require_qualified_training_record_v2",
    "near_duplicate_ubiquity_threshold_v2", "ubiquitous_near_duplicate_ids_v2",
    "atomic_write_local_dataset_v2",
    "build_local_dataset_manifest_v2", "build_local_dataset_manifest_streaming_v2",
    "build_local_record_v2", "build_trusted_permission_set_v1",
    "canonical_json_bytes_v2", "derive_complete_action_id_v1", "iter_training_examples_v2",
    "make_source_permission_manifest_v1", "parse_canonical_json_bytes_v2",
    "semantic_loss_rows_from_record_v2", "validate_local_record_v2",
    "validate_source_permission_manifest_v1",
]
