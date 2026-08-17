"""Deterministic, quota-bounded C5 targeted sample selection."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import math
from typing import Any, Iterable

from .contracts import DecisionDatasetError, digest, near_duplicate_key, validate_records
from mage_ptcg.meta_specialist.cabt_json_contract_v1 import is_ordered_selection


SELECTION_POLICY_VERSION = "targeted-selection-v2"


@dataclass(frozen=True, slots=True)
class SelectionConfig:
    limit: int
    max_per_episode: int = 2
    max_per_near_duplicate: int = 1

    def __post_init__(self) -> None:
        if any(type(item) is not int or item < 1 for item in (self.limit, self.max_per_episode, self.max_per_near_duplicate)):
            raise ValueError("selection limits must be positive integers")


def _student_components(record: dict[str, object]) -> tuple[list[str], dict[str, float]]:
    student = record.get("student")
    selection = record["selection"]  # type: ignore[index]
    ordered = is_ordered_selection(selection["type"], selection["context"])
    rule_ids = list(record["rule_v0"]["selected_action_ids"])  # type: ignore[index]
    if not isinstance(student, dict):
        return [], {}
    selected = list(student.get("selected_action_ids", []))
    scores = student.get("scores", {})
    result: dict[str, float] = {}
    reasons: list[str] = []
    disagreement = (
        tuple(selected) != tuple(rule_ids)
        if ordered
        else set(selected) != set(rule_ids)
    )
    if student.get("fallback_reason") is None and disagreement:
        reasons.append("rule_student_disagreement")
        result["rule_student_disagreement"] = 1.0
    if student.get("fallback_reason") is not None:
        reasons.append("student_fallback")
        result["student_fallback"] = 1.0
    if isinstance(scores, dict):
        finite = sorted((float(value) for value in scores.values() if isinstance(value, (int, float)) and math.isfinite(float(value))), reverse=True)
        if len(finite) > 1:
            margin = finite[0] - finite[1]
            result["student_low_margin"] = 1.0 / (1.0 + max(margin, 0.0))
            if result["student_low_margin"] >= 0.5:
                reasons.append("student_low_margin")
    return reasons, result


def _source_dataset_hash(records: list[dict[str, object]]) -> str:
    return digest([
        {"record_id": str(record["record_id"]), "content_hash": str(record["content_hash"])}
        for record in sorted(records, key=lambda item: str(item["record_id"]))
    ], domain="selection-input")


def _selection_hash(*, source_dataset_hash: str, config: dict[str, int], selected: list[dict[str, object]]) -> str:
    return digest({
        "schema_version": SELECTION_POLICY_VERSION,
        "source_dataset_hash": source_dataset_hash,
        "config": config,
        "selected": selected,
    }, domain="selection-output")


def select_targeted(records: Iterable[dict[str, object]], config: SelectionConfig) -> dict[str, object]:
    values = list(records)
    validate_records(values)
    type_counts = Counter(str(item["selection"].get("type")) for item in values)  # type: ignore[index]
    family_counts = Counter(
        str(candidate["features"].get("action_family"))
        for item in values for candidate in item["legal_actions"]  # type: ignore[index]
    )
    scored: list[dict[str, object]] = []
    for record in values:
        reasons, components = _student_components(record)
        selection = record["selection"]  # validated by validate_records above
        rule = record["rule_v0"]  # validated by validate_records above
        if not isinstance(selection, dict) or not isinstance(rule, dict):
            raise DecisionDatasetError("validated record selection/rule_v0 is unavailable")
        try:
            ordered = is_ordered_selection(selection.get("type"), selection.get("context"))
        except ValueError as exc:
            raise DecisionDatasetError("record has an unknown CABT selection schema") from exc
        rule_value = rule.get("selected_action_ids")
        if not isinstance(rule_value, list):
            raise DecisionDatasetError("validated record rule_v0 selection is unavailable")
        rule_ids = rule_value
        selection_type = str(selection.get("type"))
        rare_type = 1.0 / type_counts[selection_type]
        components["rare_selection_type"] = rare_type
        if rare_type >= 0.5:
            reasons.append("rare_selection_type")
        families = [str(item["features"].get("action_family")) for item in record["legal_actions"]]  # type: ignore[index]
        rare_family = max((1.0 / family_counts[family] for family in families), default=0.0)
        components["rare_action_family"] = rare_family
        ranking = record["rule_v0"].get("ranking", [])  # type: ignore[index]
        scores = sorted((int(item["score"]) for item in ranking if isinstance(item, dict) and type(item.get("score")) is int), reverse=True)
        if len(scores) > 1:
            components["rule_close_ranking"] = 1.0 / (1.0 + max(0, scores[0] - scores[1]))
            if components["rule_close_ranking"] >= 0.5:
                reasons.append("rule_close_ranking")
        c3 = record.get("c3")
        c3_selected = c3.get("selected_action_ids", []) if isinstance(c3, dict) else []
        c3_disagrees = (
            tuple(c3_selected) != tuple(rule_ids)
            if ordered
            else set(c3_selected) != set(rule_ids)
        )
        if isinstance(c3, dict) and c3.get("evidence_status") == "actual-cabt" and c3_disagrees:
            reasons.append("c3_rule_disagreement")
            components["c3_rule_disagreement"] = 1.0
        if not reasons:
            reasons.append("diversity_backfill")
        priority = sum(components.values())
        scored.append({
            "record_id": record["record_id"], "episode_id_hash": record["episode_id_hash"],
            "near_duplicate_key": near_duplicate_key(record), "selection_reason": sorted(set(reasons)),
            "priority_score": priority, "component_scores": dict(sorted(components.items())),
        })
    scored.sort(key=lambda item: (-float(item["priority_score"]), str(item["record_id"])))
    chosen: list[dict[str, object]] = []
    episodes: Counter[str] = Counter()
    duplicates: Counter[str] = Counter()
    for item in scored:
        episode, duplicate = str(item["episode_id_hash"]), str(item["near_duplicate_key"])
        if episodes[episode] >= config.max_per_episode or duplicates[duplicate] >= config.max_per_near_duplicate:
            continue
        chosen.append(item)
        episodes[episode] += 1
        duplicates[duplicate] += 1
        if len(chosen) == config.limit:
            break
    selected = sorted(chosen, key=lambda item: str(item["record_id"]))
    source_hash = _source_dataset_hash(values)
    manifest_config = {"limit": config.limit, "max_per_episode": config.max_per_episode, "max_per_near_duplicate": config.max_per_near_duplicate}
    return {
        "schema_version": SELECTION_POLICY_VERSION, "source_dataset_hash": source_hash,
        "config": manifest_config,
        "selected": selected,
        "selection_hash": _selection_hash(source_dataset_hash=source_hash, config=manifest_config, selected=selected),
    }


def selected_records(records: Iterable[dict[str, object]], manifest: object) -> list[dict[str, object]]:
    values = list(records)
    validate_records(values)
    required = {"schema_version", "source_dataset_hash", "config", "selected", "selection_hash"}
    if not isinstance(manifest, dict) or set(manifest) != required or manifest.get("schema_version") != SELECTION_POLICY_VERSION or not isinstance(manifest.get("selected"), list):
        raise DecisionDatasetError("invalid selection manifest")
    config = manifest.get("config")
    if not isinstance(config, dict) or set(config) != {"limit", "max_per_episode", "max_per_near_duplicate"}:
        raise DecisionDatasetError("selection manifest config is invalid")
    try:
        selection_config = SelectionConfig(**config)
    except (TypeError, ValueError) as exc:
        raise DecisionDatasetError("selection manifest config is invalid") from exc
    source_hash = _source_dataset_hash(values)
    if manifest.get("source_dataset_hash") != source_hash:
        raise DecisionDatasetError("selection manifest source dataset hash mismatch")
    selected = manifest["selected"]
    ids = [item.get("record_id") for item in selected if isinstance(item, dict)]
    if len(ids) != len(selected) or not all(isinstance(record_id, str) for record_id in ids):
        raise DecisionDatasetError("selection manifest record IDs are invalid")
    if len(ids) != len(set(ids)):
        raise DecisionDatasetError("selection manifest record IDs are duplicated")
    if ids != sorted(ids):
        raise DecisionDatasetError("selection manifest record IDs are not canonical")
    expected_hash = _selection_hash(source_dataset_hash=source_hash, config=config, selected=selected)
    if manifest.get("selection_hash") != expected_hash:
        raise DecisionDatasetError("selection manifest hash mismatch")
    expected = select_targeted(values, selection_config)
    if manifest != expected:
        raise DecisionDatasetError("selection manifest does not match deterministic recomputation")
    by_id = {str(item["record_id"]): item for item in values}
    if any(record_id not in by_id for record_id in ids):
        raise DecisionDatasetError("selection references missing records")
    return [by_id[record_id] for record_id in ids]


__all__ = ["SELECTION_POLICY_VERSION", "SelectionConfig", "select_targeted", "selected_records"]
