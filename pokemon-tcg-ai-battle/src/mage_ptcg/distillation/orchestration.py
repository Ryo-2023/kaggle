"""C5 split, C4-compatible conversion, and model provenance helpers."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
import hashlib
from typing import Iterable

from mage_ptcg.student.dataset import RuleBCExample, validate_example
from mage_ptcg.meta_specialist.cabt_json_contract_v1 import is_ordered_selection

from .contracts import DecisionDatasetError, digest, near_duplicate_key, validate_records


SPLIT_SCHEMA_VERSION = "c5-episode-near-duplicate-split-v2"


@dataclass(frozen=True, slots=True)
class SplitConfig:
    validation_percent: int = 20
    test_percent: int = 20
    seed: str = "c5-default-seed"

    def __post_init__(self) -> None:
        if type(self.validation_percent) is not int or type(self.test_percent) is not int or not 1 <= self.validation_percent < 100 or not 1 <= self.test_percent < 100 or self.validation_percent + self.test_percent >= 100:
            raise ValueError("split percentages must leave a non-empty train partition")
        if not isinstance(self.seed, str) or not self.seed:
            raise ValueError("split seed is required")


def _input_hash(records: list[dict[str, object]]) -> str:
    return digest([
        {"record_id": str(record["record_id"]), "content_hash": str(record["content_hash"])}
        for record in sorted(records, key=lambda item: str(item["record_id"]))
    ], domain="split-input")


def build_split_manifest(records: Iterable[dict[str, object]], config: SplitConfig = SplitConfig()) -> dict[str, object]:
    """Assign connected episode/near-duplicate components, never records alone."""
    values = list(records)
    validate_records(values)
    parent: dict[str, str] = {}

    def find(value: str) -> str:
        parent.setdefault(value, value)
        if parent[value] != value:
            parent[value] = find(parent[value])
        return parent[value]

    def union(left: str, right: str) -> None:
        left, right = find(left), find(right)
        if left != right:
            parent[right] = left

    owners: dict[str, str] = {}
    for record in values:
        episode = f"episode:{record['episode_id_hash']}"
        duplicate = f"duplicate:{near_duplicate_key(record)}"
        union(episode, duplicate)
        owners[str(record["record_id"])] = episode
    components: dict[str, list[str]] = defaultdict(list)
    for record_id, owner in owners.items():
        components[find(owner)].append(record_id)
    assignments: dict[str, str] = {}
    canonical_components = sorted(sorted(record_ids) for record_ids in components.values())
    for record_ids in canonical_components:
        component = digest(record_ids, domain="split-component")
        bucket = int(hashlib.sha256(f"{config.seed}\0{component}".encode()).hexdigest()[:8], 16) % 100
        split = "validation" if bucket < config.validation_percent else "test" if bucket < config.validation_percent + config.test_percent else "train"
        assignments.update({record_id: split for record_id in record_ids})
    counts = {split: sum(value == split for value in assignments.values()) for split in ("train", "validation", "test")}
    if any(count == 0 for count in counts.values()):
        raise DecisionDatasetError("group split produced an empty partition; collect more episodes")
    return {
        "schema_version": SPLIT_SCHEMA_VERSION, "config": asdict(config), "assignments": dict(sorted(assignments.items())),
        "counts": counts, "input_hash": _input_hash(values),
        "component_hash": digest(canonical_components, domain="split-components"),
    }


def validate_split_manifest(records: Iterable[dict[str, object]], manifest: object) -> dict[str, int]:
    values = list(records)
    validate_records(values)
    if not isinstance(manifest, dict) or set(manifest) != {"schema_version", "config", "assignments", "counts", "input_hash", "component_hash"} or manifest.get("schema_version") != SPLIT_SCHEMA_VERSION or not isinstance(manifest.get("assignments"), dict):
        raise DecisionDatasetError("invalid split manifest")
    config = manifest.get("config")
    if not isinstance(config, dict) or set(config) != {"validation_percent", "test_percent", "seed"}:
        raise DecisionDatasetError("split manifest config is invalid")
    try:
        split_config = SplitConfig(**config)
    except (TypeError, ValueError) as exc:
        raise DecisionDatasetError("split manifest config is invalid") from exc
    assignments = manifest["assignments"]
    if (not all(isinstance(record_id, str) for record_id in assignments)
            or not all(isinstance(split, str) for split in assignments.values())):
        raise DecisionDatasetError("split manifest assignments are invalid")
    identifiers = {str(item["record_id"]) for item in values}
    if set(assignments) != identifiers or not set(assignments.values()).issubset({"train", "validation", "test"}):
        raise DecisionDatasetError("split manifest does not cover exactly this dataset")
    recomputed = build_split_manifest(values, split_config)
    for key in ("assignments", "counts", "input_hash", "component_hash"):
        if manifest[key] != recomputed[key]:
            raise DecisionDatasetError(f"split manifest {key} does not match deterministic recomputation")
    episode_splits: dict[str, set[str]] = defaultdict(set)
    duplicate_splits: dict[str, set[str]] = defaultdict(set)
    for record in values:
        split = assignments[str(record["record_id"])]
        episode_splits[str(record["episode_id_hash"])].add(split)
        duplicate_splits[near_duplicate_key(record)].add(split)
    if any(len(value) > 1 for value in episode_splits.values()):
        raise DecisionDatasetError("episode leakage across split")
    if any(len(value) > 1 for value in duplicate_splits.values()):
        raise DecisionDatasetError("near-duplicate leakage across split")
    return {split: sum(value == split for value in assignments.values()) for split in ("train", "validation", "test")}


def records_for_split(records: Iterable[dict[str, object]], manifest: object, split: str) -> list[dict[str, object]]:
    values = list(records)
    validate_split_manifest(values, manifest)
    return [item for item in values if manifest["assignments"][item["record_id"]] == split]  # type: ignore[index]


def convert_to_rule_bc(records: Iterable[dict[str, object]]) -> list[RuleBCExample]:
    """Make a C4 Rule BC view using only C5 public candidate identity.

    The resulting C4 file intentionally uses public action IDs as its digest;
    it does not reconstruct or reintroduce C1's private ActionKey core digest.
    """
    values = list(records)
    validate_records(values)
    converted: list[RuleBCExample] = []
    for record in values:
        selection = record["selection"]
        if is_ordered_selection(selection["type"], selection["context"]):  # type: ignore[index]
            raise DecisionDatasetError(
                "legacy candidate-wise C4 conversion cannot represent ordered Skill labels"
            )
        legal = tuple({"digest": item["action_id"], "payload": item["public_payload"]} for item in record["legal_actions"])
        ranking = tuple((item["action_id"], item["score"]) for item in record["rule_v0"]["ranking"])  # type: ignore[index]
        result = RuleBCExample(
            schema_version="rule-bc-v1", example_id=str(record["record_id"]), source_id=str(record["episode_id_hash"]),
            public_state=record["public_observation"], own_private_state=record["own_private_state"], visible_history=tuple(record["history"]),  # type: ignore[arg-type]
            selection_type=selection["type"], selection_context=selection["context"], min_count=selection["min_count"], max_count=selection["max_count"],  # type: ignore[index]
            legal_actions=legal, target_action_digests=tuple(record["chosen_action_ids"]), teacher_ranking=ranking,
            fallback_used=record["fallback_reason"] is not None, deck_fingerprint=record["provenance"]["deck_fingerprint"],  # type: ignore[index]
            source_revision=record["source"]["revision"], metadata={"c5_record_id": str(record["record_id"]), "c5_public_trace_digest": str(record["provenance"]["public_trace_digest"])},  # type: ignore[index]
        )
        validate_example(result)
        converted.append(result)
    return converted


def model_provenance(*, dataset_records: Iterable[dict[str, object]], split_manifest: object, selection_manifest: object | None, config: dict[str, object], model_hash: str) -> dict[str, object]:
    records = list(dataset_records)
    validate_records(records)
    validate_split_manifest(records, split_manifest)
    return {
        "schema_version": "c5-model-provenance-v1", "dataset_hash": digest(sorted(item["content_hash"] for item in records), domain="model-dataset"),
        "split_hash": digest(split_manifest, domain="model-split"),
        "selection_hash": None if selection_manifest is None else digest(selection_manifest, domain="model-selection"),
        "config_hash": digest(config, domain="model-config"), "model_hash": model_hash,
        "synthetic_records": sum(bool(item["source"]["synthetic"]) for item in records),
        "actual_records": sum(not bool(item["source"]["synthetic"]) for item in records),
    }


__all__ = [
    "SPLIT_SCHEMA_VERSION", "SplitConfig", "build_split_manifest", "convert_to_rule_bc", "model_provenance",
    "records_for_split", "validate_split_manifest",
]
