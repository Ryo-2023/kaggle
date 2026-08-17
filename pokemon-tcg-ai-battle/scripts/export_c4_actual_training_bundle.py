"""Package a data-ops run as the canonical C4 consumer bundle.

The source run remains read-only.  This export copies only the RuleBC dataset
to the bundle root; candidate bindings remain in the source private directory
and are represented by a hash/count descriptor for provenance only.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from mage_ptcg.dataops import DataOpsError, scan_public_artifact, validate_run
from mage_ptcg.student.artifact import feature_schema
from mage_ptcg.student.dataset import DatasetValidationError, RuleBCExample, load_dataset
from scripts.accept_c4_actual_training_bundle import (
    BUNDLE_SCHEMA_VERSION,
    SPLIT_SCHEMA_VERSION,
    _canonical_json,
    _content_hash,
    _manifest_hash,
    _sha256_bytes,
)


class BundleExportError(ValueError):
    """Raised when a source run cannot safely form a canonical bundle."""


_ACTUAL_MINIMUMS = {
    "completed_episodes": 24,
    "train_episodes": 16,
    "validation_episodes": 4,
    "decisions": 800,
    "candidate_records": 3000,
}


def _read_object(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BundleExportError("source manifest is unreadable") from exc
    if not isinstance(value, dict):
        raise BundleExportError("source manifest must be an object")
    return value


def _read_rows(path: Path) -> list[dict[str, object]]:
    try:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, json.JSONDecodeError) as exc:
        raise BundleExportError("source dataset is unreadable") from exc
    if not rows or any(not isinstance(row, dict) for row in rows):
        raise BundleExportError("source dataset has no object rows")
    return rows


def _write_json(path: Path, value: object) -> None:
    path.write_text(_canonical_json(value) + "\n", encoding="utf-8")


def _actual_training_source(
    *,
    source_manifest: Mapping[str, object],
    source_split: Mapping[str, object],
    source_summary: Mapping[str, object],
    examples: list[RuleBCExample],
    bindings_count: int,
    source_dataset_path: Path,
    dataset_hash: str,
) -> bool:
    """Return true only for a complete, validated collector engineering run.

    This intentionally consumes the collector's measured manifest rather than
    an exporter CLI flag.  A source that is merely a collection smoke remains
    available only as a ``TEST_FIXTURE`` contract fixture.
    """
    if source_manifest.get("artifact_purpose") != "ACTUAL_TRAINING" or source_manifest.get("performance_eligible") is not True:
        return False
    if source_summary.get("artifact_purpose") != "ACTUAL_TRAINING" or source_summary.get("performance_eligible") is not True:
        return False
    if source_summary.get("status") != "PASS":
        return False
    if source_manifest.get("dataset_hash") != dataset_hash or source_summary.get("dataset_hash") != dataset_hash:
        return False
    if source_manifest.get("manifest_hash") != _manifest_hash(source_manifest):
        return False
    if source_split.get("manifest_hash") != _manifest_hash(source_split):
        return False
    if source_manifest.get("privacy_scan_executed") is not True or source_manifest.get("privacy_violations") != 0:
        return False
    if source_summary.get("privacy_scan_executed") is not True or source_summary.get("privacy_violations") != 0:
        return False
    gate = source_manifest.get("engineering_gate")
    if not isinstance(gate, Mapping):
        return False
    if any(type(gate.get(name)) is not int or int(gate[name]) < minimum for name, minimum in _ACTUAL_MINIMUMS.items()):
        return False
    expected_count = len(examples)
    if any(gate.get(name) != expected_count for name in ("binding_records", "chosen_targets", "teacher_targets")):
        return False
    if bindings_count != expected_count:
        return False
    if any(type(gate.get(name)) is not int or gate[name] != 0 for name in ("split_overlap_count", "duplicate_decision_count", "invalid_target_count", "non_finite_count")):
        return False
    if any(type(source_split.get(name)) is not int or source_split[name] != 0 for name in ("duplicate_episode_count", "duplicate_decision_count", "split_overlap_count")):
        return False
    assignments = source_split.get("assignments")
    source_ids = {example.source_id for example in examples}
    if not isinstance(assignments, Mapping) or set(assignments) != source_ids:
        return False
    if any(partition not in {"train", "validation"} for partition in assignments.values()):
        return False
    train = {source_id for source_id, partition in assignments.items() if partition == "train"}
    validation = {source_id for source_id, partition in assignments.items() if partition == "validation"}
    if train.intersection(validation) or len(train) < _ACTUAL_MINIMUMS["train_episodes"] or len(validation) < _ACTUAL_MINIMUMS["validation_episodes"]:
        return False
    if source_split.get("train_episode_count") != len(train) or source_split.get("validation_episode_count") != len(validation):
        return False
    expected_split_hash = _content_hash({"assignments": dict(sorted(assignments.items())), "dataset_hash": dataset_hash})
    if source_split.get("dataset_hash") != dataset_hash or source_split.get("split_hash") != expected_split_hash:
        return False
    if source_manifest.get("decision_count") != expected_count or source_manifest.get("candidate_count") != sum(len(example.legal_actions) for example in examples):
        return False
    if source_manifest.get("dataset_file_sha256") != _sha256_bytes(source_dataset_path.read_bytes()):
        return False
    try:
        report = validate_run(source_dataset_path.parents[1])
    except (DataOpsError, OSError, ValueError):
        return False
    return (
        report.get("valid") is True
        and report.get("row_count") == expected_count
        and report.get("binding_count") == expected_count
        and report.get("duplicate_decision_count") == 0
        and report.get("privacy_scan_executed") is True
        and report.get("privacy_violations") == 0
    )


def export_bundle(
    *, run_root: str | Path, output_root: str | Path, require_actual_training: bool = False
) -> dict[str, object]:
    """Export a validated collector run without allowing a CLI eligibility override."""
    source = Path(run_root)
    destination = Path(output_root)
    dataset_source = source / "private_dataset" / "rule-bc-v1.jsonl"
    bindings_source = source / "private_dataset" / "private_bindings.jsonl"
    source_manifest = _read_object(source / "dataset_manifest.json")
    source_split = _read_object(source / "split_manifest.json")
    source_summary = _read_object(source / "public_summary.json")
    if source_summary.get("privacy_scan_executed") is not True or source_summary.get("privacy_violations") != 0:
        raise BundleExportError("source privacy scan did not pass")
    if not dataset_source.is_file() or not bindings_source.is_file() or destination.exists():
        raise BundleExportError("source files are missing or output root already exists")

    rows = _read_rows(dataset_source)
    try:
        examples = load_dataset(dataset_source)
    except (OSError, DatasetValidationError) as exc:
        raise BundleExportError("source rows are not RuleBCExample compatible") from exc
    if len(rows) != len(examples):
        raise BundleExportError("source rows do not align with validated examples")
    # A real smoke can include optional engine prompts for which Rule v0
    # correctly selects nothing.  They are not BC supervision, so the bundle
    # carries only paired rows with a chosen/teacher target; source files stay
    # untouched and its complete binding ledger remains provenance-only.
    supervised = [(row, example) for row, example in zip(rows, examples, strict=True) if example.target_action_digests]
    rows = [row for row, _example in supervised]
    examples = [example for _row, example in supervised]
    if not examples:
        raise BundleExportError("source dataset has no supervised decisions")
    bindings_count = sum(1 for line in bindings_source.read_text(encoding="utf-8").splitlines() if line.strip())
    if bindings_count != len(examples):
        raise BundleExportError("source private binding count does not match decisions")
    assignments = source_split.get("assignments")
    if not isinstance(assignments, Mapping):
        raise BundleExportError("source episode assignments are missing")
    assignments = dict(assignments)
    source_ids = {example.source_id for example in examples}
    train_ids = {source_id for source_id, partition in assignments.items() if partition == "train"}
    validation_ids = {source_id for source_id, partition in assignments.items() if partition == "validation"}
    if set(assignments) != source_ids or not train_ids or not validation_ids or train_ids.intersection(validation_ids):
        raise BundleExportError("source split is incomplete, empty, or overlapping")

    dataset_hash = _content_hash(rows)
    dataset_bytes = "".join(_canonical_json(row) + "\n" for row in rows).encode("utf-8")
    dataset_file_hash = _sha256_bytes(dataset_bytes)
    split_manifest: dict[str, object] = {
        "schema_version": SPLIT_SCHEMA_VERSION,
        "dataset_hash": dataset_hash,
        "split_method": source_split.get("split_method", source_split.get("method", "external_manifest")),
        "split_seed": source_split.get("split_seed", 0),
        "assignments": dict(sorted(assignments.items())),
        "train_episode_count": len(train_ids),
        "validation_episode_count": len(validation_ids),
        "split_overlap_count": 0,
        "duplicate_episode_count": 0,
        "duplicate_decision_count": 0,
    }
    split_manifest["split_hash"] = _content_hash({"assignments": split_manifest["assignments"], "dataset_hash": dataset_hash})
    split_manifest["manifest_hash"] = _manifest_hash(split_manifest)

    schema = feature_schema()
    provenance = sorted({example.metadata.get("trace_provenance_hash") for example in examples})
    if any(not isinstance(value, str) or not value for value in provenance):
        raise BundleExportError("source rows are missing trace provenance")
    bindings_hash = _sha256_bytes(bindings_source.read_bytes())
    actual_training = _actual_training_source(
        source_manifest=source_manifest,
        source_split=source_split,
        source_summary=source_summary,
        examples=examples,
        bindings_count=bindings_count,
        source_dataset_path=dataset_source,
        dataset_hash=dataset_hash,
    )
    if require_actual_training and not actual_training:
        raise BundleExportError("source run does not meet actual-training engineering eligibility")
    source_purpose = "ACTUAL_TRAINING" if actual_training else "TEST_FIXTURE"
    manifest: dict[str, object] = {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "artifact_purpose": source_purpose,
        "source_kind": "ACTUAL_CABT_RULE_BC" if actual_training else "ACTUAL_CABT_COLLECTION_SMOKE",
        "performance_eligible": actual_training,
        "dataset_schema_version": "rule-bc-v1",
        "dataset_file": "rule-bc-v1.jsonl",
        "dataset_hash": dataset_hash,
        "dataset_file_sha256": dataset_file_hash,
        "episode_count": len(source_ids),
        "decision_count": len(examples),
        "candidate_count": sum(len(example.legal_actions) for example in examples),
        "chosen_target_decision_count": len(examples),
        "episode_group_ids": sorted(source_ids),
        "trace_provenance_hashes": provenance,
        **schema,
        "teacher_source": source_manifest.get("teacher_source", "Rule Agent v0"),
        "teacher_version": source_manifest.get("source_agent_version", "actual-viability-v0"),
        "teacher_quality": source_manifest.get("teacher_quality", "RULE_ONLY"),
        "training_objective": source_manifest.get("training_objective", "RULE_IMITATION"),
        "privacy_scan_executed": True,
        "privacy_violations": 0,
        "private_binding": {
            "path_role": "private_bindings",
            "sha256": bindings_hash,
            "record_count": bindings_count,
            "trainer_input": False,
        },
    }
    if actual_training:
        manifest["canonical_base_sha"] = source_manifest.get("canonical_base_sha")
        manifest["source_dataset_manifest_hash"] = source_manifest.get("manifest_hash")
        manifest["source_split_manifest_hash"] = source_split.get("manifest_hash")
    scan = scan_public_artifact({"dataset_manifest": manifest, "split_manifest": split_manifest})
    if scan["privacy_violations"] != 0:
        raise BundleExportError("canonical public manifests fail privacy scan")
    manifest["manifest_hash"] = _manifest_hash(manifest)
    summary: dict[str, object] = {
        "artifact_purpose": source_purpose,
        "performance_eligible": actual_training,
        "source_kind": manifest["source_kind"],
        "dataset_hash": dataset_hash,
        "dataset_manifest_hash": manifest["manifest_hash"],
        "split_hash": split_manifest["split_hash"],
        "split_manifest_hash": split_manifest["manifest_hash"],
        "episode_count": len(source_ids),
        "decision_count": len(examples),
        "candidate_count": manifest["candidate_count"],
        "privacy_scan_executed": True,
        "privacy_violations": 0,
    }
    if scan_public_artifact(summary)["privacy_violations"] != 0:
        raise BundleExportError("canonical public summary fails privacy scan")

    destination.mkdir(parents=True)
    (destination / "rule-bc-v1.jsonl").write_bytes(dataset_bytes)
    _write_json(destination / "dataset_manifest.json", manifest)
    _write_json(destination / "split_manifest.json", split_manifest)
    _write_json(destination / "public_summary.json", summary)
    return {"status": "PASS", **summary}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument(
        "--require-actual-training",
        action="store_true",
        help="reject a non-eligible source; this never promotes eligibility",
    )
    args = parser.parse_args(argv)
    try:
        print(json.dumps(export_bundle(run_root=args.run_root, output_root=args.output_root, require_actual_training=args.require_actual_training), ensure_ascii=False, sort_keys=True))
        return 0
    except (BundleExportError, OSError, ValueError) as exc:
        print(f"C4 bundle export failed: {type(exc).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
